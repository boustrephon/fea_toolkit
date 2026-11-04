"""Reading a node / line / area's soil springs, for the Inspector.

Qt-free and renderer-free.  These rows are what the Inspector appends to a
selected entity's own fields, so the lookup lives in one module
(:mod:`fea_toolkit.model.springs`) rather than beside the caller.
"""

from fea_toolkit.model.mesh_model import MeshModel, NodalSpring
from fea_toolkit.model.sap_data import (
    AreaElement,
    AreaSpring,
    FrameElement,
    LineSpring,
    Node,
    PointSpring,
    SAPModelData,
)
from fea_toolkit.model.springs import spring_rows


def _node(nid, tag=1):
    return Node(node_id=nid, node_tag=tag, x=0.0, y=0.0, z=0.0)


def _model(
    *,
    frames=None,
    areas=None,
    point_springs=None,
    point_assigns=None,
    line_springs=None,
    line_assigns=None,
    area_springs=None,
    area_assigns=None,
):
    frames = frames or {}
    areas = areas or {}
    return SAPModelData(
        nodes={"1": _node("1"), "2": _node("2"), "3": _node("3")},
        restraints={},
        materials={},
        sections={},
        frame_elements=frames,
        frame_assignments=dict.fromkeys(frames, ""),
        area_elements=areas,
        area_assignments=dict.fromkeys(areas, ""),
        groups={},
        frame_auto_mesh={},
        point_springs=point_springs or {},
        point_spring_assignments=point_assigns or {},
        line_springs=line_springs or {},
        line_spring_assignments=line_assigns or {},
        area_springs=area_springs or {},
        area_spring_assignments=area_assigns or {},
    )


def test_node_with_point_spring_reports_it_with_stiffness():
    md = _model(
        point_springs={"PS": PointSpring(name="PS", stiffness={"UX": 100.0, "UZ": 200.0})},
        point_assigns={"1": "PS"},
    )
    assert spring_rows(md, _node("1")) == [("Point Spring", "PS (UX=100, UZ=200)")]


def test_node_on_a_spring_supported_line_reports_the_line_spring():
    md = _model(
        frames={"L1": FrameElement(elem_id="L1", elem_tag=1, node_i="1", node_j="2")},
        line_springs={"LS": LineSpring(name="LS", vertical_stiffness=10.0)},
        line_assigns={"L1": "LS"},
    )
    assert spring_rows(md, _node("2")) == [("Line Spring", "L1: LS (vert 10)")]


def test_node_on_a_spring_supported_area_reports_the_area_spring():
    md = _model(
        areas={"A1": AreaElement(area_id="A1", area_tag=1, node_ids=["1", "2", "3"])},
        area_springs={"AS": AreaSpring(name="AS", stiffness={"U3": 1500.0})},
        area_assigns={"A1": "AS"},
    )
    assert spring_rows(md, _node("3")) == [("Area Spring", "A1: AS (U3=1500)")]


def test_node_reports_every_spring_it_touches_in_order():
    md = _model(
        frames={"L1": FrameElement(elem_id="L1", elem_tag=1, node_i="1", node_j="2")},
        areas={"A1": AreaElement(area_id="A1", area_tag=1, node_ids=["1", "2", "3"])},
        point_springs={"PS": PointSpring(name="PS", stiffness={"UZ": 500.0})},
        point_assigns={"1": "PS"},
        line_springs={"LS": LineSpring(name="LS", vertical_stiffness=10.0)},
        line_assigns={"L1": "LS"},
        area_springs={"AS": AreaSpring(name="AS", stiffness={"U3": 1500.0})},
        area_assigns={"A1": "AS"},
    )
    assert spring_rows(md, _node("1")) == [
        ("Point Spring", "PS (UZ=500)"),
        ("Line Spring", "L1: LS (vert 10)"),
        ("Area Spring", "A1: AS (U3=1500)"),
    ]


def test_a_frame_reports_its_line_spring():
    md = _model(
        frames={"L1": FrameElement(elem_id="L1", elem_tag=1, node_i="1", node_j="2")},
        line_springs={"LS": LineSpring(name="LS", vertical_stiffness=10.0)},
        line_assigns={"L1": "LS"},
    )
    assert spring_rows(md, md.frame_elements["L1"]) == [("Line Spring", "LS (vert 10)")]


def test_an_area_reports_its_area_spring():
    md = _model(
        areas={"A1": AreaElement(area_id="A1", area_tag=1, node_ids=["1", "2", "3"])},
        area_springs={"AS": AreaSpring(name="AS", stiffness={"U3": 1500.0})},
        area_assigns={"A1": "AS"},
    )
    assert spring_rows(md, md.area_elements["A1"]) == [("Area Spring", "AS (U3=1500)")]


def test_a_node_without_a_spring_reports_nothing():
    assert spring_rows(_model(), _node("1")) == []


def test_a_mesh_model_reports_its_resolved_nodal_spring():
    mm = MeshModel(
        nodes={},
        frame_elements={},
        frame_assignments={},
        area_elements={},
        area_assignments={},
        frame_dist_loads=[],
        restraints={},
        point_springs=[
            NodalSpring(
                node_id="5", stiffness=[0.0, 0.0, 1500.0, 0.0, 0.0, 0.0], compression_only={3}
            )
        ],
    )
    assert spring_rows(mm, _node("5")) == [("Soil Spring", "UZ=1500, compression-only candidate")]


def test_a_none_model_reports_nothing():
    assert spring_rows(None, _node("1")) == []
    assert spring_rows(_model(), None) == []
