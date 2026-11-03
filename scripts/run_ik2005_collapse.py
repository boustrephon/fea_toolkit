#!/usr/bin/env python
"""Run the IK-2005 generic-frame median collapse capacity over the LMSR-N records.

Drives :func:`~fea_toolkit.benchmarks.generic_frame_run.collapse_capacity_sa`
over the 33 locally-available LMSR-N records (the 40-record IK-2005 Table 4.2
set minus the 7 records that only exist in PEER NGA-West2 — see
``docs/ik2005_generic_frame_benchmark.md``).  The headline number is the median
``(Sa,c/g)/γ``, benchmarked at ``≈ 3.3`` for ``T1 ≈ 1 s`` (§5.5); the ``0909``
frame (N = 9, T1 = 0.9 s) is the first target.

Records fetched from CESMD are single-file (``<record_id>.csv``); records fetched
from the USGS NSMP archive are multi-component (``<record_id>_<channel>.csv``) and
read through the ``USGS_CHANNELS`` map, which selects the horizontal component
whose PGA matches Table 4.2 (Medina's per-station choice — see the component
caveat in the benchmark doc).

Usage::

    python scripts/run_ik2005_collapse.py [--code 0909] [--limit N] [--json out.json]
"""

import argparse
import json
import time
from typing import Optional

import numpy as np

from fea_toolkit._unit_scaling import g_from_units
from fea_toolkit.benchmarks import generic_frame as gf
from fea_toolkit.benchmarks import generic_frame_run as gfr
from fea_toolkit.benchmarks.ik2005_records import (
    USGS_CHANNELS,
    fetched_ids,
    peer_only_ids,
)


def _fmt(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:.3f}"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code", default="0909", help="generic-frame code")
    parser.add_argument("--tolerance", type=float, default=0.01)
    parser.add_argument("--max-scale", type=float, default=128.0)
    parser.add_argument(
        "--ladder",
        type=str,
        default="0.5,1,2,4,8,16",
        help="comma-separated ascending seed ladder (record scale factors)",
    )
    parser.add_argument("--limit", type=int, default=None, help="run only the first N records")
    parser.add_argument(
        "--damping-type",
        choices=["rayleigh", "mass_proportional"],
        default="rayleigh",
        help="damping formulation (default rayleigh)",
    )
    parser.add_argument("--json", type=str, default=None, help="optional JSON output path")
    args = parser.parse_args(argv)

    ladder = [float(x) for x in args.ladder.split(",")]
    record_ids = fetched_ids()
    skipped = [rid for rid in peer_only_ids() if rid not in record_ids]
    if args.limit:
        record_ids = record_ids[: args.limit]

    g = g_from_units(gf.UNITS)
    t0 = time.time()
    result = gfr.collapse_capacity_sa(
        args.code,
        record_ids,
        ladder,
        tolerance=args.tolerance,
        max_scale=args.max_scale,
        record_channels=USGS_CHANNELS,
        verbose=True,
        damping_type=args.damping_type,
    )

    print(f"IK-2005 generic-frame collapse capacity — code {args.code}, T1 = {result['T1']:.2f} s")
    print(
        f"  {len(record_ids)} records  |  skipped (PEER-only): {len(skipped)}  |  "
        f"g = {g:.3f} m/s²  |  tolerance = {args.tolerance}"
    )
    print(f"  {'record':<10}{'Sa(T1) [g]':>12}{'collapse scale':>15}{'Sa,c(T1) [g]':>14}")

    scales: dict[str, Optional[float]] = {}
    for rid in record_ids:
        channel = USGS_CHANNELS.get(rid)
        accel, dt = gfr._load_record(rid, 0.005, channel=channel)
        sa_t1 = gfr.sdof_spectral_acceleration(np.asarray(accel, float), dt, result["T1"])
        cap = result["capacities"][rid]
        scale = (cap / sa_t1) if cap is not None else None
        scales[rid] = scale
        print(
            f"  {rid:<10}{sa_t1 / g:>12.3f}{_fmt(scale):>15}{_fmt((cap / g) if cap else None):>14}"
        )

    med = result["median_collapse_sa"]
    med_gamma = result["median_collapse_sa_gamma"]
    print(
        f"  median Sa,c(T1) = {_fmt((med / g) if med else None)} g  "
        f"→  (Sa,c/g)/γ = {_fmt(med_gamma)}   (IK-2005 §5.5 target ≈ 3.3)"
    )
    if skipped:
        print(f"  PEER-only (not yet fetched): {', '.join(skipped)}")
    print(f"  elapsed {time.time() - t0:.1f} s")

    if args.json:
        payload = {
            "code": args.code,
            "T1": result["T1"],
            "gamma": result["gamma"],
            "ladder": ladder,
            "max_scale": args.max_scale,
            "tolerance": args.tolerance,
            "records": record_ids,
            "skipped_peer_only": skipped,
            "collapse_scale": scales,
            "capacities_Sa_T1_g": {
                rid: (
                    result["capacities"][rid] / g if result["capacities"][rid] is not None else None
                )
                for rid in record_ids
            },
            "median_collapse_Sa_T1_g": (med / g) if med is not None else None,
            "median_collapse_Sa_gamma": med_gamma,
        }
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        print(f"  wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
