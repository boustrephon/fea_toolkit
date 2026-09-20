"""Tests for the JSON combination-set codec (``io.combination_set``).

Round-trips a hand-authored definition set through a file and back, so the two
combination tools — NPZ expansion (``analysis.combinations``) and force-diagram
rendering (``plotting.force_diagram``) — can share one on-disk definition.
"""

import json

import numpy as np
import pytest

from fea_toolkit.io.combination_set import read_combination_set, write_combination_set
from fea_toolkit.model.load_combinations import combination_set_from_dict
from fea_toolkit.model.sap_data import LoadCase


def _sample() -> dict:
    """One Linear Add with a declared magnitude, one Envelope, one shorthand."""
    return {
        "SEISM": {
            "type": "Linear Add",
            "entries": [
                {"ref": "DEAD", "factor": 1.3},
                {"ref": "RSX", "factor": 1.4, "magnitude": True},
            ],
        },
        "ENV": {"type": "Envelope", "entries": [["SEISM", 1.0], ["DEAD", 1.0]]},
        "GRAV": [["DEAD", 1.2]],
    }


def test_json_round_trip_preserves_definitions(tmp_path):
    path = tmp_path / "combos.json"
    written = write_combination_set(path, combination_set_from_dict(_sample()))
    assert written == str(path.resolve())

    combos = read_combination_set(path)
    assert set(combos) == {"SEISM", "ENV", "GRAV"}
    assert combos["SEISM"].combo_type == "Linear Add"
    assert combos["ENV"].combo_type == "Envelope"
    assert combos["GRAV"].combo_type == "Linear Add"
    entry = combos["SEISM"].entries[1]
    assert (entry.name, entry.factor, entry.magnitude) == ("RSX", 1.4, True)


def test_written_file_is_plain_sorted_json(tmp_path):
    path = tmp_path / "combos.json"
    write_combination_set(path, combination_set_from_dict({"B": [["X", 1.0]], "A": [["Y", 1.0]]}))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert list(data) == ["A", "B"]
    assert data["A"] == {"type": "Linear Add", "entries": [{"ref": "Y", "factor": 1.0}]}


def test_read_rejects_a_non_object(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        read_combination_set(path)


def test_definition_drives_expansion_and_rendering_grouping(tmp_path):
    """One file feeds both tools: expansion metadata and the renderer's grouping."""
    from fea_toolkit.analysis.combinations import build_combination_results
    from fea_toolkit.plotting.force_diagram import _case_pairs

    path = tmp_path / "combos.json"
    write_combination_set(path, combination_set_from_dict(_sample()))
    definitions = read_combination_set(path)

    cases = {
        "DEAD": LoadCase("DEAD", "LinStatic", "Prog Det", "Dead", "Prog Det", "Non-Composite"),
    }
    results = {
        "DEAD": {"element_forces": {"fx_i": [10.0]}},
        "RSX": {"element_forces": {"fx_i": [5.0]}},
    }
    out, meta = build_combination_results(
        results,
        load_cases=cases,
        definitions=definitions,
        combinations=["SEISM"],
        return_meta=True,
    )
    assert set(out) == {"SEISM #1", "SEISM #2"}
    assert meta["SEISM #1"]["coords"] == "+RSX"
    assert out["SEISM #1"]["element_forces"]["fx_i"] == pytest.approx([20.0])

    # The renderer resolves the same grouping from the definition alone — the
    # archive needs no ``static_case_*`` arrays at all.
    archive = {"static_case_labels": np.array(["SEISM #1", "SEISM #2"])}
    assert _case_pairs(archive, definitions) == {
        "SEISM #1": ("SEISM", 1),
        "SEISM #2": ("SEISM", -1),
    }
