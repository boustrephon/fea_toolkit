"""Tests for the plotting/viz module — mesh construction helpers.

These tests exercise :func:`_build_deformed_mesh` which is the core
helper behind mode-shape animation and deformed-shape rendering.
"""

import numpy as np
import pytest

try:
    import pyvista as pv
    _has_pyvista = True
except ImportError:
    _has_pyvista = False


# ============================================================================
# _build_deformed_mesh
# ============================================================================

class TestBuildDeformedMesh:
    """Verify the shared deformed-mesh constructor."""

    @pytest.fixture(autouse=True)
    def _skip_if_no_pyvista(self):
        if not _has_pyvista:
            pytest.skip("pyvista not installed")

    # ── Fixtures ─────────────────────────────────────────────────────

    @pytest.fixture
    def sample_segments(self):
        """Two frame segments: a vertical column and a horizontal beam."""
        return [
            (np.array([0., 0., 0.]), np.array([0., 0., 3.]),
             np.array([0., 0., 0.]), np.array([0.1, 0., 0.])),   # column
            (np.array([0., 0., 3.]), np.array([4., 0., 3.]),
             np.array([0.1, 0., 0.]), np.array([0., 0., 0.])),   # beam
        ]

    @pytest.fixture
    def sample_seg_npoints(self):
        """Number of interpolation points per segment."""
        return [4, 4]

    @pytest.fixture
    def sample_quads(self):
        """Two shell quads and one triangle (4th vertex == 3rd)."""
        # Quad at z=0, no displacement
        q1 = (np.array([0., 0., 0.]), np.array([2., 0., 0.]),
              np.array([2., 2., 0.]), np.array([0., 2., 0.]),
              np.zeros(3), np.zeros(3), np.zeros(3), np.zeros(3))
        # Quad at z=3, small X displacement
        q2 = (np.array([0., 0., 3.]), np.array([2., 0., 3.]),
              np.array([2., 2., 3.]), np.array([0., 2., 3.]),
              np.array([0.05, 0., 0.]), np.array([0.05, 0., 0.]),
              np.array([0.05, 0., 0.]), np.array([0.05, 0., 0.]))
        # Triangle (4th vertex == 3rd)
        tri = (np.array([4., 0., 0.]), np.array([6., 0., 0.]),
               np.array([5., 2., 0.]), np.array([5., 2., 0.]),   # same as 3rd
               np.zeros(3), np.zeros(3), np.zeros(3), np.zeros(3))
        return [q1, q2, tri]

    # ── Tests ───────────────────────────────────────────────────────

    def test_import(self):
        """Verify the function can be imported and is callable."""
        from fea_toolkit.plotting.viz import _build_deformed_mesh
        assert callable(_build_deformed_mesh)

    def test_basic_structure(self, sample_segments, sample_seg_npoints,
                             sample_quads):
        """Return a (frame_mesh, shell_mesh) tuple with correct counts.

        With 2 frame segments (4 interpolation points each) and 3 shell
        quads (2 quads + 1 triangle), the frame mesh should have 6 line
        cells and 8 points; the shell mesh should have 3 face cells.
        """
        from fea_toolkit.plotting.viz import _build_deformed_mesh

        sec_idxs = [0, 0, 1]  # two quads section 0, triangle section 1
        fm, sm = _build_deformed_mesh(
            sample_segments, sample_seg_npoints, sample_quads, sec_idxs,
            scale=1.0, amp=1.0,
        )
        # 2 segments × 3 line segments each = 6 line cells
        assert fm.n_lines == 6
        # 4 + 4 interpolation points = 8 frame vertices
        assert fm.n_points == 8
        assert sm is not None
        assert sm.n_faces == 3  # 2 quads + 1 triangle

    def test_quad_is_quad_face(self, sample_segments, sample_seg_npoints,
                               sample_quads):
        """Each quad produces a single [4, i, j, k, l] face, not two tris.

        With 3 quads (5 values each) the faces array has 15 values,
        all starting with ``4``.  Triangles are rendered as degenerate
        quads (4th vertex == 3rd), so still use the quad format.
        """
        from fea_toolkit.plotting.viz import _build_deformed_mesh

        sec_idxs = [0, 0, 1]
        fm, sm = _build_deformed_mesh(
            sample_segments, sample_seg_npoints, sample_quads, sec_idxs,
            scale=1.0, amp=1.0,
        )
        faces = sm.faces
        # 3 quads × 5 values = 15
        assert len(faces) == 15, f"Expected 15 face values, got {len(faces)}"
        # All faces are quads (leading 4)
        assert faces[0] == 4
        assert faces[5] == 4
        assert faces[10] == 4

    def test_triangle_detected(self, sample_segments, sample_seg_npoints,
                               sample_quads):
        """A degenerate quad (4th vertex ≈ 3rd) still uses [4, i, j, k, l].

        PyVista 0.48 requires uniform face formats in a single array,
        so triangles are rendered as degenerate quads (4 vertices with
        the last two equal).  The face count is still 1.
        """
        from fea_toolkit.plotting.viz import _build_deformed_mesh

        tri_only = [sample_quads[2]]
        fm, sm = _build_deformed_mesh(
            sample_segments, sample_seg_npoints, tri_only, [0],
            scale=1.0, amp=1.0,
        )
        assert sm is not None
        assert sm.n_faces == 1
        # Still a quad face format (degenerate)
        assert sm.faces[0] == 4
        # Triangle data: 4 vertices with last two at same position
        assert sm.n_points == 4

    def test_amp_scales_displacement(self, sample_segments, sample_seg_npoints,
                                     sample_quads):
        """The *amp* parameter scales displacement vectors linearly.

        At ``amp=0`` all shell vertices should match their rest position;
        at ``amp=1`` the second quad's first vertex (rest x=0, dx=0.05)
        should appear at x=0.05.
        """
        from fea_toolkit.plotting.viz import _build_deformed_mesh

        sec_idxs = [0, 0, 1]
        fm0, sm0 = _build_deformed_mesh(
            sample_segments, sample_seg_npoints, sample_quads, sec_idxs,
            scale=1.0, amp=0.0,
        )
        fm1, sm1 = _build_deformed_mesh(
            sample_segments, sample_seg_npoints, sample_quads, sec_idxs,
            scale=1.0, amp=1.0,
        )
        # First quad has zero displacement — same position at any amp
        assert np.allclose(sm0.points[0], sm1.points[0])
        # Second quad vertex 0: p1=(0,0,3), d=(0.05,0,0)
        assert abs(sm0.points[4][0] - 0.0) < 1e-10    # undeformed
        assert abs(sm1.points[4][0] - 0.05) < 1e-10   # displaced

    def test_empty_segments(self, sample_quads):
        """Passing no frame segments yields a zero-point frame mesh.

        The shell mesh should still contain the 3 quads/triangles.
        """
        from fea_toolkit.plotting.viz import _build_deformed_mesh

        fm, sm = _build_deformed_mesh(
            [], [], sample_quads, [0, 0, 1],
            scale=1.0, amp=1.0,
        )
        assert fm.n_lines == 0
        assert fm.n_points == 0
        assert sm is not None
        assert sm.n_faces == 3

    def test_empty_quads(self, sample_segments, sample_seg_npoints):
        """Passing no shell quads yields ``None`` for the shell mesh.

        The frame mesh should still contain the 2 segments (6 lines).
        """
        from fea_toolkit.plotting.viz import _build_deformed_mesh

        fm, sm = _build_deformed_mesh(
            sample_segments, sample_seg_npoints, [], [],
            scale=1.0, amp=1.0,
        )
        assert fm.n_lines == 6
        assert sm is None

    def test_point_count_invariant(self, sample_segments, sample_seg_npoints,
                                   sample_quads):
        """Total point count (frame + shell) is identical at any *amp*.

        This invariant is critical for animation: the mesh topology
        (vertex count, line/face connectivity) must not change when
        only the displacement amplitude varies.
        """
        from fea_toolkit.plotting.viz import _build_deformed_mesh

        sec_idxs = [0, 0, 1]
        counts = []
        for amp in (0.0, 0.5, 1.0):
            fm, sm = _build_deformed_mesh(
                sample_segments, sample_seg_npoints, sample_quads, sec_idxs,
                scale=1.0, amp=amp,
            )
            counts.append(fm.n_points + sm.n_points)
        assert counts[0] == counts[1] == counts[2], (
            f"Point count changed with amp: {counts}")
