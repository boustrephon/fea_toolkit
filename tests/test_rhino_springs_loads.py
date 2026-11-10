"""UserText documentation of springs and loads for the Rhino importer.

The Rhino geometry helpers in :mod:`fea_toolkit.rhino.geometry` are pure string
formatters (no Rhino API), so they are exercised headlessly.  This also checks
that :func:`fea_toolkit.model.source_resolver.resolve_model_source` carries the
SAFE spring/load fields through to the
:class:`~fea_toolkit.model.source_resolver.ResolvedSource` the importer hands to
the geometry functions.
"""

from fea_toolkit.model.sap_data import (
    AreaGravityLoad,
    AreaSpring,
    AreaUniformLoad,
    FrameDistributedLoad,
    FramePointLoad,
    JointLoad,
    LineSpring,
    Node,
    PointSpring,
    SAPModelData,
)
from fea_toolkit.model.source_resolver import resolve_model_source
from fea_toolkit.rhino.geometry import (
    area_load_summaries,
    area_spring_user_strings,
    frame_load_summaries,
    joint_load_user_strings,
    joint_spring_user_strings,
    line_spring_user_strings,
)


def _node(nid):
    return Node(node_id=nid, node_tag=int(nid), x=0.0, y=0.0, z=0.0)


def _model(**kwargs):
    base = {
        "nodes": {"1": _node("1"), "2": _node("2")},
        "restraints": {},
        "materials": {},
        "sections": {},
        "frame_elements": {},
        "area_elements": {},
        "frame_assignments": {},
        "area_assignments": {},
        "groups": {},
        "frame_auto_mesh": {},
    }
    base.update(kwargs)
    return SAPModelData(**base)


def _resolved():
    md = _model(
        point_springs={"PS": PointSpring(name="PS", stiffness={"UX": 100.0, "UZ": 200.0})},
        point_spring_assignments={"1": "PS"},
        area_springs={
            "AS": AreaSpring(
                name="AS", stiffness={"U3": 1500.0}, nonlinear_options={"U3": "Compression Only"}
            )
        },
        area_spring_assignments={"A1": "AS"},
        area_uniform_loads=[
            AreaUniformLoad(pattern="Live", area_id="A1", direction="Gravity", value=5.0)
        ],
        area_gravity_loads=[AreaGravityLoad(pattern="Dead", area_id="A1", multiplier_z=-1.0)],
        joint_loads=[JointLoad(pattern="Live", node_id="1", fz=-10.0)],
    )
    return resolve_model_source(md)


def test_resolved_source_carries_springs_and_loads():
    rs = _resolved()
    assert rs.point_spring_assignments == {"1": "PS"}
    assert rs.area_spring_assignments == {"A1": "AS"}
    assert len(rs.area_uniform_loads) == 1
    assert len(rs.area_gravity_loads) == 1
    assert len(rs.joint_loads) == 1
    assert rs.point_springs["PS"].stiffness == {"UX": 100.0, "UZ": 200.0}


def test_joint_spring_user_strings():
    rs = _resolved()
    assert joint_spring_user_strings(rs, "1") == {"SAP_Spring": "PS (UX=100, UZ=200)"}
    assert joint_spring_user_strings(rs, "2") == {}


def test_joint_spring_nonlinear_option_is_recorded():
    md = _model(
        point_springs={
            "PS2": PointSpring(
                name="PS2", stiffness={"UZ": 1.0}, nonlinear_option="Compression Only"
            )
        },
        point_spring_assignments={"2": "PS2"},
    )
    rs = resolve_model_source(md)
    assert joint_spring_user_strings(rs, "2") == {
        "SAP_Spring": "PS2 (UZ=1)",
        "SAP_Spring_Nonlinear": "Compression Only",
    }


def test_area_spring_user_strings():
    rs = _resolved()
    assert area_spring_user_strings(rs, "A1") == {
        "SAP_AreaSpring": "AS (U3=1500)",
        "SAP_AreaSpring_Nonlinear": "U3:Compression Only",
    }
    assert area_spring_user_strings(rs, "A2") == {}


def test_joint_load_user_strings():
    rs = _resolved()
    assert joint_load_user_strings(rs, "1") == {"SAP_JointLoads": "Live=(0,0,-10)"}
    assert joint_load_user_strings(rs, "2") == {}


def test_area_load_summaries():
    rs = _resolved()
    assert area_load_summaries(rs) == {
        "A1": {"SAP_Loads": "Live=5(Gravity)", "SAP_GravityLoads": "Dead=(0,0,-1)"}
    }
    assert area_load_summaries(_model()) == {}


def test_area_load_summaries_merge_multiple_patterns():
    md = _model(
        area_uniform_loads=[
            AreaUniformLoad(pattern="Live", area_id="A1", direction="Z", value=3.0),
            AreaUniformLoad(pattern="SDL", area_id="A1", direction="Gravity", value=1.5),
        ]
    )
    rs = resolve_model_source(md)
    assert area_load_summaries(rs) == {"A1": {"SAP_Loads": "Live=3(Z); SDL=1.5(Gravity)"}}


def test_line_spring_user_strings():
    md = _model(
        line_springs={"LS": LineSpring(name="LS", vertical_stiffness=1500.0)},
        line_spring_assignments={"F1": "LS"},
    )
    rs = resolve_model_source(md)
    assert line_spring_user_strings(rs, "F1") == {"SAP_LineSpring": "LS (vert=1500)"}
    assert line_spring_user_strings(rs, "F2") == {}


def test_line_spring_nonlinear_option_is_recorded():
    md = _model(
        line_springs={
            "LS": LineSpring(
                name="LS", vertical_stiffness=1500.0, nonlinear_option="Compression Only"
            )
        },
        line_spring_assignments={"F1": "LS"},
    )
    rs = resolve_model_source(md)
    assert line_spring_user_strings(rs, "F1") == {
        "SAP_LineSpring": "LS (vert=1500)",
        "SAP_LineSpring_Nonlinear": "Compression Only",
    }


def test_frame_load_summaries():
    md = _model(
        frame_dist_loads=[
            FrameDistributedLoad(
                pattern="Dead",
                frame_id="F1",
                direction="Z",
                load_type="Force",
                shape="Uniform",
                val_a=2.0,
                val_b=2.0,
                rdist_a=0.0,
                rdist_b=1.0,
                dist_a=0.0,
                dist_b=5.0,
            ),
            FrameDistributedLoad(
                pattern="Wind",
                frame_id="F1",
                direction="X",
                load_type="Force",
                shape="Linear",
                val_a=0.0,
                val_b=3.0,
                rdist_a=0.0,
                rdist_b=1.0,
                dist_a=0.0,
                dist_b=5.0,
            ),
        ],
        frame_point_loads=[
            FramePointLoad(
                pattern="Live",
                frame_id="F1",
                direction="Gravity",
                load_type="Force",
                value=10.0,
                rdist=0.5,
                dist=2.5,
            ),
        ],
    )
    rs = resolve_model_source(md)
    assert frame_load_summaries(rs) == {
        "F1": {
            "SAP_FrameDistLoads": "Dead=2(Z); Wind=0→3(X)",
            "SAP_FramePointLoads": "Live=10(Gravity)@0.5",
        }
    }
    assert frame_load_summaries(_model()) == {}
