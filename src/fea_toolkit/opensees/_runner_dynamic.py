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
            ``velocities`` / ``accelerations`` (``{node_tag: (num_steps, 3)
            ndarray}``), ``base_shear`` (per-step total base shear via global
            equilibrium ``Σ m·(a_rel + ag)``), ``peak_displacement`` (m),
            ``peak_base_shear`` (N), ``peak_plastic_rotation`` (rad),
            ``max_dissipated_energy`` (N·m), ``num_yielded_hinges``,
            ``converged_steps`` and the two Rayleigh ``period_1`` / ``period_2``.
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
        ops.algorithm("Newton")
        ops.integrator("Newmark", 0.5, 0.25)
        ops.analysis("Transient")

        # ── Integrate and record ──
        node_tags = list(ops.getNodeTags())
        disp_history: dict[int, list[list[float]]] = {t: [] for t in node_tags}
        vel_history: dict[int, list[list[float]]] = {t: [] for t in node_tags}
        accel_history: dict[int, list[list[float]]] = {t: [] for t in node_tags}

        # Base shear via global equilibrium — the total nodal inertial force
        # Σ m·(a_rel + ag).  This is exact for a base-excited structure with no
        # applied loads (the benchmark convention), and robust against the
        # Transformation handler mis-resolving the reaction at a base node that
        # is *both* fixed and equalDOF-tied to its lumped-hinge node (the
        # `ops.nodeReaction` path reads back zero there).

        # Lumped-hinge damage tracking (φmax / Emax / Nd).  The zero-length
        # hinge's section deformation/force are the Aggregator DOFs
        # [P, Mz, My, T]; for a planar frame the in-plane bending is index 2
        # (My — rotation about the out-of-plane axis), verified against the
        # installed OpenSeesPy.  Dissipated energy is the net work ∫M·dθ
        # (trapezoidal), summed per hinge.
        hinge_tags = list(getattr(self, "_hinge_tags", None) or [])
        hinge_theta_y = list(getattr(self, "_hinge_yield_rotations", None) or [])
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
            converged_steps += 1
            for t in node_tags:
                disp_history[t].append([float(ops.nodeDisp(t, d)) for d in (1, 2, 3)])
                vel_history[t].append([float(ops.nodeVel(t, d)) for d in (1, 2, 3)])
                accel_history[t].append([float(ops.nodeAccel(t, d)) for d in (1, 2, 3)])
            ag = float(accel[converged_steps - 1]) if converged_steps <= len(accel) else 0.0
            base_shear_steps.append(
                sum(
                    float(ops.nodeMass(t, 1)) * (float(ops.nodeAccel(t, 1)) + ag) for t in node_tags
                )
            )
            for i, tag in enumerate(hinge_tags):
                theta = float(ops.eleResponse(tag, "deformation")[2])
                moment = float(ops.eleResponse(tag, "section", 1, "force")[2])
                peak_plastic_rotation = max(peak_plastic_rotation, abs(theta))
                hinge_energy[i] += (
                    0.5 * (hinge_prev_moment[i] + moment) * (theta - hinge_prev_theta[i])
                )
                if hinge_theta_y and abs(theta) > hinge_theta_y[i]:
                    hinge_yielded[i] = True
                hinge_prev_theta[i] = theta
                hinge_prev_moment[i] = moment

        times = np.arange(converged_steps) * dt
        displacements = {t: np.asarray(v) for t, v in disp_history.items()}
        velocities = {t: np.asarray(v) for t, v in vel_history.items()}
        accelerations = {t: np.asarray(v) for t, v in accel_history.items()}
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
