"""Tests for the plotting/viz module — mesh construction and displaced shape.

Exercises:
    - :func:`_build_deformed_mesh` — helper behind mode-shape animation
      and deformed-shape rendering.
    - :func:`_resolve_mesh_data` — mesh-geometry extraction for builders
      and NPZ dicts.
    - :func:`plot_deformed_displacement_3d` — unified displaced-shape viewer.
"""

import warnings

import matplotlib
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


@pytest.fixture(autouse=True, scope="module")
def _configure_matplotlib():
    """Set the Agg backend once per module and close all figures on exit."""
    matplotlib.use("Agg")
    yield
    import matplotlib.pyplot as plt

    plt.close("all")


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
            (
                np.array([0.0, 0.0, 0.0]),
                np.array([0.0, 0.0, 3.0]),
                np.array([0.0, 0.0, 0.0]),
                np.array([0.1, 0.0, 0.0]),
            ),  # column
            (
                np.array([0.0, 0.0, 3.0]),
                np.array([4.0, 0.0, 3.0]),
                np.array([0.1, 0.0, 0.0]),
                np.array([0.0, 0.0, 0.0]),
            ),  # beam
        ]

    @pytest.fixture
    def sample_seg_npoints(self):
        """Number of interpolation points per segment."""
        return [4, 4]

    @pytest.fixture
    def sample_quads(self):
        """Two shell quads and one triangle (4th vertex == 3rd)."""
        # Quad at z=0, no displacement
        q1 = (
            np.array([0.0, 0.0, 0.0]),
            np.array([2.0, 0.0, 0.0]),
            np.array([2.0, 2.0, 0.0]),
            np.array([0.0, 2.0, 0.0]),
            np.zeros(3),
            np.zeros(3),
            np.zeros(3),
            np.zeros(3),
        )
        # Quad at z=3, small X displacement
        q2 = (
            np.array([0.0, 0.0, 3.0]),
            np.array([2.0, 0.0, 3.0]),
            np.array([2.0, 2.0, 3.0]),
            np.array([0.0, 2.0, 3.0]),
            np.array([0.05, 0.0, 0.0]),
            np.array([0.05, 0.0, 0.0]),
            np.array([0.05, 0.0, 0.0]),
            np.array([0.05, 0.0, 0.0]),
        )
        # Triangle (4th vertex == 3rd)
        tri = (
            np.array([4.0, 0.0, 0.0]),
            np.array([6.0, 0.0, 0.0]),
            np.array([5.0, 2.0, 0.0]),
            np.array([5.0, 2.0, 0.0]),  # same as 3rd
            np.zeros(3),
            np.zeros(3),
            np.zeros(3),
            np.zeros(3),
        )
        return [q1, q2, tri]

    # ── Tests ───────────────────────────────────────────────────────

    def test_import(self):
        """Verify the function can be imported and is callable."""
        from fea_toolkit.plotting.viz import _build_deformed_mesh

        assert callable(_build_deformed_mesh)

    def test_basic_structure(self, sample_segments, sample_seg_npoints, sample_quads):
        """Return a (frame_mesh, shell_mesh) tuple with correct counts.

        With 2 frame segments (4 interpolation points each) and 3 shell
        quads (2 quads + 1 triangle), the frame mesh should have 6 line
        cells and 8 points; the shell mesh should have 3 face cells.
        """
        from fea_toolkit.plotting.viz import _build_deformed_mesh

        sec_idxs = [0, 0, 1]  # two quads section 0, triangle section 1
        fm, sm = _build_deformed_mesh(
            sample_segments,
            sample_seg_npoints,
            sample_quads,
            sec_idxs,
            scale=1.0,
            amp=1.0,
        )
        # 2 segments × 3 line segments each = 6 line cells
        assert fm.n_lines == 6
        # 4 + 4 interpolation points = 8 frame vertices
        assert fm.n_points == 8
        assert sm is not None
        assert sm.n_cells == 3  # 2 quads + 1 triangle

    def test_quad_is_quad_face(self, sample_segments, sample_seg_npoints, sample_quads):
        """Each quad produces a single [4, i, j, k, l] face, not two tris.

        With 3 quads (5 values each) the faces array has 15 values,
        all starting with ``4``.  Triangles are rendered as degenerate
        quads (4th vertex == 3rd), so still use the quad format.
        """
        from fea_toolkit.plotting.viz import _build_deformed_mesh

        sec_idxs = [0, 0, 1]
        _fm, sm = _build_deformed_mesh(
            sample_segments,
            sample_seg_npoints,
            sample_quads,
            sec_idxs,
            scale=1.0,
            amp=1.0,
        )
        faces = sm.faces
        # 3 quads × 5 values = 15
        assert len(faces) == 15, f"Expected 15 face values, got {len(faces)}"
        # All faces are quads (leading 4)
        assert faces[0] == 4
        assert faces[5] == 4
        assert faces[10] == 4

    def test_triangle_detected(self, sample_segments, sample_seg_npoints, sample_quads):
        """A degenerate quad (4th vertex ≈ 3rd) still uses [4, i, j, k, l].

        PyVista 0.48 requires uniform face formats in a single array,
        so triangles are rendered as degenerate quads (4 vertices with
        the last two equal).  The face count is still 1.
        """
        from fea_toolkit.plotting.viz import _build_deformed_mesh

        tri_only = [sample_quads[2]]
        _fm, sm = _build_deformed_mesh(
            sample_segments,
            sample_seg_npoints,
            tri_only,
            [0],
            scale=1.0,
            amp=1.0,
        )
        assert sm is not None
        assert sm.n_cells == 1
        # Still a quad face format (degenerate)
        assert sm.faces[0] == 4
        # Triangle data: 4 vertices with last two at same position
        assert sm.n_points == 4

    def test_amp_scales_displacement(self, sample_segments, sample_seg_npoints, sample_quads):
        """The *amp* parameter scales displacement vectors linearly.

        At ``amp=0`` all shell vertices should match their rest position;
        at ``amp=1`` the second quad's first vertex (rest x=0, dx=0.05)
        should appear at x=0.05.
        """
        from fea_toolkit.plotting.viz import _build_deformed_mesh

        sec_idxs = [0, 0, 1]
        _fm0, sm0 = _build_deformed_mesh(
            sample_segments,
            sample_seg_npoints,
            sample_quads,
            sec_idxs,
            scale=1.0,
            amp=0.0,
        )
        _fm1, sm1 = _build_deformed_mesh(
            sample_segments,
            sample_seg_npoints,
            sample_quads,
            sec_idxs,
            scale=1.0,
            amp=1.0,
        )
        # First quad has zero displacement — same position at any amp
        assert np.allclose(sm0.points[0], sm1.points[0])
        # Second quad vertex 0: p1=(0,0,3), d=(0.05,0,0)
        assert abs(sm0.points[4][0] - 0.0) < 1e-10  # undeformed
        assert abs(sm1.points[4][0] - 0.05) < 1e-10  # displaced

    def test_empty_segments(self, sample_quads):
        """Passing no frame segments yields a zero-point frame mesh.

        The shell mesh should still contain the 3 quads/triangles.
        """
        from fea_toolkit.plotting.viz import _build_deformed_mesh

        fm, sm = _build_deformed_mesh(
            [],
            [],
            sample_quads,
            [0, 0, 1],
            scale=1.0,
            amp=1.0,
        )
        assert fm.n_lines == 0
        assert fm.n_points == 0
        assert sm is not None
        assert sm.n_cells == 3

    def test_empty_quads(self, sample_segments, sample_seg_npoints):
        """Passing no shell quads yields ``None`` for the shell mesh.

        The frame mesh should still contain the 2 segments (6 lines).
        """
        from fea_toolkit.plotting.viz import _build_deformed_mesh

        fm, sm = _build_deformed_mesh(
            sample_segments,
            sample_seg_npoints,
            [],
            [],
            scale=1.0,
            amp=1.0,
        )
        assert fm.n_lines == 6
        assert sm is None

    def test_point_count_invariant(self, sample_segments, sample_seg_npoints, sample_quads):
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
                sample_segments,
                sample_seg_npoints,
                sample_quads,
                sec_idxs,
                scale=1.0,
                amp=amp,
            )
            counts.append(fm.n_points + sm.n_points)
        assert counts[0] == counts[1] == counts[2], f"Point count changed with amp: {counts}"


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
            AreaElement,
            FrameElement,
            ISection,
            Node,
            SAPModelData,
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
                name="COL",
                shape="2×2 Box",
                material="Steel",
                A=0.2,
                I33=0.02,
                I22=0.02,
                J=0.0,
                depth=0.2,
                bf=0.2,
                tf=0.02,
                tw=0.02,
            ),
            "BEAM": ISection(
                name="BEAM",
                shape="W16x31",
                material="Steel",
                A=0.4,
                I33=0.02,
                I22=0.02,
                J=0.0,
                depth=0.4,
                bf=0.2,
                tf=0.01,
                tw=0.01,
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
            sample_npz_data,
            sample_npz_data,
            collapse_to_parents=False,
            notebook=True,
        )
        assert pl is not None
        assert hasattr(pl, "close")
        pl.close()

    def test_deformed_displacement_collapse_to_parents(self, sample_npz_data, sample_displacements):
        """plot_deformed_displacement_3d accepts collapse_to_parents."""
        from fea_toolkit.plotting import plot_deformed_displacement_3d

        pl = plot_deformed_displacement_3d(
            sample_npz_data,
            sample_displacements,
            collapse_to_parents=False,
            scale=10.0,
            color_nodes=False,
            show_labels=False,
            show_bounds=False,
            notebook=True,
        )
        assert pl is not None
        pl.close()

    def test_mode_animation_collapse_to_parents(self, sample_npz_data):
        """plot_mode_animation accepts collapse_to_parents."""
        from fea_toolkit.plotting import plot_mode_animation

        disp = {1: (0.0, 0.0, 0.0), 2: (0.1, 0.0, 0.0), 3: (0.2, 0.0, 0.0)}
        shapes = {0: disp}
        pl = plot_mode_animation(
            sample_npz_data,
            shapes,
            mode=0,
            collapse_to_parents=False,
            scale=10.0,
            animate=False,
            notebook=True,
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
            npz,
            force_data=None,
            collapse_to_parents=False,
            quantity="Mx",
            mode="flag",
            notebook=True,
        )
        assert pl is not None
        pl.close()

    # ── _sort_children_by_location ───────────────────────────────────

    def test_sort_children_by_location(self, sample_npz_data):
        """Children are sorted by their midpoint elevation."""
        from fea_toolkit.plotting.viz import _resolve_mesh_data, _sort_children_by_location

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
            sample_npz_data,
            {},
            notebook=False,
        )
        assert result is None

    def test_dict_source_notebook(self, sample_npz_data, sample_displacements):
        """Rendering with notebook=True does not open a window."""
        from fea_toolkit.plotting.viz import plot_deformed_displacement_3d

        pl = plot_deformed_displacement_3d(
            sample_npz_data,
            sample_displacements,
            scale=10.0,
            color_nodes=True,
            show_labels=True,
            show_bounds=False,
            notebook=True,
        )
        assert pl is not None
        pl.close()

    def test_selection_on_dict_source_ignored(self, sample_npz_data, sample_displacements):
        """Selection on a dict source prints a warning but does not crash."""
        from fea_toolkit.plotting.viz import plot_deformed_displacement_3d

        # Passing selection to a dict source — should warn and ignore
        pl = plot_deformed_displacement_3d(
            sample_npz_data,
            sample_displacements,
            scale=10.0,
            color_nodes=False,
            show_labels=False,
            show_bounds=False,
            selection="dummy",  # not a real Selection, but dict source ignores it
            notebook=True,
        )
        assert pl is not None
        pl.close()

    def test_screenshot_export(self, sample_npz_data, sample_displacements, tmp_path):
        """Saving a screenshot to a temp path does not crash."""
        from fea_toolkit.plotting.viz import plot_deformed_displacement_3d

        png_path = str(tmp_path / "test_disp.png")
        pl = plot_deformed_displacement_3d(
            sample_npz_data,
            sample_displacements,
            scale=10.0,
            color_nodes=True,
            show_labels=False,
            show_bounds=False,
            save_screenshot=png_path,
            notebook=True,
        )
        assert pl is not None
        pl.close()

    def test_notebook_mode_returns_plotter(self, sample_npz_data, sample_displacements):
        """With notebook=True, the plotter object is returned."""
        from fea_toolkit.plotting.viz import plot_deformed_displacement_3d

        result = plot_deformed_displacement_3d(
            sample_npz_data,
            sample_displacements,
            scale=10.0,
            color_nodes=False,
            show_labels=False,
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

    def test_deformed_displacement_shrink(self, sample_npz_data, sample_displacements):
        """plot_deformed_displacement_3d accepts shrink on deformed lines."""
        from fea_toolkit.plotting import plot_deformed_displacement_3d

        pl = plot_deformed_displacement_3d(
            sample_npz_data,
            sample_displacements,
            scale=10.0,
            shrink=0.1,
            show_undeformed=True,
            color_nodes=False,
            show_labels=False,
            show_bounds=False,
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
            sample_npz_data,
            shapes,
            mode=0,
            scale=10.0,
            shrink=0.1,
            animate=False,
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
        assert "shrink" not in sig.parameters


# ============================================================================
# Tests for pushover visualisation helpers
# ============================================================================


class TestPushoverHeatmap:
    """Tests for plot_plastic_hinge_heatmap (Phase 4a2)."""

    @pytest.fixture
    def raw_pushover_data(self):
        """Synthetic per-step pushover data for 3 elements × 5 steps.

        Element IDs: "1", "2", "3" with mid-heights 1.5, 4.5, 7.5 (m).
        Element 3 is NOT present in step 3 (missing data → gray).
        """
        return [
            {
                "frame_forces": {
                    "1": {"mz_i": 10.0, "mz_j": -8.0},
                    "2": {"mz_i": 20.0, "mz_j": -15.0},
                    "3": {"mz_i": 5.0, "mz_j": -6.0},
                },
                "node_coords": {"1": (0, 0, 0), "2": (4, 0, 3), "3": (4, 0, 6), "4": (4, 0, 9)},
            },
            {
                "frame_forces": {
                    "1": {"mz_i": 15.0, "mz_j": -12.0},
                    "2": {"mz_i": 28.0, "mz_j": -20.0},
                    "3": {"mz_i": 8.0, "mz_j": -9.0},
                },
                "node_coords": {"1": (0, 0, 0), "2": (4, 0, 3), "3": (4, 0, 6), "4": (4, 0, 9)},
            },
            {
                "frame_forces": {
                    "1": {"mz_i": 18.0, "mz_j": -14.0},
                    "2": {"mz_i": 25.0, "mz_j": -18.0},
                    "3": {"mz_i": 6.0, "mz_j": -7.0},
                },
                "node_coords": {"1": (0, 0, 0), "2": (4, 0, 3), "3": (4, 0, 6), "4": (4, 0, 9)},
            },
            {
                "frame_forces": {
                    "1": {"mz_i": 22.0, "mz_j": -16.0},
                    "2": {"mz_i": 20.0, "mz_j": -10.0},
                    # Element 3 MISSING — no data for this step
                },
                "node_coords": {"1": (0, 0, 0), "2": (4, 0, 3), "3": (4, 0, 6), "4": (4, 0, 9)},
            },
            {
                "frame_forces": {
                    "1": {"mz_i": 30.0, "mz_j": -25.0},
                    "2": {"mz_i": 18.0, "mz_j": -12.0},
                    "3": {"mz_i": 12.0, "mz_j": -10.0},
                },
                "node_coords": {"1": (0, 0, 0), "2": (4, 0, 3), "3": (4, 0, 6), "4": (4, 0, 9)},
            },
        ]

    def test_heatmap_basic(self, raw_pushover_data):
        """Heatmap returns a matplotlib Figure with expected shape."""
        import matplotlib.pyplot as plt

        from fea_toolkit.plotting.viz import plot_plastic_hinge_heatmap

        fig = plot_plastic_hinge_heatmap(
            raw_pushover_data,
            title="Test heatmap",
            figsize=(6, 4),
        )
        assert fig is not None, "Heatmap should return a Figure"
        assert fig.axes, "Figure should have at least one axis"

        # Verify title
        assert "Test heatmap" in fig.axes[0].get_title()

        # Verify axes labels
        assert fig.axes[0].get_xlabel() == "Push step"
        assert "elevation" in fig.axes[0].get_ylabel().lower()
        plt.close(fig)

    def test_heatmap_missing_data_gray(self, raw_pushover_data):
        """Element missing in a step should show as gray (no data)."""
        import matplotlib.pyplot as plt

        from fea_toolkit.plotting.viz import plot_plastic_hinge_heatmap

        fig = plot_plastic_hinge_heatmap(
            raw_pushover_data,
            figsize=(6, 4),
        )
        assert fig is not None

        # pcolormesh creates a QuadMesh — check it exists in collections
        assert len(fig.axes[0].collections) > 0, "QuadMesh should exist"

        # Check colorbar tick labels include "No data"
        # The colorbar is the last axis in the figure
        cbar_ax = fig.axes[-1]
        tick_labels = cbar_ax.get_yticklabels()
        tick_texts = [t.get_text() for t in tick_labels]
        assert any("No data" in txt for txt in tick_texts), (
            f"Expected 'No data' in colorbar ticks, got: {tick_texts}"
        )
        plt.close(fig)

    def test_heatmap_roof_drift_xaxis(self, raw_pushover_data):
        """Heatmap with drift-based X-axis."""
        import matplotlib.pyplot as plt

        from fea_toolkit.plotting.viz import plot_plastic_hinge_heatmap

        drifts = [0.1, 0.3, 0.6, 1.0, 1.8]  # 5 drift values
        fig = plot_plastic_hinge_heatmap(
            raw_pushover_data,
            xaxis="drift",
            drifts=drifts,
            figsize=(6, 4),
        )
        assert fig is not None
        assert "drift" in fig.axes[0].get_xlabel().lower()
        plt.close(fig)

    def test_heatmap_save_path(self, raw_pushover_data, tmp_path):
        """Heatmap saves to file when save_path is provided."""
        import os

        import matplotlib.pyplot as plt

        from fea_toolkit.plotting.viz import plot_plastic_hinge_heatmap

        save_path = str(tmp_path / "test_heatmap.png")
        fig = plot_plastic_hinge_heatmap(
            raw_pushover_data,
            save_path=save_path,
            figsize=(6, 4),
        )
        assert fig is not None
        # Check the file was created
        assert os.path.exists(save_path)
        plt.close(fig)

    def test_heatmap_no_data(self):
        """Heatmap with empty data returns None."""
        from fea_toolkit.plotting.viz import plot_plastic_hinge_heatmap

        fig = plot_plastic_hinge_heatmap([], figsize=(6, 4))
        assert fig is None


# ============================================================================
# Tests for shell damage map
# ============================================================================


class TestShellDamageMap:
    """Tests for plot_shell_damage_map (Phase 4b)."""

    @pytest.fixture(autouse=True)
    def _skip_if_no_pyvista(self):
        if not _has_pyvista:
            pytest.skip("pyvista not installed")

    @pytest.fixture
    def npz_with_shells(self):
        """NPZ dict with 2 frame elements + 2 shell elements, 3 push steps."""
        return {
            "node_tag": np.array([1, 2, 3, 4, 5]),
            "node_sap_id": np.array(["1", "2", "3", "4", "5"]),
            "node_x": np.array([0.0, 4.0, 4.0, 0.0, 4.0]),
            "node_y": np.array([0.0, 0.0, 0.0, 3.0, 3.0]),
            "node_z": np.array([0.0, 0.0, 3.0, 0.0, 3.0]),
            "frame_eid": np.array([0, 1]),
            "frame_sap_id": np.array(["F1", "F2"]),
            "frame_node_i": np.array([1, 2]),
            "frame_node_j": np.array([2, 3]),
            "frame_sec_name": np.array(["COL", "BEAM"]),
            "frame_parent_sap_id": np.array(["", ""]),
            "shell_eid": np.array([0, 1]),
            "shell_sap_id": np.array(["W1", "W2"]),
            "shell_sec_name": np.array(["WALL", "WALL"]),
            "shell_node_1": np.array([1, 2]),
            "shell_node_2": np.array([2, 5]),
            "shell_node_3": np.array([5, 3]),
            "shell_node_4": np.array([4, 4]),
            # Pushover data: 3 steps
            "pushover/+X/step": np.array([0, 1, 2]),
            "pushover/+X/shell_sap_id": np.array(["W1", "W2"]),
            "pushover/+X/shell_Nx": np.array(
                [
                    [100.0, 50.0],  # step 0
                    [200.0, 80.0],  # step 1
                    [300.0, 120.0],  # step 2
                ]
            ),
            "pushover/+X/shell_Ny": np.array(
                [
                    [50.0, 25.0],
                    [100.0, 40.0],
                    [150.0, 60.0],
                ]
            ),
            "pushover/+X/shell_Nxy": np.array(
                [
                    [10.0, 5.0],
                    [20.0, 8.0],
                    [30.0, 12.0],
                ]
            ),
            "pushover/+X/shell_Mx": np.array(
                [
                    [5.0, 3.0],
                    [10.0, 5.0],
                    [15.0, 8.0],
                ]
            ),
            "pushover/+X/shell_My": np.array(
                [
                    [3.0, 2.0],
                    [6.0, 3.0],
                    [9.0, 5.0],
                ]
            ),
            "pushover/+X/shell_Mxy": np.array(
                [
                    [1.0, 0.5],
                    [2.0, 1.0],
                    [3.0, 1.5],
                ]
            ),
            # Node displacements
            "pushover/+X/node_tag": np.array([1, 2, 3, 4, 5]),
            "pushover/+X/node_disp_x": np.array(
                [
                    [0.0, 0.01, 0.02, 0.0, 0.01],
                    [0.0, 0.02, 0.04, 0.0, 0.02],
                    [0.0, 0.03, 0.06, 0.0, 0.03],
                ]
            ),
            "pushover/+X/node_disp_y": np.zeros((3, 5)),
            "pushover/+X/node_disp_z": np.zeros((3, 5)),
            # Frame data for pushover (minimal — only needed for _resolve_pushover_data)
            "pushover/+X/frame_sap_id": np.array(["F1", "F2"]),
            "pushover/+X/frame_fx_i": np.zeros((3, 2)),
            "pushover/+X/frame_fy_i": np.zeros((3, 2)),
            "pushover/+X/frame_fz_i": np.zeros((3, 2)),
            "pushover/+X/frame_mx_i": np.zeros((3, 2)),
            "pushover/+X/frame_my_i": np.zeros((3, 2)),
            "pushover/+X/frame_mz_i": np.zeros((3, 2)),
            "pushover/+X/frame_fx_j": np.zeros((3, 2)),
            "pushover/+X/frame_fy_j": np.zeros((3, 2)),
            "pushover/+X/frame_fz_j": np.zeros((3, 2)),
            "pushover/+X/frame_mx_j": np.zeros((3, 2)),
            "pushover/+X/frame_my_j": np.zeros((3, 2)),
            "pushover/+X/frame_mz_j": np.zeros((3, 2)),
        }

    def test_shell_damage_computed_correctly(self, npz_with_shells):
        """_compute_shell_damage returns a dict with positive values."""
        from fea_toolkit.plotting.viz import _compute_shell_damage

        step0_shells = {
            "W1": {"Nx": 100.0, "Ny": 50.0, "Nxy": 10.0, "Mx": 5.0, "My": 3.0, "Mxy": 1.0}
        }
        indices = _compute_shell_damage(step0_shells)
        assert "W1" in indices
        D, m_mag = indices["W1"]
        assert D > 0
        assert m_mag >= 0

    def test_shell_damage_map_notebook(self, npz_with_shells):
        """plot_shell_damage_map with notebook=True returns plotter."""
        from fea_toolkit.plotting.viz import plot_shell_damage_map

        pl = plot_shell_damage_map(npz_with_shells, notebook=True)
        assert pl is not None
        pl.close()

    def test_shell_damage_map_no_shells(self):
        """With no shell data, function returns None."""
        from fea_toolkit.plotting.viz import plot_shell_damage_map

        # Provide 1 step with 0 shells, plus minimal geometry arrays
        empty_npz = {
            "pushover/+X/step": np.array([0]),
            "pushover/+X/shell_sap_id": np.array([]),
            "pushover/+X/shell_Nx": np.empty((1, 0)),
            "pushover/+X/shell_Ny": np.empty((1, 0)),
            "pushover/+X/shell_Nxy": np.empty((1, 0)),
            "pushover/+X/shell_Mx": np.empty((1, 0)),
            "pushover/+X/shell_My": np.empty((1, 0)),
            "pushover/+X/shell_Mxy": np.empty((1, 0)),
            # Node displacements (empty — no displacements recorded)
            "pushover/+X/node_tag": np.array([]),
            "pushover/+X/node_disp_x": np.empty((1, 0)),
            "pushover/+X/node_disp_y": np.empty((1, 0)),
            "pushover/+X/node_disp_z": np.empty((1, 0)),
            # Frame arrays needed to avoid early exit in _resolve_pushover_data
            "pushover/+X/frame_sap_id": np.array([]),
            # Also need geometry frame arrays for _resolve_pushover_data to not crash
            "frame_node_i": np.array([]),
            "frame_node_j": np.array([]),
            "frame_sap_id": np.array([]),
            "pushover/+X/frame_fx_i": np.empty((1, 0)),
            "pushover/+X/frame_fy_i": np.empty((1, 0)),
            "pushover/+X/frame_fz_i": np.empty((1, 0)),
            "pushover/+X/frame_mx_i": np.empty((1, 0)),
            "pushover/+X/frame_my_i": np.empty((1, 0)),
            "pushover/+X/frame_mz_i": np.empty((1, 0)),
            "pushover/+X/frame_fx_j": np.empty((1, 0)),
            "pushover/+X/frame_fy_j": np.empty((1, 0)),
            "pushover/+X/frame_fz_j": np.empty((1, 0)),
            "pushover/+X/frame_mx_j": np.empty((1, 0)),
            "pushover/+X/frame_my_j": np.empty((1, 0)),
            "pushover/+X/frame_mz_j": np.empty((1, 0)),
            "node_tag": np.array([]),
            "node_x": np.array([]),
            "node_y": np.array([]),
            "node_z": np.array([]),
        }
        pl = plot_shell_damage_map(empty_npz, notebook=True)
        assert pl is None

    def test_shell_damage_map_static_step(self, npz_with_shells):
        """Static step renders without slider."""
        from fea_toolkit.plotting.viz import plot_shell_damage_map

        pl = plot_shell_damage_map(npz_with_shells, step=1, notebook=True)
        assert pl is not None
        pl.close()


# ============================================================================
# Tests for pushover envelope
# ============================================================================


class TestPushoverEnvelope:
    """Tests for plot_pushover_envelope (Phase 4c)."""

    @pytest.fixture(autouse=True)
    def _skip_if_no_pyvista(self):
        if not _has_pyvista:
            pytest.skip("pyvista not installed")

    @pytest.fixture
    def npz_with_frames(self):
        """NPZ dict with 2 frame elements, 3 push steps, varying moments."""
        return {
            "node_tag": np.array([1, 2, 3]),
            "node_sap_id": np.array(["1", "2", "3"]),
            "node_x": np.array([0.0, 4.0, 8.0]),
            "node_y": np.array([0.0, 0.0, 0.0]),
            "node_z": np.array([0.0, 3.0, 6.0]),
            "frame_eid": np.array([0, 1]),
            "frame_sap_id": np.array(["F1", "F2"]),
            "frame_node_i": np.array([1, 2]),
            "frame_node_j": np.array([2, 3]),
            "frame_sec_name": np.array(["COL", "BEAM"]),
            "frame_parent_sap_id": np.array(["", ""]),
            "shell_eid": np.array([]),
            # Pushover data: 3 steps, 2 frames
            "pushover/+X/step": np.array([0, 1, 2]),
            "pushover/+X/frame_sap_id": np.array(["F1", "F2"]),
            "pushover/+X/frame_fx_i": np.zeros((3, 2)),
            "pushover/+X/frame_fy_i": np.zeros((3, 2)),
            "pushover/+X/frame_fz_i": np.zeros((3, 2)),
            "pushover/+X/frame_mx_i": np.zeros((3, 2)),
            "pushover/+X/frame_my_i": np.zeros((3, 2)),
            "pushover/+X/frame_mz_i": np.array(
                [
                    [10.0, 5.0],  # step 0: F1=10, F2=5
                    [25.0, 8.0],  # step 1: F1=25 (peak), F2=8
                    [15.0, 12.0],  # step 2: F1=15, F2=12 (peak)
                ]
            ),
            "pushover/+X/frame_fx_j": np.zeros((3, 2)),
            "pushover/+X/frame_fy_j": np.zeros((3, 2)),
            "pushover/+X/frame_fz_j": np.zeros((3, 2)),
            "pushover/+X/frame_mx_j": np.zeros((3, 2)),
            "pushover/+X/frame_my_j": np.zeros((3, 2)),
            "pushover/+X/frame_mz_j": np.array(
                [
                    [-8.0, -4.0],  # step 0
                    [-20.0, -6.0],  # step 1: F1 peak j
                    [-12.0, -10.0],  # step 2: F2 peak j
                ]
            ),
            # Node displacements (empty)
            "pushover/+X/node_tag": np.array([]),
            "pushover/+X/node_disp_x": np.empty((3, 0)),
            "pushover/+X/node_disp_y": np.empty((3, 0)),
            "pushover/+X/node_disp_z": np.empty((3, 0)),
        }

    def test_envelope_notebook(self, npz_with_frames):
        """Envelope with notebook=True returns a plotter."""
        from fea_toolkit.plotting.viz import plot_pushover_envelope

        pl = plot_pushover_envelope(
            npz_with_frames,
            quantity="Mz",
            notebook=True,
        )
        assert pl is not None
        pl.close()

    def test_envelope_tube_mode(self, npz_with_frames):
        """Envelope with mode='tube' works."""
        from fea_toolkit.plotting.viz import plot_pushover_envelope

        pl = plot_pushover_envelope(
            npz_with_frames,
            quantity="Mz",
            mode="tube",
            notebook=True,
        )
        assert pl is not None
        pl.close()

    def test_envelope_force_quantity(self, npz_with_frames):
        """Envelope with force quantity 'Fx' works."""
        from fea_toolkit.plotting.viz import plot_pushover_envelope

        pl = plot_pushover_envelope(
            npz_with_frames,
            quantity="Fx",
            notebook=True,
        )
        assert pl is not None
        pl.close()

    @pytest.fixture
    def npz_with_shells_and_frames(self):
        """NPZ dict with 2 frame + 2 shell elements, 3 push steps for envelope."""
        return {
            "node_tag": np.array([1, 2, 3, 4, 5]),
            "node_sap_id": np.array(["1", "2", "3", "4", "5"]),
            "node_x": np.array([0.0, 4.0, 4.0, 0.0, 4.0]),
            "node_y": np.array([0.0, 0.0, 0.0, 3.0, 3.0]),
            "node_z": np.array([0.0, 0.0, 3.0, 0.0, 3.0]),
            "frame_eid": np.array([0, 1]),
            "frame_sap_id": np.array(["F1", "F2"]),
            "frame_node_i": np.array([1, 2]),
            "frame_node_j": np.array([2, 3]),
            "frame_sec_name": np.array(["COL", "BEAM"]),
            "frame_parent_sap_id": np.array(["", ""]),
            "shell_eid": np.array([0, 1]),
            "shell_sap_id": np.array(["W1", "W2"]),
            "shell_sec_name": np.array(["WALL", "WALL"]),
            "shell_node_1": np.array([1, 2]),
            "shell_node_2": np.array([2, 5]),
            "shell_node_3": np.array([5, 3]),
            "shell_node_4": np.array([4, 4]),
            "pushover/+X/step": np.array([0, 1, 2]),
            "pushover/+X/frame_sap_id": np.array(["F1", "F2"]),
            "pushover/+X/frame_fx_i": np.zeros((3, 2)),
            "pushover/+X/frame_fy_i": np.zeros((3, 2)),
            "pushover/+X/frame_fz_i": np.zeros((3, 2)),
            "pushover/+X/frame_mx_i": np.zeros((3, 2)),
            "pushover/+X/frame_my_i": np.zeros((3, 2)),
            "pushover/+X/frame_mz_i": np.array(
                [
                    [10.0, 5.0],
                    [25.0, 8.0],
                    [15.0, 12.0],
                ]
            ),
            "pushover/+X/frame_fx_j": np.zeros((3, 2)),
            "pushover/+X/frame_fy_j": np.zeros((3, 2)),
            "pushover/+X/frame_fz_j": np.zeros((3, 2)),
            "pushover/+X/frame_mx_j": np.zeros((3, 2)),
            "pushover/+X/frame_my_j": np.zeros((3, 2)),
            "pushover/+X/frame_mz_j": np.array(
                [
                    [-8.0, -4.0],
                    [-20.0, -6.0],
                    [-12.0, -10.0],
                ]
            ),
            "pushover/+X/shell_sap_id": np.array(["W1", "W2"]),
            "pushover/+X/shell_Nx": np.array(
                [
                    [100.0, 50.0],
                    [200.0, 80.0],
                    [300.0, 120.0],
                ]
            ),
            "pushover/+X/shell_Ny": np.array(
                [
                    [50.0, 25.0],
                    [100.0, 40.0],
                    [150.0, 60.0],
                ]
            ),
            "pushover/+X/shell_Nxy": np.array(
                [
                    [10.0, 5.0],
                    [20.0, 8.0],
                    [30.0, 12.0],
                ]
            ),
            "pushover/+X/shell_Mx": np.array(
                [
                    [5.0, 3.0],
                    [10.0, 5.0],
                    [15.0, 8.0],
                ]
            ),
            "pushover/+X/shell_My": np.array(
                [
                    [3.0, 2.0],
                    [6.0, 3.0],
                    [9.0, 5.0],
                ]
            ),
            "pushover/+X/shell_Mxy": np.array(
                [
                    [1.0, 0.5],
                    [2.0, 1.0],
                    [3.0, 1.5],
                ]
            ),
            "pushover/+X/node_tag": np.array([1, 2, 3, 4, 5]),
            "pushover/+X/node_disp_x": np.array(
                [
                    [0.0, 0.01, 0.02, 0.0, 0.01],
                    [0.0, 0.02, 0.04, 0.0, 0.02],
                    [0.0, 0.03, 0.06, 0.0, 0.03],
                ]
            ),
            "pushover/+X/node_disp_y": np.zeros((3, 5)),
            "pushover/+X/node_disp_z": np.zeros((3, 5)),
        }

    def test_envelope_with_shells(self, npz_with_shells_and_frames):
        """Envelope renders shells and returns a plotter."""
        from fea_toolkit.plotting.viz import plot_pushover_envelope

        pl = plot_pushover_envelope(
            npz_with_shells_and_frames,
            quantity="Mz",
            notebook=True,
        )
        assert pl is not None
        pl.close()

    def test_envelope_no_data(self):
        """Envelope with no data returns None."""
        from fea_toolkit.plotting.viz import plot_pushover_envelope

        empty = {"pushover/+X/step": np.array([])}
        pl = plot_pushover_envelope(empty, notebook=True)
        assert pl is None


# ============================================================================
# Tests for plastic hinge formation
# ============================================================================


class TestPlasticHingeFormation:
    """Tests for plot_plastic_hinge_formation (Phase 4a1)."""

    @pytest.fixture(autouse=True)
    def _skip_if_no_pyvista(self):
        if not _has_pyvista:
            pytest.skip("pyvista not installed")

    @pytest.fixture
    def npz_hinge_data(self):
        """NPZ dict with 2 frame elements + hinge forces, 3 push steps."""
        return {
            "node_tag": np.array([1, 2, 3]),
            "node_sap_id": np.array(["1", "2", "3"]),
            "node_x": np.array([0.0, 4.0, 4.0]),
            "node_y": np.array([0.0, 0.0, 0.0]),
            "node_z": np.array([0.0, 0.0, 3.0]),
            "frame_eid": np.array([0, 1]),
            "frame_sap_id": np.array(["F1", "F2"]),
            "frame_node_i": np.array([1, 2]),
            "frame_node_j": np.array([2, 3]),
            "frame_sec_name": np.array(["COL", "BEAM"]),
            "frame_parent_sap_id": np.array(["", ""]),
            "pushover/+X/step": np.array([0, 1, 2]),
            "pushover/+X/frame_sap_id": np.array(["F1", "F2"]),
            "pushover/+X/frame_fx_i": np.zeros((3, 2)),
            "pushover/+X/frame_fy_i": np.zeros((3, 2)),
            "pushover/+X/frame_fz_i": np.zeros((3, 2)),
            "pushover/+X/frame_mx_i": np.zeros((3, 2)),
            "pushover/+X/frame_my_i": np.zeros((3, 2)),
            "pushover/+X/frame_mz_i": np.array(
                [
                    [10.0, 5.0],
                    [25.0, 8.0],
                    [15.0, 12.0],
                ]
            ),
            "pushover/+X/frame_fx_j": np.zeros((3, 2)),
            "pushover/+X/frame_fy_j": np.zeros((3, 2)),
            "pushover/+X/frame_fz_j": np.zeros((3, 2)),
            "pushover/+X/frame_mx_j": np.zeros((3, 2)),
            "pushover/+X/frame_my_j": np.zeros((3, 2)),
            "pushover/+X/frame_mz_j": np.array(
                [
                    [-8.0, -4.0],
                    [-20.0, -6.0],
                    [-12.0, -10.0],
                ]
            ),
            "pushover/+X/node_tag": np.array([1, 2, 3]),
            "pushover/+X/node_disp_x": np.zeros((3, 3)),
            "pushover/+X/node_disp_y": np.zeros((3, 3)),
            "pushover/+X/node_disp_z": np.zeros((3, 3)),
        }

    def test_hinge_formation_notebook(self, npz_hinge_data):
        """Hinge formation with notebook=True returns plotter."""
        from fea_toolkit.plotting.viz import plot_plastic_hinge_formation

        pl = plot_plastic_hinge_formation(
            npz_hinge_data,
            step=0,
            notebook=True,
        )
        assert pl is not None
        pl.close()

    def test_hinge_formation_step_one(self, npz_hinge_data):
        """Specific step renders without slider."""
        from fea_toolkit.plotting.viz import plot_plastic_hinge_formation

        pl = plot_plastic_hinge_formation(
            npz_hinge_data,
            step=1,
            notebook=True,
        )
        assert pl is not None
        pl.close()

    def test_hinge_formation_no_data(self):
        """Empty data returns None."""
        from fea_toolkit.plotting.viz import plot_plastic_hinge_formation

        pl = plot_plastic_hinge_formation({}, notebook=True)
        assert pl is None

    def test_hinge_formation_raw_list(self):
        """Raw list without element-to-node mapping returns None gracefully.

        Raw list data has no mesh geometry for frame_eid_to_nodes.
        This is a known limitation — use NPZ dict or Builder instead.
        """
        from fea_toolkit.plotting.viz import plot_plastic_hinge_formation

        raw_data = [
            {
                "frame_forces": {
                    "F1": {"mz_i": 10.0, "mz_j": -8.0},
                    "F2": {"mz_i": 5.0, "mz_j": -4.0},
                },
                "node_coords": {"1": (0, 0, 0), "2": (4, 0, 0), "3": (4, 0, 3)},
            },
        ]
        # Known limitation — raw list has no frame_eid_to_nodes
        pl = plot_plastic_hinge_formation(raw_data, step=0, notebook=True)
        assert pl is None


# ============================================================================
# Tests for pushover deformation animation
# ============================================================================


class TestAnimatePushoverDeformation:
    """Tests for animate_pushover_deformation (Phase 4d)."""

    @pytest.fixture(autouse=True)
    def _skip_if_no_pyvista(self):
        if not _has_pyvista:
            pytest.skip("pyvista not installed")

    @pytest.fixture
    def npz_anim_data(self):
        """NPZ dict with 2 frame elements, 2 shell elements, 3 push steps."""
        return {
            "node_tag": np.array([1, 2, 3, 4, 5]),
            "node_sap_id": np.array(["1", "2", "3", "4", "5"]),
            "node_x": np.array([0.0, 4.0, 4.0, 0.0, 4.0]),
            "node_y": np.array([0.0, 0.0, 0.0, 3.0, 3.0]),
            "node_z": np.array([0.0, 0.0, 3.0, 0.0, 3.0]),
            "frame_eid": np.array([0, 1]),
            "frame_sap_id": np.array(["F1", "F2"]),
            "frame_node_i": np.array([1, 2]),
            "frame_node_j": np.array([2, 3]),
            "frame_sec_name": np.array(["COL", "BEAM"]),
            "frame_parent_sap_id": np.array(["", ""]),
            "shell_eid": np.array([0, 1]),
            "shell_sap_id": np.array(["W1", "W2"]),
            "shell_sec_name": np.array(["WALL", "WALL"]),
            "shell_node_1": np.array([1, 2]),
            "shell_node_2": np.array([2, 5]),
            "shell_node_3": np.array([5, 3]),
            "shell_node_4": np.array([4, 4]),
            "pushover/+X/step": np.array([0, 1, 2]),
            "pushover/+X/frame_sap_id": np.array(["F1", "F2"]),
            "pushover/+X/frame_fx_i": np.zeros((3, 2)),
            "pushover/+X/frame_fy_i": np.zeros((3, 2)),
            "pushover/+X/frame_fz_i": np.zeros((3, 2)),
            "pushover/+X/frame_mx_i": np.zeros((3, 2)),
            "pushover/+X/frame_my_i": np.zeros((3, 2)),
            "pushover/+X/frame_mz_i": np.array(
                [
                    [10.0, 5.0],
                    [25.0, 8.0],
                    [15.0, 12.0],
                ]
            ),
            "pushover/+X/frame_fx_j": np.zeros((3, 2)),
            "pushover/+X/frame_fy_j": np.zeros((3, 2)),
            "pushover/+X/frame_fz_j": np.zeros((3, 2)),
            "pushover/+X/frame_mx_j": np.zeros((3, 2)),
            "pushover/+X/frame_my_j": np.zeros((3, 2)),
            "pushover/+X/frame_mz_j": np.array(
                [
                    [-8.0, -4.0],
                    [-20.0, -6.0],
                    [-12.0, -10.0],
                ]
            ),
            "pushover/+X/shell_sap_id": np.array(["W1", "W2"]),
            "pushover/+X/shell_Nx": np.array(
                [
                    [100.0, 50.0],
                    [200.0, 80.0],
                    [300.0, 120.0],
                ]
            ),
            "pushover/+X/shell_Ny": np.array(
                [
                    [50.0, 25.0],
                    [100.0, 40.0],
                    [150.0, 60.0],
                ]
            ),
            "pushover/+X/shell_Nxy": np.array(
                [
                    [10.0, 5.0],
                    [20.0, 8.0],
                    [30.0, 12.0],
                ]
            ),
            "pushover/+X/shell_Mx": np.array(
                [
                    [5.0, 3.0],
                    [10.0, 5.0],
                    [15.0, 8.0],
                ]
            ),
            "pushover/+X/shell_My": np.array(
                [
                    [3.0, 2.0],
                    [6.0, 3.0],
                    [9.0, 5.0],
                ]
            ),
            "pushover/+X/shell_Mxy": np.array(
                [
                    [1.0, 0.5],
                    [2.0, 1.0],
                    [3.0, 1.5],
                ]
            ),
            "pushover/+X/node_tag": np.array([1, 2, 3, 4, 5]),
            "pushover/+X/node_disp_x": np.array(
                [
                    [0.0, 0.01, 0.02, 0.0, 0.01],
                    [0.0, 0.02, 0.04, 0.0, 0.02],
                    [0.0, 0.03, 0.06, 0.0, 0.03],
                ]
            ),
            "pushover/+X/node_disp_y": np.zeros((3, 5)),
            "pushover/+X/node_disp_z": np.zeros((3, 5)),
        }

    def test_anim_notebook(self, npz_anim_data):
        """Animation with notebook=True returns plotter."""
        from fea_toolkit.plotting.viz import animate_pushover_deformation

        pl = animate_pushover_deformation(npz_anim_data, notebook=True)
        assert pl is not None
        pl.close()

    def test_anim_no_data(self):
        """Animation with no data returns None."""
        from fea_toolkit.plotting.viz import animate_pushover_deformation

        pl = animate_pushover_deformation({}, notebook=True)
        assert pl is None

    def test_anim_frames_only(self, npz_anim_data):
        """Animation with show_shells=False works."""
        from fea_toolkit.plotting.viz import animate_pushover_deformation

        pl = animate_pushover_deformation(
            npz_anim_data,
            show_shells=False,
            notebook=True,
        )
        assert pl is not None
        pl.close()

    def test_anim_save_html_call(self, npz_anim_data, tmp_path):
        """Animation with save_html should not crash (file may not be written in off-screen mode)."""
        from fea_toolkit.plotting.viz import animate_pushover_deformation

        html_path = str(tmp_path / "test_anim.html")
        pl = animate_pushover_deformation(
            npz_anim_data,
            save_html=html_path,
            notebook=True,
        )
        assert pl is not None
        # HTML export may fail silently in off-screen mode — that's OK
        pl.close()

    def test_anim_raw_list(self):
        """Animation with raw list data returns None (no geometry data)."""
        from fea_toolkit.plotting.viz import animate_pushover_deformation

        raw_data = [
            {"frame_forces": {"1": {"mz_i": 10.0, "mz_j": -8.0}}, "shell_forces": {}},
            {"frame_forces": {"1": {"mz_i": 20.0, "mz_j": -15.0}}, "shell_forces": {}},
        ]
        pl = animate_pushover_deformation(raw_data, notebook=True)
        assert pl is None  # raw list has no node_coords or mesh data


# ============================================================================
# Tests for frame force evolution
# ============================================================================


class TestFrameForceEvolution:
    """Tests for plot_frame_force_evolution (Phase 4e)."""

    @pytest.fixture
    def force_evolution_data(self):
        """2-frame, 3-step dataset for force evolution."""
        return [
            {
                "frame_forces": {
                    "F1": {
                        "mz_i": 10.0,
                        "mz_j": -8.0,
                        "fx_i": 5.0,
                        "fx_j": -5.0,
                        "fy_i": 2.0,
                        "fy_j": -2.0,
                        "fz_i": 3.0,
                        "fz_j": -3.0,
                    },
                    "F2": {
                        "mz_i": 5.0,
                        "mz_j": -4.0,
                        "fx_i": 2.0,
                        "fx_j": -2.0,
                        "fy_i": 1.0,
                        "fy_j": -1.0,
                        "fz_i": 1.5,
                        "fz_j": -1.5,
                    },
                },
            },
            {
                "frame_forces": {
                    "F1": {
                        "mz_i": 25.0,
                        "mz_j": -20.0,
                        "fx_i": 8.0,
                        "fx_j": -8.0,
                        "fy_i": 5.0,
                        "fy_j": -5.0,
                        "fz_i": 6.0,
                        "fz_j": -6.0,
                    },
                    "F2": {
                        "mz_i": 8.0,
                        "mz_j": -6.0,
                        "fx_i": 3.0,
                        "fx_j": -3.0,
                        "fy_i": 2.0,
                        "fy_j": -2.0,
                        "fz_i": 2.5,
                        "fz_j": -2.5,
                    },
                },
            },
            {
                "frame_forces": {
                    "F1": {
                        "mz_i": 15.0,
                        "mz_j": -12.0,
                        "fx_i": 6.0,
                        "fx_j": -6.0,
                        "fy_i": 3.0,
                        "fy_j": -3.0,
                        "fz_i": 4.0,
                        "fz_j": -4.0,
                    },
                    "F2": {
                        "mz_i": 12.0,
                        "mz_j": -10.0,
                        "fx_i": 4.0,
                        "fx_j": -4.0,
                        "fy_i": 3.0,
                        "fy_j": -3.0,
                        "fz_i": 3.5,
                        "fz_j": -3.5,
                    },
                },
            },
        ]

    def test_force_evolution_basic(self, force_evolution_data):
        """Basic force evolution returns Figure with correct subplots."""
        import matplotlib.pyplot as plt

        from fea_toolkit.plotting.viz import plot_frame_force_evolution

        fig = plot_frame_force_evolution(
            force_evolution_data,
            quantity="Mz",
            figsize=(6, 4),
        )
        assert fig is not None
        # 2 elements should produce 2 subplots (1 row × 2 cols)
        assert len(fig.axes) == 2
        plt.close(fig)

    def test_force_evolution_with_yield(self, force_evolution_data):
        """Yield moment is drawn as a dashed line."""
        import matplotlib.pyplot as plt

        from fea_toolkit.plotting.viz import plot_frame_force_evolution

        fig = plot_frame_force_evolution(
            force_evolution_data,
            quantity="Mz",
            yield_moment={"F1": 20.0, "F2": 10.0},
            figsize=(6, 4),
        )
        assert fig is not None
        plt.close(fig)

    def test_force_evolution_no_data(self):
        """Empty data returns None."""
        from fea_toolkit.plotting.viz import plot_frame_force_evolution

        fig = plot_frame_force_evolution([], figsize=(6, 4))
        assert fig is None

    def test_force_evolution_quantity_v(self, force_evolution_data):
        """Force quantity 'V' (shear) works."""
        import matplotlib.pyplot as plt

        from fea_toolkit.plotting.viz import plot_frame_force_evolution

        fig = plot_frame_force_evolution(
            force_evolution_data,
            quantity="V",
            figsize=(6, 4),
        )
        assert fig is not None
        plt.close(fig)

    def test_force_evolution_quantity_n(self, force_evolution_data):
        """Force quantity 'N' (axial) works."""
        import matplotlib.pyplot as plt

        from fea_toolkit.plotting.viz import plot_frame_force_evolution

        fig = plot_frame_force_evolution(
            force_evolution_data,
            quantity="N",
            figsize=(6, 4),
        )
        assert fig is not None
        plt.close(fig)


class TestAnimationTimerCallbackArity:
    """Regression tests for PyVista ``add_timer_event`` callback arity.

    PyVista >= 0.44 invokes ``add_timer_event`` callbacks with **two**
    positional arguments ``(step, plotter)``, while older versions pass
    ``(step,)`` or nothing.  The toolkit's callbacks accept ``()``
    (``_timer_callback`` in ``animate_pushover_deformation``) or
    ``(step)`` (mode-shape callbacks in ``plot_mode_animation`` and
    ``plot_mode_3d``).  ``_add_animation_timer`` must adapt the callback
    so the correct number of arguments is forwarded regardless of the
    installed PyVista version — otherwise the classic
    ``TypeError: callback() takes N positional arguments but M were given``
    breaks mode animations on newer PyVista.
    """

    @staticmethod
    def _make_fake_plotter(register_as):
        """Build a minimal fake plotter that records how the timer is registered.

        Simulates each of the three registration paths in ``_add_animation_timer``:

        - ``"pyvista_2arg"`` — modern PyVista: ``add_timer_event`` succeeds on
          the first attempt (callback accepted as keyword).
        - ``"pyvista_no_interval"`` — older PyVista: the first call with the
          ``interval`` kwarg raises ``TypeError``; the retry without it succeeds.
        - ``"vtek"`` — both ``add_timer_event`` attempts raise, so the helper
          falls through to the low-level VTK ``AddObserver`` path.
        """

        class _FakeInteractor:
            def __init__(self, plotter_ref):
                self.plotter_ref = plotter_ref
                self._observers = []

            def AddObserver(self, event, callback):
                self._observers.append((event, callback))
                self.plotter_ref.observer_added = True

            def CreateRepeatingTimer(self, interval_ms):
                self.plotter_ref.timer_created = True
                self.plotter_ref.interval_ms = interval_ms

        class _FakePlotter:
            def __init__(self, mode):
                self.mode = mode
                self.method_calls = []
                self.observer_added = False
                self.timer_created = False
                self.registered_callback = None
                self.render_window = self  # iren lookup uses plotter.render_window
                self._interactor = _FakeInteractor(self)

            def add_timer_event(self, *args, **kwargs):
                self.method_calls.append(("add_timer_event", args, kwargs))
                if self.mode == "vtek":
                    # No timer API available — force VTK fallback.
                    raise AttributeError("no add_timer_event")
                if self.mode == "pyvista_no_interval" and "interval" in kwargs:
                    # Older PyVista rejects the interval kwarg.
                    raise TypeError("interval not supported")
                self.registered_callback = kwargs.get("callback")

            def GetInteractor(self):
                return self._interactor

        fp = _FakePlotter(register_as)
        return fp

    def _invoke_registered_callback(self, fake_plotter, register_as, pyvista_args):
        """Simulate PyVista calling the registered callback with *pyvista_args*.

        For the modern/older paths the callback was stored by
        ``add_timer_event``; for the VTK path it was stored via
        ``AddObserver("TimerEvent", ...)``.
        """
        if register_as == "vtek":
            assert fake_plotter.observer_added, "VTK observer was not added"
            event, cb = fake_plotter._interactor._observers[0]
            assert event == "TimerEvent"
            # VTK passes (caller, event) — no step meaning.
            return cb("vtk_caller", "TimerEvent")

        assert fake_plotter.registered_callback is not None, "callback not registered"
        return fake_plotter.registered_callback(*pyvista_args)

    def test_one_arg_callback_receives_two_pyvista_args(self):
        """A ``callback(step)`` must tolerate PyVista passing ``(step, plotter)``."""
        from fea_toolkit.plotting.viz import _add_animation_timer

        calls = []

        def callback(step):
            calls.append(step)
            return step

        fp = self._make_fake_plotter("pyvista_2arg")
        _add_animation_timer(fp, callback, max_steps=10, interval_ms=17)
        out = self._invoke_registered_callback(fp, "pyvista_2arg", (3, "plotter_obj"))
        assert out == 3
        assert calls == [3]

    def test_zero_arg_callback_receives_two_pyvista_args(self):
        """A ``callback()`` (pushover timer) must tolerate 2 PyVista args."""
        from fea_toolkit.plotting.viz import _add_animation_timer

        calls = []

        def callback():
            calls.append(1)

        fp = self._make_fake_plotter("pyvista_2arg")
        _add_animation_timer(fp, callback, max_steps=10, interval_ms=17)
        self._invoke_registered_callback(fp, "pyvista_2arg", (3, "plotter_obj"))
        assert calls == [1]

    def test_varargs_callback_receives_all_pyvista_args(self):
        """A ``callback(*args)`` receives both step and plotter."""
        from fea_toolkit.plotting.viz import _add_animation_timer

        received = []

        def callback(*args):
            received.append(args)
            return args

        fp = self._make_fake_plotter("pyvista_2arg")
        _add_animation_timer(fp, callback, max_steps=10, interval_ms=17)
        out = self._invoke_registered_callback(fp, "pyvista_2arg", (5, "plotter_obj"))
        assert out == (5, "plotter_obj")
        assert received == [(5, "plotter_obj")]

    def test_one_arg_callback_older_pyvista_no_interval(self):
        """Older PyVista (no interval kwarg) — callback still adapted."""
        from fea_toolkit.plotting.viz import _add_animation_timer

        calls = []

        def callback(step):
            calls.append(step)

        fp = self._make_fake_plotter("pyvista_no_interval")
        _add_animation_timer(fp, callback, max_steps=10, interval_ms=17)
        self._invoke_registered_callback(fp, "pyvista_no_interval", (2,))
        assert calls == [2]

    def test_vtk_fallback_supplies_incrementing_step(self):
        """VTK TimerEvent passes (caller, event) with no step count — a
        ``callback(step)`` must receive an internal incrementing counter so
        the sine-phase oscillation actually progresses."""
        from fea_toolkit.plotting.viz import _add_animation_timer

        calls = []

        def callback(step):
            calls.append(step)

        fp = self._make_fake_plotter("vtek")
        _add_animation_timer(fp, callback, max_steps=10, interval_ms=17)
        self._invoke_registered_callback(fp, "vtek", ("caller", "TimerEvent"))
        self._invoke_registered_callback(fp, "vtek", ("caller", "TimerEvent"))
        # Internal counter increments on each tick (starts at 1).
        assert calls == [1, 2], f"Expected incrementing steps, got {calls}"

    def test_vtk_fallback_supplies_no_args_to_zero_arg_callback(self):
        """A zero-argument callback (pushover ``_timer_callback``) is invoked
        with **no** args on the VTK fallback path."""
        from fea_toolkit.plotting.viz import _add_animation_timer

        calls = []

        def callback():
            calls.append(1)

        fp = self._make_fake_plotter("vtek")
        _add_animation_timer(fp, callback, max_steps=10, interval_ms=17)
        self._invoke_registered_callback(fp, "vtek", ("caller", "TimerEvent"))
        assert calls == [1]

    def test_vtk_fallback_sets_repeating_timer(self):
        """VTK fallback creates the repeating timer with the interval."""
        from fea_toolkit.plotting.viz import _add_animation_timer

        def callback(step):
            return step

        fp = self._make_fake_plotter("vtek")
        _add_animation_timer(fp, callback, max_steps=10, interval_ms=33)
        assert fp.observer_added
        assert fp.timer_created
        assert fp.interval_ms == 33
