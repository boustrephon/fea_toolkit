"""Tests for the unified Rhino results application (``rhino/results.py``).

Covers the pure NumPy data-extraction helpers (no Rhino import needed)
and the ``io.stage_reader.flatten_stage`` promotion that lets a single
stage file feed both the model-review and result-colouring paths.
"""

import numpy as np
import pytest

from fea_toolkit.io import flatten_stage
from fea_toolkit.rhino.colour_from_npz import _load_npz_quantities, _load_unified
from fea_toolkit.rhino.results import (
    _get_pushover_directions,
    _load_deformed_arrays,
    _load_pushover_shell_quantities,
)


@pytest.fixture
def flat_results():
    """A minimal unified (already stage-flattened) results dict."""
    return {
        "static_case_labels": np.array(["DEAD"]),
        "node_x": np.array([0.0, 1.0, 1.0, 0.0]),
        "node_y": np.zeros(4),
        "node_z": np.array([0.0, 0.0, 1.0, 1.0]),
        "frame_sap_id": np.array(["B1-0", "B1-1"]),
        "frame_parent_sap_id": np.array(["B1", "B1"]),
        "frame_node_i": np.array([0, 1]),
        "frame_node_j": np.array([1, 2]),
        "shell_sap_id": np.array(["S1-0", "S1-1"]),
        "shell_parent_sap_id": np.array(["S1", "S1"]),
        "shell_node_1": np.array([0, 1]),
        "shell_node_2": np.array([1, 2]),
        "shell_node_3": np.array([2, 3]),
        "shell_node_4": np.array([3, 0]),
        "static/DEAD/node_dx": np.array([0.0, 0.1, 0.2, 0.1]),
        "static/DEAD/node_dy": np.zeros(4),
        "static/DEAD/node_dz": np.zeros(4),
        "static/DEAD/mz_i": np.array([1.0, -2.0]),
        "static/DEAD/mz_i_local": np.array([1.5, -2.5]),
        "static/DEAD/mz_j": np.array([0.5, -1.0]),
        "pushover/+X/shell_sap_id": np.array(["S1-0", "S1-1"]),
        "pushover/+X/shell_Nx": np.array([[10.0, -20.0], [12.0, -25.0]]),
        "pushover/+X/node_disp_x": np.array([[0.0, 0.5, 1.0, 0.5]]),
        "pushover/+X/node_disp_y": np.zeros((1, 4)),
        "pushover/+X/node_disp_z": np.zeros((1, 4)),
    }


# ── flatten_stage / _load_unified ────────────────────────────────────────


class TestFlattenStage:
    def test_promotes_mesh_by_default(self):
        data = {
            "schema_version": np.array([2]),
            "node_x": np.array([9.0]),
            "stage/mesh/node_x": np.array([1.0, 2.0]),
            "stage/sap/node_x": np.array([5.0]),
        }
        flat = flatten_stage(data)
        assert list(flat["node_x"]) == [1.0, 2.0]
        assert "schema_version" in flat  # base keys retained
        assert "stage/mesh/node_x" not in flat

    def test_explicit_stage(self):
        data = {
            "node_x": np.array([9.0]),
            "stage/mesh/node_x": np.array([1.0, 2.0]),
            "stage/sap/node_x": np.array([5.0]),
        }
        flat = flatten_stage(data, stage="sap")
        assert list(flat["node_x"]) == [5.0]

    def test_missing_stage_raises(self):
        with pytest.raises(ValueError, match="not present"):
            flatten_stage({"node_x": np.array([1.0])})

    def test_legacy_dict_passthrough(self, flat_results):
        """_load_unified must leave legacy (non-stage) dicts untouched."""
        assert _load_unified(flat_results) is flat_results

    def test_stage_dict_promoted(self):
        data = {
            "schema_version": np.array([2]),
            "stage/mesh/frame_sap_id": np.array(["B1-0"]),
        }
        out = _load_unified(data)
        assert list(out["frame_sap_id"]) == ["B1-0"]


# ── Frame quantity loading ───────────────────────────────────────────────


