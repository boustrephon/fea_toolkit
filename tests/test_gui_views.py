"""The Model Tree's **Views** group: named lenses over the loaded model.

Qt-dependent (``needs_gui``).  A view is a lens, not a copy, so these tests pin
the user-visible consequences: the Views group leads the tree, switching a view
swaps the scene, the Inspector reports that view's counts, and neither the
camera nor the tree selection is disturbed by the switch.
"""

import numpy as np
import pytest

from tests.fixtures.gui_preprocess import run_preprocess

pytestmark = pytest.mark.needs_gui


@pytest.fixture(scope="module")
def qapp():
    """Provide the single process-wide ``QApplication`` Qt requires."""
    from qtpy.QtWidgets import QApplication

    yield QApplication.instance() or QApplication(["pytest-fea-gui"])


def _beam_and_column(*, auto_split: bool = True):
    """A beam (1-2) crossed by a column framing into its midpoint (node 3).

    Whose beam requests ``AtJoints`` splitting — the model itself opts in, which
    is the only way the Preprocessor subdivides anything.
    """
    from fea_toolkit.model.sap_data import (
        FrameElement,
        Material,
        Node,
        Restraint,
        SAPModelData,
        Section,
    )

    return SAPModelData(
        nodes={
            "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
            "2": Node(node_id="2", node_tag=2, x=10.0, y=0.0, z=0.0),
            "3": Node(node_id="3", node_tag=3, x=5.0, y=0.0, z=0.0),
            "4": Node(node_id="4", node_tag=4, x=5.0, y=0.0, z=-3.0),
        },
        restraints={"4": Restraint([1, 1, 1, 1, 1, 1])},
        materials={
            "Steel": Material(
                name="Steel",
                type="Steel",
                E_mod=2.0e11,
                G_mod=7.7e10,
                nu=0.3,
                unit_weight=7.85e4,
                Fy=2.5e8,
            )
        },
        sections={
            "SEC1": Section(
                name="SEC1",
                shape="I/Wide Flange",
                material="Steel",
                A=0.005,
                I33=8.4e-5,
                I22=7.7e-6,
                J=2.0e-6,
            )
        },
        frame_elements={
            "1": FrameElement(elem_id="1", elem_tag=1, node_i="1", node_j="2"),
            "2": FrameElement(elem_id="2", elem_tag=2, node_i="3", node_j="4"),
        },
        area_elements={},
        frame_assignments={"1": "SEC1", "2": "SEC1"},
        area_assignments={},
        groups={},
        frame_auto_mesh={"1": {"AtJoints": True}} if auto_split else {},
    )


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    """A ``MainWindow`` showing the beam-and-column model."""
    from fea_toolkit.gui.controllers.interaction import CONFIG_ENV_VAR
    from fea_toolkit.gui.main_window import MainWindow

    monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "absent.json"))
    win = MainWindow(model=_beam_and_column())
    win.resize(900, 700)
    win.show()
    yield win
    win.close()


def _slab_model():
    """A 2 x 2 slab of quads on a 3 x 3 node grid — area elements to draw.

    Built from archive-shaped geometry, which is the shape a results view
    reconstructs, so this is a real display path rather than a test-only model.
    """
    from fea_toolkit.io.results_repository import mesh_model_from_geometry

    xs = [0.0, 1.0, 2.0, 0.0, 1.0, 2.0, 0.0, 1.0, 2.0]
    ys = [0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 2.0, 2.0, 2.0]
    geometry = {
        "node_tag": np.arange(1, 10),
        "node_x": np.array(xs),
        "node_y": np.array(ys),
        "node_z": np.zeros(9),
        "shell_eid": np.arange(1, 5),
        "shell_sap_id": np.array(["A1", "A2", "A3", "A4"]),
        "shell_sec_name": np.array(["SLAB"] * 4),
        "shell_node_1": np.array([1, 2, 4, 5]),
        "shell_node_2": np.array([2, 3, 5, 6]),
        "shell_node_3": np.array([5, 6, 8, 9]),
        "shell_node_4": np.array([4, 5, 7, 8]),
    }
    return mesh_model_from_geometry(geometry)


