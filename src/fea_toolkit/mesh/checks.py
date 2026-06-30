"""Optional mesh quality diagnostics using COMPAS (MIT license).

All functions are soft-fail: they return diagnostics / warnings and never
raise hard errors, making them safe to use in optional validation passes
after ``mesh_area_elements()``.

Requires ``compas`` (``pip install fea_toolkit[mesh-quality]``).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np


# ── Types (duck-typed: any object with .node_ids and .thickness works) ──


def _check_compas() -> bool:
    """Return True if ``compas`` is available."""
    try:
        import compas  # noqa: F401
        return True
    except ImportError:
        return False


# ========================================================================
# Mesh quality checks
# ========================================================================


def aspect_ratios(
    area_elements: Dict[str, Any],
    nodes: Dict[str, Any],
) -> Dict[str, float]:
    """Compute aspect ratio (longest / shortest edge) per quad element.

    A ratio > 4 is generally considered poor for quadrilateral shell
    elements.

    Args:
        area_elements: ``{area_id: AreaElement}``
        nodes: ``{node_id: Node}`` with ``.x``, ``.y``, ``.z``.

    Returns:
        ``{area_id: aspect_ratio}`` for each quad element.
    """
    ratios: Dict[str, float] = {}
    for aid, elem in area_elements.items():
        nids = getattr(elem, "node_ids", None)
        if nids is None or len(nids) != 4:
            continue
        pts = []
        for nid in nids:
            nd = nodes.get(nid)
            if nd is None:
                break
            pts.append(np.array([nd.x, nd.y, nd.z]))
        if len(pts) != 4:
            continue
        edges = [
            np.linalg.norm(pts[1] - pts[0]),
            np.linalg.norm(pts[2] - pts[1]),
            np.linalg.norm(pts[3] - pts[2]),
            np.linalg.norm(pts[0] - pts[3]),
        ]
        ratios[aid] = max(edges) / max(min(edges), 1e-12)
    return ratios


def flatness(
    area_elements: Dict[str, Any],
    nodes: Dict[str, Any],
) -> Dict[str, float]:
    """Compute quad flatness as the ratio of diagonal distance to
    average edge length.

    A value of 0 means perfectly planar.  Values > 0.02 may indicate
    warped quads that could cause issues with ``ShellMITC4``.

    Args:
        area_elements: ``{area_id: AreaElement}``
        nodes: ``{node_id: Node}`` with ``.x``, ``.y``, ``.z``.

    Returns:
        ``{area_id: flatness_deviation}``.
    """
    deviations: Dict[str, float] = {}
    for aid, elem in area_elements.items():
        nids = getattr(elem, "node_ids", None)
        if nids is None or len(nids) != 4:
            continue
        pts = []
        for nid in nids:
            nd = nodes.get(nid)
            if nd is None:
                break
            pts.append(np.array([nd.x, nd.y, nd.z]))
        if len(pts) != 4:
            continue

        # Distance between the two diagonals (midpoints)
        mid_ac = (pts[0] + pts[2]) * 0.5
        mid_bd = (pts[1] + pts[3]) * 0.5
        diag_dist = float(np.linalg.norm(mid_ac - mid_bd))

        avg_edge = (
            np.linalg.norm(pts[1] - pts[0])
            + np.linalg.norm(pts[2] - pts[1])
            + np.linalg.norm(pts[3] - pts[2])
            + np.linalg.norm(pts[0] - pts[3])
        ) / 4.0

        deviations[aid] = diag_dist / max(avg_edge, 1e-12)
    return deviations


def skew(
    area_elements: Dict[str, Any],
    nodes: Dict[str, Any],
) -> Dict[str, float]:
    """Compute element skew as deviation from 90° (degrees).

    For each quad, the interior angles at the 4 corners are computed.
    The reported value is the maximum deviation from 90° (i.e.
    ``max(abs(angle - 90))``).  Skew > 30° is generally undesirable
    for quadrilateral shells.

    Args:
        area_elements: ``{area_id: AreaElement}``
        nodes: ``{node_id: Node}`` with ``.x``, ``.y``, ``.z``.

    Returns:
        ``{area_id: max_skew_degrees}``.
    """
    skews: Dict[str, float] = {}
    for aid, elem in area_elements.items():
        nids = getattr(elem, "node_ids", None)
        if nids is None or len(nids) != 4:
            continue
        pts = []
        for nid in nids:
            nd = nodes.get(nid)
            if nd is None:
                break
            pts.append(np.array([nd.x, nd.y, nd.z]))
        if len(pts) != 4:
            continue

        max_dev = 0.0
        for k in range(4):
            a = pts[k]
            b = pts[(k + 1) % 4]
            c = pts[(k - 1) % 4]
            v1 = b - a
            v2 = c - a
            dot = float(np.dot(v1, v2))
            n1 = max(float(np.linalg.norm(v1)), 1e-12)
            n2 = max(float(np.linalg.norm(v2)), 1e-12)
            angle_deg = abs(np.degrees(np.arccos(np.clip(dot / (n1 * n2), -1.0, 1.0))))
            max_dev = max(max_dev, abs(angle_deg - 90.0))
        skews[aid] = max_dev
    return skews


def report(
    area_elements: Dict[str, Any],
    nodes: Dict[str, Any],
    aspect_warn: float = 4.0,
    flatness_warn: float = 0.02,
    skew_warn: float = 30.0,
) -> Dict[str, Any]:
    """Generate a comprehensive mesh quality report.

    Args:
        area_elements: ``{area_id: AreaElement}``
        nodes: ``{node_id: Node}``
        aspect_warn: Aspect ratio threshold for warning (default 4.0).
        flatness_warn: Flatness ratio threshold for warning (default 0.02).
        skew_warn: Skew angle threshold for warning (default 30°).

    Returns:
        Dict with keys:

        * ``n_elements`` — total quad count
        * ``aspect_ratios`` — ``{aid: ratio}``
        * ``skews`` — ``{aid: max_skew_deg}``
        * ``flatness`` — ``{aid: deviation}``
        * ``warnings`` — list of human-readable warning strings
        * ``passed`` — True if no warnings
    """
    ar = aspect_ratios(area_elements, nodes)
    sk = skew(area_elements, nodes)
    fl = flatness(area_elements, nodes)

    warnings: List[str] = []
    for aid in ar:
        if ar[aid] > aspect_warn:
            warnings.append(f"Area {aid}: aspect ratio {ar[aid]:.2f} > {aspect_warn}")
    for aid in sk:
        if sk[aid] > skew_warn:
            warnings.append(f"Area {aid}: skew {sk[aid]:.1f}° > {skew_warn}°")
    for aid in fl:
        if fl[aid] > flatness_warn:
            warnings.append(f"Area {aid}: flatness {fl[aid]:.4f} > {flatness_warn}")

    return {
        "n_elements": len(ar),
        "aspect_ratios": ar,
        "skews": sk,
        "flatness": fl,
        "warnings": warnings,
        "passed": len(warnings) == 0,
    }
