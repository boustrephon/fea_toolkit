"""Display dimensions for frame sections — pure geometry, no backend.

The visualisers draw a frame section in two ways:

* the **basic display** — the extruded cross-section (``section_profiles`` /
  the Rhino extrusion) — which needs a breadth × depth × wall box; and
* the **selection cue** — a tube whose diameter is the diagonal of that box.

Most sections carry their dimensions explicitly (``depth``/``bf`` on the
rectangular, I, box and channel shapes; ``od`` on a pipe; ``diameter`` on a
circle).  Catalogue and :class:`~fea_toolkit.model.sap_data.GeneralSection`
sections carry only derived properties (``A``, ``I33``, ``I22``, ``J``), so
this module back-calculates a representative box from the inertias, per the
project's display convention:

* anything not steel → a **solid rectangle**;
* steel → a **hollow rectangle (RHS)** whose wall thickness is ``D / 40``.

The back-calculation is pure geometry (no OpenSees, no Qt, no backend), so the
PyVista viewer, the GUI selection tube and the Rhino export all share it, and
it is unit-tested on its own.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

from .sap_data import (
    BoxSection,
    ChannelSection,
    CircularSection,
    ConcreteCircularSection,
    ConcreteRectangularSection,
    ISection,
    PipeSection,
    RectangularSection,
    ShellSection,
)

__all__ = [
    "SectionDisplayBox",
    "back_calc_display_box",
    "is_steel_section",
    "resolve_section_display_box",
    "selection_tube_diameter",
    "shell_display_thickness",
]

#: Canonical shape ids that denote an open / hollow steel section (used only as
#: a fallback when a section's material type cannot be resolved).
_STEEL_SHAPE_IDS = frozenset({"I", "CH", "CHS", "RHS", "A", "AA", "T"})

#: Default shell thickness when a shell section carries none — 200 mm, expressed
#: in SI metres before conversion to the model's length unit.
_DEFAULT_SHELL_THICKNESS_M = 0.2

#: The selection tube is drawn 5 % larger than the section it marks, so it
#: stands proud of the extruded section rather than sitting flush inside it.
_SELECTION_TUBE_CLEARANCE = 1.05


@dataclass(frozen=True)
class SectionDisplayBox:
    """A rectangular envelope for a frame section, in model length units.

    Attributes:
        breadth: Overall breadth ``B`` (the local-z extent).
        depth: Overall depth ``D`` (the local-y extent).
        wall: Wall thickness for a hollow box (``0.0`` for a solid envelope).
        source: Where the dimensions came from — ``"explicit"``,
            ``"back_calc_solid"`` or ``"back_calc_hollow"``.
    """

    breadth: float
    depth: float
    wall: float = 0.0
    source: str = "explicit"

    def diagonal(self) -> float:
        """The envelope's corner-to-corner diagonal ``sqrt(B² + D²)``."""
        return math.hypot(self.breadth, self.depth)


def is_steel_section(section: Any, materials: Optional[dict] = None) -> bool:
    """Whether *section* should display as a steel (hollow) section.

    The material type is authoritative (``materials[name].type == "Steel"``);
    when the material cannot be resolved the section's canonical shape id is
    used as a fallback (an I / channel / angle / hollow shape is assumed steel,
    a general catalogue section is assumed not).

    Args:
        section: The section to test.
        materials: Optional ``{name: Material}`` mapping, for the authoritative
            type lookup.

    Returns:
        ``True`` when the section is a steel section.
    """
    if section is None:
        return False
    if materials:
        material = materials.get(getattr(section, "material", None))
        mtype = getattr(material, "type", "") or ""
        if "steel" in str(mtype).lower():
            return True
    shape_id = getattr(section, "shape_id", "")
    return shape_id in _STEEL_SHAPE_IDS


# ── Back-calculation ─────────────────────────────────────────────────


