---
title: "Benchmark & Validation Model Register"
description: "Master index of validation/benchmark models for fea_toolkit: structural-type × load-type coverage matrix, the benchmark tiers, per-type candidate sources (papers + datasets), the phased acquisition roadmap, and acceptance criteria."
status: "partial"
tags: [validation, benchmark, planning, pushover, nonlinear-dynamic, modal, response-spectrum, shear-wall, beam-column-joint, ground-motion, masonry, infill, collapse, precast, braced-frame, composite]
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
| Moment frame | ◐ | ◐ | ● SAC | ◐ | ● V&E, Duong | ● SAC | ○ |
| Shear wall | ◐ | ◐ | ○ | ○ | ◐ | ○ | ○ |
| Masonry / URM | ○ | ○ | ○ | ○ | ○ | ○ | ○ |
| Precast / post-tensioned | ○ | ○ | ○ | ○ | ○ | ○ | ○ |
| Braced frame | ○ | ○ | ○ | ○ | ○ | ○ | ○ |
| Composite (CFT) | ○ | ○ | ○ | ○ | ○ | ○ | ○ |

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
- **RC joint-shear database (Kim, LaFave & Song)** — 341 cyclic beam-column
  joint subassemblages that failed in joint shear, with per-specimen geometry,
  materials, axial load, loading protocol and measured joint shear
  stress–strain.  Kim, LaFave & Song (2009), *Mag. Concr. Res.* 61(2):119–132,
  DOI 10.1680/macr.2008.00068; Kim & LaFave (2008), *ACI Struct. J.*
  105(6):770–780, DOI 10.14359/20105; full database in Kim (2007), Newmark
  Report 020 (hdl.handle.net/2142/14281).
- **Full-scale joint series** — Melo, Pohoryles, Rossetto & Varum (2021),
  *MethodsX* 8 (PMC8374536) — full-scale cyclic beam-column joints with a
  complete monitoring scheme and digitised load/deformation data.

### Moment frame (T4 + T2)

- **SAC steel frames (3/9/20-story)** — Ohtori et al. (2004), ASCE J. Eng. Mech.
  130(4):366, DOI 10.1061/(ASCE)0733-9399(2004)130:4(366); design by Krawinkler &
  Gupta (1998), reported in full in Gupta & Krawinkler (1999), Stanford Blume
  Report TR-132 (`local/references/SAC/TR132_Gupta (Krawinkler).pdf`).  Publishes
  natural frequencies (modal T2 anchor) + NLTHA + pushover.
  Implemented as the elastic [SAC modal anchor](sac_benchmark.md) — full frequency
  sets asserted for all three buildings — and the nonlinear
  [SAC NLTHA benchmark](sac_nonlinear_benchmark.md) (bilinear hinges + K&G panel
  zone, M1 vs M2).  The 9/20-story nonlinear NLTHA widening is tracked in
  [benchmark_roadmap.md](benchmark_roadmap.md) §2.
- **SAC-derived — Krawinkler deterioration (IMK)** — Ibarra, Medina & Krawinkler
  (2005), DOI 10.1002/eqe.495; Lignos & Krawinkler (2011), DOI
  10.1061/(ASCE)ST.1943-541X.0000376; Ibarra & Krawinkler (2005), PEER 2005/06.
  The canonical backbone + energy-based cyclic-deterioration model (IMK) and its
  steel-component calibration database (>300 beam/column tests) — the next
  SAC-derived step toward collapse (see benchmark_roadmap.md §3).
- **Haselton & Deierlein (2007) / SP3 Risk** — sp3risk.com — 30+ RC SMF OpenSees
  models (1–20 stories) with pushover / NLTHA / IDA.
- **SPEAR project RC frame** (Eurocode 8) — alternative RC NLTHA benchmark.
- **Vecchio & Emara (1992)** and **Duong et al. (2007)** — already implemented
  (see vecchio_emara_benchmark.md and shear_failure_modelling.md).
- **DesignSafe infilled RC frames** — Bhat & Singh (2024), DesignSafe-CI DOI
  10.17603/ds2-4acg-tt80 — OpenSees models + responses for fly-ash-brick and
  AAC-block masonry-infilled RC frame buildings; reproduce the bare frame first,
  then add the diagonal-strut infill (Bhat, Singh & Agarwal 2024, DOI
  10.1016/j.engstruct.2024.118695).
