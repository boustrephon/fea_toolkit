"""Regression tests for area-load distribution to frame edges.

Covers the nearest-supported-edge (45-degree yield-line) tributary
partition in ``convert_area_loads_to_edge_loads`` and the SAP Uniform
(Shell) nodal-pressure path in the AnalysisBuilder.

Regression context (2026-08): the previous centroid rule produced exactly
2x the panel load on fully-supported panels and silently dropped the load
on panels whose edges had no matching frame (net +78 % on the Pumphouse
Wind +X).  The new partition conserves the total — the sum of tributary
areas is exactly the panel area — for any combination of supported edges,
and warns when a panel has no supported edge at all.
"""

import pytest

from fea_toolkit.model.geometry import convert_area_loads_to_edge_loads
from fea_toolkit.model.sap_data import (
    AreaElement,
    AreaUniformLoad,
    FrameElement,
    Node,
)

# A 6 x 3 m rectangle in the X-Y plane.
_W, _H = 6.0, 3.0
_P = 2.0  # pressure (kN/m2) -> P*A = 36 kN
_LEN = {"f0": 6.0, "f1": 3.0, "f2": 6.0, "f3": 3.0}


def _rect_model():
    nodes = {
        "n0": Node(node_id="n0", node_tag=1, x=0, y=0, z=0),
        "n1": Node(node_id="n1", node_tag=2, x=_W, y=0, z=0),
        "n2": Node(node_id="n2", node_tag=3, x=_W, y=_H, z=0),
        "n3": Node(node_id="n3", node_tag=4, x=0, y=_H, z=0),
    }
    frames = {
        "f0": FrameElement(elem_id="f0", elem_tag=10, node_i="n0", node_j="n1"),  # bottom, long
        "f1": FrameElement(elem_id="f1", elem_tag=11, node_i="n1", node_j="n2"),  # right, short
        "f2": FrameElement(elem_id="f2", elem_tag=12, node_i="n2", node_j="n3"),  # top, long
        "f3": FrameElement(elem_id="f3", elem_tag=13, node_i="n3", node_j="n0"),  # left, short
    }
    areas = {
        "a1": AreaElement(
            area_id="a1", area_tag=20, node_ids=["n0", "n1", "n2", "n3"], thickness=0.1
        ),
    }
    return nodes, frames, areas


def _convert(loads, frames):
    nodes, _frames, areas = _rect_model()
    for fid in set(_frames) - set(frames):
        _frames.pop(fid)
    return convert_area_loads_to_edge_loads(nodes, areas, _frames, loads)


def _edge_forces(edge_loads):
    forces = {}
    for ld in edge_loads:
        L = _LEN[ld.frame_id]
        forces[ld.frame_id] = forces.get(ld.frame_id, 0.0) + ld.val_a * L
    return forces


def test_four_edges_conserves_total_and_45_split():
    """Fully-supported 2:1 panel -> long edges 75 %, short edges 25 %."""
    loads = [AreaUniformLoad(pattern="WIND", area_id="a1", direction="X", value=_P)]
    forces = _edge_forces(_convert(loads, ["f0", "f1", "f2", "f3"]))
    assert sum(forces.values()) == pytest.approx(_P * _W * _H, abs=1e-6)
    # 45-degree yield-line tributary: long edge 6.75 m2, short 2.25 m2
    assert forces["f0"] == pytest.approx(_P * 6.75, abs=0.5)
    assert forces["f2"] == pytest.approx(_P * 6.75, abs=0.5)
    assert forces["f1"] == pytest.approx(_P * 2.25, abs=0.5)
    assert forces["f3"] == pytest.approx(_P * 2.25, abs=0.5)


def test_two_opposite_edges_one_way():
    """Supported on two opposite edges -> one-way split, total conserved."""
    loads = [AreaUniformLoad(pattern="WIND", area_id="a1", direction="X", value=_P)]
    forces = _edge_forces(_convert(loads, ["f0", "f2"]))
    assert sum(forces.values()) == pytest.approx(_P * _W * _H, abs=1e-6)
    # Nearest-edge partition of two opposite edges is the midline split
    # (exact up to the lattice sampling resolution, ~0.3 % here).
    assert forces["f0"] == pytest.approx(_P * _W * _H / 2.0, abs=0.2)
    assert forces["f2"] == pytest.approx(_P * _W * _H / 2.0, abs=0.2)


def test_three_edges_partial_conserves():
    """One free edge -> remaining three edges absorb the full load."""
    loads = [AreaUniformLoad(pattern="WIND", area_id="a1", direction="X", value=_P)]
    forces = _edge_forces(_convert(loads, ["f0", "f2", "f3"]))
    assert sum(forces.values()) == pytest.approx(_P * _W * _H, abs=1e-6)


