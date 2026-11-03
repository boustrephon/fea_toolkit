"""IK-2003 (PEER 2003/15) non-deteriorating drift-demand validation.

Reproduces Medina & Krawinkler (2003) §4.3: for a set of generic frames, the
median normalised maximum roof-drift angle ``θr,max / [Sd(T1)/H]`` as a
function of the relative intensity ``[Sa(T1)/g]/γ``.  The elastic anchor is
``PF1`` (the first-mode participation factor): at ``[Sa(T1)/g]/γ ≈ 1`` the
median normalised roof drift should ≈ ``PF1``, then grow with intensity (P-Δ /
higher modes).  This validates the *dynamic machinery* (Rayleigh damping,
integration, P-Δ) in isolation — no collapse criterion — before re-running the
IK-2005 collapse IDA.

Usage::

    python scripts/run_ik2003_validation.py --codes 0909,0918,1224 --ratios 1,2,4
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fea_toolkit.benchmarks import generic_frame_run as gfr
from fea_toolkit.benchmarks.ik2005_records import LMSR_N_IDS, USGS_CHANNELS


def _available_records(limit: int | None) -> list[str]:
    """The locally-fetched LMSR-N records (skip the 7 PEER-only, honour *limit*)."""
    available: list[str] = []
    for rid in LMSR_N_IDS:
        try:
            gfr._load_record(rid, 0.005, channel=USGS_CHANNELS.get(rid))
            available.append(rid)
        except FileNotFoundError:
            continue
        if limit is not None and len(available) >= limit:
            break
    return available


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codes", default="0909,0918,1224")
    parser.add_argument("--ratios", default="1,2,4")
    parser.add_argument("--n-records", type=int, default=None)
    parser.add_argument("--out", default="output/ik2003_drift_demands.json")
    args = parser.parse_args()

    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    ratios = tuple(float(r) for r in args.ratios.split(","))
    records = _available_records(args.n_records)
    print(f"records: {len(records)}; codes: {codes}; ratios: {ratios}", flush=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    results: dict[str, dict] = {}
    for code in codes:
        r = gfr.drift_demands(code, records, ratios=ratios, record_channels=USGS_CHANNELS)
        results[code] = r
        pf1 = r["PF1"]
        print(f"\n=== {code} (T1={r['T1']:.3f}s, PF1={pf1:.3f}) ===", flush=True)
        for ratio in r["ratios"]:
            med = r["median"][ratio]
            conv = sum(1 for _v, ok in r["normalized"][ratio].values() if ok)
            print(
                f"  R={ratio:g}: median θr,max/[Sd/H] = {med:.3f} "
                f"({conv}/{len(r['normalized'][ratio])} converged) "
                f"| PF1 ratio = {med / pf1:.3f}",
                flush=True,
            )
        # Incremental save so a partial run still leaves its frames on disk.
        out.write_text(json.dumps(results, indent=2), encoding="utf-8")

    print(f"\nwrote {out}", flush=True)


if __name__ == "__main__":
    main()
