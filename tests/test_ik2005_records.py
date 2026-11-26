"""Manifest invariants for the IK-2005 LMSR-N ground-motion set.

Locks the record/channel manifest shared by ``scripts/run_ik2005_collapse.py``
and ``scripts/run_ik2005_npz.py``: 40 Table-4.2 IDs, 7 PEER NGA-West2-only, and
the USGS multi-component channel selection.
"""

import pytest

from fea_toolkit.benchmarks.ik2005_records import (
    GROUND_MOTION_DIR_ENV,
    LMSR_N_IDS,
    PEER_ONLY,
    USGS_CHANNELS,
    fetched_ids,
    ground_motion_dir,
    missing_record_ids,
    peer_only_ids,
    peer_only_present,
)


@pytest.fixture(autouse=True)
def _isolate_ground_motion(tmp_path, monkeypatch):
    """Redirect the mirror to ``tmp_path`` so these tests never read the repo."""
    monkeypatch.setenv(GROUND_MOTION_DIR_ENV, str(tmp_path))


def test_lmsr_n_has_forty_unique_records():
    assert len(LMSR_N_IDS) == 40
    assert len(set(LMSR_N_IDS)) == 40


def test_peer_only_is_the_seven_nga_west2_records():
    assert {
        "LP89agw",
        "LP89sjw",
        "NR94lv6",
        "NR94pic",
        "SH87bra",
        "SH87icc",
        "SH87pls",
    } == PEER_ONLY
    assert set(LMSR_N_IDS) >= PEER_ONLY


def test_fetched_and_peer_only_partition_the_set():
    got = fetched_ids()
    skipped = peer_only_ids()
    # The 33 open-archive records are always "fetched"; a PEER-only record joins
    # fetched_ids() once its waveform is in the mirror (peer_only_present).  In a
    # clean checkout (no PEER files) that is exactly the 33/7 split.
    assert set(got) | set(skipped) == set(LMSR_N_IDS)
    assert got == [rid for rid in LMSR_N_IDS if rid not in PEER_ONLY or rid in peer_only_present()]
    assert skipped == [rid for rid in LMSR_N_IDS if rid in PEER_ONLY]


def test_peer_only_present_is_subset_of_peer_only():
    assert set(peer_only_present()) <= PEER_ONLY


def test_usgs_channels_reference_only_fetched_records():
    assert set(USGS_CHANNELS) <= set(fetched_ids())
    assert not (set(USGS_CHANNELS) & PEER_ONLY)
    assert len(USGS_CHANNELS) == 14


def test_missing_record_ids_reports_all_absent_in_a_clean_mirror():
    """Unlike ``fetched_ids``, ``missing_record_ids`` probes the whole mirror."""
    # The autouse fixture redirects the mirror to an empty tmp_path, so every
    # one of the 40 records is absent.
    assert set(missing_record_ids()) == set(LMSR_N_IDS)


def test_missing_record_ids_recognises_an_open_archive_record():
    """A present open-archive waveform drops its id from the missing list."""
    root = ground_motion_dir()
    (root / "local").mkdir(parents=True, exist_ok=True)
    # IV79cal is a USGS multi-component record whose selected channel is "a".
    (root / "local" / "IV79cal_a.csv").write_text("0,0\n0.005,1\n", encoding="utf-8")

    missing = missing_record_ids()
    assert "IV79cal" not in missing
    assert "LP89cap" in missing
