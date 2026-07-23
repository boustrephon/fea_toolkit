"""Tests for the plotting/viz module — mesh construction and displaced shape.

Exercises:
    - :func:`_build_deformed_mesh` — helper behind mode-shape animation
      and deformed-shape rendering.
    - :func:`_resolve_mesh_data` — mesh-geometry extraction for builders
      and NPZ dicts.
    - :func:`plot_deformed_displacement_3d` — unified displaced-shape viewer.
"""

import numpy as np
import pytest

try:
    import pyvista as pv
    _has_pyvista = True
except ImportError:
    _has_pyvista = False


# ============================================================================
# Fixtures — synthetic NPZ-like data dict
# ============================================================================

@pytest.fixture
def sample_npz_data():
    """Minimal dict mimicking an NPZ results file (simple 2-element frame)."""
    return {
        "node_tag": np.array([1, 2, 3]),
        "node_sap_id": np.array(["1", "2", "3"]),
        "node_x": np.array([0.0, 4.0, 4.0]),
        "node_y": np.array([0.0, 0.0, 0.0]),
        "node_z": np.array([0.0, 0.0, 3.0]),
        "frame_eid": np.array([1, 2]),
        "frame_sap_id": np.array(["1", "2"]),
        "frame_node_i": np.array([1, 2]),
        "frame_node_j": np.array([2, 3]),
        "frame_sec_name": np.array(["COL", "BEAM"]),
        "frame_parent_sap_id": np.array(["", ""]),
    }


@pytest.fixture
def sample_displacements():
    """Small static displacements for the 3-node model."""
    return {
        1: (0.0, 0.0, 0.0),
        2: (0.05, 0.0, 0.0),
        3: (0.08, 0.02, 0.0),
    }


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


# ============================================================================
# _resolve_mesh_data
# ============================================================================

class TestResolveMeshData:
    """Verify the mesh-data resolver works for dict (NPZ) sources."""

    @pytest.fixture(autouse=True)
    def _skip_if_no_pyvista(self):
        if not _has_pyvista:
            pytest.skip("pyvista not installed")

    def test_dict_source_nodes(self, sample_npz_data):
        """Resolving an NPZ-like dict produces correct node entries."""
        from fea_toolkit.plotting.viz import _resolve_mesh_data
        data = _resolve_mesh_data(sample_npz_data)
        assert len(data["nodes"]) == 3
        assert data["nodes"]["1"]["tag"] == 1
        assert data["nodes"]["1"]["x"] == 0.0
        assert data["nodes"]["2"]["x"] == 4.0
        assert data["nodes"]["3"]["z"] == 3.0

    def test_dict_source_frames(self, sample_npz_data):
        """Resolving an NPZ-like dict produces correct frame entries."""
        from fea_toolkit.plotting.viz import _resolve_mesh_data
        data = _resolve_mesh_data(sample_npz_data)
        assert len(data["frames"]) == 2
        # First frame: nodes 1→2 (column)
        fr0 = data["frames"][0]
        assert fr0["ni_tag"] == 1
        assert fr0["nj_tag"] == 2
        assert fr0["sec"] == "COL"
        # Second frame: nodes 2→3 (beam)
        fr1 = data["frames"][1]
        assert fr1["ni_tag"] == 2
        assert fr1["nj_tag"] == 3
        assert fr1["sec"] == "BEAM"

    def test_dict_source_orphan_nodes(self, sample_npz_data):
        """Dict source with no mesh model has empty orphan_nodes."""
        from fea_toolkit.plotting.viz import _resolve_mesh_data
        data = _resolve_mesh_data(sample_npz_data)
        assert data["orphan_nodes"] == {}
        assert data["edge_constraints"] == []
        assert data["mesh_node_ids"] == set()


# ============================================================================
# plot_deformed_displacement_3d — unified displaced shape
# ============================================================================

