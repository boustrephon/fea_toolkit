"""Tests for per-model view state (``gui/controllers/view_state.py``).

The controller is Qt-free, so its persistence is tested directly; the two
``needs_gui`` tests exercise the ``MainWindow`` wiring (save on close, restore
on open, and the Reset view action).
"""

import json
from pathlib import Path

import pytest

from fea_toolkit.gui.controllers.interaction import CONFIG_ENV_VAR
from fea_toolkit.gui.controllers.view_state import (
    clear_view_state,
    load_view_state,
    save_view_state,
    view_state_path,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "sample.s2k"


def _pin(tmp_path, monkeypatch):
    monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "gui.json"))


def _source(tmp_path, name="model.s2k", content=b"frame 1"):
    path = tmp_path / name
    path.write_bytes(content)
    return str(path)


def test_view_state_path_is_keyed_under_the_config_dir(tmp_path, monkeypatch):
    _pin(tmp_path, monkeypatch)
    path = view_state_path("/some/model.s2k")
    assert path.parent == tmp_path / "state"
    assert path.name.endswith(".json")
    # The same source always maps to the same file; a different one does not.
    assert view_state_path("/some/model.s2k") == path
    assert view_state_path("/other/model.s2k") != path


def test_load_missing_state_is_empty(tmp_path, monkeypatch):
    _pin(tmp_path, monkeypatch)
    state, notes = load_view_state("/some/model.s2k")
    assert state == {}
    assert notes == []


def test_save_load_round_trip(tmp_path, monkeypatch):
    _pin(tmp_path, monkeypatch)
    source = _source(tmp_path)
    camera = [[1.0, 2.0, 3.0], [0.0, 0.0, 0.0], [0.0, 0.0, 1.0]]
    notes = save_view_state(
        source, {"camera_position": camera, "hidden_elem_ids": ["Area:A1", "Frame:2"]}
    )
    assert notes == []

    state, notes = load_view_state(source)
    assert notes == []
    assert state["camera_position"] == camera
    assert state["hidden_elem_ids"] == ["Area:A1", "Frame:2"]


def test_malformed_state_is_reported(tmp_path, monkeypatch):
    _pin(tmp_path, monkeypatch)
    source = "/some/model.s2k"
    path = view_state_path(source)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json")
    state, notes = load_view_state(source)
    assert state == {}
    assert any("could not read" in note for note in notes)


def test_non_object_state_is_reported(tmp_path, monkeypatch):
    _pin(tmp_path, monkeypatch)
    source = "/some/model.s2k"
    path = view_state_path(source)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[1, 2, 3]")
    state, notes = load_view_state(source)
    assert state == {}
    assert any("JSON object" in note for note in notes)


def test_unknown_state_key_is_reported(tmp_path, monkeypatch):
    _pin(tmp_path, monkeypatch)
    source = _source(tmp_path)
    save_view_state(source, {"hidden_elem_ids": ["Area:A1"]})
    path = view_state_path(source)
    data = json.loads(path.read_text())
    data["wibble"] = 1
    path.write_text(json.dumps(data))

    state, notes = load_view_state(source)
    assert state == {"hidden_elem_ids": ["Area:A1"]}
    assert any("wibble" in note for note in notes)


def test_clear_removes_the_state_file(tmp_path, monkeypatch):
    _pin(tmp_path, monkeypatch)
    source = _source(tmp_path)
    save_view_state(source, {"hidden_elem_ids": ["Area:A1"]})
    assert view_state_path(source).is_file()
    assert clear_view_state(source) == []
    assert not view_state_path(source).is_file()


def test_changed_model_drops_stale_state(tmp_path, monkeypatch):
    _pin(tmp_path, monkeypatch)
    source = _source(tmp_path, content=b"original")
    save_view_state(source, {"hidden_elem_ids": ["Area:A1"]})

    # Re-edit the model: same path, different content.
    Path(source).write_bytes(b"changed")

    state, notes = load_view_state(source)
    assert state == {}
    assert any("model changed" in note for note in notes)


def test_save_filters_unknown_state_keys(tmp_path, monkeypatch):
    _pin(tmp_path, monkeypatch)
    source = _source(tmp_path)
    save_view_state(source, {"hidden_elem_ids": ["Area:A1"], "wibble": 1})
    state, notes = load_view_state(source)
    assert state == {"hidden_elem_ids": ["Area:A1"]}
    assert notes == []


@pytest.mark.needs_gui
def test_view_state_round_trip_through_open_and_close(qapp, monkeypatch, tmp_path):
    """Closing a window persists the camera + hidden set; reopening restores them."""
    from fea_toolkit.gui.main_window import MainWindow

    monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "gui.json"))

    win = MainWindow()
    assert win.open_path(str(_FIXTURE)) is True
    hidden = sorted(win._all_element_ids())[:1]
    assert hidden
    win._hidden_elem_ids = set(hidden)
    win.close()  # closeEvent -> _save_view_state

    state, notes = load_view_state(str(_FIXTURE))
    assert notes == []
    assert state["hidden_elem_ids"] == hidden
    assert state["camera_position"]

    win2 = MainWindow()
    assert win2.open_path(str(_FIXTURE)) is True
    assert win2._hidden_elem_ids == set(hidden)
    win2.close()


@pytest.mark.needs_gui
def test_reset_view_drops_hidden_and_saved_state(qapp, monkeypatch, tmp_path):
    """Reset view clears the hidden set and forgets the saved per-model state."""
    from fea_toolkit.gui.main_window import MainWindow

    monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "gui.json"))

    win = MainWindow()
    assert win.open_path(str(_FIXTURE)) is True
    hidden = sorted(win._all_element_ids())[:1]
    assert hidden
    win._hidden_elem_ids = set(hidden)
    win._save_view_state()
    assert load_view_state(str(_FIXTURE))[0]["hidden_elem_ids"] == hidden

    win._actions["view.reset_view"].trigger()
    assert win._hidden_elem_ids == set()
    assert load_view_state(str(_FIXTURE))[0] == {}
    win.close()
    # closeEvent must not recreate the state the reset just cleared.
    assert load_view_state(str(_FIXTURE))[0] == {}


@pytest.mark.needs_gui
def test_reset_then_a_view_change_resumes_saving(qapp, monkeypatch, tmp_path):
    """After a reset, a subsequent view change is saved again on close."""
    from fea_toolkit.gui.main_window import MainWindow

    monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "gui.json"))

    win = MainWindow()
    assert win.open_path(str(_FIXTURE)) is True
    win._actions["view.reset_view"].trigger()
    assert load_view_state(str(_FIXTURE))[0] == {}

    win._view_xy()  # a camera action changes the view away from the reset one
    win.close()

    state, notes = load_view_state(str(_FIXTURE))
    assert notes == []
    assert state.get("camera_position")
