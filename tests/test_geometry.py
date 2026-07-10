"""Tests for model/geometry.py — constraint edge detection.

These tests require ``openseespy`` and the full builder pipeline.
They are skipped when ``openseespy`` is not installed.
"""

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

import pytest

pytest.importorskip("openseespy")

import openseespy.opensees as ops
from fea_toolkit.opensees.builder import OpenSeesBuilder
from fea_toolkit.model.geometry import find_constraint_edges
from fea_toolkit.model.sap_data import (
    SAPModelData,
    Node,
    Material,
    ShellSection,
    FrameElement,
    AreaElement,
    AreaMesh,
)


# ========================================================================
# Fixtures
# ========================================================================


@pytest.fixture
def materials():
    return {"Concrete": Material("Concrete", "Concrete", E_mod=3e10)}


@pytest.fixture
def sections():
    return {
        "Slab200": ShellSection("Slab200", "Shell", "Concrete", thickness=0.2),
        "Wall200": ShellSection("Wall200", "Shell", "Concrete", thickness=0.2),
        "Brick": ShellSection("Brick", "Shell", "Concrete", thickness=0.1),
    }


# ========================================================================
# Helper
# ========================================================================


def _run_builder(md, config=None):
    """Build a model and return its constraint edges."""
    cfg = {"verbose": False, "create_shells": True}
    if config:
        cfg.update(config)
    b = OpenSeesBuilder(md, cfg)
    try:
        b.build()
        edges = find_constraint_edges(
            b.model.area_elements,
            b.model.area_assignments,
            b.model.nodes,
        )
        return edges
    finally:
        ops.wipe()


# ========================================================================
# Tests
# ========================================================================


