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

from fea_toolkit._unit_scaling import g_from_units
from fea_toolkit.benchmarks import sac, sac_run

RECORDS = ["ELCENTRO", "HACHINOHE", "NORTHRIDGE", "KOBE"]


def _float_or_none(value: str) -> Optional[float]:
    """Parse a float or the literal ``"none"`` (disables a threshold)."""
    if value.strip().lower() in ("none", "null", "off"):
        return None
    return float(value)


def _fmt(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:.3f}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--building", type=int, default=3)
    parser.add_argument("--tolerance", type=float, default=0.01)
    parser.add_argument("--max-scale", type=float, default=16.0)
    parser.add_argument(
        "--dt",
        type=float,
        default=sac.SAC_DT_CAL,
        help="record resampling / integration step in s (default SAC_DT_CAL = 0.005; "
        "use 0.01 for the 9/20-story matrices)",
    )
    parser.add_argument(
        "--damping",
        type=float,
        default=0.02,
        help="damping ratio xi (IK-2005 uses 0.05)",
    )
    parser.add_argument(
        "--damping-type",
        choices=["rayleigh", "mass_proportional"],
        default="rayleigh",
        help="damping formulation (IK-2005 uses mass_proportional)",
    )
    parser.add_argument(
        "--collapse-drift",
        type=_float_or_none,
        default=0.10,
        help="peak interstorey-drift collapse threshold, or 'none' for "
        "non-convergence only (IK-2005)",
    )
    parser.add_argument(
        "--pdelta",
        action="store_true",
        help="emit the PDelta geometric transformation (global P-Delta)",
    )
    parser.add_argument(
        "--pdelta-gravity",
        action="store_true",
        help="hold the IK-2005 gravity load (1.4 x seismic mass) through the analysis",
    )
    parser.add_argument(
        "--ladder",
        type=str,
        default="0.5,1,2,4,8,16",
        help="comma-separated ascending seed ladder (record scale factors)",
    )
    parser.add_argument(
        "--records",
        type=str,
        default=",".join(RECORDS),
        help="comma-separated record ids (default: the four SAC benchmark records)",
    )
    parser.add_argument("--json", type=str, default=None, help="optional JSON output path")
    args = parser.parse_args(argv)

    ladder = [float(x) for x in args.ladder.split(",")]
    records = [r.strip() for r in args.records.split(",") if r.strip()]
    g = g_from_units(sac.SAC3_UNITS)

    t0 = time.time()
    result = sac_run.collapse_capacity_sa(
        args.building,
        records,
        ladder,
        collapse_drift=args.collapse_drift,
        damping=args.damping,
        damping_type=args.damping_type,
        integration_dt=args.dt,
        tolerance=args.tolerance,
        max_scale=args.max_scale,
        pdelta=args.pdelta,
        pdelta_gravity=args.pdelta_gravity,
    )

    print(f"SAC {args.building}-story IMK collapse capacity (Sa(T1), {len(records)} records)")
    print(
        f"  T1 = {result['T1']:.3f} s   g = {g:.3f} m/s^2   dt = {args.dt} s   "
        f"damping = {args.damping} ({args.damping_type})   drift = {args.collapse_drift}   "
        f"tolerance = {args.tolerance}"
    )
    print(f"  {'record':<12}{'Sa(T1) [g]':>14}{'collapse scale':>16}{'Sa,c(T1) [g]':>14}")
    scales: dict[str, Optional[float]] = result["collapse_scale"]
    for rid in records:
        sa_t1 = result["Sa_T1"][rid]
        cap = result["capacities"][rid]
        scale = scales[rid]
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
                    "records": records,
                    "collapse_scale": scales,
                    "capacities_Sa_T1_g": {
                        rid: (
                            result["capacities"][rid] / g
                            if result["capacities"][rid] is not None
                            else None
                        )
                        for rid in records
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
