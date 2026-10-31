"""IK-2005 incremental-dynamic-analysis (IDA) curve and fragility plots.

Plots the per-record IDA curves (peak interstorey-drift ratio vs
``Sa(T1, 5 %)/g``) and the collapse fragility curve from a unified results
archive's ``ida`` block (schema v5).  Accepts a flat NPZ dict
(:func:`fea_toolkit.io.npz_reader.read_results_npz`) or the output of
:func:`fea_toolkit.io.npz_writer.collect_ida_arrays`.
"""

from typing import Any, Optional

import numpy as np

from ..io.results_schema import ida_record_names, make_ida_key


def _scalar(data: dict, key: str) -> Optional[float]:
    arr = data.get(key)
    if arr is None:
        return None
    return float(np.asarray(arr).reshape(-1)[0])


def _record_str(data: dict, key: str) -> Optional[str]:
    """Read a per-record scalar string (e.g. the collapse criterion)."""
    arr = data.get(key)
    if arr is None:
        return None
    a = np.asarray(arr).reshape(-1)
    if a.size == 0:
        return None
    return str(a[0])


def _ida_curves(
    data: dict,
) -> tuple[list[str], list[tuple[str, np.ndarray, np.ndarray, np.ndarray]]]:
    """Return ``(records, curves)`` — per-record ``(im, drift, converged)``.

    The IM axis is ``scale × Sa_T1_g`` (spectral acceleration at T1, normalised
    by g, the IK-2005 intensity measure); the EDP axis is the peak
    interstorey-drift ratio.  ``converged`` is the per-rung boolean ladder the
    plotter uses to separate the converged response (the curve) from the
    non-converged (collapse) rungs, whose recorded drift is the peak reached
    before numerical failure rather than the true flatline response.
    """
    records = [str(r) for r in data.get("ida/record_id", [])]
    if not records:
        records = ida_record_names(data)
    curves: list[tuple[str, np.ndarray, np.ndarray, np.ndarray]] = []
    for rid in records:
        scale = np.asarray(data.get(make_ida_key(rid, "ida/{record}/scale")), dtype=float)
        drift = np.asarray(data.get(make_ida_key(rid, "ida/{record}/drift")), dtype=float)
        converged = np.asarray(data.get(make_ida_key(rid, "ida/{record}/converged")), dtype=bool)
        if converged.shape != drift.shape:
            converged = np.ones(drift.shape, dtype=bool)
        sa_g = _scalar(data, make_ida_key(rid, "ida/{record}/Sa_T1_g"))
        sa_g = sa_g if sa_g is not None else 1.0
        curves.append((rid, scale * sa_g, drift, converged))
    return records, curves


def _median_curve(
    curves: list[tuple[str, np.ndarray, np.ndarray, np.ndarray]],
    caps: np.ndarray,
    n_grid: int = 120,
) -> tuple[np.ndarray, np.ndarray]:
    """Median drift across records on a shared log-spaced IM grid.

    Only converged rungs strictly below each record's collapse IM contribute —
    the non-converged rungs and any post-collapse rungs are excluded, matching
    the truncated per-record curves.
    """
    pieces: list[tuple[np.ndarray, np.ndarray]] = []
    for (_rid, im, drift, c), cap in zip(curves, caps):
        if not np.any(c):
            continue
        keep = c & (im < cap if np.isfinite(cap) else True)
        if np.any(keep):
            pieces.append((im[keep], drift[keep]))
    if not pieces:
        return np.array([]), np.array([])
    im_all = np.concatenate([im for im, _ in pieces])
    lo, hi = float(im_all.min()), float(im_all.max())
    if lo <= 0 or hi <= 0 or hi <= lo:
        return np.array([]), np.array([])
    grid = np.logspace(np.log10(lo), np.log10(hi), n_grid)
    medians = []
    for g in grid:
        vals = [float(np.interp(g, im, drift)) for im, drift in pieces if im[0] <= g <= im[-1]]
        medians.append(float(np.median(vals)) if vals else np.nan)
    return grid, np.asarray(medians)


def _collapse_capacities(data: dict, records: list[str]) -> np.ndarray:
    caps = np.asarray(data.get("ida/collapse_Sa_T1_g"), dtype=float)
    if caps.size == len(records):
        return caps
    # Fall back to per-record collapse_scale × Sa_T1_g.
    out = []
    for rid in records:
        cs = _scalar(data, make_ida_key(rid, "ida/{record}/collapse_scale"))
        sa_g = _scalar(data, make_ida_key(rid, "ida/{record}/Sa_T1_g"))
        if cs is None or sa_g is None or not np.isfinite(cs):
            out.append(np.nan)
        else:
            out.append(cs * sa_g)
    return np.asarray(out, dtype=float)


