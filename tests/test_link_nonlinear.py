"""Nonlinear link materials — component-level tests (P49 Phase 2).

Phase 2 emits the nonlinear SAP2000/ETABS link types as OpenSees materials.
The *one-sided / backbone family* (Gap, Hook, bilinear, multilinear, friction
dampers) is emitted with a single ``Hysteretic`` backbone whose pinching /
damage knobs are pinned neutral — see ``docs/nonlinear_link_plan.md``.  The
Plastic (Wen) link emits ``Steel02`` (smooth, default) or ``Hysteretic``
(sharp bilinear) via ``wen_model``.

The backbones are built by the shared helpers in
``fea_toolkit.opensees._links``, so the emitter and these tests cannot drift
apart.  Two reference materials pin the "one model" backbone to the
purpose-built special cases OpenSees ships:

* ``ENT`` (elastic-no-tension) — reference for a **compression-only** Gap;
* ``ElasticPP`` (``epsyN = 0``) — *monotonic* reference for a **tension-only**
  Hook.

The 1-DOF truss (``ndm=1, ndf=1``) rig lives in
``fea_toolkit.opensees._material_rig`` (shared with the ``check_hysteretic``
verb); the production workflow stays 3-D (``.clinerules`` §3.11).
"""

import math

import openseespy.opensees as ops
import pytest

from fea_toolkit.opensees._links import (
    _hysteretic_bilinear_args,
    _hysteretic_one_sided_args,
    _wen_r0,
)
from fea_toolkit.opensees._material_rig import drive_truss_history

E = 1000.0  # reference spring stiffness (force per unit displacement)
EMAX = 1.0  # backbone extent — beyond every test displacement
TINY = E * 1.0e-6  # residual stiffness in the released direction


def _emit_ent(tag: int) -> None:
    """Elastic-no-tension — the reference compression-only (Gap) material."""
    ops.uniaxialMaterial("ENT", tag, E)


def _emit_hysteretic_compression_only(tag: int) -> None:
    """Compression-only ``Hysteretic`` backbone: slope ``E``, tension released."""
    ops.uniaxialMaterial("Hysteretic", tag, *_hysteretic_one_sided_args(E, 0.0, "gap"))


def _emit_hysteretic_tension_only(tag: int) -> None:
    """Tension-only ``Hysteretic`` backbone: slope ``E``, compression released."""
    ops.uniaxialMaterial("Hysteretic", tag, *_hysteretic_one_sided_args(E, 0.0, "hook"))


def _emit_elasticpp_tension_only(tag: int) -> None:
    """The ``ElasticPP`` monotonic stand-in for a Hook: ``epsyN = 0``."""
    ops.uniaxialMaterial("ElasticPP", tag, E, EMAX, 0.0)


def _axial_force(emit, u: float) -> float:
    """Axial force of a 1-DOF truss driven to displacement ``u``.

    The imposed displacement is enforced with ``Penalty`` constraints so the
    free-DOF system never goes singular when the material's tangent stiffness
    is zero (the released direction).  Returns the truss ``axialForce``
    (positive = tension).
    """
    ops.wipe()
    ops.model("basic", "-ndm", 1, "-ndf", 1)
    ops.node(1, 0.0)
    ops.node(2, 1.0)
    ops.fix(1, 1)
    emit(1)
    ops.element("truss", 1, 1, 2, 1.0, 1)
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    ops.sp(2, 1, u)
    ops.constraints("Penalty", 1.0e14, 1.0e14)
    ops.numberer("RCM")
    ops.system("BandGeneral")
    ops.test("NormDispIncr", 1.0e-12, 50)
    ops.algorithm("Newton")
    ops.integrator("LoadControl", 1.0)
    ops.analysis("Static")
    assert ops.analyze(1) == 0
    return float(ops.eleResponse(1, "axialForce")[0])


def _axial_forces(emit, displacements: list[float]) -> list[float]:
    """Axial forces of a 1-DOF truss driven through a displacement history.

    Delegates to :func:`fea_toolkit.opensees._material_rig.drive_truss_history`
    — the shared rig the ``check_hysteretic_backbone`` verb uses for the cyclic
    (D7) check, so the emitter and the check cannot drift apart.
    """
    forces = drive_truss_history(ops, emit, displacements)
    assert forces is not None
    return forces


