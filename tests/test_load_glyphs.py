"""Tests for :mod:`fea_toolkit.model.load_glyphs` — applied-load glyph extraction.

Qt-free and OpenSees-free: the extractor resolves a model's load collections
into display glyphs straight from the original load definitions — a rectangular
flag for a uniform distributed load, a trapezoidal flag for a varying one, and
arrows for joint/frame point loads — never the AnalysisBuilder's uniform
decomposition.
"""

import numpy as np

from fea_toolkit.model.load_glyphs import extract_load_glyphs, list_load_sources
from fea_toolkit.model.sap_data import (
    FrameDistributedLoad,
    FrameElement,
    FramePointLoad,
    JointLoad,
    LoadCase,
    LoadCombination,
    LoadCombinationEntry,
    LoadPattern,
    Node,
    SAPModelData,
)


def _model(**loads):
    """A one-frame, two-node model with the given load collections."""
    return SAPModelData(
        nodes={
            "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
            "2": Node(node_id="2", node_tag=2, x=4.0, y=0.0, z=0.0),
        },
        restraints={},
        materials={},
        sections={},
        frame_elements={"F1": FrameElement(elem_id="F1", elem_tag=1, node_i="1", node_j="2")},
        area_elements={},
        frame_assignments={},
        area_assignments={},
        groups={},
        frame_auto_mesh={},
        **loads,
    )


def _uniform(**overrides):
    kwargs = {
        "pattern": "WIND",
        "frame_id": "F1",
        "direction": "Z",
        "load_type": "Force",
        "shape": "Uniform",
        "val_a": 10.0,
        "val_b": 10.0,
        "rdist_a": 0.0,
        "rdist_b": 1.0,
        "dist_a": 0.0,
        "dist_b": 4.0,
    }
    kwargs.update(overrides)
    return FrameDistributedLoad(**kwargs)


# ── Joint (point) loads ───────────────────────────────────────────────────


def test_joint_load_becomes_one_arrow_per_force_component():
    md = _model(joint_loads=[JointLoad(pattern="DEAD", node_id="2", fx=10.0, fz=-20.0)])
    glyphs = [g for g in extract_load_glyphs(md) if g.kind == "point"]
    assert {(g.label, g.magnitude) for g in glyphs} == {("DEAD fx", 10.0), ("DEAD fz", -20.0)}
    for g in glyphs:
        assert np.allclose(g.anchor, [4.0, 0.0, 0.0])


def test_joint_load_zero_components_are_skipped():
    md = _model(joint_loads=[JointLoad(pattern="DEAD", node_id="2", fy=5.0)])
    glyphs = [g for g in extract_load_glyphs(md) if g.kind == "point"]
    assert len(glyphs) == 1
    assert glyphs[0].magnitude == 5.0
    assert np.allclose(glyphs[0].direction, [0.0, 1.0, 0.0])


# ── Frame distributed loads (flags) ───────────────────────────────────────


def test_uniform_line_load_keeps_its_rectangle_shape():
    md = _model(frame_dist_loads=[_uniform()])
    glyphs = [g for g in extract_load_glyphs(md) if g.kind == "line"]
    assert len(glyphs) == 1
    g = glyphs[0]
    assert (g.val_a, g.val_b) == (10.0, 10.0)  # rectangle — not decomposed
    assert (g.rdist_a, g.rdist_b) == (0.0, 1.0)
    assert np.allclose(g.direction, [0.0, 0.0, 1.0])


def test_trapezoidal_line_load_preserves_end_intensities():
    md = _model(
        frame_dist_loads=[
            _uniform(shape="Trapezoidal", val_a=4.0, val_b=12.0, rdist_a=0.2, rdist_b=0.8)
        ]
    )
    g = next(g for g in extract_load_glyphs(md) if g.kind == "line")
    assert (g.val_a, g.val_b) == (4.0, 12.0)  # as-is, before uniform conversion
    assert (g.rdist_a, g.rdist_b) == (0.2, 0.8)


def test_gravity_line_load_points_downward():
    md = _model(frame_dist_loads=[_uniform(direction="Gravity", pattern="DEAD")])
    g = next(g for g in extract_load_glyphs(md) if g.kind == "line")
    assert np.allclose(g.direction, [0.0, 0.0, -1.0])


def test_local_axis_line_load_resolves_from_member_axes():
    md = _model(frame_dist_loads=[_uniform(direction="LocalZ")])
    g = next(g for g in extract_load_glyphs(md) if g.kind == "line")
    # The member lies along global X, so its local-z is a unit vector with
    # zero X component.
    assert abs(g.direction[0]) < 1e-9
    assert np.isclose(np.linalg.norm(g.direction), 1.0)