def _colliding_model():
    """A frame and an area that share the SAP label ``"1"``, plus a far frame.

    SAP2000 reuses object labels across element types, so ``frame 1`` and
    ``area 1`` are distinct entities with the same id string — the case the
    type-qualified hidden set exists for.
    """
    from fea_toolkit.model.sap_data import (
        AreaElement,
        FrameElement,
        Material,
        Node,
        SAPModelData,
        Section,
    )

    return SAPModelData(
        nodes={
            "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
            "2": Node(node_id="2", node_tag=2, x=10.0, y=0.0, z=0.0),
            "3": Node(node_id="3", node_tag=3, x=20.0, y=0.0, z=0.0),
            "4": Node(node_id="4", node_tag=4, x=30.0, y=0.0, z=0.0),
            "5": Node(node_id="5", node_tag=5, x=0.0, y=0.0, z=10.0),
        },
        restraints={},
        materials={
            "Steel": Material(
                name="Steel",
                type="Steel",
                E_mod=2.0e11,
                G_mod=7.7e10,
                nu=0.3,
                unit_weight=7.85e4,
                Fy=2.5e8,
            )
        },
        sections={
            "SEC1": Section(
                name="SEC1",
                shape="I/Wide Flange",
                material="Steel",
                A=0.005,
                I33=8.4e-5,
                I22=7.7e-6,
                J=2.0e-6,
            )
        },
        frame_elements={
            "1": FrameElement(elem_id="1", elem_tag=1, node_i="1", node_j="2"),
            "2": FrameElement(elem_id="2", elem_tag=2, node_i="3", node_j="4"),
        },
        area_elements={
            "1": AreaElement(area_id="1", area_tag=101, node_ids=["1", "2", "5"]),
        },
        frame_assignments={"1": "SEC1", "2": "SEC1"},
        area_assignments={"1": "SEC1"},
        groups={},
        frame_auto_mesh={},
    )


@pytest.fixture()
def slab_window(qapp, monkeypatch, tmp_path):
    """A ``MainWindow`` showing a model with area elements."""
    from fea_toolkit.gui.controllers.interaction import CONFIG_ENV_VAR
    from fea_toolkit.gui.main_window import MainWindow

    monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "absent.json"))
    win = MainWindow(model=_slab_model())
    win.resize(900, 700)
    win.show()
    yield win
    win.close()


@pytest.fixture()
def colliding_window(qapp, monkeypatch, tmp_path):
    """A ``MainWindow`` showing a model whose frame and area share label ``1``."""
    from fea_toolkit.gui.controllers.interaction import CONFIG_ENV_VAR
    from fea_toolkit.gui.main_window import MainWindow

    monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "absent.json"))
    win = MainWindow(model=_colliding_model())
    win.resize(900, 700)
    win.show()
    yield win
    win.close()


def _inspector_rows(window) -> dict:
    """The inspector's ``{field name: value text}`` for the current selection."""
    table = window._inspector._table
    return {table.item(row, 0).text(): table.item(row, 1).text() for row in range(table.rowCount())}


def _camera_flat(window):
    """The viewport camera as a flat float array (PyVista returns a named tuple)."""
    import numpy as np

    def _flatten(value):
        try:
            return [float(item) for item in value]
        except TypeError:
            flat = []
            for item in value:
                flat.extend(_flatten(item))
            return flat

    return np.array(_flatten(window._interactor.camera_position))


def test_preprocessing_freezes_the_gc_before_handing_work_to_the_worker(window, monkeypatch):
    """A worker-thread deepcopy must not walk Qt/VTK objects (macOS segfault).

    The Preprocessor copies the model, and the GUI runs that on a worker thread;
    a collection triggered there would traverse the main thread's shiboken
    objects from the wrong thread.  ``gc.freeze()`` at the handoff is the fix --
    see ``main_window._freeze_gc_once``.
    """
    from fea_toolkit.gui import main_window

    monkeypatch.setattr(main_window, "_GC_FROZEN", [])

    run_preprocess(window)

    assert main_window._GC_FROZEN == [True]


def test_opening_a_model_registers_the_unprocessed_view(window):
    """A freshly parsed model is viewable straight away, as ``Unprocessed``."""
    view = window._views.get("unprocessed")

    assert view is not None
    assert view.active is True
    assert window._views.source("unprocessed") is window._model


def test_the_views_group_leads_the_tree(window):
    """Views are the first thing in the Model Tree, above the model's groups."""
    from qtpy.QtCore import Qt

    first = window._tree_model.index(0, 0)

    assert first.data(Qt.ItemDataRole.DisplayRole) == "Views"
    assert window._tree_model.index_for("views", "Unprocessed") is not None


def test_splitting_adds_and_activates_a_processed_view(window):
    """``Model ▸ Split elements`` registers the result as a view of its own."""
    run_preprocess(window)

    assert [view.name for view in window._views.views()] == ["Unprocessed", "Processed"]
    assert window._views.active.name == "Processed"
    assert window._model is window._views.source("processed")


