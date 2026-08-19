"""Tests for geometric element classification (beam/column/brace/slab/wall).

Phase 1: the geometric classifier (``Preprocessor._classify_element_type``)
is **always-on** at preprocess time and stored on the ``MeshModel`` as
``frame_element_types`` / ``area_element_types``.  Its thresholds are
configurable — ``column_vertical_ratio`` (column iff ``dz > ratio·dh``),
``classification_span_floor`` (diagonal/brace span floor),
``slab_z_tolerance`` (area horizontality) — with defaults that preserve the
historical behaviour.

The explicit, overridable **design role** (Phase 2) is documented in
``docs/element_classification.md``.
"""

from collections import Counter

from examples.sample_model import make_rc_frame_3d
from fea_toolkit.model.sap_data import (
    AreaElement,
    FrameElement,
    Material,
    Node,
    RectangularSection,
    SAPModelData,
)
from fea_toolkit.opensees.preprocessor import Preprocessor, preprocess_model


def _classify(cfg, elem, nodes, is_area=False):
    return Preprocessor(cfg)._classify_element_type(elem, is_area=is_area, nodes=nodes)


def _frame(i, j, eid="F1"):
    return FrameElement(elem_id=eid, elem_tag=1, node_i=i, node_j=j)


def _nodes():
    """Base node cloud: a (0,0,0), b (0,0,3) above a, c (4,0,3), d (4,0,6)."""
    return {
        "a": Node("a", 1, 0.0, 0.0, 0.0),
        "b": Node("b", 2, 0.0, 0.0, 3.0),
        "c": Node("c", 3, 4.0, 0.0, 3.0),
        "d": Node("d", 4, 4.0, 0.0, 6.0),
    }


def _minimal_model(nodes, frames):
    """SAPModelData with a single steel section, for preprocess-level tests."""
    return SAPModelData(
        nodes=nodes,
        restraints={},
        materials={"M": Material(name="M", type="Steel", E_mod=200e6)},
        sections={
            "S": RectangularSection(name="S", shape="Rectangular", material="M", depth=0.3, bf=0.2)
        },
        frame_elements=frames,
        area_elements={},
        frame_assignments=dict.fromkeys(frames, "S"),
        area_assignments={},
        groups={},
        frame_auto_mesh={},
    )


# ═════════════════════════════════════════════════════════════════════
# Default geometric rules
# ═════════════════════════════════════════════════════════════════════


class TestDefaultRules:
    def test_vertical_is_column(self):
        n = _nodes()
        assert _classify({}, _frame("a", "b"), n) == "column"

    def test_horizontal_is_beam(self):
        n = _nodes()
        assert _classify({}, _frame("b", "c"), n) == "beam"

    def test_diagonal_is_brace(self):
        n = _nodes()
        assert _classify({}, _frame("a", "c"), n) == "brace"

    def test_steep_diagonal_is_column(self):
        # dz=3, dh=4 -> brace (3 > 4*4 is false); dz=3, dh=0.5 -> column.
        n = _nodes()
        assert _classify({}, _frame("a", "c"), n) == "brace"
        steep = {**n, "e": Node("e", 5, 0.5, 0.0, 3.0)}
        assert _classify({}, _frame("a", "e"), steep) == "column"

    def test_missing_nodes_unknown(self):
        n = _nodes()
        assert (
            _classify({}, FrameElement(elem_id="x", elem_tag=1, node_i="a", node_j="zz"), n)
            == "unknown"
        )


# ═════════════════════════════════════════════════════════════════════
# Configurable thresholds (GSA-style verticality criterion)
# ═════════════════════════════════════════════════════════════════════


