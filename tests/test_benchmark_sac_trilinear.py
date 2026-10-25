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


def test_beam_with_hinges_builds_and_matches_elastic_period():
    """``hinge_model='beam_with_hinges'`` keeps the exact elastic stiffness.

    The benchmark's own concentrated-plasticity element (an elastic interior
    plus M-φ hinge sections) has no "nearly rigid" lumped spring, so its first
    period matches the elastic anchor — the lumped hinge softens it (~5 %).
    """
    from openseespy.opensees import wipe

    wipe()
    try:
        elastic = sac.build_builder(nonlinear=False)
        t1_elastic = float(
            elastic.run_modal_analysis(num_modes=1, print_results=False)["periods"][0]
        )
    finally:
        wipe()

    wipe()
    try:
        b = sac.build_builder(nonlinear=True, backbone="trilinear", hinge_model="beam_with_hinges")
        t1_bwh = float(b.run_modal_analysis(num_modes=1, print_results=False)["periods"][0])
        # 27 members → 54 recorded hinge ends; end sections 1 (i) and 6 (j).
        assert len(b._hinge_tags) == 54
        assert b._hinge_sections[:2] == [1, 6]
        assert t1_bwh == pytest.approx(t1_elastic, rel=0.02)
    finally:
        wipe()


def test_beam_with_hinges_nltha_synthetic():
    """``beam_with_hinges`` converges under a synthetic pulse and yields."""
    import math

    import numpy as np
    from openseespy.opensees import wipe

    dt, n = 0.005, 300
    t = np.arange(n) * dt
    accel = np.zeros(n)
    accel[:40] = 30.0 * np.sin(2.0 * math.pi * 1.0 * t[:40])  # strong — forces yield

    wipe()
    try:
        b = sac.build_builder(nonlinear=True, backbone="trilinear", hinge_model="beam_with_hinges")
        res = b.run_transient_analysis(accel, dt, direction="X", damping_ratio=0.02)
        assert res["converged_steps"] > 0
        assert np.isfinite(sac.peak_x(res))
        assert res["num_yielded_hinges"] > 0
        assert res["peak_plastic_rotation"] > 0.0
    finally:
        wipe()
