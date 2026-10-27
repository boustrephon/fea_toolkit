---
title: "Benchmark Roadmap — extended proposals"
description: "Canonical benchmark build plan: ranked order (SAC-derived first, then RC frames, RC walls, masonry/URM) with DOIs and prerequisites, plus the detailed workstreams and the component-level (T3) database inventory."
status: "partial"
tags: [validation, benchmark, planning, shear-wall, moment-frame, sac, nltha, modal, masonry, rc-frame, collapse]
category: [planning]
related: [benchmark_register.md, sac_benchmark.md, mvlem_wall_analysis.md, shell_support.md]
---

# Benchmark Roadmap — extended proposals

> **Status: ⚠️ Partial.** The ranked build order and per-workstream phasing
> are complete; the candidate inventory keeps growing as sources are verified.

Canonical build plan for benchmark work: the ranked order, per-workstream
phasing and the candidate inventory (with DOIs) live here.  The
[benchmark register](benchmark_register.md) keeps the tiers, the coverage
matrix and the implemented-benchmark catalogue.  The build order — re-ranked
2026-10 after source verification — is **SAC-derived (Krawinkler deterioration)
first, then RC frames, then RC shear walls, then masonry/URM**; the ranked
sequence with DOIs and prerequisites is the section immediately below.  The
SAC widening (§2) is landed (B1 modal anchors + A0–A4 nonlinear); the
next SAC-derived step is the IMK deterioration model (§3).

## Integrated ranked sequence — SAC-derived first