def test_hysteretic_compression_only_matches_ent():
    """``Hysteretic`` compression-only reproduces ``ENT`` (the Gap special case).

    In compression both give ``F = E·u``; in tension both release.  This pins
    the "one model" backbone to the purpose-built reference it replaces.
    """
    try:
        for u in (-0.05, -0.02, -0.01, 0.0):
            f_ent = _axial_force(_emit_ent, u)
            f_hyst = _axial_force(_emit_hysteretic_compression_only, u)
            assert f_hyst == pytest.approx(f_ent, abs=1.0e-6)
            if u < 0.0:
                assert f_hyst == pytest.approx(E * u, rel=1.0e-6)
        # Released (tension) direction: ENT is exactly zero, Hysteretic ~zero.
        f_ent = _axial_force(_emit_ent, 0.01)
        f_hyst = _axial_force(_emit_hysteretic_compression_only, 0.01)
        assert f_ent == pytest.approx(0.0, abs=1.0e-9)
        assert abs(f_hyst) <= TINY * 0.01 * 2  # residual-stiffness bound
    finally:
        ops.wipe()


def test_hysteretic_tension_only_matches_elasticpp_monotonic():
    """``Hysteretic`` tension-only matches ``ElasticPP(epsyN=0)`` monotonically.

    The Hook's tension branch is ``E``; compression is released.  Monotonic
    agreement documents that ``ElasticPP`` is a *monotonic* stand-in only — it
    does not share ``Hysteretic``'s clean cyclic unloading, which is the
    subject of the one-time cyclic check in ``docs/nonlinear_link_plan.md``.
    """
    try:
        for u in (0.01, 0.02, 0.05):
            f_hyst = _axial_force(_emit_hysteretic_tension_only, u)
            f_epp = _axial_force(_emit_elasticpp_tension_only, u)
            assert f_hyst == pytest.approx(f_epp, abs=1.0e-6)
            assert f_hyst == pytest.approx(E * u, rel=1.0e-6)
        # Released (compression) direction: both ~zero.
        f_hyst = _axial_force(_emit_hysteretic_tension_only, -0.01)
        f_epp = _axial_force(_emit_elasticpp_tension_only, -0.01)
        assert f_epp == pytest.approx(0.0, abs=1.0e-9)
        assert abs(f_hyst) <= TINY * 0.01 * 2
    finally:
        ops.wipe()


# ── Phase 2b(i): backbone helpers + nonlinear material behaviour ────────


def test_hysteretic_bilinear_backbone_args():
    """The bilinear backbone helper builds the expected ``Hysteretic`` args."""
    args = _hysteretic_bilinear_args(1000.0, 50.0, 0.1)
    assert args[0:2] == [50.0, 0.05]  # yield point (s1p, e1p) = (fy, fy/ke)
    assert args[-5:] == [1.0, 1.0, 0.0, 0.0, 0.0]  # neutral pinch/damage
    s3, e3 = args[4], args[5]
    s2, e2 = args[2], args[3]
    assert (s3 - s2) / (e3 - e2) == pytest.approx(0.1 * 1000.0)  # post-yield slope


def test_wen_r0_mapping():
    """``n → R0`` is monotonic and clamped to the Steel02 band."""
    assert _wen_r0(1.0) == 10.0
    assert _wen_r0(2.0) == 15.0
    assert _wen_r0(4.0) == 25.0
    assert _wen_r0(20.0) == 30.0  # clamped


def test_gap_with_open_offset():
    """A Gap carries zero force until the initial ``open`` gap closes."""
    gap = 0.01

    def emit(tag):
        ops.uniaxialMaterial("Hysteretic", tag, *_hysteretic_one_sided_args(E, gap, "gap"))

    try:
        assert abs(_axial_force(emit, 0.005)) <= E * 1e-6 * 0.005 * 2  # tension
        assert abs(_axial_force(emit, -0.005)) <= E * 1e-6 * 0.005 * 2  # gap not closed
        f = _axial_force(emit, -0.03)
        assert f == pytest.approx(E * (-0.03 + gap), rel=1e-6)
    finally:
        ops.wipe()


