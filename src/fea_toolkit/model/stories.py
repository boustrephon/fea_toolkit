"""
Storey-level detection from structural models.

Provides a pipeline that identifies likely storey elevations from a parsed
SAP2000 model, using (in priority order):

1.  Explicit STORY tables in the ``.s2k`` file (if available)
2.  Diaphragm constraints (rigid diaphragms)
3.  Horizontal area elements (floor/roof slabs)
4.  Node Z-coordinate clustering (fallback for bare frame models)

The result is a list of :class:`StoryLevel` objects, each with an elevation,
confidence level, and the method used to determine it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


# ========================================================================
# StoryLevel dataclass
# ========================================================================

@dataclass
class StoryLevel:
    """A single identified storey level.

    Attributes
    ----------
    name : str
        Human-readable label (e.g. ``"Storey 1"``, ``"Roof"``).
    elevation : float
        Z-coordinate of the storey in model units.
    method : str
        Identification method: ``"s2k_table"`` | ``"diaphragm"`` |
        ``"area_elements"`` | ``"node_clustering"``.
    confidence : str
        ``"high"`` | ``"medium"`` | ``"low"``.
    bbox : tuple[float, float, float, float] or None
        Estimated horizontal extent ``(x_min, x_max, y_min, y_max)``
        of elements belonging to this storey, or ``None`` if unknown.
    """
    name: str
    elevation: float
    method: str = "unknown"
    confidence: str = "low"
    bbox: Optional[Tuple[float, float, float, float]] = None
    total_area: float = 0.0         # total plan area of horizontal elements at this level

    def __repr__(self) -> str:
        return (
            f"{self.name} @ {self.elevation:.3f} "
            f"[{self.method}, {self.confidence}]"
        )


# ========================================================================
# Tolerance helpers
# ========================================================================

_DEFAULT_Z_TOL = 0.5        # half-metre clustering band
_SLOPE_TOL_DEG = 20.0       # degrees from vertical to consider "horizontal"
                           # (catches typical pitched roofs up to ~20°)


# ========================================================================
# Pipeline: identify_stories
# ========================================================================

def identify_stories(
    md,
    raw_tables: Optional[Dict] = None,
    method: str = "auto",
    z_tolerance: float = _DEFAULT_Z_TOL,
) -> List[StoryLevel]:
    """Identify likely storey levels from a parsed SAP2000 model.

    Parameters
    ----------
    md : SAPModelData
        Parsed model data.
    raw_tables : dict, optional
        The parser's ``_raw_tables`` dict (enables STORY-table lookup).
    method : str
        ``"auto"`` (try all strategies in priority order, default),
        ``"s2k_table"``, ``"diaphragm"``, ``"area_elements"``,
        or ``"node_clustering"``.
    z_tolerance : float
        Tolerance (in model length units) for grouping Z coordinates
        into the same storey.

    Returns
    -------
    list[StoryLevel]
        Identified storeys sorted by elevation (lowest first).
    """
    if method == "auto":
        # Try strategies in priority order
        stories = _try_s2k_table(md, raw_tables)
        if stories:
            return _sort_and_name(stories)

        stories = _try_diaphragms(md, raw_tables)
        if stories:
            return _sort_and_name(stories)

        stories = _try_area_elements(md, z_tolerance)
        if stories:
            return _sort_and_name(stories)

        stories = _try_node_clustering(md, z_tolerance)
        return _sort_and_name(stories)

    # Explicit method
    dispatch = {
        "s2k_table": _try_s2k_table,
        "diaphragm": _try_diaphragms,
        "area_elements": _try_area_elements,
        "node_clustering": _try_node_clustering,
    }
    fn = dispatch.get(method)
    if fn is None:
        raise ValueError(f"Unknown method: {method}")
    # Methods that need raw_tables
    if method in ("s2k_table", "diaphragm"):
        result = fn(md, raw_tables)
    else:
        result = fn(md, z_tolerance)
    return _sort_and_name(result)


# ========================================================================
# Strategy 1 — S2K table
# ========================================================================

def _try_s2k_table(md, raw_tables) -> List[StoryLevel]:
    """Read STORY DATA from the .s2k file if available."""
    if raw_tables is None:
        return []
    # ETABS exports storey information in the "STORY DATA" table
    story_table = raw_tables.get("STORY DATA") or raw_tables.get("STORY") or []
    if not story_table:
        return []

    stories = []
    for rec in story_table:
        name = rec.get("Name") or rec.get("Story") or ""
        elev = _safe_float(rec.get("Z"))
        if elev is None:
            elev = _safe_float(rec.get("Elevation"))
        if elev is None:
            continue
        stories.append(StoryLevel(
            name=name,
            elevation=elev,
            method="s2k_table",
            confidence="high",
        ))
    return stories


# ========================================================================
# Strategy 2 — Diaphragm constraints
# ========================================================================

def _try_diaphragms(md, raw_tables) -> List[StoryLevel]:
    """Detect storeys from diaphragm constraints defined in the .s2k file.

    Uses two tables from the s2k file:
    - ``CONSTRAINT DEFINITIONS - DIAPHRAGM`` — defines diaphragm names
      and their constraint axis (e.g. Z)
    - ``JOINT CONSTRAINT ASSIGNMENTS`` — maps joints to those constraints

    Joints sharing the same diaphragm constraint are grouped; the storey
    elevation is taken from the average Z of the group.
    """
    if raw_tables is None:
        return []

    # 1. Get diaphragm constraint definitions
    diaph_defs = raw_tables.get("CONSTRAINT DEFINITIONS - DIAPHRAGM") or []
    if not diaph_defs:
        return []

    # Collect names of Z-axis diaphragms
    diaph_names: List[str] = []
    for rec in diaph_defs:
        name = rec.get("Name", "")
        axis = (rec.get("Axis") or "")
        if name and axis.upper() == "Z":
            diaph_names.append(name)

    if not diaph_names:
        return []

    # 2. Get joint assignments and group by constraint name
    assignments = raw_tables.get("JOINT CONSTRAINT ASSIGNMENTS") or []
    constraint_groups: Dict[str, List[str]] = {n: [] for n in diaph_names}
    for rec in assignments:
        joint = rec.get("Joint", "")
        cname = rec.get("Constraint", "")
        if joint and cname in constraint_groups:
            constraint_groups[cname].append(joint)

    # 3. Compute elevation for each group from joint Z coordinates
    stories = []
    for cname, joint_ids in constraint_groups.items():
        if len(joint_ids) < 2:
            continue  # skip degenerate groups
        z_vals = []
        for jid in joint_ids:
            node = md.nodes.get(jid)
            if node is not None:
                z_vals.append(node.z)
        if not z_vals:
            continue
        elev = sum(z_vals) / len(z_vals)
        bbox = _bbox_for_nodes(md, joint_ids)
        stories.append(StoryLevel(
            name=cname,
            elevation=elev,
            method="diaphragm",
            confidence="high",
            bbox=bbox,
        ))

    return stories


# ========================================================================
# Strategy 3 — Horizontal area elements (floor/roof slabs)
# ========================================================================

def _try_area_elements(md, z_tolerance: float) -> List[StoryLevel]:
    """Identify storey levels from nearly-horizontal area elements."""
    if not hasattr(md, "area_elements") or not md.area_elements:
        return []

    # Collect (z, area, node_ids) for each horizontal slab element
    slab_data: List[Tuple[float, float, List[str]]] = []
    for aid, ae in md.area_elements.items():
        if ae.inactive:
            continue
        pts = _get_area_vertices(md, ae)
        if len(pts) < 3:
            continue

        normal, area = _polygon_normal_and_area(pts)
        if area < 1e-12:
            continue

        # Check if element is nearly horizontal (normal ≈ vertical)
        angle = _angle_from_vertical(normal)
        if angle > _SLOPE_TOL_DEG:
            continue

        # Centroid Z from node coordinates
        cz = sum(p[3] for p in pts) / len(pts)
        slab_data.append((cz, area, [p[0] for p in pts]))

    if not slab_data:
        return []

    # Cluster by Z with tolerance
    clusters = _cluster_z([(cz, area) for cz, area, _ in slab_data], z_tolerance)

    stories = []
    for cz, area_sum, indices in clusters:
        # Collect all node IDs from slabs in this cluster
        cluster_nodes: List[str] = []
        for idx in indices:
            cluster_nodes.extend(slab_data[idx][2])

        bbox = _bbox_for_nodes(md, cluster_nodes)
        stories.append(StoryLevel(
            name="",
            elevation=cz,
            method="area_elements",
            confidence="medium",
            bbox=bbox,
            total_area=area_sum,
        ))

    return stories


# ========================================================================
# Strategy 4 — Node Z clustering
# ========================================================================

def _try_node_clustering(md, z_tolerance: float) -> List[StoryLevel]:
    """Cluster node Z coordinates to infer storey levels.

    This is a fallback for models with no area elements or explicit
    storey definitions. Results are flagged low-confidence for user review.
    """
    if not hasattr(md, "nodes") or not md.nodes:
        return []

    z_vals = [n.z for n in md.nodes.values()]
    if not z_vals:
        return []

    # 1D clustering of Z values
    z_array = np.array(z_vals)
    clusters = _cluster_1d(z_array, z_tolerance)

    stories = []
    for cz in sorted(clusters):
        # Find node IDs near this Z for bounding box
        node_ids = [
            nid for nid, n in md.nodes.items()
            if abs(n.z - cz) < z_tolerance / 2
        ]
        bbox = _bbox_for_nodes(md, node_ids)
        stories.append(StoryLevel(
            name="",
            elevation=cz,
            method="node_clustering",
            confidence="low",
            bbox=bbox,
        ))

    return stories


# ========================================================================
# Geometry helpers
# ========================================================================

def _get_area_vertices(md, ae):
    """Return list of (node_id, x, y, z) tuples for an area element."""
    pts = []
    for nid in ae.node_ids:
        nd = md.nodes.get(nid)
        if nd is None:
            return []
        pts.append((nid, nd.x, nd.y, nd.z))
    return pts


def _polygon_normal_and_area(pts):
    """Compute unit normal and area of a 3D polygon via Newell's method.

    Parameters
    ----------
    pts : list of (node_id, x, y, z) or (x, y, z)

    Returns
    -------
    normal : np.ndarray   (3,) unit normal
    area : float
    """
    n = len(pts)
    nx = ny = nz = 0.0
    for i in range(n):
        # Extract coordinates regardless of tuple format
        if len(pts[i]) == 4:
            _, x1, y1, z1 = pts[i]
            _, x2, y2, z2 = pts[(i + 1) % n]
        else:
            x1, y1, z1 = pts[i]
            x2, y2, z2 = pts[(i + 1) % n]
        nx += (y1 - y2) * (z1 + z2)
        ny += (z1 - z2) * (x1 + x2)
        nz += (x1 - x2) * (y1 + y2)
    area = 0.5 * math.sqrt(nx * nx + ny * ny + nz * nz)
    if area < 1e-12:
        return np.array([0.0, 0.0, 1.0]), 0.0
    normal = np.array([nx, ny, nz])
    normal /= np.linalg.norm(normal)
    return normal, area


def _angle_from_vertical(normal: np.ndarray) -> float:
    """Angle in degrees between the normal and the vertical (0, 0, 1)."""
    dot = abs(normal[2])  # dot with (0,0,1)
    return math.degrees(math.acos(min(dot, 1.0)))


def _cluster_z(data, z_tolerance: float):
    """Cluster (z, weight) pairs by z-proximity.

    Uses the first value in each cluster as the fixed anchor, so the
    cluster boundary does not drift as points are added (avoids the
    chain-merging problem of a shifting mean).

    Returns list of (weighted_avg_z, total_weight, indices_in_cluster).
    """
    # Sort by Z
    sorted_data = sorted(enumerate(data), key=lambda x: x[1][0])
    clusters = []  # each: (anchor_z, z_sum, weight_sum, indices)
    for idx, (z, w) in sorted_data:
        if not clusters:
            clusters.append((z, z * w, w, [idx]))
        else:
            anchor, prev_z_sum, prev_w, prev_idxs = clusters[-1]
            if abs(z - anchor) <= z_tolerance:
                clusters[-1] = (anchor, prev_z_sum + z * w, prev_w + w,
                                prev_idxs + [idx])
            else:
                clusters.append((z, z * w, w, [idx]))
    return [
        (z_sum / w_sum, w_sum, indices)
        for _, z_sum, w_sum, indices in clusters
    ]


def _cluster_1d(values: np.ndarray, tolerance: float) -> List[float]:
    """Simple 1D greedy clustering with fixed anchor.

    Each new value is compared to the **first** value in the current
    cluster (not the shifting mean), preventing chain-merging.

    Returns list of cluster centroids (mean of each cluster).
    """
    sorted_vals = np.sort(values)
    clusters = []
    for v in sorted_vals:
        if not clusters:
            clusters.append([v])
        else:
            if abs(v - clusters[-1][0]) <= tolerance:
                clusters[-1].append(v)
            else:
                clusters.append([v])
    return [float(np.mean(c)) for c in clusters]


def _sort_and_name(stories: List[StoryLevel]) -> List[StoryLevel]:
    """Sort by elevation and assign default names (Storey 1, Storey 2, …)."""
    stories.sort(key=lambda s: s.elevation)
    n_digits = len(str(len(stories)))
    for i, s in enumerate(stories):
        if not s.name:
            if i == len(stories) - 1 and s.elevation > 0:
                s.name = f"Roof"
            else:
                s.name = f"Storey {i + 1:{n_digits}d}".strip()
    return stories


def _bbox_for_nodes(md, node_ids: List[str]):
    """Compute (x_min, x_max, y_min, y_max) from a list of node IDs."""
    xs, ys = [], []
    for nid in node_ids:
        nd = md.nodes.get(nid)
        if nd is not None:
            xs.append(nd.x)
            ys.append(nd.y)
    if not xs:
        return None
    return (min(xs), max(xs), min(ys), max(ys))


def _round_z(z: float, precision: int = 3) -> float:
    """Round Z to avoid floating-point grouping noise."""
    return round(z, precision)


def _safe_float(v) -> Optional[float]:
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


# ========================================================================
# Summary table
# ========================================================================

def stories_dataframe(stories: List[StoryLevel]) -> "pd.DataFrame":
    """Return a pandas DataFrame summarising identified storeys."""
    import pandas as pd
    rows = []
    for s in stories:
        bbox_str = ""
        if s.bbox:
            bbox_str = (
                f"{s.bbox[0]:.1f}–{s.bbox[1]:.1f} × "
                f"{s.bbox[2]:.1f}–{s.bbox[3]:.1f}"
            )
        rows.append({
            "Storey": s.name,
            "Elevation": f"{s.elevation:.2f}",
            "Method": s.method,
            "Confidence": s.confidence,
            "BBox (X × Y)": bbox_str,
            "Area (m²)": f"{s.total_area:.1f}" if s.total_area else "",
        })
    return pd.DataFrame(rows)


# ========================================================================
# PyVista 3D visualisation
# ========================================================================

def plot_stories(
    md,
    stories: List[StoryLevel],
    off_screen: bool = True,
    window_size: Tuple[int, int] = (1200, 900),
    plane_opacity: float = 0.15,
) -> Optional[Any]:
    """Render a 3D view of the model with semi-transparent storey planes.

    Parameters
    ----------
    md : SAPModelData
        Parsed model data.
    stories : list[StoryLevel]
        Storey levels to visualise.
    off_screen : bool
        If True, suppress the PyVista rendering window.
    window_size : tuple[int, int]
        Output image size.
    plane_opacity : float
        Opacity of the storey plane fills (0 = invisible, 1 = solid).

    Returns
    -------
    matplotlib.figure.Figure or None
        A matplotlib figure for embedding in Quarto reports, or ``None``
        if PyVista is unavailable.
    """
    try:
        import pyvista as pv
        import matplotlib.pyplot as plt
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        from fea_toolkit.plotting.viz import plot_model_3d
    except ImportError:
        return None

    if not stories:
        return None

    # Save and restore OFF_SCREEN to avoid permanently mutating PyVista
    # global state for the caller.
    _prev_off_screen = pv.OFF_SCREEN
    pv.OFF_SCREEN = off_screen
    try:
        # Build a lightweight elastic model for visualisation
        b = OpenSeesBuilder(md, {
            "element_type": "elasticBeamColumn",
            "split_elements": True,
            "verbose": False,
        })
        b.build()
    except Exception as exc:
        pv.OFF_SCREEN = _prev_off_screen
        import logging
        logging.getLogger(__name__).warning(
            "Could not build visualisation model: %s", exc)
        return None

    pl = plot_model_3d(b, notebook=True, window_size=window_size)
    if pl is None:
        pv.OFF_SCREEN = _prev_off_screen
        return None

    # Determine global bounding box for plane extents
    xs = [n.x for n in md.nodes.values()]
    ys = [n.y for n in md.nodes.values()]
    zs = [n.z for n in md.nodes.values()]
    if not xs:
        pv.OFF_SCREEN = _prev_off_screen
        return None

    global_xmin, global_xmax = min(xs), max(xs)
    global_ymin, global_ymax = min(ys), max(ys)
    x_span = global_xmax - global_xmin
    y_span = global_ymax - global_ymin
    z_span = max(zs) - min(zs)

    # Add a semi-transparent plane for each storey
    for i, s in enumerate(stories):
        # Extents from bounding box if available, else global
        if s.bbox:
            xmin, xmax = s.bbox[0], s.bbox[1]
            ymin, ymax = s.bbox[2], s.bbox[3]
        else:
            xmin, xmax = global_xmin, global_xmax
            ymin, ymax = global_ymin, global_ymax

        # Pad by 20 % of the smaller span so extension is uniform in all
        # directions (avoids excessive overhang on a long narrow building)
        pad = min(x_span, y_span) * 0.20

        corners = [
            (xmin - pad, ymin - pad, s.elevation),
            (xmax + pad, ymin - pad, s.elevation),
            (xmax + pad, ymax + pad, s.elevation),
            (xmin - pad, ymax + pad, s.elevation),
        ]
        plane = pv.PolyData(corners, faces=[4, 0, 1, 2, 3])
        pl.add_mesh(plane, color="yellow", opacity=plane_opacity,
                     show_edges=True, edge_color="gold", line_width=1,
                     label=s.name)

    pl.add_legend(bcolor="w", face="circle", size=(0.2, 0.15))

    # Convert PyVista plot to matplotlib figure
    img = pl.screenshot(return_img=True, window_size=window_size)
    pl.close()

    fig, ax = plt.subplots(figsize=(10, 8))
    ax.imshow(img)
    ax.axis("off")
    fig.tight_layout()
    pv.OFF_SCREEN = _prev_off_screen
    return fig
