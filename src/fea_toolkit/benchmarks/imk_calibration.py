"""Lignos–Krawinkler steel IMK (``Bilin``) calibration data — A0 scaffolding.

The modified Ibarra–Medina–Krawinkler (IMK) deterioration model with *bilinear*
hysteretic response is OpenSees' ``Bilin`` uniaxial material, calibrated for
steel beam-to-column connections by Lignos & Krawinkler (2011),
DOI ``10.1061/(ASCE)ST.1943-541X.0000376``.

This module defines the parameter container and the A0 entry point.  The
empirical regression equations themselves are **not yet transcribed** — the
coefficients (and, critically, the *sign* of each exponent) must be read from
the primary source, since secondary OCR is unreliable for signs.  Until then
:func:`lignos_krawinkler_2011` raises ``NotImplementedError``; the hinge
*machinery* (A1) in ``opensees/_elements.py`` is complete and runs from a
hand-specified ``imk_params`` dict.

See ``docs/imk_deterioration_model.md`` for the design record.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Canonical keys of the ``imk_params`` dict stamped onto a section.  Kept in
#: sync with :meth:`SteelIMKParams.to_dict` and ``opensees/_elements.py``.
IMK_PARAM_KEYS: tuple[str, ...] = (
    "as_pos",
    "as_neg",
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
    *positive* magnitudes, as ``Bilin`` expects.
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
    as_pos: float = 0.03
    as_neg: float = 0.03
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

    def as_bilin_args(self, k0: float) -> tuple[float, ...]:
        """The 23 positional ``Bilin`` arguments (``matTag`` and ``nFactor`` excluded).

        Order matches ``Bilin matTag K0 as_pos as_neg My_pos My_neg Lamda_S
        Lamda_C Lamda_A Lamda_K c_S c_C c_A c_K theta_p_pos theta_p_neg
        theta_pc_pos theta_pc_neg Res_pos Res_neg theta_u_pos theta_u_neg
        D_pos D_neg``.  ``Lamda_A`` / ``c_A`` (accelerated reloading) are not a
        deterioration mode for a bilinear response and are forced to ``0.0`` /
        ``1.0``.
        """
        return (
            k0,
            self.as_pos,
            self.as_neg,
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
    axial_ratio: float = 0.0,
    rbs: bool = False,
    column: bool = False,
) -> SteelIMKParams:
    """Compute ``Bilin`` parameters from the Lignos–Krawinkler (2011) regressions (A0).

    Args:
        section_depth_m: Overall section depth ``d`` (m).
        web_thickness_m: Web thickness ``tw`` (m).
        flange_width_m: Flange width ``bf`` (m).
        flange_thickness_m: Flange thickness ``tf`` (m).
        radius_of_gyration_m: Weak-axis radius of gyration ``ry`` (m).
        unbraced_length_m: Unbraced length ``Lb`` (m).
        member_length_m: Member length ``L`` (m).
        fy_pa: Yield strength (Pa).
        axial_ratio: Column axial-load ratio ``P/Pye`` (columns only).
        rbs: True for reduced-beam-section beams.
        column: True for a column (else a beam).

    Raises:
        NotImplementedError: The regression coefficients are not yet transcribed
            from the primary source (the signs of the exponents cannot be trusted
            from secondary OCR).  Transcribe Eqs. 4–14 of Lignos & Krawinkler
            (2011), DOI ``10.1061/(ASCE)ST.1943-541X.0000376`` (see
            ``docs/imk_deterioration_model.md`` → *Calibration equations to
            transcribe*), then implement here.
    """
    raise NotImplementedError(
        "The Lignos-Krawinkler (2011) calibration equations (theta_p, theta_pc, "
        "Lambda) for RBS/non-RBS beams and columns are not yet transcribed — the "
        "regression coefficients and their exponent signs must be read from the "
        "primary source, DOI 10.1061/(ASCE)ST.1943-541X.0000376 (Eqs. 4-14). "
        "See docs/imk_deterioration_model.md for the exact equations to transcribe."
    )
