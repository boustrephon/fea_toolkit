"""Frame-section cross-section outlines — pure geometry, no backend.

The profile polygons for frame sections, kept free of any Rhino / PyVista
import so a section can be extruded to a solid in either backend.  These are the
single source of truth: :mod:`fea_toolkit.rhino.geometry` wraps them into Rhino
curves for ``Extrusion.Create``, and a PyVista renderer can sweep them into
extruded solid actors.

The convention: the profile lives in the section's local **YZ** plane, with
``x = width`` (along the element's local *y* axis) and ``y = depth`` (along the
element's local *z* axis) — the same convention :mod:`fea_toolkit.rhino.geometry`
uses.
"""

from __future__ import annotations

import math
from typing import Any, Optional, Union

from .sap_data import (
    BoxSection,
    ChannelSection,
    CircularSection,
    ISection,
    PipeSection,
    RectangularSection,
    Section,
)

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
    """Solid rectangular profile in the YZ plane."""
    h, w = depth / 2.0, bf / 2.0
    return [(-w, -h), (-w, h), (w, h), (w, -h)]


def profile_i(depth: float, bf: float, tf: float, tw: float) -> list[tuple[float, float]]:
    """I / wide-flange profile in the YZ plane."""
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


def profile_box(depth: float, bf: float, tf: float, tw: float) -> list[tuple[float, float]]:
    """Box / RHS (hollow rectangle) profile in the YZ plane."""
    h = depth / 2.0
    w = bf / 2.0
    hi = h - tf
    wi = w - tw
    return [
        (-w, -h),
        (w, -h),
        (w, h),
        (-w, h),
        (-w, hi),
        (wi, hi),
        (wi, -hi),
        (-w, -hi),
    ]


def profile_channel(depth: float, bf: float, tf: float, tw: float) -> list[tuple[float, float]]:
    """Channel / C-section profile in the YZ plane (CW winding)."""
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


#: The shape a :func:`section_profile_outline` returns — either a polygonal
#: ``("polygon", [(x, y), ...])`` or a ``("circle", radius)`` pair.  A single
#: union rather than a dataclass keeps the Rhino / PyVista call sites plain.
ProfileOutline = tuple[str, Any]


def section_profile_outline(section: Union[Section, Any]) -> Optional[ProfileOutline]:
    """The closed cross-section outline of *section*.

    Returns ``("polygon", [(x, y), ...])`` for polygonal sections, or
    ``("circle", radius)`` for circular ones — the same shape
    :mod:`fea_toolkit.rhino.geometry` consumes for ``Extrusion.Create``.  A
    catalogue section with zero dimensions falls back to a square of side
    ``sqrt(A)`` (or 0.1), so a section with no geometry still has an outline.

    Args:
        section: Any :class:`~fea_toolkit.model.sap_data.Section` (or a subtype).

    Returns:
        A ``("polygon", points)`` / ``("circle", radius)`` pair, or ``None`` if
        the section cannot be outlined.
    """
    pts_2d: Optional[list[tuple[float, float]]] = None
    is_circle = False
    radius = 0.0

    if isinstance(section, RectangularSection):
        pts_2d = profile_rect(section.depth, section.bf)
    elif isinstance(section, CircularSection):
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
    flanges, box → four sides), a solid section becomes its open perimeter, and a
    circular section becomes an open tube of *circle_segments* sides.  A
    catalogue section with zero dimensions falls back to a square.

    Args:
        section: Any :class:`~fea_toolkit.model.sap_data.Section` (or a subtype).
        circle_segments: Number of sides used for a circular section's tube.

    Returns:
        A list of open polylines, each a list of ``(x, y)`` points.
    """
    if isinstance(section, RectangularSection):
        return _rect_sides(section.depth / 2.0, section.bf / 2.0)

    if isinstance(section, (CircularSection, PipeSection)):
        if isinstance(section, CircularSection):
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
            [(0.0, -(h - tf)), (0.0, h - tf)],  # web
        ]

    if isinstance(section, ChannelSection):
        if section.depth <= 0 or section.bf <= 0:
            return _zero_dimension_plates(section)
        h, w = section.depth / 2.0, section.bf / 2.0
        tf = section.tf
        return [
            [(0.0, -(h - tf)), (0.0, h - tf)],  # web
            [(0.0, h - tf / 2.0), (w, h - tf / 2.0)],  # top flange
            [(0.0, -(h - tf / 2.0)), (w, -(h - tf / 2.0))],  # bottom flange
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
            [(-x, -(h - tf)), (-x, h - tf)],  # left web
            [(x, -(h - tf)), (x, h - tf)],  # right web
        ]

    return _zero_dimension_plates(section)
