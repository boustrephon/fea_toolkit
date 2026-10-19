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
    "collect_opening_areas",
    "conform_cell_to_openings",
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


def _all_nodes(model: Any) -> dict[str, Any]:
    """Merge ``model.nodes`` with ``model.orphan_nodes``.

    Opening (void) corner nodes may live in ``orphan_nodes`` after
    preprocessing (excluded from the OpenSees domain), so resolving an
    opening against its parent must look in both collections.
    """
    nodes = dict(model.nodes)
    orphan = getattr(model, "orphan_nodes", None)
    if orphan:
        nodes.update(orphan)
    return nodes


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


def _point_in_triangle_2d(p, a, b, c, tol: float = 1e-12) -> bool:
    """Whether *p* is inside or on the CCW triangle ``(a, b, c)``."""

    def _cross(o, p1, p2):
        return (p1[0] - o[0]) * (p2[1] - o[1]) - (p1[1] - o[1]) * (p2[0] - o[0])

    d1 = _cross(a, b, p)
    d2 = _cross(b, c, p)
    d3 = _cross(c, a, p)
    has_neg = d1 < -tol or d2 < -tol or d3 < -tol
    has_pos = d1 > tol or d2 > tol or d3 > tol
    return not (has_neg and has_pos)


def _triangulate(poly):
    """Triangulate a simple 2-D polygon (any winding) via ear clipping.

    Returns a list of counter-clockwise triangles whose union is exactly the
    polygon.  Handles concave polygons, which Sutherland–Hodgman clipping
    cannot (it needs a convex clip).
    """
    pts = [tuple(float(v) for v in p) for p in poly]
    n = len(pts)
    if n < 3:
        return []
    if _signed_area_2d(pts) < 0:
        pts.reverse()
    idx = list(range(n))
    triangles = []
    guard = 0
    while len(idx) > 3 and guard < n * n:
        guard += 1
        ear_k = None
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = pts[i0], pts[i1], pts[i2]
            cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
            if cross <= 0:
                continue  # reflex / collinear vertex — not an ear
            ear_vertices = {i0, i1, i2}
            if any(
                idx[j] not in ear_vertices and _point_in_triangle_2d(pts[idx[j]], a, b, c)
                for j in range(len(idx))
            ):
                continue
            ear_k = k
            break
        if ear_k is None:
            break
        k = ear_k
        i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
        triangles.append([pts[i0], pts[i1], pts[i2]])
        del idx[k]
    if len(idx) == 3:
        triangles.append([pts[idx[0]], pts[idx[1]], pts[idx[2]]])
    elif len(idx) > 3:
        # Degenerate fallback (fan) — only correct for convex, but keeps a
        # coverage rather than returning nothing.
        for i in range(1, len(idx) - 1):
            triangles.append([pts[idx[0]], pts[idx[i]], pts[idx[i + 1]]])
    return triangles


def _polygon_union_area(polygons) -> float:
    """Union area of a set of 2-D polygons (exact vertical scanline)."""
    if not polygons:
        return 0.0
    ys: set[float] = set()
    per_polygon: list[list[tuple[float, float, float, float]]] = []
    for poly in polygons:
        if len(poly) < 3:
            continue
        edges: list[tuple[float, float, float, float]] = []
        n = len(poly)
        for i in range(n):
            x0, y0 = poly[i]
            x1, y1 = poly[(i + 1) % n]
            if y0 == y1:
                continue
            if y0 > y1:
                x0, y0, x1, y1 = x1, y1, x0, y0
            edges.append((y0, y1, x0, x1))
            ys.add(y0)
            ys.add(y1)
        if edges:
            per_polygon.append(edges)
    if not per_polygon:
        return 0.0
    y_sorted = sorted(ys)
    total = 0.0
    for a, b in zip(y_sorted, y_sorted[1:]):
        if b - a <= 1e-15:
            continue
        y_mid = 0.5 * (a + b)
        intervals: list[tuple[float, float]] = []
        for edges in per_polygon:
            xs: list[float] = []
            for y0, y1, x0, x1 in edges:
                if y0 <= y_mid < y1:
                    t = (y_mid - y0) / (y1 - y0)
                    xs.append(x0 + t * (x1 - x0))
            xs.sort()
            for k in range(0, len(xs) - 1, 2):
                intervals.append((xs[k], xs[k + 1]))
        intervals.sort()
        merged: list[list[float]] = []
        for lo, hi in intervals:
            if not merged or lo > merged[-1][1]:
                merged.append([lo, hi])
            elif hi > merged[-1][1]:
                merged[-1][1] = hi
        total += sum(hi - lo for lo, hi in merged) * (b - a)
    return total


