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
