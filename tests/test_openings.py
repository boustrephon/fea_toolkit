"""Openings: net shell area and shell-creation exclusion (``model/openings.py``)."""

import pytest

from fea_toolkit.model.openings import (
    conform_cell_to_openings,
    cut_cell,
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
    assert "O1" not in mm.area_element_properties
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


def test_preprocess_subdivision_drops_cells_inside_opening():
    """The production ``subdivide_shells`` path also drops opening cells."""
    from fea_toolkit.opensees.preprocessor import preprocess_model

    areas, assigns, nodes = _meshed_slab_with_opening()
    md = SAPModelData(
        nodes=nodes,
        restraints={},
        materials={"Concrete": Material(name="Concrete", type="Concrete", unit_weight=2500.0)},
        sections={
            "SLAB": ShellSection(name="SLAB", shape="Shell", material="Concrete", thickness=0.2)
        },
        frame_elements={},
        area_elements=areas,
        frame_assignments={},
        area_assignments=assigns,
        groups={},
        frame_auto_mesh={},
    )

    mm = preprocess_model(
        md,
        {"create_shells": True, "subdivide_shells": 4, "verbose": False},
    )

    children = [ae for ae in mm.area_elements.values() if getattr(ae, "parent_id", None) == "S1"]
    # 4×4 grid = 16 cells, minus the 4 cells covering the centred 2×2 opening.
    assert len(children) == 12

    # None of the surviving subdivided cells may lie inside the opening.
    opening = [(1.0, 1.0), (3.0, 1.0), (3.0, 3.0), (1.0, 3.0)]
    for ae in children:
        xs = [mm.nodes[nid].x for nid in ae.node_ids]
        ys = [mm.nodes[nid].y for nid in ae.node_ids]
        centroid = (sum(xs) / len(xs), sum(ys) / len(ys))
        assert point_in_polygon_2d(centroid, opening) is False


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


def test_opening_nodes_moved_to_orphan_nodes_and_net_area_resolves():
    """Unconnected opening corners move to ``orphan_nodes``; net_area resolves them."""
    from fea_toolkit.opensees.preprocessor import preprocess_model

    mm = preprocess_model(_slab_with_opening(), {"create_shells": True, "verbose": False})

    # The opening's corner nodes are moved to ``orphan_nodes`` (not ``nodes``)…
    for nid in ("o1", "o2", "o3", "o4"):
        assert nid in mm.orphan_nodes
        assert nid not in mm.nodes
    # …and are recorded as opening corners.
    assert mm.opening_node_ids == {"o1", "o2", "o3", "o4"}

    # net_area still subtracts the opening by resolving its vertices from
    # ``orphan_nodes`` (16 m² slab − 1 m² opening).
    slab = mm.area_elements["S1"]
    assert net_area(mm, slab) == pytest.approx(15.0)


# ── Exact cut-out — convex half-plane chop (boolean) ──────────────


def _area2d(poly):
    """Absolute shoelace area of a 2-D polygon."""
    s = 0.0
    for i in range(len(poly)):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % len(poly)]
        s += x0 * y1 - x1 * y0
    return abs(0.5 * s)


def _is_convex_ccw(poly):
    """True when every consecutive cross product is non-negative (convex CCW)."""
    for i in range(len(poly)):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % len(poly)]
        cx, cy = poly[(i + 2) % len(poly)]
        cross = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        if cross < -1e-9:
            return False
    return True


def test_clip_by_halfplane_keeps_either_side():
    from fea_toolkit.model.openings import _clip_by_halfplane

    sq = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]  # CCW
    inside = _clip_by_halfplane(sq, (2.0, 0.0), (2.0, 4.0), keep_inside=True)
    outside = _clip_by_halfplane(sq, (2.0, 0.0), (2.0, 4.0), keep_inside=False)
    assert _area2d(inside) == pytest.approx(8.0)  # left half (x ≤ 2)
    assert _area2d(outside) == pytest.approx(8.0)  # right half (x ≥ 2)


def test_convex_difference_square_minus_triangle():
    from fea_toolkit.model.openings import _convex_difference

    cell = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]
    tri = [(1.0, 1.0), (3.0, 1.0), (1.0, 3.0)]  # area 2, CCW
    pieces = _convex_difference(cell, tri)
    assert sum(_area2d(p) for p in pieces) == pytest.approx(14.0)
    for p in pieces:
        assert _is_convex_ccw(p)


def test_cut_cell_centred_opening_yields_frame():
    cell = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]
    opening = [(1.0, 1.0), (3.0, 1.0), (3.0, 3.0), (1.0, 3.0)]
    pieces = cut_cell(cell, [opening])
    assert sum(_area2d(p) for p in pieces) == pytest.approx(12.0)  # 16 − 4
    for p in pieces:
        assert _is_convex_ccw(p)


def test_cut_cell_disjoint_opening_returns_cell_unchanged():
    cell = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]
    opening = [(5.0, 5.0), (6.0, 5.0), (6.0, 6.0), (5.0, 6.0)]
    assert cut_cell(cell, [opening]) == [cell]


