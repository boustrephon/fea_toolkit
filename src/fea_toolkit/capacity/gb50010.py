"""GB 50010-2010 reinforced-concrete member capacity functions.

Implements the nominal strength expressions of GB 50010-2010 *Code for Design
of Concrete Structures*:

* §6.2.1–6.2.10 — flexural capacity ``M_u`` of a doubly reinforced rectangular
  section (under-reinforced, ``alpha1 = 1.0`` for ≤ C50).
* §6.2.15 — axial compressive capacity ``N_u`` of an RC column.
* §6.3.4 — shear capacity ``V_u = V_c + V_s`` of frame members, including the
  ``0.07·N`` axial enhancement for compression members, capped by the §6.3.1
  strut limit.
* §6.3.1 / §7.3 — in-plane wall shear / normal stress checks.

The formulas mirror ``local/CLP_BSDG_Latest_Models/Admin_Building/
admin_pushover_checks_v8.py`` but are **unit-aware**: material strengths are
authored in SI (Pa) and scaled to the model unit system with
:func:`fea_toolkit.utils.stress_scale_factor`; section dimensions and demands
are in model units.  Capacities are returned in the model's force (or
force × length) units — the same system as the analysis results they are
compared against.

All functions take a ``units`` dict (``{"F": ..., "L": ..., "T": ...}``);
see :mod:`fea_toolkit.capacity`.
"""

import math
from dataclasses import dataclass
from typing import Any, Optional

from ._common import (
    CapacityResult,
    force_length_unit_label,
    force_unit_label,
    safe_float,
    stress_scale_factor,
)

# ═══════════════════════════════════════════════════════════════════
# Section geometry / strength resolution helpers
# ═══════════════════════════════════════════════════════════════════


def _depth_and_width(section: Any, width_fallback: bool = False) -> tuple[float, float]:
    """Return ``(depth, width)`` of a rectangular section (model units).

    Args:
        section: Section object exposing ``depth`` / ``bf``.
        width_fallback: When True and ``bf <= 0``, use ``depth`` as the width
            (mirrors the GB 50010 §6.3.4 ``b = max(bf, depth)`` convention).
    """
    d = safe_float(getattr(section, "depth", 0.0))
    b = safe_float(getattr(section, "bf", 0.0))
    if width_fallback and b <= 0.0:
        b = d
    return d, b


def _strength_pa(material: Any, attr: str, override_pa: Optional[float]) -> float:
    """Resolve a material strength to SI (Pa); an explicit override wins."""
    if override_pa is not None:
        return float(override_pa)
    value = getattr(material, attr, None)
    return float(value) if value else 0.0


def _model_stress(pa: float, units: dict) -> float:
    """Convert an SI (Pa) stress to the model's stress unit."""
    return pa * stress_scale_factor(units)


def _default_ft_pa(fc_pa: float) -> float:
    """GB 50010 §4.1.3-style tensile strength estimate (Pa) from fc (Pa).

    Uses the common ``f_t = 0.26·f_c^(2/3)`` approximation evaluated in MPa
    (the exponent makes the expression unit-specific).  Callers wanting the
    exact code value for a given grade should pass ``ft_pa`` explicitly.
    """
    return 0.26 * (fc_pa / 1e6) ** (2.0 / 3.0) * 1e6


# ═══════════════════════════════════════════════════════════════════
# Flexure — §6.2
# ═══════════════════════════════════════════════════════════════════