def plot_ida_curves(
    data: dict,
    *,
    ax: Any = None,
    figsize: tuple[float, float] = (9, 6),
    max_drift: float = 0.12,
    show_median: bool = True,
    show_collapse: bool = True,
    flatline: bool = False,
    title: Optional[str] = None,
) -> Optional[Any]:
    """Plot the per-record IDA curves (drift vs ``Sa(T1, 5 %)/g``).

    Each record's curve is drawn through its **converged** rungs and stops at
    its collapse intensity (post-collapse rungs are not drawn).  The collapse is
    then marked according to which criterion tripped first:

    * **non-convergence** (dynamic instability) — either a ``×`` at the first
      failed rung (*flatline* ``False``, the default) or the classic horizontal
      **flatline** (IM caps out, ``drift → ∞``) at the collapse intensity
      (*flatline* ``True``);
    * **drift** (peak interstorey drift exceeded ``ida/collapse_drift``) — a
      vertical dotted line at ``θ = collapse_drift`` rising to the collapse
      intensity.

    Also draws the median drift curve and the collapse capacities as markers at
    the panel edge (optional).

    Args:
        data: Flat NPZ dict carrying the ``ida`` block (schema v5).
        ax: Optional axes to draw into (``None`` creates a figure).
        figsize: Figure size when *ax* is ``None``.
        max_drift: Upper x-limit on the peak-drift axis.
        show_median: Draw the median IDA curve.
        show_collapse: Mark each record's collapse capacity.
        flatline: When ``True``, draw the classic horizontal flatline (IM caps
            out, ``drift → ∞``) at each non-convergence collapse instead of the
            ``×`` marker.
        title: Optional figure title.

    Returns:
        The matplotlib figure, or ``None`` if matplotlib is unavailable.
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    records, curves = _ida_curves(data)
    if not curves:
        return None

    if ax is None:
        fig, ax = plt.subplots(figsize=figsize)
    else:
        fig = ax.figure

    caps = _collapse_capacities(data, records)
    criteria = [
        _record_str(data, make_ida_key(rid, "ida/{record}/collapse_criterion")) for rid in records
    ]
    collapse_drift = _scalar(data, "ida/collapse_drift")
    if collapse_drift is None or collapse_drift <= 0.0:
        collapse_drift = 0.10

    nonconv_labeled = False
    flat_labeled = False
    drift_labeled = False
    for i, (_rid, im, drift, converged) in enumerate(curves):
        cap = caps[i]
        crit = criteria[i]
        collapsed = bool(np.isfinite(cap))

        # Stop the curve at the collapse IM: keep only converged rungs strictly
        # below it (the collapse itself is shown by the indicator below).
        below = im < cap if collapsed else np.ones_like(im, dtype=bool)
        keep = converged & below
        ax.plot(drift[keep], im[keep], color="#9ecae1", linewidth=1.0, alpha=0.7, zorder=1)
        last_drift = float(drift[keep][-1]) if keep.any() else 0.0

        if not collapsed:
            continue

        if crit == "nonconvergence":
            if flatline:
                if last_drift < max_drift:
                    ax.plot(
                        [last_drift, max_drift],
                        [cap, cap],
                        color="#444444",
                        linestyle="--",
                        linewidth=1.1,
                        alpha=0.9,
                        zorder=2,
                        label="collapse (flatline)" if not flat_labeled else None,
                    )
                    ax.plot(
                        max_drift,
                        cap,
                        marker=">",
                        markersize=7,
                        color="#444444",
                        linestyle="none",
                        clip_on=False,
                        zorder=3,
                    )
                    flat_labeled = True
            else:
                failed = np.flatnonzero(~converged)
                if failed.size:
                    j = int(failed[0])
                    ax.scatter(
                        [drift[j]],
                        [im[j]],
                        marker="x",
                        color="#444444",
                        s=24,
                        linewidths=1.1,
                        alpha=0.9,
                        zorder=2,
                        label="non-converged (collapse)" if not nonconv_labeled else None,
                    )
                    nonconv_labeled = True
        elif crit == "drift":
            ax.plot(
                [collapse_drift, collapse_drift],
                [0.0, cap],
                color="#2ca02c",
                linestyle=":",
                linewidth=1.3,
                alpha=0.9,
                zorder=2,
                label=f"drift collapse (θ = {collapse_drift:g})" if not drift_labeled else None,
            )
            ax.plot(
                collapse_drift,
                cap,
                marker="^",
                markersize=7,
                color="#2ca02c",
                linestyle="none",
                zorder=3,
            )
            drift_labeled = True

    if show_median:
        grid, med = _median_curve(curves, caps)
        if grid.size:
            ax.plot(med, grid, color="#d62728", linewidth=2.0, label="median IDA", zorder=3)

    if show_collapse and caps is not None:
        finite = caps[np.isfinite(caps)]
        if finite.size:
            ax.scatter(
                np.full(finite.shape, 0.005),
                finite,
                marker="x",
                color="#1f77b4",
                s=28,
                linewidths=1.2,
                label=f"collapse capacity (n={finite.size})",
                zorder=4,
            )

    median = _scalar(data, "ida/median_collapse_Sa_T1_gamma")
    sigma = _scalar(data, "ida/sigma_ln")
    ax.set_xlabel("peak interstorey-drift ratio")
    ax.set_ylabel(r"$S_a(T_1,5\%)/g$")
    ax.set_xlim(0.0, max_drift)
    ax.set_ylim(bottom=0.0)
    if title is None:
        code_arr = data.get("ida/code")
        code = str(np.asarray(code_arr).reshape(-1)[0]) if code_arr is not None else ""
        title = f"IDA — generic frame {code}"
    extra = ""
    if median is not None and sigma is not None:
        extra = f"\nmedian $(S_{{a,c}}/g)/\\gamma$ = {median:.2f}, $\\sigma_{{\\ln}}$ = {sigma:.2f}"
    ax.set_title(title + extra)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left", fontsize=8)
    fig.tight_layout()
    return fig


def plot_collapse_fragility(
    data: dict,
    *,
    ax: Any = None,
    figsize: tuple[float, float] = (7, 5),
    title: Optional[str] = None,
) -> Optional[Any]:
    """Plot the collapse fragility curve — P(collapse) vs ``Sa(T1, 5 %)/g``.

    The empirical cumulative distribution of the per-record collapse capacities
    ``ida/collapse_Sa_T1_g`` (median-ranked).

    Args:
        data: Flat NPZ dict carrying the ``ida`` block (schema v5).
        ax: Optional axes to draw into.
        figsize: Figure size when *ax* is ``None``.
        title: Optional figure title.

    Returns:
        The matplotlib figure, or ``None`` if matplotlib is unavailable.
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    records, _curves = _ida_curves(data)
    caps = _collapse_capacities(data, records)
    finite = np.sort(caps[np.isfinite(caps)])
    if finite.size == 0:
        return None

    p = (np.arange(finite.size) + 1.0) / (finite.size + 1.0)

    if ax is None:
        fig, ax = plt.subplots(figsize=figsize)
    else:
        fig = ax.figure

    ax.step(finite, p, where="post", color="#d62728", linewidth=2.0)
    median = _scalar(data, "ida/median_collapse_Sa_T1_gamma")
    if median is not None:
        ax.axvline(
            median,
            color="#1f77b4",
            linestyle="--",
            linewidth=1.2,
            label=f"median = {median:.2f}",
        )
    ax.set_xlabel(r"$S_{a,c}(T_1,5\%)/g$")
    ax.set_ylabel("P(collapse)")
    ax.set_xlim(left=0.0)
    ax.set_ylim(0.0, 1.0)
    ax.set_title(title if title is not None else "Collapse fragility")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    return fig


def plot_ida_summary(
    data: dict,
    *,
    figsize: tuple[float, float] = (16, 6),
    title: Optional[str] = None,
) -> Optional[Any]:
    """A two-panel figure — IDA curves (left) and collapse fragility (right).

    Args:
        data: Flat NPZ dict carrying the ``ida`` block (schema v5).
        figsize: Figure size.
        title: Optional figure title.

    Returns:
        The matplotlib figure, or ``None`` if matplotlib is unavailable.
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    fig, (ax_ida, ax_frag) = plt.subplots(1, 2, figsize=figsize)
    if plot_ida_curves(data, ax=ax_ida, title=title) is None:
        return None
    if plot_collapse_fragility(data, ax=ax_frag, title=title) is None:
        return None
    fig.tight_layout()
    return fig
