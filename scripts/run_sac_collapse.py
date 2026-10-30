#!/usr/bin/env python
"""Run the four-record median ``Sa(T1)`` collapse capacity of a SAC building.

The A3 headline result: incremental dynamic analysis of the IMK-hinge SAC
frame over the four benchmark records, with the collapse intensity refined by
bisection (Ibarra–Krawinkler 2005) and reported in ``Sa(T1)`` terms.  The IM
ladder is extended geometrically up to ``--max-scale``, so the collapse scale
factor need not be known a priori.

Requires the ground-motion mirror (``data/ground_motion/...`` — see
``scripts/fetch_ground_motions.py``).  See ``docs/imk_deterioration_model.md``
(§5, A3) and ``docs/sac_imk_collapse_benchmark.md``.

Usage::

    python scripts/run_sac_collapse.py [--building 3] [--max-scale 16]
"""

import argparse
import json
import time
from typing import Optional

import numpy as np

from fea_toolkit._unit_scaling import g_from_units
from fea_toolkit.benchmarks import sac, sac_run

RECORDS = ["ELCENTRO", "HACHINOHE", "NORTHRIDGE", "KOBE"]


def _fmt(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:.3f}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--building", type=int, default=3)
    parser.add_argument("--tolerance", type=float, default=0.01)
    parser.add_argument("--max-scale", type=float, default=16.0)
    parser.add_argument(
        "--ladder",
        type=str,
        default="0.5,1,2,4,8,16",
        help="comma-separated ascending seed ladder (record scale factors)",
    )
    parser.add_argument("--json", type=str, default=None, help="optional JSON output path")
    args = parser.parse_args(argv)

    ladder = [float(x) for x in args.ladder.split(",")]
    g = g_from_units(sac.SAC3_UNITS)

    t0 = time.time()
    result = sac_run.collapse_capacity_sa(
        args.building,
        RECORDS,
        ladder,
        collapse_drift=0.10,
        damping=0.02,
        tolerance=args.tolerance,
        max_scale=args.max_scale,
    )

    print(f"SAC {args.building}-story IMK collapse capacity (Sa(T1), 4 records)")
    print(f"  T1 = {result['T1']:.3f} s   g = {g:.3f} m/s^2   tolerance = {args.tolerance}")
    print(f"  {'record':<12}{'Sa(T1) [g]':>14}{'collapse scale':>16}{'Sa,c(T1) [g]':>14}")
    scales: dict[str, Optional[float]] = {}
    for rid in RECORDS:
        accel, dt = sac.load_benchmark_record(str(sac_run.record_path(rid)), sac.SAC_DT_CAL)
        sa_t1 = sac_run.sdof_spectral_acceleration(np.asarray(accel, float), dt, result["T1"])
        cap = result["capacities"][rid]
        scale = (cap / sa_t1) if cap is not None else None
        scales[rid] = scale
        print(
            f"  {rid:<12}{sa_t1 / g:>14.3f}{_fmt(scale):>16}{_fmt((cap / g) if cap else None):>14}"
        )
    med = result["median_collapse_sa"]
    print(f"  median Sa,c(T1) = {_fmt((med / g) if med else None)} g")
    print(f"  elapsed {time.time() - t0:.1f} s")

    if args.json:
        with open(args.json, "w") as f:
            json.dump(
                {
                    "building": result["building"],
                    "T1": result["T1"],
                    "ladder": ladder,
                    "max_scale": args.max_scale,
                    "tolerance": args.tolerance,
                    "records": RECORDS,
                    "collapse_scale": scales,
                    "capacities_Sa_T1_g": {
                        rid: (
                            result["capacities"][rid] / g
                            if result["capacities"][rid] is not None
                            else None
                        )
                        for rid in RECORDS
                    },
                    "median_collapse_Sa_T1_g": (med / g) if med is not None else None,
                },
                f,
                indent=2,
            )
        print(f"  wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
