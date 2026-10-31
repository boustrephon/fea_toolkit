#!/usr/bin/env python
"""Download the IK-2005 LMSR-N (40-record) set from CESMD.

CESMD (<https://strongmotioncenter.org>) is the USGS + California Geological
Survey strong-motion archive — the same 40 California recordings as PEER
NGA-West2, but free and with no academic-email requirement.  The record list is
IK-2005 Table 4.2 (see ``docs/ik2005_generic_frame_benchmark.md``).

Two modes:

* ``--list`` — query record metadata for each of the 40 records and print the
  matches (CESMD station name, event, PGA).  Run this first to verify the
  station-name mapping before downloading.
* ``--download`` — fetch the processed (Vol2) dataset for each record and write
  ``data/ground_motion/local/<record_id>.csv`` (``time,accel`` in m/s²).

A dataset download needs a registered e-mail (any address).  Pass ``--email``
or set the ``CESMD_EMAIL`` environment variable.

Usage::

    python scripts/fetch_cesmd_records.py --list
    python scripts/fetch_cesmd_records.py --download --email you@example.com
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
import zipfile
from pathlib import Path
from typing import Optional

from fea_toolkit.io.cesmd import (
    CESMDError,
    download_dataset,
    parse_vol2,
    query_records,
    year_bounds,
)

#: The 40 LMSR-N records (IK-2005 Table 4.2):
#: ``(record_id, year, search_key, station)``.  ``search_key`` is a short
#: distinctive substring of the CESMD station name (CESMD uses its own naming,
#: e.g. ``"Calipatria; Fire Station"``, ``"El Centro; Array 12, …"``).  Three
#: Imperial Valley records — Chihuahua, Compuertas, Cucapah — are Baja
#: California (Mexico) stations that CESMD does **not** hold; they need PEER
#: NGA-West2 instead.
LMSR_N_RECORDS: list[tuple[str, int, str, str]] = [
    # Imperial Valley 1979 (Mw 6.5)
    ("IV79cal", 1979, "Calipatria", "Calipatria Fire Station"),
    ("IV79chi", 1979, "Chihuahua", "Chihuahua"),  # MX — not in CESMD
    ("IV79cmp", 1979, "Compuertas", "Compuertas"),  # MX — not in CESMD
    ("IV79e01", 1979, "Array 01", "El Centro Array #1"),
    ("IV79e12", 1979, "Array 12", "El Centro Array #12"),
    ("IV79e13", 1979, "Array 13", "El Centro Array #13"),
    ("IV79nil", 1979, "Niland", "Niland Fire Station"),
    ("IV79pls", 1979, "Plaster City", "Plaster City"),
    ("IV79qkp", 1979, "Cucapah", "Cucapah"),  # MX — not in CESMD
    ("IV79wsm", 1979, "Westmorland", "Westmorland Fire Station"),
    # Loma Prieta 1989 (Mw 6.9)
    ("LP89agw", 1989, "Agnews", "Agnews State Hospital"),
    ("LP89cap", 1989, "Capitola", "Capitola"),
    ("LP89g03", 1989, "Gilroy #3", "Gilroy Array #3"),
    ("LP89g04", 1989, "Gilroy #4", "Gilroy Array #4"),
    ("LP89gmr", 1989, "Gilroy #7", "Gilroy Array #7"),
    ("LP89hch", 1989, "Hollister City Hall", "Hollister City Hall"),
    ("LP89hda", 1989, "Hollister Differential", "Hollister Differential Array"),
    ("LP89hvr", 1989, "Halls Valley", "Halls Valley"),
    ("LP89sjw", 1989, "Salinas", "Salinas - John & Work"),
    ("LP89slc", 1989, "SLAC", "Palo Alto - SLAC Lab."),
    ("LP89svl", 1989, "Sunnyvale", "Sunnyvale - Colton Ave."),
    # Northridge 1994 (Mw 6.7)
    ("NR94cen", 1994, "Centinela", "LA - Centinela St."),
    ("NR94cnp", 1994, "Topanga Canyon", "Canoga Park - Topanga Can."),
    ("NR94far", 1994, "Faring", "LA - N Faring Rd."),
    ("NR94fle", 1994, "Fletcher", "LA - Fletcher Dr."),
    ("NR94glp", 1994, "Las Palmas", "Glendale - Las Palmas"),
    ("NR94hol", 1994, "Hollywood Storage Grounds", "LA - Hollywood Stor FF"),
    ("NR94lh1", 1994, "Lake Hughes #1", "Lake Hughes #1"),
    ("NR94lv2", 1994, "Leona Valley #2", "Leona Valley #2"),
    ("NR94lv6", 1994, "Leona Valley #6", "Leona Valley #6"),
    ("NR94nya", 1994, "Crescenta", "La Crescenta-New York"),
    ("NR94pic", 1994, "Pico", "LA - Pico & Sentous"),
    ("NR94stc", 1994, "Saticoy", "Northridge - 17645 Saticoy St."),
    ("NR94stn", 1994, "Saturn", "LA - Saturn St"),
    ("NR94ver", 1994, "Vernon", "LA - E Vernon Ave"),
    # San Fernando 1971 (Mw 6.6)
    ("SF71pel", 1971, "Hollywood Storage Lot", "LA - Hollywood Stor Lot"),
    # Superstition Hills 1987 (Mw 6.7)
    ("SH87bra", 1987, "Brawley", "Brawley"),
    ("SH87icc", 1987, "Imperial County", "El Centro Imp. Co. Cent"),
    ("SH87pls", 1987, "Plaster City", "Plaster City"),
    ("SH87wsm", 1987, "Westmorland", "Westmorland Fire Station"),
]

#: LMSR-N records that CESMD does **not** expose (older CSMIP/USGS records absent
#: from CESMD's web services) and that still need PEER NGA-West2.  (Some may
#: exist under different names; none are retrievable via the current search keys.)
NOT_IN_CESMD: set[str] = {
    "LP89agw",  # Agnews State Hospital (CSMIP; CESMD metadata-only)
    "LP89sjw",  # Salinas - John & Work (CSMIP; CESMD metadata-only)
    "NR94lv6",  # Leona Valley #6 (CESMD has #1-#5)
    "SH87bra",  # Brawley (Superstition Hills 1987)
    "SH87icc",  # El Centro Imp. Co. Cent
    "SH87pls",  # Plaster City (Superstition Hills 1987)
}

#: LMSR-N records already fetched from the USGS NSMP archive (see
#: ``scripts/fetch_usgs_records.py``) — skip them here.  This covers the whole
#: Imperial Valley 1979 event (CESMD holds none of it) and the four Loma Prieta
#: stations whose CESMD name either differs (Hollister City Hall / Diff Array,
#: SLAC) or maps to the wrong building (Sunnyvale → Salsman Residence).
VIA_USGS: set[str] = {
    "IV79cal",
    "IV79e01",
    "IV79e12",
    "IV79e13",
    "IV79nil",
    "IV79pls",
    "IV79wsm",
    "IV79chi",
    "IV79cmp",
    "IV79qkp",
    "LP89hch",
    "LP89hda",
    "LP89slc",
    "LP89svl",
}

#: Where downloaded records are written (the runner's local mirror).
LOCAL = Path("data/ground_motion/local")


def _iter_matches(res: dict) -> list[dict]:
    """Flatten a record-query response into per-record dicts."""
    out = []
    for ev in res.get("results", {}).get("events", []):
        for st in ev.get("stations", []):
            rec = st.get("record") or {}
            out.append(
                {
                    "event": ev.get("title") or ev.get("place") or "?",
                    "station": st.get("name") or "?",
                    "network": st.get("network") or "?",
                    "pga_raw": rec.get("pgav1"),
                    "pga_proc": rec.get("pgav2"),
                    "epidist": rec.get("epidist"),
                    "availability": rec.get("data_availability") or {},
                }
            )
    return out


def _search(key: str, year: int):
    start, end = year_bounds(year)
    return query_records(stname=key, startdate=start, enddate=end)


def _first_vol2_member(zf: zipfile.ZipFile) -> Optional[zipfile.ZipInfo]:
    """Pick the processed-acceleration Volume-2 member out of a dataset ZIP."""
    for info in zf.infolist():
        name = info.filename.upper()
        if name.endswith(("/", ".TXT")) and "VOL" not in name:
            continue
        if ("V2" in name or "VOL2" in name or "ACC" in name) and not name.endswith("/"):
            return info
    # Fallback: the first non-directory text member.
    for info in zf.infolist():
        if not info.is_dir():
            return info
    return None


def _zip_to_csv(zip_path: Path, csv_path: Path) -> None:
    """Extract the acceleration from a CESMD dataset ZIP and write ``time,accel``."""
    with zipfile.ZipFile(zip_path) as zf:
        member = _first_vol2_member(zf)
        if member is None:
            raise CESMDError(f"no acceleration member found in {zip_path}")
        text = zf.read(member).decode("utf-8", errors="replace")
    times, accel = parse_vol2(text)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["time", "accel"])
        for t, a in zip(times, accel):
            writer.writerow([f"{t:.6f}", f"{a:.6e}"])


def cmd_list(delay: float = 1.0, limit: Optional[int] = None) -> int:
    """Query metadata for every record and print the matches (no download)."""
    failed = 0
    records = LMSR_N_RECORDS[:limit] if limit else LMSR_N_RECORDS
    for i, (rid, year, key, station) in enumerate(records):
        if i:
            time.sleep(delay)
        if rid in NOT_IN_CESMD:
            print(f"{rid:8s} {station!r} ({year}) -> not in CESMD (needs PEER)")
            continue
        if rid in VIA_USGS:
            print(f"{rid:8s} {station!r} ({year}) -> already fetched from USGS NSMP")
            continue
        try:
            matches = _iter_matches(_search(key, year))
        except CESMDError as exc:
            print(f"{rid:8s} !! {exc}", file=sys.stderr)
            failed += 1
            continue
        print(f"{rid:8s} {station!r} ({year}) -> {len(matches)} match(es)")
        for m in matches[:3]:
            avail = m["availability"]
            print(
                f"          - {m['event']} | {m['station']} | "
                f"rawPGA={m['pga_raw']} procPGA={m['pga_proc']} | {avail}"
            )
    return 1 if failed else 0


def cmd_download(
    email: Optional[str],
    keep_zip: bool = False,
    delay: float = 1.0,
    limit: Optional[int] = None,
) -> int:
    """Download and convert every record to ``data/ground_motion/local/<id>.csv``."""
    failed = 0
    records = LMSR_N_RECORDS[:limit] if limit else LMSR_N_RECORDS
    for i, (rid, year, key, station) in enumerate(records):
        if i:
            time.sleep(delay)
        if rid in NOT_IN_CESMD:
            print(f"skip   {rid} (not in CESMD — needs PEER NGA-West2)")
            continue
        if rid in VIA_USGS:
            print(f"skip   {rid} (already fetched from USGS NSMP)")
            continue
        csv_path = LOCAL / f"{rid}.csv"
        if csv_path.exists():
            print(f"skip   {rid} (already present)")
            continue
        start, end = year_bounds(year)
        try:
            zip_path = download_dataset(
                LOCAL, rid, stname=key, startdate=start, enddate=end, email=email
            )
            _zip_to_csv(zip_path, csv_path)
            if not keep_zip:
                zip_path.unlink(missing_ok=True)
            print(f"fetch  {rid} -> {csv_path}")
        except CESMDError as exc:
            print(f"{rid:8s} !! {exc}", file=sys.stderr)
            failed += 1
    return 1 if failed else 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="query metadata only")
    group.add_argument("--download", action="store_true", help="download and convert")
    parser.add_argument("--email", help="registered e-mail for dataset downloads")
    parser.add_argument("--keep-zip", action="store_true", help="keep the .zip files")
    parser.add_argument(
        "--delay", type=float, default=1.0, help="seconds between requests (default 1.0)"
    )
    parser.add_argument("--limit", type=int, default=None, help="process only the first N records")
    args = parser.parse_args(argv)

    if args.list:
        return cmd_list(delay=args.delay, limit=args.limit)
    return cmd_download(args.email, keep_zip=args.keep_zip, delay=args.delay, limit=args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