def test_cut_cell_disjoint_opening_crossing_extended_edge_returns_cell():
    """A disjoint triangle whose *extended* edge line crosses the cell is not
    a subtraction — the cell is returned unchanged, not spuriously split."""
    cell = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]
    # Triangle entirely to the right of the cell; its edge (4.5,1)→(6,1) has an
    # extended line y=1 that crosses the cell, but the triangle itself is disjoint.
    opening = [(4.5, 0.0), (4.5, 1.0), (6.0, 1.0)]
    assert cut_cell(cell, [opening]) == [cell]


def test_cut_cell_cell_vertex_on_opening_edge_has_no_repeated_vertices():
    """Cell vertices on an opening edge must not produce repeated vertices."""
    cell = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]
    # Lower-left half triangle: cell vertices (4,0) and (0,4) lie on the
    # diagonal opening edge (4,0)→(0,4).
    opening = [(0.0, 0.0), (4.0, 0.0), (0.0, 4.0)]
    pieces = cut_cell(cell, [opening])
    assert sum(_area2d(p) for p in pieces) == pytest.approx(8.0)  # cell − tri
    for piece in pieces:
        for i in range(len(piece)):
            assert piece[i] != piece[(i + 1) % len(piece)]


def test_cut_cell_fully_covered_returns_empty():
    cell = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]
    opening = [(-1.0, -1.0), (5.0, -1.0), (5.0, 5.0), (-1.0, 5.0)]
    assert cut_cell(cell, [opening]) == []


def test_cut_cell_corner_overlap_conserves_area():
    cell = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]
    opening = [(2.0, 2.0), (6.0, 2.0), (6.0, 6.0), (2.0, 6.0)]  # covers NE corner
    pieces = cut_cell(cell, [opening])
    # overlap = 2×2 = 4 → cell − overlap = 12
    assert sum(_area2d(p) for p in pieces) == pytest.approx(12.0)
    for p in pieces:
        assert _is_convex_ccw(p)


def test_cut_cell_concave_opening():
    cell = [(0.0, 0.0), (6.0, 0.0), (6.0, 6.0), (0.0, 6.0)]
    # L-shaped (concave) opening: a 4×4 square with a 2×2 bite out of the NE corner.
    opening = [(1.0, 1.0), (5.0, 1.0), (5.0, 3.0), (3.0, 3.0), (3.0, 5.0), (1.0, 5.0)]
    pieces = cut_cell(cell, [opening])
    # opening area = 16 − 4 = 12 → cell − opening = 36 − 12 = 24
    assert sum(_area2d(p) for p in pieces) == pytest.approx(24.0)
    for p in pieces:
        assert _is_convex_ccw(p)


def test_cut_cell_overlapping_openings_subtract_union():
    cell = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]
    a = [(1.0, 1.0), (3.0, 1.0), (3.0, 3.0), (1.0, 3.0)]  # area 4
    b = [(2.0, 2.0), (3.5, 2.0), (3.5, 3.5), (2.0, 3.5)]  # area 2.25, overlap 1
    pieces = cut_cell(cell, [a, b])
    # union = 4 + 2.25 − 1 = 5.25 → cell − union = 10.75
    assert sum(_area2d(p) for p in pieces) == pytest.approx(10.75)
    for p in pieces:
        assert _is_convex_ccw(p)


def test_cut_cell_clockwise_cell_is_normalised():
    cell = [(0.0, 0.0), (0.0, 4.0), (4.0, 4.0), (4.0, 0.0)]  # CW
    opening = [(1.0, 1.0), (3.0, 1.0), (3.0, 3.0), (1.0, 3.0)]
    pieces = cut_cell(cell, [opening])
    assert sum(_area2d(p) for p in pieces) == pytest.approx(12.0)
    for p in pieces:
        assert _is_convex_ccw(p)


def test_subdivide_area_mesh_trims_cells_around_opening():
    """Partial-overlap cells are trimmed to the opening boundary (area conserved)."""
    from fea_toolkit.model.geometry import subdivide_area_mesh

    md = _slab_with_opening()
    areas, assigns, nodes = md.area_elements, md.area_assignments, dict(md.nodes)
    areas, assigns, nodes, _tag = subdivide_area_mesh(areas, assigns, nodes, n=4)

    children = [ae for ae in areas.values() if getattr(ae, "parent_id", None) == "S1"]
    total = sum(_area2d([(nodes[nid].x, nodes[nid].y) for nid in ae.node_ids]) for ae in children)
    assert total == pytest.approx(15.0)  # 16 m² slab − 1 m² opening

    # Boundary-node reuse: the opening corners become shared mesh nodes.
    for nid in ("o1", "o2", "o3", "o4"):
        assert any(nid in ae.node_ids for ae in children)


def test_preprocess_trims_and_reuses_opening_corners():
    """The meshed model keeps the opening corners as shared shell nodes."""
    from fea_toolkit.opensees.preprocessor import preprocess_model

    mm = preprocess_model(
        _slab_with_opening(), {"create_shells": True, "subdivide_shells": 4, "verbose": False}
    )

    children = [ae for ae in mm.area_elements.values() if getattr(ae, "parent_id", None) == "S1"]
    for nid in ("o1", "o2", "o3", "o4"):
        assert nid in mm.nodes
        assert nid not in mm.orphan_nodes
        assert any(nid in ae.node_ids for ae in children)
