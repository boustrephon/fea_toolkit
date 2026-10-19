"""Openings: net shell area and shell-creation exclusion (``model/openings.py``)."""

import pytest

from fea_toolkit.model.openings import (
    conform_cell_to_openings,
    net_area,
    opening_overlap_area,
    point_in_polygon_2d,
)
from fea_toolkit.model.sap_data import (
    AreaElement,
    AreaMesh,
    Material,
    Node,
    SAPModelData,
    ShellSection,
)
from fea_toolkit.model.self_weight import selection_weight


def _slab_with_opening() -> SAPModelData:
    """A 4×4 horizontal slab with a centred 1×1 opening area (both in XY)."""
    nodes = {
        "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
        "2": Node(node_id="2", node_tag=2, x=4.0, y=0.0, z=0.0),
        "3": Node(node_id="3", node_tag=3, x=4.0, y=4.0, z=0.0),
        "4": Node(node_id="4", node_tag=4, x=0.0, y=4.0, z=0.0),
        "o1": Node(node_id="o1", node_tag=5, x=1.5, y=1.5, z=0.0),
        "o2": Node(node_id="o2", node_tag=6, x=2.5, y=1.5, z=0.0),
        "o3": Node(node_id="o3", node_tag=7, x=2.5, y=2.5, z=0.0),
        "o4": Node(node_id="o4", node_tag=8, x=1.5, y=2.5, z=0.0),
    }
    areas = {
        "S1": AreaElement(area_id="S1", area_tag=1, node_ids=["1", "2", "3", "4"]),
        "O1": AreaElement(
            area_id="O1", area_tag=2, node_ids=["o1", "o2", "o3", "o4"], opening=True
        ),
    }
    return SAPModelData(
        nodes=nodes,
        restraints={},
        materials={"Concrete": Material(name="Concrete", type="Concrete", unit_weight=2500.0)},
        sections={
            "SLAB": ShellSection(name="SLAB", shape="Shell", material="Concrete", thickness=0.2)
        },
        frame_elements={},
        area_elements=areas,
        frame_assignments={},
        area_assignments={"S1": "SLAB"},
        groups={},
        frame_auto_mesh={},
    )


def test_net_area_subtracts_a_coplanar_opening():
    md = _slab_with_opening()
    slab = md.area_elements["S1"]

    assert opening_overlap_area(md, slab) == pytest.approx(1.0)
    assert net_area(md, slab) == pytest.approx(15.0)  # 16 − 1


def test_selection_weight_excludes_the_opening_region():
    md = _slab_with_opening()
    sw = selection_weight(md)

    # 15 m² × 0.2 m × 2500 N/m³ = 7500 N; the opening area contributes nothing.
    assert sw.area_count == 1
    assert sw.area_weight == pytest.approx(15.0 * 0.2 * 2500.0)


def test_non_coplanar_opening_is_not_subtracted():
    md = _slab_with_opening()
    # Move the opening to a different plane (z = 1).
    for nid in ("o1", "o2", "o3", "o4"):
        md.nodes[nid].z = 1.0

    slab = md.area_elements["S1"]
    assert opening_overlap_area(md, slab) == pytest.approx(0.0)
    assert net_area(md, slab) == pytest.approx(16.0)


def test_opening_dropped_from_shell_creation():
    from fea_toolkit.opensees.preprocessor import preprocess_model

    mm = preprocess_model(_slab_with_opening(), {"create_shells": True, "verbose": False})

    assert "O1" in mm.loads_only_area_ids
    assert "O1" not in mm.area_element_types
    assert mm.area_element_types.get("S1") == "slab"


# ── Stage 3a — hole cutting in the structured meshers ──────────────


