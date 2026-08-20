"""Elwood & Moehle column limit-state parameters and drift-capacity models.

This module implements the empirical drift-capacity models of Elwood &
Moehle (PEER 2003/01) for shear-critical RC columns, in a **unit-agnostic**
way that mirrors what OpenSees' ``limitCurve`` / ``LimitState`` commands do
internally.  It is the physics layer behind the toolkit's column
shear/axial-failure modelling (see ``docs/shear_failure_modelling.md``,
"Phase 3 -- Elwood & Moehle column limit states").

The OpenSees ``limitCurve`` commands embed imperial units (``f'c`` in psi,
forces in kip, lengths in in).  The empirical functions here therefore
rescale model quantities to that kip-in-psi basis internally via the unit
factors from :mod:`fea_toolkit.utils`, so callers work in model units
throughout and the predicted **drift ratios** (dimensionless) are identical
in every unit system.

Key equations (transcribed from the OpenSees source so the toolkit's
reference functions match what ``ShearCurve`` / ``AxialCurve`` do):

* **Drift at shear failure** -- ``ShearCurve::findLimit(DR)`` solved for the
  drift at a given shear ``V`` and axial ``P``::

      V = 500 * (0.03 + 4*rho - DR - 0.025*P/(b*h*(fc/1000))) * (b*d*sqrt(fc)/1000)

  with ``V``, ``P`` in kip; ``b``, ``h``, ``d`` in in; ``fc`` in psi; and a
  floor at ``DR >= 0.01`` (no shear failure below 1% drift).

* **Drift at axial failure** -- ``AxialCurve::findLimit`` (shear-friction
  model, crack angle :math:`\\theta = 65\\degree`)::

      P = ((1 + tan^2(theta)) / (25*DR) - tan(theta)) * Fsw * tan(theta)

  where :math:`F_{sw} = A_{st} f_{yt} d_c / s`.

References
----------
* Elwood, K.J. & Moehle, J.P. (2003). *Shake Table Tests and Analytical
  Studies on the Gravity Load Collapse of Reinforced Concrete Frames*,
  PEER 2003/01, UC Berkeley.
* OpenSees source: ``SRC/material/uniaxial/limitState/limitCurve/``
  (``ShearCurve.cpp``, ``AxialCurve.cpp``, ``ThreePointCurve.cpp``).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Optional

from ..model.sap_data import Section
from ..utils import force_scale_factor, length_scale_factor, stress_scale_factor
from .shear_capacity import DEFAULT_TIE_LEG_COUNT, section_shear_geometry

# ═════════════════════════════════════════════════════════════════════
# Imperial anchor constants (the OpenSees limitCurve basis)
# ═════════════════════════════════════════════════════════════════════
_IN_PER_M = 1.0 / 0.0254  # 39.3701
_KIP_PER_N = 1.0 / 4448.0
_PSI_PER_PA = 1.0 / 6894.76

ELWOOD_SHEAR_THETA_DEG = 65.0  # shear-friction crack angle (deg)
ELWOOD_AXIAL_SLOPE_FACTOR = 99.0  # axial spring = factor * Ec*Ag/L
ELWOOD_AXIAL_KDEG_RATIO = -0.02  # Kdeg_axial = ratio * Ec*Ag/L
ELWOOD_SURFACE_HIGH_DRIFT = 0.08  # 8% drift: upper end of axial surface fit
ELWOOD_SURFACE_LOW_DRIFT = 0.005  # drift used for the high-force plateau point


# ── Imperial rescaling helpers (model units -> kip-in-psi) ──────────


def _to_inch(value: float, units: dict) -> float:
    """Convert a model length to inches."""
    return value * _IN_PER_M / length_scale_factor(units)


def _to_kip(value: float, units: dict) -> float:
    """Convert a model force to kips."""
    return value * _KIP_PER_N / force_scale_factor(units)


def _to_psi(value: float, units: dict) -> float:
    """Convert a model stress to psi."""
    return value * _PSI_PER_PA / stress_scale_factor(units)


# ═════════════════════════════════════════════════════════════════════
# Dataclasses
# ═════════════════════════════════════════════════════════════════════


@dataclass
class ElwoodColumnGeometry:
    """Column shear geometry and transverse steel data in model units.

    Args:
        b: Section width (model length).
        h: Section depth (model length).
        d: Effective depth to longitudinal steel (model length).
        dc: Core depth measured between tie centrelines, parallel to the
            applied shear (model length).
        ast: Transverse-reinforcement area per tie plane (model length^2),
            i.e. ``n_legs * A_bar``.
        s: Tie spacing (model length).
        fyt: Tie yield stress (model stress).
        fc: Concrete compressive strength (model stress, positive).
    """

    b: float = 0.0
    h: float = 0.0
    d: float = 0.0
    dc: float = 0.0
    ast: float = 0.0
    s: float = 0.0
    fyt: float = 0.0
    fc: float = 0.0

    @property
    def rho(self) -> float:
        """Transverse reinforcement ratio ``rho'' = A_st/(b*s)`` used by the
        empirical drift-capacity model (the ``4*rho''`` term).  Falls back
        to ``A_st/(b*h)`` when the tie spacing is unknown."""
        if self.b <= 0.0:
            return 0.0
        s = self.s if self.s > 0.0 else self.h
        return self.ast / (self.b * s)

    @property
    def fsw(self) -> float:
        """Transverse-reinforcement term ``A_st * f_yt * d_c / s`` (model
        force)."""
        return self.ast * self.fyt * self.dc / self.s if self.s > 0.0 else 0.0


@dataclass
class ElwoodColumnParameters:
    """Elwood limit-state parameters in model units.

    Args:
        geometry: The :class:`ElwoodColumnGeometry` this was derived from.
        fsw: Transverse-reinforcement term ``A_st*f_yt*d_c/s``.
        rho: Transverse ratio ``A_st/(b*s)`` (the empirical ``rho''``).
        shear_elastic_slope: Elastic shear-spring slope ``G*A_v/L``
            (model force / model length).
        axial_elastic_slope: Axial-spring slope ``factor * E_c*A_g/L``
            (model force / model length).
        kdeg_shear: Shear post-failure / unloading slope (model F/L).
        fres_shear: Residual shear capacity (model force).
        kdeg_axial: Axial post-failure slope (model F/L), negative.
        fres_axial: Residual axial capacity (model force).
    """

    geometry: ElwoodColumnGeometry
    fsw: float = 0.0
    rho: float = 0.0
    shear_elastic_slope: float = 0.0
    axial_elastic_slope: float = 0.0
    kdeg_shear: float = 0.0
    fres_shear: float = 0.0
    kdeg_axial: float = 0.0
    fres_axial: float = 0.0


# ═════════════════════════════════════════════════════════════════════
# Geometry / parameter extraction
# ═════════════════════════════════════════════════════════════════════


def elwood_column_geometry(
    section: Section,
    concrete: Any,
    tie: Optional[Any] = None,
    tie_legs: int = DEFAULT_TIE_LEG_COUNT,
    core_depth: Optional[float] = None,
) -> ElwoodColumnGeometry:
    """Build :class:`ElwoodColumnGeometry` from a frame section.

    Reuses :func:`fea_toolkit.analysis.shear_capacity.section_shear_geometry`
    for the web/depth/effective-depth/spacing/transverse-area extraction and
    reads the concrete and tie strengths from the supplied materials.

    Args:
        section: Frame section (e.g. ``ConcreteRectangularSection``).
        concrete: Concrete ``Material`` (``Fc`` in model stress units).
        tie: Transverse rebar ``Material`` (``Fy``); ``section.tie_fy`` wins
            when both are present.
        tie_legs: Number of tie legs crossing the shear plane (default 2).
        core_depth: Core depth ``d_c`` (model length) measured between tie
            centrelines.  When ``None``, derived as
            ``h - 2*cover + tie_diameter`` (tie centreline at clear cover).

    Returns:
        The extracted geometry; missing data yields ``0.0`` fields.
    """
    g = section_shear_geometry(section, tie_legs=tie_legs)
    fc = float(getattr(concrete, "Fc", 0.0) or 0.0)
    fyt = float(getattr(section, "tie_fy", 0.0) or 0.0) or float(getattr(tie, "Fy", 0.0) or 0.0)
    h = float(getattr(section, "depth", 0.0) or 0.0)
    cover = float(getattr(section, "cover", 0.0) or 0.0)
    tie_dia = float(getattr(section, "tie_diameter", 0.0) or 0.0)
    dc = max(h - 2.0 * cover + tie_dia, 0.0) if core_depth is None else max(float(core_depth), 0.0)
    return ElwoodColumnGeometry(
        b=g.bw,
        h=g.h,
        d=g.d,
        dc=dc,
        ast=g.av,
        s=g.s,
        fyt=fyt,
        fc=fc,
    )


def elwood_spring_slopes(
    concrete: Any,
    geometry: ElwoodColumnGeometry,
    column_length: float,
    axial_factor: float = ELWOOD_AXIAL_SLOPE_FACTOR,
    poisson: Optional[float] = None,
) -> tuple[float, float]:
    """Elastic slopes of the Elwood shear and axial springs (model F/L).

    Shear spring: ``G*A_v/L`` with ``G = E_c / (2*(1+nu))`` and ``A_v``
    approximated by the gross area ``b*h`` (the uncracked-column value used
    by Elwood).  Axial spring: ``factor * E_c*A_g/L`` (99x stiffer than the
    column so it adds no axial flexibility before failure).

    Args:
        concrete: Concrete ``Material`` (``E_mod`` in model stress units).
        geometry: Column geometry.
        column_length: Element length (model length).
        axial_factor: Axial-spring stiffness factor (default 99).
        poisson: Poisson ratio; ``concrete.nu`` is used when ``None``.

    Returns:
        ``(shear_slope, axial_slope)`` in model force / model length.
    """
    ec = float(getattr(concrete, "E_mod", 0.0) or 0.0)
    nu = poisson if poisson is not None else float(getattr(concrete, "nu", 0.0) or 0.0)
    if column_length <= 0.0 or ec <= 0.0:
        return 0.0, 0.0
    g_mod = ec / (2.0 * (1.0 + nu)) if nu >= 0.0 else ec / 2.0
    ag = geometry.b * geometry.h
    shear_slope = g_mod * ag / column_length
    axial_slope = axial_factor * ec * ag / column_length
    return shear_slope, axial_slope


def elwood_axial_deg_slope(
    concrete: Any,
    geometry: ElwoodColumnGeometry,
    column_length: float,
    ratio: float = ELWOOD_AXIAL_KDEG_RATIO,
) -> float:
    """Post-failure axial-spring degrading slope ``ratio * E_c*A_g/L``.

    Elwood reports ``K_deg = -0.02 E_c A_g / L`` for the example column
    (PEER 2003/01, Section 4.5.3).  The result is negative (capacity drops
    as the axial deformation grows).

    Args:
        concrete: Concrete ``Material`` (``E_mod``).
        geometry: Column geometry.
        column_length: Element length (model length).
        ratio: Slope ratio (default -0.02).

    Returns:
        Degrading slope in model force / model length (negative).
    """
    ec = float(getattr(concrete, "E_mod", 0.0) or 0.0)
    if column_length <= 0.0 or ec <= 0.0:
        return 0.0
    return ratio * ec * geometry.b * geometry.h / column_length


def elwood_column_parameters(
    section: Section,
    concrete: Any,
    tie: Optional[Any] = None,
    column_length: Optional[float] = None,
    tie_legs: int = DEFAULT_TIE_LEG_COUNT,
    core_depth: Optional[float] = None,
    axial_factor: float = ELWOOD_AXIAL_SLOPE_FACTOR,
    kdeg_axial_ratio: float = ELWOOD_AXIAL_KDEG_RATIO,
    kdeg_shear: Optional[float] = None,
    fres_shear: Optional[float] = None,
    fres_axial: Optional[float] = None,
    shear_residual_ratio: float = 0.20,
) -> ElwoodColumnParameters:
    """Compute the Elwood limit-state parameters for a column section.

    The exact calibrated degradation slopes / residuals depend on the
    analysis and are normally set by the builder (e.g. the shear ``K_deg``
    is the flexural unloading stiffness measured for the member); the
    defaults here are conservative stand-ins.

    Args:
        section: Frame section (``ConcreteRectangularSection``).
        concrete: Concrete ``Material`` (``Fc``, ``E_mod``, ``nu``).
        tie: Transverse rebar ``Material`` (optional).
        column_length: Element length (model length).  When given, the
            spring slopes and the axial ``K_deg`` are populated.
        tie_legs: Tie legs per shear plane.
        core_depth: Override for the core depth ``d_c``.
        axial_factor: Axial-spring stiffness factor (default 99).
        kdeg_axial_ratio: Axial degrading-slope ratio (default -0.02).
        kdeg_shear: Shear post-failure slope (model F/L); ``None`` -> 0.
        fres_shear: Residual shear capacity (model force); ``None`` ->
            ``shear_residual_ratio * G*A_v/L``.
        fres_axial: Residual axial capacity (model force); ``None`` -> 0.
        shear_residual_ratio: Default shear-residual fraction of the elastic
            shear slope (default 0.20).

    Returns:
        :class:`ElwoodColumnParameters`.
    """
    geom = elwood_column_geometry(
        section, concrete, tie=tie, tie_legs=tie_legs, core_depth=core_depth
    )
    shear_slope = 0.0
    axial_slope = 0.0
    kdeg_ax = 0.0
    if column_length is not None and column_length > 0.0:
        shear_slope, axial_slope = elwood_spring_slopes(
            concrete, geom, column_length, axial_factor=axial_factor
        )
        kdeg_ax = elwood_axial_deg_slope(concrete, geom, column_length, ratio=kdeg_axial_ratio)
    fr_sh = fres_shear if fres_shear is not None else shear_residual_ratio * shear_slope
    return ElwoodColumnParameters(
        geometry=geom,
        fsw=geom.fsw,
        rho=geom.rho,
        shear_elastic_slope=shear_slope,
        axial_elastic_slope=axial_slope,
        kdeg_shear=kdeg_shear if kdeg_shear is not None else 0.0,
        fres_shear=fr_sh,
        kdeg_axial=kdeg_ax,
        fres_axial=fres_axial if fres_axial is not None else 0.0,
    )


# ═════════════════════════════════════════════════════════════════════
# Empirical drift-capacity equations (unit-agnostic references)
# ═════════════════════════════════════════════════════════════════════


def elwood_shear_drift_at_failure(
    V: float,
    P: float,
    geometry: ElwoodColumnGeometry,
    units: dict,
    delta: float = 0.0,
) -> float:
    """Drift ratio at shear failure for the current shear and axial loads.

    Solves the OpenSees ``ShearCurve::findLimit`` relation for the drift::

        V = 500 * (0.03 + delta + 4*rho - DR - 0.025*P/(b*h*(fc/1000)))
                * (b*d*sqrt(fc)/1000)

    All quantities are supplied in **model units** and rescaled to the
    kip-in-psi basis internally, so the returned drift is dimensionless and
    unit-agnostic.  Mirrors the OpenSees 1%-drift floor: the relation is
    only meaningful for ``DR >= 0.01`` (the code reports "no failure"
    below it), so the returned value is clamped at ``0.01``.

    Args:
        V: Shear force at the section (model force).
        P: Axial force at the section, compression positive (model force).
        geometry: :class:`ElwoodColumnGeometry`.
        units: Model units dict (required -- the OpenSees equation is
            anchored to the kip-in-psi convention).
        delta: Drift shift of the limit surface (default 0).

    Returns:
        Drift ratio (dimensionless) at which shear failure initiates for
        the given ``V``, ``P``.
    """
    if units is None:
        raise ValueError(
            "elwood_shear_drift_at_failure requires the model 'units' dict -- "
            "the OpenSees shear-drift equation is anchored to kip-in-psi."
        )
    b_in = _to_inch(geometry.b, units)
    h_in = _to_inch(geometry.h, units)
    d_in = _to_inch(geometry.d, units)
    fc_psi = _to_psi(geometry.fc, units)
    v_k = _to_kip(V, units)
    p_k = _to_kip(P, units)
    if b_in <= 0.0 or h_in <= 0.0 or d_in <= 0.0 or fc_psi <= 0.0:
        return 0.01
    rho = geometry.rho
    k_slope = 500.0 * (b_in * d_in * math.sqrt(fc_psi) / 1000.0)
    dr = 0.03 + delta + 4.0 * rho - v_k / k_slope - 0.025 * p_k / (b_in * h_in * (fc_psi / 1000.0))
    return max(float(dr), 0.01)


def elwood_shear_limit_force(
    drift: float,
    P: float,
    geometry: ElwoodColumnGeometry,
    units: dict,
    delta: float = 0.0,
) -> float:
    """Limiting shear force ``V(DR)`` on the Elwood shear limit surface.

    Direct form of ``ShearCurve::findLimit`` (the inverse of
    :func:`elwood_shear_drift_at_failure`):

    .. code-block:: text

        V = 500 * (0.03 + delta + 4*rho - DR - 0.025*P/(b*h*(fc/1000)))
                 * (b*d*sqrt(fc)/1000)

    with everything in the kip-in-psi imperial basis.  ``V`` is returned
    in **model force units** (converted back from the imperial evaluation).
    Below the 1%-drift floor the surface returns a near-infinite force
    (no failure), mirroring ``ShearCurve::findLimit``.

    Args:
        drift: Drift ratio (dimensionless).
        P: Axial force, compression positive (model force).
        geometry: :class:`ElwoodColumnGeometry`.
        units: Model units dict (required).
        delta: Drift shift of the limit surface (default 0).

    Returns:
        Limiting shear force (model force) at the drift, or a large value
        when ``drift < 0.01``.
    """
    if units is None:
        raise ValueError(
            "elwood_shear_limit_force requires the model 'units' dict -- "
            "the OpenSees shear-drift equation is anchored to kip-in-psi."
        )
    b_in = _to_inch(geometry.b, units)
    h_in = _to_inch(geometry.h, units)
    d_in = _to_inch(geometry.d, units)
    fc_psi = _to_psi(geometry.fc, units)
    if b_in <= 0.0 or h_in <= 0.0 or d_in <= 0.0 or fc_psi <= 0.0:
        return 0.0
    if drift < 0.01:
        # No failure below 1% drift — a near-infinite force in model units.
        # Convert the kip-anchored sentinel via the same kip -> model-force
        # factor used by the main return path below.
        return 9.9e9 / (_KIP_PER_N / force_scale_factor(units))
    rho = geometry.rho
    v_k = (
        500.0
        * (
            0.03
            + delta
            + 4.0 * rho
            - drift
            - 0.025 * _to_kip(P, units) / (b_in * h_in * (fc_psi / 1000.0))
        )
        * (b_in * d_in * math.sqrt(fc_psi) / 1000.0)
    )
    v_k = max(v_k, 0.0)
    # Convert kip -> model force via the same factor used by _to_kip.
    return v_k / (_KIP_PER_N / force_scale_factor(units))


def axial_capacity_surface(
    drift: float,
    fsw: float,
    units: Optional[dict] = None,
    theta_deg: float = ELWOOD_SHEAR_THETA_DEG,
    fres: Optional[float] = None,
) -> float:
    """Axial capacity surface ``P(DR)`` (shear-friction model).

    ``P(DR) = ((1+tan^2(theta))/(25*DR) - tan(theta)) * Fsw * tan(theta)``.
    ``fsw`` is supplied in model force units and converted to kip when a
    ``units`` dict is given; otherwise ``fsw`` is already in kips.

    Args:
        drift: Drift ratio (dimensionless).
        fsw: ``A_st*f_yt*d_c/s`` term (model force, or kip if ``units`` is
            ``None``).
        units: Model units dict (optional).
        theta_deg: Shear-friction crack angle (default 65 deg).
        fres: Residual axial capacity floor (same units as ``fsw``).

    Returns:
        Axial capacity at the drift (model force, or kip if units ``None``).
    """
    t = math.tan(math.radians(theta_deg))
    d = max(float(drift), 1.0e-9)
    fsw_out = _to_kip(fsw, units) if units is not None else float(fsw)
    y = ((1.0 + t * t) / (25.0 * d) - t) * fsw_out * t
    if fres is not None:
        fres_out = _to_kip(fres, units) if units is not None else float(fres)
        y = max(y, fres_out)
    return float(y)


def elwood_axial_drift_at_failure(
    P: float,
    fsw: float,
    units: Optional[dict] = None,
    theta_deg: float = ELWOOD_SHEAR_THETA_DEG,
    delta: float = 0.0,
) -> float:
    """Drift ratio at axial failure for an operating axial load ``P``.

    Inverts the shear-friction surface::

        DR = (1 + tan^2(theta)) / (25*(tan(theta) + P/(Fsw*tan(theta))))

    Args:
        P: Axial force (model force), compression positive.
        fsw: ``A_st*f_yt*d_c/s`` (model force).
        units: Model units dict (optional; both inputs are kip when ``None``).
        theta_deg: Crack angle (default 65 deg).
        delta: Drift shift of the surface (default 0).

    Returns:
        Drift ratio (dimensionless) at which axial failure initiates.
    """
    t = math.tan(math.radians(theta_deg))
    p_k = _to_kip(P, units) if units is not None else float(P)
    fsw_k = _to_kip(fsw, units) if units is not None else float(fsw)
    if fsw_k <= 0.0 or t <= 0.0:
        return 0.10  # fsw <= 0 (no ties) -> 0.10 fallback drift
    return float((1.0 + t * t) / (25.0 * (t + p_k / (fsw_k * t))) + delta)


# ═════════════════════════════════════════════════════════════════════
# OpenSeesPy bridge helpers
# ═════════════════════════════════════════════════════════════════════


def three_point_axial_surface(
    p_gravity: float,
    fsw: float,
    units: dict,
    theta_deg: float = ELWOOD_SHEAR_THETA_DEG,
    high_drift: float = ELWOOD_SURFACE_HIGH_DRIFT,
    low_drift: float = ELWOOD_SURFACE_LOW_DRIFT,
    fres: Optional[float] = None,
) -> list[tuple[float, float]]:
    """Three-point piecewise-linear fit of the axial capacity surface.

    OpenSeesPy 3.8.0 cannot construct ``limitCurve Axial`` (the binding
    always raises a silent ``OpenSeesError``); the toolkit therefore emits
    a ``limitCurve ThreePoint`` surface instead.  Because
    ``ThreePointCurve::findLimit`` returns ``0`` for ``x < x1``, the first
    point is placed at zero drift with a high force plateau, and the middle
    point is pinned to the operating gravity axial load so the failure
    triggers at exactly the Elwood axial-failure drift.

    Returns the points in the **imperial** (drift, kip) form required by the
    ``limitCurve ThreePoint`` command::

        (x1, y1) = (0, P(0.5% drift) plateau)   -- no failure at low drift
        (x2, y2) = (DR_a(P_gravity), P_gravity) -- the axial-failure point
        (x3, y3) = (8%, P(8% drift))            -- low-drift residual region

    Args:
        p_gravity: Operating gravity axial load (model force, positive
            compression).
        fsw: ``A_st*f_yt*d_c/s`` (model force).
        units: Model units dict.
        theta_deg: Crack angle (default 65 deg).
        high_drift: Upper drift bound of the fit (default 0.08).
        low_drift: Drift used for the high-force plateau point (default
            0.005).
        fres: Residual axial capacity floor (model force).

    Returns:
        List of three ``(drift, force_kips)`` tuples for the OpenSees
        command.
    """
    if units is None:
        raise ValueError(
            "three_point_axial_surface requires the model 'units' dict -- the "
            "ThreePoint curve is emitted in kip-in."
        )
    fsw_k = _to_kip(fsw, units)
    p_k = _to_kip(p_gravity, units)
    y_low = axial_capacity_surface(
        high_drift,
        fsw_k,
        None,
        theta_deg=theta_deg,
        fres=_to_kip(fres, units) if fres is not None else None,
    )
    y_high = axial_capacity_surface(low_drift, fsw_k, None, theta_deg=theta_deg)
    y_high = max(y_high, 2.0 * p_k)
    x_fail = elwood_axial_drift_at_failure(p_k, fsw_k, None, theta_deg=theta_deg)
    x_fail = max(x_fail, low_drift)
    return [(0.0, y_high), (x_fail, p_k), (high_drift, y_low)]


def elwood_limit_state_envelope(
    forces: Sequence[float],
    elastic_slope: float,
) -> list[tuple[float, float]]:
    """(force, deformation) backbone points for a ``LimitState`` material.

    Elwood's shear/axial springs use a three-point envelope whose points all
    lie on the elastic slope, e.g. ``(V_i, V_i / k_elastic)``.  The returned
    pairs are used directly as ``(s1p, e1p, s2p, e2p, s3p, e3p)`` in the
    ``uniaxialMaterial LimitState`` command.

    Args:
        forces: Three backbone forces (model force), increasing.
        elastic_slope: Elastic spring slope (model force / model length).

    Returns:
        List of ``(force, deformation)`` tuples.
    """
    if elastic_slope <= 0.0:
        return [(f, 0.0) for f in forces]
    return [(float(f), float(f) / elastic_slope) for f in forces]
