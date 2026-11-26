#!/usr/bin/env python
"""CESMD (Vol2) vs PEER NGA-West2 ``Sa(T1)`` comparison for the LMSR-N records.

For each record available in both the CESMD mirror (``<id>.csv``) and as a PEER
NGA ``.AT2``, computes the 5 %-damped ``Sa(T1 = 0.9 s)`` of each and reports the
ratio.  This tests whether CESMD Vol2 processing (which 19 of the 40 records
use) shifts the collapse intensity measure ``Sa,c(T1) = collapse_scale ×
Sa(T1)`` relative to PEER NGA — the remaining record-side suspect for the
4.67-vs-3.4 gap.

Sources are read in two tiers:

* **mirror** — ``data/ground_motion/local/<id>.csv`` vs ``<id>.AT2`` (the
  already-corrected PEER versions of the wrong-component / processing-outlier
  records);
* **downloads** — ``~/Downloads/UnscaledAccRecord*/`` NGA ``.AT2`` files matched
  to the CESMD ``.csv`` by event + station code, selecting the NGA horizontal
  component whose PGA matches IK-2005 Table 4.2.

All signals are resampled to a common 0.005 s before the spectral solve, so the
comparison is on equal footing.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from fea_toolkit._unit_scaling import g_from_units
from fea_toolkit.io.ground_motion import read_peer_record, read_time_history_csv
from fea_toolkit.spectrum import sdof_spectral_acceleration

T1 = 0.9  # 0909 frame fundamental period (s)
ZETA = 0.05
DT = 0.005
_G = g_from_units({"F": "N", "L": "m", "T": "C"})

MIRROR = Path(__file__).resolve().parent.parent / "data" / "ground_motion" / "local"
DOWNLOADS = Path.home() / "Downloads"

#: IK-2005 Table 4.2 PGA (g) for the CESMD records (component-selection anchor).
TABLE_PGA = {
    "LP89gmr": 0.226,
    "NR94cen": 0.322,
    "NR94cnp": 0.420,
    "NR94far": 0.273,
    "NR94fle": 0.240,
    "NR94glp": 0.206,
    "NR94lh1": 0.087,
    "NR94lv2": 0.063,
    "NR94nya": 0.159,
    "NR94stc": 0.368,
    "NR94stn": 0.474,
    "NR94ver": 0.153,
    "SH87wsm": 0.172,
}

#: CESMD record id → (NGA-West2 event code, station abbreviation), parsed from
#: ``RSN*_EVENT_STATION*.AT2`` (e.g. ``RSN987_NORTHR_CEN155.AT2`` → ``NORTHR`` /
#: ``CEN``).  Grouping by *both* keeps a shared station abbreviation from two
#: different earthquakes apart (e.g. ``WSM`` is Westmorland in both Imperial
#: Valley and Superstition Hills).
STATION_CODE = {
    "LP89gmr": ("LOMAP", "GMR"),
    "NR94cen": ("NORTHR", "CEN"),
    "NR94cnp": ("NORTHR", "CNP"),
    "NR94far": ("NORTHR", "FAR"),
    "NR94fle": ("NORTHR", "FLE"),
    "NR94glp": ("NORTHR", "GLP"),
    "NR94lh1": ("NORTHR", "L01"),
    "NR94lv2": ("NORTHR", "LV2"),
    "NR94nya": ("NORTHR", "NYA"),
    "NR94stc": ("NORTHR", "STC"),
    "NR94stn": ("NORTHR", "STN"),
    "NR94ver": ("NORTHR", "VER"),
    "SH87wsm": ("SUPERST", "WSM"),
}


def _resample(times: np.ndarray, accel: np.ndarray, dt: float) -> tuple[np.ndarray, float]:
    dt0 = float(times[1] - times[0])
    if abs(dt0 - dt) > 1e-9:
        t_new = np.arange(0.0, times[-1] + dt, dt)
        accel = np.interp(t_new, times, accel)
    return np.asarray(accel, float), float(dt)


def _load_at2(path: Path) -> tuple[np.ndarray, float]:
    times, accel = read_peer_record(str(path))
    return _resample(times, accel, DT)


def _load_csv(path: Path) -> tuple[np.ndarray, float]:
    times, accel = read_time_history_csv(str(path))
    return _resample(times, accel, DT)


def _sa(accel: np.ndarray, dt: float) -> float:
    return float(sdof_spectral_acceleration(accel, dt, T1, zeta=ZETA))


def _pga_g(accel: np.ndarray) -> float:
    return float(np.max(np.abs(accel))) / _G


def _report(rid: str, sa_cesmd: float, sa_nga: float, note: str = "") -> None:
    ratio = sa_nga / sa_cesmd if sa_cesmd else float("nan")
    print(
        f"  {rid:<10}{sa_cesmd / _G:>8.4f}{sa_nga / _G:>9.4f}{ratio:>9.3f}   {note}",
        flush=True,
    )


def main() -> int:
    print(f"Sa(T1={T1:g} s, zeta={ZETA:g}) — CESMD Vol2 (.csv) vs PEER NGA (.AT2), in g")
    print(f"  {'record':<10}{'Sa(CSV)':>8}{'Sa(NGA)':>9}{'NGA/CSV':>9}")
    print("  " + "-" * 40)

    # ── Tier 1: mirror-internal overlaps (both .csv and .AT2 present) ──
    for rid in STATION_CODE:
        csv_p = MIRROR / f"{rid}.csv"
        at2_p = MIRROR / f"{rid}.AT2"
        if not (csv_p.exists() and at2_p.exists()):
            continue
        accel_c, dt_c = _load_csv(csv_p)
        accel_n, _ = _load_at2(at2_p)
        _report(rid, _sa(accel_c, dt_c), _sa(accel_n, dt_c), "(mirror)")

    # ── Tier 2: downloads NGA .AT2 matched by event + station code ──
    nga_files: dict[tuple[str, str], list[Path]] = {}
    for p in DOWNLOADS.glob("UnscaledAccRecord*/*.AT2"):
        if "-UP." in p.name:
            continue  # vertical component — skip
        # e.g. "RSN987_NORTHR_CEN155.AT2" → event "NORTHR", station "CEN"
        parts = p.name.split("_")
        if len(parts) < 3:
            continue
        station = parts[2][:3] if not parts[2][0].isdigit() else parts[2]
        nga_files.setdefault((parts[1], station), []).append(p)

    for rid, (event, code) in STATION_CODE.items():
        if (event, code) not in nga_files:
            continue
        csv_p = MIRROR / f"{rid}.csv"
        if not csv_p.exists():
            continue
        accel_c, dt_c = _load_csv(csv_p)
        candidates = nga_files[(event, code)]
        # Select the horizontal component whose PGA matches Table 4.2.
        best = min(candidates, key=lambda p: abs(_pga_g(_load_at2(p)[0]) - TABLE_PGA[rid]))
        accel_n, _ = _load_at2(best)
        _report(rid, _sa(accel_c, dt_c), _sa(accel_n, dt_c), f"(downloads {best.name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
