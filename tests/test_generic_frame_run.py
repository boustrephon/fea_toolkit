"""Generic-frame collapse (IDA) runner — smoke test on a synthetic pulse.

The real validation needs the LMSR-N record set (non-redistributable, fetched
locally).  This smoke test drives :func:`~fea_toolkit.benchmarks.generic_frame_run._ida_run`
end-to-end with a synthetic sine pulse so the plumbing — P-Δ + held gravity,
mass-proportional damping, and the drift/metric extraction — is exercised
without any record on disk.
"""

import math

import numpy as np
import pytest

from fea_toolkit.benchmarks import generic_frame_run as gfr

_G = 9.80665


def _sine_pulse(dt: float, period: float, cycles: int = 8, pga: float = 0.3) -> np.ndarray:
    """A sine pulse at *period*, scaled to *pga* (g)."""
    n = int(cycles * period / dt)
    t = np.arange(n) * dt
    return pga * _G * np.sin(2.0 * math.pi * t / period)


def test_ida_run_smoke():
    """A short IDA ladder over a synthetic pulse returns the expected structure."""
    code = "0303"
    dt = 0.005
    accel = _sine_pulse(dt, gfr.period_1(code), cycles=8, pga=0.3)
    res = gfr._ida_run(code, "synthetic", accel, dt, [0.5, 1.0, 2.0])
    assert res["code"] == "0303"
    assert res["record"] == "synthetic"
    assert res["intensities"] == [0.5, 1.0, 2.0]
    assert len(res["drifts"]) == 3
    assert len(res["converged"]) == 3
    # Drifts are non-negative and monotonically non-decreasing with intensity.
    drifts = res["drifts"]
    assert all(d >= 0.0 for d in drifts)
    assert drifts[0] <= drifts[1] <= drifts[2]
    assert "collapse_intensity" in res and "bisection" in res


def test_collapse_capacity_result_shape():
    """``collapse_capacity_sa`` returns the IK-2005 normalisation fields."""
    # No records needed — feed empty lists to check the shape only.
    res = gfr.collapse_capacity_sa("0909", [], [])
    assert res["code"] == "0909"
    assert res["T1"] == pytest.approx(0.9)
    assert res["gamma"] == 1.0
    assert res["median_collapse_sa"] is None  # no records collapsed
    assert res["median_collapse_sa_gamma"] is None
    assert res["records"] == []
