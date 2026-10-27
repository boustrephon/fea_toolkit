"""Lignos–Krawinkler (2011) steel IMK (``Bilin``) calibration — A0.

The modified Ibarra–Medina–Krawinkler (IMK) deterioration model with *bilinear*
hysteretic response is OpenSees' ``Bilin`` uniaxial material, calibrated for
steel beam-to-column connections by Lignos & Krawinkler (2011),
DOI ``10.1061/(ASCE)ST.1943-541X.0000376``.

**Beam equations are transcribed verbatim** from Lignos' EPFL "Backbone Curve
Plotter for Steel Components" (``resslabtools.epfl.ch/component/``, whose own
page says it refers to Lignos & Krawinkler 2011), cross-checked against
PEER/ATC-72-1 §3.2.2 (Eqs. 3-1 to 3-6).  ``lignos_krawinkler_2011()`` therefore
works for *beams* — RBS and other-than-RBS, split at ``d = 533 mm``.

**Column equations transcribed** from Lignos, Hartloper, Elkady, Deierlein &
Hamburger (2019), "Proposed Updates to the ASCE 41 Nonlinear Modeling Parameters
for Wide-Flange Steel Columns", DOI ``10.1061/(ASCE)ST.1943-541X.0002353`` (via
the companion 2018 NCEE paper and the ASCE/SEI 41-17 Errata, which adopted them).
These are the *first-cycle envelope* (ASCE 41) parameters; the cyclic-
deterioration ``Λ`` of the monotonic backbone lives in the paywalled Lignos &
Krawinkler (2011) paper (Table 3) and is left at 0.

See ``docs/imk_deterioration_model.md`` for the design record.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

#: Canonical keys of the ``imk_params`` dict stamped onto a section.  Kept in
#: sync with :meth:`SteelIMKParams.to_dict` and ``opensees/_elements.py``.
IMK_PARAM_KEYS: tuple[str, ...] = (
    "mc_my",
    "My_pos",
    "My_neg",
    "lamda_S",
    "lamda_C",
    "lamda_K",
    "c_S",
    "c_C",
    "c_K",
    "theta_p_pos",
    "theta_p_neg",
    "theta_pc_pos",
    "theta_pc_neg",
    "res_pos",
    "res_neg",
    "theta_u_pos",
    "theta_u_neg",
    "D_pos",
    "D_neg",
)


@dataclass
class SteelIMKParams:
    """Parameters for a ``Bilin`` (modified IMK) steel hinge.

    Rotation quantities are in radians; moments in the model's own force·length
    units; ``lamda_*`` are the Lignos–Krawinkler cyclic-deterioration parameters
    (reference energy ``E_t = Λ · My``, so ``Λ`` is in radians).  ``My_neg`` is
    stored as a *negative* value (``Bilin`` expects a negative ``My_Neg``);
    ``theta_p_neg`` / ``theta_pc_neg`` / ``res_neg`` / ``theta_u_neg`` are
    *positive* magnitudes, as ``Bilin`` expects.  ``mc_my`` is the capping-to-
    yield moment ratio; the hardening ratio ``as`` is *derived* from it (and the
    spring's elastic stiffness ``k0``) so the capping moment ``Mc = mc_my · My``
    is reproduced exactly regardless of the ``k0`` convention.
    """

    My_pos: float
    My_neg: float
    theta_p_pos: float
    theta_p_neg: float
    theta_pc_pos: float
    theta_pc_neg: float
    res_pos: float
    res_neg: float
    lamda_S: float
    lamda_C: float
    lamda_K: float
    mc_my: float = 1.05
    theta_u_pos: float = 0.0
    theta_u_neg: float = 0.0
    c_S: float = 1.0
    c_C: float = 1.0
    c_K: float = 1.0
    D_pos: float = 1.0
    D_neg: float = 1.0

    def to_dict(self) -> dict[str, float]:
        """Flatten to the ``imk_params`` dict stamped onto a section."""
        return {key: float(getattr(self, key)) for key in IMK_PARAM_KEYS}

    @staticmethod
    def _hardening_ratio(mc_my: float, my_mag: float, k0: float, theta_p: float) -> float:
        """``as = (Mc - My) / (K0·θp)`` so the capping moment is exact."""
        if theta_p <= 0.0 or k0 <= 0.0:
            return 0.0
        return (mc_my - 1.0) * my_mag / (k0 * theta_p)

    def as_bilin_args(self, k0: float) -> tuple[float, ...]:
        """The 23 positional ``Bilin`` arguments (``matTag``/``nFactor`` excluded).

        Order matches ``Bilin matTag K0 as_pos as_neg My_pos My_neg Lamda_S
        Lamda_C Lamda_A Lamda_K c_S c_C c_A c_K theta_p_pos theta_p_neg
        theta_pc_pos theta_pc_neg Res_pos Res_neg theta_u_pos theta_u_neg
        D_pos D_neg``.  ``Lamda_A`` / ``c_A`` (accelerated reloading) are not a
        deterioration mode for a bilinear response and are forced to ``0.0`` /
        ``1.0``.
        """
        as_pos = self._hardening_ratio(self.mc_my, self.My_pos, k0, self.theta_p_pos)
        as_neg = self._hardening_ratio(self.mc_my, -self.My_neg, k0, self.theta_p_neg)
        return (
            k0,
            as_pos,
            as_neg,
            self.My_pos,
            self.My_neg,
            self.lamda_S,
            self.lamda_C,
            0.0,
            self.lamda_K,
            self.c_S,
            self.c_C,
            1.0,
            self.c_K,
            self.theta_p_pos,
            self.theta_p_neg,
            self.theta_pc_pos,
            self.theta_pc_neg,
            self.res_pos,
            self.res_neg,
            self.theta_u_pos,
            self.theta_u_neg,
            self.D_pos,
            self.D_neg,
        )


def _beam_rotations(
    h_tw: float,
    bf_2tf: float,
    lb_ry: float,
    l_over_2d: float,
    d_mm: float,
    fy_mpa: float,
    rbs: bool,
) -> tuple[float, float, float]:
    """Lignos–Krawinkler (2011) beam regressions: return ``(theta_p, theta_pc, lamda)``.

    Transcribed verbatim from the EPFL component tool (``resslabtools.epfl.ch``):
    ``h_tw`` = clear web-depth ratio ``(d - 2·tf)/tw``; ``bf_2tf`` = flange
    slenderness ``bf/(2·tf)``; ``lb_ry`` = ``Lb/ry``; ``l_over_2d`` = ``L/(2d)``
    (the tool's double-curvature member-length term); ``d_mm`` = section depth in
    mm; ``fy_mpa`` = yield stress in MPa.
    """
    if rbs:
        theta_p = (
            0.19
            * h_tw**-0.314
            * bf_2tf**-0.1
            * lb_ry**-0.185
            * l_over_2d**0.113
            * (d_mm / 533.0) ** -0.76
            * (fy_mpa / 355.0) ** -0.07
        )
        theta_pc = 9.52 * h_tw**-0.513 * bf_2tf**-0.863 * lb_ry**-0.108 * (fy_mpa / 355.0) ** -0.36
        lamda = 585.0 * h_tw**-1.14 * bf_2tf**-0.632 * lb_ry**-0.205 * (fy_mpa / 355.0) ** -0.391
    elif d_mm < 533.0:
        theta_p = (
            0.0865
            * h_tw**-0.365
            * bf_2tf**-0.14
            * l_over_2d**0.34
            * (d_mm / 533.0) ** -0.721
            * (fy_mpa / 355.0) ** -0.23
        )
        theta_pc = (
            5.63 * h_tw**-0.565 * bf_2tf**-0.8 * (d_mm / 533.0) ** -0.28 * (fy_mpa / 355.0) ** -0.43
        )
        lamda = 495.0 * h_tw**-1.34 * bf_2tf**-0.595 * (fy_mpa / 355.0) ** -0.36
    else:
        theta_p = (
            0.318
            * h_tw**-0.55
            * bf_2tf**-0.345
            * lb_ry**-0.023
            * l_over_2d**0.09
            * (d_mm / 533.0) ** -0.33
            * (fy_mpa / 355.0) ** -0.13
        )
        theta_pc = (
            7.5
            * h_tw**-0.61
            * bf_2tf**-0.71
            * lb_ry**-0.11
            * (d_mm / 533.0) ** -0.161
            * (fy_mpa / 355.0) ** -0.32
        )
        lamda = 536.0 * h_tw**-1.26 * bf_2tf**-0.525 * lb_ry**-0.13 * (fy_mpa / 355.0) ** -0.291
    return theta_p, theta_pc, lamda


def lignos_2019_wide_flange_column(
    *,
    section_depth_m: float,
    web_thickness_m: float,
    flange_thickness_m: float,
    radius_of_gyration_m: float,
    unbraced_length_m: float,
    fy_pa: float,
    plastic_modulus_m3: float,
    axial_ratio: float,
    ry_factor: float = 1.1,
    theta_u: Optional[float] = None,
) -> SteelIMKParams:
    """Wide-flange steel column ``Bilin`` parameters (Lignos et al. 2019 / ASCE 41-17).

    Transcribes Eqs. 6–10 of Lignos, Hartloper, Elkady, Deierlein & Hamburger
    (2019), DOI ``10.1061/(ASCE)ST.1943-541X.0002353`` (via the companion 2018
    NCEE paper and the ASCE/SEI 41-17 Errata, which adopted them):

    * ``My* = 1.15·Z·Ry·Fy·(1 − Pg/Pye)`` for ``Pg/Pye ≤ 0.20``; and
      ``My* = 1.15·Z·Ry·Fy·(9/8)(1 − Pg/Pye)`` for ``Pg/Pye > 0.20``  (Eq. 6);
    * ``a* = Mc/My = 9.5·(h/tw)^-0.4·(Lb/ry)^-0.16·(1 − Pg/Pye)^0.2 ≥ 1.0``  (Eq. 7);
    * ``θp = 15·(h/tw)^-1.6·(Lb/ry)^-0.3·(1 − Pg/Pye)^2.3 ≤ 0.10``  (Eq. 8);
    * ``θpc = 14·(h/tw)^-0.8·(Lb/ry)^-0.5·(1 − Pg/Pye)^3.2 ≤ 0.10``  (Eq. 9);
    * ``Mr = (0.4 − 0.4·Pg/Pye)·My*``  (Eq. 10).

    These are the *first-cycle envelope* (ASCE 41) parameters.  The cyclic-
    deterioration ``Λ`` of the monotonic (``Bilin``) backbone is **not** given in
    this paper (it lives in Lignos & Krawinkler 2011, Table 3), so ``lamda_*``
    are set to ``0``.

    Args:
        section_depth_m: Overall section depth ``d`` (m).
        web_thickness_m: Web thickness ``tw`` (m).
        flange_thickness_m: Flange thickness ``tf`` (m) — used for the clear web
            depth ``h = d − 2·tf``.
        radius_of_gyration_m: Weak-axis radius of gyration ``ry`` (m).
        unbraced_length_m: Unbraced length ``Lb`` (m).
        fy_pa: Nominal yield strength (Pa).
        plastic_modulus_m3: Strong-axis plastic section modulus ``Zx`` (m³).
        axial_ratio: Gravity axial-load ratio ``Pg/Pye``.
        ry_factor: AISC expected-yield ratio ``Ry`` (default 1.1 for A992/A572-50).
        theta_u: Ultimate rotation (rad); defaults to ``theta_p + theta_pc``.

    Note:
        Columns with ``Pg/Pye > 0.6`` are force-controlled (elastic) per ASCE 41;
        the caller should not assign a hinge in that case.
    """
    h = section_depth_m - 2.0 * flange_thickness_m
    h_tw = h / web_thickness_m
    lb_ry = unbraced_length_m / radius_of_gyration_m
    one_minus_axial = 1.0 - axial_ratio

    # Eq. 6 — effective yield moment (cyclic hardening + axial-load reduction).
    if axial_ratio <= 0.20:
        my = 1.15 * plastic_modulus_m3 * ry_factor * fy_pa * one_minus_axial
    else:
        my = 1.15 * plastic_modulus_m3 * ry_factor * fy_pa * (9.0 / 8.0) * one_minus_axial

    # Eq. 7 — capping ratio (hardening) a* = Mc/My.
    mc_my = 9.5 * h_tw**-0.4 * lb_ry**-0.16 * one_minus_axial**0.2
    mc_my = max(mc_my, 1.0)

    # Eqs. 8 & 9 — pre-/post-capping plastic rotations (capped at 0.10 rad).
    theta_p = min(15.0 * h_tw**-1.6 * lb_ry**-0.3 * one_minus_axial**2.3, 0.10)
    theta_pc = min(14.0 * h_tw**-0.8 * lb_ry**-0.5 * one_minus_axial**3.2, 0.10)

    # Eq. 10 — residual strength ratio.
    kappa = 0.4 - 0.4 * axial_ratio

    if theta_u is None:
        theta_u = theta_p + theta_pc

    return SteelIMKParams(
        My_pos=my,
        My_neg=-my,
        theta_p_pos=theta_p,
        theta_p_neg=theta_p,
        theta_pc_pos=theta_pc,
        theta_pc_neg=theta_pc,
        res_pos=kappa,
        res_neg=kappa,
        lamda_S=0.0,
        lamda_C=0.0,
        lamda_K=0.0,
        mc_my=mc_my,
        theta_u_pos=theta_u,
        theta_u_neg=theta_u,
    )


def lignos_krawinkler_2011(
    *,
    section_depth_m: float,
    web_thickness_m: float,
    flange_width_m: float,
    flange_thickness_m: float,
    radius_of_gyration_m: float,
    unbraced_length_m: float,
    member_length_m: float,
    fy_pa: float,
    plastic_modulus_m3: float,
    axial_ratio: float = 0.0,
    rbs: bool = False,
    column: bool = False,
    mc_my: float = 1.05,
    kappa: float = 0.4,
    theta_u: Optional[float] = None,
) -> SteelIMKParams:
    """Compute ``Bilin`` parameters from the Lignos–Krawinkler (2011) regressions (A0).

    Implemented for **beams** (RBS and other-than-RBS, Lignos–Krawinkler 2011);
    **columns** delegate to :func:`lignos_2019_wide_flange_column` (Lignos et al.
    2019, ASCE 41-17).

    Args:
        section_depth_m: Overall section depth ``d`` (m).
        web_thickness_m: Web thickness ``tw`` (m).
        flange_width_m: Flange width ``bf`` (m).
        flange_thickness_m: Flange thickness ``tf`` (m).
        radius_of_gyration_m: Weak-axis radius of gyration ``ry`` (m).
        unbraced_length_m: Unbraced length ``Lb`` (m).
        member_length_m: Member length ``L`` (m).
        fy_pa: Yield strength (Pa).
        plastic_modulus_m3: Strong-axis plastic section modulus ``Zx`` (m³).
        axial_ratio: Gravity axial-load ratio ``Pg/Pye`` (column path only).
        rbs: True for reduced-beam-section beams.
        column: True for a column (else a beam).
        mc_my: Capping-to-yield moment ratio (beams; default 1.05).
        kappa: Residual strength ratio ``Mr/My`` (beams; default 0.4).
        theta_u: Ultimate rotation (rad); defaults to ``theta_p + theta_pc``.
    """
    if column:
        return lignos_2019_wide_flange_column(
            section_depth_m=section_depth_m,
            web_thickness_m=web_thickness_m,
            flange_thickness_m=flange_thickness_m,
            radius_of_gyration_m=radius_of_gyration_m,
            unbraced_length_m=unbraced_length_m,
            fy_pa=fy_pa,
            plastic_modulus_m3=plastic_modulus_m3,
            axial_ratio=axial_ratio,
            theta_u=theta_u,
        )

    h = section_depth_m - 2.0 * flange_thickness_m  # clear web depth
    h_tw = h / web_thickness_m
    bf_2tf = flange_width_m / (2.0 * flange_thickness_m)
    lb_ry = unbraced_length_m / radius_of_gyration_m
    l_over_2d = member_length_m / (2.0 * section_depth_m)
    d_mm = section_depth_m * 1000.0
    fy_mpa = fy_pa * 1e-6

    theta_p, theta_pc, lamda = _beam_rotations(h_tw, bf_2tf, lb_ry, l_over_2d, d_mm, fy_mpa, rbs)

    my = fy_pa * plastic_modulus_m3
    if theta_u is None:
        theta_u = theta_p + theta_pc

    return SteelIMKParams(
        My_pos=my,
        My_neg=-my,
        theta_p_pos=theta_p,
        theta_p_neg=theta_p,
        theta_pc_pos=theta_pc,
        theta_pc_neg=theta_pc,
        res_pos=kappa,
        res_neg=kappa,
        lamda_S=lamda,
        lamda_C=lamda,
        lamda_K=lamda,
        mc_my=mc_my,
        theta_u_pos=theta_u,
        theta_u_neg=theta_u,
    )
