"""Vectorised construction of extruded frame meshes (no PyVista import).

Turns a list of :class:`~fea_toolkit.plotting.renderers.base.FrameGeom` into one
combined ``(points, faces, colours)`` triple by sweeping each member's section
cross-section along its axis.  Two detail levels are offered:

* ``"plates"`` — the efficient path.  Thin-walled sections become their open
  plate mid-lines (an I-section is three plates), solid sections become their
  open perimeter sides, and circular sections become an open tube.  No end caps,
  so a large model costs far fewer faces than a closed sweep.
* ``"solid"`` — the closed perimeter (matching Rhino's ``Extrusion.Create``),
  with end caps.

The construction is fully vectorised and batched into a single mesh: member axes
are computed in one pass, and members are grouped by section so each unique
cross-section is swept in one broadcast over every member that uses it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import numpy as np

from ..model.section_profiles import section_plates, section_profile_outline

__all__ = ["ExtrudedMesh", "build_extruded_frame_mesh", "local_axes_batch"]


@dataclass
class ExtrudedMesh:
    """The combined mesh for every extruded member.

    Attributes:
        points: ``(N, 3)`` vertex coordinates.
        faces: Flat VTK faces array — ``[n, i0, i1, ...]`` per face.
        face_colors: ``(F, 3)`` one RGB row per face, aligned with the faces in
            *faces*.
    """

    points: np.ndarray
    faces: np.ndarray
    face_colors: np.ndarray


def local_axes_batch(
    axes: np.ndarray, angles: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorised ``(vx, vy, vz)`` for ``M`` member axes.

    Matches :func:`~fea_toolkit.model.geometry.get_local_axes` for every row —
    including the vertical-member ``±global Y`` fallback and the rotation about
    the local x-axis — but in one numpy pass over the whole model.

    Args:
        axes: ``(M, 3)`` member axis vectors (I→J, need not be unit).
        angles: ``(M,)`` rotation about the local x-axis, degrees.

    Returns:
        ``(vx, vy, vz)`` — each ``(M, 3)`` of unit, orthonormal vectors.
    """
    axes = np.asarray(axes, dtype=float)
    angles = np.asarray(angles, dtype=float)
    if axes.ndim == 1:
        axes = axes[None, :]
        angles = np.atleast_1d(angles)

    norm = np.linalg.norm(axes, axis=1)
    norm = np.where(norm < 1e-12, 1.0, norm)
    vx = axes / norm[:, None]

    global_y = np.array([0.0, 1.0, 0.0])
    global_z = np.array([0.0, 0.0, 1.0])

    cos_sim = vx @ global_z
    vertical = np.abs(cos_sim) > 0.9999

    # Default vecxz = cross(local_x, global_Z); a vertical member uses ±global_Y.
    vecxz = np.cross(vx, global_z)
    sign = np.where(cos_sim > 0.0, 1.0, -1.0)
    vecxz[vertical] = global_y * sign[vertical, None]
    # Project out any axial component (near-vertical members).
    vecxz = vecxz - np.einsum("ij,ij->i", vecxz, vx)[:, None] * vx

    # Rodrigues rotation about vx by *angle*; angle=0 leaves vecxz unchanged.
    theta = np.deg2rad(angles)
    c = np.cos(theta)
    s = np.sin(theta)
    vecxz = vecxz * c[:, None] + np.cross(vx, vecxz) * s[:, None]

    vz = vecxz / np.linalg.norm(vecxz, axis=1)[:, None]
    vy = np.cross(vz, vx)
    vy = vy / np.linalg.norm(vy, axis=1)[:, None]
    return vx, vy, vz


def _shrunk_segment_batch(starts: np.ndarray, ends: np.ndarray, shrink: float):
    """Pull each segment's endpoints toward its midpoint by *shrink*.

    Args:
        starts: Member start points, ``(M, 3)``.
        ends: Member end points, ``(M, 3)``.
        shrink: Fraction of the original length to keep (``1.0`` = no shrink).

    Returns:
        ``(shrunk_starts, shrunk_ends)`` — the endpoints pulled toward each
        segment's midpoint by *shrink*, each ``(M, 3)``.  When *shrink* is
        ``>= 1.0`` the inputs are returned unchanged.
    """
    if shrink >= 1.0:
        return starts, ends
    mid = (starts + ends) * 0.5
    return mid + (starts - mid) * shrink, mid + (ends - mid) * shrink


