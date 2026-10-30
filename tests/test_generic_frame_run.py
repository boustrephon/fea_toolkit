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


def test_load_record_reads_at2_and_csv():
    """``_load_record`` accepts PEER ``.AT2`` (g→m/s²) and ``.csv`` (as-is)."""
    from pathlib import Path

    root = Path(gfr.__file__).resolve().parent.parent.parent.parent
    local = root / "data" / "ground_motion" / "local"
    local.mkdir(parents=True, exist_ok=True)
    at2_id = "_test_lmsr_n_at2"
    csv_id = "_test_lmsr_n_csv"
    at2_path = local / f"{at2_id}.AT2"
    csv_path = local / f"{csv_id}.csv"
    at2_path.write_text(
        "PEER NGA STRONG MOTION RECORD\nNPTS=    5, DT=  0.005\n0.1\n-0.1\n0.2\n0.0\n-0.05\n"
    )
    csv_path.write_text("time,accel\n0.0,0.0\n0.005,1.0\n0.010,-1.0\n")
    try:
        accel_at2, dt_at2 = gfr._load_record(at2_id, 0.005)
        accel_csv, dt_csv = gfr._load_record(csv_id, 0.005)
    finally:
        at2_path.unlink(missing_ok=True)
        csv_path.unlink(missing_ok=True)

    # .AT2 stores g — converted to m/s² by read_peer_record.
    assert dt_at2 == 0.005
    assert accel_at2 == pytest.approx(np.array([0.1, -0.1, 0.2, 0.0, -0.05]) * _G)
    # .csv stores m/s² directly.
    assert dt_csv == 0.005
    assert accel_csv == pytest.approx(np.array([0.0, 1.0, -1.0]))


def test_load_record_missing_raises():
    """An absent record raises a clear ``FileNotFoundError``."""
    with pytest.raises(FileNotFoundError):
        gfr._load_record("_no_such_lmsr_n_record", 0.005)
