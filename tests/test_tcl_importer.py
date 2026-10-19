"""Tests for :func:`fea_toolkit.io.tcl_importer.parse_tcl_model`.

Covers the SAC-style 2-D elastic plane frame → ``SAPModelData`` import,
including the 2-D → planar-3-D lift, and a closed-form tip-deflection check
through the full Preprocessor → AnalysisBuilder pipeline.
"""

import pytest

from fea_toolkit.io.tcl_importer import parse_tcl_model
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
from fea_toolkit.opensees.preprocessor import preprocess_model

# A two-storey cantilever column, kip-in units (the SAC convention).
SAC_CANTILEVER = """
wipe
model BasicBuilder -ndm 2 -ndf 3
node 1 0.0 0.0
node 2 0.0 120.0
node 3 0.0 240.0
fix 1 1 1 1
mass 2 1.0 1.0 0.0
mass 3 1.0 1.0 0.0
geomTransf Linear 1
element elasticBeamColumn 1 1 2 10.0 29000.0 100.0 1
element elasticBeamColumn 2 2 3 10.0 29000.0 100.0 1
pattern Plain 1 Linear {
    load 3 1.0 0.0 0.0
}
"""

UNITS_KIP_IN = {"F": "kip", "L": "in", "T": "C"}


def _parse():
    return parse_tcl_model(SAC_CANTILEVER, UNITS_KIP_IN)


def test_lifts_2d_nodes_to_planar_3d():
    md = _parse()
    assert md.nodes["1"].z == 0.0 and md.nodes["1"].y == 0.0
    assert md.nodes["2"].z == 120.0 and md.nodes["2"].x == 0.0
    assert md.nodes["3"].z == 240.0


def test_base_fixed_and_other_nodes_out_of_plane_restrained():
    md = _parse()
    assert list(md.restraints["1"].dofs) == [1, 1, 1, 1, 1, 1]
    assert list(md.restraints["2"].dofs) == [0, 1, 0, 1, 0, 1]
    assert list(md.restraints["3"].dofs) == [0, 1, 0, 1, 0, 1]


def test_elements_sections_and_materials_deduplicated():
    md = _parse()
    assert set(md.frame_elements) == {"1", "2"}
    assert len(md.sections) == 1
    assert len(md.materials) == 1
    sec = next(iter(md.sections.values()))
    assert sec.A == 10.0
    assert sec.I33 == 100.0 and sec.I22 == 100.0
    mat = next(iter(md.materials.values()))
    assert mat.E_mod == 29000.0


def test_masses_and_lateral_load_imported():
    md = _parse()
    assert md.added_masses == {"2": 1.0, "3": 1.0}
    assert "MSSSRC1" in md.mass_sources
    assert len(md.joint_loads) == 1
    jl = md.joint_loads[0]
    assert jl.node_id == "3" and jl.fx == 1.0 and jl.fz == 0.0
    assert jl.pattern in md.load_patterns


def test_tip_deflection_matches_closed_form():
    from openseespy.opensees import wipe

    wipe()
    try:
        md = _parse()
        mesh = preprocess_model(md, {"element_type": "elasticBeamColumn"})
        builder = AnalysisBuilder(mesh, {})
        builder.build_domain()
        result = builder.run_static_analysis(pattern_scales={"P1": 1.0})
        disp = result["nodal_displacements"]["3"]
        # PL^3 / (3 E I) with P = 1 kip, L = 240 in, E = 29000 ksi, I = 100 in^4
        expected = 1.0 * 240.0**3 / (3.0 * 29000.0 * 100.0)
        assert disp[0] == pytest.approx(expected, rel=1e-4)
    finally:
        wipe()


def test_added_masses_flow_to_builder():
    from openseespy.opensees import wipe

    wipe()
    try:
        md = _parse()
        mesh = preprocess_model(md, {"element_type": "elasticBeamColumn"})
        builder = AnalysisBuilder(mesh, {})
        builder.build_domain()
        node_mass = builder.compute_seismic_masses()
        assert node_mass["2"] == pytest.approx(1.0)
        assert node_mass["3"] == pytest.approx(1.0)
    finally:
        wipe()


def test_unhandled_model_commands_warn():
    tcl = (
        "model BasicBuilder -ndm 2 -ndf 3\n"
        "node 1 0 0\n"
        "node 2 0 10\n"
        "fix 1 1 1 1\n"
        "element truss 1 1 2 1.0 29000.0\n"
    )
    with pytest.warns(UserWarning, match="elasticBeamColumn"):
        md = parse_tcl_model(tcl)
    assert md.frame_elements == {}
