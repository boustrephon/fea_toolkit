"""GB 50011 seismic response spectrum computation and plotting.

Provides both a direct spectrum function (``_gb50011_spectrum``) and a
config-driven builder (``_build_spectrum``) that reads intensity, site
class, level and damping from a dictionary.  The two functions implement
the same GB 50011 elastic spectrum but use slightly different formulas
for the ascending branch:

* ``_gb50011_spectrum`` — older form: ``0.45 + 5.5·T``
* ``_build_spectrum`` — damping-corrected form: ``0.45 + (η₂ − 0.45)·10·T``

``plot_seismic_spectrum`` renders all three levels (frequent,
fortification, rare) on a single figure; it lives in
:mod:`fea_toolkit.plotting.seismic_spectrum` and is re-exported here.

The :class:`ResponseSpectrum` dataclass is the canonical carrier for an
arbitrary T/Sa spectrum (GB 50011, IEC 62271-207, ASCE 7, site-specific
hazard curves, etc.) that can be injected into pushover analysis — no
code is wired to a single design code.

``_iec_spectrum`` implements the IEC 62271-207 seismic response spectrum
for high-voltage switchgear — a frequency-banded spectrum anchored to the
peak ground acceleration.  ``ResponseSpectrum.from_iec62271`` builds a
canonical :class:`ResponseSpectrum` from it.
"""

from dataclasses import dataclass
from typing import Any, Optional

import numpy as np

from .utils import DEFAULT_GRAVITY_MS2, g_from_units
from .utils import cqc_combine as _cqc_combine_modal

# ── Legacy re-export ──────────────────────────────────────────────────
# ``plot_seismic_spectrum`` moved to the plotting layer (2026-08-27).
# Re-exported lazily here (rather than imported at module load) because
# the function itself imports ``_build_spectrum`` / ``_interp_sa`` back
# from this module — an eager import would be circular.


def __getattr__(name: str):
    if name == "plot_seismic_spectrum":
        from .plotting.seismic_spectrum import plot_seismic_spectrum

        return plot_seismic_spectrum
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _resolve_g(g: Optional[float], units: Optional[dict]) -> float:
    """Resolve gravitational acceleration for a GB 50011 spectrum.

    Precedence, highest first: an explicit *g*, the model's *units* dict
    (via :func:`fea_toolkit.utils.g_from_units`), then the shared SI
    constant :data:`fea_toolkit.utils.DEFAULT_GRAVITY_MS2` (9.80665 m/s²).
    An explicit *g* is returned verbatim — it is never re-derived — so
    callers may pass an already unit-scaled value.

    Args:
        g: Explicit gravitational acceleration, in the caller's unit system.
        units: Model units dict (e.g. ``{"F": "N", "L": "mm", "T": "C"}``).
            Ignored when *g* is given.

    Returns:
        Gravitational acceleration in the resolved unit system.
    """
    if g is not None:
        return g
    if units is not None:
        return g_from_units(units)
    return DEFAULT_GRAVITY_MS2


