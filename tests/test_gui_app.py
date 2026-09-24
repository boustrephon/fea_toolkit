"""Milestone-2 chrome tests: menubar, docks, status bar and the wire-ups.

Qt-dependent, so the whole module is gated by the ``needs_gui`` marker;
``tests/conftest.py`` skips it when the optional ``[gui]`` extra is absent.
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.needs_gui

_FIXTURE = Path(__file__).parent / "fixtures" / "sample.s2k"
_MENUS = ["File", "Edit", "View", "Model", "Analysis", "Results", "Help"]


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


def test_menubar_has_the_full_menu_set(window):
    """All seven roadmap menus exist (the full-but-greyed decision)."""
    titles = [a.text().replace("&", "") for a in window.menuBar().actions()]
    assert titles == _MENUS


def test_docks_are_present(window):
    """The left trees/inspector and the bottom message log docks exist."""
    from qtpy.QtWidgets import QDockWidget

    for name in ("dock_trees", "dock_inspector", "dock_messages"):
        assert window.findChild(QDockWidget, name) is not None


def test_status_bar_shows_units_from_the_model(window):
    """The units readout reflects the model; the progress bar is idle."""
    assert window._units_label.text() == "N \u00b7 m \u00b7 C"
    assert window._coord_label.text().startswith("x")
    assert window._progress.isVisible() is False


def test_message_log_records_activity(window):
    """Construction logs Ready; showing a model logs the geometry step."""
    text = window._message_log.toPlainText()
    assert "Ready." in text
    assert "Displayed model geometry." in text


def test_real_actions_enabled_placeholders_disabled(window):
    """Live actions are enabled; placeholders carry their milestone and are off."""
    assert window._actions["file.open"].isEnabled()
    assert window._actions["view.iso"].isEnabled()
    assert window._actions["help.about"].isEnabled()
    run = window._actions["analysis.run"]
    assert not run.isEnabled()
    assert "Milestone 5" in run.toolTip()


def test_show_is_graceful_without_an_interactive_viewport(window):
    """Showing the window never raises, even when the viewport is off-screen.

    ``enable_terrain_style`` and ``track_mouse_position`` are best-effort
    extras that require a live interactor; this asserts the window still
    opens and stays visible when they are unavailable.
    """
    window.show()
    assert window.isVisible()
    window.hide()


def test_camera_actions_change_the_view(window):
    """A camera action reorients the embedded viewport."""
    before = window._interactor.camera_position
    window._actions["view.xy"].trigger()
    assert window._interactor.camera_position != before


def test_open_path_renders_a_fixture(window):
    """``open_path`` parses a real .s2k and displays it (dialog-free)."""
    assert _FIXTURE.exists()
    assert window.open_path(str(_FIXTURE)) is True
    assert window._model is not None


def test_open_path_reports_failure_without_a_dialog(window):
    """A bad path logs an error and returns False -- no modal dialog in tests."""
    missing = str(_FIXTURE.with_name("does_not_exist.s2k"))
    assert window.open_path(missing) is False
    assert "Failed to open" in window._message_log.toPlainText()


def test_model_tree_is_populated(window):
    """The left dock's Model Tree is driven by the real tree model."""
    assert window._tree_view.model() is window._tree_model
    assert window._tree_model.rowCount() > 0


def test_selecting_a_node_fills_the_inspector(window):
    """Tree selection drives the inspector through the real signal wiring."""
    from qtpy.QtCore import QItemSelectionModel

    tree = window._tree_model
    group = tree.index(0, 0)  # Nodes
    tree.fetchMore(group)
    child = tree.index(0, 0, group)
    window._tree_view.selectionModel().setCurrentIndex(
        child, QItemSelectionModel.SelectionFlag.ClearAndSelect
    )
    assert "Node" in window._inspector._title.text()
    assert window._inspector._table.rowCount() > 0


def test_window_title_is_the_product_name(window):
    """The chrome is titled for users, not after the Python interpreter."""
    assert window.windowTitle() == "FEA Toolkit"


def test_placeholder_tips_never_name_a_finished_milestone(window):
    """A greyed item must not promise a milestone that has already landed.

    Milestones 1-4 shipped, so the remaining placeholders name the backlog item
    or the milestone that will actually wire them (the Model menu points at P24,
    element labels at P23) instead of a stale "Milestone 3".
    """
    finished = ("Milestone 1", "Milestone 2", "Milestone 3", "Milestone 4")
    stale = [
        key
        for key, action in window._actions.items()
        if not action.isEnabled() and any(done in action.toolTip() for done in finished)
    ]
    assert stale == []


def test_module_entry_point_matches_the_console_script():
    """``python -m fea_toolkit.gui`` runs the same entry point as ``fea-gui``.

    Importing the module is safe: its ``__main__`` guard only fires under
    ``-m`` (where ``__name__`` is ``"__main__"``), so no event loop starts.
    """
    from fea_toolkit.gui import __main__ as module_entry
    from fea_toolkit.gui.app import main

    assert module_entry.main is main


def test_macos_menu_roles_are_set(window):
    """About / Quit / Preferences carry their macOS Application-menu roles.

    Qt's own Application-menu items are labelled from the process bundle and
    read "About Python" / "Quit Python"; a role-carrying action lets Qt place
    and label ours instead (the role is inert off macOS).
    """
    from qtpy.QtGui import QAction

    roles = QAction.MenuRole
    assert window._actions["help.about"].menuRole() == roles.AboutRole
    assert window._actions["file.quit"].menuRole() == roles.QuitRole
    assert window._actions["edit.preferences"].menuRole() == roles.PreferencesRole


def test_macos_menu_renaming_never_raises(qapp):
    """The AppKit retitle is cosmetic: it reports a result and never raises.

    Under the offscreen test platform there is no native menu, and off macOS
    there is no AppKit -- both must be quiet no-ops.
    """
    from fea_toolkit.gui.app import rename_macos_application_menu

    assert rename_macos_application_menu("FEA Toolkit") in (True, False)


def test_configure_application_sets_the_qt_identity(qapp):
    """Qt's application / display / organisation names are set from constants.

    The native macOS Application-menu titles are not asserted here: they are
    derived from the process bundle and retitled through AppKit at launch,
    which needs a real Cocoa session -- see ``docs/dev_notes.md``.
    """
    from qtpy.QtCore import QCoreApplication
    from qtpy.QtGui import QGuiApplication

    from fea_toolkit.gui.app import APP_NAME, ORG_NAME, configure_application

    configure_application()
    assert (APP_NAME, ORG_NAME) == ("FEA Toolkit", "fea_toolkit")
    assert QCoreApplication.applicationName() == APP_NAME
    assert QCoreApplication.organizationName() == ORG_NAME
    assert QGuiApplication.applicationDisplayName() == APP_NAME
