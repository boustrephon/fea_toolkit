#!/usr/bin/env python
"""Download USGS NSMP corrected SMA-1 records for the IK-2005 LMSR-N set.

CESMD's EDC holds no waveform for several LMSR-N records.  The USGS National
Strong-Motion Project archive (``escweb.wr.usgs.gov/nsmp-data``) has them, in
two layouts:

* **1986+ events** — SMC-format files inside a per-event
  ``corr_sma-1.zip`` (``vol_2/<instrument><channel>.smc``), e.g. Loma Prieta.
* **pre-1986 events** — the DDS-7 CD-ROM ``data2/cdrom_sm_1933-1986/<year>/<year>.zip``,
  one file per component named ``DDDHHMMSS.SSc`` (day, hour, minute, station,
  component), e.g. Imperial Valley 1979.

Both are parsed by :func:`fea_toolkit.io.ground_motion.parse_smc` and written to
``data/ground_motion/local/<record_id>_<channel>.csv`` (``time,accel`` in m/s²).

Usage::

    python scripts/fetch_usgs_records.py --download
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

#: The USGS archive is Cloudflare-fronted and rejects urllib's default
#: ``Python-urllib/3.x`` user agent with HTTP 403 — send an explicit one.
USER_AGENT = "fea_toolkit/1.0 (ground-motion fetcher)"

#: Where downloaded records are written (the runner's local mirror).
LOCAL = Path("data/ground_motion/local")

#: Loma Prieta 1989 (SMC): ``(record_id, event_id, instrument, station)``.
LOMA_PRIETA: list[tuple[str, str, int, str]] = [
    ("LP89hch", "891018_1", 2504, "Hollister City Hall"),
    ("LP89hda", "891018_1", 3928, "Hollister Airport Differential Array"),
    ("LP89slc", "891018_1", 4766, "Palo Alto SLAC Test Lab"),
    ("LP89svl", "891018_1", 4053, "Sunnyvale Colton Ave"),
]

#: Imperial Valley 1979 (DDS-7): ``(record_id, member_base, station)`` where
#: ``member_base`` is the DDS-7 filename minus the trailing channel char.
IMPERIAL_VALLEY: list[tuple[str, str, str]] = [
    ("IV79e01", "288x16e0.10", "El Centro Array #1"),
    ("IV79e12", "288x16e1.20", "El Centro Array #12"),
    ("IV79e13", "288x16e1.30", "El Centro Array #13"),
    ("IV79cal", "288x16ca.l0", "Calipatria Fire Station"),
    ("IV79nil", "288x16ni.l0", "Niland Fire Station"),
    ("IV79pls", "288x16pl.s0", "Plaster City"),
    ("IV79wsm", "288x16ws.m0", "Westmoreland Fire Station"),
    ("IV79chi", "288x16ch.ih", "Chihuahua"),
    ("IV79cmp", "288x16cm.pt", "Compuertas"),
    ("IV79qkp", "288x16cu.ca", "Cucapah"),
]

LOMA_PRIETA_ZIP_URL = "https://ca.water.usgs.gov/nsmp/891018_1/sma-1/zips/corr_sma-1.zip"
IV79_ZIP_URL = "https://escweb.wr.usgs.gov/nsmp-data/data2/cdrom_sm_1933-1986/1979/1979.zip"


def _download(url: str, dest: Path) -> None:
    """Download *url* to *dest* (with an explicit User-Agent).

    The response is written to a temporary file first and promoted to *dest*
    only once it is a readable ZIP archive whose members pass CRC validation,
    so a failed or truncated download can never leave a bad cached archive at
    *dest*.
    """
    print(f"fetch  {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    tmp = dest.with_name(dest.name + ".tmp")
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            tmp.write_bytes(resp.read())
        # Only promote a body that is a readable ZIP archive whose members all
        # pass CRC validation — a truncated download must never be promoted.
        with zipfile.ZipFile(tmp) as zf:
            bad_member = zf.testzip()
            if bad_member is not None:
                raise zipfile.BadZipFile(f"corrupt member {bad_member!r} in {url}")
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)


def _write_csv(csv_path: Path, times, accel) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["time", "accel"])
        for t, a in zip(times, accel):
            writer.writerow([f"{t:.6f}", f"{a:.6e}"])


def _extract_and_write(zf: zipfile.ZipFile, member, rid: str, channel: str, csv_path: Path) -> bool:
    """Extract and write one SMC record; ``False`` when ``parse_smc`` fails."""
    text = zf.read(member).decode("utf-8", errors="replace")
    try:
        times, accel, meta = parse_smc(text)
    except ValueError as exc:
        print(f"{rid:8s} {channel}: {exc}", file=sys.stderr)
        return False
    _write_csv(csv_path, times, accel)
    print(
        f"fetch  {rid}_{channel} -> {csv_path} (comp={meta['component']}, orient={meta['orientation']})"
    )
    return True


def cmd_download() -> int:
    """Download and convert every USGS record into ``local/<id>_<channel>.csv``."""
    failed = 0
    # ── Loma Prieta 1989 (SMC, corr_sma-1.zip) ──
    lp_zip = LOCAL / "891018_1_corr_sma1.zip"
    if not lp_zip.exists():
        _download(LOMA_PRIETA_ZIP_URL, lp_zip)
    with zipfile.ZipFile(lp_zip) as zf:
        for rid, _event_id, instrument, _station in LOMA_PRIETA:
            for channel in "abc":
                csv_path = LOCAL / f"{rid}_{channel}.csv"
                if csv_path.exists():
                    print(f"skip   {rid}_{channel} (already present)")
                    continue
                member = next(
                    (
                        i
                        for i in zf.infolist()
                        if i.filename.endswith(f"vol_2/{instrument:04d}{channel}.smc")
                    ),
                    None,
                )
                if member is None:
                    continue  # a station may lack a channel
                if not _extract_and_write(zf, member, rid, channel, csv_path):
                    failed += 1

    # ── Imperial Valley 1979 (DDS-7 CD-ROM, 1979.zip) ──
    iv_zip = LOCAL / "1979.zip"
    if not iv_zip.exists():
        _download(IV79_ZIP_URL, iv_zip)
    with zipfile.ZipFile(iv_zip) as zf:
        for rid, base, _station in IMPERIAL_VALLEY:
            for channel in "abc":
                csv_path = LOCAL / f"{rid}_{channel}.csv"
                if csv_path.exists():
                    print(f"skip   {rid}_{channel} (already present)")
                    continue
                name = f"{base}{channel}"
                member = next((i for i in zf.infolist() if i.filename == name), None)
                if member is None:
                    continue  # Cucapah has only two channels
                if not _extract_and_write(zf, member, rid, channel, csv_path):
                    failed += 1
    return 1 if failed else 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="download and convert")
    args = parser.parse_args(argv)
    if not args.download:
        parser.print_help()
        return 0
    return cmd_download()


if __name__ == "__main__":
    raise SystemExit(main())
