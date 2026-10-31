"""Tests for the USGS NSMP SMC-format reader."""

import numpy as np
import pytest

from fea_toolkit.io.ground_motion import parse_smc, read_smc_record


def _field(v: float) -> str:
    """Format a value as a 10-char Fortran E10.4-style field (single-digit exponent)."""
    mant, exp = f"{v:.4E}".split("E")
    return f"{mant}E{int(exp):+d}".rjust(10)


def _make_smc_text(npts: int = 5, rate: float = 200.0, orientation: int = 90) -> str:
    """Build a minimal valid SMC-format text with a known 5-value time series."""
    lines = [
        "2 CORRECTED ACCELEROGRAM",
        "*",
        "TESTA",
        "     1989  10  18    0004 Loma Prieta, CA",
        "Moment Mag=      7.1 Ms=      7.1 Ml=      7.0",
        "station = Test Station component=   180",
        "epicentral dist =     89.0       pk     =    104.4",
        "inst type=SMA-1      data source = USGS",
        "*",
        "*",
        "*",
    ]
    # 6 integer-header lines, 8 values each (8I10).
    ints = [-32768] * 48
    ints[13] = orientation  # horizontal orientation (degrees east of north)
    ints[15] = 0  # number of comment lines
    ints[16] = npts  # number of time-series values
    for row in range(6):
        lines.append(" ".join(f"{v:8d}" for v in ints[row * 8 : (row + 1) * 8]))
    # 10 real-header lines, 5 values each (5E15.7).
    reals = [1.7e38] * 50
    reals[1] = rate  # sample rate (Hz)
    for row in range(10):
        lines.append(" ".join(f"{v:14.7E}" for v in reals[row * 5 : (row + 1) * 5]))
    # Time series: npts values, 10 fixed-width chars each (8 per line).
    vals = [1.0, -2.0, 3.0, -4.0, 5.0][:npts]
    lines.append("".join(_field(v) for v in vals))
    return "\n".join(lines) + "\n"


def test_parse_smc_extracts_times_accel_and_meta():
    times, accel, meta = parse_smc(_make_smc_text())
    assert meta["npts"] == 5
    assert meta["sample_rate"] == 200.0
    assert meta["dt"] == pytest.approx(0.005)
    assert meta["orientation"] == 90
    assert meta["component"] == "180"
    assert "Test Station" in meta["station"]
    np.testing.assert_allclose(times, np.arange(5) * 0.005)
    # cm/s² → m/s² (÷100)
    np.testing.assert_allclose(accel, np.array([0.01, -0.02, 0.03, -0.04, 0.05]))


def test_read_smc_record(tmp_path):
    path = tmp_path / "record.smc"
    path.write_text(_make_smc_text())
    _, accel = read_smc_record(str(path))
    assert len(accel) == 5
    assert accel[0] == pytest.approx(0.01)


def test_parse_smc_rejects_short_text():
    with pytest.raises(ValueError):
        parse_smc("too short\n")
