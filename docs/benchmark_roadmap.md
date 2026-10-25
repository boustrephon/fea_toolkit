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
- **A1 — trilinear backbone (done).** `benchmarks/sac_member_data.py` transcribes
  `mat_tbl` from `struct3/9/20.m` (EI1/EI2/EI3 + EA/GA + break curvatures d1/d2 +
  splice rows), verified byte-for-byte by `tests/test_benchmark_sac_member_data.py`.
  `sac.enrich_trilinear()` stamps those backbones onto the sections, and
  `opensees/_elements.py` gained a `hinge_backbone="trilinear"` lumped-hinge
  `Hysteretic` branch (6EI/L κ→θ), tested by
  `tests/test_benchmark_sac_trilinear.py`.  The trilinear variant is exposed as
  `backbone="trilinear"` on `builder_config()` / `load_model()` / `build_builder()`
  (the bilinear M1/M2 default is unchanged) with a modal/yield anchor test.  The
  lumped-hinge trilinear is the chosen A1 deliverable (topology-consistent with
  M1/M2); the more faithful `beamWithHinges` one-component mapping is deferred —
  escalate only if the A4 comparison drifts outside ±5–10 % (see P40).
- **A2 — 9/20-story models (done).** `load_model(building=9|20)` /
  `build_builder(building=9|20)` build the Ohtori models programmatically from
  `struct9/20.m` (geometry, seismic masses, and the base fixity — a pinned base
  at the bottom basement level plus a lateral tie at the ground level), with
  sections named `mat{n}` carrying `A = EA/E` / `I = EI1/E`.  `enrich_trilinear`
  / `enrich_display_sections` are generalised to the per-building `element_tbl`;
  `sac_member_data.py` gains `COLUMN_MATERIALS` / `BEAM_MATERIALS` index dicts.
  Anchored by `tests/test_benchmark_sac920.py`: the 9-story reproduces all five
  frequencies to ≤ 0.4 % and the 20-story all ten to ≤ 0.25 % (tighter than the
  `fema355c` generator's B1 anchor, because the rigidities are the benchmark's
  own `EI1`).  The rigid floor diaphragms (`slv_tbl` master-slave) are not
  modelled — the periods match without them.  Left for the nonlinear comparison
  (A3/A4): the `krawinkler_gupta` panel-zone path and the 9-story gravity-bay
  pin under `hinge_model='lumped'` (the lumped hinges re-point the member ends
  and win over the release).
- **A3 — NLTHA + peak extraction (recorders landed; P-Δ / Rayleigh confirmed).**
  `run_transient_analysis` now records the seven peak quantities — drift (from
  `displacements`), per-node velocity / acceleration, base shear (global
  equilibrium `Σ m·(a_rel + ag)`), and φmax / Emax / Nd from the lumped hinges
  (see P41 in `_pending_work.md`).  Settings confirmed: Newmark β=1/4 γ=1/2,
  dt=0.005 s, Rayleigh ζ=2 % at mode 1 + `nCutOff` (mode 2 for 3/9-story, mode 5
  for 20-story — pass `period_2 = T5` explicitly); the benchmark applies **no
  P-Δ** (linear stiffness, `geomTransf Linear`).  The remaining piece is the A4
  comparison itself.
- **A4 — comparison (preferred `beamWithHinges` hinge landed; matrix can resume).**
  The ``unctrl#.mat`` targets are transcribed
  (`benchmarks/sac_response_data.py`; ``tests/test_benchmark_sac_response_data.py``),
  and the case ordering is resolved (``Run_NLBM.m``: El Centro 0.5/1.0/1.5,
  Hachinohe 0.5/1.0/1.5, Northridge 0.5/1.0, Kobe 0.5/1.0).

  Two conventions, both verified against the benchmark sources:

  * **`F_max` is the whole-building base shear.**  ``EvalNLBM.m``'s ``mi`` is
    the FEMA 355C storey mass, exactly 2× the per-frame ``seismic_mass#`` in
    ``struct#.m`` (``Num_MRF = 2``) — to within 0.15 % (kip-sec²/ft rounding).
    The 2-D model simulates one N-S MRF at half the storey mass, so a single
    planar frame's base shear must be **doubled** to compare to `F_max`.
  * **`dt_cal = 0.005 s`.**  ``struct#.m`` integrates at 0.005 s (Newmark
    β=1/4 γ=1/2), sub-stepping the records' native 0.02 s sampling.
    ``benchmarks.sac.load_benchmark_record`` resamples to ``SAC_DT_CAL``;
    integrating at 0.02 s inflated the base shear ~55 % and `xdd_max` ~25 %.

  The first comparison exposed a **cyclic-softening bug** in the trilinear
  hinge (``zeroLengthSection``+Aggregator+``equalDOF`` lost elastic stiffness
  on reversal) — **fixed** by a ``zeroLength`` hinge with ``-mat``/``-dir``.
  The transient solver also needed ``NewtonLineSearch`` (plain Newton died at
  the trilinear yield corners).

  **Preferred hinge — `beamWithHinges`.**  ``hinge_model='beam_with_hinges'``
  (trilinear only): an elastic interior (E, A, Iz, Iy, G, J) with a trilinear
  moment-*curvature* `Uniaxial` section (strong-axis Mz) at each end, Lp = L/6
  (the benchmark's θ = φ·L/6 member-end rotation).  No "nearly rigid" spring,
  so the elastic stiffness is the exact 6EI1/L (T1 0.993 s vs elastic 0.994 s).

  **Four hinge formulations** now share the one transcribed trilinear backbone
  (see `sac_trilinear_hinge_approaches.md`):

  * `hinge_model='lumped'` + `hinge_backbone='trilinear'` — zero-length
    rotational `Hysteretic` springs;
  * `hinge_model='beam_with_hinges'` + `hinge_backbone='trilinear'` — the
    one-component element with an end `Hysteretic` M-φ section;
  * `hinge_model='beam_with_hinges'` + `hinge_backbone='trilinear_steel02'` —
    the same element, but the end M-φ section is **two `Steel02` springs in
    parallel** (the Giuffré–Menegotto–Pinto transition smooths the trilinear
    corners);
  * `hinge_model='beam_with_hinges'` + `hinge_backbone='trilinear_clough'` —
    the same `Hysteretic` hinge with Clough/Takeda unloading-stiffness
    degradation (β=0.5).

  **El Centro 1.0 (3-story, dt=0.005)**, lumped / bwh+Hysteretic /
  bwh+Steel02 / bwh+Clough (target in parentheses): roof 0.153 / 0.149 /
  0.145 / 0.126 m (0.151), base shear ×2 10.75 / 12.03 / 11.55 / 11.91 MN
  (10.12), xdd 6.99 / 6.88 / 6.47 / 6.89 m/s² (6.54), Nd 22 / 22 / 22 / 16
  (22).  The Clough run is the decisive control: changing *only* the cyclic law
  moves displacement and yielding (roof −17 %, Nd 16) but **not** the base
  shear (+18 %), so the base shear is set by the **backbone / elastic
  stiffness**, not the unloading/reloading rule.  The Lp hypothesis was
  **retracted** (a L/10→L/3 sweep moved base shear <1 %), as was the
  floor-diaphragm hypothesis (`equalDOF` added nothing — the rigid-axial beams
  already tie the floors).  What remains is ``Nonlin12``'s yield-index α
  interpolation, which smooths the elastic→plastic *transition* (a monotonic
  backbone effect, not a cyclic one) and lowers the secant stiffness once a
  member yields.  Run the full 10×3 matrix next.
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