def test_steel02_wen_smooth_yield():
    """Steel02 (the Wen default) is elastic, then yields toward ``ratio * ke``."""
    fy, ke, ratio = 50.0, E, 0.1

    def emit(tag):
        ops.uniaxialMaterial(
            "Steel02", tag, fy, ke, ratio, _wen_r0(2.0), 0.925, 0.15, 0.0, 1.0, 0.0, 1.0, 0.0
        )

    try:
        ey = fy / ke
        assert _axial_force(emit, 0.02) == pytest.approx(ke * 0.02, rel=1e-6)
        f = _axial_force(emit, 4 * ey)  # well past yield
        assert f == pytest.approx(fy + ratio * ke * (4 * ey - ey), rel=2e-2)
        assert f > fy  # it did yield
    finally:
        ops.wipe()


def test_steel02_wen_reversal_stress_is_finite():
    """Steel02 (a2/a4 = 1, no isotropic hardening) stays finite through a reversal."""
    fy, ke, ratio = 50.0, E, 0.1

    def emit(tag):
        ops.uniaxialMaterial(
            "Steel02", tag, fy, ke, ratio, _wen_r0(2.0), 0.925, 0.15, 0.0, 1.0, 0.0, 1.0, 0.0
        )

    try:
        forces = _axial_forces(emit, [0.05, 0.01, 0.05])
        assert forces[0] > 0.0  # active loading transmits force
        assert all(math.isfinite(f) for f in forces)  # finite stress
    finally:
        ops.wipe()


def test_hysteretic_wen_bilinear_yield():
    """``wen_model='hysteretic'`` gives an exact bilinear backbone."""
    fy, ke, ratio = 50.0, E, 0.1

    def emit(tag):
        ops.uniaxialMaterial("Hysteretic", tag, *_hysteretic_bilinear_args(ke, fy, ratio))

    try:
        ey = fy / ke
        assert _axial_force(emit, 0.02) == pytest.approx(ke * 0.02, rel=1e-6)
        assert _axial_force(emit, 0.1) == pytest.approx(fy + ratio * ke * (0.1 - ey), rel=1e-6)
    finally:
        ops.wipe()


def test_hysteretic_cyclic_neutral_defaults():
    """D7: a full cycle shows no pinching and no strength degradation."""
    fy, ke, ratio = 50.0, E, 0.1
    peak = fy + ratio * ke * (0.1 - fy / ke)

    def emit(tag):
        ops.uniaxialMaterial("Hysteretic", tag, *_hysteretic_bilinear_args(ke, fy, ratio))

    try:
        # Sample the reload branch (not just the extrema) so a pinched loop
        # cannot pass on peak values alone.
        history = [0.1, 0.0, -0.1, 0.0, 0.05, 0.1]
        forces = _axial_forces(emit, history)
        assert forces[0] == pytest.approx(peak, rel=1e-6)
        assert forces[2] == pytest.approx(-peak, rel=1e-6)
        assert forces[5] == pytest.approx(peak, rel=1e-6)  # no degradation

        # A pinched loop (pinchX/pinchY < 1) lands at the same extrema but
        # diverges on the reload branch — the intermediate samples are what
        # catch it.
        def emit_pinched(tag):
            args = _hysteretic_bilinear_args(ke, fy, ratio)
            args[-5] = args[-4] = 0.5
            ops.uniaxialMaterial("Hysteretic", tag, *args)

        pinched = _axial_forces(emit_pinched, history)
        assert pinched[0] == pytest.approx(peak, rel=1e-6)  # same extrema
        assert pinched[5] == pytest.approx(peak, rel=1e-6)
        assert any(abs(f - p) > 1e-6 for f, p in zip(forces[3:], pinched[3:]))
    finally:
        ops.wipe()


