"""Tests for SAFE soil/foundation spring resolution and emission."""

from __future__ import annotations

import pytest

from fea_toolkit.model.mesh_model import NodalSpring
from fea_toolkit.model.sap_data import (
    AreaElement,
    AreaSpring,
    FrameElement,
    LineSpring,
    Node,
    PointSpring,
    Restraint,
    SAPModelData,
)
from fea_toolkit.opensees.preprocessor import Preprocessor


def _node(nid, tag, x, y, z=0.0):
    return Node(node_id=nid, node_tag=tag, x=x, y=y, z=z)


def _model(
    *,
    nodes,
    frames=None,
    areas=None,
    restraints=None,
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
        nodes=nodes,
        restraints=restraints or {},
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
        units={"F": "N", "L": "m", "T": "C"},
    )


def _run(md):
    return Preprocessor({}).run(md)


def test_point_spring_resolves_to_nodal_spring():
    md = _model(
        nodes={"1": _node("1", 1, 0, 0, 0)},
        point_springs={
            "PS": PointSpring(
                name="PS", stiffness={"UX": 100.0, "UZ": 200.0}, nonlinear_option="Compression Only"
            )
        },
        point_assigns={"1": "PS"},
    )
    mm = _run(md)
    assert mm.point_springs == [
        NodalSpring(node_id="1", stiffness=[100.0, 0.0, 200.0, 0.0, 0.0, 0.0], compression_only={3})
    ]


def test_line_spring_distributes_to_endpoints():
    md = _model(
        nodes={"2": _node("2", 2, 0, 0, 0), "3": _node("3", 3, 4, 0, 0)},
        frames={"L1": FrameElement(elem_id="L1", elem_tag=1, node_i="2", node_j="3")},
        line_springs={
            "LS": LineSpring(name="LS", vertical_stiffness=10.0, rotational_stiffness=20.0)
        },
        line_assigns={"L1": "LS"},
    )
    mm = _run(md)
    # length 4 → half-length 2 → vertical 10*2=20 (UZ), rotational 20*2=40 (RX)
    vec = [0.0, 0.0, 20.0, 40.0, 0.0, 0.0]
    assert mm.point_springs == [
        NodalSpring(node_id="2", stiffness=vec),
        NodalSpring(node_id="3", stiffness=vec),
    ]


def test_area_spring_distributes_to_corners():
    md = _model(
        nodes={
            "4": _node("4", 4, 0, 0, 0),
            "5": _node("5", 5, 2, 0, 0),
            "6": _node("6", 6, 2, 2, 0),
            "7": _node("7", 7, 0, 2, 0),
        },
        areas={"A1": AreaElement(area_id="A1", area_tag=1, node_ids=["4", "5", "6", "7"])},
        area_springs={"AS": AreaSpring(name="AS", stiffness={"U3": 100.0})},
        area_assigns={"A1": "AS"},
    )
    mm = _run(md)
    # 2×2 square area 4 → per-node U3 = 100 * 4 / 4 = 100
    assert mm.point_springs == [
        NodalSpring(node_id=n, stiffness=[0.0, 0.0, 100.0, 0.0, 0.0, 0.0])
        for n in ("4", "5", "6", "7")
    ]


def test_stiffnesses_accumulate_per_node():
    # node "1" is both a point-spring target (UZ=100) and a corner of an area
    # with an area spring (UZ=100 → 100 per node) → UZ accumulates to 200.
    md = _model(
        nodes={
            "1": _node("1", 1, 0, 0, 0),
            "5": _node("5", 5, 2, 0, 0),
            "6": _node("6", 6, 2, 2, 0),
            "7": _node("7", 7, 0, 2, 0),
        },
        areas={"A1": AreaElement(area_id="A1", area_tag=1, node_ids=["1", "5", "6", "7"])},
        point_springs={"PS": PointSpring(name="PS", stiffness={"UZ": 100.0})},
        point_assigns={"1": "PS"},
        area_springs={"AS": AreaSpring(name="AS", stiffness={"U3": 100.0})},
        area_assigns={"A1": "AS"},
    )
    mm = _run(md)
    by_id = {s.node_id: s for s in mm.point_springs}
    assert by_id["1"].stiffness[2] == 200.0  # DOF 3 (UZ)


def test_compression_only_flag_propagates():
    md = _model(
        nodes={"1": _node("1", 1, 0, 0, 0)},
        point_springs={
            "PS": PointSpring(
                name="PS", stiffness={"UZ": 100.0}, nonlinear_option="Compression Only"
            )
        },
        point_assigns={"1": "PS"},
    )
    mm = _run(md)
    assert mm.point_springs[0].compression_only == {3}


