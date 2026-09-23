"""Milestone-4 selection-sync tests: tree selection -> viewport highlight.

Qt-dependent, so the whole module is gated by the ``needs_gui`` marker;
``tests/conftest.py`` skips it when the optional ``[gui]`` extra is absent.

The reverse direction (viewport pick -> tree select) rides on the
``cell_id <-> SAP label`` map and is **not** covered here yet -- see
``docs/gui_roadmap.md`` milestone row 4.
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
    """A ``MainWindow`` showing the sample model."""
    from examples.sample_model import make_sample_model
    from fea_toolkit.gui.main_window import MainWindow

    win = MainWindow(model=make_sample_model())
    yield win
    win.close()


def _select(window, group_key, row=0):
    """Select one tree row, as a user click would."""
    from qtpy.QtCore import QItemSelectionModel

    tree = window._tree_model
    group_row = next(i for i, group in enumerate(tree._groups) if group.key == group_key)
    group = tree.index(group_row, 0)
    tree.fetchMore(group)
    child = tree.index(row, 0, group)
    window._tree_view.selectionModel().setCurrentIndex(
        child, QItemSelectionModel.SelectionFlag.ClearAndSelect
    )
    return child


def _highlights(window):
    """The actors currently drawn for the selection highlight."""
    return window._backend._categories.get("highlights", [])


def test_selecting_a_frame_highlights_it(window):
    """A frame row in the tree draws exactly one highlight actor."""
    _select(window, "frame_elements")
    assert len(_highlights(window)) == 1


def test_selecting_a_node_highlights_it(window):
    """A node row highlights through ``highlight_nodes`` instead."""
    _select(window, "nodes")
    assert len(_highlights(window)) == 1


def test_successive_selections_replace_rather_than_accumulate(window):
    """Two clicks, one highlight: the previous set is dropped, not stacked.

    Frame and node highlights each contribute one actor, so an accumulating
    implementation would report two here.
    """
    _select(window, "frame_elements")
    assert len(_highlights(window)) == 1
    _select(window, "nodes")
    assert len(_highlights(window)) == 1


def test_entities_without_geometry_clear_the_highlight(window):
    """A material has no geometry of its own, so nothing stays highlighted."""
    _select(window, "frame_elements")
    assert _highlights(window)
    _select(window, "materials")
    assert _highlights(window) == []


def test_clear_highlights_action_removes_the_overlay(window):
    """The View -> Display -> Clear highlights action drops the highlight."""
    _select(window, "frame_elements")
    assert _highlights(window)
    window._actions["view.clear_highlights"].trigger()
    assert _highlights(window) == []


def test_actor_ledger_stays_complete_after_highlighting(window):
    """``_actors`` must keep accounting for every actor, or ``clear()`` leaks.

    ``clear()`` only removes what it can see in ``_actors``, so a category
    actor that was skipped there would survive a scene reset.
    """
    _select(window, "frame_elements")
    ledger = window._backend._actors
    categorised = [a for actors in window._backend._categories.values() for a in actors]
    assert len(ledger) == len(categorised)


def test_node_display_toggle_hides_and_shows_the_markers(window):
    """``View -> Display -> Show nodes`` flips the markers' visibility."""
    node_actor = window._backend._categories["nodes"][0]
    assert node_actor.GetVisibility()
    window._actions["view.show_nodes"].setChecked(False)
    assert not node_actor.GetVisibility()
    window._actions["view.show_nodes"].setChecked(True)
    assert node_actor.GetVisibility()


def test_displaying_a_second_model_replaces_the_scene(window):
    """Opening another model must not leave the first one's geometry behind."""
    from examples.sample_model import make_sample_model

    n_before = len(window._backend._actors)
    assert n_before > 0
    window.show_model(make_sample_model())
    assert len(window._backend._actors) == n_before