def moment_capacity(
    section: Any,
    concrete: Any,
    steel: Any,
    units: dict,
    *,
    fc_pa: Optional[float] = None,
    fy_pa: Optional[float] = None,
    top_bars: Optional[int] = None,
    bot_bars: Optional[int] = None,
    top_bar_dia: Optional[float] = None,
    bot_bar_dia: Optional[float] = None,
    cover: Optional[float] = None,
    fallback_elastic: bool = True,
) -> CapacityResult:
    """Nominal flexural capacity ``M_u`` (GB 50010 §6.2, under-reinforced).

    Args:
        section: Rectangular RC section — ``ConcreteRectangularSection`` or any
            object exposing ``depth``, ``bf``, ``cover``, ``top_bars`` /
            ``bot_bars``, ``top_bar_dia`` / ``bot_bar_dia`` and (for the
            elastic fallback) ``Z33``.
        concrete: Concrete ``Material`` (``Fc`` in Pa), or an object with a
            ``Fc`` attribute.
        steel: Longitudinal rebar ``Material`` (``Fy`` in Pa), or an object
            with a ``Fy`` attribute.
        units: Model units dict.
        fc_pa: Override concrete strength (Pa); defaults to ``concrete.Fc``.
        fy_pa: Override rebar yield (Pa); defaults to ``steel.Fy``.
        top_bars: Number of top bars (overrides ``section.top_bars``).
        bot_bars: Number of bottom bars (overrides ``section.bot_bars``).
        top_bar_dia: Top bar diameter, model length units (overrides section).
        bot_bar_dia: Bottom bar diameter, model length units (overrides section).
        cover: Clear cover to bar centreline, model length units.
        fallback_elastic: When rebar data is missing, fall back to the elastic
            extreme-fibre yield moment ``fy·Z33`` (default True).

    Returns:
        :class:`CapacityResult` in model force × length units; ``extra``
        carries ``h0``, ``x`` and the resolved bar areas.
    """
    d, b = _depth_and_width(section)
    if b <= 0.0 or d <= 0.0:
        return CapacityResult(0.0, force_length_unit_label(units), "GB 50010 §6.2")

    fy = _model_stress(_strength_pa(steel, "Fy", fy_pa), units)

    cv = cover if cover is not None else safe_float(getattr(section, "cover", 0.0))
    t_dia = (
        top_bar_dia if top_bar_dia is not None else safe_float(getattr(section, "top_bar_dia", 0.0))
    )
    b_dia = (
        bot_bar_dia if bot_bar_dia is not None else safe_float(getattr(section, "bot_bar_dia", 0.0))
    )
    n_top = int(top_bars if top_bars is not None else (getattr(section, "top_bars", 0) or 0))
    n_bot = int(bot_bars if bot_bars is not None else (getattr(section, "bot_bars", 0) or 0))

    h0 = max(d - cv, 0.0)

    # No rebar data → elastic extreme-fibre yield fallback
    if n_top <= 0 or n_bot <= 0 or (t_dia <= 0.0 and b_dia <= 0.0):
        if fallback_elastic:
            z = safe_float(getattr(section, "Z33", 0.0))
            if z > 0.0:
                return CapacityResult(
                    fy * z,
                    force_length_unit_label(units),
                    "GB 50010 §6.2 (elastic fallback)",
                    extra={"h0": h0},
                )
        return CapacityResult(
            0.0, force_length_unit_label(units), "GB 50010 §6.2", extra={"h0": h0}
        )

    fc = _model_stress(_strength_pa(concrete, "Fc", fc_pa), units)
    if fc <= 0.0:
        return CapacityResult(
            0.0, force_length_unit_label(units), "GB 50010 §6.2", extra={"h0": h0}
        )

    a_top = n_top * math.pi * (t_dia / 2.0) ** 2 if t_dia > 0.0 else 0.0
    a_bot = n_bot * math.pi * (b_dia / 2.0) ** 2 if b_dia > 0.0 else 0.0

    a_s = max(cv + max(t_dia, b_dia) / 2.0, 0.02)
    h0 = d - a_s
    alpha1 = 1.0  # ≤ C50

    a_tens = max(a_top, a_bot)  # tension layer (bottom assumed)
    a_comp = min(a_top, a_bot)  # compression layer (top)

    x = (fy * a_tens - fy * a_comp) / (alpha1 * fc * b)
    x = max(x, 2.0 * a_s)
    x = min(x, 0.55 * h0)  # ductility bound ξb

    mu = alpha1 * fc * b * x * (h0 - x / 2.0) + fy * a_comp * (h0 - a_s)
    return CapacityResult(
        mu,
        force_length_unit_label(units),
        "GB 50010 §6.2.10",
        extra={"h0": h0, "x": x, "a_top": a_top, "a_bot": a_bot},
    )


# ═══════════════════════════════════════════════════════════════════
# Axial — §6.2.15
# ═══════════════════════════════════════════════════════════════════


