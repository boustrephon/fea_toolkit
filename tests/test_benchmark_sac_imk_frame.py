"""A3 (first increment) — SAC 3-story frame with IMK (IMKBilin) hinges.

``enrich_imk`` stamps per-section ``imk_params`` from the member's physical AISC
geometry (columns via Lignos et al. 2019, beams via Lignos–Krawinkler 2011).
These tests check that the 3-story SAC model stamps only the moment-frame
sections (columns + beams, not the elastic leaning column) and that the full
``load_model`` → preprocess → ``hinge_backbone='imk'`` build succeeds.

See ``docs/imk_deterioration_model.md`` (§5, A3).
"""

import pytest
from openseespy.opensees import wipe

from fea_toolkit.benchmarks import sac
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
from fea_toolkit.opensees.preprocessor import preprocess_model


def _split_by_material(md):
    cols, beams, other = [], [], []
    for sname, sec in md.sections.items():
        if sec.material == "SteelCol":
            cols.append(sname)
        elif sec.material == "SteelBeam":
            beams.append(sname)
        else:
            other.append(sname)
    return cols, beams, other


def test_enrich_imk_stamps_only_moment_sections():
    """Columns and beams carry imk_params; the elastic leaning column does not."""
    wipe()
    md = sac.load_model(nonlinear=True, backbone="imk")
    cols, beams, other = _split_by_material(md)
    assert cols and beams and other  # the 3-story model has all three kinds
    for sname in cols + beams:
        params = md.sections[sname].imk_params
        assert params["mc_my"] >= 1.0
        assert params["My_pos"] > 0.0
        assert params["My_neg"] < 0.0
        assert params["theta_p_pos"] > 0.0
        assert params["theta_pc_pos"] > 0.0
    for sname in other:
        assert not getattr(md.sections[sname], "imk_params", None)


def test_enrich_imk_column_capping_exceeds_beam():
    """Column capping ratio (Lignos 2019) > 1; beams keep the 1.05 default."""
    wipe()
    md = sac.load_model(nonlinear=True, backbone="imk")
    cols, beams, _ = _split_by_material(md)
    col_mc = min(md.sections[s].imk_params["mc_my"] for s in cols)
    beam_mc = max(md.sections[s].imk_params["mc_my"] for s in beams)
    assert col_mc > 1.0
    assert beam_mc == pytest.approx(1.05)


def test_enrich_imk_records_theta_u_convention():
    """``enrich_imk`` forwards ``theta_u_model`` and records it on the model."""
    wipe()
    md = sac.load_model(nonlinear=True, backbone="imk")
    assert md.imk_theta_u_model == "plateau"  # default

    sac.enrich_imk(md, building=3, theta_u_model="lignos")
    assert md.imk_theta_u_model == "lignos"
    cols, _, _ = _split_by_material(md)
    assert all(md.sections[s].imk_params["theta_u_pos"] == pytest.approx(0.15) for s in cols)


def test_imk_frame_builds_end_to_end():
    """``load_model(backbone='imk')`` → preprocess → build_domain succeeds."""
    wipe()
    try:
        md = sac.load_model(nonlinear=True, backbone="imk")
        mesh = preprocess_model(md, sac.preprocess_config())
        b = AnalysisBuilder(mesh, sac.builder_config("imk", "lumped"))
        b.build_domain()
        assert b is not None
    finally:
        wipe()


def test_derive_column_axial_ratios():
    """Tributary ``Pg/Pye`` matches the FEMA 355C seismic weight (half frame)."""
    wipe()
    md = sac.load_model(nonlinear=True, backbone="imk")
    ratios = sac.derive_column_axial_ratios(md)
    # Two moment-frame column sections.  The smaller W14x257 exterior section
    # sits on the gravity-bay line and carries a *full* bay (≈0.195); the larger
    # W14x311 interior section carries ≈0.162.  Both sit below the 0.20
    # ASCE-41 force-controlled threshold.
    assert len(ratios) == 2
    assert max(ratios.values()) == pytest.approx(0.19536, rel=1e-3)
    assert min(ratios.values()) == pytest.approx(0.16159, rel=1e-3)
    assert all(0.0 < r < 0.35 for r in ratios.values())


def test_gravity_axial_ratios_matches_tributary():
    """The full elastic gravity solve agrees with the tributary estimate."""
    wipe()
    try:
        md = sac.load_model(nonlinear=True, backbone="imk")
        tributary = sac.derive_column_axial_ratios(md)
        full = sac.gravity_axial_ratios(md)
        assert set(full) == set(tributary)
        for sname, r in tributary.items():
            assert full[sname] == pytest.approx(r, rel=5e-3)
    finally:
        wipe()


def test_enrich_imk_applies_column_axial_ratios():
    """Per-section axial ratios reduce the column yield moment vs pure flexure."""
    wipe()
    md = sac.load_model(nonlinear=True, backbone="imk")  # baseline: ratio 0
    cols, _, _ = _split_by_material(md)
    baseline_my = {s: md.sections[s].imk_params["My_pos"] for s in cols}
    assert md.imk_column_axial_source == "none"

    ratios = sac.derive_column_axial_ratios(md)
    sac.enrich_imk(md, building=3, column_axial_ratios=ratios)
    assert md.imk_column_axial_source == "per-section"
    for s in cols:
        assert md.sections[s].imk_params["My_pos"] < baseline_my[s]
