"""Incremental dynamic analysis (IDA).

Runs a ground-motion record over an intensity ladder, detecting collapse at the
first intensity that either fails to converge to the end of the record or
exceeds a peak-drift threshold, then refines that intensity by bisection.  The
generic driver re-runs
:func:`~fea_toolkit.analysis.nonlinear_dynamic.run_nonlinear_dynamic_analysis`
at each rung, so it works on any prepared ``MeshModel``; the benchmark-specific
drivers in :mod:`fea_toolkit.benchmarks` remain the rigorous per-storey versions
and share the collapse criteria below.

The heavy OpenSees import lives inside :func:`run_ida` (through the
nonlinear-dynamic runner), so importing this module does not load OpenSees.
"""

from collections.abc import Sequence
from typing import Any, Optional

import numpy as np

from .base import AnalysisResult

__all__ = ["collapse_criterion", "collapse_intensity", "run_ida"]


def collapse_intensity(
    intensities: Sequence[float],
    drifts: Sequence[float],
    converged: Sequence[bool],
    collapse_drift: Optional[float] = 0.10,
) -> Optional[float]:
    """First intensity at which the structure collapses.

    A run *collapses* when it either fails to converge to the end of the record
    (``converged`` is ``False`` — the Ibarra–Krawinkler non-convergence
    criterion) or its peak drift ratio exceeds *collapse_drift* (the dynamic
    instability / sidesway criterion).  The two criteria are evaluated in the
    order they appear; the first intensity that trips either one is the
    collapse intensity.

    Args:
        intensities: The IM ladder (record scale factors), ascending.
        drifts: Peak drift ratio per intensity.
        converged: ``converged_steps == num_steps`` per intensity.
        collapse_drift: Peak drift ratio above which the structure is deemed
            collapsed (default ``0.10``).  ``None`` disables the drift proxy.

    Returns:
        The collapse intensity (scale factor), or ``None`` if no run collapsed.
    """
    for im, drift, ok in zip(intensities, drifts, converged):
        if not ok or (collapse_drift is not None and drift > collapse_drift):
            return float(im)
    return None


def collapse_criterion(
    intensities: Sequence[float],
    drifts: Sequence[float],
    converged: Sequence[bool],
    collapse_drift: Optional[float] = 0.10,
) -> Optional[str]:
    """Which collapse criterion tripped first on the ladder.

    Mirrors :func:`collapse_intensity` (same first-wins order): a run collapses
    when it fails to converge to the end of the record (``"nonconvergence"``)
    or its peak drift ratio exceeds *collapse_drift* (``"drift"``).  The first
    intensity to trip either one is the collapse intensity; this returns the
    criterion that tripped there.

    Args:
        intensities: The IM ladder (record scale factors), ascending.
        drifts: Peak drift ratio per intensity.
        converged: ``converged_steps == num_steps`` per intensity.
        collapse_drift: Peak drift ratio above which the structure is deemed
            collapsed (default ``0.10``).  ``None`` disables the drift proxy.

    Returns:
        ``"nonconvergence"`` if the first collapsed rung failed to converge,
        ``"drift"`` if it converged but exceeded *collapse_drift*, or ``None``
        if no rung collapsed.
    """
    for _, drift, ok in zip(intensities, drifts, converged):
        if not ok:
            return "nonconvergence"
        if collapse_drift is not None and drift > collapse_drift:
            return "drift"
    return None


def _structure_height(model: Any) -> float:
    """Vertical span of the model's nodes — the drift-ratio denominator.

    A zero-height model (or one without nodes) returns ``1.0`` so the ratio is
    a plain displacement rather than a division by zero.
    """
    nodes = getattr(model, "nodes", {}) or {}
    zs = [float(getattr(node, "z", 0.0)) for node in nodes.values()]
    if not zs:
        return 1.0
    return max(0.0, max(zs) - min(zs)) or 1.0


def _peak_roof_drift(data: Any, height: float) -> float:
    """Peak roof-drift ratio — max horizontal nodal displacement / height.

    The generic driver has no storey map, so it uses the largest horizontal
    (X/Y) nodal displacement over the record as the drift measure, normalised
    by the structure height — a first-mode roof-drift proxy for the FEMA P-695
    sidesway criterion, distinct from the benchmark's per-storey ``d_max``.
    """
    if not isinstance(data, dict):
        return 0.0
    displacements = data.get("displacements") or {}
    if not isinstance(displacements, dict):
        return 0.0
    peak = 0.0
    for hist in displacements.values():
        arr = np.asarray(hist, dtype=float)
        if arr.ndim == 2 and arr.shape[1] >= 2 and arr.size:
            peak = max(peak, float(np.max(np.abs(arr[:, :2]))))
    return peak / height if height > 0.0 else 0.0


