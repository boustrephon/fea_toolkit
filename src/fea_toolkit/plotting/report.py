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
import pandas as pd


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


def plot_modal_participation(
    df_modal: Any,
    min_participation: float = 0.0,
) -> Optional[Any]:
    """Two-panel bar chart of mass participation by mode — translational and rotational DOFs.

    Each bar shows the mode's contribution (solid) with the cumulative sum
    (semi-transparent, same colour) stacked on top.  A dashed line marks
    the 90 % threshold.

    Parameters
    ----------
    df_modal : pd.DataFrame
        DataFrame with columns ``Mode``, ``Mx (%)``, ``My (%)``, ``Mz (%)``,
        ``Rx (%)``, ``Ry (%)``, ``Rz (%)`` — as produced by
        :func:`fea_toolkit.io.report.modal_table_enhanced`.
    min_participation : float
        Minimum participation percentage (e.g. ``1.0``) — modes where ALL
        translational DOFs are below this threshold are aggregated into a
        single \"low modes\" bar.  The cumulative sums at the end of the
        chart still include their contribution.  ``0.0`` = show all modes.

    Returns
    -------
    matplotlib.figure.Figure or None
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    data_all = df_modal[df_modal["Mode"] != "<strong>SUM</strong>"].copy()
    for col in data_all.columns:
        if col not in ("Mode", "Period (s)"):
            data_all[col] = pd.to_numeric(data_all[col], errors="coerce").fillna(0)

    n_all = len(data_all)
    if n_all == 0:
        return None

    # ── Filter modes by min_participation ─────────────────────────
    if min_participation > 0:
        transl_dofs = ["Mx (%)", "My (%)", "Mz (%)"]
        max_transl = data_all[transl_dofs].abs().max(axis=1)
        significant = data_all[max_transl >= min_participation].copy()
        n_hidden = n_all - len(significant)
        # Keep cumulative sums from ALL modes
        full_cum = {}
        for col in data_all.columns:
            if col not in ("Mode", "Period (s)"):
                full_cum[col] = data_all[col].sum()
    else:
        significant = data_all
        n_hidden = 0

    data = significant
    n_modes = len(data)

    # ── Stacked cumulative bar for hidden low-participation modes ──
    _hidden_bar = None
    _hidden_cum = {}
    if n_hidden > 0:
        _hidden_bar = {
            "Mode": f"<{min_participation}%",
        }
        for col in data_all.columns:
            if col not in ("Mode", "Period (s)"):
                _hidden_bar[col] = data_all[~data_all.index.isin(data.index)][col].sum()

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(max(8, n_modes * 0.55 + 2), 6),
        sharex=True,
    )

    # ── Helper: plot one panel ────────────────────────────────────
    def _plot_panel(ax, dofs, colors, title, y_lim):
        x = np.arange(n_modes + (1 if _hidden_bar else 0))
        w = 0.22
        for i, dof in enumerate(dofs):
            vals = data[dof].values if dof in data.columns else np.zeros(n_modes)
            cum = np.cumsum(vals)
            # Significant modes
            ax.bar(x[:n_modes] + (i - 1) * w, vals, w,
                   label=dof, color=colors[i], zorder=3)
            ax.bar(x[:n_modes] + (i - 1) * w, cum - vals, w,
                   bottom=vals, color=colors[i], alpha=0.10, zorder=3)
            # Hidden low-participation modes (aggregated)
            if _hidden_bar:
                h_val = _hidden_bar.get(dof, 0.0)
                hx = n_modes
                ax.bar(hx + (i - 1) * w, h_val, w,
                       color=colors[i], alpha=0.35, zorder=3,
                       hatch="//")
                # Cumulative marker at end
                total = full_cum.get(dof, cum[-1] + h_val)
                ax.annotate(f"{total:.0f}%",
                            xy=(hx + (i - 1) * w, total),
                            fontsize=6, color=colors[i],
                            ha="center", va="bottom",
                            fontweight="bold")
        ax.axhline(90, color="grey", linewidth=0.8, linestyle="--", zorder=2)
        ax.text(n_modes + 1.5 + (1 if _hidden_bar else 0), 91, "90 %",
                fontsize=7, color="grey", va="bottom")
        ax.set_ylabel("Mass participation (%)")
        ax.set_title(title, fontsize=10)
        ax.set_ylim(0, y_lim)
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3, axis="y")

    # ── Top: translational DOFs ──
    _plot_panel(
        ax1,
        ["Mx (%)", "My (%)", "Mz (%)"],
        ["#1f77b4", "#ff7f0e", "#2ca02c"],
        "Translational DOFs",
        105,
    )

    # ── Bottom: rotational DOFs ──
    rot_dofs = [d for d in ["Rx (%)", "Ry (%)", "Rz (%)"] if d in data.columns]
    rot_max = data[rot_dofs].values.max() if rot_dofs else 0
    _plot_panel(
        ax2,
        rot_dofs,
        ["#d62728", "#9467bd", "#8c564b"],
        "Rotational DOFs",
        max(105, rot_max * 1.2),
    )
    ax2.set_xlabel("Mode")
    n_ticks = n_modes + (1 if _hidden_bar else 0)
    x = np.arange(n_ticks)
    ax2.set_xticks(x)

    # X-axis labels: mode number above, period below
    if "Period (s)" in data.columns:
        periods = pd.to_numeric(data["Period (s)"], errors="coerce")
        labels = []
        for m, p in zip(data["Mode"], periods):
            if pd.notna(p):
                labels.append(f"{m}\nT={p:.2f}s")
            else:
                labels.append(str(m))
    else:
        labels = [str(m) for m in data["Mode"]]
    if _hidden_bar:
        labels.append(f"Low\n({n_hidden} modes)")
    ax2.set_xticklabels(labels, fontsize=7)

    fig.suptitle("Modal Mass Participation by Degree of Freedom",
                 fontsize=11, fontweight="bold")
    fig.tight_layout()
    return fig


def plot_rs_modal_analysis(modal_props: dict,
                           modal_base_shear_x: list,
                           modal_base_shear_y: list,
                           periods: Optional[List[float]] = None,
                           base_shear_rigid_x: float = 0.0,
                           base_shear_rigid_y: float = 0.0,
                           T_rigid: Optional[float] = None) -> Optional[Any]:
    """Two-panel figure: mass participation (top) + modal base shear (bottom).

    Parameters
    ----------
    modal_props : dict
        The ``modal_props`` dict from ``ops.modalProperties('-return', '-unorm')``.
    modal_base_shear_x : list
        Per-mode base shear values from RS analysis in X (kN).
    modal_base_shear_y : list
        Per-mode base shear values from RS analysis in Y (kN).
    periods : list of float, optional
        Modal periods in seconds.  If provided, shown on the x-axis
        beneath each mode number.  ``None`` = mode numbers only.

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

    ax2.bar(x - w, sx, w, label="RS-X", color="#1f77b4")
    ax2.bar(x,     sy, w, label="RS-Y", color="#ff7f0e")
    # Empty placeholder bar at the RZ position so bars align with the top panel
    ax2.bar(x + w, [0] * n, w, label="_RS-RZ", color="none", edgecolor="none")
    ax2.set_xlabel("Mode")
    ax2.set_ylabel("Base shear (kN)")
    ax2.set_title("Modal Base Shear \u2014 Response Spectrum Analysis")
    ax2.set_xticks(x)
    if periods is not None and len(periods) >= n:
        labels = [f"{i + 1}\nT={p:.2f}s" for i, p in enumerate(periods[:n])]
    else:
        labels = [str(i + 1) for i in range(n)]
    ax2.set_xticklabels(labels, fontsize=7)
    # ── Rigid cut-off (horizontal dashed line) ──
    if T_rigid is not None and (base_shear_rigid_x > 0 or base_shear_rigid_y > 0):
        label = f"Rigid (T\u2264{T_rigid:.3f}s)"
        if base_shear_rigid_x > 0:
            ax2.axhline(base_shear_rigid_x, color="#1f77b4", linewidth=1.0,
                        linestyle="--", alpha=0.7)
            ax2.text(n - 0.5, base_shear_rigid_x * 1.02, label,
                     color="#1f77b4", fontsize=6, ha="right", va="bottom")
        if base_shear_rigid_y > 0:
            label_y = f"Rigid Y (T≤{T_rigid:.3f}s)"
            ax2.axhline(base_shear_rigid_y, color="#ff7f0e", linewidth=1.0,
                        linestyle=":", alpha=0.7)
            ax2.text(n - 0.5, base_shear_rigid_y * 1.02, label_y,
                     color="#ff7f0e", fontsize=6, ha="right", va="bottom")
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

        # Determine bounds — if yield exceeds the visible range, report NA
        max_Sa = max(S_a.max(), Sa_plot.max())
        x_lim = 0.30
        yield_ok = (
            pp.get("S_dy") and pp["S_dy"] > 0
            and pp["S_dy"] <= x_lim and pp["S_ay"] <= max_Sa
        )
        yield_label = (
            f"Yield ({pp['S_dy']:.3f}, {pp['S_ay']:.1f})"
            if yield_ok else "Yield (NA, NA)"
        )
        pp_label = (
            f"Perf. Pt. ({pp['S_dp']:.3f}, {pp['S_ap']:.1f})"
            if pp["converged"] and pp["S_dp"] > 0 else ""
        )

        title_text = (
            f"{label}   μ={pp['mu']:.2f}  "
            f"$S_{{dp}}$=({pp['S_dp']:.3f}m, {pp['S_ap']:.1f}m/s²)"
        )

        ax.plot(S_d, S_a, "-o", markersize=2.5, linewidth=1.5,
                color="tab:blue", label="Capacity", zorder=3)
        ax.plot(Sd_plot, Sa_plot, "--", color="tab:red", linewidth=1.5,
                label="Demand (rare)", zorder=2)

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
                    markersize=10, zorder=6, label=pp_label)
            ax.axvline(pp["S_dp"], color="tab:green", linewidth=0.8,
                       linestyle="--", alpha=0.5)
            ax.axhline(pp["S_ap"], color="tab:green", linewidth=0.8,
                       linestyle="--", alpha=0.5)

        if pp.get("S_dy") and pp["S_dy"] > 0:
            ax.plot(pp["S_dy"], pp["S_ay"], "s", color="tab:orange",
                    markersize=7, zorder=5, label=yield_label)

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