def collect_opening_areas(area_elements: dict[str, Any]) -> list[Any]:
    """Return the opening (void) areas among *area_elements*, in iteration order.

    Collected once per measurement pass so :func:`net_area` and
    :func:`opening_overlap_area` do not rescan the area collection for every
    shell.
    """
    return [a for a in area_elements.values() if getattr(a, "opening", False)]


def opening_polygons_2d(
    area_elements: dict[str, Any],
    nodes: dict[str, Any],
    area: Any,
    rel_tol: float = 1e-6,
    openings: Optional[list[Any]] = None,
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
        openings: Optional precomputed list of opening areas (from
            :func:`collect_opening_areas`); collected from *area_elements*
            when omitted.

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

    if openings is None:
        openings = collect_opening_areas(area_elements)

    openings_2d: list[list[tuple[float, float]]] = []
    for oarea in openings:
        if oarea is area:
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
        openings_2d.append(_project_2d(overts, axes))
    return clip, openings_2d, axes, verts


def opening_overlap_area(
    model: Any, area: Any, rel_tol: float = 1e-6, openings: Optional[list[Any]] = None
) -> float:
    """Total area of *area* covered by coplanar opening (void) areas.

    Clips each opening against the (possibly concave) parent by triangulating
    the parent first, then unions the clipped pieces so overlapping openings
    contribute their union area rather than being double-counted.

    Args:
        model: A ``SAPModelData`` or ``MeshModel`` (exposes ``area_elements``
            and ``nodes``).
        area: The parent shell area whose opening overlap is to be measured.
        rel_tol: Relative coplanarity tolerance, as a fraction of the parent's
            characteristic size (unit-agnostic).
        openings: Optional precomputed list of opening areas (from
            :func:`collect_opening_areas`); collected from *model* when
            omitted.

    Returns:
        The union area (in the model's length unit squared) of the portions of
        opening polygons that lie inside *area*.
    """
    if openings is None:
        openings = collect_opening_areas(model.area_elements)
    if not openings:
        return 0.0
    info = opening_polygons_2d(
        model.area_elements, _all_nodes(model), area, rel_tol, openings=openings
    )
    if info is None:
        return 0.0
    clip, openings_2d, _axes, _verts = info
    pieces: list[list[tuple[float, float]]] = []
    for triangle in _triangulate(clip):
        for subject in openings_2d:
            clipped = _clip_sutherland_hodgman(subject, triangle)
            if len(clipped) >= 3:
                pieces.append(clipped)
    return _polygon_union_area(pieces)


def net_area(model: Any, area: Any, openings: Optional[list[Any]] = None) -> float:
    """Net shell area of *area* after subtracting overlapping openings.

    An opening (void) area itself contributes zero area; for a shell area it
    returns ``polygon_area_3d(vertices) − opening_overlap_area(model, area)``,
    floored at zero.  *openings* is an optional precomputed list of opening
    areas (see :func:`collect_opening_areas`) so a caller measuring many areas
    can collect them once; when the list is empty the raw area is returned
    without overlap processing.
    """
    if getattr(area, "opening", False):
        return 0.0
    raw = polygon_area_3d(area_vertices(model, area))
    if raw <= 0.0:
        return 0.0
    if openings is None:
        openings = collect_opening_areas(model.area_elements)
    if not openings:
        return raw
    return max(0.0, raw - opening_overlap_area(model, area, openings=openings))


def conform_cell_to_openings(
    cell: list[tuple[float, float]],
    openings: list[list[tuple[float, float]]],
) -> list[list[tuple[float, float]]]:
    """Return whether *cell* is not fully covered by the coplanar *openings*.

    Returns ``[cell]`` when the cell survives (it is not fully inside any
    opening) and ``[]`` when it is fully covered and must be dropped.  The
    decision uses exact polygon intersection against the cell's true boundary,
    never the cell centroid, so a partially-covered cell is never dropped and a
    fully-covered cell is never retained.

    Args:
        cell: The cell polygon (2-D, counter-clockwise) to test.
        openings: Coplanar opening polygons in the same 2-D basis (any winding).

    Returns:
        ``[cell]`` (keep) or ``[]`` (drop).
    """
    cell_area = abs(_signed_area_2d(cell))
    tol = 1e-12 * max(1.0, cell_area)
    pieces: list[list[tuple[float, float]]] = []
    for opening in openings:
        for triangle in _triangulate(opening):
            clipped = _clip_sutherland_hodgman(cell, triangle)
            if len(clipped) >= 3 and abs(_signed_area_2d(clipped)) > tol:
                pieces.append(clipped)
    if not pieces:
        return [list(cell)]
    if _polygon_union_area(pieces) >= cell_area - tol:
        return []  # fully covered — drop the cell
    return [list(cell)]
