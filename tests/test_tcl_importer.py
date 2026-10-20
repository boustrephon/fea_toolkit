"""Tests for :func:`fea_toolkit.io.tcl_importer.parse_tcl_model`.

Covers the SAC-style 2-D elastic plane frame → ``SAPModelData`` import,
including the 2-D → planar-3-D lift, and a closed-form tip-deflection check
through the full Preprocessor → AnalysisBuilder pipeline.
"""

import pytest

from fea_toolkit.io.tcl_importer import (
    _has_unescaped_closing_brace,
    _strip_tcl_comment,
    parse_tcl_model,
)
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


def test_compact_inline_pattern_body():
    """A brace body on the same line (delimiters attached) still parses."""
    tcl = (
        "model BasicBuilder -ndm 2 -ndf 3\n"
        "node 1 0 0\n"
        "node 2 0 10\n"
        "fix 1 1 1 1\n"
        "element elasticBeamColumn 1 1 2 10.0 29000.0 100.0 1\n"
        "pattern Plain 1 Linear {load 2 1.0 0.0 0.0}\n"
    )
    md = parse_tcl_model(tcl)
    assert len(md.joint_loads) == 1
    jl = md.joint_loads[0]
    assert jl.node_id == "2" and jl.fx == 1.0 and jl.fz == 0.0


def test_closing_brace_attached_to_block_command():
    """A closing brace attached to the last block command still parses."""
    tcl = (
        "model BasicBuilder -ndm 2 -ndf 3\n"
        "node 1 0 0\n"
        "node 2 0 10\n"
        "fix 1 1 1 1\n"
        "element elasticBeamColumn 1 1 2 10.0 29000.0 100.0 1\n"
        "pattern Plain 1 Linear {\n"
        "    load 2 1.0 0.0 0.0}\n"
    )
    md = parse_tcl_model(tcl)
    assert len(md.joint_loads) == 1
    assert md.joint_loads[0].node_id == "2"


@pytest.mark.parametrize(
    ("tcl", "expected"),
    [
        # Single beam: length 120 x mass/length 0.1 = 12.0, split 6.0 per node.
        (
            (
                "model BasicBuilder -ndm 2 -ndf 3\n"
                "node 1 0 0\n"
                "node 2 0 120\n"
                "fix 1 1 1 1\n"
                "element elasticBeamColumn 1 1 2 10.0 29000.0 100.0 1 -mass 0.1\n"
            ),
            {"1": 6.0, "2": 6.0},
        ),
        # Element mass adds to an explicit node mass on the shared end node.
        (
            (
                "model BasicBuilder -ndm 2 -ndf 3\n"
                "node 1 0 0\n"
                "node 2 0 120\n"
                "fix 1 1 1 1\n"
                "mass 2 2.0 2.0 0.0\n"
                "element elasticBeamColumn 1 1 2 10.0 29000.0 100.0 1 -mass 0.1\n"
            ),
            {"1": 6.0, "2": 8.0},
        ),
        # A node shared by two beams accumulates half of each beam's mass.
        (
            (
                "model BasicBuilder -ndm 2 -ndf 3\n"
                "node 1 0 0\n"
                "node 2 0 120\n"
                "node 3 0 240\n"
                "fix 1 1 1 1\n"
                "element elasticBeamColumn 1 1 2 10.0 29000.0 100.0 1 -mass 0.1\n"
                "element elasticBeamColumn 2 2 3 10.0 29000.0 100.0 1 -mass 0.1\n"
            ),
            {"1": 6.0, "2": 12.0, "3": 6.0},
        ),
    ],
    ids=["single-beam", "combined-with-node-mass", "shared-node-two-beams"],
)
def test_element_mass_accumulated_to_nodes(tcl, expected):
    """Distributed ``-mass`` is lumped (half per end node), never dropped."""
    md = parse_tcl_model(tcl)
    assert md.added_masses == pytest.approx(expected)