def test_switching_views_swaps_the_scene(window):
    """Selecting a view redraws that view's geometry — the point of the feature."""
    run_preprocess(window)
    assert len(window._viewer.geometry()[0]) == 3  # two sub-elements + the column

    assert window._select_entity_in_tree("views", "Unprocessed") is True

    assert len(window._viewer.geometry()[0]) == 2  # the members as drawn
    assert window._views.active.key == "unprocessed"

    assert window._select_entity_in_tree("views", "Processed") is True

    assert len(window._viewer.geometry()[0]) == 3
    assert window._views.active.key == "processed"


def test_the_inspector_reports_the_counts_of_the_view(window):
    """A view is inspected like any other object: its counts, not its geometry."""
    assert window._select_entity_in_tree("views", "Unprocessed") is True
    rows = _inspector_rows(window)

    assert rows["name"] == "Unprocessed"
    assert rows["n_nodes"] == "4"
    assert rows["n_frames"] == "2"
    assert rows["n_frames_active"] == "2"

    run_preprocess(window)
    assert window._select_entity_in_tree("views", "Processed") is True
    rows = _inspector_rows(window)

    # The split parent is kept, so the total exceeds the analysis-ready beams.
    assert rows["n_frames"] == "4"
    assert rows["n_frames_active"] == "3"
    # The Model-menu presets write a step and run the recipe, so a view's
    # provenance names the recipe step that produced it rather than a bare
    # Preprocessor call.
    assert rows["source"] == "Recipe: Split"


def test_the_view_stays_reported_after_switching(window):
    """Switching must not rebuild the tree out from under the row being handled.

    A rebuild would invalidate the very index the selection arrived on, Qt would
    report an empty selection, and the Inspector would be cleared again.
    """
    run_preprocess(window)

    assert window._select_entity_in_tree("views", "Unprocessed") is True

    assert "Unprocessed" in window._inspector._title.text()


def test_switching_views_keeps_the_camera(window):
    """Comparing two views of one model must not move the user's viewpoint."""
    import numpy as np

    run_preprocess(window)
    camera = _camera_flat(window)

    window._select_entity_in_tree("views", "Unprocessed")
    assert np.allclose(_camera_flat(window), camera, rtol=1e-6, atol=1e-9)

    window._select_entity_in_tree("views", "Processed")
    assert np.allclose(_camera_flat(window), camera, rtol=1e-6, atol=1e-9)


class TestDisplayQuality:
    """The View toolbar's shell-opacity and shrink knobs.

    They are wired differently on purpose: opacity is an actor property, so it
    changes in place; shrink is geometry, so it re-renders the active view.
    """

    @staticmethod
    def _shell_actor(window):
        actors = window._backend.actors("shells")
        assert actors, "no shell actor was drawn"
        return actors[0]

    def test_the_default_shell_opacity_is_seventy_percent(self, slab_window):
        """Slabs are translucent so what is behind them stays readable."""
        assert slab_window._shell_opacity.value() == pytest.approx(0.7)
        actor = self._shell_actor(slab_window)
        assert actor.GetProperty().GetOpacity() == pytest.approx(0.7)

    def test_changing_the_opacity_updates_the_actors_in_place(self, slab_window):
        """No re-render — the same actor, just translucent, so it stays interactive."""
        actor = self._shell_actor(slab_window)

        slab_window._shell_opacity.setValue(0.3)

        assert self._shell_actor(slab_window) is actor, "opacity must not rebuild the model"
        assert actor.GetProperty().GetOpacity() == pytest.approx(0.3)

    def test_the_opacity_survives_a_view_switch(self, slab_window):
        """A later render takes the value from the knob, not from a default."""
        slab_window._shell_opacity.setValue(0.25)

        slab_window._refresh_display()

        actor = self._shell_actor(slab_window)
        assert actor.GetProperty().GetOpacity() == pytest.approx(0.25)

    def test_changing_the_shrink_redraws_the_geometry_shrunken(self, slab_window):
        """Shrink *is* geometry, so it re-renders — in place, keeping the view."""
        before = np.asarray(self._shell_actor(slab_window).mapper.dataset.points)

        slab_window._shrink.setValue(0.9)

        after = np.asarray(self._shell_actor(slab_window).mapper.dataset.points)
        assert not np.allclose(before, after), "the drawn geometry did not change"
        # The 2 x 2 slab pulls inward from every edge.
        assert after[:, 0].min() > before[:, 0].min()
        assert after[:, 0].max() < before[:, 0].max()
        # Still the same view: re-drawing must not lose the user's place.
        assert slab_window._views.active is not None

    def test_shrinking_leaves_the_viewers_geometry_true_sized(self, slab_window):
        """Display-only: what the viewer reports is still the real model."""
        slab_window._shrink.setValue(0.9)

        # The 2 x 2 slab keeps its true extent in the extracted geometry.
        maxima = [s.vertices[:, 0].max() for s in slab_window._viewer._shells]
        assert max(maxima) == pytest.approx(2.0)


