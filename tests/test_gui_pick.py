"""Milestone-4 viewport -> tree selection: the GUI wiring.

Qt-dependent, so the module is gated by ``needs_gui``.  The mapping itself is
Qt-free (``test_gui_selection_index.py``), the policy and click-versus-drag
logic Qt-free (``test_gui_interaction.py``), and the real VTK picking on an
off-screen render (``test_picking.py``).  Here the *wiring* is covered: the
gesture handlers, the lazy tree row, the inspector and the highlight.

The pickers cannot run here -- the ``offscreen`` Qt platform has no GL context,
so ``ViewportInteraction.install()`` degrades to "picking unavailable" -- which
is why ``pick_at`` is stubbed while the gesture handling stays real.
"""

import pytest

pytestmark = pytest.mark.needs_gui


@pytest.fixture(scope="module")
def qapp():
    """Provide the single process-wide ``QApplication`` Qt requires."""
    from qtpy.QtWidgets import QApplication

    yield QApplication.instance() or QApplication(["pytest-fea-gui"])


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    """A shown ``MainWindow`` with the sample model and default settings."""
    from examples.sample_model import make_sample_model
    from fea_toolkit.gui.controllers.interaction import CONFIG_ENV_VAR
    from fea_toolkit.gui.main_window import MainWindow

    # Pin the settings file, so the developer's own ~/.config cannot leak in.
    monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "absent.json"))
    win = MainWindow(model=make_sample_model())
    win.resize(900, 700)
    win.show()
    yield win
    win.close()


def _click(window, xy=(100.0, 100.0), *, drag_to=None):
    """Drive the real gesture handlers (logical pixels, as Qt reports them).

    ``device_xy`` is irrelevant here because ``pick_at`` is stubbed; the real
    conversion is covered by the Qt-filter tests and ``test_picking.py``.
    """
    interaction = window._interaction
    interaction.begin_gesture(xy)
    if drag_to is not None:
        interaction.update_gesture(drag_to)
    interaction.end_gesture(drag_to or xy, drag_to or xy)


def _stub_pick(result):
    """A ``pick_at`` replacement that returns *result*."""

    def pick_at(*_args, **_kwargs):
        return result

    return pick_at


def _batch_actor(window, category, index=0):
    """The actor the backend created for *category*."""
    return window._backend.actors(category)[index]


def _current_label(window):
    """SAP label of whatever the tree currently has selected."""
    from qtpy.QtCore import Qt

    index = window._tree_view.selectionModel().currentIndex()
    if not index.isValid():
        return None
    entity = index.data(Qt.ItemDataRole.UserRole)
    return getattr(entity, "elem_id", None) or getattr(entity, "node_id", None)


def test_a_clean_click_selects_the_matching_tree_row(window, monkeypatch):
    """A click on a member resolves through the batch to its tree row."""
    from fea_toolkit.gui.views.interactor import PickResult

    frame = window._viewer.geometry()[0][0]
    monkeypatch.setattr(
        window._interaction,
        "pick_at",
        _stub_pick(PickResult(actor=_batch_actor(window, "frames"), index=0)),
    )

    _click(window)

    assert _current_label(window) == frame.elem_id
    # The group was never expanded by hand -- the click must expand it.
    current = window._tree_view.selectionModel().currentIndex()
    assert window._tree_view.isExpanded(current.parent())
    assert window._inspector._table.rowCount() > 0
    assert window._backend.actors("highlights")


def test_a_node_click_selects_the_node_row(window, monkeypatch):
    """A node-first pick resolves through the node cloud instead."""
    from fea_toolkit.gui.views.interactor import PickResult

    nodes = window._viewer.geometry()[2]
    monkeypatch.setattr(
        window._interaction,
        "pick_at",
        _stub_pick(PickResult(actor=_batch_actor(window, "nodes"), index=1, node=True)),
    )

    _click(window)

    assert _current_label(window) == nodes[1].node_id


def test_a_drag_never_picks(window, monkeypatch):
    """The point of the click-versus-drag policy: a drag belongs to the camera."""

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("a drag must not pick")

    monkeypatch.setattr(window._interaction, "pick_at", _forbidden)

    _click(window, (100.0, 100.0), drag_to=(200.0, 180.0))

    assert _current_label(window) is None


def test_a_click_on_empty_space_clears_the_selection(window, monkeypatch):
    """SAP2000-style: clicking nothing empties the inspector and the highlight."""
    from fea_toolkit.gui.views.interactor import PickResult

    monkeypatch.setattr(
        window._interaction,
        "pick_at",
        _stub_pick(PickResult(actor=_batch_actor(window, "frames"), index=0)),
    )
    _click(window)
    assert window._backend.actors("highlights")

    monkeypatch.setattr(window._interaction, "pick_at", _stub_pick(PickResult()))
    _click(window)

    assert window._backend.actors("highlights") == []
    assert window._inspector._title.text() == "No selection"


def test_a_click_on_a_foreign_actor_clears_the_selection(window, monkeypatch):
    """An actor the backend never created (a future overlay) is not selectable."""
    from fea_toolkit.gui.views.interactor import PickResult

    monkeypatch.setattr(
        window._interaction, "pick_at", _stub_pick(PickResult(actor=object(), index=0))
    )

    _click(window)

    assert _current_label(window) is None


