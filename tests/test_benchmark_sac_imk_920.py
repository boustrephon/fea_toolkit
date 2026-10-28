"""A3 — SAC 9/20-story IMK (IMKBilin) matrices (Step 3).

Wires ``enrich_imk`` for the 9/20-story benchmark frames: per-member unbraced
lengths (the varying storey heights) and the column-splice rows (mapped to their
below-splice physical section).  The 9/20-story sections are all ``Steel`` (no
``SteelCol``/``SteelBeam`` material marker), so the column/beam role is read
from element orientation, and each member gets its own ``imk_params`` — the
20-story ``mat8`` (W24x131) is used as *both* a column and a beam, which a
per-section stamp could not express.

See ``docs/imk_deterioration_model.md`` §5 (A3 item 5).
"""

import pytest

from fea_toolkit.benchmarks import sac


@pytest.mark.parametrize("building", [9, 20], ids=["9-story", "20-story"])
def test_imk_920_stamps_every_member(building):
    """Every 9/20-story member carries ``imk_params`` (no leaning column)."""
    from openseespy.opensees import wipe

    wipe()
    try:
        md = sac.load_model(nonlinear=True, building=building, backbone="imk")
        assert md.frame_elements, "empty model"
        for eid, elem in md.frame_elements.items():
            assert elem.imk_params is not None, f"{eid} missing imk_params"
            params = elem.imk_params
            assert params["mc_my"] >= 1.0
            assert params["My_pos"] > 0.0
            assert params["theta_p_pos"] > 0.0
            assert params["theta_pc_pos"] > 0.0
    finally:
        wipe()


def test_imk_920_shared_section_distinguishes_column_and_beam():
    """``mat8`` (W24x131) is a column *and* a beam in the 20-story — the two roles
    get different ``imk_params`` (a per-section stamp cannot express this)."""
    from openseespy.opensees import wipe

    wipe()
    try:
        md = sac.load_model(nonlinear=True, building=20, backbone="imk")
        col_my = []
        beam_my = []
        for eid, elem in md.frame_elements.items():
            if md.frame_assignments.get(eid) != "mat8":
                continue
            ni = md.nodes[elem.node_i]
            nj = md.nodes[elem.node_j]
            if abs(nj.z - ni.z) > abs(nj.x - ni.x):
                col_my.append(elem.imk_params["My_pos"])
            else:
                beam_my.append(elem.imk_params["My_pos"])
        assert col_my and beam_my  # mat8 is both a column and a beam
        # Column yield moment (fy=345 MPa, ×1.15 cyclic-hardening ×1.1 Ry) exceeds
        # the beam yield moment (fy=248 MPa).
        assert min(col_my) > max(beam_my)
    finally:
        wipe()


@pytest.mark.parametrize(
    "building,governing",
    [(9, ("mat1", "mat2")), (20, ("mat1", "mat2"))],
    ids=["9-story", "20-story"],
)
def test_imk_920_derive_column_axial_ratios(building, governing):
    """The 9/20-story tributary axial ratios are per base-section, interior > corner."""
    from openseespy.opensees import wipe

    wipe()
    try:
        md = sac.load_model(nonlinear=True, building=building, backbone="imk")
        ratios = sac.derive_column_axial_ratios(md, building=building)
        assert set(ratios) == set(governing)  # only the base sections govern
        corner, interior = ratios["mat1"], ratios["mat2"]
        assert 0.0 < corner < interior  # interior (full bay) > corner (half bay)
        assert interior < 0.6  # below the ASCE 41 force-controlled threshold
    finally:
        wipe()


@pytest.mark.parametrize("building", [9, 20], ids=["9-story", "20-story"])
def test_imk_920_builds_end_to_end(building):
    """The 9/20-story IMK frames build a lumped-hinge domain (M1)."""
    import openseespy.opensees as ops
    from openseespy.opensees import wipe

    wipe()
    try:
        b = sac.build_builder(
            nonlinear=True,
            building=building,
            backbone="imk",
            hinge_model="lumped",
            derive_column_axial_ratio=True,
        )
        assert len(ops.getEleTags()) > 0
        assert b._hinge_tags  # IMK hinges were created
    finally:
        wipe()


def test_imk_920_nltha_synthetic_9story():
    """The 9-story IMK frame runs a synthetic transient (smoke)."""
    import numpy as np
    from openseespy.opensees import wipe

    wipe()
    try:
        b = sac.build_builder(
            nonlinear=True,
            building=9,
            backbone="imk",
            hinge_model="lumped",
            derive_column_axial_ratio=True,
        )
        dt, n = 0.005, 100
        t = np.arange(n) * dt
        accel = np.zeros(n)
        accel[:20] = 2.0 * np.sin(2.0 * np.pi * 1.0 * t[:20])
        res = b.run_transient_analysis(accel, dt, direction="X", damping_ratio=0.02)
        assert res["converged_steps"] == n
        assert res["peak_displacement"] > 0.0
        assert np.isfinite(res["peak_displacement"])
    finally:
        wipe()