def test_wen_steel02_emitter_args(monkeypatch):
    """The Wen emitter passes the correct ``Steel02`` material args."""
    import fea_toolkit.opensees._links as links_mod
    from fea_toolkit.model.sap_data import LinkProperty, WenParams

    calls = []
    monkeypatch.setattr(links_mod.ops, "uniaxialMaterial", lambda *a: calls.append(a))

    prop = LinkProperty(
        name="W",
        link_type="Plastic (Wen)",
        ke=[1000.0] * 6,
        nonlinear_params=WenParams(fy=[50.0] * 6, ratio=0.1, n=2.0),
    )
    tag = links_mod.LinkMixin._wen_dof_mat(links_mod.LinkMixin(), prop, 0, "steel02", {})
    assert calls == [
        ("Steel02", tag, 50.0, 1000.0, 0.1, 15.0, 0.925, 0.15, 0.0, 1.0, 0.0, 1.0, 0.0)
    ]


def test_damper_dof_material_selection(monkeypatch):
    """A damper DOF emits ``Viscous`` (``ce > 0``) or ``Parallel`` (``ke > 0`` too)."""
    import fea_toolkit.opensees._links as links_mod
    from fea_toolkit.model.sap_data import DamperParams, LinkProperty

    calls = []
    monkeypatch.setattr(links_mod.ops, "uniaxialMaterial", lambda *a: calls.append(a))

    pure = LinkProperty(
        name="D",
        link_type="Damper",
        ke=[0.0] * 6,
        ce=[50.0, 0, 0, 0, 0, 0],
        nonlinear_params=DamperParams(cexp=0.3),
    )
    tag = links_mod.LinkMixin._damper_dof_mat(links_mod.LinkMixin(), pure, 0, {})
    assert calls == [("Viscous", tag, 50.0, 0.3)]

    calls.clear()
    ve = LinkProperty(
        name="VE",
        link_type="Damper - Viscoelastic",
        ke=[200.0, 0, 0, 0, 0, 0],
        ce=[50.0, 0, 0, 0, 0, 0],
        nonlinear_params=DamperParams(cexp=0.3),
    )
    links_mod.LinkMixin._damper_dof_mat(links_mod.LinkMixin(), ve, 0, {})
    assert [c[0] for c in calls] == ["Elastic", "Viscous", "Parallel"]


def test_damper_bilinear_friction_material(monkeypatch):
    """Bilinear → Hysteretic (hardening), friction → Hysteretic (EPP), on K not Ke."""
    import fea_toolkit.opensees._links as links_mod
    from fea_toolkit.model.sap_data import DamperParams, LinkProperty

    calls = []
    monkeypatch.setattr(links_mod.ops, "uniaxialMaterial", lambda *a: calls.append(a))

    bilin = LinkProperty(
        name="BL",
        link_type="Damper - Bilinear",
        ke=[100.0, 0, 0, 0, 0, 0],
        ce=[0.0] * 6,
        nonlinear_params=DamperParams(k=[1000.0] * 6, fy=[50.0] * 6, ratio=0.1),
    )
    tag = links_mod.LinkMixin._damper_dof_mat(links_mod.LinkMixin(), bilin, 0, {})
    assert calls[0][0] == "Hysteretic"
    assert calls[0][1] == tag
    assert calls[0][2] == pytest.approx(50.0)  # s1p = fy
    assert calls[0][3] == pytest.approx(0.05)  # e1p = fy / K (the nonlinear K)
    assert calls[0][4] == pytest.approx(95.0)  # s2p: post-yield hardening
    assert calls[0][4] > calls[0][2]

    calls.clear()
    fric = LinkProperty(
        name="FR",
        link_type="Damper - Friction",
        ke=[100.0, 0, 0, 0, 0, 0],
        ce=[0.0] * 6,
        nonlinear_params=DamperParams(k=[1000.0] * 6, fy=[50.0] * 6),
    )
    links_mod.LinkMixin._damper_dof_mat(links_mod.LinkMixin(), fric, 0, {})
    assert calls[0][0] == "Hysteretic"
    assert calls[0][3] == pytest.approx(0.05)  # e1p = fy / K
    assert calls[0][4] == pytest.approx(calls[0][2])  # s2p == s1p → EPP (ratio 0)