Re-ranked 2026-10-27 after source verification.  Four clusters, in build
order; each later cluster needs progressively more new physics.  All DOIs are
verified and live in [references.bib](https://github.com/boustrephon/fea_toolkit/blob/main/papers/references.bib).

| # | Cluster | Benchmarks | Tier / cell | Needs |
|---|---|---|---|---|
| 1 | **SAC-derived (steel frames)** | Krawinkler deterioration (§3) | T4 / steel moment frame | nothing new |
| 2 | **RC frames** | DesignSafe infilled (bare → struts); E-Defense 10- and 4-story (§4) | T4 / RC moment frame | diagonal-strut infill |
| 3 | **RC shear walls** | UCSD 7-story; FEMA P-695 / Marafi; SAFE/CASH squat (§5); Hidalgo buildings (§1) | T2–T4 / shear wall | none (MVLEM/fiber landed) |
| 4 | **Masonry / URM** | URM Benchmark Project BS1–BS6; URM shake-table database (§6) | T3–T4 / masonry | masonry/URM physics |
| 5 | **New system types (deferred)** | PRESSS precast/PT; BRBF; CFT composite (§7) | T3–T4 / new rows | PT/self-centering, BRB, CFT section |

**Cluster 1 — SAC-derived (Krawinkler first).**  Replace the ad-hoc `Clough`
degradation hinge (β=0.5, loses convergence on 4/10 strong cases, §2 A4) with the
**Ibarra–Medina–Krawinkler (IMK)** backbone + energy-based cyclic deterioration,
calibrated from the **Lignos–Krawinkler steel-component database** (>300
beam/column tests).  Sources: Ibarra, Medina & Krawinkler (2005)
`10.1002/eqe.495`; Lignos & Krawinkler (2011) `10.1061/(ASCE)ST.1943-541X.0000376`;
Ibarra & Krawinkler (2005), PEER 2005/06.  (The SAC A4 comparison itself
landed 2026-10-26; this cluster is now the deterioration model only.)

**Cluster 2 — RC frames.**  Bhat & Singh (2024) `10.17603/ds2-4acg-tt80` ships
OpenSees models + responses for infilled RC frame buildings — reproduce the
**bare frame** first (no new physics), then the diagonal-strut infill (Bhat,
Singh & Agarwal 2024 `10.1016/j.engstruct.2024.118695`).  Janevski, Kang &
Isaković (2023) `10.1007/s10518-023-01734-7` is the E-Defense 10-storey RC
frame+wall dual system (frame and wall directions).

**Cluster 3 — RC shear walls.**  Gogus & Wallace (2015)
`10.1061/(ASCE)ST.1943-541X.0001221` (20 special + 20 ordinary walls) and
Marafi's `nassermarafi/SRCSWArchetypes` (69 archetypes, 4–40 storeys).  Squat
walls: 13 SAFE pseudodynamic walls (JRC Ispra), Bisch & Coin (2006)
`10.1007/s10518-006-9014-1`, CASH benchmark Gallitre et al. (2019).  Hidalgo
buildings stay the building-level anchor (§1).

**Cluster 4 — Masonry / URM.**  Cattari & Magenes (2021)
`10.1007/s10518-021-01078-0` (BS1–BS6 with full input data) and Haindl, Smith &
Beyer (2024) `10.1177/87552930241268214` (69 shake-table URM buildings).

**Deferred.**  Li, Zuo, Zhai, Xu & Xie (2016) `10.1016/j.engstruct.2016.03.032`
— 3-storey RC frame shake-table **collapse** test; needs contact/falling-debris
and large-displacement physics.  Revisit after the collapse/IDA driver lands.

**Component-level (T3) databases** — the anchors for the still-empty
component cells, before the building-level workstreams below:

- **PEER Structural Performance Database** — nisee.berkeley.edu/spd — 400+ cyclic
  RC column tests (geometry, materials, rebar, axial load, force–displacement
  history); reference PEER Report 2006/04 (Berry/Parrish/Eberhard).
- **Beam-column joints.**  Lowes & Altoontash (2003), *ASCE J. Struct. Eng.*
  129(12):1686, DOI `10.1061/(ASCE)0733-9445(2003)129:12(1686)` (companion PEER
  2003-10) — interior/exterior subassemblages; the Kim–LaFave–Song 341-joint
  shear database (Kim, LaFave & Song 2009, *Mag. Concr. Res.* 61(2):119–132, DOI
  `10.1680/macr.2008.00068`; Kim & LaFave 2008, *ACI Struct. J.* 105(6):770–780;
  Kim 2007, Newmark Report 020) and the Melo et al. (2021, *MethodsX* 8) full-scale
  series.  **Two tracks:** (1) rigid panel zones — SAC M1 vs M2, §2; (2) the
  Lowes–Altoontash `beamColumnJoint` RC joint element.

**New system types** (precast/PT, braced frame, composite CFT) are listed in §7
with their sources.  A 606 m mega-tall OpenSees model (Tian, Xie & Lu) is
available as a cross-platform reference (modal + El Centro), not a CI target.

## 1. Chilean shear-wall benchmark — Hidalgo et al. (2005) — priority 3

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

## 2. Wider use of the SAC buildings (Ohtori 2004) — priority 1

> **Canonical plan.** This §2 is the single source of truth for the A0–A4
> widening; `sac_benchmark.md`, `sac_nonlinear_benchmark.md` and
> `sac_trilinear_hinge_approaches.md` reference it rather than restate it.

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
  lumped-hinge trilinear is topology-consistent with M1/M2; the more faithful
  `beamWithHinges` one-component mapping is now implemented too (see A4 and
  `sac_trilinear_hinge_approaches.md`).
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
  own `EI1`).  The rigid floor diaphragms (`slv_tbl` master-slave) are opt-in
  under ``fidelity="faithful"`` — they move the base shear by ~0.004 % (the
  beam axial stiffness already ties each floor) and are off by default.  Left
  for the nonlinear comparison (A3/A4): the `krawinkler_gupta` panel-zone path
  and the 9-story gravity-bay pin under `hinge_model='lumped'` (the lumped
  hinges re-point the member ends and win over the release).
- **A3 — NLTHA + peak extraction (recorders landed; P-Δ / Rayleigh confirmed).**
  `run_transient_analysis` now records the seven peak quantities — drift (from
  `displacements`), per-node velocity / acceleration, base shear (global
  equilibrium `Σ m·(a_rel + ag)`), and φmax / Emax / Nd from the lumped hinges
  (archived as P41 in `history/_done_log.md`).  Settings confirmed: Newmark β=1/4 γ=1/2,
  dt=0.005 s, Rayleigh ζ=2 % at mode 1 + `nCutOff` (mode 2 for 3/9-story, mode 5
  for 20-story — pass `period_2 = T5` explicitly); the benchmark applies **no
  P-Δ** (linear stiffness, `geomTransf Linear`).  The A4 comparison itself is now complete — the ten-case and four-formulation
  matrices are run below.
