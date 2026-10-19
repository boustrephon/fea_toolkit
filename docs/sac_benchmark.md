---
title: "SAC 3-Story Frame Benchmark (T2)"
description: "SAC 3-story steel moment frame (Ohtori et al. 2004): elastic modal periods against the published evaluation-model frequencies."
status: "complete"
tags: [validation, benchmark, sac, moment-frame, modal, tcl]
category: [planning]
related: [benchmark_register.md, tcl_import.md, shear_building_benchmark.md]
---

# SAC 3-Story Frame Benchmark (T2)

## Purpose

The three-story SAC steel moment frame (LA3) anchors the elastic modal path
against a published, independently-computed result, and exercises the Tcl →
`SAPModelData` importer end-to-end.

## Model

Transcribed from Ohtori et al. (2004), Figure 1 / benchmark data:

- N-S perimeter MRF, **4 bays × 9.144 m**, **3 stories × 3.9624 m**, fixed bases.
- **Columns W24×68** (A = 1.290e-2 m², I = 7.617e-4 m⁴), 345 MPa steel.
- **Beams W30×116** (A = 2.206e-2 m², I = 2.052e-3 m⁴), 248 MPa steel, modelled **bare**.
- E = 2.0e11 Pa.  Seismic mass per level, one frame (half the building):
  L1 = L2 = 4.785e5 kg, L3 (roof) = 5.20e5 kg, lumped at the floor nodes by
  tributary bay width.

## Reference

Ohtori, Christenson, Spencer & Dyke (2004), *ASCE J. Eng. Mech.* 130(4):366,
DOI 10.1061/(ASCE)0733-9399(2004)130:4(366).  First two natural frequencies of
the 3-story evaluation model: **0.99 Hz and 3.06 Hz** (periods **1.01 s** and
**0.327 s**).

## Acceptance

T2 — published code verification.  The test asserts the first two periods
against the reference within **±10 %**.

### Composite-action caveat

Ohtori's frequencies are for the *composite* beam-and-slab evaluation model;
the fixture models the beams as bare steel, whose effective inertia is ~50-60 %
lower, so the centreline model overestimates the periods by ~8 %
(1.09 s / 0.35 s vs 1.01 s / 0.327 s).  The ±10 % band brackets this documented
gap rather than claiming a T2-tight ±3 %; the closed-form cantilever and
shear-building anchors cover the tight solver-accuracy tier.

## Implementation

`tests/fixtures/sac_3story.tcl` (Tcl source) + `tests/test_benchmark_sac3.py`.

## NLTHA (smoke anchor)

`tests/test_benchmark_sac3_nltha.py` runs the elastic frame through the native
Newmark transient runner.  An always-on synthetic-pulse case validates the full
Preprocessor → AnalysisBuilder → `run_transient_analysis` pipeline; a gated
`@pytest.mark.needs_ground_motion("LA21")` case runs the real SAC LA record and
skips when the mirror is empty.

Ohtori et al. (2004) Table 3 reports a peak roof displacement of ~0.151 m for
the *nonlinear* 3-story evaluation model under the El Centro record at
intensity 1.0.

## Nonlinear evaluation model (T4)

`tests/test_benchmark_sac3_nltha.py` also builds the **nonlinear** evaluation
model: concentrated-plasticity **bilinear steel hinges** (Steel01, `My = Zx·fy`,
2 % hardening) at the member ends, with an optional **Krawinkler & Gupta panel
zone** at each beam-column joint.

- **Bilinear hinge** (`hinge_model='lumped'`, `hinge_backbone='bilinear'`):
  yield moment `My = Zx·fy` from AISC plastic section moduli (W24×68
  Zx = 2.524e-3 m³ → ≈ 870 kN·m at 345 MPa; W30×116 Zx = 6.194e-3 m³ →
  ≈ 1540 kN·m at 248 MPa).  The benchmark's exact hinge data are not
  redistributable, so these are computed from AISC tables.
- **Panel zone** (`panel_zone_model='krawinkler_gupta'`): a zero-length
  bilinear shear-rotation spring at each joint with the K&G backbone
  `My = 0.55·fy·dc·tp·db` and `Kθ = 0.95·G·tp·dc·db`.

The gated LA21 test asserts the **M1** (centreline joint, bilinear hinges only)
peak roof displacement within **±20 %** of 0.151 m — M1 lands at ≈ 0.159 m.
The **M2** (panel zone) run is a smoke check only: the K&G panel zone combined
with the bilinear hinge in the full 3-story frame currently produces a
degenerate (over-stiff) modal response, a known limitation.  Panel-zone
softening is verified independently in `test_analysis_csm.py::TestPanelZone`
and by the elastic-model period shift (1.09 → 1.31 s).
