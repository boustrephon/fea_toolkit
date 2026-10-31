"""Tests for the IDA (incremental dynamic analysis) block in the unified NPZ schema.

The ``ida/<record>/...`` block gives the IK-2005 generic-frame collapse run a
first-class home in the canonical exchange format: per-record ``scale`` /
``drift`` / ``converged`` ladders, the collapse scalars
(``collapse_scale`` / ``collapse_criterion``), the ``Sa_T1_g`` / ``source``
provenance, and the ``ida/record_id`` + ``ida/collapse_Sa_T1_g`` summaries
plus ``ida/median_collapse_Sa_T1_gamma`` / ``ida/sigma_ln``.  These tests cover
the collector, the schema validator, the reader's summary and the writer's
``analysis_types`` stamping.
"""

import numpy as np

from examples.sample_model import make_sample_model
from fea_toolkit.io.npz_reader import describe_results_npz, read_results_npz
from fea_toolkit.io.npz_writer import collect_ida_arrays, write_results_npz
from fea_toolkit.io.results_schema import (
    SCHEMA_VERSION,
    ida_record_names,
    make_ida_key,
    validate_arrays,
    validate_npz,
)


def _synthetic_ida_result() -> dict:
    """A ``collapse_ida_curves``-shaped result dict with two records."""
    g = 9.80665
    return {
        "code": "0909",
        "T1": 0.9,
        "gamma": 1.0,
        "g": g,
        "ladder": [0.5, 1.0, 2.0, 4.0],
        "max_scale": 4.0,
        "tolerance": 0.01,
        "collapse_drift": 0.10,
        "records": ["IV79cal", "IV79chi"],
        "records_detail": {
            "IV79cal": {
                "Sa_T1": 3.2,
                "Sa_T1_g": 3.2 / g,
                "source": "USGS",
                "ida": {
                    "intensities": [0.5, 1.0, 2.0, 4.0],
                    "drifts": [0.01, 0.03, 0.07, 0.15],
                    "converged": [True, True, True, False],
                    "collapse_intensity": 3.0,
                    "collapse_criterion": "nonconvergence",
                },
            },
            "IV79chi": {
                "Sa_T1": 4.0,
                "Sa_T1_g": 4.0 / g,
                "source": "CESMD",
                "ida": {
                    "intensities": [0.5, 1.0, 2.0, 4.0],
                    "drifts": [0.02, 0.05, 0.11, 0.20],
                    "converged": [True, True, True, True],
                    "collapse_intensity": 2.0,
                    "collapse_criterion": "drift",
                },
            },
        },
        "capacities": {"IV79cal": 3.0 * 3.2, "IV79chi": 2.0 * 4.0},
        "collapse_Sa_T1_g": {"IV79cal": 3.0 * 3.2 / g, "IV79chi": 2.0 * 4.0 / g},
        "median_collapse_sa": 8.8,
        "median_collapse_sa_gamma": 8.8 / g,
        "sigma_ln": 0.4,
    }


def test_collect_ida_arrays_block_shapes():
    """The collector emits per-record ladders and the summary arrays."""
    arrays = collect_ida_arrays(_synthetic_ida_result())

    assert list(arrays["ida/record_id"]) == ["IV79cal", "IV79chi"]
    assert arrays["ida/collapse_Sa_T1_g"].shape == (2,)
    assert arrays["ida/collapse_Sa_T1_gamma"].shape == (2,)
    assert arrays[make_ida_key("IV79cal", "ida/{record}/scale")].shape == (4,)
    assert arrays[make_ida_key("IV79cal", "ida/{record}/drift")].shape == (4,)
    assert arrays[make_ida_key("IV79cal", "ida/{record}/converged")].dtype.kind == "b"
    assert arrays[make_ida_key("IV79cal", "ida/{record}/collapse_scale")].shape == ()
    assert (
        str(arrays[make_ida_key("IV79cal", "ida/{record}/collapse_criterion")]) == "nonconvergence"
    )
    assert str(arrays[make_ida_key("IV79cal", "ida/{record}/source")]) == "USGS"
    assert arrays["ida/median_collapse_Sa_T1_gamma"].shape == ()
    assert arrays["ida/sigma_ln"].shape == ()
    assert ida_record_names(arrays) == ["IV79cal", "IV79chi"]


def test_collect_ida_arrays_output_has_no_ida_errors():
    """The ida block validates cleanly (geometry is absent by design)."""
    arrays = collect_ida_arrays(_synthetic_ida_result())
    arrays["analysis_types"] = np.array(["ida"])
    messages = validate_arrays(arrays)
    assert not any("ida" in m for m in messages)


def test_write_results_npz_ida_round_trips(tmp_path):
    """An IDA archive validates and describes after a write/read round-trip."""
    md = make_sample_model()
    path = write_results_npz(str(tmp_path / "ida.npz"), md, ida_results=_synthetic_ida_result())
    assert validate_npz(path) == []

    data = read_results_npz(path)
    assert "ida" in [str(v) for v in data["analysis_types"].tolist()]
    assert list(data["schema_version"]) == [SCHEMA_VERSION]
    assert list(data["ida/record_id"]) == ["IV79cal", "IV79chi"]

    summary = describe_results_npz(path)
    assert summary["ida_records"] == ["IV79cal", "IV79chi"]


def test_validate_arrays_ida_without_record_block():
    """Declaring ``ida`` without any record block is an error."""
    messages = validate_arrays({"analysis_types": np.array(["ida"])})
    assert any("no ida record block" in m for m in messages)


def test_validate_arrays_ida_missing_ladder():
    """A record missing its ``scale`` ladder is reported."""
    arrays = collect_ida_arrays(_synthetic_ida_result())
    arrays["analysis_types"] = np.array(["ida"])
    arrays.pop(make_ida_key("IV79chi", "ida/{record}/scale"))
    assert any("ida/IV79chi/scale" in m for m in validate_arrays(arrays))
