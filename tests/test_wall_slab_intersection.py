"""Tests for wall-slab intersection detection and splitting.

Tests the geometry functions used by the Preprocessor's
``detect_wall_slab_intersections`` and ``split_slabs_at_walls`` features.
"""

import math
import pytest

from fea_toolkit.model.sap_data import (
    SAPModelData, Node, Restraint, Material, Section, FrameElement,
    AreaElement, LoadPattern,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def materials():
    return {"Concrete": Material("Concrete", "Concrete", E_mod=3e10)}


@pytest.fixture
def sections():
    return {
        "Slab200": Section("Slab200", "Shell", "Concrete", A=0, I33=0, I22=0, J=0),
        "Wall300": Section("Wall300", "Shell", "Concrete", A=0, I33=0, I22=0, J=0),
    }


# ============================================================================
# Wall-slab intersection detection
# ============================================================================

class TestFindWallNodesInsideSlabs:
    """Tests for :func:`find_wall_nodes_inside_slabs`."""

    def test_simple_wall_through_slab(self, materials, sections):
        """A vertical wall passing through a horizontal slab is detected.

        Layout: 4×4 m slab at z=0.  A 4 m tall wall runs along the
        X-axis from (1,2,0) to (3,2,0)→(3,2,4).  The wall's bottom
        nodes (1,2,0) and (3,2,0) lie inside the slab.
        """
        from fea_toolkit.model.geometry import find_wall_nodes_inside_slabs

        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 4.0, 0.0, 0.0),
            "3": Node("3", 3, 4.0, 4.0, 0.0),
            "4": Node("4", 4, 0.0, 4.0, 0.0),
            # Wall bottom nodes (inside slab)
            "5": Node("5", 5, 1.0, 2.0, 0.0),
            "6": Node("6", 6, 3.0, 2.0, 0.0),
            # Wall top nodes (above slab)
            "7": Node("7", 7, 1.0, 2.0, 4.0),
            "8": Node("8", 8, 3.0, 2.0, 4.0),
        }
        areas = {
            "Slab": AreaElement("Slab", 10, ["1", "2", "3", "4"]),
            "Wall": AreaElement("Wall", 20, ["5", "6", "8", "7"]),
        }
        assigns = {"Slab": "Slab200", "Wall": "Wall300"}

        findings = find_wall_nodes_inside_slabs(areas, assigns, nodes)
        assert len(findings) >= 1, f"Expected ≥1 finding, got {len(findings)}"
        # The wall should be detected
        wall_findings = [f for f in findings if f["wall_id"] == "Wall"]
        assert len(wall_findings) == 1, (
            f"Expected 1 finding for Wall, got {len(wall_findings)}"
        )
        wf = wall_findings[0]
        assert wf["slab_id"] == "Slab"
        assert len(wf["nodes"]) >= 2, (
            f"Expected ≥2 wall nodes inside slab, got {len(wf['nodes'])}"
        )
        # Wall bottom nodes should be inside slab
        inside_ids = {n["node_id"] for n in wf["nodes"]}
        assert "5" in inside_ids, "Wall bottom-left node missing from findings"
        assert "6" in inside_ids, "Wall bottom-right node missing from findings"
        # Wall top nodes should NOT be inside slab (they're above it)
        assert "7" not in inside_ids, "Wall top-left node incorrectly reported"

    def test_no_intersection_returns_empty(self, materials, sections):
        """Areas that don't intersect produce no findings."""
        from fea_toolkit.model.geometry import find_wall_nodes_inside_slabs

        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 10.0, 0.0, 0.0),
            "3": Node("3", 3, 10.0, 10.0, 0.0),
            "4": Node("4", 4, 0.0, 10.0, 0.0),
            # Wall offset — no overlap with slab
            "5": Node("5", 5, 20.0, 20.0, 0.0),
            "6": Node("6", 6, 25.0, 20.0, 0.0),
            "7": Node("7", 7, 25.0, 20.0, 4.0),
            "8": Node("8", 8, 20.0, 20.0, 4.0),
        }
        areas = {
            "Slab": AreaElement("Slab", 10, ["1", "2", "3", "4"]),
            "Wall": AreaElement("Wall", 20, ["5", "6", "7", "8"]),
        }
        assigns = {"Slab": "Slab200", "Wall": "Wall300"}

        findings = find_wall_nodes_inside_slabs(areas, assigns, nodes)
        assert len(findings) == 0, (
            f"Expected empty findings for non-intersecting areas, "
            f"got {len(findings)}"
        )

    def test_slab_only_returns_empty(self, materials, sections):
        """Only slab areas (no walls) produce no findings."""
        from fea_toolkit.model.geometry import find_wall_nodes_inside_slabs

        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 4.0, 0.0, 0.0),
            "3": Node("3", 3, 4.0, 4.0, 0.0),
            "4": Node("4", 4, 0.0, 4.0, 0.0),
        }
        areas = {"Slab": AreaElement("Slab", 10, ["1", "2", "3", "4"])}
        assigns = {"Slab": "Slab200"}

        findings = find_wall_nodes_inside_slabs(areas, assigns, nodes)
        assert len(findings) == 0

    def test_slabs_at_different_z_levels(self, materials, sections):
        """Wall nodes at one slab's Z are not confused with another slab."""
        from fea_toolkit.model.geometry import find_wall_nodes_inside_slabs

        nodes = {
            # Ground slab at z=0
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 6.0, 0.0, 0.0),
            "3": Node("3", 3, 6.0, 6.0, 0.0),
            "4": Node("4", 4, 0.0, 6.0, 0.0),
            # Roof slab at z=3
            "5": Node("5", 5, 0.0, 0.0, 3.0),
            "6": Node("6", 6, 6.0, 0.0, 3.0),
            "7": Node("7", 7, 6.0, 6.0, 3.0),
            "8": Node("8", 8, 0.0, 6.0, 3.0),
            # Wall from ground to roof through interior
            "9":  Node("9", 9, 2.0, 2.0, 0.0),
            "10": Node("10", 10, 4.0, 2.0, 0.0),
            "11": Node("11", 11, 4.0, 2.0, 3.0),
            "12": Node("12", 12, 2.0, 2.0, 3.0),
        }
        areas = {
            "Ground": AreaElement("Ground", 10, ["1", "2", "3", "4"]),
            "Roof": AreaElement("Roof", 20, ["5", "6", "7", "8"]),
            "Wall": AreaElement("Wall", 30, ["9", "10", "11", "12"]),
        }
        assigns = {"Ground": "Slab200", "Roof": "Slab200", "Wall": "Wall300"}

        findings = find_wall_nodes_inside_slabs(areas, assigns, nodes)
        # Wall bottom nodes should be found in ground slab
        ground_findings = [f for f in findings
                          if f["slab_id"] == "Ground" and f["wall_id"] == "Wall"]
        assert len(ground_findings) == 1
        bottom_ids = {n["node_id"] for n in ground_findings[0]["nodes"]}
        assert "9" in bottom_ids
        assert "10" in bottom_ids

        # Wall top nodes should be found in roof slab
        roof_findings = [f for f in findings
                        if f["slab_id"] == "Roof" and f["wall_id"] == "Wall"]
        assert len(roof_findings) == 1
        top_ids = {n["node_id"] for n in roof_findings[0]["nodes"]}
        assert "11" in top_ids
        assert "12" in top_ids


