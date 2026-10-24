---
title: "Benchmark Roadmap — extended proposals"
description: "Extended proposals for closing the benchmark coverage gaps: the Chilean shear-wall benchmark (Hidalgo et al. 2005) and the wider use of the SAC buildings, plus the deferred VecTor / SeismoBuild / base-isolation candidates."
status: "partial"
tags: [validation, benchmark, planning, shear-wall, moment-frame, sac, nltha, modal]
category: [planning]
related: [benchmark_register.md, sac_benchmark.md, mvlem_wall_analysis.md, shell_support.md]
---

# Benchmark Roadmap — extended proposals

Living plan for the next benchmark work, expanding on
[benchmark_register.md](benchmark_register.md).  Two workstreams are the
priority: the **Chilean shear-wall benchmark** (a building-level wall-system
benchmark) and the **wider use of the SAC buildings** (higher modes + nonlinear
NLTHA for the 9- and 20-story frames).  The first implemented slice is the SAC
modal-anchor completion (B1), in `tests/test_benchmark_sac3.py` and
`tests/test_benchmark_fema355c_modal.py`.

## 1. Chilean shear-wall benchmark — Hidalgo et al. (2005) — priority 1

**Source.** Hidalgo, Gelmi, Nuñez & Magna (2005), *IX Chilean Conference on
Seismology* — `local/references/SAC/NA04-03 (Chile Shear Wall Models).pdf`.
Fifty RC shear-wall buildings (12/15/20/22/28 storeys) designed to NCh433 +
ACI 318.  Table 1 publishes the fundamental period T1 for every model
(0.51–2.37 s) — a ready modal acceptance matrix — plus elastic/inelastic drift
and collapse data for nonlinear validation.

**Four sweep parameters map onto generator knobs:**

| Parameter | Range | Generator knob |
|---|---|---|
| Wall density | 0.73–6.53 % | `wall_density` |
| Coupling-beam depth | 15 / 60 / 120 cm | `coupling_beam_depth` |
| Wall aspect ratio | 4 m vs 8 m walls | `wall_length` |
| Lower-storey stiffness reduction | — | `lower_storey_stiffness_reduction` |

**Figure 1 geometry (already extracted):** storey height 260 cm; walls
800×10/20/30/42.5/55/90 cm; columns 70×70; beams 30×60; coupling beam
30×(15/60/120) cm; slab 15 cm.

**Reference chain to resolve at/after A0.** Analytical wall model = Hidalgo,
Jordán & Martínez (2002), *Eng. Struct.* 24(1):85–98 (2D macro-model, bilinear
moment–curvature + trilinear shear-deformation envelopes).  Squat-wall shear
envelopes = Hidalgo, Ledezma & Jordán (2002), *Earthquake Spectra* 18(2):287–308.
Material properties (fc′, fy, reinforcement) are in refs [4]–[6], not the PDF —
the 2002 *Eng. Struct.* paper is the authoritative source.

**Element machinery — available but with caveats** (see
[`mvlem_wall_analysis.md`](mvlem_wall_analysis.md)): elastic `LayeredShell` +
`ShellMITC4` is validated; `MVLEM_3D` / `SFI_MVLEM_3D` (macro-element) is the
working nonlinear path (`ConcreteCM` + `Steel02` + `ElasticPP` shear);
smeared-plane-stress shell concretes and `SFI_MVLEM` (2D) are not available on
the shipped wheel, and `MVLEM_3D` has a documented geometric axial softness.
So this is a *data + generator* task that exercises existing elements, not new
physics.

**Phasing.**

- **A0 — data provenance.** Transcribe the 50-model table (storeys, wall plan,
  coupling-beam depth, sizes, T1) and resolve refs [4]–[6] for fc′/fy/rebar.
- **A1 — T2 modal anchor.** Elastic `LayeredShell` walls on a constant-stiffness
  subset (one wall density per storey count); assert T1 vs Table 1 at ±5 %
  (geometry + E + mass only — no reinforcement needed).
- **A2 — T2 sweep.** Full 50-model matrix vs Table 1.
- **A3 — T4 nonlinear.** `MVLEM_3D`/`SFI_MVLEM_3D` with the Hidalgo–Jordán–
  Martínez trilinear shear envelope; validate against the elastic-vs-inelastic
  roof-displacement ratios (Rd ≈ 1.6–2.2) and flexure-first collapse.
- **A4 (optional).** Pushover sweep across the aspect-ratio axis, cross-checked
  with Salonikios (2007).

**Architecture.** `src/fea_toolkit/benchmarks/chile_wall.py` (OpenSees-free,
mirrors `fema355c.py`): `build_chile_wall(...)`, `write_snapshots()`, a `MODELS`
data table; tests `tests/test_benchmark_chile_wall.py` (+ `_nltha.py`) drive it
through `preprocess_model → AnalysisBuilder → run_modal_analysis`.

## 2. Wider use of the SAC buildings — priority 2

See [sac_benchmark.md](sac_benchmark.md) for the modal-gap findings and the
detailed plan.  Summary:

- **B1 — modal-anchor completion (done).** Assert the full published frequency
  set: 3-story (0.99/3.06/5.83 Hz), 9-story (5 modes), 20-story (10 modes).
- **B2 — 9-story nonlinear NLTHA (T4).** Generalise `sac.py`'s bilinear-hinge +
  K&G panel-zone enrichment to the 9-story schedules in `fema355c.py`; validate
  peak roof displacement against Ohtori Table 3.
- **B3 — 20-story nonlinear NLTHA (T4, slow).** Same for the 20-story; mark
  `@pytest.mark.slow` and gate on the ground-motion mirror.
- **B4 — Phase-I base isolation — deferred.** Needs a base-isolation element
  path the toolkit does not have (friction pendulum / elastomeric isolator).

## 3. Other benchmarks — priority 3 (mostly deferred)

- **VecTor family** — jp68/69 (Guner & Vecchio pushover) and jp74 (cyclic
  shear-critical frames) are the natural next additions after jp16 (done);
  jp12 (Vecchio & Balopoulou) is a candidate.
- **SeismoBuild verification models** (ASCE 41-17 / EC8 / NTC-18) — `.sbf`
  native, need transcription; the EC8 wall/frame models complement the Chilean
  set.
- **NIST GCR 17-917-46v2** — reference only (ASCE 41 capacity models).

## 4. Cross-cutting conventions

- Generators live in `src/fea_toolkit/benchmarks/…` (never `local/`); PDFs and
  `local/257/_extract/*.m` are provenance only, cited from docstrings/docs.
- One `docs/<name>_benchmark.md` per implemented benchmark;
  `tests/test_<name>_benchmark.py` with `ops.wipe()` fixtures and synthetic
  builders local to the test file.
- SAC analysis benchmarks are planar (2D) 3D models (§3.11); production stays
  3D-only.
- Commit in two groups: `feat(benchmarks)` (generator + tests + docs) then
  `chore(benchmarks)` (regenerated JSON snapshots), ruff → restage → `--no-verify`.
