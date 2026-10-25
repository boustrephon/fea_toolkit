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

> **Canonical plan.** This §2 is the single source of truth for the A0–A4
> widening; `_pending_work.md` P40/P41, `sac_benchmark.md` and
> `sac_nonlinear_benchmark.md` reference it rather than restate it.

See [sac_benchmark.md](sac_benchmark.md) for the modal-gap findings (B1).
Summary:

- **B1 — modal-anchor completion (done).** Assert the full published frequency
  set: 3-story (0.99/3.06/5.83 Hz), 9-story (5 modes), 20-story (10 modes).
The nonlinear NLTHA widening — matching Ohtori Table 3–5 for the 3/9/20-story
frames under the ten record cases — is planned as an A0–A4 sequence:

- **A0 — record sourcing (done).** El Centro / Hachinohe / Northridge / Kobe
  extracted from `Files_bldg_full3.zip` into the ground-motion mirror,
  manifests registered, gated PGA test (`tests/test_benchmark_sac_records.py`).
- **A1 — trilinear backbone (transcription ✅ · machinery ✅ · wiring pending).**
  `benchmarks/sac_member_data.py` transcribes `mat_tbl` from `struct3/9/20.m`
  (EI1/EI2/EI3 + EA/GA + break curvatures d1/d2 + splice rows), verified
  byte-for-byte by `tests/test_benchmark_sac_member_data.py`.  Landed
  2026-10-25: `sac.enrich_trilinear()` stamps those backbones onto the sections,
  and `opensees/_elements.py` gained a `hinge_backbone="trilinear"` lumped-hinge
  `Hysteretic` branch (6EI/L κ→θ), tested by `tests/test_benchmark_sac_trilinear.py`.
  **Remaining:** expose a first-class trilinear variant (`backbone=` on
  `builder_config`/`build_builder`/`load_model`; the bilinear M1/M2 path stays
  untouched) and add a modal/yield anchor test.  The lumped-hinge trilinear is
  the chosen A1 deliverable (topology-consistent with M1/M2); the more faithful
  `beamWithHinges` one-component mapping is deferred — escalate only if the A4
  comparison drifts outside ±5–10 % (see P40).
- **A2 — 9/20-story models (groundwork done; generators pending).** Build the
  9/20-story Ohtori models from `struct9/20.m` (geometry, seismic masses, base
  fixity, rigid floors, and the `element_tbl` member→backbone assignment).  The
  earlier "Zx/fy per W-section" framing is superseded — the benchmark defines
  members by backbone directly.  **Groundwork landed 2026-10-25:** in-code AISC
  W/box tables (`model/steel_sections.py`), `fema355c` column splices 6 ft above
  the floor centreline (2D + 3D), and `sac.enrich_display_sections()` +
  `MAT_TO_SECTION_{9,20}` maps (display-only shapes), plus the `element_tbl`
  transcribes already in `sac_member_data.py`.  **Remaining:** a
  `build_sac(building=9|20)` generator (transcribe geometry/masses/fixity/rigid
  floors — cite `local/257/_extract/*.m`, don't paste), generalise
  `enrich_trilinear`'s 3-story area/inertia matching to the per-building
  `element_tbl`, reuse `enrich_display_sections(building=…)` and the
  `krawinkler_gupta` panel-zone path, and anchor by elastic modal periods (B1
  already passes) before NLTHA.
- **A3 — NLTHA + peak extraction (not started).** Reproduce settings exactly
  (Newmark β=1/4 γ=1/2, dt=0.005 s, 100/180 s; Rayleigh ζ=2 % at mode 1 +
  `nCutOff` — mode 2 for 3/9-story, mode 5 for 20-story) and add the seven peak
  recorders (drift/accel/velocity/base-shear/φmax/Emax/Nd; see P41 in
  `_pending_work.md`).  Whether the benchmark applies P-Δ (vs Linear geometry)
  is still to be confirmed from the paper.
- **A4 — comparison (not started).** Parse `unctrl3/9/20.mat` as the
  authoritative targets, reconcile column ordering and `d_max`/`δmax`
  semantics, and assert within ±5–10 % — 3-story El Centro 1.0 first, then the
  full 10-case × 3-building matrix.
- **B4 — Phase-I base isolation — deferred.** Needs a base-isolation element
  path the toolkit does not have (friction pendulum / elastomeric isolator).

The 3-story panel-zone / plastic-modulus audit (2026-10-25) corrected the two
AISC plastic moduli (W14×257 `Zx` 448→487 in³, W24×68 154→177 in³) and kept the
column panel-zone `tw` as-is (~1/2 in ≈ 0.013 m over the bare web — a
non-documented doubler plate), documented rather than "corrected".  See
`sac_nonlinear_benchmark.md`.

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
