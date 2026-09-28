"""Regression tests for the PyVista render backend force-flag path.

Covers :meth:`fea_toolkit.plotting.renderers.pyvista.PyVistaRenderer.render_force_flags`
and the user-facing :meth:`~fea_toolkit.plotting.ModelViewer.overlay_forces`
route.  The renderer previously raised ``ModuleNotFoundError`` (wrong
relative import), ``TypeError`` (wrong argument list — no flag normal was
computed), and ``ValueError`` (the ``compute_flag_parts`` generator was
unpacked as if it returned a fixed ``(verts, tris, colors)`` 3-tuple).
"""

import numpy as np
import openseespy.opensees as ops
import pytest

#: Every test here drives the PyVista render backend.
pytestmark = pytest.mark.needs_pyvista


def _frame(elem_id="1", start=(0.0, 0.0, 0.0), end=(0.0, 0.0, 3.0)):
    """Build a minimal :class:`FrameGeom` for renderer tests."""
    from fea_toolkit.plotting.renderers.base import FrameGeom

    return FrameGeom(
        elem_id=elem_id,
        section="UB300",
        node_i="1",
        node_j="2",
        start=np.array(start, dtype=float),
        end=np.array(end, dtype=float),
    )


class TestRenderForceFlags:
    """``PyVistaRenderer.render_force_flags`` builds and clears geometry."""

    def test_render_force_flags_builds_actor(self):
        from fea_toolkit.plotting.renderers.pyvista import PyVistaRenderer

        renderer = PyVistaRenderer(off_screen=True)
        try:
            # Double-curvature moment -> a single trapezoid flag polygon.
            renderer.render_force_flags(
                [_frame()], {"1": (10.0, -4.0)}, quantity="Mz", scale_factor=0.1
            )
            assert len(renderer._actors) == 1
        finally:
            renderer.clear()
            renderer.plotter.close()
        assert renderer._actors == []

    def test_render_force_flags_multiple_quantities(self):
        from fea_toolkit.plotting.renderers.pyvista import PyVistaRenderer

        frames = [
            _frame("1", end=(0.0, 0.0, 3.0)),
            _frame("2", start=(3.0, 0.0, 0.0), end=(3.0, 0.0, 3.0)),
        ]
        forces = {"1": (5.0, 5.0), "2": (0.0, -8.0)}
        renderer = PyVistaRenderer(off_screen=True)
        try:
            for quantity in ("Mz", "Fy", "My"):
                renderer.render_force_flags(frames, forces, quantity=quantity, scale_factor=0.2)
            # Three overlay calls -> three merged-mesh actors.
            assert len(renderer._actors) == 3
        finally:
            renderer.clear()
            renderer.plotter.close()

    def test_render_force_flags_empty_is_noop(self):
        from fea_toolkit.plotting.renderers.pyvista import PyVistaRenderer

        renderer = PyVistaRenderer(off_screen=True)
        renderer.render_force_flags([], {}, quantity="Mz")
        renderer.render_force_flags([], {"1": (1.0, 2.0)})
        # No geometry requested -> no plotter is even created.
        assert renderer._actors == []
        assert renderer._plotter is None

    def test_render_force_flags_zero_force_is_noop(self):
        from fea_toolkit.plotting.renderers.pyvista import PyVistaRenderer

        renderer = PyVistaRenderer(off_screen=True)
        try:
            renderer.render_force_flags([_frame()], {"1": (0.0, 0.0)}, scale_factor=0.1)
            # Zero end forces -> compute_flag_parts yields nothing -> no actor.
            assert renderer._actors == []
        finally:
            renderer.clear()
            if renderer._plotter is not None:
                renderer.plotter.close()


class TestModelViewerOverlayForces:
    """``ModelViewer.overlay_forces`` reaches the fixed renderer path."""

    def test_overlay_forces_end_to_end(self):
        from examples.sample_model import make_sample_model
        from fea_toolkit.plotting import ModelViewer

        md = make_sample_model()
        viewer = ModelViewer(model_data=md, backend="pyvista", off_screen=True)
        try:
            viewer.show_model(show_nodes=False, show_shells=False)
            elem_forces = {"1": {"mz_i_local": 500.0, "mz_j_local": -250.0}}
            viewer.overlay_forces(elem_forces=elem_forces, quantity="Mz")
            assert len(viewer._backend._actors) >= 1
        finally:
            viewer._backend.clear()
            viewer._backend.plotter.close()


