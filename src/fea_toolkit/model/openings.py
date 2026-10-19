"""Opening (void) geometry: net shell area after subtracting openings.

An *opening* is an :class:`~fea_toolkit.model.sap_data.AreaElement` flagged
``opening=True`` — assigned the built-in ``"NONE"`` section (modern
ETABS/SAP2000), or ``OPENING "YES"`` (legacy ETABS 9.7).  Openings are voids,
not shells: they carry no weight, and their region is cut out of any coplanar
parent slab/wall they overlap (CSiXRevit documents wall openings as area
objects that *overlap* the wall area).

The self-weight paths call :func:`net_area` in place of the raw
:func:`~fea_toolkit.model.geometry_core.polygon_area_3d` so the applied and
expected weights agree on what a holed panel weighs.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np

from .geometry_core import area_local_axes, polygon_area_3d

__all__ = [
    "area_vertices",
    "net_area",
    "opening_overlap_area",
    "opening_polygons_2d",
    "point_in_polygon_2d",
]


def _vertices(nodes: dict[str, Any], area: Any) -> list[tuple[float, float, float]]:
    """Ordered 3-D vertices of *area* resolved from *nodes*.

    Returns an empty list when any corner node is missing (a dangling area
    resolves to zero area).
    """
    verts: list[tuple[float, float, float]] = []
    for nid in area.node_ids:
        nd = nodes.get(nid)
        if nd is None:
            return []
        verts.append((nd.x, nd.y, nd.z))
    return verts


def area_vertices(model: Any, area: Any) -> list[tuple[float, float, float]]:
    """Ordered 3-D vertices of *area* (only nodes present in ``model.nodes``).

    Returns an empty list when any corner node is missing (a dangling area
    resolves to zero area).
    """
    return _vertices(model.nodes, area)


def _project_2d(vertices, axes) -> list[tuple[float, float]]:
    """Project 3-D *vertices* onto the face plane as ``(u, v)`` pairs."""
    l1, l2, _ = axes
    pts = np.asarray(vertices, dtype=float)
    return [(float(np.dot(p, l1)), float(np.dot(p, l2))) for p in pts]


def _signed_area_2d(poly) -> float:
    """Shoelace area of a 2-D polygon (positive → counter-clockwise)."""
    s = 0.0
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        s += x0 * y1 - x1 * y0
    return 0.5 * s


def point_in_polygon_2d(point: tuple[float, float], poly: list[tuple[float, float]]) -> bool:
    """Return True when *point* lies inside the 2-D polygon *poly*.

    Uses the ray-casting (even–odd) test, valid for convex and concave
    polygons in any winding order.  Classification of points that fall
    exactly on an edge is not guaranteed (the standard half-open
    convention); callers should pass points not on the boundary — e.g.
    cell centroids — which is how the structured meshers use it.

    Args:
        point: ``(x, y)`` pair.
        poly: Sequence of ``(x, y)`` vertices (closed implicitly).

    Returns:
        True when *point* is inside *poly*.
    """
    x, y = point
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def _clip_sutherland_hodgman(subject, clip):
    """Clip *subject* (any winding) against a convex *clip* polygon (CCW).

    Sutherland–Hodgman half-plane clipping: *subject* is clipped against each
    directed edge of *clip*, keeping points on the inside (left) of every edge.
    *clip* must be convex and counter-clockwise (the caller normalises
    orientation); *subject* may have any winding.
    """
    output = list(subject)
    n = len(clip)
    for i in range(n):
        if not output:
            break
        ax, ay = clip[i]
        bx, by = clip[(i + 1) % n]
        ex, ey = bx - ax, by - ay
        incoming = output
        output = []

        def _inside(pt, ax=ax, ay=ay, ex=ex, ey=ey) -> bool:
            return ex * (pt[1] - ay) - ey * (pt[0] - ax) >= -1e-12

        for j, p in enumerate(incoming):
            q = incoming[(j + 1) % len(incoming)]
            p_in = _inside(p)
            q_in = _inside(q)
            if p_in:
                output.append(p)
            if p_in != q_in:
                px, py = p
                qx, qy = q
                dx, dy = qx - px, qy - py
                denom = ex * dy - ey * dx
                if abs(denom) > 1e-15:
                    f_p = ex * (py - ay) - ey * (px - ax)
                    t = -f_p / denom
                    output.append((px + t * dx, py + t * dy))
    return output


def opening_polygons_2d(
    area_elements: dict[str, Any],
    nodes: dict[str, Any],
    area: Any,
    rel_tol: float = 1e-6,
) -> Optional[
    tuple[
        list[tuple[float, float]],
        list[list[tuple[float, float]]],
        tuple[np.ndarray, np.ndarray, np.ndarray],
        list[tuple[float, float, float]],
    ]
]:
    """Collect coplanar opening polygons projected onto *area*'s face plane.

    Shared by :func:`opening_overlap_area` (weight accounting) and the
    structured meshers in :mod:`fea_toolkit.model.geometry_mesh` (hole
    cutting): both need the same set of coplanar openings expressed in the
    parent's 2-D face basis.

    Args:
        area_elements: ``{area_id: AreaElement}``.
        nodes: ``{node_id: Node}`` with ``.x``, ``.y``, ``.z``.
        area: The parent area whose openings are collected.
        rel_tol: Coplanarity tolerance, a fraction of the parent's
            characteristic size (unit-agnostic).

    Returns:
        ``(clip_2d, openings_2d, axes, verts_3d)``:

        * ``clip_2d`` — the parent polygon projected to its face plane and
          counter-clockwise normalised, as ``[(u, v), ...]``.
        * ``openings_2d`` — the coplanar opening polygons in the same 2-D
          basis (winding preserved), ``[(u, v), ...]`` each.
        * ``axes`` — the ``(l1, l2, normal)`` face basis returned by
          :func:`~fea_toolkit.model.geometry_core.area_local_axes`.
        * ``verts_3d`` — the parent's ordered 3-D vertices.

        Returns ``None`` when *area* is degenerate or dangling (fewer than
        three resolvable corner nodes, or no face normal).
    """
    verts = _vertices(nodes, area)
    if len(verts) < 3:
        return None
    axes = area_local_axes(verts)
    if axes is None:
        return None
    clip = _project_2d(verts, axes)
    if _signed_area_2d(clip) < 0:
        clip = list(reversed(clip))

    pts = np.asarray(verts, dtype=float)
    scale = float(np.linalg.norm(pts - pts[0], axis=1).max()) if len(pts) > 1 else 0.0
    tol = rel_tol * max(1.0, scale)

    openings: list[list[tuple[float, float]]] = []
    for oarea in area_elements.values():
        if not getattr(oarea, "opening", False) or oarea is area:
            continue
        overts = _vertices(nodes, oarea)
        if len(overts) < 3:
            continue
        oaxes = area_local_axes(overts)
        if oaxes is None:
            continue
        # Coplanar when the normals are parallel and the opening lies in the
        # parent's plane (its first vertex is within ``tol`` of that plane).
        if abs(float(np.dot(axes[2], oaxes[2]))) < 1.0 - 1e-9:
            continue
        dist = abs(float(np.dot(np.asarray(overts[0]) - pts[0], axes[2])))
        if dist > tol:
            continue
        openings.append(_project_2d(overts, axes))
    return clip, openings, axes, verts


def opening_overlap_area(model: Any, area: Any, rel_tol: float = 1e-6) -> float:
    """Total area of *area* covered by coplanar opening (void) areas.

    Args:
        model: A ``SAPModelData`` or ``MeshModel`` (exposes ``area_elements``
            and ``nodes``).
        area: The parent shell area whose opening overlap is to be measured.
        rel_tol: Relative coplanarity tolerance, as a fraction of the parent's
            characteristic size (unit-agnostic).

    Returns:
        The summed area (in the model's length unit squared) of the portions of
        opening polygons that lie inside *area*.
    """
    info = opening_polygons_2d(model.area_elements, model.nodes, area, rel_tol)
    if info is None:
        return 0.0
    clip, openings, _axes, _verts = info
    total = 0.0
    for subject in openings:
        clipped = _clip_sutherland_hodgman(subject, clip)
        if len(clipped) >= 3:
            total += abs(_signed_area_2d(clipped))
    return total


def net_area(model: Any, area: Any) -> float:
    """Net shell area of *area* after subtracting overlapping openings.

    Returns ``polygon_area_3d(vertices) − opening_overlap_area(model, area)``,
    floored at zero.
    """
    raw = polygon_area_3d(area_vertices(model, area))
    if raw <= 0.0:
        return 0.0
    return max(0.0, raw - opening_overlap_area(model, area))
