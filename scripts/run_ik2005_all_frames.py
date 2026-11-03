#!/usr/bin/env python
"""Run the IK-2005 collapse IDA over the full generic-frame matrix.

Extends ``scripts/run_ik2005_npz.py`` (single frame) to all 12 frames
(``0303 … 1836``: N = 3/6/9/12/15/18 storeys × stiff/flexible).  Each frame is
run over the 33 locally-available LMSR-N records, its ``ida`` block is archived
to ``<out-dir>/ik2005_<code>.npz``, and the per-frame summary (median
``(Sa,c/g)/γ``, σ_ln) is appended to ``<out-dir>/ik2005_all_frames_summary.json``
**incrementally** — so the run is safe to kill and resume: a frame whose archive
already exists is skipped.

At the end it writes a combined markdown table
(``<out-dir>/ik2005_all_frames_summary.md``) and a
``median-vs-period`` plot (``<out-dir>/ik2005_median_vs_period.png``).

Usage::

    python scripts/run_ik2005_all_frames.py                          # all 12 frames
    python scripts/run_ik2005_all_frames.py --codes 0303,0306        # a subset
    python scripts/run_ik2005_all_frames.py --limit 2 --out-dir /tmp/smoke --force
"""

from __future__ import annotations

import argparse
import json
import os
import time
from typing import Optional

import numpy as np

from fea_toolkit.benchmarks import generic_frame as gf
from fea_toolkit.benchmarks import generic_frame_run as gfr
from fea_toolkit.benchmarks.generic_frame_data import FRAME_CODES, FRAME_DATA
from fea_toolkit.benchmarks.ik2005_records import USGS_CHANNELS, fetched_ids, peer_only_ids
from fea_toolkit.io.npz_writer import write_results_npz

#: Seed ladder (record scale factors) — same as the ``0909`` reference run.
LADDER = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0)


def _ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _load(path: str) -> dict:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def _save(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)


def _seed_existing(summary: dict, out_dir: str) -> None:
    """Back-fill already-written archives (e.g. ``0909``) into the summary."""
    for code in FRAME_CODES:
        if code in summary:
            continue
        path = os.path.join(out_dir, f"ik2005_{code}.npz")
        if not os.path.exists(path):
            continue
        z = np.load(path)
        summary[code] = {
            "code": code,
            "N": FRAME_DATA[code]["N"],
            "T1": FRAME_DATA[code]["T1"],
            "n_records": int(z["ida/record_id"].size),
            "median_collapse_sa_gamma": float(z["ida/median_collapse_Sa_T1_gamma"]),
            "sigma_ln": float(z["ida/sigma_ln"]),
            "elapsed_s": None,
            "source": "archive",
        }
        z.close()


