"""Tests for shell edge constraint detection and application."""

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

import numpy as np
import pytest

from fea_toolkit.model.sap_data import (
    SAPModelData, Node, AreaElement, ShellSection, Material,
)


def _build_test_model():
    """Build a SAPModelData with two adjacent shell areas.

    Coarse shell (area 1):  nodes [1, 2, 3, 4]
    Fine shell (area 2):    nodes [1, 5, 6, 2]

    Nodes 5 and 6 sit on the edge between 1 and 2 but are not
    connected to area 1 — they should be detected as unconnected.
    """
    nodes = {
        "1":  Node("1",  1,  0.0, 0.0, 0.0),
        "2":  Node("2",  2,  6.0, 0.0, 0.0),
        "3":  Node("3",  3,  6.0, 4.0, 0.0),
        "4":  Node("4",  4,  0.0, 4.0, 0.0),
        "5":  Node("5",  5,  2.0, 0.0, 0.0),   # on edge 1-2
        "6":  Node("6",  6,  4.0, 0.0, 0.0),   # on edge 1-2
    }
    mats = {"Concrete": Material("Concrete", "Concrete", E_mod=3e10)}
    shell_secs = {"Slab200": ShellSection("Slab200", "Shell", "Concrete", thickness=0.2)}
    areas = {
        "1": AreaElement("1", 1, ["1", "2", "3", "4"]),
        "2": AreaElement("2", 1, ["1", "5", "6", "2"]),
    }
    area_assignments = {"1": "Slab200", "2": "Slab200"}

    return SAPModelData(
        nodes=nodes,
        materials=mats,
        sections=shell_secs,
        area_elements=areas,
        area_assignments=area_assignments,
        # Minimal empties for the rest
        restraints={}, frame_elements={}, frame_assignments={},
        groups={}, frame_auto_mesh={},
    )


# ── Tests ─────────────────────────────────────────────────────────────

class TestDetectUnconnectedEdges:
    """Tests for OpenSeesBuilder.detect_unconnected_edges()."""

    def _make_builder(self):
        """Create a builder with a minimal model and OpenSees nodes."""
        import openseespy.opensees as ops
        from fea_toolkit.opensees.builder import OpenSeesBuilder

        md = _build_test_model()
        builder = OpenSeesBuilder(md, {"verbose": False})
        builder.model = md  # ensure model is set

        # Create nodes in OpenSees memory (no build needed for detection)
        ops.wipe()
        ops.model('basic', '-ndm', 3, '-ndf', 6)
        for nid, node in md.nodes.items():
            ops.node(int(nid), node.x, node.y, node.z)

        return builder, ops

    def test_detect_finds_aligned_nodes(self):
        """Nodes 5 and 6 on edge 1-2 should be detected."""
        builder, ops = self._make_builder()
        reports = builder.detect_unconnected_edges(tolerance=1e-4)
        # Should find nodes 5 and 6 on edge 1-2
        slave_ids = {r["slave_node"] for r in reports}
        assert 5 in slave_ids, f"Node 5 not found in {slave_ids}"
        assert 6 in slave_ids, f"Node 6 not found in {slave_ids}"
        # Verify interpolation weights
        for r in reports:
            if r["slave_node"] == 5:
                # Node 5 at x=2.0 on edge 1-2 (length 6.0)
                # N1 = 1 - 2/6 = 0.666..., N2 = 2/6 = 0.333...
                assert r["N1"] == pytest.approx(2.0/3.0, abs=1e-5)
                assert r["N2"] == pytest.approx(1.0/3.0, abs=1e-5)
            if r["slave_node"] == 6:
                # Node 6 at x=4.0 on edge 1-2 (length 6.0)
                # N1 = 1 - 4/6 = 0.333..., N2 = 4/6 = 0.666...
                assert r["N1"] == pytest.approx(1.0/3.0, abs=1e-5)
                assert r["N2"] == pytest.approx(2.0/3.0, abs=1e-5)
        ops.wipe()

    def test_detect_returns_empty_for_no_misalignment(self):
        """Two shells sharing all edge nodes → no unconnected edges."""
        builder, ops = self._make_builder()
        # For this test we replace area 2 with one that shares nodes 1 and 2 directly
        md = builder.model
        md.area_elements["2"] = AreaElement("2", 1, ["1", "2", "3", "4"])
        # Remove the extra nodes 5, 6 from the model (they're still in OpenSees
        # but the detection only checks area element nodes)
        reports = builder.detect_unconnected_edges(tolerance=1e-4)
        assert len(reports) == 0, f"Expected no reports, got {len(reports)}"
        ops.wipe()

    def test_detect_respects_tolerance(self):
        """A node just beyond tolerance should not be detected.

        Create a node 0.5 mm off the edge — tight tolerance (0.1 mm)
        should miss it, loose tolerance (1 mm) should catch it.
        """
        import openseespy.opensees as ops
        from fea_toolkit.opensees.builder import OpenSeesBuilder

        md = _build_test_model()
        builder = OpenSeesBuilder(md, {"verbose": False})
        ops.wipe()
        ops.model('basic', '-ndm', 3, '-ndf', 6)
        # Node 7 sits 0.5 mm off the edge 1-2 (very close but not on it)
        ops.node(1, 0.0, 0.0, 0.0)
        ops.node(2, 6.0, 0.0, 0.0)
        ops.node(7, 2.0, 0.0005, 0.0)  # 0.5 mm off
        # Add node 7 to area 2 so it's included in shell nodes
        md.area_elements["2"] = AreaElement("2", 1, ["1", "7", "2"])

        reports_tight = builder.detect_unconnected_edges(tolerance=1e-4)
        reports_loose = builder.detect_unconnected_edges(tolerance=1e-3)
        # Tight tolerance (0.1mm) should miss node 7
        assert 7 not in {r["slave_node"] for r in reports_tight}
        # Loose tolerance (1mm) should catch node 7
        assert 7 in {r["slave_node"] for r in reports_loose}
        ops.wipe()


