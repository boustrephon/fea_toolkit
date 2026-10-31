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
   The dataset ZIP contains CESMD "Volume 2" corrected accelerograms whose
   exact column layout is not fully pinned down here; :func:`parse_vol2` is a
   best-effort reader to be validated against a real file once CESMD is
   reachable again.
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
DEFAULT_TIMEOUT = 60.0

#: Standard gravity (m/s²) for the g → m/s² conversion.
_G_MS2 = 9.80665


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

_NPTS_RE = re.compile(r"NPTS\s*=\s*(\d+)", re.IGNORECASE)
_DT_RE = re.compile(r"DT\s*=\s*([\d.]+)", re.IGNORECASE)
_NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


def parse_vol2(text: str) -> tuple[np.ndarray, np.ndarray]:
    """Best-effort parse of a CESMD Volume-2 corrected accelerogram.

    Reads the header for ``NPTS`` and ``DT``, then treats the first ``NPTS``
    numeric tokens following the header as the acceleration series (in g),
    converting it to m/s².

    Args:
        text: The raw text of a Volume-2 file.

    Returns:
        ``(times, accel)`` — time points (s) and acceleration (m/s²).

    Raises:
        CESMDError: If ``NPTS`` / ``DT`` cannot be read, or too few values.

    Note:
        Unverified against a real CESMD file (the site was down at the time of
        writing).  Validate against a real Volume-2 file before trusting it.
    """
    npts_match = _NPTS_RE.search(text)
    dt_match = _DT_RE.search(text)
    if not npts_match or not dt_match:
        raise CESMDError("could not find NPTS/DT in the Volume-2 header")
    npts = int(npts_match.group(1))
    dt = float(dt_match.group(1))

    # Data begins after the header; collect every numeric token from there on.
    header_end = text.rfind("SEC")  # a common marker at the end of the NPTS line
    tail = text[header_end:] if header_end != -1 else text
    values = [float(tok) for tok in _NUM_RE.findall(tail)]
    if len(values) < npts:
        raise CESMDError(f"Volume-2 file has {len(values)} numeric values, expected {npts}")
    accel_g = np.asarray(values[:npts], dtype=float)
    times = np.arange(npts, dtype=float) * dt
    return times, accel_g * _G_MS2
