"""Interaction policy, settings file and click-versus-drag gesture (Qt-free).

The policy decides how the mouse behaves -- which button selects, how far the
pointer may stray before a gesture is a *drag*, how fat the picking region is --
and the settings file overrides it.  All pure logic, so this runs without Qt,
VTK or a render window (no marker).
"""

import json
from dataclasses import replace

import pytest

from fea_toolkit.gui.controllers.interaction import (
    CONFIG_ENV_VAR,
    DEFAULT_PRESET,
    PRESETS,
    ClickGesture,
    default_config_path,
    load_policy,
    resolve_policy,
)

# ── Policy resolution ───────────────────────────────────────────────


def test_defaults_are_the_click_drag_preset():
    policy, notes = resolve_policy()
    assert notes == []
    assert policy.preset == DEFAULT_PRESET == "click_drag"
    assert policy.pick_button == "left"
    assert policy.node_priority is True
    assert policy.pick_tolerance < 0.025  # not VTK's fat default region


def test_a_preset_can_be_named():
    policy, notes = resolve_policy({"preset": "right_click"})
    assert notes == []
    assert policy.pick_button == "right"


def test_raw_knobs_override_the_preset():
    policy, notes = resolve_policy(
        {"preset": "click_drag", "drag_threshold_px": 12, "node_priority": False}
    )
    assert notes == []
    assert policy.drag_threshold_px == 12
    assert policy.node_priority is False


def test_unknown_preset_and_settings_are_reported_not_fatal():
    policy, notes = resolve_policy({"preset": "wibble", "pick_tolernce": 0.01})
    assert policy.preset == DEFAULT_PRESET  # typo'd key, still a usable policy
    assert any("wibble" in note for note in notes)
    assert any("pick_tolernce" in note for note in notes)


def test_invalid_values_fall_back_to_the_preset_with_a_note():
    policy, notes = resolve_policy({"pick_button": "middle", "pick_tolerance": 3.0})
    assert policy.preset == DEFAULT_PRESET
    assert policy.pick_button == "left"
    assert notes and "rejected" in notes[0]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pick_button", "middle"),
        ("drag_threshold_px", -1),
        ("pick_tolerance", 1.5),
        ("node_snap_tolerance", -0.1),
    ],
)
def test_the_policy_rejects_nonsense(field, value):
    with pytest.raises(ValueError):
        replace(PRESETS[DEFAULT_PRESET], **{field: value})


# ── Settings file ───────────────────────────────────────────────────


def test_missing_settings_file_uses_the_defaults(tmp_path):
    policy, notes = load_policy(tmp_path / "absent.json")
    assert notes == []
    assert policy == PRESETS[DEFAULT_PRESET]


def test_settings_file_is_applied(tmp_path):
    path = tmp_path / "gui.json"
    path.write_text(json.dumps({"preset": "right_click", "pick_tolerance": 0.001}))
    policy, notes = load_policy(path)
    assert notes == []
    assert policy.pick_button == "right"
    assert policy.pick_tolerance == 0.001


def test_malformed_settings_file_falls_back_and_says_so(tmp_path):
    path = tmp_path / "gui.json"
    path.write_text("{not json")
    policy, notes = load_policy(path)
    assert policy == PRESETS[DEFAULT_PRESET]
    assert notes and "could not read" in notes[0]


def test_settings_file_must_hold_an_object(tmp_path):
    path = tmp_path / "gui.json"
    path.write_text("[1, 2, 3]")
    _policy, notes = load_policy(path)
    assert notes and "JSON object" in notes[0]


def test_config_path_honours_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "custom.json"))
    assert default_config_path() == tmp_path / "custom.json"
    monkeypatch.delenv(CONFIG_ENV_VAR)
    assert default_config_path().name == "gui.json"


# ── Click versus drag ───────────────────────────────────────────────


def _gesture(**overrides) -> ClickGesture:
    """A gesture tracker on a preset with *overrides* applied."""
    return ClickGesture(replace(PRESETS[DEFAULT_PRESET], **overrides))


def test_a_clean_click_selects():
    gesture = _gesture()
    gesture.press((100, 100))
    assert gesture.release((101, 102)) is True


def test_a_drag_does_not_select():
    gesture = _gesture()
    gesture.press((100, 100))
    gesture.move((140, 130))
    assert gesture.release((140, 130)) is False


def test_a_fast_drag_without_move_events_does_not_select():
    """A quick flick can outrun the move observer, so release re-checks."""
    gesture = _gesture()
    gesture.press((100, 100))
    assert gesture.release((130, 100)) is False


def test_movement_without_a_press_is_ignored():
    gesture = _gesture()
    gesture.move((10, 10))  # hovering before any click
    assert gesture.release((10, 10)) is False


def test_release_without_a_press_does_not_select():
    assert _gesture().release((5, 5)) is False


def test_the_threshold_is_configurable():
    strict = _gesture(drag_threshold_px=0)
    strict.press((0, 0))
    assert strict.release((1, 0)) is False

    loose = _gesture(drag_threshold_px=20)
    loose.press((0, 0))
    assert loose.release((10, 10)) is True


def test_only_the_policy_button_starts_a_gesture():
    left = _gesture()
    assert left.handles("left") is True
    assert left.handles("right") is False

    right = _gesture(pick_button="right")
    assert right.handles("right") is True
    assert right.handles("left") is False


def test_reset_forgets_an_in_flight_gesture():
    gesture = _gesture()
    gesture.press((0, 0))
    gesture.reset()
    assert gesture.release((0, 0)) is False


def test_a_second_press_restarts_the_gesture():
    gesture = _gesture()
    gesture.press((0, 0))
    gesture.move((50, 50))  # a drag...
    gesture.press((200, 200))  # ...but the user pressed again
    assert gesture.release((200, 200)) is True
