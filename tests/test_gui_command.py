"""The command palette widget and the command echo (offscreen)."""

import time

import pytest

pytestmark = pytest.mark.needs_gui


def _model():
    from examples.sample_model import make_sample_model

    return make_sample_model()


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    from fea_toolkit.gui.controllers.interaction import CONFIG_ENV_VAR
    from fea_toolkit.gui.main_window import MainWindow

    monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "absent.json"))
    win = MainWindow(model=_model())
    win.resize(900, 700)
    win.show()
    try:
        yield win
    finally:
        try:
            win.close()
        finally:
            import openseespy.opensees as ops

            ops.wipe()


def _spin(win, timeout=60.0):
    """Spin the GUI until the current worker ends."""
    from qtpy.QtCore import QCoreApplication

    deadline = time.monotonic() + timeout
    while win._recipe_controller.worker is not None and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert win._recipe_controller.worker is None, "a worker never finished"


def test_command_action_is_live_and_enabled(window):
    action = window._actions["recipe.command"]
    assert action.isEnabled()
    assert action.shortcut().toString() == "Ctrl+K"


def test_palette_compiles_a_command(qapp):
    from fea_toolkit.gui.views.command_palette import _PaletteDialog

    dialog = _PaletteDialog(None, [], initial=())
    dialog._edit.setText('mesh section="brick wall" create_shells=True')
    dialog._on_accept()
    step = dialog.step()
    assert step is not None
    assert step.verb == "mesh"
    assert step.params["create_shells"] is True
    assert step.selection is not None


def test_palette_hints_a_missing_precursor(qapp):
    from fea_toolkit.gui.views.command_palette import _PaletteDialog

    dialog = _PaletteDialog(None, [], initial=())
    dialog._on_changed("run_static")
    assert "needs geometry" in dialog._hint.text()


def test_palette_refuses_acceptance_when_a_precursor_is_missing(qapp):
    """Enter cannot accept a command whose precursor is unsatisfied."""
    from fea_toolkit.gui.views.command_palette import _PaletteDialog

    dialog = _PaletteDialog(None, [], initial=())
    dialog._edit.setText('run_static cases={"DEAD": {"DEAD": 1.0}}')
    dialog._on_accept()
    assert dialog.step() is None  # not accepted — geometry is missing
    assert "needs geometry" in dialog._hint.text()


def test_palette_accepts_when_precursors_are_satisfied(qapp):
    """A command whose precursor is present is still accepted."""
    from fea_toolkit.gui.views.command_palette import _PaletteDialog

    dialog = _PaletteDialog(None, [], initial={"geometry"})
    dialog._edit.setText('run_static cases={"DEAD": {"DEAD": 1.0}}')
    dialog._on_accept()
    step = dialog.step()
    assert step is not None
    assert step.verb == "run_static"


def test_palette_reports_an_invalid_command(qapp):
    from fea_toolkit.gui.views.command_palette import _PaletteDialog

    dialog = _PaletteDialog(None, [], initial=())
    dialog._edit.setText("bogus_verb")
    dialog._on_accept()
    assert dialog.step() is None
    assert "unknown verb" in dialog._hint.text()


def test_a_preset_echoes_its_command(window):
    """The Model-menu mesh preset appends a step and logs its command form."""
    window._mesh_preset({"split_elements": True, "create_shells": False}, "Splitting")
    _spin(window)
    assert "command: mesh split_elements=True create_shells=False" in (
        window._message_log.toPlainText()
    )
