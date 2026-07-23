"""
Storey-level displacement, drift, and shear calculations.
=============================================================================

Methodology
-----------
Each storey's nodal displacements are decomposed into a **rigid-body**
motion consisting of two translations (Ux, Uy) and one rotation (Rz)
about the storey **centre of mass (CM)**.

Rigid-body fit
~~~~~~~~~~~~~~
For a storey with *n* nodes at positions (xᵢ, yᵢ) having displacements
(uxᵢ, uyᵢ), and CM at (x̄, ȳ), the rigid-body assumption gives::

    uxᵢ = Ux − Rz · (yᵢ − ȳ)
    uyᵢ = Uy + Rz · (xᵢ − x̄)

These 2n equations are solved via least squares for the 3 unknowns
(Ux, Uy, Rz).  The result is the **best-fit** rigid-body motion —
the storey-level average translation and twist.

Outlier rejection (two-pass)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Not all nodes on a storey move with the floor (e.g. cantilever signs,
parapets, isolated equipment supports).  A two-pass approach handles
this:

1. **Pass 1**: fit rigid body to all nodes.
2. **Residual**: compute the resultant error at each node.
3. **Rejection**: any node whose residual exceeds
   ``outlier_threshold × median(residual)`` is flagged.
4. **Pass 2**: re-fit using only the inlier nodes.

Peak displacement
~~~~~~~~~~~~~~~~~
The peak displacement at a storey is the maximum resultant
``sqrt(uxᵢ² + uyᵢ²)`` evaluated at **every** node on that storey
using the fitted (Ux, Uy, Rz).  This naturally captures torsional
amplification — a positive Rz increases displacement on one side
of the CM and reduces it on the other.  The peak is at whichever
corner is worst.

Storey drifts
~~~~~~~~~~~~~
Between consecutive storeys *j* and *j-1*::

    Drift_X  = (Uxⱼ − Uxⱼ₋₁) / (Zⱼ − Zⱼ₋₁)
    Drift_Y  = (Uyⱼ − Uyⱼ₋₁) / (Zⱼ − Zⱼ₋₁)
    Drift_Rz = (Rzⱼ − Rzⱼ₋₁) / (Zⱼ − Zⱼ₋₁)

Peak drift is a conservative bound computed using the maximum
distance from the CM to any node on the upper storey.

Modal drifts (CQC combination)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
For each mode *m*, nodal eigenvector displacements are extracted and
a rigid-body fit is performed per storey.  Per-mode inter-storey
drifts are then combined using the **Complete Quadratic Combination**
(CQC) formula::

    Drift_total = sqrt( Σᵢ Σⱼ ρᵢⱼ · Driftᵢ · Driftⱼ )

where ρᵢⱼ is the correlation coefficient between modes *i* and *j*
based on their frequency ratio and damping ratio (Der Kiureghian,
1981).

Storey shears
~~~~~~~~~~~~~
Storey shear forces are computed by summing element-end forces at all
nodes belonging to each storey level.  This gives the net force
transmitted through that storey for any load case.

References
----------
- Der Kiureghian, A. (1981).  "A response spectrum method for
  random vibration analysis of MDOF systems."  *Earthquake
  Engineering & Structural Dynamics*, 9(5), 419–435.
- Wilson, E. L., Der Kiureghian, A., & Bayo, E. P. (1981).  "A
  replacement for the SRSS method in seismic analysis."  *Earthquake
  Engineering & Structural Dynamics*, 9(2), 187–194.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


# ========================================================================
# Dataclasses
# ========================================================================

@dataclass
class StoreyRigidBody:
    """Rigid-body fit result for one storey under one load case.

    Attributes
    ----------
    storey : str
        Storey name (e.g. ``"Storey 1"``).
    elevation : float
        Storey Z coordinate.
    x_cm, y_cm : float
        Centre-of-mass coordinates of the storey nodes.
    Ux, Uy : float
        Average translational displacement at the CM.
    Rz : float
        Twist (rotation about Z, radians, positive anticlockwise).
    rms_residual : float
        RMS of the fit residual — small = good rigid-body fit.
    n_nodes : int
        Number of nodes used in the fit (after outlier rejection).
    n_outliers : int
        Number of nodes rejected as outliers.
    peak_disp : float
        Maximum resultant displacement at any node
        (Ux, Uy + Rz lever-arm evaluated at all node positions).
    """
    storey: str
    elevation: float
    x_cm: float
    y_cm: float
    Ux: float
    Uy: float
    Rz: float
    rms_residual: float
    n_nodes: int
    n_outliers: int
    peak_disp: float


# ========================================================================
# Node-to-storey assignment
# ========================================================================

def assign_nodes_to_storeys(md, stories, z_tolerance: float = 0.5):
    """Group node IDs by storey based on Z proximity or stored membership.

    If a :class:`StoryLevel` has ``node_ids`` populated (from area-element
    or node-clustering detection), those IDs are used directly for that
    storey.  Otherwise (e.g. ``s2k_table``-derived stories), distance-based
    matching is used as a fallback.

    Each node is assigned to the **nearest** storey whose elevation is
    within ``z_tolerance/2``.  A node is never assigned to more than
    one storey.

    Parameters
    ----------
    md : SAPModelData
    stories : list[StoryLevel]
    z_tolerance : float
        Half-band for Z matching (nodes within this distance of a
        storey elevation are assigned to that storey).

    Returns
    -------
    dict[str, list[str]]
        ``{storey_name: [node_id, ...]}``
    """
    if not hasattr(md, "nodes"):
        return {}
    assignments: Dict[str, List[str]] = {s.name: [] for s in stories}
    half_band = z_tolerance / 2
    # Precompute sets for O(1) membership checks
    node_id_sets: Dict[str, set] = {}
    for s in stories:
        if s.node_ids is not None:
            node_id_sets[s.name] = set(s.node_ids)
    for nid, nd in md.nodes.items():
        best = None
        best_dist = float("inf")
        for s in stories:
            # Use stored membership when available
            nset = node_id_sets.get(s.name)
            if nset is not None:
                if nid in nset:
                    assignments[s.name].append(nid)
                    best = None  # skip distance fallback
                    break
            else:
                dist = abs(nd.z - s.elevation)
                if dist <= half_band and dist < best_dist:
                    best = s.name
                    best_dist = dist
        if best is not None:
            assignments[best].append(nid)
    return assignments


# ========================================================================
# Centre of mass per storey
# ========================================================================

def storey_centroids(md, stories, node_masses: Optional[Dict[str, float]] = None,
                    z_tolerance: float = 0.5):
    """Compute centre of mass for each storey.

    Parameters
    ----------
    md : SAPModelData
    stories : list[StoryLevel]
    node_masses : dict, optional
        ``{node_id: mass}``.  If ``None``, geometric centroid
        (equal weight per node) is used.
    z_tolerance : float
        Passed to :func:`assign_nodes_to_storeys` to ensure the same
        node-to-storey assignment as used elsewhere.

    Returns
    -------
    dict[str, tuple[float, float]]
        ``{storey_name: (x_cm, y_cm)}``
    """
    assign = assign_nodes_to_storeys(md, stories, z_tolerance)
    centroids: Dict[str, Tuple[float, float]] = {}
    for s in stories:
        nids = assign.get(s.name, [])
        if not nids:
            centroids[s.name] = (0.0, 0.0)
            continue
        xs, ys, ws = [], [], []
        for nid in nids:
            nd = md.nodes.get(nid)
            if nd is None:
                continue
            w = node_masses.get(nid, 1.0) if node_masses else 1.0
            xs.append(nd.x * w)
            ys.append(nd.y * w)
            ws.append(w)
        total_w = sum(ws)
        centroids[s.name] = (
            sum(xs) / total_w if total_w > 0 else 0.0,
            sum(ys) / total_w if total_w > 0 else 0.0,
        )
    return centroids


# ========================================================================
# Rigid-body fit
# ========================================================================

def rigid_body_fit(
    ux: np.ndarray,
    uy: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    x_cm: float,
    y_cm: float,
    outlier_threshold: float = 3.0,
) -> Tuple[float, float, float, float, int, int, np.ndarray]:
    """Least-squares rigid-body fit about centre of mass.

    Solves::

        uxᵢ = Ux − Rz · (yᵢ − y_cm)
        uyᵢ = Uy + Rz · (xᵢ − x_cm)

    with two-pass outlier rejection.

    Parameters
    ----------
    ux, uy : np.ndarray
        Nodal displacements (1-D, same length).
    x, y : np.ndarray
        Nodal coordinates.
    x_cm, y_cm : float
        Centre-of-mass coordinates.
    outlier_threshold : float
        Residual threshold in multiples of median absolute residual.
        Nodes exceeding this are rejected.

    Returns
    -------
    Ux, Uy, Rz, rms_residual, n_used, n_rejected, mask
    """
    n = len(ux)
    x_tilde = x - x_cm
    y_tilde = y - y_cm

    # Pass 1: fit all nodes using 2n equations
    # [1,  0, -ỹᵢ]   [Ux]   [uxᵢ]
    # [0,  1,  x̃ᵢ] · [Uy] = [uyᵢ]
    #                  [Rz]
    A = np.zeros((2 * n, 3))
    b = np.zeros(2 * n)
    for i in range(n):
        A[2 * i, 0] = 1.0
        A[2 * i, 2] = -y_tilde[i]
        b[2 * i] = ux[i]
        A[2 * i + 1, 1] = 1.0
        A[2 * i + 1, 2] = x_tilde[i]
        b[2 * i + 1] = uy[i]

    theta, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
    Ux1, Uy1, Rz1 = theta[0], theta[1], theta[2]

    # Compute residuals
    ux_pred = Ux1 - Rz1 * y_tilde
    uy_pred = Uy1 + Rz1 * x_tilde
    res = np.sqrt((ux - ux_pred)**2 + (uy - uy_pred)**2)

    # Outlier rejection
    med_res = np.median(res)
    if med_res < 1e-12:
        mask = np.ones(n, dtype=bool)
    else:
        mask = res < outlier_threshold * med_res

    n_outliers = n - int(mask.sum())

    n_inliers = int(mask.sum())

    # Pass 2: fit inliers only
    if n_outliers > 0 and n_inliers >= 2:
        n2 = n_inliers
        A2 = np.zeros((2 * n2, 3))
        b2 = np.zeros(2 * n2)
        idx = np.where(mask)[0]
        for k, i in enumerate(idx):
            A2[2 * k, 0] = 1.0
            A2[2 * k, 2] = -y_tilde[i]
            b2[2 * k] = ux[i]
            A2[2 * k + 1, 1] = 1.0
            A2[2 * k + 1, 2] = x_tilde[i]
            b2[2 * k + 1] = uy[i]
        theta2, _, _, _ = np.linalg.lstsq(A2, b2, rcond=None)
        Ux, Uy, Rz = theta2[0], theta2[1], theta2[2]
        ux_pred2 = Ux - Rz * y_tilde[mask]
        uy_pred2 = Uy + Rz * x_tilde[mask]
        res2 = np.sqrt((ux[mask] - ux_pred2)**2 + (uy[mask] - uy_pred2)**2)
        rms = float(np.sqrt(np.mean(res2**2)))
    else:
        # Fall back to Pass-1 fit using ALL nodes; reset counts accordingly
        Ux, Uy, Rz = Ux1, Uy1, Rz1
        rms = float(np.sqrt(np.mean(res**2)))
        n_inliers = n
        n_outliers = 0

    return Ux, Uy, Rz, rms, n_inliers, n_outliers, mask


# ========================================================================
# Peak displacement at worst-node location
# ========================================================================

def peak_displacement(
    x: np.ndarray,
    y: np.ndarray,
    x_cm: float,
    y_cm: float,
    Ux: float,
    Uy: float,
    Rz: float,
) -> float:
    """Maximum resultant displacement at any node position.

    Evaluates the rigid-body field at each node and returns the
    maximum ``sqrt(uxᵢ² + uyᵢ²)``.
    """
    x_tilde = x - x_cm
    y_tilde = y - y_cm
    ux_i = Ux - Rz * y_tilde
    uy_i = Uy + Rz * x_tilde
    return float(np.max(np.sqrt(ux_i**2 + uy_i**2)))


# ========================================================================
# Full storey displacement pipeline
# ========================================================================

def storey_displacements(
    md,
    stories,
    node_ux: Dict[str, float],
    node_uy: Dict[str, float],
    node_masses: Optional[Dict[str, float]] = None,
    outlier_threshold: float = 3.0,
    z_tolerance: float = 0.5,
) -> pd.DataFrame:
    """Compute rigid-body storey displacements for a load case.

    Parameters
    ----------
    md : SAPModelData
    stories : list[StoryLevel]
    node_ux, node_uy : dict
        ``{node_id: displacement}`` for each DOF.
    node_masses : dict, optional
        For CM calculation.  ``None`` → geometric centroid.
    outlier_threshold : float
        See :func:`rigid_body_fit`.
    z_tolerance : float
        See :func:`assign_nodes_to_storeys`.

    Returns
    -------
    pd.DataFrame
        Columns: Storey, Elevation, X_cm, Y_cm,
        Ux, Uy, Rz, Peak_disp, RMS_residual, R_max, N_nodes, N_outliers.
    """
    assign = assign_nodes_to_storeys(md, stories, z_tolerance)
    cm = storey_centroids(md, stories, node_masses, z_tolerance=z_tolerance)

    rows = []
    for s in stories:
        nids = assign.get(s.name, [])
        if len(nids) < 2:
            rows.append(_empty_row(s))
            continue

        x_a, y_a, ux_a, uy_a = [], [], [], []
        for nid in nids:
            nd = md.nodes.get(nid)
            if nd is None:
                continue
            ux_val = node_ux.get(nid, 0.0)
            uy_val = node_uy.get(nid, 0.0)
            if ux_val is None or uy_val is None:
                continue
            x_a.append(nd.x)
            y_a.append(nd.y)
            ux_a.append(ux_val)
            uy_a.append(uy_val)

        if len(x_a) < 2:
            rows.append(_empty_row(s))
            continue

        x_arr = np.array(x_a)
        y_arr = np.array(y_a)
        ux_arr = np.array(ux_a)
        uy_arr = np.array(uy_a)
        xc, yc = cm.get(s.name, (0.0, 0.0))

        Ux, Uy, Rz, rms, n_used, n_out, mask = rigid_body_fit(
            ux_arr, uy_arr, x_arr, y_arr, xc, yc, outlier_threshold,
        )
        peak = peak_displacement(x_arr, y_arr, xc, yc, Ux, Uy, Rz)
        # R_max = farthest node-to-CM distance (actual storey geometry)
        r_max = float(np.max(np.sqrt((x_arr - xc)**2 + (y_arr - yc)**2)))

        rows.append({
            "Storey": s.name,
            "Elevation": s.elevation,
            "X_cm": round(xc, 3),
            "Y_cm": round(yc, 3),
            "Ux": round(Ux, 6),
            "Uy": round(Uy, 6),
            "Rz": round(Rz, 8),
            "Peak_disp": round(peak, 6),
            "RMS_residual": round(rms, 6),
            "R_max": round(r_max, 3),
            "N_nodes": n_used,
            "N_outliers": n_out,
        })

    return pd.DataFrame(rows)


def _empty_row(s):
    return {
        "Storey": s.name,
        "Elevation": s.elevation,
        "X_cm": 0.0, "Y_cm": 0.0,
        "Ux": 0.0, "Uy": 0.0, "Rz": 0.0,
        "Peak_disp": 0.0, "RMS_residual": 0.0, "R_max": 0.0,
        "N_nodes": 0, "N_outliers": 0,
    }


# ========================================================================
# Storey drifts
# ========================================================================

def storey_drifts(
    df_disp: pd.DataFrame,
    stories,
) -> pd.DataFrame:
    """Compute inter-storey drift ratios from storey displacements.

    Parameters
    ----------
    df_disp : pd.DataFrame
        Output of :func:`storey_displacements`, sorted by elevation.
    stories : list[StoryLevel]
        Used to get elevation ordering (must match df_disp).

    Returns
    -------
    pd.DataFrame
        Columns: Storey, Elevation (m), Drift_X, Drift_Y, Drift_Rz,
        Drift_peak, h (m).
    """
    if df_disp.empty or len(df_disp) < 2:
        return pd.DataFrame()

    rows = []
    for i in range(1, len(df_disp)):
        prev = df_disp.iloc[i - 1]
        curr = df_disp.iloc[i]
        h = curr["Elevation"] - prev["Elevation"]
        if h < 1e-12:
            continue
        drift_x = (curr["Ux"] - prev["Ux"]) / h
        drift_y = (curr["Uy"] - prev["Uy"]) / h
        drift_rz = (curr["Rz"] - prev["Rz"]) / h
        # Peak drift: triangle-inequality bound using the upper storey's
        # actual farthest node-to-CM distance (R_max).
        r_max = curr["R_max"]
        peak_drift = math.sqrt(drift_x**2 + drift_y**2) + abs(drift_rz) * r_max
        rows.append({
            "Storey": curr["Storey"],
            "Elevation (m)": curr["Elevation"],
            "Drift_X": round(drift_x, 6),
            "Drift_Y": round(drift_y, 6),
            "Drift_Rz": round(drift_rz, 8),
            "Drift_peak": round(peak_drift, 6),
            "h (m)": round(h, 3),
        })

    return pd.DataFrame(rows)


# ========================================================================
# CQC combination coefficient
# ========================================================================

def _cqc_coeff(f_i: float, f_j: float, zeta: float = 0.05) -> float:
    """CQC cross-modal correlation coefficient."""
    r = f_i / f_j if f_j > 0 else 0
    num = 8 * zeta**2 * (1 + r) * r**1.5
    den = (1 - r**2)**2 + 4 * zeta**2 * r * (1 + r)**2
    return num / den if den > 0 else 0.0


# ========================================================================
# Modal storey drifts (CQC-combined)
# ========================================================================

def modal_storey_drifts(
    md,
    stories,
    modal: Dict,
    n_modes: int = 12,
    damping: float = 0.05,
    z_tolerance: float = 0.5,
    spectrum_periods: Optional[List[float]] = None,
    spectrum_accels: Optional[List[float]] = None,
    node_masses: Optional[Dict[str, float]] = None,
) -> pd.DataFrame:
    """Compute CQC-combined storey drifts from modal analysis.

    For each mode, extracts nodal eigenvector displacements, scales
    them by the spectral displacement (Sd = Sa / ω²) when spectrum
    data is provided, fits a rigid-body per storey, computes per-mode
    storey drifts, then combines with CQC.

    Parameters
    ----------
    md : SAPModelData
    stories : list[StoryLevel]
    modal : dict
        Modal analysis result (``run_modal_analysis`` output).
    n_modes : int
    damping : float
    z_tolerance : float
    spectrum_periods : list[float], optional
        Period axis of the design spectrum (s).  If provided along
        with *spectrum_accels*, each mode eigenvector is scaled by
        the spectral displacement Sd = Sa / ω² before drift
        computation.
    spectrum_accels : list[float], optional
        Spectral acceleration values (m/s²) corresponding to
        *spectrum_periods*.
    node_masses : dict, optional
        ``{node_id: mass}``.  Passed to :func:`storey_displacements`
        so the rigid-body fit uses the mass centroid.  ``None`` →
        geometric centroid (equal weight per node).

    Returns
    -------
    pd.DataFrame
        CQC-combined drift per storey.
    """
    shapes = modal.get("shapes", {})
    periods = modal.get("periods", [])
    if not shapes or not periods:
        return pd.DataFrame()

    n_avail = min(n_modes, len(periods), len(shapes))
    freqs = [1.0 / max(p, 1e-12) for p in periods[:n_avail]]

    # Per-mode spectral displacement scaling factors
    scale_factors = [1.0] * n_avail
    if spectrum_periods is not None and spectrum_accels is not None:
        import numpy as np
        spec_p = np.array(spectrum_periods)
        spec_a = np.array(spectrum_accels)
        T_min = float(spec_p.min())
        T_max = float(spec_p.max())
        for m in range(n_avail):
            T_m = periods[m]
            if T_m < T_min or T_m > T_max:
                import warnings
                warnings.warn(
                    f"Mode {m+1} period T={T_m:.4f}s outside spectrum range "
                    f"[{T_min:.4f}, {T_max:.4f}] — clamping to nearest boundary"
                )
            omega_m = 2.0 * math.pi / max(T_m, 1e-12)
            # Interpolate Sa at T_m
            Sa_m = float(np.interp(T_m, spec_p, spec_a))
            Sd_m = Sa_m / (omega_m * omega_m)
            scale_factors[m] = Sd_m

    # Per-mode storey displacement DataFrames
    mode_dfs: List[pd.DataFrame] = []
    for m in range(n_avail):
        node_shapes = shapes.get(m, {})  # {tag: (dx, dy, dz)}
        scale = scale_factors[m]
        node_ux: Dict[str, float] = {}
        node_uy: Dict[str, float] = {}
        # Iterate md.nodes (string IDs) and look up each by tag
        for nid, nd in md.nodes.items():
            vec = node_shapes.get(nd.node_tag)
            if vec is not None:
                dx, dy, dz = vec
                node_ux[nid] = dx * scale
                node_uy[nid] = dy * scale
        df_m = storey_displacements(
            md, stories, node_ux, node_uy,
            z_tolerance=z_tolerance,
            node_masses=node_masses,
        )
        mode_dfs.append(df_m)

    # Compute per-mode drifts
    n_storeys = len(stories)
    n_gaps = max(n_storeys - 1, 0)
    drift_modes = np.zeros((n_gaps, n_avail))  # one per inter-storey gap
    for m in range(n_avail):
        df_drift = storey_drifts(mode_dfs[m], stories)
        for i in range(min(n_gaps, len(df_drift))):
            drift_modes[i, m] = df_drift.iloc[i].get("Drift_peak", 0.0)

    # CQC combination
    rho = np.zeros((n_avail, n_avail))
    for i in range(n_avail):
        for j in range(n_avail):
            rho[i, j] = _cqc_coeff(freqs[i], freqs[j], damping)

    cqc_drift = np.sqrt(np.abs(np.einsum("sm, mn, sn -> s", drift_modes, rho, drift_modes)))

    rows = []
    for i, s in enumerate(stories):
        if i == 0:
            continue  # Base / first storey — no drift yet
        # gap index = i - 1
        rows.append({
            "Storey": s.name,
            "Elevation (m)": s.elevation,
            "Drift_CQC": round(float(cqc_drift[i - 1]), 6),
        })

    return pd.DataFrame(rows)


# ========================================================================
# Storey shears (summed element forces)
# ========================================================================

def storey_shears(
    md,
    stories,
    element_forces: Dict,
    z_tolerance: float = 0.5,
) -> pd.DataFrame:
    """Sum element-end forces at each storey level.

    Parameters
    ----------
    md : SAPModelData
    stories : list[StoryLevel]
    element_forces : dict
        ``{elem_id: {"F": [Fx, Fy, Fz, Mx, My, Mz], "node_i": ..., "node_j": ...}}``
    z_tolerance : float

    Returns
    -------
    pd.DataFrame
        Columns: Storey, Elevation, Fx, Fy, Fz, Mx, My, Mz.
    """
    assign = assign_nodes_to_storeys(md, stories, z_tolerance)
    # Build reverse map: node_id -> storey name
    node_to_storey: Dict[str, str] = {}
    for sname, nids in assign.items():
        for nid in nids:
            node_to_storey[nid] = sname

    # Initialise accumulators
    storey_forces: Dict[str, Dict[str, float]] = {
        s.name: {"Fx": 0.0, "Fy": 0.0, "Fz": 0.0,
                 "Mx": 0.0, "My": 0.0, "Mz": 0.0}
        for s in stories
    }
    dropped_i: int = 0
    dropped_j: int = 0

    for eid, edata in element_forces.items():
        f_i = edata.get("F_i", edata.get("F", [0.0] * 6))
        f_j = edata.get("F_j", f_i)
        # I-end forces at node_i
        nid_i = edata.get("node_i")
        if nid_i is not None:
            sname = node_to_storey.get(nid_i)
            if sname is not None:
                acc = storey_forces[sname]
                acc["Fx"] += f_i[0]
                acc["Fy"] += f_i[1]
                acc["Fz"] += f_i[2]
                acc["Mx"] += f_i[3]
                acc["My"] += f_i[4]
                acc["Mz"] += f_i[5]
            else:
                dropped_i += 1
        # J-end forces at node_j
        nid_j = edata.get("node_j")
        if nid_j is not None:
            sname = node_to_storey.get(nid_j)
            if sname is not None:
                acc = storey_forces[sname]
                acc["Fx"] += f_j[0]
                acc["Fy"] += f_j[1]
                acc["Fz"] += f_j[2]
                acc["Mx"] += f_j[3]
                acc["My"] += f_j[4]
                acc["Mz"] += f_j[5]
            else:
                dropped_j += 1

    rows = []
    total_dropped = dropped_i + dropped_j
    if total_dropped:
        warnings.warn(
            f"{total_dropped} element-end contribution(s) dropped — "
            f"node has no storey assignment ({dropped_i} I-end, {dropped_j} J-end)"
        )
    for s in stories:
        f = storey_forces[s.name]
        rows.append({
            "Storey": s.name,
            "Elevation": s.elevation,
            "Fx": round(f["Fx"], 1),
            "Fy": round(f["Fy"], 1),
            "Fz": round(f["Fz"], 1),
            "Mx": round(f["Mx"], 1),
            "My": round(f["My"], 1),
            "Mz": round(f["Mz"], 1),
        })

    return pd.DataFrame(rows)


# ========================================================================
# Storey response helpers — extracted from pumphouse reports
# ========================================================================

def resultant_shear(br: dict) -> float:
    """Resultant horizontal shear from a reactions dict."""
    return math.sqrt((br.get("Fx", 0) or 0)**2 + (br.get("Fy", 0) or 0)**2)


def resultant_moment(br: dict, shear_fx: float = 0.0,
                    shear_fy: float = 0.0,
                    cz: float = 0.0, min_z: float = 0.0) -> float:
    """Resultant horizontal moment about the BASE (z=min_z).

    ``df_linear`` reports moments about the BB centroid (cz).
    Convert to base reference: M_base = M_centroid + V × (cz - z_base)
    """
    mx = br.get("Mx", 0) or 0
    my = br.get("My", 0) or 0
    mx_base = mx + shear_fy * (cz - min_z)
    my_base = my + shear_fx * (cz - min_z)
    return math.sqrt(mx_base**2 + my_base**2)


def build_storey_table(elev_col: str, data_dict: dict,
                       min_z: float = 0.0,
                       base_rxns: dict = None,
                       total_height: float = 1.0,
                       base_val: str = "",
                       _resultant_shear_fn=None,
                       _resultant_moment_fn=None) -> pd.DataFrame:
    """Build a storey-level table with an appended Base row.

    Merges per-case DataFrames from *data_dict* on ``["Storey", elev_col]``,
    then inserts a ``"Base"`` row at *min_z* using the base reactions.

    Parameters
    ----------
    elev_col : str
        Column name for elevation.
    data_dict : dict
        ``{case_name: pd.DataFrame}``.
    min_z : float
        Base elevation for the inserted row.
    base_rxns : dict or None
        ``{case_name: {"Fx": ..., "Fy": ..., "Mx": ..., "My": ...}}``.
    total_height : float
        Total building height, used for moment fallback.
    base_val : str
        ``"shear"`` or ``"moment"``.  Empty for displacement/drift.
    _resultant_shear_fn, _resultant_moment_fn : callable
        Function references (injected so call sites can pass their own).
    """
    import pandas as pd
    if base_rxns is None:
        base_rxns = {}
    if _resultant_shear_fn is None:
        _resultant_shear_fn = resultant_shear
    if _resultant_moment_fn is None:
        _resultant_moment_fn = resultant_moment

    if not data_dict:
        return pd.DataFrame()
    items = list(data_dict.items())
    df = items[0][1].copy()
    for k, df_k in items[1:]:
        df = df.merge(df_k, on=["Storey", elev_col], how="outer")
    base_row: dict = {"Storey": "Base", elev_col: min_z}
    for c in df.columns:
        if c not in ("Storey", elev_col):
            br = base_rxns.get(c, {})
            if base_val == "shear":
                base_row[c] = round(_resultant_shear_fn(br), 1)
            elif base_val == "moment":
                fx_b = br.get("Fx", 0) or 0
                fy_b = br.get("Fy", 0) or 0
                brm = _resultant_moment_fn(br, shear_fx=fx_b, shear_fy=fy_b)
                if brm < 1e-3:
                    brm = _resultant_shear_fn(br) * 0.7 * total_height
                base_row[c] = round(brm, 1)
            else:
                base_row[c] = 0.0
    df = pd.concat([pd.DataFrame([base_row]), df], ignore_index=True)
    return df.sort_values(elev_col).reset_index(drop=True)
