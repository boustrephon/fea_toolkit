"""Frame point loads (FRAME LOADS - POINT) → OpenSees eleLoad (P14)."""

import numpy as np

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
    assert ele_loads[0][1][4:] == (0.0, 5.0, 0.5)  # Py, Pz, xL
    assert not any(c[0] == "load" for c in rec.calls)


def test_axial_force_falls_back_to_nodal_load(monkeypatch):
    rec = _RecordingOps()
    monkeypatch.setattr(loads_mod, "ops", rec)
    mixin, pl = _mixin_with_point_load("X", value=5.0)
    _patch_axes(monkeypatch, mixin)

    mixin._apply_point_loads("P", [pl], mixin.mesh_model.frame_elements, 1.0)

    assert not any(c[0] == "eleLoad" for c in rec.calls)
    loads = [c for c in rec.calls if c[0] == "load"]
    assert len(loads) == 1
    assert loads[0][1][1:4] == (5.0, 0.0, 0.0)  # Fx, Fy, Fz at the near end


def test_point_moment_falls_back_to_nodal_moment(monkeypatch):
    rec = _RecordingOps()
    monkeypatch.setattr(loads_mod, "ops", rec)
    mixin, pl = _mixin_with_point_load("X", load_type="Moment", value=5.0)
    _patch_axes(monkeypatch, mixin)

    mixin._apply_point_loads("P", [pl], mixin.mesh_model.frame_elements, 1.0)

    assert not any(c[0] == "eleLoad" for c in rec.calls)
    loads = [c for c in rec.calls if c[0] == "load"]
    assert len(loads) == 1
    assert loads[0][1][4:7] == (5.0, 0.0, 0.0)  # Mx, My, Mz


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
