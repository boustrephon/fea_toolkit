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
        "PEAK ACCELERATION = -390.792 CM/SEC/SEC\n"
        " 5 POINTS OF ACCEL DATA EQUALLY SPACED AT  .005 SEC.  (UNITS: CM/SEC/SEC)\n"
        "0.1 0.2 -0.3 0.4 -0.05\n"
    )
    times, accel = cesmd.parse_vol2(text)
    np.testing.assert_allclose(times, np.arange(5) * 0.005)
    # cm/s² → m/s² (÷100)
    np.testing.assert_allclose(accel, np.array([0.1, 0.2, -0.3, 0.4, -0.05]) / 100.0)


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


def test_download_dataset_rejects_empty_response(tmp_path, monkeypatch):
    """An empty (HTTP 204) response — unregistered e-mail — is a clear error."""
    import urllib.request

    monkeypatch.setenv(cesmd.CESMD_EMAIL_ENV, "test@example.com")

    class _Empty:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b""

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Empty())
    with pytest.raises(CESMDError, match="not registered"):
        cesmd.download_dataset(tmp_path, "IV79cal", stname="Calipatria")


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


_EXPECTED_NOT_IN_CESMD = {
    "LP89agw",  # Agnews State Hospital
    "LP89sjw",  # Salinas - John & Work
    "NR94lv6",  # Leona Valley #6
    "NR94pic",  # LA - Pico & Sentous
    "SH87bra",  # Brawley
    "SH87icc",  # El Centro Imp. Co. Cent
    "SH87pls",  # Plaster City (Superstition Hills)
}

_EXPECTED_VIA_USGS = {
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


def test_not_in_cesmd_subset_of_manifest():
    fetch = _load_fetch_script()
    ids = {rid for rid, _, _, _ in fetch.LMSR_N_RECORDS}
    assert fetch.NOT_IN_CESMD == _EXPECTED_NOT_IN_CESMD
    assert fetch.VIA_USGS == _EXPECTED_VIA_USGS
    assert ids >= fetch.NOT_IN_CESMD
    assert ids >= fetch.VIA_USGS
    assert not (fetch.NOT_IN_CESMD & fetch.VIA_USGS), "skip sets must be disjoint"


# ── Event / channel selection ─────────────────────────────────────────


def test_event_for_maps_every_record_to_its_mainshock():
    """Each record id resolves to the mainshock its prefix encodes."""
    fetch = _load_fetch_script()
    expected = {
        "IV79": "Imperial Valley",
        "LP89": "Loma Prieta",
        "NR94": "Northridge",
        "SF71": "San Fernando",
        "SH87": "Superstition Hills",
    }
    for rid, _year, _key, _station in fetch.LMSR_N_RECORDS:
        assert fetch._event_for(rid) == expected[rid[:4]]


def test_first_vol2_member_verifies_channel_and_skips_vertical():
    """The member picker binds to an explicit channel marker, never a vertical one."""
    import io
    import zipfile

    fetch = _load_fetch_script()
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as zf:
        zf.writestr("station.V2", "1 2 3")  # bare .V2 → no marker (header-resolved)
        zf.writestr("station-2.V2", "4 5 6")  # explicit CH2 marker
        zf.writestr("stationUP.V2", "7 8 9")  # vertical marker
    data.seek(0)
    with zipfile.ZipFile(data) as zf:
        member1 = fetch._first_vol2_member(zf, channel="CH1")
        assert member1 is not None and member1.filename == "station.V2"
        member2 = fetch._first_vol2_member(zf, channel="CH2")
        assert member2 is not None and member2.filename == "station-2.V2"


def test_vol2_channel_ignores_station_number_suffix():
    """A trailing station-number digit is not read as a channel label."""
    fetch = _load_fetch_script()
    assert fetch._vol2_channel("CIWTT2.V2") is None  # station "Watts" (code WTT2)
    assert fetch._vol2_channel("CIWTT3.V2") is None  # not rejected as vertical
    assert fetch._vol2_channel("station-3.V2") == ""  # explicit CH3 marker → vertical
    assert fetch._vol2_channel("station_1.V2") == "CH1"
    assert fetch._vol2_channel("station-CH2.V2") == "CH2"


def test_parse_vol2_channel_selects_block_by_header():
    """A multichannel Vol2 file resolves a channel from its ``Chan  N:`` header."""
    text = (
        "Corrected accelerogram  38457511.CI.WTT2.--.H      Chan  2:   0 Deg\n"
        "Chan  2:   0 Deg\n"
        " 2 POINTS OF ACCEL DATA EQUALLY SPACED AT .005 SEC. (UNITS: CM/SEC/SEC)\n"
        "0.6 0.7\n"
        "Corrected accelerogram  38457511.CI.WTT2.--.H      Chan  1:  90 Deg\n"
        "Chan  1:  90 Deg\n"
        " 2 POINTS OF ACCEL DATA EQUALLY SPACED AT .005 SEC. (UNITS: CM/SEC/SEC)\n"
        "0.1 0.2\n"
    )
    times, accel = cesmd.parse_vol2_channel(text, 1)
    np.testing.assert_allclose(times, np.arange(2) * 0.005)
    np.testing.assert_allclose(accel, np.array([0.1, 0.2]) / 100.0)


def test_parse_vol2_channel_single_block_falls_back_to_1():
    """A single-channel file with no ``Chan N:`` header is addressable as channel 1."""
    text = (
        "Corrected accelerogram\n"
        " 2 POINTS OF ACCEL DATA EQUALLY SPACED AT .005 SEC. (UNITS: CM/SEC/SEC)\n"
        "0.1 0.2\n"
    )
    _times, accel = cesmd.parse_vol2_channel(text, 1)
    np.testing.assert_allclose(accel, np.array([0.1, 0.2]) / 100.0)
    with pytest.raises(CESMDError):
        cesmd.parse_vol2_channel(text, 2)


def test_zip_to_csv_selects_channel_block_by_header(tmp_path):
    """A bare multichannel .V2 member writes the CH1 block, not the first block."""
    import io
    import zipfile

    fetch = _load_fetch_script()
    text = (
        "Corrected accelerogram  38457511.CI.WTT2.--.H      Chan  2:   0 Deg\n"
        "Chan  2:   0 Deg\n"
        " 2 POINTS OF ACCEL DATA EQUALLY SPACED AT .005 SEC. (UNITS: CM/SEC/SEC)\n"
        "0.6 0.7\n"
        "Corrected accelerogram  38457511.CI.WTT2.--.H      Chan  1:  90 Deg\n"
        "Chan  1:  90 Deg\n"
        " 2 POINTS OF ACCEL DATA EQUALLY SPACED AT .005 SEC. (UNITS: CM/SEC/SEC)\n"
        "0.1 0.2\n"
    )
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as zf:
        zf.writestr("station.V2", text)
    data.seek(0)
    zip_path = tmp_path / "dataset.zip"
    zip_path.write_bytes(data.getvalue())
    csv_path = tmp_path / "record.csv"
    fetch._zip_to_csv(zip_path, csv_path, channel="CH1")
    rows = csv_path.read_text().splitlines()
    assert rows[0] == "time,accel"
    # The first block is CH2; the CH1 block must be selected, not the first one.
    assert float(rows[1].split(",")[1]) == pytest.approx(0.1 / 100.0)
    assert float(rows[2].split(",")[1]) == pytest.approx(0.2 / 100.0)
