#!/usr/bin/env python
"""Download USGS NSMP corrected SMA-1 records for the IK-2005 LMSR-N set.

CESMD's EDC holds no waveform for several LMSR-N records.  The USGS National
Strong-Motion Project archive (``escweb.wr.usgs.gov/nsmp-data``) has them as
SMC-format files inside per-event ZIPs — ``corr_sma-1.zip`` is the corrected
SMA-1 (free-field) accelerograms, ``vol_2/<instrument><channel>.smc``.

This fetches those, parses the SMC format
(:func:`fea_toolkit.io.ground_motion.parse_smc`), and writes
``data/ground_motion/local/<record_id>_<channel>.csv`` (``time,accel`` in m/s²).

Usage::

    python scripts/fetch_usgs_records.py --download [--keep-zip]
"""

from __future__ import annotations

import argparse
import csv
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

from fea_toolkit.io.ground_motion import parse_smc

#: The LMSR-N records that CESMD lacks but the USGS NSMP archive holds:
#: ``(record_id, event_id, instrument_number, station)``.  The corrected SMA-1
#: data for an event is ``ca.water.usgs.gov/nsmp/<event_id>/sma-1/zips/corr_sma-1.zip``.
USGS_RECORDS: list[tuple[str, str, int, str]] = [
    # Loma Prieta 1989 (event 891018_1)
    ("LP89hch", "891018_1", 2504, "Hollister City Hall"),
    ("LP89hda", "891018_1", 3928, "Hollister Airport Differential Array"),
    ("LP89slc", "891018_1", 4766, "Palo Alto SLAC Test Lab"),
    ("LP89svl", "891018_1", 4053, "Sunnyvale Colton Ave"),
]

#: Where downloaded records are written (the runner's local mirror).
LOCAL = Path("data/ground_motion/local")


def _zip_url(event_id: str) -> str:
    return f"https://ca.water.usgs.gov/nsmp/{event_id}/sma-1/zips/corr_sma-1.zip"


def _event_zip(event_id: str) -> Path:
    """Download (once, cached) and return the ``corr_sma-1.zip`` for an event."""
    path = LOCAL / f"{event_id}_corr_sma1.zip"
    if not path.exists():
        url = _zip_url(event_id)
        print(f"fetch  {url}")
        with urllib.request.urlopen(url, timeout=180) as resp:
            path.write_bytes(resp.read())
    return path


def _smc_member(zf: zipfile.ZipFile, instrument: int, channel: str):
    suffix = f"vol_2/{instrument:04d}{channel}.smc"
    for info in zf.infolist():
        if info.filename.endswith(suffix):
            return info
    return None


def _write_csv(csv_path: Path, times, accel) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["time", "accel"])
        for t, a in zip(times, accel):
            writer.writerow([f"{t:.6f}", f"{a:.6e}"])


def cmd_download(keep_zip: bool = False) -> int:
    """Download and convert every USGS record into ``local/<id>_<channel>.csv``."""
    failed = 0
    zips: dict[str, Path] = {}
    for rid, event_id, instrument, station in USGS_RECORDS:
        if event_id not in zips:
            zips[event_id] = _event_zip(event_id)
        with zipfile.ZipFile(zips[event_id]) as zf:
            for channel in "abc":
                csv_path = LOCAL / f"{rid}_{channel}.csv"
                if csv_path.exists():
                    print(f"skip   {rid}_{channel} (already present)")
                    continue
                member = _smc_member(zf, instrument, channel)
                if member is None:
                    print(f"{rid:8s} {channel}: not found", file=sys.stderr)
                    failed += 1
                    continue
                text = zf.read(member).decode("utf-8", errors="replace")
                try:
                    times, accel, meta = parse_smc(text)
                except ValueError as exc:
                    print(f"{rid:8s} {channel}: {exc}", file=sys.stderr)
                    failed += 1
                    continue
                _write_csv(csv_path, times, accel)
                print(
                    f"fetch  {rid}_{channel} -> {csv_path} "
                    f"(comp={meta['component']}, orient={meta['orientation']})"
                )
    if not keep_zip:
        for path in zips.values():
            path.unlink(missing_ok=True)
    return 1 if failed else 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="download and convert")
    parser.add_argument("--keep-zip", action="store_true", help="keep the event .zip files")
    args = parser.parse_args(argv)
    return cmd_download(keep_zip=args.keep_zip)


if __name__ == "__main__":
    raise SystemExit(main())