def test_codec_round_trip():
    from fea_toolkit.io.model_codec import dict_to_model, model_to_dict

    md = _model(
        nodes={"1": _node("1", 1, 0, 0, 0)},
        point_springs={"PS": PointSpring(name="PS", stiffness={"UZ": 100.0})},
        point_assigns={"1": "PS"},
    )
    mm = _run(md)
    restored = dict_to_model(model_to_dict(mm))
    assert restored.point_springs == mm.point_springs


@pytest.fixture(autouse=True)
def _wipe_ops():
    yield
    import openseespy.opensees as ops

    ops.wipe()


def test_builder_emits_zerolength_supports():
    import openseespy.opensees as ops

    from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

    md = _model(
        nodes={"1": _node("1", 1, 0, 0, 0)},
        point_springs={"PS": PointSpring(name="PS", stiffness={"UX": 100.0, "UZ": 200.0})},
        point_assigns={"1": "PS"},
    )
    mm = _run(md)
    AnalysisBuilder(mm, {"verbose": False}).build_domain()
    # model node + one coincident fixed ground node
    assert len(ops.getNodeTags()) == 2
    # exactly one zeroLength support element
    assert len(ops.getEleTags()) == 1


def test_builder_skips_springs_when_disabled():
    import openseespy.opensees as ops

    from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

    md = _model(
        nodes={"1": _node("1", 1, 0, 0, 0)},
        point_springs={"PS": PointSpring(name="PS", stiffness={"UZ": 100.0})},
        point_assigns={"1": "PS"},
    )
    mm = _run(md)
    AnalysisBuilder(mm, {"verbose": False, "build_soil_springs": False}).build_domain()
    assert len(ops.getNodeTags()) == 1
    assert len(ops.getEleTags()) == 0


def test_builder_ent_for_compression_only():
    import openseespy.opensees as ops

    from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

    md = _model(
        nodes={"1": _node("1", 1, 0, 0, 0)},
        point_springs={
            "PS": PointSpring(
                name="PS", stiffness={"UZ": 100.0}, nonlinear_option="Compression Only"
            )
        },
        point_assigns={"1": "PS"},
    )
    mm = _run(md)
    # ENT (elastic-no-tension) material builds and the support still emits.
    AnalysisBuilder(mm, {"verbose": False, "soil_spring_compression_only": True}).build_domain()
    assert len(ops.getEleTags()) == 1


def test_ent_zerolength_resists_downward_not_uplift():
    """Ground-as-node-i: downward is compression (ENT resists), uplift is tension.

    Builds the spring domain through :class:`AnalysisBuilder` (exercising the
    production :meth:`_create_point_springs` path) and checks its ``zeroLength``
    node order against the ``ENT`` (elastic-no-tension) convention: the fixed
    ground node is node i and the model node is node j, so a downward (-Z)
    displacement of the model node is compression — which ENT resists — while
    uplift is tension, which it releases.
    """
    import openseespy.opensees as ops

    from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

    k = 1000.0
    load = 100.0

    def _build(sign: float) -> tuple[int, int]:
        ops.wipe()
        md = _model(
            nodes={"1": _node("1", 1, 0, 0, 0)},
            restraints={"1": Restraint(dofs=[1, 1, 0, 1, 1, 1])},  # only UZ free
            point_springs={
                "PS": PointSpring(
                    name="PS", stiffness={"UZ": k}, nonlinear_option="Compression Only"
                )
            },
            point_assigns={"1": "PS"},
        )
        mm = _run(md)
        AnalysisBuilder(mm, {"verbose": False, "soil_spring_compression_only": True}).build_domain()

        # Exactly one zeroLength support, ground node i → model node j.
        assert len(ops.getEleTags()) == 1
        ground_tag, model_tag = ops.eleNodes(ops.getEleTags()[0])
        assert ground_tag != model_tag
        assert ops.nodeCoord(ground_tag) == ops.nodeCoord(model_tag)

        # Apply the Z load to the model node (node j) and solve one step.
        ops.timeSeries("Linear", 1)
        ops.pattern("Plain", 1, 1)
        ops.load(model_tag, 0.0, 0.0, sign * load, 0.0, 0.0, 0.0)
        ops.system("BandGeneral")
        ops.numberer("RCM")
        ops.constraints("Plain")
        ops.integrator("LoadControl", 1.0)
        ops.algorithm("Newton")
        ops.analysis("Static")
        return int(ops.analyze(1)), model_tag

    # Downward: ENT resists compression → the model node settles by load/k.
    result, model_tag = _build(-1.0)
    assert result == 0
    assert ops.nodeDisp(model_tag, 3) == pytest.approx(-load / k)

    # Uplift: ENT releases tension → the Z DOF has no stiffness and the solve
    # cannot converge (the node would lift freely).
    result, _model_tag = _build(1.0)
    assert result < 0