class TestPlotDeformedDisplacement3d:
    """Smoke tests for the unified displaced-shape viewer."""

    @pytest.fixture(autouse=True)
    def _skip_if_no_pyvista(self):
        if not _has_pyvista:
            pytest.skip("pyvista not installed")

    def test_import(self):
        """Verify function is exposed and callable."""
        from fea_toolkit.plotting import plot_deformed_displacement_3d
        assert callable(plot_deformed_displacement_3d)

    def test_no_displacements_returns_none(self, sample_npz_data):
        """Empty displacement dict should print and return None."""
        from fea_toolkit.plotting.viz import plot_deformed_displacement_3d
        result = plot_deformed_displacement_3d(
            sample_npz_data, {},
            notebook=False,
        )
        assert result is None

    def test_dict_source_notebook(self, sample_npz_data,
                                   sample_displacements):
        """Rendering with notebook=True does not open a window."""
        from fea_toolkit.plotting.viz import plot_deformed_displacement_3d
        pl = plot_deformed_displacement_3d(
            sample_npz_data, sample_displacements,
            scale=10.0, color_nodes=True, show_labels=True,
            show_bounds=False,
            notebook=True,
        )
        assert pl is not None
        pl.close()

    def test_selection_on_dict_source_ignored(self, sample_npz_data,
                                               sample_displacements):
        """Selection on a dict source prints a warning but does not crash."""
        from fea_toolkit.plotting.viz import plot_deformed_displacement_3d
        # Passing selection to a dict source — should warn and ignore
        pl = plot_deformed_displacement_3d(
            sample_npz_data, sample_displacements,
            scale=10.0, color_nodes=False, show_labels=False,
            show_bounds=False,
            selection="dummy",  # not a real Selection, but dict source ignores it
            notebook=True,
        )
        assert pl is not None
        pl.close()

    def test_screenshot_export(self, sample_npz_data, sample_displacements,
                                tmp_path):
        """Saving a screenshot to a temp path does not crash."""
        from fea_toolkit.plotting.viz import plot_deformed_displacement_3d
        png_path = str(tmp_path / "test_disp.png")
        pl = plot_deformed_displacement_3d(
            sample_npz_data, sample_displacements,
            scale=10.0, color_nodes=True, show_labels=False,
            show_bounds=False,
            save_screenshot=png_path,
            notebook=True,
        )
        assert pl is not None
        pl.close()

    def test_notebook_mode_returns_plotter(self, sample_npz_data,
                                            sample_displacements):
        """With notebook=True, the plotter object is returned."""
        from fea_toolkit.plotting.viz import plot_deformed_displacement_3d
        result = plot_deformed_displacement_3d(
            sample_npz_data, sample_displacements,
            scale=10.0, color_nodes=False, show_labels=False,
            show_bounds=False,
            notebook=True,
        )
        # Notebook mode returns a pv.Plotter when pyvista is available
        import pyvista as pv
        assert isinstance(result, pv.Plotter)
        result.close()


# ============================================================================
# shrink parameter — frame/shell element gap
# ============================================================================

class TestShrinkParameter:
    """Verify ``shrink`` parameter works in all functions that support it."""

    @pytest.fixture(autouse=True)
    def _skip_if_no_pyvista(self):
        if not _has_pyvista:
            pytest.skip("pyvista not installed")

    def test_plot_mesh_shrink(self, sample_npz_data):
        """plot_mesh accepts shrink and returns a plotter."""
        from fea_toolkit.plotting import plot_mesh
        pl = plot_mesh(sample_npz_data, shrink=0.1, notebook=True)
        assert pl is not None
        pl.close()

    def test_deformed_displacement_shrink(self, sample_npz_data,
                                           sample_displacements):
        """plot_deformed_displacement_3d accepts shrink on deformed lines."""
        from fea_toolkit.plotting import plot_deformed_displacement_3d
        pl = plot_deformed_displacement_3d(
            sample_npz_data, sample_displacements,
            scale=10.0, shrink=0.1, show_undeformed=True,
            color_nodes=False, show_labels=False, show_bounds=False,
            notebook=True,
        )
        assert pl is not None
        pl.close()

    def test_mode_animation_shrink(self, sample_npz_data):
        """plot_mode_animation accepts shrink on segment endpoints.

        Uses fixture data (no real mode shapes) — only checks the
        shrink parameter is accepted without error.
        """
        from fea_toolkit.plotting import plot_mode_animation
        # Minimal mode shape data matching the 3-node fixture
        disp = {1: (0.0, 0.0, 0.0), 2: (0.1, 0.0, 0.0), 3: (0.2, 0.0, 0.0)}
        shapes = {0: disp}
        pl = plot_mode_animation(
            sample_npz_data, shapes, mode=0,
            scale=10.0, shrink=0.1, animate=False,
            notebook=True,
        )
        assert pl is not None
        pl.close()

    def test_force_diagram_no_shrink(self, sample_npz_data):
        """plot_force_diagram_3d does not accept shrink (flags unaffected).

        Confirms the function signature has no ``shrink`` parameter.
        """
        import inspect
        from fea_toolkit.plotting import plot_force_diagram_3d
        sig = inspect.signature(plot_force_diagram_3d)
        assert 'shrink' not in sig.parameters
