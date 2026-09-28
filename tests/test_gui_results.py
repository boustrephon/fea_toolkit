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


def _write_archive(tmp_path, *, cases=("DEAD", "COMB1"), with_displacement=False):
    """A minimal results archive: one member, two nodes, those cases.

    ``with_displacement`` adds nodal displacement — a cantilever bending 0.02 m
    at the base and 0.10 m at the tip, so an amplified shape is recognisable by
    eye and assertable exactly.
    """
    path = tmp_path / "results.npz"
    payload = {f"static/{case}/fx_i": np.zeros(1) for case in cases}
    if with_displacement:
        for case in cases:
            payload[f"static/{case}/node_dx"] = np.array([0.02, 0.10])
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
        **payload,
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


# ═══════════════════════════════════════════════════════════════════
# Deformed shape — M7's first results action
# ═══════════════════════════════════════════════════════════════════


def _deformed_points(window):
    """The deformed overlay's vertices as drawn (``None`` when there is none)."""
    actors = window._backend.actors("deformed")
    if not actors:
        return None
    return np.asarray(actors[0].mapper.dataset.points, dtype=float)


class TestDeformedShape:
    """Results ▸ Deformed shape, driven through the real actions.

    Amplification is a *display* factor: the archive's displacements are read in
    model units and never modified, so every expected value below is the
    archive's number times the toolbar's scale.
    """

    def test_it_is_disabled_without_displacement(self, window, tmp_path):
        """No displacement in the archive → greyed, not a button that only refuses."""
        window.open_results_path(str(_write_archive(tmp_path)))
        assert window._actions["results.deformed"].isEnabled() is False

    def test_it_is_enabled_once_a_case_carries_displacement(self, window, tmp_path):
        window.open_results_path(str(_write_archive(tmp_path, with_displacement=True)))
        assert window._actions["results.deformed"].isEnabled() is True

    def test_it_draws_the_shape_at_the_toolbar_scale(self, window, tmp_path):
        window.open_results_path(str(_write_archive(tmp_path, with_displacement=True)))
        window._deformed_scale.setValue(50.0)
        window._actions["results.deformed"].trigger()

        points = _deformed_points(window)
        assert points is not None, "no deformed overlay was drawn"
        assert points[0] == pytest.approx([1.0, 0.0, 0.0])  # base: 0.02 * 50
        assert points[1] == pytest.approx([5.0, 0.0, 10.0])  # tip: 0.10 * 50, at z = 10

    def test_changing_the_scale_redraws_at_the_new_factor(self, window, tmp_path):
        window.open_results_path(str(_write_archive(tmp_path, with_displacement=True)))
        window._actions["results.deformed"].trigger()
        window._deformed_scale.setValue(100.0)

        points = _deformed_points(window)
        assert points[0] == pytest.approx([2.0, 0.0, 0.0])  # 0.02 * 100

    def test_the_scale_alone_never_starts_drawing(self, window, tmp_path):
        """Turning the knob must not be a second way to trigger the action."""
        window.open_results_path(str(_write_archive(tmp_path, with_displacement=True)))
        window._deformed_scale.setValue(200.0)
        assert _deformed_points(window) is None

    def test_switching_cases_drops_the_overlay(self, window, tmp_path):
        """The shape belongs to one case's geometry, so a switch must clear it."""
        window.open_results_path(str(_write_archive(tmp_path, with_displacement=True)))
        window._actions["results.deformed"].trigger()
        assert _deformed_points(window) is not None

        assert window._select_entity_in_tree("views", "COMB1") is True
        assert _deformed_points(window) is None
        assert window._actions["results.deformed"].isChecked() is False

    def test_clear_results_removes_the_overlay(self, window, tmp_path):
        window.open_results_path(str(_write_archive(tmp_path, with_displacement=True)))
        window._actions["results.deformed"].trigger()
        window._actions["results.clear"].trigger()

        assert _deformed_points(window) is None
        assert window._actions["results.deformed"].isChecked() is False
