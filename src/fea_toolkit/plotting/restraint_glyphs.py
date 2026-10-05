"""Support-restraint glyph geometry — per-DOF squares, circles, arrows and curls.

Pure numpy plus PyVista primitives: no Qt and no plotter, so the shapes can be
measured without a render window.

**One glyph per restrained DOF** is what makes this right for any restraint set,
rather than only the textbook ones.  Two styles cover the same DOFs:

* ``"block"`` (the default) — a restrained **translation** (U1/U2/U3) draws a
  square **wireframe outline** whose face normal points along that global axis,
  and a restrained **rotation** (R1/R2/R3) draws a circular **wireframe
  outline** whose face normal points along the rotation axis.  The circle is
  larger than the square, and both are centred on the node.  No fill is used, so
  a square and a circle that share a plane both stay visible.
* ``"arrow"`` (the fallback, kept as a config option) — an arrow pointing at the
  joint per translation and a curl per rotation, the earlier representation.

Every glyph is centred on the node, so a fixed base reads as three squares and
three circles stacked at the joint rather than a symbol offset from it.
"""

from typing import Optional

import numpy as np

#: Global axis for each restrained DOF: U1 → X, U2 → Y, U3 → Z (R1..R3 likewise,
#: as rotation *about* that axis).
_AXES = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))

#: Arrow proportions, as fractions of the glyph size, so the parts stay in step.
_TIP_LENGTH = 0.35
_TIP_RADIUS = 0.12
_SHAFT_RADIUS = 0.03

#: A rotation glyph curls this far (degrees) at this fraction of the glyph size.
_ARC_DEGREES = 270.0
_ARC_RADIUS = 0.5
_ARC_RESOLUTION = 24

#: Block-style glyph proportions, as fractions of *size* (the caller's glyph
#: length).  The square is the translation symbol and the circle the rotation
#: one; the circle is deliberately larger so the two kinds cannot be mistaken.
#: Both are wireframe outlines, so no thickness is needed.
_PLATE_HALF_WIDTH = 0.7
_DISC_RADIUS = 1.0
_CIRCLE_RESOLUTION = 32

#: Cached unit-arrow geometry per translation axis (built once).  ``pv.Arrow``
#: constructs a fresh VTK source pipeline on every call, and
#: :func:`render_restraints` draws one arrow per restrained translation DOF —
#: on a real model that is hundreds of arrows, each followed by a
#: translate/transform/merge.  Caching the numpy ``(points, faces)`` and
#: rebuilding with :class:`pyvista.PolyData` keeps the per-glyph cost to a
#: broadcast-multiply-and-add plus one small mesh.
_UNIT_ARROW: dict[int, tuple[np.ndarray, np.ndarray]] = {}


def _unit_arrow(axis: int) -> tuple[np.ndarray, np.ndarray]:
    """Return cached ``(points, faces)`` of a unit arrow pointing at the origin.

    The tail sits one unit along the positive *axis* and the tip at the origin,
    matching :func:`translation_glyph`'s ``start`` / ``direction`` convention.
    """
    cached = _UNIT_ARROW.get(axis)
    if cached is None:
        import pyvista as pv

        direction = np.asarray(_AXES[axis], dtype=float)
        mesh = pv.Arrow(
            start=direction,
            direction=-direction,
            scale=1.0,
            tip_length=_TIP_LENGTH,
            tip_radius=_TIP_RADIUS,
            shaft_radius=_SHAFT_RADIUS,
        )
        cached = (np.array(mesh.points, copy=True), np.array(mesh.faces, copy=True))
        _UNIT_ARROW[axis] = cached
    return cached