class TestFindConstraintEdges:
    """Verify constraint edge detection under various mesh configurations."""

    def test_incompatible_mesh_produces_tear(self, materials, sections):
        """Two adjacent areas with different mesh levels produce a tear."""
        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 6.0, 0.0, 0.0),
            "3": Node("3", 3, 6.0, 6.0, 0.0),
            "4": Node("4", 4, 0.0, 6.0, 0.0),
            "5": Node("5", 5, 12.0, 0.0, 0.0),
            "6": Node("6", 6, 12.0, 6.0, 0.0),
        }
        areas = {
            "1": AreaElement("1", 10, ["1", "2", "3", "4"]),  # left, meshed
            "2": AreaElement("2", 20, ["2", "5", "6", "3"]),  # right, unmeshed
        }
        md = SAPModelData(
            nodes=nodes, restraints={}, materials=materials,
            sections=sections, frame_elements={}, area_elements=areas,
            frame_assignments={}, area_assignments={"1": "Slab200", "2": "Wall200"},
            groups={}, frame_auto_mesh={},
            area_mesh={"1": AreaMesh(auto_mesh=True, max_size=3.0)},
        )
        edges = _run_builder(md)

        # Should detect at least the shared-edge tear (B→C with intermediate)
        assert len(edges) >= 1
        for nids, ta, tb in edges:
            assert len(nids) >= 3
            assert ta != "unknown"
            assert tb != "unknown"

    def test_no_tear_when_meshes_match(self, materials, sections):
        """Two areas sharing an edge with the SAME mesh produce no tear."""
        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 6.0, 0.0, 0.0),
            "3": Node("3", 3, 6.0, 6.0, 0.0),
            "4": Node("4", 4, 0.0, 6.0, 0.0),
            "5": Node("5", 5, 12.0, 0.0, 0.0),
            "6": Node("6", 6, 12.0, 6.0, 0.0),
        }
        areas = {
            "1": AreaElement("1", 10, ["1", "2", "3", "4"]),
            "2": AreaElement("2", 20, ["2", "5", "6", "3"]),
        }
        # Both areas meshed with the SAME max_size → compatible edge
        md = SAPModelData(
            nodes=nodes, restraints={}, materials=materials,
            sections=sections, frame_elements={}, area_elements=areas,
            frame_assignments={}, area_assignments={"1": "Slab200", "2": "Slab200"},
            groups={}, frame_auto_mesh={},
            area_mesh={
                "1": AreaMesh(auto_mesh=True, max_size=3.0),
                "2": AreaMesh(auto_mesh=True, max_size=3.0),
            },
        )
        edges = _run_builder(md)
        assert len(edges) == 0

    def test_no_areas_returns_empty(self, materials, sections):
        """No area elements → no constraints."""
        md = SAPModelData(
            nodes={}, restraints={}, materials=materials,
            sections=sections, frame_elements={}, area_elements={},
            frame_assignments={}, area_assignments={},
            groups={}, frame_auto_mesh={},
        )
        edges = _run_builder(md)
        assert edges == []

    def test_exclude_type_filters_brick_wall(self, materials, sections):
        """Areas with an excluded type do not produce tears."""
        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 6.0, 0.0, 0.0),
            "3": Node("3", 3, 6.0, 6.0, 0.0),
            "4": Node("4", 4, 0.0, 6.0, 0.0),
            "5": Node("5", 5, 12.0, 0.0, 0.0),
            "6": Node("6", 6, 12.0, 6.0, 0.0),
        }
        areas = {
            "1": AreaElement("1", 10, ["1", "2", "3", "4"]),
            "2": AreaElement("2", 20, ["2", "5", "6", "3"]),
        }
        md = SAPModelData(
            nodes=nodes, restraints={}, materials=materials,
            sections=sections, frame_elements={}, area_elements=areas,
            frame_assignments={},
            area_assignments={"1": "Brick", "2": "Brick"},
            groups={}, frame_auto_mesh={},
            area_mesh={"1": AreaMesh(auto_mesh=True, max_size=3.0)},
        )
        # Default exclude_types={'brick wall'} — "Brick" is not excluded
        # because the exclude check uses the string 'brick wall' which
        # doesn't match 'Brick'.  Override exclude_types to filter it.
        b = OpenSeesBuilder(md, {"verbose": False, "create_shells": True})
        try:
            b.build()
            edges = find_constraint_edges(
                b.model.area_elements,
                b.model.area_assignments,
                b.model.nodes,
                exclude_types={"Brick"},
            )
            assert len(edges) == 0
        finally:
            ops.wipe()

    def test_output_contains_type_info(self, materials, sections):
        """Merged tears report element type names."""
        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 6.0, 0.0, 0.0),
            "3": Node("3", 3, 6.0, 6.0, 0.0),
            "4": Node("4", 4, 0.0, 6.0, 0.0),
            "5": Node("5", 5, 12.0, 0.0, 0.0),
            "6": Node("6", 6, 12.0, 6.0, 0.0),
        }
        areas = {
            "1": AreaElement("1", 10, ["1", "2", "3", "4"]),
            "2": AreaElement("2", 20, ["2", "5", "6", "3"]),
        }
        md = SAPModelData(
            nodes=nodes, restraints={}, materials=materials,
            sections=sections, frame_elements={}, area_elements=areas,
            frame_assignments={},
            area_assignments={"1": "Slab200", "2": "Wall200"},
            groups={}, frame_auto_mesh={},
            area_mesh={"1": AreaMesh(auto_mesh=True, max_size=3.0)},
        )
        edges = _run_builder(md)
        assert len(edges) >= 1
        for _, ta, tb in edges:
            # Both types should be valid section names
            assert ta in ("Slab200", "Wall200")
            assert tb in ("Slab200", "Wall200")

    def test_frame_slab_tear(self, materials, sections):
        """A beam along a meshed slab edge produces a tear."""
        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 12.0, 0.0, 0.0),
            "3": Node("3", 3, 12.0, 6.0, 0.0),
            "4": Node("4", 4, 0.0, 6.0, 0.0),
        }
        areas = {
            "1": AreaElement("1", 10, ["1", "2", "3", "4"]),
        }
        # A beam along edge 2→3 (same edge the slab has)
        frames = {
            "B1": FrameElement("B1", 100, node_i="2", node_j="3"),
        }
        md = SAPModelData(
            nodes=nodes, restraints={}, materials=materials,
            sections=sections,
            frame_elements=frames, area_elements=areas,
            frame_assignments={"B1": "Slab200"},
            area_assignments={"1": "Slab200"},
            groups={}, frame_auto_mesh={},
            area_mesh={"1": AreaMesh(auto_mesh=True, max_size=4.0)},
        )
        b = OpenSeesBuilder(md, {"verbose": False, "create_shells": True})
        try:
            b.build()
            edges = find_constraint_edges(
                b.model.area_elements,
                b.model.area_assignments,
                b.model.nodes,
                frame_elements=b.model.frame_elements,
                frame_assignments=b.model.frame_assignments,
            )
            # The slab mesh creates sub-elements with intermediate nodes
            # on the 2→3 edge — the beam has only 2→3 directly.
            # Should detect at least one tear.
            assert len(edges) >= 1
        finally:
            ops.wipe()
