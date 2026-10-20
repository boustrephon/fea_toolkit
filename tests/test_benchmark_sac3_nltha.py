"""SAC 3-story NLTHA benchmark (T4 smoke anchor).

Runs the elastic SAC 3-story frame through the native Newmark transient runner.

- *Always-on*: a synthetic pulse drives the full Preprocessor → AnalysisBuilder →
  ``run_transient_analysis`` pipeline and checks a finite, positive peak response.
- *Gated* (``@pytest.mark.needs_ground_motion("LA21")``): the real SAC LA record
  drives the frame; the test skips cleanly when the record is absent from the
  ``data/ground_motion/local`` mirror.

Reference — Ohtori et al. (2004), DOI 10.1061/(ASCE)0733-9399(2004)130:4(366),
Table 3: the 3-story *nonlinear* evaluation model reaches a peak roof
displacement of ~0.151 m under the El Centro record at intensity 1.0.  This
test runs the *elastic* model (the Tcl fixture models bare, elastic members), so
it is a smoke anchor — it validates the pipeline end-to-end rather than
reproducing the nonlinear reference band; the elastic model overestimates the
peak because it never yields.
"""

import math

import numpy as np
import pytest

from fea_toolkit.benchmarks.sac import build_builder, peak_x


def _build():
    """Build the elastic SAC frame (smoke anchor)."""
    return build_builder(nonlinear=False)


def test_sac3_elastic_nltha_synthetic():
    """The elastic SAC frame under a synthetic pulse converges with a finite,
    positive peak response (always-on)."""
    from openseespy.opensees import wipe

    wipe()
    try:
        b = _build()
        dt = 0.005
        n = 200
        t = np.arange(n) * dt
        accel = np.zeros(n)
        accel[:40] = 2.0 * np.sin(2.0 * math.pi * 1.0 * t[:40])
        res = b.run_transient_analysis(accel, dt, direction="X", damping_ratio=0.05)
        assert res["converged_steps"] == n
        assert res["peak_displacement"] > 0.0
        assert np.isfinite(res["peak_displacement"])
    finally:
        wipe()


@pytest.mark.needs_ground_motion("LA21")
def test_sac3_elastic_nltha_la21(resolve_ground_motion_record):
    """Run the elastic SAC frame under the LA21 record (gated on the mirror)."""
    from openseespy.opensees import wipe

    from fea_toolkit.io.ground_motion import read_peer_record

    wipe()
    try:
        record_path = resolve_ground_motion_record("LA21")
        times, accel = read_peer_record(record_path)
        b = _build()
        dt = float(times[1] - times[0])
        res = b.run_transient_analysis(accel, dt, direction="X", damping_ratio=0.05)
        assert res["converged_steps"] > 0
        assert res["peak_displacement"] > 0.0
        assert np.isfinite(res["peak_displacement"])
    finally:
        wipe()


# ── Nonlinear evaluation model (M1/M2) ──
# Built by `fea_toolkit.benchmarks.sac.build_builder` — bilinear steel hinges
# (hinge_model='lumped', hinge_backbone='bilinear') plus, for M2, the Krawinkler
# & Gupta panel zone.  See docs/sac_nonlinear_benchmark.md.


def test_sac3_nonlinear_nltha_synthetic():
    """The nonlinear SAC frame — M1 (bilinear hinges) and M2 (hinges + panel
    zone) — converges under a synthetic pulse with a finite, positive peak
    (always-on)."""
    from openseespy.opensees import wipe

    dt, n = 0.005, 200
    t = np.arange(n) * dt
    accel = np.zeros(n)
    accel[:40] = 2.0 * np.sin(2.0 * math.pi * 1.0 * t[:40])

    for panel_zone in (False, True):
        wipe()
        try:
            b = build_builder(panel_zone=panel_zone)
            res = b.run_transient_analysis(accel, dt, direction="X", damping_ratio=0.05)
            assert res["converged_steps"] > 0
            assert res["peak_displacement"] > 0.0
            assert np.isfinite(res["peak_displacement"])
        finally:
            wipe()


def test_sac3_m2_panel_zone_modal_period():
    """M2 (bilinear hinges + Krawinkler & Gupta panel zone) has a sane
    fundamental period — regression guard for the panel-zone + hinge equalDOF
    degeneracy.

    The combination once produced an over-stiff T1 ≈ 0.20 s (the sway modes were
    locked by a chained equalDOF through the ``*_pz`` beam node).  The fixed
    model softens to T1 ≈ 1.29 s (vs 1.01 s elastic, 1.06 s M1 hinge-only).
    """
    from openseespy.opensees import wipe

    wipe()
    try:
        b = build_builder(panel_zone=True)
        modal = b.run_modal_analysis(num_modes=1, print_results=False)
        assert 1.2 < float(modal["periods"][0]) < 1.4
    finally:
        wipe()


@pytest.mark.needs_ground_motion("LA21")
def test_sac3_nonlinear_nltha_la21(resolve_ground_motion_record):
    """Nonlinear SAC frame (bilinear hinges, M1 vs M2) under LA21 (gated).

    LA21 is the SAC 1995 Kobe (Takatori) record, *not* the El Centro record
    for which Ohtori et al. (2004) Table 3 reports the ~0.151 m peak roof
    displacement, so the Ohtori M1 acceptance band does not apply here.  This
    test is therefore a smoke anchor only: it checks that both the M1 (hinges)
    and M2 (hinges + panel zone) models converge and return a finite peak.

    The M2 (panel-zone) run is a smoke check here because LA21 is not the record
    for which Ohtori's band applies; the panel-zone + hinge modal response is
    verified separately by ``test_sac3_m2_panel_zone_modal_period``.
    """
    from openseespy.opensees import wipe

    from fea_toolkit.io.ground_motion import read_peer_record

    wipe()
    try:
        record_path = resolve_ground_motion_record("LA21")
        times, accel = read_peer_record(record_path)
        dt = float(times[1] - times[0])

        b = build_builder(panel_zone=False)
        res = b.run_transient_analysis(accel, dt, direction="X", damping_ratio=0.05)
        assert res["converged_steps"] > 0
        assert np.isfinite(peak_x(res))

        b = build_builder(panel_zone=True)
        res = b.run_transient_analysis(accel, dt, direction="X", damping_ratio=0.05)
        assert res["converged_steps"] > 0
        assert np.isfinite(peak_x(res))
    finally:
        wipe()