def _perpendicular(axis: np.ndarray) -> tuple:
    """Two orthonormal vectors spanning the plane normal to *axis*."""
    seed = np.array([0.0, 0.0, 1.0]) if abs(axis[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    u = np.cross(axis, seed)
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    return u, v / np.linalg.norm(v)


def translation_glyph(position: np.ndarray, axis: int, size: float) -> object:
    """An arrow of length *size* along translation axis *axis* (``0`` = U1).

    Pointing **at the joint**: the tail sits *size* from the node and the head
    converges on it, which is how a support reaction reads.
    """
    import pyvista as pv

    pts, faces = _unit_arrow(axis)
    new_pts = pts * size + np.asarray(position, dtype=float)
    return pv.PolyData(new_pts, faces=faces.copy())


def rotation_glyph(position: np.ndarray, axis: int, size: float) -> object:
    """A curved arrow about rotation axis *axis* (``0`` = R1, about X)."""
    import pyvista as pv

    position = np.asarray(position, dtype=float)
    direction = np.asarray(_AXES[axis], dtype=float)
    u, v = _perpendicular(direction)
    radius = _ARC_RADIUS * size

    angles = np.radians(np.linspace(0.0, _ARC_DEGREES, _ARC_RESOLUTION))
    circle = np.array([position + radius * (np.cos(a) * u + np.sin(a) * v) for a in angles])
    # Explicit ``[2, i, i+1]`` segments rather than a helper: this is the same
    # encoding the frame renderer uses, so there is nothing to look up.
    segments = np.hstack([[2, i, i + 1] for i in range(len(circle) - 1)])
    arc = pv.PolyData(circle, lines=segments)

    # A head along the arc's tangent, so the curl reads as turning.
    tangent = np.cross(direction, circle[-1] - circle[-2])
    head = pv.Cone(
        center=circle[-1],
        direction=tangent,
        height=_TIP_LENGTH * size,
        radius=_TIP_RADIUS * size,
    )
    return arc.merge(head)


def translation_block_glyph(position: np.ndarray, axis: int, size: float) -> object:
    """A square wireframe outline centred on *position*, face normal along *axis*.

    The square's normal is the direction of the restrained translation (U1 → X,
    U2 → Y, U3 → Z), so a fixed base reads as three mutually perpendicular
    squares stacked at the joint.  It is an outline (no fill), so a rotational
    circle drawn in the same plane does not hide it.
    """
    import pyvista as pv

    direction = np.asarray(_AXES[axis], dtype=float)
    u, v = _perpendicular(direction)
    position = np.asarray(position, dtype=float)
    w = _PLATE_HALF_WIDTH * size
    du, dv = u * w, v * w

    corners = np.array(
        [
            position - du - dv,
            position + du - dv,
            position + du + dv,
            position - du + dv,
        ]
    )
    # Four closed segments: 0→1, 1→2, 2→3, 3→0.
    lines = np.array([2, 0, 1, 2, 1, 2, 2, 2, 3, 2, 3, 0])
    return pv.PolyData(corners, lines=lines)


def rotation_disc_glyph(position: np.ndarray, axis: int, size: float) -> object:
    """A circular wireframe outline centred on *position*, normal along *axis*.

    Larger than the translation square, so a rotational restraint reads as a
    different shape than a translational one.  It is an outline (no fill), so
    the square and circle remain distinguishable when both are drawn for a node.
    """
    import pyvista as pv

    direction = np.asarray(_AXES[axis], dtype=float)
    u, v = _perpendicular(direction)
    position = np.asarray(position, dtype=float)
    radius = _DISC_RADIUS * size

    angles = np.linspace(0.0, 2.0 * np.pi, _CIRCLE_RESOLUTION, endpoint=False)
    pts = np.array([position + radius * (np.cos(a) * u + np.sin(a) * v) for a in angles])
    n = len(pts)
    lines = np.hstack([[2, i, (i + 1) % n] for i in range(n)])
    return pv.PolyData(pts, lines=lines)


def support_glyph(
    position: np.ndarray,
    dofs,
    size: float,
    style: str = "block",
) -> Optional[object]:
    """One merged glyph for the restrained DOFs in *dofs*, or ``None``.

    Args:
        position: Node coordinates, shape ``(3,)``.
        dofs: ``[U1, U2, U3, R1, R2, R3]`` flags; ``1`` = fixed.
        size: Glyph length in model units — the caller scales it to the model.
        style: ``"block"`` (plates and discs, the default) or ``"arrow"``
            (arrows and curls, the earlier representation).

    Returns:
        A ``PolyData`` holding one glyph per restrained DOF, or ``None`` when
        nothing is restrained.
    """
    position = np.asarray(position, dtype=float)
    block = style == "block"
    parts = []
    for index, flag in enumerate(list(dofs)[:6]):
        if not flag:
            continue
        if index < 3:
            parts.append(
                translation_block_glyph(position, index, size)
                if block
                else translation_glyph(position, index, size)
            )
        else:
            parts.append(
                rotation_disc_glyph(position, index - 3, size)
                if block
                else rotation_glyph(position, index - 3, size)
            )
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    import pyvista as pv

    return pv.merge(parts)
