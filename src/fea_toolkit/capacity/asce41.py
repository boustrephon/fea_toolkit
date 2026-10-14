"""ASCE 41-17 member capacity functions.

Implements the nominal strength / deformation expressions of ASCE/SEI 41-17
*Seismic Evaluation and Retrofit of Existing Buildings*:

* §10.8 — plastic hinge length ``L_p`` (Eqs. 10-1 / 10-2 for steel and brace
  members; the ACI 440-style RC expression for reinforced concrete), capped
  at ``0.33·L``.

Aligned with the rest of :mod:`fea_toolkit.capacity` (see
:mod:`fea_toolkit.capacity._common`): material strengths are authored in SI
(Pa), section dimensions and ``elem_length`` are in model units, and the
result is returned in model length units.  The legacy
``(md, sec_name, elem_length)`` entry point survives as
:func:`fea_toolkit.model.checks.compute_asce41_hinge_length`, which resolves
the section/material from the model, converts the material strengths to Pa,
and delegates here.

Unit convention
---------------
Material strengths are authored in SI (Pa); section dimensions are converted
from model length → m / mm with :func:`fea_toolkit.utils.length_to_si_factor`,
and the result is returned in model length units.  Fallback material
constants are authored in SI (Pa) (see :data:`DEFAULT_FY_STEEL_PA` /
:data:`DEFAULT_FC_PA`).
"""

from typing import Any

from ..model.sap_data import ConcreteRectangularSection
from ..utils import (
    DEFAULT_FC_PA,
    DEFAULT_FY_STEEL_PA,
    length_to_si_factor,
)

__all__ = ["hinge_length"]


def hinge_length(
    section: Any,
    concrete: Any,
    steel: Any,
    units: dict,
    *,
    elem_length: float,
) -> float:
    """Plastic hinge length ``L_p`` per ASCE 41-17 §10.8 (model length units).

    Computes ``L_p`` from the member's material and section properties:
    ``0.05·L + 0.1·d_b·f_y/√f_c`` for RC (ACI 440-style), ``0.08·L +
    0.015·d_b·f_y`` for braces, and ``0.08·L + 0.022·d_b·f_y`` for steel
    members — capped at ``0.33·L``.  Falls back to ``0.1·L`` when the section
    is missing.

    Args:
        section: Section object (dimensions in model length units).  ``None``
            yields the ``0.1·L`` fallback.
        concrete: Concrete ``Material`` (``Fc`` in Pa) or ``None`` (falls back
            to :data:`DEFAULT_FC_PA`).
        steel: Steel / rebar ``Material`` (``Fy`` in Pa) or ``None`` (falls
            back to :data:`DEFAULT_FY_STEEL_PA`).
        units: Model units dict (``{"F": ..., "L": ..., "T": ...}``).
        elem_length: Element length in model units.

    Returns:
        Plastic hinge length in model length units.
    """
    if section is None:
        return max(0.05, elem_length * 0.1)

    lf = length_to_si_factor(units)

    fy_pa = (getattr(steel, "Fy", 0.0) or 0.0) or DEFAULT_FY_STEEL_PA
    fc_pa = (getattr(concrete, "Fc", 0.0) or 0.0) or DEFAULT_FC_PA
    fy_mpa = fy_pa / 1e6
    fc_mpa = fc_pa / 1e6

    # ── Convert section dimensions from model length units → mm ──
    # Use an explicit ConcreteRectangularSection check rather than generic
    # attribute-based detection which can misidentify sections.
    is_concrete = isinstance(section, ConcreteRectangularSection)
    is_brace = hasattr(section, "od") or hasattr(section, "t")

    def _to_mm(val: float) -> float:
        """Convert a value in model length units to mm."""
        return val * lf * 1000.0

    if is_concrete:
        # ASCE 41-17 d_b = longitudinal rebar diameter (mm)
        if (getattr(section, "top_bar_dia", None) or 0) > 0:
            db = _to_mm(section.top_bar_dia)
        elif (getattr(section, "bar_dia", None) or 0) > 0:
            db = _to_mm(section.bar_dia)
        else:
            db = 20.0  # fallback rebar diameter in mm
    # Steel: db = section depth in the loading direction (mm).
    # ASCE 41-17 §9.3.3.2 uses overall section depth or OD for steel
    # members — flange thickness (tf) and wall thickness (t) are not
    # valid d_b terms.
    elif (getattr(section, "depth", None) or 0) > 0:
        db = _to_mm(section.depth)
    elif (getattr(section, "od", None) or 0) > 0:
        db = _to_mm(section.od)
    else:
        db = 20.0  # fallback in mm

    # ── ASCE 41-17 formula §10.8 (convert elem_length to metres) ──
    L_m = elem_length * lf

    if is_concrete:
        Lp = 0.05 * L_m + 0.1 * db * fy_mpa / max(fc_mpa, 1.0) ** 0.5 / 1000.0
    elif is_brace:
        Lp = 0.08 * L_m + 0.015 * db * fy_mpa / 1000.0
    else:
        Lp = 0.08 * L_m + 0.022 * db * fy_mpa / 1000.0

    # Lp from formula is in metres — clamp and convert back to model units.
    Lp_m = min(Lp, 0.33 * L_m)
    return Lp_m / lf if lf != 0 else Lp_m
