"""Client for the CESMD (Center for Engineering Strong Motion Data) web services.

CESMD (<https://strongmotioncenter.org>) is the merged USGS (NSMP) + California
Geological Survey (CSMIP) strong-motion archive.  Its web services expose
earthquake / station / record metadata and zipped record datasets, which is
what the IK-2005 LMSR-N collapse benchmark needs (the 40 California recordings
are all in this archive, and unlike PEER NGA-West2 it needs no academic email).

Endpoints (per the CESMD web-services documentation, mirrored in the Internet
Archive 2026-01-19 — the live site was DNS-unreachable at the time of writing):

* ``/wserv/events/query``   — earthquake metadata (GeoJSON / QuakeML / GeoCSV)
* ``/wserv/stations/query`` — station metadata (GeoJSON / StationXML / CSV)
* ``/wserv/records/query``  — record metadata (JSON/XML/CSV) or, with
  ``rettype=dataset``, a zipped package of waveforms

A *dataset* download is authenticated with a registered e-mail address (any
address — CESMD's registration form is optional for browsing).  Pass it as the
``email`` argument or set the :data:`CESMD_EMAIL_ENV` environment variable.

.. note::
   The dataset ZIP nests a per-record inner ZIP whose ``.V2`` member is the
   CSMIP "Volume 2" corrected accelerogram; :func:`parse_vol2` reads its first
   channel block, and :func:`parse_vol2_channel` selects a named channel.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

import numpy as np

#: Base URL of the CESMD web services.
CESMD_BASE = "https://strongmotioncenter.org"
#: Record-query endpoint (metadata and dataset).
RECORDS_QUERY = CESMD_BASE + "/wserv/records/query"

#: Environment variable holding the registered e-mail used to authenticate
#: dataset downloads.
CESMD_EMAIL_ENV = "CESMD_EMAIL"
#: Default request timeout (seconds).
DEFAULT_TIMEOUT = 120.0


class CESMDError(RuntimeError):
    """A CESMD request failed (network, HTTP, or parse error)."""


def _records_url(**params: Any) -> str:
    """Build a ``/wserv/records/query`` URL, dropping ``None`` parameters."""
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    return f"{RECORDS_QUERY}?{qs}"


def _fetch_json(url: str, timeout: float) -> dict[str, Any]:
    """``GET`` *url* and decode the JSON response body."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read()
    except (urllib.error.URLError, OSError) as exc:
        raise CESMDError(
            f"could not reach CESMD ({url}): {exc}. "
            "The CESMD site may be down (DNS or network outage)."
        ) from exc
    text = raw.decode("utf-8").strip()
    if not text:
        # A no-match query returns an empty body — treat it as zero results.
        return {"count": 0, "results": {}}
    try:
        return json.loads(text)
    except ValueError as exc:
        raise CESMDError(f"invalid JSON from CESMD ({url}): {exc}") from exc


