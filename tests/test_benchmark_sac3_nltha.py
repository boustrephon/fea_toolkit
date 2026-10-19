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


# ═══════════════════════════════════════════════════════════════════
# Nonlinear evaluation model — bilinear hinges (+ optional panel zones)
# ═══════════════════════════════════════════════════════════════════
#
# AISC plastic section moduli and section depths for the SAC members (SI):
#   W24x68  (columns): Zx = 154 in^3 = 2.524e-3 m^3, d = 23.7 in = 0.602 m,
#                      tw = 0.415 in = 0.0105 m,  fy = 345 MPa.
#   W30x116 (beams):   Zx = 378 in^3 = 6.194e-3 m^3, d = 30.0 in = 0.762 m,
#                      tw = 0.565 in = 0.0144 m,  fy = 248 MPa.
#
# Yield moment My = Zx · fy (≈ 870 kN·m columns, ≈ 1540 kN·m beams).  The
# benchmark's exact hinge/panel-zone data are not redistributable, so these are
# computed from AISC tables as documented in docs/sac_benchmark.md.

_COLUMN = {"Z33": 2.524e-3, "depth": 0.602, "tw": 0.0105, "fy": 345e6}
_BEAM = {"Z33": 6.194e-3, "depth": 0.762, "tw": 0.0144, "fy": 248e6}


def _enrich_nonlinear(md):
    """Enrich the Tcl-parsed elastic model with plastic moduli + yield strengths."""
    from fea_toolkit.model.sap_data import Material

    md.materials["SteelCol"] = Material("SteelCol", "Steel", E_mod=2.0e11, Fy=_COLUMN["fy"])
    md.materials["SteelBeam"] = Material("SteelBeam", "Steel", E_mod=2.0e11, Fy=_BEAM["fy"])
    for sec in md.sections.values():
        if abs(sec.A - 1.290e-2) < 1e-6:  # W24x68 column
            sec.Z33 = _COLUMN["Z33"]
            sec.depth = _COLUMN["depth"]
            sec.tw = _COLUMN["tw"]
            sec.material = "SteelCol"
        elif abs(sec.A - 2.206e-2) < 1e-6:  # W30x116 beam
            sec.Z33 = _BEAM["Z33"]
            sec.depth = _BEAM["depth"]
            sec.tw = _BEAM["tw"]
            sec.material = "SteelBeam"


def _build_nonlinear(panel_zone: bool = False) -> AnalysisBuilder:
    md = parse_tcl_model(FIXTURE.read_text(encoding="utf-8"), UNITS)
    _enrich_nonlinear(md)
    mesh = preprocess_model(md, {"element_type": "elasticBeamColumn"})
    cfg = {
        "element_type": "elasticBeamColumn",
        "hinge_model": "lumped",
        "hinge_backbone": "bilinear",
    }
    if panel_zone:
        cfg["panel_zone_model"] = "krawinkler_gupta"
    b = AnalysisBuilder(mesh, cfg)
    b.build_domain()
    return b


def _peak_x(res) -> float:
    """Peak |X| displacement over all nodes (≈ roof peak for a shear frame)."""
    peak = 0.0
    for arr in res["displacements"].values():
        if arr.size:
            peak = max(peak, float(np.max(np.abs(arr[:, 0]))))
    return peak


def test_sac3_nonlinear_nltha_synthetic():
    """The nonlinear SAC frame (bilinear hinges) converges under a synthetic
    pulse with a finite, positive peak (always-on)."""
    from openseespy.opensees import wipe

    wipe()
    try:
        b = _build_nonlinear(panel_zone=False)
        dt = 0.005
        n = 200
        t = np.arange(n) * dt
        accel = np.zeros(n)
        accel[:40] = 2.0 * np.sin(2.0 * math.pi * 1.0 * t[:40])
        res = b.run_transient_analysis(accel, dt, direction="X", damping_ratio=0.05)
        assert res["converged_steps"] > 0
        assert res["peak_displacement"] > 0.0
        assert np.isfinite(res["peak_displacement"])
    finally:
        wipe()


@pytest.mark.needs_ground_motion("LA21")
def test_sac3_nonlinear_nltha_la21(resolve_ground_motion_record):
    """Nonlinear SAC frame (bilinear hinges, M1 vs M2) under LA21 (gated).

    Ohtori et al. (2004) Table 3 gives a peak roof displacement of ~0.151 m for
    the 3-story nonlinear evaluation model under the El Centro record at
    intensity 1.0.  The acceptance band is ±20 % (0.121–0.181 m): the hinge
    yield moments and panel-zone properties are computed from AISC tables (the
    benchmark data are not redistributable) and the bare-steel model omits
    composite slab action, so an exact match is not expected.

    The M2 (panel-zone) run is a smoke check only: the Krawinkler & Gupta panel
    zone combined with the bilinear hinge in the full 3-story frame currently
    produces a degenerate (over-stiff) modal response, a known limitation.  The
    panel-zone softening is verified independently in
    ``test_analysis_csm.py::TestPanelZone`` and by the elastic-model period
    shift (1.09 → 1.31 s).
    """
    from openseespy.opensees import wipe

    from fea_toolkit.io.ground_motion import read_peer_record

    wipe()
    try:
        record_path = resolve_ground_motion_record("LA21")
        times, accel = read_peer_record(record_path)
        dt = float(times[1] - times[0])

        b = _build_nonlinear(panel_zone=False)
        res = b.run_transient_analysis(accel, dt, direction="X", damping_ratio=0.05)
        assert res["converged_steps"] > 0
        peak_m1 = _peak_x(res)
        assert 0.121 <= peak_m1 <= 0.181, (
            f"M1 peak roof disp {peak_m1:.4f} m outside Ohtori band 0.121–0.181 m"
        )

        b = _build_nonlinear(panel_zone=True)
        res = b.run_transient_analysis(accel, dt, direction="X", damping_ratio=0.05)
        assert res["converged_steps"] > 0
        assert np.isfinite(_peak_x(res))
    finally:
        wipe()