@dataclass
class ResponseSpectrum:
    """Seismic demand spectrum as (T, Sa) ordinate pairs.

    This is the canonical exchange type for seismic demand spectra used
    by pushover CSM post-processing.  Any code path (GB 50011, ASCE 7,
    site-specific hazard curve, etc.) can produce one via the factories
    below, so the pushover path never hardwires a particular design code.

    Attributes
    ----------
    T : list of float
        Period ordinates (s), ascending.
    Sa : list of float
        Spectral acceleration ordinates (model units, e.g. m/s²).
    code : str
        Optional design-code label (``"GB50011"``, ``"ASCE7-16"``, etc.).
    description : str
        Optional free-text note (e.g. ``"Rare, site class II"``).
    """

    T: list[float]
    Sa: list[float]
    code: str = ""
    description: str = ""

    @classmethod
    def from_gb50011(
        cls,
        alpha_max: float,
        tg: float,
        zeta: float = 0.05,
        g: Optional[float] = None,
        units: Optional[dict] = None,
        T_max: float = 6.0,
        n_pts: int = 200,
        description: str = "",
    ) -> "ResponseSpectrum":
        """Build a GB 50011 elastic spectrum as a :class:`ResponseSpectrum`.

        Uses the *damping-corrected* ascending branch
        (``0.45 + (η₂ − 0.45)·10·T``) consistent with :func:`_build_spectrum`.
        The damping-dependent shape factors γ, η₁, η₂ are derived from
        *zeta* using the GB 50011 §5.1.5 formulas.

        Parameters
        ----------
        alpha_max : float
            Seismic influence coefficient maximum (Table 5.1.4-1).
        tg : float
            Characteristic period (s) — site-class dependent (Table 5.1.4-2).
        zeta : float
            Damping ratio (default 0.05).
        g : float, optional
            Gravitational acceleration (m/s²).  Used verbatim when given;
            ``None`` falls back to *units*, then to the shared SI constant
            :data:`fea_toolkit.utils.DEFAULT_GRAVITY_MS2` (9.80665 m/s²).
        units : dict, optional
            Model units dict (e.g. ``{"F": "N", "L": "mm", "T": "C"}``).
            Ignored when *g* is given; otherwise the spectrum is scaled into
            the model's unit system (:func:`fea_toolkit.utils.g_from_units`).
        T_max : float
            Upper period bound (s, default 6.0).
        n_pts : int
            Number of ordinates (default 200).
        description : str
            Optional label stored on the instance.

        Returns
        -------
        ResponseSpectrum
            The spectrum ordinates.
        """
        g = _resolve_g(g, units)

        gamma = 0.9 + (0.05 - zeta) / (0.3 + 6.0 * zeta)
        eta_1 = max(0.0, 0.02 + (0.05 - zeta) / (4.0 + 32.0 * zeta))
        eta_2 = max(0.55, 1.0 + (0.05 - zeta) / (0.08 + 1.6 * zeta))

        T_spec = np.linspace(0.0, T_max, n_pts)
        Sa_spec = np.array(
            [
                0.45 * alpha_max * g
                if T <= 0.0
                else (0.45 + (eta_2 - 0.45) * 10.0 * T) * alpha_max * g
                if T <= 0.1
                else eta_2 * alpha_max * g
                if tg >= T
                else (tg / T) ** gamma * eta_2 * alpha_max * g
                if 5.0 * tg >= T
                else (eta_2 * 0.2**gamma - eta_1 * (T - 5.0 * tg)) * alpha_max * g
                for T in T_spec
            ]
        )

        return cls(
            T=T_spec.tolist(),
            Sa=Sa_spec.tolist(),
            code="GB50011",
            description=description or f"GB 50011 elastic, alpha_max={alpha_max}, tg={tg}",
        )

    @classmethod
    def from_iec62271(
        cls,
        pga: float,
        zeta: float = 0.05,
        T_max: float = 4.0,
        n_pts: int = 200,
        description: str = "",
    ) -> "ResponseSpectrum":
        """Build an IEC 62271-207 seismic response spectrum.

        The spectrum is defined for equipment frequencies up to 33 Hz;
        *T_max* defaults to 4.0 s so the rising branch is captured down
        to 0.25 Hz.  *pga* carries the caller's unit system — the
        returned accelerations have the same units.

        Parameters
        ----------
        pga : float
            Peak ground acceleration in the model's acceleration units.
        zeta : float
            Damping ratio (default 0.05).
        T_max : float
            Upper period bound (s, default 4.0).
        n_pts : int
            Number of ordinates (default 200).
        description : str
            Optional label stored on the instance.

        Returns
        -------
        ResponseSpectrum
            The spectrum ordinates.
        """
        # IEC 62271-207 defines branch transitions at 1.1, 8.0 and 33.0 Hz.
        # Sample those exact periods (1/1.1, 1/8, 1/33 s) in addition to
        # the uniform grid so the piecewise spectrum is captured at its
        # corner points, not just approximately.
        branch_periods = [1.0 / 33.0, 1.0 / 8.0, 1.0 / 1.1]
        T_spec = np.unique(np.concatenate([np.linspace(0.0, T_max, n_pts), branch_periods]))
        T_spec = T_spec[T_spec <= T_max]
        Sa_spec = _iec_spectrum(T_spec, pga, zeta=zeta)
        return cls(
            T=T_spec.tolist(),
            Sa=Sa_spec.tolist(),
            code="IEC62271-207",
            description=description or f"IEC 62271-207, pga={pga}, zeta={zeta}",
        )

    @classmethod
    def from_eurocode8(
        cls,
        ag_r: float,
        gamma_i: float = 1.0,
        ground_type: str = "A",
        spectrum_type: int = 1,
        q: float = 1.0,
        zeta: float = 0.05,
        vertical: bool = False,
        elastic: bool = False,
        T_max: float = 4.0,
        n_pts: int = 200,
        description: str = "",
    ) -> "ResponseSpectrum":
        """Build an EN 1998-1 (Eurocode 8) acceleration spectrum.

        By default returns the **design spectrum** of EN 1998-1 §3.2.2.5
        (Eqs 3.13-3.16) with the behaviour factor *q*; when *elastic* is
        ``True`` it returns the **elastic spectrum** (§3.2.2.2 horizontal,
        §3.2.2.3 vertical), in which ``q`` does not enter.  The design
        ground acceleration is ``a_g = gamma_i * ag_r`` (EN 1998-1
        §2.1(4)).  For the elastic displacement spectrum S_De(T) use
        :meth:`from_eurocode8_displacement`.

        Parameters
        ----------
        ag_r : float
            Reference peak ground acceleration on rock (type A ground),
            in the caller's acceleration units (e.g. ``0.10`` for 0.10 g,
            or ``0.981`` for m/s²).  The returned ordinates use the same
            units.
        gamma_i : float
            Importance factor γI (EN 1998-1 §4.2.5, Table 4.3); the
            design ground acceleration is ``a_g = gamma_i * ag_r``.
            Defaults to 1.0.
        ground_type : str
            Ground type ``"A"``-``"E"`` (EN 1998-1 Table 3.1).  Ignored
            for the vertical spectrum (``S = 1.0`` always).  Defaults to
            ``"A"``.
        spectrum_type : int
            ``1`` (high seismicity, *M_s* > 5.5) or ``2`` (low
            seismicity, *M_s* ≤ 5.5), selecting the parameter table
            (EN 1998-1 Tables 3.2 / 3.3).  Defaults to 1.
        q : float
            Behaviour factor (EN 1998-1 §3.2.2.5); ``q = 1`` is the
            non-dissipative case.  Ignored when *elastic* is True.
            Defaults to 1.0.
        zeta : float
            Damping ratio (default 0.05).  The damping correction
            ``η = √(10 / (5 + 100·ζ)) ≥ 0.55`` of §3.2.2.2(3) is applied.
        vertical : bool
            When ``True``, build the vertical spectrum: ``a_vg = 0.9 * a_g``,
            ``S = 1.0``, ``T_B/T_C/T_D = 0.05/0.15/1.0`` s, amplification
            ``3.0``.  Defaults to ``False`` (horizontal).
        elastic : bool
            When ``True``, build the *elastic* spectrum (§3.2.2.2
            horizontal, §3.2.2.3 vertical) instead of the *design*
            spectrum (§3.2.2.5): rising-branch intercept ``1`` (vs
            ``2/3``), plateau ``η·a`` (vs ``η·a/q``), and no ``β·a_g``
            lower bound.  Defaults to ``False``.
        T_max : float
            Upper period bound (s, default 4.0).
        n_pts : int
            Number of ordinates on the uniform grid (default 200).  The
            branch corner periods *T_B*, *T_C*, *T_D* are sampled
            explicitly.
        description : str
            Optional label stored on the instance.

        Returns
        -------
        ResponseSpectrum
            The spectrum ordinates, in the same acceleration units as
            *ag_r*.

        Raises
        ------
        ValueError
            If *ag_r* is negative or non-finite, *gamma_i* is not
            positive, *ground_type* is not ``"A"``-``"E"``,
            *spectrum_type* is not 1 or 2, *q* < 1, or *zeta* is outside
            ``(0, 0.20]``.

        Examples
        --------
        Build the EN 1998-1 Type 2, ground A, γI = 1.4 spectrum and
        compare its 0.35 g plateau with the IEC 62271-207 RRS:

        >>> s = ResponseSpectrum.from_eurocode8(
        ...     ag_r=0.10, gamma_i=1.4, ground_type="A", spectrum_type=2, q=1.0
        ... )
        >>> s.code
        'EC8-Type2'
        """
        _, tb, tc, td, _, _ = _ec8_params(ground_type, spectrum_type, vertical)

        T_spec = _ec8_sample_grid(tb, tc, td, T_max, n_pts)
        Sa_spec = _eurocode8_spectrum(
            T_spec,
            ag_r,
            gamma_i=gamma_i,
            ground_type=ground_type,
            spectrum_type=spectrum_type,
            q=q,
            zeta=zeta,
            vertical=vertical,
            elastic=elastic,
        )
        if vertical:
            code, label = "EC8-Vertical", "EN 1998-1 vertical"
        else:
            gt = str(ground_type).upper()
            code = f"EC8-Type{spectrum_type}"
            label = f"EN 1998-1 Type {spectrum_type} ground {gt}"
        if elastic:
            code += "-Elastic"
            label += " elastic"
        else:
            label += f" design, q={q}"
        label += f", agR={ag_r}, gammaI={gamma_i}, zeta={zeta}"
        return cls(
            T=T_spec.tolist(),
            Sa=Sa_spec.tolist(),
            code=code,
            description=description or label,
        )

    @classmethod
    def from_eurocode8_displacement(
        cls,
        ag_r: float,
        gamma_i: float = 1.0,
        ground_type: str = "A",
        spectrum_type: int = 1,
        zeta: float = 0.05,
        vertical: bool = False,
        T_max: float = 4.0,
        n_pts: int = 200,
        description: str = "",
    ) -> "ResponseSpectrum":
        """Build the EN 1998-1 elastic displacement spectrum S_De(T).

        The displacement spectrum is ``S_De(T) = S_e(T)·(T/2π)²``
        (EN 1998-1 §3.2.2.4, Eq. 3.6), where S_e is the *elastic*
        acceleration spectrum (§3.2.2.2 horizontal, §3.2.2.3 vertical).
        The behaviour factor ``q`` does not enter.

        The ordinates are **displacements**, stored on the ``Sa`` field
        with a ``code`` ending in ``-Displacement``.  For a consistent
        unit system (e.g. m/s², s) they are lengths (m); the caller is
        responsible for unit consistency (see .clinerules §4.6).

        Parameters
        ----------
        ag_r : float
            Reference peak ground acceleration on rock, in the caller's
            acceleration units.
        gamma_i : float
            Importance factor γI; ``a_g = gamma_i * ag_r``.  Default 1.0.
        ground_type : str
            Ground type ``"A"``-``"E"`` (ignored when *vertical*).
        spectrum_type : int
            1 (high seismicity) or 2 (low seismicity).
        zeta : float
            Damping ratio (default 0.05).
        vertical : bool
            When ``True``, use the vertical elastic spectrum (§3.2.2.3).
        T_max : float
            Upper period bound (s, default 4.0).
        n_pts : int
            Number of ordinates on the uniform grid (default 200); the
            branch corners T_B, T_C, T_D are sampled explicitly.
        description : str
            Optional label stored on the instance.

        Returns
        -------
        ResponseSpectrum
            The spectral-displacement ordinates (``Sa`` holds S_De).

        Raises
        ------
        ValueError
            Propagated from :func:`_eurocode8_spectrum`.

        Examples
        --------
        >>> s = ResponseSpectrum.from_eurocode8_displacement(
        ...     ag_r=0.10, gamma_i=1.4, ground_type="A", spectrum_type=2
        ... )
        >>> s.code
        'EC8-Type2-Displacement'
        """
        _, tb, tc, td, _, _ = _ec8_params(ground_type, spectrum_type, vertical)
        T_spec = _ec8_sample_grid(tb, tc, td, T_max, n_pts)
        Sd_spec = _eurocode8_displacement_spectrum(
            T_spec,
            ag_r,
            gamma_i=gamma_i,
            ground_type=ground_type,
            spectrum_type=spectrum_type,
            zeta=zeta,
            vertical=vertical,
        )
        if vertical:
            code, label = "EC8-Vertical-Displacement", "EN 1998-1 vertical"
        else:
            gt = str(ground_type).upper()
            code = f"EC8-Type{spectrum_type}-Displacement"
            label = f"EN 1998-1 Type {spectrum_type} ground {gt}"
        label += f" elastic displacement, agR={ag_r}, gammaI={gamma_i}, zeta={zeta}"
        return cls(
            T=T_spec.tolist(),
            Sa=Sd_spec.tolist(),
            code=code,
            description=description or label,
        )

    @classmethod
    def from_arrays(
        cls,
        T: list[float],
        Sa: list[float],
        *,
        code: str = "",
        description: str = "",
    ) -> "ResponseSpectrum":
        """Build a :class:`ResponseSpectrum` from explicit T/Sa arrays.

        Use this for non-GB-50011 spectra (ASCE 7, site-specific hazard
        curves, user-defined).

        Parameters
        ----------
        T : list of float
            Period ordinates (s), ascending.
        Sa : list of float
            Spectral acceleration ordinates (model units).
        code : str
            Design-code label.
        description : str
            Free-text note.

        Returns
        -------
        ResponseSpectrum
            The spectrum ordinates.
        """
        return cls(
            T=list(T),
            Sa=list(Sa),
            code=code,
            description=description,
        )

    def interpolate(self, T_query: Any) -> np.ndarray:
        """Interpolate Sa onto *T_query* periods.

        Parameters
        ----------
        T_query : array-like
            Target period values (s).

        Returns
        -------
        np.ndarray
            Interpolated spectral acceleration values.
        """
        return np.interp(np.asarray(T_query), np.asarray(self.T), np.asarray(self.Sa))

    def __post_init__(self) -> None:
        """Validate that T and Sa are equal-length, non-empty lists."""
        if len(self.T) == 0 or len(self.Sa) == 0:
            raise ValueError("ResponseSpectrum requires non-empty T and Sa arrays")
        if len(self.T) != len(self.Sa):
            raise ValueError(
                f"T and Sa must be the same length (got {len(self.T)} vs {len(self.Sa)})"
            )
        self.T = [float(t) for t in self.T]
        self.Sa = [float(s) for s in self.Sa]
        # Reject non-finite ordinates (NaN, inf) — NaN comparisons always
        # return False, so NaN would pass the strictly-increasing check
        # and silently corrupt the interpolated spectrum.
        for i, t in enumerate(self.T):
            if not np.isfinite(t):
                raise ValueError(f"ResponseSpectrum T ordinates must be finite; found T[{i}]={t!r}")
        for i, s in enumerate(self.Sa):
            if not np.isfinite(s):
                raise ValueError(
                    f"ResponseSpectrum Sa ordinates must be finite; found Sa[{i}]={s!r}"
                )
        # Require strictly increasing period ordinates: unsorted or
        # duplicate periods produce silently incorrect interpolated
        # spectral accelerations (e.g. via `interpolate_sa`).
        for i in range(1, len(self.T)):
            if self.T[i] <= self.T[i - 1]:
                raise ValueError(
                    f"ResponseSpectrum T ordinates must be strictly increasing; "
                    f"found T[{i}]={self.T[i]} <= T[{i - 1}]={self.T[i - 1]}"
                )


