"""Frame-section cross-section outlines — pure geometry, no backend.

The profile polygons for frame sections, kept free of any Rhino / PyVista
import so a section can be extruded to a solid in either backend.  These are the
single source of truth: :mod:`fea_toolkit.rhino.geometry` wraps them into Rhino
curves for ``Extrusion.Create``, and a PyVista renderer can sweep them into
extruded solid actors.

The convention: the profile lives in the section's local **YZ** plane, with
``x = width`` (along the element's local *z* axis) and ``y = depth`` (along the
element's local *y* axis) — the same convention :mod:`fea_toolkit.rhino.geometry`
uses.  The section's *depth* is its major (strong-axis) dimension, sitting on
the local 2-axis, and its *width* the minor dimension on the local 3-axis,
matching CSI and the OpenSees fibre-section patches.
"""

from __future__ import annotations

import math
from typing import Any, Optional, Union

from .sap_data import (
    BoxSection,
    ChannelSection,
    CircularSection,
    ConcreteCircularSection,
    ConcreteRectangularSection,
    ISection,
    PipeSection,
    RectangularSection,
    SDSection,
    Section,
)
from .section_properties import polygon_props

__all__ = [
    "ProfileOutline",
    "profile_box",
    "profile_channel",
    "profile_i",
    "profile_rect",
    "section_plates",
    "section_profile_outline",
]


def profile_rect(depth: float, bf: float) -> list[tuple[float, float]]:
    """Solid rectangular profile in the YZ plane.

    Args:
        depth: Section depth — the profile's extent along the element's local
            *y* axis (the profile's ``y`` coordinate).
        bf: Section breadth / flange width — the profile's extent along the
            element's local *z* axis (the profile's ``x`` coordinate).

    Returns:
        A ``list`` of four ``(x, y)`` points, one per corner, tracing the
        rectangle **clockwise** in the YZ plane.
    """
    h, w = depth / 2.0, bf / 2.0
    return [(-w, -h), (-w, h), (w, h), (w, -h)]


def profile_i(depth: float, bf: float, tf: float, tw: float) -> list[tuple[float, float]]:
    """I / wide-flange profile in the YZ plane.

    Args:
        depth: Section depth (overall height along the local *y* axis).
        bf: Flange width (overall breadth along the local *z* axis).
        tf: Flange thickness.
        tw: Web thickness.

    Returns:
        A ``list`` of twelve ``(x, y)`` points tracing the I outline —
        flanges and web as one closed perimeter — **counter-clockwise** in the
        YZ plane.
    """
    h = depth / 2.0
    w = bf / 2.0
    wi = tw / 2.0
    fi = h - tf
    return [
        (-w, -h),
        (w, -h),
        (w, -fi),
        (wi, -fi),
        (wi, fi),
        (w, fi),
        (w, h),
        (-w, h),
        (-w, fi),
        (-wi, fi),
        (-wi, -fi),
        (-w, -fi),
    ]


