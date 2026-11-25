#!/usr/bin/env python
"""IK-2005 tangent-stiffness βK probe (frame 0909, two records).

Runs the two bracketing records under tangent-stiffness Rayleigh βK
(``damping_type="rayleigh_tangent"`` → ``C = a0·M + a1·K_t``) and prints the
collapse scale next to the initial-stiffness βK baseline already measured by
``ik2005_damping_probe.py`` (Rayleigh 5 % → IV79cal 72.750, IV79e13 77.750).

Caveat: in this lumped-hinge model the zeroLength springs carry no βK
(``doRayleighDamping = 0``), so the tangent βK differs from the initial βK only
through the P-Δ geometric-stiffness softening of the elastic members — hinge
yielding itself does *not* reduce βK (the IK-2005 Appendix B.4
zero-βK-on-springs rule).  A near-null result therefore refutes the "βK decays
as the hinges yield" reading of DRAIN-2DX's behaviour, not this implementation.
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

#: Initial-stiffness βK baseline (Rayleigh 5 %), from ik2005_damping_probe.py.
BASELINE = {"IV79cal": 72.750, "IV79e13": 77.750}


def main() -> int:
    header = f"{'record':<10}{'config':<20}{'collapse_scale':>16}{'baseline βK₀':>16}{'t':>6}"
    print(header, flush=True)
    for rid in RECORDS:
        channel = USGS_CHANNELS.get(rid)
        t0 = time.time()
        res = gfr.run_ida(
            CODE,
            rid,
            LADDER,
            tolerance=TOLERANCE,
            max_scale=MAX_SCALE,
            channel=channel,
            damping=0.05,
            damping_type="rayleigh_tangent",
        )
        cs = res["collapse_intensity"]
        cs_s = "—" if cs is None else f"{cs:.3f}"
        print(
            f"{rid:<10}{'tangent βK 5.0%':<20}{cs_s:>16}{BASELINE[rid]:>16.3f}"
            f"{time.time() - t0:>6.0f}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