class TestFlagDirectionFallback:
    """``_flag_direction`` mirrors ``_compute_flag_direction`` on error."""

    def test_exception_falls_back_and_applies_quantity_mapping(self, monkeypatch):
        from fea_toolkit.model import geometry
        from fea_toolkit.plotting.renderers.pyvista import _flag_direction

        def _boom(_axis):
            raise RuntimeError("boom")

        monkeypatch.setattr(geometry, "get_local_axes", _boom)
        start = np.array([0.0, 0.0, 0.0])
        end = np.array([1.0, 0.0, 0.0])

        # Fallback vy = global Y, vz = global Z, then the quantity map applies.
        assert np.allclose(_flag_direction("Mz", start, end), [0.0, 1.0, 0.0])
        assert np.allclose(_flag_direction("Fx", start, end), [0.0, 0.0, 1.0])
        assert np.allclose(_flag_direction("My", start, end), [0.0, 0.0, -1.0])


class TestFlagDirectionAngle:
    """A nonzero SAP section rotation rotates the flag plane's local axes."""

    def test_nonzero_angle_rotates_direction(self):
        from fea_toolkit.plotting.renderers.pyvista import _flag_direction

        start = np.array([0.0, 0.0, 0.0])
        end = np.array([1.0, 0.0, 0.0])

        # Axis along +X: angle 0 -> vy = +Z; a 90° rotation swaps vy and vz.
        assert np.allclose(_flag_direction("Mz", start, end, 0.0), [0.0, 0.0, 1.0])
        assert np.allclose(_flag_direction("Mz", start, end, 90.0), [0.0, -1.0, 0.0])


class TestEndForceValues:
    """``_end_force_values`` tolerates the toolkits' key conventions."""

    def test_lowercase_local_keys(self):
        from fea_toolkit.plotting.viewer import _end_force_values

        assert _end_force_values({"mz_i_local": 5.0, "mz_j_local": -3.0}, "Mz", True) == (5.0, -3.0)

    def test_builder_uppercase_form(self):
        from fea_toolkit.plotting.viewer import _end_force_values

        assert _end_force_values({"Mz": 7.0, "Mz_j": -2.0}, "Mz", True) == (7.0, -2.0)

    def test_use_local_false_reads_base_keys(self):
        from fea_toolkit.plotting.viewer import _end_force_values

        assert _end_force_values({"mz_i": 4.0, "mz_j": 1.0}, "Mz", False) == (4.0, 1.0)

    def test_local_rejects_global_keys(self):
        """``use_local=True`` must not read documented global ``{q}_i`` keys."""
        from fea_toolkit.plotting.viewer import _end_force_values

        assert _end_force_values({"mz_i": 4.0, "mz_j": 1.0}, "Mz", True) is None

    def test_global_rejects_bare_local_keys(self):
        """``use_local=False`` must not read the bare local builder form."""
        from fea_toolkit.plotting.viewer import _end_force_values

        assert _end_force_values({"Mz": 7.0, "Mz_j": -2.0}, "Mz", False) is None

    def test_missing_quantity_returns_none(self):
        from fea_toolkit.plotting.viewer import _end_force_values

        assert _end_force_values({"fx_i": 1.0}, "Mz", True) is None