class TestSupportDisplay:
    """Support symbols in the viewport, and support rows in the Inspector.

    The beam-and-column model fixes node 4 (``Restraint([1, 1, 1, 1, 1, 1])``),
    which is what makes it the fixture for both halves of this.
    """

    def test_a_restrained_node_reports_its_supports(self, window):
        """A node's conditions live on the model, so the Inspector is given it."""
        assert window._select_entity_in_tree("nodes", "4") is True
        assert _inspector_rows(window)["Restraints"] == "Fixed (U1 U2 U3 R1 R2 R3)"

    def test_an_unrestrained_node_gains_no_support_rows(self, window):
        """Only rows with something to say — ordinary nodes stay uncluttered."""
        assert window._select_entity_in_tree("nodes", "1") is True
        assert "Restraints" not in _inspector_rows(window)

    def test_the_model_draws_support_symbols(self, window):
        assert window._backend.actors("restraints"), "no support symbols were drawn"

    def test_the_display_toggle_hides_and_shows_them(self, window):
        actor = window._backend.actors("restraints")[0]

        window._actions["view.show_restraints"].setChecked(False)
        assert not actor.GetVisibility()  # VTK's getter returns an int

        window._actions["view.show_restraints"].setChecked(True)
        assert actor.GetVisibility()

    def test_a_node_selection_does_not_object_to_an_archive(self, window):
        """No store means no constraint source — the node rows carry on alone."""
        window._select_entity_in_tree("nodes", "4")
        window._inspector.set_source_model(None)
        window._inspector.show_object(window._views.source("unprocessed").nodes["4"])
        assert "Restraints" not in _inspector_rows(window)


class TestElementContext:
    """A frame/area's section, material and groups reach the Inspector."""

    def test_a_frame_reports_its_section_and_material(self, window):
        """The assignment lives on the model, so the Inspector is given it."""
        assert window._select_entity_in_tree("frame_elements", "1") is True
        rows = _inspector_rows(window)
        assert rows["Section"] == "SEC1 (I/Wide Flange)"
        assert rows["Material"] == "Steel"

    def test_a_node_has_no_section_or_material(self, window):
        assert window._select_entity_in_tree("nodes", "4") is True
        rows = _inspector_rows(window)
        assert "Section" not in rows
        assert "Material" not in rows


class TestSelectionFeedback:
    """Selecting an element has to be visible in the viewport.

    Frames get a tube; area elements get an **outline**, because a coincident
    translucent fill is invisible over a translucent slab — the selection
    previously read as nothing at all.
    """

    def test_selecting_an_area_element_shows_a_slab(self, slab_window):
        from fea_toolkit.gui.main_window import _SELECT_COLOR

        assert slab_window._select_entity_in_tree("area_elements", "A1") is True

        (slab,) = slab_window._backend.actors("highlights")
        mesh = slab.mapper.dataset
        # A slab straddling the element, not a coincident surface: the 2 x 2 slab
        # lies in z = 0, so the cue must extend to either side of it.
        assert mesh.bounds[4] < 0.0 < mesh.bounds[5]
        assert slab.GetProperty().GetColor() == pytest.approx(_SELECT_COLOR, abs=0.01)

    def test_clearing_the_highlight_removes_the_outline(self, slab_window):
        slab_window._select_entity_in_tree("area_elements", "A1")

        slab_window._actions["view.clear_highlights"].trigger()

        assert slab_window._backend.actors("highlights") == []


