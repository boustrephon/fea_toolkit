"""Analysis-builder mixin: analysis runners and result extraction."""

import contextlib
import logging
import math
from typing import TYPE_CHECKING, Any, Optional, Union

import numpy as np
import openseespy.opensees as ops

from ..model.geometry import get_local_axes, polygon_area_3d
from ..model.sap_data import FrameElement, ShellSection
from ..model.tree_utils import collect_descendants
from ..utils import cqc_combine, g_from_units

if TYPE_CHECKING:
    # pandas is not a required dependency — imported only at runtime
    # inside check_load_equilibrium().
    import pandas as pd

    from .analysis_builder import AnalysisBuilder

logger = logging.getLogger(__name__)


class RunnerMixin:
    """Analysis execution, result extraction, and serialization (static / modal / RS / pushover)."""

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
            fb_test_tol = max(_fb_total_mass * _g * 1e-6, test_tol * 10.0)
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

            total_reaction_fz = 0.0
            for nid, restraint in self.mesh_model.restraints.items():
                # Full fixity only (6 DOFs all True)
                if not all(restraint.dofs):
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

    def run_modal_analysis(
        self, num_modes: int = 30, print_results: bool = True, eigen_solver: str = "default"
    ) -> dict[str, Any]:
        """Run eigenvalue / modal analysis and return results.

        Requires that seismic masses have been assigned (call
        :meth:`compute_seismic_masses` first) and the domain has been
        built via :meth:`build_domain`.

        Args:
            num_modes: Number of eigenvalues to solve for.
            print_results: If True, print a modal properties table.
            eigen_solver: Solver strategy.

                ``"default"``
                    ARPACK (fast), fallback to fullGenLapack.
                ``"fullGenLapack"``
                    Robust but slow for large models.
                ``"genBandArpack"``
                    Generalized banded ARPACK — requires a Ritz pre-step.
                ``"symmBandLapack"``
                    Symmetric banded Lapack solver.
                ``"ritz"``
                    Gravity pre-step then ARPACK.

        Returns:
            Dictionary with keys:

            * ``'eigenvalues'`` — list of eigenvalues (omega^2).
            * ``'periods'`` — list of natural periods (s).
            * ``'frequencies'`` — list of natural frequencies (Hz).
            * ``'modal_props'`` — the full ``ops.modalProperties()`` dict.
            * ``'num_modes'`` — number of converged modes.
            * ``'nodal_masses'`` — dict of nodal masses ``{tag: (mx, my, mz)}``
                in model units (tonnes for kN-m models).
        """
        if self.config.get("verbose"):
            print(f"Running modal analysis for {num_modes} modes...")

        # ── Ensure seismic masses are present ────────────────────
        _has_mass = False
        for t in ops.getNodeTags():
            try:
                m = ops.nodeMass(t)
                if sum(abs(x) for x in m) > 1e-12:
                    _has_mass = True
                    break
            except Exception:
                pass
        if not _has_mass:
            self.compute_seismic_masses()

        # ── Ritz / pre-load nudge ────────────────────────────────
        _needs_nudge = eigen_solver in ("genBandArpack", "ritz")
        if _needs_nudge:
            if self.config.get("verbose"):
                print("  Ritz pre-step (static gravity)...")
            # Run a self-weight gravity load step
            self.create_loads(pattern_scales={"Self weight": 1.0})
            try:
                if self._edge_constraint_method == "penalty":
                    ops.constraints("Penalty", 1.0e12, 1.0e12)
                else:
                    ops.constraints("Transformation")
                ops.numberer("RCM")
                ops.system(self.config.get("solver_system", "BandGen"))
                ops.test("NormDispIncr", 1e-3, 5, 0)
                _algorithms = ["Newton", "NewtonLineSearch", "ModifiedNewton", "KrylovNewton"]
                _ok = -1
                for _alg in _algorithms:
                    try:
                        ops.algorithm(_alg)
                    except Exception:
                        continue
                    ops.integrator("LoadControl", 1.0)
                    ops.analysis("Static")
                    _ok = ops.analyze(1)
                    if _ok == 0:
                        break
                if _ok != 0 and self.config.get("verbose"):
                    print("  ⚠ Ritz pre-step did not converge — continuing with zero initial state")
            except Exception:
                if self.config.get("verbose"):
                    print("  ⚠ Ritz pre-step failed — continuing")

        # ── Set constraint handler for eigen analysis ────────────
        try:
            if self._edge_constraint_method == "penalty":
                ops.constraints("Penalty", 1.0e12, 1.0e12)
            else:
                ops.constraints(self.config.get("solver_constraints", "Transformation"))
            ops.numberer("RCM")
            ops.system(self.config.get("solver_system", "BandGen"))
        except Exception:
            pass

        # ── Eigenvalue solver ────────────────────────────────────
        eigenvals_all = []
        _solver_map = {
            "genBandArpack": "-genBandArpack",
            "symmBandLapack": "-symmBandLapack",
            "fullGenLapack": "-fullGenLapack",
            "default": None,
        }
        solver_flag = _solver_map.get(eigen_solver)
        if solver_flag is not None:
            try:
                eigenvals_all = ops.eigen(solver_flag, num_modes)
            except Exception:
                eigenvals_all = []
            if not eigenvals_all:
                print(f"  ⚠ {eigen_solver} solver failed — falling back to ARPACK")
                try:
                    eigenvals_all = ops.eigen(num_modes)
                except Exception:
                    eigenvals_all = []
                if not eigenvals_all:
                    print("  ⚠ ARPACK also failed — falling back to fullGenLapack")
                    try:
                        eigenvals_all = ops.eigen("-fullGenLapack", num_modes)
                    except Exception:
                        eigenvals_all = []
        else:
            try:
                eigenvals_all = ops.eigen(num_modes)
            except Exception:
                eigenvals_all = []
            if not eigenvals_all:
                print("  ⚠ ARPACK solver failed — falling back to fullGenLapack")
                try:
                    eigenvals_all = ops.eigen("-fullGenLapack", num_modes)
                except Exception:
                    eigenvals_all = []

        eigenvals = [ev for ev in eigenvals_all if ev > 1e-12]
        n_modes = len(eigenvals)
        if n_modes < num_modes and self.config.get("verbose"):
            print(
                f"  Warning: only {n_modes} positive eigenvalues out of "
                f"{num_modes}.  Proceeding with {n_modes} modes."
            )

        periods = [2.0 * math.pi / math.sqrt(ev) for ev in eigenvals]
        frequencies = [math.sqrt(ev) / (2.0 * math.pi) for ev in eigenvals]

        try:
            modal_props = ops.modalProperties("-return", "-unorm")
        except Exception:
            modal_props = {}

        results = {
            "eigenvalues": eigenvals,
            "periods": periods,
            "frequencies": frequencies,
            "modal_props": modal_props,
            "num_modes": n_modes,
            "nodal_masses": self._query_nodal_masses(),
        }

        if print_results:
            print("\n===== MODAL ANALYSIS =====")
            if modal_props:
                try:
                    total_mass = modal_props.get("totalFreeMass", [0])[0]
                    print(f"Total translational mass (free DOFs): {total_mass:.2f} tonnes\n")
                    header = (
                        f"{'Mode':>5} {'Freq(Hz)':>10} {'Period(s)':>10} "
                        f"{'Mx(t)':>12} {'My(t)':>12} {'Mz(t)':>12} "
                        f"{'%X':>7} {'%Y':>7} {'%Z':>7}"
                    )
                    print(header)
                    print("-" * len(header))
                    for i in range(n_modes):
                        mx = modal_props.get("partiMassMX", [0] * n_modes)[i]
                        my = modal_props.get("partiMassMY", [0] * n_modes)[i]
                        mz = modal_props.get("partiMassMZ", [0] * n_modes)[i]
                        rx = modal_props.get("partiMassRatiosMX", [0] * n_modes)[i]
                        ry = modal_props.get("partiMassRatiosMY", [0] * n_modes)[i]
                        rz = modal_props.get("partiMassRatiosMZ", [0] * n_modes)[i]
                        print(
                            f"{i + 1:5d} {frequencies[i]:10.4f} "
                            f"{periods[i]:10.4f} {mx:12.2f} {my:12.2f} "
                            f"{mz:12.2f} {rx:6.2f}% {ry:6.2f}% {rz:6.2f}%"
                        )
                except Exception:
                    pass
            else:
                print(f"{'Mode':>5} {'Period(s)':>10} {'Freq(Hz)':>10}")
                print("-" * 30)
                for i in range(n_modes):
                    print(f"{i + 1:5d} {periods[i]:10.4f} {frequencies[i]:10.4f}")

        return results

    def run_response_spectrum_analysis(
        self,
        num_modes: int,
        modal_periods: list[float],
        spectrum_periods: list[float],
        spectrum_accels: list[float],
        direction: str = "X",
        damping_ratio: float = 0.05,
        T_rigid: Optional[float] = None,
        print_results: bool = True,
    ) -> dict[str, Any]:
        """Run a response‑spectrum analysis using CQC modal combination.

        Performs mode‑by‑mode RS analysis using OpenSees'
        ``responseSpectrumAnalysis``, then combines with CQC.

        **How base reactions are computed**

        ``ops.responseSpectrumAnalysis(... '-mode', mode)`` sets the
        modal displacement field for a single mode on the domain (via
        ``node->setTrialDisp()``).  After ``commitDomain()``, element
        internal forces are consistent with those displacements.  We then
        query ``ops.eleResponse(eid, 'forces')`` which returns **global
        element‑end forces** ``[Fx, Fy, Fz, Mx, My, Mz]`` at the I-end
        then J-end.

        For each base-connected element we extract the base-end forces
        and accumulate into a per-mode 6-DoF reaction vector.  The
        element-end moments include column bending directly, but the
        **axial force lever‑arm** (Fz from one column × distance to the
        centroid of another) is a structural‑level effect not present in
        individual element stiffness outputs.  We add it here:

        ``mx += Mx_direct + Fz·dy − Fy·dz``
        ``my += My_direct + Fx·dz − Fz·dx``

        using the **fixed geometric centroid** (bounding-box midpoint
        ``(min+max)/2`` of all nodes).  This fixed reference is the same
        as :func:`~fea_toolkit.utils.sum_reactions_with_overturning`
        uses for static lateral loads — ensuring consistent moment
        origins across all analysis types.

        .. note::
           A fixed reference point is **required** for CQC combination:
           if each mode used its own Fz-weighted centroid, the per-mode
           moments would reference different points and CQC would be
           physically invalid.

        Args:
            num_modes: Number of modes to include.
            modal_periods: Natural periods of each mode (s).
            spectrum_periods: Period axis of the response spectrum (s).
            spectrum_accels: Spectral acceleration values (m/s^2).
            direction: Excitation direction — ``'X'``, ``'Y'``, or ``'Z'``.
            damping_ratio: Damping ratio for CQC correlation.
            T_rigid: Rigid cut-off period (s). ``None`` = no cut-off.
            print_results: If True, print a summary table.

        Returns:
            Dictionary with:
            - ``modal_base_shear`` / ``modal_base_moment`` (scalar, backward compat)
            - ``base_shear_cqc`` / ``base_shear_srss`` / ``base_moment_cqc`` / ``base_moment_srss``
            - ``modal_periods``
            - ``modal_base_reactions`` (list of 6-DoF dicts per mode)
            - ``base_reactions_cqc`` / ``base_reactions_srss`` (6-DoF combined)
                where Mx/My include overturning from Fz × lever-arm about
                the fixed geometric centroid (bounding-box midpoint).
                This fixed reference ensures CQC validity across modes.
        """
        if self.config.get("verbose"):
            print(f"Running response spectrum analysis (dir={direction})...")

        num_modes = min(num_modes, len(modal_periods))
        if num_modes == 0:
            raise ValueError("No modal periods available for RS analysis")

        omega = [2.0 * math.pi / T if T > 0 else 0.0 for T in modal_periods]
        damp_ratios = [damping_ratio] * num_modes

        SPECTRUM_TS_TAG = 9999
        with contextlib.suppress(Exception):
            ops.remove("timeSeries", SPECTRUM_TS_TAG)
        ops.timeSeries(
            "Path", SPECTRUM_TS_TAG, "-time", *spectrum_periods, "-values", *spectrum_accels
        )

        modal_base_shear = []
        modal_base_moment = []
        dof = {"X": 1, "Y": 2, "Z": 3}[direction]

        dof_idx = {"X": 0, "Y": 1, "Z": 2}[direction]
        base_nodes = {
            nid
            for nid, r in self.mesh_model.restraints.items()
            if len(r.dofs) > dof_idx and r.dofs[dof_idx] == 1
        }

        elements = self.mesh_model.frame_elements
        base_elements = []
        for eid, elem in elements.items():
            if getattr(elem, "inactive", False):
                continue
            nd_i = self.mesh_model.nodes.get(elem.node_i)
            nd_j = self.mesh_model.nodes.get(elem.node_j)
            if nd_i is None or nd_j is None:
                continue
            # Use the actual OpenSees tag from the frame_tag_map so that
            # split-frame children are addressed by their correct tag.
            ops_tag = self.frame_tag_map.get(eid, elem.elem_tag)
            if elem.node_i in base_nodes and elem.node_j not in base_nodes:
                base_elements.append((ops_tag, "i"))
            elif elem.node_j in base_nodes and elem.node_i not in base_nodes:
                base_elements.append((ops_tag, "j"))

        # ── Pre-compute fixed reference point for overturning moment ──
        # Compute from base (support) nodes only — the centre of the base
        # footprint. This ensures a consistent reference across all modes
        # for valid CQC combination.  Same approach as
        # sum_reactions_with_overturning in utils.py.
        _base_nds = [
            self.mesh_model.nodes[nid] for nid in base_nodes if nid in self.mesh_model.nodes
        ]
        if _base_nds:
            _xs = [n.x for n in _base_nds]
            _ys = [n.y for n in _base_nds]
            _cx = (min(_xs) + max(_xs)) * 0.5
            _cy = (min(_ys) + max(_ys)) * 0.5
            _z_base = sum(n.z for n in _base_nds) / len(_base_nds)
        else:
            _cx = _cy = _z_base = 0.0

        # Pre-compute base-element node coordinates for lever-arm
        # Build a one-time tag-to-element index (ops tag → element)
        _elem_by_tag: dict = {}
        for _e in elements.values():
            _elem_by_tag[_e.elem_tag] = _e

        _base_elem_coords = []
        for eid, end in base_elements:
            elem = elements.get(str(eid)) or _elem_by_tag.get(eid)
            if elem is None:
                continue
            nid = elem.node_i if end == "i" else elem.node_j
            nd = self.mesh_model.nodes.get(nid)
            if nd is None:
                continue
            _base_elem_coords.append((eid, end, nd.x, nd.y, nd.z))

        modal_base_reactions = []
        for mode in range(1, num_modes + 1):
            ops.responseSpectrumAnalysis(SPECTRUM_TS_TAG, dof, "-mode", mode)

            rxn = {"fx": 0.0, "fy": 0.0, "fz": 0.0, "mx": 0.0, "my": 0.0, "mz": 0.0}
            for eid, end, nx, ny, nz in _base_elem_coords:
                try:
                    forces = ops.eleResponse(eid, "forces")
                except Exception:
                    continue
                if end == "i":
                    fx, fy, fz, mx, my, mz = (
                        forces[0],
                        forces[1],
                        forces[2],
                        forces[3],
                        forces[4],
                        forces[5],
                    )
                else:
                    fx, fy, fz, mx, my, mz = (
                        forces[6],
                        forces[7],
                        forces[8],
                        forces[9],
                        forces[10],
                        forces[11],
                    )

                rxn["fx"] += fx
                rxn["fy"] += fy
                rxn["fz"] += fz
                # Overturning: direct moment + force × lever-arm about fixed reference
                dx = nx - _cx
                dy = ny - _cy
                dz = nz - _z_base
                rxn["mx"] += mx + fz * dy - fy * dz
                rxn["my"] += my + fx * dz - fz * dx
                rxn["mz"] += mz + fy * dx - fx * dy

            modal_base_reactions.append(rxn)

        # ── CQC / SRSS per component ───────────────────────────
        dof_map = {"X": (0, 4), "Y": (1, 3), "Z": (2, 4)}
        #   X: shear=fx(idx 0), overturning=my(idx 4)
        #   Y: shear=fy(idx 1), overturning=mx(idx 3)  ← was mz before fix
        #   Z: shear=fz(idx 2), overturning=my(idx 4)
        f_idx, m_idx = dof_map[direction]
        comp_order = ["fx", "fy", "fz", "mx", "my", "mz"]

        # Keep scalar arrays for backward compat
        modal_base_shear = [r[comp_order[f_idx]] for r in modal_base_reactions]
        modal_base_moment = [r[comp_order[m_idx]] for r in modal_base_reactions]

        base_reactions_cqc = {}
        base_reactions_srss = {}
        for comp in comp_order:
            vals = [r[comp] for r in modal_base_reactions]
            base_reactions_cqc[comp] = cqc_combine(vals, omega, damp_ratios)
            base_reactions_srss[comp] = math.sqrt(sum(v * v for v in vals))

        base_shear_cqc = base_reactions_cqc[comp_order[f_idx]]
        base_shear_srss = base_reactions_srss[comp_order[f_idx]]
        base_moment_cqc = base_reactions_cqc[comp_order[m_idx]]
        base_moment_srss = base_reactions_srss[comp_order[m_idx]]

        result = {
            "modal_base_shear": modal_base_shear,
            "modal_base_moment": modal_base_moment,
            "base_shear_cqc": base_shear_cqc,
            "base_shear_srss": base_shear_srss,
            "base_moment_cqc": base_moment_cqc,
            "base_moment_srss": base_moment_srss,
            "modal_periods": modal_periods,
            # New: full 6-DoF base reactions per-mode and combined
            "modal_base_reactions": modal_base_reactions,
            "base_reactions_cqc": base_reactions_cqc,
            "base_reactions_srss": base_reactions_srss,
        }

        if print_results:
            print(f"\n===== RESPONSE SPECTRUM ({direction}) =====")
            print(f"{'Mode':>5} {'Period(s)':>10} {'Shear (kN)':>14} {'Moment (kN-m)':>16}")
            print("-" * 48)
            for i, (T, v, m) in enumerate(
                zip(modal_periods[:num_modes], modal_base_shear, modal_base_moment)
            ):
                print(f"{i + 1:5d} {T:10.4f} {v:14.2f} {m:16.2f}")
            print("-" * 48)
            print(f"{'CQC':>5} {'':>10} {base_shear_cqc:14.2f} {base_moment_cqc:16.2f}")
            print(f"{'SRSS':>5} {'':>10} {base_shear_srss:14.2f} {base_moment_srss:16.2f}")
            print()

        return result

    def extract_element_rs_forces(
        self,
        num_modes: int,
        modal_periods: list[float],
        spectrum_periods: list[float],
        spectrum_accels: list[float],
        direction: str = "X",
        damping_ratio: float = 0.05,
        print_results: bool = True,
    ) -> dict[str, Any]:
        """Run RS analysis and return CQC‑combined element forces sorted by height.

        For each element this returns the CQC‑combined moments (My_i, My_j,
        Mz_i, Mz_j) and the corresponding shears derived from the moment
        gradient (Vy = dMz/dx, Vz = dMy/dx).

        The parameters mirror :meth:`run_response_spectrum_analysis`.

        Returns:
            Dictionary with keys:

            * ``'element_results'`` — list of dicts sorted by elevation, each
                containing ``elem_id``, ``z_bot``, ``z_mid``, ``Vy_i``, ``Vy_j``,
                ``Vz_i``, ``Vz_j``, ``My_i``, ``My_j``, ``Mz_i``, ``Mz_j``.
            * ``'modal_periods'``, ``'omega'`` — for diagnostics.
        """
        if self.config.get("verbose"):
            print("Extracting element RS forces...")

        omega = [2.0 * math.pi / T if T > 0 else 0.0 for T in modal_periods]
        damp_ratios = [damping_ratio] * num_modes

        dof = {"X": 1, "Y": 2, "Z": 3}[direction]

        SPECTRUM_TS_TAG = 9999

        elements = self.mesh_model.frame_elements

        # Pre-compute element info + storage
        elem_data = {}
        for eid, elem in elements.items():
            if getattr(elem, "inactive", False):
                continue
            ni = self.mesh_model.nodes.get(elem.node_i)
            nj = self.mesh_model.nodes.get(elem.node_j)
            if ni is None or nj is None:
                continue
            z_i, z_j = ni.z, nj.z
            if z_i > z_j:
                z_i, z_j = z_j, z_i
            ops_tag = self.frame_tag_map.get(eid, elem.elem_tag)
            elem_data[eid] = {
                "tag": ops_tag,
                "elem_id": eid,
                "z_bot": z_i,
                "z_mid": (z_i + z_j) * 0.5,
                "My_i": [],
                "My_j": [],
                "Mz_i": [],
                "Mz_j": [],
            }

        # Mode-by-mode extraction
        for mode in range(1, num_modes + 1):
            ops.responseSpectrumAnalysis(SPECTRUM_TS_TAG, dof, "-mode", mode)
            for eid, ed in elem_data.items():
                try:
                    forces = ops.eleResponse(ed["tag"], "forces")
                except Exception:
                    forces = [0.0] * 12
                ed["My_i"].append(forces[4])
                ed["My_j"].append(forces[10])
                ed["Mz_i"].append(forces[5])
                ed["Mz_j"].append(forces[11])

        # CQC combine per element and compute shears
        element_results = []
        for eid, ed in elem_data.items():
            ne = len(ed["My_i"])
            n_use = min(ne, num_modes)
            o_use = omega[:n_use]
            d_use = damp_ratios[:n_use]

            My_i = cqc_combine(ed["My_i"][:n_use], o_use, d_use)
            My_j = cqc_combine(ed["My_j"][:n_use], o_use, d_use)
            Mz_i = cqc_combine(ed["Mz_i"][:n_use], o_use, d_use)
            Mz_j = cqc_combine(ed["Mz_j"][:n_use], o_use, d_use)

            # Element length
            elem = elements.get(eid)
            if elem:
                ni = self.mesh_model.nodes.get(elem.node_i)
                nj = self.mesh_model.nodes.get(elem.node_j)
                L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z) if ni and nj else 1.0
            else:
                L = 1.0

            # Shear from moment gradient
            Vy_i = (Mz_i - Mz_j) / L if L > 1e-12 else 0.0
            Vy_j = Vy_i
            Vz_i = (My_i - My_j) / L if L > 1e-12 else 0.0
            Vz_j = Vz_i

            element_results.append(
                {
                    "elem_id": ed["elem_id"],
                    "z_bot": ed["z_bot"],
                    "z_mid": ed["z_mid"],
                    "Vy_i": Vy_i,
                    "Vy_j": Vy_j,
                    "Vz_i": Vz_i,
                    "Vz_j": Vz_j,
                    "My_i": My_i,
                    "My_j": My_j,
                    "Mz_i": Mz_i,
                    "Mz_j": Mz_j,
                }
            )

        # Sort by height
        element_results.sort(key=lambda r: r["z_mid"])

        if print_results:
            print(
                f"\n===== RESPONSE SPECTRUM RESULTS ({direction} only, CQC) FOR ALL ELEMENTS ====="
            )
            header = (
                f"{'Elem':>30} {'Z_bot(m)':>10} {'Z_mid(m)':>10} {'End':>5} "
                f"{'Vy (kN)':>12} {'Vz (kN)':>12} {'My (kN-m)':>12} {'Mz (kN-m)':>12}"
            )
            print(header)
            print("-" * len(header))
            for r in element_results:
                eid_str = f"{r['elem_id']:30s}"
                print(
                    f"{eid_str} {r['z_bot']:10.2f} {r['z_mid']:10.2f} {'I':>5} "
                    f"{r['Vy_i']:12.2f} {r['Vz_i']:12.2f} {r['My_i']:12.2f} {r['Mz_i']:12.2f}"
                )
                print(
                    f"{eid_str} {r['z_bot']:10.2f} {r['z_mid']:10.2f} {'J':>5} "
                    f"{r['Vy_j']:12.2f} {r['Vz_j']:12.2f} {r['My_j']:12.2f} {r['Mz_j']:12.2f}"
                )

        return {
            "element_results": element_results,
            "modal_periods": modal_periods,
            "omega": omega,
        }

    def compute_rs_nodal_displacements(
        self,
        num_modes: int,
        modal_periods: list[float],
        eigenvalues: list[float],
        spectrum_func,
        direction: str = "X",
        damping_ratio: float = 0.05,
        return_srss: bool = False,
    ) -> Union[
        dict[int, tuple[float, float, float]],
        tuple[dict[int, tuple[float, float, float]], dict[int, tuple[float, float, float]]],
    ]:
        """Compute CQC‑ (and optionally SRSS‑) combined peak nodal
        displacements from RS analysis.

        Uses mode‑shape superposition rather than re‑running the RS analysis:

            u_m = Γ_m · φ_m · Sa_m / ω²_m

        then combined with CQC (and optionally SRSS).

        Args:
            num_modes: Number of modes.
            modal_periods: Natural periods of each mode (s).
            eigenvalues: Eigenvalues (ω²) from :meth:`run_modal_analysis`.
            spectrum_func: Callable ``f(T) → Sa`` in **m/s²**.
            direction: Excitation direction ``'X'``, ``'Y'``, or ``'Z'``.
            damping_ratio: Damping ratio for CQC correlation.
            return_srss: If True, return ``(cqc_result, srss_result)``
                as a tuple of two dicts.  If False (default), return
                only ``cqc_result`` for backward compatibility.

        Returns:
            Dict mapping ``node_tag`` → ``(dx, dy, dz)`` in model length
            units.  When ``return_srss=True``, returns a tuple of two
            such dicts: ``(cqc, srss)``.
        """
        dof = {"X": 1, "Y": 2, "Z": 3}[direction]
        dof_idx = dof - 1

        # Get participation factors from modalProperties
        try:
            mp = ops.modalProperties("-return", "-unorm")
        except Exception:
            mp = {}
        mass_key = (
            "partiMassMX"
            if direction == "X"
            else "partiMassMY"
            if direction == "Y"
            else "partiMassMZ"
        )
        eff_masses = mp.get(mass_key, [0.0] * num_modes)

        omega = [2.0 * math.pi / T if T > 0 else 0.0 for T in modal_periods]
        damp = [damping_ratio] * num_modes

        node_tags = list(ops.getNodeTags())

        per_mode = {tag: {d: [] for d in range(3)} for tag in node_tags}

        for m in range(num_modes):
            if eigenvalues[m] <= 1e-12 or omega[m] <= 1e-12:
                for tag in node_tags:
                    for d in range(3):
                        per_mode[tag][d].append(0.0)
                continue

            T = modal_periods[m]
            Sa = spectrum_func(T)
            Gamma = math.sqrt(abs(eff_masses[m])) if eff_masses[m] != 0 else 0.0
            factor = Gamma * Sa / (omega[m] ** 2)

            if abs(factor) < 1e-15:
                for tag in node_tags:
                    for d in range(3):
                        per_mode[tag][d].append(0.0)
                continue

            for tag in node_tags:
                phi = ops.nodeEigenvector(tag, m + 1, dof)
                per_mode[tag][dof_idx].append(phi * factor)
                for d in range(3):
                    if d != dof_idx:
                        per_mode[tag][d].append(0.0)

        cqc_result = {}
        srss_result = {}
        for tag in node_tags:
            cqc_vals = tuple(cqc_combine(per_mode[tag][d], omega, damp) for d in range(3))
            cqc_result[tag] = cqc_vals
            srss_vals = tuple(math.sqrt(sum(v * v for v in per_mode[tag][d])) for d in range(3))
            srss_result[tag] = srss_vals

        if return_srss:
            return cqc_result, srss_result
        return cqc_result

    def extract_mode_shapes(
        self, num_modes: int
    ) -> dict[int, dict[int, tuple[float, float, float]]]:
        """Extract mode shape displacements for each node and each mode.

        Must be called **after** :meth:`run_modal_analysis`.

        Args:
            num_modes: Number of modes to extract.

        Returns:
            ``{mode_index: {node_tag: (dx, dy, dz)}}`` where *mode_index*
            is 0‑based and displacements are raw eigenvector components.
        """
        node_tags = list(ops.getNodeTags())
        dof_map = {0: 1, 1: 2, 2: 3}
        shapes: dict[int, dict[int, tuple]] = {}
        for m in range(num_modes):
            mode_num = m + 1
            per_node: dict[int, tuple] = {}
            for tag in node_tags:
                dx = ops.nodeEigenvector(tag, mode_num, dof_map[0])
                dy = ops.nodeEigenvector(tag, mode_num, dof_map[1])
                dz = ops.nodeEigenvector(tag, mode_num, dof_map[2])
                per_node[tag] = (dx, dy, dz)
            shapes[m] = per_node
        return shapes

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

    def run_pushover_analysis(
        self,
        gravity_patterns: dict[str, float],
        lateral_load_type: str = "uniform",
        lateral_pattern_name: Optional[str] = None,
        lateral_point_nodes: Optional[list[int]] = None,
        lateral_direction: str = "X",
        control_node_tag: Optional[int] = None,
        max_disp: float = 0.5,
        num_steps: int = 100,
        fundamental_period: Optional[float] = None,
        mode_shapes: Optional[dict] = None,
        mode_index: int = 0,
        node_mass_overrides: Optional[dict[str, float]] = None,
        print_progress: bool = True,
        record_element_forces: bool = False,
    ) -> dict[str, Any]:
        """Run a displacement‑controlled pushover analysis.

        **Two‑stage process:**

        1. **Gravity** — apply the specified gravity patterns via
           :meth:`run_static_analysis` with ``extract_reactions=True``.
        2. **Lateral push** — lock gravity, apply lateral loads, then
           push a control node in increments using
           ``DisplacementControl`` integration.

        Five lateral load types are supported:

        * ``'uniform'`` — mass‑proportional acceleration (uniform
          acceleration of the structure).
        * ``'triangular'`` — load proportional to :math:`m_i h_i^k`
          per ASCE 7 equivalent lateral force.
        * ``'mode1'`` — load proportional to the fundamental
          eigenvector :math:`\\mathbf{M} \\boldsymbol{\\phi}_1`
          (modal pushover).
        * ``'pattern'`` — read an existing SAP2000 load pattern
          (frame distributed loads) from the model data.
        * ``'point'`` — a unit point load at the node(s) given by
          *lateral_point_nodes* (default: the control node).  A single
          point load reproduces the Duong et al. (2007) and Vecchio &
          Emara (1992) test setups, which pushed the top beam with one
          actuator.

        Args:
            gravity_patterns: Dict mapping load pattern name → scale
                factor for gravity loads, e.g. ``{"DEAD": 1.0}``.
            lateral_load_type: ``'uniform'``, ``'triangular'``,
                ``'mode1'``, ``'pattern'``, or ``'point'``.
            lateral_pattern_name: SAP2000 load pattern name (required
                when *lateral_load_type* is ``'pattern'``).
            lateral_point_nodes: OpenSees node tags loaded by the
                ``'point'`` type (each receives a unit load in the push
                direction).  ``None`` → the control node only.
            lateral_direction: Push direction — ``'X'``, ``'Y'``, or
                ``'Z'``.
            control_node_tag: OpenSees node tag for displacement
                control.  ``None`` = auto‑select (highest unrestrained
                node in the push direction).
            max_disp: Target displacement at the control node (m).
            num_steps: Number of push steps.
            fundamental_period: Fundamental period (s) for
                ``'triangular'`` load exponent ``k``.  ``None`` uses
                the period of the first mode from the model.
            mode_shapes: Dict ``{mode_idx: {node_tag: (dx, dy, dz)}}``
                from :meth:`extract_mode_shapes`; required for
                ``'mode1'``.
            mode_index: Mode index (0‑based) for ``'mode1'``.
            node_mass_overrides: Optional dict mapping **node ID** →
                mass scale factor (multiplier) applied after seismic
                masses are computed.  Enables per‑storey masonry mass
                corrections (``factor = 1.0 + m_storey_extra/m_storey``)
                that change the mass distribution rather than a single
                global scale.  Node IDs match
                :attr:`node_masses` keys (string SAP IDs), not
                OpenSees tags.
            print_progress: Print a progress line per step.
            record_element_forces: When ``True``, capture the local end
                forces of every active frame element after each push step
                and expose them as ``results["element_forces_history"]``
                (list aligned with ``results["step"]``; index 0 is the
                post-gravity state).  Required by
                :func:`fea_toolkit.capacity.shear_capacity.report_shear_failure`.

        Returns:
            Dict with keys ``step``, ``control_disp``, ``base_shear``,
            ``status``, ``gravity_displacements``, ``control_node``,
            ``dof``, ``lateral_load_type`` and (when
            ``record_element_forces=True``) ``element_forces_history``.
        """
        valid_types = {"uniform", "triangular", "mode1", "pattern", "point"}
        if lateral_load_type not in valid_types:
            raise ValueError(
                f"Unknown lateral_load_type '{lateral_load_type}'. Choose from {valid_types}."
            )
        if lateral_load_type == "pattern" and not lateral_pattern_name:
            raise ValueError("lateral_pattern_name is required when lateral_load_type='pattern'")

        if self.config.get("verbose") or print_progress:
            print(
                f"Running pushover: {lateral_load_type} in "
                f"{lateral_direction}, {num_steps} steps, "
                f"max disp = {max_disp:.3f} m"
            )

        dof = {"X": 1, "Y": 2, "Z": 3}[lateral_direction]

        # ── Rebuild with fiber sections ──────────────────────────
        # Pushover always attempts fiber sections (nonlinear).  Check
        # whether any section overrides the base to_fiber_patches —
        # if none do, fall back to elastic sections.
        # Note: brace_truss is orthogonal — braces use Hysteretic truss
        # elements while beams/columns can still use fiber sections.
        _use_fiber = True
        for sec in self.mesh_model.sections.values():
            if isinstance(sec, ShellSection):
                continue
            try:
                sec.to_fiber_patches(mat_tag=1)
            except NotImplementedError:
                _use_fiber = False
                import warnings

                warnings.warn(
                    f"Section '{sec.name}' does not support fiber patches — "
                    f"falling back to elastic sections for all frame elements. "
                    f"Consider implementing to_fiber_patches() for mixed "
                    f"steel/RC models.",
                    UserWarning,
                    stacklevel=3,
                )
                break

        if not _use_fiber:
            overrides: dict[str, Any] = {
                "element_type": "elasticBeamColumn",
                "create_fiber_sections": False,
                "use_elastic_sections": True,
            }
            self.build_domain(config_overrides=overrides)
        else:
            self.rebuild_with_fiber_sections(
                brace_selection=self._brace_selection,
            )

        # ── Re-apply edge constraints ────────────────────────────
        _spring_scale = float(self.config.get("pushover_spring_scale", 1.0))
        if self._saved_edge_constraints and _spring_scale > 0:
            for args in self._saved_edge_constraints:
                coarse_edges, fine_nodes, coarse_elems, tolerance, k, verbose = args
                if _spring_scale != 1.0 and k is not None:
                    k = k * _spring_scale
                self.apply_edge_constraints(
                    coarse_edges=coarse_edges,
                    fine_nodes=fine_nodes,
                    coarse_elements=coarse_elems,
                    tolerance=tolerance,
                    penalty_stiffness=k,
                    verbose=verbose or self.config.get("verbose", False),
                )
            if self.config.get("verbose", False) or print_progress:
                n = len(self._saved_edge_constraints)
                print(f"  Re-applied edge constraints from {n} tear(s)")

        # ── Seismic masses (for lateral load shape) ──────────────
        try:
            self.compute_seismic_masses()
        except Exception:
            if self.config.get("verbose"):
                print("  compute_seismic_masses failed, using fallback masses")
            self._compute_fallback_masses()

        # ── Apply per-node mass overrides (masonry/storey scaling) ─
        if node_mass_overrides:
            for nid, factor in node_mass_overrides.items():
                if nid not in self.node_masses or factor <= 0:
                    continue
                scaled = self.node_masses[nid] * factor
                self.node_masses[nid] = scaled
                node = self.mesh_model.nodes.get(nid)
                if node is None:
                    continue
                # Re-issue: ops.mass() overwrites the previous value,
                # keeping the OpenSees model consistent with the scaled
                # Python-side masses (affects dynamic analysis + lateral
                # load shapes).
                with contextlib.suppress(Exception):
                    ops.mass(node.node_tag, scaled, scaled, scaled, 0.0, 0.0, 0.0)
            if self.config.get("verbose") or print_progress:
                print(
                    f"  Applied node_mass_overrides to "
                    f"{len([f for f in node_mass_overrides.values() if f > 0])} node(s)"
                )

        # ── Gravity analysis ─────────────────────────────────────
        # Create loads directly (domain was just rebuilt by
        # rebuild_with_fiber_sections above).  Avoid passing
        # pattern_scales to run_static_analysis, which would trigger
        # a second build_domain() without fiber overrides, replacing
        # dispBeamColumn elements with elasticBeamColumn.
        self.create_loads(pattern_scales=gravity_patterns)
        grav_results = self.run_static_analysis(
            extract_reactions=True,
        )
        grav_disp = grav_results.get("nodal_displacements", {})

        # ── Gravity diagnostic: reaction summary ────────────────
        # Report the total applied vs. reacted vertical load so the
        # user can verify the design gravity combination (λ=1.0) is
        # actually in place before lateral pushover begins.
        _lr_check = grav_results.get("load_reaction_check", {})
        if _lr_check:
            _applied = _lr_check.get("applied_fz", 0.0)
            _reaction = _lr_check.get("reaction_fz", 0.0)
            logger.info(
                "  Gravity reached λ=1.0 — applied Fz=%.3f, reacted Fz=%.3f, Δ=%.3f",
                _applied,
                _reaction,
                _lr_check.get("delta", 0.0),
            )
        if print_progress:
            _reac = grav_results.get("reactions", {})
            _sum_fz = sum(float(r.get("fz", 0.0)) for r in _reac.values())
            _n_full = sum(1 for r in self.mesh_model.restraints.values() if all(r.dofs))
            print(
                f"  Gravity converged — total vertical reaction = {_sum_fz:.1f} "
                f"({_n_full} fully-fixed base node(s))"
            )

        # ── Gravity diagnostic: concrete/rbar strain check ──────
        # After gravity converges, probe the extreme fibre strains at
        # the end sections of every fiber frame element.  Flags any
        # element whose concrete reaches crushing or rebar reaches
        # yield under the *design gravity load alone* — a useful
        # pre-pushover damage assessment.  Purely diagnostic (never
        # raises); wrapped so a query failure cannot abort the run.
        try:
            # threshold defaults — concrete crushing ~ -0.003
            # rebar yield ~ 0.0025 (typical εy ≈ 500 MPa / 200 GPa)
            _crush_eps = -0.0030
            _yield_eps = 0.0025
            _flagged: list[tuple[str, float]] = []
            _n_scanned = 0
            _assignments = self.mesh_model.frame_assignments or {}
            for eid, elem in self.mesh_model.frame_elements.items():
                tag = self.frame_tag_map.get(eid)
                if tag is None:
                    continue
                sec_name = _assignments.get(eid, "")
                if not sec_name:
                    continue
                _sec = self.mesh_model.sections.get(sec_name)
                if _sec is None:
                    continue
                try:
                    # first integration-point section deformation
                    sec_def = ops.eleResponse(int(tag), "section", 1, "deformation")
                except Exception:
                    continue
                if not sec_def or len(sec_def) < 3:
                    continue
                # axial strain eps0 + curvature about local z × h/2
                eps0 = float(sec_def[0])
                kz = float(sec_def[2])
                _n_scanned += 1
                half_depth = 0.5 * float(
                    getattr(_sec, "h", getattr(_sec, "depth", getattr(_sec, "t3", 0.0))) or 0.5
                )
                strain_upper = eps0 + kz * half_depth
                strain_lower = eps0 - kz * half_depth
                eps_max = max(strain_upper, strain_lower)
                eps_min = min(strain_upper, strain_lower)
                if eps_min < _crush_eps:
                    _flagged.append((str(eid), eps_min))
                elif eps_max > _yield_eps:
                    _flagged.append((str(eid), eps_max))
            if _flagged:
                logger.warning(
                    "  ⚠ Gravity-only damage check: %d / %d frame element(s) "
                    "exceed strain limits (crush < %.4f or yield > %.4f): %s",
                    len(_flagged),
                    _n_scanned,
                    _crush_eps,
                    _yield_eps,
                    _flagged[:8],
                )
            elif print_progress:
                print(
                    f"  Gravity-only damage check: 0 / {_n_scanned} "
                    f"frame element(s) exceed concrete crush / rebar yield strain"
                )
        except Exception:
            logger.debug("  Gravity damage check skipped", exc_info=True)

        # ── Control node auto‑select ─────────────────────────────
        if control_node_tag is None:
            candidate = None
            max_z = -1e12
            for nid, nd in self.mesh_model.nodes.items():
                restraint = self.mesh_model.restraints.get(nid)
                if restraint and len(restraint.dofs) > dof - 1 and restraint.dofs[dof - 1] == 1:
                    continue  # restrained in push direction
                try:
                    z = ops.nodeCoord(nd.node_tag)[2]
                except Exception:
                    continue
                if z > max_z:
                    max_z = z
                    candidate = nd.node_tag
            if candidate is not None:
                control_node_tag = candidate
            else:
                raise RuntimeError(
                    "Could not auto-select control node — no unrestrained nodes found"
                )

        if print_progress:
            print(f"  Control node = {control_node_tag}")

        # ── Record gravity control displacement ──────────────────
        try:
            grav_ctrl_disp = ops.nodeDisp(int(control_node_tag))[dof - 1]
        except Exception:
            grav_ctrl_disp = 0.0

        # ── Lock gravity ─────────────────────────────────────────
        ops.loadConst("-time", 0.0)

        # Find a free pattern tag
        _pat_tag = 9001
        try:
            existing = ops.getLoadPatternTags()
            if existing:
                _pat_tag = max(*existing, 9000) + 1
        except Exception:
            pass

        # ── Apply lateral loads ──────────────────────────────────
        if lateral_load_type == "pattern":
            # Use existing SAP2000 frame distributed loads projected
            # onto the push direction.
            dir_map = {"Gravity": (0, 0, -1), "X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}

            for ld in self.mesh_model.frame_dist_loads:
                if ld.pattern != lateral_pattern_name:
                    continue

                gx, gy, gz = dir_map.get(ld.direction, (0, 0, 0))
                elem = self.mesh_model.frame_elements.get(ld.frame_id)
                if elem is None or getattr(elem, "inactive", False):
                    continue
                ops_tag = self.frame_tag_map.get(ld.frame_id, elem.elem_tag)

                wa, wb = float(ld.val_a), float(ld.val_b)
                aL, bL = ld.rdist_a, ld.rdist_b

                nd_i = self.mesh_model.nodes.get(elem.node_i)
                nd_j = self.mesh_model.nodes.get(elem.node_j)
                if nd_i is None or nd_j is None:
                    continue
                axis = np.array([nd_j.x - nd_i.x, nd_j.y - nd_i.y, nd_j.z - nd_i.z])
                try:
                    vx, vy, vz = get_local_axes(axis, getattr(elem, "angle", 0.0))
                except Exception:
                    continue

                T = np.column_stack([vx, vy, vz])
                g_local = np.linalg.solve(T, np.array([gx, gy, gz]))
                wy_a = g_local[1] * wa
                wz_a = g_local[2] * wa
                wx_a = g_local[0] * wa
                wy_b = g_local[1] * wb
                wz_b = g_local[2] * wb
                wx_b = g_local[0] * wb

                if abs(wa) < 1e-12 and abs(wb) < 1e-12:
                    continue

                is_uniform = abs(wa - wb) < 1e-12
                if is_uniform and abs(aL) < 1e-12 and abs(bL - 1.0) < 1e-12:
                    ops.eleLoad("-ele", ops_tag, "-type", "-beamUniform", wy_a, wz_a, wx_a)
                elif is_uniform:
                    ops.eleLoad("-ele", ops_tag, "-type", "-beamUniform", wy_a, wz_a, wx_a, aL, bL)
                else:
                    for i in range(4):
                        span = bL - aL
                        seg_a = aL + i * span / 4
                        seg_b = aL + (i + 1) * span / 4
                        xi = (i + 0.5) / 4
                        ops.eleLoad(
                            "-ele",
                            ops_tag,
                            "-type",
                            "-beamUniform",
                            wy_a + (wy_b - wy_a) * xi,
                            wz_a + (wz_b - wz_a) * xi,
                            wx_a + (wx_b - wx_a) * xi,
                            seg_a,
                            seg_b,
                        )

            if print_progress:
                n = sum(
                    1
                    for ld in self.mesh_model.frame_dist_loads
                    if ld.pattern == lateral_pattern_name
                )
                print(
                    f"  Applied lateral loads from pattern '{lateral_pattern_name}' ({n} load(s))"
                )
        else:
            ops.timeSeries("Linear", _pat_tag)
            ops.pattern("Plain", _pat_tag, _pat_tag)

            if lateral_load_type == "uniform":
                node_loads = self._compute_uniform_lateral_loads(
                    direction=lateral_direction,
                    node_masses=self.node_masses,
                )
            elif lateral_load_type == "triangular":
                node_loads = self._compute_triangular_lateral_loads(
                    direction=lateral_direction,
                    node_masses=self.node_masses,
                    fundamental_period=fundamental_period,
                )
            elif lateral_load_type == "mode1":
                if mode_shapes is None:
                    raise ValueError("mode_shapes is required when lateral_load_type='mode1'")
                node_loads = self._compute_mode_shape_lateral_loads(
                    direction=lateral_direction,
                    node_masses=self.node_masses,
                    mode_shapes=mode_shapes,
                    mode_index=mode_index,
                )
            elif lateral_load_type == "point":
                _pts = lateral_point_nodes or [int(control_node_tag)]
                # Load along the push direction's global axis (dof 1/2/3
                # for X/Y/Z) instead of a hardcoded X-only unit load.
                _vec = [0.0, 0.0, 0.0]
                _vec[dof - 1] = 1.0
                node_loads = {int(t): tuple(_vec) for t in _pts}
            else:
                node_loads = {}

            for tag, (fx, fy, fz) in node_loads.items():
                ops.load(int(tag), fx, fy, fz, 0.0, 0.0, 0.0)

            n_loaded = len(node_loads)
            if print_progress:
                print(f"  Applied lateral loads ({lateral_load_type}) to {n_loaded} node(s)")

        # ── Displacement‑controlled push analysis setup ──────────
        disp_inc = max_disp / max(num_steps, 1)

        # Use looser tolerances matching v1 (builder.py) pushover —
        # NormDispIncr with 1e-4 tolerance, 20 iterations, energy
        # norm.  Tight tolerances (1e-6/10 iter) prevent convergence
        # for mode-shape-based pushover patterns.
        _algo = self.config.get("solver_algorithm", "Newton")
        _test_tol = self.config.get("solver_test_tol", 1e-4)
        _test_iter = self.config.get("solver_test_max_iter", 20)
        _system = self.config.get("solver_system", "BandGen")

        ops.wipeAnalysis()
        _cs = self.config.get("solver_constraints", "Transformation")
        if self._edge_constraint_method == "penalty":
            _cs = "Penalty"
            ops.constraints("Penalty", 1.0e12, 1.0e12)
        else:
            ops.constraints(_cs)
        ops.numberer("RCM")
        ops.system(_system)
        ops.test("NormDispIncr", _test_tol, _test_iter, 0, 2)

        ops.integrator("DisplacementControl", int(control_node_tag), dof, disp_inc)
        ops.analysis("Static")

        # ── Per-step recording setup (opt-in) ─────────────────────
        record = self.config.get("record_pushover_steps", False)
        record_sel = self.config.get("pushover_record_selection", None)
        record_frames: set[str] = set()
        record_areas: set[str] = set()
        record_node_tags: set[int] = set()
        if record:
            if record_sel is not None:
                # Pass storey data if available in config (for story-based Selection filtering)
                _storey_data = self.config.get("pushover_record_storey_data", None)
                record_frames, record_areas = record_sel.resolve_to_mesh_sets(
                    self.mesh_model,
                    storey_data=_storey_data,
                )
            else:
                record_frames = {
                    eid
                    for eid, fe in self.mesh_model.frame_elements.items()
                    if not getattr(fe, "inactive", False)
                }
                record_areas = {
                    aid
                    for aid, ae in self.mesh_model.area_elements.items()
                    if not getattr(ae, "inactive", False)
                }
            # Collect node tags from selected frames/areas only
            for eid in record_frames:
                fe = self.mesh_model.frame_elements.get(eid)
                if fe is None:
                    continue
                for nid in (fe.node_i, fe.node_j):
                    nd = self.mesh_model.nodes.get(nid)
                    if nd is not None:
                        record_node_tags.add(nd.node_tag)
            for aid in record_areas:
                ae = self.mesh_model.area_elements.get(aid)
                if ae is None:
                    continue
                for nid in ae.node_ids:
                    nd = self.mesh_model.nodes.get(nid)
                    if nd is not None:
                        record_node_tags.add(nd.node_tag)
            if print_progress and (record_frames or record_areas):
                print(
                    f"  Recording {len(record_frames)} frame(s) + "
                    f"{len(record_areas)} area(s) + "
                    f"{len(record_node_tags)} node(s) per step"
                )
        step_results: list[dict[str, Any]] = []
        element_forces_history: list[dict[int, dict[str, float]]] = []

        # ── Gravity state (step 0) ───────────────────────────────
        steps: list[int] = [0]
        ctrl_disps: list[float] = [0.0]
        base_shears: list[float] = [0.0]
        statuses: list[int] = [0]
        if record_element_forces:
            element_forces_history.append(self.extract_static_element_forces())

        try:
            ops.reactions()
            bs0 = 0.0
            for nid, nd in self.mesh_model.nodes.items():
                r = self.mesh_model.restraints.get(nid)
                if r and len(r.dofs) > dof - 1 and r.dofs[dof - 1] == 1:
                    try:
                        rxn = ops.nodeReaction(nd.node_tag, dof)
                        if isinstance(rxn, (list, tuple)):
                            rxn = rxn[0] if rxn else 0.0
                        bs0 += float(rxn)
                    except Exception:
                        pass
            # Sign convention: nodeReaction() returns the force the
            # ground exerts on the structure (Newton's 3rd law pair of
            # the applied lateral push).  Negate so base_shear records
            # the structure's lateral resistance, which is positive
            # when pushed in the positive DOF direction.
            base_shears[0] = -bs0
        except Exception:
            pass

        # ── Push loop with algorithm fallback chain ──────────────
        for step in range(1, num_steps + 1):
            _algo_chain: list = [_algo]
            if _algo != "NewtonLineSearch":
                _algo_chain.append("NewtonLineSearch")
            if _algo != "ModifiedNewton":
                _algo_chain.append(("ModifiedNewton", "-initial"))
            _algo_chain.append("KrylovNewton")

            ok = -1
            for attempt in _algo_chain:
                if isinstance(attempt, tuple):
                    ops.algorithm(attempt[0], attempt[1])
                else:
                    ops.algorithm(attempt)
                ok = ops.analyze(1)
                if ok == 0:
                    break

            # Per-step fallback (Gap 5): on failure, retry once with
            # relaxed NormUnbalance + ModifiedNewton(-initial), then
            # restore the primary test settings for subsequent steps.
            if ok != 0:
                _fallback = self.config.get(
                    "pushover_fallback_defaults", self.PUSHOVER_FALLBACK_DEFAULTS
                )
                # Units-aware fallback tolerance (see run_static_analysis).
                _g = g_from_units(self.units)
                _fb_total_mass = sum(self.node_masses.values()) if self.node_masses else 0.0
                if _fb_total_mass > 0:
                    _fb_tol = max(_fb_total_mass * _g * 1e-6, _test_tol * 10.0)
                else:
                    _fb_tol = _test_tol * 10.0
                ops.test(
                    _fallback.get("solver_test_type", "NormUnbalance"),
                    _fb_tol,
                    _fallback.get("solver_test_max_iter", 1000),
                )
                _fb_algo = _fallback.get("solver_algorithm", "ModifiedNewton")
                if _fb_algo == "ModifiedNewton":
                    ops.algorithm("ModifiedNewton", "-initial")
                else:
                    ops.algorithm(_fb_algo)
                ok = ops.analyze(1)
                # Restore primary settings for subsequent steps
                ops.test("NormDispIncr", _test_tol, _test_iter, 0, 2)

            statuses.append(ok)

            # Record control node displacement (relative to gravity)
            try:
                cd_total = ops.nodeDisp(int(control_node_tag))[dof - 1]
                cd = cd_total - grav_ctrl_disp
            except Exception:
                cd = 0.0
            ctrl_disps.append(cd)

            # Calculate base shear
            try:
                ops.reactions()
                bs = 0.0
                for nid, nd in self.mesh_model.nodes.items():
                    r = self.mesh_model.restraints.get(nid)
                    if r and len(r.dofs) > dof - 1 and r.dofs[dof - 1] == 1:
                        try:
                            rxn = ops.nodeReaction(nd.node_tag, dof)
                            if isinstance(rxn, (list, tuple)):
                                rxn = rxn[0] if rxn else 0.0
                            bs += float(rxn)
                        except Exception:
                            pass
            except Exception:
                bs = 0.0
            # Same sign convention as step 0: nodeReaction() is the
            # ground-on-structure force (Newton's 3rd law pair of the
            # applied lateral push).  Negate so base_shear is the
            # structure's lateral resistance (positive in push direction).
            base_shears.append(-bs)
            steps.append(step)

            # ── Per-step element-force recording (Phase 1 reporter) ──
            if record_element_forces and ok == 0:
                element_forces_history.append(self.extract_static_element_forces())

            # ── Per-step element recording ──────────────────────
            if record and ok == 0:
                step_data = _record_step(
                    self,
                    step,
                    record_frames,
                    record_areas,
                    node_tags=record_node_tags,
                )
                step_results.append(step_data)

            if print_progress:
                s = "✓" if ok == 0 else "✗"
                print(f"    Step {step:4d}/{num_steps}: u={cd:.6f} m  V={bs:.2f} kN  {s}")

            if ok != 0:
                if print_progress:
                    print(
                        f"    Push stopped — non-converged step (last algorithm: {_algo_chain[-1]})"
                    )
                break

        # Store step results on builder for downstream export
        self.pushover_step_results = step_results

        result = {
            "step": steps,
            "control_disp": ctrl_disps,
            "base_shear": base_shears,
            "status": statuses,
            "gravity_displacements": grav_disp,
            "control_node": control_node_tag,
            "dof": dof,
            "lateral_load_type": lateral_load_type,
            "element_forces_history": element_forces_history,
            "units": self.mesh_model.units,
        }
        if record:
            result["step_results"] = step_results

        return result

    def export_pushover_results(
        self,
        path: str,
        direction: str = "+X",
        pushover_results: Optional[dict[str, Any]] = None,
    ) -> str:
        """Export recorded pushover step results to NPZ.

        Args:
            path: Output .npz file path.
            direction: Push direction label, e.g. ``"+X"``, ``"+Y"``.
            pushover_results: Optional full result dict from
                :meth:`run_pushover_analysis`.  When provided, the
                global arrays (step, control_disp, base_shear) are
                included in the NPZ file alongside per-element forces.

        Returns:
            The path to the written .npz file.

        Raises:
            ValueError: If no step results have been recorded.
        """
        if not getattr(self, "pushover_step_results", None):
            raise ValueError(
                "No pushover step results to export. "
                "Ensure run_pushover_analysis() was called with "
                "record_pushover_steps=True in config."
            )
        from ..io.npz_writer import write_pushover_results_npz

        return write_pushover_results_npz(
            path,
            self.mesh_model,
            self.pushover_step_results,
            direction=direction,
            pushover_results=pushover_results,
        )

    def _compute_fallback_masses(self) -> dict[str, float]:
        """Compute nodal masses from element self‑weight when no MASS SOURCE.

        Used as a fallback when the model has no mass source definitions.
        Masses are used to define the shape of uniform/triangular pushover
        load patterns.
        """
        g = g_from_units(self.mesh_model.units)
        node_mass: dict[str, float] = {}

        for eid, elem in self.mesh_model.frame_elements.items():
            if getattr(elem, "inactive", False):
                continue
            sec_name = self.mesh_model.frame_assignments.get(eid)
            if not sec_name or sec_name not in self.mesh_model.sections:
                continue
            sec = self.mesh_model.sections[sec_name]
            mat = self.mesh_model.materials.get(sec.material)
            if mat is None or mat.unit_weight == 0:
                continue
            ni = self.mesh_model.nodes.get(elem.node_i)
            nj = self.mesh_model.nodes.get(elem.node_j)
            if ni is None or nj is None:
                continue
            L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
            if L < 1e-12:
                continue
            weight = sec.A * mat.unit_weight * L
            mass = weight / g
            node_mass[elem.node_i] = node_mass.get(elem.node_i, 0.0) + mass * 0.5
            node_mass[elem.node_j] = node_mass.get(elem.node_j, 0.0) + mass * 0.5

        return node_mass

    def _compute_uniform_lateral_loads(
        self,
        direction: str,
        node_masses: dict[str, float],
    ) -> dict[int, tuple[float, float, float]]:
        """Compute mass‑proportional lateral loads (uniform acceleration).

        Per ASCE 41 / ATC‑40 \"Uniform\" pattern — each node with mass
        receives a load proportional to its mass in the push direction.
        The absolute magnitude is irrelevant because ``DisplacementControl``
        scales the entire pattern to achieve the target displacement.

        Returns:
            ``{node_tag: (fx, fy, fz)}`` in global coordinates.
        """
        dof_idx = {"X": 0, "Y": 1, "Z": 2}.get(direction.upper(), 0)

        nodal_loads: dict[int, tuple[float, float, float]] = {}
        for nid, mass in node_masses.items():
            if mass <= 0:
                continue
            node = self.mesh_model.nodes.get(nid)
            if node is None:
                continue
            f = [0.0, 0.0, 0.0]
            f[dof_idx] = mass
            nodal_loads[node.node_tag] = (f[0], f[1], f[2])
        return nodal_loads

    def _compute_triangular_lateral_loads(
        self,
        direction: str,
        node_masses: dict[str, float],
        fundamental_period: Optional[float] = None,
    ) -> dict[int, tuple[float, float, float]]:
        """Compute triangular (ELF) lateral loads proportional to $m_i h_i^k$.

        Per ASCE 7 / ASCE 41:
        * $k = 1.0$ for $T \\le 0.5$ s
        * $k = 2.0$ for $T \\ge 2.5$ s
        * Linear interpolation for $0.5 < T < 2.5$ s

        Height $h_i$ is measured relative to the lowest node in the model.

        Returns:
            ``{node_tag: (fx, fy, fz)}`` in global coordinates.
        """
        dof_idx = {"X": 0, "Y": 1, "Z": 2}.get(direction.upper(), 0)

        # Find base elevation
        z_vals = [node.z for node in self.mesh_model.nodes.values()]
        z_min = min(z_vals) if z_vals else 0.0

        # Compute k exponent per ASCE 7
        if fundamental_period is None or fundamental_period <= 0.5:
            k = 1.0
        elif fundamental_period >= 2.5:
            k = 2.0
        else:
            k = 1.0 + (fundamental_period - 0.5) / 2.0

        nodal_loads: dict[int, tuple[float, float, float]] = {}
        for nid, mass in node_masses.items():
            if mass <= 0:
                continue
            node = self.mesh_model.nodes.get(nid)
            if node is None:
                continue
            h = max(node.z - z_min, 0.0)
            f_mag = mass * (h**k)
            if abs(f_mag) < 1e-12:
                continue
            f = [0.0, 0.0, 0.0]
            f[dof_idx] = f_mag
            nodal_loads[node.node_tag] = (f[0], f[1], f[2])
        return nodal_loads

    def _compute_mode_shape_lateral_loads(
        self,
        direction: str,
        node_masses: dict[str, float],
        mode_shapes: dict[int, dict[int, tuple[float, float, float]]],
        mode_index: int = 0,
    ) -> dict[int, tuple[float, float, float]]:
        """Compute mode‑shape‑proportional lateral loads $F_i = m_i \\cdot |\\phi_i|$.

        Each node receives a load proportional to its mass times the
        **absolute value** of the eigenvector component in the push
        direction.  Using absolute values ensures all loads act in
        the same direction — without this, nodes with opposite mode-
        shape signs would oppose the push, creating a near-self-
        equilibrating pattern that prevents convergence.

        The sign of the control-node mode shape is used to set the
        global direction (positive or negative push).

        Returns:
            ``{node_tag: (fx, fy, fz)}`` in global coordinates.
        """
        if mode_index not in mode_shapes:
            raise ValueError(f"Mode index {mode_index} not found in mode_shapes")

        mode = mode_shapes[mode_index]
        dof_idx = {"X": 0, "Y": 1, "Z": 2}.get(direction.upper(), 0)

        nodal_loads: dict[int, tuple[float, float, float]] = {}
        for nid, mass in node_masses.items():
            if mass <= 0:
                continue
            node = self.mesh_model.nodes.get(nid)
            if node is None:
                continue
            phi = mode.get(node.node_tag, (0.0, 0.0, 0.0))
            f_mag = mass * abs(phi[dof_idx])
            if abs(f_mag) < 1e-12:
                continue
            f = [0.0, 0.0, 0.0]
            f[dof_idx] = f_mag
            nodal_loads[node.node_tag] = (f[0], f[1], f[2])
        return nodal_loads

    def pushover_to_adrs(
        self,
        pushover_results: dict[str, Any],
        modal_results: dict[str, Any],
        mode_shapes: dict[int, dict[int, tuple[float, float, float]]],
        direction: str = "X",
    ) -> dict[str, Any]:
        """Convert a pushover capacity curve to ADRS coordinates.

        Delegates to :func:`~fea_toolkit.model.csm.pushover_to_adrs`.

        Args:
            pushover_results: Output from :meth:`run_pushover_analysis`.
            modal_results: Output from :meth:`run_modal_analysis`.
            mode_shapes: Output from :meth:`extract_mode_shapes`.
            direction: Push direction (``'X'``, ``'Y'``, or ``'Z'``).

        Returns:
            Dict with ``'S_a'``, ``'S_d'``, ``'Gamma'``, ``'M_eff'``,
            ``'phi_control'`` — see :func:`~fea_toolkit.model.csm.pushover_to_adrs`.
        """
        from ..model.csm import pushover_to_adrs as _csm_pushover_to_adrs

        return _csm_pushover_to_adrs(
            pushover_results=pushover_results,
            modal_results=modal_results,
            mode_shapes=mode_shapes,
            direction=direction,
        )

    def compute_performance_point(
        self,
        pushover_results: dict[str, Any],
        modal_results: dict[str, Any],
        mode_shapes: dict[int, dict[int, tuple[float, float, float]]],
        spectrum_periods: list[float],
        spectrum_accels: list[float],
        direction: str = "X",
        damping_ratio: float = 0.05,
        max_iter: int = 50,
        tol: float = 0.01,
    ) -> dict[str, Any]:
        """Find the performance point using the Capacity Spectrum Method.

        Delegates to :func:`~fea_toolkit.model.csm.compute_performance_point`.

        Args:
            pushover_results: Output from :meth:`run_pushover_analysis`.
            modal_results: Output from :meth:`run_modal_analysis`.
            mode_shapes: Output from :meth:`extract_mode_shapes`.
            spectrum_periods: Periods (s) defining the elastic demand spectrum.
            spectrum_accels: Spectral accelerations (m/s²).
            direction: Push direction.
            damping_ratio: Elastic damping ratio (default 0.05).
            max_iter: Maximum iterations (default 50).
            tol: Convergence tolerance on S_d (default 0.01).

        Returns:
            Dict with ``'S_dp'``, ``'S_ap'``, ``'V_base'``, ``'D_roof'``,
            ``'T_eq'``, ``'mu'``, ``'converged'`` — see
            :func:`~fea_toolkit.model.csm.compute_performance_point`.
        """
        from ..model.csm import compute_performance_point as _csm_compute

        return _csm_compute(
            pushover_results=pushover_results,
            modal_results=modal_results,
            mode_shapes=mode_shapes,
            spectrum_periods=spectrum_periods,
            spectrum_accels=spectrum_accels,
            direction=direction,
            damping_ratio=damping_ratio,
            max_iter=max_iter,
            tol=tol,
        )


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
    :func:`_record_step` — the two call sites disagreed on whether a
    6-value (3D truss) response was recordable, which silently dropped
    truss members from per-step pushover recording.

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


def _record_step(
    builder: "AnalysisBuilder",
    step: int,
    frame_ids: set[str],
    area_ids: set[str],
    node_tags: Optional[set[int]] = None,
) -> dict[str, Any]:
    """Query ``ops.eleResponse()`` and ``ops.nodeDisp()`` at the current step.

    Args:
        builder: The ``AnalysisBuilder`` instance with active OpenSees domain.
        step: Current push step number.
        frame_ids: SAP2000 frame element IDs to record.
        area_ids: SAP2000 area element IDs to record.
        node_tags: Optional set of OpenSees node tags to record displacements
            for.  When ``None``, no displacement data is collected.

    Returns:
        Dict with keys:
        * ``"step"`` — int
        * ``"frame_forces"`` — ``{eid: {fx_i, fy_i, fz_i, mx_i, my_i, mz_i,
            fx_j, fy_j, fz_j, mx_j, my_j, mz_j}}``
        * ``"shell_forces"`` — ``{aid: {Nx, Ny, Nxy, Mx, My, Mxy}}``
        * ``"node_displacements"`` — ``{tag: (dx, dy, dz)}`` (when *node_tags* is provided)
    """
    data: dict[str, Any] = {"step": step}

    # ── Frame elements ──
    frame_forces: dict[str, dict[str, float]] = {}
    for eid in frame_ids:
        ops_tag = builder.frame_tag_map.get(eid)
        if ops_tag is None:
            continue
        try:
            f = ops.eleResponse(ops_tag, "localForces")  # 12 local values
        except Exception:
            continue
        f = _normalise_frame_response(f)
        if f is None:
            continue
        frame_forces[eid] = {
            "fx_i": f[0],
            "fy_i": f[1],
            "fz_i": f[2],
            "mx_i": f[3],
            "my_i": f[4],
            "mz_i": f[5],
            "fx_j": f[6],
            "fy_j": f[7],
            "fz_j": f[8],
            "mx_j": f[9],
            "my_j": f[10],
            "mz_j": f[11],
        }
    data["frame_forces"] = frame_forces

    # ── Shell elements (stress resultants) ──
    shell_forces: dict[str, dict[str, float]] = {}
    for aid in area_ids:
        ops_tag = builder._shell_tag_map.get(aid)
        if ops_tag is None:
            continue
        try:
            f = ops.eleResponse(ops_tag, "section", 1, "forces")  # Shell resultants
        except Exception:
            continue
        # Section forces return [Nx, Ny, Nxy, Mx, My, Mxy, ?, ?] — the
        # per-unit-width membrane/bending resultants.  (Plain "forces" on a
        # shell returns 24 local nodal forces, which must NOT be used here.)
        if len(f) >= 6:
            shell_forces[aid] = {
                "Nx": f[0],
                "Ny": f[1],
                "Nxy": f[2],
                "Mx": f[3],
                "My": f[4],
                "Mxy": f[5],
            }
    data["shell_forces"] = shell_forces

    # ── Node displacements ──
    if node_tags is not None:
        node_disp: dict[int, tuple[float, float, float]] = {}
        for tag in node_tags:
            try:
                d = ops.nodeDisp(tag)  # list: [dx, dy, dz, rx, ry, rz]
                node_disp[tag] = (float(d[0]), float(d[1]), float(d[2]))
            except Exception:
                continue
        data["node_displacements"] = node_disp

    return data
