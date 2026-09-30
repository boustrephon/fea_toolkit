"""Screen-space geometry tests for rubber-band (marquee) selection.

The rectangle a marquee drags is measured in **display pixels**; deciding what
it selects is pure 2-D geometry -- is a point inside, does a segment cross the
rectangle, does a polygon touch it -- so this module is Qt- and VTK-free and
unit-tested on its own (``tests/test_gui_marquee.py``).  The *projection* of
world geometry into those pixels is the GUI's business (it needs the renderer);
these predicates only test the result.
"""

from collections.abc import Sequence

__all__ = [
    "point_in_rect",
    "polygon_hits_rect",
    "polygon_inside_rect",
    "segment_hits_rect",
    "segment_inside_rect",
]


def point_in_rect(px: float, py: float, x0: float, y0: float, x1: float, y1: float) -> bool:
    """Whether ``(px, py)`` lies inside (or on) the rectangle.

    The rectangle's corners may be given in either order; the bounds are
    normalised first, so a left-to-right or right-to-left drag behaves alike.
    """
    x_lo, x_hi = sorted((x0, x1))
    y_lo, y_hi = sorted((y0, y1))
    return x_lo <= px <= x_hi and y_lo <= py <= y_hi


def segment_hits_rect(
    ax: float, ay: float, bx: float, by: float, x0: float, y0: float, x1: float, y1: float
) -> bool:
    """Whether the closed segment ``A-B`` intersects the rectangle.

    A member whose *endpoints* are both outside the box but whose span passes
    through it must still be selected -- a marquee around a column's base should
    catch the column above it.  So the test is a proper segment-vs-rectangle
    intersection, not an endpoint or midpoint test.
    """
    x_lo, x_hi = sorted((x0, x1))
    y_lo, y_hi = sorted((y0, y1))
    if point_in_rect(ax, ay, x_lo, y_lo, x_hi, y_hi):
        return True
    if point_in_rect(bx, by, x_lo, y_lo, x_hi, y_hi):
        return True
    # Otherwise the segment crosses the rectangle iff it crosses one of its edges.
    return (
        _segments_cross(ax, ay, bx, by, x_lo, y_lo, x_lo, y_hi)
        or _segments_cross(ax, ay, bx, by, x_lo, y_hi, x_hi, y_hi)
        or _segments_cross(ax, ay, bx, by, x_hi, y_hi, x_hi, y_lo)
        or _segments_cross(ax, ay, bx, by, x_hi, y_lo, x_lo, y_lo)
    )


def segment_inside_rect(
    ax: float, ay: float, bx: float, by: float, x0: float, y0: float, x1: float, y1: float
) -> bool:
    """Whether the whole closed segment ``A-B`` lies inside the rectangle.

    The rectangle is convex, so a straight segment is fully enclosed exactly when
    both endpoints are inside (or on) the box.  This is the *window* marquee test,
    in contrast to :func:`segment_hits_rect` (the *crossing* test).
    """
    return point_in_rect(ax, ay, x0, y0, x1, y1) and point_in_rect(bx, by, x0, y0, x1, y1)


def polygon_hits_rect(
    vertices: Sequence[tuple[float, float]], x0: float, y0: float, x1: float, y1: float
) -> bool:
    """Whether a polygon touches the rectangle -- a vertex inside, an edge
    crossing it, or the rectangle lying wholly inside the polygon.

    Args:
        vertices: Polygon corners as ``(x, y)`` pairs, in order.
    """
    x_lo, x_hi = sorted((x0, x1))
    y_lo, y_hi = sorted((y0, y1))
    n = len(vertices)
    for i in range(n):
        ax, ay = vertices[i]
        bx, by = vertices[(i + 1) % n]
        if point_in_rect(ax, ay, x_lo, y_lo, x_hi, y_hi):
            return True
        if segment_hits_rect(ax, ay, bx, by, x_lo, y_lo, x_hi, y_hi):
            return True
    # A rectangle lying wholly inside the polygon has no vertex in the box and
    # no edge crossing it, so test a corner of the box against the polygon.
    return _point_in_polygon(x_lo, y_lo, vertices)


def polygon_inside_rect(
    vertices: Sequence[tuple[float, float]], x0: float, y0: float, x1: float, y1: float
) -> bool:
    """Whether a polygon lies entirely inside the rectangle.

    Every vertex inside (or on) a convex rectangle keeps the whole polygon inside
    it, because the polygon lies within the convex hull of its vertices.  This is
    the *window* marquee test for areas, in contrast to :func:`polygon_hits_rect`
    (the *crossing* test).
    """
    return all(point_in_rect(px, py, x0, y0, x1, y1) for px, py in vertices)


def _point_in_polygon(px: float, py: float, vertices: Sequence[tuple[float, float]]) -> bool:
    """Whether ``(px, py)`` lies inside the polygon (even-odd ray casting).

    A point exactly on an edge or vertex is treated as outside -- the caller
    already tested the polygon's own vertices and edges against the rectangle,
    so a corner on the boundary is covered by those checks.
    """
    inside = False
    n = len(vertices)
    j = n - 1
    for i in range(n):
        xi, yi = vertices[i]
        xj, yj = vertices[j]
        if (yi > py) != (yj > py):
            x_cross = (xj - xi) * (py - yi) / (yj - yi) + xi
            if px < x_cross:
                inside = not inside
        j = i
    return inside


def _segments_cross(
    ax: float,
    ay: float,
    bx: float,
    by: float,
    cx: float,
    cy: float,
    dx: float,
    dy: float,
) -> bool:
    """Whether closed segments ``A-B`` and ``C-D`` intersect.

    The standard orientation test: the two segments straddle each other, or a
    collinear endpoint lies on the other segment.
    """

    def orient(px: float, py: float, qx: float, qy: float, rx: float, ry: float) -> float:
        return (qx - px) * (ry - py) - (qy - py) * (rx - px)

    def on(px: float, py: float, qx: float, qy: float, rx: float, ry: float) -> bool:
        return min(px, qx) <= rx <= max(px, qx) and min(py, qy) <= ry <= max(py, qy)

    o1 = orient(ax, ay, bx, by, cx, cy)
    o2 = orient(ax, ay, bx, by, dx, dy)
    o3 = orient(cx, cy, dx, dy, ax, ay)
    o4 = orient(cx, cy, dx, dy, bx, by)

    if (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0):
        return True
    return (
        (o1 == 0 and on(ax, ay, bx, by, cx, cy))
        or (o2 == 0 and on(ax, ay, bx, by, dx, dy))
        or (o3 == 0 and on(cx, cy, dx, dy, ax, ay))
        or (o4 == 0 and on(cx, cy, dx, dy, bx, by))
    )
