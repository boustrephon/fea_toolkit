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
Both frequency sets reproduce to within ±1 %.
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
            assert g == pytest.approx(r, rel=0.01), f"{building}-story {g:.4f} s vs {r}"
    finally:
        wipe()


@pytest.mark.parametrize("building", [9, 20])
def test_trilinear_builder_builds(building):
    """``backbone='trilinear'`` builds a lumped-hinge domain for 9/20-story.

    Under ``hinge_model='lumped'`` the member-end releases are honoured: the
    9-story gravity-bay pin (``element_tbl`` type 5, M3 released at the far end)
    gets a release ``zeroLength`` and no hinge spring at that end (see
    ``test_9story_gravity_bay_pin_modeled_under_lumped``).
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


def test_9story_gravity_bay_pin_modeled_under_lumped():
    """The 9-story bay-4 fixed-pin beams keep their M3 release under lumped.

    Each ``B{lvl}-4`` beam (``lvl`` 1..10) has its far end M3-released; under
    ``hinge_model='lumped'`` the release route re-points that end onto its
    ``*_rel_j`` node while the hinge route leaves it alone (a hinge spring only
    at the moment end).
    """
    from openseespy.opensees import wipe

    wipe()
    try:
        b = build_builder(building=9, backbone="trilinear")
        bay4 = {f"B{lvl}-4" for lvl in range(1, 11)}
        released = set(b._release_repoints) & bay4
        assert released == bay4
        for eid in bay4:
            assert b._release_repoints[eid]["J"].endswith("_rel_j")
            # The released J-end carries no hinge re-point: it stays the mesh node.
            assert b._hinge_repoints[eid][1] == b.mesh_model.frame_elements[eid].node_j
    finally:
        wipe()


@pytest.mark.parametrize(
    "building,expected_levels",
    [
        (9, list(range(2, 11))),  # 2..10
        (20, [1, *range(3, 23)]),  # 1 and 3..22
    ],
    ids=["9-story", "20-story"],
)
def test_floor_ux_tie_groups(building, expected_levels):
    """The ``slv_tbl`` floor ties skip the base and ground-tie levels.

    Each tied level yields one ``[master, *5 slaves]`` group with the middle
    line (``{j}-2``) as master.  The 9-story ties levels 2..10 (skipping the
    base 0 and ground-tie 1); the 20-story ties level 1 and levels 3..22
    (skipping the base 0 and ground-tie 2) — matching ``struct#.m``'s
    master-slave table.
    """
    from fea_toolkit.benchmarks.sac import _floor_ux_tie_groups

    groups = _floor_ux_tie_groups(building)
    # Exact level set: the base (0) and ground lateral-tie level
    # (``n_basements``) are excluded; every other above-ground storey is tied.
    levels = [int(g[0].split("-")[0]) for g in groups]
    assert levels == expected_levels
    for group in groups:
        assert len(group) == 6  # master + 5 slaves
        master = group[0]
        assert master.endswith("-2")  # middle line
        slaves = group[1:]
        assert len(set(slaves)) == 5
        # every slave is at the same level as its master
        for s in slaves:
            assert s.split("-")[0] == master.split("-")[0]


def test_faithful_fidelity_applies_floor_ties():
    """``fidelity='faithful'`` wires the floor ties into the builder config."""
    from openseespy.opensees import wipe

    wipe()
    try:
        b = build_builder(building=20, backbone="trilinear", fidelity="faithful")
        assert len(b.config.get("floor_ux_ties", [])) == 21
    finally:
        wipe()
