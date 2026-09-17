"""Tests for ``fea_toolkit.plotting.viz_model`` — model / mesh visualisation.

Covers :func:`_build_deformed_mesh`, :func:`_resolve_mesh_data`,
:func:`plot_deformed_displacement_3d`, :func:`plot_mode_animation`, the
shrink-parameter override, and modal mass-participation annotation.
Uses synthetic NPZ-like data dicts — no OpenSees.
"""

import numpy as np
import pytest

# ============================================================================
# Fixtures — synthetic NPZ-like data dicts
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


@pytest.mark.needs_pyvista
class TestBuildDeformedMesh:
    """Verify the shared deformed-mesh constructor."""

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


@pytest.mark.needs_pyvista
class TestResolveMeshData:
    """Verify the mesh-data resolver works for dict (NPZ), SAPModelData,
    and builder sources."""

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

    def test_builder_model_alias(self):
        """``AnalysisBuilder.model`` exposes the frozen ``MeshModel``.

        Regression test: ``plot_interactive_viewer`` reads ``builder.model``,
        which the two-stage :class:`AnalysisBuilder` previously did not
        provide (it only exposed ``mesh_model``), so the viewer raised
        ``AttributeError``.
        """
        from examples.sample_model import make_sample_model
        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
        from fea_toolkit.opensees.preprocessor import preprocess_model

        md = make_sample_model()
        config = {"element_type": "elasticBeamColumn", "split_elements": True, "verbose": False}
        mesh_model = preprocess_model(md, config)
        builder = AnalysisBuilder(mesh_model, config)

        assert builder.model is builder.mesh_model
        assert builder.model.frame_elements is mesh_model.frame_elements
        assert builder.model.frame_assignments is mesh_model.frame_assignments

    def test_interactive_viewer_accepts_builder(self):
        """``plot_interactive_viewer`` accepts a two-stage ``AnalysisBuilder``.

        Regression test: the viewer read the legacy
        ``builder.split_elements`` / ``builder.split_assignments`` attributes,
        which ``AnalysisBuilder`` no longer exposes.
        """
        from examples.sample_model import make_sample_model
        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
        from fea_toolkit.opensees.preprocessor import preprocess_model
        from fea_toolkit.plotting import plot_interactive_viewer

        md = make_sample_model()
        config = {"element_type": "elasticBeamColumn", "split_elements": True, "verbose": False}
        mesh_model = preprocess_model(md, config)
        builder = AnalysisBuilder(mesh_model, config)

        plotter = plot_interactive_viewer(
            builder,
            combo_forces={"All": {}},
            combo_results={"All": {}},
            notebook=True,
        )
        assert plotter is not None
        plotter.close()

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
        pytest.importorskip("IPython")  # notebook display needs IPython
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

    def test_mode_amplitude_is_normalised(self, sample_npz_data):
        """Mode-shape amplitude must not depend on the eigenvector magnitude.

        ``ops.nodeEigenvector`` returns *mass-normalised* eigenvectors
        (``phiᵀM phi = 1``), so a local mode with a small modal mass has far
        larger components than a global sway mode — ~20x on the pipe-rack
        model.  Two shapes differing only by a constant factor must therefore
        render identically, which is what normalising to unit peak achieves.
        """
        pytest.importorskip("IPython")  # notebook display needs IPython
        from fea_toolkit.plotting import plot_mode_animation

        base = {1: (0.0, 0.0, 0.0), 2: (0.25, 0.0, 0.0), 3: (0.5, 0.0, 0.0)}

        def _render(disp, scale):
            pl = plot_mode_animation(
                sample_npz_data,
                {0: disp},
                mode=0,
                scale=scale,
                animate=False,
                notebook=True,
            )
            bounds = pl.bounds
            pl.close()
            return bounds

        # 100% of span, so the deformed mesh dominates the plotter bounds.
        small = _render(base, 100.0)
        big = _render({k: tuple(c * 20.0 for c in v) for k, v in base.items()}, 100.0)
        assert small == pytest.approx(big, rel=1e-9, abs=1e-9)

        # ...and a 20x smaller shape does NOT shrink the display either.
        shrunk = _render({k: tuple(c / 20.0 for c in v) for k, v in base.items()}, 100.0)
        assert shrunk == pytest.approx(small, rel=1e-9, abs=1e-9)

        # ``scale`` itself still controls the exaggeration.
        doubled = _render(base, 200.0)
        assert doubled != pytest.approx(small, rel=1e-6, abs=1e-6)

    def test_force_diagram_collapse_to_parents(self):
        """plot_force_diagram accepts collapse_to_parents parameter."""
        from fea_toolkit.plotting import plot_force_diagram

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
        pl = plot_force_diagram(
            npz,
            force_data=None,
            collapse_to_parents=False,
            quantity="Mx",
            mode="flag",
            dimension="3d",
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

    def test_static_force_map_uses_frame_tag_map(self):
        """``_build_static_force_map`` resolves tags via ``frame_tag_map``.

        Regression: the map used ``elem.elem_tag`` directly, so forces keyed
        by the generated OpenSees tag (as returned by
        ``extract_static_element_forces()``) were silently dropped whenever
        ``frame_tag_map`` assigned a different tag.
        """
        from types import SimpleNamespace

        from examples.sample_model import make_sample_model
        from fea_toolkit.plotting.force_diagram import _build_static_force_map

        md = make_sample_model()
        elem = next(iter(md.frame_elements.values()))
        ni = md.nodes[elem.node_i]
        nj = md.nodes[elem.node_j]

        source = SimpleNamespace(model=md, frame_tag_map={elem.elem_id: 99})
        geometry = {"frames": [{"ni_tag": ni.node_tag, "nj_tag": nj.node_tag}]}
        force_data = {99: {"Fx": 1.0, "Fx_j": -1.0}}

        force_map = _build_static_force_map(source, geometry, force_data)
        assert list(force_map) == [0]
        assert force_map[0]["FX"] == 1.0
        assert force_map[0]["FX_j"] == -1.0


# ============================================================================
# plot_deformed_displacement_3d
# ============================================================================


@pytest.mark.needs_pyvista
class TestPlotDeformedDisplacement3d:
    """Smoke tests for the unified displaced-shape viewer."""

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
        pytest.importorskip("IPython")  # notebook display needs IPython
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
# shrink parameter
# ============================================================================


@pytest.mark.needs_pyvista
class TestShrinkParameter:
    """Verify ``shrink`` parameter works in all functions that support it."""

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
        pytest.importorskip("IPython")  # notebook display needs IPython
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
        """plot_force_diagram does not accept shrink (flags unaffected).

        Confirms the function signature has no ``shrink`` parameter.
        """
        import inspect

        from fea_toolkit.plotting import plot_force_diagram

        sig = inspect.signature(plot_force_diagram)
        assert "shrink" not in sig.parameters


# ============================================================================
# Modal mass-participation annotation
# ============================================================================


def _minimal_modal_npz_dict() -> dict:
    """Minimal NPZ-style dict carrying the ``modal/*`` block.

    The ratios mimic ``ops.modalProperties()`` output written verbatim by
    ``npz_writer._collect_modal`` — the display reads them, it does not
    compute them.
    """
    return {
        "node_tag": np.array([1, 2, 3]),
        "node_sap_id": np.array(["1", "2", "3"]),
        "node_x": np.array([0.0, 4.0, 4.0]),
        "node_y": np.zeros(3),
        "node_z": np.array([0.0, 0.0, 3.0]),
        "frame_eid": np.array([0, 1]),
        "frame_sap_id": np.array(["1", "2"]),
        "frame_node_i": np.array([1, 2]),
        "frame_node_j": np.array([2, 3]),
        "frame_sec_name": np.array(["COL", "BEAM"]),
        "frame_parent_sap_id": np.array(["", ""]),
        "modal/period": np.array([0.35523676, 0.28321302]),
        "modal/mx_ratio": np.array([0.0319629, 12.329605]),
        "modal/my_ratio": np.array([25.298915, 6.733203]),
        "modal/mz_ratio": np.array([2.794e-06, 5.819e-09]),
        "modal/rx_ratio": np.array([1.5, 2.5]),
        "modal/ry_ratio": np.array([3.5, 4.5]),
        "modal/rz_ratio": np.array([5.5, 6.5]),
        "modal/mode_dx": np.array([[0.5, 0.0], [0.6, 0.0], [0.7, 0.0]]),
        "modal/mode_dy": np.zeros((3, 2)),
        "modal/mode_dz": np.zeros((3, 2)),
    }


#: OpenSees ``modalProperties()`` payload (two modes), passed through verbatim.
_MODAL_PARTICIPATION_PROPS = {
    "partiMassRatiosMX": [0.0319629006, 12.32960535],
    "partiMassRatiosMY": [25.29891542, 6.73320313],
    "partiMassRatiosMZ": [2.794e-06, 5.818e-09],
    "partiMassRatiosRMX": [1.5, 2.5],
    "partiMassRatiosRMY": [3.5, 4.5],
    "partiMassRatiosRMZ": [5.5, 6.5],
}


# ============================================================================
# Modal mass-participation annotation
# ============================================================================


@pytest.mark.needs_pyvista
class TestModalParticipationAnnotation:
    """Modal annotation: period plus six-DOF mass participation.

    The ratios must be OpenSees ``modalProperties()`` values passed through
    verbatim — the toolkit never recomputes participation factors.
    """

    def test_ratios_pass_through_verbatim(self):
        """The OpenSees values reach the caller unmodified."""
        from fea_toolkit.plotting.viz_model import mass_participation_ratios

        ratios = mass_participation_ratios(_MODAL_PARTICIPATION_PROPS)
        assert len(ratios) == 2
        assert ratios[0] == (0.0319629006, 25.29891542, 2.794e-06, 1.5, 3.5, 5.5)

    def test_missing_dof_is_none_not_zero(self):
        """An absent DOF is ``None``, not a misleading ``0.0``."""
        from fea_toolkit.plotting.viz_model import mass_participation_ratios

        ratios = mass_participation_ratios({"partiMassRatiosMX": [10.0]})
        assert ratios == [(10.0, None, None, None, None, None)]

    def test_empty_modal_props(self):
        from fea_toolkit.plotting.viz_model import mass_participation_ratios

        assert mass_participation_ratios({}) == []
        assert mass_participation_ratios(None) == []

    def test_annotation_has_translation_and_rotation_rows(self):
        from fea_toolkit.plotting.viz_model import (
            _format_mass_participation,
            mass_participation_ratios,
        )

        text = _format_mass_participation(mass_participation_ratios(_MODAL_PARTICIPATION_PROPS)[0])
        lines = text.split("\n")
        assert lines[0] == "Mass participation (%):"
        assert all(lbl in lines[1] for lbl in ("X", "Y", "Z"))
        assert all(lbl in lines[2] for lbl in ("RX", "RY", "RZ"))
        assert "25.30%" in lines[1]

    def test_annotation_legacy_translation_only(self):
        """A pre-rotational record yields a single row, no ``RX`` padding."""
        from fea_toolkit.plotting.viz_model import _format_mass_participation

        text = _format_mass_participation((0.03, 25.30, 0.0, None, None, None))
        assert text.count("\n") == 1
        assert "RX" not in text

    def test_annotation_empty_when_nothing_available(self):
        from fea_toolkit.plotting.viz_model import _format_mass_participation

        assert _format_mass_participation(None) == ""
        assert _format_mass_participation((None,) * 6) == ""

    def test_npz_ratios_read_from_archive(self):
        """The NPZ path reads the archived ratios verbatim."""
        from fea_toolkit.plotting.viz_model import _npz_mass_participation

        data = _minimal_modal_npz_dict()
        ratios = _npz_mass_participation(data)
        assert ratios[0] == pytest.approx((0.0319629, 25.298915, 2.794e-06, 1.5, 3.5, 5.5))
        # An archive without the rotational keys leaves those DOFs as None.
        for key in ("modal/rx_ratio", "modal/ry_ratio", "modal/rz_ratio"):
            data.pop(key)
        legacy = _npz_mass_participation(data)
        assert legacy[0][:3] == pytest.approx((0.0319629, 25.298915, 2.794e-06))
        assert legacy[0][3:] == (None, None, None)

    def test_plot_adds_period_and_participation_text(self):
        """The plotter receives the mode title, period and six-DOF block."""
        from unittest.mock import patch

        import pyvista as pv

        from fea_toolkit.plotting.viz_model import plot_mode_animation

        added = []
        orig = pv.Plotter.add_text

        def _spy(self, text, *args, **kwargs):
            added.append((text, kwargs.get("position")))
            return orig(self, text, *args, **kwargs)

        with patch.object(pv.Plotter, "add_text", _spy):
            pl = plot_mode_animation(
                _minimal_modal_npz_dict(), None, mode=0, animate=False, notebook=True
            )
        pl.close()

        assert any("Mode 1" in t for t, _ in added), added
        assert any("0.3552" in t for t, _ in added), "period missing from the annotation"
        annotation = [t for t, _ in added if "Mass participation" in t]
        assert annotation, added
        assert all(lbl in annotation[0] for lbl in ("X", "Y", "Z", "RX", "RY", "RZ"))

    def test_plot_still_titles_mode_without_participation(self):
        """A 3-DOF-only archive still gets the title, minus rotational rows."""
        from unittest.mock import patch

        import pyvista as pv

        from fea_toolkit.plotting.viz_model import plot_mode_animation

        data = _minimal_modal_npz_dict()
        for key in ("modal/rx_ratio", "modal/ry_ratio", "modal/rz_ratio"):
            data.pop(key)

        added = []
        orig = pv.Plotter.add_text

        def _spy(self, text, *args, **kwargs):
            added.append(text)
            return orig(self, text, *args, **kwargs)

        with patch.object(pv.Plotter, "add_text", _spy):
            pl = plot_mode_animation(data, None, mode=0, animate=False, notebook=True)
        pl.close()

        assert any("Mode 1" in t for t in added), added
        annotation = [t for t in added if "Mass participation" in t]
        assert annotation, added
        assert "RX" not in annotation[0]


# ============================================================================
# Node highlighting (node_colors)
# ============================================================================


@pytest.mark.needs_pyvista
class TestNodeHighlighting:
    """``plot_mesh(node_colors=...)`` splits plain and highlighted markers.

    Highlighted nodes are drawn as a second, larger point cloud on top of the
    plain black ones — the mechanism ``examples/view_model.py`` uses to draw
    the joints of a SAP2000 constraint group.
    """

    @staticmethod
    def _capture(data, **kwargs):
        """Render *data* and return ``[(n_points, color, point_size), ...]``.

        Only node markers are captured: they are the meshes drawn with a
        ``point_size`` keyword (frames use ``line_width``, shells face cells).
        """
        from unittest.mock import patch

        import pyvista as pv

        from fea_toolkit.plotting import plot_mesh

        captured = []
        real = pv.Plotter.add_mesh

        def _spy(self, mesh, *args, **kw):
            if "point_size" in kw and mesh.n_points:
                captured.append((mesh.n_points, kw.get("color"), kw.get("point_size")))
            return real(self, mesh, *args, **kw)

        with patch.object(pv.Plotter, "add_mesh", _spy):
            pl = plot_mesh(
                data,
                show_frames=False,
                show_shells=False,
                notebook=True,
                **kwargs,
            )
        pl.close()
        return captured

    def test_plain_nodes_are_one_black_cloud(self, sample_npz_data):
        """Without ``node_colors`` every node is a single black marker mesh."""
        assert self._capture(sample_npz_data) == [(3, "black", 6)]

    def test_highlighted_nodes_split_off(self, sample_npz_data):
        """A node-colour entry moves that node into its own coloured cloud."""
        captured = self._capture(sample_npz_data, node_colors={"2": "#ff2d2d"})

        assert (2, "black", 6) in captured
        assert (1, "#ff2d2d", 14) in captured

    def test_keys_are_source_node_ids_not_tags(self, sample_npz_data):
        """A key matching no node ID leaves every node plain.

        ``node_colors`` is keyed by the source's node IDs (SAP joint labels for
        an ``SAPModelData`` / NPZ source), so unlike ``section_colors`` there is
        no informal tag fallback.
        """
        captured = self._capture(sample_npz_data, node_colors={"not-a-node": "#ff2d2d"})

        assert captured == [(3, "black", 6)]

    def test_highlighted_label_uses_node_id_key(self):
        """Highlighted nodes are labelled with the key, plain ones by tag.

        The SAP joint label is what the ``JOINT CONSTRAINT ASSIGNMENTS`` table
        lists, so a highlighted joint must not be labelled with its OpenSees
        tag (which is a different number for a parsed model).
        """
        from unittest.mock import patch

        import pyvista as pv

        from fea_toolkit.plotting import plot_mesh

        # SAP IDs deliberately differ from the tags to tell the two apart.
        data = {
            "node_tag": np.array([101, 102, 103]),
            "node_sap_id": np.array(["J1", "J2", "J3"]),
            "node_x": np.array([0.0, 4.0, 4.0]),
            "node_y": np.array([0.0, 0.0, 0.0]),
            "node_z": np.array([0.0, 0.0, 3.0]),
        }

        captured = []
        real = pv.Plotter.add_point_labels

        def _spy(self, points, labels, *args, **kw):
            captured.extend(str(lbl) for lbl in labels)
            return real(self, points, labels, *args, **kw)

        with patch.object(pv.Plotter, "add_point_labels", _spy):
            pl = plot_mesh(
                data,
                show_frames=False,
                show_shells=False,
                show_node_labels=True,
                node_colors={"J2": "#ff2d2d"},
                notebook=True,
            )
        pl.close()

        assert sorted(captured) == sorted(["N101", "NJ2", "N103"])


# ============================================================================
# Selection overlay (highlight_selection)
# ============================================================================


def _selection_model():
    """Return a small ``SAPModelData`` with sections, a group and 4 nodes.

    4 nodes / 2 frames / 1 area is enough to tell a frame selection from a
    node selection and to exercise section- and group-based criteria.
    """
    from fea_toolkit.model.sap_data import (
        AreaElement,
        Constraint,
        FrameElement,
        Group,
        ISection,
        Node,
        SAPModelData,
    )

    def _isection(name):
        return ISection(
            name=name,
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
        )

    return SAPModelData(
        nodes={
            "N1": Node("N1", 1, 0.0, 0.0, 0.0),
            "N2": Node("N2", 2, 4.0, 0.0, 0.0),
            "N3": Node("N3", 3, 4.0, 0.0, 3.0),
            "N4": Node("N4", 4, 0.0, 4.0, 0.0),
        },
        restraints={},
        materials={},
        sections={"COL": _isection("COL"), "BEAM": _isection("BEAM")},
        frame_elements={
            "F1": FrameElement("F1", 10, "N1", "N3"),
            "F2": FrameElement("F2", 20, "N2", "N3"),
        },
        area_elements={"A1": AreaElement("A1", 30, ["N1", "N2", "N3", "N4"])},
        frame_assignments={"F1": "COL", "F2": "BEAM"},
        area_assignments={"A1": "SLAB"},
        groups={"Cols": Group(name="Cols", color="", objects=["Frame:F1", "Joint:N1"])},
        frame_auto_mesh={},
        constraints={"Fix": Constraint(name="Fix", constraint_type="BODY")},
        constraint_assignments={"N1": "Fix", "N2": "Fix", "N4": "Fix"},
    )


@pytest.mark.needs_pyvista
class TestSelectionOverlay:
    """A ``Selection`` handed to ``plot_mesh`` is overdrawn in yellow."""

    @staticmethod
    def _capture(source, selection, color="yellow", **kwargs):
        """Render and return the overlay meshes added to the plotter.

        Returns ``[(n_points, n_cells, color, kwargs), ...]`` for every mesh
        drawn in the overlay *color* with an ``opacity`` — the base geometry
        uses the section palette, so it is excluded.
        """
        from unittest.mock import patch

        import pyvista as pv

        from fea_toolkit.plotting import plot_mesh

        captured = []
        real = pv.Plotter.add_mesh

        def _spy(self, mesh, *args, **kw):
            if kw.get("color") == color and "opacity" in kw:
                captured.append((mesh.n_points, mesh.n_cells, kw.get("color"), kw))
            return real(self, mesh, *args, **kw)

        with patch.object(pv.Plotter, "add_mesh", _spy):
            pl = plot_mesh(source, highlight_selection=selection, notebook=True, **kwargs)
        pl.close()
        return captured

    def test_frame_selection_draws_wide_translucent_lines(self):
        """A section selection overdraws exactly the matching frames."""
        from fea_toolkit.model.selection import Selection

        md = _selection_model()
        sel = Selection(element_types=["Frame"], sections=["COL"])

        captured = self._capture(md, sel, show_shells=False)

        assert len(captured) == 1, captured
        _n_points, n_cells, color, kw = captured[0]
        assert color == "yellow"
        assert kw.get("line_width") == 10
        assert kw.get("opacity") == 0.5
        # One straight segment per matched frame (a single 2-point polyline).
        assert n_cells == 1, captured

    def test_node_selection_draws_translucent_dots(self):
        """A node selection draws large, half-opaque dots — no lines."""
        from fea_toolkit.model.selection import Selection

        md = _selection_model()
        sel = Selection(element_types=["Node"], element_ids=["N1", "N3"])

        captured = self._capture(md, sel, show_frames=False, show_shells=False)

        assert len(captured) == 1, captured
        n_points, _n_cells, color, kw = captured[0]
        assert (n_points, color, kw.get("point_size"), kw.get("opacity")) == (
            2,
            "yellow",
            18,
            0.5,
        )

    def test_frames_and_nodes_in_one_selection(self):
        """``element_types=['Frame', 'Node']`` overlays both kinds of mark."""
        from fea_toolkit.model.selection import Selection

        md = _selection_model()
        sel = Selection(element_types=["Frame", "Node"], element_ids=["F2", "N4"])

        captured = self._capture(md, sel, show_shells=False)

        styles = sorted((c[0], c[3].get("line_width"), c[3].get("point_size")) for c in captured)
        assert styles == [(1, None, 18), (2, 10, None)], captured

    def test_group_selection_selects_nodes(self):
        """Group membership resolves in the ``Joint:<id>`` reference space."""
        from fea_toolkit.model.selection import Selection

        md = _selection_model()
        sel = Selection(element_types=["Node"], groups=["Cols"])

        captured = self._capture(md, sel, show_frames=False, show_shells=False)

        assert [c[0] for c in captured] == [1]

    def test_area_selection_draws_translucent_faces(self):
        """An area-only selection is not a silent no-op."""
        from fea_toolkit.model.selection import Selection

        md = _selection_model()
        sel = Selection(element_types=["Area"])

        captured = self._capture(md, sel, show_frames=False, show_nodes=False)

        assert len(captured) == 1, captured
        n_points, n_cells, color, kw = captured[0]
        assert (n_points, n_cells, color) == (4, 1, "yellow")
        assert kw.get("opacity") == 0.35

    def test_selection_colour_is_overridable(self):
        from fea_toolkit.model.selection import Selection

        captured = self._capture(
            _selection_model(),
            Selection(element_types=["Area"]),
            color="#00ff00",
            selection_color="#00ff00",
        )

        assert [c[2] for c in captured] == ["#00ff00"]

    def test_empty_selection_warns(self):
        """A selection that matches nothing warns instead of doing nothing."""
        from fea_toolkit.model.selection import Selection

        sel = Selection(element_types=["Frame"], sections=["NOPE"])

        with pytest.warns(UserWarning, match="matched nothing"):
            self._capture(_selection_model(), sel)

    def test_selection_on_data_dict_raises(self, sample_npz_data):
        """A Selection cannot resolve against a model-less NPZ dict."""
        from fea_toolkit.model.selection import Selection
        from fea_toolkit.plotting import plot_mesh

        with pytest.raises(ValueError, match="data dict"):
            plot_mesh(
                sample_npz_data,
                highlight_selection=Selection(element_types=["Frame"]),
                notebook=True,
            )

    def test_explicit_ids_work_on_data_dict(self, sample_npz_data):
        """Explicit ID sets are the model-less path for NPZ data."""
        captured = self._capture(
            sample_npz_data,
            {"frames": ["2"], "nodes": ["3"]},
            show_frames=True,
            show_nodes=True,
        )

        styles = {(c[1], c[3].get("line_width"), c[3].get("point_size")) for c in captured}
        assert styles == {(1, 10, None), (1, None, 18)}, captured

    def test_story_criterion_is_rejected(self):
        """``story`` needs storey data — fail loudly, not by matching nothing."""
        from fea_toolkit.model.selection import Selection
        from fea_toolkit.plotting import plot_mesh

        with pytest.raises(ValueError, match="storey data"):
            plot_mesh(
                _selection_model(),
                highlight_selection=Selection(element_types=["Frame"], story=["Level 2"]),
                notebook=True,
            )

    def test_non_selection_entry_is_rejected(self):
        from fea_toolkit.plotting import plot_mesh

        with pytest.raises(TypeError, match="must be Selection objects"):
            plot_mesh(_selection_model(), highlight_selection=["F1"], notebook=True)

    def test_constraint_selection_draws_joint_dots(self):
        """A constraint criterion selects the joints assigned to that group."""
        from fea_toolkit.model.selection import Selection

        md = _selection_model()
        sel = Selection(constraints=["Fix"])

        captured = self._capture(md, sel, show_frames=False, show_shells=False)

        assert len(captured) == 1, captured
        n_points, _n_cells, color, kw = captured[0]
        assert n_points == 3, captured  # the three joints assigned to "Fix"
        assert (color, kw.get("point_size")) == ("yellow", 18)

    def test_constraint_criterion_on_mesh_source_raises(self):
        """A MeshModel/builder source carries no constraint_assignments."""
        from fea_toolkit.model.mesh_model import MeshModel
        from fea_toolkit.model.sap_data import FrameElement, Node
        from fea_toolkit.model.selection import Selection
        from fea_toolkit.plotting import plot_mesh

        mesh = MeshModel(
            nodes={"1": Node("1", 1, 0.0, 0.0, 0.0)},
            frame_elements={"1": FrameElement("1", 1, "1", "1")},
            frame_assignments={},
            area_elements={},
            area_assignments={},
            frame_dist_loads=[],
        )

        with pytest.raises(ValueError, match="constraint_assignments"):
            plot_mesh(
                mesh,
                highlight_selection=Selection(constraints=["Fix"]),
                notebook=True,
            )


class TestSplitElementSelectionExpansion:
    """An ID selection reaches split children and their parent."""

    @staticmethod
    def _mesh_model():
        from fea_toolkit.model.mesh_model import MeshModel
        from fea_toolkit.model.sap_data import FrameElement, Node

        return MeshModel(
            nodes={
                "N1": Node("N1", 1, 0.0, 0.0, 0.0),
                "N2": Node("N2", 2, 2.0, 0.0, 0.0),
                "N3": Node("N3", 3, 4.0, 0.0, 0.0),
            },
            frame_elements={
                "P1": FrameElement("P1", 10, "N1", "N3", inactive=True),
                "P1-1": FrameElement("P1-1", 11, "N1", "N2", parent_id="P1"),
                "P1-2": FrameElement("P1-2", 12, "N2", "N3", parent_id="P1"),
                "P2": FrameElement("P2", 20, "N1", "N3"),
            },
            frame_assignments={"P1-1": "BEAM", "P1-2": "BEAM", "P2": "BEAM"},
            area_elements={},
            area_assignments={},
            frame_dist_loads=[],
            sections={},
            groups={},
        )

    def test_expand_split_frames(self):
        from fea_toolkit.plotting.viz_model import _expand_split_frames

        model = self._mesh_model()

        # A child ID reaches the parent and all its siblings …
        assert _expand_split_frames(model, {"P1-1"}) == {"P1", "P1-1", "P1-2"}
        # … and a parent ID reaches all of its children.
        assert _expand_split_frames(model, {"P1"}) == {"P1", "P1-1", "P1-2"}
        # Unsplittable elements are untouched.
        assert _expand_split_frames(model, {"P2"}) == {"P2"}

    def test_selection_id_sets_expand_children(self):
        from fea_toolkit.model.selection import Selection
        from fea_toolkit.plotting.viz_model import _selection_id_sets

        frames, nodes, areas = _selection_id_sets(
            self._mesh_model(), Selection(element_types=["Frame"], element_ids=["P1-2"])
        )

        assert frames == {"P1", "P1-1", "P1-2"}
        assert nodes == set()
        assert areas == set()


# ============================================================================
# Model-viewer mode index
# ============================================================================


class TestViewModelModeIndex:
    """``examples/view_model.py`` presents modes 1-based to the user."""

    @staticmethod
    def _args(mode):
        from argparse import Namespace

        return Namespace(mode=mode)

    def test_first_mode_is_one(self):
        from examples.view_model import mode_index

        assert mode_index(self._args(1)) == 0
        assert mode_index(self._args(3)) == 2

    def test_zero_or_negative_mode_exits(self):
        from examples.view_model import mode_index

        for bad in (0, -1):
            with pytest.raises(SystemExit):
                mode_index(self._args(bad))


# ============================================================================
# Model-viewer constraint highlighting
# ============================================================================


class TestViewModelConstraintHighlight:
    """``--highlight-constraint`` resolves SAP2000 constraint groups to joints.

    The resolution is type-agnostic (it reads the assignment table, not the
    definition type), so ``BODY`` rigid bodies, ``DIAPHRAGM`` groups and the
    other constraint types all work.
    """

    @staticmethod
    def _args(names):
        from argparse import Namespace

        return Namespace(highlight_constraint=names)

    @staticmethod
    def _md():
        from types import SimpleNamespace

        from fea_toolkit.model.sap_data import Constraint, Node

        return SimpleNamespace(
            constraints={
                "Fix": Constraint("Fix", "BODY"),
                "D1": Constraint("D1", "DIAPHRAGM"),
            },
            constraint_assignments={"1": "Fix", "2": "Fix", "10": "D1"},
            nodes={
                "1": Node("1", 1, 0.0, 0.0, 0.0),
                "2": Node("2", 2, 1.0, 0.0, 0.0),
                "10": Node("10", 10, 0.0, 0.0, 3.0),
            },
        )

    def test_unused_option_returns_empty(self):
        from examples.view_model import constraint_node_colors

        assert constraint_node_colors(self._args(None), self._md()) == {}
        assert constraint_node_colors(self._args([]), self._md()) == {}

    def test_resolves_body_group(self, capsys):
        from examples.view_model import constraint_node_colors

        colors = constraint_node_colors(self._args(["Fix"]), self._md())

        assert set(colors) == {"1", "2"}
        assert set(colors.values()) == {"#ff2d2d"}
        assert "Highlighting constraint 'Fix' (BODY): 2 of 2" in capsys.readouterr().out

    def test_multiple_groups_are_unioned(self):
        from examples.view_model import constraint_node_colors

        colors = constraint_node_colors(self._args(["Fix", "D1"]), self._md())

        assert set(colors) == {"1", "2", "10"}

    def test_unknown_name_is_reported_and_ignored(self, capsys):
        from examples.view_model import constraint_node_colors

        assert constraint_node_colors(self._args(["Nope"]), self._md()) == {}
        assert "not defined in the model" in capsys.readouterr().out

    def test_joints_missing_from_the_model_are_skipped(self, capsys):
        """A joint removed by the importer must not colour a different node."""
        from examples.view_model import constraint_node_colors

        md = self._md()
        md.constraint_assignments = {**md.constraint_assignments, "99": "Fix"}

        colors = constraint_node_colors(self._args(["Fix"]), md)

        assert set(colors) == {"1", "2"}
        assert "2 of 3 assigned joint(s) present" in capsys.readouterr().out


# ============================================================================
# Model-viewer selection expressions (--select)
# ============================================================================


class TestViewModelSelectionExpression:
    """CLI behaviour of ``--select``: parsing delegates to
    :meth:`Selection.from_string`, the CLI adds warnings and exits."""

    @staticmethod
    def _args(exprs):
        from argparse import Namespace

        return Namespace(select=exprs)

    def test_unused_option_returns_none(self):
        from examples.view_model import selection_highlights

        assert selection_highlights(self._args(None)) is None
        assert selection_highlights(self._args([])) is None

    def test_multiple_expressions_become_multiple_selections(self):
        from examples.view_model import selection_highlights

        sels = selection_highlights(self._args(["type=Frame; section=2xR3", "id=1,2"]))

        assert [s.sections for s in sels] == [["2xR3"], None]
        assert [s.element_ids for s in sels] == [None, ["1", "2"]]

    def test_bad_expression_exits_with_message(self):
        from examples.view_model import selection_highlights

        with pytest.raises(SystemExit) as exc:
            selection_highlights(self._args(["bogus=1"]))

        assert "unknown selection key" in str(exc.value)

    def test_node_only_selection_warns_about_ignored_criteria(self, capsys):
        """Nodes are matched by type / id / group — say so for section / z."""
        from examples.view_model import selection_highlights

        sels = selection_highlights(self._args(["type=Node; z=0:3.4"]))

        assert len(sels) == 1
        assert "Node-only Selection" in capsys.readouterr().out

    def test_frames_with_elevation_do_not_warn(self, capsys):
        from examples.view_model import selection_highlights

        selection_highlights(self._args(["type=Frame; z=0:3.4"]))

        assert capsys.readouterr().out == ""

    def test_constraint_only_does_not_warn(self, capsys):
        from examples.view_model import selection_highlights

        selection_highlights(self._args(["constraint=Fix"]))

        assert capsys.readouterr().out == ""

    def test_constraint_with_element_type_warns(self, capsys):
        """constraint= selects joints, so type=Frame cannot match anything."""
        from examples.view_model import selection_highlights

        selection_highlights(self._args(["constraint=Fix; type=Frame"]))

        assert "matches nothing" in capsys.readouterr().out

    def test_constraint_with_section_warns_about_ignored_criteria(self, capsys):
        from examples.view_model import selection_highlights

        selection_highlights(self._args(["constraint=Fix; section=2xR3"]))

        assert "Node-only Selection" in capsys.readouterr().out


# ============================================================================
# View-model input formats (.s2k / raw-table JSON / model-codec JSON)
# ============================================================================


class TestViewModelInputPolicy:
    """CLI policy for what a loaded model can be asked to do."""

    @staticmethod
    def _mesh_snapshot():
        """A post-preprocessing MeshModel — codec-serialisable, not analysable."""
        from fea_toolkit.model.mesh_model import MeshModel
        from fea_toolkit.model.sap_data import FrameElement, Node

        return MeshModel(
            nodes={"1": Node("1", 1, 0.0, 0.0, 0.0)},
            frame_elements={"1": FrameElement("1", 1, "1", "1")},
            frame_assignments={},
            area_elements={},
            area_assignments={},
            frame_dist_loads=[],
        )

    def test_mesh_snapshot_only_supports_the_mesh_view(self):
        """An already-meshed snapshot has no input for an analysis."""
        from examples.view_model import check_result_supported

        check_result_supported(self._mesh_snapshot(), "mesh")  # no raise

        with pytest.raises(SystemExit) as exc:
            check_result_supported(self._mesh_snapshot(), "static")

        assert "mesh only" in str(exc.value)

    def test_sap_model_snapshot_supports_every_result(self):
        from examples.view_model import check_result_supported

        check_result_supported(_selection_model(), "static")  # no raise

    def test_newer_schema_version_is_rejected(self, tmp_path):
        """A snapshot from a newer build is refused, not mis-decoded."""
        import json as _json

        from examples.view_model import load_model
        from fea_toolkit.io.model_codec import SCHEMA_KEY, model_to_dict

        payload = model_to_dict(_selection_model())
        payload[SCHEMA_KEY] = 999
        path = tmp_path / "future.json"
        path.write_text(_json.dumps(payload), encoding="utf-8")

        with pytest.raises(SystemExit) as exc:
            load_model(path)

        assert "upgrade fea_toolkit" in str(exc.value)
