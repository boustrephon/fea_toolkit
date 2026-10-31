"""Manifest invariants for the IK-2005 LMSR-N ground-motion set.

Locks the record/channel manifest shared by ``scripts/run_ik2005_collapse.py``
and ``scripts/run_ik2005_npz.py``: 40 Table-4.2 IDs, 7 PEER NGA-West2-only, and
the USGS multi-component channel selection.
"""

from fea_toolkit.benchmarks.ik2005_records import (
    LMSR_N_IDS,
    PEER_ONLY,
    USGS_CHANNELS,
    fetched_ids,
    peer_only_ids,
)


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
    assert len(got) == 33
    assert len(skipped) == 7
    assert set(got) | set(skipped) == set(LMSR_N_IDS)
    assert not (set(got) & set(skipped))
    # table order is preserved
    assert got == [rid for rid in LMSR_N_IDS if rid not in PEER_ONLY]
    assert skipped == [rid for rid in LMSR_N_IDS if rid in PEER_ONLY]


def test_usgs_channels_reference_only_fetched_records():
    assert set(USGS_CHANNELS) <= set(fetched_ids())
    assert not (set(USGS_CHANNELS) & PEER_ONLY)
    assert len(USGS_CHANNELS) == 14
