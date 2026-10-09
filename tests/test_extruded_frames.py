"""The vectorised extruded-frame mesh builder (no PyVista needed).

Covers :func:`fea_toolkit.plotting.extruded_frames.local_axes_batch` (parity with
the model's per-vector ``get_local_axes``) and
:func:`fea_toolkit.plotting.extruded_frames.build_extruded_frame_mesh` for both
the ``"plates"`` and ``"solid"`` detail levels.
"""

import numpy as np
import pytest

from fea_toolkit.model.sap_data import (
    BoxSection,
    ChannelSection,
    CircularSection,
    ISection,
    PipeSection,
    RectangularSection,
)
from fea_toolkit.plotting.extruded_frames import (
    build_extruded_frame_mesh,
    local_axes_batch,
)
from fea_toolkit.plotting.renderers.base import FrameGeom


def _frame(eid, section, start, end, angle=0.0):
    return FrameGeom(eid, section, "a", "b", np.array(start, float), np.array(end, float), angle)


_SECTIONS = {
    "I": ISection("I", "I/Wide Flange", "S", depth=0.3, bf=0.15, tf=0.01, tw=0.006),
    "R": RectangularSection("R", "Rectangular", "C", depth=0.4, bf=0.2),
    "C": CircularSection("C", "Circle", "C", diameter=0.3),
    "P": PipeSection("P", "Pipe", "S", od=0.2, t=0.01),
    "B": BoxSection("B", "Box", "S", depth=0.3, bf=0.2, tf=0.01, tw=0.006),
    "CH": ChannelSection("CH", "Channel", "S", depth=0.3, bf=0.15, tf=0.01, tw=0.006),
}
_COLORS = dict.fromkeys(_SECTIONS, (0.5, 0.5, 0.5))


def _face_count(mesh) -> int:
    """Walk the flat VTK faces array and count the faces."""
    faces = mesh.faces
    count = 0
    i = 0
    while i < len(faces):
        count += 1
        i += 1 + int(faces[i])
    assert i == len(faces)
    return count


def test_local_axes_batch_matches_get_local_axes():
    """One numpy pass reproduces the per-vector reference, verticals included."""
    from fea_toolkit.model.geometry import get_local_axes

    rng = np.random.default_rng(0)
    axes = rng.normal(size=(20, 3))
    axes[0] = [0.0, 0.0, 1.0]  # vertical up
    axes[1] = [0.0, 0.0, -1.0]  # vertical down
    axes[2] = [1.0, 0.0, 0.0]
    angles = rng.uniform(0.0, 360.0, size=20)
    angles[0] = 0.0
    angles[1] = 30.0
    angles[2] = 45.0

    vx, vy, vz = local_axes_batch(axes, angles)
    for i in range(len(axes)):
        rx, ry, rz = get_local_axes(axes[i], float(angles[i]))
        np.testing.assert_allclose(vx[i], rx, atol=1e-10)
        np.testing.assert_allclose(vy[i], ry, atol=1e-10)
        np.testing.assert_allclose(vz[i], rz, atol=1e-10)


def test_empty_frames_returns_an_empty_mesh():
    mesh = build_extruded_frame_mesh([], _SECTIONS, _COLORS)
    assert mesh.points.size == 0
    assert mesh.faces.size == 0
    assert mesh.face_colors.size == 0


@pytest.mark.parametrize(
    "detail, expected_faces",
    [
        ("plates", 30),  # I=3, R=4, C=8, P=8, B=4, CH=3
        ("solid", 66),  # I=14, R=6, C=10, P=10, B=16, CH=10
    ],
)
def test_face_counts_per_detail(detail, expected_faces):
    frames = [
        _frame("1", "I", (0, 0, 0), (1, 0, 0)),
        _frame("2", "R", (0, 0, 0), (0, 1, 0)),
        _frame("3", "C", (0, 0, 0), (0, 0, 1)),
        _frame("4", "P", (0, 0, 0), (0, 0, 1)),
        _frame("5", "B", (0, 0, 0), (1, 0, 0)),
        _frame("6", "CH", (0, 0, 0), (1, 0, 0)),
    ]
    mesh = build_extruded_frame_mesh(frames, _SECTIONS, _COLORS, detail=detail)
    assert _face_count(mesh) == expected_faces
    assert len(mesh.face_colors) == expected_faces


def test_shrink_shortens_the_extruded_axis():
    frames = [_frame("1", "R", (0, 0, 0), (2, 0, 0))]
    full = build_extruded_frame_mesh(frames, _SECTIONS, _COLORS)
    shrunk = build_extruded_frame_mesh(frames, _SECTIONS, _COLORS, shrink=0.5)

    full_span = full.points[:, 0].max() - full.points[:, 0].min()
    shrunk_span = shrunk.points[:, 0].max() - shrunk.points[:, 0].min()
    assert shrunk_span < full_span
    # A 0.5 shrink draws the member at half length (the section width still
    # adds its half-depth on each end, so the span is less than the full one
    # but not exactly half).
    assert shrunk_span > 0.0


def test_face_colors_are_per_face_and_match_section():
    frames = [
        _frame("1", "R", (0, 0, 0), (1, 0, 0)),
        _frame("2", "C", (0, 0, 0), (0, 0, 1)),
    ]
    colors = {"R": (1.0, 0.0, 0.0), "C": (0.0, 0.0, 1.0)}
    mesh = build_extruded_frame_mesh(frames, _SECTIONS, colors)
    n_faces = _face_count(mesh)
    assert len(mesh.face_colors) == n_faces
    # The first faces belong to R (red), the rest to C (blue).
    assert np.all(mesh.face_colors[0] == [1.0, 0.0, 0.0])
    assert np.all(mesh.face_colors[-1] == [0.0, 0.0, 1.0])