def _solid_rect_dims(I33: float, I22: float) -> Optional[tuple[float, float]]:
    """``(breadth, depth)`` of a solid rectangle with the given inertias.

    Inverts ``I33 = B·D³/12`` and ``I22 = D·B³/12`` exactly: the product
    ``I33·I22`` fixes ``B·D`` and the ratio ``I33/I22`` fixes ``D/B``.
    """
    i3 = max(float(I33), 0.0)
    i2 = max(float(I22), 0.0)
    if i3 <= 0.0 and i2 <= 0.0:
        return None
    if i3 <= 0.0:
        i3 = i2  # only the minor inertia is known — assume a square
    if i2 <= 0.0:
        i2 = i3  # only the major inertia is known — assume a square
    product = (144.0 * i3 * i2) ** 0.25
    ratio = (i3 / i2) ** 0.5
    depth = (product * ratio) ** 0.5
    breadth = (product / ratio) ** 0.5
    return breadth, depth


def _hollow_aspect(I33: float, I22: float) -> Optional[float]:
    """Aspect ``u = B/D`` of an RHS (wall ``D/40``) matching ``I33/I22``.

    Both inertias are homogeneous of degree 4 in ``(B, D)``, so their ratio
    depends on ``u`` alone; the ratio is monotonic decreasing from ``→∞`` at
    ``u → D/20`` to ``0`` at ``u → ∞``, so a bounded bisection finds ``u``.
    """
    target = I33 / I22

    def ratio(u: float) -> float:
        g33 = (u - (u - 0.05) * 0.95**3) / 12.0
        g22 = (u**3 - 0.95 * (u - 0.05) ** 3) / 12.0
        return g33 / g22 if g22 > 0.0 else float("inf")

    lo = 0.05 + 1e-9  # the physical minimum: B > 2·t = D/20
    hi = 1e6
    if not (ratio(hi) < target < ratio(lo)):
        # The target ratio is outside the hollow box's achievable range (a
        # section whose breadth is thinner than two walls, or an absurd aspect).
        return None
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if ratio(mid) > target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _hollow_rect_dims(I33: float, I22: float) -> Optional[tuple[float, float, float]]:
    """``(breadth, depth, wall)`` of an RHS with wall ``D/40`` and the inertias."""
    i3 = max(float(I33), 0.0)
    i2 = max(float(I22), 0.0)
    if i3 <= 0.0 and i2 <= 0.0:
        return None
    if i3 <= 0.0 or i2 <= 0.0:
        # Only one inertia is known — assume a square box, where g33(1) == g22(1).
        g = (1.0 - (1.0 - 0.05) * 0.95**3) / 12.0
        depth = (max(i3, i2) / g) ** 0.25
        breadth = depth
    else:
        u = _hollow_aspect(i3, i2)
        if u is None:
            return None
        g33 = (u - (u - 0.05) * 0.95**3) / 12.0
        depth = (i3 / g33) ** 0.25
        breadth = u * depth
    return breadth, depth, depth / 40.0


def back_calc_display_box(
    section: Any, materials: Optional[dict] = None
) -> Optional[SectionDisplayBox]:
    """The display box back-calculated from a section's inertias, or ``None``.

    Only applies to sections that carry **no** explicit dimensions (a
    :class:`~fea_toolkit.model.sap_data.GeneralSection` or bare
    :class:`~fea_toolkit.model.sap_data.Section`).  Steel sections become a
    hollow rectangle (wall ``D/40``); everything else becomes a solid
    rectangle.  ``None`` is returned when the section already has explicit
    dimensions or has no usable inertia.

    Args:
        section: The section to back-calculate for.
        materials: Optional ``{name: Material}`` mapping (for steel detection).

    Returns:
        A :class:`SectionDisplayBox`, or ``None`` when not applicable.
    """
    if section is None or _explicit_box(section) is not None:
        return None
    i33 = float(getattr(section, "I33", 0.0) or 0.0)
    i22 = float(getattr(section, "I22", 0.0) or 0.0)
    if i33 <= 0.0 and i22 <= 0.0:
        return None
    if is_steel_section(section, materials):
        dims = _hollow_rect_dims(i33, i22)
        if dims is None:
            return None
        breadth, depth, wall = dims
        return SectionDisplayBox(breadth, depth, wall=wall, source="back_calc_hollow")
    dims = _solid_rect_dims(i33, i22)
    if dims is None:
        return None
    breadth, depth = dims
    return SectionDisplayBox(breadth, depth, wall=0.0, source="back_calc_solid")


