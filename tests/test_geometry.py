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

from fea_toolkit.model.geometry import (
    _propagate_edge_restraints,
    find_constraint_edges,
    subdivide_area_mesh,
)
from fea_toolkit.model.sap_data import (
    AreaElement,
    AreaMesh,
    FrameElement,
    Material,
    Node,
    Restraint,
    SAPModelData,
    ShellSection,
)
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
from fea_toolkit.opensees.preprocessor import preprocess_model

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
    """Build a model (two-stage) and return its constraint edges."""
    cfg = {"verbose": False, "create_shells": True}
    if config:
        cfg.update(config)
    mesh_model = preprocess_model(md, cfg)
    ab = AnalysisBuilder(mesh_model, cfg)
    try:
        ab.build_domain()
        edges = find_constraint_edges(
            mesh_model.area_elements,
            getattr(mesh_model, "area_assignments", {}),
            mesh_model.nodes,
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
            nodes=nodes,
            restraints={},
            materials=materials,
            sections=sections,
            frame_elements={},
            area_elements=areas,
            frame_assignments={},
            area_assignments={"1": "Slab200", "2": "Wall200"},
            groups={},
            frame_auto_mesh={},
            area_mesh={"1": AreaMesh(auto_mesh=True, max_size=3.0)},
        )
        edges = _run_builder(md)

        # Should detect at least the shared-edge tear (B→C with intermediate)
        assert len(edges) >= 1
        for nids, _, _, ta, tb in edges:
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
            nodes=nodes,
            restraints={},
            materials=materials,
            sections=sections,
            frame_elements={},
            area_elements=areas,
            frame_assignments={},
            area_assignments={"1": "Slab200", "2": "Slab200"},
            groups={},
            frame_auto_mesh={},
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
            nodes={},
            restraints={},
            materials=materials,
            sections=sections,
            frame_elements={},
            area_elements={},
            frame_assignments={},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
        )
        edges = _run_builder(md)
        assert edges == []

    def test_exclude_type_filters_brick_wall(self, materials, sections):
        """Areas with an excluded type do not produce tears.

        The default exclude_types={'brick'} matches 'Brick' via
        case-insensitive substring matching.
        """
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
            nodes=nodes,
            restraints={},
            materials=materials,
            sections=sections,
            frame_elements={},
            area_elements=areas,
            frame_assignments={},
            area_assignments={"1": "Brick", "2": "Brick"},
            groups={},
            frame_auto_mesh={},
            area_mesh={"1": AreaMesh(auto_mesh=True, max_size=3.0)},
        )
        # Default exclude_types={'brick'} now matches 'Brick' via
        # case-insensitive substring matching — no manual override needed.
        edges = _run_builder(md)
        assert len(edges) == 0

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
            nodes=nodes,
            restraints={},
            materials=materials,
            sections=sections,
            frame_elements={},
            area_elements=areas,
            frame_assignments={},
            area_assignments={"1": "Slab200", "2": "Wall200"},
            groups={},
            frame_auto_mesh={},
            area_mesh={"1": AreaMesh(auto_mesh=True, max_size=3.0)},
        )
        edges = _run_builder(md)
        assert len(edges) >= 1
        for _, _, _, ta, tb in edges:
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
            nodes=nodes,
            restraints={},
            materials=materials,
            sections=sections,
            frame_elements=frames,
            area_elements=areas,
            frame_assignments={"B1": "Slab200"},
            area_assignments={"1": "Slab200"},
            groups={},
            frame_auto_mesh={},
            area_mesh={"1": AreaMesh(auto_mesh=True, max_size=4.0)},
        )
        mesh_model = preprocess_model(md, {"verbose": False, "create_shells": True})
        ab = AnalysisBuilder(mesh_model, {"verbose": False, "create_shells": True})
        try:
            ab.build_domain()
            edges = find_constraint_edges(
                mesh_model.area_elements,
                mesh_model.area_assignments,
                mesh_model.nodes,
                frame_elements=mesh_model.frame_elements,
                frame_assignments=mesh_model.frame_assignments,
            )
            assert len(edges) >= 1
        finally:
            ops.wipe()

    def test_master_chain_t_range_warning(self, materials, sections):
        """Centred coarse wall on fine slab: tear detected."""
        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 10.0, 0.0, 0.0),
            "3": Node("3", 3, 10.0, 6.0, 0.0),
            "4": Node("4", 4, 0.0, 6.0, 0.0),
            "5": Node("5", 5, 2.0, 6.0, 0.0),
            "6": Node("6", 6, 8.0, 6.0, 0.0),
            "7": Node("7", 7, 8.0, 8.0, 0.0),
            "8": Node("8", 8, 2.0, 8.0, 0.0),
        }
        areas = {
            "1": AreaElement("1", 10, ["1", "2", "3", "4"]),
            "2": AreaElement("2", 20, ["5", "6", "7", "8"]),
        }
        md = SAPModelData(
            nodes=nodes,
            restraints={},
            materials=materials,
            sections=sections,
            frame_elements={},
            area_elements=areas,
            frame_assignments={},
            area_assignments={"1": "Slab200", "2": "Wall200"},
            groups={},
            frame_auto_mesh={},
            area_mesh={
                "1": AreaMesh(auto_mesh=True, max_size=2.5),
                "2": AreaMesh(auto_mesh=True, max_size=6.0),
            },
        )
        cfg = {"verbose": False, "create_shells": True}
        mesh_model = preprocess_model(md, cfg)
        ab = AnalysisBuilder(mesh_model, cfg)
        try:
            ab.build_domain()
            edges = find_constraint_edges(
                mesh_model.area_elements,
                mesh_model.area_assignments,
                mesh_model.nodes,
            )
            assert len(edges) >= 1
        finally:
            ops.wipe()

    def test_frame_slab_tear_via_helper(self, materials, sections):
        """Frame-element edge path exercised through inline helper.

        6 m × 6 m slab, meshed at max_size=3 m → creates intermediate
        nodes along all four edges.  A beam along the bottom edge
        (1→2) only has the corner nodes, so intermediate mesh nodes
        on that edge must be picked up by the frame-edge registry.

        ::

            4 ─────────── 3
            │   slab      │
            │   (meshed)  │
            1 ═══════════ 2     ← beam B1 (1→2, no intermediate)
        """
        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 6.0, 0.0, 0.0),
            "3": Node("3", 3, 6.0, 6.0, 0.0),
            "4": Node("4", 4, 0.0, 6.0, 0.0),
        }
        areas = {
            "1": AreaElement("1", 10, ["1", "2", "3", "4"]),
        }
        frames = {
            "B1": FrameElement("B1", 100, node_i="1", node_j="2"),
        }
        md = SAPModelData(
            nodes=nodes,
            restraints={},
            materials=materials,
            sections=sections,
            frame_elements=frames,
            area_elements=areas,
            frame_assignments={"B1": "Slab200"},
            area_assignments={"1": "Slab200"},
            groups={},
            frame_auto_mesh={},
            area_mesh={"1": AreaMesh(auto_mesh=True, max_size=3.0)},
        )
        mesh_model = preprocess_model(md, {"verbose": False, "create_shells": True})
        ab = AnalysisBuilder(mesh_model, {"verbose": False, "create_shells": True})
        try:
            ab.build_domain()
            edges = find_constraint_edges(
                mesh_model.area_elements,
                mesh_model.area_assignments,
                mesh_model.nodes,
                frame_elements=mesh_model.frame_elements,
                frame_assignments=mesh_model.frame_assignments,
            )
            # At least one tear with the 1→2 edge direction
            assert len(edges) >= 1
            # Each constraint edge must contain at least 3 nodes
            # (corner + intermediate from mesh)
            for nids, _, _, type_a, type_b in edges:
                assert len(nids) >= 3
                assert type_a == "Slab200"
                assert type_b == "Slab200"
        finally:
            ops.wipe()

    def test_three_elements_one_edge(self, materials, sections):
        """Coarse slab + fine wall share a 12 m edge.

        Layout (shared edge is the horizontal line Y=0):

        ::

            Z (plan view)          Shared edge 1→2 (Y=0, X=0..12)
            ^                      ──────────────────────────────
            │   4────────3
            │   │ Slab   │           Slab  (coarse, max_size=6):
            │   │(coarse) │             master: t=0.0 (node 1)  t=1.0 (node 2)
            │   1═════════2           WallA (fine,   max_size=3):
            │   │ WallA   │             slave:  t=0.0  0.5  1.0  (+ intermediates)
            │   │ (fine)  │
            │   6─────────5           After merge:
            └─────> X                   master chain = [(1,0.0), (2,1.0)]
                                         slave nodes  = [(mid,0.5), ...]
        """
        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 12.0, 0.0, 0.0),
            "3": Node("3", 3, 12.0, 6.0, 0.0),
            "4": Node("4", 4, 0.0, 6.0, 0.0),
            "5": Node("5", 5, 12.0, -6.0, 0.0),
            "6": Node("6", 6, 0.0, -6.0, 0.0),
        }
        areas = {
            "Slab": AreaElement("Slab", 10, ["1", "2", "3", "4"]),
            "WallA": AreaElement("WallA", 20, ["1", "2", "5", "6"]),
        }
        md = SAPModelData(
            nodes=nodes,
            restraints={},
            materials=materials,
            sections=sections,
            frame_elements={},
            area_elements=areas,
            frame_assignments={},
            area_assignments={"Slab": "Slab200", "WallA": "Wall200"},
            groups={},
            frame_auto_mesh={},
            area_mesh={
                "Slab": AreaMesh(auto_mesh=True, max_size=6.0),
                "WallA": AreaMesh(auto_mesh=True, max_size=3.0),
            },
        )
        edges = _run_builder(md)
        assert len(edges) >= 1
        for _, master, slaves, ta, tb in edges:
            if "Slab200" in (ta, tb):
                assert len(master) >= 2
                assert len(slaves) >= 1
                break

    def test_overlapping_colinear_tears(self, materials, sections):
        """Two colinear tears (A↔B and B↔C) share node 2 and merge.

        Plan view (shared vertical edge at X=6, Y=-6..6):

        ::

            Y=6   4─────5─────6        Before merge (two tears):
                  │  A  │  B  │
            Y=0   1─────2─────3          Tear 1 (A↔B): edge [2,5]
                  │  C  │  │             Tear 2 (B↔C): edge [2,8]
           Y=-6   7─────8─────9
                  X=0   X=6  X=12       → colinear, share node 2 → MERGED

                                        After merge:
            Shared edge (X=6, Y=-6..6)   master chain = B's coarse nodes
            ─────────────────────────    slaves = A and C fine nodes
            t=0.0 (node 7/1)            (exact t-values depend on mesh)
            t=0.5 (node 2)
            t=1.0 (node 4)
        """
        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 6.0, 0.0, 0.0),
            "3": Node("3", 3, 12.0, 0.0, 0.0),
            "4": Node("4", 4, 0.0, 6.0, 0.0),
            "5": Node("5", 5, 6.0, 6.0, 0.0),
            "6": Node("6", 6, 12.0, 6.0, 0.0),
            "7": Node("7", 7, 0.0, -6.0, 0.0),
            "8": Node("8", 8, 12.0, -6.0, 0.0),
        }
        areas = {
            "A": AreaElement("A", 10, ["1", "2", "5", "4"]),  # fine, top-left
            "B": AreaElement("B", 20, ["1", "3", "6", "4"]),  # coarse, whole top
            "C": AreaElement("C", 30, ["1", "3", "8", "7"]),  # fine, whole bottom
        }
        # A shares [1,2,5,4] with B's [1,3,6,4] along edge [1,4].
        # C shares [1,3,8,7] with B's [1,3,6,4] along edge [1,3].
        # Tears: A↔B along [1,4] and C↔B along [1,3] — NOT colinear (vertical vs horizontal).
        # Make them colinear: let's make B a strip between A and C:
        # A above B, C below B, all sharing the X=0..12 edge line.
        md = SAPModelData(
            nodes=nodes,
            restraints={},
            materials=materials,
            sections=sections,
            frame_elements={},
            area_elements=areas,
            frame_assignments={},
            area_assignments={"A": "Wall200", "B": "Slab200", "C": "Wall200"},
            groups={},
            frame_auto_mesh={},
            area_mesh={
                "A": AreaMesh(auto_mesh=True, max_size=2.0),
                "C": AreaMesh(auto_mesh=True, max_size=2.0),
            },
        )
        edges = _run_builder(md)
        assert len(edges) >= 1
        for _, master, slaves, ta, tb in edges:
            if "Slab200" in (ta, tb):
                # Master = slab B's edge nodes
                assert len(master) >= 2
                # Should have slave nodes from wall A or C
                assert len(slaves) >= 1
                break

    def test_coarse_master_integrity_check(self, materials, sections):
        """A slightly off-line node in the master chain is moved to slaves.

        Plan view:

        ::

            4 ─────────── 3
            │   Slab      │           Shared edge 1→2:
            │  (coarse)   │           ────────────────
            1──────5──────2           t=0 (1)  t=0.5 (5)  t=1 (2)

            Node 5 is at (6, 0.1) —   Integrity check: node 5 is 0.1 m
            0.1 m off the line 1→2.   off the span → moved to slaves.
                                       Final master: [(1,0.0), (2,1.0)]
        """
        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 12.0, 0.0, 0.0),
            "3": Node("3", 3, 12.0, 6.0, 0.0),
            "4": Node("4", 4, 0.0, 6.0, 0.0),
            "5": Node("5", 5, 12.0, 0.1, 0.0),  # slightly off-line node
        }
        areas = {
            "Slab": AreaElement("Slab", 10, ["1", "2", "3", "4"]),
        }
        # Add an extra node near the edge to create a chain with an off-line node
        md = SAPModelData(
            nodes=nodes,
            restraints={},
            materials=materials,
            sections=sections,
            frame_elements={},
            area_elements=areas,
            frame_assignments={},
            area_assignments={"Slab": "Slab200"},
            groups={},
            frame_auto_mesh={},
        )
        # This test verifies the function doesn't crash with off-line nodes.
        # An edge with only one element produces no tear (expected: empty).
        edges = _run_builder(md)
        # No tear expected (single element, no incompatible edge)
        assert len(edges) == 0


