"""A3 — SAC 3-story IMK (IMKBilin) NLTHA convergence.

Runs the IMK hinge (``hinge_model='lumped'``, ``hinge_backbone='imk'``) through
the native Newmark transient runner.  The headline A3 result: ``IMKBilin``
converges on the strong benchmark cases where the Clough trilinear hinge
(β=0.5) lost convergence.

- *Always-on*: a monotonic pulse drives the IMK frame into yield and checks a
  finite, positive peak response with yielded hinges.
- *Gated* (``@pytest.mark.needs_ground_motion("ELCENTRO")``): the strongest
  benchmark case (El Centro ×1.5) converges end-to-end.

See ``docs/imk_deterioration_model.md`` (§5, A3 item 4).
"""

import numpy as np
import pytest

from fea_toolkit.benchmarks import sac


def _build_imk():
    """The IMK evaluation model with the derived FEMA 355C column axial ratios."""
    return sac.build_builder(
        nonlinear=True,
        backbone="imk",
        hinge_model="lumped",
        derive_column_axial_ratio=True,
    )


def test_imk_nltha_synthetic_yields_then_collapses():
    """The IMK frame yields, then reaches dynamic instability (always-on).

    A monotonic 30 m/s² pulse is an extreme demand: the ``IMKBilin`` hinges yield
    and then degrade into their post-capping branch, so the transient ends in
    dynamic instability (``converged_steps`` < the full record) rather than
    running to the end.  This is the deterioration model working as intended —
    the opposite of the trilinear hinge, which hardens at 3 % and converges.
    """
    from openseespy.opensees import wipe

    wipe()
    try:
        b = _build_imk()
        dt, n = 0.005, 300
        accel = 30.0 * np.ones(n)  # monotonic — forces yield, then collapse
        res = b.run_transient_analysis(accel, dt, direction="X", damping_ratio=0.02)
        assert 0 < res["converged_steps"] < n  # dynamic instability before the end
        assert res["peak_displacement"] > 0.0
        assert np.isfinite(res["peak_displacement"])
        assert res["num_yielded_hinges"] > 0
        assert res["peak_plastic_rotation"] > 0.0
    finally:
        wipe()


@pytest.mark.needs_ground_motion("ELCENTRO")
def test_imk_nltha_strong_case_converges():
    """``IMKBilin`` converges on El Centro ×1.5 — the strongest Clough-failing case."""
    from openseespy.opensees import wipe

    from fea_toolkit.benchmarks import sac_run
    from fea_toolkit.benchmarks.sac import load_benchmark_record

    accel_raw, dt = load_benchmark_record(str(sac_run.record_path("ELCENTRO")))
    accel = np.asarray(accel_raw, dtype=float) * 1.5
    # The benchmark anchors Rayleigh ζ=2 % at the *elastic* mode-1 / mode-2
    # periods (the IMK lumped-hinge model is ~3× larger, so its eigen solve is
    # avoided — see ``sac_run._elastic_rayleigh_periods``).
    period_1, period_2 = sac_run._elastic_rayleigh_periods(3)

    wipe()
    try:
        b = _build_imk()
        res = b.run_transient_analysis(
            accel,
            dt,
            direction="X",
            damping_ratio=0.02,
            period_1=period_1,
            period_2=period_2,
        )
        assert res["converged_steps"] == len(accel)
        assert res["peak_displacement"] > 0.0
        assert res["num_yielded_hinges"] > 0
    finally:
        wipe()