class TestOverlayForcesBuilderPath:
    """The documented ``ModelViewer(builder)`` usage works end-to-end."""

    def test_builder_geometry_and_overlay_forces(self):
        """``ModelViewer(builder)`` extracts geometry and renders flags.

        Regressions: ``_extract_geometry`` read the removed
        ``builder.split_elements`` attribute (``AttributeError``), and
        ``overlay_forces`` read the never-set ``builder._last_static_results``
        (silent no-op).
        """
        from examples.sample_model import make_sample_model
        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
        from fea_toolkit.opensees.preprocessor import preprocess_model
        from fea_toolkit.plotting import ModelViewer

        md = make_sample_model()
        cfg = {"element_type": "elasticBeamColumn", "verbose": False, "create_shells": False}
        mesh = preprocess_model(md, cfg)
        builder = AnalysisBuilder(mesh, cfg)
        viewer = None
        try:
            builder.build_domain()
            builder.create_loads({"DEAD": 1.0})
            builder.run_static_analysis()

            viewer = ModelViewer(builder=builder, backend="pyvista", off_screen=True)
            viewer.show_model(show_nodes=False, show_shells=False)
            assert len(viewer._frames) == 1

            n_before = len(viewer._backend._actors)
            # The sample model's DEAD case is axial (Fx ≈ 2000, Mz ≈ 0).
            viewer.overlay_forces(quantity="Fx")
            assert len(viewer._backend._actors) == n_before + 1
        finally:
            if viewer is not None:
                viewer._backend.clear()
                viewer._backend.plotter.close()
            ops.wipe()

    def test_overlay_forces_use_local_false_warns(self):
        from examples.sample_model import make_sample_model
        from fea_toolkit.plotting import ModelViewer

        md = make_sample_model()
        viewer = ModelViewer(model_data=md, backend="pyvista", off_screen=True)
        try:
            viewer.show_model(show_nodes=False, show_shells=False)
            elem_forces = {"1": {"mz_i": 500.0, "mz_j": -250.0}}
            with pytest.warns(UserWarning, match="use_local=False"):
                viewer.overlay_forces(elem_forces=elem_forces, use_local=False)
        finally:
            viewer._backend.clear()
            viewer._backend.plotter.close()

    def test_build_domain_invalidates_cached_results(self):
        """Rebuilding the domain drops stale cached static results.

        Regression: ``_last_static_results`` was only ever assigned on a
        successful run, so a rebuilt (or wiped) domain still exposed the
        previous run's displacements to result-aware viewers.
        """
        from examples.sample_model import make_sample_model
        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
        from fea_toolkit.opensees.preprocessor import preprocess_model

        md = make_sample_model()
        cfg = {"element_type": "elasticBeamColumn", "verbose": False, "create_shells": False}
        mesh = preprocess_model(md, cfg)
        builder = AnalysisBuilder(mesh, cfg)
        try:
            builder.build_domain()
            builder.create_loads({"DEAD": 1.0})
            builder.run_static_analysis()
            assert builder._last_static_results is not None

            builder.build_domain()
            assert builder._last_static_results is None
        finally:
            ops.wipe()

    def test_builder_overlay_deformed_uses_cached_results(self):
        """``overlay_deformed()`` reads the builder's cached static results.

        Regression: the builder never populated ``_last_static_results`` and
        the viewer looked displacements up by node *tag* while the result
        dict is keyed by node *id*.
        """
        from examples.sample_model import make_sample_model
        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
        from fea_toolkit.opensees.preprocessor import preprocess_model
        from fea_toolkit.plotting import ModelViewer

        md = make_sample_model()
        cfg = {"element_type": "elasticBeamColumn", "verbose": False, "create_shells": False}
        mesh = preprocess_model(md, cfg)
        builder = AnalysisBuilder(mesh, cfg)
        viewer = None
        try:
            builder.build_domain()
            builder.create_loads({"DEAD": 1.0})
            builder.run_static_analysis()
            assert hasattr(builder, "_last_static_results")

            viewer = ModelViewer(builder=builder, backend="pyvista", off_screen=True)
            viewer.show_model(show_nodes=False, show_shells=False)
            n_before = len(viewer._backend._actors)
            viewer.overlay_deformed(scale=10.0)
            assert len(viewer._backend._actors) == n_before + 1
        finally:
            if viewer is not None:
                viewer._backend.clear()
                viewer._backend.plotter.close()
            ops.wipe()


class TestModelViewerSplitElements:
    """``show_model`` selects the right element set for split models."""

    def test_active_and_collapsed_element_counts(self):
        """Default view shows every active element; collapse shows parents.

        Regression: the default ``collapse_to_parents=False`` view dropped
        active split children, and the collapsed view included them.
        """
        from pathlib import Path

        from fea_toolkit.io.s2k_parser import SAP2000Parser
        from fea_toolkit.opensees.preprocessor import preprocess_model
        from fea_toolkit.plotting import ModelViewer

        fixture = Path(__file__).parent / "fixtures" / "sample.s2k"
        if not fixture.exists():
            pytest.skip(f"Fixture missing: {fixture}")

        md = SAP2000Parser(fixture).parse().get_model_data()
        cfg = {
            "element_type": "elasticBeamColumn",
            "verbose": False,
            "create_shells": False,
            "split_elements": True,
        }
        mm = preprocess_model(md, cfg)

        n_active = sum(1 for e in mm.frame_elements.values() if not getattr(e, "inactive", False))
        n_children = sum(
            1
            for e in mm.frame_elements.values()
            if not getattr(e, "inactive", False) and getattr(e, "parent_id", None) is not None
        )
        n_collapsed = sum(
            1
            for e in mm.frame_elements.values()
            if getattr(e, "inactive", False) or getattr(e, "parent_id", None) is None
        )
        assert n_children > 0, "fixture must contain split children for this regression"

        active = ModelViewer(mesh_model=mm, backend="pyvista", off_screen=True)
        collapsed = ModelViewer(
            mesh_model=mm, backend="pyvista", off_screen=True, collapse_to_parents=True
        )
        try:
            active.show_model(show_nodes=False, show_shells=False)
            collapsed.show_model(show_nodes=False, show_shells=False)
            assert len(active._frames) == n_active
            assert len(collapsed._frames) == n_collapsed
        finally:
            active._backend.plotter.close()
            collapsed._backend.plotter.close()


