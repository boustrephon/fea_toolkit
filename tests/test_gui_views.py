"""The Model Tree's **Views** group: named lenses over the loaded model.

Qt-dependent (``needs_gui``).  A view is a lens, not a copy, so these tests pin
the user-visible consequences: the Views group leads the tree, switching a view
swaps the scene, the Inspector reports that view's counts, and neither the
camera nor the tree selection is disturbed by the switch.
"""

import time

import pytest

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


def _run_preprocess(window, action_key="model.split", timeout=30.0):
    """Trigger a Model-menu action and spin the GUI until its worker ends."""
    from qtpy.QtCore import QCoreApplication

    window._actions[action_key].trigger()
    deadline = time.monotonic() + timeout
    while window._worker is not None and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert window._worker is None, "the preprocessing worker never finished"


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
    _run_preprocess(window)

    assert [view.name for view in window._views.views()] == ["Unprocessed", "Processed"]
    assert window._views.active.name == "Processed"
    assert window._model is window._views.source("processed")


def test_switching_views_swaps_the_scene(window):
    """Selecting a view redraws that view's geometry — the point of the feature."""
    _run_preprocess(window)
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

    _run_preprocess(window)
    assert window._select_entity_in_tree("views", "Processed") is True
    rows = _inspector_rows(window)

    # The split parent is kept, so the total exceeds the analysis-ready beams.
    assert rows["n_frames"] == "4"
    assert rows["n_frames_active"] == "3"
    assert rows["source"] == "Preprocessor: split_elements"


def test_the_view_stays_reported_after_switching(window):
    """Switching must not rebuild the tree out from under the row being handled.

    A rebuild would invalidate the very index the selection arrived on, Qt would
    report an empty selection, and the Inspector would be cleared again.
    """
    _run_preprocess(window)

    assert window._select_entity_in_tree("views", "Unprocessed") is True

    assert "Unprocessed" in window._inspector._title.text()


def test_switching_views_keeps_the_camera(window):
    """Comparing two views of one model must not move the user's viewpoint."""
    import numpy as np

    _run_preprocess(window)
    camera = _camera_flat(window)

    window._select_entity_in_tree("views", "Unprocessed")
    assert np.allclose(_camera_flat(window), camera, rtol=1e-6, atol=1e-9)

    window._select_entity_in_tree("views", "Processed")
    assert np.allclose(_camera_flat(window), camera, rtol=1e-6, atol=1e-9)
