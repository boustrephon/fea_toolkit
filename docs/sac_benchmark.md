---
title: "SAC 3-Story Frame Benchmark (T2)"
description: "SAC 3-story steel moment frame (Ohtori et al. 2004): elastic modal periods against the published evaluation-model frequencies."
status: "complete"
tags: [validation, benchmark, sac, moment-frame, modal, tcl]
category: [planning]
related: [benchmark_register.md, tcl_import.md, shear_building_benchmark.md, sac_nonlinear_benchmark.md]
---

# SAC 3-Story Frame Benchmark (T2)

## Purpose

The three-story SAC steel moment frame (LA3) anchors the elastic modal path
against a published, independently-computed result, and exercises the Tcl →
`SAPModelData` importer end-to-end.

## Model

Transcribed from Ohtori et al. (2004), Figure 1 / benchmark data:

- N-S perimeter MRF, **4 bays × 9.144 m**, **3 stories × 3.9624 m**, fixed bases.
- **Columns** (345 MPa): W14×257 (exterior lines), W14×311 (interior lines), plus a
  leaning column at the far line (models the interior gravity framing for P-delta).
- **Beams** (248 MPa), per the benchmark schedule — W33×118 (level 1), W30×116
  (level 2), W24×68 (roof), W21×44 (fourth bay); bare-steel inertia, rigid axial
  (EA = 1e10) for the floor diaphragm.
- E = 2.0e11 Pa.  Seismic mass per level, one frame (half the building):
  L1 = L2 = 4.7835e5 kg, L3 (roof) = 5.1779e5 kg, lumped at the floor nodes by
  tributary bay width.

## Reference

Ohtori, Christenson, Spencer & Dyke (2004), *ASCE J. Eng. Mech.* 130(4):366,
DOI 10.1061/(ASCE)0733-9399(2004)130:4(366).  First two natural frequencies of
the 3-story evaluation model: **0.99 Hz and 3.06 Hz** (periods **1.01 s** and
**0.327 s**).

## Acceptance

T2 — published code verification.  The test asserts the first two periods
against the reference within **±3 %**.

### Member schedule

Transcribed from the official benchmark model data (`struct3.m`): the beams are
per-level (W33×118 / W30×116 / W24×68) with a lighter W21×44 fourth bay, the
columns are W14×257 (exterior) / W14×311 (interior) with a leaning column, and
the lateral model uses bare-steel beam inertia (the composite slab is a
gravity-only effect).  This lands the first two periods on the published values
(1.01 s / 0.327 s) to within ±3 %.

## Implementation

`fea_toolkit/benchmarks/sac_3story.tcl` (Tcl source) + `tests/test_benchmark_sac3.py`.

## NLTHA (smoke anchor)

`tests/test_benchmark_sac3_nltha.py` runs the elastic frame through the native
Newmark transient runner.  An always-on synthetic-pulse case validates the full
Preprocessor → AnalysisBuilder → `run_transient_analysis` pipeline; a gated
`@pytest.mark.needs_ground_motion("LA21")` case runs the real SAC LA record and
skips when the mirror is empty.

The *nonlinear* evaluation model (bilinear hinges + optional panel zone), its
NLTHA benchmark against Ohtori Table 3, and the generated figures are covered
in [SAC 3-Story Nonlinear Benchmark (T4)](sac_nonlinear_benchmark.md).
