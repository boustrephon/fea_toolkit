"""Frame point loads (FRAME LOADS - POINT) → OpenSees eleLoad (P14)."""

import numpy as np
import pytest

from fea_toolkit.model.sap_data import FramePointLoad
from fea_toolkit.opensees import _loads as loads_mod
from fea_toolkit.opensees._loads import LoadMixin


class _RecordingOps:
    """Record ``eleLoad`` / ``load`` calls so the emission can be asserted."""

    def __init__(self):
        self.calls = []

    def eleLoad(self, *args):
        self.calls.append(("eleLoad", args))

    def load(self, *args):
        self.calls.append(("load", args))

    def timeSeries(self, *args):
        pass

    def pattern(self, *args):
        pass


def _mixin_with_point_load(direction, load_type="Force", value=5.0, rdist=0.5):
    from examples.sample_model import make_sample_model

    md = make_sample_model()
    eid = next(iter(md.frame_elements))
    elem = md.frame_elements[eid]
    pl = FramePointLoad(
        pattern="P",
        frame_id=eid,
        direction=direction,
        load_type=load_type,
        value=value,
        rdist=rdist,
        dist=0.0,
    )
    md.frame_point_loads = [pl]
    mixin = LoadMixin()
    mixin.mesh_model = md
    mixin.frame_tag_map = {eid: elem.elem_tag}
    mixin._point_load_totals = {}
    return mixin, pl


def _patch_axes(monkeypatch, mixin):
    identity = (
        np.array([1.0, 0.0, 0.0]),
        np.array([0.0, 1.0, 0.0]),
        np.array([0.0, 0.0, 1.0]),
    )
    monkeypatch.setattr(mixin, "get_local_axes", lambda elem: identity, raising=False)


def test_transverse_force_emits_beam_point(monkeypatch):
    rec = _RecordingOps()
    monkeypatch.setattr(loads_mod, "ops", rec)
    mixin, pl = _mixin_with_point_load("Z", value=5.0, rdist=0.5)
    _patch_axes(monkeypatch, mixin)

    total = mixin._apply_point_loads("P", [pl], mixin.mesh_model.frame_elements, 1.0)

    assert total == 5.0
    ele_loads = [c for c in rec.calls if c[0] == "eleLoad"]
    assert len(ele_loads) == 1
    assert ele_loads[0][1][4:] == (0.0, 5.0, 0.5, 0.0)  # Py, Pz, xL, Px
    assert not any(c[0] == "load" for c in rec.calls)


def test_axial_force_uses_beam_point(monkeypatch):
    rec = _RecordingOps()
    monkeypatch.setattr(loads_mod, "ops", rec)
    mixin, pl = _mixin_with_point_load("X", value=5.0)
    _patch_axes(monkeypatch, mixin)

    mixin._apply_point_loads("P", [pl], mixin.mesh_model.frame_elements, 1.0)

    ele_loads = [c for c in rec.calls if c[0] == "eleLoad"]
    assert len(ele_loads) == 1
    # Py=0, Pz=0, xL=0.5, Px=5.0 — the axial load rides the beamPoint form.
    assert ele_loads[0][1][4:] == (0.0, 0.0, 0.5, 5.0)
    assert not any(c[0] == "load" for c in rec.calls)


def test_point_moment_splits_to_both_ends(monkeypatch):
    rec = _RecordingOps()
    monkeypatch.setattr(loads_mod, "ops", rec)
    mixin, pl = _mixin_with_point_load("X", load_type="Moment", value=5.0)
    _patch_axes(monkeypatch, mixin)

    mixin._apply_point_loads("P", [pl], mixin.mesh_model.frame_elements, 1.0)

    assert not any(c[0] == "eleLoad" for c in rec.calls)
    loads = [c for c in rec.calls if c[0] == "load"]
    assert len(loads) == 2
    # Midpoint (rdist=0.5) splits the moment 50/50 between the two ends.
    assert loads[0][1][4:7] == (2.5, 0.0, 0.0)  # Mx, My, Mz at the I-end
    assert loads[1][1][4:7] == (2.5, 0.0, 0.0)  # Mx, My, Mz at the J-end


def test_point_load_total_is_tracked(monkeypatch):
    rec = _RecordingOps()
    monkeypatch.setattr(loads_mod, "ops", rec)
    mixin, pl = _mixin_with_point_load("Z", value=5.0)
    _patch_axes(monkeypatch, mixin)

    mixin._apply_point_loads("P", [pl], mixin.mesh_model.frame_elements, 1.0)

    assert mixin._point_load_totals["P"]["fz"] == 5.0


def test_applied_load_totals_includes_point_loads():
    """The load/reaction summary merges ``_point_load_totals`` (P14)."""
    from fea_toolkit.analysis.linear import _applied_load_totals

    class _FakeAB:
        def __init__(self):
            self._sw_load_totals = {}
            self._gravity_load_totals = {}
            self._joint_load_totals = {}
            self._dist_load_totals = {}
            self._point_load_totals = {
                "P": {"fx": 0.0, "fy": 0.0, "fz": 5.0, "mx": 0.0, "my": 0.0, "mz": 0.0}
            }

    applied = _applied_load_totals(_FakeAB())

    assert applied["P"]["fz"] == 5.0