def _meshed_slab_with_opening():
    """A 4×4 slab with a centred 2×2 opening (1..3 in both axes, in-plane)."""
    nodes = {
        "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
        "2": Node(node_id="2", node_tag=2, x=4.0, y=0.0, z=0.0),
        "3": Node(node_id="3", node_tag=3, x=4.0, y=4.0, z=0.0),
        "4": Node(node_id="4", node_tag=4, x=0.0, y=4.0, z=0.0),
        "o1": Node(node_id="o1", node_tag=5, x=1.0, y=1.0, z=0.0),
        "o2": Node(node_id="o2", node_tag=6, x=3.0, y=1.0, z=0.0),
        "o3": Node(node_id="o3", node_tag=7, x=3.0, y=3.0, z=0.0),
        "o4": Node(node_id="o4", node_tag=8, x=1.0, y=3.0, z=0.0),
    }
    areas = {
        "S1": AreaElement(area_id="S1", area_tag=1, node_ids=["1", "2", "3", "4"]),
        "O1": AreaElement(
            area_id="O1", area_tag=2, node_ids=["o1", "o2", "o3", "o4"], opening=True
        ),
    }
    assigns = {"S1": "SLAB"}
    return areas, assigns, nodes


def test_point_in_polygon_2d():
    square = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]

    assert point_in_polygon_2d((2.0, 2.0), square) is True
    assert point_in_polygon_2d((5.0, 2.0), square) is False


def test_mesh_area_elements_drops_cells_inside_opening():
    from fea_toolkit.model.geometry import mesh_area_elements

    areas, assigns, nodes = _meshed_slab_with_opening()
    areas, assigns, nodes, _tag = mesh_area_elements(
        areas, assigns, nodes, {"S1": AreaMesh(auto_mesh=True, max_size=1.0)}
    )

    sub_ids = [aid for aid, ae in areas.items() if getattr(ae, "parent_id", None) == "S1"]
    # 4×4 grid = 16 cells, minus the 4 cells covering the centred 2×2 opening.
    assert len(sub_ids) == 12
    assert areas["S1"].inactive is True
    assert len(areas["S1"].child_ids) == 12
    # The opening area itself is never meshed or deactivated.
    assert areas["O1"].opening is True
    assert areas["O1"].inactive is False


def test_subdivide_area_mesh_drops_cells_inside_opening():
    from fea_toolkit.model.geometry import subdivide_area_mesh

    areas, assigns, nodes = _meshed_slab_with_opening()
    areas, assigns, nodes, _tag = subdivide_area_mesh(areas, assigns, nodes, n=4)

    sub_ids = [aid for aid, ae in areas.items() if getattr(ae, "parent_id", None) == "S1"]
    assert len(sub_ids) == 12
    assert areas["S1"].inactive is True
    assert areas["O1"].opening is True
    assert areas["O1"].inactive is False


def test_net_area_of_opening_area_is_zero():
    """An opening (void) area itself contributes zero net area."""
    md = _slab_with_opening()
    opening = md.area_elements["O1"]
    assert net_area(md, opening) == 0.0


def test_opening_overlap_area_unions_overlapping_openings():
    """Overlapping openings contribute their union area, not the sum."""
    nodes = {
        "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
        "2": Node(node_id="2", node_tag=2, x=4.0, y=0.0, z=0.0),
        "3": Node(node_id="3", node_tag=3, x=4.0, y=4.0, z=0.0),
        "4": Node(node_id="4", node_tag=4, x=0.0, y=4.0, z=0.0),
        "a1": Node(node_id="a1", node_tag=5, x=1.0, y=1.0, z=0.0),
        "a2": Node(node_id="a2", node_tag=6, x=3.0, y=1.0, z=0.0),
        "a3": Node(node_id="a3", node_tag=7, x=3.0, y=3.0, z=0.0),
        "a4": Node(node_id="a4", node_tag=8, x=1.0, y=3.0, z=0.0),
        "b1": Node(node_id="b1", node_tag=9, x=2.0, y=2.0, z=0.0),
        "b2": Node(node_id="b2", node_tag=10, x=3.5, y=2.0, z=0.0),
        "b3": Node(node_id="b3", node_tag=11, x=3.5, y=3.5, z=0.0),
        "b4": Node(node_id="b4", node_tag=12, x=2.0, y=3.5, z=0.0),
    }
    slab = AreaElement(area_id="S1", area_tag=1, node_ids=["1", "2", "3", "4"])
    areas = {
        "S1": slab,
        "A": AreaElement(area_id="A", area_tag=2, node_ids=["a1", "a2", "a3", "a4"], opening=True),
        "B": AreaElement(area_id="B", area_tag=3, node_ids=["b1", "b2", "b3", "b4"], opening=True),
    }
    md = SAPModelData(
        nodes=nodes,
        restraints={},
        materials={},
        sections={},
        frame_elements={},
        area_elements=areas,
        frame_assignments={},
        area_assignments={},
        groups={},
        frame_auto_mesh={},
    )
    # A = 4.0, B = 2.25, their overlap = 1.0 → union = 5.25 (not 6.25).
    assert opening_overlap_area(md, slab) == pytest.approx(5.25)
    assert net_area(md, slab) == pytest.approx(16.0 - 5.25)


