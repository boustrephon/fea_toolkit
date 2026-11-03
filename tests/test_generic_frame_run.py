"""Generic-frame collapse (IDA) runner — smoke test on a synthetic pulse.

The real validation needs the LMSR-N record set (non-redistributable, fetched
locally).  This smoke test drives :func:`~fea_toolkit.benchmarks.generic_frame_run._ida_run`
end-to-end with a synthetic sine pulse so the plumbing — P-Δ + held gravity,
Rayleigh damping, and the drift/metric extraction — is exercised
without any record on disk.
"""

import math

import numpy as np
import pytest

from fea_toolkit.benchmarks import generic_frame_run as gfr

_G = 9.80665


@pytest.fixture(autouse=True)
def _isolate_ground_motion(tmp_path, monkeypatch):
    """Redirect the mirror to ``tmp_path`` so tests never touch the repo."""
    from fea_toolkit.benchmarks.ik2005_records import GROUND_MOTION_DIR_ENV

    monkeypatch.setenv(GROUND_MOTION_DIR_ENV, str(tmp_path))


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


@pytest.mark.parametrize(
    ("code", "t1_target", "t95"),
    [
        ("0909", 0.9, 0.2077),
        ("0918", 1.8, 0.4154),
        ("1224", 2.4, 0.3931),
    ],
)
def test_rayleigh_periods(code, t1_target, t95):
    """Rayleigh anchor = T1 + the period at 95 % cumulative X participation.

    IK-2005 anchors 5 % Rayleigh at T1 and the mode where cumulative X mass
    participation first exceeds 95 % (§5.3.1, Appendix B.4).  For the 9-storey
    frames that is the third lateral mode (cumulative 78.9 + 11.7 + 4.5 % =
    95.2 %), for the 12-storey frame the fourth (77.9 + 11.5 + 4.6 + 2.3 % =
    96.4 %) — not the ``T1·0.5`` mass-proportional fallback.
    """
    t1, t95_actual = gfr._rayleigh_periods(code)
    assert t1 == pytest.approx(t1_target, abs=0.02)
    assert t95_actual == pytest.approx(t95, abs=0.01)
    assert t95_actual < t1


@pytest.mark.parametrize(
    ("code", "pf1"),
    [
        ("0303", 1.286),
        ("0909", 1.421),
        ("0918", 1.421),
        ("1224", 1.440),
    ],
)
def test_first_mode_participation(code, pf1):
    """PF1 ≈ 3N/(2N+1) — the roof-normalised first-mode participation factor."""
    assert gfr.first_mode_participation(code) == pytest.approx(pf1, abs=0.01)


def test_drift_demands_returns_structure(tmp_path):
    """``drift_demands`` reports PF1 and per-ratio normalised roof drifts."""
    local = tmp_path / "local"
    local.mkdir(parents=True, exist_ok=True)
    rid = "_test_drift_demands"
    path = local / f"{rid}.csv"
    t = np.arange(120) * 0.005  # 0.6 s
    acc = 0.3 * _G * np.sin(2 * math.pi * t / 0.9)
    path.write_text("time,accel\n" + "".join(f"{tt},{aa}\n" for tt, aa in zip(t, acc)))
    try:
        res = gfr.drift_demands("0909", [rid], ratios=(1.0,))
    finally:
        path.unlink(missing_ok=True)

    assert res["code"] == "0909"
    assert res["PF1"] == pytest.approx(1.421, abs=0.01)
    assert set(res["ratios"]) == {1.0}
    assert res["median"][1.0] > 0.0


def test_load_record_reads_at2_and_csv(tmp_path):
    """``_load_record`` accepts PEER ``.AT2`` (g→m/s²) and ``.csv`` (as-is)."""
    local = tmp_path / "local"
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


def test_load_record_reads_channel_suffix(tmp_path):
    """``_load_record`` reads ``<record_id>_<channel>.csv`` when channel is given."""
    local = tmp_path / "local"
    local.mkdir(parents=True, exist_ok=True)
    path = local / "_test_lmsr_n_chan_a.csv"
    path.write_text("time,accel\n0.0,0.0\n0.005,2.0\n")
    try:
        accel, dt = gfr._load_record("_test_lmsr_n_chan", 0.005, channel="a")
    finally:
        path.unlink(missing_ok=True)
    assert dt == 0.005
    assert accel == pytest.approx(np.array([0.0, 2.0]))
