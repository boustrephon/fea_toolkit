"""
GB 50011 seismic response spectrum computation and plotting.

Provides both a direct spectrum function (``_gb50011_spectrum``) and a
config-driven builder (``_build_spectrum``) that reads intensity, site
class, level and damping from a dictionary.  The two functions implement
the same GB 50011 elastic spectrum but use slightly different formulas
for the ascending branch:

* ``_gb50011_spectrum`` — older form: ``0.45 + 5.5·T``
* ``_build_spectrum`` — damping-corrected form: ``0.45 + (η₂ − 0.45)·10·T``

``plot_seismic_spectrum`` renders all three levels (frequent,
fortification, rare) on a single figure.

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

from .utils import cqc_combine as _cqc_combine_modal


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
            Gravitational acceleration (m/s²).  Defaults to ``9.81`` when
            ``None`` — override with ``g_from_units(model.units)`` to keep
            the spectrum in the model's unit system.
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
        if g is None:
            g = 9.81

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
    g: float = 9.81,
) -> np.ndarray:
    """Return spectral acceleration Sa (m/s²) for a GB 50011 elastic spectrum.

    Parameters
    ----------
    T_values : list of float
        Period values (s) at which to evaluate the spectrum.
    alpha_max : float
        Seismic influence coefficient maximum (Table 5.1.4-1).
    tg : float
        Characteristic period (s) — Site-class dependent (Table 5.1.4-2).
    gamma : float
        Descending-branch exponent (default 0.9 for 5 % damping).
    eta1 : float
        Linear-drop correction factor (default 0.02 for 5 % damping).
    eta2 : float
        Damping reduction factor (default 1.0 for 5 % damping).
    g : float
        Gravitational acceleration (m/s²).  Default 9.81.

    Returns
    -------
    np.ndarray
        Spectral acceleration values (m/s²).
    """
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


def _build_spectrum(cfg: dict) -> tuple:
    """Build a GB 50011 response spectrum from a configuration dict.

    The dict should contain keys *intensity*, *acceleration*, *site_class*,
    *damping*, and *level* (``'frequent'`` / ``'fortification'`` / ``'rare'``).

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

    g = 9.81
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


def cqc_combine(
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


def plot_seismic_spectrum(
    spec: dict,
    modal: Optional[dict] = None,
) -> Optional[Any]:
    """Plot GB 50011 design spectra at 3 levels (frequent / fortification / rare).

    Parameters
    ----------
    spec : dict
        Spectrum configuration dict compatible with ``_build_spectrum()``.
        Keys include *intensity*, *acceleration*, *site_class*, *damping*.
    modal : dict, optional
        Modal analysis result (``run_modal_analysis`` output).  When provided,
        vertical dashed lines mark the dominant period in X and Y directions.

    Returns
    -------
    matplotlib.figure.Figure or None
        The figure object, or ``None`` if matplotlib is unavailable.
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    levels_info = [
        ("Frequent", {**spec, "level": "frequent"}),
        ("Fortification", {**spec, "level": "fortification"}),
        ("Rare", {**spec, "level": "rare"}),
    ]
    colors = {"Frequent": "#2ca02c", "Fortification": "#1f77b4", "Rare": "#d62728"}

    # Roman numeral helpers for title
    _int_map = {6: "VI", 7: "VII", 8: "VIII", 9: "IX"}
    _int_display = _int_map.get(spec.get("intensity", 7), str(spec.get("intensity", 7)))
    _sc = spec.get("site_class", "II")
    _site_display = _sc.replace("0", "\u2080").replace("1", "\u2081")
    _accel = spec.get("acceleration", 0.10)

    T_max = 6.0
    T_plot = np.linspace(0.01, T_max, 300)

    fig, ax = plt.subplots(figsize=(9, 5))

    for label, cfg in levels_info:
        T_spec, Sa, amax, _tg, zeta, _lbl = _build_spectrum(cfg)
        ax.plot(
            T_plot,
            _interp_sa(T_plot, T_spec, Sa),
            label=f"{label} (α_max={amax:.2f}, ζ={zeta})",
            color=colors[label],
            linewidth=1.5,
        )

    # Vertical lines for fundamental periods
    if modal is not None:
        periods = modal.get("periods", [])
        mp = modal.get("modal_props", {})
        for dir_label, ratio_key in [("X", "partiMassRatiosMX"), ("Y", "partiMassRatiosMY")]:
            ratios = mp.get(ratio_key, [])
            best = max(range(len(ratios)), key=lambda i: abs(ratios[i])) if ratios else -1
            if best >= 0 and best < len(periods):
                T_dom = periods[best]
                ax.axvline(T_dom, color="grey", linestyle="--", linewidth=0.8, alpha=0.7)
                ax.text(
                    T_dom,
                    ax.get_ylim()[1] * 0.95,
                    f"T\u2081({dir_label})={T_dom:.3f}s",
                    fontsize=8,
                    rotation=90,
                    va="top",
                    ha="right",
                    alpha=0.7,
                )

    ax.set_xlabel("Period T (s)")
    ax.set_ylabel("Spectral acceleration S\u2090 (m/s\u00b2)")
    ax.set_title(f"GB 50011 Design Spectra \u2014 {_int_display}({_accel}g), Site {_site_display}")
    ax.set_xlim(0, T_max)
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    return fig