def _gb50011_spectrum(
    T_values: list[float],
    alpha_max: float,
    tg: float,
    gamma: float = 0.9,
    eta1: float = 0.02,
    eta2: float = 1.0,
    g: Optional[float] = None,
) -> np.ndarray:
    """Return spectral acceleration Sa (in the same units as *g*) for a GB 50011 elastic spectrum.

    Args:
        T_values: Period values (s) at which to evaluate the spectrum.
        alpha_max: Seismic influence coefficient maximum (Table 5.1.4-1).
        tg: Characteristic period (s) — Site-class dependent (Table 5.1.4-2).
        gamma: Descending-branch exponent (default 0.9 for 5 % damping).
        eta1: Linear-drop correction factor (default 0.02 for 5 % damping).
        eta2: Damping reduction factor (default 1.0 for 5 % damping).
        g: Gravitational acceleration in the caller's acceleration unit (e.g.
            m/s²).  Used verbatim when given; ``None`` falls back to the shared
            SI constant :data:`fea_toolkit.utils.DEFAULT_GRAVITY_MS2`
            (9.80665 m/s²).

    Returns:
        Spectral acceleration values, in the same units as *g*.
    """
    g = _resolve_g(g, None)
    Sa = []
    for T in T_values:
        if T <= 0.0:
            Sa.append(0.45 * alpha_max * g)
        elif T <= 0.1:
            Sa.append((0.45 + 5.5 * T) * alpha_max * g)
        elif tg >= T:
            Sa.append(alpha_max * g)
        elif 5.0 * tg >= T:
            Sa.append((tg / T) ** gamma * eta2 * alpha_max * g)
        else:
            Sa.append((eta2 * 0.2**gamma - eta1 * (T - 5.0 * tg)) * alpha_max * g)
    return np.array(Sa)