# ============================================================================
# Wall-slab splitting (Preprocessor integration)
# ============================================================================

class TestSplitSlabsAtWalls:
    """Tests for :func:`split_slabs_at_wall_intersections`."""

    def test_split_slab_at_wall(self, materials, sections):
        """A slab is split along a wall edge that passes through it."""
        from fea_toolkit.model.geometry import split_slabs_at_wall_intersections

        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 6.0, 0.0, 0.0),
            "3": Node("3", 3, 6.0, 6.0, 0.0),
            "4": Node("4", 4, 0.0, 6.0, 0.0),
            # Wall base along Y=3 from X=0 to X=6
            "5": Node("5", 5, 0.0, 3.0, 0.0),
            "6": Node("6", 6, 6.0, 3.0, 0.0),
            "7": Node("7", 7, 0.0, 3.0, 3.0),
            "8": Node("8", 8, 6.0, 3.0, 3.0),
        }
        areas = {
            "Slab": AreaElement("Slab", 10, ["1", "2", "3", "4"]),
            "Wall": AreaElement("Wall", 20, ["5", "6", "8", "7"]),
        }
        assigns = {"Slab": "Slab200", "Wall": "Wall300"}

        result_areas, result_assign, result_nodes, _ = (
            split_slabs_at_wall_intersections(areas, assigns, nodes)
        )
        # The slab should now be split into multiple sub-areas
        slab_sub_ids = [aid for aid in result_areas
                       if aid != "Wall" and "_sub_" in aid]
        assert len(slab_sub_ids) >= 2, (
            f"Expected ≥2 slab sub-areas after splitting, "
            f"got {len(slab_sub_ids)}: {slab_sub_ids}"
        )
        # Original slab should be inactive
        assert result_areas["Slab"].inactive is True

    def test_no_wall_no_split(self, materials, sections):
        """No walls → no splitting occurs."""
        from fea_toolkit.model.geometry import split_slabs_at_wall_intersections

        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 4.0, 0.0, 0.0),
            "3": Node("3", 3, 4.0, 4.0, 0.0),
            "4": Node("4", 4, 0.0, 4.0, 0.0),
        }
        areas = {"Slab": AreaElement("Slab", 10, ["1", "2", "3", "4"])}
        assigns = {"Slab": "Slab200"}

        result_areas, result_assign, result_nodes, _ = (
            split_slabs_at_wall_intersections(areas, assigns, nodes)
        )
        # Slab unchanged
        assert "Slab" in result_areas
        assert result_areas["Slab"].inactive is False
        assert len(result_areas["Slab"].node_ids) == 4


