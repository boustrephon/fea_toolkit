"""IK-2005 generic-frame collapse (IDA) runner.

Runs an incremental dynamic analysis over a record ladder, reports the first
collapse scale factor (refined by bisection), and converts it to the
spectral-acceleration collapse capacity ``Sa,c(T1)``.  Dividing by ``g`` and by
the strength coefficient ``γ = Vy/W = 1.0`` gives the IK-2005 collapse
intensity ``(Sa,c/g)/γ`` — their MDOF intensity measure (§5.2), benchmarked at
``≈ 3.3`` for ``T1 ≈ 1 s``.

Reuses the record-free collapse machinery from
:mod:`~fea_toolkit.benchmarks.sac_run` (``collapse_intensity``,
``sdof_spectral_acceleration``, ``floor_levels``, ``compute_metrics``,
``record_path``).  The IK-2005-specific settings are the P-Δ + held
``1.4 × seismic mass`` gravity, **5 % Rayleigh** damping on ``T1`` + the
95 %-participation mode (§5.3.1, Appendix B.4) and the peak-oriented
``IMKPeakOriented`` hinge (already in
:func:`~fea_toolkit.benchmarks.generic_frame.builder_config`).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any, Optional

import numpy as np

from .._unit_scaling import g_from_units
from ..io.ground_motion import read_peer_record, read_time_history_csv
from . import generic_frame as gf
from .generic_frame_data import FRAME_DATA
from .sac_run import (
    collapse_criterion,
    collapse_intensity,
    compute_metrics,
    floor_levels,
    sdof_spectral_acceleration,
)

#: IK-2005 damping ratio (ξ = 5 %, Rayleigh — §5.3.1, Appendix B.4 Eq B.8).
_DAMPING = 0.05
#: Peak interstorey-drift ratio collapse threshold.  ``None`` disables the
#: drift proxy so collapse is judged by non-convergence (dynamic instability)
#: alone — the IK-2005 §5.5 definition.  The FEMA P-695 sidesway proxy (0.10)
#: remains available for the SAC path via ``sac_run``'s default.
_COLLAPSE_DRIFT: Optional[float] = None


def period_1(code: str) -> float:
    """The frame's fundamental period ``T1 = 0.1N`` / ``0.2N`` (s)."""
    return float(FRAME_DATA[code]["T1"])


def _trips_collapse(converged: bool, drift: float, collapse_drift: Optional[float]) -> bool:
    """Whether a ladder rung trips collapse.

    ``True`` when the rung failed to converge (dynamic instability) or — when
    the drift proxy is enabled — its peak interstorey-drift ratio exceeds
    *collapse_drift*.  ``collapse_drift=None`` disables the proxy, so only
    non-convergence trips collapse (the IK-2005 §5.5 definition).
    """
    if not converged:
        return True
    return collapse_drift is not None and drift > collapse_drift


def _rayleigh_periods(code: str) -> tuple[float, float]:
    """Rayleigh anchor periods ``(T1, T95)`` for *code*.

    IK-2005 assigns 5 % Rayleigh damping to the first mode and to the mode at
    which the cumulative X-direction mass participation exceeds 95 % (§5.3.1,
    Appendix B.4 Eq B.8: ``C = αM + βK₀``).  The periods come from the
    elastic-initial model (P-Δ off; the IMK springs sit at their elastic
    stiffness before loading, so the full
    :func:`~fea_toolkit.benchmarks.generic_frame.build_builder` eigen-solve
    already yields the elastic periods).

    Returns:
        ``(T1, T95)`` in seconds; ``T95`` falls back to ``T1`` when the modal
        solve returns no periods.
    """
    from openseespy.opensees import wipe

    n = int(FRAME_DATA[code]["N"])
    wipe()
    try:
        builder = gf.build_builder(code, pdelta=False)
        modal = builder.run_modal_analysis(num_modes=n, print_results=False, eigen_solver="default")
        periods = list(modal.get("periods", []))
        # ``partiMassRatiosMX`` is already in percent (OpenSees ``-unorm``).
        ratios = list(modal.get("modal_props", {}).get("partiMassRatiosMX", []))
    finally:
        wipe()

    if not periods:
        return period_1(code), period_1(code) * 0.5
    t1 = float(periods[0])
    cum = 0.0
    t95 = t1
    for p, r in zip(periods, ratios):
        try:
            cum += float(r)
        except (TypeError, ValueError):
            continue
        if cum >= 95.0:
            t95 = float(p)
            break
    return t1, t95