def profile_box(
    depth: float, bf: float, tf: float, tw: float
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Box / RHS (hollow rectangle) profile in the YZ plane.

    Args:
        depth: Section depth (overall height along the local *y* axis).
        bf: Section breadth (overall width along the local *z* axis).
        tf: Wall (flange) thickness.
        tw: Wall (web) thickness.

    Returns:
        The outer and inner boundaries as two separate closed loops, a
        ``tuple`` ``(outer, inner)`` with *outer* first — each loop a ``list``
        of four ``(x, y)`` points.  The inner loop is the box's void; returning
        it separately lets an extrusion consumer keep it as a hole instead of
        tracing a self-intersecting single path that opens the left wall.
    """
    h = depth / 2.0
    w = bf / 2.0
    hi = h - tf
    wi = w - tw
    outer = [(-w, -h), (w, -h), (w, h), (-w, h)]
    inner = [(-wi, -hi), (wi, -hi), (wi, hi), (-wi, hi)]
    return outer, inner


def profile_channel(depth: float, bf: float, tf: float, tw: float) -> list[tuple[float, float]]:
    """Channel / C-section profile in the YZ plane (CW winding).

    Args:
        depth: Section depth (overall height along the local *y* axis).
        bf: Flange width (overall breadth along the local *z* axis).
        tf: Flange thickness.
        tw: Web thickness.

    Returns:
        A ``list`` of eight ``(x, y)`` points tracing the channel outline
        **clockwise** in the YZ plane.
    """
    h = depth / 2.0
    w = bf / 2.0
    fi = h - tf
    wi = tw / 2.0
    return [
        (-w, -h),
        (-w, h),
        (w, h),
        (w, fi),
        (wi, fi),
        (wi, -fi),
        (w, -fi),
        (w, -h),
    ]


#: The shape a :func:`section_profile_outline` returns — a polygonal
#: ``("polygon", [(x, y), ...])``, a hollow ``("polygon", (outer, inner))``
#: (where *outer* and *inner* are two closed loops), a ``("circle", radius)``
#: pair, or a multi-region ``("regions", [(outer, [holes...]), ...])`` list
#: (one outer boundary plus its holes per disjoint solid part).  A single
#: union rather than a dataclass keeps the Rhino / PyVista call sites plain.
ProfileOutline = tuple[str, Any]

#: One extruded region: an outer boundary loop plus zero or more holes (all
#: closed ``(x, y)`` loops in the profile convention).
Region = tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]


# ── Section Designer (SD) polygon helpers ──
# ``SDSection.polygons`` stores each material's closed corner loop in
# ``(local 2, local 3)`` order (the E2K ``(SD Y, SD X)`` mapping); the profile
# convention used by the other helpers is ``(x, y)`` = ``(local 3, local 2)``.


def _loop_sides(loop: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    """The perimeter edges of a closed loop, as open two-point polylines."""
    n = len(loop)
    return [[loop[i], loop[(i + 1) % n]] for i in range(n)]


def _point_in_loop(pt: tuple[float, float], loop: list[tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon test (on-boundary counts as inside)."""
    x, y = pt
    inside = False
    n = len(loop)
    for i in range(n):
        x0, y0 = loop[i]
        x1, y1 = loop[(i + 1) % n]
        if (y0 > y) != (y1 > y):
            x_cross = (x1 - x0) * (y - y0) / (y1 - y0) + x0
            if x < x_cross:
                inside = not inside
    return inside


def _loop_inside(inner: list[tuple[float, float]], outer: list[tuple[float, float]]) -> bool:
    """True when every vertex of *inner* lies inside (or on) *outer*."""
    return all(_point_in_loop(pt, outer) for pt in inner)


def _sd_loops(section: SDSection) -> list[list[tuple[float, float]]]:
    """The SD section's polygon loops, centred and in profile ``(x, y)`` order.

    Each stored loop is a closed ``(local 2, local 3)`` corner list; the loops
    are shifted so the composite **area centroid** sits at the origin (so the
    section centres on the member axis, like the parametric profiles) and then
    transposed to ``(x, y)`` = ``(local 3, local 2)`` (``x`` = width along local
    3, ``y`` = depth along local 2).
    """
    loops = [list(loop) for (_mat, loop) in section.polygons if len(loop) >= 3]
    if not loops:
        return []
    cy = cz = area = 0.0
    for loop in loops:
        props = polygon_props(loop)
        a = props["A"]
        cy += a * props["cy"]
        cz += a * props["cz"]
        area += a
    if area > 0.0:
        cy /= area
        cz /= area
    return [[(z - cz, y - cy) for (y, z) in loop] for loop in loops]


def _sd_regions(section: SDSection) -> list[Region]:
    """The SD section's extruded regions — outer boundaries with their holes.

    Classifies the centred, transposed loops into **outer boundaries** (even
    nesting depth) and **holes** (odd nesting depth) by counting how many other
    loops contain each one, then groups each hole under its tightest enclosing
    outer.  Returns one ``(outer, [holes...])`` per disjoint solid part.  An
    island *inside* a hole reads as its own outer region — the flat region list
    cannot nest a region inside another region's hole.
    """
    loops = _sd_loops(section)
    if not loops:
        return []
    depth = [
        sum(i != j and _loop_inside(loop, other) for j, other in enumerate(loops))
        for i, loop in enumerate(loops)
    ]
    outers = [loop for loop, d in zip(loops, depth) if d % 2 == 0]
    holes = [loop for loop, d in zip(loops, depth) if d % 2 == 1]
    regions: list[Region] = []
    for outer in outers:
        tighter = [o for o in outers if o is not outer and _loop_inside(o, outer)]
        region_holes = [
            hole
            for hole in holes
            if _loop_inside(hole, outer) and not any(_loop_inside(hole, o) for o in tighter)
        ]
        regions.append((outer, region_holes))
    return regions


def section_profile_outline(section: Union[Section, Any]) -> Optional[ProfileOutline]:
    """The closed cross-section outline of *section*.

    Returns ``("polygon", [(x, y), ...])`` for polygonal sections, or
    ``("polygon", (outer, inner))`` for a hollow section (a box, where
    *outer* and *inner* are the two closed loops), or ``("circle", radius)``
    for circular ones — the same shape
    :mod:`fea_toolkit.rhino.geometry` consumes for ``Extrusion.Create``.  A
    catalogue section with zero dimensions falls back to a square of side
    ``sqrt(A)`` (or 0.1), so a section with no geometry still has an outline.
    A :class:`~fea_toolkit.model.sap_data.SDSection` returns its polygon
    perimeter (centred on the composite centroid); a multi-polygon composite
    becomes ``("regions", [(outer, [holes...]), ...])`` — one outer boundary
    per disjoint part, each with its holes.

    Args:
        section: Any :class:`~fea_toolkit.model.sap_data.Section` (or a subtype).

    Returns:
        A ``("polygon", points)`` / ``("polygon", (outer, inner))`` /
        ``("circle", radius)`` pair, or a ``("regions", [(outer, [holes...]),
        ...])`` list, or ``None`` if the section cannot be outlined.
    """
    pts_2d: Any = None
    is_circle = False
    radius = 0.0

    if isinstance(section, (RectangularSection, ConcreteRectangularSection)):
        if section.depth > 0 and section.bf > 0:
            pts_2d = profile_rect(section.depth, section.bf)
    elif isinstance(section, (CircularSection, ConcreteCircularSection)):
        is_circle = True
        radius = section.diameter / 2.0
    elif isinstance(section, ISection):
        if section.depth > 0 and section.bf > 0:
            pts_2d = profile_i(section.depth, section.bf, section.tf, section.tw)
    elif isinstance(section, PipeSection):
        if section.od > 0:
            is_circle = True
            radius = section.od / 2.0
    elif isinstance(section, BoxSection):
        if section.depth > 0 and section.bf > 0:
            pts_2d = profile_box(section.depth, section.bf, section.tf, section.tw)
    elif isinstance(section, ChannelSection) and section.depth > 0 and section.bf > 0:
        pts_2d = profile_channel(section.depth, section.bf, section.tf, section.tw)
    elif isinstance(section, SDSection):
        regions = _sd_regions(section)
        if regions:
            if len(regions) == 1:
                outer, holes = regions[0]
                if not holes:
                    return ("polygon", outer)
                if len(holes) == 1:
                    return ("polygon", (outer, holes[0]))
            return ("regions", regions)

    # Fallback for catalogue sections with zero dimensions.
    if pts_2d is None and not is_circle:
        side = (section.A**0.5) if section.A > 0 else 0.1
        pts_2d = profile_rect(side, side)

    if is_circle:
        if radius <= 0:
            radius = ((section.A / 3.14159) ** 0.5) if section.A > 0 else 0.05
        return ("circle", radius)

    if pts_2d is None:
        return None
    return ("polygon", pts_2d)


# ── Plate / surface decomposition (the efficient render path) ─────────
# A thin-walled section is a handful of open *plate* mid-lines (an I-section is
# three — two flanges and a web); a solid section is its open perimeter sides.
# Each returned polyline is swept along the member axis as flat quads, so this
# path has far fewer faces than the closed perimeter and needs no end caps.


def _circle_open_points(radius: float, segments: int) -> list[tuple[float, float]]:
    """An open circle outline as *segments* points (no closing duplicate)."""
    pts: list[tuple[float, float]] = []
    for i in range(segments):
        theta = 2.0 * math.pi * i / segments
        pts.append((radius * math.cos(theta), radius * math.sin(theta)))
    return pts


def _rect_sides(h: float, w: float) -> list[list[tuple[float, float]]]:
    """The four perimeter sides of a rectangle, as open edges (no closure)."""
    return [
        [(-w, -h), (w, -h)],
        [(w, -h), (w, h)],
        [(w, h), (-w, h)],
        [(-w, h), (-w, -h)],
    ]


def _zero_dimension_plates(section: Section) -> list[list[tuple[float, float]]]:
    """Fallback plates for a catalogue section with no real dimensions."""
    side = (section.A**0.5) if section.A > 0 else 0.1
    return _rect_sides(side / 2.0, side / 2.0)


def section_plates(
    section: Union[Section, Any],
    *,
    circle_segments: int = 8,
) -> list[list[tuple[float, float]]]:
    """The open plate/surface decomposition of *section*.

    Each returned item is an **open** polyline in the local YZ plane, swept along
    the member axis as flat quads (no end caps): a thin-walled section becomes
    its plate mid-lines (I-section → two flanges + a web, channel → web + two
    flanges, box → four sides), a solid section becomes its open perimeter, an
    SD (Section Designer) section becomes the perimeter edges of all its
    polygon loops (every outer and hole boundary), and a
    circular section becomes an open tube of *circle_segments* sides.  A
    catalogue section with zero dimensions falls back to a square.

    Args:
        section: Any :class:`~fea_toolkit.model.sap_data.Section` (or a subtype).
        circle_segments: Number of sides used for a circular section's tube.

    Returns:
        A list of open polylines, each a list of ``(x, y)`` points.
    """
    if isinstance(section, (RectangularSection, ConcreteRectangularSection)):
        if section.depth <= 0 or section.bf <= 0:
            return _zero_dimension_plates(section)
        return _rect_sides(section.depth / 2.0, section.bf / 2.0)

    if isinstance(section, (CircularSection, ConcreteCircularSection, PipeSection)):
        if isinstance(section, (CircularSection, ConcreteCircularSection)):
            radius = section.diameter / 2.0
        else:
            radius = section.od / 2.0
        if radius <= 0:
            radius = ((section.A / math.pi) ** 0.5) if section.A > 0 else 0.05
        return [_circle_open_points(radius, circle_segments)]

    if isinstance(section, ISection):
        if section.depth <= 0 or section.bf <= 0:
            return _zero_dimension_plates(section)
        h, w = section.depth / 2.0, section.bf / 2.0
        tf = section.tf
        return [
            [(-w, h - tf / 2.0), (w, h - tf / 2.0)],  # top flange
            [(-w, -(h - tf / 2.0)), (w, -(h - tf / 2.0))],  # bottom flange
            [(0.0, -(h - tf / 2.0)), (0.0, h - tf / 2.0)],  # web
        ]

    if isinstance(section, ChannelSection):
        if section.depth <= 0 or section.bf <= 0:
            return _zero_dimension_plates(section)
        h, w = section.depth / 2.0, section.bf / 2.0
        tf = section.tf
        web_x = -(w - section.tw / 2.0)  # the left wall's midline
        return [
            [(web_x, -(h - tf / 2.0)), (web_x, h - tf / 2.0)],  # web
            [(-w, h - tf / 2.0), (w, h - tf / 2.0)],  # top flange
            [(-w, -(h - tf / 2.0)), (w, -(h - tf / 2.0))],  # bottom flange
        ]

    if isinstance(section, BoxSection):
        if section.depth <= 0 or section.bf <= 0:
            return _zero_dimension_plates(section)
        h, w = section.depth / 2.0, section.bf / 2.0
        tf, tw = section.tf, section.tw
        y = h - tf / 2.0
        x = w - tw / 2.0
        return [
            [(-w, y), (w, y)],  # top flange
            [(-w, -y), (w, -y)],  # bottom flange
            [(-x, -y), (-x, y)],  # left web
            [(x, -y), (x, y)],  # right web
        ]

    if isinstance(section, SDSection):
        loops = _sd_loops(section)
        if not loops:
            return _zero_dimension_plates(section)
        sides: list[list[tuple[float, float]]] = []
        for loop in loops:
            sides.extend(_loop_sides(loop))
        return sides

    return _zero_dimension_plates(section)
