"""**File ▸ Save model…** — writing the meshed model to JSON / NPZ / HDF5.

Qt-dependent (``needs_gui``).  The round-trip readers (``read_model_stages``,
``json_to_model``) are exercised here because the GUI action must write files
those readers can open — the same contract the headless ``write_model_stages``
promises.
"""

import time

import pytest

pytestmark = pytest.mark.needs_gui


def _model():
    """A small sample model — enough for the Preprocessor to produce a mesh."""
    from examples.sample_model import make_sample_model

    return make_sample_model()


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    """A ``MainWindow`` showing the sample model."""
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


def _mesh(window):
    """Run a mesh preset so a preprocessed model exists to save."""
    from qtpy.QtCore import QCoreApplication

    window._mesh_preset(
        {"split_elements": True, "create_shells": False}, "Splitting elements at joints"
    )
    deadline = time.monotonic() + 60.0
    while window._worker is not None and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert window._worker is None, "the recipe worker never finished"
    assert window._store.preprocessed() is not None


def _patch_dialog(monkeypatch, target, chosen_filter):
    """Return *target* from ``QFileDialog.getSaveFileName``."""
    from qtpy.QtWidgets import QFileDialog

    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), chosen_filter))
    )


class TestSaveModelAction:
    """The action follows the preprocessed model's existence."""

    def test_disabled_until_a_mesh_exists(self, window):
        assert window._actions["file.save_model"].isEnabled() is False
        _mesh(window)
        assert window._actions["file.save_model"].isEnabled() is True

    def test_without_a_mesh_it_warns_and_writes_nothing(self, window, tmp_path, monkeypatch):
        target = tmp_path / "nothing.h5"
        _patch_dialog(monkeypatch, target, "HDF5 stage file (*.h5 *.hdf5)")
        window._on_save_model()
        assert not target.exists()
        assert "needs a preprocessed model" in window._message_log.toPlainText()


class TestSaveModelFormats:
    """Each format writes a file its own reader can open losslessly."""

    def test_json_writes_a_round_trippable_mesh(self, window, tmp_path, monkeypatch):
        from fea_toolkit.io.model_codec import json_to_model

        _mesh(window)
        target = tmp_path / "saved_mesh.json"
        _patch_dialog(monkeypatch, target, "JSON codec (*.json)")
        window._on_save_model()

        assert target.exists()
        assert json_to_model(target.read_text(encoding="utf-8")) == window._store.preprocessed()
        assert "Saved model to" in window._message_log.toPlainText()

    def test_npz_writes_a_stage_file(self, window, tmp_path, monkeypatch):
        from fea_toolkit.io.stage_reader import read_model_stages

        _mesh(window)
        target = tmp_path / "saved_mesh.npz"
        _patch_dialog(monkeypatch, target, "NPZ stage file (*.npz)")
        window._on_save_model()

        assert target.exists()
        assert read_model_stages(str(target), "mesh") == window._store.preprocessed()

    def test_h5_writes_a_stage_file(self, window, tmp_path, monkeypatch):
        pytest.importorskip("h5py")
        from fea_toolkit.io.stage_reader import read_model_stages

        _mesh(window)
        target = tmp_path / "saved_mesh.h5"
        _patch_dialog(monkeypatch, target, "HDF5 stage file (*.h5 *.hdf5)")
        window._on_save_model()

        assert target.exists()
        assert read_model_stages(str(target), "mesh") == window._store.preprocessed()


class TestModelFileFormat:
    """The format resolver honours an explicit extension, then the filter."""

    def test_explicit_extension_wins(self):
        from fea_toolkit.gui.main_window import _model_file_format

        assert _model_file_format("m.json", "NPZ stage file (*.npz)") == ("json", "m.json")
        assert _model_file_format("m.npz", "JSON codec (*.json)") == ("npz", "m.npz")
        assert _model_file_format("m.hdf5", "") == ("h5", "m.hdf5")

    def test_filter_appends_the_extension(self):
        from fea_toolkit.gui.main_window import _model_file_format

        assert _model_file_format("m", "JSON codec (*.json)") == ("json", "m.json")
        assert _model_file_format("m", "NPZ stage file (*.npz)") == ("npz", "m.npz")
        assert _model_file_format("m", "HDF5 stage file (*.h5 *.hdf5)") == ("h5", "m.h5")
        assert _model_file_format("m", "") == ("h5", "m.h5")
