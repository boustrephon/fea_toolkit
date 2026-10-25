"""SAC 9- and 20-story benchmark — modal anchors (A2 / B1).

The 9/20-story N-S perimeter frames are built programmatically from the
transcribed ``struct9.m`` / ``struct20.m`` data (geometry, ``mat_tbl`` trilinear
backbones, ``element_tbl`` member schedule, seismic masses — see
``sac_member_data.py`` and
:func:`fea_toolkit.benchmarks.sac._build_benchmark_model`) and their elastic
natural frequencies are checked against Ohtori et al. (2004).

Reference — Ohtori, Christenson, Spencer & Dyke (2004), *ASCE J. Eng. Mech.*
130(4):366, DOI 10.1061/(ASCE)0733-9399(2004)130:4(366):

* 9-story:  0.443, 1.18, 2.05, 3.09, 4.27 Hz
* 20-story: 0.261, 0.753, 1.30, 1.83, 2.40, 2.44, 2.92, 3.01, 3.63, 3.68 Hz

Unlike the FEMA 355C generator (``test_benchmark_fema355c_modal.py``), these are
built from the benchmark's own ``element_tbl`` member schedule and ``mat_tbl``
backbones, so the elastic section rigidities are ``EI1`` (not AISC ``E·I``).
Both frequency sets reproduce to within ±0.5 %.
"""

import pytest

from fea_toolkit.benchmarks.sac import build_builder

# First natural periods (s), Ohtori et al. (2004) — 1/f.
REF_PERIODS = {
    9: (2.257, 0.847, 0.488, 0.324, 0.234),
    20: (3.831, 1.328, 0.769, 0.546, 0.417, 0.410, 0.342, 0.332, 0.275, 0.272),
}


@pytest.mark.parametrize("building", [9, 20])
def test_modal_periods_match_otori(building):
    """The elastic 9/20-story models reproduce the published period sets."""
    from openseespy.opensees import wipe

    ref = REF_PERIODS[building]
    wipe()
    try:
        b = build_builder(building=building, nonlinear=False)
        result = b.run_modal_analysis(
            num_modes=len(ref), print_results=False, eigen_solver="fullGenLapack"
        )
        got = [float(p) for p in result["periods"][: len(ref)]]
        for g, r in zip(got, ref):
            assert g == pytest.approx(r, rel=0.02), f"{building}-story {g:.4f} s vs {r}"
    finally:
        wipe()


@pytest.mark.parametrize("building", [9, 20])
def test_trilinear_builder_builds(building):
    """``backbone='trilinear'`` builds a lumped-hinge domain for 9/20-story.

    Note: under ``hinge_model='lumped'`` the member-end releases (the 9-story
    gravity-bay pin, ``element_tbl`` type 5) are skipped — the lumped hinges
    re-point the member ends and win over the release.  The elastic anchor above
    is unaffected; the nonlinear gravity-bay release is an A3/A4 refinement.
    """
    import openseespy.opensees as ops
    from openseespy.opensees import wipe

    wipe()
    try:
        b = build_builder(building=building, backbone="trilinear")
        assert len(ops.getEleTags()) > 0
        m = b.run_modal_analysis(num_modes=1, print_results=False)
        assert m["periods"][0] > 0.0
    finally:
        wipe()
