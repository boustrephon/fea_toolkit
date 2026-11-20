"""Nonlinear link materials — component-level comparison tests (P49 Phase 2).

Phase 2 emits the nonlinear SAP2000/ETABS link types as OpenSees materials.
The *one-sided / backbone family* (Gap, Hook, bilinear, multilinear, friction
dampers) is emitted with a single ``Hysteretic`` backbone whose pinching /
damage knobs are pinned neutral — see ``docs/nonlinear_link_plan.md``.

These tests pin that backbone to the purpose-built special-case materials
OpenSees ships, so the "one model" choice is backed by evidence rather than
assumption:

* ``ENT`` (elastic-no-tension) is the reference for a **compression-only**
  Gap;
* ``ElasticPP`` (with ``epsyN = 0``) is the *monotonic* reference for a
  **tension-only** Hook.

Both drives use a 1-DOF truss (``ndm=1, ndf=1``) as a material rig.  This is a
test-only convenience; the production workflow stays 3-D (``.clinerules``
§3.11).
"""

import openseespy.opensees as ops
import pytest

E = 1000.0  # reference spring stiffness (force per unit displacement)
EMAX = 1.0  # backbone extent — beyond every test displacement
TINY = E * 1.0e-6  # residual stiffness in the released direction


def _emit_ent(tag: int) -> None:
    """Elastic-no-tension — the reference compression-only (Gap) material."""
    ops.uniaxialMaterial("ENT", tag, E)


def _emit_hysteretic_compression_only(tag: int) -> None:
    """Compression-only ``Hysteretic`` backbone: slope ``E``, tension released."""
    ops.uniaxialMaterial(
        "Hysteretic",
        tag,
        # positive (tension) branch — released, tiny residual stiffness
        TINY * EMAX,
        EMAX,
        TINY * EMAX,
        2 * EMAX,
        TINY * EMAX,
        3 * EMAX,
        # negative (compression) branch — slope E
        -E * EMAX,
        -EMAX,
        -2 * E * EMAX,
        -2 * EMAX,
        -3 * E * EMAX,
        -3 * EMAX,
        1.0,
        1.0,
        0.0,
        0.0,
        0.0,  # neutral pinching / damage
    )


def _emit_hysteretic_tension_only(tag: int) -> None:
    """Tension-only ``Hysteretic`` backbone: slope ``E``, compression released."""
    ops.uniaxialMaterial(
        "Hysteretic",
        tag,
        E * EMAX,
        EMAX,
        2 * E * EMAX,
        2 * EMAX,
        3 * E * EMAX,
        3 * EMAX,
        -TINY * EMAX,
        -EMAX,
        -TINY * EMAX,
        -2 * EMAX,
        -TINY * EMAX,
        -3 * EMAX,
        1.0,
        1.0,
        0.0,
        0.0,
        0.0,
    )


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
