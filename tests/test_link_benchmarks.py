"""P49 §10 reference-benchmark tests — published isolator backbones.

Unlike the component tests in ``test_bearing_behavior.py`` (which assert the
element's closed-form law directly), these reproduce a *published* isolator
backbone within tolerance.  Units are kN·m (force in kN, length in m) to match
the sources.

Sources
-------
* Lead-rubber bearing — Qin Dong, Okazaki, Midorikawa, Ryan, Sato & Sasaki
  (2013), "Analysis of Shake-Table Tests of a Full-Scale Building Isolated by
  Lead-Rubber Bearings" (E-Defense), http://hdl.handle.net/2115/54236.
  Manufacturer's bilinear model: initial stiffness ``K0 = 6.5 kN/mm``,
  post-yield stiffness ``KF = 0.65 kN/mm``, yield strength ``FU = 73 kN``;
  tributary axial load per bearing ``N = 1325 kN``.
* Friction pendulum (first-stage single pendulum) — Morgan & Mahin (2010),
  "The Use of Base Isolation Systems to Achieve Complex Seismic Performance
  Objectives" (PEER 2011/06), Table 1 isolator ``TP-3-0.10``: first-stage
  effective radius ``L1 = 11 in = 0.2794 m``, ``mu1 = 0.01``.
"""

import pytest


def _shear_at(elem_type, elem_args, weight, u_lat, friction=None):
    """Zero-length bearing under gravity, pushed laterally to ``u_lat`` (m).

    Returns ``(axial, shear)`` from the first node's ``localForce`` — axial is
    local x (global z), shear is local z (global y, the pushed direction).
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
    # Lateral push (dof 2 = y = local z), displacement-controlled.
    ops.pattern("Plain", 2, 1)
    ops.load(2, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0)
    ops.integrator("DisplacementControl", 2, 2, 0.001)
    ops.analysis("Static")
    for _ in range(int(round(u_lat / 0.001))):
        assert ops.analyze(1) == 0, "lateral push failed"
    lf = list(ops.eleResponse(1, "localForce")[:6])
    return lf[0], -lf[2]


def _lrb_shear(u):
    """E-Defense LRB shear (kN) at displacement ``u`` (m)."""
    # kInit = K0 = 6500 kN/m, alpha1 = KF/K0 = 0.1, qd = FU*(1 - alpha1) = 65.7.
    return _shear_at("elastomericBearingPlasticity", [6500.0, 65.7, 0.1, 0.0, 1.0], 1325.0, u)[1]


def test_lrb_e_defense_backbone():
    """E-Defense LRB: K0=6.5 kN/mm, KF=0.65 kN/mm, FU=73 kN (Qin Dong et al. 2013)."""
    try:
        # Elastic branch (5 mm < uy = FU/K0 = 11.23 mm): F = K0*u = 6.5*5 = 32.5.
        assert _lrb_shear(0.005) == pytest.approx(32.5, rel=1.0e-3)
        # Post-yield: F = FU + KF*(u - uy) = 73 + 0.65*(100 - 11.23) = 130.7.
        assert _lrb_shear(0.1) == pytest.approx(130.7, rel=1.0e-3)
        # At 200 mm: 73 + 0.65*(200 - 11.23) = 195.7.
        assert _lrb_shear(0.2) == pytest.approx(195.7, rel=1.0e-3)
    finally:
        import openseespy.opensees as ops

        ops.wipe()


def test_fp_morgan_mahin_first_stage():
    """Morgan & Mahin TP-3-0.10 first stage: Reff=11 in, mu=0.01 (single FP).

    The element adds geometric stiffening (its normal force grows with lateral
    displacement, ``N = -P + F*u/Reff``), so the *linearised* published backbone
    ``F = mu*N + N/Reff*u`` is reproduced at small ``u/Reff``; at ``u = 0.05 m``
    (``u/Reff = 0.18``) the stiffening is already ~3.5 %.
    """
    try:
        # F/N = mu + u/Reff = 0.01 + u/0.2794 — load-proportional, N cancels.
        reff = 0.2794  # 11 in
        mu = 0.01
        n = 1000.0  # arbitrary axial load; the normalised backbone is N-free

        def shear(u):
            return _shear_at(
                "singleFPBearing", [10, reff, 1.0e6], n, u, friction=("Coulomb", 10, mu)
            )[1]

        assert shear(0.02) == pytest.approx(mu * n + n / reff * 0.02, rel=2.0e-2)
        assert shear(0.03) == pytest.approx(mu * n + n / reff * 0.03, rel=2.0e-2)
    finally:
        import openseespy.opensees as ops

        ops.wipe()