def _shell(area_id="1", section="SLAB", n_vertices=4):
    """Build a planar area element: a triangle, a quad or a pentagon."""
    from fea_toolkit.plotting.renderers.base import ShellGeom

    polygons = {
        3: [[0.0, 0.0, 0.0], [2.0, 0.0, 0.0], [1.0, 2.0, 0.0]],
        4: [[0.0, 0.0, 0.0], [2.0, 0.0, 0.0], [2.0, 1.0, 0.0], [0.0, 1.0, 0.0]],
        5: [
            [0.0, 0.0, 0.0],
            [2.0, 0.0, 0.0],
            [3.0, 1.0, 0.0],
            [1.0, 2.0, 0.0],
            [-1.0, 1.0, 0.0],
        ],
    }
    return ShellGeom(
        area_id=area_id,
        section=section,
        vertices=np.array(polygons[n_vertices], dtype=float),
    )


def _quad_geometry() -> dict:
    """One quad area element (2 x 1) on four nodes — archive-shaped geometry."""
    return {
        "node_tag": np.array([1, 2, 3, 4]),
        "node_x": np.array([0.0, 2.0, 2.0, 0.0]),
        "node_y": np.array([0.0, 0.0, 1.0, 1.0]),
        "node_z": np.zeros(4),
        "shell_eid": np.array([1]),
        "shell_sap_id": np.array(["1"]),
        "shell_sec_name": np.array(["SLAB"]),
        "shell_node_1": np.array([1]),
        "shell_node_2": np.array([2]),
        "shell_node_3": np.array([3]),
        "shell_node_4": np.array([4]),
    }


