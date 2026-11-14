"""The generic incremental-dynamic-analysis driver (``analysis/ida.py``)."""

import numpy as np
import pytest

from examples.sample_model import make_sample_model
from fea_toolkit.analysis.base import AnalysisResult
from fea_toolkit.analysis.ida import collapse_criterion, collapse_intensity, run_ida


def _modal() -> AnalysisResult:
    return AnalysisResult(
        name="Modal",
        analysis_type="ModalAnalysis",
        data={"modal": {"periods": [1.0, 0.5]}},
    )


def _fake_runner(threshold: float = 1.8):
    """A ``run_nonlinear_dynamic_analysis`` stub.

    Peak drift scales with the record intensity (``0.02 × im``); a rung above
    *threshold* fails to converge (``converged_steps = num_steps - 1``).  The
    displacement history ramps the X drift over the record so the driver's roof
    drift measure recovers exactly ``0.02 × im``.
    """

    def fake(mesh_model, modal_result, ground_motion_file, **kwargs):
        accel = np.asarray(kwargs["accel"], dtype=float)
        im = float(np.max(accel))
        num_steps = kwargs["num_steps"]
        n_steps = num_steps if im < threshold else num_steps - 1
        drift = 0.02 * im
        disp = np.zeros((n_steps, 3))
        disp[:, 0] = drift * 10.0 * np.linspace(0.0, 1.0, n_steps)
        return AnalysisResult(
            name="NLD",
            analysis_type="NonlinearDynamicAnalysis",
            data={"displacements": {1: disp}, "converged_steps": n_steps, "direction": "X"},
        )

    return fake


def test_collapse_intensity_and_criterion():
    """Non-convergence wins before drift, in ladder order."""
    assert collapse_intensity([1.0, 2.0, 3.0], [0.01, 0.02, 0.03], [True, True, True], 0.10) is None
    assert collapse_intensity([1.0, 2.0, 3.0], [0.01, 0.02, 0.03], [True, False, True], 0.10) == 2.0
    assert collapse_intensity([1.0, 2.0, 3.0], [0.01, 0.12, 0.15], [True, True, True], 0.10) == 2.0
    assert (
        collapse_criterion([1.0, 2.0, 3.0], [0.01, 0.02, 0.03], [True, False, True], 0.10)
        == "nonconvergence"
    )
    assert (
        collapse_criterion([1.0, 2.0, 3.0], [0.01, 0.12, 0.15], [True, True, True], 0.10) == "drift"
    )


def test_run_ida_nonconvergence_collapse(monkeypatch):
    """A rung that fails to converge is the collapse intensity."""
    import fea_toolkit.analysis.nonlinear_dynamic as nld_module

    monkeypatch.setattr(nld_module, "run_nonlinear_dynamic_analysis", _fake_runner(threshold=1.8))
    result = run_ida(
        make_sample_model(),
        _modal(),
        "record.txt",
        [0.5, 1.0, 1.5, 2.0],
        accel_base=np.ones(20),
        num_steps=10,
        tolerance=None,
    )
    assert result["intensities"] == [0.5, 1.0, 1.5, 2.0]
    assert result["converged"] == [True, True, True, False]
    assert result["collapse_intensity"] == 2.0
    assert result["collapse_criterion"] == "nonconvergence"
    assert result["bisection"] is None
    assert result["drifts"] == pytest.approx([0.01, 0.02, 0.03, 0.04])


def test_run_ida_drift_collapse(monkeypatch):
    """A converged rung whose drift exceeds the threshold collapses by drift."""
    import fea_toolkit.analysis.nonlinear_dynamic as nld_module

    monkeypatch.setattr(nld_module, "run_nonlinear_dynamic_analysis", _fake_runner(threshold=99.0))
    result = run_ida(
        make_sample_model(),
        _modal(),
        "record.txt",
        [0.5, 1.0, 2.0, 3.0],
        accel_base=np.ones(20),
        num_steps=10,
        collapse_drift=0.05,
        tolerance=None,
    )
    assert result["collapse_intensity"] == 3.0
    assert result["collapse_criterion"] == "drift"


def test_run_ida_bisection(monkeypatch):
    """Bisection refines the first collapse intensity to tolerance."""
    import fea_toolkit.analysis.nonlinear_dynamic as nld_module

    monkeypatch.setattr(nld_module, "run_nonlinear_dynamic_analysis", _fake_runner(threshold=1.8))
    result = run_ida(
        make_sample_model(),
        _modal(),
        "record.txt",
        [0.5, 1.0, 1.5, 2.0],
        accel_base=np.ones(20),
        num_steps=10,
        tolerance=0.01,
    )
    assert result["bisection"] is not None
    assert 1.5 < result["collapse_intensity"] < 2.0


def test_run_ida_requires_a_one_dimensional_record():
    """A 2-D record array is rejected before any rung runs."""
    with pytest.raises(ValueError, match="single column"):
        run_ida(
            make_sample_model(),
            _modal(),
            "record.txt",
            [1.0],
            accel_base=np.ones((2, 2)),
            num_steps=10,
        )


def test_run_ida_missing_record_file():
    """An unreadable record path raises a clear error."""
    with pytest.raises(ValueError, match="Cannot read ground motion file"):
        run_ida(make_sample_model(), _modal(), "/no/such/record.txt", [1.0], num_steps=10)
