---
title: "Benchmark & Validation Model Register"
description: "Master index of validation/benchmark models for fea_toolkit: structural-type × load-type coverage matrix, the benchmark tiers, per-type candidate sources (papers + datasets), the phased acquisition roadmap, and acceptance criteria."
status: "partial"
tags: [validation, benchmark, planning, pushover, nonlinear-dynamic, modal, response-spectrum, shear-wall, beam-column-joint, ground-motion]
category: [planning]
related: [vecchio_emara_benchmark.md, ground_motion_data.md, pushover_analysis.md, nonlinear_dynamic_analysis.md]
---

# Benchmark & Validation Model Register

Living index of the benchmark models the toolkit is validated against, and the
plan for closing the remaining gaps.  The goal is **one paper-backed model per
matrix cell first, then three-to-five per nonlinear type** for statistical
confidence.

## 1. Benchmark tiers (what "validated" means)

| Tier | Source | Proves | Numeric target |
|---|---|---|---|
| **T1 — Closed form** | Beam theory, Euler, Przemieniecki/Paz, ACI joint shear | Solver correctness, units, DOF | ≈ 0% (solver tol) |
| **T2 — Published code verification** | Textbook / verification problems (cited, not reproduced) | Full linear workflow vs an independent result | ±1–3% |
| **T3 — Experimental database** | PEER columns, Thomsen & Wallace walls, joint subassemblages | Nonlinear material/element fidelity | ±10–15% |
| **T4 — Published FE model** | SAC frames, SP3 / Haselton–Deierlein, SPEAR | Cross-code / cross-platform equivalence | ±5–10%, or within NLTHA scatter |

Every load type needs at least one **T1/T2 anchor** (cheap, catches regressions)
plus one **T3/T4** where nonlinear physics is claimed.

## 2. Coverage matrix

Legend: ● have a real benchmark · ◐ exercised vs synthetic/semi-analytical only · ○ empty

| Type | Static grav | Static lateral | Modal | RS | Pushover | NLTHA | IDA |
|---|---|---|---|---|---|---|---|
| Vertical cantilever | ◐ | ◐ | ◐ | ◐ | ◐ | ◐ | ○ |
| Beam-column joint | ○ | ○ | ○ | ○ | ○ | ○ | ○ |
| Moment frame | ◐ | ◐ | ◐ | ◐ | ● V&E, Duong | ◐ | ○ |
| Shear wall | ◐ | ◐ | ○ | ○ | ◐ | ○ | ○ |

## 3. Candidate sources by structural type

### Vertical cantilever (T3 + T1)

- **PEER Structural Performance Database** — nisee.berkeley.edu/spd — 400+ cyclic
  RC column tests (geometry, materials, rebar, axial load, force–displacement
  history).  Reference: PEER Report 2006/04 (Berry/Parrish/Eberhard).
- **T1 anchor**: Euler critical load + tip deflection `PL³/(3EI)` on
  `make_sample_model()`.

### Beam-column joint (T3 + T1)

- **Lowes & Altoontash (2003)**, ASCE J. Struct. Eng. 129(12):1686,
  DOI 10.1061/(ASCE)0733-9445(2003)129:12(1686); companion PEER Report 2003-10.
  Interior/exterior subassemblage tests with load–deformation histories.
- **T1 anchor**: Park & Paulay (1975) joint-shear `Vjh`.
- **Joint modelling** is a two-track workstream (see §5): rigid panel zones
  (steel + RC) *and* a full RC joint element.

### Moment frame (T4 + T2)

- **SAC steel frames (3/9/20-story)** — Ohtori et al. (2004), ASCE J. Eng. Mech.
  130(4):366, DOI 10.1061/(ASCE)0733-9399(2004)130:4(366); design by Krawinkler &
  Gupta (1998).  Publishes natural frequencies (modal T2 anchor) + NLTHA + pushover.
- **Haselton & Deierlein (2007) / SP3 Risk** — sp3risk.com — 30+ RC SMF OpenSees
  models (1–20 stories) with pushover / NLTHA / IDA.
- **SPEAR project RC frame** (Eurocode 8) — alternative RC NLTHA benchmark.
- **Vecchio & Emara (1992)** and **Duong et al. (2007)** — already implemented
  (see vecchio_emara_benchmark.md and shear_failure_modelling.md).

### Shear wall (T3)

- **Thomsen & Wallace RW2** — canonical OpenSees wall benchmark (3.66 m tall,
  102 mm, 1.22 m web); parameters tabulated in the OpenSees Days 2014 concrete
  walls workshop.
- **UoA-UW RC Wall Database** — Shegay et al. (2021), DesignSafe DOI
  10.17603/ds2-r12q-t415 (142 walls).
- **UCLA-RCWalls** (Abdullah 2019) — 1100+ walls.

### Modal + Response-spectrum closed-form anchors (T2)

- Przemieniecki (1968) stiffness, Paz (1985) modal, Wilson et al. (1981) CQC,
  applied to a three-story plane frame (El Centro 1940 N-S, 5% damping).  Build
  our own replica; cite CSI verification examples rather than reproducing their
  tables (see .clinerules §5.8).

## 4. Phased roadmap

- **Phase 1 — one benchmark per cell (anchors).**  Euler/`PL³/(3EI)` cantilever,
  Przemieniecki/Paz 3-story modal + CQC RS, one PEER column, SAC 3-story (modal
  + NLTHA + panel-zone A/B), Lowes & Altoontash interior joint, Thomsen &
  Wallace RW2.  Includes the Tcl → `SAPModelData` importer that unlocks the SAC
  models.
- **Phase 2 — widen nonlinear cells to 3–5.**  Additional PEER columns, joint
  subassemblages, 9-story SAC / SP3 RC SMF, and 2–4 walls across aspect ratios.
- **IDA — deferred.**  The IDA driver does not exist yet; gather the record-set
  manifest now (cheap) and validate once a driver lands.

## 5. Joint-modelling workstream (two tracks)

- **Track 1 — rigid panel zones** (steel + RC): rigid offsets at beam/column
  faces + optional panel-zone shear stiffness.  Benchmark: SAC M1 vs M2
  (Krawinkler & Gupta 1998) — M2 models the panel zone explicitly.
- **Track 2 — RC joint element**: the Lowes–Altoontash joint (OpenSees
  `beamColumnJoint`), with a data model for its calibration parameters.
  Benchmark: Lowes & Altoontash (2003) subassemblages.

## 6. Repository conventions

- One `docs/<name>_benchmark.md` per benchmark (acceptance band, units,
  limitations) — see vecchio_emara_benchmark.md.
- One `tests/test_<name>_benchmark.py` (fixture + `ops.wipe()`; synthetic
  builders stay local to the test file).
- 2D models (SAC, joints) are test-only; production stays 3D-only (§3.11).
- Ground-motion waveforms are never committed; see ground_motion_data.md.

## 7. How to add a benchmark

1. Pick the cell and tier; confirm the source (paper + DOI + dataset URL).
2. Add a provenance card + `docs/<name>_benchmark.md`.
3. Convert the model to `SAPModelData → MeshModel` (native `.s2k`/`.e2k`, Tcl
   importer, or specimen builder).
4. Add `tests/test_<name>_benchmark.py` with the tier's acceptance band.
5. Update this register's matrix and catalogue.