@pytest.mark.parametrize(
    ("line", "closes"),
    [
        ("    load 2 1.0 0.0 0.0}", True),
        ("    load 2 1.0 0.0 0.0\\}", False),
    ],
    ids=["unescaped", "escaped"],
)
def test_has_unescaped_closing_brace(line, closes):
    """A backslash-escaped closing brace does not terminate the pattern block."""
    assert _has_unescaped_closing_brace(line) == closes


def test_added_mass_applied_to_all_translational_dofs():
    """The lifted scalar mass is applied to all three translational DOFs."""
    import openseespy.opensees as ops
    from openseespy.opensees import wipe

    wipe()
    try:
        md = _parse()
        mesh = preprocess_model(md, {"element_type": "elasticBeamColumn"})
        builder = AnalysisBuilder(mesh, {})
        builder.build_domain()
        builder.compute_seismic_masses()
        # Node 2 carries an added mass of 1.0 → applied to UX, UY, UZ (m, m, m).
        assert list(ops.nodeMass(2)) == pytest.approx([1.0, 1.0, 1.0, 0.0, 0.0, 0.0])
    finally:
        wipe()


def test_negative_element_mass_rejected():
    """A negative ``-mass`` is rejected (warned + dropped), not accumulated."""
    tcl = (
        "model BasicBuilder -ndm 2 -ndf 3\n"
        "node 1 0 0\n"
        "node 2 0 120\n"
        "fix 1 1 1 1\n"
        "element elasticBeamColumn 1 1 2 10.0 29000.0 100.0 1 -mass -0.1\n"
    )
    with pytest.warns(UserWarning, match="negative or non-finite '-mass'"):
        md = parse_tcl_model(tcl)
    assert md.added_masses == {}


def test_element_mass_with_missing_node_warns():
    """A beam whose end node is missing warns and drops its ``-mass``."""
    tcl = (
        "model BasicBuilder -ndm 2 -ndf 3\n"
        "node 1 0 0\n"
        "fix 1 1 1 1\n"
        "element elasticBeamColumn 1 1 99 10.0 29000.0 100.0 1 -mass 0.1\n"
    )
    with pytest.warns(UserWarning, match="missing node"):
        md = parse_tcl_model(tcl)
    assert md.added_masses == {}


def test_comment_brace_does_not_close_block():
    """A ``;# }`` trailing comment inside a block does not terminate it."""
    tcl = (
        "model BasicBuilder -ndm 2 -ndf 3\n"
        "node 1 0 0\n"
        "node 2 0 10\n"
        "fix 1 1 1 1\n"
        "element elasticBeamColumn 1 1 2 10.0 29000.0 100.0 1\n"
        "pattern Plain 1 Linear {\n"
        "    load 2 1.0 0.0 0.0 ;# }\n"
        "    load 1 0.5 0.0 0.0\n"
        "}\n"
    )
    md = parse_tcl_model(tcl)
    assert len(md.joint_loads) == 2
    assert md.joint_loads[0].node_id == "2"
    assert md.joint_loads[1].node_id == "1"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("set x 1 # comment", "set x 1"),
        ("set x {1 #2}", "set x {1 #2}"),
        ('set x "a # b" # comment', 'set x "a # b"'),
        ("load 1 2 ;# comment", "load 1 2"),
    ],
    ids=["plain", "brace", "quote", "semicolon"],
)
def test_strip_tcl_comment_ignores_quotes_and_braces(line, expected):
    """``#`` is only a comment outside double-quoted strings and brace groups."""
    assert _strip_tcl_comment(line) == expected


@pytest.mark.parametrize("bad_mass", ["nan", "inf", "-inf"])
def test_non_finite_element_mass_rejected(bad_mass):
    """A non-finite ``-mass`` is rejected (warned + dropped), not accumulated."""
    tcl = (
        "model BasicBuilder -ndm 2 -ndf 3\n"
        "node 1 0 0\n"
        "node 2 0 120\n"
        "fix 1 1 1 1\n"
        f"element elasticBeamColumn 1 1 2 10.0 29000.0 100.0 1 -mass {bad_mass}\n"
    )
    with pytest.warns(UserWarning, match="non-finite '-mass'"):
        md = parse_tcl_model(tcl)
    assert md.added_masses == {}
