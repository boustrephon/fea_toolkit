#!/usr/bin/env python
"""Download the 7 PEER NGA-West2-only IK-2005 LMSR-N records.

The 40-record LMSR-N set used by the IK-2005 generic-frame collapse benchmark
(``docs/ik2005_generic_frame_benchmark.md``) is split across three archives:
33 records come from CESMD / the USGS NSMP archive, and 7 exist only in PEER
NGA-West2.  This script fetches those 7 (``scripts/fetch_cesmd_records.py`` and
``scripts/fetch_usgs_records.py`` cover the other 33).

NGA-West2 needs an interactive login, so the download is driven with Selenium
(installed via the ``[nga-download]`` extra).  Credentials come from the
``NGA_W2_USER`` / ``NGA_W2_PASS`` environment variables.  Each record's two
horizontal components are downloaded, and the one whose PGA matches IK-2005
Table 4.2 is copied to ``data/ground_motion/local/<record_id>.AT2``.

Search by event + station, not the historical hand-guessed RSNs (which are
unreliable): run ``--list`` to see the records, look up the correct RSN for
each on <https://ngawest2.berkeley.edu>, then download by RSN.

Usage::

    python scripts/fetch_peer_records.py --list
    python scripts/fetch_peer_records.py --download --rsns 763,779,... [--no-headless]
    python scripts/fetch_peer_records.py --select <dir-of-at2-files> --rsns 763,779,...

To download (or select) only the first N records, pass ``--limit N``; the
``--rsns`` list must then hold exactly N RSNs in ``PEER_RECORDS`` order, e.g.
``--download --rsns 763 --limit 1`` for the first record alone.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import shutil
import sys
import time
import zipfile
from pathlib import Path
from typing import Optional

from fea_toolkit.io.ground_motion import read_peer_record, select_component_by_pga

#: The 7 LMSR-N records that exist only in PEER NGA-West2:
#: ``(record_id, event, station, target_pga_g)``.  ``target_pga_g`` is the
#: IK-2005 Table 4.2 PGA (in g) used to select the horizontal component.
PEER_RECORDS: list[tuple[str, str, str, float]] = [
    ("LP89agw", "Loma Prieta", "Agnews State Hospital", 0.172),
    ("LP89sjw", "Loma Prieta", "Salinas - John & Work", 0.112),
    ("NR94lv6", "Northridge", "Leona Valley #6", 0.178),
    ("NR94pic", "Northridge", "LA - Pico & Sentous", 0.186),
    ("SH87bra", "Superstition Hills", "Brawley", 0.156),
    ("SH87icc", "Superstition Hills", "El Centro Imp. Co. Cent", 0.358),
    ("SH87pls", "Superstition Hills", "Plaster City", 0.186),
]

#: Where the selected .AT2 records are written (the runner's local mirror).
LOCAL = Path("data/ground_motion/local")

#: Environment variables holding the PEER NGA-West2 credentials.
USER_ENV = "NGA_W2_USER"
PASS_ENV = "NGA_W2_PASS"

#: Hard per-run cap.  PEER limits ~200 records / 2 weeks, 400 / month; a run
#: over this many RSNs is refused so the account is never tripped by accident.
MAX_RECORDS = 50

_SIGN_IN_URL = "https://ngawest2.berkeley.edu/members/sign_in"
_SEARCH_URL = "https://ngawest2.berkeley.edu/spectras/new?sourceDb_flag=1"
_ZIP_PREFIX = "PEERNGARecords_Unscaled"

_RSN_RE = re.compile(r"RSN(\d+)", re.IGNORECASE)
_VERTICAL_SUFFIXES = (
    "-UP",
    "_UP",
    "UP",
    "-V",
    "_V",
    "-VERT",
    "_VERT",
    "-VRT",
    "_VRT",
    "-UD",
    "_UD",
    "UD",
    "-DWN",
    "_DWN",
    "DWN",
)


def _is_vertical(filename: str) -> bool:
    """Whether *filename* names a vertical (non-horizontal) ``.AT2`` component.

    NGA-West2 writes the vertical channel as a trailing ``-UP`` / ``_UP`` /
    ``-UD`` / ``-DWN`` (or ``-V``) marker; the trailing bare ``UP`` / ``UD`` /
    ``DWN`` forms are also covered because a horizontal azimuth suffix (e.g.
    ``…090``) never ends in any of them.
    """
    return Path(filename).stem.upper().endswith(_VERTICAL_SUFFIXES)


def _rsn_of(filename: str) -> Optional[int]:
    m = _RSN_RE.search(Path(filename).name)
    return int(m.group(1)) if m else None


def group_at2_by_rsn(paths: list[Path]) -> dict[int, list[Path]]:
    """Group horizontal ``.AT2`` files by their leading RSN."""
    grouped: dict[int, list[Path]] = {}
    for p in paths:
        if not p.is_file() or p.suffix.lower() != ".at2" or _is_vertical(p.name):
            continue
        rsn = _rsn_of(p.name)
        if rsn is not None:
            grouped.setdefault(rsn, []).append(p)
    return grouped


def select_record_at2(paths: list[Path], target_pga_g: float) -> Optional[Path]:
    """Return the horizontal ``.AT2`` whose PGA (g) is closest to *target_pga_g*."""
    candidates = {str(p): read_peer_record(str(p)) for p in paths}
    label = select_component_by_pga(candidates, target_pga_g)
    return Path(label) if label is not None else None


def select_records(
    records: list[tuple[str, str, str, float]], rsns: list[int], at2_dir: Path
) -> int:
    """Copy the PGA-matching horizontal of each record to ``LOCAL/<id>.AT2``."""
    grouped = group_at2_by_rsn(list(at2_dir.rglob("*")))
    LOCAL.mkdir(parents=True, exist_ok=True)
    failed = 0
    for (rid, _ev, _st, pga), rsn in zip(records, rsns):
        paths = grouped.get(int(rsn), [])
        if not paths:
            print(f"{rid:8s} !! no horizontal .AT2 for RSN {rsn} in {at2_dir}", file=sys.stderr)
            failed += 1
            continue
        chosen = select_record_at2(paths, pga)
        if chosen is None:
            print(f"{rid:8s} !! could not select a horizontal for RSN {rsn}", file=sys.stderr)
            failed += 1
            continue
        target = LOCAL / f"{rid}.AT2"
        shutil.copyfile(chosen, target)
        print(f"select {rid} <- {chosen.name} (target PGA {pga:.3f} g) -> {target}")
    return failed


def _dir_size(directory: Path) -> int:
    return sum(p.stat().st_size for p in directory.rglob("*") if p.is_file())


def _wait_for_download_stable(
    directory: Path, interval: float, stable_checks: int, timeout: float
) -> None:
    """Poll until a new ``PEERNGARecords_Unscaled*.zip`` finishes downloading.

    A stable directory size only counts as "complete" once a ZIP has actually
    appeared: a directory that is merely idle (no archive yet) must keep
    waiting, so the run's own download — not a stale archive left by an
    earlier run — is what completes.  The configured *timeout* bounds the
    whole wait.
    """
    start = time.time()
    stable = 0
    last = _dir_size(directory)
    while stable < stable_checks:
        if time.time() - start > timeout:
            raise TimeoutError(f"download did not finish within {timeout:.0f}s")
        time.sleep(interval)
        size = _dir_size(directory)
        has_zip = any(directory.glob(_ZIP_PREFIX + "*.zip"))
        stable = stable + 1 if (has_zip and size <= last) else 0
        last = size


def download_rsns(
    username: str,
    password: str,
    rsns: list[int],
    download_dir: Path,
    *,
    headless: bool = True,
    wait_interval: float = 6.0,
    stable_checks: int = 5,
    timeout: float = 600.0,
) -> Path:
    """Log in and download the unscaled ``.AT2`` records for *rsns*.

    Follows the flow of the reference tool ``NGA_W2_RSNs2TH`` (and ``PyPeerNGA``);
    the element locators below were verified against those references and should
    be re-checked against the live site before first use.  Returns the path of
    the downloaded ``PEERNGARecords_Unscaled*.zip``.
    """
    from selenium.common.exceptions import NoSuchElementException
    from selenium.webdriver.common.by import By

    driver = _new_driver(download_dir, headless)
    try:
        driver.get(_SIGN_IN_URL)
        driver.implicitly_wait(10)
        driver.find_element(By.ID, "user_email").send_keys(username)
        driver.find_element(By.ID, "user_password").send_keys(password)
        driver.find_element(By.ID, "user_submit").click()
        _wait_for_login(driver, headless)

        driver.get(_SEARCH_URL)
        # terms-of-use confirmation shown on first entry
        with contextlib.suppress(NoSuchElementException):
            driver.find_element(By.CSS_SELECTOR, "#buttons > button").click()
        driver.find_element(By.ID, "search_search_nga_number").send_keys(
            ",".join(str(int(r)) for r in rsns)
        )
        driver.find_element(
            By.XPATH, "//button[@type='button' and contains(@onclick, 'OnSubmit')]"
        ).click()
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")
        driver.find_element(
            By.XPATH, "//button[@type='button' and contains(@onclick, 'getSelectedResult')]"
        ).click()
        driver.switch_to.alert.accept()
        driver.switch_to.alert.accept()
        _wait_for_download_stable(download_dir, wait_interval, stable_checks, timeout)
    except NoSuchElementException as exc:
        raise RuntimeError(
            "Could not drive the NGA-West2 download — a page element was not found "
            "(the site UI may have changed, or human verification blocked the headless "
            "browser).  Retry with --no-headless, complete any verification manually, "
            "and re-check the selectors in scripts/fetch_peer_records.py."
        ) from exc
    finally:
        driver.quit()
    for p in download_dir.glob(_ZIP_PREFIX + "*.zip"):
        return p
    raise FileNotFoundError(f"no {_ZIP_PREFIX}*.zip appeared in {download_dir}")


def _new_driver(download_dir: Path, headless: bool):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_experimental_option(
        "prefs",
        {
            "download.default_directory": str(download_dir),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
        },
    )
    return webdriver.Chrome(options=opts)


def _wait_for_login(driver, headless: bool) -> None:
    """Wait for the sign-in to redirect away from the members sign-in page.

    A successful login navigates the browser off the sign-in URL, so this
    returns immediately once that happens; a failed one (or a pending human
    verification) leaves the browser there.  Headless runs get a short window —
    verification cannot be completed headless, so a timeout means the run
    should be retried visibly — while ``--no-headless`` runs get a longer one
    so a human can complete any CAPTCHA / two-factor check.
    """
    from selenium.common.exceptions import TimeoutException
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    timeout = 30.0 if headless else 300.0
    try:
        WebDriverWait(driver, timeout).until(EC.url_changes(_SIGN_IN_URL))
    except TimeoutException as exc:
        raise RuntimeError(
            "Login did not complete — the browser stayed on the sign-in page.  "
            "Check the credentials, then retry with --no-headless and complete "
            "any CAPTCHA / verification manually."
        ) from exc


def _parse_rsns(s: str) -> list[int]:
    return [int(tok) for tok in s.replace(" ", "").split(",") if tok]


def cmd_list(limit: Optional[int] = None) -> int:
    records = PEER_RECORDS[:limit] if limit else PEER_RECORDS
    print("PEER NGA-West2-only LMSR-N records (search by event + station):")
    for i, (rid, event, station, pga) in enumerate(records, 1):
        print(f"  {i:2d}. {rid:8s}  {event:20s}  {station:30s}  PGA {pga:.3f} g")
    print(
        "\nLook up the correct RSN for each record on ngawest2.berkeley.edu (the "
        "historical hand-guessed RSNs are unreliable), then download with:\n"
        "    --download --rsns <n1>,<n2>,...   (one RSN per record, in the order shown)"
    )
    return 0


def _records_for(limit: Optional[int]):
    return PEER_RECORDS[:limit] if limit else PEER_RECORDS


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--list", action="store_true", help="list the 7 records (no browser)")
    group.add_argument("--download", action="store_true", help="log in and download by RSN")
    group.add_argument(
        "--select",
        metavar="DIR",
        help="select + rename from an existing directory of downloaded .AT2 files",
    )
    parser.add_argument(
        "--rsns", help="comma-separated RSNs, one per record, in PEER_RECORDS order"
    )
    parser.add_argument("--no-headless", action="store_true", help="run the browser visibly")
    parser.add_argument("--limit", type=int, default=None, help="process only the first N records")
    parser.add_argument(
        "--dir", default=str(Path("data/ground_motion/downloads")), help="download directory"
    )
    args = parser.parse_args(argv)

    if args.list:
        return cmd_list(args.limit)

    rsns = _parse_rsns(args.rsns) if args.rsns else []
    records = _records_for(args.limit)

    if args.select:
        if len(rsns) != len(records):
            print(f"error: {len(rsns)} RSNs given for {len(records)} records", file=sys.stderr)
            return 2
        return 1 if select_records(records, rsns, Path(args.select)) else 0

    if args.download:
        user = os.environ.get(USER_ENV)
        password = os.environ.get(PASS_ENV)
        if not user or not password:
            print(
                f"set {USER_ENV} and {PASS_ENV} to your PEER NGA-West2 credentials", file=sys.stderr
            )
            return 2
        if not rsns:
            print(
                "--download requires --rsns (one RSN per record, in PEER_RECORDS order)",
                file=sys.stderr,
            )
            return 2
        if len(rsns) != len(records):
            print(f"error: {len(rsns)} RSNs given for {len(records)} records", file=sys.stderr)
            return 2
        if len(rsns) > MAX_RECORDS:
            print(
                f"refusing to download {len(rsns)} records (cap {MAX_RECORDS}); batch them",
                file=sys.stderr,
            )
            return 2
        download_dir = Path(args.dir).resolve()
        download_dir.mkdir(parents=True, exist_ok=True)
        # Chrome ignores a relative ``download.default_directory``, so resolve
        # to an absolute path and use the *same* resolved directory for the
        # browser preference, the size-stable polling and the archive discovery.
        # A run-specific subdirectory also makes the returned ZIP unambiguous:
        # it can only be the one this run downloaded, never a stale archive
        # already sitting in ``download_dir``.
        run_dir = download_dir / f"run-{time.time_ns()}"
        run_dir.mkdir(parents=True, exist_ok=True)
        try:
            zip_path = download_rsns(user, password, rsns, run_dir, headless=not args.no_headless)
        except (RuntimeError, FileNotFoundError, TimeoutError) as exc:
            print(f"download failed: {exc}", file=sys.stderr)
            return 1
        extract_dir = run_dir / "extracted"
        extract_dir.mkdir(exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_dir)
        n_files = sum(
            1 for p in extract_dir.rglob("*") if p.is_file() and p.suffix.lower() == ".at2"
        )
        print(f"extracted {n_files} .AT2 file(s) from {zip_path.name}")
        failed = select_records(records, rsns, extract_dir)
        print(
            f"\n{len(records) - failed}/{len(records)} records selected; "
            f"quota consumed: {len(records)} this run."
        )
        return 1 if failed else 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