def _explicit_box(section: Any) -> Optional[SectionDisplayBox]:
    """The ``B × D`` envelope for a section whose dimensions are explicit."""
    if isinstance(
        section,
        (
            RectangularSection,
            ConcreteRectangularSection,
            ISection,
            BoxSection,
            ChannelSection,
        ),
    ):
        breadth = float(getattr(section, "bf", 0.0) or 0.0)
        depth = float(getattr(section, "depth", 0.0) or 0.0)
        if breadth > 0.0 and depth > 0.0:
            return SectionDisplayBox(breadth, depth)
    return None


def resolve_section_display_box(
    section: Any, materials: Optional[dict] = None
) -> Optional[SectionDisplayBox]:
    """The display box for *section* — explicit dimensions or a back-calc.

    Args:
        section: The section to resolve.
        materials: Optional ``{name: Material}`` mapping (for steel detection
            in the back-calculation).

    Returns:
        A :class:`SectionDisplayBox`, or ``None`` when the section has neither
        explicit dimensions nor usable inertias (the caller falls back to a
        model-scaled cue).
    """
    if section is None:
        return None
    return _explicit_box(section) or back_calc_display_box(section, materials)


# ── Selection cue ────────────────────────────────────────────────────


def selection_tube_diameter(section: Any, materials: Optional[dict] = None) -> Optional[float]:
    """The selection tube's diameter for *section*, in model length units.

    ``sqrt(B² + D²)`` for a rectangular envelope (the section's own ``B``/``D``,
    or the back-calculated box), and the outer diameter for a circular section
    (``od`` for a pipe, ``diameter`` for a solid circle).  Every diameter is
    then increased by 5 % so the cue stands proud of the section it marks.
    ``None`` when the section has no geometry to size a cue from.

    Args:
        section: The section to size the selection tube for.
        materials: Optional ``{name: Material}`` mapping.

    Returns:
        The tube diameter, or ``None``.
    """
    if section is None:
        return None
    if isinstance(section, (CircularSection, ConcreteCircularSection)):
        diameter = float(getattr(section, "diameter", 0.0) or 0.0)
        return diameter * _SELECTION_TUBE_CLEARANCE if diameter > 0.0 else None
    if isinstance(section, PipeSection):
        diameter = float(getattr(section, "od", 0.0) or 0.0)
        return diameter * _SELECTION_TUBE_CLEARANCE if diameter > 0.0 else None
    box = resolve_section_display_box(section, materials)
    if box is None:
        return None
    return box.diagonal() * _SELECTION_TUBE_CLEARANCE


# ── Shell thickness ──────────────────────────────────────────────────


def shell_display_thickness(section: Any, units: Optional[dict] = None) -> float:
    """The shell's display thickness, in model length units.

    The section's own :attr:`ShellSection.thickness` when it carries one;
    otherwise a 200 mm default converted to the model's length unit via
    :func:`~fea_toolkit._unit_scaling.length_scale_factor`.

    Args:
        section: The shell (area) section, or ``None``.
        units: Optional unit-system dict (``{"F": …, "L": …, "T": …}``); an
            empty dict means SI metres.

    Returns:
        The display thickness in model units.
    """
    thickness = float(getattr(section, "thickness", 0.0) or 0.0)
    if isinstance(section, ShellSection) and thickness > 0.0:
        return thickness
    from .._unit_scaling import length_scale_factor

    return _DEFAULT_SHELL_THICKNESS_M * length_scale_factor(units or {})