def _iec_spectrum(T, pga, zeta: float = 0.05):
    """Evaluate the IEC 62271-207 seismic response spectrum.

    The spectrum is defined piecewise in frequency ``f = 1 / T`` with a
    damping correction factor ``beta`` that depends on the percentage of
    critical damping ``d = 100 * zeta``:

    * ``0 <= f <= 1.1`` — rising branch: ``(pga / 0.25) * 0.572 * beta * f``
    * ``1.1 <= f <= 8.0`` — plateau: ``pga * 2.5 * beta``
    * ``8.0 <= f <= 33.0`` — falling branch:
      ``(pga / 0.25) * ((6.6 * beta - 2.64) / f - 0.2 * beta + 0.33)``
    * ``f > 33`` — constant: ``pga``

    At ``T = 0`` the zero-period acceleration is returned (``Sa = pga``).
    *pga* carries the caller's unit system — the returned acceleration
    has the same units (e.g. m/s² or g), so no gravity constant is
    hardcoded (see .clinerules §4.6).

    Parameters
    ----------
    T : float or array-like
        Period values (s) at which to evaluate the spectrum.
    pga : float
        Peak ground acceleration in the model's acceleration units.
    zeta : float
        Damping ratio (fraction of critical damping, default 0.05).

    Returns
    -------
    float or np.ndarray
        Spectral acceleration(s) in the same units as *pga* — a float
        for scalar *T*, otherwise an array.

    Raises
    ------
    ValueError
        If *pga* is not finite or is negative, *zeta* is non-positive or
        exceeds 0.20 (the damping factor uses ``log(100 * zeta)``), or *T*
        is negative or not finite.  ``T = 0`` is valid and maps to the
        zero-period acceleration.
    """
    if zeta <= 0 or zeta > 0.20:
        raise ValueError("zeta must be in (0, 0.20] (damping factor uses log(100 * zeta))")
    if not np.isfinite(pga):
        raise ValueError("pga must be finite")
    if pga < 0:
        raise ValueError("pga must be non-negative")
    if np.any(~np.isfinite(T)):
        raise ValueError("T values must be finite")
    T_arr = np.asarray(T, dtype=float)
    if np.any(T_arr < 0):
        raise ValueError("T values must be non-negative")

    scalar_in = T_arr.ndim == 0
    T_arr = np.atleast_1d(T_arr)

    d = 100.0 * zeta
    beta = (3.21 - 0.68 * np.log(d)) / 2.1156

    Sa = np.empty_like(T_arr)
    positive = T_arr > 0
    with np.errstate(divide="ignore", invalid="ignore"):
        f = 1.0 / T_arr

    # T <= 0 maps to the zero-period acceleration.
    Sa[~positive] = pga

    # Bands are checked in IEC 62271-207 order; the first match wins
    # (the bands overlap at their shared boundaries).
    rising = positive & (f <= 1.1)
    Sa[rising] = pga / 0.25 * 0.572 * beta * f[rising]

    plateau = positive & (f >= 1.0) & (f <= 8.0) & ~rising
    Sa[plateau] = pga * 2.5 * beta

    falling = positive & (f >= 8.0) & (f <= 33.0) & ~rising & ~plateau
    Sa[falling] = pga / 0.25 * ((6.6 * beta - 2.64) / f[falling] - 0.2 * beta + 0.33)

    high_freq = positive & ~rising & ~plateau & ~falling
    Sa[high_freq] = pga

    return float(Sa[0]) if scalar_in else Sa


