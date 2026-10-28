"""Tests for the transient (time-history) block in the unified NPZ schema (P43).

The ``transient/<case>/...`` block gives the in-process Newmark result a
first-class home in the canonical exchange format: ``time`` / ``base_shear``
(``N_t``), the recorded-node subset with dense ``node_disp_*`` / ``node_vel_*``
/ ``node_accel_*`` (``N_t × N_rec``), the peak reductions and the Rayleigh
periods.  These tests cover the collector, the schema validator, the reader's
``describe_results_npz`` summary and both writers' ``analysis_types`` stamping.
"""

import numpy as np
import pytest

from examples.sample_model import make_sample_model
from fea_toolkit.io.npz_reader import describe_results_npz, read_results
from fea_toolkit.io.npz_writer import collect_transient_arrays, write_results_npz
from fea_toolkit.io.results_schema import (
    SCHEMA_VERSION,
    make_transient_key,
    transient_case_names,
    validate_arrays,
    validate_npz,
)
from fea_toolkit.io.unified_writer import write_results


def _synthetic_transient(node_tags, n_steps=6, with_kinematics=True):
    """Build a synthetic runner result keyed on the given node tags."""
    n = n_steps
    t = np.arange(n, dtype=float) * 0.01
    result = {
        "times": t,
        "displacements": {
            tag: np.column_stack(
                [
                    np.linspace(0.0, 0.05, n) + i * 1e-3,
                    np.zeros(n),
                    np.zeros(n),
                ]
            )
            for i, tag in enumerate(sorted(node_tags))
        },
        "base_shear": np.linspace(0.0, 40.0, n),
        "direction": "X",
        "dt": 0.01,
        "gm_file": "record.csv",
        "peak_displacement": 0.051,
        "peak_base_shear": 40.0,
        "peak_plastic_rotation": 0.002,
        "max_dissipated_energy": 12.0,
        "num_yielded_hinges": 2,
        "converged_steps": n,
        "period_1": 1.0,
        "period_2": 0.3,
    }
    if with_kinematics:
        result["velocities"] = {tag: np.full((n, 3), 0.1) for tag in sorted(node_tags)}
        result["accelerations"] = {tag: np.full((n, 3), 1.0) for tag in sorted(node_tags)}
    return result


def test_collect_transient_arrays_block_shapes():
    """The collector emits dense ``N_t × N_rec`` node kinematics + scalars."""
    res = _synthetic_transient([11, 22, 33], n_steps=6)
    arrays = collect_transient_arrays({"ELC": res})

    assert arrays[make_transient_key("ELC", "transient/{case}/time")].shape == (6,)
    assert arrays[make_transient_key("ELC", "transient/{case}/base_shear")].shape == (6,)
    assert list(arrays[make_transient_key("ELC", "transient/{case}/node_tag")]) == [11, 22, 33]
    for comp in ("x", "y", "z"):
        for prefix in ("node_disp", "node_vel", "node_accel"):
            key = make_transient_key("ELC", f"transient/{{case}}/{prefix}_{comp}")
            assert arrays[key].shape == (6, 3), key
    # Peak reductions round-trip as 0-d arrays.
    assert arrays[make_transient_key("ELC", "transient/{case}/peak_base_shear")].shape == ()
    assert (
        arrays[make_transient_key("ELC", "transient/{case}/num_yielded_hinges")].dtype.kind == "i"
    )
    assert transient_case_names(arrays) == ["ELC"]


def test_collect_transient_arrays_omits_unrecorded_kinematics():
    """A run without velocities/accelerations emits only the displacement block."""
    res = _synthetic_transient([11, 22], n_steps=4, with_kinematics=False)
    arrays = collect_transient_arrays({"BARE": res})

    assert make_transient_key("BARE", "transient/{case}/node_disp_x") in arrays
    assert make_transient_key("BARE", "transient/{case}/node_vel_x") not in arrays
    assert make_transient_key("BARE", "transient/{case}/node_accel_x") not in arrays


