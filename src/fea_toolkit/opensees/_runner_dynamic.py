"""Analysis-builder mixin: native Newmark transient (time-history) analysis."""

import contextlib
import logging
import math
from typing import Any, Optional

import numpy as np
import openseespy.opensees as ops

logger = logging.getLogger(__name__)


class TransientRunnerMixin:
    """Native Newmark transient analysis (ground-motion time history).

    Runs a time-history analysis in-process (no Tcl/Xara export), mirroring the
    Tcl path in :func:`fea_toolkit.opensees.builder.dynamic_time_history_tcl`:
    a gravity hand-off via ``loadConst``, Rayleigh damping from two modal
    periods, a uniform ground excitation, and a Newmark average-acceleration
    integrator (gamma=0.5, beta=0.25).
    """

    def run_transient_analysis(
        self,
        accel: np.ndarray,
        dt: float,
        direction: str = "X",
        damping_ratio: float = 0.05,
        period_1: Optional[float] = None,
        period_2: Optional[float] = None,
        gravity_patterns: Optional[dict[str, float]] = None,
        num_steps: Optional[int] = None,
    ) -> dict[str, Any]:
        """Run a native Newmark time-history analysis.

        Args:
            accel: Ground acceleration samples, one per time step, in the
                model's acceleration units (length / s²).
            dt: Time step (s).
            direction: Excitation direction — ``"X"``, ``"Y"`` or ``"Z"``.
            damping_ratio: Rayleigh damping ratio for the two retained modes.
            period_1: First Rayleigh period (s); ``None`` → from a 2-mode
                modal solve.
            period_2: Second Rayleigh period (s); ``None`` → from a 2-mode
                modal solve (a single-mode model falls back to
                ``period_1 * 0.5``).
            gravity_patterns: Load-pattern scale dict for the gravity stage
                (e.g. ``{"DEAD": 1.0}``).  ``None`` skips the gravity hand-off.
            num_steps: Number of integration steps (default ``len(accel)``).

        Returns:
            Dict with ``times`` (s), per-node ``displacements`` /
            ``velocities`` / ``accelerations`` (``{node_tag: (converged_steps, 3)
            ndarray}``), ``base_shear`` (per-step total base shear via global
            equilibrium ``Σ m·(a_rel + ag)``), ``peak_displacement`` (m),
            ``peak_base_shear`` (N), ``peak_plastic_rotation`` (rad),
            ``max_dissipated_energy`` (N·m), ``num_yielded_hinges``,
            ``converged_steps`` and the two Rayleigh ``period_1`` / ``period_2``.

        The builder ``config`` may set ``transient_algorithm`` (the solver
        algorithm; default ``"Newton"``) and ``transient_record_node_tags`` (an
        iterable of node tags whose histories are recorded; default ``None`` =
        every node).
        """
        accel = np.asarray(accel, dtype=float)
        if num_steps is None:
            num_steps = len(accel)
        dof = {"X": 1, "Y": 2, "Z": 3}[direction.upper()]

        # ── Rebuild for an independent record ──
        # Each ground-motion record must start from a fresh domain: a previous
        # record leaves committed material/response state and its excitation
        # pattern behind, so reusing the domain would make the new record depend
        # on the old one.  Rebuild — reproducing the last build's config
        # overrides (e.g. the fiber sections from ``rebuild_with_fiber_sections``)
        # rather than the elastic defaults — re-apply any saved edge constraints,
        # and re-apply masses (``build_domain`` does not apply them).
        self.build_domain(config_overrides=self._last_build_overrides)
        self._reapply_edge_constraints()
        self.compute_seismic_masses()

        # ── Rayleigh periods (from a modal solve when not supplied) ──
        if period_1 is None or period_2 is None:
            modal = self.run_modal_analysis(
                num_modes=2, print_results=False, eigen_solver="fullGenLapack"
            )
            periods = list(modal.get("periods", []))
            if period_1 is None:
                period_1 = float(periods[0]) if periods else 0.2
            if period_2 is None:
                period_2 = float(periods[1]) if len(periods) > 1 else period_1 * 0.5

        # Rayleigh coefficients — C = a0 * M + a1 * K (exact two-period formula).
        a0 = 4.0 * math.pi * damping_ratio / (period_1 + period_2)
        a1 = damping_ratio * period_1 * period_2 / (math.pi * (period_1 + period_2))

        # ── Gravity hand-off ──
        if gravity_patterns:
            self.create_loads(pattern_scales=gravity_patterns)
            self.run_static_analysis()
        # Reset pseudo-time to zero (the modal solve's static gravity probe, and
        # any gravity solve above, advance it) and lock applied loads so the
        # ground-motion Path series starts from t = 0.
        ops.loadConst("-time", 0.0)

        # ── Damping + uniform ground excitation ──
        # Use high, unique tags so they never collide with the load patterns
        # `create_loads()` / `_static_gravity_ok()` leave in the domain (those
        # use low sequential tags).
        ts_tag = 900001
        pattern_tag = 900002
        # Repeated runs on the same domain reuse these high tags; remove any
        # previous pattern / series first so a new record or dt does not
        # collide with (and fail to recreate over) the old objects.
        with contextlib.suppress(Exception):
            ops.remove("loadPattern", pattern_tag)
        with contextlib.suppress(Exception):
            ops.remove("timeSeries", ts_tag)
        ops.rayleigh(a0, 0.0, a1, 0.0)
        ops.timeSeries("Path", ts_tag, "-dt", dt, "-values", *accel)
        ops.pattern("UniformExcitation", pattern_tag, dof, "-accel", ts_tag)

        # ── Transient solver ──
        ops.wipeAnalysis()
        ops.constraints("Transformation")
        ops.numberer("RCM")
        ops.system("BandGeneral")
        ops.test("EnergyIncr", 1.0e-6, 200, 0)
        # Algorithm is configurable.  Plain Newton is the default; the SAC
        # trilinear Hysteretic hinge has two sharp yield corners where plain
        # Newton fails to converge (the 3-story El Centro run died at t≈10.4 s),
        # so that model opts in to ``transient_algorithm='NewtonLineSearch'``.
        # The line search walks the Newton step back when a full step overshoots
        # a corner.
        ops.algorithm(self.config.get("transient_algorithm", "Newton"))
        ops.integrator("Newmark", 0.5, 0.25)
        ops.analysis("Transient")

        # ── Integrate and record ──
        all_node_tags = list(ops.getNodeTags())
        # Optional config subset of node tags whose histories are recorded
        # (default: every node).  Base shear still needs the *full* node set, so
        # ``all_node_tags`` is kept separately below.
        record_cfg = self.config.get("transient_record_node_tags")
        if record_cfg:
            wanted = {int(t) for t in record_cfg}
            record_node_tags = [t for t in all_node_tags if t in wanted]
        else:
            record_node_tags = all_node_tags

        # Preallocate per-node kinematic histories (num_steps, 3); the returned
        # dicts are sliced to the converged step count at the end.
        disp_history: dict[int, np.ndarray] = {
            t: np.zeros((num_steps, 3)) for t in record_node_tags
        }
        vel_history: dict[int, np.ndarray] = {t: np.zeros((num_steps, 3)) for t in record_node_tags}
        accel_history: dict[int, np.ndarray] = {
            t: np.zeros((num_steps, 3)) for t in record_node_tags
        }

        # Cache each node's mass once before the time loop.
        node_mass = {t: float(ops.nodeMass(t, dof)) for t in all_node_tags}

        # Base shear via global equilibrium — the total nodal inertial force
        # Σ m·(a_rel + ag).  This is exact for a base-excited structure with no
        # applied loads (the benchmark convention) and is independent of how the
        # constraint handler resolves the base-node reaction.

        # Hinge damage tracking (φmax / Emax / Nd).  Two hinge topologies:
        # * a lumped zero-length hinge (``_hinge_sections[i] is None``) — a
        #   `zeroLength` element whose in-plane deformation is index 4 (dir 5,
        #   the strong-axis hinge);
        # * a beamWithHinges end section (``_hinge_sections[i]`` is 1 or 6) —
        #   the member-end curvature, read via ``section``.
        # Dissipated energy is the net work ∫M·dθ (trapezoidal), summed per
        # hinge.
        hinge_tags = list(getattr(self, "_hinge_tags", None) or [])
        hinge_theta_y = list(getattr(self, "_hinge_yield_rotations", None) or [])
        hinge_sections = list(getattr(self, "_hinge_sections", None) or [])
        n_hinges = len(hinge_tags)
        hinge_prev_theta = [0.0] * n_hinges
        hinge_prev_moment = [0.0] * n_hinges
        hinge_energy = [0.0] * n_hinges
        hinge_yielded = [False] * n_hinges
        peak_plastic_rotation = 0.0

        base_shear_steps: list[float] = []
        converged_steps = 0
        for _ in range(num_steps):
            if ops.analyze(1, dt) != 0:
                break
            step = converged_steps
            for t in record_node_tags:
                disp_history[t][step] = [float(ops.nodeDisp(t, d)) for d in (1, 2, 3)]
                vel_history[t][step] = [float(ops.nodeVel(t, d)) for d in (1, 2, 3)]
                accel_history[t][step] = [float(ops.nodeAccel(t, d)) for d in (1, 2, 3)]
            # Ground acceleration at the integrator's *current* pseudo-time after
            # this step (sample index ``step + 1`` — ``accel[0]`` is t=0),
            # zero once that time is beyond the acceleration series.
            ag = float(accel[step + 1]) if step + 1 < len(accel) else 0.0
            # Base shear via global equilibrium over *all* nodes, reusing the
            # recorded acceleration where available (no duplicate nodeAccel call
            # for the recorded subset).
            shear = 0.0
            for t in all_node_tags:
                if t in accel_history:
                    a_rel = float(accel_history[t][step][dof - 1])
                else:
                    a_rel = float(ops.nodeAccel(t, dof))
                shear += node_mass[t] * (a_rel + ag)
            base_shear_steps.append(shear)
            for i, tag in enumerate(hinge_tags):
                sec_num = hinge_sections[i] if hinge_sections else None
                if sec_num is None:
                    deform = float(ops.eleResponse(tag, "deformation")[4])
                    moment = float(ops.eleResponse(tag, "force")[4])
                else:
                    deform = float(ops.eleResponse(tag, "section", sec_num, "deformation")[0])
                    moment = float(ops.eleResponse(tag, "section", sec_num, "force")[0])
                peak_plastic_rotation = max(peak_plastic_rotation, abs(deform))
                hinge_energy[i] += (
                    0.5 * (hinge_prev_moment[i] + moment) * (deform - hinge_prev_theta[i])
                )
                if hinge_theta_y and abs(deform) > hinge_theta_y[i]:
                    hinge_yielded[i] = True
                hinge_prev_theta[i] = deform
                hinge_prev_moment[i] = moment
            converged_steps += 1

        times = np.arange(converged_steps) * dt
        displacements = {t: v[:converged_steps] for t, v in disp_history.items()}
        velocities = {t: v[:converged_steps] for t, v in vel_history.items()}
        accelerations = {t: v[:converged_steps] for t, v in accel_history.items()}
        peak = 0.0
        for v in displacements.values():
            if v.size:
                peak = max(peak, float(np.max(np.abs(v))))

        return {
            "times": times,
            "displacements": displacements,
            "velocities": velocities,
            "accelerations": accelerations,
            "base_shear": np.asarray(base_shear_steps),
            "peak_displacement": peak,
            "peak_base_shear": float(np.max(np.abs(base_shear_steps))) if base_shear_steps else 0.0,
            "peak_plastic_rotation": peak_plastic_rotation,
            "max_dissipated_energy": float(max(hinge_energy)) if hinge_energy else 0.0,
            "num_yielded_hinges": int(sum(hinge_yielded)),
            "converged_steps": converged_steps,
            "period_1": period_1,
            "period_2": period_2,
        }
