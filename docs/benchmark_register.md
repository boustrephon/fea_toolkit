---
title: "Benchmark & Validation Model Register"
description: "Master index of validation/benchmark models for fea_toolkit: benchmark tiers, the structural-type × load-type coverage matrix, the implemented-benchmark catalogue, reference collections, and acceptance criteria. Candidate sources and the ranked build order live in benchmark_roadmap.md."
status: "partial"
tags: [validation, benchmark, planning, pushover, nonlinear-dynamic, modal, response-spectrum, shear-wall, beam-column-joint, ground-motion, masonry, infill, collapse, precast, braced-frame, composite]
category: [planning]
related: [benchmark_roadmap.md, vecchio_emara_benchmark.md, duong_benchmark.md, ground_motion_data.md, pushover_analysis.md, nonlinear_dynamic_analysis.md]
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
| Steel moment frame | ○ | ○ | ● SAC | ○ | ○ | ● SAC | ○ |
| RC moment frame | ◐ | ◐ | ◐ | ◐ | ● V&E, Duong | ○ | ○ |
| Shear wall | ◐ | ◐ | ○ | ○ | ◐ | ○ | ○ |
| Masonry / URM | ○ | ○ | ○ | ○ | ○ | ○ | ○ |
| Precast / post-tensioned | ○ | ○ | ○ | ○ | ○ | ○ | ○ |
| Braced frame | ○ | ○ | ○ | ○ | ○ | ○ | ○ |
| Composite (CFT) | ○ | ○ | ○ | ○ | ○ | ○ | ○ |

## 3. Implemented benchmarks

| Benchmark | Type | Tier | Doc | Test |
|---|---|---|---|---|
| Euler cantilever `PL³/(3EI)` | vertical cantilever · static | T1 | [cantilever_benchmark.md](cantilever_benchmark.md) | `test_benchmark_cantilever.py` |
| Przemieniecki/Paz shear building + CQC | shear building · modal/RS | T1 | [shear_building_benchmark.md](shear_building_benchmark.md) | `test_benchmark_shear_building.py` |
| SAC 3/9/20-story frame — modal | steel moment frame · modal | T2 | [sac_benchmark.md](sac_benchmark.md) | `test_benchmark_sac3.py`, `test_benchmark_sac920.py` |
| SAC 3/9/20-story frame — NLTHA | steel moment frame · NLTHA | T4 | [sac_nonlinear_benchmark.md](sac_nonlinear_benchmark.md) | `test_benchmark_sac3_nltha.py`, `test_benchmark_sac920.py` |
| Vecchio & Emara (1992) frame | RC moment frame · pushover | T3 | [vecchio_emara_benchmark.md](vecchio_emara_benchmark.md) | `test_rc_benchmark.py` |
| Duong et al. (2007) shear-critical frame | RC moment frame · pushover | T3 | [duong_benchmark.md](duong_benchmark.md) | `test_duong_benchmark.py` |
| RC wall pushover (LayeredShell + MVLEM) | shear wall · pushover | — | [mvlem_wall_analysis.md](mvlem_wall_analysis.md), [shell_support.md](shell_support.md) | `test_wall_pushover.py` |

> `—` = a synthetic exercise, not a T-tier benchmark (matrix cell ◐); its docs
> are the feature pages rather than a `_benchmark.md`.

Supporting code, not separate benchmarks: the SAC data generators
(`benchmarks/fema355c.py`, `sac_member_data.py`, `sac_response_data.py`), the
SAC record/response manifests, and the Elwood column limit-state checks
(`test_limit_state_columns.py`).

## 4. Candidate sources & build order

> The ranked build order (SAC-derived → RC frames → RC shear walls →
> masonry/URM), the per-workstream phasing and the DOI-laden candidate
> inventory (columns, joints, frames, walls, URM, PRESSS, BRBF, CFT) live in
> [`benchmark_roadmap.md`](benchmark_roadmap.md).  This register keeps the
> coverage matrix (§2) and the implemented catalogue (§3); the roadmap owns the
> candidates and the sequence.  The two-track joint-modelling workstream
> (rigid panel zones / RC joint element) is specified there too.

## 5. Reference collections & cross-code anchors

- **Sandia Sierra verification manuals** — Sierra/SolidMechanics (SAND2026-21931)
  and Sierra/SD (SAND2025-12350) publish analytic closed-form structural-dynamics
  problems (beam/plate natural frequencies, static deflections, transient) with
  nightly-checked reference results — cite as cross-code T2 anchors.  The Sierra/SD
  Example Problems Manual (SAND2025-12345) ships Exodus-based models (a TET10
  Titanium "fixture" + eigen/static/transient decks); deferred — 3D solid, out of
  the frame/wall scope (notes in local/references/sandia/README.md).
- **OpenSeesPy docs + examples** — `zhuminjie/OpenSeesPyDoc` (the official
  OpenSeesPy documentation; its `pyExamples/` tree covers structure, earthquake,
  tsunami, geotech, thermal) — a large collection of runnable OpenSeesPy models.
- **Namadchi's OpenSeesPy-Examples** (`AmirHosseinNamadchi/OpenSeesPy-Examples`,
  MIT) — pythonic structural models: 2-story steel MRF (gravity/modal/pushover/
  NLTHA), 1-story RC 3D frame, elastoplastic SDOF, response spectra, shallow
  dome, arch snap-through.  Useful cross-code checks for model-building
  conventions.

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