"""FEMA 355C / SAC 9-story benchmark (T2): modal periods vs Ohtori et al. (2004).

The nine-story FEMA 355C / SAC Phase-2 steel moment frame (LA, pre-Northridge)
is built by the unified generator (:func:`fea_toolkit.benchmarks.fema355c.build_fema355c`)
as a planar N-S frame and solved with the native modal runner.  This closes the
gap between the *generator* benchmark (geometry/schedule checks only) and an
actual OpenSees modal analysis against the published evaluation-model
frequencies.

Reference — Ohtori, Christenson, Spencer & Dyke (2004), "Benchmark Control
Problems for Seismically Excited Nonlinear Buildings", *ASCE J. Eng. Mech.*
130(4):366, DOI 10.1061/(ASCE)0733-9399(2004)130:4(366): the first five natural
frequencies of the 9-story evaluation model are 0.443, 1.18, 2.05, 3.09 and
4.27 Hz — periods 2.257 s, 0.847 s and 0.488 s for the first three modes.

The 9-story model reproduces these to well inside ±1 % (measured ≈ 2.242 /
0.845 / 0.485 s) — a direct consequence of matching the benchmark's base
definition: a pinned base at B-1 plus a lateral tie at the *ground* level
(``struct9.m``'s Fix_node).  The 20-story model matches its published
frequencies (0.261 / 0.753 / 1.30 Hz → 3.831 / 1.328 / 0.769 s) to within
±1 % once the same ground-level tie is placed at the ground level (level 2,
top of the B-1 basement) rather than the intermediate B-1 level.

For basement buildings the ground-level lateral tie is optional
(``build_fema355c(..., restrain_ground_laterally=False)``) — the common
"bounding" alternative that restrains the structure only at the bottom
basement level.
"""

import pytest

from fea_toolkit.benchmarks.fema355c import build_fema355c
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
from fea_toolkit.opensees.preprocessor import preprocess_model

# First three natural periods (s), Ohtori et al. (2004) — 1/0.443, 1/1.18, 1/2.05.
REF_PERIODS = (2.257, 0.847, 0.488)

# First three 20-story periods (s) — 1/0.261, 1/0.753, 1/1.30.
REF_PERIODS_20 = (3.831, 1.328, 0.769)


def _periods(stories: int, **kwargs) -> list[float]:
    """Build and solve a planar N-S frame; return its first three periods (s)."""
    from openseespy.opensees import wipe

    wipe()
    try:
        md = build_fema355c("LA", stories, "pre", "2d", **kwargs)
        mesh = preprocess_model(md, {"element_type": "elasticBeamColumn"})
        b = AnalysisBuilder(mesh, {})
        b.build_domain()
        res = b.run_modal_analysis(num_modes=3, print_results=False, eigen_solver="fullGenLapack")
        return [float(p) for p in res["periods"][:3]]
    finally:
        wipe()


@pytest.fixture
def builder():
    """The LA 9-story pre-Northridge planar N-S frame, domain already built."""
    from openseespy.opensees import wipe

    wipe()
    md = build_fema355c("LA", 9, "pre", "2d")
    mesh = preprocess_model(md, {"element_type": "elasticBeamColumn"})
    b = AnalysisBuilder(mesh, {})
    b.build_domain()
    yield b
    wipe()


def test_modal_periods_match_ohtori(builder):
    """The first three periods land on Ohtori's evaluation-model values.

    The first two modes are checked at ±3 % (matching the SAC 3-story anchor);
    the third at ±5 % — higher modes are more sensitive to the level-by-level
    splice approximation and the gravity-bay simplification.
    """
    result = builder.run_modal_analysis(
        num_modes=3, print_results=False, eigen_solver="fullGenLapack"
    )
    got = result["periods"][:3]
    assert len(got) == 3
    for g, r in zip(got[:2], REF_PERIODS[:2]):
        assert g == pytest.approx(r, rel=0.03), f"period {g:.4f} vs reference {r}"
    assert got[2] == pytest.approx(REF_PERIODS[2], rel=0.05), (
        f"period {got[2]:.4f} vs reference {REF_PERIODS[2]}"
    )


def test_masses_only_mass_source_is_emitted():
    """The generator emits a masses-only MASS SOURCE so the seismic masses land.

    Regression guard for the bug where ``added_masses`` were populated but no
    ``mass_sources`` entry marked ``masses=True`` — ``compute_seismic_masses``
    then fell back to element self-weight + DEAD loads and silently dropped the
    benchmark's seismic masses (yielding T1 ≈ 0.41 s instead of ≈ 2.26 s).
    """
    md = build_fema355c("LA", 9, "pre", "2d")
    assert md.added_masses, "expected per-level seismic masses"
    assert "MSSSRC1" in md.mass_sources
    assert md.mass_sources["MSSSRC1"].masses is True


def test_20story_modal_periods_match_ohtori():
    """The 20-story first three periods land on Ohtori's values (3.831/1.328/0.769).

    Ohtori's 20-story frequencies (0.261, 0.753, 1.30 Hz) match only once the
    ground-level lateral tie is placed at the *ground* level (level 2, top of
    the B-1 basement), not at the intermediate B-1 level (level 1) — the
    earlier code tied level 1 and overestimated T1 by ≈ 3.5 %.
    """
    got = _periods(20)
    assert len(got) == 3
    for g, r in zip(got[:2], REF_PERIODS_20[:2]):
        assert g == pytest.approx(r, rel=0.03), f"period {g:.4f} vs reference {r}"
    assert got[2] == pytest.approx(REF_PERIODS_20[2], rel=0.05), (
        f"period {got[2]:.4f} vs reference {REF_PERIODS_20[2]}"
    )


def test_ground_lateral_restraint_bounds_the_period():
    """Removing the ground lateral tie (base-only restraint) lengthens T1.

    The two bounding cases for a basement building — laterally restrained at
    ground level (Ohtori) vs only at the bottom basement level — bracket the
    fundamental period; the base-only model is the flexible (longer-period)
    bound.
    """
    restrained = _periods(9)[0]
    released = _periods(9, restrain_ground_laterally=False)[0]
    assert released > restrained


def test_ground_lateral_tie_is_at_ground_level():
    """The 20-story lateral tie sits at the ground level, not the B-1 level.

    Regression guard for the ``n_base`` fix: level 2 (``01F``, ground) is
    laterally restrained while level 1 (``01B``, the B-1 basement) is free —
    matching Ohtori's ``struct20.m`` Fix_node (base nodes + ground nodes only).
    """
    md = build_fema355c("LA", 20, "pre", "2d")
    assert md.restraints["A-02B"].dofs == [1, 1, 1, 1, 0, 1]  # pinned base
    assert md.restraints["A-01B"].dofs == [0, 1, 0, 1, 0, 1]  # B-1 free laterally
    assert md.restraints["A-01F"].dofs == [1, 1, 0, 1, 0, 1]  # ground laterally tied

    released = build_fema355c("LA", 20, "pre", "2d", restrain_ground_laterally=False)
    assert released.restraints["A-01F"].dofs == [0, 1, 0, 1, 0, 1]  # ground released
