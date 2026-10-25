"""Unit tests for the SAC A4 matrix runner's pure helpers (``sac_run``).

These exercise :func:`compute_metrics`, :func:`floor_levels` and
:func:`write_manifest` on synthetic data — no OpenSees and no ground-motion
mirror — so they run on any interpreter.
"""

import json

import numpy as np
import pytest

from fea_toolkit.benchmarks.sac_run import compute_metrics, floor_levels, write_manifest


def _synthetic_result(n: int = 5) -> dict:
    """A transient result with two levels (base z=0, roof z=4)."""
    base = np.zeros((n, 3))
    roof_x = np.array([0.0, 0.04, -0.04, 0.02, 0.0])
    roof = np.column_stack([roof_x, np.zeros(n), np.zeros(n)])
    rel = np.column_stack([np.full(n, 3.0), np.zeros(n), np.zeros(n)])
    return {
        "converged_steps": n,
        "displacements": {10: base, 20: roof},
        "accelerations": {10: np.zeros((n, 3)), 20: rel},
        "peak_base_shear": 500.0,
        "num_yielded_hinges": 3,
        "peak_plastic_rotation": 0.01,
        "max_dissipated_energy": 100.0,
        "period_1": 1.0,
        "period_2": 0.3,
    }


def test_compute_metrics_extracts_benchmark_quantities():
    levels = [(0.0, 10), (4.0, 20)]
    ground = np.array([0.0, 1.0, 1.0, 1.0, 1.0, 1.0])  # ag = ground[1:] = 1.0
    m = compute_metrics(_synthetic_result(), ground, levels)

    assert m["roof_disp"] == pytest.approx(0.04)
    assert m["d_max"] == pytest.approx(0.01)  # |u_roof - u_base| / 4.0
    assert m["xdd_max"] == pytest.approx(4.0)  # |a_rel + ag| = 3 + 1
    assert m["F_max"] == pytest.approx(1000.0)  # 2 × per-frame base shear
    assert m["F_max_frame"] == pytest.approx(500.0)
    assert m["N_d"] == 3


def test_compute_metrics_handles_early_break():
    """A run that broke early only uses the converged prefix."""
    res = _synthetic_result(n=5)
    res["converged_steps"] = 2
    ground = np.array([0.0, 1.0, 1.0, 1.0, 1.0, 1.0])
    m = compute_metrics(res, ground, [(0.0, 10), (4.0, 20)])
    assert m["roof_disp"] == pytest.approx(0.04)  # |u_roof[0..1]| max = 0.04


class _Node:
    def __init__(self, tag: int, z: float):
        self.node_tag = tag
        self.z = z


class _Builder:
    def __init__(self, nodes):
        self.mesh_model = type("_Mesh", (), {"nodes": nodes})()


def test_floor_levels_picks_highest_tag_per_elevation():
    nodes = {
        1: _Node(1, 0.0),
        2: _Node(2, 4.0),
        3: _Node(3, 0.0),  # same elevation as 1 → representative is the higher tag
        4: _Node(4, 8.0),
    }
    levels = floor_levels(_Builder(nodes))
    assert levels == [(0.0, 3), (4.0, 2), (8.0, 4)]


def test_write_manifest_roundtrips_and_omits_cache_keys(tmp_path):
    rows = [
        {
            "_building": 3,
            "_record": "ELCENTRO",
            "_intensity": 1.0,
            "_hinge_model": "beam_with_hinges",
            "_backbone": "trilinear",
            "_converged": True,
            "_fingerprint": "deadbeef",
            "roof_disp": 0.149,
            "d_max": 0.0148,
            "xdd_max": 6.95,
            "F_max_frame": 5.14e6,
            "F_max": 1.028e7,
            "N_d": 22,
            "converged_steps": 10006,
            "num_steps": 10006,
        }
    ]
    path = write_manifest(rows, str(tmp_path / "m.json"))
    payload = json.loads(path.read_text())
    assert payload[0]["building"] == 3
    assert payload[0]["converged"] is True
    assert payload[0]["F_max"] == pytest.approx(1.028e7)
    assert "_fingerprint" not in payload[0]
