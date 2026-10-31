"""Tests for the CESMD web-services client and the LMSR-N fetch script.

The CESMD web services are documented (see ``fea_toolkit.io.cesmd``), but the
live site was DNS-unreachable at the time of writing, so these tests exercise
the offline parts — URL construction, the Volume-2 parser, the manifest, and
the download e-mail guard — with no network access.
"""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

from fea_toolkit.io import cesmd
from fea_toolkit.io.cesmd import CESMDError

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

#: The 40 LMSR-N record IDs (IK-2005 Table 4.2), sorted — the manifest must
#: contain exactly these.
_EXPECTED_IDS = [
    "IV79cal",
    "IV79chi",
    "IV79cmp",
    "IV79e01",
    "IV79e12",
    "IV79e13",
    "IV79nil",
    "IV79pls",
    "IV79qkp",
    "IV79wsm",
    "LP89agw",
    "LP89cap",
    "LP89g03",
    "LP89g04",
    "LP89gmr",
    "LP89hch",
    "LP89hda",
    "LP89hvr",
    "LP89sjw",
    "LP89slc",
    "LP89svl",
    "NR94cen",
    "NR94cnp",
    "NR94far",
    "NR94fle",
    "NR94glp",
    "NR94hol",
    "NR94lh1",
    "NR94lv2",
    "NR94lv6",
    "NR94nya",
    "NR94pic",
    "NR94stc",
    "NR94stn",
    "NR94ver",
    "SF71pel",
    "SH87bra",
    "SH87icc",
    "SH87pls",
    "SH87wsm",
]


def _load_fetch_script():
    spec = importlib.util.spec_from_file_location(
        "fetch_cesmd_records", _SCRIPTS / "fetch_cesmd_records.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ── cesmd module ────────────────────────────────────────────────────


def test_records_url_builds_expected_query():
    url = cesmd._records_url(stname="Calipatria Fire Station", rettype="metadata")
    assert url.startswith("https://strongmotioncenter.org/wserv/records/query?")
    assert "stname=Calipatria+Fire+Station" in url
    assert "rettype=metadata" in url


def test_records_url_drops_none_params():
    url = cesmd._records_url(evname=None, stname="Capitola", rettype="dataset")
    assert "evname" not in url
    assert "stname=Capitola" in url


def test_year_bounds():
    assert cesmd.year_bounds(1979) == ("1979-01-01 00:00:00", "1979-12-31 23:59:59")


def test_parse_vol2_extracts_acceleration_in_ms2():
    text = (
        "CORRECTED ACCELEROGRAM\n"
        "LOMA PRIETA EARTHQUAKE OF OCTOBER 17, 1989\n"
        "NPTS=   5, DT= .00500 SEC,\n"
        "0.1 0.2\n-0.3 0.4\n-0.05\n"
    )
    times, accel = cesmd.parse_vol2(text)
    np.testing.assert_allclose(times, np.arange(5) * 0.005)
    np.testing.assert_allclose(accel, np.array([0.1, 0.2, -0.3, 0.4, -0.05]) * 9.80665)


def test_parse_vol2_rejects_missing_header():
    with pytest.raises(CESMDError):
        cesmd.parse_vol2("no header here\n0.1 0.2 0.3\n")


def test_download_dataset_requires_email(tmp_path, monkeypatch):
    monkeypatch.delenv(cesmd.CESMD_EMAIL_ENV, raising=False)
    with pytest.raises(CESMDError):
        cesmd.download_dataset(tmp_path, "IV79cal", stname="Calipatria Fire Station")


def test_query_records_includes_groupby(monkeypatch):
    """The records query must pass ``groupby`` (required by the live API)."""
    captured: dict[str, str] = {}

    def fake_fetch(url: str, timeout: float) -> dict:
        captured["url"] = url
        return {"count": 0, "results": {}}

    monkeypatch.setattr(cesmd, "_fetch_json", fake_fetch)
    cesmd.query_records(stname="Capitola", groupby="event")
    assert "groupby=event" in captured["url"]
    assert "nodata" not in captured["url"]


# ── fetch script manifest ───────────────────────────────────────────


def test_lmsr_n_manifest_has_40_unique_records():
    fetch = _load_fetch_script()
    records = fetch.LMSR_N_RECORDS
    assert len(records) == 40
    ids = [rid for rid, _, _, _ in records]
    assert len(set(ids)) == 40, "duplicate record ids in the manifest"
    for _, year, key, station in records:
        assert 1900 <= year <= 2100
        assert key.strip()
        assert station.strip()


def test_lmsr_n_manifest_ids_match_ik2005_table():
    fetch = _load_fetch_script()
    ids = sorted(rid for rid, _, _, _ in fetch.LMSR_N_RECORDS)
    assert ids == sorted(_EXPECTED_IDS)
