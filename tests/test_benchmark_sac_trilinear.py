"""Trilinear backbone enrichment + hinge for the SAC 3-story model (A1).

The benchmark's member hysteresis is trilinear (``mat_tbl``: ``EI1/EI2/EI3`` +
break curvatures ``d1/d2``).  ``enrich_trilinear`` stamps that backbone onto
the SAC sections; ``hinge_backbone='trilinear'`` builds it as a ``Hysteretic``
hinge.  See ``docs/sac_nonlinear_benchmark.md``.
"""

import openseespy.opensees as ops
import pytest

from fea_toolkit.benchmarks import sac
from fea_toolkit.benchmarks.sac_member_data import MAT_TBL_3
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
from fea_toolkit.opensees.preprocessor import preprocess_model


def test_enrich_trilinear_stamps_mat_tbl_backbones():
    """The SAC sections carry the exact struct3.m trilinear backbones."""
    md = sac.load_model(nonlinear=True)
    sac.enrich_trilinear(md)
    try:
        cols = {round(s.A, 9): s for s in md.sections.values()}
        beams = {round(s.I33, 9): s for s in md.sections.values()}

        w14x257 = cols[round(0.048759, 9)]
        assert w14x257.ei1 == pytest.approx(MAT_TBL_3[1].ei1)
        assert w14x257.d1 == pytest.approx(MAT_TBL_3[1].d1)
        assert w14x257.d2 == pytest.approx(MAT_TBL_3[1].d2)

        w24x68 = beams[round(7.6145e-4, 9)]
        assert w24x68.ei1 == pytest.approx(MAT_TBL_3[5].ei1)

        # Leaning column (material 6) is the softest EI1 (~28× below the
        # moment-frame columns).
        leaning = min(md.sections.values(), key=lambda s: s.ei1 or 1e99)
        assert leaning.ei1 == pytest.approx(MAT_TBL_3[6].ei1)
    finally:
        ops.wipe()


def test_trilinear_builder_builds():
    """``hinge_backbone='trilinear'`` builds a lumped-hinge domain without error."""
    md = sac.load_model(nonlinear=True)
    sac.enrich_trilinear(md)
    try:
        mesh = preprocess_model(md, sac.preprocess_config(panel_zone=False))
        b = AnalysisBuilder(
            mesh,
            {
                "element_type": "elasticBeamColumn",
                "hinge_model": "lumped",
                "hinge_backbone": "trilinear",
                "verbose": False,
            },
        )
        b.build_domain()
        assert len(ops.getEleTags()) > 0
    finally:
        ops.wipe()


def _first_period(backbone: str) -> float:
    """First modal period of the nonlinear model with the given backbone."""
    from openseespy.opensees import wipe

    wipe()
    try:
        b = sac.build_builder(nonlinear=True, backbone=backbone)
        return float(b.run_modal_analysis(num_modes=1, print_results=False)["periods"][0])
    finally:
        wipe()


def test_load_model_trilinear_wires_backbones():
    """``load_model(backbone="trilinear")`` stamps the backbones via the new wiring."""
    try:
        md = sac.load_model(nonlinear=True, backbone="trilinear")
        cols = {round(s.A, 9): s for s in md.sections.values()}
        w14x257 = cols[round(0.048759, 9)]
        assert w14x257.ei1 == pytest.approx(MAT_TBL_3[1].ei1)
        assert w14x257.d1 == pytest.approx(MAT_TBL_3[1].d1)
    finally:
        ops.wipe()


def test_build_builder_trilinear_matches_m1_period():
    """``backbone="trilinear"`` lands on the M1 period (EI1 ≈ elastic EI)."""
    t1_tri = _first_period("trilinear")
    t1_bi = _first_period("bilinear")
    # EI1 is the benchmark's own elastic rigidity, so the trilinear hinge's
    # elastic stiffness matches the bilinear hinge's — same first period.
    assert t1_tri == pytest.approx(t1_bi, rel=0.02)
    # And it stays on the published elastic anchor (0.99 Hz → 1.01 s).
    assert t1_tri == pytest.approx(1.01, rel=0.05)