def first_mode_participation(code: str) -> float:
    """First-mode participation factor ``PF1`` (mode normalised to 1 at the roof).

    IK-2003 (PEER 2003/15) §4.3 anchors the elastic roof-drift demand at
    ``PF1``: for ``[Sa(T1)/g]/γ ≈ 1`` the median normalised roof drift
    ``θr,max / [Sd(T1)/H] ≈ PF1``.  ``PF1`` is ``Γ1 = Σ mᵢφᵢ / Σ mᵢφᵢ²`` over
    the lumped masses ``mᵢ`` and the roof-normalised X-mode shape ``φᵢ``.
    Because the generic frames are tuned to a straight-line first mode with
    near-uniform floor masses, ``PF1 ≈ 3N/(2N+1)`` (≈ 1.29 → 1.46 for
    N = 3 → 18).
    """
    from openseespy.opensees import wipe

    n = int(FRAME_DATA[code]["N"])
    wipe()
    try:
        builder = gf.build_builder(code, pdelta=False)
        modal = builder.run_modal_analysis(num_modes=1, print_results=False)
        masses = modal.get("nodal_masses", {})
        shapes = builder.extract_mode_shapes(1)
        roof_tag = builder.mesh_model.nodes[f"{n}-0"].node_tag
        roof_dx = shapes[0][roof_tag][0]
        num = den = 0.0
        for tag, (dx, _dy, _dz) in shapes[0].items():
            phi = dx / roof_dx
            m = float(masses.get(tag, 0.0))
            num += m * phi
            den += m * phi * phi
    finally:
        wipe()

    return num / den if den > 0.0 else float("nan")


def modal_participation_factors(code: str, num_modes: int = 5) -> list[float]:
    """Participation factors ``PF1..PFk`` in the paper's mode normalisation.

    MK-2003 (PEER 2003/15) Tables A.1--A.6 report participation factors for the
    first five modes.  Their normalisation is: mode 1 to unit roof displacement
    (a straight-line ``φ1``); every higher mode to unit ``max|φ|`` over the
    floor levels (base = 0).  ``Γi = Σ mᵢφᵢ / Σ mᵢφᵢ²`` over the floor masses
    with ``φ`` re-normalised to that convention.

    Args:
        code: Generic-frame code (used for ``N`` and the mass/stiffness layout).
        num_modes: Number of modes to return (default 5).

    Returns:
        ``[PF1, PF2, ...]`` — ``NaN`` for a mode with a vanishing normalisation
        (not expected for the first ``min(N, 5)`` lateral modes).
    """
    from openseespy.opensees import wipe

    n = int(FRAME_DATA[code]["N"])
    wipe()
    try:
        builder = gf.build_builder(code, pdelta=False)
        modal = builder.run_modal_analysis(num_modes=num_modes, print_results=False)
        masses = modal.get("nodal_masses", {})
        shapes = builder.extract_mode_shapes(num_modes)
        floor_tags = [builder.mesh_model.nodes[f"{level}-0"].node_tag for level in range(1, n + 1)]
        pfs: list[float] = []
        for mode in range(num_modes):
            dxs = [shapes[mode][tag][0] for tag in floor_tags]
            # Paper convention: mode 1 → roof = 1; modes 2+ → max|φ| = 1.
            scale = dxs[-1] if mode == 0 else max(abs(d) for d in dxs)
            if abs(scale) < 1e-12:
                pfs.append(float("nan"))
                continue
            num = den = 0.0
            for tag, dx in zip(floor_tags, dxs):
                phi = dx / scale
                m = float(masses.get(tag, 0.0))
                num += m * phi
                den += m * phi * phi
            pfs.append(num / den if den > 0.0 else float("nan"))
    finally:
        wipe()
    return pfs


