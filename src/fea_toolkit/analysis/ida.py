"""Incremental dynamic analysis (IDA).

Runs a ground-motion record over an intensity ladder, detecting collapse at the
first intensity that either fails to converge to the end of the record or
exceeds a peak-drift threshold, then refines that intensity by bisection.  The
generic driver re-runs
:func:`~fea_toolkit.analysis.nonlinear_dynamic.run_nonlinear_dynamic_analysis`
at each rung, so it works on any prepared ``MeshModel``; the benchmark-specific
drivers in :mod:`fea_toolkit.benchmarks` remain the rigorous per-storey versions
and share the collapse criteria below.

The intensity measure is ``Sa(T1, 5 %)/g`` (the record's 5 %-damped spectral
acceleration at the first-mode period, normalised by gravity), computed once
per run by :func:`fea_toolkit.spectrum.sdof_spectral_acceleration`.

The heavy OpenSees import lives inside :func:`run_ida` (through the
nonlinear-dynamic runner), so importing this module does not load OpenSees.
"""

from collections.abc import Sequence
from typing import Any, Optional

import numpy as np

from .base import AnalysisResult

__all__ = ["collapse_criterion", "collapse_intensity", "run_ida"]

#: Ceiling on bisection refinements, so a tolerance that cannot be represented
#: in floating point (or a stalled bracket) terminates instead of re-running the
#: transient analysis indefinitely.
_BISECTION_MAX_ITER = 100


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


def _node_z(mesh_model: Any) -> dict:
    """``{OpenSees node tag: z}`` for the mesh's recorded nodes.

    Transient results key per-node histories by the OpenSees node tag, while
    the mesh stores nodes by id with a ``node_tag`` attribute — this re-keys
    the Z coordinates to the tag space so storey assignment can index the
    displacement histories directly.
    """
    nodes = getattr(mesh_model, "nodes", {}) or {}
    return {
        node.node_tag: float(getattr(node, "z", 0.0))
        for node in nodes.values()
        if getattr(node, "node_tag", None) is not None
    }


def _node_xy(mesh_model: Any) -> dict:
    """``{OpenSees node tag: (x, y)}`` for the mesh's recorded nodes.

    Complements :func:`_node_z`: the rigid-body drift reduction needs each
    node's plan position so the rotational (torsional) contribution to
    interstorey drift is preserved instead of cancelling in the level mean.
    """
    nodes = getattr(mesh_model, "nodes", {}) or {}
    return {
        node.node_tag: (float(getattr(node, "x", 0.0)), float(getattr(node, "y", 0.0)))
        for node in nodes.values()
        if getattr(node, "node_tag", None) is not None
    }


def _cluster_elevations(node_z: dict) -> list[float]:
    """Cluster the mesh's node Z coordinates into storey elevations.

    The bare :func:`run_ida` call has no ``StoryLevel`` list (only the verb
    passes one), so it derives levels from the mesh directly — the same
    fixed-anchor clustering :func:`identify_stories`' node-clustering strategy
    uses — and falls back to the roof proxy when fewer than two levels emerge.
    """
    from ..model.storey_response import storey_levels_from_z

    return [float(elev) for elev, _ in storey_levels_from_z(list(node_z.values()))]


def _fundamental_period(modal_result: Any) -> Optional[float]:
    """First-mode period (s) from a modal result, or ``None`` when unavailable.

    :func:`~fea_toolkit.analysis.modal.run_modal_analysis` stores its result as
    ``data = {"modal": {…}, "shapes": {…}}``, while the builder's own
    :meth:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder.run_modal_analysis`
    returns the flat modal dict — so both the nested ``{"modal": {"periods": …}}``
    and the flat ``{"periods": …}`` representations are read.  ``periods`` may be
    a list or a NumPy array, so it is sized with ``len`` (never truth-tested) and
    the first entry is the fundamental period (the list is ascending).
    """
    try:
        data = getattr(modal_result, "data", None)
        if not isinstance(data, dict):
            data = {"modal": data}
        modal = data.get("modal", data)
        if not isinstance(modal, dict):
            return None
        periods = modal.get("periods")
        if periods is None or np.ndim(periods) == 0 or len(periods) == 0:
            return None
        t1 = float(np.asarray(periods).ravel()[0])
        if t1 > 0.0:
            return t1
    except (KeyError, TypeError, IndexError, ValueError):
        pass
    return None


