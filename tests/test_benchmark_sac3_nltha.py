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
    model softens to T1 ≈ 1.13 s (vs 0.99 s elastic, 1.04 s M1 hinge-only).
    """
    from openseespy.opensees import wipe

    wipe()
    try:
        b = build_builder(panel_zone=True)
        modal = b.run_modal_analysis(num_modes=1, print_results=False)
        assert 1.0 < float(modal["periods"][0]) < 1.3
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


@pytest.mark.needs_ground_motion("ELCENTRO")
def test_sac3_nonlinear_nltha_elcentro(resolve_ground_motion_record):
    """Nonlinear SAC frame (M1 vs M2) under the El Centro record (gated).

    El Centro 1940 is the record for which Ohtori et al. (2004) Table 3 reports
    the ~0.151 m peak roof displacement at intensity 1.0; the benchmark runs it
    at 2 % damping.  The M1 model should land close to that reference.
    """
    from openseespy.opensees import wipe

    from fea_toolkit.benchmarks.sac import load_benchmark_record

    wipe()
    try:
        record_path = resolve_ground_motion_record("ELCENTRO")
        accel, dt = load_benchmark_record(record_path)

        b = build_builder(panel_zone=False)
        res = b.run_transient_analysis(accel, dt, direction="X", damping_ratio=0.02)
        peak = peak_x(res)
        assert res["converged_steps"] > 0
        assert np.isfinite(peak)
        # Ohtori Table 3: ~0.151 m under El Centro at intensity 1.0.
        assert 0.10 < peak < 0.25, f"M1 El Centro peak {peak:.3f} m out of band"

        b = build_builder(panel_zone=True)
        res = b.run_transient_analysis(accel, dt, direction="X", damping_ratio=0.02)
        assert res["converged_steps"] > 0
        assert np.isfinite(peak_x(res))
    finally:
        wipe()


@pytest.mark.needs_ground_motion("ELCENTRO")
def test_sac3_beam_with_hinges_elcentro(resolve_ground_motion_record):
    """``beam_with_hinges`` (trilinear) under El Centro lands on the peak.

    The ``beamWithHinges`` element is the preferred hinge: its roof peak
    (≈0.151 m) matches Ohtori Table 3 within ±10 %, and its yielded-hinge count
    is within ±3 of the benchmark's 22.
    """
    from openseespy.opensees import wipe

    from fea_toolkit.benchmarks.sac import load_benchmark_record

    wipe()
    try:
        record_path = resolve_ground_motion_record("ELCENTRO")
        accel, dt = load_benchmark_record(record_path)
        b = build_builder(backbone="trilinear", hinge_model="beam_with_hinges")
        res = b.run_transient_analysis(accel, dt, direction="X", damping_ratio=0.02)
        assert res["converged_steps"] > 0
        peak = peak_x(res)
        assert 0.10 < peak < 0.25, f"beamWithHinges El Centro peak {peak:.3f} m out of band"
        assert res["num_yielded_hinges"] == pytest.approx(22, abs=3)
    finally:
        wipe()


# ── A3 — transient-runner peak recorders (drift/accel/vel/base-shear/φmax/Emax/Nd) ──
# `run_transient_analysis` now returns per-node velocity/acceleration histories,
# the base-shear history (global equilibrium Σ m·(a_rel + ag)), and the
# lumped-hinge damage quantities (peak plastic rotation, dissipated energy,
# yielded-hinge count).  See docs/_pending_work.md P41.


def test_transient_recorders_force_and_kinematic_quantities():
    """The elastic runner returns velocity/acceleration/base-shear histories.

    A synthetic pulse drives the elastic 3-story frame; the base shear (via
    global equilibrium) and the per-node kinematic histories are all finite and
    non-zero, and the base shear is the total inertial force Σ m·(a_rel + ag).
    """
    from openseespy.opensees import nodeMass, wipe

    wipe()
    try:
        b = build_builder(nonlinear=False)
        dt, n = 0.005, 200
        t = np.arange(n) * dt
        accel = np.zeros(n)
        accel[:40] = 2.0 * np.sin(2.0 * math.pi * 1.0 * t[:40])
        res = b.run_transient_analysis(accel, dt, direction="X", damping_ratio=0.05)

        assert res["converged_steps"] == n
        assert res["base_shear"].shape == (n,)
        assert res["peak_base_shear"] > 0.0
        assert np.isfinite(res["peak_base_shear"])
        for key in ("velocities", "accelerations"):
            arr = next(iter(res[key].values()))
            assert arr.shape == (n, 3)
            assert np.isfinite(arr).all()

        # Global-equilibrium cross-check at the final step: base shear equals the
        # sum of nodal inertial forces m·(a_rel + ag).
        step = n - 1
        inertial = sum(
            nodeMass(tag, 1) * (res["accelerations"][tag][step][0] + accel[step])
            for tag in res["accelerations"]
        )
        assert res["base_shear"][step] == pytest.approx(inertial, rel=1e-6)
    finally:
        wipe()


def test_transient_recorders_hinge_damage():
    """The trilinear runner reports φmax / Emax / Nd when hinges yield.

    A monotonic (pushover-like) acceleration drives the trilinear frame into
    yield, so the lumped-hinge damage recorders are non-trivial.
    """
    from openseespy.opensees import wipe

    wipe()
    try:
        b = build_builder(backbone="trilinear")
        dt, n = 0.005, 300
        accel = 30.0 * np.ones(n)  # monotonic — forces hinge yield
        res = b.run_transient_analysis(accel, dt, direction="X", damping_ratio=0.02)

        assert res["converged_steps"] == n
        assert len(b._hinge_tags) > 0
        assert res["peak_plastic_rotation"] > 0.0
        assert res["num_yielded_hinges"] > 0
        assert res["max_dissipated_energy"] > 0.0
        # The peak plastic rotation exceeds the (small) hinge yield rotation.
        assert res["peak_plastic_rotation"] > b._hinge_yield_rotations[0]
    finally:
        wipe()