def query_records(
    *,
    evname: Optional[str] = None,
    stname: Optional[str] = None,
    startdate: Optional[str] = None,
    enddate: Optional[str] = None,
    minmag: Optional[float] = None,
    maxmag: Optional[float] = None,
    groupby: str = "event",
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Query record *metadata* for records matching the given criteria.

    Args:
        evname: Event name (accepts wildcards, e.g. ``"Loma Prieta"``).
        stname: Station name (substring match, e.g. ``"Capitola"``).
        startdate: Earliest origin time ``"YYYY-MM-DD HH:MM:SS"`` (UTC).
        enddate: Latest origin time ``"YYYY-MM-DD HH:MM:SS"`` (UTC).
        minmag: Minimum magnitude (inclusive).
        maxmag: Maximum magnitude (inclusive).
        groupby: Grouping of the results — ``"event"`` (default) nests stations
            under ``results.events``, ``"station"`` nests events under
            ``results.stations``.
        timeout: Request timeout (s).

    Returns:
        The parsed JSON response — ``{"details", "count", "results"}`` where
        ``results.events[*].stations[*]`` carry a singular ``record`` dict
        (``pgav2``, ``epidist``, ``data_availability``, …).  A no-match query
        returns ``{"count": 0, "results": {}}``.

    Raises:
        CESMDError: On network failure, an HTTP error, or invalid JSON.
    """
    return _fetch_json(
        _records_url(
            evname=evname,
            stname=stname,
            startdate=startdate,
            enddate=enddate,
            minmag=minmag,
            maxmag=maxmag,
            rettype="metadata",
            format="json",
            groupby=groupby,
        ),
        timeout,
    )


def year_bounds(year: int) -> tuple[str, str]:
    """``(startdate, enddate)`` UTC strings spanning the whole of *year*."""
    return f"{year}-01-01 00:00:00", f"{year}-12-31 23:59:59"


def download_dataset(
    dest: Path,
    filename: str,
    *,
    evname: Optional[str] = None,
    stname: Optional[str] = None,
    startdate: Optional[str] = None,
    enddate: Optional[str] = None,
    email: Optional[str] = None,
    download: str = "P",
    groupby: str = "event",
    timeout: float = DEFAULT_TIMEOUT,
) -> Path:
    """Download a zipped record dataset into *dest* and return the saved path.

    Args:
        dest: Directory to write the ``.zip`` into (created if missing).
        filename: Name (without extension) of the saved file.
        evname: Event name (wildcards allowed).
        stname: Station name (wildcards allowed).
        startdate: Earliest origin time ``"YYYY-MM-DD HH:MM:SS"`` (UTC).
        enddate: Latest origin time ``"YYYY-MM-DD HH:MM:SS"`` (UTC).
        email: Registered e-mail authenticating the download (defaults to the
            :data:`CESMD_EMAIL_ENV` environment variable).
        download: ``"P"`` processed (Vol2), ``"R"`` raw, or ``"T"`` plot.
        groupby: Grouping of the returned dataset — ``"event"`` (default) or
            ``"station"``.
        timeout: Request timeout (s).

    Returns:
        The path of the saved ``.zip`` file.

    Raises:
        CESMDError: If no e-mail is available, or the request fails.
    """
    email = email or os.environ.get(CESMD_EMAIL_ENV)
    if not email:
        raise CESMDError(
            "a registered e-mail is required to download CESMD datasets; "
            f"pass email=... or set the {CESMD_EMAIL_ENV} environment variable"
        )
    url = _records_url(
        evname=evname,
        stname=stname,
        startdate=startdate,
        enddate=enddate,
        rettype="dataset",
        download=download,
        groupby=groupby,
        email=email,
    )
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / f"{filename}.zip"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = resp.read()
    except (urllib.error.URLError, OSError) as exc:
        raise CESMDError(
            f"could not download from CESMD ({url}): {exc}. "
            "The CESMD site may be down (DNS or network outage)."
        ) from exc
    if not data:
        raise CESMDError(
            f"CESMD returned an empty dataset for {filename!r} — the e-mail "
            f"{email!r} is likely not registered (a registered e-mail is "
            "required to download datasets)."
        )
    target.write_bytes(data)
    return target


# ── Volume-2 (corrected accelerogram) parsing ────────────────────────

_ACCEL_MARKER_RE = re.compile(r"(\d+)\s*POINTS\s+OF\s+ACC(?:EL)?\s+DATA", re.IGNORECASE)
_AT_SEC_RE = re.compile(r"AT\s+([\d.]+)\s*SEC", re.IGNORECASE)
#: A ``Corrected accelerogram`` line opens each channel block of a (possibly
#: multichannel) CSMIP/CESMD Volume-2 file; single-channel files may omit it.
_BLOCK_START_RE = re.compile(r"corrected\s+accelerogram", re.IGNORECASE)
#: The channel number in a block's header, e.g. ``Chan  1:  90 Deg``.
_CHANNEL_RE = re.compile(r"\bchan(?:nel)?\s+(\d+)\s*:", re.IGNORECASE)


def _vol2_spans(lines: list[str]) -> list[tuple[Optional[int], int, int]]:
    """The ``(channel_or_None, start, end)`` spans of a Volume-2 file's blocks.

    A block opens at each ``Corrected accelerogram`` line (a single-channel file
    may omit it, yielding one block spanning the whole file).  Each span's
    channel number is read from its ``Chan  N:`` header line, ``None`` when that
    header is absent (the caller then numbers it by position).
    """
    starts = [i for i, line in enumerate(lines) if _BLOCK_START_RE.search(line)]
    if not starts:
        starts = [0]
    spans: list[tuple[Optional[int], int, int]] = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(lines)
        chan: Optional[int] = None
        for line in lines[start:end]:
            match = _CHANNEL_RE.search(line)
            if match:
                chan = int(match.group(1))
                break
        spans.append((chan, start, end))
    return spans


def parse_vol2_channels(text: str) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """Parse every channel block of a (possibly multichannel) Volume-2 file.

    A CSMIP/CESMD Volume-2 file holds one or more channel blocks, each opened
    by a ``Corrected accelerogram`` line and, in multichannel files, carrying
    its channel number in a ``Chan  N:`` header line (e.g.
    ``Chan  1:  90 Deg``).  Each block's acceleration is returned keyed by that
    channel number; a block with no ``Chan  N:`` line is numbered by its
    position (1, 2, …), which keeps single-channel files addressable as
    channel 1.

    Args:
        text: The raw text of a Volume-2 file.

    Returns:
        ``{channel_number: (times, accel)}`` — times (s) and acceleration
        (m/s², converted from the file's cm/s²) for each channel block, in file
        order.

    Raises:
        CESMDError: If no acceleration block is present, if a block's time step
            is missing, if a block holds fewer values than ``NPTS``, if a
            non-numeric token appears in a block's values, or if two blocks
            resolve to the same channel number (a later block must not silently
            replace an earlier one).
    """
    lines = text.replace("\r", "").split("\n")
    spans = _vol2_spans(lines)

    channels: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for fallback, (chan, start, end) in enumerate(spans, start=1):
        key = chan if chan is not None else fallback
        if key in channels:
            raise CESMDError(
                f"Volume-2 file has more than one channel {key} block; "
                "refusing to merge or drop one"
            )
        channels[key] = _parse_vol2_block(lines, start, end)
    return channels


def _parse_vol2_block(lines: list[str], start: int, end: int) -> tuple[np.ndarray, np.ndarray]:
    """Parse the acceleration block between ``lines[start]`` and ``lines[end]``."""
    for i in range(start, end):
        match = _ACCEL_MARKER_RE.search(lines[i])
        if not match:
            continue
        npts = int(match.group(1))
        dt_match = _AT_SEC_RE.search(lines[i])
        if not dt_match:
            raise CESMDError("ACCEL DATA marker is missing its time step")
        dt = float(dt_match.group(1))
        values: list[float] = []
        for data_line in lines[i + 1 : end]:
            for tok in data_line.split():
                try:
                    values.append(float(tok))
                except ValueError as exc:
                    raise CESMDError(f"non-numeric token {tok!r} in the ACCEL DATA block") from exc
                if len(values) >= npts:
                    break
            if len(values) >= npts:
                break
        if len(values) < npts:
            raise CESMDError(f"Volume-2 file has {len(values)} values, expected {npts}")
        accel_cms2 = np.asarray(values[:npts], dtype=float)
        times = np.arange(npts, dtype=float) * dt
        return times, accel_cms2 / 100.0  # cm/s² → m/s²
    raise CESMDError("could not find the ACCEL DATA block")


def parse_vol2(text: str) -> tuple[np.ndarray, np.ndarray]:
    """Parse the first channel block of a CESMD/CSMIP Volume-2 accelerogram.

    The CSMIP "Volume 2" file has a prose header and integer/real headers, then
    blocks of ``NPTS`` acceleration / velocity / displacement values, each
    introduced by a line like ``"2000 POINTS OF ACCEL DATA EQUALLY SPACED AT
    .020 SEC.  (UNITS: CM/SEC/SEC)"``.  This reads the acceleration block.
    For a multichannel file, the first block in file order is returned; use
    :func:`parse_vol2_channel` to select a specific channel.

    Args:
        text: The raw text of a Volume-2 file.

    Returns:
        ``(times, accel)`` — time points (s) and acceleration (m/s², converted
        from the file's cm/s²).

    Raises:
        CESMDError: If the first channel block's acceleration marker or time
            step is missing, if that block holds fewer values than ``NPTS``, or
            if a non-numeric token appears in that block.  Later blocks are not
            parsed, so a malformed later block does not raise here.
    """
    lines = text.replace("\r", "").split("\n")
    _chan, start, end = _vol2_spans(lines)[0]
    return _parse_vol2_block(lines, start, end)


def parse_vol2_channel(text: str, channel: int) -> tuple[np.ndarray, np.ndarray]:
    """Parse the *channel* block of a (possibly multichannel) Volume-2 file.

    Selects the channel block whose ``Chan  N:`` header line matches *channel*;
    a single-channel file with no such line is addressable as channel 1 (its
    block is numbered by position — see :func:`parse_vol2_channels`).

    Args:
        text: The raw text of a Volume-2 file.
        channel: The 1-based channel number to select (``1`` for ``CH1``).

    Returns:
        ``(times, accel)`` — time points (s) and acceleration (m/s²) for the
        requested channel.

    Raises:
        CESMDError: If the file has no acceleration block, no block matching
            *channel*, or more than one block matching *channel*.
    """
    lines = text.replace("\r", "").split("\n")
    spans = _vol2_spans(lines)
    matches: list[tuple[int, int]] = []
    available: set[int] = set()
    for fallback, (chan, start, end) in enumerate(spans, start=1):
        key = chan if chan is not None else fallback
        available.add(key)
        if key == channel:
            matches.append((start, end))
    if not matches:
        raise CESMDError(
            f"Volume-2 file has no channel {channel} block (channels: {sorted(available)})"
        )
    if len(matches) > 1:
        raise CESMDError(
            f"Volume-2 file has more than one channel {channel} block; "
            "refusing to merge or drop one"
        )
    start, end = matches[0]
    return _parse_vol2_block(lines, start, end)
