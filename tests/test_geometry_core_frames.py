"""Tests for the model geometry helpers (split, local axes, spatial grid)."""

"""Tests for the model layer: dataclasses, geometry utilities, and sections."""

from pathlib import Path

import numpy as np
import pytest

from fea_toolkit.model.geometry import (
    get_SAP_vecxz,
    trapezoidal_force_split,
)
from fea_toolkit.model.sap_data import (
    FrameElement,
    Node,
    SAPModelData,
    Section,
)

# ============================================================================
# Fixtures
# ============================================================================

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ============================================================================
# Dataclass construction tests
# ============================================================================


# ═══════════════════════════════════════════════════════════════════
# Geometry helpers
# ═══════════════════════════════════════════════════════════════════


class TestSectionLibrary:
    @pytest.fixture
    def db_path(self):
        p = FIXTURES_DIR.parent.parent / "data" / "section_dict.pkl"
        if not p.exists():
            pytest.skip(f"Section database not found: {p}")
        return p

    def test_load_database(self, db_path):
        from fea_toolkit.model.sections import SectionLibrary

        lib = SectionLibrary(db_path, target_units="m")
        assert len(lib.list_catalogues()) > 0

    def test_get_section_properties(self, db_path):
        from fea_toolkit.model.sections import SectionLibrary

        lib = SectionLibrary(db_path, target_units="m")
        catalogues = lib.list_catalogues()
        # Try to find a section from the first catalogue
        first_cat = catalogues[0]
        cat_data = lib._catalogues[first_cat]
        sections_dict = cat_data.get("SECTIONS", cat_data)
        if sections_dict:
            first_sec_name = next(iter(sections_dict))
            props = lib.get_section_properties(first_sec_name)
            assert props is not None
            assert "_catalogue" in props

    def test_enrich_section(self, db_path):
        from fea_toolkit.model.sections import SectionLibrary

        lib = SectionLibrary(db_path, target_units="m")
        # Create a basic section
        sec = Section(
            name="dummy",
            shape="I/Wide Flange",
            material="Steel",
            A=0.01,
            I33=1e-4,
            I22=5e-5,
            J=1e-6,
        )
        # Enrichment should not crash even if section is not in DB
        lib.enrich_section(sec)
        # Z33/Z22 may be None if not in DB
        assert hasattr(sec, "Z33")


# ============================================================================
# Integration test: parser -> model data -> geometry
# ============================================================================


class TestParserModelIntegration:
    @pytest.fixture
    def parsed_model(self):
        """Parse the sample.s2k fixture and return SAPModelData."""
        s2k_file = FIXTURES_DIR / "sample.s2k"
        if not s2k_file.exists():
            pytest.skip(f"Sample file not found: {s2k_file}")
        from fea_toolkit.io.s2k_parser import SAP2000Parser

        parser = SAP2000Parser(s2k_file)
        parser.parse()
        return parser.get_model_data()

    def test_nodes_parsed(self, parsed_model):
        assert len(parsed_model.nodes) > 0
        # Verify first node
        n1 = parsed_model.nodes.get("1")
        if n1:
            assert n1.x == 0.0

    def test_frames_parsed(self, parsed_model):
        assert len(parsed_model.frame_elements) > 0

    def test_auto_mesh_parsed(self, parsed_model):
        assert len(parsed_model.frame_auto_mesh) > 0
        # Check AtJoints flag is set on some frames
        at_joints_count = sum(1 for v in parsed_model.frame_auto_mesh.values() if v.get("AtJoints"))
        assert at_joints_count > 0

    def test_units_parsed(self, parsed_model):
        units = parsed_model.units
        assert "L" in units
        assert units["L"] in ("m", "mm", "in", "ft", "cm")

    def test_sections_parsed(self, parsed_model):
        assert len(parsed_model.sections) >= 0

    def test_restraints_parsed(self, parsed_model):
        assert len(parsed_model.restraints) >= 0

    def test_split_elements(self, parsed_model):
        """Test that split_elements can run on parsed model data."""
        from fea_toolkit.model.geometry import split_elements

        result = split_elements(
            nodes=parsed_model.nodes,
            elements=parsed_model.frame_elements,
            assignments=parsed_model.frame_assignments,
            dist_loads=parsed_model.frame_dist_loads,
            auto_mesh=parsed_model.frame_auto_mesh,
            tol=1e-6,
            verbose=False,
        )
        new_elements, _new_assignments, _new_dist_loads = result
        assert len(new_elements) > 0
        # Parent elements should be marked inactive
        inactive = [e for e in new_elements.values() if e.inactive]
        assert len(inactive) > 0

    def test_split_elements_tracking(self, parsed_model):
        """Verify parent-child tracking after splitting."""
        from fea_toolkit.model.geometry import split_elements

        result = split_elements(
            nodes=parsed_model.nodes,
            elements=parsed_model.frame_elements,
            assignments=parsed_model.frame_assignments,
            dist_loads=parsed_model.frame_dist_loads,
            auto_mesh=parsed_model.frame_auto_mesh,
            tol=1e-6,
            verbose=False,
        )
        new_elements, _, _ = result
        # Check that parent elements have child_ids populated
        for eid, elem in new_elements.items():
            if elem.inactive:
                assert len(elem.child_ids) > 0, f"Inactive element {eid} should have children"