# ═══════════════════════════════════════════════════════════════════════
# Eurocode 8 (EN 1998-1) design spectrum
# ═══════════════════════════════════════════════════════════════════════
# Ground-type parameters (S, T_B, T_C, T_D) per EN 1998-1 Tables 3.2
# (Type 1, high seismicity) and 3.3 (Type 2, low seismicity), using the
# recommended (CEN default) values.  National Annexes may prescribe
# different values for these NDPs.

_EC8_TYPE1_PARAMS: dict[str, tuple[float, float, float, float]] = {
    "A": (1.00, 0.15, 0.40, 2.00),
    "B": (1.20, 0.15, 0.50, 2.00),
    "C": (1.15, 0.20, 0.60, 2.00),
    "D": (1.35, 0.20, 0.80, 2.00),
    "E": (1.40, 0.15, 0.50, 2.00),
}

_EC8_TYPE2_PARAMS: dict[str, tuple[float, float, float, float]] = {
    "A": (1.00, 0.05, 0.25, 1.20),
    "B": (1.35, 0.05, 0.25, 1.20),
    "C": (1.50, 0.10, 0.25, 1.20),
    "D": (1.80, 0.10, 0.30, 1.20),
    "E": (1.60, 0.05, 0.25, 1.20),
}

# Vertical spectrum (EN 1998-1 §3.2.2.3): fixed shape, S is always 1.0.
_EC8_VERTICAL_S = 1.0
_EC8_VERTICAL_TB = 0.05
_EC8_VERTICAL_TC = 0.15
_EC8_VERTICAL_TD = 1.00
_EC8_VERTICAL_AVG_RATIO = 0.9  # a_vg = 0.9 * a_g (recommended value)
_EC8_VERTICAL_AMPLIFICATION = 3.0  # elastic vertical amplification
_EC8_HORIZONTAL_AMPLIFICATION = 2.5  # elastic horizontal amplification
_EC8_BETA_LOWER_BOUND = 0.2  # β, EN 1998-1 §3.2.2.5(4)
_EC8_ETA_MIN = 0.55  # minimum damping correction (§3.2.2.2(3))


def _ec8_params(
    ground_type: str,
    spectrum_type: int,
    vertical: bool,
) -> tuple[float, float, float, float, float, float]:
    """Resolve the EN 1998-1 spectrum parameters for a ground type.

    Args:
        ground_type: Ground type ``"A"``-``"E"`` (ignored when *vertical*).
        spectrum_type: 1 (high seismicity) or 2 (low seismicity).
        vertical: When True, return the fixed vertical-spectrum parameters.

    Returns:
        Tuple ``(S, T_B, T_C, T_D, accel_ratio, amplification)``, where
        *accel_ratio* scales the design ground acceleration (1.0
        horizontal, 0.9 vertical) and *amplification* is the elastic
        spectral amplification (2.5 horizontal, 3.0 vertical).

    Raises:
        ValueError: If *spectrum_type* is not 1 or 2, or *ground_type* is
            not one of ``"A"``-``"E"``.
    """
    if vertical:
        return (
            _EC8_VERTICAL_S,
            _EC8_VERTICAL_TB,
            _EC8_VERTICAL_TC,
            _EC8_VERTICAL_TD,
            _EC8_VERTICAL_AVG_RATIO,
            _EC8_VERTICAL_AMPLIFICATION,
        )
    if spectrum_type not in (1, 2):
        raise ValueError(f"spectrum_type must be 1 or 2, got {spectrum_type!r}")
    table = _EC8_TYPE1_PARAMS if spectrum_type == 1 else _EC8_TYPE2_PARAMS
    gt = str(ground_type).upper()
    if gt not in table:
        raise ValueError(f"ground_type must be one of A-E, got {ground_type!r}")
    soil, tb, tc, td = table[gt]
    return soil, tb, tc, td, 1.0, _EC8_HORIZONTAL_AMPLIFICATION


