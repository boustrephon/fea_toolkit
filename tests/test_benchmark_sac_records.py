"""SAC benchmark ground-motion records (A0 sourcing).

The four Ohtori et al. (2004) benchmark signals — El Centro (1940), Hachinohe
(1968), Northridge / Sylmar (1994) and Kobe / JMA (1995) — are extracted from
the SAC benchmark archive (``local/257/Files_bldg_full3.zip`` →
``MATLAB_Utility/*.mat``) into the ``data/ground_motion/local/`` mirror and
registered in ``tests/fixtures/ground_motion_manifests.json``.  This test is
gated on the records' presence and guards their identity via each record's
peak ground acceleration.
"""

import pytest

from fea_toolkit.io.ground_motion import read_time_history_csv, record_summary

# record_id -> (peak acceleration in m/s^2, relative tolerance).  These are the
# benchmark archive's own peak accelerations (the .mat records store SI m/s^2);
# asserting them confirms the correct component was extracted and converted.
_EXPECTED_PGA = {
    "ELCENTRO": (3.417, 0.05),
    "HACHINOHE": (2.250, 0.05),
    "KOBE": (8.178, 0.05),
    "NORTHRIDGE": (8.268, 0.05),
}


@pytest.mark.needs_ground_motion("ELCENTRO", "HACHINOHE", "KOBE", "NORTHRIDGE")
def test_benchmark_records_present_with_expected_pga(resolve_ground_motion_record):
    """Each benchmark record reads with the archive's own peak acceleration."""
    for record_id, (expected, tol) in _EXPECTED_PGA.items():
        path = resolve_ground_motion_record(record_id)
        times, accel = read_time_history_csv(path)
        summary = record_summary(times, accel)
        assert summary["pga"] == pytest.approx(expected, rel=tol), (
            f"{record_id} PGA {summary['pga']:.3f} m/s^2 vs expected {expected}"
        )
        assert summary["npts"] > 0
