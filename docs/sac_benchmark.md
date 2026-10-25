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
DOI 10.1061/(ASCE)0733-9399(2004)130:4(366).  First three natural frequencies of
the 3-story evaluation model: **0.99 Hz, 3.06 Hz and 5.83 Hz** (periods
**1.01 s, 0.327 s and 0.172 s**).

## Acceptance

T2 — published code verification.  The test asserts the first three periods
against the reference within **±3 %**.

### Member schedule

Transcribed from the official benchmark model data (`struct3.m`): the beams are
per-level (W33×118 / W30×116 / W24×68) with a lighter W21×44 fourth bay, the
columns are W14×257 (exterior) / W14×311 (interior) with a leaning column, and
the lateral model uses bare-steel beam inertia (the composite slab is a
gravity-only effect).  This lands the first two periods on the published values
(1.01 s / 0.327 s) to within ±3 %.

## Modal anchor — full frequency set

The SAC benchmark publishes complete natural-frequency sets for all three
buildings, and the toolkit now asserts the full set for each (B1):

| Building | Published natural frequencies (Hz) | Asserted |
|---|---|---|
| 3-story | 0.99, 3.06, 5.83 | ✅ 3 modes (`test_benchmark_sac3.py`) |
| 9-story | 0.443, 1.18, 2.05, 3.09, 4.27 | ✅ 5 modes (`test_benchmark_fema355c_modal.py`) |
| 20-story | 0.261, 0.753, 1.30, 1.83, 2.40, 2.44, 2.92, 3.01, 3.63, 3.68 | ✅ 10 modes (`test_benchmark_fema355c_modal.py`) |

The 9-story reproduces all five to within ±1 % and the 20-story all ten to
within ±1.5 % (measured, `fullGenLapack`).  The 20-story's closely-spaced
pairs (2.40/2.44, 2.92/3.01, 3.63/3.68 Hz) are **not** torsional 3D modes —
the planar N-S frame reproduces them all, so they arise within the 2D frame
itself (the benchmark evaluation models are 2D, `Num_DOF = 3 × Num_node`).

### The 3-story generator gap (fema355c vs the faithful transcription)

The unified :mod:`fea_toolkit.benchmarks.fema355c` generator also builds a
3-story model, but its third mode is ≈ 5.44 Hz — 6.8 % below Ohtori's
5.83 Hz — and it gains a spurious extra mode (≈ 5.67 Hz).  The faithful
`struct3.m` transcription in :mod:`fea_toolkit.benchmarks.sac` reproduces all
three (5.85 Hz faithful, 5.74 Hz simplified), so **the canonical 3-story modal
anchor is `sac.py`, not `fema355c`**.

The cause is the gravity-bay idealisation: `fema355c`'s 2D builder models the
fourth (gravity) bay with a lighter *released* beam (W18×35 / W16×26, pinned at
the gravity column), whereas `struct3.m` / `sac.py` use a W21×44 flexural beam
against a small-inertia leaning column.  The released bay decouples the gravity
column into its own low-frequency mode and softens the frame's third mode.
The `fema355c` 3-story still gets the first two modes right (0.99 / 3.06 Hz)
because its total lateral stiffness is well calibrated; only the higher-mode
stiffness *distribution* differs.  Closing this is a follow-up to the `fema355c`
3-story generator, not a physics gap.

## SAC widening roadmap

See [benchmark_roadmap.md](benchmark_roadmap.md) §2 — the canonical A0–A4
sequence: A1 (trilinear backbone), A2 (9/20-story models), A3 (NLTHA + peak
extraction), A4 (comparison); B4 (Phase-I base isolation) is deferred.

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
