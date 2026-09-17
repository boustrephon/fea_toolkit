"""Tests for model/geometry_mesh.py — floating-node cleanup.

Mirrors the source: the ``remove_floating_nodes`` implementation lives in
:mod:`fea_toolkit.model.geometry_mesh` (re-exported through the
:mod:`fea_toolkit.model.geometry` facade), so its tests live here.
"""

import pytest

from fea_toolkit.model.sap_data import (
    FrameElement,
    JointLoad,
    Node,
    Restraint,
    SAPModelData,
)

# ============================================================================
# remove_floating_nodes
# ============================================================================


class TestRemoveFloatingNodes:
    """Tests for geometry.remove_floating_nodes()."""

    def test_remove_floating_nodes(self):
        """remove_floating_nodes eliminates unreferenced nodes."""
        from fea_toolkit.model.geometry import remove_floating_nodes

        md = SAPModelData(
            nodes={
                "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
                "2": Node(node_id="2", node_tag=2, x=6, y=0, z=0),
                "3": Node(node_id="3", node_tag=3, x=3, y=4, z=0),  # floating
            },
            restraints={},
            materials={},
            sections={},
            frame_elements={
                "F1": FrameElement(elem_id="F1", elem_tag=10, node_i="1", node_j="2"),
            },
            area_elements={},
            frame_assignments={"F1": "SEC1"},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
        )
        rows = remove_floating_nodes(md)
        # Inert node is removed silently (no mass/loads/restraint to redistribute)
        assert len(rows) == 0
        assert "3" not in md.nodes

    def test_remove_floating_nodes_with_restraint(self):
        """Floating node with restraint transfers it to nearest neighbour.

        Node ``3`` sits at x = 4, so it is strictly nearer to node ``2``
        (2 units) than to node ``1`` (4 units) — the destination is
        unambiguous, with no distance tie to break.
        """
        from fea_toolkit.model.geometry import remove_floating_nodes

        md = SAPModelData(
            nodes={
                "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
                "2": Node(node_id="2", node_tag=2, x=6, y=0, z=0),
                "3": Node(node_id="3", node_tag=3, x=4, y=0, z=0),  # floating
            },
            restraints={"3": Restraint([1, 1, 1, 1, 1, 1])},
            materials={},
            sections={},
            frame_elements={
                "F1": FrameElement(elem_id="F1", elem_tag=10, node_i="1", node_j="2"),
            },
            area_elements={},
            frame_assignments={"F1": "SEC1"},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
        )
        rows = remove_floating_nodes(md)
        assert len(rows) == 1
        assert rows[0]["restrained"] is True
        assert rows[0]["nearest_node"] == "2"
        assert rows[0]["distance"] == pytest.approx(2.0)
        # Restraint transferred to the nearest connected node; the
        # floating node's own entry is dropped.
        assert set(md.restraints) == {"2"}
        assert md.restraints["2"].dofs == [1, 1, 1, 1, 1, 1]

    def test_remove_floating_nodes_transfers_joint_loads_per_pattern(self):
        """Floating node's joint loads transfer per source pattern.

        Regression test: the transferred ``JointLoad`` must use the
        dataclass's real ``node_id`` field and preserve each source
        load's ``pattern`` — the previous patternless
        ``JointLoad(node=...)`` construction raised ``TypeError`` and
        aggregated every pattern into one anonymous entry.

        Node ``3`` sits at x = 4, strictly nearer to node ``2`` (2 units)
        than to node ``1`` (4 units), so every transferred load must land
        on node ``2`` — and the removed node's own entries must be gone, so
        nothing references a node that no longer exists.
        """
        from fea_toolkit.model.geometry import remove_floating_nodes

        md = SAPModelData(
            nodes={
                "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
                "2": Node(node_id="2", node_tag=2, x=6, y=0, z=0),
                "3": Node(node_id="3", node_tag=3, x=4, y=0, z=0),  # floating
            },
            restraints={},
            materials={},
            sections={},
            frame_elements={
                "F1": FrameElement(elem_id="F1", elem_tag=10, node_i="1", node_j="2"),
            },
            area_elements={},
            frame_assignments={"F1": "SEC1"},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
            joint_loads=[
                JointLoad(pattern="DEAD", node_id="3", fz=-100.0),
                JointLoad(pattern="LIVE", node_id="3", fz=-50.0),
            ],
        )
        rows = remove_floating_nodes(md)
        assert len(rows) == 1
        assert rows[0]["nearest_node"] == "2"
        transferred = list(md.joint_loads)
        # The originals on the removed node are deleted, so the list holds
        # exactly the two transferred entries — nothing still points at the
        # phantom node "3".
        assert len(transferred) == 2
        assert {jl.node_id for jl in transferred} == {"2"}
        # Both patterns transferred independently, keeping their own
        # pattern names and landing on the nearest connected node.
        by_pattern = {jl.pattern: jl for jl in transferred}
        assert set(by_pattern) == {"DEAD", "LIVE"}
        assert by_pattern["DEAD"].fz == pytest.approx(-100.0)
        assert by_pattern["LIVE"].fz == pytest.approx(-50.0)

    def test_remove_floating_nodes_preserves_loads_without_destination(self):
        """A loaded floating node with no connected neighbour is kept.

        Regression: when ``connected`` is empty every node is floating, so
        the nearest-neighbour search finds no destination.  Removing the
        node would silently delete its joint loads, so the node and its
        loads must be preserved instead.
        """
        from fea_toolkit.model.geometry import remove_floating_nodes

        md = SAPModelData(
            nodes={
                "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
                "2": Node(node_id="2", node_tag=2, x=6, y=0, z=0),
            },
            restraints={},
            materials={},
            sections={},
            frame_elements={},
            area_elements={},
            frame_assignments={},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
            joint_loads=[
                JointLoad(pattern="DEAD", node_id="1", fz=-100.0),
                JointLoad(pattern="LIVE", node_id="2", fz=-50.0),
            ],
        )
        rows = remove_floating_nodes(md)

        assert rows == []
        assert set(md.nodes) == {"1", "2"}
        assert {(jl.node_id, jl.pattern) for jl in md.joint_loads} == {
            ("1", "DEAD"),
            ("2", "LIVE"),
        }