def test_direction_vector_parsing():
    """Axis-aligned tokens and arbitrary coordinate triples parse to unit vectors."""
    import numpy as np

    from fea_toolkit.opensees._links import _direction_vector

    assert np.allclose(_direction_vector("+X"), [1.0, 0.0, 0.0])
    assert np.allclose(_direction_vector("-Z"), [0.0, 0.0, -1.0])
    v = _direction_vector("(0.707, 0, 0.707)")
    assert v is not None
    assert np.linalg.norm(v) == pytest.approx(1.0)
    assert v[0] == pytest.approx(v[2])  # symmetric (45° in X–Z)
    assert v[1] == pytest.approx(0.0)
    v = _direction_vector("0 1 0")
    assert v is not None and np.allclose(v, [0.0, 1.0, 0.0])
    assert _direction_vector("") is None
    assert _direction_vector("garbage") is None
    assert _direction_vector("1 2") is None  # wrong arity


def test_nonlinear_per_dof_links_build():
    """Gap/Hook/Wen/Damper links emit their nonlinear materials (no raise)."""
    from fea_toolkit.model.sap_data import (
        DamperParams,
        GapParams,
        HookParams,
        LinkElement,
        LinkProperty,
        Node,
        Restraint,
        SAPModelData,
        WenParams,
    )
    from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
    from fea_toolkit.opensees.preprocessor import preprocess_model

    md = SAPModelData(
        nodes={
            "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
            "2": Node(node_id="2", node_tag=2, x=0.0, y=0.0, z=1000.0),
        },
        restraints={"1": Restraint([1, 1, 1, 1, 1, 1])},
        materials={},
        sections={},
        frame_elements={},
        area_elements={},
        frame_assignments={},
        area_assignments={},
        groups={},
        frame_auto_mesh={},
        link_connectivity=[
            LinkElement(link_id="L1", node_i="1", node_j="2"),
            LinkElement(link_id="L2", node_i="1", node_j="2"),
            LinkElement(link_id="L3", node_i="1", node_j="2"),
            LinkElement(link_id="L4", node_i="1", node_j="2"),
        ],
        link_assignments={"L1": "GAP", "L2": "HOOK", "L3": "WEN", "L4": "DMP"},
        link_properties={
            "GAP": LinkProperty(
                name="GAP",
                link_type="Gap",
                ke=[1000.0] * 6,
                nonlinear_params=GapParams(open=[0.0] * 6),
            ),
            "HOOK": LinkProperty(
                name="HOOK",
                link_type="Hook",
                ke=[1000.0] * 6,
                nonlinear_params=HookParams(open=[0.0] * 6),
            ),
            "WEN": LinkProperty(
                name="WEN",
                link_type="Plastic (Wen)",
                ke=[1000.0] * 6,
                nonlinear_params=WenParams(fy=[50.0] * 6, ratio=0.1, n=2.0),
            ),
            "DMP": LinkProperty(
                name="DMP",
                link_type="Damper - Exponential",
                ke=[0.0] * 6,
                ce=[50.0] + [0.0] * 5,
                nonlinear_params=DamperParams(cexp=0.3),
            ),
        },
    )
    try:
        mm = preprocess_model(md, {})
        ab = AnalysisBuilder(mm, {"link_model": "nonlinear"})
        ab.build_domain()
        assert len(ops.getEleTags()) == 4  # one twoNodeLink per link
    finally:
        ops.wipe()


# ── Phase 2b(ii): bearing elements (isolators) ─────────────────────────


def test_rubber_bearing_params():
    """The rubber bearing helper extracts (kInit, qd, alpha1) from the shear DOFs."""
    from fea_toolkit.model.sap_data import LinkProperty, RubberIsolatorParams
    from fea_toolkit.opensees._links import _rubber_bearing_params

    prop = LinkProperty(
        name="R",
        link_type="Rubber Isolator",
        ke=[0.0] * 6,
        nonlinear_params=RubberIsolatorParams(
            k=[0.0, 5000.0, 5000.0, 0.0, 0.0, 0.0], fy=[0.0, 50.0, 50.0, 0.0, 0.0, 0.0], ratio=0.1
        ),
    )
    assert _rubber_bearing_params(prop) == (5000.0, 45.0, 0.1)


