"""Display-only W-section shapes for the Ohtori backbone sections (Part 3).

``enrich_display_sections`` rebuilds each backbone-defined section as an
``ISection`` (or ``BoxSection``) carrying the physical section's flange
dimensions, so the visualiser draws the cross-section.  The analysis
properties (A/I33/I22/J/Z33/Z22 + the backbone) and the panel-zone depth/tw
are preserved unchanged.
"""

import openseespy.opensees as ops
import pytest

from fea_toolkit.benchmarks import sac
from fea_toolkit.model.sap_data import ISection


def test_load_model_sections_are_isections_with_flange_dims():
    """load_model() returns ISection sections with the AISC flange dimensions."""
    md = sac.load_model(nonlinear=True)
    try:
        for sec in md.sections.values():
            assert isinstance(sec, ISection)
            assert sec.bf > 0
            assert sec.tf > 0
            assert sec.depth_disp > 0
        # W14X257 (SEC1): bf = 16.0 in, tf = 1.89 in, d = 16.4 in.
        w14 = md.sections["SEC1"]
        assert w14.bf == pytest.approx(0.4064)
        assert w14.tf == pytest.approx(0.048006)
        # W33X118 beam (SEC4): bf = 11.5 in, tf = 0.74 in.
        w33 = md.sections["SEC4"]
        assert w33.bf == pytest.approx(0.2921)
        assert w33.tf == pytest.approx(0.018796)
    finally:
        ops.wipe()


def test_display_enrichment_preserves_analysis_properties():
    """The display rebuild leaves A/I33/I22/J/Z33/Z22, panel-zone dims and the
    trilinear backbone byte-identical."""
    md = sac.load_model(nonlinear=False)
    sac.enrich_nonlinear(md)
    sac.enrich_trilinear(md)
    before = {
        name: (
            s.A,
            s.I33,
            s.I22,
            s.J,
            s.Z33,
            s.Z22,
            s.ei1,
            s.ei2,
            s.ei3,
            s.d1,
            s.d2,
        )
        for name, s in md.sections.items()
    }
    before_depth_tw = {
        name: (getattr(s, "depth", None), getattr(s, "tw", None)) for name, s in md.sections.items()
    }
    sac.enrich_display_sections(md)
    try:
        for name, s in md.sections.items():
            # Analysis properties are byte-identical.
            assert (
                s.A,
                s.I33,
                s.I22,
                s.J,
                s.Z33,
                s.Z22,
                s.ei1,
                s.ei2,
                s.ei3,
                s.d1,
                s.d2,
            ) == before[name], f"{name}: analysis changed"
            # Panel-zone dims are preserved where they were set, and stay unset
            # where they were not — the display depth/tw go to the separate
            # depth_disp/tw_disp fields instead.
            d_before, tw_before = before_depth_tw[name]
            if d_before is not None:
                assert s.depth == d_before, f"{name}: depth changed"
            else:
                assert not s.depth, f"{name}: display fallback populated depth"
            if tw_before is not None:
                assert s.tw == tw_before, f"{name}: tw changed"
            else:
                assert not s.tw, f"{name}: display fallback populated tw"
            assert s.depth_disp is not None and s.depth_disp > 0
            assert s.tw_disp is not None and s.tw_disp > 0
    finally:
        ops.wipe()


def test_display_enrichment_does_not_change_panel_zones():
    """``enrich_display_sections`` leaves ``MeshModel.panel_zones`` unchanged,
    including at the leaning-column joints where the display fallback used to
    populate ``depth``/``tw``."""
    from fea_toolkit.opensees.preprocessor import preprocess_model

    def _panel_zones(with_display: bool):
        md = sac.load_model(nonlinear=False)
        sac.enrich_nonlinear(md)
        sac.enrich_trilinear(md)
        if with_display:
            sac.enrich_display_sections(md)
        mesh = preprocess_model(md, sac.preprocess_config(panel_zone=True))
        return mesh.panel_zones

    ops.wipe()
    try:
        with_display = _panel_zones(True)
    finally:
        ops.wipe()
    try:
        without_display = _panel_zones(False)
    finally:
        ops.wipe()

    assert with_display, "expected panel zones in the enriched 3-story model"
    assert with_display.keys() == without_display.keys()
    for key, spec in with_display.items():
        assert spec == without_display[key], f"{key}: panel zone changed by display enrichment"
