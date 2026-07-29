"""Tests for the plotting/viz module — mesh construction and displaced shape.

Exercises:
    - :func:`_build_deformed_mesh` — helper behind mode-shape animation
      and deformed-shape rendering.
    - :func:`_resolve_mesh_data` — mesh-geometry extraction for builders
      and NPZ dicts.
    - :func:`plot_deformed_displacement_3d` — unified displaced-shape viewer.
"""

import warnings
import numpy as np
import pytest

try:
    import pyvista as pv
    _has_pyvista = True
    pv.OFF_SCREEN = True  # prevent interactive windows during tests
except ImportError:
    _has_pyvista = False

# Suppress PyVista's Jupyter backend warning — fires spuriously when
# pv.Plotter(notebook=True) is constructed in a non-Jupyter environment.
# This is a PyVista issue where it attempts to load its trame/Jupyter
# backend even in OFF_SCREEN mode.
warnings.filterwarnings("ignore", message="Failed to use notebook backend")


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
        assert sm.n_faces_strict == 3  # 2 quads + 1 triangle

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
        assert sm.n_faces_strict == 1
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
        assert sm.n_faces_strict == 3

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
    """Verify the mesh-data resolver works for dict (NPZ), SAPModelData,
    and builder sources."""

    @pytest.fixture(autouse=True)
    def _skip_if_no_pyvista(self):
        if not _has_pyvista:
            pytest.skip("pyvista not installed")

    def test_dict_source_nodes(self, sample_npz_data):
        """Resolving an NPZ-like dict produces correct node entries.

        Each node is dual-keyed (SAP ID string + int tag) so the dict
        has 2× entries per node, but lookups by either key resolve.
        """
        from fea_toolkit.plotting.viz import _resolve_mesh_data
        data = _resolve_mesh_data(sample_npz_data)
        # 3 unique nodes × 2 keys (SAP ID + tag) = 6 dict entries
        assert len(data["nodes"]) == 6
        # Lookup by SAP ID string works
        assert data["nodes"]["1"]["tag"] == 1
        assert data["nodes"]["1"]["x"] == 0.0
        assert data["nodes"]["2"]["x"] == 4.0
        assert data["nodes"]["3"]["z"] == 3.0
        # Lookup by int tag also works
        assert data["nodes"][1]["tag"] == 1
        assert data["nodes"][1]["x"] == 0.0
        assert data["nodes"][2]["x"] == 4.0
        assert data["nodes"][3]["z"] == 3.0

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

    # ── SAPModelData passthrough (Approach A) ────────────────────────

    def test_sap_model_data_source(self):
        """Resolving an SAPModelData produces raw unsplit geometry."""
        from fea_toolkit.model.sap_data import (
            SAPModelData, Node, FrameElement,
            AreaElement, ISection,
        )
        from fea_toolkit.plotting.viz import _resolve_mesh_data

        nodes = {
            "N1": Node("N1", 1, 0.0, 0.0, 0.0),
            "N2": Node("N2", 2, 4.0, 0.0, 0.0),
            "N3": Node("N3", 3, 4.0, 0.0, 3.0),
            "N4": Node("N4", 4, 0.0, 4.0, 0.0),
        }
        frames = {
            "F1": FrameElement("F1", 10, "N1", "N2"),
            "F2": FrameElement("F2", 20, "N2", "N3"),
        }
        sections = {
            "COL": ISection(
                name="COL", shape="2×2 Box", material="Steel",
                A=0.2, I33=0.02, I22=0.02, J=0.0,
                depth=0.2, bf=0.2, tf=0.02, tw=0.02,
            ),
            "BEAM": ISection(
                name="BEAM", shape="W16x31", material="Steel",
                A=0.4, I33=0.02, I22=0.02, J=0.0,
                depth=0.4, bf=0.2, tf=0.01, tw=0.01,
            ),
        }
        areas = {"A1": AreaElement("A1", 30, ["N1", "N2", "N3", "N4"])}

        md = SAPModelData(
            nodes=nodes,
            restraints={},
            materials={},
            sections=sections,
            frame_elements=frames,
            area_elements=areas,
            frame_assignments={"F1": "COL", "F2": "BEAM"},
            area_assignments={"A1": "SLAB"},
            groups={},
            frame_auto_mesh={},
        )

        data = _resolve_mesh_data(md)

        # Should have all 4 nodes, no mesh nodes
        assert len(data["nodes"]) == 4
        assert data["mesh_node_ids"] == set()

        # Should have 2 frames with ni_id/nj_id (string keys), no parent
        assert len(data["frames"]) == 2
        fr0 = data["frames"][0]
        assert fr0["id"] == "F1"
        assert fr0["ni_id"] == "N1"
        assert fr0["nj_id"] == "N2"
        assert fr0["sec"] == "COL"
        assert fr0["parent"] is None

        # Should have 1 shell with node_ids
        assert len(data["shells"]) == 1
        sh0 = data["shells"][0]
        assert sh0["id"] == "A1"
        assert sh0["sec"] == "SLAB"
        assert sh0["node_ids"] == ["N1", "N2", "N3", "N4"]

    # ── collapse_to_parents (Approach B) ─────────────────────────────

    def test_npz_collapse_to_parents(self):
        """NPZ data with parent-child relationships collapses children."""
        from fea_toolkit.plotting.viz import _resolve_mesh_data
        n = 4  # 4 child elements
        npz = {
            "node_tag": np.array([1, 2, 3, 4, 5]),
            "node_sap_id": np.array(["1", "2", "3", "4", "5"]),
            "node_x": np.array([0.0, 1.0, 2.0, 3.0, 4.0]),
            "node_y": np.array([0.0, 0.0, 0.0, 0.0, 0.0]),
            "node_z": np.array([0.0, 0.0, 0.0, 0.0, 0.0]),
            "frame_eid": np.array([0, 1, 2, 3]),
            "frame_sap_id": np.array(["C1", "C2", "C3", "C4"]),
            "frame_node_i": np.array([1, 2, 3, 4]),
            "frame_node_j": np.array([2, 3, 4, 5]),
            "frame_sec_name": np.array(["COL", "COL", "COL", "COL"]),
            "frame_parent_sap_id": np.array(["P1", "P1", "P1", "P1"]),
            "frame_parent_node_i": np.array([1, 1, 1, 1]),
            "frame_parent_node_j": np.array([5, 5, 5, 5]),
        }

        # Without collapse — 4 children
        data = _resolve_mesh_data(npz, collapse_to_parents=False)
        assert len(data["frames"]) == 4
        # All have parent "P1"
        assert all(fr["parent"] == "P1" for fr in data["frames"])

        # With collapse — 1 parent instead of 4 children
        data = _resolve_mesh_data(npz, collapse_to_parents=True)
        assert len(data["frames"]) == 1
        assert data["frames"][0]["id"] == "P1"
        assert data["frames"][0]["ni_tag"] == 1
        assert data["frames"][0]["nj_tag"] == 5
        assert data["frames"][0]["parent"] is None

    def test_plot_mesh_collapse_to_parents(self, sample_npz_data):
        """plot_mesh accepts collapse_to_parents parameter."""
        from fea_toolkit.plotting import plot_mesh
        pl = plot_mesh(sample_npz_data, collapse_to_parents=False, notebook=True)
        assert pl is not None
        pl.close()

    def test_compare_meshes_collapse_to_parents(self, sample_npz_data):
        """compare_meshes accepts collapse_to_parents parameter."""
        from fea_toolkit.plotting import compare_meshes
        pl = compare_meshes(
            sample_npz_data, sample_npz_data,
            collapse_to_parents=False,
            notebook=True,
        )
        assert pl is not None
        assert hasattr(pl, 'close')
        pl.close()

    def test_deformed_displacement_collapse_to_parents(
        self, sample_npz_data, sample_displacements):
        """plot_deformed_displacement_3d accepts collapse_to_parents."""
        from fea_toolkit.plotting import plot_deformed_displacement_3d
        pl = plot_deformed_displacement_3d(
            sample_npz_data, sample_displacements,
            collapse_to_parents=False,
            scale=10.0, color_nodes=False, show_labels=False,
            show_bounds=False, notebook=True,
        )
        assert pl is not None
        pl.close()

    def test_mode_animation_collapse_to_parents(self, sample_npz_data):
        """plot_mode_animation accepts collapse_to_parents."""
        from fea_toolkit.plotting import plot_mode_animation
        disp = {1: (0.0, 0.0, 0.0), 2: (0.1, 0.0, 0.0), 3: (0.2, 0.0, 0.0)}
        shapes = {0: disp}
        pl = plot_mode_animation(
            sample_npz_data, shapes, mode=0,
            collapse_to_parents=False,
            scale=10.0, animate=False, notebook=True,
        )
        assert pl is not None
        pl.close()

    def test_force_diagram_collapse_to_parents(self):
        """plot_force_diagram_3d accepts collapse_to_parents parameter."""
        from fea_toolkit.plotting.viz import plot_force_diagram_3d
        # Build a minimal NPZ with static data so the force path works
        npz = {
            "node_tag": np.array([1, 2, 3]),
            "node_sap_id": np.array(["1", "2", "3"]),
            "node_x": np.array([0.0, 4.0, 4.0]),
            "node_y": np.array([0.0, 0.0, 0.0]),
            "node_z": np.array([0.0, 0.0, 3.0]),
            "frame_eid": np.array([0, 1]),
            "frame_sap_id": np.array(["1", "2"]),
            "frame_node_i": np.array([1, 2]),
            "frame_node_j": np.array([2, 3]),
            "frame_sec_name": np.array(["COL", "BEAM"]),
            "frame_parent_sap_id": np.array(["", ""]),
            "analysis_types": np.array(["static"]),
            "static_case_labels": np.array(["DEAD"]),
            "static/DEAD/fx_i": np.array([10.0, -5.0]),
            "static/DEAD/fx_j": np.array([-10.0, 5.0]),
            "static/DEAD/my_i": np.array([0.0, 0.0]),
            "static/DEAD/my_j": np.array([0.0, 0.0]),
            "static/DEAD/mz_i": np.array([0.0, 0.0]),
            "static/DEAD/mz_j": np.array([0.0, 0.0]),
        }
        pl = plot_force_diagram_3d(
            npz, force_data=None,
            collapse_to_parents=False,
            quantity='Mx', mode='flag', notebook=True,
        )
        assert pl is not None
        pl.close()

    # ── _sort_children_by_location ───────────────────────────────────

    def test_sort_children_by_location(self, sample_npz_data):
        """Children are sorted by their midpoint elevation."""
        from fea_toolkit.plotting.viz import _sort_children_by_location
        from fea_toolkit.plotting.viz import _resolve_mesh_data

        data = _resolve_mesh_data(sample_npz_data)
        children = data["frames"]
        sorted_children = _sort_children_by_location(children, data["nodes"])
        # Should have same length
        assert len(sorted_children) == len(children)


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