def test_emit_friction_model_coulomb_vs_veldependent(monkeypatch):
    """Coulomb is used unless mu_fast/rate are present, then VelDependent."""
    import fea_toolkit.opensees._links as links_mod
    from fea_toolkit.model.sap_data import FrictionIsolatorParams

    calls = []
    monkeypatch.setattr(links_mod.ops, "frictionModel", lambda *a: calls.append(a))

    links_mod._emit_friction_model(1, FrictionIsolatorParams(mu=0.06))
    assert calls == [("Coulomb", 1, 0.06)]

    calls.clear()
    links_mod._emit_friction_model(2, FrictionIsolatorParams(mu=0.05, mu_fast=0.16, rate=0.6))
    assert calls == [("VelDependent", 2, 0.05, 0.16, 0.6)]


# Note: a static shear-push behaviour test for the bearing elements is deferred
# to P2d (benchmark tests).  The ``elastomericBearing*`` / ``singleFPBearing``
# elements are numerically sensitive (OpenSees wiki: "the element can be
# sensitive numerically … smaller time step recommended") and need the
# reference dynamic models (gravity + mass + ground motion), not a one-DOF
# static rig.  The emitter's parameters are pinned by
# ``test_rubber_bearing_params`` / ``test_emit_friction_model_*`` above and by
# ``test_bearing_links_build`` in ``tests/test_analysis_csm.py``.


# ── Phase 2c: emission-family registry + linearisation warning ────────


def test_link_emission_family():
    """The emission-family helper classifies every link type."""
    from fea_toolkit.model.sap_data import LinkProperty
    from fea_toolkit.opensees._links import _link_emission_family

    def fam(t):
        return _link_emission_family(LinkProperty(name="X", link_type=t))

    assert fam("Linear") == "linear"
    assert fam("Gap") == "per-dof"
    assert fam("Hook") == "per-dof"
    assert fam("Plastic (Wen)") == "per-dof"
    assert fam("Damper") == "per-dof"
    assert fam("Damper - Exponential") == "per-dof"  # subtype
    assert fam("Rubber Isolator") == "bearing"
    assert fam("Friction Isolator") == "bearing"
    assert fam("Triple Friction Pendulum") == "bearing"
    assert fam("T/C Friction Isolator") == "bearing"
    assert fam("Multilinear Elastic") == "unsupported"
    assert fam("Triple Pendulum Isolator") == "unsupported"
    assert fam("High Damping Rubber Isolator") == "unsupported"
    assert fam("Damper - Friction Directional") == "unsupported"


def test_elastic_mode_linearisation_warning(caplog):
    """A nonlinear link under link_model='elastic' warns it is linearised."""
    import logging

    from fea_toolkit.model.sap_data import LinkElement, LinkProperty, Node, Restraint, SAPModelData
    from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
    from fea_toolkit.opensees.preprocessor import preprocess_model

    md = SAPModelData(
        nodes={
            "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
            "2": Node(node_id="2", node_tag=2, x=0.0, y=0.0, z=1000.0),
        },
        restraints={"1": Restraint([1, 1, 1, 1, 1, 1])},
        materials={},
        sections={},
        frame_elements={},
        area_elements={},
        frame_assignments={},
        area_assignments={},
        groups={},
        frame_auto_mesh={},
        link_connectivity=[LinkElement(link_id="B1", node_i="1", node_j="2")],
        link_assignments={"B1": "RUB"},
        link_properties={
            "RUB": LinkProperty(name="RUB", link_type="Rubber Isolator", ke=[1000.0] * 6)
        },
    )
    try:
        mm = preprocess_model(md, {})
        ab = AnalysisBuilder(mm, {"link_model": "elastic"})
        with caplog.at_level(logging.WARNING):
            ab.build_domain()
        assert "represented by effective stiffness" in caplog.text
    finally:
        ops.wipe()
