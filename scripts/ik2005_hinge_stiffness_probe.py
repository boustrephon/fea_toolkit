#!/usr/bin/env python
"""IK-2005 hinge-stiffness (geometric-response) probe (frame 0909, two records).

Runs the two bracketing records under a reduced ``hinge_stiffness_factor``
(the spring stiffness ``Ks = factor × 6EI/L``) and prints the collapse scale
next to the near-rigid baseline (factor 100) already measured by
``ik2005_damping_probe.py``.

The IK-2005 Appendix B.4 model fixes the hinge spring at ``n = 10 × the member``
(``Ks = n·Kbc``).  Our default ``hinge_stiffness_factor = 100`` makes the spring
near-rigid, so the elastic member carries ~all of ``6EI/L`` and the spring's
post-yield softening is masked until near exhaustion.  Lowering the factor to 10
lets the hinge softening feed into the structure's tangent stiffness sooner —
the geometric-response hypothesis for why the frame destabilises at ~7.94
instead of the paper's ~3.4.
"""

from __future__ import annotations

import time

from fea_toolkit.benchmarks import generic_frame_run as gfr
from fea_toolkit.benchmarks.ik2005_records import USGS_CHANNELS

CODE = "0909"
LADDER = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0)
MAX_SCALE = 128.0
TOLERANCE = 0.01
RECORDS = ("IV79cal", "IV79e13")

#: Near-rigid baseline (hinge_stiffness_factor = 100, Rayleigh 5 %) from
#: ik2005_damping_probe.py.
BASELINE = {"IV79cal": 72.750, "IV79e13": 77.750}

#: (label, hinge_stiffness_factor) — n = 10 is the Appendix B.4 prescription.
CONFIGS = [
    ("n=10 (paper)", 10.0),
    ("n=20", 20.0),
]


def main() -> int:
    print(
        f"{'record':<10}{'config':<16}{'collapse_scale':>16}{'baseline(×100)':>16}{'t':>6}",
        flush=True,
    )
    for rid in RECORDS:
        channel = USGS_CHANNELS.get(rid)
        for label, factor in CONFIGS:
            t0 = time.time()
            res = gfr.run_ida(
                CODE,
                rid,
                LADDER,
                tolerance=TOLERANCE,
                max_scale=MAX_SCALE,
                channel=channel,
                damping=0.05,
                damping_type="rayleigh",
                hinge_stiffness_factor=factor,
            )
            cs = res["collapse_intensity"]
            cs_s = "—" if cs is None else f"{cs:.3f}"
            print(
                f"{rid:<10}{label:<16}{cs_s:>16}{BASELINE[rid]:>16.3f}{time.time() - t0:>6.0f}",
                flush=True,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
