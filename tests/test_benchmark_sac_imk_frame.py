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