def _eurocode8_spectrum(
    T,
    ag_r: float,
    *,
    gamma_i: float = 1.0,
    ground_type: str = "A",
    spectrum_type: int = 1,
    q: float = 1.0,
    zeta: float = 0.05,
    vertical: bool = False,
    elastic: bool = False,
):
    """Evaluate the EN 1998-1 (Eurocode 8) acceleration spectrum.

    Returns the **design** spectrum (§3.2.2.5, Eqs 3.13-3.16) by default,
    or — when *elastic* is True — the **elastic** spectrum (§3.2.2.2
    horizontal, §3.2.2.3 vertical, Eqs 3.2-3.5).  Both are piecewise in
    the period ``T``, with plateau coefficient ``η · a / q`` (design) or
    ``η · a`` (elastic), where *a* is the elastic amplification (2.5
    horizontal, 3.0 vertical) and ``η`` is the damping correction of
    §3.2.2.2(3):

    * ``0 <= T <= T_B`` — rising: ``a_g·S·(i + (T/T_B)·(η·a/q − i))``
    * ``T_B <= T <= T_C`` — plateau: ``a_g·S·(η·a/q)``
    * ``T_C <= T <= T_D`` — falling (1/T): ``a_g·S·(η·a/q)·(T_C/T)``
    * ``T_D <= T`` — falling (1/T²): ``a_g·S·(η·a/q)·(T_C·T_D/T²)``

    where the rising-branch intercept ``i`` is ``2/3`` for the design
    spectrum and ``1`` for the elastic spectrum, and the ``/q`` factor is
    present only for the design spectrum.  The design spectrum has a
    lower bound of ``β·a_g`` (``β = 0.2``); the elastic spectrum has none.
    The design ground acceleration is ``a_g = gamma_i · ag_r``
    (EN 1998-1 §2.1(4)).

    *ag_r* carries the caller's unit system — the returned acceleration
    has the same units (e.g. m/s² or g), so no gravity constant is
    hardcoded (see .clinerules §4.6).

    Args:
        T: Period value(s) (s) at which to evaluate the spectrum.
        ag_r: Reference peak ground acceleration on rock, in the model's
            acceleration units.
        gamma_i: Importance factor γI; ``a_g = gamma_i * ag_r``.
        ground_type: Ground type ``"A"``-``"E"`` (ignored when *vertical*).
        spectrum_type: 1 (high seismicity) or 2 (low seismicity).
        q: Behaviour factor (≥ 1; ignored when *elastic*, which must then
            be left at its default ``1.0``).
        zeta: Damping ratio (fraction of critical).
        vertical: When True, evaluate the vertical spectrum (§3.2.2.3).
        elastic: When True, evaluate the elastic spectrum (§3.2.2.2 /
            §3.2.2.3) instead of the design spectrum (§3.2.2.5).

    Returns:
        float or np.ndarray: Spectral acceleration(s) in the same units as
        *ag_r* — a float for scalar *T*, otherwise an array.

    Raises:
        ValueError: If *ag_r* is non-finite or negative, *gamma_i* is not
            finite and positive, *q* is less than 1, *zeta* is outside
            ``(0, 0.20]``, *spectrum_type* is not 1 or 2, *ground_type* is
            not ``"A"``-``"E"``, *T* is negative or not finite, or
            *elastic* is True together with ``q != 1``.
    """
    if not np.isfinite(ag_r):
        raise ValueError("ag_r must be finite")
    if ag_r < 0:
        raise ValueError("ag_r must be non-negative")
    if not np.isfinite(gamma_i) or gamma_i <= 0:
        raise ValueError("gamma_i must be finite and positive")
    if not np.isfinite(q) or q < 1.0:
        raise ValueError("q must be finite and >= 1.0")
    if elastic and q != 1.0:
        raise ValueError("q has no effect for the elastic spectrum (elastic=True); pass q=1.0")
    if zeta <= 0 or zeta > 0.20:
        raise ValueError("zeta must be in (0, 0.20]")
    if spectrum_type not in (1, 2):
        raise ValueError(f"spectrum_type must be 1 or 2, got {spectrum_type!r}")

    soil, tb, tc, td, accel_ratio, ampl = _ec8_params(ground_type, spectrum_type, vertical)

    ag = gamma_i * ag_r
    accel = accel_ratio * ag
    eta = max(_EC8_ETA_MIN, float(np.sqrt(10.0 / (5.0 + 100.0 * zeta))))
    # Elastic spectrum: plateau η·a, rising intercept 1.  Design spectrum:
    # plateau η·a/q, rising intercept 2/3 (EN 1998-1 §3.2.2.2 vs §3.2.2.5).
    intercept = 1.0 if elastic else 2.0 / 3.0
    a_plat = eta * ampl / (1.0 if elastic else q)  # plateau (× accel·soil)
    base = accel * soil

    T_arr = np.asarray(T, dtype=float)
    if np.any(~np.isfinite(T_arr)):
        raise ValueError("T values must be finite")
    if np.any(T_arr < 0):
        raise ValueError("T values must be non-negative")

    scalar_in = T_arr.ndim == 0
    T_arr = np.atleast_1d(T_arr)

    Sa = np.empty_like(T_arr)
    with np.errstate(divide="ignore", invalid="ignore"):
        rising = T_arr < tb
        Sa[rising] = base * (intercept + (T_arr[rising] / tb) * (a_plat - intercept))

        plateau = (T_arr >= tb) & (T_arr <= tc)
        Sa[plateau] = base * a_plat

        falling = (T_arr > tc) & (T_arr <= td)
        Sa[falling] = base * a_plat * (tc / T_arr[falling])

        long_period = T_arr > td
        Sa[long_period] = base * a_plat * (tc * td / T_arr[long_period] ** 2)

    if not elastic:
        # The design spectrum shall not be taken less than β·a_g.
        Sa = np.maximum(Sa, _EC8_BETA_LOWER_BOUND * accel)

    return float(Sa[0]) if scalar_in else Sa


def _eurocode8_displacement_spectrum(
    T,
    ag_r: float,
    *,
    gamma_i: float = 1.0,
    ground_type: str = "A",
    spectrum_type: int = 1,
    zeta: float = 0.05,
    vertical: bool = False,
):
    """Evaluate the EN 1998-1 elastic displacement spectrum S_De(T).

    The displacement spectrum is obtained from the *elastic* acceleration
    spectrum by ``S_De(T) = S_e(T)·(T/2π)²`` (EN 1998-1 §3.2.2.4, Eq. 3.6).
    The behaviour factor q does not enter.

    The ordinates are **displacements** — ``acceleration-unit · s²`` —
    which for a consistent unit system (e.g. m/s², s) are lengths (m).

    Args:
        T: Period value(s) (s).
        ag_r: Reference peak ground acceleration on rock, in the model's
            acceleration units.
        gamma_i: Importance factor γI; ``a_g = gamma_i * ag_r``.
        ground_type: Ground type ``"A"``-``"E"`` (ignored when *vertical*).
        spectrum_type: 1 (high seismicity) or 2 (low seismicity).
        zeta: Damping ratio (fraction of critical).
        vertical: When True, use the vertical elastic spectrum (§3.2.2.3).

    Returns:
        float or np.ndarray: Spectral displacement(s) — a float for scalar
        *T*, otherwise an array.

    Raises:
        ValueError: Propagated from :func:`_eurocode8_spectrum`.
    """
    T_arr = np.asarray(T, dtype=float)
    scalar_in = T_arr.ndim == 0
    Se = _eurocode8_spectrum(
        T,
        ag_r,
        gamma_i=gamma_i,
        ground_type=ground_type,
        spectrum_type=spectrum_type,
        zeta=zeta,
        vertical=vertical,
        elastic=True,
    )
    Sde = np.asarray(Se) * (np.atleast_1d(T_arr) / (2.0 * np.pi)) ** 2
    return float(Sde[0]) if scalar_in else Sde


