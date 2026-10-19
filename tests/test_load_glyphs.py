"""Tests for :mod:`fea_toolkit.model.load_glyphs` — applied-load glyph extraction.

Qt-free and OpenSees-free: the extractor resolves a model's load collections
into display glyphs straight from the original load definitions — a rectangular
flag for a uniform distributed load, a trapezoidal flag for a varying one, and
arrows for joint/frame point loads — never the AnalysisBuilder's uniform
decomposition.
"""

import numpy as np

from fea_toolkit.model.load_glyphs import area_load_values, extract_load_glyphs, list_load_sources
from fea_toolkit.model.load_queries import load_rows
from fea_toolkit.model.sap_data import (
    AreaElement,
    AreaUniformLoad,
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


def test_glyphs_carry_their_owner_element():
    md = _model(
        joint_loads=[JointLoad(pattern="DEAD", node_id="2", fz=-100.0)],
        frame_dist_loads=[_uniform()],
    )
    glyphs = extract_load_glyphs(md)
    line = next(g for g in glyphs if g.kind == "line")
    assert (line.owner_kind, line.owner_id) == ("frame", "F1")
    node_points = [g for g in glyphs if g.kind == "point"]
    assert all((g.owner_kind, g.owner_id) == ("node", "2") for g in node_points)


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


def test_list_load_sources_lists_cases_combinations_and_orphan_patterns():
    md = _model(
        load_patterns={
            "DEAD": LoadPattern(name="DEAD", pattern_type="DEAD"),
            "WIND": LoadPattern(name="WIND", pattern_type="WIND"),
            "SNOW": LoadPattern(name="SNOW", pattern_type="SNOW"),
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
        frame_dist_loads=[_uniform(pattern="WIND"), _uniform(pattern="SNOW")],
    )
    sources = list_load_sources(md)
    kinds = {(s.kind, s.name) for s in sources}
    assert ("case", "DEAD") in kinds
    assert ("case", "ULT") in kinds
    assert ("combination", "COMB1") in kinds
    # A pattern a static case already covers is not listed again — only the
    # orphan SNOW (loads but no case references it) appears on its own.
    assert ("pattern", "SNOW") in kinds
    assert not any(s.kind == "pattern" and s.name in ("DEAD", "WIND") for s in sources)
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


# ── Area uniform loads (shell-face pressures) ──────────────────────────────


def _area_model(**loads):
    """A one-quad area model with the given load collections."""
    return SAPModelData(
        nodes={
            "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
            "2": Node(node_id="2", node_tag=2, x=4.0, y=0.0, z=0.0),
            "3": Node(node_id="3", node_tag=3, x=4.0, y=4.0, z=0.0),
            "4": Node(node_id="4", node_tag=4, x=0.0, y=4.0, z=0.0),
        },
        restraints={},
        materials={},
        sections={},
        frame_elements={},
        area_elements={"A1": AreaElement(area_id="A1", area_tag=1, node_ids=["1", "2", "3", "4"])},
        frame_assignments={},
        area_assignments={},
        groups={},
        frame_auto_mesh={},
        **loads,
    )


def _area_uniform(**overrides):
    kwargs = {"pattern": "SDL", "area_id": "A1", "direction": "Z", "value": 12.5}
    kwargs.update(overrides)
    return AreaUniformLoad(**kwargs)


def test_area_uniform_load_becomes_an_area_glyph():
    md = _area_model(area_uniform_loads=[_area_uniform()])
    glyphs = [g for g in extract_load_glyphs(md) if g.kind == "area"]
    assert len(glyphs) == 1
    g = glyphs[0]
    assert g.vertices.shape == (4, 3)
    assert g.magnitude == 12.5
    assert np.allclose(g.direction, [0.0, 0.0, 1.0])
    assert (g.owner_kind, g.owner_id) == ("area", "A1")


def test_area_gravity_pressure_points_down():
    md = _area_model(area_uniform_loads=[_area_uniform(direction="Gravity", value=-5.0)])
    g = next(g for g in extract_load_glyphs(md) if g.kind == "area")
    assert g.magnitude == -5.0
    assert np.allclose(g.direction, [0.0, 0.0, -1.0])


def test_area_local_directions_resolve_to_face_axes():
    cases = {
        "Local1": [1.0, 0.0, 0.0],
        "Local2": [0.0, 1.0, 0.0],
        "Local3": [0.0, 0.0, 1.0],
        "-Local3": [0.0, 0.0, -1.0],
    }
    for direction, expected in cases.items():
        md = _area_model(area_uniform_loads=[_area_uniform(direction=direction)])
        g = next(g for g in extract_load_glyphs(md) if g.kind == "area")
        assert np.allclose(g.direction, expected), direction


def test_to_frame_area_load_is_not_drawn_on_the_face():
    md = _area_model(area_uniform_loads=[_area_uniform(to_frame=True)])
    assert [g for g in extract_load_glyphs(md) if g.kind == "area"] == []


def test_area_load_resolves_to_leaf_sub_areas():
    md = SAPModelData(
        nodes={
            "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
            "2": Node(node_id="2", node_tag=2, x=4.0, y=0.0, z=0.0),
            "3": Node(node_id="3", node_tag=3, x=4.0, y=4.0, z=0.0),
            "4": Node(node_id="4", node_tag=4, x=0.0, y=4.0, z=0.0),
            "5": Node(node_id="5", node_tag=5, x=4.0, y=2.0, z=0.0),
            "6": Node(node_id="6", node_tag=6, x=0.0, y=2.0, z=0.0),
        },
        restraints={},
        materials={},
        sections={},
        frame_elements={},
        area_elements={
            "A1": AreaElement(
                area_id="A1",
                area_tag=1,
                node_ids=["1", "2", "3", "4"],
                inactive=True,
                child_ids=["A1-0", "A1-1"],
            ),
            "A1-0": AreaElement(
                area_id="A1-0", area_tag=2, node_ids=["1", "2", "5", "6"], parent_id="A1"
            ),
            "A1-1": AreaElement(
                area_id="A1-1", area_tag=3, node_ids=["2", "3", "4", "6"], parent_id="A1"
            ),
        },
        frame_assignments={},
        area_assignments={},
        groups={},
        frame_auto_mesh={},
        area_uniform_loads=[_area_uniform()],
    )
    glyphs = [g for g in extract_load_glyphs(md) if g.kind == "area"]
    assert len(glyphs) == 2  # one per active leaf
    assert all(g.magnitude == 12.5 for g in glyphs)
    # Collapse draws the authored panel instead of fanning to leaves.
    collapsed = [g for g in extract_load_glyphs(md, collapse=True) if g.kind == "area"]
    assert len(collapsed) == 1
    assert collapsed[0].owner_id == "A1"
    assert collapsed[0].vertices.shape == (4, 3)


def test_area_load_values_keys_the_panel_id():
    md = _area_model(area_uniform_loads=[_area_uniform()])
    assert area_load_values(md) == {"A1": 12.5}


def test_area_load_values_filters_and_scales_by_pattern_factors():
    md = _area_model(
        area_uniform_loads=[
            _area_uniform(pattern="DEAD", value=10.0),
            _area_uniform(pattern="LIVE", value=2.0),
        ]
    )
    assert area_load_values(md, pattern_factors={"DEAD": 1.4}) == {"A1": 14.0}


def test_area_load_values_excludes_to_frame_loads():
    md = _area_model(
        area_uniform_loads=[
            _area_uniform(value=10.0),
            _area_uniform(pattern="TOFRAME", to_frame=True, value=7.0),
        ]
    )
    assert area_load_values(md) == {"A1": 10.0}


# ── Inspector queries (load_rows) ──────────────────────────────────────────


def test_load_rows_reports_an_areas_pressure():
    md = _area_model(area_uniform_loads=[_area_uniform()])
    assert ("Load: SDL (Z)", "12.5") in load_rows(md, md.area_elements["A1"])


def test_load_rows_reports_frame_and_joint_loads():
    md = _model(
        joint_loads=[JointLoad(pattern="DEAD", node_id="2", fz=-100.0)],
        frame_dist_loads=[_uniform()],
    )
    assert ("Load: DEAD (fz)", "-100") in load_rows(md, md.nodes["2"])
    assert ("Load: WIND (Z Uniform)", "10 \u2192 10") in load_rows(md, md.frame_elements["F1"])


def test_load_rows_filters_and_scales_by_pattern_factors():
    md = _area_model(area_uniform_loads=[_area_uniform(pattern="SDL", value=10.0)])
    assert ("Load: SDL (Z)", "14") in load_rows(md, md.area_elements["A1"], {"SDL": 1.4})
    assert load_rows(md, md.area_elements["A1"], {"DEAD": 1.0}) == []
