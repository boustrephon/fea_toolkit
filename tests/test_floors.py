"""Tests for :mod:`fea_toolkit.mesh.floors` (gmsh floor meshing)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("gmsh")

from fea_toolkit.mesh.floors import _chain_loops, mesh_floors
from fea_toolkit.model.sap_data import (
    AreaElement,
    FrameElement,
    Node,
    Restraint,
    ShellSection,
)

#: A reduced single-slab SAFE model (a slab + drop panel + an opening), parsed
#: by :mod:`fea_toolkit.io.safe_db_parser`.  The meshing integration test below
#: uses it so it has no developer-specific or external file dependency.
_SLAB_FIXTURE = Path(__file__).parent / "fixtures" / "sample.$sf"


def _node(nid, tag, x, y, z=0.0):
    return Node(node_id=nid, node_tag=tag, x=x, y=y, z=z)


def _area(aid, tag, node_ids, thickness=0.2, opening=False):
    area = AreaElement(area_id=aid, area_tag=tag, node_ids=node_ids, thickness=thickness)
    area.opening = opening
    return area


def _section(name, shape, thickness):
    return ShellSection(name=name, shape=shape, material="C45", thickness=thickness)


def _centroid(area, nodes):
    pts = [nodes[n] for n in area.node_ids]
    return (sum(p.x for p in pts) / len(pts), sum(p.y for p in pts) / len(pts))


def _subs(areas):
    return {k: a for k, a in areas.items() if "_floor_" in k}


def test_overlay_precedence_and_opening():
    """A drop panel (0.5) over a slab (0.2) with an opening cut out."""
    nodes = {
        "1": _node("1", 1, 0, 0),
        "2": _node("2", 2, 10, 0),
        "3": _node("3", 3, 10, 10),
        "4": _node("4", 4, 0, 10),
        "5": _node("5", 5, 3, 3),
        "6": _node("6", 6, 7, 3),
        "7": _node("7", 7, 7, 7),
        "8": _node("8", 8, 3, 7),
        "9": _node("9", 9, 4, 4),
        "10": _node("10", 10, 6, 4),
        "11": _node("11", 11, 6, 6),
        "12": _node("12", 12, 4, 6),
    }
    areas = {
        "SLAB": _area("SLAB", 1, ["1", "2", "3", "4"], thickness=0.2),
        "DROP": _area("DROP", 2, ["5", "6", "7", "8"], thickness=0.5),
        "OPEN": _area("OPEN", 3, ["9", "10", "11", "12"], thickness=0.0, opening=True),
    }
    assign = {"SLAB": "SLAB", "DROP": "DROP"}
    sections = {
        "SLAB": _section("SLAB", "Slab", 0.2),
        "DROP": _section("DROP", "Drop", 0.5),
    }
    new_areas, _, new_nodes, _, skipped = mesh_floors(
        areas, assign, nodes, floor_mesh_options={"max_size": 1.0}, sections=sections
    )
    assert skipped == []
    subs = _subs(new_areas)
    assert subs
    drop = [a for k, a in subs.items() if k.startswith("DROP_")]
    slab = [a for k, a in subs.items() if k.startswith("SLAB_")]
    assert drop and all(abs(a.thickness - 0.5) < 1e-9 for a in drop)
    assert slab and all(abs(a.thickness - 0.2) < 1e-9 for a in slab)
    assert areas["SLAB"].inactive and areas["DROP"].inactive
    for k, a in subs.items():
        cx, cy = _centroid(a, new_nodes)
        assert not (4 < cx < 6 and 4 < cy < 6), f"element {k} inside opening"


def test_adjacent_tiling_is_conforming():
    """Two side-by-side slabs share one conforming mesh edge."""
    nodes = {
        "1": _node("1", 1, 0, 0),
        "2": _node("2", 2, 5, 0),
        "3": _node("3", 3, 5, 5),
        "4": _node("4", 4, 0, 5),
        "5": _node("5", 5, 5, 0),
        "6": _node("6", 6, 10, 0),
        "7": _node("7", 7, 10, 5),
        "8": _node("8", 8, 5, 5),
    }
    areas = {
        "A": _area("A", 1, ["1", "2", "3", "4"], thickness=0.2),
        "B": _area("B", 2, ["5", "6", "7", "8"], thickness=0.2),
    }
    new_areas, _, _, _, skipped = mesh_floors(
        areas, {"A": "A", "B": "B"}, nodes, floor_mesh_options={"max_size": 2.0}
    )
    assert skipped == []
    subs = _subs(new_areas)
    assert subs
    assert areas["A"].inactive and areas["B"].inactive
    assert all(abs(a.thickness - 0.2) < 1e-9 for a in subs.values())
    assert all(len(a.node_ids) in (3, 4) for a in subs.values())


def test_beam_constraint_creates_nodes_along_beam():
    """A beam across the slab forces shell nodes onto the beam line."""
    nodes = {
        "1": _node("1", 1, 0, 0),
        "2": _node("2", 2, 10, 0),
        "3": _node("3", 3, 10, 10),
        "4": _node("4", 4, 0, 10),
        "5": _node("5", 5, 0, 5),
        "6": _node("6", 6, 10, 5),
    }
    areas = {"SLAB": _area("SLAB", 1, ["1", "2", "3", "4"], thickness=0.2)}
    frames = {"BEAM": FrameElement(elem_id="BEAM", elem_tag=1, node_i="5", node_j="6")}
    _, _, new_nodes, _, _ = mesh_floors(
        areas,
        {"SLAB": "SLAB"},
        nodes,
        floor_mesh_options={"max_size": 2.0},
        frame_elements=frames,
        frame_assignments={"BEAM": "1500x675"},
    )
    assert any(abs(nd.x - 5.0) < 1e-3 and abs(nd.y - 5.0) < 1e-3 for nd in new_nodes.values())


def test_column_point_is_embedded():
    """A restrained point in the slab becomes a mesh node."""
    nodes = {
        "1": _node("1", 1, 0, 0),
        "2": _node("2", 2, 10, 0),
        "3": _node("3", 3, 10, 10),
        "4": _node("4", 4, 0, 10),
        "COL": _node("COL", 5, 5, 5),
    }
    areas = {"SLAB": _area("SLAB", 1, ["1", "2", "3", "4"], thickness=0.2)}
    new_areas, _, new_nodes, _, _ = mesh_floors(
        areas,
        {"SLAB": "SLAB"},
        nodes,
        floor_mesh_options={"max_size": 3.0},
        restraints={"COL": Restraint(dofs=[1, 1, 1, 0, 0, 0])},
    )
    ids = {n for a in _subs(new_areas).values() for n in a.node_ids}
    assert any(abs(new_nodes[n].x - 5.0) < 1e-3 and abs(new_nodes[n].y - 5.0) < 1e-3 for n in ids)


def test_non_horizontal_area_is_skipped():
    nodes = {
        "1": _node("1", 1, 0, 0, 0),
        "2": _node("2", 2, 5, 0, 0),
        "3": _node("3", 3, 5, 5, 2),
        "4": _node("4", 4, 0, 5, 0),
    }
    areas = {"RAMP": _area("RAMP", 1, ["1", "2", "3", "4"], thickness=0.2)}
    _, _, _, _, skipped = mesh_floors(
        areas, {"RAMP": "RAMP"}, nodes, floor_mesh_options={"max_size": 1.0}
    )
    assert skipped == ["RAMP"]
    assert not areas["RAMP"].inactive


def test_requires_positive_element_size():
    nodes = {
        "1": _node("1", 1, 0, 0),
        "2": _node("2", 2, 5, 0),
        "3": _node("3", 3, 5, 5),
        "4": _node("4", 4, 0, 5),
    }
    areas = {"SLAB": _area("SLAB", 1, ["1", "2", "3", "4"])}
    with pytest.raises(ValueError):
        mesh_floors(areas, {"SLAB": "SLAB"}, nodes, floor_mesh_options={})


def test_chain_loops_reconstructs_a_square():
    edges = [
        ((0, 0, 0), (10, 0, 0)),
        ((10, 0, 0), (10, 10, 0)),
        ((10, 10, 0), (0, 10, 0)),
        ((0, 10, 0), (0, 0, 0)),
    ]
    loops = _chain_loops(edges)
    assert len(loops) == 1
    assert len(loops[0]) == 4


def test_parsed_slab_meshes_buildable():
    from fea_toolkit.io.safe_db_parser import SAFEDBParser

    md = SAFEDBParser(_SLAB_FIXTURE).parse().get_model_data()
    areas, assigns, _, _, _ = mesh_floors(
        md.area_elements,
        md.area_assignments,
        md.nodes,
        floor_mesh_options={"max_size": 5000.0},
        frame_elements=md.frame_elements,
        frame_assignments=md.frame_assignments,
        restraints=md.restraints,
        sections=md.sections,
    )
    active = {k: a for k, a in areas.items() if not a.inactive and not a.opening}
    assert active
    assert all(len(a.node_ids) in (3, 4) for a in active.values())
    assert all(k in assigns for k in active)


def test_preprocessor_routes_floor_meshing():
    """Preprocessor(create_shells=True) meshes a SAFE n-gon floor + splits the beam."""
    from fea_toolkit.model.sap_data import (
        Material,
        SAPModelData,
    )
    from fea_toolkit.opensees.preprocessor import Preprocessor

    nodes = {
        "1": _node("1", 1, 0, 0),
        "2": _node("2", 2, 10, 0),
        "3": _node("3", 3, 10, 10),
        "4": _node("4", 4, 0, 10),
        "5": _node("5", 5, 3, 3),
        "6": _node("6", 6, 6, 3),
        "7": _node("7", 7, 7, 6),
        "8": _node("8", 8, 5, 8),
        "9": _node("9", 9, 3, 6),
        "10": _node("10", 10, 0, 5),
        "11": _node("11", 11, 10, 5),
    }
    slab = _area("SLAB", 1, ["1", "2", "3", "4"], thickness=0.2)
    opening = _area("OPEN", 2, ["5", "6", "7", "8", "9"], thickness=0.0, opening=True)
    beam = FrameElement(elem_id="BEAM", elem_tag=1, node_i="10", node_j="11")
    md = SAPModelData(
        nodes=nodes,
        restraints={"4": Restraint(dofs=[1, 1, 1, 0, 0, 0])},
        materials={"C30": Material(name="C30", type="Concrete", E_mod=30e9)},
        sections={
            "SLAB": _section("SLAB", "Slab", 0.2),
            "BEAMSEC": _section("BEAMSEC", "Drop", 0.675),
        },
        frame_elements={"BEAM": beam},
        frame_assignments={"BEAM": "BEAMSEC"},
        area_elements={"SLAB": slab, "OPEN": opening},
        area_assignments={"SLAB": "SLAB"},
        groups={},
        frame_auto_mesh={},
        floor_mesh_options={"max_size": 1.5},
        units={"F": "N", "L": "m", "T": "C"},
    )
    mm = Preprocessor({"create_shells": True, "split_frames_at_shell_nodes": True}).run(md)
    active = {k: a for k, a in mm.area_elements.items() if not a.inactive and not a.opening}
    assert active
    assert all(len(a.node_ids) in (3, 4) for a in active.values())
    # the beam was split at shell nodes (child elements reference the BEAM parent)
    assert any(getattr(f, "parent_id", None) == "BEAM" for f in mm.frame_elements.values())