def test_midpoint_torsion_moment_gives_equal_support_moments():
    """A midpoint LocalX point moment splits evenly between torsion-restrained ends."""
    import openseespy.opensees as ops

    from fea_toolkit.model.sap_data import (
        FrameElement,
        Material,
        Node,
        Restraint,
        SAPModelData,
        Section,
    )
    from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
    from fea_toolkit.opensees.preprocessor import preprocess_model

    md = SAPModelData(
        nodes={
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=5, y=0, z=0),
        },
        restraints={
            "1": Restraint([1, 1, 1, 1, 1, 1]),
            # Only the torsional DOF (R1) is restrained at the far end — the
            # member must keep a free DOF so the static solve is not singular.
            "2": Restraint([0, 0, 0, 1, 0, 0]),
        },
        materials={"Steel": Material(name="Steel", type="Steel", E_mod=2e11, G_mod=8e10)},
        sections={
            "UB100": Section(
                name="UB100",
                shape="I/Wide Flange",
                material="Steel",
                A=0.01,
                I33=1e-4,
                I22=1e-5,
                J=1e-5,
            )
        },
        frame_elements={"1": FrameElement(elem_id="1", elem_tag=1, node_i="1", node_j="2")},
        area_elements={},
        frame_assignments={"1": "UB100"},
        area_assignments={},
        groups={},
        frame_auto_mesh={},
        frame_point_loads=[
            FramePointLoad(
                pattern="DEAD",
                frame_id="1",
                direction="LocalX",
                load_type="Moment",
                value=100.0,
                rdist=0.5,
                dist=0.0,
            )
        ],
    )
    cfg = {"verbose": False, "use_elastic_sections": True, "split_elements": False}
    mm = preprocess_model(md, cfg)
    b = AnalysisBuilder(mm, cfg)
    try:
        b.build_domain()
        b.create_loads({"DEAD": 1.0})
        ops.constraints("Transformation")
        ops.numberer("RCM")
        ops.system("BandGeneral")
        ops.test("NormDispIncr", 1e-8, 30)
        ops.algorithm("Newton")
        ops.integrator("LoadControl", 1.0)
        ops.analysis("Static")
        assert ops.analyze(1) == 0
        ops.reactions()
        # A midpoint torque of 100 splits 50/50: equal support moments at the
        # two torsion-restrained ends.
        r_i = ops.nodeReaction(1, 4)
        r_j = ops.nodeReaction(2, 4)
        assert abs(r_i) == pytest.approx(50.0)
        assert abs(r_j) == pytest.approx(50.0)
        assert r_i == pytest.approx(r_j)
    finally:
        ops.wipe()


def test_corotational_offcenter_localz_force_resultant_and_moment(monkeypatch):
    """An off-centre LocalZ point force under Corotational falls back to
    consistent nodal loads that reproduce the resultant and its first moment."""
    from fea_toolkit.model.sap_data import (
        FrameElement,
        Material,
        Node,
        Restraint,
        SAPModelData,
        Section,
    )

    L = 5.0
    md = SAPModelData(
        nodes={
            "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
            "2": Node(node_id="2", node_tag=2, x=L, y=0.0, z=0.0),
        },
        restraints={
            "1": Restraint([1, 1, 1, 1, 1, 1]),
            "2": Restraint([1, 1, 1, 1, 1, 1]),
        },
        materials={"Steel": Material(name="Steel", type="Steel", E_mod=2e11, G_mod=8e10)},
        sections={
            "UB100": Section(
                name="UB100",
                shape="I/Wide Flange",
                material="Steel",
                A=0.01,
                I33=1e-4,
                I22=1e-5,
                J=1e-5,
            )
        },
        frame_elements={"1": FrameElement(elem_id="1", elem_tag=1, node_i="1", node_j="2")},
        area_elements={},
        frame_assignments={"1": "UB100"},
        area_assignments={},
        groups={},
        frame_auto_mesh={},
        frame_point_loads=[
            FramePointLoad(
                pattern="P",
                frame_id="1",
                direction="LocalZ",
                load_type="Force",
                value=10.0,
                rdist=0.3,
                dist=0.0,
            )
        ],
    )
    mixin = LoadMixin()
    mixin.mesh_model = md
    mixin.frame_tag_map = {"1": 1}
    mixin._point_load_totals = {}
    identity = (
        np.array([1.0, 0.0, 0.0]),
        np.array([0.0, 1.0, 0.0]),
        np.array([0.0, 0.0, 1.0]),
    )
    monkeypatch.setattr(mixin, "get_local_axes", lambda elem: identity, raising=False)
    monkeypatch.setattr(mixin, "_uses_corotational_transform", lambda: True)

    rec = _RecordingOps()
    monkeypatch.setattr(loads_mod, "ops", rec)

    mixin._apply_point_loads("P", [md.frame_point_loads[0]], md.frame_elements, 1.0)

    loads = [c for c in rec.calls if c[0] == "load"]
    assert len(loads) == 2
    fi, fj = loads[0][1], loads[1][1]
    fz_i, fz_j = fi[3], fj[3]
    my_i, my_j = fi[5], fj[5]

    # Force resultant: the nodal transverse forces sum to the applied load.
    assert fz_i + fz_j == pytest.approx(10.0)
    # First moment about node i (y-axis): the load at x = 0.3 * L yields
    # M_y = -10 * 0.3 * L, reproduced by the end moments plus the J-end force.
    assert my_i + my_j - L * fz_j == pytest.approx(-10.0 * 0.3 * L)
