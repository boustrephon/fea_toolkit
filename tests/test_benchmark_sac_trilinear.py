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
