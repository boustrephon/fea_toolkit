"""Qt tests for the external load-combination editor widget.

Gated by ``needs_gui`` — the editor is Qt.  It is self-contained (no model or
file reference), so it is driven directly here; the modal ``QInputDialog``
prompts behind Add / Rename are not exercised.
"""

import pytest

from fea_toolkit.model.sap_data import LoadCombination, LoadCombinationEntry

pytestmark = pytest.mark.needs_gui


def _combo(name, *entries):
    return LoadCombination(
        name=name,
        combo_type="Linear Add",
        entries=[LoadCombinationEntry(name=ref, factor=factor) for ref, factor in entries],
    )


def test_combination_editor_round_trips_a_seed(qapp):
    from fea_toolkit.gui.views.combination_editor import CombinationEditor

    editor = CombinationEditor(
        {
            "ULT": _combo("ULT", ("DEAD", 1.3), ("WIND", 1.4)),
            "SERVICE": _combo("SERVICE", ("DEAD", 1.0)),
        }
    )
    out = editor.value()
    assert set(out) == {"ULT", "SERVICE"}
    assert [(e.name, e.factor) for e in out["ULT"].entries] == [("DEAD", 1.3), ("WIND", 1.4)]


def test_combination_editor_adds_and_removes_entries(qapp):
    from fea_toolkit.gui.views.combination_editor import CombinationEditor

    editor = CombinationEditor({"ULT": _combo("ULT", ("DEAD", 1.0))})
    editor._on_add_entry()
    # The new (blank) entry sits at table row 1.
    editor._table.item(1, 0).setText("WIND")
    editor._table.cellWidget(1, 1).setValue(1.6)
    entries = editor.value()["ULT"].entries
    assert [(e.name, e.factor) for e in entries] == [("DEAD", 1.0), ("WIND", 1.6)]

    editor._table.setCurrentCell(1, 0)
    editor._on_remove_entry()
    assert [(e.name, e.factor) for e in editor.value()["ULT"].entries] == [("DEAD", 1.0)]


def test_combination_editor_deletes_a_combination(qapp):
    from fea_toolkit.gui.views.combination_editor import CombinationEditor

    editor = CombinationEditor({"A": _combo("A"), "B": _combo("B")})
    editor._list.setCurrentRow(1)  # select "B"
    editor._on_delete()
    assert set(editor.value()) == {"A"}


def test_combination_editor_an_empty_table_flushes_no_entries(qapp):
    from fea_toolkit.gui.views.combination_editor import CombinationEditor

    editor = CombinationEditor({"A": _combo("A", ("DEAD", 1.0))})
    editor._save_table()
    editor._table.setRowCount(0)
    assert editor.value()["A"].entries == []
