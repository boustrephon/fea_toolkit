"""The named-selection GUI surface (Model ▸ Name selection…, Selections tree group)."""

import pytest


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
        win.close()


def test_name_selection_action_is_live(window):
    action = window._actions["model.selections"]
    assert action.isEnabled()
    assert action.text() == "Name selection\u2026"


def test_name_selection_stores_and_rebuilds_the_tree(window, monkeypatch):
    from qtpy.QtWidgets import QInputDialog

    replies = iter([("core", True), ("section=COL", True)])
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: next(replies))

    window._on_name_selection()

    assert "core" in window._named_selections
    assert window._named_selections["core"].selection.to_string() == "section=COL"
    assert "Named selection 'core'" in window._message_log.toPlainText()


def test_build_groups_includes_and_omits_a_selections_group():
    from fea_toolkit.gui.models.model_index import build_groups
    from fea_toolkit.model.named_selection import NamedSelection

    named = {"core": NamedSelection(name="core")}
    groups = build_groups(_model(), selections=named)
    assert any(group.key == "selections" and group.label == "Selections" for group in groups)

    groups = build_groups(_model(), selections={})
    assert not any(group.key == "selections" for group in groups)


def test_name_selection_uses_the_viewport_pick_when_chosen(window, monkeypatch):
    """A current viewport/tree selection becomes the named selection's ids."""
    from qtpy.QtWidgets import QInputDialog, QMessageBox

    frames, _shells, _nodes = window._viewer.geometry()
    window._select_entity_in_tree("frame_elements", frames[0].elem_id)

    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("core", True))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    window._on_name_selection()

    assert "core" in window._named_selections
    named = window._named_selections["core"]
    assert named.element_ids == [frames[0].elem_id]
    assert named.node_ids == []
    assert named.selection.to_string() == ""  # a pick carries ids, not criteria