- **E-Defense 10-storey** — Janevski, Kang & Isaković (2023), DOI
  10.1007/s10518-023-01734-7 — full-scale 10-storey RC frame+wall dual system
  (frame and wall directions) tested at E-Defense; macro-model cross-code anchor.
- **E-Defense 2010 4-story RC + PT buildings** — Nagae et al.; design and
  instrumentation in PEER Report 2011/104; results in Nagae et al. (2012, WCEE)
  and Tuna Değer et al. (2014), PURR DOI 10.4231/d33x83m2p.  Full-scale 4-story
  RC frame+wall (and post-tensioned) buildings shaken with JMA-Kobe to
  near-collapse; blind-prediction study.

### Shear wall (T3)

- **Thomsen & Wallace RW2** — canonical OpenSees wall benchmark (3.66 m tall,
  102 mm, 1.22 m web); parameters tabulated in the OpenSees Days 2014 concrete
  walls workshop.
- **FEMA P-695 / Marafi RC wall archetypes** — Gogus & Wallace (2015), DOI
  10.1061/(ASCE)ST.1943-541X.0001221 (20 special + 20 ordinary walls, pushover +
  IDA) and Marafi's `nassermarafi/SRCSWArchetypes` OpenSees generator (69
  archetypes, 4–40 storeys, Seattle).
- **SAFE/CASH squat walls** — 13 pseudodynamic squat RC walls (JRC Ispra); Bisch
  & Coin (2006), DOI 10.1007/s10518-006-9014-1; benchmark phases 1/2 Gallitre et
  al. (2019), SMiRT-25 (benchmark-cash.org).  Squat-wall shear envelopes for the
  MVLEM shear spring.
- **Hidalgo et al. (2005) Chilean shear-wall buildings** — 50 RC wall buildings
  (12/15/20/22/28 storeys) with published fundamental periods (Table 1) — the
  building-level wall-system benchmark; see
  [benchmark_roadmap.md](benchmark_roadmap.md) §1.
- **UCSD 7-story RC wall building slice** — Panagiotou, Restrepo & Conte
  (2011), *J. Struct. Eng.* 137(6), DOI 10.1061/(ASCE)ST.1943-541X.0000332
  (Phase I rectangular wall) and SSRP-07/08 (Phase II T-wall).  Full-scale
  7-story cantilever-wall slice, four California records incl. near-fault
  Sylmar; published measured NLTHA response + NEES/PCA blind-prediction
  contest.  Data: DesignSafe `NEES-2006-0203`, PURR DOI 10.4231/d35t3g04t.
- **UoA-UW RC Wall Database** — Shegay et al. (2021), DesignSafe DOI
  10.17603/ds2-r12q-t415 (142 walls).
- **UCLA-RCWalls** (Abdullah 2019) — 1100+ walls.

### Masonry / unreinforced masonry — URM (T3 + T4)

- **URM Benchmark Project** — Cattari & Magenes (2021), Bull. Earthquake Eng.
  20:1901–1936, DOI 10.1007/s10518-021-01078-0.  Six benchmark structures
  (BS1–BS6) with detailed input data for nonlinear static assessment
  (equivalent-frame and continuum models).
- **URM shake-table database** — Haindl, Smith & Beyer (2024), Earthquake
  Spectra, DOI 10.1177/87552930241268214.  69 shake-table URM buildings
  (≥1:4 scale): drifts, failure modes, dynamic-property evolution.
- **Deferred — Li et al. (2016) collapse test** — DOI
  10.1016/j.engstruct.2016.03.032, a 3-storey RC frame shake-table collapse;
  needs contact / large-displacement physics (see benchmark_roadmap.md §6).

### Precast / post-tensioned / self-centering (T4)

- **PRESSS 5-story precast concrete building** — Priestley, Sritharan, Conley &
  Pampanin (1999), *PCI Journal* 44(6):42–67, DOI 10.15554/pcij.11011999.42.67.
  60%-scale 5-story (2×2 bays): four ductile precast frame systems + a
  post-tensioned "jointed" wall system, pseudodynamic histories to ~4.5 % drift,
  self-centering response.  The PCI Phase-3 reports (Vol 3-3 design /
  instrumentation, 3-4 frame direction, 3-5 wall direction) at pci.org give
  geometry, materials, test procedures and measured response.

### Braced frame (T4)