def test_sweep_mesh_caps_reference_appended_vertices():
    """Cap indices offset past the side vertices, for grouped members."""
    from fea_toolkit.plotting.extruded_frames import _sweep_mesh

    pts2d = np.array([[-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0]])
    s0 = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 2.0]])  # two grouped members
    s1 = np.array([[1.0, 0.0, 0.0], [1.0, 0.0, 2.0]])
    vy = np.array([[0.0, 1.0, 0.0], [0.0, 1.0, 0.0]])
    vz = np.array([[0.0, 0.0, 1.0], [0.0, 0.0, 1.0]])

    points, _quads, caps = _sweep_mesh(pts2d, closed=True, caps=True, s0=s0, s1=s1, vy=vy, vz=vz)

    P = pts2d.shape[0]
    M = s0.shape[0]
    side_vertices = P * M * 4  # E == P for a closed loop
    assert caps is not None
    assert caps.shape == (2 * M, P)

    # Cap indices must point past the swept side vertices into the appended caps.
    assert caps.min() == side_vertices
    assert caps.max() == len(points) - 1

    offsets = pts2d[:, 0:1] * np.array([0.0, 1.0, 0.0]) + pts2d[:, 1:2] * np.array([0.0, 0.0, 1.0])

    def _rows(arr):
        return {tuple(np.round(row, 9)) for row in arr}

    for m in range(M):
        assert _rows(points[caps[m]]) == _rows(s0[m] + offsets)  # bottom cap
        assert _rows(points[caps[m + M]]) == _rows(s1[m] + offsets)  # top cap


def test_sweep_mesh_caps_wind_outward_for_both_orientations():
    """CCW and CW profiles both produce outward-facing cap normals."""
    from fea_toolkit.plotting.extruded_frames import _sweep_mesh

    s0 = np.array([[0.0, 0.0, 0.0]])
    s1 = np.array([[1.0, 0.0, 0.0]])  # member along +X
    vy = np.array([[0.0, 1.0, 0.0]])
    vz = np.array([[0.0, 0.0, 1.0]])

    ccw = np.array([[-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0]])
    cw = ccw[::-1].copy()

    def _normal_x(ring):
        n = np.zeros(3)
        for a, b in zip(ring, np.roll(ring, -1, axis=0)):
            n += np.cross(a, b)
        return n[0]

    for pts2d in (ccw, cw):
        points, _quads, caps = _sweep_mesh(
            pts2d, closed=True, caps=True, s0=s0, s1=s1, vy=vy, vz=vz
        )
        assert caps is not None
        # Bottom cap points -X (outward), top cap +X (outward).
        assert _normal_x(points[caps[0]]) < 0
        assert _normal_x(points[caps[1]]) > 0


def test_sweep_mesh_hollow_box_winds_outward_for_both_orientations():
    """CCW and CW box profiles both get outward caps and void-facing inner tubes."""
    from fea_toolkit.plotting.extruded_frames import _sweep_mesh

    s0 = np.array([[0.0, 0.0, 0.0]])
    s1 = np.array([[1.0, 0.0, 0.0]])  # member along +X
    vy = np.array([[0.0, 1.0, 0.0]])
    vz = np.array([[0.0, 0.0, 1.0]])

    outer = np.array([[-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0]])
    inner = np.array([[-0.5, -0.5], [0.5, -0.5], [0.5, 0.5], [-0.5, 0.5]])

    def _normal_x(ring):
        n = np.zeros(3)
        for a, b in zip(ring, np.roll(ring, -1, axis=0)):
            n += np.cross(a, b)
        return n[0]

    def _face_normal(face):
        n = np.zeros(3)
        for a, b in zip(face, np.roll(face, -1, axis=0)):
            n += np.cross(a, b)
        return n

    for o, i in ((outer, inner), (outer[::-1], inner[::-1])):
        points, quads, caps = _sweep_mesh(
            o, closed=True, caps=True, s0=s0, s1=s1, vy=vy, vz=vz, inner=i
        )
        assert caps is not None
        assert caps.shape == (8, 4)  # 2 ends × 4 annular quads
        # Bottom cap points -X (outward), top cap +X (outward).
        assert _normal_x(points[caps[0]]) < 0
        assert _normal_x(points[caps[4]]) > 0
        # The inner tube (the last four side quads) faces into the void, toward
        # the box centre at the local origin.
        for q in quads[4:]:
            face = points[q]
            assert np.dot(_face_normal(face), -face.mean(axis=0)) > 0


def test_sweep_mesh_hollow_mismatched_loops_skip_caps():
    """An inner loop with a different vertex count is swept without caps."""
    from fea_toolkit.plotting.extruded_frames import _sweep_mesh

    s0 = np.array([[0.0, 0.0, 0.0]])
    s1 = np.array([[1.0, 0.0, 0.0]])
    vy = np.array([[0.0, 1.0, 0.0]])
    vz = np.array([[0.0, 0.0, 1.0]])

    outer = np.array([[-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0]])
    inner = np.array([[-0.5, -0.5], [0.5, -0.5], [0.5, 0.0], [0.0, 0.5], [-0.5, 0.5]])

    points, quads, caps = _sweep_mesh(
        outer, closed=True, caps=True, s0=s0, s1=s1, vy=vy, vz=vz, inner=inner
    )
    assert caps is None
    # Outer tube (4 quads) plus inner tube (5 quads), no cap quads appended.
    assert quads.shape == (9, 4)
    assert points.shape[0] == 9 * 4