def test_single_edge_absorbs_all():
    """Only one supported edge -> it carries the entire panel load."""
    loads = [AreaUniformLoad(pattern="WIND", area_id="a1", direction="X", value=_P)]
    forces = _edge_forces(_convert(loads, ["f0"]))
    assert forces["f0"] == pytest.approx(_P * _W * _H, abs=1e-6)


def test_no_supported_edge_warns_and_drops():
    """Panel with no matching frame -> RuntimeWarning, no edge loads."""
    loads = [AreaUniformLoad(pattern="WIND", area_id="a1", direction="X", value=_P)]
    with pytest.warns(RuntimeWarning):
        edge_loads = _convert(loads, [])
    assert edge_loads == []


def test_one_way_flag_opposite_pair():
    """SAP OneWay distribution -> long edges each carry half the panel."""
    loads = [
        AreaUniformLoad(
            pattern="WIND",
            area_id="a1",
            direction="X",
            value=_P,
            to_frame=True,
            distribution="OneWay",
        )
    ]
    forces = _edge_forces(_convert(loads, ["f0", "f1", "f2", "f3"]))
    assert forces["f0"] == pytest.approx(_P * _W * _H / 2.0, abs=1e-6)
    assert forces["f2"] == pytest.approx(_P * _W * _H / 2.0, abs=1e-6)
    assert forces.get("f1", 0.0) == 0.0
    assert forces.get("f3", 0.0) == 0.0


# ── SAP Uniform (Shell) nodal-pressure path ────────────────────────


@pytest.fixture
def shell_model():
    """A 6 x 3 slab with a shell section and a uniform Z pressure."""
    import openseespy.opensees as ops

    ops.wipe()
    from fea_toolkit.model.sap_data import (
        Material,
        SAPModelData,
        ShellSection,
    )

    nodes = {
        "n0": Node(node_id="n0", node_tag=1, x=0, y=0, z=0),
        "n1": Node(node_id="n1", node_tag=2, x=_W, y=0, z=0),
        "n2": Node(node_id="n2", node_tag=3, x=_W, y=_H, z=0),
        "n3": Node(node_id="n3", node_tag=4, x=0, y=_H, z=0),
    }
    frames = {
        "f0": FrameElement(elem_id="f0", elem_tag=10, node_i="n0", node_j="n1"),
        "f1": FrameElement(elem_id="f1", elem_tag=11, node_i="n1", node_j="n2"),
        "f2": FrameElement(elem_id="f2", elem_tag=12, node_i="n2", node_j="n3"),
        "f3": FrameElement(elem_id="f3", elem_tag=13, node_i="n3", node_j="n0"),
    }
    areas = {
        "a1": AreaElement(
            area_id="a1", area_tag=20, node_ids=["n0", "n1", "n2", "n3"], thickness=0.2
        ),
    }
    materials = {
        "Concrete": Material(name="Concrete", type="Concrete", E_mod=3e10, unit_weight=24000)
    }
    sections = {
        "Slab200": ShellSection(
            name="Slab200",
            shape="Shell",
            material="Concrete",
            A=0,
            I33=0,
            I22=0,
            J=0,
            thickness=0.2,
        ),
    }
    return SAPModelData(
        nodes=nodes,
        restraints={},
        materials=materials,
        sections=sections,
        frame_elements=frames,
        area_elements=areas,
        frame_assignments={"f0": "Slab200", "f1": "Slab200", "f2": "Slab200", "f3": "Slab200"},
        area_assignments={"a1": "Slab200"},
        groups={},
        frame_auto_mesh={},
        area_uniform_loads=[AreaUniformLoad(pattern="WIND", area_id="a1", direction="Z", value=_P)],
        area_gravity_loads=[],
        units={"F": "kN", "L": "m", "T": "C"},
    )


def test_shell_nodal_pressure_path(shell_model):
    """create_shells=True -> pressure goes to the panel's own nodes."""
    import openseespy.opensees as ops

    from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
    from fea_toolkit.opensees.preprocessor import preprocess_model

    try:
        mm = preprocess_model(
            shell_model,
            {"create_shells": True, "element_type": "elasticBeamColumn", "verbose": False},
        )
        # With shells enabled and no loads-only selection, no perimeter edge
        # loads are generated for the plain Uniform (Shell) pressure.
        assert not any(ld.pattern == "WIND" for ld in mm.edge_loads_from_areas)

        ab = AnalysisBuilder(
            mm, {"create_shells": True, "element_type": "elasticBeamColumn", "verbose": False}
        )
        ab.build_domain()
        ab.create_loads({"WIND": 1.0})
        # The applied total equals P*A exactly.
        assert ab._dist_load_totals["WIND"]["fz"] == pytest.approx(_P * _W * _H, abs=1e-6)
        assert ab._dist_load_totals["WIND"]["fx"] == pytest.approx(0.0, abs=1e-9)
    finally:
        ops.wipe()