def _recorded_level_coverage(
    node_z: dict,
    displacements: dict,
    storey_elevations: Sequence[float],
    z_tolerance: float = 0.5,
) -> int:
    """Count of adjacent storey intervals both of whose levels are recorded.

    The interstorey path reduces drift over *consecutive* level pairs, so it
    needs both levels of every interval to carry a usable history.  Two
    non-consecutive covered levels — e.g. only the base and the roof of a
    three-level model — resolve no interval at all and would read as a zero
    drift, so this count of resolvable adjacent intervals lets
    :func:`_peak_drift` report the drift as unavailable unless every interval is
    resolvable.
    """
    elevations = np.unique(np.asarray([float(e) for e in storey_elevations], dtype=float))
    covered: set = set()
    for key, z in node_z.items():
        hist = displacements.get(key)
        if hist is None:
            continue
        arr = np.asarray(hist, dtype=float)
        usable = (arr.ndim == 1 or (arr.ndim == 2 and arr.shape[1] >= 2)) and arr.size > 0
        if not usable:
            continue
        level = int(np.argmin(np.abs(elevations - float(z))))
        if abs(elevations[level] - float(z)) <= z_tolerance / 2:
            covered.add(level)
    return sum(1 for j in range(1, len(elevations)) if j - 1 in covered and j in covered)