# ========================================================================
# Storey force and displacement profile plots
# ========================================================================

def plot_storey_forces(
    df_shear: pd.DataFrame,
    df_moment: pd.DataFrame,
    *,
    force_unit: str = "kN",
    moment_unit: str = "kN·m",
    figsize: tuple = (8, 6),
    n_points: int = 100,
) -> Optional[Any]:
    """Side-by-side storey shear and moment profiles (smooth curves).

    **Principle**
    The base shear *V*:sub:`base` and base moment *M*:sub:`base` (taken
    from the row with the lowest elevation, i.e. the **Base** row) are
    used to reconstruct an **equivalent trapezoidal distributed load**
    :math:`w(z) = w_0 + (w_1 - w_0)\\,z/H` that satisfies:

    .. math::

        V_{\\mathrm{base}} = \\frac{w_0 + w_1}{2}\\,H \\qquad
        M_{\\mathrm{base}} = \\frac{2w_1 + w_0}{6}\\,H^{2}

    From :math:`w_0, w_1` the shear and moment at any elevation
    :math:`z` above the base are obtained by closed-form integration:

    .. math::

        V(z) = V_{\\mathrm{base}} - w_0 z - \\frac{w_1-w_0}{2H}\\,z^{2} \\\\
        M(z) = M_{\\mathrm{base}} - V_{\\mathrm{base}} z
               + \\frac{w_0}{2}\\,z^{2}
               + \\frac{w_1-w_0}{6H}\\,z^{3}

    The curves are evaluated at *n_points* elevations and plotted as
    smooth continuous lines, automatically satisfying
    :math:`dM/dz = -V(z)` (equilibrium for the free-body diagram of the
    portion **above** elevation *z*).

    **Shortcomings**

    * The load is assumed to vary **linearly** with height (trapezoidal).
      Actual wind or seismic load distributions may be more complex
      (e.g. power-law wind profiles, multi-modal seismic distributions).
    * The reconstruction uses only **two integral quantities** (*V*:sub:`base`,
      *M*:sub:`base`).  Any number of distributed-load shapes can produce
      the same base shear and moment — the trapezoidal shape is a
      convenient choice, not a unique solution.
    * Local force concentrations (e.g. point loads, stiff element
      connections) are smeared into the smooth distribution.
    * The plotted curves **do not** reflect element-level force
      variations — they are an *equivalent* smeared representation.

    **Contraindications**

    * **Do not use** for gravity (vertical) load cases — the trapezoidal
      model assumes horizontal loading only.
    * **Do not use** when the storey-level DataFrames lack a Base row
      (first row with the base reaction value) and a Roof row (last row
      with the roof value).
    * **Do not use** for drift / displacement profiles — those are
      computed directly from nodal displacements and have nothing to do
      with the equivalent load distribution.
    * For element-level force verification use
      :func:`~fea_toolkit.opensees.builder.OpenSeesBuilder.extract_static_element_forces`
      together with :func:`~fea_toolkit.model.storey_response.storey_shears`
      (Option B / nodal summation approach).

    Parameters
    ----------
    df_shear : pd.DataFrame
        Columns ``Storey``, ``Elevation``, plus one column per load case.
        First row must be the **Base** row (with base shear value).
    df_moment : pd.DataFrame
        Same structure for moment.
    force_unit, moment_unit : str
        Axis labels.
    figsize : tuple
        Figure dimensions.
    n_points : int
        Number of evaluation points along the height.

    Returns
    -------
    matplotlib.figure.Figure or None
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=figsize, sharey=True)

    # Resolve elevation column first, then exclude it from data columns
    elev_candidates = [c for c in df_shear.columns if "Elevation" in c]
    if not elev_candidates:
        return None
    elev_col_s = elev_candidates[0]
    elev_col_m = ([c for c in df_moment.columns if "Elevation" in c]
                  or [None])[0]

    base_skip = {"Storey", elev_col_s}
    if elev_col_m and elev_col_m != elev_col_s:
        base_skip.add(elev_col_m)
    num_cols = [c for c in df_shear.columns if c not in base_skip]
    mom_cols = [c for c in df_moment.columns if c not in base_skip]
    # Only use columns that exist in both (matching cases)
    common_cols = [c for c in num_cols if c in mom_cols]
    dropped_shear = [c for c in num_cols if c not in common_cols]
    dropped_moment = [c for c in mom_cols if c not in common_cols]
    if dropped_shear or dropped_moment:
        import warnings
        if dropped_shear:
            warnings.warn(
                f"Columns in shear DataFrame not found in moment DataFrame: "
                f"{dropped_shear}")
        if dropped_moment:
            warnings.warn(
                f"Columns in moment DataFrame not found in shear DataFrame: "
                f"{dropped_moment}")
    elev = df_shear[elev_col_s].values
    base_elev = elev.min()
    roof_elev = elev.max()
    H = roof_elev - base_elev
    if H < 1e-12:
        return None

    def _trapezoidal_curves(V_base, M_base):
        """Return (z_pts, V_pts, M_pts) for smooth V(z), M(z) curves
        reconstructed from the equivalent trapezoidal load.
        """
        if abs(V_base) < 1e-6:
            return np.array([]), np.array([]), np.array([])
        # Solve for trapezoidal load coefficients w0 (base), w1 (roof)
        w_sum = 2.0 * V_base / H
        w1_plus_w0 = 6.0 * M_base / H**2 if abs(M_base) > 1e-6 else w_sum
        w1 = w1_plus_w0 - w_sum
        w0 = w_sum - w1
        # Evaluate at n_points
        z_pts = np.linspace(0, H, n_points)
        V_pts = V_base - w0*z_pts - (w1 - w0) * z_pts**2 / (2.0 * H)
        M_pts = (M_base - V_base*z_pts + w0*z_pts**2/2.0
                 + (w1 - w0) * z_pts**3 / (6.0 * H))
        elev_pts = base_elev + z_pts
        return elev_pts, V_pts, M_pts

    for col in common_cols:
        # Identify Base row by minimum elevation (first row may not be Base)
        base_idx_s = int(df_shear[elev_col_s].idxmin())
        base_idx_m = int(df_moment[elev_col_m].idxmin()) if elev_col_m else base_idx_s
        V_base = abs(df_shear[col].loc[base_idx_s])
        M_base = abs(df_moment[col].loc[base_idx_m])
        if V_base < 1e-12:
            continue
        z_pts, V_pts, M_pts = _trapezoidal_curves(V_base, M_base)
        if len(z_pts) == 0:
            continue
        ax1.plot(V_pts, z_pts, label=col)
        ax2.plot(M_pts, z_pts, label=col)

    ax1.set_xlabel(f"Storey shear ({force_unit})")
    ax1.set_ylabel("Elevation")
    ax1.set_title("Storey Shear")
    ax1.grid(True, alpha=0.3)
    ax1.legend(fontsize=7)

    ax2.set_xlabel(f"Storey moment ({moment_unit})")
    ax2.set_title("Storey Moment")
    ax2.grid(True, alpha=0.3)
    ax2.legend(fontsize=7)

    fig.suptitle("Storey Forces & Moments", fontsize=11, fontweight="bold")
    fig.tight_layout()
    return fig


def plot_storey_displacements(
    df_disp: pd.DataFrame,
    df_drift: pd.DataFrame,
    *,
    disp_unit: str = "mm",
    drift_unit: str = "mm/m",
    source_length_unit: str = "m",
    figsize: tuple = (8, 6),
) -> Optional[Any]:
    """Side-by-side storey displacement and drift profiles.

    Displacement and drift values are absolute.  Elevation on the y-axis,
    displacement/drift on the x-axis.

    Parameters
    ----------
    df_disp : pd.DataFrame
        Columns ``Storey``, ``Elevation``, ``Peak_disp`` (and optionally
        ``Ux``, ``Uy``).  Peak displacement is the worst-node resultant.
    df_drift : pd.DataFrame
        Columns ``Storey``, ``Elevation``, ``Drift_peak`` (and optionally
        ``Drift_X``, ``Drift_Y``).
    disp_unit, drift_unit : str
        Axis labels.
    source_length_unit : str
        Length unit of the raw data (``"m"``, ``"mm"``, etc.).  Defaults
        to ``"m"`` (SI).  Use ``md.units.get("L", "m")`` from the model
        metadata to derive the correct value.
    figsize : tuple
        Figure dimensions.

    Returns
    -------
    matplotlib.figure.Figure or None
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=figsize, sharey=True)

    # Resolve elevation column
    elev_col_disp = "Elevation" if "Elevation" in df_disp.columns else (
        [c for c in df_disp.columns if "Elevation" in c][0]
        if any("Elevation" in c for c in df_disp.columns) else None)
    elev_col_drift = "Elevation" if "Elevation" in df_drift.columns else (
        [c for c in df_drift.columns if "Elevation" in c][0]
        if any("Elevation" in c for c in df_drift.columns) else None)

    if elev_col_disp is None or elev_col_drift is None:
        return None

    # Unit conversion: raw data is in source_length_unit, display in disp_unit / drift_unit
    _LENGTH_TO_METRE = {"m": 1.0, "mm": 0.001, "cm": 0.01, "ft": 0.3048, "in": 0.0254}
    if source_length_unit not in _LENGTH_TO_METRE:
        raise ValueError(
            f"Unsupported source_length_unit={source_length_unit!r}. "
            f"Supported: {list(_LENGTH_TO_METRE)}"
        )
    if disp_unit not in _LENGTH_TO_METRE:
        raise ValueError(
            f"Unsupported disp_unit={disp_unit!r}. "
            f"Supported: {list(_LENGTH_TO_METRE)}"
        )
    source_to_m = _LENGTH_TO_METRE[source_length_unit]
    target_to_m = _LENGTH_TO_METRE[disp_unit]
    disp_scale = source_to_m / target_to_m

    # drift_scale: drift values are dimensionless ratios (m/m).
    # Parse the display unit to get the multiplier.
    _DRIFT_SCALES = {
        "mm/m": 1000.0,
        "cm/m": 100.0,
        "%": 100.0,
        "m/m": 1.0,
        "rad": 1.0,
        "radians": 1.0,
    }
    if drift_unit not in _DRIFT_SCALES:
        raise ValueError(
            f"Unsupported drift_unit={drift_unit!r}. "
            f"Supported: {list(_DRIFT_SCALES)}"
        )
    drift_scale = _DRIFT_SCALES[drift_unit]

    # Exclude the resolved elevation columns dynamically
    base_skip = {"Storey"}
    disp_cols = [c for c in df_disp.columns
                 if c not in base_skip and c != elev_col_disp]
    drift_cols = [c for c in df_drift.columns
                  if c not in base_skip and c != elev_col_drift]

    elev_disp = df_disp[elev_col_disp].values
    elev_drift = df_drift[elev_col_drift].values
    base_elev = min(elev_disp.min(), elev_drift.min())

    for col in disp_cols:
        vals = df_disp[col].values * disp_scale
        # Prepend zero at base elevation for a continuous profile
        x_plot = np.concatenate([[0.0], vals])
        y_plot = np.concatenate([[base_elev], elev_disp])
        ax1.plot(x_plot, y_plot, "o-", label=col)
    ax1.set_xlabel(f"Displacement ({disp_unit})")
    ax1.set_ylabel("Elevation")
    ax1.set_title("Storey Displacement")
    ax1.grid(True, alpha=0.3)
    ax1.legend(fontsize=7)

    for col in drift_cols:
        vals = df_drift[col].values * drift_scale
        x_plot = np.concatenate([[0.0], vals])
        y_plot = np.concatenate([[base_elev], elev_drift])
        ax2.plot(x_plot, y_plot, "s-", label=col)
    ax2.set_xlabel(f"Drift ({drift_unit})")
    ax2.set_title("Storey Drift")
    ax2.grid(True, alpha=0.3)
    ax2.legend(fontsize=7)

    fig.suptitle("Storey Displacement & Drift", fontsize=11, fontweight="bold")
    fig.tight_layout()
    return fig
