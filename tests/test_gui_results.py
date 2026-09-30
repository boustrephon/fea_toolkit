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


def _write_archive(
    tmp_path, *, cases=("DEAD", "COMB1"), with_displacement=False, with_forces=False
):
    """A minimal results archive: one member, two nodes, those cases.

    ``with_displacement`` adds nodal displacement — a cantilever bending 0.02 m
    at the base and 0.10 m at the tip, so an amplified shape is recognisable by
    eye and assertable exactly.

    ``with_forces`` adds an end-force block with one distinguishing value per
    component, so the *size* of a drawn flag identifies the quantity it came
    from (the flag height is ``|value| * scale``, and the J-end negates it).
    """
    path = tmp_path / "results.npz"
    payload = {f"static/{case}/fx_i": np.zeros(1) for case in cases}
    if with_displacement:
        for case in cases:
            payload[f"static/{case}/node_dx"] = np.array([0.02, 0.10])
    if with_forces:
        # The toolkit's writers always declare this, so a realistic archive
        # carries it; `test_a_legacy_archive_reads_its_bare_arrays_as_global`
        # strips it again to cover a file from elsewhere.
        payload["forces_coordinate_system"] = np.array(["local"], dtype=str)
        for case in cases:
            payload.update(
                {
                    f"static/{case}/fx_i": np.array([1.0]),
                    f"static/{case}/fx_j": np.array([1.0]),
                    f"static/{case}/fy_i": np.array([2.0]),
                    f"static/{case}/fy_j": np.array([-2.0]),
                    f"static/{case}/my_i": np.array([3.0]),
                    f"static/{case}/my_j": np.array([-3.0]),
                    f"static/{case}/mz_i": np.array([4.0]),
                    f"static/{case}/mz_j": np.array([-4.0]),
                }
            )
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
    assert window._actions["model.mesh"].isEnabled() is False


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

    def test_it_draws_the_shape_autoscaled_to_ten_percent_of_the_model(self, window, tmp_path):
        window.open_results_path(str(_write_archive(tmp_path, with_displacement=True)))
        window._actions["results.deformed"].trigger()

        points = _deformed_points(window)
        assert points is not None, "no deformed overlay was drawn"
        # Auto-scale: max displacement 0.10 m → 10 % of the 10 m model → ×10.
        assert points[0] == pytest.approx([0.2, 0.0, 0.0])  # base: 0.02 × 10
        assert points[1] == pytest.approx([1.0, 0.0, 10.0])  # tip: 0.10 × 10

    def test_changing_the_size_redraws_at_the_new_percentage(self, window, tmp_path):
        window.open_results_path(str(_write_archive(tmp_path, with_displacement=True)))
        window._actions["results.deformed"].trigger()
        window._deformed_scale.setValue(20.0)

        points = _deformed_points(window)
        assert points[1] == pytest.approx([2.0, 0.0, 10.0])  # 20 % → ×20

    def test_a_large_displacement_stays_bounded_by_the_model(self, window, tmp_path):
        """Auto-scale is unit-agnostic: a 5 m sway still reads as 10 % of the model."""
        path = _write_archive(tmp_path, with_displacement=True)
        data = dict(np.load(path, allow_pickle=False))
        for case in ("DEAD", "COMB1"):
            data[f"static/{case}/node_dx"] = np.array([0.02, 5.0])
        np.savez_compressed(path, **data)

        window.open_results_path(str(path))
        window._actions["results.deformed"].trigger()

        points = _deformed_points(window)
        assert points[1] == pytest.approx([1.0, 0.0, 10.0])  # 5 m × (0.1 × 10 / 5)

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


# ═══════════════════════════════════════════════════════════════════
# Force diagrams — M7's second results action
# ═══════════════════════════════════════════════════════════════════


def _write_force_free_archive(tmp_path):
    """The same archive with a case that carries no force arrays at all.

    A perfectly valid archive — recording displacement without forces is
    normal — and the state the force action has to stay greyed for.
    """
    path = _write_archive(tmp_path)
    data = {
        key: value
        for key, value in dict(np.load(path, allow_pickle=False)).items()
        if not key.startswith("static/")
    }
    np.savez_compressed(path, **data)
    return path


def _flag_offset(window):
    """The drawn flag's largest distance from the member axis (``None`` if none).

    The fixture member runs along global Z, and each of the six quantities is
    extruded *perpendicular* to the member (``_flag_direction`` maps them onto
    the local y or z axis), so the offset from the z-axis is the force times
    the scale — without having to know which of the two axes it went along.
    """
    actors = window._backend.actors("force_flags")
    if not actors:
        return None
    points = np.asarray(actors[0].mapper.dataset.points, dtype=float)
    return float(np.hypot(points[:, 0], points[:, 1]).max())