# ============================================================================
# Preprocessor integration test
# ============================================================================

class TestWallSlabPreprocessor:
    """Preprocessor detects wall-slab intersections when configured."""

    def test_preprocessor_detects_intersection(self):
        """Preprocessor.run() with detect_wall_slab_intersections detects walls."""
        from fea_toolkit.opensees.preprocessor import Preprocessor

        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 6.0, 0.0, 0.0),
            "3": Node("3", 3, 6.0, 6.0, 0.0),
            "4": Node("4", 4, 0.0, 6.0, 0.0),
            "5": Node("5", 5, 2.0, 2.0, 0.0),
            "6": Node("6", 6, 4.0, 2.0, 0.0),
            "7": Node("7", 7, 2.0, 2.0, 3.0),
            "8": Node("8", 8, 4.0, 2.0, 3.0),
        }
        materials = {"Concrete": Material("Concrete", "Concrete", E_mod=3e10)}
        sections = {
            "Slab200": Section("Slab200", "Shell", "Concrete",
                               A=0, I33=0, I22=0, J=0),
            "Wall300": Section("Wall300", "Shell", "Concrete",
                               A=0, I33=0, I22=0, J=0),
        }
        md = SAPModelData(
            nodes=nodes, restraints={}, materials=materials,
            sections=sections, frame_elements={},
            area_elements={
                "Slab": AreaElement("Slab", 10, ["1", "2", "3", "4"]),
                "Wall": AreaElement("Wall", 20, ["5", "6", "7", "8"]),
            },
            frame_assignments={},
            area_assignments={"Slab": "Slab200", "Wall": "Wall300"},
            groups={}, frame_auto_mesh={},
        )

        pp = Preprocessor({
            "detect_wall_slab_intersections": True,
            "split_slabs_at_walls": False,  # detection only, no split
            "verbose": False,
        })
        mm = pp.run(md)
        # The Preprocessor should not crash — detection is silent when
        # split_slabs_at_walls is False.  Just verify the run completed.
        assert mm is not None
        assert "Slab" in mm.area_elements
        assert "Wall" in mm.area_elements

    def test_preprocessor_splits_slabs_at_walls(self):
        """Preprocessor.run() with split_slabs_at_walls=True splits the slab."""
        from fea_toolkit.opensees.preprocessor import Preprocessor
        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
        import openseespy.opensees as ops

        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 6.0, 0.0, 0.0),
            "3": Node("3", 3, 6.0, 6.0, 0.0),
            "4": Node("4", 4, 0.0, 6.0, 0.0),
            "5": Node("5", 5, 2.0, 2.0, 0.0),
            "6": Node("6", 6, 4.0, 2.0, 0.0),
            "7": Node("7", 7, 2.0, 2.0, 3.0),
            "8": Node("8", 8, 4.0, 2.0, 3.0),
        }
        materials = {"Concrete": Material("Concrete", "Concrete", E_mod=3e10)}
        sections = {
            "Slab200": Section("Slab200", "Shell", "Concrete",
                               A=0, I33=0, I22=0, J=0),
            "Wall300": Section("Wall300", "Shell", "Concrete",
                               A=0, I33=0, I22=0, J=0),
        }
        md = SAPModelData(
            nodes=nodes, restraints={}, materials=materials,
            sections=sections, frame_elements={},
            area_elements={
                "Slab": AreaElement("Slab", 10, ["1", "2", "3", "4"]),
                "Wall": AreaElement("Wall", 20, ["5", "6", "7", "8"]),
            },
            frame_assignments={},
            area_assignments={"Slab": "Slab200", "Wall": "Wall300"},
            groups={}, frame_auto_mesh={},
        )

        cfg = {"detect_wall_slab_intersections": True,
               "split_slabs_at_walls": True,
               "create_shells": True,
               "verbose": False}
        pp = Preprocessor(cfg)
        mm = pp.run(md)
        b = AnalysisBuilder(mm, cfg)
        try:
            b.build_domain()
            # The original slab should now be split into sub-areas
            slab_sub_ids = [aid for aid in mm.area_elements
                          if "_sub_" in aid]
            assert len(slab_sub_ids) >= 2, (
                f"Expected ≥2 slab sub-areas, "
                f"got {len(slab_sub_ids)}: {slab_sub_ids}"
            )
            # The wall and slab sub-areas should share nodes
            wall_node_ids = set(mm.area_elements["Wall"].node_ids)
            for sid in slab_sub_ids:
                shared = wall_node_ids & set(mm.area_elements[sid].node_ids)
                if shared:
                    break  # At least one sub-area shares nodes with wall
            else:
                pytest.fail("No slab sub-area shares nodes with wall")
        finally:
            ops.wipe()
