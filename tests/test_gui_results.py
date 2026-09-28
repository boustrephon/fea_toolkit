"""Opening a results archive from the GUI: one view per load case.

Qt-dependent (``needs_gui``).  The fixture archive is written with numpy alone —
an archive is a *format*, not a model — and it carries the geometry a view
draws, which is what lets these views work with **no model open at all**.
"""

import numpy as np
import pytest

pytestmark = pytest.mark.needs_gui


@pytest.fixture(scope="module")
def qapp():
    """Provide the single process-wide ``QApplication`` Qt requires."""
    from qtpy.QtWidgets import QApplication

    yield QApplication.instance() or QApplication(["pytest-fea-gui"])


def _write_archive(tmp_path, *, cases=("DEAD", "COMB1")):
    """A minimal results archive: one member, two nodes, those cases."""
    path = tmp_path / "results.npz"
    np.savez_compressed(
        path,
        node_tag=np.array([1, 2], dtype=int),
        node_sap_id=np.array(["1", "2"], dtype=str),
        node_x=np.zeros(2),
        node_y=np.zeros(2),
        node_z=np.array([0.0, 10.0]),
        frame_eid=np.array([1], dtype=int),
        frame_sap_id=np.array(["1"], dtype=str),
        frame_sec_name=np.array(["UB300"], dtype=str),
        frame_parent_sap_id=np.array([""], dtype=str),
        frame_node_i=np.array([1], dtype=int),
        frame_node_j=np.array([2], dtype=int),
        static_case_labels=np.array(list(cases), dtype=str),
        static_case_group=np.array(list(cases), dtype=str),
        **{f"static/{case}/fx_i": np.zeros(1) for case in cases},
    )
    return path


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    """A ``MainWindow`` with **no model** — the archive must stand on its own."""
    from fea_toolkit.gui.controllers.interaction import CONFIG_ENV_VAR
    from fea_toolkit.gui.main_window import MainWindow

    monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "absent.json"))
    win = MainWindow()
    win.resize(900, 700)
    win.show()
    yield win
    win.close()


def test_opening_an_archive_registers_one_view_per_case(window, tmp_path):
    assert window.open_results_path(str(_write_archive(tmp_path))) is True

    assert [view.name for view in window._views.views()] == ["DEAD", "COMB1"]
    assert window._views.active.name == "DEAD"
    assert "Opened 2 results view(s)" in window._message_log.toPlainText()


def test_the_archive_draws_without_a_model(window, tmp_path):
    """Self-contained: the view renders the geometry the archive carries."""
    window.open_results_path(str(_write_archive(tmp_path)))

    frames, _shells, nodes = window._viewer.geometry()

    assert len(frames) == 1
    assert len(nodes) == 2
    assert window._model is window._views.source("results:DEAD")


def test_the_case_views_share_one_model(window, tmp_path):
    """A lens, not a copy: a dozen cases cost one display model between them."""
    window.open_results_path(str(_write_archive(tmp_path)))

    assert window._views.source("results:DEAD") is window._views.source("results:COMB1")


def test_the_tree_lists_the_case_views(window, tmp_path):
    window.open_results_path(str(_write_archive(tmp_path)))

    assert window._tree_model.index_for("views", "DEAD") is not None
    assert window._tree_model.index_for("views", "COMB1") is not None


def test_preprocessing_stays_unavailable(window, tmp_path):
    """An archive is display-only: there is no store behind it to preprocess."""
    window.open_results_path(str(_write_archive(tmp_path)))

    assert window._store is None
    assert window._actions["model.split"].isEnabled() is False


def test_a_case_from_another_combination_is_qualified(window, tmp_path):
    """Two variants of one combination must not look identical in the tree."""
    from fea_toolkit.io.results_repository import NpzResultsRepository

    path = _write_archive(tmp_path, cases=("COMB1",))
    # The writer records the combination a case was generated from.
    data = dict(np.load(path, allow_pickle=False))
    data["static_case_group"] = np.array(["COMB1"], dtype=str)
    np.savez_compressed(path, **data)

    window.open_results_path(str(path))

    assert NpzResultsRepository(path).case_meta("COMB1")["group"] == "COMB1"
    assert window._views.get("results:COMB1").name == "COMB1"


def test_a_file_that_is_not_a_results_archive_is_reported(window, tmp_path):
    other = tmp_path / "other.npz"
    np.savez_compressed(other, something=np.zeros(2))

    assert window.open_results_path(str(other)) is False
    assert "not a results archive" in window._message_log.toPlainText()


def test_a_missing_file_is_reported(window, tmp_path):
    assert window.open_results_path(str(tmp_path / "nope.npz")) is False
    assert "Failed to open results" in window._message_log.toPlainText()