# ── Frame point loads ─────────────────────────────────────────────────────


def test_frame_point_load_anchors_at_rdist():
    md = _model(
        frame_point_loads=[
            FramePointLoad(
                pattern="DEAD",
                frame_id="F1",
                direction="Gravity",
                load_type="Force",
                value=-50.0,
                rdist=0.5,
                dist=2.0,
            )
        ]
    )
    g = next(g for g in extract_load_glyphs(md) if g.kind == "point")
    assert g.magnitude == -50.0
    assert np.allclose(g.anchor, [2.0, 0.0, 0.0])
    assert np.allclose(g.direction, [0.0, 0.0, -1.0])


# ── Source polymorphism ───────────────────────────────────────────────────


def test_mesh_model_is_accepted_as_a_source():
    from fea_toolkit.model.mesh_model import MeshModel

    mm = MeshModel(
        nodes={
            "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
            "2": Node(node_id="2", node_tag=2, x=4.0, y=0.0, z=0.0),
        },
        frame_elements={"F1": FrameElement(elem_id="F1", elem_tag=1, node_i="1", node_j="2")},
        frame_assignments={},
        area_elements={},
        area_assignments={},
        frame_dist_loads=[_uniform()],
    )
    glyphs = extract_load_glyphs(mm)
    assert any(g.kind == "line" and (g.val_a, g.val_b) == (10.0, 10.0) for g in glyphs)


def test_a_load_on_a_missing_frame_is_skipped():
    md = _model(frame_dist_loads=[_uniform(frame_id="GONE")])
    assert [g for g in extract_load_glyphs(md) if g.kind == "line"] == []


# ── Load-source listing & combination resolution ──────────────────────────


def _static_case(name, *assignments, design_type="DEAD"):
    return LoadCase(
        case_name=name,
        case_type="LinStatic",
        design_type_option="Prog Det",
        design_type=design_type,
        design_action_option="Prog Det",
        design_action="Non-Composite",
        case_data={
            "CASE - STATIC 1 - LOAD ASSIGNMENTS": [
                {"LoadName": pattern, "LoadSF": factor} for pattern, factor in assignments
            ]
        },
    )


def test_list_load_sources_lists_patterns_cases_and_combinations():
    md = _model(
        load_patterns={
            "DEAD": LoadPattern(name="DEAD", pattern_type="DEAD"),
            "WIND": LoadPattern(name="WIND", pattern_type="WIND"),
        },
        load_cases={
            "DEAD": _static_case("DEAD", ("DEAD", 1.0)),
            "ULT": _static_case("ULT", ("DEAD", 1.3), ("WIND", 1.4), design_type="COMBO"),
        },
        load_combinations={
            "COMB1": LoadCombination(
                name="COMB1",
                combo_type="Linear Add",
                entries=[LoadCombinationEntry(name="ULT", factor=1.0, kind="case")],
            )
        },
        joint_loads=[JointLoad(pattern="DEAD", node_id="2", fz=-100.0)],
        frame_dist_loads=[_uniform(pattern="WIND")],
    )
    sources = list_load_sources(md)
    kinds = {(s.kind, s.name) for s in sources}
    assert ("pattern", "WIND") in kinds
    assert ("case", "DEAD") in kinds
    assert ("case", "ULT") in kinds
    assert ("combination", "COMB1") in kinds
    combo = next(s for s in sources if s.kind == "combination")
    assert combo.patterns == {"DEAD": 1.3, "WIND": 1.4}


def test_envelope_combination_is_omitted_from_sources():
    md = _model(
        load_cases={"DEAD": _static_case("DEAD", ("DEAD", 1.0))},
        load_combinations={
            "ENV1": LoadCombination(
                name="ENV1",
                combo_type="Envelope",
                entries=[LoadCombinationEntry(name="DEAD", factor=1.0, kind="case")],
            )
        },
        frame_dist_loads=[_uniform(pattern="DEAD", direction="Gravity")],
    )
    sources = list_load_sources(md)
    assert not any(s.name == "ENV1" for s in sources)


def test_extract_load_glyphs_filters_and_scales_by_pattern_factors():
    md = _model(
        joint_loads=[JointLoad(pattern="DEAD", node_id="2", fz=-100.0)],
        frame_dist_loads=[_uniform(pattern="WIND")],
    )
    glyphs = extract_load_glyphs(md, pattern_factors={"WIND": 1.4})
    assert not any(g.pattern == "DEAD" for g in glyphs)
    line = next(g for g in glyphs if g.kind == "line")
    assert (line.val_a, line.val_b) == (14.0, 14.0)