def drift_demands(
    code: str,
    record_ids: Sequence[str],
    ratios: Sequence[float] = (1.0, 2.0, 4.0),
    *,
    dt: float = 0.005,
    damping: float = _DAMPING,
    pdelta: bool = True,
    pdelta_gravity: bool = True,
    record_channels: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Median normalised roof-drift demand vs ``[Sa(T1)/g]/γ`` (IK-2003 §4.3).

    For each record and each target relative-intensity ratio ``R``, scales the
    record so ``[Sa(T1)/g]/γ = R`` (with ``γ = Vy/W = 1.0``), runs one NLTH and
    reports the normalised maximum roof-drift angle ``θr,max / [Sd(T1)/H] =
    roof_disp / Sd(T1)``.  IK-2003 (PEER 2003/15) §4.3 anchors this at ``PF1``
    for ``R ≈ 1`` (the elastic roof drift is dominated by the first mode); it
    then grows with ``R`` as P-Δ / higher modes contribute.  Same frame, P-Δ +
    held gravity and Rayleigh damping as the collapse runner, but **no collapse
    detection** — just the peak roof drift at each target ratio.

    Args:
        code: Generic-frame code.
        record_ids: Record identifiers (the LMSR-N set).
        ratios: Target ``[Sa(T1)/g]/γ`` levels (default ``(1, 2, 4)``).
        dt: Integration/resampling step (s).
        damping: Damping ratio (Rayleigh).
        pdelta: When True, emit the ``PDelta`` geometric transformation.
        pdelta_gravity: When True, hold the IK-2005 gravity load.
        record_channels: Optional ``record_id → channel`` map (see
            :func:`_load_record`).

    Returns:
        ``{"code", "T1", "T95", "PF1", "H", "ratios", "normalized", "median"}``
        — ``normalized`` maps ``ratio → {record_id: (normalized_drift,
        converged)}`` and ``median`` maps ``ratio → median over converged
        records`` (``NaN`` when none converged).
    """
    from openseespy.opensees import wipe

    g = g_from_units(gf.UNITS)
    t1, t2 = _rayleigh_periods(code)
    pf1 = first_mode_participation(code)
    h = gf.storey_height_m() * float(FRAME_DATA[code]["N"])

    normalized: dict[float, dict[str, tuple[float, bool]]] = {float(r): {} for r in ratios}
    for rid in record_ids:
        channel = record_channels.get(rid) if record_channels else None
        accel_base, dt_ = _load_record(rid, dt, channel=channel)
        sa = sdof_spectral_acceleration(accel_base, dt_, t1, zeta=0.05)
        for r in ratios:
            im = float(r) * 1.0 * g / sa  # γ = 1.0
            accel = np.asarray(accel_base, dtype=float) * im
            wipe()
            try:
                builder = gf.build_builder(code, pdelta=pdelta, pdelta_gravity=pdelta_gravity)
                levels = floor_levels(builder)
                tags = [t for _, t in levels]
                builder.config["transient_record_node_tags"] = tags
                builder.config["transient_record_kinematics"] = True
                res = builder.run_transient_analysis(
                    accel,
                    dt_,
                    direction="X",
                    damping_ratio=damping,
                    damping_type="rayleigh",
                    period_1=t1,
                    period_2=t2,
                    gravity_patterns={"GRAV": 1.0} if pdelta_gravity else None,
                )
                m = compute_metrics(res, accel, levels)
            finally:
                wipe()

            # Sd(T1) at the scaled intensity: Sa = im·sa = r·γ·g = r·g.
            sd = (im * sa) * (t1 / (2.0 * math.pi)) ** 2
            converged = bool(m["converged_steps"] == m["num_steps"])
            normalized[float(r)][rid] = (
                m["roof_disp"] / sd if sd > 0.0 else float("nan"),
                converged,
            )

    median: dict[float, float] = {}
    for r, per_record in normalized.items():
        vals = [v for v, ok in per_record.values() if ok and not math.isnan(v)]
        median[r] = float(np.median(vals)) if vals else float("nan")
    return {
        "code": code,
        "T1": t1,
        "T95": t2,
        "PF1": pf1,
        "H": h,
        "ratios": [float(r) for r in ratios],
        "normalized": normalized,
        "median": median,
    }


def _load_record(
    record_id: str, dt: float, *, channel: Optional[str] = None
) -> tuple[np.ndarray, float]:
    """Load a record from the local/public mirror as ``(accel, dt)`` in m/s².

    PEER NGA waveforms are downloaded as ``.AT2`` (native, g → m/s² via
    :func:`~fea_toolkit.io.ground_motion.read_peer_record`); the SAC mirror uses
    ``.csv`` (``time,accel`` in m/s²).  Either is accepted, ``.AT2`` first.  The
    signal is resampled to *dt* when the native sampling differs.

    Multi-component mirrors store one file per channel, ``<record_id>_<channel>.csv``
    (the USGS records fetched by ``scripts/fetch_usgs_records.py``).  Pass
    *channel* to read ``<record_id>_<channel>.csv`` instead of the single-file
    ``<record_id>.csv`` / ``.AT2``.

    Args:
        record_id: Record identifier (an LMSR-N id such as ``"IV79cal"``).
        dt: Target integration step (s).
        channel: Optional channel suffix (e.g. ``"a"``) for a multi-component
            mirror; ``None`` reads the single-file record.

    Returns:
        ``(accel, dt)`` — resampled acceleration samples (m/s²) and the step.

    Raises:
        FileNotFoundError: If the record is absent from both mirrors.
    """
    from .ik2005_records import ground_motion_dir

    suffix = f"_{channel}" if channel else ""
    root = ground_motion_dir()
    for sub in ("local", "public"):
        for ext in (".AT2", ".csv"):
            path = root / sub / f"{record_id}{suffix}{ext}"
            if not path.exists():
                continue
            if ext == ".AT2":
                times, accel = read_peer_record(str(path))
            else:
                times, accel = read_time_history_csv(str(path))
            dt0 = float(times[1] - times[0])
            if abs(dt0 - dt) > 1e-9:
                t_new = np.arange(0.0, times[-1] + dt, dt)
                accel = np.interp(t_new, times, accel)
            return np.asarray(accel, dtype=float), float(dt)
    raise FileNotFoundError(
        f"record {record_id!r} not found in data/ground_motion/{{local,public}} "
        f"(expected {record_id}{suffix}.AT2 or {record_id}{suffix}.csv)"
    )


def _ida_run(
    code: str,
    record_id: str,
    accel_base: np.ndarray,
    dt: float,
    intensities: Sequence[float],
    *,
    collapse_drift: Optional[float] = _COLLAPSE_DRIFT,
    damping: float = _DAMPING,
    tolerance: Optional[float] = None,
    max_scale: Optional[float] = None,
    pdelta: bool = True,
    pdelta_gravity: bool = True,
    damping_type: str = "rayleigh",
) -> dict[str, Any]:
    """Run one IDA ladder for *code* (a base record scaled over *intensities*).

    Mirrors :func:`fea_toolkit.benchmarks.sac_run._ida_run`, with the generic
    frame (``gf.build_builder``, peak-oriented IMK, P-Δ + held gravity) and
    Rayleigh damping in place of the SAC building.  Each
    ``run_transient_analysis`` call rebuilds the domain, so no intensity sees
    the previous intensity's state.

    Args:
        code: Generic-frame code (e.g. ``"0909"``).
        record_id: Record label (provenance only — any identifier works).
        accel_base: Base acceleration samples (length/s²), one per time step.
        dt: Integration time step (s).
        intensities: The IM ladder (ascending scale factors on *accel_base*).
        collapse_drift: Peak interstorey-drift ratio collapse threshold (``None`` disables the drift proxy).
        damping: Damping ratio (Rayleigh).
        damping_type: ``"rayleigh"`` (default) or ``"mass_proportional"``.
        tolerance: Relative tolerance for the bisection refinement of the
            collapse intensity (``None`` disables bisection).
        max_scale: Optional upper bound on the ladder (geometric extension).
        pdelta: When True, emit the ``PDelta`` geometric transformation.
        pdelta_gravity: When True, hold the IK-2005 ``1.4 × seismic mass``
            gravity load through the analysis.

    Returns:
        ``{"code", "record", "intensities", "drifts", "converged",
        "collapse_intensity", "collapse_criterion", "collapse_drift",
        "bisection"}``.
    """
    from openseespy.opensees import wipe

    t1, t2 = _rayleigh_periods(code)

    wipe()
    try:
        builder = gf.build_builder(code, pdelta=pdelta, pdelta_gravity=pdelta_gravity)
        levels = floor_levels(builder)
        tags = [t for _, t in levels]
        builder.config["transient_record_node_tags"] = tags
        builder.config["transient_record_kinematics"] = True

        def _eval(im: float) -> tuple[float, bool]:
            accel = np.asarray(accel_base, dtype=float) * im
            res = builder.run_transient_analysis(
                accel,
                dt,
                direction="X",
                damping_ratio=damping,
                damping_type=damping_type,
                period_1=t1,
                period_2=t2,
                gravity_patterns={"GRAV": 1.0} if pdelta_gravity else None,
            )
            m = compute_metrics(res, accel, levels)
            return float(m["d_max"]), bool(m["converged_steps"] == m["num_steps"])

        ladder = [float(im) for im in intensities]
        drifts: list[float] = []
        converged: list[bool] = []
        for im in ladder:
            d, c = _eval(im)
            drifts.append(d)
            converged.append(c)

        im_collapse = collapse_intensity(ladder, drifts, converged, collapse_drift)

        # ── Geometric ladder extension ───────────────────────────
        if max_scale is not None and im_collapse is None and ladder and ladder[-1] < max_scale:
            top = ladder[-1]
            while 0 < top < max_scale:
                top = min(2.0 * top, max_scale)
                d, c = _eval(top)
                ladder.append(top)
                drifts.append(d)
                converged.append(c)
                if _trips_collapse(c, d, collapse_drift):
                    im_collapse = top
                    break

        # ── Bisection refinement (Ibarra–Krawinkler 2005) ──────────
        bisection: Optional[dict[str, Any]] = None
        if tolerance is not None and im_collapse is not None:
            idx = ladder.index(im_collapse)
            if idx > 0:
                lo = ladder[idx - 1]
                hi = im_collapse
                evaluations: list[dict[str, Any]] = []
                while (hi - lo) / hi > tolerance:
                    mid = 0.5 * (lo + hi)
                    d, c = _eval(mid)
                    evaluations.append({"intensity": mid, "drift": d, "converged": c})
                    if _trips_collapse(c, d, collapse_drift):
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
    finally:
        wipe()

    return {
        "code": code,
        "record": record_id,
        "intensities": ladder,
        "drifts": drifts,
        "converged": converged,
        "collapse_intensity": im_collapse,
        "collapse_criterion": collapse_criterion(ladder, drifts, converged, collapse_drift),
        "collapse_drift": collapse_drift,
        "bisection": bisection,
    }


def run_ida(
    code: str,
    record_id: str,
    intensities: Sequence[float],
    *,
    dt: float = 0.005,
    collapse_drift: Optional[float] = _COLLAPSE_DRIFT,
    damping: float = _DAMPING,
    tolerance: float = 0.01,
    max_scale: Optional[float] = None,
    pdelta: bool = True,
    pdelta_gravity: bool = True,
    channel: Optional[str] = None,
    damping_type: str = "rayleigh",
) -> dict[str, Any]:
    """Incremental dynamic analysis of one record over an IM ladder.

    Loads the record from the ground-motion mirror (``record_path``), resamples
    it to *dt*, then scales it over *intensities* and reports the first collapse
    scale factor, refined by bisection to *tolerance*.

    Args:
        code: Generic-frame code.
        record_id: Record identifier (a key of the ground-motion manifest, e.g.
            an LMSR-N record).
        intensities: Ascending record scale factors (the IM ladder).
        dt: Integration/resampling step (s; default 0.005).
        collapse_drift: Peak interstorey-drift ratio collapse threshold (``None`` disables the drift proxy).
        damping: Damping ratio (Rayleigh).
        damping_type: ``"rayleigh"`` (default) or ``"mass_proportional"``.
        tolerance: Bisection relative tolerance (default 0.01).
        max_scale: Optional upper bound on the ladder (geometric extension).
        pdelta: When True, emit the ``PDelta`` geometric transformation.
        pdelta_gravity: When True, hold the IK-2005 gravity load.
        channel: Optional channel suffix for a multi-component mirror (see
            :func:`_load_record`).

    Returns:
        The :func:`_ida_run` result dict.
    """
    accel, dt = _load_record(record_id, dt, channel=channel)
    return _ida_run(
        code,
        record_id,
        accel,
        dt,
        intensities,
        collapse_drift=collapse_drift,
        damping=damping,
        tolerance=tolerance,
        max_scale=max_scale,
        pdelta=pdelta,
        pdelta_gravity=pdelta_gravity,
        damping_type=damping_type,
    )


def collapse_ida_curves(
    code: str,
    record_ids: Sequence[str],
    intensities: Sequence[float],
    *,
    dt: float = 0.005,
    collapse_drift: Optional[float] = _COLLAPSE_DRIFT,
    damping: float = _DAMPING,
    tolerance: float = 0.01,
    max_scale: Optional[float] = None,
    pdelta: bool = True,
    pdelta_gravity: bool = True,
    record_channels: Optional[dict[str, str]] = None,
    verbose: bool = False,
    damping_type: str = "rayleigh",
) -> dict[str, Any]:
    """Run the full IDA over *record_ids* and keep the per-record curves.

    Like :func:`collapse_capacity_sa`, but instead of reducing each record to a
    scalar capacity it retains the complete :func:`_ida_run` result — the ladder
    ``intensities``, the peak-interstorey-drift ``drifts`` and the per-level
    ``converged`` flags — together with the record's ``Sa(T1)``, its source
    mirror, and the collapse dispersion ``sigma_ln``.  This is the data source
    for the ``ida`` block of the unified NPZ archive.

    Args:
        code: Generic-frame code.
        record_ids: Record identifiers (the LMSR-N set).
        intensities: Ascending record scale factors (the IM ladder).
        dt: Integration/resampling step (s).
        collapse_drift: Peak interstorey-drift ratio collapse threshold (``None`` disables the drift proxy).
        damping: Damping ratio (Rayleigh).
        damping_type: ``"rayleigh"`` (default) or ``"mass_proportional"``.
        tolerance: Bisection relative tolerance.
        max_scale: Optional upper bound on the ladder.
        pdelta: When True, emit the ``PDelta`` geometric transformation.
        pdelta_gravity: When True, hold the IK-2005 gravity load.
        record_channels: Optional ``record_id → channel suffix`` map for the
            multi-component mirror.  Records present in the map are marked
            ``"USGS"``; the rest are marked ``"CESMD"`` (single-file).
        verbose: When True, print per-record progress.

    Returns:
        ``{"code", "T1", "gamma", "g", "ladder", "max_scale", "tolerance",
        "collapse_drift", "records", "records_detail", "capacities",
        "collapse_Sa_T1_g", "median_collapse_sa", "median_collapse_sa_gamma",
        "sigma_ln", "n_records", "n_collapsed", "n_survived"}``.
    """
    t1 = period_1(code)
    g = g_from_units(gf.UNITS)
    capacities: dict[str, Optional[float]] = {}
    records_detail: dict[str, dict[str, Any]] = {}
    collapse_sa_g: dict[str, Optional[float]] = {}
    for i, rid in enumerate(record_ids):
        if verbose:
            print(f"  [{i + 1}/{len(record_ids)}] {rid}", flush=True)
        channel = record_channels.get(rid) if record_channels else None
        accel_base, dt = _load_record(rid, dt, channel=channel)
        sa_t1 = sdof_spectral_acceleration(accel_base, dt, t1, zeta=0.05)
        res = _ida_run(
            code,
            rid,
            accel_base,
            dt,
            intensities,
            collapse_drift=collapse_drift,
            damping=damping,
            tolerance=tolerance,
            max_scale=max_scale,
            pdelta=pdelta,
            pdelta_gravity=pdelta_gravity,
            damping_type=damping_type,
        )
        im = res["collapse_intensity"]
        capacities[rid] = (im * sa_t1) if im is not None else None
        collapse_sa_g[rid] = (im * sa_t1 / g) if im is not None else None
        records_detail[rid] = {
            "Sa_T1": float(sa_t1),
            "Sa_T1_g": float(sa_t1 / g),
            "source": "USGS" if (record_channels and rid in record_channels) else "CESMD",
            "ida": res,
        }
    collapsed = [v for v in capacities.values() if v is not None]
    n_collapsed = len(collapsed)
    n_survived = len(capacities) - n_collapsed
    # Survivors are right-censored: the median is only meaningful once at least
    # half the records collapsed, and a σ_ln computed from the collapsed subset
    # alone must not be reported as though it described every record.
    median = float(np.median(collapsed)) if collapsed and n_survived <= n_collapsed else None
    sigma_ln = (
        float(np.std(np.log(np.asarray(collapsed, dtype=float))))
        if collapsed and n_survived == 0
        else None
    )
    return {
        "code": code,
        "T1": t1,
        "gamma": 1.0,  # the Appendix-B design value Vy/W
        "g": float(g),
        "ladder": [float(im) for im in intensities],
        "max_scale": max_scale,
        "tolerance": tolerance,
        "collapse_drift": collapse_drift,
        "records": [str(rid) for rid in record_ids],
        "records_detail": records_detail,
        "capacities": capacities,
        "collapse_Sa_T1_g": collapse_sa_g,
        "median_collapse_sa": median,
        "median_collapse_sa_gamma": (median / g) if median is not None else None,
        "sigma_ln": sigma_ln,
        "n_records": len(record_ids),
        "n_collapsed": n_collapsed,
        "n_survived": n_survived,
    }


def collapse_capacity_sa(
    code: str,
    record_ids: Sequence[str],
    intensities: Sequence[float],
    *,
    dt: float = 0.005,
    collapse_drift: Optional[float] = _COLLAPSE_DRIFT,
    damping: float = _DAMPING,
    tolerance: float = 0.01,
    max_scale: Optional[float] = None,
    pdelta: bool = True,
    pdelta_gravity: bool = True,
    record_channels: Optional[dict[str, str]] = None,
    verbose: bool = False,
    damping_type: str = "rayleigh",
) -> dict[str, Any]:
    """Per-record collapse capacity in ``Sa(T1)`` terms + the median, for *code*.

    Thin wrapper over :func:`collapse_ida_curves` that drops the per-record
    curves, returning only the scalar capacities and the median.

    Returns:
        ``{"code", "T1", "gamma", "capacities", "median_collapse_sa",
        "median_collapse_sa_gamma", "records", "n_collapsed", "n_survived"}``.
    """
    full = collapse_ida_curves(
        code,
        record_ids,
        intensities,
        dt=dt,
        collapse_drift=collapse_drift,
        damping=damping,
        tolerance=tolerance,
        max_scale=max_scale,
        pdelta=pdelta,
        pdelta_gravity=pdelta_gravity,
        record_channels=record_channels,
        verbose=verbose,
        damping_type=damping_type,
    )
    return {
        "code": full["code"],
        "T1": full["T1"],
        "gamma": full["gamma"],
        "capacities": full["capacities"],
        "median_collapse_sa": full["median_collapse_sa"],
        "median_collapse_sa_gamma": full["median_collapse_sa_gamma"],
        "records": full["records"],
        "n_collapsed": full["n_collapsed"],
        "n_survived": full["n_survived"],
    }
