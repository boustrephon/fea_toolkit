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
``1.4 × seismic mass`` gravity, **5 % mass-proportional** damping (§4.3,
``damping_type="mass_proportional"``) and the peak-oriented
``IMKPeakOriented`` hinge (already in
:func:`~fea_toolkit.benchmarks.generic_frame.builder_config`).
"""

from __future__ import annotations

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

#: IK-2005 damping ratio (§4.3: ξ = 5 %).
_DAMPING = 0.05
#: Peak interstorey-drift ratio collapse threshold (FEMA P-695 sidesway proxy,
#: the same value ``sac_run`` uses).
_COLLAPSE_DRIFT = 0.10


def period_1(code: str) -> float:
    """The frame's fundamental period ``T1 = 0.1N`` / ``0.2N`` (s)."""
    return float(FRAME_DATA[code]["T1"])


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
    from pathlib import Path

    suffix = f"_{channel}" if channel else ""
    root = Path(__file__).resolve().parent.parent.parent.parent / "data" / "ground_motion"
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
    collapse_drift: float = _COLLAPSE_DRIFT,
    damping: float = _DAMPING,
    tolerance: Optional[float] = None,
    max_scale: Optional[float] = None,
    pdelta: bool = True,
    pdelta_gravity: bool = True,
) -> dict[str, Any]:
    """Run one IDA ladder for *code* (a base record scaled over *intensities*).

    Mirrors :func:`fea_toolkit.benchmarks.sac_run._ida_run`, with the generic
    frame (``gf.build_builder``, peak-oriented IMK, P-Δ + held gravity) and
    mass-proportional damping in place of the SAC building.  Each
    ``run_transient_analysis`` call rebuilds the domain, so no intensity sees
    the previous intensity's state.

    Args:
        code: Generic-frame code (e.g. ``"0909"``).
        record_id: Record label (provenance only — any identifier works).
        accel_base: Base acceleration samples (length/s²), one per time step.
        dt: Integration time step (s).
        intensities: The IM ladder (ascending scale factors on *accel_base*).
        collapse_drift: Peak interstorey-drift ratio collapse threshold.
        damping: Damping ratio (mass-proportional).
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

    t1 = period_1(code)
    t2 = t1 * 0.5  # unused for mass-proportional damping (a1 = 0)

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
                damping_type="mass_proportional",
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
                if (not c) or d > collapse_drift:
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
                    if (not c) or d > collapse_drift:
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
        "collapse_drift": float(collapse_drift),
        "bisection": bisection,
    }


def run_ida(
    code: str,
    record_id: str,
    intensities: Sequence[float],
    *,
    dt: float = 0.005,
    collapse_drift: float = _COLLAPSE_DRIFT,
    damping: float = _DAMPING,
    tolerance: float = 0.01,
    max_scale: Optional[float] = None,
    pdelta: bool = True,
    pdelta_gravity: bool = True,
    channel: Optional[str] = None,
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
        collapse_drift: Peak interstorey-drift ratio collapse threshold.
        damping: Damping ratio (mass-proportional).
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
    )


def collapse_ida_curves(
    code: str,
    record_ids: Sequence[str],
    intensities: Sequence[float],
    *,
    dt: float = 0.005,
    collapse_drift: float = _COLLAPSE_DRIFT,
    damping: float = _DAMPING,
    tolerance: float = 0.01,
    max_scale: Optional[float] = None,
    pdelta: bool = True,
    pdelta_gravity: bool = True,
    record_channels: Optional[dict[str, str]] = None,
    verbose: bool = False,
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
        collapse_drift: Peak interstorey-drift ratio collapse threshold.
        damping: Damping ratio (mass-proportional).
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
        "sigma_ln"}``.
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
    median = float(np.median(collapsed)) if collapsed else None
    sigma_ln = (
        float(np.std(np.log(np.asarray(collapsed, dtype=float)))) if collapsed else float("nan")
    )
    return {
        "code": code,
        "T1": t1,
        "gamma": 1.0,  # the Appendix-B design value Vy/W
        "g": float(g),
        "ladder": [float(im) for im in intensities],
        "max_scale": max_scale,
        "tolerance": tolerance,
        "collapse_drift": float(collapse_drift),
        "records": [str(rid) for rid in record_ids],
        "records_detail": records_detail,
        "capacities": capacities,
        "collapse_Sa_T1_g": collapse_sa_g,
        "median_collapse_sa": median,
        "median_collapse_sa_gamma": (median / g) if median is not None else None,
        "sigma_ln": sigma_ln,
    }


def collapse_capacity_sa(
    code: str,
    record_ids: Sequence[str],
    intensities: Sequence[float],
    *,
    dt: float = 0.005,
    collapse_drift: float = _COLLAPSE_DRIFT,
    damping: float = _DAMPING,
    tolerance: float = 0.01,
    max_scale: Optional[float] = None,
    pdelta: bool = True,
    pdelta_gravity: bool = True,
    record_channels: Optional[dict[str, str]] = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """Per-record collapse capacity in ``Sa(T1)`` terms + the median, for *code*.

    Thin wrapper over :func:`collapse_ida_curves` that drops the per-record
    curves, returning only the scalar capacities and the median.

    Returns:
        ``{"code", "T1", "gamma", "capacities", "median_collapse_sa",
        "median_collapse_sa_gamma", "records"}``.
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
    )
    return {
        "code": full["code"],
        "T1": full["T1"],
        "gamma": full["gamma"],
        "capacities": full["capacities"],
        "median_collapse_sa": full["median_collapse_sa"],
        "median_collapse_sa_gamma": full["median_collapse_sa_gamma"],
        "records": full["records"],
    }