def test_opening_overlap_area_handles_concave_parent():
    """An opening clipped against a concave parent gives the right area."""
    nodes = {
        "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
        "2": Node(node_id="2", node_tag=2, x=2.0, y=0.0, z=0.0),
        "3": Node(node_id="3", node_tag=3, x=2.0, y=1.0, z=0.0),
        "4": Node(node_id="4", node_tag=4, x=1.0, y=1.0, z=0.0),
        "5": Node(node_id="5", node_tag=5, x=1.0, y=2.0, z=0.0),
        "6": Node(node_id="6", node_tag=6, x=0.0, y=2.0, z=0.0),
        "o1": Node(node_id="o1", node_tag=7, x=0.0, y=0.0, z=0.0),
        "o2": Node(node_id="o2", node_tag=8, x=2.0, y=0.0, z=0.0),
        "o3": Node(node_id="o3", node_tag=9, x=2.0, y=2.0, z=0.0),
        "o4": Node(node_id="o4", node_tag=10, x=0.0, y=2.0, z=0.0),
    }
    l_shape = AreaElement(area_id="L", area_tag=1, node_ids=["1", "2", "3", "4", "5", "6"])
    opening = AreaElement(area_id="O", area_tag=2, node_ids=["o1", "o2", "o3", "o4"], opening=True)
    md = SAPModelData(
        nodes=nodes,
        restraints={},
        materials={},
        sections={},
        frame_elements={},
        area_elements={"L": l_shape, "O": opening},
        frame_assignments={},
        area_assignments={},
        groups={},
        frame_auto_mesh={},
    )
    # The 2×2 opening fully covers the L-shape (area 3), so the overlap is 3.
    assert opening_overlap_area(md, l_shape) == pytest.approx(3.0)


def test_conform_cell_to_openings_uses_boundary_not_centroid():
    cell = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]
    # Partially-covered cell (centroid outside) is kept.
    assert conform_cell_to_openings(cell, [[(1.0, 1.0), (3.0, 1.0), (3.0, 3.0), (1.0, 3.0)]]) == [
        cell
    ]
    # Fully-covered cell is dropped.
    assert conform_cell_to_openings(cell, [[(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]]) == []
    # Disjoint opening keeps the cell.
    assert conform_cell_to_openings(cell, [[(5.0, 5.0), (6.0, 5.0), (6.0, 6.0), (5.0, 6.0)]]) == [
        cell
    ]


def test_opening_nodes_kept_in_mesh_model_nodes():
    """Unconnected opening corners stay in ``MeshModel.nodes`` for net_area."""
    from fea_toolkit.opensees.preprocessor import preprocess_model

    mm = preprocess_model(_slab_with_opening(), {"create_shells": True, "verbose": False})

    # The opening's corner nodes survive in ``nodes`` (for net_area resolution)…
    for nid in ("o1", "o2", "o3", "o4"):
        assert nid in mm.nodes
    # …and are recorded as excluded from OpenSees node creation.
    assert mm.opening_node_ids == {"o1", "o2", "o3", "o4"}
