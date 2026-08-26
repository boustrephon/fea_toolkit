"""Shared plotting utilities (backend-agnostic)."""

from typing import Optional

import numpy as np


def _unit_vec(v: np.ndarray) -> np.ndarray:
    """Unit vector, falling back to +x for zero-length input."""
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else np.array([1.0, 0.0, 0.0])


def _flag_color(value: float) -> tuple[int, int, int]:
    """Map a signed force value to an RGB triple (blue = positive, red = negative)."""
    if value >= 0:
        return (59, 130, 246)  # blue
    return (239, 68, 68)  # red


def compute_flag_parts(
    start: np.ndarray,
    end: np.ndarray,
    vi: float,
    vj: float,
    scale_factor: float = 1.0,
) -> Optional[tuple[np.ndarray, np.ndarray, list[tuple[int, int, int]]]]:
    """Build a triangular "flag" mesh for a bending-moment diagram.

    The flag protrudes perpendicular to the member axis, with signed
    height proportional to the force value at each end.  A quad (two
    triangles) is returned when both ends carry load; a single triangle
    when only one end does.  Returns ``None`` for degenerate members or
    negligible forces.

    End-force noise (a pinned end's ~1e-16 residual) is snapped to exact
    zero via the shared relative tolerance from
    :func:`fea_toolkit.utils._snap_flag_noise`, so a zero-area flag tip is
    never built.  Any triangle that still degenerates (e.g. a genuinely
    small tip offset at large site coordinates, where float precision
    collapses it) is dropped.

    Args:
        start: 3D coordinate of the member start node.
        end: 3D coordinate of the member end node.
        vi: Force value at the start node (e.g. bending moment Mz).
        vj: Force value at the end node.
        scale_factor: Multiplies the flag offset for display.

    Returns:
        ``(verts, tris, colors)`` — an (V, 3) float vertex array, a
        (T, 3) integer triangle-index array, and a list of (T) RGB
        triples — or ``None`` when there is nothing to draw.
    """
    from ..utils import _FLAG_ZERO_SNAP_TOL, _snap_flag_noise

    p0 = np.asarray(start, dtype=float)
    p1 = np.asarray(end, dtype=float)
    axis = p1 - p0
    length = np.linalg.norm(axis)
    if length < 1e-12:
        return None

    unit = axis / length
    # Reference direction for the perpendicular plane (avoid degenerate
    # cross product when the member is near-vertical).
    ref = np.array([0.0, 0.0, 1.0])
    if abs(np.dot(unit, ref)) > 0.99:
        ref = np.array([1.0, 0.0, 0.0])
    side = _unit_vec(np.cross(unit, ref))
    up = _unit_vec(np.cross(side, unit))

    # Snap end-force noise to exact zero (relative tolerance) so a pinned
    # end's residual cannot build a zero-area flag tip.
    vi, vj = _snap_flag_noise(vi, vj, _FLAG_ZERO_SNAP_TOL)
    hi = vi * scale_factor
    hj = vj * scale_factor
    mag_i, mag_j = abs(hi), abs(hj)
    if mag_i == 0.0 and mag_j == 0.0:
        return None

    # Flag tip offsets (signed — positive above the member, negative below)
    q0 = p0 + up * hi
    q1 = p1 + up * hj

    if mag_i > 0.0 and mag_j > 0.0:
        verts = np.array([p0, q0, q1, p1])
        all_tris = [(0, 1, 2), (0, 2, 3)]
        all_colors = [_flag_color(vi), _flag_color((vi + vj) * 0.5)]
    elif mag_i > 0.0:
        verts = np.array([p0, q0, p1])
        all_tris = [(0, 1, 2)]
        all_colors = [_flag_color(vi)]
    else:
        verts = np.array([p0, q1, p1])
        all_tris = [(0, 1, 2)]
        all_colors = [_flag_color(vj)]

    # Defence in depth: drop any triangle that degenerated to zero area
    # (coincident tip at large site coordinates / float32 collapse).
    tris, colors = [], []
    for t, c in zip(all_tris, all_colors):
        e1 = verts[t[1]] - verts[t[0]]
        e2 = verts[t[2]] - verts[t[0]]
        if np.linalg.norm(np.cross(e1, e2)) > 1e-12:
            tris.append(t)
            colors.append(c)
    if not tris:
        return None

    return verts, tris, colors