def test_transient_archive_validates_and_describes(tmp_path):
    """``write_results_npz`` transient archives satisfy the v4 validator."""
    md = make_sample_model()
    tags = sorted(nd.node_tag for nd in md.nodes.values())
    p = str(tmp_path / "transient.npz")
    write_results_npz(
        p,
        md,
        transient_results={"ELC": _synthetic_transient(tags, n_steps=6)},
    )

    assert validate_npz(p) == []
    summary = describe_results_npz(p)
    assert "transient" in summary["analysis_types"]
    assert summary["transient_cases"] == ["ELC"]

    data = read_results(p)
    assert list(data["schema_version"]) == [SCHEMA_VERSION]
    assert make_transient_key("ELC", "transient/{case}/direction") in data


def test_unified_writer_emits_transient_block(tmp_path):
    """``write_results`` lists ``"transient"`` in ``analysis_types``."""
    md = make_sample_model()
    tags = sorted(nd.node_tag for nd in md.nodes.values())
    p = write_results(
        str(tmp_path / "unified_transient.npz"),
        model=md,
        transient_results={"ELC": _synthetic_transient(tags, n_steps=6)},
    )

    assert validate_npz(p) == []
    assert "transient" in describe_results_npz(p)["analysis_types"]
    data = read_results(p)
    assert list(data["schema_version"]) == [SCHEMA_VERSION]


def test_transient_block_is_optional_for_geometry_only_archive(tmp_path):
    """A geometry-only archive carries no transient block and still validates."""
    md = make_sample_model()
    p = str(tmp_path / "geometry_only.npz")
    write_results_npz(p, md)

    assert validate_npz(p) == []
    assert describe_results_npz(p)["transient_cases"] == []


def _transient_archive(case="ELC"):
    """A fully-populated transient archive dict (collector output + analysis_types)."""
    res = _synthetic_transient([11, 22], n_steps=4)
    arrays = collect_transient_arrays({case: res})
    arrays["analysis_types"] = np.array(["transient"])
    return arrays


def test_collect_transient_arrays_rejects_slash_in_case_label():
    """A case label containing ``/`` would create a nested NPZ key."""
    res = _synthetic_transient([11], n_steps=3)
    with pytest.raises(ValueError, match="must not contain"):
        collect_transient_arrays({"a/b": res})


def test_collect_transient_arrays_requires_base_shear():
    """A transient run without a ``base_shear`` history is rejected before writing."""
    res = _synthetic_transient([11], n_steps=3)
    del res["base_shear"]
    with pytest.raises(ValueError, match="base_shear"):
        collect_transient_arrays({"ELC": res})


def test_transient_case_names_discovers_cases_without_time():
    """Cases are discovered from any transient key, not only the ``time`` anchor."""
    arrays = {make_transient_key("BARE", "transient/{case}/base_shear"): np.zeros(3)}
    assert transient_case_names(arrays) == ["BARE"]


def test_validate_arrays_requires_node_disp_when_node_tag_present():
    """All three displacement grids are required whenever ``node_tag`` is present."""
    arrays = _transient_archive()
    arrays.pop(make_transient_key("ELC", "transient/{case}/node_disp_y"))
    assert any("node_disp_y" in m for m in validate_arrays(arrays))


def test_validate_arrays_requires_complete_vel_accel_groups():
    """Velocity / acceleration groups must be complete when any component is present."""
    arrays = _transient_archive()
    arrays.pop(make_transient_key("ELC", "transient/{case}/node_vel_y"))
    assert any("node_vel_y" in m for m in validate_arrays(arrays))


def test_validate_arrays_flags_transient_without_case_block():
    """Declaring ``"transient"`` without any transient case block is an error."""
    messages = validate_arrays({"analysis_types": np.array(["transient"])})
    assert any("no transient case block" in m for m in messages)