def _circle_ring(radius: float, segments: int) -> np.ndarray:
    """``(segments, 2)`` points around a circle (open — no closing duplicate).

    Args:
        radius: Circle radius.
        segments: Number of points on the ring.

    Returns:
        ``(segments, 2)`` array of ``(x, y)`` points, evenly spaced around the
        circle without a closing duplicate.
    """
    theta = np.linspace(0.0, 2.0 * np.pi, segments, endpoint=False)
    return np.column_stack([radius * np.cos(theta), radius * np.sin(theta)])


def _sweeps(section: Any, detail: str, circle_segments: int) -> list[tuple[np.ndarray, bool, bool]]:
    """The ``(points, closed, caps)`` sweeps for one section.

    ``"plates"`` produces the open plate decomposition (no caps); ``"solid"``
    produces the closed perimeter (with caps).  Each sweep's *points* are the
    cross-section coordinates in the local YZ plane, ``(P, 2)``.

    Args:
        section: The section to sweep.
        detail: ``"plates"`` (open plates, no caps) or ``"solid"`` (closed
            perimeter, with caps).
        circle_segments: Number of sides used to approximate a circular section.

    Returns:
        A list of ``(points, closed, caps)`` tuples, one per sweep.
    """
    if detail == "plates":
        polylines = section_plates(section, circle_segments=circle_segments)
        sweeps: list[tuple[np.ndarray, bool, bool]] = []
        for poly in polylines:
            pts = np.asarray(poly, dtype=float)
            if pts.shape[0] > 2:
                # An open tube (a circular section's perimeter) — closed loop, no caps.
                sweeps.append((pts, True, False))
            else:
                # A flat plate — one open edge.
                sweeps.append((pts, False, False))
        return sweeps

    outline = section_profile_outline(section)
    if outline is None:
        return []
    kind, value = outline
    if kind == "circle":
        return [(_circle_ring(float(value), circle_segments), True, True)]
    if isinstance(value, tuple):
        # Hollow section (e.g. a box): sweep the outer and inner boundaries as
        # two open tubes so the inner boundary stays a hole in the solid.  The
        # inner loop is reversed so its faces point into the void.
        outer, inner = value
        return [
            (np.asarray(outer, dtype=float), True, False),
            (np.asarray(inner, dtype=float)[::-1], True, False),
        ]
    return [(np.asarray(value, dtype=float), True, True)]


def _sweep_mesh(
    pts2d: np.ndarray,
    closed: bool,
    caps: bool,
    s0: np.ndarray,
    s1: np.ndarray,
    vy: np.ndarray,
    vz: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]:
    """Sweep one cross-section over ``M`` members.

    Args:
        pts2d: Cross-section coordinates in the local YZ plane, ``(P, 2)``.
        closed: Whether the cross-section is a closed loop (otherwise an open
            polyline is swept).
        caps: Whether to append end caps (only valid when *closed*).
        s0: Member start points, ``(M, 3)``.
        s1: Member end points, ``(M, 3)``.
        vy: Member local Y axes, ``(M, 3)``.
        vz: Member local Z axes, ``(M, 3)``.

    Returns:
        ``(points, quads, caps)`` — ``points`` is ``(K, 3)`` the combined
        vertex array, ``quads`` is ``(Q, 4)`` vertex indices into *points*, and
        ``caps`` is ``(2M, P)`` vertex indices (bottom then top ring) **into
        *points***, or ``None`` when no caps are produced.
    """
    P = pts2d.shape[0]
    M = s0.shape[0]

    if closed:
        i0 = np.arange(P)
        i1 = (i0 + 1) % P
    else:
        i0 = np.arange(P - 1)
        i1 = i0 + 1

    p0 = pts2d[i0]  # (E, 2)
    p1 = pts2d[i1]  # (E, 2)

    o0 = p0[:, 0:1, None] * vy[None, :, :] + p0[:, 1:2, None] * vz[None, :, :]
    o1 = p1[:, 0:1, None] * vy[None, :, :] + p1[:, 1:2, None] * vz[None, :, :]

    b0 = s0[None, :, :] + o0
    b1 = s0[None, :, :] + o1
    t0 = s1[None, :, :] + o0
    t1 = s1[None, :, :] + o1

    corners = np.stack([b0, b1, t1, t0], axis=2)  # (E, M, 4, 3)
    points = corners.reshape(-1, 3)  # (E*M*4, 3)
    base = np.arange(corners.shape[0] * corners.shape[1], dtype=np.int64) * 4
    quads = np.stack([base, base + 1, base + 2, base + 3], axis=1)  # (E*M, 4)

    caps_arr: Optional[np.ndarray] = None
    if caps and closed:
        o_all = pts2d[:, 0:1, None] * vy[None, :, :] + pts2d[:, 1:2, None] * vz[None, :, :]
        bottom = (s0[None, :, :] + o_all).reshape(P * M, 3)
        top = (s1[None, :, :] + o_all).reshape(P * M, 3)
        side_vertices = points.shape[0]
        points = np.concatenate([points, bottom, top], axis=0)
        ring = (np.arange(M)[None, :] + np.arange(P)[:, None] * M).T  # (M, P)
        # Offset both rings past the swept side vertices so they index the
        # appended cap vertices, and reverse the bottom ring (not the top) so
        # both caps wind outward.
        bottom_ring = ring[:, ::-1] + side_vertices
        top_ring = ring + side_vertices + P * M
        caps_arr = np.concatenate([bottom_ring, top_ring], axis=0)  # (2M, P)

    return points, quads, caps_arr