class TestShellFaces:
    """Area elements are drawn as their own polygon — no fan diagonal.

    A fan split every quad into two triangles, and with ``show_edges`` on, the
    **invented diagonal was drawn** across every slab.  It also mattered under
    deformation, where a displaced quad is generally non-planar and VTK's choice
    of diagonal is arbitrary (``docs/dev_notes.md`` → *Quads stay quads*).
    """

    def test_a_quad_shell_is_one_quad_face(self):
        """One cell with four corners — the diagonal is not a cell edge."""
        from fea_toolkit.plotting.renderers.pyvista import PyVistaRenderer

        renderer = PyVistaRenderer(off_screen=True)
        try:
            renderer.render_shells([_shell()], {"SLAB": (0.7, 0.7, 0.7)})
            mesh = renderer.actors("shells")[0].mapper.dataset
            assert mesh.n_cells == 1
            assert mesh.get_cell(0).n_points == 4
            assert mesh.faces.tolist() == [4, 0, 1, 2, 3]
        finally:
            renderer.clear()
            renderer.plotter.close()

    def test_a_triangle_shell_stays_a_triangle(self):
        from fea_toolkit.plotting.renderers.pyvista import PyVistaRenderer

        renderer = PyVistaRenderer(off_screen=True)
        try:
            renderer.render_shells([_shell(n_vertices=3)], {"SLAB": (0.7, 0.7, 0.7)})
            mesh = renderer.actors("shells")[0].mapper.dataset
            assert mesh.n_cells == 1
            assert mesh.faces.tolist() == [3, 0, 1, 2]
        finally:
            renderer.clear()
            renderer.plotter.close()

    def test_a_pentagon_is_fanned_deterministically(self):
        """5+ sided elements have no quad to preserve, so they fan from vertex 0."""
        from fea_toolkit.plotting.renderers.pyvista import PyVistaRenderer

        renderer = PyVistaRenderer(off_screen=True)
        try:
            renderer.render_shells([_shell(n_vertices=5)], {"SLAB": (0.7, 0.7, 0.7)})
            mesh = renderer.actors("shells")[0].mapper.dataset
            assert mesh.n_cells == 3
            assert mesh.faces.tolist() == [3, 0, 1, 2, 3, 0, 2, 3, 3, 0, 3, 4]
        finally:
            renderer.clear()
            renderer.plotter.close()

    def test_mixed_polygons_keep_their_own_shapes(self):
        """One mesh, three element shapes — each cell keeps its vertex count."""
        from fea_toolkit.plotting.renderers.pyvista import PyVistaRenderer

        shells = [_shell("1", n_vertices=3), _shell("2", n_vertices=4), _shell("3", n_vertices=5)]
        renderer = PyVistaRenderer(off_screen=True)
        try:
            renderer.render_shells(shells, {"SLAB": (0.7, 0.7, 0.7)})
            mesh = renderer.actors("shells")[0].mapper.dataset
            assert mesh.n_cells == 5
            assert [mesh.get_cell(i).n_points for i in range(mesh.n_cells)] == [3, 4, 3, 3, 3]
        finally:
            renderer.clear()
            renderer.plotter.close()

    def test_the_colour_array_has_one_entry_per_face(self):
        """Colours are per *cell*; a quad now contributes one, not two."""
        from fea_toolkit.plotting.renderers.pyvista import PyVistaRenderer

        renderer = PyVistaRenderer(off_screen=True)
        try:
            renderer.render_shells(
                [_shell("1"), _shell("2"), _shell("3", n_vertices=5)],
                {"SLAB": (1.0, 0.0, 0.0)},
            )
            mesh = renderer.actors("shells")[0].mapper.dataset
            assert mesh.n_cells == 5  # quad + quad + three pentagon triangles
            assert mesh.cell_data["rgb"].shape == (5, 3)
        finally:
            renderer.clear()
            renderer.plotter.close()

    def test_a_highlighted_quad_is_also_a_single_face(self):
        """The highlight overlay fanned quads the same way; it is fixed with it."""
        from fea_toolkit.plotting.renderers.base import HighlightDef
        from fea_toolkit.plotting.renderers.pyvista import PyVistaRenderer

        renderer = PyVistaRenderer(off_screen=True)
        try:
            renderer.render_highlights(
                [HighlightDef(area_ids=["1"], shells=[_shell()], color=(1.0, 0.0, 0.0))]
            )
            mesh = renderer.actors("highlights")[0].mapper.dataset
            assert mesh.n_cells == 1
            assert mesh.get_cell(0).n_points == 4
        finally:
            renderer.clear()
            renderer.plotter.close()

    def test_a_modelviewer_shell_quad_has_no_diagonal(self):
        """End to end through the viewer the GUI drives — one quad, one cell.

        The geometry comes from an archive-shaped dict, so this is the path a
        results view takes: ``mesh_model_from_geometry`` → ``ModelViewer`` →
        ``render_shells``.
        """
        from fea_toolkit.io.results_repository import mesh_model_from_geometry
        from fea_toolkit.plotting.viewer import ModelViewer

        viewer = ModelViewer(
            mesh_model=mesh_model_from_geometry(_quad_geometry()),
            backend="pyvista",
            off_screen=True,
        )
        try:
            viewer.show_model(show_nodes=False, show_shells=True)
            mesh = viewer._backend.actors("shells")[0].mapper.dataset
            assert mesh.n_cells == 1
            assert mesh.get_cell(0).n_points == 4
        finally:
            viewer._backend.plotter.close()


