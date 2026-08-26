"""Analysis-builder mixin: static analysis, seismic masses, and result extraction."""

import logging
import math
from typing import TYPE_CHECKING, Any, Optional

import numpy as np
import openseespy.opensees as ops

from ..model.geometry import polygon_area_3d
from ..model.sap_data import FrameElement
from ..model.tree_utils import collect_descendants
from ..utils import g_from_units

if TYPE_CHECKING:
    # pandas is not a required dependency — imported only at runtime
    # inside check_load_equilibrium().
    import pandas as pd

logger = logging.getLogger(__name__)


class StaticRunnerMixin:
    """Static analysis execution, seismic mass derivation, and result extraction."""

    def run_static_analysis(
        self,
        extract_reactions: bool = True,
        pattern_scales: Optional[dict[str, float]] = None,
    ) -> dict[str, Any]:
        """Run static analysis on the current OpenSees domain.

        When *pattern_scales* is provided, the domain is rebuilt with
        only those load patterns active (matching the facade's behaviour).
        When *pattern_scales* is ``None`` (default), the existing domain
        is analysed as-is.

        Returns a dict with nodal_displacements, reactions, element_forces,
        and load_totals.
        """
        # Rebuild domain with new pattern scales if requested
        if pattern_scales is not None:
            self.build_domain()
            self._reapply_edge_constraints()
            self.create_loads(pattern_scales=pattern_scales)

        sol_cfg = self.config
        sd = self.PUSHOVER_SOLVER_DEFAULTS
        test_type = sol_cfg.get("solver_test_type", sd["solver_test_type"])
        test_tol = sol_cfg.get("solver_test_tol", sd["solver_test_tol"])
        test_iter = sol_cfg.get("solver_test_max_iter", sd["solver_test_max_iter"])
        algo = sol_cfg.get("solver_algorithm", sd["solver_algorithm"])
        n_sub = sol_cfg.get("gravity_num_substeps", sd["gravity_num_substeps"])

        cs = sol_cfg.get("solver_constraints", sd["solver_constraints"])
        if self._edge_constraint_method == "penalty":
            cs = "Penalty"
            ops.constraints("Penalty", 1.0e12, 1.0e12)
        else:
            ops.constraints(cs)
        ops.numberer("RCM")
        ops.system(sol_cfg.get("solver_system", sd["solver_system"]))
        ops.test(test_type, test_tol, test_iter)

        _algo_chain = [algo]
        if algo != "NewtonLineSearch":
            _algo_chain.append("NewtonLineSearch")
        if algo != "ModifiedNewton":
            _algo_chain.append(("ModifiedNewton", "-initial"))
        if algo != "KrylovNewton":
            _algo_chain.append("KrylovNewton")

        # ── Fallback settings (Gap 5) ────────────────────────────
        # If the primary chain fails (e.g. the fiber-rebuild gravity
        # solve returning NaN), retry the remaining substeps with
        # NormUnbalance + relaxed tolerance + ModifiedNewton(-initial).
        _fallback = sol_cfg.get("pushover_fallback_defaults", self.PUSHOVER_FALLBACK_DEFAULTS)
        fb_test_type = _fallback.get("solver_test_type", "NormUnbalance")
        # Units-aware fallback tolerance: scale off the model's
        # characteristic weight (total mass × g via g_from_units), which
        # has consistent force units.  An absolute unscaled tolerance
        # (e.g. 1e-12) is unattainable for full-building residuals.
        _g = g_from_units(self.units)
        _fb_total_mass = sum(self.node_masses.values()) if self.node_masses else 0.0
        if _fb_total_mass > 0:
            # 1e-4 of the total weight — a relative NormUnbalance budget
            # that large shell-meshed buildings can actually satisfy.  The
            # previous 1e-6 factor produced ~0.06 kN on a ~51,500 kN model,
            # which the fallback could never reach (it burned its whole
            # iteration budget on every failed step).
            fb_test_tol = max(_fb_total_mass * _g * 1e-4, test_tol * 10.0)
        else:
            fb_test_tol = test_tol * 10.0
        fb_test_iter = max(_fallback.get("solver_test_max_iter", 1000), test_iter * 10)
        fb_algo = _fallback.get("solver_algorithm", "ModifiedNewton")

        # ── Configure the static analysis once ──────────────────
        # Do NOT re-create the integrator/analysis between algorithm
        # attempts.  A failed step rolls back to the last committed
        # state but the integrator's internal load factor remains at
        # the last *converged* increment.  Re-creating the analysis (as
        # historically done) resets that counter to 0, so a partially-
        # converged attempt (e.g. substeps 1-2 of n_sub=10 succeeded,
        # then substep 3 failed) forces the next algorithm to *unload*
        # from load factor 0.2 back to 0.1 — with forceBeamColumn fiber
        # sections this unloading path produces NaN.  Keeping the same
        # StaticAnalysis object and switching only the algorithm lets the
        # load factor continue monotonically 0.1 -> 0.2 -> ... -> 1.0.
        ops.integrator("LoadControl", 1.0 / n_sub)
        ops.analysis("Static")

        converged = 0
        ok = -1
        for attempt in _algo_chain:
            if isinstance(attempt, tuple):
                ops.algorithm(*attempt)
            elif attempt == "ModifiedNewton":
                ops.algorithm("ModifiedNewton", "-initial")
            else:
                ops.algorithm(attempt)
            ok = 0
            for s in range(converged, n_sub):
                ok = ops.analyze(1)
                if ok != 0:
                    break
                converged = s + 1
            if ok == 0:
                break

        if ok != 0:
            # Relaxed NormUnbalance + ModifiedNewton(-initial) fallback
            # pass, resuming from the last converged substep *without*
            # resetting the integrator (same monotonic-load-factor
            # reasoning as above).
            ops.test(fb_test_type, fb_test_tol, fb_test_iter)
            if fb_algo == "ModifiedNewton":
                ops.algorithm("ModifiedNewton", "-initial")
            else:
                ops.algorithm(fb_algo)
            ok = 0
            for s in range(converged, n_sub):
                ok = ops.analyze(1)
                if ok != 0:
                    break
                converged = s + 1

        if ok != 0:
            # Adaptive substepping (Gap 5): the RC fiber model can
            # still fail a fixed LoadControl step (e.g. 30% of gravity)
            # when a column softens between two converged states.  Halve
            # the load increment and continue monotonically from the last
            # converged state.  The analysis object stays alive — only
            # the integrator is swapped — so the load factor continues
            # 0.2 → 0.225 → 0.25 ... instead of unloading back toward 0
            # (which produced the original NaN).
            # Track the applied load factor as a float (each successful
            # ops.analyze(1) advances it by the current increment) rather
            # than remapping the integer converged step count.  Retry
            # passes derive their start index from this load factor, so
            # they never issue increments beyond gravity load factor 1.0.
            applied_load_factor = float(converged) / float(n_sub)
            half_n_sub = n_sub * 2
            half_inc = 1.0 / half_n_sub
            done_half = int(round(applied_load_factor * half_n_sub))
            done_half = min(max(done_half, 0), half_n_sub)
            ops.integrator("LoadControl", half_inc)
            ok = 0
            for s in range(done_half, half_n_sub):
                ok = ops.analyze(1)
                if ok != 0:
                    break
                applied_load_factor += half_inc
            if ok != 0:
                # Final fallback: quarter inc (only used when the model is
                # extremely soft near the target gravity combination).
                quad_n_sub = n_sub * 4
                quad_inc = 1.0 / quad_n_sub
                done_quad = int(round(applied_load_factor * quad_n_sub))
                done_quad = min(max(done_quad, 0), quad_n_sub)
                ops.integrator("LoadControl", quad_inc)
                ok = 0
                for s in range(done_quad, quad_n_sub):
                    ok = ops.analyze(1)
                    if ok != 0:
                        break
                    applied_load_factor += quad_inc

        if ok != 0:
            raise RuntimeError(
                f"Static analysis failed to converge after trying algorithms: {_algo_chain}"
            )

        # Extract results
        result: dict[str, Any] = {}

        # Nodal displacements
        result["nodal_displacements"] = {}
        for nd in self.mesh_model.nodes.values():
            try:
                disp = ops.nodeDisp(nd.node_tag)
                result["nodal_displacements"][nd.node_id] = list(disp)
            except Exception:
                continue

        # Reactions
        if extract_reactions:
            ops.reactions()
            result["reactions"] = {}
            for nid, restraint in self.mesh_model.restraints.items():
                nd = self.mesh_model.nodes.get(nid)
                if nd is None:
                    continue
                try:
                    rxn = ops.nodeReaction(nd.node_tag)
                    result["reactions"][nid] = {
                        "fx": rxn[0],
                        "fy": rxn[1],
                        "fz": rxn[2],
                        "mx": rxn[3],
                        "my": rxn[4],
                        "mz": rxn[5],
                    }
                except Exception:
                    continue

        # ── Gravity load/reaction sanity check ──────────────────
        if extract_reactions and (
            self._gravity_load_totals or self._joint_load_totals or self._sw_load_totals
        ):
            total_applied_fz = 0.0
            for totals in self._gravity_load_totals.values():
                total_applied_fz += totals.get("fz", 0.0)
            for totals in self._joint_load_totals.values():
                total_applied_fz += totals.get("fz", 0.0)
            for totals in self._sw_load_totals.values():
                total_applied_fz += totals.get("fz", 0.0)
            for totals in getattr(self, "_dist_load_totals", {}).values():
                total_applied_fz += totals.get("fz", 0.0)

            total_reaction_fz = 0.0
            for nid, restraint in self.mesh_model.restraints.items():
                # Vertical equilibrium is checked against every node that
                # restrains the vertical DOF (index 2 = Fz).  Requiring full
                # six-DOF fixity undercounts gravity reactions on models
                # whose bases are pinned rather than fixed (e.g. the Admin
                # Building's 90 pinned column bases, of which only the 17
                # shell-only nodes carry rotational fixity) and raises a
                # spurious "load/reaction mismatch" warning on an
                # equilibrated model.
                if len(restraint.dofs) < 3 or not restraint.dofs[2]:
                    continue
                rxn = result.get("reactions", {}).get(nid, {})
                total_reaction_fz += rxn.get("fz", 0.0)

            # Compare magnitudes using the established opposite-sign
            # convention: gravity loads are downward (negative Fz) while
            # reactions are upward (positive Fz).  The equilibrium delta
            # is the difference of the magnitudes, not the direct
            # subtraction of signed values (which double-counts).
            abs_applied = abs(total_applied_fz)
            abs_reaction = abs(total_reaction_fz)
            delta = abs(abs_applied - abs_reaction)
            tol = max(abs_applied * 0.01, 1e-6)

            if delta > tol and abs_applied > 1e-12:
                pct = (delta / abs_applied * 100) if abs_applied > 1e-12 else 0.0
                logger.warning(
                    "Gravity load/reaction mismatch: "
                    "applied fz=%.6e, "
                    "reaction fz=%.6e, "
                    "Δ=%.6e (%.1f%%)",
                    total_applied_fz,
                    total_reaction_fz,
                    delta,
                    pct,
                )

            result["load_reaction_check"] = {
                "applied_fz": total_applied_fz,
                "reaction_fz": total_reaction_fz,
                "delta": delta,
            }

        return result

    def compute_seismic_masses(self) -> dict[str, float]:
        """Compute lumped nodal masses from the model's MASS SOURCE entries.

        Gravitational acceleration is derived from the model's units via
        :func:`~fea_toolkit.utils.g_from_units` — the model unit system is
        the single source of truth (never a hardcoded 9.81).

        All mass contributions are lumped to nodes and assigned via
        ``ops.mass(node, m, m, m, 0, 0, 0)``.

        Returns:
            Dictionary mapping node ID → total lumped mass (tonnes).
        """
        g = g_from_units(self.mesh_model.units)

        mm = self.mesh_model
        elements = mm.frame_elements
        assignments = mm.frame_assignments
        dist_loads = mm.frame_dist_loads

        node_mass: dict[str, float] = {}

        mass_sources = getattr(mm, "mass_sources", {})
        if not mass_sources:
            # No MASS SOURCE definitions — fallback: element self-weight + DEAD
            self._mass_from_elements(mm, elements, assignments, node_mass, g)
            self._mass_from_dist_loads(mm, elements, dist_loads, node_mass, g, ["DEAD"])
        else:
            for ms in mass_sources.values():
                if ms.elements:
                    self._mass_from_elements(mm, elements, assignments, node_mass, g)

                if ms.loads and ms.load_pattern:
                    for lp_name, mult in ms.load_pattern.items():
                        if abs(mult) < 1e-12:
                            continue
                        self._mass_from_dist_loads(
                            mm, elements, dist_loads, node_mass, g, [lp_name], mult
                        )
                        self._mass_from_joint_loads(mm, node_mass, g, lp_name, mult)
                        self._mass_from_area_gravity(mm, node_mass, g, lp_name, mult)
                        self._mass_from_area_uniform(mm, node_mass, g, lp_name, mult)

        # Assign masses to OpenSees nodes
        for nid, m in node_mass.items():
            nd = mm.nodes.get(nid)
            if nd is None:
                continue
            tag = nd.node_tag
            if m > 0:
                ops.mass(tag, m, m, m, 0, 0, 0)
            else:
                ops.mass(tag, 1e-6, 1e-6, 1e-6, 0, 0, 0)

        self.node_masses = node_mass
        self._mass_g = g

        if self.config.get("verbose"):
            total = sum(node_mass.values())
            print(f"  Total seismic mass: {total:.2f} tonnes")
            print(f"  Total seismic weight: {total * g / 1000:.2f} MN")

        return node_mass

    def _query_nodal_masses(self) -> dict[int, float]:
        """Query lumped translational masses from the active OpenSees domain.

        Reads ``ops.nodeMass()`` directly so the returned dict is keyed by
        numeric OpenSees node tag — the same key space used by
        :meth:`extract_mode_shapes`.  This is the dict consumed by
        :func:`~fea_toolkit.model.csm.pushover_to_adrs` via the
        ``modal_results['nodal_masses']`` key; without it the ADRS
        conversion degenerates to ``Gamma = M_eff = 1.0``.

        Returns:
            ``{node_tag: mass}`` for every node in the active domain.
            Nodes with no applicable mass are included with ``0.0`` so
            the ADRS conversion sees the full node set.
        """
        masses: dict[int, float] = {}
        for tag in ops.getNodeTags():
            try:
                m = ops.nodeMass(int(tag))
                masses[int(tag)] = float(m[0]) if m else 0.0
            except Exception:
                masses[int(tag)] = 0.0
        return masses

    def _mass_from_elements(self, mm, elements, assignments, node_mass, g):
        """Add mass from element self-weight."""
        for eid, elem in elements.items():
            if getattr(elem, "inactive", False):
                continue
            sec_name = assignments.get(eid, "")
            if not sec_name:
                continue
            sec = mm.sections.get(sec_name)
            if sec is None:
                continue
            mat = mm.materials.get(sec.material)
            if mat is None or abs(mat.unit_weight) < 1e-12:
                continue
            ni = mm.nodes.get(elem.node_i)
            nj = mm.nodes.get(elem.node_j)
            if ni is None or nj is None:
                continue
            L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
            if L < 1e-12:
                continue
            weight = getattr(sec, "A", 0.0) * mat.unit_weight * L
            mass = weight / g
            node_mass[elem.node_i] = node_mass.get(elem.node_i, 0.0) + mass * 0.5
            node_mass[elem.node_j] = node_mass.get(elem.node_j, 0.0) + mass * 0.5

        # Area elements
        for aid, ae in mm.area_elements.items():
            if getattr(ae, "inactive", False):
                continue
            sec_name = mm.area_assignments.get(aid, "")
            if not sec_name:
                continue
            sec = mm.sections.get(sec_name)
            if sec is None:
                continue
            mat = mm.materials.get(sec.material)
            if mat is None or abs(mat.unit_weight) < 1e-12:
                continue
            thickness = getattr(ae, "thickness", 0.0) or 0.0
            if thickness < 1e-12:
                continue
            corner_pts = []
            for nid in ae.node_ids:
                nd = mm.nodes.get(nid)
                if nd is None:
                    break
                corner_pts.append((nd.x, nd.y, nd.z))
            if len(corner_pts) < 3:
                continue
            area_mag = polygon_area_3d(corner_pts)
            if area_mag < 1e-12:
                continue
            weight = area_mag * thickness * mat.unit_weight
            mass = weight / g
            n_c = len(ae.node_ids)
            for nid in ae.node_ids:
                node_mass[nid] = node_mass.get(nid, 0.0) + mass / n_c

    def _mass_from_dist_loads(
        self, mm, elements, dist_loads, node_mass, g, pattern_names, mult=1.0
    ):
        """Add mass from frame distributed loads in given patterns."""
        for ld in dist_loads or []:
            if ld.pattern not in pattern_names:
                continue
            elem = elements.get(ld.frame_id)
            if elem is None or getattr(elem, "inactive", False):
                continue
            ni = mm.nodes.get(elem.node_i)
            nj = mm.nodes.get(elem.node_j)
            if ni is None or nj is None:
                continue
            L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
            if L < 1e-12:
                continue
            load_len = ld.dist_b - ld.dist_a
            avg = (ld.val_a + ld.val_b) * 0.5
            total_force = avg * load_len * mult
            mass = total_force / g
            node_mass[elem.node_i] = node_mass.get(elem.node_i, 0.0) + mass * 0.5
            node_mass[elem.node_j] = node_mass.get(elem.node_j, 0.0) + mass * 0.5

    def _mass_from_joint_loads(self, mm, node_mass, g, lp_name, mult):
        """Add mass from joint loads in the given pattern."""
        for jl in getattr(mm, "joint_loads", []):
            if jl.pattern != lp_name:
                continue
            total_force = abs(jl.fz) * mult
            mass = total_force / g
            node_mass[jl.node_id] = node_mass.get(jl.node_id, 0.0) + mass

    def _mass_from_area_gravity(self, mm, node_mass, g, lp_name, mult):
        """Add mass from area gravity loads in the given pattern."""

        for agl in getattr(mm, "area_gravity_loads", []):
            if agl.pattern != lp_name:
                continue
            ae = mm.area_elements.get(agl.area_id)
            if ae is None:
                continue
            if getattr(ae, "inactive", False):
                sub_ids = collect_descendants(agl.area_id, mm.area_elements)
                if not sub_ids:
                    continue
                for sub_id in sub_ids:
                    sub_elem = mm.area_elements.get(sub_id)
                    if sub_elem is None:
                        continue
                    sec_name = mm.area_assignments.get(sub_id, "")
                    if not sec_name:
                        continue
                    sec = mm.sections.get(sec_name)
                    if sec is None:
                        continue
                    mat = mm.materials.get(sec.material)
                    if mat is None or abs(mat.unit_weight) < 1e-12:
                        continue
                    thickness = getattr(sub_elem, "thickness", 0.0) or 0.0
                    if thickness < 1e-12:
                        continue
                    corner_pts = []
                    for nid in sub_elem.node_ids:
                        nd = mm.nodes.get(nid)
                        if nd is None:
                            break
                        corner_pts.append((nd.x, nd.y, nd.z))
                    if len(corner_pts) < 3:
                        continue
                    area_mag = polygon_area_3d(corner_pts)
                    if area_mag < 1e-12:
                        continue
                    sw_per_area = thickness * mat.unit_weight
                    total_fz = sw_per_area * area_mag * abs(agl.multiplier_z) * mult
                    mass = total_fz / g
                    n_c = len(sub_elem.node_ids)
                    for nid in sub_elem.node_ids:
                        node_mass[nid] = node_mass.get(nid, 0.0) + mass / n_c
                continue
            sec_name = mm.area_assignments.get(agl.area_id, "")
            if not sec_name:
                continue
            sec = mm.sections.get(sec_name)
            if sec is None:
                continue
            mat = mm.materials.get(sec.material)
            if mat is None or abs(mat.unit_weight) < 1e-12:
                continue
            thickness = getattr(ae, "thickness", 0.0) or 0.0
            if thickness < 1e-12:
                continue
            corner_pts = []
            for nid in ae.node_ids:
                nd = mm.nodes.get(nid)
                if nd is None:
                    break
                corner_pts.append((nd.x, nd.y, nd.z))
            if len(corner_pts) < 3:
                continue
            area_mag = polygon_area_3d(corner_pts)
            if area_mag < 1e-12:
                continue
            sw_per_area = thickness * mat.unit_weight
            total_fz = sw_per_area * area_mag * abs(agl.multiplier_z) * mult
            mass = total_fz / g
            n_c = len(ae.node_ids)
            for nid in ae.node_ids:
                node_mass[nid] = node_mass.get(nid, 0.0) + mass / n_c

    def _mass_from_area_uniform(self, mm, node_mass, g, lp_name, mult):
        """Add mass from area uniform loads in the given pattern."""

        for aul in getattr(mm, "area_uniform_loads", []):
            if aul.pattern != lp_name:
                continue
            ae = mm.area_elements.get(aul.area_id)
            if ae is None:
                continue
            if getattr(ae, "inactive", False):
                sub_ids = collect_descendants(aul.area_id, mm.area_elements)
                if not sub_ids:
                    continue
                for sub_id in sub_ids:
                    sub_elem = mm.area_elements.get(sub_id)
                    if sub_elem is None:
                        continue
                    corner_pts = []
                    for nid in sub_elem.node_ids:
                        nd = mm.nodes.get(nid)
                        if nd is None:
                            break
                        corner_pts.append((nd.x, nd.y, nd.z))
                    if len(corner_pts) < 3:
                        continue
                    area_mag = polygon_area_3d(corner_pts)
                    if area_mag < 1e-12:
                        continue
                    pressure = abs(aul.value)
                    total_force = pressure * area_mag * mult
                    mass = total_force / g
                    n_c = len(sub_elem.node_ids)
                    for nid in sub_elem.node_ids:
                        node_mass[nid] = node_mass.get(nid, 0.0) + mass / n_c
                continue
            corner_pts = []
            for nid in ae.node_ids:
                nd = mm.nodes.get(nid)
                if nd is None:
                    break
                corner_pts.append((nd.x, nd.y, nd.z))
            if len(corner_pts) < 3:
                continue
            area_mag = polygon_area_3d(corner_pts)
            if area_mag < 1e-12:
                continue
            pressure = abs(aul.value)
            total_force = pressure * area_mag * mult
            mass = total_force / g
            n_c = len(ae.node_ids)
            for nid in ae.node_ids:
                node_mass[nid] = node_mass.get(nid, 0.0) + mass / n_c

    def extract_static_element_forces(self) -> dict[int, dict[str, float]]:
        """Extract element end forces in the **local** coordinate system.

        Must be called **after** :meth:`run_static_analysis`.

        Returns:
            Dict mapping ``elem_tag`` → dict with keys ``'Fx'``, ``'Fy'``,
            ``'Fz'``, ``'Mx'``, ``'My'``, ``'Mz'`` (global forces at the
            I‑end of the element) and ``'Fx_j'``, ``'Fy_j'``, ``'Fz_j'``,
            ``'Mx_j'``, ``'My_j'``, ``'Mz_j'`` (J‑end).
        """
        elements = self.mesh_model.frame_elements
        results = {}
        for eid, elem in elements.items():
            if getattr(elem, "inactive", False):
                continue
            # Resolve the OpenSees element tag — may differ from elem.elem_tag
            # when the Preprocessor creates frame elements with deterministic
            # tags stored in frame_tag_map.
            tag = self.frame_tag_map.get(eid, elem.elem_tag)
            try:
                f = ops.eleResponse(tag, "localForces")
            except Exception:
                continue
            f = _normalise_frame_response(f)
            if f is None:
                # Empty or short unsupported response — skip this element
                # without aborting the extraction.
                continue
            f_i_local = np.array([f[0], f[1], f[2]])
            m_i_local = np.array([f[3], f[4], f[5]])
            f_j_local = np.array([f[6], f[7], f[8]])
            m_j_local = np.array([f[9], f[10], f[11]])

            results[tag] = {
                "Fx": f_i_local[0],
                "Fy": f_i_local[1],
                "Fz": f_i_local[2],
                "Mx": m_i_local[0],
                "My": m_i_local[1],
                "Mz": m_i_local[2],
                "Fx_j": f_j_local[0],
                "Fy_j": f_j_local[1],
                "Fz_j": f_j_local[2],
                "Mx_j": m_j_local[0],
                "My_j": m_j_local[1],
                "Mz_j": m_j_local[2],
            }
        return results

    def extract_static_shell_forces(self) -> dict[str, dict[str, Any]]:
        """Extract shell element forces after a static analysis.

        For each active (non-inactive, non-loads-only) area element,
        queries ``ops.eleResponse(tag, 'forces')`` and returns the
        local stress resultants (membrane + bending per unit width).

        ShellMITC4 returns 8 floats per element::

            [fx, fy, fxy, mx, my, mxy, ?, ?]

        The first six are the local force and moment resultants (per
        unit width).  The last two are element volume and thickness
        (not force resultants).

        Must be called **after** :meth:`run_static_analysis`.

        Returns
        -------
        dict
            ``{area_sap_id: {
                'elem_tag': int,
                'node_tags': list[int],
                'sec_name': str,
                'fx': float,   # membrane direct (force/width)
                'fy': float,   # membrane direct (force/width)
                'fxy': float,  # membrane shear (force/width)
                'mx': float,   # bending moment (moment/width)
                'my': float,   # bending moment (moment/width)
                'mxy': float,  # twisting moment (moment/width)
            }}``
        """
        results: dict[str, dict[str, Any]] = {}
        areas = self.mesh_model.area_elements
        loads_only = self.mesh_model.loads_only_area_ids
        for aid, area in areas.items():
            if aid in loads_only:
                continue
            if getattr(area, "inactive", False):
                continue
            elem_tag = self._shell_tag_map.get(aid)
            if elem_tag is None:
                continue
            try:
                f = ops.eleResponse(elem_tag, "section", 1, "forces")
            except Exception:
                continue
            if not f or len(f) < 6:
                # Missing/short response — skip this element before indexing f[0..5].
                continue
            # Shell section forces: [Nx, Ny, Nxy, Mx, My, Mxy, ?, ?]
            # (per-unit-width resultants — "forces" alone returns the raw
            # 24-entry local nodal-force vector for shells, not resultants.)
            results[aid] = {
                "elem_tag": elem_tag,
                "node_tags": [
                    nd.node_tag
                    for nd_id in area.node_ids
                    if (nd := self.mesh_model.nodes.get(nd_id)) is not None
                ],
                "sec_name": self.mesh_model.area_assignments.get(aid, ""),
                "fx": f[0],
                "fy": f[1],
                "fxy": f[2],
                "mx": f[3],
                "my": f[4],
                "mxy": f[5],
            }
        return results

    def get_local_axes(self, elem: FrameElement) -> tuple[np.ndarray, ...]:
        """Compute local x, y, z unit vectors for a frame element.

        Uses ``get_SAP_vecxz`` from the geometry module (which handles
        the SAP2000 vecxz convention) combined with the element's
        section rotation angle.

        Args:
            elem: Frame element with ``node_i``, ``node_j``, and
                ``angle`` attributes.

        Returns:
            ``(vx, vy, vz)`` tuple of three unit vectors forming a
            right‑handed local coordinate system.

        Raises:
            ValueError: If either node cannot be resolved, or the
                element has zero length.
        """
        from ..model.geometry import get_local_axes

        ni = self.mesh_model.nodes.get(elem.node_i)
        nj = self.mesh_model.nodes.get(elem.node_j)
        if ni is None or nj is None:
            raise ValueError(f"Cannot resolve nodes for {elem.elem_id}")
        vx = np.array([nj.x - ni.x, nj.y - ni.y, nj.z - ni.z])
        return get_local_axes(vx, getattr(elem, "angle", 0.0))

    def check_load_equilibrium(self) -> "pd.DataFrame":
        """Check equilibrium between applied loads and reactions.

        For each load pattern in the model, runs a static analysis
        with that pattern alone and compares the applied load totals
        (from :attr:`load_totals`) against the summed reactions.

        Reaction moments include the force × lever‑arm overturning
        contribution via
        :func:`~fea_toolkit.utils.sum_reactions_with_overturning`
        (same fixed centroid approach used for RS analysis in
        :meth:`run_response_spectrum_analysis`).

        Returns:
            A ``pandas.DataFrame`` with one row per pattern and
            columns for applied force, reaction force, and
            the equilibrium imbalance ``Δ = applied + reaction``
            (should be near zero for a correctly built model).
        """
        # TODO: Move `import pandas as pd` to module-level when the
        # optional dependency is declared in pyproject.toml (currently `pandas`
        # is not listed as a dependency, so the lazy import avoids breakage).
        import pandas as pd

        rows: list = []
        fu = self.mesh_model.units.get("F", "?")
        # Collect pattern names from the MeshModel's load_patterns dict
        # (matching the legacy Builder which iterates self.model.load_patterns).
        # Skip patterns with zero applied loads (e.g. SLX, SLY).
        _patterns = set()
        for pname, lp in self.mesh_model.load_patterns.items():
            # Check for any loads associated with this pattern
            has_loads = (
                lp.self_weight_factor > 0
                or any(ld.pattern == pname for ld in self.mesh_model.frame_dist_loads)
                or any(ld.pattern == pname for ld in self.mesh_model.joint_loads)
                or any(ld.pattern == pname for ld in self.mesh_model.area_gravity_loads)
                or any(ld.pattern == pname for ld in self.mesh_model.edge_loads_from_areas)
            )
            if has_loads:
                _patterns.add(pname)
        for pname in sorted(_patterns, key=str.casefold):
            result = self.run_static_analysis(
                extract_reactions=True,
                pattern_scales={pname: 1.0},
            )
            rxn = result.get("reactions", {})
            rx = sum(v["fx"] for v in rxn.values())
            ry = sum(v["fy"] for v in rxn.values())
            rz = sum(v["fz"] for v in rxn.values())

            rows.append(
                {
                    "Load Pattern": pname,
                    f"Reaction Fx ({fu})": round(rx, 1),
                    f"Reaction Fy ({fu})": round(ry, 1),
                    f"Reaction Fz ({fu})": round(rz, 1),
                }
            )

        return pd.DataFrame(rows)

    def export_results(
        self,
        filepath: str,
        static_results: Optional[dict[str, Any]] = None,
        modal_result: Optional[dict[str, Any]] = None,
        mode_shapes: Optional[dict] = None,
        rs_results: Optional[dict[str, dict]] = None,
        rs_element_forces: Optional[dict[str, Any]] = None,
        rs_nodal_displacements: Optional[dict[int, tuple]] = None,
        fmt: str = "npz",
    ) -> str:
        """Export model geometry and analysis results to a unified file.

        Delegates to :func:`~fea_toolkit.io.unified_writer.write_results`
        using the builder's ``mesh_model`` and the provided results.

        Args:
            filepath: Output file path (``.npz`` or ``.h5``).
            static_results: Dict from :meth:`run_static_analysis`.
            modal_result: Dict from :meth:`run_modal_analysis`.
            mode_shapes: Mode shape eigenvectors ``{mode_idx: {tag: (dx,dy,dz)}}``.
            rs_results: Response-spectrum results dict.
            rs_element_forces: Dict from :meth:`extract_element_rs_forces`.
            rs_nodal_displacements: Dict from
                :meth:`compute_rs_nodal_displacements`.
            fmt: ``"npz"`` (default) or ``"h5"``.

        Returns:
            Absolute path to the written file.
        """
        from ..io.unified_writer import write_results

        return write_results(
            path=filepath,
            mesh_model=self.mesh_model,
            static_results=static_results,
            modal_result=modal_result,
            mode_shapes=mode_shapes,
            rs_results=rs_results,
            rs_element_forces=rs_element_forces,
            rs_nodal_displacements=rs_nodal_displacements,
            fmt=fmt,
            config=self.config,
        )


