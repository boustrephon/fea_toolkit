"""Milestone-4 viewport -> tree selection: the GUI wiring.

Qt-dependent, so the module is gated by ``needs_gui``.  The mapping itself is
tested Qt-free in ``test_gui_selection_index.py``, and the pyvista/VTK side
(callback arity, picker cell ids, actor -> batch lookup) against a real
off-screen render in ``test_picking.py``; here the *wiring* is covered: the
lazy tree row, the inspector and the highlight that a pick should drive.
"""

import pytest

pytestmark = pytest.mark.needs_gui


@pytest.fixture(scope="module")
def qapp():
    """Provide the single process-wide ``QApplication`` Qt requires."""
    from qtpy.QtWidgets import QApplication

    yield QApplication.instance() or QApplication(["pytest-fea-gui"])


@pytest.fixture()
def window(qapp):
    """A shown ``MainWindow`` with the sample model."""
    from examples.sample_model import make_sample_model
    from fea_toolkit.gui.main_window import MainWindow

    win = MainWindow(model=make_sample_model())
    win.resize(900, 700)
    win.show()
    yield win
    win.close()


def _current_label(window):
    """SAP label of whatever the tree currently has selected."""
    from qtpy.QtCore import Qt

    index = window._tree_view.selectionModel().currentIndex()
    if not index.isValid():
        return None
    entity = index.data(Qt.ItemDataRole.UserRole)
    return getattr(entity, "elem_id", None) or getattr(entity, "node_id", None)


def test_a_frame_pick_selects_the_matching_tree_row(window, monkeypatch):
    """The picked batch plus its cell index resolves to the frame's row."""
    frame = window._viewer.geometry()[0][0]
    actor = window._backend._categories["frames"][0]
    monkeypatch.setattr(window, "_picked_cell_id", lambda: 0)

    window._on_viewport_pick(actor)

    assert _current_label(window) == frame.elem_id
    # The frame group was never expanded by hand -- the pick must expand it.
    assert window._tree_view.isExpanded(window._tree_view.selectionModel().currentIndex().parent())


def test_a_node_pick_selects_the_matching_tree_row(window, monkeypatch):
    """The node cloud is resolved through the same batch bookkeeping."""
    nodes = window._viewer.geometry()[2]
    actor = window._backend._categories["nodes"][0]
    monkeypatch.setattr(window, "_picked_cell_id", lambda: 1)

    window._on_viewport_pick(actor)

    assert _current_label(window) == nodes[1].node_id


def test_a_pick_highlights_and_updates_the_inspector(window, monkeypatch):
    """Selecting from the viewport drives the rest of the window like a click."""
    actor = window._backend._categories["frames"][0]
    monkeypatch.setattr(window, "_picked_cell_id", lambda: 0)

    window._on_viewport_pick(actor)

    assert window._inspector._table.rowCount() > 0
    assert window._backend._categories.get("highlights")


def test_picks_on_unknown_actors_are_ignored(window, monkeypatch):
    """Overlays (highlights, labels, force flags) are not selectable."""
    monkeypatch.setattr(window, "_picked_cell_id", lambda: 0)
    before = window._tree_view.selectionModel().currentIndex()

    window._on_viewport_pick(object())  # an actor the backend never created

    assert window._tree_view.selectionModel().currentIndex() == before


def test_a_pick_outside_the_batch_is_ignored(window, monkeypatch):
    """A cell index past the end of the batch selects nothing, without raising."""
    actor = window._backend._categories["frames"][0]
    monkeypatch.setattr(window, "_picked_cell_id", lambda: 99)

    window._on_viewport_pick(actor)

    assert _current_label(window) is None


def test_selecting_an_unknown_label_reports_failure(window):
    assert window._select_entity_in_tree("frame_elements", "no-such-element") is False
    assert window._select_entity_in_tree("no_such_group", "1") is False
