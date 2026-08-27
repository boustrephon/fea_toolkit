"""GB 50011 design-spectrum figure (frequent / fortification / rare levels).

Kept in the plotting layer (not :mod:`fea_toolkit.spectrum`) because it is
a matplotlib figure generator; :mod:`fea_toolkit.spectrum` re-exports it
for backward compatibility.
"""

from typing import Any, Optional

import numpy as np

from ..spectrum import _build_spectrum, _interp_sa


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