class TestFrameQuantities:
    def test_local_quantities(self, flat_results):
        values, (vmin, vmax), _ = _load_npz_quantities(
            flat_results, "Mz", use_local=True, case="DEAD"
        )
        assert values == {"B1-0": 1.5, "B1-1": -2.5}
        assert vmin == -2.5
        assert vmax == 1.5

    def test_global_fallback(self, flat_results):
        values, _, _ = _load_npz_quantities(flat_results, "Mz", use_local=False, case="DEAD")
        assert values == {"B1-0": 1.0, "B1-1": -2.0}

    def test_aggregate_parents(self, flat_results):
        values, (vmin, _), _ = _load_npz_quantities(
            flat_results, "Mz", use_local=True, case="DEAD", aggregate_parents=True
        )
        # Parent takes the max-abs of its children; children retained.
        assert values["B1"] == -2.5
        assert values["B1-0"] == 1.5
        assert values["B1-1"] == -2.5
        assert vmin == -2.5

    def test_unknown_case_raises(self, flat_results):
        with pytest.raises(ValueError, match="not found"):
            _load_npz_quantities(flat_results, "Mz", case="NOPE")


# ── Pushover shell quantities ────────────────────────────────────────────


class TestPushoverShellQuantities:
    def test_last_step_default(self, flat_results):
        values, (vmin, vmax) = _load_pushover_shell_quantities(flat_results, "Nx", direction="+X")
        assert values == {"S1-0": 12.0, "S1-1": -25.0}
        assert vmin == -25.0
        assert vmax == 12.0

    def test_explicit_step(self, flat_results):
        values, _ = _load_pushover_shell_quantities(flat_results, "Nx", direction="+X", step=0)
        assert values == {"S1-0": 10.0, "S1-1": -20.0}

    def test_aggregate_parents(self, flat_results):
        values, _ = _load_pushover_shell_quantities(
            flat_results, "Nx", direction="+X", aggregate_parents=True
        )
        assert values["S1"] == -25.0

    def test_missing_quantity_is_empty(self, flat_results):
        values, _ = _load_pushover_shell_quantities(flat_results, "My", direction="+X")
        assert values == {}

    def test_first_direction(self, flat_results):
        values, _ = _load_pushover_shell_quantities(flat_results, "Nx")
        assert values  # direction auto-resolved to "+X"

    def test_directions(self, flat_results):
        assert _get_pushover_directions(flat_results) == ["+X"]


# ── Deformed-shape displacement loading ──────────────────────────────────


class TestDeformedArrays:
    def test_static(self, flat_results):
        dx, dy, dz, label = _load_deformed_arrays(flat_results, "static", case="DEAD")
        assert label == "static/DEAD"
        assert list(dx) == [0.0, 0.1, 0.2, 0.1]
        assert list(dy) == [0.0] * 4
        assert list(dz) == [0.0] * 4

    def test_static_first_case(self, flat_results):
        _, _, _, label = _load_deformed_arrays(flat_results, "static")
        assert label == "static/DEAD"

    def test_modal_mode_clamped(self):
        data = {
            "modal/mode_dx": np.zeros((4, 2)),
            "modal/mode_dy": np.zeros((4, 2)),
            "modal/mode_dz": np.ones((4, 2)),
        }
        _dx, _dy, dz, label = _load_deformed_arrays(data, "modal", mode=5)
        assert label == "modal/2"  # clamped to last mode
        assert list(dz) == [1.0] * 4

    def test_pushover_last_step(self, flat_results):
        _, _, _, label = _load_deformed_arrays(flat_results, "pushover", direction="+X")
        assert label == "pushover/+X/step0"

    def test_rs_and_modal_missing(self, flat_results):
        assert _load_deformed_arrays(flat_results, "rs") is None
        assert _load_deformed_arrays(flat_results, "modal") is None

    def test_unknown_source_raises(self, flat_results):
        with pytest.raises(ValueError, match="Unknown deformed source_type"):
            _load_deformed_arrays(flat_results, "spectral")


# ── Real stage-file integration ──────────────────────────────────────────


@pytest.mark.parametrize("fmt", ["npz", "h5"])
def test_flatten_stage_real_file(tmp_path, fmt):
    """A real stage file flattens to unprefixed geometry for colouring."""
    from examples.sample_model import make_sample_model
    from fea_toolkit.io.stage_writer import write_model_stages

    md = make_sample_model()
    path = str(tmp_path / f"stage.{fmt}")
    write_model_stages(path, sap=md, fmt=fmt)

    flat = flatten_stage(path, stage="sap")
    assert "node_x" in flat
    assert "frame_sap_id" in flat
    assert "stage/sap/node_x" not in flat

    # The colouring loader also reads stage files in both formats.
    data = _load_unified(path, stage="sap")
    assert "frame_sap_id" in data
