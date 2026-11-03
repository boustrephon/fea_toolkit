#!/usr/bin/env python
"""SAC pushover strength ratio (both yield definitions) + normalised curve plot.

Runs :func:`fea_toolkit.benchmarks.sac_run.pushover_strength_ratio` for the
3/9/20-story buildings under P-Δ + held ``1.4 × seismic mass`` gravity, reports
the equal-energy and first-yield strength ratios, saves the raw curves to
``output/sac_pushover/`` and writes a normalised pushover figure to
``docs/assets/sac_pushover_curves.png``.

Usage::

    python scripts/run_sac_pushover.py [--buildings 3,9,20]
"""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from fea_toolkit.benchmarks import sac_run

#: SAC storey height (13 ft), used to convert roof displacement → drift ratio.
STOREY_H = 3.96


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--buildings", type=str, default="3,9,20")
    args = parser.parse_args(argv)
    buildings = [int(b) for b in args.buildings.split(",") if b.strip()]

    out_dir = Path("output/sac_pushover")
    out_dir.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(8, 5))
    rows = []
    for b in buildings:
        res = sac_run.pushover_strength_ratio(b)
        rows.append(res)
        drift = res["control_disp"] / (b * STOREY_H)
        gamma_curve = res["base_shear"] / res["W"]
        ax.plot(drift * 100, gamma_curve, marker=".", ms=3, label=f"{b}-story")
        if res["gamma_first_yield"] is not None and res["disp_first_yield"] is not None:
            ax.plot(
                res["disp_first_yield"] / (b * STOREY_H) * 100,
                res["gamma_first_yield"],
                "o",
                ms=8,
                markeredgewidth=1.5,
                markerfacecolor="none",
            )
        (out_dir / f"building_{b}.json").write_text(
            json.dumps(
                {
                    "building": b,
                    "W_N": res["W"],
                    "roof_drift": drift.tolist(),
                    "base_shear_coeff": gamma_curve.tolist(),
                    "base_shear_N": res["base_shear"].tolist(),
                    "gamma_equal_energy": res["gamma"],
                    "gamma_first_yield": res["gamma_first_yield"],
                    "disp_first_yield_m": res["disp_first_yield"],
                    "V_peak_N": res["V_peak"],
                    "roof_drift_peak": res["roof_drift_peak"],
                },
                indent=2,
            )
        )

    ax.set_xlabel("Roof drift ratio (%)")
    ax.set_ylabel("Base-shear coefficient V/W")
    ax.set_title("SAC pushover — IMK, P-Δ + held 1.4×mass gravity (open marker = first yield)")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig("docs/assets/sac_pushover_curves.png", dpi=150)
    print("wrote docs/assets/sac_pushover_curves.png")

    print(f"\n{'bld':<4}{'W (MN)':>10}{'γ_eq-energy':>13}{'γ_first-yield':>15}{'drift@fy (%)':>15}")
    for r in rows:
        fy = (
            f"{r['disp_first_yield'] / (r['building'] * STOREY_H) * 100:.2f}"
            if r["disp_first_yield"] is not None
            else "—"
        )
        gfy = f"{r['gamma_first_yield']:.3f}" if r["gamma_first_yield"] is not None else "—"
        print(f"{r['building']:<4}{r['W'] / 1e6:>10.2f}{r['gamma']:>13.3f}{gfy:>15}{fy:>15}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