# ============================================================================
# Edge cases
# ============================================================================


class TestSplitElementsAtFrames:
    """Tests for the AtFrames frame-frame intersection splitting."""

    def test_at_frames_no_intersection_no_split(self):
        """Elements with AtFrames=True that don't intersect should not split."""
        from fea_toolkit.model.geometry import split_elements

        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=10, y=0, z=0),
            "3": Node(node_id="3", node_tag=3, x=0, y=10, z=0),
            "4": Node(node_id="4", node_tag=4, x=10, y=10, z=0),
        }
        elements = {
            "A": FrameElement(elem_id="A", elem_tag=10, node_i="1", node_j="2"),
            "B": FrameElement(elem_id="B", elem_tag=11, node_i="3", node_j="4"),
        }
        auto_mesh = {"A": {"AtFrames": True}, "B": {"AtFrames": True}}
        result = split_elements(
            nodes=nodes, elements=elements, assignments={}, dist_loads=[], auto_mesh=auto_mesh
        )
        new_elems, _, _ = result
        assert len(new_elems) == 2
        assert "A" in new_elems and "B" in new_elems
        assert not new_elems["A"].inactive
        assert not new_elems["B"].inactive

    def test_at_frames_crossing_split(self):
        """Two perpendicular frames crossing should split at intersection."""
        from fea_toolkit.model.geometry import split_elements

        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=10, y=0, z=0),
            "3": Node(node_id="3", node_tag=3, x=5, y=-5, z=0),
            "4": Node(node_id="4", node_tag=4, x=5, y=5, z=0),
        }
        elements = {
            "A": FrameElement(elem_id="A", elem_tag=10, node_i="1", node_j="2"),
            "B": FrameElement(elem_id="B", elem_tag=11, node_i="3", node_j="4"),
        }
        auto_mesh = {"A": {"AtFrames": True}, "B": {"AtFrames": True}}
        # Pass nodes directly (not a copy) so we can check new nodes
        result = split_elements(
            nodes=nodes, elements=elements, assignments={}, dist_loads=[], auto_mesh=auto_mesh
        )
        new_elems, _, _ = result
        # Both elements should be split → 2 children each + 2 inactive parents
        assert "A" in new_elems
        assert new_elems["A"].inactive
        assert len(new_elems["A"].child_ids) == 2
        assert "B" in new_elems
        assert new_elems["B"].inactive
        assert len(new_elems["B"].child_ids) == 2
        # Check a new node was created at (5, 0, 0) in the nodes dict
        split_node = None
        for nid, nd in nodes.items():
            if nid.startswith("split_n_"):
                split_node = nd
                break
        assert split_node is not None, "No split node created"
        assert abs(split_node.x - 5) < 1e-6
        assert abs(split_node.y - 0) < 1e-6
        assert abs(split_node.z - 0) < 1e-6
        assert split_node.node_tag == 5, f"Expected next tag 5, got {split_node.node_tag}"
        assert split_node.node_id == "split_n_1"

    def test_at_frames_skips_shared_joint(self):
        """Elements already sharing a joint should not be split."""
        from fea_toolkit.model.geometry import split_elements

        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=10, y=0, z=0),
            "3": Node(node_id="3", node_tag=3, x=10, y=5, z=0),
        }
        elements = {
            "A": FrameElement(elem_id="A", elem_tag=10, node_i="1", node_j="2"),
            "B": FrameElement(elem_id="B", elem_tag=11, node_i="2", node_j="3"),
        }
        auto_mesh = {"A": {"AtFrames": True}, "B": {"AtFrames": True}}
        # Should not crash — they share node "2"
        result = split_elements(
            nodes=nodes, elements=elements, assignments={}, dist_loads=[], auto_mesh=auto_mesh
        )
        new_elems, _, _ = result
        # Should not crash — they share node "2"
        assert len(new_elems) == 2
        assert not new_elems["A"].inactive
        assert not new_elems["B"].inactive

    def test_at_frames_3d_crossing(self):
        """Elements crossing at different elevations (3D) — no split."""
        from fea_toolkit.model.geometry import split_elements

        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=10, y=0, z=0),
            "3": Node(node_id="3", node_tag=3, x=5, y=-5, z=5),
            "4": Node(node_id="4", node_tag=4, x=5, y=5, z=5),
        }
        elements = {
            "A": FrameElement(elem_id="A", elem_tag=10, node_i="1", node_j="2"),
            "B": FrameElement(elem_id="B", elem_tag=11, node_i="3", node_j="4"),
        }
        auto_mesh = {"A": {"AtFrames": True}, "B": {"AtFrames": True}}
        result = split_elements(
            nodes=nodes, elements=elements, assignments={}, dist_loads=[], auto_mesh=auto_mesh
        )
        new_elems, _, _ = result
        # Non-coplanar → no intersection → no split
        assert not new_elems["A"].inactive
        assert not new_elems["B"].inactive

    def test_at_frames_no_joints_but_frames(self):
        """Element with AtFrames=True but AtJoints=False should still split."""
        from fea_toolkit.model.geometry import split_elements

        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=10, y=0, z=0),
            "3": Node(node_id="3", node_tag=3, x=5, y=-5, z=0),
            "4": Node(node_id="4", node_tag=4, x=5, y=5, z=0),
        }
        elements = {
            "A": FrameElement(elem_id="A", elem_tag=10, node_i="1", node_j="2"),
            "B": FrameElement(elem_id="B", elem_tag=11, node_i="3", node_j="4"),
        }
        # Only AtFrames, no AtJoints
        auto_mesh = {
            "A": {"AtJoints": False, "AtFrames": True},
            "B": {"AtJoints": False, "AtFrames": True},
        }
        result = split_elements(
            nodes=nodes, elements=elements, assignments={}, dist_loads=[], auto_mesh=auto_mesh
        )
        new_elems, _, _ = result
        assert new_elems["A"].inactive
        assert len(new_elems["A"].child_ids) == 2
        assert new_elems["B"].inactive
        assert len(new_elems["B"].child_ids) == 2
        # Verify split node got correct sequential tag (5 after 1..4)
        split_nid = next(nid for nid in nodes if nid.startswith("split_n_"))
        assert nodes[split_nid].node_tag == 5
        assert nodes[split_nid].node_id == "split_n_1"

    def test_at_frames_skips_existing_joint_when_no_atjoints(self):
        """An existing joint on an element should NOT split it when
        AtJoints=False, even if AtFrames is True and creates a split."""
        from fea_toolkit.model.geometry import split_elements

        # Elements A (0→10) and C (3→7) intersect at (3,0,0) — existing joint.
        # Element B crosses at (5,0,0) — AtFrames intersection.
        # With AtJoints=False, only the AtFrames intersection should split.
        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=10, y=0, z=0),
            "3": Node(node_id="3", node_tag=3, x=3, y=0, z=0),
            "4": Node(node_id="4", node_tag=4, x=7, y=0, z=0),
            # Element B crosses at (5,0,0)
            "5": Node(node_id="5", node_tag=5, x=5, y=-5, z=0),
            "6": Node(node_id="6", node_tag=6, x=5, y=5, z=0),
        }
        elements = {
            "A": FrameElement(elem_id="A", elem_tag=10, node_i="1", node_j="2"),
            "B": FrameElement(elem_id="B", elem_tag=11, node_i="5", node_j="6"),
            "C": FrameElement(elem_id="C", elem_tag=12, node_i="3", node_j="4"),
        }
        auto_mesh = {
            "A": {"AtJoints": False, "AtFrames": True},
            "B": {"AtJoints": False, "AtFrames": True},
            "C": {"AtJoints": False, "AtFrames": False},
        }
        result = split_elements(
            nodes=nodes, elements=elements, assignments={}, dist_loads=[], auto_mesh=auto_mesh
        )
        new_elems, _, _ = result
        # Element A should be split only at the AtFrames node (5,0,0),
        # NOT at the existing joint node 3 (3,0,0).
        # So it should have 2 children (split at t=0.5).
        assert new_elems["A"].inactive
        assert len(new_elems["A"].child_ids) == 2, (
            f"Expected 2 children, got {len(new_elems['A'].child_ids)}"
        )
        # The AtFrames node should get the next sequential tag (7 after 1..6)
        split_nid = next(nid for nid in nodes if nid.startswith("split_n_"))
        assert nodes[split_nid].node_tag == 7, f"Expected tag 7, got {nodes[split_nid].node_tag}"
        # Element C (no AtFrames, no AtJoints) should not be split
        assert not new_elems["C"].inactive
        # Only one new node should exist (the AtFrames intersection)
        at_frames_nodes = [nid for nid in nodes if nid.startswith("split_n_")]
        assert len(at_frames_nodes) == 1

    def test_at_frames_dedup_near_duplicate_t(self):
        """Two intersections that create separate nodes via node-reuse.

        Elements B and C cross A at positions close enough (1e-8 m apart)
        that the 3D node-reuse check (abs_tol ≈ 1e-5) creates only one
        shared split_n_ node.  This exercises the coordinate-based node
        reuse path.

        The secondary t-based dedup (in the main splitting loop) cannot
        currently be triggered by AtFrames-only splits because the node-
        reuse threshold (abs_tol ≈ tol × L) equals the t-dedup threshold
        (tol × L) for equal-length segments.  It guards against future
        cross-source merging (e.g., storey-level + AtFrames).
        """
        from fea_toolkit.model.geometry import split_elements

        # Element A: horizontal beam from (0,0,0) to (10,0,0)
        # Two other elements cross it at nearly the same point:
        #   Element B: crosses at (5,0,0) exactly
        #   Element C: crosses at (5.00000001, 0, 0) — 10 nm off
        # Distance from (5,0,0) to (5.00000001,0,0) = 1e-8 ≤ 1e-6 tol, so
        # split_n_1 is reused. Cross product over 10m = 10×1e-8 = 1e-7 ≤ tol.
        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=10, y=0, z=0),
            "3": Node(node_id="3", node_tag=3, x=5, y=-5, z=0),
            "4": Node(node_id="4", node_tag=4, x=5, y=5, z=0),
            "5": Node(node_id="5", node_tag=5, x=5.00000001, y=-5, z=0),
            "6": Node(node_id="6", node_tag=6, x=5.00000001, y=5, z=0),
        }
        elements = {
            "A": FrameElement(elem_id="A", elem_tag=10, node_i="1", node_j="2"),
            "B": FrameElement(elem_id="B", elem_tag=11, node_i="3", node_j="4"),
            "C": FrameElement(elem_id="C", elem_tag=12, node_i="5", node_j="6"),
        }
        auto_mesh = {
            "A": {"AtJoints": False, "AtFrames": True},
            "B": {"AtJoints": False, "AtFrames": True},
            "C": {"AtJoints": False, "AtFrames": True},
        }
        result = split_elements(
            nodes=nodes, elements=elements, assignments={}, dist_loads=[], auto_mesh=auto_mesh
        )
        new_elems, _, _ = result
        # Element A should have 2 children (split once, not twice)
        assert new_elems["A"].inactive
        assert len(new_elems["A"].child_ids) == 2, (
            f"Expected 2 children (dedup), got {len(new_elems['A'].child_ids)}"
        )

        # Exactly one split node should exist (B and C share it)
        split_nodes = [nid for nid in nodes if nid.startswith("split_n_")]
        assert len(split_nodes) == 1, (
            f"Expected 1 split node, got {len(split_nodes)}: {split_nodes}"
        )

        # A's breakpoint metadata should also reflect the dedup: only one
        # t-location kept (0.5), not both near-identical s entries
        assert len(new_elems["A"].t_locations) == 1, (
            f"Expected 1 t-location (deduped), got {new_elems['A'].t_locations}"
        )

        # B and C's children should both reference that same shared node
        shared_nid = split_nodes[0]
        b_children = [c for cid in new_elems["B"].child_ids for c in [new_elems.get(cid)] if c]
        c_children = [c for cid in new_elems["C"].child_ids for c in [new_elems.get(cid)] if c]
        b_refs = {c.node_i for c in b_children} | {c.node_j for c in b_children}
        c_refs = {c.node_i for c in c_children} | {c.node_j for c in c_children}
        assert shared_nid in b_refs, f"Element B children don't reference {shared_nid}"
        assert shared_nid in c_refs, f"Element C children don't reference {shared_nid}"

    def test_at_frames_t_dedup_direct(self):
        """Integration test: t-based dedup via split_elements().

        Two existing model nodes at nearly the same parametric location
        on element A (AtJoints=True) produce intermediate (nid, t) pairs
        with |t₁ - t₂| ≤ tol.  The main loop's t-dedup drops the second
        node, leaving a single split → 2 children instead of 3.

        AtFrames-only splits cannot trigger this path because the node-
        reuse threshold (abs_tol = tol × L) equals the t-dedup threshold
        (tol × L), so node-reuse always catches near-coincident points
        first in the AtFrames double-loop.  AtJoints nodes bypass that
        check and feed directly into the intermediate list.
        """
        from fea_toolkit.model.geometry import split_elements

        # Element A: horizontal beam from (0,0,0) to (10,0,0)
        # Node 3 lies on A at (5,0,0)          → t = 0.5
        # Node 5 lies on A at (5.000006,0,0)   → t = 0.5000006
        # |t₃ − t₅| = 6e-7 < tol (1e-6) → t-dedup merges them
        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=10, y=0, z=0),
            "3": Node(node_id="3", node_tag=3, x=5, y=0, z=0),
            "5": Node(node_id="5", node_tag=5, x=5.000006, y=0, z=0),
        }
        elements = {
            "A": FrameElement(elem_id="A", elem_tag=10, node_i="1", node_j="2"),
        }
        auto_mesh = {"A": {"AtJoints": True, "AtFrames": False}}
        result = split_elements(
            nodes=nodes, elements=elements, assignments={}, dist_loads=[], auto_mesh=auto_mesh
        )
        new_elems, _, _ = result

        # Element A should have 2 children (one split, the second
        # candidate was deduped away)
        assert new_elems["A"].inactive
        assert len(new_elems["A"].child_ids) == 2, (
            f"Expected 2 children (t-dedup), got {len(new_elems['A'].child_ids)}"
        )

        # The intermediate children reference existing node IDs (no
        # split_n_ nodes are created for AtJoints).  Verify that node 5
        # was deduped away: children span (1→3) and (3→2).
        children = [new_elems[cid] for cid in new_elems["A"].child_ids]
        node_pairs = {(c.node_i, c.node_j) for c in children}
        assert ("1", "3") in node_pairs, f"Expected child 1→3, got {node_pairs}"
        assert ("3", "2") in node_pairs, f"Expected child 3→2, got {node_pairs}"
        child_endpoints = {nid for pair in node_pairs for nid in pair}
        assert "5" not in child_endpoints, "Node 5 should have been deduped"

        # Distinct t values → all kept (sanity: no accidental dedup)
        # Node 6 at (3,0,0) → t=0.3, Node 7 at (7,0,0) → t=0.7
        nodes2 = {
            "1": Node("1", 1, 0, 0, 0),
            "2": Node("2", 2, 10, 0, 0),
            "6": Node("6", 6, 3, 0, 0),
            "7": Node("7", 7, 7, 0, 0),
        }
        elements2 = {"B": FrameElement("B", 20, "1", "2")}
        auto_mesh2 = {"B": {"AtJoints": True, "AtFrames": False}}
        result2 = split_elements(
            nodes=nodes2, elements=elements2, assignments={}, dist_loads=[], auto_mesh=auto_mesh2
        )
        new_elems2, _, _ = result2
        assert new_elems2["B"].inactive
        assert len(new_elems2["B"].child_ids) == 3, (
            f"Expected 3 children (2 splits, no dedup), got {len(new_elems2['B'].child_ids)}"
        )


