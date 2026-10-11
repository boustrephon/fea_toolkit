"""**File ▸ Open project…** — the native file picker for a ``.zip`` project.

A project is a single ``<name>.zip`` file, so the open action uses
``getOpenFileName`` — the native picker, which selects the file cleanly.  The
macOS directory picker cannot select a folder whose name carries a file
extension, which is why the bundle is a file rather than a directory.
"""

import pytest

pytestmark = pytest.mark.needs_gui


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    """A ``MainWindow`` with no model — opening a project needs only the window."""
    from fea_toolkit.gui.controllers.interaction import CONFIG_ENV_VAR
    from fea_toolkit.gui.main_window import MainWindow

    monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "absent.json"))
    win = MainWindow()
    win.resize(900, 700)
    win.show()
    try:
        yield win
    finally:
        win.close()


class TestOpenProjectDialog:
    def test_it_uses_the_native_file_picker(self, window, monkeypatch):
        from qtpy.QtWidgets import QFileDialog

        call = {}

        def _fake_open_file(*args, **kwargs):
            call["args"] = args
            return "/chosen/project/admin_2.zip", ""

        monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(_fake_open_file))

        opened = []

        monkeypatch.setattr(window, "open_project_path", lambda path: opened.append(path) or True)

        window._on_open_project()

        assert opened == ["/chosen/project/admin_2.zip"]
        assert "*.zip" in call["args"][3]


def test_tree_source_prefers_the_mesh(window):
    """The tree presents the mesh when one exists, else the raw model."""
    from examples.sample_model import make_sample_model

    sap = make_sample_model()
    window._remember_source(sap)
    assert window._tree_model_source(None) is sap  # no mesh yet → raw

    window._store.set_preprocessed(object())
    assert window._tree_model_source(None) is window._store.preprocessed()


def test_preferred_open_view_chooses_geometry(window):
    """A project reopens on a geometry view, not the saved results view."""
    from examples.sample_model import make_sample_model

    window._remember_source(make_sample_model())  # registers "unprocessed"
    window._views.add_geometry("meshed", "Meshed", make_sample_model(), source="")

    view = window._preferred_open_view("results:Mode-2")
    assert view is not None
    assert view.key == "meshed"


def test_preferred_open_view_falls_back_to_unprocessed(window):
    """With no meshed/processed view, the unprocessed geometry view is shown."""
    from examples.sample_model import make_sample_model

    window._remember_source(make_sample_model())  # registers "unprocessed" only

    view = window._preferred_open_view("results:Mode-2")
    assert view is not None
    assert view.key == "unprocessed"


def test_backfill_mesh_load_metadata_restores_a_stale_mesh(window):
    """A project saved before the fields existed still shows the load groups."""
    from examples.sample_model import make_sample_model
    from fea_toolkit.model.sap_data import LoadCase, LoadCombination, ResponseSpectrumFunction
    from fea_toolkit.opensees.preprocessor import preprocess_model

    sap = make_sample_model()
    sap.load_cases = {
        "DEAD": LoadCase("DEAD", "LinStatic", "Prog Det", "DEAD", "Prog Det", "Non-Composite")
    }
    sap.load_combinations = {"COMB1": LoadCombination("COMB1", "Linear Add")}
    sap.response_spectrum_functions = {
        "RS1": ResponseSpectrumFunction("RS1", [0.0, 1.0], [0.1, 0.2])
    }

    mesh = preprocess_model(sap)  # now carries them…
    mesh.load_cases = {}  # …so strip them to simulate an old save
    mesh.load_combinations = {}
    mesh.response_spectrum_functions = {}

    window._backfill_mesh_load_metadata(mesh, sap)

    assert set(mesh.load_cases) == {"DEAD"}
    assert set(mesh.load_combinations) == {"COMB1"}
    assert set(mesh.response_spectrum_functions) == {"RS1"}