def _peak_drift(
    data: Any,
    height: float,
    node_z: dict,
    node_xy: Optional[dict],
    storey_elevations: Sequence[float],
) -> tuple[float, str]:
    """Peak drift ratio for a transient result — interstorey ``d_max`` or the roof proxy.

    With at least two storey elevations the drift is the peak interstorey-drift
    ratio (:func:`~fea_toolkit.model.storey_response.peak_interstorey_drift`,
    passed *node_xy* so the torsional contribution is preserved);
    otherwise the roof-drift proxy (max horizontal nodal displacement / height),
    which for a single-interval structure is the same quantity.  When storey
    levels exist but the recorded histories do not resolve every adjacent level
    interval — a recording restricted to one level, or to two non-consecutive
    levels (e.g. only the base and roof via ``transient_record_node_tags``) —
    the drift cannot be evaluated and is reported as ``(NaN, "unavailable")``
    rather than a zero that would read as "no drift".

    Returns:
        ``(drift_ratio, measure)`` where *measure* is ``"interstorey_d_max"``,
        ``"roof_proxy"`` or ``"unavailable"``.
    """
    if not isinstance(data, dict):
        return 0.0, "roof_proxy"
    displacements = data.get("displacements") or {}
    if not isinstance(displacements, dict):
        return 0.0, "roof_proxy"
    if len(storey_elevations) >= 2:
        from ..model.storey_response import peak_interstorey_drift

        elevations = np.unique(np.asarray([float(e) for e in storey_elevations], dtype=float))
        # The reducer differences each *adjacent* level pair, so the recorded
        # histories must resolve every one of those intervals — two non-consecutive
        # levels (e.g. only the base and the roof of a three-level model) produce
        # no resolvable interval and would read as a zero drift.
        if _recorded_level_coverage(node_z, displacements, storey_elevations) < len(elevations) - 1:
            return float("nan"), "unavailable"
        return (
            peak_interstorey_drift(node_z, displacements, storey_elevations, node_xy=node_xy),
            "interstorey_d_max",
        )
    peak = 0.0
    for hist in displacements.values():
        arr = np.asarray(hist, dtype=float)
        if arr.ndim == 2 and arr.shape[1] >= 2 and arr.size:
            peak = max(peak, float(np.max(np.abs(arr[:, :2]))))
    return (peak / height if height > 0.0 else 0.0), "roof_proxy"


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
    stories: Optional[list] = None,
) -> dict[str, Any]:
    """Incremental dynamic analysis of one record over an IM ladder.

    Re-runs the nonlinear-dynamic runner at each rung — scaling the record in
    memory (``accel_base``) — and reports the first collapse intensity, refined
    by bisection.  Collapse is the shared :func:`collapse_intensity` predicate:
    non-convergence to the end of the record **or** peak drift ratio above
    *collapse_drift*.

    The drift measure is the **peak interstorey-drift ratio** when storey
    levels are available (at least two), and the **roof-drift proxy** (max
    horizontal nodal displacement / structure height) otherwise.  When storey
    levels exist but the recorded histories do not resolve every adjacent level
    interval — a recording restricted to one level, or to two non-consecutive
    levels (e.g. only the base and roof via ``transient_record_node_tags``) —
    the drift cannot be evaluated and is reported as ``NaN`` with :attr:`drift_measure` = ``"unavailable"``.  The
    :attr:`drift_measure` field records which of the three was used, so a proxy
    or unavailable value is never misread as interstorey drift.

    The intensity measure is the record's **``Sa(T1, 5 %)/g``** — its 5 %-damped
    spectral acceleration at the first-mode period, normalised by gravity,
    computed once from the unscaled record via
    :func:`~fea_toolkit.spectrum.sdof_spectral_acceleration` and reported as
    :attr:`Sa_T1_g`.  Because the SDOF oscillator is linear, the physical IM at
    any rung is ``scale × Sa_T1_g`` and the collapse capacity is
    ``collapse_scale × Sa_T1_g`` (:attr:`collapse_Sa_T1_g`).  When the modal
    result carries no period, the driver falls back to scale-as-IM
    (``Sa_T1_g = 1.0``, :attr:`sa_method` = ``"scale_as_im"``).

    Args:
        mesh_model: Pre-processed topology (needs ``nodes`` with ``z`` and
            ``node_tag``).
        modal_result: A preceding :func:`run_modal_analysis` result.
        ground_motion_file: Record path (one acceleration value per line); used
            as the result label and to load ``accel_base`` when not supplied.
        intensities: Ascending record scale factors (the IM ladder).
        dt: Integration time step (s).
        num_steps: Integration steps per rung.  Reconciled to
            ``len(accel_base) - 1`` — the step count
            :func:`~fea_toolkit.spectrum.sdof_spectral_acceleration` integrates
            — so the transient covers exactly the excitation interval the
            spectral IM covers.
        direction: Excitation direction (``"X"``/``"Y"``/``"Z"``).
        damping_ratio: Rayleigh damping ratio.
        collapse_drift: Peak drift-ratio collapse threshold (``None`` disables
            the drift proxy).
        tolerance: Bisection relative tolerance on the collapse intensity
            (``None`` disables bisection).
        max_scale: Optional upper bound for geometric ladder extension.
        config: Builder config overrides.
        gravity_patterns: Gravity pattern scale dict for the transient's
            gravity stage.
        name: Result label.
        accel_base: Pre-loaded record; when ``None``, read from
            *ground_motion_file*.
        stories: Optional :class:`~fea_toolkit.model.stories.StoryLevel` list
            (the storey levels ``identify_stories`` resolved from the parsed
            model).  When omitted, levels are clustered from the mesh's node Z
            coordinates; fewer than two levels falls back to the roof proxy.

    Returns:
        The IDA result dict — ``intensities``, ``drifts``, ``converged``,
        ``collapse_intensity``, ``collapse_criterion``, ``collapse_drift``,
        ``bisection``, ``record``, ``structure_height``, the drift provenance
        (``drift_measure``, ``storey_method``, ``storey_confidence``,
        ``n_storeys``), the intensity-measure provenance (``T1``, ``Sa_T1``,
        ``Sa_T1_g``, ``sa_method``, ``collapse_Sa_T1_g``) and the retained
        collapse-rung response (``transient`` — the full time-history ``data``
        of the first rung that collapsed, or the highest rung evaluated when
        nothing collapsed — and its ``transient_scale``).
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
    # The spectral IM is computed over the whole record by
    # ``sdof_spectral_acceleration``, which integrates ``len(accel) - 1``
    # Newmark steps (``accel[1:]`` — ``accel[0]`` is only the quiescent initial
    # condition).  Reconcile ``num_steps`` to ``len(accel) - 1`` so the transient
    # covers the same excitation interval: the Path series is defined only
    # through ``accel[-1]``, so one extra step would append an unexcited zero
    # tail the spectral IM never sees.
    num_steps = max(len(accel_base) - 1, 0)

    height = _structure_height(mesh_model)
    node_z = _node_z(mesh_model)
    node_xy = _node_xy(mesh_model)
    if stories:
        storey_elevations = [float(s.elevation) for s in stories]
        storey_method = getattr(stories[0], "method", None) or "identify_stories"
        storey_confidence = getattr(stories[0], "confidence", None)
    else:
        storey_elevations = _cluster_elevations(node_z)
        storey_method = "node_clustering" if len(storey_elevations) >= 2 else "roof_proxy"
        storey_confidence = "low" if len(storey_elevations) >= 2 else None
    # Resolve the structural base (the lowest node elevation) and include it in
    # the ladder, so the base-to-first-floor interval contributes to the peak
    # interstorey drift instead of being dropped when the story list omits it.
    if node_z:
        base_elevation = min(node_z.values())
        if not any(abs(float(e) - base_elevation) <= 1e-9 for e in storey_elevations):
            storey_elevations = sorted([base_elevation, *storey_elevations])
    # Normalize the ladder before it is counted or handed to the drift
    # reduction: duplicate (equivalent) elevations would otherwise inflate the
    # level count and flip ``drift_measure`` while :func:`peak_interstorey_drift`
    # sees a different, deduplicated ladder.  Both the measure choice and the
    # drift calculation must use the same normalized elevations.
    storey_elevations = sorted({float(e) for e in storey_elevations})
    drift_measure = "interstorey_d_max" if len(storey_elevations) >= 2 else "roof_proxy"

    # ── Record intensity measure — Sa(T1, 5 %)/g ─────────────────────
    # The record's 5 %-damped spectral acceleration at the first-mode period,
    # normalised by gravity, is the IK-2005 intensity measure.  It is computed
    # once from the unscaled record; because the SDOF oscillator is linear, the
    # physical IM at any rung is ``scale × Sa_T1_g``.  When a period cannot be
    # resolved the driver falls back to scale-as-IM (``Sa_T1_g = 1.0``).
    from .._unit_scaling import g_from_units
    from ..spectrum import sdof_spectral_acceleration

    t1 = _fundamental_period(modal_result)
    units = getattr(mesh_model, "units", None)
    g = g_from_units(units) if units else None
    if t1 is not None and g and g > 0.0 and dt > 0.0 and accel_base.size:
        sa_t1 = sdof_spectral_acceleration(accel_base, dt, t1, zeta=0.05)
        sa_t1_g = float(sa_t1 / g)
        sa_method = "sdof_newmark"
    else:
        sa_t1 = None
        sa_t1_g = 1.0
        sa_method = "scale_as_im"

    def _run(im: float) -> tuple[float, bool, dict, str]:
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
        drift, measure = _peak_drift(result.data, height, node_z, node_xy, storey_elevations)
        converged = bool(result.data.get("converged_steps") == num_steps)
        return drift, converged, result.data, measure

    # ── Collapse-rung transient retention ─────────────────────────────
    # The full time-history response of the first rung that trips the collapse
    # criterion (or, when nothing collapses, the highest rung evaluated) is
    # retained so a standalone plotter can render the response at the extreme
    # intensity — beyond the scalar drift ladder.  Only these two histories are
    # ever held, so the memory cost stays bounded.
    collapse_data: Optional[dict] = None
    collapse_transient_im: Optional[float] = None
    last_data: Optional[dict] = None
    last_im: Optional[float] = None

    def _note(im: float, ok: bool, drift: float, data: dict) -> None:
        nonlocal collapse_data, collapse_transient_im, last_data, last_im
        last_data = data
        last_im = im
        if collapse_data is None and (
            not ok or (collapse_drift is not None and drift > collapse_drift)
        ):
            collapse_data = data
            collapse_transient_im = im

    ladder = [float(im) for im in intensities]
    drifts: list[float] = []
    converged: list[bool] = []
    for im in ladder:
        drift, ok, data, measure = _run(im)
        _note(im, ok, drift, data)
        drifts.append(drift)
        converged.append(ok)
        # A rung whose recorded histories cannot span two levels degrades the
        # whole run's drift measure to "unavailable".  Two causes map here, and
        # only the first is fixed across rungs:
        #   * the recording restriction (``transient_record_node_tags``) omits
        #     the nodes at some level — fixed for every rung; and
        #   * a rung that fails to converge at its first step records
        #     zero-length histories, so no level is usable — per-rung.
        # Because the second cause can first appear in a ladder-extension or
        # bisection rung, the check is repeated in those loops too rather than
        # done once up front.
        if measure == "unavailable":
            drift_measure = "unavailable"

    im_collapse = collapse_intensity(ladder, drifts, converged, collapse_drift)

    # ── Geometric ladder extension ──────────────────────────────────
    # Extend the top rung (doubling, capped at *max_scale*) until collapse, so a
    # collapse scale factor need not be known a priori.
    if max_scale is not None and im_collapse is None and ladder and ladder[-1] < max_scale:
        top = ladder[-1]
        while 0 < top < max_scale:
            top = min(2.0 * top, max_scale)
            drift, ok, data, measure = _run(top)
            _note(top, ok, drift, data)
            ladder.append(top)
            drifts.append(drift)
            converged.append(ok)
            if measure == "unavailable":
                drift_measure = "unavailable"
            if (not ok) or (collapse_drift is not None and drift > collapse_drift):
                im_collapse = top
                break

    # ── Bisection refinement (Ibarra–Krawinkler 2005) ───────────────
    bisection: Optional[dict[str, Any]] = None
    if tolerance is not None and im_collapse is not None:
        if not (np.isfinite(tolerance) and tolerance > 0.0):
            raise ValueError(f"tolerance must be finite and positive, got {tolerance!r}")
        idx = ladder.index(im_collapse)
        if idx > 0:
            lo = ladder[idx - 1]
            hi = im_collapse
            evaluations: list[dict[str, Any]] = []
            for _ in range(_BISECTION_MAX_ITER):
                if (hi - lo) / hi <= tolerance:
                    break
                mid = 0.5 * (lo + hi)
                if mid <= lo or mid >= hi:
                    # The bracket can no longer be narrowed in floating point;
                    # stop rather than re-running the same transient forever.
                    break
                drift, ok, data, measure = _run(mid)
                _note(mid, ok, drift, data)
                if measure == "unavailable":
                    drift_measure = "unavailable"
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

    transient = collapse_data if collapse_data is not None else last_data
    transient_scale = collapse_transient_im if collapse_data is not None else last_im

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
        "drift_measure": drift_measure,
        "storey_method": storey_method,
        "storey_confidence": storey_confidence,
        "n_storeys": len(storey_elevations),
        "T1": t1,
        "Sa_T1": sa_t1,
        "Sa_T1_g": sa_t1_g,
        "sa_method": sa_method,
        "collapse_Sa_T1_g": (im_collapse * sa_t1_g) if im_collapse is not None else None,
        "transient": transient,
        "transient_scale": transient_scale,
    }