class TestEdgeCases:
    def test_empty_model(self):
        """SAPModelData with no data should not crash."""
        model = SAPModelData(
            nodes={},
            restraints={},
            materials={},
            sections={},
            frame_elements={},
            area_elements={},
            frame_assignments={},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
        )
        assert len(model.nodes) == 0
        assert len(model.frame_elements) == 0

    def test_zero_length_element_skipped(self):
        """A zero-length element should be handled gracefully."""
        from fea_toolkit.model.geometry import split_elements

        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=0, y=0, z=0),
        }
        elements = {
            "1": FrameElement(elem_id="1", elem_tag=1, node_i="1", node_j="2"),
        }
        assignments = {"1": "Sec1"}
        auto_mesh = {"1": {"AtJoints": True}}
        result = split_elements(
            nodes=nodes,
            elements=elements,
            assignments=assignments,
            dist_loads=[],
            auto_mesh=auto_mesh,
            verbose=False,
        )
        new_elements, new_assignments, _ = result
        # Zero-length element should be kept as-is, not split
        assert "1" in new_elements
        assert new_assignments.get("1") == "Sec1"

    def test_trapezoidal_split_no_intermediate(self):
        """trapezoidal_force_split with empty t_values returns one segment."""
        f_data = ((0.0, 5.0), (1.0, 10.0))
        result = trapezoidal_force_split(f_data, [])
        assert len(result) == 1

    def test_get_SAP_vecxz_with_list_input(self):
        """Should accept plain Python lists as input."""
        vecxz = get_SAP_vecxz([5.0, 0.0, 0.0])
        assert np.allclose(vecxz, [0.0, -1.0, 0.0], atol=1e-6)