class TestConfigurableThresholds:
    def test_column_vertical_ratio(self):
        # dz=3, dh=1: default ratio 4.0 -> brace; ratio 2.0 -> column.
        n = {**_nodes(), "e": Node("e", 5, 1.0, 0.0, 3.0)}
        assert _classify({}, _frame("a", "e"), n) == "brace"
        assert _classify({"column_vertical_ratio": 2.0}, _frame("a", "e"), n) == "column"
        assert _classify({"column_vertical_ratio": 10.0}, _frame("a", "e"), n) == "brace"

    def test_classification_span_floor(self):
        # Tiny diagonal dz=dh=0.005: default floor 0.01 -> beam (not brace).
        n = {**_nodes(), "e": Node("e", 5, 0.005, 0.0, 0.005)}
        assert _classify({}, _frame("a", "e"), n) == "beam"
        assert _classify({"classification_span_floor": 0.001}, _frame("a", "e"), n) == "brace"

    def test_slab_z_tolerance(self):
        nodes = {
            "a": Node("a", 1, 0.0, 0.0, 0.0),
            "b": Node("b", 2, 4.0, 0.0, 0.015),
            "c": Node("c", 3, 4.0, 4.0, 0.015),
            "d": Node("d", 4, 0.0, 4.0, 0.0),
        }
        slab = AreaElement("A1", 1, ["a", "b", "c", "d"])
        # Default tolerance 0.02 > 0.015 spread -> slab.
        assert _classify({}, slab, nodes, is_area=True) == "slab"
        # Tighter tolerance -> wall.
        assert _classify({"slab_z_tolerance": 0.01}, slab, nodes, is_area=True) == "wall"

    def test_wall_area(self):
        n = _nodes()
        wall = AreaElement("A1", 1, ["a", "b", "c", "d"])  # Z spread 0..6
        assert _classify({}, wall, n, is_area=True) == "wall"


# ═════════════════════════════════════════════════════════════════════
# Always-on at preprocess (no stiffness_factors required)
# ═════════════════════════════════════════════════════════════════════


class TestAlwaysOnPreprocess:
    def test_frame_types_populated_without_stiffness_factors(self):
        mm = preprocess_model(
            make_rc_frame_3d(),
            {"element_type": "elasticBeamColumn", "verbose": False, "create_shells": False},
        )
        roles = Counter(mm.frame_element_types.values())
        assert roles["column"] == 9
        assert roles["beam"] == 12
        assert len(mm.frame_element_types) == len(mm.frame_elements)

    def test_areas_classified_when_shells_created(self):
        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 4.0, 0.0, 0.0),
            "3": Node("3", 3, 4.0, 4.0, 0.015),
            "4": Node("4", 4, 0.0, 4.0, 0.015),
            "5": Node("5", 5, 0.0, 0.0, 3.0),
            "6": Node("6", 6, 4.0, 0.0, 3.0),
        }
        areas = {
            "SLAB1": AreaElement("SLAB1", 1, ["1", "2", "3", "4"]),  # near-flat
            "WALL1": AreaElement("WALL1", 2, ["1", "2", "6", "5"]),  # vertical plane
        }
        md = _minimal_model(nodes, {})
        md.area_elements = areas
        md.area_assignments = dict.fromkeys(areas, "S")
        mm = preprocess_model(
            md, {"element_type": "elasticBeamColumn", "verbose": False, "create_shells": True}
        )
        roles = Counter(mm.area_element_types.values())
        assert roles["slab"] == 1
        assert roles["wall"] == 1

    def test_split_children_inherit_parent_type(self):
        # Column C1 (1-3) with an intermediate joint at node 2 (beam stub 2-4)
        # splits into two children that inherit the 'column' role.
        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 0.0, 0.0, 1.5),
            "3": Node("3", 3, 0.0, 0.0, 3.0),
            "4": Node("4", 4, 1.0, 0.0, 1.5),
        }
        frames = {
            "C1": FrameElement(elem_id="C1", elem_tag=1, node_i="1", node_j="3"),
            "B1": FrameElement(elem_id="B1", elem_tag=2, node_i="2", node_j="4"),
        }
        mm = preprocess_model(
            _minimal_model(nodes, frames),
            {"element_type": "elasticBeamColumn", "verbose": False, "create_shells": False},
        )
        roles = Counter(mm.frame_element_types.values())
        assert roles["column"] >= 1
        assert roles["beam"] >= 1
        for eid, etype in mm.frame_element_types.items():
            if mm.frame_elements[eid].parent_id == "C1":
                assert etype == "column"
        assert mm.frame_element_types["B1"] == "beam"
