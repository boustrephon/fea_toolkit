"""Support-restraint glyph geometry — per-DOF arrows and rotation arcs.

Pure numpy plus PyVista primitives: no Qt and no plotter, so the shapes can be
measured without a render window.

**One glyph per restrained DOF** is what makes this right for any restraint set,
rather than only the textbook ones:

* a restrained **translation** (U1/U2/U3) draws an arrow pointing at the joint
  along that global axis — a fixed base reads as three arrows converging on it, a
  roller along X as a single arrow;
* a restrained **rotation** (R1/R2/R3) draws a curved arrow about that axis.

The planned refinement — classic hatched-ground / pin-triangle / roller symbols —
replaces these only for the patterns it recognises and keeps these arrows as the
fallback, so no restraint set is ever left undrawn.
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

    direction = np.asarray(_AXES[axis], dtype=float)
    return pv.Arrow(
        start=np.asarray(position, dtype=float) + direction * size,
        direction=-direction,
        scale=size,
        tip_length=_TIP_LENGTH,
        tip_radius=_TIP_RADIUS,
        shaft_radius=_SHAFT_RADIUS,
    )


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


def support_glyph(position: np.ndarray, dofs, size: float) -> Optional[object]:
    """One merged glyph for the restrained DOFs in *dofs*, or ``None``.

    Args:
        position: Node coordinates, shape ``(3,)``.
        dofs: ``[U1, U2, U3, R1, R2, R3]`` flags; ``1`` = fixed.
        size: Glyph length in model units — the caller scales it to the model.

    Returns:
        A ``PolyData`` holding one glyph per restrained DOF (arrows and curls
        together), or ``None`` when nothing is restrained.
    """
    position = np.asarray(position, dtype=float)
    parts = []
    for index, flag in enumerate(list(dofs)[:6]):
        if not flag:
            continue
        parts.append(
            translation_glyph(position, index, size)
            if index < 3
            else rotation_glyph(position, index - 3, size)
        )
    if not parts:
        return None
    merged = parts[0]
    for part in parts[1:]:
        merged = merged.merge(part)
    return merged
