"""Lignos–Krawinkler (2011) steel IMK (``IMKBilin``) calibration — A0.

The modified Ibarra–Medina–Krawinkler (IMK) deterioration model with *bilinear*
hysteretic response is OpenSees' ``IMKBilin`` uniaxial material, calibrated for
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
These include the *monotonic-backbone* parameters (``My*``, ``a``, ``θp``,
``θpc``, ``Mr``) and the cyclic-deterioration ``Λ`` (Eq. 13):
``Λs`` for basic-strength deterioration (two branches split at
``Pg/Pye = 0.35``, capped at 3.0 rad) and ``Λc = Λk = 0.9·Λs`` for post-capping
and unloading-stiffness deterioration.

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


# ── Ultimate-rotation (``theta_u`` / ``IMKBilin`` ``du``) conventions ──────
# The ``IMKBilin`` material's ``du`` is the ultimate rotation at which the
# strength drops to zero.  The papers do not give a single flexural ``du``
# regression, so the toolkit exposes the choice as a named convention (plus an
# explicit float override for benchmark calibration).  See
# ``docs/imk_deterioration_model.md`` → "The ultimate rotation θu".
THETA_U_MODELS: tuple[str, ...] = ("plateau", "none", "lignos", "hybrid")

#: Lignos et al. (2019) Eq. 11 — monotonic-backbone ultimate chord rotation
#: (rad), the rotation at which a column loses its axial-load-carrying capacity.
LIGNOS_ULT_ROTATION: float = 0.15

#: Residual-plateau length added after the post-capping branch (rad).
THETA_U_PLATEAU: float = 0.02


def _resolve_theta_u(
    theta_u: Optional[float],
    model: str,
    theta_p: float,
    theta_pc: float,
) -> float:
    """Resolve the ultimate rotation ``θu`` (``IMKBilin`` ``du``) from a convention.

    An explicit *theta_u* (non-``None``) always wins.  Otherwise the named
    convention selects the value:

    * ``"plateau"`` — ``θp + θpc + 0.02`` (flexural fracture + residual plateau;
      the default);
    * ``"none"`` — ``θp + θpc`` (no plateau);
    * ``"lignos"`` — ``0.15`` rad (Lignos et al. 2019 Eq. 11, monotonic
      axial-collapse rotation);
    * ``"hybrid"`` — ``max(θp + θpc + 0.02, 0.15)`` (floor at the paper's
      collapse point, plateau otherwise).

    Every resolved value — explicit *theta_u* included — is validated against
    the capping rotation: an ultimate rotation that does not exceed *theta_p*
    (e.g. ``"lignos"``'s fixed ``0.15`` rad for a stocky section with
    ``θp = 0.20``) places the flexural collapse point *before* the capping
    point and is rejected, since it is not a valid ``IMKBilin`` backbone.

    Args:
        theta_u: Explicit ultimate rotation (rad) or ``None`` to use *model*.
        model: One of :data:`THETA_U_MODELS`.
        theta_p: Pre-capping plastic rotation (rad).
        theta_pc: Post-capping plastic rotation (rad).

    Returns:
        The resolved ultimate rotation (rad), always greater than *theta_p*.

    Raises:
        ValueError: If *model* is not one of :data:`THETA_U_MODELS`, or if the
            resolved ultimate rotation is not greater than *theta_p*.
    """
    if theta_u is not None:
        resolved = float(theta_u)
    elif model == "plateau":
        resolved = theta_p + theta_pc + THETA_U_PLATEAU
    elif model == "none":
        resolved = theta_p + theta_pc
    elif model == "lignos":
        resolved = LIGNOS_ULT_ROTATION
    elif model == "hybrid":
        resolved = max(theta_p + theta_pc + THETA_U_PLATEAU, LIGNOS_ULT_ROTATION)
    else:
        raise ValueError(f"theta_u_model must be one of {THETA_U_MODELS}, got {model!r}")
    # NaN-safe: reject any value not strictly greater than theta_p — a NaN (or
    # a resolved rotation at/below the capping point) is not a valid backbone.
    if not resolved > theta_p:
        raise ValueError(
            f"resolved ultimate rotation theta_u={resolved:g} rad must exceed the "
            f"capping rotation theta_p={theta_p:g} rad; the selected convention "
            f"(or explicit theta_u) places the collapse point before the capping "
            f"point, which is not a valid IMKBilin backbone"
        )
    return resolved


@dataclass
class SteelIMKParams:
    """Parameters for a ``IMKBilin`` (modified IMK) steel hinge.

    Rotation quantities are in radians; moments in the model's own force·length
    units; ``lamda_*`` are the Lignos–Krawinkler cyclic-deterioration parameters
    (reference energy ``E_t = Λ · My``, so ``Λ`` is in radians).  ``My_neg`` is
    stored as a *negative* value (converted to the positive ``Fy_neg`` magnitude
    by ``as_bilin_args``).  ``theta_p_neg`` / ``theta_pc_neg`` / ``res_neg`` /
    ``theta_u_neg`` are *positive* magnitudes, as ``IMKBilin`` expects.
    ``mc_my`` is the capping-to-yield ratio (``FmaxFy``) and ``res_*`` the
    residual ratio (``FresFy``); ``as_bilin_args`` maps ``θpc`` to ``IMKBilin``'s
    ``dpc = mc_my·θpc/(mc_my − res)`` so the post-capping slope matches the
    calibrated Lignos–Krawinkler backbone.  ``theta_u_model`` records *which*
    ultimate-rotation convention produced ``theta_u_*`` (one of
    :data:`THETA_U_MODELS`); it is provenance only and is **not** serialized to
    the ``imk_params`` dict (:meth:`to_dict`).
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
    theta_u_model: str = "plateau"

    def to_dict(self) -> dict[str, float]:
        """Flatten to the ``imk_params`` dict stamped onto a section."""
        return {key: float(getattr(self, key)) for key in IMK_PARAM_KEYS}

    def as_bilin_args(self, k0: float) -> tuple[float, ...]:
        """The 21 positional ``IMKBilin`` arguments (``matTag`` excluded).

        Delegates to :func:`fea_toolkit.opensees._elements._imk_bilin_args`,
        the single implementation of the ``IMKBilin`` argument order, the
        ``theta_u`` fallback and the ``θpc → dpc`` conversion.  ``My_neg`` is
        passed as-is (negative); the delegate emits ``Fy_neg = -My_neg`` as the
        positive magnitude ``IMKBilin`` expects.

        Args:
            k0: Initial (elastic) rotational stiffness ``Ke``.

        Returns:
            The 21 positional arguments (``matTag`` excluded) for
            ``ops.uniaxialMaterial("IMKBilin", …)``.
        """
        from ..opensees._elements import _imk_bilin_args

        return _imk_bilin_args(self.to_dict(), k0, self.My_pos, self.My_neg)


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
    theta_u_model: str = "plateau",
) -> SteelIMKParams:
    """Wide-flange steel column ``IMKBilin`` parameters (Lignos et al. 2019 / ASCE 41-17).

    Transcribes the *monotonic-backbone* parameters of Lignos, Hartloper, Elkady,
    Deierlein & Hamburger (2019), DOI ``10.1061/(ASCE)ST.1943-541X.0002353``
    (via the companion 2018 NCEE paper and the ASCE/SEI 41-17 Errata, which
    adopted the first-cycle envelope values):

    * ``My* = 1.15·Z·Ry·Fy·(1 − Pg/Pye)`` for ``Pg/Pye ≤ 0.20``; and
      ``My* = 1.15·Z·Ry·Fy·(9/8)(1 − Pg/Pye)`` for ``Pg/Pye > 0.20``  (Eq. 2);
    * ``a = Mc/My = 12.5·(h/tw)^-0.2·(Lb/ry)^-0.4·(1 − Pg/Pye)^0.4``,
      ``1.0 ≤ a ≤ 1.3``  (Eq. 3);
    * ``θp = 294·(h/tw)^-1.7·(Lb/ry)^-0.7·(1 − Pg/Pye)^1.6 ≤ 0.20``  (Eq. 7);
    * ``θpc = 90·(h/tw)^-0.8·(Lb/ry)^-0.8·(1 − Pg/Pye)^2.5 ≤ 0.30``  (Eq. 9);
    * ``Mr = (0.5 − 0.4·Pg/Pye)·My*``  (Eq. 5).
    * ``Λs = 25 500·(h/tw)^-2.14·(Lb/ry)^-0.53·(1 − Pg/Pye)^4.92 ≤ 3.0`` for
      ``Pg/Pye ≤ 0.35``; and
      ``Λs = 268 000·(h/tw)^-2.30·(Lb/ry)^-1.30·(1 − Pg/Pye)^1.19 ≤ 3.0``
      for ``Pg/Pye > 0.35``  (Eq. 13 — basic-strength cyclic deterioration).

    ``My*`` (Eq. 2) is shared by both curves; ``a``, ``θp``, ``θpc`` and ``Mr``
    are the paper's *monotonic-backbone* parameters — the curve the ``IMKBilin``
    hinge models for nonlinear response-history analysis — paired with the
    cyclic-deterioration ``Λ`` (Eq. 13): ``Λc = Λk = 0.9·Λs`` control
    post-capping and unloading-stiffness deterioration (Lignos et al. 2019).
    (The *first-cycle envelope* / ASCE 41 curve uses different coefficients —
    see ``docs/imk_deterioration_model.md``.)

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
        theta_u: Explicit ultimate rotation (rad).  When ``None``, the
            *theta_u_model* convention is used; a non-``None`` value always
            takes precedence (benchmark calibration).
        theta_u_model: Ultimate-rotation convention, one of
            :data:`THETA_U_MODELS` (``"plateau"`` / ``"none"`` / ``"lignos"`` /
            ``"hybrid"``).  Default ``"plateau"`` (``θp + θpc + 0.02``).

    Note:
        Columns with ``Pg/Pye > 0.6`` are force-controlled (elastic) per ASCE 41;
        the caller should not assign a hinge in that case.
    """
    h = section_depth_m - 2.0 * flange_thickness_m
    h_tw = h / web_thickness_m
    lb_ry = unbraced_length_m / radius_of_gyration_m
    one_minus_axial = 1.0 - axial_ratio

    # Eq. 2 — effective yield moment (cyclic hardening + axial-load reduction).
    if axial_ratio <= 0.20:
        my = 1.15 * plastic_modulus_m3 * ry_factor * fy_pa * one_minus_axial
    else:
        my = 1.15 * plastic_modulus_m3 * ry_factor * fy_pa * (9.0 / 8.0) * one_minus_axial

    # Eq. 3 — capping ratio (hardening) a = Mc/My of the monotonic backbone,
    # clamped to 1.0 ≤ a ≤ 1.3 (the cap binds for stocky sections, h/tw < 15).
    mc_my = 12.5 * h_tw**-0.2 * lb_ry**-0.4 * one_minus_axial**0.4
    mc_my = min(max(mc_my, 1.0), 1.3)

    # Eqs. 7 & 9 — pre-/post-capping plastic rotations of the monotonic
    # backbone (capped at 0.20 / 0.30 rad respectively).
    theta_p = min(294.0 * h_tw**-1.7 * lb_ry**-0.7 * one_minus_axial**1.6, 0.20)
    theta_pc = min(90.0 * h_tw**-0.8 * lb_ry**-0.8 * one_minus_axial**2.5, 0.30)

    # Eq. 5 — residual strength ratio of the monotonic backbone.
    kappa = 0.5 - 0.4 * axial_ratio

    # Eq. 13 — reference cumulative plastic rotation Λs (basic-strength cyclic
    # deterioration).  Two branches split at Pg/Pye = 0.35, capped at 3.0 rad;
    # Λc and Λk (post-capping / unloading-stiffness) are 0.9·Λs.
    if axial_ratio <= 0.35:
        lamda_s = 25_500.0 * h_tw**-2.14 * lb_ry**-0.53 * one_minus_axial**4.92
    else:
        lamda_s = 268_000.0 * h_tw**-2.30 * lb_ry**-1.30 * one_minus_axial**1.19
    lamda_s = min(lamda_s, 3.0)
    lamda_c = 0.9 * lamda_s
    lamda_k = 0.9 * lamda_s

    theta_u = _resolve_theta_u(theta_u, theta_u_model, theta_p, theta_pc)

    return SteelIMKParams(
        My_pos=my,
        My_neg=-my,
        theta_p_pos=theta_p,
        theta_p_neg=theta_p,
        theta_pc_pos=theta_pc,
        theta_pc_neg=theta_pc,
        res_pos=kappa,
        res_neg=kappa,
        lamda_S=lamda_s,
        lamda_C=lamda_c,
        lamda_K=lamda_k,
        mc_my=mc_my,
        theta_u_pos=theta_u,
        theta_u_neg=theta_u,
        theta_u_model=theta_u_model,
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
    theta_u_model: str = "plateau",
) -> SteelIMKParams:
    """Compute ``IMKBilin`` parameters from the Lignos–Krawinkler (2011) regressions (A0).

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
        theta_u: Explicit ultimate rotation (rad).  When ``None``, the
            *theta_u_model* convention is used; a non-``None`` value always
            takes precedence (benchmark calibration).
        theta_u_model: Ultimate-rotation convention, one of
            :data:`THETA_U_MODELS` (``"plateau"`` / ``"none"`` / ``"lignos"`` /
            ``"hybrid"``).  Default ``"plateau"`` (``θp + θpc + 0.02``).
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
            theta_u_model=theta_u_model,
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
    theta_u = _resolve_theta_u(theta_u, theta_u_model, theta_p, theta_pc)

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
        theta_u_model=theta_u_model,
    )