def _write_table(summary: dict, out_dir: str) -> None:
    rows = [summary[c] for c in FRAME_CODES if c in summary]
    rows.sort(key=lambda r: (r.get("N") or 0, r.get("T1") or 0.0))
    lines = [
        "# IK-2005 collapse — all frames (33 LMSR-N records)",
        "",
        "| code | N | T1 (s) | median (Sa,c/g)/γ | σ_ln | notes |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        med = r.get("median_collapse_sa_gamma")
        sig = r.get("sigma_ln")
        if "error" in r:
            note = f"**FAILED**: {r['error']}"
        elif r.get("source") == "archive":
            note = "archived earlier"
        else:
            note = ""
        med_s = "—" if med is None else f"{med:.3f}"
        sig_s = "—" if sig is None else f"{sig:.3f}"
        lines.append(f"| {r['code']} | {r['N']} | {r['T1']} | {med_s} | {sig_s} | {note} |")
    lines += [
        "",
        "IK-2005 §5.5 target: median `(Sa,c/g)/γ ≈ 3.3` at `T1 ≈ 1 s` (the `0909` frame).",
        "",
    ]
    with open(os.path.join(out_dir, "ik2005_all_frames_summary.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _write_plot(summary: dict, out_dir: str) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    rows = [summary[c] for c in FRAME_CODES if c in summary and "error" not in summary[c]]
    if not rows:
        return
    rows.sort(key=lambda r: (r.get("N") or 0, r.get("T1") or 0.0))
    t1 = [r["T1"] for r in rows]
    med = [r.get("median_collapse_sa_gamma") for r in rows]
    fig, ax = plt.subplots(figsize=(7.5, 5))
    ax.plot(t1, med, "o-", color="#1f77b4", label="median (Sa,c/g)/γ")
    ax.axhline(
        3.3, color="#d62728", linestyle="--", linewidth=1.0, label="IK-2005 §5.5 ≈ 3.3 (T1 ≈ 1 s)"
    )
    for r in rows:
        ax.annotate(
            r["code"],
            (r["T1"], r["median_collapse_sa_gamma"]),
            textcoords="offset points",
            xytext=(6, 4),
            fontsize=8,
        )
    ax.set_xlabel("T1 (s)")
    ax.set_ylabel("median (Sa,c/g)/γ")
    ax.set_title("IK-2005 generic-frame collapse capacity vs period (33 LMSR-N records)")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "ik2005_median_vs_period.png"), dpi=130)
    plt.close(fig)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codes", default=None, help="comma-separated subset of frame codes")
    parser.add_argument("--out-dir", default="output")
    parser.add_argument("--tolerance", type=float, default=0.01)
    parser.add_argument("--max-scale", type=float, default=128.0)
    parser.add_argument("--limit", type=int, default=None, help="run only the first N records")
    parser.add_argument(
        "--force", action="store_true", help="re-run frames that already have an archive"
    )
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args(argv)

    os.makedirs(args.out_dir, exist_ok=True)
    codes = FRAME_CODES if args.codes is None else tuple(c.strip() for c in args.codes.split(","))
    record_ids = fetched_ids()
    skipped = [rid for rid in peer_only_ids() if rid not in record_ids]
    if args.limit:
        record_ids = record_ids[: args.limit]

    summary_path = os.path.join(args.out_dir, "ik2005_all_frames_summary.json")
    summary = _load(summary_path)
    _seed_existing(summary, args.out_dir)
    _save(summary_path, summary)

    print(f"{_ts()}  IK-2005 frames: {', '.join(codes)}", flush=True)
    print(
        f"{_ts()}  records: {len(record_ids)} (PEER-only skipped: {len(skipped)}), "
        f"ladder={list(LADDER)}, max_scale={args.max_scale}, tolerance={args.tolerance}",
        flush=True,
    )

    for idx, code in enumerate(codes, 1):
        out = os.path.join(args.out_dir, f"ik2005_{code}.npz")
        if os.path.exists(out) and not args.force:
            print(f"{_ts()}  [{idx}/{len(codes)}] {code}: archive exists — skip", flush=True)
            continue
        n_storeys = FRAME_DATA[code]["N"]
        t1 = FRAME_DATA[code]["T1"]
        print(
            f"{_ts()}  [{idx}/{len(codes)}] {code} (N={n_storeys}, T1={t1}s): "
            f"running {len(record_ids)} records …",
            flush=True,
        )
        t0 = time.time()
        try:
            md = gf.enrich_imk(gf.build_model(code), code)
            ida = gfr.collapse_ida_curves(
                code,
                record_ids,
                LADDER,
                tolerance=args.tolerance,
                max_scale=args.max_scale,
                record_channels=USGS_CHANNELS,
                verbose=True,
            )
            write_results_npz(out, md, ida_results=ida)
            entry = {
                "code": code,
                "N": n_storeys,
                "T1": t1,
                "n_records": len(record_ids),
                "median_collapse_sa_gamma": ida["median_collapse_sa_gamma"],
                "sigma_ln": ida["sigma_ln"],
                "elapsed_s": round(time.time() - t0, 1),
                "source": "run",
            }
            summary[code] = entry
            _save(summary_path, summary)
            print(
                f"{_ts()}    → median (Sa,c/g)/γ = {ida['median_collapse_sa_gamma']:.3f}  "
                f"σ_ln = {ida['sigma_ln']:.3f}  ({entry['elapsed_s']:.0f} s)",
                flush=True,
            )
        except Exception as exc:  # keep the long run alive past one bad frame
            print(f"{_ts()}    ✗ FAILED: {exc!r}", flush=True)
            summary[code] = {
                "code": code,
                "N": n_storeys,
                "T1": t1,
                "error": repr(exc),
                "source": "failed",
            }
            _save(summary_path, summary)

    _write_table(summary, args.out_dir)
    if not args.no_plot:
        _write_plot(summary, args.out_dir)
    print(f"{_ts()}  done — {len(summary)} frames in {summary_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