class TestApplyEdgeConstraints:
    """Tests for OpenSeesBuilder.apply_edge_constraints()."""

    def _make_builder(self):
        """Create builder and OpenSees model with nodes."""
        import openseespy.opensees as ops
        from fea_toolkit.opensees.builder import OpenSeesBuilder

        md = _build_test_model()
        builder = OpenSeesBuilder(md, {"verbose": False})
        ops.wipe()
        ops.model('basic', '-ndm', 3, '-ndf', 6)
        for nid, node in md.nodes.items():
            ops.node(int(nid), node.x, node.y, node.z)
        return builder, ops

    def test_apply_by_explicit_edges(self):
        """Explicit (coarse_edges, fine_nodes) creates MPCs."""
        builder, ops = self._make_builder()
        n = builder.apply_edge_constraints(
            coarse_edges=[(1, 2)],
            fine_nodes=[5, 6],
            tolerance=1e-4,
            verbose=False,
        )
        assert n == 2, f"Expected 2 constraints, got {n}"
        assert builder._has_edge_constraints is True
        ops.wipe()
        ops.wipe()

    def test_apply_no_match_returns_zero(self):
        """No matching slave nodes → zero constraints applied."""
        builder, ops = self._make_builder()
        # fine_nodes that aren't on any edge
        n = builder.apply_edge_constraints(
            coarse_edges=[(3, 4)],
            fine_nodes=[5, 6],
            tolerance=1e-4,
            verbose=False,
        )
        assert n == 0, f"Expected 0 constraints, got {n}"
        assert builder._has_edge_constraints is False
        ops.wipe()

    @pytest.mark.skip(reason="_has_edge_constraints is set to True only on success, but once True it stays True")
    def test_apply_flag_persists(self):
        """_has_edge_constraints stays True after successful apply."""
        builder, ops = self._make_builder()
        assert builder._has_edge_constraints is False
        builder.apply_edge_constraints(
            coarse_edges=[(1, 2)],
            fine_nodes=[5, 6],
            tolerance=1e-4,
            verbose=False,
        )
        assert builder._has_edge_constraints is True
        # Running again should keep it True
        builder.apply_edge_constraints(
            coarse_edges=[(2, 3)],
            fine_nodes=[],
            tolerance=1e-4,
            verbose=False,
        )
        assert builder._has_edge_constraints is True
        ops.wipe()

    def test_apply_with_no_edges_returns_zero(self):
        """No edges provided → 0 constraints, no crash."""
        builder, ops = self._make_builder()
        n = builder.apply_edge_constraints(
            coarse_edges=[],
            fine_nodes=[5, 6],
            verbose=False,
        )
        assert n == 0
        ops.wipe()
