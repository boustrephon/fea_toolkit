"""**File ▸ Open project…** — the native file picker for a ``.zip`` project.

A project is a single ``<name>.zip`` file, so the open action uses
``getOpenFileName`` — the native picker, which selects the file cleanly.  The
macOS directory picker cannot select a folder whose name carries a file
extension, which is why the bundle is a file rather than a directory.
"""

import zipfile

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
        monkeypatch.setattr(window, "_confirm_project_open", lambda path: True)

        window._on_open_project()

        assert opened == ["/chosen/project/admin_2.zip"]
        assert "*.zip" in call["args"][3]


def test_confirm_project_open_previews_members_and_confirms(window, tmp_path, monkeypatch):
    from qtpy.QtWidgets import QMessageBox

    archive = tmp_path / "session.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("project.json", "{}")
        zf.writestr("results.npz", b"abc")

    shown = {}

    def _fake_question(parent, title, text, *args, **kwargs):
        shown["text"] = text
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", staticmethod(_fake_question))

    assert window._confirm_project_open(str(archive)) is True
    assert "project.json" in shown["text"]
    assert "results.npz" in shown["text"]


def test_confirm_project_open_returns_false_when_declined(window, tmp_path, monkeypatch):
    from qtpy.QtWidgets import QMessageBox

    archive = tmp_path / "session.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("project.json", "{}")

    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No),
    )

    assert window._confirm_project_open(str(archive)) is False


def test_confirm_project_open_skips_preview_for_a_non_project(window, tmp_path, monkeypatch):
    from qtpy.QtWidgets import QMessageBox

    archive = tmp_path / "notes.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("README.txt", "hello")

    called = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: called.append(True) or QMessageBox.StandardButton.No),
    )

    # Not a project → no dialog; open_project_path reports the failure instead.
    assert window._confirm_project_open(str(archive)) is True
    assert called == []


def test_dropping_a_project_zip_reopens_the_model(window, tmp_path, monkeypatch):
    """Dropping a project ``.zip`` confirms, then routes through ``open_project_path``."""
    from examples.sample_model import make_sample_model
    from fea_toolkit.gui.project import build_manifest, write_project_zip

    sap = make_sample_model()
    manifest = build_manifest(
        name="session",
        source_label="sample.s2k",
        model_file="model.npz",
        units=dict(sap.units),
        recipe=None,
        views=[],
        active_view=None,
        camera=None,
        hidden_elem_ids=[],
        display={},
    )
    zip_path = tmp_path / "session.zip"
    write_project_zip(
        zip_path,
        manifest=manifest,
        sap=sap,
        mesh=None,
        results_arrays={},
        figures={},
        tables={},
        model_fmt="npz",
    )
    confirmed = []
    monkeypatch.setattr(
        window, "_confirm_project_open", lambda path: confirmed.append(path) or True
    )
    assert window._open_dropped_files([str(zip_path)]) == 1
    assert confirmed == [str(zip_path)]
    assert window._session.store is not None


def test_opening_a_different_project_refits_secondary_viewports(window, tmp_path):
    """Secondary viewports must not inherit the outgoing project's cameras.

    A project open replaces the scene outright, so only the primary camera is
    restored from the manifest; the front / top / side viewports are re-fit to
    the incoming geometry rather than keeping the outgoing project's viewpoint.
    """
    import numpy as np

    from examples.sample_model import make_sample_model
    from fea_toolkit.gui.project import build_manifest, write_project_zip

    def _flat(camera):
        flat = []
        for row in camera:
            flat.extend(float(x) for x in row)
        return np.array(flat)

    # Show one model, then park a secondary viewport somewhere clearly wrong.
    sap = make_sample_model()
    window._remember_source(sap)
    window.show_model(sap)
    wrong = (
        (1000.0, 1000.0, 1000.0),
        (0.0, 0.0, 0.0),
        (0.0, 0.0, 1.0),
    )
    window._quad.interactors[1].camera_position = wrong
    assert np.allclose(_flat(window._quad.interactors[1].camera_position), _flat(wrong))

    manifest = build_manifest(
        name="session",
        source_label="sample.s2k",
        model_file="model.npz",
        units=dict(sap.units),
        recipe=None,
        views=[],
        active_view=None,
        camera=None,
        hidden_elem_ids=[],
        display={},
    )
    zip_path = tmp_path / "session.zip"
    write_project_zip(
        zip_path,
        manifest=manifest,
        sap=sap,
        mesh=None,
        results_arrays={},
        figures={},
        tables={},
        model_fmt="npz",
    )
    assert window.open_project_path(str(zip_path)) is True

    restored = _flat(window._quad.interactors[1].camera_position)
    assert not np.allclose(restored, _flat(wrong), rtol=1e-6, atol=1e-9)


def test_tree_source_prefers_the_mesh(window):
    """The tree presents the mesh when one exists, else the raw model."""
    from examples.sample_model import make_sample_model

    sap = make_sample_model()
    window._remember_source(sap)
    assert window._tree_model_source(None) is sap  # no mesh yet → raw

    window._session.store.set_preprocessed(object())
    assert window._tree_model_source(None) is window._session.store.preprocessed()


def test_preferred_open_view_chooses_geometry(window):
    """A project reopens on a geometry view, not the saved results view."""
    from examples.sample_model import make_sample_model

    window._remember_source(make_sample_model())  # registers "unprocessed"
    window._session.views.add_geometry("meshed", "Meshed", make_sample_model(), source="")

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
    from fea_toolkit.model.sap_data import (
        LoadCase,
        LoadCombination,
        ResponseSpectrumFunction,
        StoryDefinition,
    )
    from fea_toolkit.opensees.preprocessor import preprocess_model

    sap = make_sample_model()
    sap.load_cases = {
        "DEAD": LoadCase("DEAD", "LinStatic", "Prog Det", "DEAD", "Prog Det", "Non-Composite")
    }
    sap.load_combinations = {"COMB1": LoadCombination("COMB1", "Linear Add")}
    sap.response_spectrum_functions = {
        "RS1": ResponseSpectrumFunction("RS1", [0.0, 1.0], [0.1, 0.2])
    }
    sap.stories = [
        StoryDefinition(name="ROOF", elevation=3.0),
        StoryDefinition(name="BASE", elevation=0.0),
    ]

    mesh = preprocess_model(sap)  # now carries them…
    assert [(s.name, s.elevation) for s in mesh.stories] == [("ROOF", 3.0), ("BASE", 0.0)]
    mesh.load_cases = {}  # …so strip them to simulate an old save
    mesh.load_combinations = {}
    mesh.response_spectrum_functions = {}
    mesh.stories = []

    window._backfill_mesh_load_metadata(mesh, sap)

    assert set(mesh.load_cases) == {"DEAD"}
    assert set(mesh.load_combinations) == {"COMB1"}
    assert set(mesh.response_spectrum_functions) == {"RS1"}
    assert [(s.name, s.elevation) for s in mesh.stories] == [("ROOF", 3.0), ("BASE", 0.0)]