- **BRBF — NIST First-Generation PBEE study, Vol. 4** — 4/8/16-story
  buckling-restrained braced-frame archetypes (ASCE 7) with full design and
  ASCE 41 nonlinear assessment.  URL:
  https://tsapps.nist.gov/publication/get_pdf.cfm?pub_id=922024.
- **BRBF — UT10 5-story hybrid benchmark** — Mojiri, Mortazavi, Kwon &
  Christopoulos (2021), *Earthq. Eng. Struct. Dyn.*, DOI 10.1002/eqe.3508 —
  multi-element pseudo-dynamic hybrid simulation of a 5-story BRBF with
  published experimental benchmark results.

### Composite — concrete-filled tube (T3)

- **CFT / CFST column databases** — Denavit composite-column database
  (mark.denavit.me/Composite-Column-Database/); Thai et al. (2020) CFST database
  of 3,208 tests, DOI 10.82444/warw.33817750.v1; NEES-MAST full-scale CFT
  beam-column series (Perea et al. 2010).  Full geometry, materials, cyclic
  protocols and force–deformation data.  Needs a CFT section + confined-concrete
  material (new physics).

### Modal + Response-spectrum closed-form anchors (T2)

- Przemieniecki (1968) stiffness, Paz (1985) modal, Wilson et al. (1981) CQC,
  applied to a three-story plane frame (El Centro 1940 N-S, 5% damping).  Build
  our own replica; cite CSI verification examples rather than reproducing their
  tables (see .clinerules §5.8).
- **Sandia Sierra verification manuals** — Sierra/SolidMechanics (SAND2026-21931)
  and Sierra/SD (SAND2025-12350) publish analytic closed-form structural-dynamics
  problems (beam/plate natural frequencies, static deflections, transient) with
  nightly-checked reference results — cite as cross-code T2 anchors.  The Sierra/SD
  Example Problems Manual (SAND2025-12345) ships Exodus-based models (a TET10
  Titanium "fixture" + eigen/static/transient decks); deferred — 3D solid, out of
  the frame/wall scope (notes in local/references/sandia/README.md).

## 4. Phased roadmap

> The ranked build order (SAC-derived → RC frames → RC walls → masonry/URM)
> lives in [benchmark_roadmap.md](benchmark_roadmap.md); the detailed
> workstreams (SAC widening, Krawinkler deterioration, RC frames, walls,
> masonry) are in its later sections.

- **Phase 1 — one benchmark per cell (anchors).**  Euler/`PL³/(3EI)` cantilever,
  Przemieniecki/Paz 3-story modal + CQC RS, one PEER column, SAC 3-story (modal
  + NLTHA + panel-zone A/B), Lowes & Altoontash interior joint, Thomsen &
  Wallace RW2.  Includes the Tcl → `SAPModelData` importer that unlocks the SAC
  models.
- **Phase 2 — widen nonlinear cells to 3–5.**  Additional PEER columns, joint
  subassemblages, 9-story SAC / SP3 RC SMF, and 2–4 walls across aspect ratios.
- **Phase 3 — collapse-level steel (SAC-derived).**  IMK deterioration backbone
  (Ibarra–Medina–Krawinkler; Lignos–Krawinkler calibration) replaces the ad-hoc
  Clough hinge; finish the SAC 9/20-story A4 comparison.
- **Phase 4 — RC frames, then walls, then masonry.**  DesignSafe bare → strut
  infill and E-Defense (10- and 4-story); UCSD 7-story + FEMA P-695/Marafi +
  SAFE/CASH + Hidalgo walls; the Kim–LaFave–Song joint database; URM Benchmark
  Project + URM shake-table database.
- **Phase 5 — new system types (deferred).**  PRESSS precast / PT, BRBF braced
  frames, CFT composite columns — once PT/self-centering, BRB and CFT machinery
  land.
- **IDA — deferred.**  The IDA driver does not exist yet; gather the record-set
  manifest now (cheap) and validate once a driver lands.

## 5. Joint-modelling workstream (two tracks)

- **Track 1 — rigid panel zones** (steel + RC): rigid offsets at beam/column
  faces + optional panel-zone shear stiffness.  Benchmark: SAC M1 vs M2
  (Krawinkler & Gupta 1998; Gupta & Krawinkler 1999, TR-132) — M2 models the
  panel zone explicitly.
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