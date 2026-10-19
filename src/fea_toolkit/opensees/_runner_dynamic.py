"""Analysis-builder mixin: native Newmark transient (time-history) analysis."""

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
            Dict with ``times`` (s), ``displacements`` (``{node_tag:
            (num_steps, 3) ndarray}``), ``peak_displacement`` (m),
            ``converged_steps`` and the two Rayleigh ``period_1`` / ``period_2``.
        """
        accel = np.asarray(accel, dtype=float)
        if num_steps is None:
            num_steps = len(accel)
        dof = {"X": 1, "Y": 2, "Z": 3}[direction.upper()]

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

        # ── Ensure seismic masses are in the domain ──
        # ``build_domain`` does not apply masses; the modal solve above does,
        # but only when the periods were auto-computed.  Apply them explicitly
        # so the transient mass matrix is always populated.
        self.compute_seismic_masses()

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
        converged_steps = 0
        for _ in range(num_steps):
            if ops.analyze(1, dt) != 0:
                break
            converged_steps += 1
            for t in node_tags:
                disp_history[t].append([float(ops.nodeDisp(t, d)) for d in (1, 2, 3)])

        times = np.arange(converged_steps) * dt
        displacements = {t: np.asarray(v) for t, v in disp_history.items()}
        peak = 0.0
        for v in displacements.values():
            if v.size:
                peak = max(peak, float(np.max(np.abs(v))))

        return {
            "times": times,
            "displacements": displacements,
            "peak_displacement": peak,
            "converged_steps": converged_steps,
            "period_1": period_1,
            "period_2": period_2,
        }
