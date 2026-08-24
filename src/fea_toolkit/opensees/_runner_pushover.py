"""Analysis-builder mixin: pushover analysis, ADRS conversion, and performance point."""

import contextlib
import logging
import math
from typing import TYPE_CHECKING, Any, Optional

import numpy as np
import openseespy.opensees as ops

from ..model.geometry import get_local_axes
from ..model.sap_data import ShellSection
from ..utils import g_from_units
from ._runner_static import _normalise_frame_response

if TYPE_CHECKING:
    from .analysis_builder import AnalysisBuilder

logger = logging.getLogger(__name__)


class PushoverRunnerMixin:
    """Pushover analysis execution, lateral-load derivation, and post-processing."""

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
        _test_type = self.config.get("solver_test_type", "NormDispIncr")
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
        ops.test(_test_type, _test_tol, _test_iter, 0, 2)

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
                # Restore primary settings for subsequent steps (honour a
                # user-configured solver_test_type rather than hardcoding).
                ops.test(_test_type, _test_tol, _test_iter, 0, 2)

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


# ── Module-level helper: per-step recording ──────────────────────


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
