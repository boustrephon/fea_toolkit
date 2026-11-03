#!/usr/bin/env python
"""Run the IK-2005 generic-frame collapse IDA and write a unified NPZ.

Produces ``ik2005_<code>.npz`` carrying the model geometry (the generated
generic frame) plus the ``ida`` block (schema v5): the per-record IDA curves,
collapse capacities and the median/σ_ln summary.  This archive is what
:func:`fea_toolkit.plotting.plot_ida_curves` / ``plot_collapse_fragility`` and
the FEA-GUI's "Open results" path consume.

Usage::

    python scripts/run_ik2005_npz.py --code 0909 --limit 3   # smoke test
    python scripts/run_ik2005_npz.py --code 0909              # full 33 records
"""

import argparse
import time
from typing import Optional

from fea_toolkit.benchmarks import generic_frame as gf
from fea_toolkit.benchmarks import generic_frame_run as gfr
from fea_toolkit.benchmarks.ik2005_records import (
    USGS_CHANNELS,
    fetched_ids,
    peer_only_ids,
)
from fea_toolkit.io.npz_writer import write_results_npz


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code", default="0909", help="generic-frame code")
    parser.add_argument("--tolerance", type=float, default=0.01)
    parser.add_argument("--max-scale", type=float, default=128.0)
    parser.add_argument("--ladder", type=str, default="0.5,1,2,4,8,16")
    parser.add_argument("--limit", type=int, default=None, help="run only the first N records")
    parser.add_argument("--out", type=str, default=None, help="output .npz path")
    args = parser.parse_args(argv)

    ladder = [float(x) for x in args.ladder.split(",")]
    record_ids = fetched_ids()
    skipped = [rid for rid in peer_only_ids() if rid not in record_ids]
    if args.limit:
        record_ids = record_ids[: args.limit]

    # 1. Model — the generated generic frame, IMK-enriched.
    md = gf.enrich_imk(gf.build_model(args.code), args.code)

    # 2. IDA over the records (keeps the per-record curves).
    t0 = time.time()
    print(f"Running IDA on {args.code} over {len(record_ids)} records …")
    ida = gfr.collapse_ida_curves(
        args.code,
        record_ids,
        ladder,
        tolerance=args.tolerance,
        max_scale=args.max_scale,
        record_channels=USGS_CHANNELS,
        verbose=True,
    )
    print(
        f"  median (Sa,c/g)/γ = {ida['median_collapse_sa_gamma']:.2f}  "
        f"σ_ln = {ida['sigma_ln']:.2f}  ({time.time() - t0:.0f} s)"
    )

    # 3. Write the archive.
    out = args.out or f"ik2005_{args.code}.npz"
    path = write_results_npz(out, md, ida_results=ida)
    print(f"wrote {path}")
    if skipped:
        print(f"PEER-only (not yet fetched): {', '.join(skipped)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