def _ec8_sample_grid(
    tb: float,
    tc: float,
    td: float,
    T_max: float,
    n_pts: int,
) -> np.ndarray:
    """Return the EC8 period grid, sampling T_B, T_C and T_D explicitly.

    Args:
        tb: Start of the constant-acceleration plateau (s).
        tc: Start of the constant-velocity branch (s).
        td: Start of the constant-displacement branch (s).
        T_max: Upper period bound (s).
        n_pts: Number of ordinates on the uniform grid.

    Returns:
        Ascending period ordinates (s) ≤ *T_max*, including the branch
        corners where they fall within range.
    """
    T_spec = np.unique(np.concatenate([np.linspace(0.0, T_max, n_pts), [tb, tc, td]]))
    return T_spec[T_spec <= T_max]


def _build_spectrum(
    cfg: dict,
    *,
    g: Optional[float] = None,
    units: Optional[dict] = None,
) -> tuple:
    """Build a GB 50011 response spectrum from a configuration dict.

    The dict should contain keys *intensity*, *acceleration*, *site_class*,
    *damping*, and *level* (``'frequent'`` / ``'fortification'`` / ``'rare'``).

    Args:
        cfg: Spectrum configuration dict (see above).
        g: Gravitational acceleration in the model's length-unit per second
            squared.  An explicit value is used as-is; ``None`` falls back
            to *units*, then to the shared SI constant
            :data:`fea_toolkit.utils.DEFAULT_GRAVITY_MS2` (9.80665 m/s²).
            Pass ``g_from_units(model.units)`` so the returned spectral
            accelerations are in the model's unit system (e.g. mm/s² for a
            millimetre model).
        units: Model units dict (e.g. ``{"F": "N", "L": "mm", "T": "C"}``).
            Ignored when *g* is given; otherwise the returned accelerations
            are scaled into the model's unit system via
            :func:`fea_toolkit.utils.g_from_units`.

    Returns
    -------
    tuple
        ``(T_spec, Sa_spec, alpha_max, tg, zeta, label)`` where *T_spec* and
        *Sa_spec* are lists of period (s) and spectral acceleration (m/s²),
        *alpha_max* is the seismic influence coefficient, *tg* is the
        characteristic period, *zeta* is the damping ratio, and *label* is
        a human-readable level name.
    """
    # GB 50011 Table 5.1.4-1: α_max for each level
    alpha_frequent = {6: 0.04, 7: 0.08, 8: 0.16, 9: 0.32}
    alpha_rare = {6: 0.28, 7: 0.50, 8: 0.90, 9: 1.40}

    def _fort_alpha(intensity, accel):
        return max(accel * 2.25, alpha_frequent.get(intensity, 0.08) * 2.5)

    intensity = cfg.get("intensity", 7)
    accel = cfg.get("acceleration", 0.10)
    level = cfg.get("level", "rare")
    tg = cfg.get("tg")
    zeta = cfg.get("damping", 0.05)

    # Site class → T_g (Table 5.1.4-2, Design Group 1)
    tg_map = {
        "I0": 0.20,
        "I1": 0.25,
        "II": 0.35,
        "III": 0.45,
        "IV": 0.65,
    }
    if tg is None:
        tg = tg_map.get(cfg.get("site_class", "II"), 0.35)

    if level == "frequent":
        alpha_max = alpha_frequent.get(intensity, 0.08)
        label = "Frequent (多遇)"
    elif level == "fortification":
        alpha_max = _fort_alpha(intensity, accel)
        label = "Fortification (设防)"
    else:
        alpha_max = alpha_rare.get(intensity, 0.50)
        label = "Rare (罕遇)"

    # Resolve g: explicit value → units dict → shared SI constant.  Never a
    # hardcoded literal (.clinerules §4.6); an explicit g is used verbatim.
    g = _resolve_g(g, units)
    gamma = 0.9 + (0.05 - zeta) / (0.3 + 6.0 * zeta)
    eta1 = max(0.0, 0.02 + (0.05 - zeta) / (4.0 + 32.0 * zeta))
    eta2 = max(0.55, 1.0 + (0.05 - zeta) / (0.08 + 1.6 * zeta))

    T_max = 6.0
    n_pts = 300
    T_spec = np.linspace(0.0, T_max, n_pts)
    Sa_spec = np.array(
        [
            (0.45 + (eta2 - 0.45) * 10.0 * T) * alpha_max * g
            if T <= 0.1
            else eta2 * alpha_max * g
            if tg >= T
            else (tg / T) ** gamma * eta2 * alpha_max * g
            if 5.0 * tg >= T
            else (eta2 * 0.2**gamma - eta1 * (T - 5.0 * tg)) * alpha_max * g
            if T > 0
            else 0.45 * alpha_max * g
            for T in T_spec
        ]
    )

    return T_spec.tolist(), Sa_spec.tolist(), alpha_max, tg, zeta, label


def _interp_sa(T_query, T_spec, Sa_spec):
    """Interpolate spectral acceleration values onto *T_query*.

    Parameters
    ----------
    T_query : array-like
        Target period values (s).
    T_spec : array-like
        Source period axis (s).
    Sa_spec : array-like
        Source spectral acceleration values (m/s²).

    Returns
    -------
    np.ndarray
        Interpolated acceleration values.
    """
    return np.interp(np.asarray(T_query), np.asarray(T_spec), np.asarray(Sa_spec))