def run_ida(
    mesh_model: Any,
    modal_result: AnalysisResult,
    ground_motion_file: str,
    intensities: Sequence[float],
    *,
    dt: float = 0.005,
    num_steps: int = 1000,
    direction: str = "X",
    damping_ratio: float = 0.05,
    collapse_drift: Optional[float] = 0.10,
    tolerance: Optional[float] = 0.01,
    max_scale: Optional[float] = None,
    config: Optional[dict] = None,
    gravity_patterns: Optional[dict[str, float]] = None,
    name: str = "IDA",
    accel_base: Optional[np.ndarray] = None,
) -> dict[str, Any]:
    """Incremental dynamic analysis of one record over an IM ladder.

    Re-runs the nonlinear-dynamic runner at each rung — scaling the record in
    memory (``accel_base``) — and reports the first collapse intensity, refined
    by bisection.  Collapse is the shared :func:`collapse_intensity` predicate:
    non-convergence to the end of the record **or** peak roof-drift ratio above
    *collapse_drift*.

    Args:
        mesh_model: Pre-processed topology (needs ``nodes`` with ``z``).
        modal_result: A preceding :func:`run_modal_analysis` result.
        ground_motion_file: Record path (one acceleration value per line); used
            as the result label and to load ``accel_base`` when not supplied.
        intensities: Ascending record scale factors (the IM ladder).
        dt: Integration time step (s).
        num_steps: Integration steps per rung.
        direction: Excitation direction (``"X"``/``"Y"``/``"Z"``).
        damping_ratio: Rayleigh damping ratio.
        collapse_drift: Peak roof-drift ratio collapse threshold (``None``
            disables the drift proxy).
        tolerance: Bisection relative tolerance on the collapse intensity
            (``None`` disables bisection).
        max_scale: Optional upper bound for geometric ladder extension.
        config: Builder config overrides.
        gravity_patterns: Gravity pattern scale dict for the transient's
            gravity stage.
        name: Result label.
        accel_base: Pre-loaded record; when ``None``, read from
            *ground_motion_file*.

    Returns:
        The IDA result dict — ``intensities``, ``drifts``, ``converged``,
        ``collapse_intensity``, ``collapse_criterion``, ``collapse_drift``,
        ``bisection``, ``record`` and ``structure_height`` — mirroring the
        benchmark ``_ida_run`` shape.
    """
    from .nonlinear_dynamic import run_nonlinear_dynamic_analysis

    if accel_base is None:
        try:
            accel_base = np.loadtxt(ground_motion_file, ndmin=1)
        except Exception as e:
            raise ValueError(
                f"Cannot read ground motion file: {ground_motion_file}. "
                "Each line should be one acceleration value in model acceleration units."
            ) from e
    accel_base = np.asarray(accel_base, dtype=float)
    if accel_base.ndim != 1:
        raise ValueError("The ground motion must be a single column of acceleration values.")

    height = _structure_height(mesh_model)

    def _run(im: float) -> tuple[float, bool]:
        accel = accel_base * im
        result = run_nonlinear_dynamic_analysis(
            mesh_model,
            modal_result,
            ground_motion_file,
            dt=dt,
            num_steps=num_steps,
            direction=direction,
            damping_ratio=damping_ratio,
            name=name,
            config=config,
            gravity_patterns=gravity_patterns,
            accel=accel,
        )
        drift = _peak_roof_drift(result.data, height)
        converged = bool(result.data.get("converged_steps") == num_steps)
        return drift, converged

    ladder = [float(im) for im in intensities]
    drifts: list[float] = []
    converged: list[bool] = []
    for im in ladder:
        drift, ok = _run(im)
        drifts.append(drift)
        converged.append(ok)

    im_collapse = collapse_intensity(ladder, drifts, converged, collapse_drift)

    # ── Geometric ladder extension ──────────────────────────────────
    # Extend the top rung (doubling, capped at *max_scale*) until collapse, so a
    # collapse scale factor need not be known a priori.
    if max_scale is not None and im_collapse is None and ladder and ladder[-1] < max_scale:
        top = ladder[-1]
        while 0 < top < max_scale:
            top = min(2.0 * top, max_scale)
            drift, ok = _run(top)
            ladder.append(top)
            drifts.append(drift)
            converged.append(ok)
            if (not ok) or (collapse_drift is not None and drift > collapse_drift):
                im_collapse = top
                break

    # ── Bisection refinement (Ibarra–Krawinkler 2005) ───────────────
    bisection: Optional[dict[str, Any]] = None
    if tolerance is not None and im_collapse is not None:
        idx = ladder.index(im_collapse)
        if idx > 0:
            lo = ladder[idx - 1]
            hi = im_collapse
            evaluations: list[dict[str, Any]] = []
            while (hi - lo) / hi > tolerance:
                mid = 0.5 * (lo + hi)
                drift, ok = _run(mid)
                evaluations.append({"intensity": mid, "drift": drift, "converged": ok})
                if (not ok) or (collapse_drift is not None and drift > collapse_drift):
                    hi = mid
                else:
                    lo = mid
            bisection = {
                "lo": lo,
                "hi": hi,
                "tolerance": float(tolerance),
                "evaluations": evaluations,
            }
            im_collapse = 0.5 * (lo + hi)

    return {
        "record": ground_motion_file,
        "intensities": ladder,
        "drifts": drifts,
        "converged": converged,
        "collapse_intensity": im_collapse,
        "collapse_criterion": collapse_criterion(ladder, drifts, converged, collapse_drift),
        "collapse_drift": collapse_drift,
        "bisection": bisection,
        "structure_height": height,
    }
