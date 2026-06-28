"""
Reusable matplotlib-based plotting functions for structural analysis reports.

All functions return a ``matplotlib.figure.Figure`` (or ``None`` if
matplotlib is unavailable) and are designed for direct display in Quarto
notebooks.

See also :mod:`fea_toolkit.plotting.viz` for PyVista 3D views.
"""

from typing import Dict, List, Optional, Any
import math
import numpy as np


def plot_pushover_curves(
    all_out: Dict,
    units: Optional[Dict] = None,
) -> Optional[Any]:
    """Plot pushover capacity curves for all 4 directions.

    Parameters
    ----------
    all_out : dict
        Pushover results nested as ``{direction: {"results": ...,
        "pp": {"converged": ..., "D_roof": ..., "V_base": ...}}}``.
    units : dict, optional
        Unit dict (e.g. ``md.units``) used for the base-shear axis label.

    Returns
    -------
    matplotlib.figure.Figure or None
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    fig, ax = plt.subplots(figsize=(9, 6))
    clr = {"+X": "#1f77b4", "-X": "#ff7f0e", "+Y": "#2ca02c", "-Y": "#d62728"}

    for lb in ["+X", "-X", "+Y", "-Y"]:
        r = all_out[lb]["results"]
        ax.plot(r["control_disp"], r["base_shear"],
                label=lb, color=clr[lb], lw=1.5)
        pp = all_out[lb]["pp"]
        if pp["converged"] and pp["D_roof"] != 0:
            ax.plot(pp["D_roof"], pp["V_base"], "D", color=clr[lb], ms=10)

    force_unit = (units or {}).get("F", "?")
    ax.set_xlabel("Control node displacement (m)")
    ax.set_ylabel(f"Base shear ({force_unit})")
    ax.set_title("Pushover Curves (4 directions)")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    return fig


def plot_modal_participation(df_modal: Any) -> Optional[Any]:
    """Side-by-side bar chart of mass participation by mode, coloured by DOF.

    Parameters
    ----------
    df_modal : pd.DataFrame
        DataFrame with columns ``Mode``, ``Mx (%)``, ``My (%)``, ``Mz (%)``,
        ``Rx (%)``, ``Ry (%)``, ``Rz (%)`` — as produced by
        :func:`fea_toolkit.io.report.modal_table_enhanced`.

    Returns
    -------
    matplotlib.figure.Figure or None
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    data = df_modal[df_modal["Mode"] != "<strong>SUM</strong>"].copy()
    dofs = ["Mx (%)", "My (%)", "Mz (%)", "Rx (%)", "Ry (%)", "Rz (%)"]
    colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b"]
    n_modes = len(data)
    fig, ax = plt.subplots(figsize=(max(8, n_modes * 0.6), 5))
    x = np.arange(n_modes)
    width = 0.12
    for i, dof in enumerate(dofs):
        vals = data[dof].values if dof in data.columns else [0] * n_modes
        ax.bar(x + i * width, vals, width, label=dof, color=colors[i])
    ax.set_xlabel("Mode")
    ax.set_ylabel("Mass participation (%)")
    ax.set_title("Modal Mass Participation by Degree of Freedom")
    ax.set_xticks(x + width * 2.5)
    ax.set_xticklabels([str(m) for m in data["Mode"]])
    ax.set_ylim(0, 105)
    ax.legend(fontsize=7, ncol=2)
    ax.grid(True, alpha=0.3, axis="y")
    fig.tight_layout()
    return fig


