#!/usr/bin/env python
"""Rayleigh-anchor probe: does the ``T2`` anchor explain the 0909 shift?

The 40-record re-run (paper's ``(T1, T95)`` anchor, ``_rayleigh_periods`` →
``T2 = 0.2077 s``) gave ``IV79cal`` a collapse scale of 72.75, versus 57.25 in
the pre-refactor archive.  Hypothesis: the archive used the *single-mode* anchor
``T2 = T1 × 0.5 = 0.45 s``, and switching to ``(T1, T95)`` moved the collapse
scale up (and pushed several records past the ladder, i.e. survivors).

This re-runs the two collapsed bracketing records plus four 40-record survivors
under the old ``T2 = T1 × 0.5`` anchor (by monkeypatching ``_rayleigh_periods``)
and prints the collapse scale against the known T95 value.  A match on the
collapsed records (and a survivor flipping to a finite scale) implicates the
anchor as the lever.
"""

from __future__ import annotations

import time

from fea_toolkit.benchmarks import generic_frame_run as gfr
from fea_toolkit.benchmarks.ik2005_records import USGS_CHANNELS

CODE = "0909"
LADDER = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0)
MAX_SCALE = 128.0
TOLERANCE = 0.01

#: ``(record, T95-anchor collapse scale)`` — ``None`` = survived the ladder.
T95: dict[str, float | None] = {
    "IV79cal": 72.75,
    "LP89cap": 29.125,
    "IV79e01": None,
    "NR94far": None,
    "NR94stn": None,
    "SH87bra": None,
}


def _run(rid: str) -> float | None:
    res = gfr.run_ida(
        CODE,
        rid,
        LADDER,
        tolerance=TOLERANCE,
        max_scale=MAX_SCALE,
        channel=USGS_CHANNELS.get(rid),
    )
    return res["collapse_intensity"]


def _fmt(v: float | None) -> str:
    return "— (survived)" if v is None else f"{v:.3f}"


def main() -> int:
    t0 = time.time()
    print(f"{'record':<10}{'T2 anchor':<12}{'collapse_scale':>20}{'T95 value':>20}", flush=True)

    orig = gfr._rayleigh_periods

    def old_anchor(code: str) -> tuple[float, float]:
        t1 = gfr.period_1(code)
        return t1, t1 * 0.5

    gfr._rayleigh_periods = old_anchor
    try:
        for rid, t95_value in T95.items():
            cs = _run(rid)
            print(
                f"{rid:<10}{'T1×0.5':<12}{_fmt(cs):>20}{_fmt(t95_value):>20}",
                flush=True,
            )
    finally:
        gfr._rayleigh_periods = orig

    print(f"elapsed {time.time() - t0:.0f} s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