def axial_capacity(
    section: Any,
    concrete: Any,
    steel: Any,
    units: dict,
    *,
    fc_pa: Optional[float] = None,
    fy_pa: Optional[float] = None,
    rho: float = 0.006,
) -> CapacityResult:
    """Nominal axial compressive capacity ``N_u`` (GB 50010 §6.2.15).

    Args:
        section: RC section; the gross area ``A`` is used when positive,
            otherwise ``bf·depth``.
        concrete: Concrete ``Material`` (``Fc`` in Pa).
        steel: Longitudinal rebar ``Material`` (``Fy`` in Pa).
        units: Model units dict.
        fc_pa: Override concrete strength (Pa).
        fy_pa: Override rebar yield (Pa).
        rho: Longitudinal steel ratio used when the section carries no
            explicit rebar data (default 0.006, i.e. 0.6 %).

    Returns:
        :class:`CapacityResult` in model force units.
    """
    ag = safe_float(getattr(section, "A", 0.0))
    if ag <= 0.0:
        d, b = _depth_and_width(section)
        ag = b * d

    fc = _model_stress(_strength_pa(concrete, "Fc", fc_pa), units)
    fy = _model_stress(_strength_pa(steel, "Fy", fy_pa), units)
    a_steel = rho * ag
    nu = 0.9 * (fc * ag + fy * a_steel)
    return CapacityResult(
        nu,
        force_unit_label(units),
        "GB 50010 §6.2.15",
        extra={"a_gross": ag, "a_steel": a_steel, "rho": rho},
    )


# ═══════════════════════════════════════════════════════════════════
# Shear — §6.3.4
# ═══════════════════════════════════════════════════════════════════


def shear_capacity(
    section: Any,
    concrete: Any,
    steel: Any,
    units: dict,
    *,
    fc_pa: Optional[float] = None,
    ft_pa: Optional[float] = None,
    fy_pa: Optional[float] = None,
    tie_fy_pa: Optional[float] = None,
    tie_dia: Optional[float] = None,
    tie_spacing: Optional[float] = None,
    axial: float = 0.0,
    is_column: bool = True,
    lambda_param: float = 3.0,
) -> CapacityResult:
    """Nominal shear capacity ``V_u = V_c + V_s`` (GB 50010 §6.3.4).

    ``V_c`` uses the §6.3.4 expressions (``0.7·f_t·b·h0`` for flexural members;
    ``1.75/(λ+1)·f_t·b·h0 + 0.07·N`` for compression members with λ clamped to
    [1.5, 3.0]).  ``V_s = f_yv·A_sw/s·h0`` uses a two-leg stirrup.  The result
    is capped by the §6.3.1 strut limit ``0.25·f_c·b·h0``.

    Args:
        section: Rectangular RC section exposing ``depth``, ``bf``, ``cover``
            and optionally ``tie_diameter`` / ``tie_spacing``.
        concrete: Concrete ``Material`` (``Fc`` in Pa).
        steel: Rebar ``Material`` (``Fy`` in Pa); also the default tie steel.
        units: Model units dict.
        fc_pa: Override concrete strength (Pa).
        ft_pa: Override concrete tensile strength (Pa); defaults to a
            §4.1.3-style estimate from ``fc``.
        fy_pa: Override longitudinal rebar yield (Pa).
        tie_fy_pa: Override stirrup yield (Pa); defaults to the longitudinal
            rebar yield.
        tie_dia: Stirrup diameter, model length units (overrides section).
        tie_spacing: Stirrup spacing, model length units (overrides section).
        axial: Axial force at the section, compression positive (model force).
        is_column: Treat the member as a compression member (§6.3.4 column
            branch with the ``0.07·N`` enhancement).
        lambda_param: Shear-span ratio λ for columns, clamped to [1.5, 3.0]
            (default 3.0).

    Returns:
        :class:`CapacityResult` in model force units.
    """
    d, b = _depth_and_width(section, width_fallback=True)
    cv = safe_float(getattr(section, "cover", 0.0))
    if cv <= 0.0:
        cv = 0.04
    h0 = d - cv
    if b <= 0.0 or h0 <= 0.0:
        return CapacityResult(0.0, force_unit_label(units), "GB 50010 §6.3.4")

    fc_pa_val = _strength_pa(concrete, "Fc", fc_pa)
    fc = _model_stress(fc_pa_val, units)
    ft = _model_stress(ft_pa if ft_pa is not None else _default_ft_pa(fc_pa_val), units)
    fy_pa_val = _strength_pa(steel, "Fy", fy_pa)
    tie_fy = _model_stress(tie_fy_pa if tie_fy_pa is not None else fy_pa_val, units)

    t_dia = (
        tie_dia
        if tie_dia is not None
        else (safe_float(getattr(section, "tie_diameter", 0.0)) or 0.010)
    )
    t_s = (
        tie_spacing
        if tie_spacing is not None
        else (safe_float(getattr(section, "tie_spacing", 0.0)) or 0.300)
    )

    a_bar = math.pi * (t_dia / 2.0) ** 2
    asw = 2.0 * a_bar  # two legs crossing the shear plane

    lam = max(1.5, min(3.0, lambda_param))
    if is_column:
        vc = (1.75 / (lam + 1.0)) * ft * b * h0
        if axial > 0.0:
            vc += 0.07 * axial
    else:
        vc = 0.7 * ft * b * h0
    vs = tie_fy * asw / t_s * h0
    vu = vc + vs
    if fc > 0.0:
        vu = min(vu, 0.25 * fc * b * h0)  # §6.3.1 strut limit

    return CapacityResult(
        vu,
        force_unit_label(units),
        "GB 50010 §6.3.4",
        extra={"h0": h0, "vc": vc, "vs": vs, "lambda": lam},
    )


