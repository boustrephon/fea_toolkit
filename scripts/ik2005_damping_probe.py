#!/usr/bin/env python
"""IK-2005 damping-sensitivity probe (frame 0909, two records).

Maps the "damping cliff" identified in the benchmark doc — the ~5x collapse
sensitivity to the higher-mode damping content — by running two records under
several damping configurations and printing the collapse scale for each.

All configurations use the existing ``run_ida`` API (no code change):

* Rayleigh ξ = 5 / 2.5 / 1 % (anchored on T1 + T95, initial-stiffness βK₀);
* mass-proportional ξ = 5 % (``a1 = 0``, under-damps every higher mode).

Records ``IV79cal`` (clean non-convergence) and ``IV79e13`` (the steepest cliff
record in the doc) are the two the root-cause analysis already bracketed, so the
numbers extend that single-record table.
"""

from __future__ import annotations

import time

from fea_toolkit.benchmarks import generic_frame_run as gfr
from fea_toolkit.benchmarks.ik2005_records import USGS_CHANNELS

CODE = "0909"
LADDER = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0)
MAX_SCALE = 128.0
TOLERANCE = 0.01

#: (label, damping_type, damping_ratio)
CONFIGS = [
    ("Rayleigh 5.0%", "rayleigh", 0.05),
    ("Rayleigh 2.5%", "rayleigh", 0.025),
    ("Rayleigh 1.0%", "rayleigh", 0.01),
    ("mass-prop 5.0%", "mass_proportional", 0.05),
]

RECORDS = ("IV79cal", "IV79e13")


def main() -> int:
    print(
        f"{'record':<10}{'config':<18}{'collapse_scale':>16}{'criterion':>16}{'t':>6}", flush=True
    )
    for rid in RECORDS:
        channel = USGS_CHANNELS.get(rid)
        for label, dtype, ratio in CONFIGS:
            t0 = time.time()
            res = gfr.run_ida(
                CODE,
                rid,
                LADDER,
                tolerance=TOLERANCE,
                max_scale=MAX_SCALE,
                channel=channel,
                damping=ratio,
                damping_type=dtype,
            )
            cs = res["collapse_intensity"]
            cs_s = "—" if cs is None else f"{cs:.3f}"
            print(
                f"{rid:<10}{label:<18}{cs_s:>16}{res['collapse_criterion']!s:>16}"
                f"{time.time() - t0:>6.0f}",
                flush=True,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