class TestShrinkAndOpacity:
    """The two display-quality knobs: shrink (geometry) and opacity (an actor).

    Both are *display* transforms — the geometry the caller passes in is never
    modified — which is what lets the same model be drawn true-sized or shrunken
    without touching selection or results.
    """

    def test_shrinking_frames_pulls_the_endpoints_toward_the_midpoint(self):
        from fea_toolkit.plotting.renderers.pyvista import PyVistaRenderer

        renderer = PyVistaRenderer(off_screen=True)
        try:
            renderer.render_frames(
                [_frame(start=(0.0, 0.0, 0.0), end=(0.0, 0.0, 10.0))],
                {"UB300": (1.0, 0.0, 0.0)},
                shrink=0.9,
            )
            points = np.asarray(renderer.actors("frames")[0].mapper.dataset.points)
            assert points[0] == pytest.approx([0.0, 0.0, 0.5])  # 5% of 10 either end
            assert points[1] == pytest.approx([0.0, 0.0, 9.5])
        finally:
            renderer.clear()
            renderer.plotter.close()

    def test_shrinking_shells_scales_them_about_their_centroid(self):
        """The 2 x 1 quad's centroid is (1, 0.5); half size pulls it inward."""
        from fea_toolkit.plotting.renderers.pyvista import PyVistaRenderer

        renderer = PyVistaRenderer(off_screen=True)
        try:
            renderer.render_shells([_shell()], {"SLAB": (0.7, 0.7, 0.7)}, shrink=0.5)
            points = np.asarray(renderer.actors("shells")[0].mapper.dataset.points)
            assert points[:, 0].min() == pytest.approx(0.5)
            assert points[:, 0].max() == pytest.approx(1.5)
            assert points[:, 1].min() == pytest.approx(0.25)
            assert points[:, 1].max() == pytest.approx(0.75)
        finally:
            renderer.clear()
            renderer.plotter.close()

    def test_the_geometry_passed_in_is_never_modified(self):
        """Display-only: the caller's own arrays survive a shrunk draw."""
        from fea_toolkit.plotting.renderers.pyvista import PyVistaRenderer

        shell = _shell()
        original = shell.vertices.copy()
        renderer = PyVistaRenderer(off_screen=True)
        try:
            renderer.render_shells([shell], {"SLAB": (0.7, 0.7, 0.7)}, shrink=0.5)
        finally:
            renderer.clear()
            renderer.plotter.close()
        assert np.array_equal(shell.vertices, original)

    def test_a_highlight_is_shrunk_with_the_model_it_marks(self):
        """Otherwise the highlight sticks out past the element it marks."""
        from fea_toolkit.plotting.renderers.base import HighlightDef
        from fea_toolkit.plotting.renderers.pyvista import PyVistaRenderer

        renderer = PyVistaRenderer(off_screen=True)
        try:
            renderer.render_highlights(
                [HighlightDef(area_ids=["1"], shells=[_shell()], color=(1.0, 0.0, 0.0))],
                shrink=0.5,
            )
            points = np.asarray(renderer.actors("highlights")[0].mapper.dataset.points)
            assert points[:, 0].min() == pytest.approx(0.5)
            assert points[:, 0].max() == pytest.approx(1.5)
        finally:
            renderer.clear()

    def test_opacity_reaches_the_shell_actor(self):
        from fea_toolkit.plotting.renderers.pyvista import PyVistaRenderer

        renderer = PyVistaRenderer(off_screen=True)
        try:
            renderer.render_shells([_shell()], {"SLAB": (0.7, 0.7, 0.7)}, opacity=0.4)
            actor = renderer.actors("shells")[0]
            assert actor.GetProperty().GetOpacity() == pytest.approx(0.4)
        finally:
            renderer.clear()
            renderer.plotter.close()

    def test_opacity_can_be_changed_without_rebuilding_the_mesh(self):
        """What keeps a transparency control interactive on a large model."""
        from fea_toolkit.plotting.renderers.pyvista import PyVistaRenderer

        renderer = PyVistaRenderer(off_screen=True)
        try:
            renderer.render_shells([_shell()], {"SLAB": (0.7, 0.7, 0.7)})
            actor = renderer.actors("shells")[0]

            renderer.set_category_opacity("shells", 0.3)

            # The *actor* is the same object — only its opacity changed, so no
            # mesh was rebuilt.  (Not ``mapper.dataset is …``: PyVista wraps the
            # same VTK data in a new Python object on every access.)
            assert renderer.actors("shells") == [actor], "the mesh was rebuilt"
            assert actor.GetProperty().GetOpacity() == pytest.approx(0.3)
        finally:
            renderer.clear()
            renderer.plotter.close()

    def test_show_model_draws_shrunk_while_keeping_true_geometry(self):
        """The viewer keeps the model as the source of truth for everything else."""
        from fea_toolkit.io.results_repository import mesh_model_from_geometry
        from fea_toolkit.plotting.viewer import ModelViewer

        viewer = ModelViewer(
            mesh_model=mesh_model_from_geometry(_quad_geometry()),
            backend="pyvista",
            off_screen=True,
        )
        try:
            viewer.show_model(show_nodes=False, show_shells=True, shrink=0.5)
            assert viewer._shrink == pytest.approx(0.5)
            # The extracted geometry is still the true quad...
            assert viewer._shells[0].vertices[:, 0].max() == pytest.approx(2.0)
            # ...while what was drawn is the half-size one.
            points = np.asarray(viewer._backend.actors("shells")[0].mapper.dataset.points)
            assert points[:, 0].max() == pytest.approx(1.5)
        finally:
            viewer._backend.plotter.close()

    def test_shell_opacity_is_independent_of_the_model_opacity(self):
        """A slab goes translucent while the frame lines stay crisp."""
        from fea_toolkit.io.results_repository import mesh_model_from_geometry
        from fea_toolkit.plotting.viewer import ModelViewer

        viewer = ModelViewer(
            mesh_model=mesh_model_from_geometry(_quad_geometry()),
            backend="pyvista",
            off_screen=True,
        )
        try:
            viewer.show_model(show_nodes=False, show_shells=True, shell_opacity=0.5)
            shells = viewer._backend.actors("shells")[0]
            assert shells.GetProperty().GetOpacity() == pytest.approx(0.5)
            # This model has no frames, so draw one to check it stayed opaque.
            viewer._backend.render_frames([_frame()], {"UB300": (1.0, 0.0, 0.0)})
            frames = viewer._backend.actors("frames")[0]
            assert frames.GetProperty().GetOpacity() == pytest.approx(1.0)
        finally:
            viewer._backend.plotter.close()

    def test_shell_opacity_falls_back_to_the_single_opacity_knob(self):
        """``opacity`` alone still governs everything — the pre-existing behaviour."""
        from fea_toolkit.io.results_repository import mesh_model_from_geometry
        from fea_toolkit.plotting.viewer import ModelViewer

        viewer = ModelViewer(
            mesh_model=mesh_model_from_geometry(_quad_geometry()),
            backend="pyvista",
            off_screen=True,
        )
        try:
            viewer.show_model(show_nodes=False, show_shells=True, opacity=0.4)
            shells = viewer._backend.actors("shells")[0]
            assert shells.GetProperty().GetOpacity() == pytest.approx(0.4)
        finally:
            viewer._backend.plotter.close()

    def test_set_shell_opacity_applies_in_place(self):
        """The live path a GUI control uses, and the value is remembered."""
        from fea_toolkit.io.results_repository import mesh_model_from_geometry
        from fea_toolkit.plotting.viewer import ModelViewer

        viewer = ModelViewer(
            mesh_model=mesh_model_from_geometry(_quad_geometry()),
            backend="pyvista",
            off_screen=True,
        )
        try:
            viewer.show_model(show_nodes=False, show_shells=True)
            actor = viewer._backend.actors("shells")[0]
            viewer.set_shell_opacity(0.35)
            assert viewer._shell_opacity == pytest.approx(0.35)
            assert actor.GetProperty().GetOpacity() == pytest.approx(0.35)
        finally:
            viewer._backend.plotter.close()