def plot_rs_modal_analysis(modal_props: dict,
                           modal_base_shear_x: list,
                           modal_base_shear_y: list) -> Optional[Any]:
    """Two-panel figure: mass participation (top) + modal base shear (bottom).

    Parameters
    ----------
    modal_props : dict
        The ``modal_props`` dict from ``ops.modalProperties('-return', '-unorm')``.
    modal_base_shear_x : list
        Per-mode base shear values from RS analysis in X (kN).
    modal_base_shear_y : list
        Per-mode base shear values from RS analysis in Y (kN).

    Returns
    -------
    matplotlib.figure.Figure or None
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    n = max(len(modal_props.get("partiMassRatiosMX", [])),
            len(modal_base_shear_x))

    fig, (ax1, ax2) = plt.subplots(2, 1,
        figsize=(max(8, n * 0.45), 6),
        sharex=True, gridspec_kw={"height_ratios": [1, 2]})
    x = np.arange(n)
    w = 0.25

    def _pad(v, n_):
        return (list(v) + [0] * n_)[:n_]

    # ── Top: mass participation ──
    mx = _pad(modal_props.get("partiMassRatiosMX", []), n)
    my = _pad(modal_props.get("partiMassRatiosMY", []), n)
    mrz = _pad(modal_props.get("partiMassRatiosRMZ", []), n)

    ax1.bar(x - w, mx, w, label="X", color="#1f77b4")
    ax1.bar(x,     my, w, label="Y", color="#ff7f0e")
    ax1.bar(x + w, mrz, w, label="RZ", color="#2ca02c")
    ax1.set_ylabel("Mass participation (%)")
    ax1.set_title("Modal Mass Participation")
    ax1.legend(fontsize=8, ncol=3)
    ax1.grid(True, alpha=0.3, axis="y")

    # ── Bottom: modal base shear ──
    sx = _pad(modal_base_shear_x, n)
    sy = _pad(modal_base_shear_y, n)

    ax2.bar(x - w / 2, sx, w, label="RS-X", color="#1f77b4")
    ax2.bar(x + w / 2, sy, w, label="RS-Y", color="#ff7f0e")
    ax2.set_xlabel("Mode")
    ax2.set_ylabel("Base shear (kN)")
    ax2.set_title("Modal Base Shear \u2014 Response Spectrum Analysis")
    ax2.set_xticks(x)
    ax2.set_xticklabels([str(i + 1) for i in range(n)])
    ax2.legend(fontsize=8)
    ax2.grid(True, alpha=0.3, axis="y")

    fig.tight_layout()
    return fig


def plot_csm_4panel(
    all_out: Dict,
    modal: Dict,
    tg: float = 0.25,
    zeta: float = 0.05,
    alpha_max_rare: float = 0.50,
    g: float = 9.81,
    out_dir: Optional[str] = None,
) -> Optional[Any]:
    """Generate a 2×2 ADRS Capacity Spectrum Method plot for all 4 directions.

    Parameters
    ----------
    all_out : dict
        Pushover output dict from ``run_pushover_4dir()`` or ``run_pushover_truss()``.
    modal : dict
        Modal analysis result (``run_modal_analysis`` output).
    tg : float
        Characteristic period (s).
    zeta : float
        Damping ratio.
    alpha_max_rare : float
        Rare-earthquake seismic influence coefficient.
    g : float
        Gravitational acceleration (m/s²).
    out_dir : str, optional
        If provided, save the figure as ``csm_4panel.png`` to this directory.

    Returns
    -------
    matplotlib.figure.Figure or None
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    from ..spectrum import _gb50011_spectrum

    dirs = ["+X", "-X", "+Y", "-Y"]

    gamma = 0.9 + (0.05 - zeta) / (0.3 + 6.0 * zeta)
    eta_1 = max(0.0, 0.02 + (0.05 - zeta) / (4.0 + 32.0 * zeta))
    eta_2 = max(0.55, 1.0 + (0.05 - zeta) / (0.08 + 1.6 * zeta))

    T_max_plot = 6.0
    n_plot = 200
    T_plot = np.linspace(0.01, T_max_plot, n_plot)
    Sa_plot = _gb50011_spectrum(T_plot, alpha_max_rare, tg,
                                 gamma=gamma, eta1=eta_1, eta2=eta_2, g=g)
    Sd_plot = Sa_plot * (T_plot / (2.0 * math.pi)) ** 2

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    axes_flat = axes.flatten()

    for idx, label in enumerate(dirs):
        ax = axes_flat[idx]
        data = all_out[label]
        adrs = data["adrs"]
        pp = data["pp"]

        S_d = np.array(adrs["S_d"])
        S_a = np.array(adrs["S_a"])

        title_text = (
            f"{label}   \u03bc={pp['mu']:.2f}  "
            f"$S_{{dp}}$=({pp['S_dp']:.3f}m, {pp['S_ap']:.1f}m/s\u00b2)"
        )

        ax.plot(S_d, S_a, "-o", markersize=2.5, linewidth=1.5,
                color="tab:blue", label="Capacity", zorder=3)
        ax.plot(Sd_plot, Sa_plot, "--", color="tab:red", linewidth=1.5,
                label="Demand (rare)", zorder=2)

        x_lim = 0.30
        y_lim = max(S_a.max(), Sa_plot.max()) * 1.15
        for T in [0.1, 0.2, 0.5, 1.0, 2.0, 4.0]:
            sd_test = np.linspace(0, x_lim, 200)
            sa_test = (2.0 * math.pi / T) ** 2 * sd_test
            mask = sa_test <= y_lim
            if not mask.any():
                continue
            ax.plot(sd_test[mask], sa_test[mask], ":", color="grey",
                    linewidth=0.5, alpha=0.3)
            ax.text(sd_test[mask][-1], sa_test[mask][-1], f"T={T}s", fontsize=6,
                    color="grey", alpha=0.5, va="bottom", ha="left")

        if pp["converged"] and pp["S_dp"] > 0:
            ax.plot(pp["S_dp"], pp["S_ap"], "D", color="tab:green",
                    markersize=10, zorder=6)
            ax.axvline(pp["S_dp"], color="tab:green", linewidth=0.8,
                       linestyle="--", alpha=0.5)
            ax.axhline(pp["S_ap"], color="tab:green", linewidth=0.8,
                       linestyle="--", alpha=0.5)

        if pp.get("S_dy") and pp["S_dy"] > 0:
            ax.plot(pp["S_dy"], pp["S_ay"], "s", color="tab:orange",
                    markersize=7, zorder=5,
                    label=f"Yield ({pp['S_dy']:.3f}, {pp['S_ay']:.1f})")

        ax.set_title(title_text, fontsize=10, fontweight="bold")
        ax.set_xlabel("S$_d$ (m)", fontsize=9)
        ax.set_ylabel("S$_a$ (m/s\u00b2)", fontsize=9)
        ax.set_xlim(0, x_lim)
        ax.set_ylim(0, y_lim)
        ax.grid(True, alpha=0.25)
        ax.legend(fontsize=7, loc="upper left")
        ax.tick_params(labelsize=8)

    fig.suptitle(
        f"Pumphouse \u2014 Capacity Spectrum Method\n"
        f"GB\u200950011 Rare Earthquake, Intensity VII(0.10g), Site I\u2081 (Tg={tg}s)",
        fontsize=12, fontweight="bold", y=0.98,
    )
    fig.subplots_adjust(top=0.88, bottom=0.08, hspace=0.28, wspace=0.25)

    if out_dir:
        from pathlib import Path
        p = Path(out_dir) / "csm_4panel.png"
        fig.savefig(p, dpi=200, bbox_inches="tight")

    return fig
