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
"""

from typing import Dict, List, Optional, Any, Tuple
import numpy as np


def _gb50011_spectrum(
    T_values: List[float],
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
        Descending-branch exponent (default 0.9 for 5 % damping).
    eta1 : float
        Linear-drop correction factor (default 0.02 for 5 % damping).
    eta2 : float
        Damping reduction factor (default 1.0 for 5 % damping).
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
        elif T <= tg:
            Sa.append(alpha_max * g)
        elif T <= 5.0 * tg:
            Sa.append((tg / T) ** gamma * eta2 * alpha_max * g)
        else:
            Sa.append((eta2 * 0.2 ** gamma - eta1 * (T - 5.0 * tg)) * alpha_max * g)
    return np.array(Sa)


def _build_spectrum(cfg: dict) -> Tuple:
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
    tg = cfg.get("tg", None)
    zeta = cfg.get("damping", 0.05)

    # Site class → T_g (Table 5.1.4-2, Design Group 1)
    tg_map = {
        "I0": 0.20, "I1": 0.25, "II": 0.35, "III": 0.45, "IV": 0.65,
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
    Sa_spec = np.array([
        (0.45 + (eta2 - 0.45) * 10.0 * T) * alpha_max * g if T <= 0.1 else
        eta2 * alpha_max * g if T <= tg else
        (tg / T) ** gamma * eta2 * alpha_max * g if T <= 5.0 * tg else
        (eta2 * 0.2 ** gamma - eta1 * (T - 5.0 * tg)) * alpha_max * g
        if T > 0 else 0.45 * alpha_max * g
        for T in T_spec
    ])

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


def plot_seismic_spectrum(
    spec: dict,
    modal: Optional[Dict] = None,
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
    _int_map = {6: 'VI', 7: 'VII', 8: 'VIII', 9: 'IX'}
    _int_display = _int_map.get(spec.get('intensity', 7), str(spec.get('intensity', 7)))
    _sc = spec.get('site_class', 'II')
    _site_display = _sc.replace('0', '\u2080').replace('1', '\u2081')
    _accel = spec.get('acceleration', 0.10)

    T_max = 6.0
    T_plot = np.linspace(0.01, T_max, 300)

    fig, ax = plt.subplots(figsize=(9, 5))

    for label, cfg in levels_info:
        T_spec, Sa, amax, tg, zeta, _lbl = _build_spectrum(cfg)
        ax.plot(T_plot, _interp_sa(T_plot, T_spec, Sa),
                label=f"{label} (α_max={amax:.2f}, ζ={zeta})",
                color=colors[label], linewidth=1.5)

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
                ax.text(T_dom, ax.get_ylim()[1] * 0.95,
                        f"T\u2081({dir_label})={T_dom:.3f}s", fontsize=8,
                        rotation=90, va="top", ha="right", alpha=0.7)

    ax.set_xlabel("Period T (s)")
    ax.set_ylabel("Spectral acceleration S\u2090 (m/s\u00b2)")
    ax.set_title(
        f"GB 50011 Design Spectra \u2014 {_int_display}({_accel}g), Site {_site_display}"
    )
    ax.set_xlim(0, T_max)
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    return fig