def _restraint(node_id="1", position=(0.0, 0.0, 0.0), dofs=(1, 1, 1, 0, 0, 0)):
    """Build a restrained node for renderer tests."""
    from fea_toolkit.plotting.renderers.base import RestraintGeom

    return RestraintGeom(
        node_id=node_id,
        position=np.array(position, dtype=float),
        dofs=tuple(dofs),
    )


class TestRestraintGlyphs:
    """Support symbols: one glyph per restrained DOF.

    Arrows for translations and curls for rotations, which is what makes **any**
    restraint set drawable — not only the textbook patterns — and what the
    classic-symbol refinement will keep as its fallback.
    """

    def test_a_translation_is_an_arrow_pointing_at_the_joint(self):
        from fea_toolkit.plotting.restraint_glyphs import support_glyph

        glyph = support_glyph(np.zeros(3), [1, 0, 0, 0, 0, 0], 1.0)
        xmin, xmax, ymin, ymax, _zmin, _zmax = glyph.bounds
        assert (xmin, xmax) == pytest.approx((0.0, 1.0))  # tail a size away, head on it
        # Thin about its own axis, so three of them read as three DOFs.
        assert abs(ymin) < 0.2
        assert abs(ymax) < 0.2

    def test_a_y_translation_uses_the_y_axis(self):
        from fea_toolkit.plotting.restraint_glyphs import support_glyph

        glyph = support_glyph(np.zeros(3), [0, 1, 0, 0, 0, 0], 1.0)
        assert glyph.bounds[2:4] == pytest.approx((0.0, 1.0))
        assert glyph.bounds[0:2] == pytest.approx((-0.12, 0.12), abs=0.02)

    def test_a_rotation_curls_about_its_axis(self):
        """R1 turns about X, so the glyph spans Y and Z, not X."""
        from fea_toolkit.plotting.restraint_glyphs import support_glyph

        glyph = support_glyph(np.zeros(3), [0, 0, 0, 1, 0, 0], 1.0)
        xmin, xmax, ymin, ymax, zmin, zmax = glyph.bounds
        assert ymin == pytest.approx(-0.5, abs=0.02) and ymax == pytest.approx(0.5, abs=0.02)
        assert zmin <= -0.4 and zmax >= 0.4
        assert abs(xmin) < 0.2 and abs(xmax) < 0.2

    def test_a_pinned_node_draws_three_arrows(self):
        from fea_toolkit.plotting.restraint_glyphs import support_glyph

        glyph = support_glyph(np.zeros(3), [1, 1, 1, 0, 0, 0], 1.0)
        bounds = glyph.bounds
        for low, high in ((0, 1), (2, 3), (4, 5)):
            assert bounds[high] == pytest.approx(1.0)
            assert bounds[low] == pytest.approx(-0.12, abs=0.02)

    def test_a_free_node_draws_nothing(self):
        from fea_toolkit.plotting.restraint_glyphs import support_glyph

        assert support_glyph(np.zeros(3), [0, 0, 0, 0, 0, 0], 1.0) is None

    def test_the_glyph_follows_the_node(self):
        from fea_toolkit.plotting.restraint_glyphs import support_glyph

        glyph = support_glyph(np.array([10.0, 0.0, 0.0]), [1, 0, 0, 0, 0, 0], 1.0)
        assert glyph.bounds[0:2] == pytest.approx((10.0, 11.0))

    def test_render_restraints_adds_a_non_pickable_overlay(self):
        """A support is not a thing to pick — the click belongs to the node."""
        from fea_toolkit.plotting.renderers.pyvista import PyVistaRenderer

        renderer = PyVistaRenderer(off_screen=True)
        try:
            renderer.render_restraints([_restraint()], size=0.5)
            actors = renderer.actors("restraints")
            assert len(actors) == 1
            # VTK's getters return an int, so this is truthiness, not identity.
            assert not actors[0].GetPickable()
        finally:
            renderer.clear()
            renderer.plotter.close()

    def test_the_category_toggle_hides_the_symbols(self):
        from fea_toolkit.plotting.renderers.pyvista import PyVistaRenderer

        renderer = PyVistaRenderer(off_screen=True)
        try:
            renderer.render_restraints([_restraint()], size=0.5)
            actor = renderer.actors("restraints")[0]

            renderer.set_category_visible("restraints", False)
            assert not actor.GetVisibility()
            renderer.set_category_visible("restraints", True)
            assert actor.GetVisibility()
        finally:
            renderer.clear()
            renderer.plotter.close()

    def test_a_free_node_contributes_no_actor(self):
        from fea_toolkit.plotting.renderers.pyvista import PyVistaRenderer

        renderer = PyVistaRenderer(off_screen=True)
        try:
            renderer.render_restraints([_restraint(dofs=(0, 0, 0, 0, 0, 0))], size=0.5)
            assert renderer.actors("restraints") == []
        finally:
            renderer.clear()
            renderer.plotter.close()

    def test_show_model_draws_the_supports_it_found(self):
        """End to end: the model's restraints reach the scene, scaled to it."""
        from fea_toolkit.io.results_repository import mesh_model_from_geometry
        from fea_toolkit.model.sap_data import Restraint
        from fea_toolkit.plotting.viewer import ModelViewer

        model = mesh_model_from_geometry(_quad_geometry())
        model.restraints = {"1": Restraint([1, 1, 1, 1, 1, 1])}
        viewer = ModelViewer(mesh_model=model, backend="pyvista", off_screen=True)
        try:
            viewer.show_model(show_nodes=False, show_shells=False)
            assert [r.node_id for r in viewer._restraints] == ["1"]
            assert viewer.restraint_size() > 0
            assert len(viewer._backend.actors("restraints")) == 1
        finally:
            viewer._backend.plotter.close()

    def test_show_model_can_leave_the_supports_out(self):
        from fea_toolkit.io.results_repository import mesh_model_from_geometry
        from fea_toolkit.model.sap_data import Restraint
        from fea_toolkit.plotting.viewer import ModelViewer

        model = mesh_model_from_geometry(_quad_geometry())
        model.restraints = {"1": Restraint([1, 1, 1, 0, 0, 0])}
        viewer = ModelViewer(mesh_model=model, backend="pyvista", off_screen=True)
        try:
            viewer.show_model(show_nodes=False, show_shells=False, show_restraints=False)
            assert viewer._backend.actors("restraints") == []
        finally:
            viewer._backend.plotter.close()