class TestForceDiagrams:
    """Results ▸ Force diagrams, driven through the real action and selector.

    The archive carries ``fx = 1``, ``fy = 2``, ``my = 3`` and ``mz = 4``, so a
    flag's size says *which* quantity was drawn — a selector wired to the wrong
    key, or to none at all, draws a diagram of the wrong size rather than an
    empty scene.
    """

    def test_it_is_disabled_without_end_forces(self, window, tmp_path):
        """No force arrays → greyed, selector included, not a button that refuses."""
        window.open_results_path(str(_write_force_free_archive(tmp_path)))

        assert window._actions["results.forces"].isEnabled() is False
        assert window._force_quantity.isEnabled() is False

    def test_it_is_enabled_once_a_case_carries_forces(self, window, tmp_path):
        window.open_results_path(str(_write_archive(tmp_path, with_forces=True)))

        assert window._actions["results.forces"].isEnabled() is True
        assert window._force_quantity.isEnabled() is True

    def test_the_selector_opens_on_m3(self, window, tmp_path):
        """Bending is what a diagram is usually read for: M3 is the ``Mz`` key."""
        window.open_results_path(str(_write_archive(tmp_path, with_forces=True)))

        assert window._force_quantity.currentText() == "M3"

    def test_it_draws_the_diagram_autoscaled_to_ten_percent_of_the_model(self, window, tmp_path):
        window.open_results_path(str(_write_archive(tmp_path, with_forces=True)))
        window._actions["results.forces"].trigger()

        # Auto-scale: the largest flag (mz = 4) is 10 % of the 10 m model.
        assert _flag_offset(window) == pytest.approx(1.0)

    def test_changing_the_size_redraws_at_the_new_percentage(self, window, tmp_path):
        window.open_results_path(str(_write_archive(tmp_path, with_forces=True)))
        window._actions["results.forces"].trigger()
        window._force_scale.setValue(50.0)

        assert _flag_offset(window) == pytest.approx(5.0)  # 50 % of the 10 m model

    def test_a_huge_force_stays_bounded_by_the_model(self, window, tmp_path):
        """Auto-scale is unit-agnostic: a million-unit moment still fits the model."""
        path = _write_archive(tmp_path, with_forces=True)
        data = dict(np.load(path, allow_pickle=False))
        for case in ("DEAD", "COMB1"):
            data[f"static/{case}/mz_i"] = np.array([4.0e6])
            data[f"static/{case}/mz_j"] = np.array([-4.0e6])
        np.savez_compressed(path, **data)

        window.open_results_path(str(path))
        window._actions["results.forces"].trigger()

        assert _flag_offset(window) == pytest.approx(1.0)  # still 10 % of the model

    def test_the_scale_alone_never_starts_drawing(self, window, tmp_path):
        """Turning the knob must not be a second way to trigger the action."""
        window.open_results_path(str(_write_archive(tmp_path, with_forces=True)))
        window._force_scale.setValue(200.0)

        assert _flag_offset(window) is None

    def test_the_selector_chooses_the_component(self, window, tmp_path):
        """The label maps to its schema key: M3 draws ``mz``, P draws ``fx``."""
        window.open_results_path(str(_write_archive(tmp_path, with_forces=True)))
        window._actions["results.forces"].trigger()

        window._force_quantity.setCurrentText("P")

        assert _flag_offset(window) == pytest.approx(1.0)  # fx = 1 → 10 % of model

    def test_the_selector_reads_the_local_dof_it_names(self, window, tmp_path):
        """V2 is the local-2 shear — ``fy`` (2), not ``my`` (3) and not ``mz``."""
        window.open_results_path(str(_write_archive(tmp_path, with_forces=True)))
        window._actions["results.forces"].trigger()

        window._force_quantity.setCurrentText("V2")

        assert _flag_offset(window) == pytest.approx(1.0)  # fy = 2 → 10 % of model

    def test_the_selector_alone_never_starts_drawing(self, window, tmp_path):
        """Choosing a component must not be a second way to trigger the action."""
        window.open_results_path(str(_write_archive(tmp_path, with_forces=True)))
        window._force_quantity.setCurrentText("V2")

        assert _flag_offset(window) is None

    def test_a_legacy_archive_reads_its_bare_arrays_as_global(self, window, tmp_path):
        """No ``forces_coordinate_system`` flag: the values are global, and read so.

        Every archive this toolkit writes declares ``local``, but a file from
        elsewhere may not — and a flag diagram of global components is only
        geometrically honest when the local and global axes coincide, which
        ``overlay_forces`` warns about rather than hides.
        """
        path = _write_archive(tmp_path, with_forces=True)
        data = {
            key: value
            for key, value in dict(np.load(path, allow_pickle=False)).items()
            if key != "forces_coordinate_system"
        }
        np.savez_compressed(path, **data)

        window.open_results_path(str(path))
        with pytest.warns(UserWarning):
            window._actions["results.forces"].trigger()

        assert _flag_offset(window) == pytest.approx(1.0)  # still mz = 4 → 10 % of model

    def test_switching_cases_drops_the_overlay(self, window, tmp_path):
        window.open_results_path(str(_write_archive(tmp_path, with_forces=True)))
        window._actions["results.forces"].trigger()
        assert _flag_offset(window) is not None

        assert window._select_entity_in_tree("views", "COMB1") is True
        assert _flag_offset(window) is None
        assert window._actions["results.forces"].isChecked() is False

    def test_clear_results_removes_the_overlay(self, window, tmp_path):
        window.open_results_path(str(_write_archive(tmp_path, with_forces=True)))
        window._actions["results.forces"].trigger()
        window._actions["results.clear"].trigger()

        assert _flag_offset(window) is None
        assert window._actions["results.forces"].isChecked() is False

    def test_the_shape_and_the_diagram_are_independent(self, window, tmp_path):
        """Two overlays on one scene: toggling one off must not drop the other."""
        window.open_results_path(
            str(_write_archive(tmp_path, with_displacement=True, with_forces=True))
        )
        window._actions["results.deformed"].trigger()
        window._actions["results.forces"].trigger()
        assert _deformed_points(window) is not None
        assert _flag_offset(window) is not None

        window._actions["results.deformed"].trigger()  # toggle the shape off

        assert _deformed_points(window) is None
        assert _flag_offset(window) is not None
