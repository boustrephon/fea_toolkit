"""ASCE 41-17 member capacity functions.

Implements the nominal strength / deformation expressions of ASCE/SEI 41-17
*Seismic Evaluation and Retrofit of Existing Buildings*:

* §10.8 — plastic hinge length ``L_p`` (Eqs. 10-1 / 10-2 for steel and brace
  members; the ACI 440-style RC expression for reinforced concrete), capped
  at ``0.33·L``.

Migrated verbatim from ``fea_toolkit.model.checks.compute_asce41_hinge_length``
(kept there as a legacy delegation wrapper).  ``hinge_length()`` below retains
the original ``(md, sec_name, elem_length)`` model-data-coupled signature and
its exact conversion logic so the migration is behaviour-preserving;
aligning it to the unit-aware ``(section, concrete, steel, units, ...)``
convention is a documented follow-up.

Unit convention
---------------
The hinge-length formula is unit-correct: material strengths are converted
from the model's stress units → Pa (then MPa) with
:func:`fea_toolkit.utils.stress_to_si_factor`, section dimensions from model
length → m / mm with :func:`fea_toolkit.utils.length_to_si_factor`, and the
result is returned in model length units.  Fallback material constants are
authored in SI (Pa) and bypass the unit conversion (see
:data:`DEFAULT_FY_STEEL_PA` / :data:`DEFAULT_FC_PA`).
"""

from typing import Any

from ..model.sap_data import ConcreteRectangularSection
from ..utils import (
    DEFAULT_FC_PA,
    DEFAULT_FY_STEEL_PA,
    length_to_si_factor,
    stress_to_si_factor,
)

__all__ = ["hinge_length"]


def _get_conversion_factors(md: Any) -> tuple:
    """Extract stress and length conversion factors (model → SI).

    Works with both ``SAPModelData`` (``stress_factor`` / ``length_factor``
    properties) and ``MeshModel`` (units dict).  Both branches return the
    same quantity: the factor that converts **model units → SI** (Pa and
    metres respectively).

    Args:
        md: Any model-like object exposing either ``stress_factor`` /
            ``length_factor`` or a ``units`` dict.

    Returns:
        ``(stress_factor, length_factor)`` as floats (model → SI).
    """
    if hasattr(md, "stress_factor") and hasattr(md, "length_factor"):
        return md.stress_factor, md.length_factor
    units = getattr(md, "units", {}) or {}
    return stress_to_si_factor(units), length_to_si_factor(units)


def hinge_length(md: Any, sec_name: str, elem_length: float) -> float:
    """Plastic hinge length ``L_p`` per ASCE 41-17 §10.8 (model length units).

    Computes ``L_p`` from the member's material and section properties:
    ``0.05·L + 0.1·d_b·f_y/√f_c`` for RC (ACI 440-style), ``0.08·L +
    0.015·d_b·f_y`` for braces, and ``0.08·L + 0.022·d_b·f_y`` for steel
    members — capped at ``0.33·L``.  Falls back to ``0.1·L`` when section or
    material data is unavailable.

    Material strengths are taken from the model's unit system and converted
    to SI (Pa) internally; the result is returned in model length units.

    Args:
        md: ``SAPModelData`` or ``MeshModel`` for section/material lookup and
            unit conversion.  Missing material strengths fall back to the SI
            defaults (250 MPa steel / 30 MPa concrete, authored in Pa).
        sec_name: Section name for material and geometry lookup.
        elem_length: Element length in model units.

    Returns:
        Plastic hinge length in model length units.
    """
    sec = md.sections.get(sec_name)
    if sec is None:
        return max(0.05, elem_length * 0.1)

    mat = md.materials.get(sec.material)
    if mat is None:
        return max(0.05, elem_length * 0.1)

    sf, lf = _get_conversion_factors(md)

    # ── Convert material strengths from model stress units → Pa → MPa ──
    # Model-provided values are in model stress units and need sf;
    # fallback constants are already in Pa and bypass sf.
    fy_pa = mat.Fy * sf if (mat.Fy or 0) > 0 else DEFAULT_FY_STEEL_PA
    fc_pa = mat.Fc * sf if (mat.Fc or 0) > 0 else DEFAULT_FC_PA
    fy_mpa = fy_pa / 1e6
    fc_mpa = fc_pa / 1e6

    # ── Convert section dimensions from model length units → mm ──
    # Use an explicit ConcreteRectangularSection check rather than generic
    # attribute-based detection which can misidentify sections.
    is_concrete = isinstance(sec, ConcreteRectangularSection)
    is_brace = hasattr(sec, "od") or hasattr(sec, "t")

    def _to_mm(val: float) -> float:
        """Convert a value in model length units to mm."""
        return val * lf * 1000.0

    if is_concrete:
        # ASCE 41-17 d_b = longitudinal rebar diameter (mm)
        if (getattr(sec, "top_bar_dia", None) or 0) > 0:
            db = _to_mm(sec.top_bar_dia)
        elif (getattr(sec, "bar_dia", None) or 0) > 0:
            db = _to_mm(sec.bar_dia)
        else:
            db = 20.0  # fallback rebar diameter in mm
    # Steel: db = section depth in the loading direction (mm).
    # ASCE 41-17 §9.3.3.2 uses overall section depth or OD for steel
    # members — flange thickness (tf) and wall thickness (t) are not
    # valid d_b terms.
    elif (getattr(sec, "depth", None) or 0) > 0:
        db = _to_mm(sec.depth)
    elif (getattr(sec, "od", None) or 0) > 0:
        db = _to_mm(sec.od)
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