def cqc_base_shear(
    eff_masses: list[float],
    periods: list[float],
    spectrum_fn: Any,
    damping: float = 0.05,
    T_rigid: Optional[float] = None,
    total_mass: Optional[float] = None,
) -> dict[str, Any]:
    """CQC‑combine modal base shears from a response spectrum.

    Uses the Der Kiureghian (1980) correlation formula for CQC combination,
    with optional rigid cut‑off and missing‑mass correction.

    Parameters
    ----------
    eff_masses : list of float
        Effective modal masses for the direction of interest (e.g.
        from ``modal_props["partiMassMX"]``).
    periods : list of float
        Modal periods (s).  Must be the same length as *eff_masses*.
    spectrum_fn : callable
        ``spectrum_fn(T)`` → spectral acceleration Sa(T) in **model units**
        (e.g. m/s²).  Called once per mode plus once at T=0 for the
        rigid cut‑off and missing‑mass correction.
    damping : float
        Damping ratio (default 0.05).
    T_rigid : float, optional
        Period threshold (s) — modes with *T < T_rigid* are treated as
        rigid (response taken at Sa(0)) and combined via SRSS with the
        flexible CQC result.  ``None`` means no rigid cut‑off.
    total_mass : float, optional
        Total mass of the structure for missing‑mass correction.  When
        omitted, no missing‑mass correction is applied.

    Returns
    -------
    dict
        ``modal_base_shear``: Per‑mode base shear before combination.
        ``base_shear_cqc``: CQC combination of flexible modes.
        ``base_shear_srss``: SRSS combination of all modes.
        ``base_shear_rigid_cutoff``: Rigid cut‑off contribution.
        ``base_shear_missing_mass``: Missing‑mass correction.
        ``base_shear_total``: SRSS of CQC + rigid + missing.
        ``total_mass``, ``captured_mass``, ``residual_mass``,
        ``participation_ratio``: Mass statistics.
        ``modal_periods``, ``spectral_accels``, ``effective_masses``:
        Per‑mode data.
        ``direction``, ``T_rigid``: Pass‑through metadata.
        ``n_modes_flexible``, ``n_modes_rigid``: Mode counts.
    """
    import math

    n_modes = len(periods)
    if n_modes == 0 or not eff_masses:
        return {}

    # Spectral acceleration at each modal period
    Sa = np.array([spectrum_fn(T) for T in periods])
    Sa_0 = spectrum_fn(0.0)

    # Per-mode base shear: V_n = S_a(T_n) × M_eff_n
    modal_shear = [Sa[i] * abs(eff_masses[i]) for i in range(n_modes)]

    # CQC on flexible modes only (via utils.cqc_combine)
    omega = [2.0 * math.pi / T if T > 0 else 1e12 for T in periods]
    damp_list = [damping] * n_modes

    rigid_indices: set = set()
    if T_rigid and T_rigid > 0:
        rigid_indices = {i for i, T in enumerate(periods) if T_rigid > T}
    # Always exclude modes with non-positive periods (invalid for CQC)
    flexible_indices = [i for i in range(n_modes) if i not in rigid_indices and periods[i] > 0]

    if flexible_indices:
        flex_shear = [modal_shear[i] for i in flexible_indices]
        flex_omega = [omega[i] for i in flexible_indices]
        flex_damp = [damp_list[i] for i in flexible_indices]
        V_cqc = _cqc_combine_modal(flex_shear, flex_omega, flex_damp)
    else:
        V_cqc = 0.0

    V_srss = math.sqrt(sum(v**2 for v in modal_shear))

    # Rigid part
    V_rigid = 0.0
    if rigid_indices:
        for i in rigid_indices:
            ratio = Sa_0 / Sa[i] if abs(Sa[i]) > 1e-12 else 1.0
            V_rigid += (modal_shear[i] * ratio) ** 2
        V_rigid = math.sqrt(V_rigid)

    # Missing-mass correction
    sum_meff_captured = sum(abs(eff_masses[i]) for i in range(n_modes))
    V_missing = 0.0
    if total_mass is not None and total_mass > 0:
        residual_mass = max(0.0, total_mass - sum_meff_captured)
        V_missing = Sa_0 * residual_mass
    else:
        residual_mass = 0.0

    # Total: SRSS of CQC + rigid + missing
    V_total = math.sqrt(V_cqc**2 + V_rigid**2 + V_missing**2)

    return {
        "modal_base_shear": modal_shear,
        "base_shear_cqc": V_cqc,
        "base_shear_srss": V_srss,
        "base_shear_rigid_cutoff": V_rigid,
        "base_shear_missing_mass": V_missing,
        "base_shear_rigid": V_rigid + V_missing,
        "base_shear_total": V_total,
        "total_mass": total_mass or 0.0,
        "captured_mass": sum_meff_captured,
        "residual_mass": residual_mass,
        "participation_ratio": (
            sum_meff_captured / total_mass if total_mass and total_mass > 0 else 0.0
        ),
        "modal_periods": periods,
        "spectral_accels": Sa.tolist(),
        "effective_masses": eff_masses,
        "T_rigid": T_rigid,
        "n_modes_flexible": len(flexible_indices),
        "n_modes_rigid": len(rigid_indices),
    }


# ── Legacy aliases ─────────────────────────────────────────────────
# ``cqc_base_shear`` was renamed from ``cqc_combine`` (2026-08-27) to
# disambiguate it from :func:`fea_toolkit.utils.cqc_combine` (the raw
# Der Kiureghian kernel).  Keep a thin wrapper for import stability.


def cqc_combine(
    eff_masses: list[float],
    periods: list[float],
    spectrum_fn: Any,
    damping: float = 0.05,
    T_rigid: Optional[float] = None,
    total_mass: Optional[float] = None,
) -> dict[str, Any]:
    """Legacy alias for :func:`cqc_base_shear`."""
    return cqc_base_shear(
        eff_masses=eff_masses,
        periods=periods,
        spectrum_fn=spectrum_fn,
        damping=damping,
        T_rigid=T_rigid,
        total_mass=total_mass,
    )