# ═══════════════════════════════════════════════════════════════════
# Walls — §6.3.1 / §7.3
# ═══════════════════════════════════════════════════════════════════


@dataclass
class WallShearCheckResult:
    """Result of a GB 50010 in-plane wall stress check.

    Attributes:
        tau: In-plane shear stress (force / area, model units).
        sigma: Vertical normal stress (force / area, model units).
        tau_limit: Governing shear limit = min(strut, diagonal-tension) limit.
        sigma_limit: Normal-stress limit.
        ok_shear: ``True`` when ``tau <= tau_limit``.
        ok_normal: ``True`` when ``sigma <= sigma_limit``.
    """

    tau: float
    sigma: float
    tau_limit: float
    sigma_limit: float
    ok_shear: bool
    ok_normal: bool


def wall_shear_check(
    Nxy: float,
    Ny: float,
    t: float,
    concrete: Any,
    units: dict,
    *,
    fc_pa: Optional[float] = None,
    ft_pa: Optional[float] = None,
    tie_fy_pa: Optional[float] = None,
    tie_dia: float = 0.010,
    tie_spacing: float = 0.300,
    strut_factor: float = 0.20,
    sigma_limit_factor: float = 0.40,
) -> WallShearCheckResult:
    """In-plane shear / normal stress check for an RC wall panel.

    Args:
        Nxy: In-plane membrane shear resultant (force / unit width, model units).
        Ny: Vertical membrane resultant (force / unit width, model units).
        t: Wall thickness (model length units).
        concrete: Concrete ``Material`` (``Fc`` in Pa).
        units: Model units dict.
        fc_pa: Override concrete strength (Pa).
        ft_pa: Override concrete tensile strength (Pa); defaults to a
            §4.1.3-style estimate from ``fc``.
        tie_fy_pa: Web/tie rebar yield (Pa).  Omit (or 0) for no web steel.
        tie_dia: Web rebar diameter, model length units.
        tie_spacing: Web rebar spacing, model length units.
        strut_factor: Compression-strut stress coefficient (default 0.20).
        sigma_limit_factor: Normal-stress limit coefficient (default 0.40).

    Returns:
        :class:`WallShearCheckResult`.
    """
    if t <= 0.0:
        return WallShearCheckResult(0.0, 0.0, 0.0, 0.0, False, False)

    tau = abs(Nxy) / t
    sigma = abs(Ny) / t

    fc_pa_val = _strength_pa(concrete, "Fc", fc_pa)
    fc = _model_stress(fc_pa_val, units)
    ft = _model_stress(ft_pa if ft_pa is not None else _default_ft_pa(fc_pa_val), units)
    tie_fy = _model_stress(tie_fy_pa if tie_fy_pa is not None else 0.0, units)

    tau_lim = strut_factor * fc  # §6.3.1 strut limit
    a_tie = math.pi * (tie_dia / 2.0) ** 2
    rho_sh = 2.0 * a_tie / tie_spacing / t  # both faces, per unit height
    tau_cap = 0.7 * ft + tie_fy * rho_sh
    tau_limit = min(tau_lim, tau_cap)

    sigma_lim = sigma_limit_factor * fc
    return WallShearCheckResult(
        tau,
        sigma,
        tau_limit,
        sigma_lim,
        tau <= tau_limit,
        sigma <= sigma_lim,
    )
