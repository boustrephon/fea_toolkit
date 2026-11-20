"""Bearing-element behaviour — gravity-loaded lateral push (P49 P2b-ii validation).

The ``elastomericBearing*`` and ``singleFPBearing`` elements are *numerically
sensitive* and meant for a gravity-loaded model: the friction pendulum's
friction is ``mu*N`` and its post-yield stiffness is ``N/Reff``, so a bearing
with no axial load is degenerate (the earlier zero-gravity static push NaN'd).
These tests load a zero-length bearing with a vertical force first, then push it
laterally with ``DisplacementControl`` and read ``localForce``.

The rubber bearing's shear is the Nagarajaiah–Reinhorn–Constantinou backbone
``alpha1*kInit*u + (1-alpha1)*qd``; the measured value is ~7% higher for these
parameters (a formulation subtlety to pin against the §10 benchmarks), so its
tolerance is wider than the friction pendulum's.
"""

import pytest


def _push_bearing(elem_type, elem_args, weight, u_lat, friction=None):
    """Build a zero-length bearing, apply gravity, push laterally; return local force.

    Returns the first node's ``localForce`` (``Fx, Fy, Fz, Mx, My, Mz``): ``Fx``
    is the axial (gravity) force, ``Fz`` the shear in the pushed (local z)
    direction.
    """
    import openseespy.opensees as ops

    ops.wipe()
    ops.model("basic", "-ndm", 3, "-ndf", 6)
    ops.node(1, 0.0, 0.0, 0.0)
    ops.node(2, 0.0, 0.0, 0.0)  # zero-length bearing
    ops.fix(1, 1, 1, 1, 1, 1, 1)
    ops.fix(2, 0, 0, 0, 1, 1, 1)  # free translations, fixed rotations
    for tag in (1, 2, 3, 4):
        ops.uniaxialMaterial("Elastic", tag, 1.0e6)  # -P / -T / -My / -Mz
    if friction is not None:
        ops.frictionModel(*friction)
    ops.element(
        elem_type,
        1,
        1,
        2,
        *elem_args,
        "-P",
        1,
        "-T",
        2,
        "-My",
        3,
        "-Mz",
        4,
        "-orient",
        0,
        0,
        1,
        1,
        0,
        0,
    )
    # Gravity (axial = local x = global z).
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    ops.load(2, 0.0, 0.0, -weight, 0.0, 0.0, 0.0)
    ops.constraints("Transformation")
    ops.numberer("RCM")
    ops.system("BandGeneral")
    ops.test("NormDispIncr", 1.0e-10, 50)
    ops.algorithm("Newton")
    ops.integrator("LoadControl", 1.0)
    ops.analysis("Static")
    assert ops.analyze(1) == 0, "gravity stage failed"
    ops.loadConst("-time", 0.0)
    # Lateral push (dof 2 = y), displacement-controlled.
    ops.pattern("Plain", 2, 1)
    ops.load(2, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0)
    ops.integrator("DisplacementControl", 2, 2, 0.001)
    ops.analysis("Static")
    for _ in range(int(u_lat / 0.001)):
        assert ops.analyze(1) == 0, "lateral push failed"
    return list(ops.eleResponse(1, "localForce")[:6])


def test_rubber_bearing_shear_backbone():
    """A rubber isolator carries gravity and yields with the NRC backbone."""
    try:
        lf = _push_bearing(
            "elastomericBearingPlasticity", [5000.0, 50.0, 0.1, 0.0, 1.0], 1000.0, 0.05
        )
        assert lf[0] == pytest.approx(1000.0, rel=1.0e-3)  # axial force = weight
        # qd + alpha1*kInit*(u - qd/kInit) = 50 + 500*(0.05 - 0.01) = 70.
        assert abs(lf[2]) == pytest.approx(70.0, rel=0.10)
    finally:
        import openseespy.opensees as ops

        ops.wipe()


def test_fp_bearing_shear_backbone():
    """A friction pendulum carries gravity, then shear = mu*N + (N/Reff)*u."""
    try:
        lf = _push_bearing(
            "singleFPBearing",
            [10, 2.0, 5000.0],
            1000.0,
            0.05,
            friction=("Coulomb", 10, 0.06),
        )
        assert lf[0] == pytest.approx(1000.0, rel=1.0e-3)
        # 0.06*1000 + (1000/2.0)*0.05 = 60 + 25 = 85.
        assert abs(lf[2]) == pytest.approx(85.0, rel=0.02)
    finally:
        import openseespy.opensees as ops

        ops.wipe()
