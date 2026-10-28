"""A3 — SAC member type reconcile (study override vs analytical role).

The benchmark's ``element_tbl(:,4)`` tags members as moment / brace / fixed-pin.
The general classifier derives the role analytically; the transcribed type is a
study *override* applied on top, warning wherever it disagrees.  The SAC frames
have no diagonal members or brace-shaped sections, so the only mismatches are the
3-story link bay and the 20-story 1st-basement beams — both tagged ``brace`` by
the study while the analytical rule (an I-section, horizontal) calls them moment.
The 9-story gravity bay maps to ``fixed-pin`` *analytically* (beam + pin release),
so it does not warn.

See ``docs/imk_member_classification.md``.
"""

import warnings

import pytest

from fea_toolkit.benchmarks import sac
from fea_toolkit.benchmarks.sac_member_data import (
    BEAM_TYPES,
    BEAM_TYPES_3,
    BEAM_TYPES_9,
    BEAM_TYPES_20,
)


def _load(building: int):
    return sac.load_model(nonlinear=True, building=building, backbone="imk")


def test_beam_type_transcription():
    """The transcribed ``BEAM_TYPES`` reproduce the source ``element_tbl(:,4)`` lines."""
    # struct3.m: element_tbl(Num_col+[4 8 12], 4) = 3 → bay 3 of every level.
    assert BEAM_TYPES_3 == [[2, 2, 2, 3]] * 3
    # struct9.m: element_tbl(Num_col+[5 10 15 20 25 30 35 40 45 50], 4) = 5 → bay 4.
    assert BEAM_TYPES_9 == [[2, 2, 2, 2, 5]] * 10
    # struct20.m: element_tbl(Num_col+[1 2 3 4 5], 4) = 3 → level 1 (1st basement).
    assert BEAM_TYPES_20[0] == [3, 3, 3, 3, 3]
    assert all(row == [2, 2, 2, 2, 2] for row in BEAM_TYPES_20[1:])
    assert set(BEAM_TYPES) == {3, 9, 20}


def test_reconcile_3story_warns_link_bay_brace():
    """The 3-story link bay is a study brace overriding an analytical moment role."""
    md = _load(3)
    brace_ids = sorted(eid for eid, t in md.imk_member_types.items() if t == "brace")
    assert brace_ids == ["19", "23", "27"]  # bay 3 of each floor
    assert set(md.imk_member_types.values()) == {"moment", "brace"}  # no fixed-pin
    assert len(md.imk_member_type_mismatches) == 3


def test_reconcile_9story_matches_analytically():
    """The 9-story gravity bay maps to fixed-pin analytically (beam + pin release)."""
    md = _load(9)
    fixed = {eid for eid, t in md.imk_member_types.items() if t == "fixed-pin"}
    assert fixed == {f"B{lvl}-4" for lvl in range(1, 11)}
    assert md.imk_member_type_mismatches == []  # no override warning


def test_reconcile_20story_warns_basement_braces():
    """The 20-story 1st-basement beams are study braces overriding moment."""
    md = _load(20)
    brace_ids = sorted(eid for eid, t in md.imk_member_types.items() if t == "brace")
    assert brace_ids == [f"B1-{i}" for i in range(5)]
    assert len(md.imk_member_type_mismatches) == 5


def test_reconcile_warns_userwarning():
    """``reconcile_member_types`` warns (``UserWarning``) on every mismatch."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # suppress the load_model stamping warnings
        md = _load(3)
        with pytest.warns(UserWarning, match="overrides analytical role 'moment'"):
            sac.reconcile_member_types(md, building=3)


def test_enrich_imk_skips_study_braces():
    """Study braces (link bay, basement beams) carry no ``imk_params``."""
    md = _load(3)
    for eid in ("19", "23", "27"):
        assert md.frame_elements[eid].imk_params is None

    md20 = _load(20)
    for eid in (f"B1-{i}" for i in range(5)):
        assert md20.frame_elements[eid].imk_params is None