class TestHideSelection:
    """``View ▸ Display ▸ Hide selected`` / ``Isolate selected`` / ``Show all`` (P34)."""

    def test_hide_selected_removes_the_shell_and_its_orphaned_nodes(self, slab_window):
        slab_window._select_entity_in_tree("area_elements", "A1")
        slab_window._actions["view.hide_selected"].trigger()

        _frames, shells, nodes = slab_window._viewer.geometry()
        assert {s.area_id for s in shells} == {"A2", "A3", "A4"}
        # Node 1 is referenced only by the hidden slab; shared corners 2/4/5 stay.
        assert {n.node_id for n in nodes} == {"2", "3", "4", "5", "6", "7", "8", "9"}

    def test_show_all_restores_the_hidden_shell(self, slab_window):
        slab_window._select_entity_in_tree("area_elements", "A1")
        slab_window._actions["view.hide_selected"].trigger()
        assert {s.area_id for s in slab_window._viewer.geometry()[1]} == {"A2", "A3", "A4"}

        slab_window._actions["view.show_all"].trigger()
        assert {s.area_id for s in slab_window._viewer.geometry()[1]} == {"A1", "A2", "A3", "A4"}

    def test_hide_survives_a_display_refresh(self, slab_window):
        slab_window._select_entity_in_tree("area_elements", "A1")
        slab_window._actions["view.hide_selected"].trigger()

        slab_window._shrink.setValue(0.9)  # re-render via _refresh_display

        assert {s.area_id for s in slab_window._viewer.geometry()[1]} == {"A2", "A3", "A4"}

    def test_hide_selected_with_no_selection_warns(self, slab_window):
        slab_window._actions["view.hide_selected"].trigger()
        assert "Nothing selected to hide" in slab_window._message_log.toPlainText()

    def test_show_all_with_nothing_hidden_warns(self, slab_window):
        slab_window._actions["view.show_all"].trigger()
        assert "Nothing is hidden" in slab_window._message_log.toPlainText()

    def test_a_preprocessing_result_clears_the_hidden_set(self, slab_window):
        slab_window._select_entity_in_tree("area_elements", "A1")
        slab_window._actions["view.hide_selected"].trigger()
        assert slab_window._hidden_elem_ids == {"Area:A1"}

        slab_window._show_geometry_result("Meshed", _slab_model())

        assert slab_window._hidden_elem_ids == set()
        assert "hidden elements were restored" in slab_window._message_log.toPlainText()

    def test_isolate_selected_hides_everything_else(self, slab_window):
        slab_window._select_entity_in_tree("area_elements", "A1")
        slab_window._actions["view.isolate_selected"].trigger()

        assert slab_window._hidden_elem_ids == {"Area:A2", "Area:A3", "Area:A4"}
        _frames, shells, nodes = slab_window._viewer.geometry()
        assert {s.area_id for s in shells} == {"A1"}
        # Only A1's corners remain: its exclusive node 1 plus the shared 2/4/5.
        assert {n.node_id for n in nodes} == {"1", "2", "4", "5"}

    def test_show_all_restores_after_isolate(self, slab_window):
        slab_window._select_entity_in_tree("area_elements", "A1")
        slab_window._actions["view.isolate_selected"].trigger()
        assert {s.area_id for s in slab_window._viewer.geometry()[1]} == {"A1"}

        slab_window._actions["view.show_all"].trigger()
        assert {s.area_id for s in slab_window._viewer.geometry()[1]} == {"A1", "A2", "A3", "A4"}

    def test_isolate_selected_replaces_the_prior_hidden_set(self, slab_window):
        slab_window._select_entity_in_tree("area_elements", "A4")
        slab_window._actions["view.hide_selected"].trigger()
        assert slab_window._hidden_elem_ids == {"Area:A4"}

        slab_window._select_entity_in_tree("area_elements", "A1")
        slab_window._actions["view.isolate_selected"].trigger()
        assert slab_window._hidden_elem_ids == {"Area:A2", "Area:A3", "Area:A4"}

    def test_isolate_selected_with_no_selection_warns(self, slab_window):
        slab_window._actions["view.isolate_selected"].trigger()
        assert "Nothing selected to isolate" in slab_window._message_log.toPlainText()

    def test_isolate_tells_a_frame_from_an_area_that_share_an_id(self, colliding_window):
        colliding_window._select_entity_in_tree("frame_elements", "1")
        colliding_window._actions["view.isolate_selected"].trigger()

        assert colliding_window._hidden_elem_ids == {"Frame:2", "Area:1"}
        frames, shells, _nodes = colliding_window._viewer.geometry()
        assert {f.elem_id for f in frames} == {"1"}
        assert {s.area_id for s in shells} == set()

    def test_isolate_an_area_leaves_a_same_id_frame_hidden(self, colliding_window):
        colliding_window._select_entity_in_tree("area_elements", "1")
        colliding_window._actions["view.isolate_selected"].trigger()

        assert colliding_window._hidden_elem_ids == {"Frame:1", "Frame:2"}
        frames, shells, _nodes = colliding_window._viewer.geometry()
        assert {f.elem_id for f in frames} == set()
        assert {s.area_id for s in shells} == {"1"}