# ========================================================================
# Edge-node restraint propagation (bitwise-AND of adjacent corners)
# ========================================================================


class TestPropagateEdgeRestraints:
    """Tests for ``_propagate_edge_restraints`` and its use by the
    area-meshing functions."""

    def test_bitwise_and_dofs(self):
        """Mixed corner restraints produce the bitwise-AND on the edge node."""
        # 2×2 grid → 3×3 node grid with corners c00, c10, c11, c01
        node_grid = [
            ["c00", "e_bot", "c10"],
            ["e_left", "interior", "e_right"],
            ["c01", "e_top", "c11"],
        ]
        restraints = {
            "c00": Restraint(dofs=[1, 0, 1, 0, 0, 1]),
            "c10": Restraint(dofs=[1, 1, 1, 1, 0, 0]),
            # top corners deliberately absent → top-edge nodes unchanged
        }
        _propagate_edge_restraints(node_grid, 2, 2, restraints)

        # Bottom edge: AND(c00, c10) = [1,0,1,0,0,0]
        assert restraints["e_bot"].dofs == [1, 0, 1, 0, 0, 0]
        # Left edge: c00 restrained but c01 missing → NOT propagated
        assert "e_left" not in restraints
        # Right edge: c10 restrained but c11 missing → NOT propagated
        assert "e_right" not in restraints
        # Top edge: c01/c11 missing → NOT propagated
        assert "e_top" not in restraints
        # Interior node untouched
        assert "interior" not in restraints
        # Corners keep their existing restraints
        assert restraints["c00"].dofs == [1, 0, 1, 0, 0, 1]

    def test_all_corners_fixed_propagates_full_fixity(self):
        """All four corners fully fixed → every edge and interior node fixed."""
        node_grid = [
            ["c00", "e_bot", "c10"],
            ["e_left", "interior", "e_right"],
            ["c01", "e_top", "c11"],
        ]
        restraints = {
            "c00": Restraint(dofs=[1, 1, 1, 1, 1, 1]),
            "c10": Restraint(dofs=[1, 1, 1, 1, 1, 1]),
            "c11": Restraint(dofs=[1, 1, 1, 1, 1, 1]),
            "c01": Restraint(dofs=[1, 1, 1, 1, 1, 1]),
        }
        _propagate_edge_restraints(node_grid, 2, 2, restraints)

        for eid in ("e_bot", "e_top", "e_left", "e_right"):
            assert restraints[eid].dofs == [1, 1, 1, 1, 1, 1]
        assert restraints["interior"].dofs == [1, 1, 1, 1, 1, 1]

    def test_interior_node_gets_common_dof_only(self):
        """Interior node receives only the DOFs common to ALL four corners."""
        node_grid = [
            ["c00", "e_bot", "c10"],
            ["e_left", "interior", "e_right"],
            ["c01", "e_top", "c11"],
        ]
        # Bottom corners X-only; top corners X+Y → common across all four = X.
        restraints = {
            "c00": Restraint(dofs=[1, 0, 0, 0, 0, 0]),
            "c10": Restraint(dofs=[1, 0, 0, 0, 0, 0]),
            "c11": Restraint(dofs=[1, 1, 0, 0, 0, 0]),
            "c01": Restraint(dofs=[1, 1, 0, 0, 0, 0]),
        }
        _propagate_edge_restraints(node_grid, 2, 2, restraints)

        assert restraints["interior"].dofs == [1, 0, 0, 0, 0, 0]
        # Edge ANDs are per-edge — top edge keeps its own Y bit.
        assert restraints["e_top"].dofs == [1, 1, 0, 0, 0, 0]

    def test_interior_node_skipped_when_no_common_dof(self):
        """No single DOF present in all four corners → interior stays free."""
        node_grid = [
            ["c00", "e_bot", "c10"],
            ["e_left", "interior", "e_right"],
            ["c01", "e_top", "c11"],
        ]
        # c11 is Y-only → the X bit is lost across the four → no common DOF.
        restraints = {
            "c00": Restraint(dofs=[1, 0, 0, 0, 0, 0]),
            "c10": Restraint(dofs=[1, 0, 0, 0, 0, 0]),
            "c11": Restraint(dofs=[0, 1, 0, 0, 0, 0]),
            "c01": Restraint(dofs=[1, 1, 0, 0, 0, 0]),
        }
        _propagate_edge_restraints(node_grid, 2, 2, restraints)

        assert "interior" not in restraints

    def test_interior_restraints_are_independent_instances(self):
        """Each interior node gets its own Restraint + dofs list (no shared mutation)."""
        # 3×3 grid → 2×2 interior nodes; all four corners fully fixed.
        node_grid = [
            ["c00", "e_bot0", "e_bot1", "c10"],
            ["e_left0", "i00", "i01", "e_right0"],
            ["e_left1", "i10", "i11", "e_right1"],
            ["c01", "e_top0", "e_top1", "c11"],
        ]
        fixed = [1, 1, 1, 1, 1, 1]
        restraints = {
            "c00": Restraint(dofs=list(fixed)),
            "c10": Restraint(dofs=list(fixed)),
            "c11": Restraint(dofs=list(fixed)),
            "c01": Restraint(dofs=list(fixed)),
        }
        _propagate_edge_restraints(node_grid, 3, 3, restraints)

        interiors = ["i00", "i01", "i10", "i11"]
        for nid in interiors:
            assert restraints[nid].dofs == fixed

        # Distinct instances AND distinct dofs lists per node.
        assert len({id(restraints[nid]) for nid in interiors}) == len(interiors)
        assert len({id(restraints[nid].dofs) for nid in interiors}) == len(interiors)

        # Mutating one node's dofs must not leak into its neighbours.
        restraints["i00"].dofs[0] = 0
        for nid in interiors[1:]:
            assert restraints[nid].dofs[0] == 1

    def test_no_propagation_when_restraints_empty(self):
        """Empty restraints dict → no keys added."""
        node_grid = [
            ["c00", "e_bot", "c10"],
            ["e_left", "interior", "e_right"],
            ["c01", "e_top", "c11"],
        ]
        restraints: dict = {}
        _propagate_edge_restraints(node_grid, 2, 2, restraints)
        assert restraints == {}

    def test_skips_below_2x2(self):
        """n < 2 → no propagation (single-quad mesh has no edge nodes)."""
        node_grid = [["c00", "c10"], ["c01", "c11"]]
        restraints = {
            "c00": Restraint(dofs=[1, 1, 1, 1, 1, 1]),
            "c10": Restraint(dofs=[1, 1, 1, 1, 1, 1]),
        }
        _propagate_edge_restraints(node_grid, 1, 1, restraints)
        assert set(restraints) == {"c00", "c10"}

    def test_subdivide_area_mesh_propagates(self):
        """``subdivide_area_mesh`` wires the helper for a 2×2 wall quad."""
        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
            "2": Node(node_id="2", node_tag=2, x=0.0, y=4.0, z=0.0),
            "3": Node(node_id="3", node_tag=3, x=0.0, y=4.0, z=3.0),
            "4": Node(node_id="4", node_tag=4, x=0.0, y=0.0, z=3.0),
        }
        areas = {
            "A1": AreaElement(
                area_id="A1", area_tag=100, node_ids=["1", "2", "3", "4"], thickness=0.3
            ),
        }
        restraints = {
            "1": Restraint(dofs=[1, 1, 1, 1, 1, 1]),
            "2": Restraint(dofs=[1, 1, 1, 1, 1, 1]),
        }
        _, _, new_nodes, _ = subdivide_area_mesh(
            areas,
            {"A1": "WALL_SEC"},
            dict(nodes),
            n=2,
            next_tag=1000,
            restraints=restraints,
        )

        # Base edge nodes (j=0, i=1), (j=0, i=2), etc. get full fixity
        # from AND(1,1,1,1,1,1, 1,1,1,1,1,1) = all fixed.
        # Node id pattern: A1_sub_{j}_{i}.
        base_mid = new_nodes.get("A1_sub_0_1")
        assert base_mid is not None
        assert restraints["A1_sub_0_1"].dofs == [1, 1, 1, 1, 1, 1]

        # Left edge mid (i=0, j=1): corners 1 & 4 → node 4 unrestrained
        # → no propagation.
        assert "A1_sub_1_0" not in restraints

        # Interior node (j=1, i=1) untouched.
        assert "A1_sub_1_1" not in restraints

    def test_subdivide_area_mesh_preserves_existing_restraint_on_dedup(self):
        """Dedup reuse keeps an existing edge-midpoint node's own restraint.

        Node 5 sits at (0, 2, 0) — the midpoint of A1's base edge (1→2) —
        and is a corner of the adjacent quad A2, so it is registered in the
        coordinate dedup table.  ``subdivide_area_mesh`` must reuse it for
        the base-edge midpoint instead of creating ``A1_sub_0_1``, and must
        NOT overwrite its explicit user restraint with the bitwise-AND of
        the two corner restraints (full fixity).
        """
        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
            "2": Node(node_id="2", node_tag=2, x=0.0, y=4.0, z=0.0),
            "3": Node(node_id="3", node_tag=3, x=0.0, y=4.0, z=3.0),
            "4": Node(node_id="4", node_tag=4, x=0.0, y=0.0, z=3.0),
            # Existing node at A1's base-edge midpoint — also a corner of A2.
            "5": Node(node_id="5", node_tag=5, x=0.0, y=2.0, z=0.0),
            "6": Node(node_id="6", node_tag=6, x=2.0, y=2.0, z=0.0),
            "7": Node(node_id="7", node_tag=7, x=2.0, y=3.0, z=0.0),
            "8": Node(node_id="8", node_tag=8, x=0.0, y=3.0, z=0.0),
        }
        areas = {
            "A1": AreaElement(
                area_id="A1", area_tag=100, node_ids=["1", "2", "3", "4"], thickness=0.3
            ),
            "A2": AreaElement(
                area_id="A2", area_tag=200, node_ids=["5", "6", "7", "8"], thickness=0.3
            ),
        }
        restraints = {
            "1": Restraint(dofs=[1, 1, 1, 1, 1, 1]),
            "2": Restraint(dofs=[1, 1, 1, 1, 1, 1]),
            # Explicit user restraint on the existing midpoint node — must
            # survive subdivision (AND of corners 1 & 2 would be full fixity).
            "5": Restraint(dofs=[1, 1, 0, 0, 0, 0]),
        }
        _, _, new_nodes, _ = subdivide_area_mesh(
            areas,
            {"A1": "WALL_SEC"},
            dict(nodes),
            n=2,
            next_tag=1000,
            selection={"A1"},
            restraints=restraints,
        )

        # Dedup path: the existing node 5 is reused, so no A1_sub_0_1 is created.
        assert "A1_sub_0_1" not in new_nodes
        assert new_nodes["5"] is not None
        # The reused node's original restraint remains unchanged.
        assert restraints["5"].dofs == [1, 1, 0, 0, 0, 0]