def test_a_pick_outside_the_batch_selects_nothing(window, monkeypatch):
    """A stale cell index must not raise -- it simply selects nothing."""
    from fea_toolkit.gui.views.interactor import PickResult

    monkeypatch.setattr(
        window._interaction,
        "pick_at",
        _stub_pick(PickResult(actor=_batch_actor(window, "frames"), index=99)),
    )

    _click(window)

    assert _current_label(window) is None


def test_the_hint_and_policy_follow_the_settings_file(qapp, monkeypatch, tmp_path):
    """A settings file nominating the right button is honoured and announced."""
    import json

    from examples.sample_model import make_sample_model
    from fea_toolkit.gui.controllers.interaction import CONFIG_ENV_VAR
    from fea_toolkit.gui.main_window import MainWindow

    settings = tmp_path / "gui.json"
    settings.write_text(json.dumps({"preset": "right_click"}))
    monkeypatch.setenv(CONFIG_ENV_VAR, str(settings))

    win = MainWindow(model=make_sample_model())
    try:
        assert win._policy.pick_button == "right"
        assert "Right-click an element to select it" in win._message_log.toPlainText()
    finally:
        win.close()


def test_a_typo_in_the_settings_file_is_reported(qapp, monkeypatch, tmp_path):
    """A misspelled setting must be visible in the log, not silently ignored."""
    from examples.sample_model import make_sample_model
    from fea_toolkit.gui.controllers.interaction import CONFIG_ENV_VAR
    from fea_toolkit.gui.main_window import MainWindow

    settings = tmp_path / "gui.json"
    settings.write_text('{"pick_tolernce": 0.002}')
    monkeypatch.setenv(CONFIG_ENV_VAR, str(settings))

    win = MainWindow(model=make_sample_model())
    try:
        assert "pick_tolernce" in win._message_log.toPlainText()
    finally:
        win.close()


def test_selecting_an_unknown_label_reports_failure(window):
    assert window._select_entity_in_tree("frame_elements", "no-such-element") is False
    assert window._select_entity_in_tree("no_such_group", "1") is False


# ── The Qt event filter (the path a real click actually takes) ──────


def _qt_mouse_event(event_type, xy, button=None):
    """Build a Qt mouse event of *event_type* at logical *xy*."""
    from qtpy.QtCore import QPointF, Qt
    from qtpy.QtGui import QMouseEvent

    if button is None:
        button = Qt.MouseButton.LeftButton
    position = QPointF(*xy)
    return QMouseEvent(
        event_type,
        position,
        position,
        button,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )


def test_the_qt_filter_drives_the_gesture_in_device_pixels(window, monkeypatch):
    """A real Qt click reaches the adapter, converted to device pixels.

    VTK pickers take *device* pixels with a bottom-left origin while Qt reports
    logical pixels from the top left; mixing the two is what made an early
    calibration miss by the device pixel ratio.
    """
    from qtpy.QtCore import QEvent

    from fea_toolkit.gui.views.interactor import PickResult

    seen = []

    def _record(x, y):
        seen.append((x, y))
        return PickResult()

    monkeypatch.setattr(window._interaction, "pick_at", _record)

    widget = window._interactor
    ratio = float(widget.devicePixelRatioF())
    height = float(widget.render_window.GetSize()[1])
    logical = (120.0, 80.0)

    window._mouse_filter.eventFilter(widget, _qt_mouse_event(QEvent.Type.MouseButtonPress, logical))
    window._mouse_filter.eventFilter(
        widget, _qt_mouse_event(QEvent.Type.MouseButtonRelease, logical)
    )

    assert seen == [(120.0 * ratio, height - 80.0 * ratio)]


def test_the_qt_filter_ignores_a_drag(window, monkeypatch):
    """Press, move past the threshold, release: the camera keeps that gesture."""

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("a drag must not pick")

    monkeypatch.setattr(window._interaction, "pick_at", _forbidden)

    from qtpy.QtCore import QEvent

    widget = window._interactor
    window._mouse_filter.eventFilter(
        widget, _qt_mouse_event(QEvent.Type.MouseButtonPress, (10.0, 10.0))
    )
    window._mouse_filter.eventFilter(widget, _qt_mouse_event(QEvent.Type.MouseMove, (80.0, 70.0)))
    window._mouse_filter.eventFilter(
        widget, _qt_mouse_event(QEvent.Type.MouseButtonRelease, (80.0, 70.0))
    )


def test_the_qt_filter_ignores_the_other_button(window, monkeypatch):
    """With the default policy the right button is not a selecting gesture."""
    from qtpy.QtCore import QEvent, Qt

    from fea_toolkit.gui.views.interactor import PickResult

    seen = []
    monkeypatch.setattr(
        window._interaction, "pick_at", lambda x, y: (seen.append((x, y)), PickResult())[1]
    )

    widget = window._interactor
    other = Qt.MouseButton.RightButton
    window._mouse_filter.eventFilter(
        widget, _qt_mouse_event(QEvent.Type.MouseButtonPress, (5.0, 5.0), other)
    )
    window._mouse_filter.eventFilter(
        widget, _qt_mouse_event(QEvent.Type.MouseButtonRelease, (5.0, 5.0), other)
    )

    assert seen == []
