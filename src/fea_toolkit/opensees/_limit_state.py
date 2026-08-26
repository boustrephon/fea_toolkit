"""Analysis-builder mixin: Elwood & Moehle column limit states."""

import copy
import logging
import math
from typing import Any

import numpy as np
import openseespy.opensees as ops

from ..model.sap_data import Node, Restraint

logger = logging.getLogger(__name__)


class LimitStateMixin:
    """Elwood & Moehle column limit-state column preparation and creation."""

    def _limit_state_requested(self) -> bool:
        """True when any ``limit_state_columns`` are configured."""
        return bool((self.config or {}).get("limit_state_columns"))

    def _ensure_limit_state_units(self) -> None:
        """Rescale the model to kip-in-ksi when limit-state columns need it.

        The Elwood ``limitCurve`` equations are hard-anchored to the
        kip-in-ksi convention (``f'c`` in psi, forces in kip, lengths in
        in).  When ``limit_state_columns`` is non-empty and the mesh is in
        any other system, :func:`convert_mesh_units` deep-copies the mesh
        to ``KIP_IN_UNITS`` (the caller's model is untouched) unless
        ``limit_state_auto_convert_units`` is False.  Wall nD / layered
        shell materials cannot be rescaled by ``convert_mesh_units`` — for
        such models the user must pre-convert and re-parse, or raise.
        """
        if not self.config.get("limit_state_auto_convert_units", True):
            units = self.mesh_model.units
            if units.get("L") == "in" and units.get("F") == "kip":
                return
            raise ValueError(
                "limit_state_columns require a kip-in-ksi model, but the "
                f"mesh is in {units} and 'limit_state_auto_convert_units' "
                "is False. Convert the model to L='in', F='kip' before "
                "running the analysis, or leave "
                "'limit_state_auto_convert_units' = True."
            )
        units = self.mesh_model.units
        if units.get("L") == "in" and units.get("F") == "kip":
            return
        if self.mesh_model.nd_materials or self.mesh_model.layered_shell_sections:
            raise ValueError(
                "limit-state columns require a kip-in-ksi domain, but this model is in "
                f"{units} and contains nd_materials/layered_shell_sections that "
                "convert_mesh_units cannot rescale. Re-parse/pre-process the model "
                "in kip-in-ksi, or set config 'limit_state_auto_convert_units' = False."
            )
        from ..model.units import KIP_IN_UNITS, convert_mesh_units

        converted = convert_mesh_units(self.mesh_model, KIP_IN_UNITS)
        self.mesh_model = converted
        self.units = converted.units
        logger.info("limit-state columns: mesh rescaled to kip-in-ksi units")

    def _restore_limit_state_canonical_state(self) -> None:
        """Restore the canonical column topology before a build cycle.

        Removes the ``*_limit_top`` / ``*_limit_anchor`` instrumentation
        nodes, restores re-pointed beam/column endpoints, restraints and
        joint loads, and clears the per-build emission plan.  Called at
        the start of :meth:`build_domain` (before node creation).
        """
        if not hasattr(self, "_limit_state_canonical"):
            self._limit_state_plan = None
            return
        snap = self._limit_state_canonical
        for nid in [
            k
            for k in list(self.mesh_model.nodes.keys())
            if k.endswith(("_limit_top", "_limit_anchor"))
        ]:
            del self.mesh_model.nodes[nid]
        for eid, elem in self.mesh_model.frame_elements.items():
            if eid in snap["frame_elements"]:
                elem.node_i, elem.node_j = snap["frame_elements"][eid]
        self.mesh_model.frame_assignments = dict(snap["frame_assignments"])
        self.mesh_model.restraints = dict(snap["restraints"])
        # Independent copies each restore cycle, so subsequent builds can
        # re-point node_id values without mutating the canonical snapshot.
        self.mesh_model.joint_loads = copy.deepcopy(snap["joint_loads"])
        self._limit_state_plan = None

    def _default_limit_state_shear_kdeg(self, sec: Any, concrete: Any, L: float) -> float:
        """Default shear degrading slope: 20 % of the cracked fixed-fixed
        flexural stiffness ``12*E_c*I_g/L^3`` (Elwood's ``kf`` proxy)."""
        ec = float(getattr(concrete, "E_mod", 0.0) or 0.0)
        b = float(getattr(sec, "bf", 0.0) or 0.0) or float(getattr(sec, "b", 0.0) or 0.0)
        h = float(getattr(sec, "depth", 0.0) or 0.0) or float(getattr(sec, "h", 0.0) or 0.0)
        if ec <= 0.0 or b <= 0.0 or h <= 0.0 or L <= 0.0:
            return 0.0
        ig = b * h**3 / 12.0
        return 0.2 * 12.0 * ec * ig / L**3

    def _prepare_limit_state_columns(self) -> None:
        """Topology + parameter planning for limit-state columns (Phase A).

        Runs inside :meth:`build_domain` **before** node/element creation
        so that the re-pointed beams and the control/anchor nodes are part
        of the regular domain build.  No OpenSees commands are emitted here
        — the OpenSees curves/materials/springs are created later by
        :meth:`_create_limit_state_columns`.
        """
        cols = self.config.get("limit_state_columns")
        if not cols:
            self._limit_state_plan = None
            return

        # ── Idempotency: canonical snapshot on the first call ──────
        if not hasattr(self, "_limit_state_canonical"):
            self._limit_state_canonical = {
                "frame_elements": {
                    eid: (elem.node_i, elem.node_j)
                    for eid, elem in self.mesh_model.frame_elements.items()
                    if not getattr(elem, "inactive", False)
                },
                "frame_assignments": dict(self.mesh_model.frame_assignments),
                "restraints": dict(self.mesh_model.restraints),
                # Deep copies: ``_prepare_limit_state_columns`` mutates the
                # joint loads' node_id in place, so the canonical snapshot
                # must not share objects with the live mesh (shallow
                # copies would let those mutations corrupt the snapshot).
                "joint_loads": copy.deepcopy(self.mesh_model.joint_loads),
            }

        from ..capacity.elwood_limit_state import (
            elwood_column_geometry,
            elwood_column_parameters,
            elwood_shear_limit_force,
        )
        from ..model.sap_data import ConcreteRectangularSection

        units = self.units
        params_all = self.config.get("limit_state_params") or {}
        p_g_map = self._derive_gravity_axial_loads(cols)
        nodes = self.mesh_model.nodes
        next_node_tag = max((nd.node_tag for nd in nodes.values()), default=0) + 1
        plan: list[dict] = []

        for eid in cols:
            elem = self.mesh_model.frame_elements.get(eid)
            if elem is None or getattr(elem, "inactive", False):
                logger.warning("limit-state: unknown/inactive frame element '%s' skipped", eid)
                continue
            sec_name = self.mesh_model.frame_assignments.get(eid)
            sec = self.mesh_model.sections.get(sec_name) if sec_name else None
            if not isinstance(sec, ConcreteRectangularSection):
                logger.warning(
                    "limit-state: frame element '%s' has no concrete rectangular section "
                    "(got %s) — skipped",
                    eid,
                    sec_name,
                )
                continue
            concrete = self.mesh_model.materials.get(sec.material)
            if concrete is None:
                logger.warning(
                    "limit-state: material '%s' missing for '%s' — skipped", sec.material, eid
                )
                continue
            if not str(getattr(concrete, "type", "") or "").lower().startswith("concrete"):
                logger.warning(
                    "limit-state: material '%s' of '%s' is not concrete — skipped",
                    sec.material,
                    eid,
                )
                continue

            # ── Geometry / column axis ──
            ni = nodes.get(elem.node_i)
            nj = nodes.get(elem.node_j)
            if ni is None or nj is None:
                logger.warning("limit-state: missing nodes for '%s' — skipped", eid)
                continue
            dx, dy, dz = nj.x - ni.x, nj.y - ni.y, nj.z - ni.z
            L = math.hypot(dx, dy, dz)
            if L <= 1e-9:
                logger.warning("limit-state: zero-length element '%s' — skipped", eid)
                continue
            axis = int(np.argmax([abs(dx), abs(dy), abs(dz)]))
            if max(abs(dx), abs(dy), abs(dz)) / L < 0.99:
                logger.warning(
                    "limit-state: element '%s' is not aligned with a global axis "
                    "(Elwood model assumes straight columns) — skipped",
                    eid,
                )
                continue
            axial_dof = axis + 1
            horiz = [i for i in (0, 1, 2) if i != axis]
            shear_dof = horiz[0] + 1
            # ``perpDirn`` is the direction OpenSees uses to measure the
            # distance between ndI/ndJ for the drift (1/oneOverL).  For a
            # vertical column that is the column-axis DOF — matching the
            # 2D Elwood example (column along Y -> perpDirn = 2).
            perp_dof = axial_dof

            # ── Tie rebar material ──
            tie_name = getattr(sec, "tie_rebar_mat", None) or getattr(sec, "rebar_material", None)
            tie = self.mesh_model.materials.get(tie_name) if tie_name else None

            # ── Operating gravity axial load (override wins) ──
            p_g = float(p_g_map.get(eid, 0.0) or 0.0)
            if p_g <= 0.0:
                fc = float(getattr(concrete, "Fc", 0.0) or 0.0)
                b_w = float(getattr(sec, "bf", 0.0) or 0.0) or float(getattr(sec, "b", 0.0) or 0.0)
                h_d = float(getattr(sec, "depth", 0.0) or 0.0) or float(
                    getattr(sec, "h", 0.0) or 0.0
                )
                if b_w > 0.0 and h_d > 0.0 and fc > 0.0:
                    p_g = 0.25 * b_w * h_d * fc  # PEER 2003/01 reference P_g
                    logger.warning(
                        "limit-state: no gravity axial load derived for '%s'; "
                        "using 0.25*A_g*f'c = %.3g (supply 'column_gravity_loads' to override)",
                        eid,
                        p_g,
                    )
                else:
                    logger.warning("limit-state: cannot derive P_g for '%s' — skipped", eid)
                    continue

            # ── Elwood parameters ──
            overrides = dict(params_all.get(eid, {}) or {})
            kwargs = dict(overrides)
            geom = elwood_column_geometry(
                sec,
                concrete,
                tie=tie,
                tie_legs=kwargs.get("tie_legs", 2),
                core_depth=kwargs.get("core_depth"),
            )
            try:
                v_ref = elwood_shear_limit_force(0.01, p_g, geom, units)
            except ValueError as exc:
                logger.warning(
                    "limit-state: degenerate geometry for '%s' - skipped (%s)",
                    eid,
                    exc,
                )
                continue
            kwargs.setdefault("kdeg_shear", self._default_limit_state_shear_kdeg(sec, concrete, L))
            # Post-failure shear residual as a fraction of the 1%-drift shear
            # capacity V(0.01) (Elwood's Vr ~ 10 % of the peak).  The config key
            # ``limit_state_shear_residual_ratio`` feeds ``fres_shear`` directly:
            # ``elwood_column_parameters`` only consults ``shear_residual_ratio``
            # when ``fres_shear`` is None, so passing both would make it dead.
            kwargs.setdefault(
                "fres_shear",
                float(self.config.get("limit_state_shear_residual_ratio", 0.10)) * v_ref,
            )
            params = elwood_column_parameters(sec, concrete, tie=tie, column_length=L, **kwargs)

            # ── Topology: control + anchor nodes at the column top ──
            top_id = self._top_node_id(elem)
            top_node = nodes[top_id]
            bottom_id = elem.node_j if top_id == elem.node_i else elem.node_i
            bottom_node = nodes.get(bottom_id, ni)
            control_id = f"{eid}_limit_top"
            anchor_id = f"{eid}_limit_anchor"
            control_tag = next_node_tag
            next_node_tag += 1
            anchor_tag = next_node_tag
            next_node_tag += 1
            nodes[control_id] = Node(control_id, control_tag, top_node.x, top_node.y, top_node.z)
            nodes[anchor_id] = Node(anchor_id, anchor_tag, top_node.x, top_node.y, top_node.z)
            self.mesh_model.restraints[anchor_id] = Restraint([1, 1, 1, 1, 1, 1])
            # Move the joint restraint onto the control node (the spring
            # rigid-ties the original top node through the zeroLength).
            if top_id in self.mesh_model.restraints:
                self.mesh_model.restraints[control_id] = self.mesh_model.restraints[top_id]
                del self.mesh_model.restraints[top_id]
            # Re-point beams above to the control node
            for eid2, elem2 in self.mesh_model.frame_elements.items():
                if eid2 == eid or getattr(elem2, "inactive", False):
                    continue
                if elem2.node_i == top_id:
                    elem2.node_i = control_id
                if elem2.node_j == top_id:
                    elem2.node_j = control_id
            # Re-point joint loads so gravity enters above the spring
            for jl in self.mesh_model.joint_loads:
                if jl.node_id == top_id:
                    jl.node_id = control_id

            plan.append(
                {
                    "eid": eid,
                    "elem_tag": self.frame_tag_map.get(eid),
                    "bottom_tag": bottom_node.node_tag,
                    "top_tag": top_node.node_tag,
                    "control_tag": control_tag,
                    "anchor_tag": anchor_tag,
                    "axis": axis,
                    "axial_dof": axial_dof,
                    "shear_dof": shear_dof,
                    "perp_dof": perp_dof,
                    "geometry": geom,
                    "params": params,
                    "p_g": p_g,
                }
            )

        self._limit_state_plan = plan
        if not plan:
            logger.warning("limit_state_columns configured but no valid concrete columns found")

    def _create_limit_state_columns(self) -> None:
        """Emit OpenSees limit-state curves/materials/springs (Phase B).

        Called after :meth:`_create_elements` (so the flexural element tags
        exist on the domain).  For each planned column:

        * ``limitCurve Shear`` — Elwood shear capacity surface (imperial).
        * ``limitCurve ThreePoint`` — axial surface (OpenSeesPy 3.8.0
          cannot construct ``limitCurve Axial``; ThreePoint with
          ``forType=2`` monitors the beam-column's axial force).
        * Two ``uniaxialMaterial LimitState`` laws (shear + axial).
        * A ``zeroLength`` spring between the column top and the new
          control node: shear on the first horizontal DOF, axial on the
          column-axis DOF, the remaining DOFs rigid-tied.
        * A soft axial ``zeroLength`` catch from the control node to a
          fixed anchor so gravity is still supported after axial failure.

        This mirrors the validated PEER 2003/01 §8.2.2 series model and the
        ``local/elwood_prototype.py`` topology (top spring, co-located
        control node).
        """
        plan = getattr(self, "_limit_state_plan", None)
        if not plan:
            return
        from ..capacity.elwood_limit_state import (
            elwood_limit_state_envelope,
            elwood_shear_limit_force,
            three_point_axial_surface,
        )

        units = self.units
        pinch_x = float(self.config.get("limit_state_pinch_x", 0.5))
        pinch_y = float(self.config.get("limit_state_pinch_y", 0.4))
        damage1 = float(self.config.get("limit_state_damage1", 0.0))
        damage2 = float(self.config.get("limit_state_damage2", 0.0))
        beta = float(self.config.get("limit_state_beta", 0.4))
        soft_fraction = float(self.config.get("limit_state_soft_axial_fraction", 2.0e-4))

        # ── Tag allocation (materials/curves are distinct namespaces) ──
        try:
            max_ops_mat = max(ops.getMaterialTags(), default=0)
        except Exception:
            max_ops_mat = 0
        mat_tag = max(max(self.material_tags.values(), default=0), max_ops_mat) + 1
        curve_tag = mat_tag + 10 * len(plan) + 10
        try:
            max_ops_ele = max(ops.getEleTags(), default=0)
        except Exception:
            max_ops_ele = 0
        spring_tag = (
            max(
                max_ops_ele,
                max(self.frame_tag_map.values(), default=0),
                max((r[3] for r in self._offset_rigid_links), default=0),
            )
            + 1
        )

        for col in plan:
            eid = col["eid"]
            elem_tag = col["elem_tag"]
            params = col["params"]
            geom = col["geometry"]
            p_g = col["p_g"]
            bottom_tag = col["bottom_tag"]
            top_tag = col["top_tag"]
            control_tag = col["control_tag"]
            anchor_tag = col["anchor_tag"]
            shear_dof = col["shear_dof"]
            perp_dof = col["perp_dof"]
            axial_dof = col["axial_dof"]

            # ── Imperial constants (domain is kip-in-ksi) ──
            b_in = float(geom.b)
            h_in = float(geom.h)
            d_in = float(geom.d)
            fc_psi = float(geom.fc) * 1000.0
            fsw_k = float(params.fsw)

            # ── Rigid elastic for tied DOFs ──
            rigid_tag = mat_tag
            mat_tag += 1
            ops.uniaxialMaterial("Elastic", rigid_tag, 9.9e9)

            # ── Shear limit curve ──
            shear_curve_tag = curve_tag
            curve_tag += 1
            ops.limitCurve(
                "Shear",
                shear_curve_tag,
                elem_tag,
                float(params.rho),
                fc_psi,
                b_in,
                h_in,
                d_in,
                fsw_k,
                float(params.kdeg_shear),
                float(params.fres_shear),
                2,  # defType = interstory drift (chord rotation)
                0,  # forType = spring force
                bottom_tag,
                control_tag,
                shear_dof,
                perp_dof,
                0.0,  # delta
            )

            # ── Axial three-point surface (limitCurve Axial workaround) ──
            axial_curve_tag = curve_tag
            curve_tag += 1
            pts = three_point_axial_surface(
                p_g,
                float(params.fsw),
                units,
                fres=float(params.fres_axial) if params.fres_axial else None,
            )
            (x1, y1), (x2, y2), (x3, y3) = pts
            ops.limitCurve(
                "ThreePoint",
                axial_curve_tag,
                elem_tag,
                x1,
                y1,
                x2,
                y2,
                x3,
                y3,
                float(params.kdeg_axial),
                float(params.fres_axial or 0.0),
                2,  # defType = interstory drift
                2,  # forType = axial force of the beam-column
                bottom_tag,
                control_tag,
                shear_dof,
                perp_dof,
            )

            # ── Shear LimitState material (elastic pre-failure backbone) ──
            shear_mat_tag = mat_tag
            mat_tag += 1
            v_ref = 2.0 * elwood_shear_limit_force(0.01, p_g, geom, units)
            v_backbone = [0.4 * v_ref, 0.7 * v_ref, v_ref]
            k_shear = float(params.shear_elastic_slope)
            sp = elwood_limit_state_envelope(v_backbone, k_shear)
            s_pos = [val for pair in sp for val in pair]
            s_neg = [-val for val in s_pos]
            ops.uniaxialMaterial(
                "LimitState",
                shear_mat_tag,
                *s_pos,
                *s_neg,
                pinch_x,
                pinch_y,
                damage1,
                damage2,
                beta,
                shear_curve_tag,
                2,
                0,  # trailing flag matches Elwood's example scripts
            )

            # ── Axial LimitState material ──
            axial_mat_tag = mat_tag
            mat_tag += 1
            p_backbone = [0.92 * p_g, p_g, 1.2 * p_g]
            k_ax = float(params.axial_elastic_slope)
            ap = elwood_limit_state_envelope(p_backbone, k_ax)
            a_pos = [val for pair in ap for val in pair]
            a_neg = [-val for val in a_pos]
            ops.uniaxialMaterial(
                "LimitState",
                axial_mat_tag,
                *a_pos,
                *a_neg,
                0.5,
                0.5,
                0.0,
                0.0,
                0.0,
                axial_curve_tag,
                2,
            )

            # ── Soft axial catch spring ──
            soft_tag = mat_tag
            mat_tag += 1
            soft_k = max(k_ax * soft_fraction, 1e-9)
            ops.uniaxialMaterial("Elastic", soft_tag, soft_k)

            # ── Top zeroLength spring: shear + axial + rigid ties ──
            mats_by_dof = {axial_dof: axial_mat_tag, shear_dof: shear_mat_tag}
            dirs = [1, 2, 3, 4, 5, 6]
            mats = [mats_by_dof.get(d, rigid_tag) for d in dirs]
            ops.element(
                "zeroLength",
                spring_tag,
                top_tag,
                control_tag,
                "-mat",
                *mats,
                "-dir",
                *dirs,
            )
            spring_tag += 1
            # Soft axial catch: control node → fixed anchor
            ops.element(
                "zeroLength",
                spring_tag,
                control_tag,
                anchor_tag,
                "-mat",
                soft_tag,
                "-dir",
                axial_dof,
            )
            spring_tag += 1

            # ── Register synthetic material tags so rebuilds don't reuse ──
            self.material_tags[f"limit_state_rigid_{eid}"] = rigid_tag
            self.material_tags[f"limit_state_shear_{eid}"] = shear_mat_tag
            self.material_tags[f"limit_state_axial_{eid}"] = axial_mat_tag
            self.material_tags[f"limit_state_soft_{eid}"] = soft_tag
