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
import pathlib

import numpy as np
import pytest

from fea_toolkit.io.tcl_importer import parse_tcl_model
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
from fea_toolkit.opensees.preprocessor import preprocess_model

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "sac_3story.tcl"
UNITS = {"F": "N", "L": "m", "T": "C"}


def _build() -> AnalysisBuilder:
    md = parse_tcl_model(FIXTURE.read_text(encoding="utf-8"), UNITS)
    mesh = preprocess_model(md, {"element_type": "elasticBeamColumn"})
    b = AnalysisBuilder(mesh, {})
    b.build_domain()
    return b


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
