#!/usr/bin/env python
"""Validate the current IK-2005 code against the archived 0909 IDA baseline.

Re-runs two *unchanged* LMSR-N records on frame ``0909`` and compares their
collapse scale factors against ``output/ik2005_40record/ik2005_0909.npz`` (the
40-record, Rayleigh-on-(``T1``, ``T95``) baseline written by the full-matrix
re-run).  This is the gate for the overnight 40-record re-run: a match means the
current code still reproduces the archive, so the long run may proceed; a
mismatch means the code has drifted since the archive was written and the long
run must be held until it is understood.

Exit codes: 0 = match, 1 = mismatch, 2 = error.
"""

from __future__ import annotations

import numpy as np

from fea_toolkit.benchmarks import generic_frame_run as gfr
from fea_toolkit.benchmarks.ik2005_records import USGS_CHANNELS

#: Records re-run for the check — both *unchanged* since the archive, one from
#: each source path (USGS multi-component ``.csv`` and CESMD single-file ``.csv``).
VALIDITY_RECORDS = ("IV79cal", "LP89cap")

#: Match the archive's run settings exactly.
CODE = "0909"
LADDER = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0)
MAX_SCALE = 128.0
TOLERANCE = 0.01
ARCHIVE = "output/ik2005_40record/ik2005_0909.npz"
#: Relative difference below which a re-run is considered a match.
MATCH_REL = 0.01


def _archive_collapse_scale(archive: str, rid: str) -> float | None:
    with np.load(archive, allow_pickle=False) as z:
        key = f"ida/{rid}/collapse_scale"
        if key not in z.files:
            return None
        val = z[key].item()
    return None if (val is None or np.isnan(val)) else float(val)


def main() -> int:
    failed = False
    for rid in VALIDITY_RECORDS:
        channel = USGS_CHANNELS.get(rid)
        print(f"[{rid}] running IDA (channel={channel!r}) …", flush=True)
        res = gfr.run_ida(
            CODE,
            rid,
            LADDER,
            tolerance=TOLERANCE,
            max_scale=MAX_SCALE,
            channel=channel,
        )
        new_scale = res["collapse_intensity"]
        old_scale = _archive_collapse_scale(ARCHIVE, rid)
        if new_scale is None or old_scale is None:
            if new_scale is None and old_scale is None:
                print(f"[{rid}] new={new_scale!r} archive={old_scale!r}  → OK (both survivor)")
            else:
                failed = True
                print(
                    f"[{rid}] new={new_scale!r} archive={old_scale!r}  → MISMATCH (collapsed/survivor differs)"
                )
            continue
        rel = abs(new_scale - old_scale) / abs(old_scale)
        ok = rel <= MATCH_REL
        failed = failed or (not ok)
        print(
            f"[{rid}] new={new_scale:.6f} archive={old_scale:.6f} "
            f"rel_diff={rel:.6f}  → {'OK' if ok else 'MISMATCH'}"
        )
    print("VALIDITY CHECK PASSED" if not failed else "VALIDITY CHECK FAILED")
    return 0 if not failed else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"VALIDITY CHECK ERROR: {exc!r}")
        raise SystemExit(2) from exc