def build_extruded_frame_mesh(
    frames: list[Any],
    sections: dict[str, Any],
    colors: dict[str, tuple[float, float, float]],
    *,
    detail: str = "plates",
    circle_segments: int = 8,
    shrink: float = 1.0,
) -> ExtrudedMesh:
    """Build one combined mesh for every member's extruded section.

    Args:
        frames: The members to draw (each exposes ``start``, ``end``, ``section``
            and ``angle``).
        sections: ``{section_name: Section}`` — the model's sections.
        colors: ``{section_name: (r, g, b)}`` — RGB in 0..1.
        detail: ``"plates"`` (efficient, no caps) or ``"solid"`` (closed, caps).
        circle_segments: Sides used to approximate a circular section.
        shrink: Display shrink toward each member's midpoint (``1.0`` = true).

    Returns:
        An :class:`ExtrudedMesh`, or an empty mesh when nothing can be swept.
    """
    n = len(frames)
    empty = ExtrudedMesh(
        points=np.zeros((0, 3)),
        faces=np.zeros(0, dtype=np.int64),
        face_colors=np.zeros((0, 3)),
    )
    if n == 0:
        return empty

    starts = np.array([f.start for f in frames], dtype=float)
    ends = np.array([f.end for f in frames], dtype=float)
    angles = np.array([float(getattr(f, "angle", 0.0)) for f in frames], dtype=float)
    s0, s1 = _shrunk_segment_batch(starts, ends, shrink)
    _vx, vy, vz = local_axes_batch(s1 - s0, angles)

    groups: dict[str, list[int]] = {}
    for i, f in enumerate(frames):
        groups.setdefault(f.section, []).append(i)

    points_parts: list[np.ndarray] = []
    faces_parts: list[np.ndarray] = []
    color_parts: list[np.ndarray] = []
    vertex_offset = 0

    for sec_name, member_idx in groups.items():
        idxs = np.asarray(member_idx, dtype=int)
        section = sections.get(sec_name)
        if section is None:
            # A section referenced by a frame but absent from the model's section
            # dict has nothing to sweep — skip it so rendering continues.
            continue
        sweeps = _sweeps(section, detail, circle_segments)
        if not sweeps:
            continue
        rgb = colors.get(sec_name, (0.5, 0.5, 0.5))
        for pts2d, closed, caps in sweeps:
            points, quads, caps_arr = _sweep_mesh(
                pts2d, closed, caps, s0[idxs], s1[idxs], vy[idxs], vz[idxs]
            )
            if len(points) == 0:
                continue
            q = quads + vertex_offset
            faces: list[np.ndarray] = []
            n_faces = q.shape[0]
            if n_faces:
                quad_faces = np.empty((n_faces, 5), dtype=np.int64)
                quad_faces[:, 0] = 4
                quad_faces[:, 1:] = q
                faces.append(quad_faces)
            if caps_arr is not None and caps_arr.shape[0]:
                c = caps_arr + vertex_offset
                cap_faces = np.empty((c.shape[0], c.shape[1] + 1), dtype=np.int64)
                cap_faces[:, 0] = c.shape[1]
                cap_faces[:, 1:] = c
                faces.append(cap_faces)
                n_faces += c.shape[0]

            points_parts.append(points)
            faces_parts.append(np.concatenate([f.ravel() for f in faces]))
            color_parts.append(np.tile(rgb, (n_faces, 1)))
            vertex_offset += len(points)

    if not points_parts:
        return empty
    return ExtrudedMesh(
        points=np.concatenate(points_parts, axis=0),
        faces=np.concatenate(faces_parts, axis=0),
        face_colors=np.concatenate(color_parts, axis=0),
    )
