"""SAC benchmark response-target transcription (``unctrl#.mat``, A4).

The Ohtori et al. (2004) ``unctrl#.mat`` targets are transcribed in
``fea_toolkit.benchmarks.sac_response_data``.  These checks pin the transcription
and the case ordering (``Run_NLBM.m``: El Centro 0.5/1.0/1.5, Hachinohe
0.5/1.0/1.5, Northridge 0.5/1.0, Kobe 0.5/1.0) against the archive values.
"""

from fea_toolkit.benchmarks.sac_response_data import CASE_ORDER, case_targets


def test_case_ordering_matches_run_nlbm():
    """The ten cases follow Run_NLBM.m's record × intensity loop order."""
    expected = [
        ("ELCENTRO", 0.5),
        ("ELCENTRO", 1.0),
        ("ELCENTRO", 1.5),
        ("HACHINOHE", 0.5),
        ("HACHINOHE", 1.0),
        ("HACHINOHE", 1.5),
        ("NORTHRIDGE", 0.5),
        ("NORTHRIDGE", 1.0),
        ("KOBE", 0.5),
        ("KOBE", 1.0),
    ]
    assert expected == CASE_ORDER


def test_3story_elcentro_1p0_targets():
    """The 3-story El Centro 1.0 (case 1) targets match the archive."""
    t = case_targets(3, 1)
    assert t.record == "ELCENTRO" and t.intensity == 1.0
    assert t.d_max == 0.015034
    assert t.xdd_max == 6.5389
    assert t.F_max == 1.0120e7
    assert t.phi_max == 1.8119
    assert t.E_max == 91.945
    assert t.N_d == 22


def test_9story_and_20story_first_cases():
    """The 9/20-story El Centro 0.5 (case 0) values are pinned."""
    assert case_targets(9, 0).F_max == 1.1580815e7
    assert case_targets(20, 0).F_max == 3.6882261e6
    assert case_targets(20, 0).d_max == 0.003542
