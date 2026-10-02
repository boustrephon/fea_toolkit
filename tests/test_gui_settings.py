"""Tests for the GUI display-settings module (``gui/controllers/settings.py``)."""

import json

import pytest

from fea_toolkit.gui.controllers.settings import (
    DISPLAY_DEFAULTS,
    load_gui_settings,
    resolve_display,
    save_display_settings,
)


def test_defaults_when_the_file_is_missing(tmp_path):
    policy, display, notes = load_gui_settings(tmp_path / "none.json")
    assert display == DISPLAY_DEFAULTS
    assert display.shell_opacity == 0.7
    assert display.shrink == 1.0
    assert display.force_quantity == "M3"
    assert policy.pick_button == "left"
    assert notes == []


def test_display_section_is_applied_and_interaction_keys_still_parse(tmp_path):
    path = tmp_path / "gui.json"
    path.write_text(
        json.dumps({"preset": "right_click", "display": {"shrink": 0.9, "shell_opacity": 0.5}})
    )
    policy, display, notes = load_gui_settings(path)
    assert display.shrink == 0.9
    assert display.shell_opacity == 0.5
    assert policy.pick_button == "right"
    # The ``display`` key must not leak into the interaction resolver's notes.
    assert notes == []


def test_unknown_display_key_is_reported():
    settings, notes = resolve_display({"shrink": 0.8, "wibble": 1})
    assert settings.shrink == 0.8
    assert any("wibble" in note for note in notes)


def test_bad_numeric_value_falls_back_to_the_default():
    settings, notes = resolve_display({"shrink": "not-a-number"})
    assert settings.shrink == DISPLAY_DEFAULTS.shrink
    assert any("shrink" in note for note in notes)


def test_unknown_force_quantity_falls_back_to_the_default():
    settings, notes = resolve_display({"force_quantity": "NOPE"})
    assert settings.force_quantity == DISPLAY_DEFAULTS.force_quantity
    assert any("force_quantity" in note for note in notes)


def test_non_finite_numeric_value_falls_back_to_the_default():
    for value in (float("nan"), float("inf"), float("-inf")):
        settings, notes = resolve_display({"shell_opacity": value})
        assert settings.shell_opacity == DISPLAY_DEFAULTS.shell_opacity
        assert any("shell_opacity" in note for note in notes)


def test_out_of_range_numeric_value_falls_back_to_the_default():
    settings, notes = resolve_display({"shrink": 2.0, "shell_opacity": 0.0})
    assert settings.shrink == DISPLAY_DEFAULTS.shrink
    assert settings.shell_opacity == DISPLAY_DEFAULTS.shell_opacity
    assert any("shrink" in note for note in notes)
    assert any("shell_opacity" in note for note in notes)


def test_non_dict_display_section_is_reported():
    settings, notes = resolve_display(["not", "a", "mapping"])
    assert settings == DISPLAY_DEFAULTS
    assert any("JSON object" in note for note in notes)


def test_save_display_settings_with_a_malformed_file_reports_failure(tmp_path):
    path = tmp_path / "gui.json"
    path.write_text("{not valid json")
    notes = save_display_settings(path, {"shrink": 0.8})
    assert any("not saved" in note for note in notes)
    # The malformed file is left untouched, not overwritten.
    assert path.read_text() == "{not valid json"


def test_save_display_settings_with_a_non_object_file_reports_failure(tmp_path):
    path = tmp_path / "gui.json"
    path.write_text("[1, 2, 3]")
    notes = save_display_settings(path, {"shrink": 0.8})
    assert any("not saved" in note for note in notes)
    # A valid-but-non-object file is likewise left untouched, not overwritten.
    assert path.read_text() == "[1, 2, 3]"


def test_save_round_trip_preserves_interaction_keys(tmp_path):
    path = tmp_path / "gui.json"
    path.write_text(json.dumps({"preset": "right_click", "pick_tolerance": 0.004}))
    notes = save_display_settings(path, {"shrink": 0.85, "shell_opacity": 0.6})
    assert notes == []

    data = json.loads(path.read_text())
    assert data["preset"] == "right_click"
    assert data["pick_tolerance"] == 0.004
    assert data["display"]["shrink"] == 0.85

    policy, display, notes = load_gui_settings(path)
    assert display.shrink == 0.85
    assert display.shell_opacity == 0.6
    assert policy.pick_button == "right"
    assert policy.pick_tolerance == 0.004


@pytest.mark.needs_gui
def test_display_settings_round_trip_through_main_window(qapp, monkeypatch, tmp_path):
    """Closing a window persists the knobs; a new window reloads them."""
    from fea_toolkit.gui.controllers.interaction import CONFIG_ENV_VAR
    from fea_toolkit.gui.main_window import MainWindow

    monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "gui.json"))

    win = MainWindow()
    win._shell_opacity.setValue(0.42)
    win._shrink.setValue(0.6)
    win.close()  # closeEvent -> _save_display_settings

    data = json.loads((tmp_path / "gui.json").read_text())
    assert data["display"]["shell_opacity"] == pytest.approx(0.42)
    assert data["display"]["shrink"] == pytest.approx(0.6)

    win2 = MainWindow()
    assert win2._display.shell_opacity == pytest.approx(0.42)
    assert win2._display.shrink == pytest.approx(0.6)
    win2.close()
