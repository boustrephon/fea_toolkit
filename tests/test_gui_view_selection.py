"""Derived views in the GUI: duplicate a view, edit its selection expression.

Qt-dependent (``needs_gui``).  The selection dialog is stubbed for the action
tests (a modal ``exec()`` would block), and driven directly for its own tests.
"""

import pytest

pytestmark = pytest.mark.needs_gui


@pytest.fixture(scope="module")
def qapp():
    """Provide the single process-wide ``QApplication`` Qt requires."""
    from qtpy.QtWidgets import QApplication

    yield QApplication.instance() or QApplication(["pytest-fea-gui"])


def _chain_model():
    """The sample member extended into the chain 1-2-3-4 (see the Qt-free suite)."""
    from examples.sample_model import make_sample_model
    from fea_toolkit.model.sap_data import FrameElement, Node

    md = make_sample_model()
    md.nodes["3"] = Node(node_id="3", node_tag=3, x=0.0, y=0.0, z=20.0)
    md.nodes["4"] = Node(node_id="4", node_tag=4, x=0.0, y=0.0, z=30.0)
    md.frame_elements["2"] = FrameElement(elem_id="2", elem_tag=2, node_i="2", node_j="3")
    md.frame_elements["3"] = FrameElement(elem_id="3", elem_tag=3, node_i="3", node_j="4")
    md.frame_assignments["2"] = "UB300"
    md.frame_assignments["3"] = "UB300"
    return md


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    """A ``MainWindow`` showing the chain, with one view registered."""
    from fea_toolkit.gui.controllers.interaction import CONFIG_ENV_VAR
    from fea_toolkit.gui.main_window import MainWindow

    monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "absent.json"))
    win = MainWindow(model=_chain_model())
    win.resize(900, 700)
    win.show()
    yield win
    win.close()


def _stub_dialog(monkeypatch, selection):
    """Answer the selection dialog without opening it (``None`` = cancelled)."""
    from fea_toolkit.gui.views.selection_dialog import SelectionDialog

    monkeypatch.setattr(
        SelectionDialog,
        "edit",
        staticmethod(lambda current=None, parent=None: selection),
    )


def _ok_button(dialog):
    from qtpy.QtWidgets import QDialogButtonBox

    return dialog._buttons.button(QDialogButtonBox.StandardButton.Ok)


class TestSelectionDialog:
    """The dialog parses the same expression the CLI does, as you type."""

    def test_a_valid_expression_parses_and_enables_ok(self, qapp):
        from fea_toolkit.gui.views.selection_dialog import SelectionDialog

        dialog = SelectionDialog()
        dialog._field.setText("section=UB300 z=0:10")

        assert dialog.selection() is not None
        assert _ok_button(dialog).isEnabled() is True
        assert dialog._error.text() == ""

    def test_an_invalid_expression_reports_and_disables_ok(self, qapp):
        from fea_toolkit.gui.views.selection_dialog import SelectionDialog

        dialog = SelectionDialog()
        dialog._field.setText("wibble=1")

        assert dialog.selection() is None
        assert _ok_button(dialog).isEnabled() is False
        assert "wibble" in dialog._error.text()

    def test_it_starts_from_the_current_selection(self, qapp):
        from fea_toolkit.gui.views.selection_dialog import SelectionDialog
        from fea_toolkit.model.selection import Selection

        dialog = SelectionDialog(Selection(sections=["UB300"]))

        assert dialog._field.text() == "section=UB300"

    def test_an_empty_field_is_an_unfiltered_selection(self, qapp):
        from fea_toolkit.gui.views.selection_dialog import SelectionDialog
        from fea_toolkit.model.selection import Selection

        dialog = SelectionDialog(Selection(sections=["UB300"]))
        dialog._field.setText("")

        assert dialog.selection() == Selection()


class TestDuplicateView:
    """**Edit ▸ Duplicate view** adds a lens on the active view."""

    def test_duplicating_registers_a_derived_view_and_renders_it(self, window, monkeypatch):
        from fea_toolkit.model.selection import Selection

        selection = Selection(element_types=["Node"], node_ids=["2"])
        _stub_dialog(monkeypatch, selection)

        window._on_duplicate_view()

        derived = window._views.active
        assert derived.parent == "unprocessed"
        assert derived.selection is selection
        assert len(window._viewer.geometry()[0]) == 2  # members 1 and 2, never 3
        assert "Added view:" in window._message_log.toPlainText()

    def test_cancelling_adds_nothing(self, window, monkeypatch):
        _stub_dialog(monkeypatch, None)
        before = len(window._views)

        window._on_duplicate_view()

        assert len(window._views) == before

    def test_the_derived_view_appears_in_the_tree(self, window, monkeypatch):
        from fea_toolkit.model.selection import Selection

        _stub_dialog(monkeypatch, Selection(sections=["UB300"]))

        window._on_duplicate_view()

        assert window._tree_model.index_for("views", window._views.active.name) is not None

    def test_the_inspector_reports_the_derived_view(self, window, monkeypatch):
        from fea_toolkit.model.selection import Selection

        _stub_dialog(monkeypatch, Selection(element_types=["Node"], node_ids=["2"]))

        window._on_duplicate_view()

        assert "joint" not in window._inspector._title.text()  # the name is the label
        rows = {
            window._inspector._table.item(row, 0).text(): window._inspector._table.item(
                row, 1
            ).text()
            for row in range(window._inspector._table.rowCount())
        }
        assert rows["parent"] == "unprocessed"
        assert rows["n_frames"] == "2"
        # The expression itself is inspectable, so a view explains its own filter.
        assert rows["selection"].startswith("Selection(")
        assert "element_ids=['2']" in rows["selection"]

    def test_picking_follows_the_filtered_scene(self, window, monkeypatch):
        """The pick index is rebuilt from what is drawn, so hidden cells are not pickable."""
        from fea_toolkit.model.selection import Selection

        _stub_dialog(monkeypatch, Selection(element_types=["Node"], node_ids=["2"]))

        window._on_duplicate_view()

        index = window._selection_index
        assert len(index.frames) == 2  # members 1 and 2
        assert index.label("frames", 1) is not None
        assert index.label("frames", 2) is None  # nothing is drawn as cell 2


class TestEditViewSelection:
    """**Edit ▸ Edit view selection…** re-filters the derived view in place."""

    def test_editing_replaces_the_filter_of_the_same_view(self, window, monkeypatch):
        from fea_toolkit.model.selection import Selection

        _stub_dialog(monkeypatch, Selection(sections=["UB300"]))
        window._on_duplicate_view()
        key = window._views.active.key

        newer = Selection(element_types=["Node"], node_ids=["1"])
        _stub_dialog(monkeypatch, newer)

        window._on_edit_view_selection()

        assert window._views.active.key == key  # the same view, a new lens
        assert window._views.active.selection is newer
        assert len(window._viewer.geometry()[0]) == 1  # just member 1

    def test_it_is_only_offered_for_a_derived_view(self, window):
        assert window._actions["edit.duplicate_view"].isEnabled() is True
        assert window._actions["edit.edit_selection"].isEnabled() is False
