"""Tests for the PEER NGA-West2 fetch script (offline parts only).

The NGA-West2 site needs an interactive login, so these tests exercise the
offline parts — the 7-record manifest, the vertical-component filter, the RSN
grouping and the PGA-based component selection — with no network and no Selenium.
"""

import importlib.util
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load_fetch_script():
    spec = importlib.util.spec_from_file_location(
        "fetch_peer_records", _SCRIPTS / "fetch_peer_records.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_EXPECTED_PEER_IDS = {
    "LP89agw",
    "LP89sjw",
    "NR94lv6",
    "NR94pic",
    "SH87bra",
    "SH87icc",
    "SH87pls",
}


def test_peer_records_are_the_seven_nga_west2_only():
    fetch = _load_fetch_script()
    ids = {rid for rid, _ev, _st, _pga in fetch.PEER_RECORDS}
    assert ids == _EXPECTED_PEER_IDS
    assert len(fetch.PEER_RECORDS) == 7
    for rid, event, station, pga in fetch.PEER_RECORDS:
        assert rid in _EXPECTED_PEER_IDS
        assert event.strip() and station.strip()
        assert 0.0 < pga < 1.0


def test_is_vertical():
    fetch = _load_fetch_script()
    assert fetch._is_vertical("RSN763_LOMAP_AGW-UP.AT2")
    assert fetch._is_vertical("RSN763_LOMAP_AGW_UP.AT2")
    assert fetch._is_vertical("RSN763_LOMAP_AGWUP.AT2")
    assert fetch._is_vertical("RSN763_LOMAP_AGW-UD.AT2")
    assert fetch._is_vertical("RSN763_LOMAP_AGW_UD.AT2")
    assert fetch._is_vertical("RSN763_LOMAP_AGWUD.AT2")
    assert fetch._is_vertical("RSN763_LOMAP_AGW-DWN.AT2")
    assert fetch._is_vertical("RSN763_LOMAP_AGW_DWN.AT2")
    assert fetch._is_vertical("RSN763_LOMAP_AGWDWN.AT2")
    assert not fetch._is_vertical("RSN763_LOMAP_AGW000.AT2")
    assert not fetch._is_vertical("RSN763_LOMAP_AGW090.AT2")


def test_group_at2_by_rsn_skips_vertical(tmp_path):
    fetch = _load_fetch_script()
    (tmp_path / "RSN763_LOMAP_AGW000.AT2").write_text("", encoding="utf-8")
    (tmp_path / "RSN763_LOMAP_AGW090.AT2").write_text("", encoding="utf-8")
    (tmp_path / "RSN763_LOMAP_AGW-UP.AT2").write_text("", encoding="utf-8")
    (tmp_path / "RSN1045_NORTHR_PIC000.AT2").write_text("", encoding="utf-8")
    (tmp_path / "readme.txt").write_text("not an at2", encoding="utf-8")
    grouped = fetch.group_at2_by_rsn(list(tmp_path.iterdir()))
    assert set(grouped) == {763, 1045}
    assert len(grouped[763]) == 2  # vertical excluded
    assert len(grouped[1045]) == 1


def test_select_record_at2_picks_pga_matching_horizontal(tmp_path):
    fetch = _load_fetch_script()
    # Two horizontals with PGA 0.17 g and 0.10 g (stored in g, as NGA-West2 does).
    for name, pga in (("RSN763_LOMAP_AGW000.AT2", 0.17), ("RSN763_LOMAP_AGW090.AT2", 0.10)):
        (tmp_path / name).write_text(f"NPTS=2\nDT=0.005\n{pga}\n-{pga}\n", encoding="utf-8")
    chosen = fetch.select_record_at2(list(tmp_path.glob("*.AT2")), target_pga_g=0.172)
    assert chosen is not None
    assert chosen.name == "RSN763_LOMAP_AGW000.AT2"


def test_select_records_writes_named_at2(tmp_path, monkeypatch):
    fetch = _load_fetch_script()
    monkeypatch.setattr(fetch, "LOCAL", tmp_path / "local")
    src = tmp_path / "src"
    src.mkdir()
    (src / "RSN763_LOMAP_AGW000.AT2").write_text(
        "NPTS=2\nDT=0.005\n0.17\n-0.17\n", encoding="utf-8"
    )
    (src / "RSN763_LOMAP_AGW090.AT2").write_text(
        "NPTS=2\nDT=0.005\n0.10\n-0.10\n", encoding="utf-8"
    )
    failed = fetch.select_records([("LP89agw", "Loma Prieta", "Agnews", 0.172)], [763], src)
    assert failed == 0
    out = tmp_path / "local" / "LP89agw.AT2"
    assert out.exists()
    assert "0.17" in out.read_text()


def test_select_records_excludes_closest_vertical(tmp_path, monkeypatch):
    """A vertical component closest to the target PGA is still not selected."""
    fetch = _load_fetch_script()
    monkeypatch.setattr(fetch, "LOCAL", tmp_path / "local")
    src = tmp_path / "src"
    src.mkdir()
    # The vertical matches the target PGA best; it must be excluded, so the
    # (further) horizontal is the one selected and copied.
    (src / "RSN763_LOMAP_AGW000.AT2").write_text(
        "NPTS=2\nDT=0.005\n0.10\n-0.10\n", encoding="utf-8"
    )
    (src / "RSN763_LOMAP_AGW-UD.AT2").write_text(
        "NPTS=2\nDT=0.005\n0.172\n-0.172\n", encoding="utf-8"
    )
    failed = fetch.select_records([("LP89agw", "Loma Prieta", "Agnews", 0.172)], [763], src)
    assert failed == 0
    out = tmp_path / "local" / "LP89agw.AT2"
    assert out.exists()
    assert "0.10" in out.read_text()
