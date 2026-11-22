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
* Friction pendulum (first stage) — Morgan & Mahin (2008), "The Optimization of
  Multi-Stage Friction Pendulum Isolators for Loss Mitigation Considering a
  Range of Seismic Hazard" (14WCEE, paper 11-0070): the first stage slides on
  the two inner surfaces at once, so the effective radius is
  ``2·L1 = 2 × 11 in = 0.5588 m``, ``mu1 = 0.01``.
* Viscous damper — Constantinou & Symans (1993), "Experimental study of
  seismic response of buildings with supplemental fluid dampers", and Pekcan,
  Mander & Chen (1999), "Fundamental considerations for the design of
  non-linear viscous dampers" — the fluid-viscous constitutive law
  ``F = C·|v|^α·sign(v)``; under a constant force the terminal velocity is
  ``v = (F/C)^(1/α)``.
* Gap / pounding — Anagnostopoulos (1988), "Pounding of buildings in series
  during earthquakes" — a contact element that is force-free until the
  separation gap ``g`` closes, then ``F = k·(δ − g)`` for the penetration
  ``δ > g``.
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
    """Morgan & Mahin (2008) TP first stage: Reff = 2·L1 = 22 in, mu=0.01.

    The triple pendulum's first stage slides on the two inner surfaces at once,
    so the first-stage effective radius is ``2·L1`` and the slope is
    ``N/(2·L1)``.  The element adds geometric stiffening (its normal force
    grows with lateral displacement, ``N = -P + F*u/Reff``), so the *linearised*
    backbone ``F = mu*N + N/Reff*u`` is reproduced at small ``u/Reff``.
    """
    try:
        # F/N = mu + u/Reff = 0.01 + u/(2*0.2794) — load-proportional, N cancels.
        reff = 2 * 0.2794  # 2 × L1 (L1 = 11 in), the two inner surfaces
        mu = 0.01
        n = 1000.0  # arbitrary axial load; the normalised backbone is N-free

        def shear(u):
            return _shear_at(
                "singleFPBearing", [10, reff, 1.0e6], n, u, friction=("Coulomb", 10, mu)
            )[1]

        assert shear(0.005) == pytest.approx(mu * n + n / reff * 0.005, rel=2.0e-2)
        assert shear(0.010) == pytest.approx(mu * n + n / reff * 0.010, rel=2.0e-2)
    finally:
        import openseespy.opensees as ops

        ops.wipe()


def _viscous_terminal(C, alpha, force, mass=1.0, dt=0.001, n_steps=4000):
    """Terminal velocity of a mass on a ``Viscous`` dashpot under constant force.

    Node 1 is fixed; node 2 (mass ``mass``) carries a constant force ``force``
    resisted only by the dashpot ``F = C·|v|^α·sign(v)``.  The terminal
    velocity is ``v = (force/C)^(1/α)`` and the dashpot force balances
    ``force``.  Returns ``(v, f)`` — node-2 velocity and the element's local
    force (signed so it opposes the velocity).
    """
    import openseespy.opensees as ops

    ops.wipe()
    ops.model("basic", "-ndm", 1, "-ndf", 1)
    ops.node(1, 0.0)
    ops.node(2, 0.0)
    ops.fix(1, 1)
    ops.mass(2, mass)
    ops.uniaxialMaterial("Viscous", 1, C, alpha)
    ops.element("zeroLength", 1, 1, 2, "-mat", 1, "-dir", 1)
    ops.timeSeries("Constant", 1)
    ops.pattern("Plain", 1, 1)
    ops.load(2, force)
    ops.constraints("Transformation")
    ops.numberer("RCM")
    ops.system("BandGeneral")
    ops.test("NormDispIncr", 1.0e-10, 50)
    ops.algorithm("Newton")
    ops.integrator("Newmark", 0.5, 0.25)
    ops.analysis("Transient")
    v = f = 0.0
    for _ in range(n_steps):
        assert ops.analyze(1, dt) == 0, "transient step failed"
        v = float(ops.nodeVel(2, 1))
        f = float(ops.eleResponse(1, "force")[0])
    return v, f


def test_viscous_damper_force_velocity_law():
    """``Viscous`` damper: terminal ``v = (F/C)^(1/α)`` and ``F = C·v^α``.

    The nonlinear fluid-viscous damper law (Constantinou & Symans 1993; Pekcan,
    Mander & Chen 1999) is checked both for a nonlinear exponent (α = 0.3) and
    the linear limit (α = 1).
    """
    try:
        # Nonlinear damper (α = 0.3): v = (50/100)^(1/0.3) ≈ 0.0992 m/s.
        C, alpha, F0 = 100.0, 0.3, 50.0
        v, f = _viscous_terminal(C, alpha, F0)
        assert v == pytest.approx((F0 / C) ** (1.0 / alpha), rel=1.0e-3)
        assert abs(f) == pytest.approx(F0, rel=1.0e-3)  # equilibrium
        assert abs(f) == pytest.approx(C * abs(v) ** alpha, rel=1.0e-3)  # F = C·v^α

        # Linear damper (α = 1): v = F0/C.
        C, alpha, F0 = 100.0, 1.0, 50.0
        v, f = _viscous_terminal(C, alpha, F0)
        assert v == pytest.approx(F0 / C, rel=1.0e-3)
        assert abs(f) == pytest.approx(C * abs(v), rel=1.0e-3)
    finally:
        import openseespy.opensees as ops

        ops.wipe()


def _gap_axial_force(k, open_, u):
    """Axial force of a 1-DOF truss with a compression-only gap, driven to ``u``.

    ``u`` is the relative displacement (positive = separation); the gap engages
    in compression once the opening ``open_`` is closed.
    """
    import openseespy.opensees as ops

    from fea_toolkit.opensees._links import _hysteretic_one_sided_args

    ops.wipe()
    ops.model("basic", "-ndm", 1, "-ndf", 1)
    ops.node(1, 0.0)
    ops.node(2, 1.0)
    ops.fix(1, 1)
    ops.uniaxialMaterial("Hysteretic", 1, *_hysteretic_one_sided_args(k, open_, "gap"))
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
    assert ops.analyze(1) == 0, "static step failed"
    return float(ops.eleResponse(1, "axialForce")[0])


def test_gap_anagnostopoulos_pounding():
    """Gap element: contact force ``k·(δ − g)`` once closed, ~0 while open.

    Anagnostopoulos (1988) models pounding with a contact element that carries
    no force until the separation gap ``g`` closes, then a linear spring of
    stiffness ``k`` with penetration ``δ − g``.  The one-sided ``Hysteretic``
    gap emitter reproduces that backbone.
    """
    try:
        g = 0.01  # separation gap (m)
        k = 9740.0  # contact stiffness (kN/m) — pounding-element order of magnitude
        for delta in (0.011, 0.02, 0.05):
            f = _gap_axial_force(k, g, -delta)  # u = -δ (closing)
            assert f == pytest.approx(-k * (delta - g), rel=1.0e-6)
        # While the gap is still open (separation) the force is ~zero.
        f_open = _gap_axial_force(k, g, 0.005)
        f_closed = _gap_axial_force(k, g, -0.011)
        assert abs(f_open) < abs(f_closed) * 1.0e-3
    finally:
        import openseespy.opensees as ops

        ops.wipe()