- **A4 — comparison (done — preferred `beamWithHinges` hinge landed; ten-case + four-formulation matrices below).**
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
  bwh+Steel02 (target in parentheses): roof 0.153 / 0.149 / 0.145 m (0.151),
  base shear ×2 10.15 / 10.27 / 10.23 MN (10.12), xdd 7.09 / 6.95 / 6.54 m/s²
  (6.54), Nd 22 / 22 / 22 (22).  The bwh+Clough variant (D) fails to converge
  on El Centro 1.0 (and on 4 of the 10 cases overall), so it has no
  reproducible peak to compare; the three converged A/B/C forms cluster within
  ±4 % on base shear, which is set by the **backbone / elastic stiffness**, not
  the unloading/reloading rule.  An earlier +6–19 % base-shear
  gap was an artefact of the transient runner (the ground acceleration in
  `Σ m·(a_rel + ag)` was sampled one step too early); with the sampling
  corrected the base shear reproduces the target to ±4 %, so no unpublished
  `Nonlin12` interpolation is needed.  The 3-story ten-case matrix is run (see
  `sac_trilinear_hinge_approaches.md` and the nonlinear paper).

  **9/20-story NLTHA — `beamWithHinges` is unusable for the taller frames; the
  lumped hinge is the working path.**  The force-based `beamWithHinges` element
  inverts its 6×6 flexibility matrix, and that inversion is ill-conditioned at
  *both* stiffness extremes: on the 20-story the **soft** members (I33 ≲ 1.5e-3
  m⁴ — the 15×15 box columns but also the upper W columns and beams) fail at
  build (`ForceBeamColumn3d::getInitialStiff()` prints "could not invert
  flexibility"); on the 9-story the **stiff** basement columns (I33 ≈ 3.4e-3
  m⁴) build but die at step 7 of the transient.  Only the 3-story sits in the
  element's sweet spot.  The lumped hinge (`elasticBeamColumn` + `zeroLength`
  spring) is displacement-based and robust at both extremes, so the 9/20-story
  matrix runs `hinge_model='lumped'` + `backbone='trilinear'` (a ~4–5 % elastic
  softening vs the exact `beamWithHinges`).  A "hybrid" (`beamWithHinges` for
  the stiff members, lumped for the soft) was probed and abandoned — the
  soft-member set is broad enough that it collapses to the full lumped path,
  and the two hinge material-tag counters collide.  The 3-story **four-way
  matrix** (lumped / bwh+Hysteretic / bwh+Steel02 / bwh+Clough) is now run: B
  is the best (base shear −11…+7 %, drift −14…+48 %), and D (Clough, β = 0.5)
  is unstable — it loses convergence on 4 of the 10 strong cases.  The Rayleigh
  anchor periods are taken from the *elastic* model (ARPACK, milliseconds).
  The 9-story gravity-bay release is skipped under the lumped hinge (lumped
  hinges re-point the member ends and win over the release), as before.

  **20-story base-shear fix — the ground lateral tie's mass.**  The 20-story
  (and, to a lesser extent, the 9-story) base shear was over-predicted because
  the transient runner summed ``Σ m·(a_rel + ag)`` over *every* node,
  including the **ground lateral tie** level (the ``Fix_node`` prescription
  ties the top-of-basement level laterally, ``n_basements=2``).  That level is
  restrained in X, so its ``seismic_mass1`` (2.6561e5 kg) is anchored to the
  support and its ``m·ag`` is carried by the support, not by the building's
  lateral load path.  The runner now sums only over nodes with a free
  excitation DOF (``_runner_dynamic._free_dof_node_tags``), which removes that
  term: the 20-story base shear drops from +5…+52 % to −1…+29 % against the
  targets, with drift and ``xdd_max`` unchanged (the fix only touches the base-
  shear quantity).

  **Ground-motion records — verified byte-exact against the benchmark archive.**
  The four signals (``ELCENTRO``/``HACHINOHE``/``NORTHRIDGE``/``KOBE``) were
  compared to the benchmark's own ``*.mat`` files
  (``Files_bldg_full3.zip → MATLAB_Utility/*.mat``): El Centro, Hachinohe and
  Northridge are byte-identical; Kobe agrees to ~7 significant digits (the CSV
  transcription rounds the last decimal — max |Δ| ≈ 3e-7 m/s² on a PGA of
  8.18 m/s²).  ``dt`` (0.02 / 0.01), units (m/s²) and the linear intensity
  scaling (``Run_NLBM.m``'s ``intensity`` gain, 0.5 / 1.0 / 1.5) all match; the
  ``unctrl#.mat`` targets are transcribed byte-for-byte into
  ``sac_response_data.py``.  The records and targets are therefore locked down.

  **Below-ground basement mass — a second base-shear bug.**  ``EvalNLBM.m``'s
  ``F_max = Σ mi·xdd_i`` sums only the **above-ground** storeys (its ``mi``
  vector starts at the first floor above the ground tie), so the benchmark
  excludes the below-ground substructure.  The earlier fix only dropped the
  ux-fixed ground-tie nodes, so the 20-story's **free B-1 basement** level
  (``seismic_mass1`` = 2.6561e5 kg, below the ground tie but unrestrained)
  still contributed its ``m·ag`` rigid-body term.  ``_free_dof_node_tags`` now
  also drops nodes at or below the highest excitation-fixed elevation, matching
  the benchmark's above-ground-only definition.  Re-running the full 9/20-story
  matrix moves the 20-story base shear to **−8.5 % … +8.9 %** (was −0.6 … +28.7 %
  after the ground-tie fix alone): Kobe 1.0 +28.7 % → +8.9 %, Kobe 0.5
  −0.6 % → −5.9 %, Northridge 1.0 +7.3 % → −5.4 %, El Centro 1.5 +16.9 % →
  +2.7 % — the ~2.26e6 N B-1 rigid-body term accounted for ~20 % of the
  benchmark's ``F_max``.  The 9-story is unchanged (it has no free below-ground
  level).  Drift and ``xdd_max`` are untouched (the fix only changes the
  base-shear quantity), so the remaining Kobe-1.0 residual is a genuine ~+9 %
  base-shear / ~+44 % drift nonlinear-behaviour gap.

  **Floor-diaphragm hypothesis — refuted.**  The benchmark's ``slv_tbl``
  master-slave floor tie (a 1-DOF ``equalDOF`` on the horizontal X, not a full
  rigid diaphragm) was implemented as an opt-in ``fidelity="faithful"`` path
  (``_apply_floor_ux_ties`` / ``_floor_ux_tie_groups``) and tested on the
  20-story: it moves the base shear by **~0.004 %** while making the transient
  **~35× slower** (the ``Transformation`` handler condenses each of the 105
  master-slave ties).  The beam axial stiffness (``EA`` from ``mat_tbl``)
  already ties each floor near-rigidly, so the explicit tie is a second-order
  effect — and the ARPACK modal solve hangs with the ties active.  After the
  basement-mass correction the residual at Kobe 1.0 is a modest **+8.9 % base
  shear** against a **+44 % drift** — the drift, not the base shear, is the
  dominant gap.  It therefore lies in the **nonlinear hinge/backbone behaviour
  at high intensity**, not in the diaphragm, mass, damping or elastic
  stiffness — all of which have now been individually ruled out.  The trilinear
  post-yield segments (``EI2``/``EI3``) and yield moment (``My = EI1·d1``) at
  the 20-story member scale remain the next thing to scrutinise.

  **Material-model hypothesis — ruled out (kinematic + 3 % hardening, both).**
  The Ohtori paper describes the member model verbatim as *"a bilinear
  hysteresis model is used to characterize the nonlinear bending stiffness"* —
  the standard **kinematic** (Bauschinger) steel model, not isotropic
  hardening; the cyclic law itself lives in the compiled ``Nonlin12`` S-function
  (unpublished source).  The published ``mat_tbl`` backbone is uniform across
  every member: ``EI3/EI1 = 0.0300`` exactly (a **3 % work-hardening** slope
  beyond ``d2``), ``EI2/EI1 ≈ 0.905`` (the near-elastic "rounding" knee over the
  short ``d2/d1 ≈ 1.053`` range), and ``My = EI1·d1``.  The toolkit reproduces
  this backbone byte-for-byte and emits it as a **kinematic** ``Hysteretic``
  (Masing) or ``Steel02`` (Menegotto–Pinto) hinge with the same 3 % hardening —
  so there is **no difference in hardening type or magnitude**.  The only
  divergence is the *shape* of the yield transition (the benchmark's smooth
  yield-index α interpolation vs the sharp ``Hysteretic`` corner), which the
  ``Steel02`` formulation already smooths and which the 3-story four-way matrix
  showed to be a ±4 % base-shear effect, not ±9 %.  The Kobe-1.0 residual is
  therefore **not** the material model either.
- **B4 — Phase-I base isolation — deferred.** Needs a base-isolation element
  path the toolkit does not have (friction pendulum / elastomeric isolator).

The 3-story panel-zone / plastic-modulus audit (2026-10-25) corrected the two
AISC plastic moduli (W14×257 `Zx` 448→487 in³, W24×68 154→177 in³) and kept the
column panel-zone `tw` as-is (~1/2 in ≈ 0.013 m over the bare web — a
non-documented doubler plate), documented rather than "corrected".  See
`sac_nonlinear_benchmark.md`.

## 3. SAC-derived — Krawinkler deterioration models (collapse) — priority 1

The SAC lineage ends at the trilinear `Hysteretic` hinge with an ad-hoc `Clough`
degradation law (β=0.5), which loses convergence on 4 of the 10 strong cases
(§2 A4).  The canonical replacement is the **Ibarra–Medina–Krawinkler (IMK)**
phenomenological deterioration model — a monotonic backbone (yield, peak/capping,
post-capping negative stiffness, residual) plus an energy-based rule for three
cyclic-deterioration modes (basic strength, post-capping strength,
unloading/reloading stiffness) — calibrated from the **Lignos–Krawinkler steel
component database** (>300 wide-flange beam/column tests).

**Status (2026-10-27).** A1 machinery landed — `hinge_backbone="imk"` builds a
`IMKBilin` (modified IMK, **bilinear** hysteretic — the *steel* variant; note
`ModIMKPeakOriented` is the RC-flexure variant) strong-axis hinge in
`opensees/_elements.py`, wired through `sac.builder_config`.  A0 landed —
`lignos_krawinkler_2011()` computes the full `IMKBilin` set for **beams**
(Lignos–Krawinkler 2011, transcribed from Lignos' EPFL component tool +
PEER/ATC-72-1 §3.2.2) and **columns** (Lignos et al. 2019, adopted in the
ASCE 41-17 Errata; first-cycle envelope — the column cyclic-deterioration Λ is
left at 0).  A2 landed (single-member cyclic M-θ checks —
`tests/test_benchmark_sac_imk_cyclic.py`); A3 in progress (first increment:
`enrich_imk` + 3-story `backbone="imk"`, `tests/test_benchmark_sac_imk_frame.py`).
See `docs/imk_deterioration_model.md`.

Note: this "Krawinkler" thread (deterioration/collapse) is distinct from the
SAC frame *design* thread — Gupta & Krawinkler (1999), Stanford Blume Report
TR-132, the source of the SAC member schedules and the Krawinkler & Gupta
panel-zone model already landed in §2 / `sac_nonlinear_benchmark.md`.

**Sources.** Ibarra, Medina & Krawinkler (2005), *Earthq. Eng. Struct. Dyn.*
34(12):1489–1511, DOI `10.1002/eqe.495` (the IMK model); Lignos & Krawinkler
(2011), *J. Struct. Eng.* 137(11):1291–1302, DOI
`10.1061/(ASCE)ST.1943-541X.0000376` (modified-IK + RBS / non-RBS calibration
equations); Ibarra & Krawinkler (2005), PEER Report 2005/06 (collapse
methodology and SDOF/MDOF collapse capacities).

**Why first.**  No new topology or element: only a backbone + cyclic-deterioration
uniaxial material and a per-member calibration table — the shape of the existing
`sac_member_data.py` backbones.  It upgrades the steel-frame NLTHA cell from "no
deterioration" to "collapse-level", and unlocks the IDA cell when that driver
lands.

**Phasing.**

- **A0 — backbone data.** Transcribe the Lignos–Krawinkler calibration equations
  (θp precapping, θpc post-capping, Λ cyclic deterioration, My, Mr, κ) for RBS
  and non-RBS beams and columns.
- **A1 — material.** An IMK uniaxial material behind the existing
  `hinge_backbone` switch.
- **A2 — single-member / subassemblage checks.** Moment–rotation hysteretic loops
  vs the component-database envelopes.
- **A3 — frame collapse.** Re-run the SAC 3/9/20-story matrices with IMK; compare
  collapse capacity / IDA to Ibarra–Krawinkler (2005).

**Architecture.** `benchmarks/imk_calibration.py` (backbones, A0) +
`opensees/_elements.py` (the ``hinge_backbone='imk'`` branch, A1); no
preprocessor change.  Tests `tests/test_benchmark_sac_imk.py`.

## 4. RC frames — priority 2

Fills the RC moment-frame NLTHA cell (the RC moment-frame column currently
anchors on Vecchio–Emara pushover; the steel moment-frame column anchors on
SAC modal + NLTHA).  Two cross-code (T4) benchmarks, bare first:

- **DesignSafe infilled RC frames (bare → struts).**  Bhat & Singh (2024),
  DesignSafe-CI DOI `10.17603/ds2-4acg-tt80`, ships OpenSees models + responses
  for fly-ash-brick and AAC-block masonry-infilled RC frame buildings.  Step 1
  reproduces the **bare frame** (modal + pushover + NLTHA — no new physics);
  step 2 adds the diagonal-strut infill (Bhat, Singh & Agarwal 2024, *Eng.
  Struct.*, DOI `10.1016/j.engstruct.2024.118695`).
- **E-Defense 10-storey.**  Janevski, Kang & Isaković (2023), *Bull. Earthq.
  Eng.* 21(15):6553–6584, DOI `10.1007/s10518-023-01734-7`.  Full-scale 10-storey
  RC frame+wall dual system, frame and wall directions, at E-Defense (Kang et
  al. 2023 describe the test).  Exercises lumped-plasticity frames + MVLEM walls
  + scissors joints — all already implemented — so it is a data + model task.
- **E-Defense 2010 4-story RC + PT buildings.**  Nagae et al.; design and
  instrumentation in PEER Report 2011/104; results in Nagae et al. (2012, WCEE)
  and Tuna Değer et al. (2014), PURR DOI `10.4231/d33x83m2p`.  Full-scale 4-story
  RC frame+wall and post-tensioned buildings shaken with JMA-Kobe to
  near-collapse; blind-prediction study.  The RC building is replicable with
  existing machinery; the PT variant needs post-tensioning/self-centering
  materials.

- **Haselton & Deierlein (2007) / SP3 Risk** — sp3risk.com — 30+ RC SMF OpenSees
  models (1–20 stories) with pushover / NLTHA / IDA.
- **SPEAR project RC frame** (Eurocode 8) — alternative RC NLTHA benchmark.

**Phasing.** bare-frame anchors → strut infill (equivalent-strut element, new) →
E-Defense frame direction → wall direction.  The strut is the one new piece of
physics.

## 5. RC shear walls — additional sources — priority 3

Complements the Chilean building-level benchmark (§1).  Both are data +
existing-element tasks (MVLEM_3D / SFI_MVLEM_3D and fiber walls are landed).

- **FEMA P-695 / Marafi archetypes.**  Gogus & Wallace (2015), *J. Struct.
  Eng.*, DOI `10.1061/(ASCE)ST.1943-541X.0001221` — 20 special + 20 ordinary RC
  walls, static pushover + IDA on 44 ground motions, published CMRs.  Marafi's
  `nassermarafi/SRCSWArchetypes` generator emits OpenSees scripts for 69 special
  RC wall archetypes (4–40 storeys, Seattle; ASCE 7-10/7-16), modelled per
  Marafi, Ahmed, Lowes & Lehman (2019), DOI
  `10.1061/(ASCE)ST.1943-541X.0002311`.  Native OpenSees models — direct
  cross-code targets.
- **SAFE/CASH squat walls.**  13 pseudodynamic squat RC walls (JRC Ispra); Bisch
  & Coin (2006), *Bull. Earthq. Eng.*, DOI `10.1007/s10518-006-9014-1` (SAFE
  tests); benchmark phases 1/2 Gallitre, Viallet, Szczesiak, Thénint & Fabre
  (2019), SMiRT-25 (benchmark-cash.org).  Squat-wall shear envelopes exercise
  the MVLEM shear spring (aspect ratio < 2).
- **UCSD 7-story RC wall building slice.**  Panagiotou, Restrepo & Conte (2011),
  *J. Struct. Eng.* 137(6), DOI `10.1061/(ASCE)ST.1943-541X.0000332` (Phase I
  rectangular wall) and SSRP-07/08 (Phase II T-wall).  Full-scale 7-story
  cantilever-wall slice under four California records incl. near-fault Sylmar;
  published measured NLTHA response + a NEES/PCA blind-prediction contest.
  Data: DesignSafe `NEES-2006-0203`, PURR DOI `10.4231/d35t3g04t`.

- **Thomsen & Wallace RW2** — canonical OpenSees wall benchmark (3.66 m tall,
  102 mm, 1.22 m web); parameters tabulated in the OpenSees Days 2014 concrete
  walls workshop.
- **UoA-UW RC Wall Database** — Shegay et al. (2021), DesignSafe DOI
  10.17603/ds2-r12q-t415 (142 walls).
- **UCLA-RCWalls** (Abdullah 2019) — 1100+ walls.

## 6. Masonry / URM — priority 4

First masonry/URM coverage (a new matrix row).  Needs new masonry/URM physics
(equivalent-frame macro-elements and/or smeared-crack continuum masonry), so it
is deferred behind the RC tiers.

- **URM Benchmark Project.**  Cattari & Magenes (2021), *Bull. Earthq. Eng.*
  20:1901–1936, DOI `10.1007/s10518-021-01078-0` — six benchmark structures
  (BS1–BS6) with detailed input data for nonlinear static assessment
  (equivalent-frame and continuum models); blind-prediction companion results
  across participating teams.
- **URM shake-table database.**  Haindl, Smith & Beyer (2024), *Earthquake
  Spectra*, DOI `10.1177/87552930241268214` — 69 shake-table URM buildings
  (≥1:4 scale): drifts, failure modes, dynamic-property evolution (Zenodo
  record 11093882).

**Deferred.**  Li, Zuo, Zhai, Xu & Xie (2016), *Eng. Struct.*, DOI
`10.1016/j.engstruct.2016.03.032` — a 3-storey RC frame shake-table **collapse**
test.  Needs contact/falling-debris and large-displacement physics the toolkit
does not have; revisit after the collapse/IDA driver lands.

## 7. Other benchmarks — deferred

- **VecTor family** — jp68/69 (Guner & Vecchio pushover) and jp74 (cyclic
  shear-critical frames) are the natural next additions after jp16 (done);
  jp12 (Vecchio & Balopoulou) is a candidate.
- **SeismoBuild verification models** (ASCE 41-17 / EC8 / NTC-18) — `.sbf`
  native, need transcription; the EC8 wall/frame models complement the Chilean
  set.
- **NIST GCR 17-917-46v2** — reference only (ASCE 41 capacity models).
- **PRESSS 5-story precast (precast / PT / self-centering)** — Priestley,
  Sritharan, Conley & Pampanin (1999), *PCI Journal* 44(6):42–67, DOI
  `10.15554/pcij.11011999.42.67`; full PCI Phase-3 reports (Vol 3-3 design /
  instrumentation, 3-4 frame direction, 3-5 wall direction) at pci.org.  Needs
  unbonded-PT + energy-dissipator materials.
- **BRBF (braced frame)** — NIST First-Generation PBEE study Vol. 4 (pub 922024;
  4/8/16-story archetypes) and the UT10 five-story BRBF hybrid benchmark
  (Mojiri et al. 2021, *EESD*, DOI `10.1002/eqe.3508`).  Needs BRB truss
  (Steel02/Hysteretic) + gusset modelling.
- **CFT composite columns** — Denavit composite-column database
  (mark.denavit.me/Composite-Column-Database/) and the 3,208-test CFST database
  (Thai et al. 2020, DOI `10.17632/j3f5cx9yjh.1`).  Needs a CFT section +
  confined-concrete-in-tube material.
- **606 m mega-tall building (reference model)** — Tian, Xie & Lu OpenSees TCL
  models (luxinzheng.net), validated vs ETABS and MSC.Marc; ShellMITC4 walls +
  coupling beams.  Cross-platform reference only (27k–81k nodes, too heavy for
  CI).

## 8. Cross-cutting conventions

- Generators live in `src/fea_toolkit/benchmarks/…` (never `local/`); PDFs and
  `local/257/_extract/*.m` are provenance only, cited from docstrings/docs.
- One `docs/<name>_benchmark.md` per implemented benchmark;
  `tests/test_<name>_benchmark.py` with `ops.wipe()` fixtures and synthetic
  builders local to the test file.
- SAC analysis benchmarks are planar (2D) 3D models (§3.11); production stays
  3D-only.
- Commit in two groups: `feat(benchmarks)` (generator + tests + docs) then
  `chore(benchmarks)` (regenerated JSON snapshots), ruff → restage → `--no-verify`.
