"""A3 — SAC incremental dynamic analysis (IDA) and collapse detection.

The IDA driver scales one record over an IM ladder and reports the first
collapse intensity, using the two Ibarra–Krawinkler collapse criteria —
non-convergence to the end of the record, and a peak interstorey-drift ratio
beyond ``collapse_drift``.

- *Always-on*: a monotonic synthetic pulse drives the 3-story IMK frame over a
  three-rung ladder and checks the collapse intensity is detected at the top of
  the ladder (the same non-convergence collapse the
  ``test_imk_nltha_synthetic_yields_then_collapses`` case exhibits).
- *Gated* (``@pytest.mark.needs_ground_motion("ELCENTRO")``): the real El Centro
  record over a small ladder.

See ``docs/imk_deterioration_model.md`` (§5, A3) and
``docs/benchmark_roadmap.md`` (§3).
"""

import numpy as np
import pytest

from fea_toolkit.benchmarks.sac_run import (
    _ida_run,
    collapse_intensity,
    sdof_spectral_acceleration,
)


def test_collapse_intensity_criteria():
    """The pure collapse predicate trips on non-convergence or excess drift."""
    # No collapse: all runs converge and stay under the drift threshold.
    assert collapse_intensity([1.0, 2.0, 3.0], [0.01, 0.02, 0.03], [True, True, True], 0.10) is None
    # Non-convergence at the second rung → collapse at that intensity.
    assert collapse_intensity([1.0, 2.0, 3.0], [0.01, 0.02, 0.03], [True, False, True], 0.10) == 2.0
    # Drift beyond the threshold → collapse at the second rung.
    assert collapse_intensity([1.0, 2.0, 3.0], [0.01, 0.12, 0.15], [True, True, True], 0.10) == 2.0
    # The drift threshold is honoured (a lower threshold collapses earlier).
    assert collapse_intensity([1.0, 2.0], [0.05, 0.08], [True, True], 0.06) == 2.0


def test_ida_synthetic_ladder_collapses_at_top():
    """A monotonic pulse ladder yields, then collapses at the top rung (always-on)."""
    dt, n = 0.005, 300
    accel_base = np.ones(n)  # amplitude 1 m/s²; scaled by the ladder
    intensities = [0.2, 1.0, 5.0]
    result = _ida_run(3, "SYNTHETIC", accel_base, dt, intensities, damping=0.02)

    assert result["building"] == 3
    assert result["record"] == "SYNTHETIC"
    assert result["intensities"] == intensities
    assert len(result["drifts"]) == len(intensities)
    assert len(result["converged"]) == len(intensities)
    # The two lower rungs converge; the top rung reaches dynamic instability
    # (non-convergence) — the same collapse the IMK NLTHA test exhibits.
    assert result["converged"] == [True, True, False]
    # Drift grows with intensity (more demand → more drift), and the top rung's
    # drift also exceeds the sidesway threshold (0.1258 > 0.10).
    assert result["drifts"][0] < result["drifts"][1] < result["drifts"][2]
    assert result["drifts"][2] > 0.10
    assert result["collapse_intensity"] == intensities[-1]


def test_ida_bisection_refines_collapse():
    """A coarse ladder + bisection refines the collapse intensity (always-on)."""
    dt, n = 0.005, 300
    accel_base = np.ones(n)  # amplitude 1 m/s²; scaled by the ladder
    result = _ida_run(3, "SYNTHETIC", accel_base, dt, [0.2, 1.0, 5.0], damping=0.02, tolerance=0.25)

    assert result["bisection"] is not None
    lo = result["bisection"]["lo"]
    hi = result["bisection"]["hi"]
    assert lo < hi
    # The bracket is within tolerance and inside the original ladder's top rungs.
    assert (hi - lo) / hi <= 0.25
    assert 1.0 <= lo < hi <= 5.0
    # The reported collapse intensity is the bracket midpoint.
    assert result["collapse_intensity"] == pytest.approx(0.5 * (lo + hi))
    # The bisection recorded its evaluations.
    assert result["bisection"]["evaluations"]


def test_ida_extends_ladder_to_collapse():
    """A short ladder + ``max_scale`` extends geometrically until collapse (always-on)."""
    dt, n = 0.005, 300
    accel_base = np.ones(n)  # amplitude 1 m/s²; scaled by the ladder
    result = _ida_run(3, "SYNTHETIC", accel_base, dt, [0.2, 1.0], damping=0.02, max_scale=6.0)

    # The seed ladder [0.2, 1.0] converges (both True); the extension then
    # doubles the top rung until collapse, so the ladder grew past its seed.
    assert result["converged"][:2] == [True, True]
    assert len(result["intensities"]) > 2
    assert result["intensities"][-1] <= 6.0
    # Collapse was found at the last extended rung (no bisection without tolerance).
    assert result["collapse_intensity"] is not None
    assert result["collapse_intensity"] == result["intensities"][-1]


def test_ida_max_scale_stops_without_collapse():
    """The ladder extension halts at ``max_scale`` when nothing collapses (always-on)."""
    dt, n = 0.005, 300
    accel_base = np.ones(n)
    # max_scale below the collapse intensity (≈5) → no collapse reported.
    result = _ida_run(3, "SYNTHETIC", accel_base, dt, [0.2, 1.0], damping=0.02, max_scale=2.0)
    assert result["collapse_intensity"] is None
    assert result["intensities"][-1] == 2.0


def test_sdof_spectral_acceleration_stiff_tracks_pga():
    """A very stiff oscillator tracks the ground — ``Sa(T→0) ≈ PGA``."""
    dt = 0.001
    t = np.arange(4000) * dt
    accel = 0.5 * np.sin(2.0 * np.pi * 2.0 * t)  # 2 Hz, PGA = 0.5
    sa = sdof_spectral_acceleration(accel, dt, 0.01, zeta=0.05)
    assert sa == pytest.approx(0.5, rel=0.05)


def test_sdof_spectral_acceleration_linear_in_amplitude():
    """A linear SDOF response doubles when the record amplitude doubles."""
    dt = 0.005
    t = np.arange(2000) * dt
    accel = np.sin(2.0 * np.pi * 1.0 * t)
    sa1 = sdof_spectral_acceleration(accel, dt, 0.5, zeta=0.05)
    sa2 = sdof_spectral_acceleration(2.0 * accel, dt, 0.5, zeta=0.05)
    assert sa2 == pytest.approx(2.0 * sa1, rel=1e-6)


@pytest.mark.needs_ground_motion("ELCENTRO")
def test_ida_elcentro_ladder(resolve_ground_motion_record):
    """The real El Centro record over a small ladder returns a drift curve (gated)."""
    from fea_toolkit.benchmarks.sac_run import run_ida

    result = run_ida(3, "ELCENTRO", [0.5, 1.0, 1.5], collapse_drift=0.10, damping=0.02)
    assert result["record"] == "ELCENTRO"
    assert len(result["drifts"]) == 3
    assert all(d >= 0.0 for d in result["drifts"])