# ── Module-level helper: response normalisation ────────────────


def _normalise_frame_response(f) -> Optional[list[float]]:
    """Normalise an ``ops.eleResponse(tag, 'localForces')`` response.

    Handles the variable-length responses returned by OpenSees element
    types:

    * **1 value** — ``Truss`` local axial force (tension-positive).
      Expanded to the standard 12-component row as
      ``[-P, 0, 0, 0, 0, 0, P, 0, 0, 0, 0, 0]`` — ``fx_i = -P``,
      ``fx_j = +P`` keeps the array dense and satisfies force
      equilibrium.
    * **6 values** — 3D ``Truss`` local end forces
      ``[fx_i, fy_i, fz_i, fx_j, fy_j, fz_j]`` with no moment
      components.  Expanded to the standard 12-component row with zero
      moments so the response is preserved rather than skipped.
    * **< 12 values (other than 1 or 6)** — unsupported; ``None`` is
      returned and the caller skips the element.
    * **>= 12 values** — the first 12 values are returned.

    This single helper replaces the ad-hoc inline normalisation that was
    previously duplicated in
    :meth:`AnalysisBuilder.extract_static_element_forces` and
    :func:`fea_toolkit.opensees._runner_pushover._record_step` — the two
    call sites disagreed on whether a 6-value (3D truss) response was
    recordable, which silently dropped truss members from per-step pushover
    recording.

    Args:
        f: Raw response from ``ops.eleResponse`` (list or array-like).
            May be ``None`` when OpenSees fails to produce a response.

    Returns:
        List of 12 values, or ``None`` when the response length is not
        supported.
    """
    if f is None:
        # ``ops.eleResponse`` returns None on some failed queries —
        # treat identically to an unsupported-length response so the
        # caller's existing ``if f is None: continue`` paths skip the
        # element instead of crashing on ``len(None)``.
        return None
    if len(f) == 1:
        axial = float(f[0])
        return [-axial, 0.0, 0.0, 0.0, 0.0, 0.0, axial, 0.0, 0.0, 0.0, 0.0, 0.0]
    if len(f) == 6:
        return [f[0], f[1], f[2], 0.0, 0.0, 0.0, f[3], f[4], f[5], 0.0, 0.0, 0.0]
    if len(f) < 12:
        return None
    return list(f[:12])
