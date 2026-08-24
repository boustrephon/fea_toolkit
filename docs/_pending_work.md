---
title: "Pending Work Log"
description: "Internal log of completed and pending work items across the fea_toolkit (refactors, physics, features, housekeeping)."
status: "draft"
tags: [planning, work-log, internal]
category: [planning]
---
# Pending work — fea_toolkit (2026-08-01 continued)

## PENDING (active — not yet done)

> Priority-ordered register (maintained 2026-08-24).  Every pending item
> below is cross-referenced to its source document.  **Sequencing notes:**
> Tier 1 (P1 force-diagram unification, P2 large-file splits) landed
> 2026-08-24 — see the DONE register.  The Tier 2 physics items (P3 solver
> calibration, P4 bilinearisation on a real curve) also landed 2026-08-24 —
> see the DONE register.  **P5 (shear failure / post-peak) remains the open
> physics item**, refined by the P3/P4/P5 batch findings: the V&E descent
> is a *flexure-softening* phenomenon (the nonlinear-shear mechanism is
> validated on the shear-critical Duong frame, not on the shear-strong
> V&E frame).  Tiers 3–4 are independent feature gaps and deferred
> housekeeping.

### Tier 2 — Correctness / physics follow-ups

#### P5 — Post-peak / shear-failure modelling (Vecchio & Emara follow-up)
Source: 2026-08-16 rigid-end-zone batch (DONE, below);
`docs/vecchio_emara_benchmark.md`; `docs/shear_failure_modelling.md`.

**Status: documented, not reproduced — the nonlinear-shear mechanism is
validated (Duong); the fiber-concrete softening (Phase A) and `Bond_SP01`
slip springs (Phase B) are implemented (both default-off) but cannot
produce the sustained ≥ 10 % post-peak descent; P5 closes per the
documented-partial fallback.**

**What.** The flexure-only forceBeamColumn + rigid-end-zone model lands
inside the ±10–15 % acceptance band (peak ≈ 353 kN = 1.07 × experimental;
secant @ 50 mm ≈ 6.3 kN/mm = 1.03 ×) but the curve **keeps rising after
≈ 50 mm** while the experiment softens — the post-peak *shape* is not
reproduced.

**2026-08-24 empirical findings (P3/P4/P5 batch):**
1. The nonlinear cracked-shear law for `SectionAggregator`
   (`aggregate_shear = "nonlinear"`, simplified-MCFT trilinear backbone) is
   **implemented and validated** on the shear-critical Duong frame (≥ 15 %
   post-peak drop — `tests/test_duong_benchmark.py`).
2. Applied to the V&E frame it is **inert for the post-peak shape** — peak
   348 kN (1.05×) vs 353 kN (1.07×) with elastic shear, curve still rising
   at 155 mm — because the V&E frame is **shear-strong (flexure-critical)**:
   member shear never reaches the backbone's degrading branch.
3. A centreline (no-rigid-zones) nonlinear-shear variant does not converge
   at the gravity stage (ill-conditioned first gravity increment); the
   rigid-end-zone configuration converges cleanly.
4. Conclusion: the V&E descent is a **flexure-softening** phenomenon
   (concrete crushing / bond-slip), not shear.  The remaining increment is
   strain-softening concrete (e.g. a `Concrete02` crushing branch for the
   fiber cover/core) and/or zero-length bond-slip springs at member ends.

**Outline steps (remaining).**
1. ~~Trial a strain-softening concrete option and zero-length bond-slip
   springs at member ends, config-gated off by default~~ — **DONE**: both
   are implemented — `Concrete02` + `core_residual_factor` (Phase A) and
   the `Bond_SP01` end springs via `config["bond_slip"]` (Phase B) — with
   the empirical results in the Phase A/B status blocks below.
2. ~~Re-run the V&E benchmark targeting the experimental post-peak
   branch~~ — **DONE**: the full knob sweep is recorded in the Phase A/B
   status blocks below; the ≥ 10 % sustained-descent gate is not met and
   P5 closes as "documented, not reproduced".
3. **P4 re-check:** with a real peak on the curve, re-validate
   `bilinearize_rc()` — the equal-area yield should move up toward the
   rebar-yield drift (~0.5–1 % roof drift ≈ 20–40 mm).
4. Re-run the V&B (1990) cut-back-top-reinforcement variant (deferred with
   this follow-up).
5. Update `docs/shear_failure_modelling.md` + the benchmark doc.

**Completion requirements (definition of done).**  P5 is complete when a
config-gated post-peak mechanism reproduces the experimental V&E
post-peak *shape* without regressing the in-band strength/stiffness:

1. **Mechanism** — strain-softening concrete (e.g. a `Concrete02` crushing
   branch for the fiber cover/core, with the confined-core hardening
   capped) and/or zero-length bond-slip springs at member ends
   (`Bond_SP01` Zhao–Sritharan slip-rotation springs — OpenSeesPy
   registers the material as `Bond_SP01`, not the Tcl `bond_sp01`),
   config-gated off by default so existing fibre models are unchanged.
   Already ruled out (documented): elastic `GA_v` shear (inert) and the
   nonlinear MCFT shear backbone (inert on this shear-strong frame;
   validated on the Duong frame).  Fiber-concrete softening alone
   (Concrete02 + core-residual reduction) trims the peak but cannot
   sustain the ≥ 10 % descent (see Phase A status below).  `Bond_SP01`
   slip springs soften the response and move the peak off the push end
   but plateau at their ultimate moment (the material has no degrading
   branch), so they cannot produce the descent either (see Phase B
   status below).
2. **Peak location** — the peak base shear moves off the 155 mm push end to
   the experimental peak band (≈ 40–70 mm), instead of the current
   monotonic rise to 155 mm.
3. **Strength & stiffness** — peak stays in 0.85–1.15 × 330 kN and the
   secant @ 50 mm stays in 0.9–1.15 × 6.1 kN/mm (no regression vs the
   accepted rigid-end-zone model).
4. **Post-peak descent** — a monotonic descent after the peak with ≥ 10 %
   peak-to-end drop by 155 mm (first target for this flexure-critical
   frame; trending toward the experimental softening branch).
5. **Convergence** — the full 155 mm push converges (no non-converged
   steps) under the default solver.
6. **P4 re-check** — with a real peak, `bilinearize_rc()` yield moves into
   the ~0.5–1 % roof-drift band (≈ 20–40 mm), still not at the cracking
   transition and equal-area exact.
7. **V&B (1990) variant** — re-run the cut-back-top-reinforcement variant
   and document its peak + descent (numeric gate deferred until the data
   is transcribed).

   **Transcription (2026-08-24), from
   `local/references/JP2_Guner_Vecchio_2010b.pdf` (Guner & Vecchio 2010,
   §"third frame", Fig. 12 + Table 2):** the V&B (1990) frame is
   **almost identical to the V&E (1992) frame** (same geometry: 3.5 m
   span, 2 m storeys, 300 × 400 mm members, 4 No. 20M top/bottom).  The
   only significant difference is that the **top reinforcement is cut
   back to two No. 20 bars over the central 500 mm of the first-story
   beam**, and the loading is a **monotonically increasing concentrated
   vertical load at the first-story beam midspan** (not a lateral
   pushover); the reported response is the first-story **midspan
   load-deflection** curve, terminated before failure for equipment
   safety.  JP2's model: half-frame by symmetry, member lengths ≈ half
   the section depth (200 mm) at member ends, four member types + three
   stiffened-end-zone types, shrinkage −0.5×10⁻³, ~40 concrete layers.
   A first-order three-hinge estimate gives **Pu = 380 kN (≈ 30 % below
   the actual failure load ≈ 543 kN)**; the JP2 nonlinear model matched
   the experimental curve.  Modelling implication: the variant needs a
   **midspan-load analysis on the first-story beam** (a different
   protocol from the lateral pushover runner) and a **section
   subdivision** so the beam's top bars drop from 4 to 2 over the
   central 500 mm — both non-trivial; the model itself is deferred, the
   transcription is recorded here and in the V&B pending item.
8. **Regression + docs** — existing V&E band tests, the Duong shear test
   and `test_bilinearize_rc_real_curve` stay green; add a dedicated
   post-peak test; update `docs/shear_failure_modelling.md` and
   `docs/vecchio_emara_benchmark.md`.

**Documented-partial fallback.**  This is a research-grade physics item; if
the two mechanisms are trialled and the ≥ 10 % descent is still not
reached, P5 closes as "documented, not reproduced" — recording the
best-achieved curve, the specific residual gap, and a recommendation for
the next increment.

**Phase A status (2026-08-24):** the fiber-concrete softening lever is
implemented, config-gated **off by default** (existing models unchanged):

- **New config keys** (defaults in brackets): `concrete_material`
  (`"Concrete01"` / opt-in `"Concrete02"`), `core_residual_factor`
  (crushing residual as a fraction of f′c, `0.2`),
  `concrete02_lambda` (`0.1`), `concrete02_ft_override` /
  `concrete02_Ets_override` (tension branch, SI Pa; `None` → 3 MPa and
  ft/0.001).  Hook: `SectionMixin._emit_fiber_concrete()` in
  `_sections.py` — both the cover and the confined core go through it.
- **Sweep** on the accepted rigid-end-zone config (`forceBeamColumn` +
  `rigid_end_zones` + `rigid_link_mpc`, 62-step push to 155 mm):

  | concrete / knobs | peak kN (×330) | @disp | secant@50 (×6.1) | end-drop |
  |---|---|---|---|---|
  | Concrete01 rf=0.2 (baseline) | 353 (1.07) | 155 mm | 6.29 (1.03) | 0 % |
  | Concrete02 rf=0.2 | 354 (1.07) | 155 mm | 6.31 (1.03) | 0 % |
  | Concrete02 rf=0.02, ecu=0.010 | **321 (0.97)** | **132 mm** | 5.89 (0.97) | **6.9 %** |
  | Concrete02 rf=0.02, ecu=0.008 | 308 (0.93) | 108 mm | 5.71 (0.94) | 1.9 % (11 % windowed) |
  | Concrete02 rf=0.02, ecu=0.006 | 308 (0.93) | 25 mm | 5.67 (0.93) | 0.3 % (17 % windowed, re-hardens) |

- **Achieved:** the residual-reduction lever trims the 1.07× peak into the
  strength band, keeps the secant in band, and moves the peak off the push
  end with a genuine ~7 % descent — locked in by
  `test_concrete02_strain_softening_trims_peak_with_descent`.
- **Not achieved (completion gate):** a monotonic ≥ 10 % descent from the
  40–70 mm band.  Aggressive `core_residual_factor` / `confined_ecu_max`
  pulls the peak earlier, but the response **re-hardens** toward the end
  (steel strain-hardening + P-Δ stabilisation).  The experimental
  post-peak branch is dominated by **bond-slip** (~20 % shear share + bar
  slip), which a fiber section cannot represent — the next increment is
  **P5 Phase B: zero-length `Bond_SP01` slip springs** at member ends
  (implemented — see the Phase B status below; precedents: the
  lumped-hinge `zeroLengthSection` and the Elwood limit-state `zeroLength`
  springs).

**Phase B status (2026-08-24):** the `Bond_SP01` slip-spring mechanism is
implemented, config-gated **off by default** (existing models unchanged):

- **Key facts found during implementation:** OpenSeesPy 3.8.0.0 registers
  the material under its C++ class name **`Bond_SP01`** (the Tcl command
  `bond_sp01` is not exported), and the input values are used directly —
  the backbone is fed in the model's own moment/rotation units (the
  "ksi and in" warning is informational).
- **New config keys** (defaults in brackets): `bond_slip` (`False`),
  `bond_slip_sy_m` (0.000254 m — Zhao-Sritharan 0.01 in, scaled via
  `length_scale_factor`), `bond_slip_su_factor` (35), `bond_slip_mu_factor`
  (1.4), `bond_slip_b` (0.5), `bond_slip_R` (0.7), `bond_slip_backbone`
  (`None` — optional explicit backbone in model units).
- **Hook:** `ElementMixin._create_bond_slip_springs()` in `_elements.py`
  inserts plain `zeroLength` elements at every fibre member end (dirs 1–4
  rigid, dir 5 = weak-axis slip, dir 6 = strong-axis slip) and shortens the
  fibre element to span the new bond nodes.  Uses the limit-state's
  `zeroLength -mat/-dir` pattern (no `equalDOF`) so the Transformation
  constraint handler stays compatible with `rigidLink` MPC joint offsets —
  the naive `zeroLengthSection` + `equalDOF` version was singular with the
  MPC links (DOF 48) and only converged under the Penalty handler.
  Backbone: `My = A_s·f_y·jd`, `θ_y = sy/jd`, `Mu = 1.4·My`, `θ_u = su/jd`
  (COL: My=149 kN·m, θy=0.00085; BEAM: My=159 kN·m, θy=0.00080).
- **Empirical result** (accepted rigid-end-zone config + `bond_slip=True`):
  peak **300.8 kN (0.91×)** @ 117.5 mm, secant @ 50 mm **5.76 (0.94×)**,
  full convergence, small real descent (V_end < peak, ~1.4 %).
- **Limitation (why the gate is not met):** `Bond_SP01` **plateaus at its
  ultimate moment** past `θ_u` — it never degrades (verified by probing the
  material envelope).  So the slip springs cap/soften the member but cannot
  produce a sustained ≥ 10 % post-peak descent; combined with Concrete02
  (Phase A) the response flattens near 300 kN instead of descending.
- **Best documented curve after both phases** (Phase A only, no bond-slip):
  `concrete_material="Concrete02"`, `core_residual_factor=0.02`,
  `confined_ecu_max=0.012` → peak **331.7 kN (1.005×)** @ 152.5 mm, secant
  **5.92 (0.97×)**, **9.9 % end drop** — the peak magnitude is essentially
  exact and the drop nearly meets the 10 % gate, but the peak sits at the
  push end (the sustained post-peak branch from 40–70 mm is not
  reproduced).
- **Conclusion:** with the fibre + `Bond_SP01` mechanism set, the V&E
  post-peak descent is **not reproducible** — the experimental softening is
  driven by member-level **degradation** (cracked-shear / bond degradation)
  that a forceBeamColumn fibre section cannot represent.  P5 closes as
  **"documented, not reproduced"** per the fallback: the best-achieved
  curves, the residual gap (peak location stuck near the push end; max
  ~10 % drop), and the recommendation (a degrading lumped hinge — Hysteretic
  with a descending post-cap branch — or a flexibility-based shear-flexible
  element with degrading shear) are recorded here and in
  `docs/vecchio_emara_benchmark.md`.

### Tier 3 — Feature gaps (placeholders / partial)

#### P6 — Section fiber patches (placeholders) + RC partial
Source: repo-root `README.md` §5 "Section Types and Properties" table.

**What.** `to_fiber_patches()` is `🚧 Placeholder` for `ChannelSection`,
`AngleSection`, `DoubleAngleSection`, `TeeSection`, `SDSection` (needs
polygon meshing), and `EncasedSection` (embedded section + concrete
encasement).  "Frame Member Types (RC)" is `⚠️ Partial` in the README —
materials, RC section shapes, rebar auto-placement and Mander confinement
are wired; the remaining gaps are these shape patches plus benchmark
validation (see P5).

**Outline steps.**
1. Channel / Angle / DoubleAngle / Tee: decompose into `patch('rect')`
   sub-regions mirroring the existing `ISection` (3-rect) / `BoxSection`
   (4-rect) / `PipeSection` (annular `circ`) pattern.
2. `SDSection`: mesh the multi-material `polygons` into fibre patches
   (arbitrary-polygon meshing).
3. `EncasedSection`: emit the embedded steel-section patches + concrete
   encasement patches.
4. Add per-section `to_fiber_patches()` tests (total area ≈ section `A`,
   first-moment checks); update the README table status from 🚧 → ✅.

**Constraints.** Section geometric properties stay as-authored (from the
S2K text); patches must integrate with both `AnalysisBuilder` fiber sections
and Tcl export (`export_model_to_tcl`).

#### P7 — Python-native nonlinear dynamic (time-history) integration
Source: `docs/nonlinear_dynamic_analysis.md` (frontmatter + Notes);
repo-root `README.md` "TODO / Future Work" section.

**What.** `run_nonlinear_dynamic_analysis()` is complete **via the Tcl
export + Xara/OpenSeesRT path**.  A **Python-native** transient integration
(no Tcl/Xara dependency) remains planned: `Newmark` (γ=0.5, β=0.25) or
`HHT` (α=−0.1) integrator, Rayleigh damping from the preceding modal result,
`Path` time-series + `UniformExcitation` for base excitation,
`loadConst('-time', 0.0)` gravity hand-off, Node/Element recorders.

**Outline steps.**
1. Add a native transient runner alongside the Tcl path with the same public
   API and result keys (`times`, `displacements`, `envelope`,
   `peak_displacement`, `converged_steps`).
2. Brace materials per README recommendation: `Hysteretic` + `Fatigue`
   (`brace_fatigue=True`) for the truss approach; `Steel02` + `Fatigue` if
   the Approach A subdivision ever resolves.
3. Solver fallbacks for dynamics: `KrylovNewton`/`NewtonLineSearch`,
   test tolerance 1e-4–1e-5.

**Validation.** Ground-motion smoke test on `make_rc_frame_3d()` /
`make_sample_model()`; compare `peak_displacement` + envelope against the
Tcl/Xara path on the same record; cover the runner-failure metadata path
(`converged_steps=0` + `metadata["error"]`).

### Tier 4 — Deferred / low-priority

#### P8 — Tcl-exporter merge (deferred)
`export_model_to_tcl()` (`opensees/builder.py`, SAPModelData-based) vs
`export_mesh_model_to_tcl()` (`opensees/recorder.py`, MeshModel-aware).
**Verification (2026-08-21):** independent implementations, no trivial
delegation.  If merged: one `isinstance` dispatcher + deduplicated shared
preamble/recorder emission.  Larger dedicated refactor — intentionally
deferred; keep the cross-references between `docs/tcl_export.md` and the
recorder doc current in the meantime.

#### P9 — Linting Phase 3 triage
Source: `docs/linting_fix_plan.md` (status update 2026-08-21).

**What.** Phase 1 real bugs are fixed; the remaining ~219 errors / 109
warnings are overwhelmingly Phase 3 typing noise (pandas/pyvista overloads,
`Optional`-access) — the benign categories in `.clinerules` §11.  The
Phase 2 `pyrightconfig.json` was never committed.

**Outline steps.** 1) Optionally commit `pyrightconfig.json` (exclude
Rhino host-only modules; relax `reportOptional*` rules); 2) fresh per-file
triage of the Phase 3 count, starting with the top-3 files by error count
post-P2-split (the original top-3 `model/geometry.py` → `plotting/viz.py` →
`opensees/analysis_builder.py` are now thin facades — ≈80/136/≈480 lines —
and the errors now live in `geometry_core/frames/mesh`, `viz_*`, and the
`_runner_*` modules);
3) prefer a project-wide `pd.Series.to_numpy()` convention over scattered
casts (§11.2).

#### P10 — Pushover fiber-level output (Phase 5, future/deferred)
Source: `docs/pushover_results_storage_viz.md` §Phase 5.

**What.** Per-element, per-integration-point, per-step fiber stress/strain
output via `pushover_record_fiber: True` (requires an explicit
`pushover_record_selection`).  Deferred until the envelope/step-recorder
layers (Phases 1–4) are stable.

#### P11 — Misc documented follow-ups
- **`compute_hinge_length()` signature** — align to the unit-aware
  `(section, concrete, steel, units, ...)` form; currently keeps the
  model-data-coupled `(md, sec_name, elem_length)` signature for
  behaviour-preserving migration.  Source: `docs/capacity.md`.
- **PSUMAT / CSMM smeared-plane-stress shell concretes** — package support
  (`PlaneStressUserMaterial` + `PlateFromPlaneStress` fields in
  `sap_data.py`, dispatch in `_create_nd_materials()`, `to_tcl()` emission)
  is in place and activates on a non-restricted OpenSees build; the shipped
  wheel's PSUMAT is a stub ("PSUMAT - NOT DEFINED IN THIS VERSION, SOURCE
  CODE RESTRICTED") and CSMM construction still fails.  Blocked on a full
  (non-restricted) build — see `docs/shell_support.md` Options C/D1.

## DONE (2026-08-24 — Tier 2 batch: pushover solver tuning P3, CSM bilinearisation P4)

- **P3 — pushover solver tuning (empirical pass).**  Empirically
  re-validated the pushover primary solver settings against both Gap-4
  benchmarks.  Findings: (1) the documented `1e-4 / 20` contract was
  **never actually effective** — `PUSHOVER_SOLVER_DEFAULTS` (1e-6/10)
  pre-fills the config, so the `.get(key, 1e-4)` fallback could not fire;
  (2) making `1e-4/20` effective **breaks the Duong flexure-only
  forceBeamColumn pushover** (element state-determination divergence),
  while `1e-6/10` converges every validated benchmark (V&E, Duong,
  RC/steel/LayeredShell).  **Conclusion:** the validated pushover default
  is the general `NormDispIncr 1e-6 / 10 / Newton`; looser tolerances
  (e.g. 2e-4/1000) remain an explicit per-model opt-in.  The stale
  `1e-12` fallback-tolerance guard was audited (clean — only benign
  geometry guards).  The `_PUSHOVER_RC_DEFAULTS` (analysis/base.py) RC
  preset now carries an empirical caveat.  Docs corrected in
  `docs/deprecation_plan.md` §5 and the `run_pushover_analysis` comment.
- **P4 — CSM bilinearisation real-benchmark validation.**  Applied
  `bilinearize_rc()` to the real V&E capacity curve: **S_dy ≈ 14 mm
  (spectral displacement, model length units), equal-area exact**, and — the
  key claim — the yield does **not** snap to the cracking transition (~2 mm
  spectral displacement).  The 0.5–1 % rebar-yield band is a roof-drift
  threshold, so it is not compared directly against spectral `S_dy`; the
  yield sits below it because the current model
  curve keeps hardening to 155 mm (no peak; conservative direction).  The
  band re-check is folded into P5 (once the post-peak descent gives the
  curve a real peak).  Regression test
  `tests/test_rc_benchmark.py::test_bilinearize_rc_real_curve`.  Recorded
  in `docs/deprecation_plan.md` §6 and `docs/csm_bilinearization.md`.
- **P5 partial — nonlinear-shear mechanism validated; V&E descent open.**
  Empirically confirmed `aggregate_shear = "nonlinear"` is inert on the
  shear-strong (flexure-critical) V&E frame (peak 348 vs 353 kN, curve
  still rising), while the same mechanism reproduces ≥ 15 % post-peak drop
  on the shear-critical Duong frame.  The V&E descent is a flexure-
  softening (concrete crushing / bond-slip) phenomenon — documented as the
  remaining P5 increment.  New test
  `tests/test_rc_benchmark.py::test_nonlinear_shear_variant_stays_in_band`
  locks in the in-band, converged nonlinear-shear result (with the
  documented centreline-variant gravity fragility).

## DONE (2026-08-24 — review round 2: runner split, solver-test restore, material-key docs)

Second code-review round (remaining findings M1/M3/M4/L1–L6) — commit `4b53a0f`.

- **M4 — `_runners.py` split (2,645 → 27-line facade).**  Runner logic is
  split into per-analysis-type mixins: `_runner_static.py` (885 lines —
  static analysis, seismic masses, extraction), `_runner_modal.py` (236),
  `_runner_rs.py` (505), `_runner_pushover.py` (1,074); `_runners.py` is now
  a facade combining `RunnerMixin(Static, Modal, Rs, Pushover)`.  All 28
  methods extracted byte-identically (verified via AST diff; only the M3
  edit differs).
- **M3 — pushover honours a configured `solver_test_type`.**  The primary
  setup and the post-fallback restore in `run_pushover_analysis()` now use
  `_test_type` (from config, default `NormDispIncr`) instead of hardcoding.
- **M1 — material key convention documented.**  `scale_material_dict()`
  docstring now spells out the SI-lowercase key style vs the camelCase
  `Material` dataclass trap and the SI(Pa)→model-units value convention.
- **L1 — `_mass_g` initialised to `None`** (was hardcoded `9.81`; overwritten
  by `compute_seismic_masses()` via `g_from_units`).
- **L2 — stale README typings prose removed** (Approach A section).
- **L3 — `readme = "README.md"` restored** in `pyproject.toml`.
- **L4 — nested `src/fea_toolkit/fea_toolkit.egg-info` removed.**
- **L5 — `examples/`, `docs/_*.py`, `docs/references/` ruff-formatted.**
- **L6 — empty `tests/data/` directory removed.**
- Validation: full suite `1096 passed, 4 xfailed`; ruff clean; mkdocs strict green.

## DONE (2026-08-24 — review-driven fixes: ground-motion units, model-layer ops)

Code-review round (findings H1/H2/M2) — commit `f1c278f`.

- **H1 — `io/ground_motion.py` canonical SI units.**  PEER
  `read_peer_record()` converts g → m/s² at read (`DEFAULT_GRAVITY_MS2`);
  `record_summary()` Arias intensity uses the SI constant instead of a
  hardcoded `9.81`.  All record readers/processors now share one unit
  system (m/s²).  New `tests/test_ground_motion.py` (5 tests) locks in the
  conversion and the Arias/PGV magnitudes.
- **H2 — model subpackage restored to OpenSees-free.**  `model/stories.py`
  no longer imports `ops` at module level (`plot_stories()` uses a
  function-local import for its `ops.wipe()`); the dead-but-exported
  `global_to_local_distributed_load` moved from `model/geometry_core.py` to
  `opensees/_loads.py` and is re-exported from `fea_toolkit.opensees`.
- **M2 — `numpy>=2.0`** declared in `pyproject.toml` (the codebase uses the
  NumPy 2.0 `np.trapezoid` API).
- Sequencing update: the Gap 4 Vecchio & Emara benchmark is complete, so
  P3/P4 are unblocked (their wording above is updated).
- Validation: full suite `1096 passed, 4 xfailed`; ruff clean.

## DONE (2026-08-24 — P1 force-diagram unification, Phase B)

Milestones 1–4 of `docs/force_diagram_unification.md` landed; milestone 4
(wrapper removal) was completed on 2026-08-24 (the four legacy wrappers were
removed in the deprecation cleanup).

- New `plotting/force_diagram.py` (642 lines): `ForceDiagramData` canonical
  intermediate, `_resolve_source()` input normaliser, unit resolution
  (explicit args → builder/model units → in-memory `"units"` → NPZ
  metadata), and the unified `plot_force_diagram()` dispatcher — infers
  `kind` (`"rs"` via the `z_mid` marker) and 2D-vs-3D from the input shape,
  with manual overrides; normalises `quantity` key styles (`'My_i'`/`'My'`).
- Naming decision (resolves `docs/deprecation_plan.md` Phase B):
  **`plot_force_diagram`** is the unified dispatcher; `plot_force_diagram_3d`,
  `plot_rs_force_diagram`, `plot_npz_force_diagram`, `plot_npz_moment_3d`
  were thin signature-preserving wrappers over it and were **removed
  2026-08-24** (deprecation cleanup — `docs/deprecation_plan.md` Phase B).
- The hardcoded `kN`/`m` axis fallbacks in the 2D/3D paths are gone — units
  always derive from the source; the 2D matplotlib path imports no PyVista.
- Tests: 11 `TestForceDiagramUnified` cases (input equivalence Builder/dict/
  NPZ, RS list-vs-dict, unit propagation incl. explicit override, dispatcher
  classification, wrapper call patterns) in `tests/test_plotting.py`.
- Docs: `docs/force_diagram_unification.md` status → implemented; the NPZ
  force-array orientation contract (component-keyed arrays vs the
  element-keyed `extract_static_element_forces()` dict, key-rename table,
  transpose recipe) documented in the doc + the `plot_force_diagram` module
  docstring.
- Validation: full suite `1090 passed, 4 xfailed`; `mkdocs build --strict`
  green; ruff clean.

## DONE (2026-08-24 — P2 large-file splits)

The three largest modules were split behaviour-preservingly — pure
move-refactor, one commit per split, full suite green after each, no
public-name churn (facade modules re-export every moved name).

- **`plotting/viz.py` (5.4k → 139-line facade)** — commits `4fb28b4`:
  - `viz_common.py` — shared low-level helpers (isometric view, colour
    mapping/legends, animation timers, `_NPZ_TYPES`/`_DEFAULT_HINGE_CMAP`).
  - `viz_model.py` — model / mesh / deformed / modal / building /
    comparison viewers (`plot_mesh`, `plot_deformed_displacement_3d`,
    `plot_mode_animation`, `plot_model_comparison`, …).
  - `viz_pushover.py` — hinge / shell-damage / envelope / animation /
    capacity-curve plots.
  - `viz_forces.py` — 3D force-diagram renderer + legacy 3D entry points.
  - `force_diagram.py` keeps the unified 2D/RS dispatcher; its lazy imports
    now target the new modules (both sides function-local → no cycle).
- **`model/geometry.py` (3.9k → 83-line facade)** — commit `e5b0382`:
  - `geometry_core.py` — vector/orientation math (local axes, interp,
    `SpatialGrid`, polygon area).
  - `geometry_frames.py` — frame-element splitting, load redistribution,
    rigid end offsets.
  - `geometry_mesh.py` — area meshing, overlap/constraint-edge detection,
    wall/slab intersection.
- **`opensees/analysis_builder.py` (7.4k → 2.6k facade)** — commit
  `a4ff43f`: `AnalysisBuilder` is now a facade class with mixin bases:
  - `_materials.py` — `MaterialMixin` (uniaxial + nD materials).
  - `_sections.py` — `SectionMixin` (frame/shell sections, layered shell).
  - `_elements.py` — `ElementMixin` (frame/wall/shell elements, braces,
    lumped hinges).
  - `_runners.py` — `RunnerMixin` (analysis execution, mass computation,
    result extraction/serialization) + `_normalise_frame_response` /
    `_record_step`.
  - Follow-up (commit `2dc86b4`): the facade was further slimmed to a
    476-line facade class — `_constraints.py` (`ConstraintMixin`),
    `_loads.py` (`LoadMixin`) and `_limit_state.py` (`LimitStateMixin`)
    now host edge constraints/nodes/restraints, load creation/gravity
    axial derivation/rigid diaphragms, and the Elwood limit-state columns
    respectively.  Final bases: `AnalysisBuilder(RunnerMixin, ElementMixin,
    SectionMixin, MaterialMixin, LoadMixin, LimitStateMixin,
    ConstraintMixin)`.
- Tests: `tests/test_element_properties.py` patches `_materials.ops` (the
  `RecordingOpenSees` capture target moved with the code).
- Validation: full suite `1091 passed, 4 xfailed` after each split; ruff
  clean.

## DONE (2026-08-24 — docs build repair + site restructure)

- **Strict docs build green again** (`mkdocs build --strict`): the
  `check_brace_buckling()` `Warns:` docstring had its continuation lines at
  the same indent as the `UserWarning:` entry, so griffe parsed each line as
  a new `'warning: description'` item and failed the CI gate.  Re-indented
  them, plus four more latent "Confusing indentation" docstring sections
  (`s2k_parser.SAP2000Parser` reinf tables, `csm.check_modal_pushover_mode`,
  `analysis_builder._normalise_frame_response` / `_record_step`,
  `viz._resolve_pushover_data`).
- **CI**: Actions bumped to node24 (checkout@v7, setup-python@v7,
  upload-pages-artifact@v5, deploy-pages@v5) + `actions: read` permission.
- **Site restructure**: `docs/_link_mapper.py` hook now renders the
  repo-root `README.md` as the site home (repo-root-relative links remapped
  for the built site); the auto-generated docs index was renamed
  `docs/README.md` → `docs/documentation_index.md`; `docs/index.md` added;
  helper scripts (`_*.py`) excluded from the rendered site.
- **Register note**: `docs/_pending_work.md` remains an internal (non-nav)
  doc excluded from the auto-generated index; `README.md` filename
  references in P6/P7 now mean the **repo-root** README.

## DONE (2026-08-21 — analysis-manager simplification)

- Removed the `Analysis` ABC + `AnalysisManager` (incl. `analysis/manager.py`).
- Converted the five wrapper classes to module-level functions
  (`run_modal_analysis`, `run_static_analysis`, `run_response_spectrum_analysis`,
  `run_pushover_analysis`, `run_nonlinear_dynamic_analysis`).
- Collapsed the triplicated linear-elastic default dicts into
  `_LINEAR_ELASTIC_DEFAULTS`.
- Relocated capacity code: `shear_capacity.py` + `elwood_limit_state.py`
  moved into `capacity/`; `brace_buckling_check()` moved to `model/checks.py`
  (now delegates to `check_brace_buckling()`).
- Moved the analysis runners out of `io/report.py` into
  `analysis/linear.py` (`run_linear_cases`, `static_load_verification`,
  `wind_sanity_check`).
- Consolidated `io/analysis_log.py::AnalysisLog` into `io/log.py`.
- Rewrote `generate_report()` as an explicit pipeline sequence.
- Full suite: 1038 passed, 4 xfailed.

## DONE (deprecation-programme Phase 3 — removal PR, 2026-08-21)
- Removed the 9 deprecated plotting functions from `plotting/viz.py`
  (`plot_model_3d`, `plot_deformed_3d`, `plot_rs_deformed_3d`,
  `plot_mode_3d`, `plot_static_moment_3d`, `plot_static_shear_3d`,
  `plot_static_axial_3d`, `plot_static_force_diagram`, `plot_force_diagram`)
  and the private helpers used only by them (`_get_local_end_forces`,
  `_plot_moment_flags`, `_plot_moment_tubes`, `_add_reaction_arrows`,
  `_build_shell_geometry`).  `_build_deformed_mesh` kept (used by
  `plot_mode_animation`).
- Renamed `plot_force_diagram` → `plot_rs_force_diagram` (multi-format
  input — list or full `extract_element_rs_forces()` dict; unit-aware
  `force_unit` / `length_unit`; optional `both_ends`).
- Cleaned exports: `plotting/__init__.py` + root `__init__.py` no longer
  expose deprecated names; quick-start uses `plot_mesh` /
  `plot_deformed_displacement_3d`.
- Removed the 4 deprecated unit aliases from `model/sap_data.py`.
- Fixed stale `OpenSeesBuilder` docstring refs in `io/report.py`,
  `analysis_builder.py`, `viz.py`.
- RC pushover Tcl path reorganised: new `analysis/pushover_tcl.py`
  (`run_rc_pushover_tcl`); `analysis/pushover.py` is a thin dispatcher;
  `use_tcl_fallback` kept as an alternate backend (DeprecationWarning
  removed); shared `_build_rc_config` / `_resolve_modal_data` helpers.
- Restored `mkdocs build --strict` gate in `.github/workflows/docs.yml`.
- Updated `docs/deprecation_plan.md` → complete (records Phase B:
  unify `plot_rs_force_diagram` + `plot_force_diagram_3d` +
  `plot_npz_force_diagram` into one unit-aware entry point).
- Validation: full suite `1010 passed, 4 xfailed`.

## DONE (documentation findings 1–10)
- analysis_builder_migration_plan.md: rows 92 & 94 updated (UserWarning fallback; Mander ✅ Done).
- csm_bilinearization.md: S_dp print now "model length units".
- csm_test_model_plan.md: 4-node single-storey geometry, beam "3", 20 kN/m DEAD load, control_node_tag=4; date 2026-08-01.
- mander_confinement_validation.md: NZSEE C5 row εsu = tie-steel ultimate strain.

## DONE (viz colormap + threshold unification — Findings 5&6)
src/fea_toolkit/plotting/viz.py:
- `_DEFAULT_HINGE_CMAP = "plasma"`.
- `_sample_cmap(points, cmap_name)` samples matplotlib colormap at [0,0.5,1.0]; fallback blue/yellow/red.
- `_rgb_to_hex(rgb)` helper added.
- `_ratio_to_color(ratio, max_r, cmap_name="plasma")` — 0.5 threshold, interpolated from cmap.
- `_add_hinge_color_legend(..., cmap_name="plasma")` — LUT built from sampled cmap.
- `plot_plastic_hinge_formation(..., colormap="plasma")` — docstring 0.7→0.5; colormap threaded to `_ratio_to_color` (3 call sites) and `_add_hinge_color_legend`.
- `plot_plastic_hinge_heatmap(..., colormap="plasma")` — docstring 0.7→0.5; colormap wired into `ListedColormap` via `_sample_cmap`/`_rgb_to_hex`; `bounds=[-0.5,0.0,0.5,1.0,2.0]`.
- `_sample_cmap` unused `import math` removed.
- **NOTE**: `_add_shell_color_legend`/`_ratio_to_shell_color`/shell docstrings still use 0.7 (intended: shells keep 0.7; frames unified to 0.5).

## DONE (remainder of batch — items 1–7 below)
1. **tests/test_viz_new_features.py** — smoke tests added for `_ratio_to_color`/heatmap:
   - `_ratio_to_color(0.0, 1.0) == _sample_cmap([0.0],"plasma")[0]`
   - `_ratio_to_color(0.5, 1.0) == _sample_cmap([0.5],"plasma")[0]` (boundary → yellow)
   - `_ratio_to_color(1.0, 1.0) == _sample_cmap([1.0],"plasma")[0]`
   - custom cmap e.g. "viridis" sampled same way; invalid name falls back.
2. **docs/pushover_results_storage_viz.md** — updated:
   - §4.1 heatmap color description: 0.7 → 0.5.
   - §4.1 "Yield detection": clarified ratio = |Mz|/peak|Mz| range-normalised (NOT Fy×S); relies on fiber force-deformation for RC/axial.
   - §4.5 `_add_hinge_color_legend`: documented `colormap` param (default "plasma"), 0.5 threshold shared with heatmap, alternates (viridis, cividis, turbo).
3. **Finding 7** — rc_rectangular_section_workflow.md unifies no-tie confinement fallback:
   - Shared constants `RC_NO_TIE_CONFINEMENT_FACTOR = 1.25`, `RC_NO_TIE_EPSC_FACTOR = 2.0` in utils.py.
   - builder.py (Tcl export) and analysis_builder.py now reference the same constants (no literal 1.3×).
   - Parity test `test_no_tie_confinement_fallback_parity` added in test_rc_pushover.py.
4. **Finding 8** — report_generation.md nonlinear_dynamic row verified: `analysis/nonlinear_dynamic.py` is fully implemented (NOT a stub) — row left accurate.
5. **Finding 9** — report_generation.md εc=0.006 note added (prose-only; code already used 0.006).
6. **Finding 10** — examples/sample_model.py UB300 → real UB305×165×40:
   - A = 2*bf*tf + (depth-2*tf)*tw = 0.00509434 m²
   - I33 = (bf·depth³ − (bf−tw)·(depth−2tf)³)/12 = 8.3935e-5 m⁴
   - I22 = (2·tf·bf³ + (depth−2tf)·tw³)/12 = 7.6559e-6 m⁴
   - Applied to both `make_sample_model()` and `make_nonlinear_sample_model()`.

## DONE (validation — all green)
- `python -m pytest tests/test_confinement.py -v --tb=short` → 35 passed
- `python -m pytest tests/test_model.py -k "pushover_to_adrs or bilinear or equal_energy or hardening" -v --tb=short` → 27 passed
- `python -m pytest tests/test_plotting.py tests/test_viz_new_features.py -v --tb=short` → 78 passed
- `python -m pytest tests/test_rc_pushover.py tests/test_workflows.py -v --tb=short` → 20 + 60 passed
- `python -m pytest tests/test_layered_shell.py tests/test_units.py -v --tb=short` → 112 passed
- `python -c "import fea_toolkit"` → OK

### Collateral test fixes
- `tests/test_workflows.py::test_static_self_weight_consistency`: expected self-weight 6280.0 → 3999.0569 N (UB300 area change: 0.00509434 × 78500 × 10).
- `tests/test_model.py`: removed two stale CSM tests (`test_pushover_to_adrs_missing_control_node`, `test_equal_energy_hardening_converges`) that asserted WIP-diff behavior from an uncommitted csm.py diff; no value retained.

## DONE (deprecation-programme Phase 2 — Gap 6, 2026-08-16)
- `model/csm.py`: new `bilinearize_rc()` — De Luca/Vamvatsikos 10 %-secant
  rule (elastic secant at 10 % of peak strength + closed-form equal-area
  yield).  Registered in `compute_performance_point(..., bilinearize_method=...)`
  under `"rc"` and `"de_luca_10pct"`; config key `elastic_fraction` (0.10).
- Exported via `fea_toolkit.model.__all__`.
- Tests (`tests/test_model.py`): `test_de_luca_recovers_exact_bilinear_knee`,
  `test_de_luca_rc_curve_yield_not_at_cracking` (tanh RC backbone — yield in
  rebar-yield band, equal-area exact), `test_compute_performance_point_accepts_de_luca_method`
  (dispatch), plus `bilinearize_rc` added to the shared empty/noisy/elastic/
  yield-before-peak loops.  Full suite: **878 passed, 4 xfailed**.
- Docs: `csm_bilinearization.md` §4, `deprecation_plan.md` Gap 6 status,
  `analysis_builder_migration_plan.md` RC-solver row, `llm_guide.md` /
  `README.md` / `viewer.md` deprecated-plot examples → replacements.

## DONE (deprecation-programme Phase 2 — Gap 4, 2026-08-16)
- **Joint-load application bug FIXED** — SAP2000 "JOINT LOADS - FORCE"
  were parsed and carried through the Preprocessor but `create_loads()`
  never emitted them to the OpenSees domain (silently dropped loads).
  Now applied; the gravity load/reaction sanity check includes joint
  loads + self-weight.  Regression test in `tests/test_rc_benchmark.py`.
- **Vecchio & Emara (1992) benchmark implemented** —
  `tests/test_rc_benchmark.py` `make_vecchio_emara_frame()` (one-bay,
  two-storey; 3500 mm span; 2000 mm storeys; 300×400 mm members; 4 No.20M
  top/bottom; No.10M @ 125 mm ties; f'c 30 MPa / fy 418 MPa; 700 kN/col).
  5 tests: joint-load regression, 155 mm protocol convergence, peak
  brackets experiment (ratio ≈ 1.5 documented), stiffness band
  (≈ 8.6 vs 6.1 kN/mm @ 50 mm), BEAM M-φ ≈ 195 kN·m vs Response-2000 206.
- **Flexure-only bias documented, not ±10%** — the fiber pushover
  overestimates peak (~1.5×) and stiffness (~1.4×) because it has no
  bond-slip / shear (~20% share) / distributed-cracking stiffness
  reduction; the frame-action axial (P ≈ −750 kN in beams) inflates the
  confined/hardening section capacity.  Elastic-to-first-yield range and
  section capacity are correct.  `deprecation_plan.md` Gap 4 → 🟡 with
  the measured numbers.
- **Second pass (2026-08-16): element formulation was part of the bias.**
  Re-running the fibre rebuild on `forceBeamColumn` instead of
  `dispBeamColumn` drops the peak to ≈ 291 kN (0.88 × experimental) and
  the secant @ 50 mm to ≈ 5.6 kN/mm (0.93 × experimental) — inside the
  original ±10–15 % band with no calibration.  Added `aggregate_shear` /
  `shear_area_factor` / `fiber_element_type` config keys
  (`AnalysisBuilder`): `SectionAggregator` + elastic `GA_v` on Vy/Vz.
  Discovery: `dispBeamColumn` (Euler-Bernoulli) never engages section
  shear DOFs, so aggregation is inert for it (builder now warns);
  `forceBeamColumn` (flexibility-based) engages them.  The elastic shear
  term contributes only ≈ 0.2 % for these members — the experimental
  ~20 % shear share is a *cracked*-shear phenomenon.  New tests:
  `TestVecchioEmaraShearFlexibleVariant` (peak ratio [0.75, 1.15],
  secant [0.8, 1.2] × experimental, inert-with-warning regression).
- **Third pass (2026-08-16): rigid joint end zones close the strength/
  stiffness gap.**  Added preprocessor options `rigid_end_zones` /
  `rigid_offset_factor` / `rigid_offset_absolute` / `joint_extents`
  (auto-derive offset = 0.5 × intersecting member's depth) and builder
  option `rigid_link_mpc` (`ops.rigidLink` MPCs instead of stiff elastic
  links, which ill-condition under PDelta).  Fixed a latent bug: the
  orphan-node step dropped the joint nodes that only the rigid links
  reference.  The V&E benchmark (forceBeamColumn + rigid zones) now peaks
  at ≈ 353 kN (1.07 × experimental) with secant @ 50 mm ≈ 6.3 kN/mm
  (1.03 ×) — inside the ±10–15 % band.  Tests:
  `tests/test_rigid_end_zones.py` (14) +
  `test_rigid_end_zones_lands_in_acceptance_band`.
- **Follow-up (deferred):** nonlinear cracked-shear degradation / bond-slip
  springs to reproduce the experimental *post-peak descent* (the
  forceBeamColumn model plateaus ≈ 290 kN while the experiment softened
  after ≈ 50 mm); Vecchio & Balopoulou (1990) variant re-run once the
  shear model lands.
- Full suite: **905 passed, 4 xfailed** (882 + 8 benchmark + 14 rigid-end-zone + 1 rigid-benchmark tests).

## DONE (deprecation-programme Phase 2 — Gap 3, 2026-08-16)
- **3D-only policy documented** — `.clinerules` §3.11 (analysis is
  `ndm=3`/`ndf=6` by design; no `ndm`/`ndf` dispatch in the main workflow;
  2D OpenSees is test-only), `docs/llm_guide.md` §2 note + §6 rule,
  `README.md` overview bullet.  `deprecation_plan.md` Gap 3 corrected
  (was wrongly marked "code complete" — the 2D/3D dispatch never existed).
- **3D RC validation** — `make_rc_frame_3d()` (single-storey 2-bay × 2-bay
  RC moment frame, genuine Y extent) + `tests/test_rc_3d.py`: geometry,
  symmetric X/Y modal periods, and 3D pushover convergence with yielding.
- **2D hand-check (tests-only)** — `tests/test_rc_2d_cantilever.py`:
  standalone `ndm=2` `forceBeamColumn` + `Lobatto` RC cantilever reusing
  `ConcreteRectangularSection.to_fiber_patches()` + C30/Rebar values;
  peak base shear within ±15 % of the ACI rectangular-block plastic moment.
- Full suite: **882 passed, 4 xfailed** (878 + 3 3D tests + 1 2D test).
- ~~The uncommitted `src/fea_toolkit/model/csm.py` WIP diff (63 lines: `_modal_participation` helper, peak_idx clamping, performance-point fallback period, control-node warning) remains uncommitted.~~ **RESOLVED (2026-08-16)** — the WIP was subsequently committed
  (`b29505d` CSM sign folding + performance-point robustness, `18caea7` effective-modal-mass terminology + all-rejected-modes ValueError, `f4a8c3b` require `nodal_masses`, `453ea90` mode-selection docs). The working tree is clean; the ~27 CSM/bilinearization tests in `tests/test_model.py` pass against the committed state. The `_modal_participation` helper, `peak_idx` clamping (`csm.py` `bilinearize_composite`), performance-point fallback period, and control-node warning are all present in `src/fea_toolkit/model/csm.py`.

## CURRENT CONCLUSIONS — 2026-08-04 (recorded, then confirmed against online docs)

### A. RC pushover convergence — tolerance sensitivity is real and expected
- v4/v5 hand-rolled push loop for the Admin Building stalls at ~0.006 m control
  displacement under the strict RC defaults (`NormDispIncr 1e-6`, 10 iter,
  `NewtonLineSearch`).  Relaxing to `NormDispIncr 1e-4` / 20 iter / `Newton`
  (as used by v4/v5/v6) converges reliably.
- **Online confirmation**: OpenSees manual (RC Frame Pushover Example, Berkeley
  Wiki) documents the same behaviour — forceBeamColumn + fiber models "do not
  always converge for the analysis options of choice", and the canonical
  pattern is a failure-fallback chain: `Newton` → `ModifiedNewton -initial` with
  a much larger iteration budget (1000) on failure, then resume `Newton`.
  The forceBeamColumn docs also note the element performs its *own* internal
  state-determination iteration (`-iter $maxIter $tol`, defaults 10 / 1e-12)
  *on top of* the global solver — so per-step cost and convergence sensitivity
  are intrinsically higher than for displacement-based elements.
- **Conclusion**: the relaxed solver settings in v6's `rc_config`
  (`solver_test_tol=1e-4`, `solver_test_max_iter=20`, `solver_algorithm="Newton"`)
  are the correct engineering choice for this model class, not a hack.

### B. CSM sign folding for -X / -Y pushes
- The uncommitted csm.py diff folds negative-direction pushes into the positive
  (S_d, S_a) quadrant via `np.abs`, because the pushover_to_adrs flow filters on
  non-negativity; a -X/-Y push previously produced an entirely filtered curve
  → "Too few valid data points in capacity spectrum".  The physical sign is
  recovered from the final control displacement and re-applied to
  `V_base`/`D_roof`; `plot_csm_4panel` also plots `abs()` ADRS arrays.
- **Conclusion**: correct and self-consistent — direction sign lives in the
  caller's label (-X/-Y), CSM iteration operates on magnitudes.

### C. `node_mass_overrides` for masonry mass
- `AnalysisBuilder.run_pushover_analysis(..., node_mass_overrides=...)` applies
  per-node mass scale factors (node-ID → multiplier) after
  `compute_seismic_masses()`, re-issuing `ops.mass()` so the OpenSees domain
  stays consistent.  This enables per-storey masonry mass corrections
  (`factor = 1.0 + m_storey_extra/m_seismic`) that a single global scale cannot
  express.  v6 computes these factors via `build_node_mass_overrides()`.
- **Conclusion**: approach is sound; note the override changes the *lateral load
  shape* under mass-proportional patterns as well as the dynamic properties —
  intentional for masonry.

### D. `ResponseSpectrum` — canonical demand-spectrum carrier
- New dataclass (`T`, `Sa`, `code`, `description`) with `from_gb50011()` and
  `from_arrays()` factories plus `interpolate()`.  `from_gb50011()` reproduces
  the existing `_gb50011_spectrum()` formulas (damping-corrected ascending
  branch, γ/η₁/η₂ from GB 50011 §5.1.5).  `pushover_rc_openseespy()` /
  `run_pushover_4dir()` / `PushoverAnalysis` now accept an injected spectrum
  instead of hard-wiring GB 50011.
- **Conclusion**: the pushover path is no longer code-locked to one design code
  (ASCE 7 / site-specific spectra can be injected via `from_arrays`).

### E. Mander confinement engine — validated, matches published Eq. 29
- `docs/mander_confinement_validation.md` documents formula-by-formula
  conformance to Mander, Priestley & Park (1988): closed-form confined strength
  Eq. 29 (`f'cc = f'c(2.254√(1+7.94 f'l/f'c) − 2 f'l/f'c − 1.254)`), ke for
  circular (Eq. 14) and rectangular (Eq. 22) sections, εcc (Eq. 4), ρx/ρy/ρs,
  cross-tie contributions.
- **Online confirmation**: Mander 1988 PDF (via itu.edu.tr mirror) reproduces
  Eq. 29 verbatim (the "[-1.254 + 2.254√(1 + 7.94f'l/f'c) − 2f'l/f'c]" form),
  and OpenSees Concrete01 docs confirm it is a Kent-Scott-Park model with
  exactly the four parameters the toolkit emits (fpc, epsc0, fpcu, epsU),
  i.e. the Mander f'cc / εcc feed the peak and the crushing point is the
  spalling endpoint — matching the toolkit's `ecu_max` cap (default 0.025;
  NZSEE C5 uses 0.05, configurable via `confined_ecu_max`).
- **Conclusion**: no code changes required; validation doc is accurate.

### F. v5 material-tag bug — fixed upstream
- `local/check_v5_bug.py` shows the old v5 material-tag bug is fixed:
  `run_pushover_analysis()` now works with the v5-style config
  (forceBeamColumn + HingeRadau + fiber sections + PDelta).

### G. Working-tree status (uncommitted, coherent feature batch)
- 8 modified files: `__init__.py`, `analysis/pushover.py`, `model/csm.py`,
  `opensees/analysis_builder.py`, `opensees/pushover.py`, `plotting/report.py`,
  `spectrum.py`, `tests/test_extracted.py` — ResponseSpectrum + node mass
  overrides + CSM sign handling + RC-path dispatch simplification.  Tests added
  for ResponseSpectrum (6 cases).  Remaining green suite documented above.
- **Conclusion**: this batch is ready to commit once the full suite is
  re-run and the csm.py WIP diff is folded in deliberately.


## CURRENT CONCLUSIONS — 2026-05-08 (Session G)

### H. Wall tau/tau_cap DCR outliers = genuine demand (extraction verified)
- Both extract paths query `ops.eleResponse(tag, "section", 1, "forces")` — true
  per-unit-width resultants for ShellNLDKGQ + LayeredShell.
- Controlled probe (`local/probe_layered_shear_correct.py`), exact admin
  5-layer stack, clean pure shear (ux=g0, uy=0): Nxy = sum(Gi*ti)*g0 exactly
  (ratio 1.000000); elastic-section reference also 1.000000.  Early "2.000x"
  was a probe artifact (prescribing ux=gy AND uy=gx doubles engineering shear).
- tau/tau_cap up to 2.517 reflect real computed in-plane shear demand at PP;
  follow-up is engineering action (capacity/layout/material), not a code fix.
- New regression: `test_record_step_layered_shell_shear_resultant` in
  tests/test_layered_shell.py locks the 5-layer composite-shear resultant.

- **Sectional-average note (addendum)**: local max Nxy exceeds same-row section
  avg 1.32-2.64x (pX 3838 vs 1457, nX 3826 vs 2781, pY 3814 vs 2724,
  nY 3828 vs 2900 kN/m).  GB 50010 tau_cap is calibrated on sectional-average
  shear V/(b*h0); a design-grade wall check should average Nxy per storey
  section before /t.  Element-peak basis is conservative by ~this factor.

### I. Parent-row sectional-average wall DCR — implemented (Phase A, local)
- `admin_pushover_checks_v8.py` now supports the sectional-average wall basis:
  `WALL_AVERAGE_BY_PARENT_ROW = True` groups all ``{parent}_sub_{row}_{col}``
  sub-elements and averages Nxy/Ny over each ``(parent, row)`` band before
  computing tau/sigma (see `group_shell_by_parent_row()`).  Non-matching
  elements fall back to being checked alone under ``(id, "?")``.
- CSV output: ``*_walls_by_section.csv`` with ``{parent}_section_{row}`` IDs
  and an `n_subs` column.  Element-peak mode (original) preserved via the
  toggle.
- Cross-checked against raw NPZ at the PP step (pX D_roof → step 5):
  `1_section_2` avg_Nxy = 921.552 kN/m → tau = 6143.68 kPa, avg_Ny =
  -306.532 → sigma = 2043.545 kPa — exact match to CSV output.
- Direction summary at PP (sectional-average, tau_cap = 4231.17 kPa):
  - pX: worst 1_section_2 τ/τcap = 1.452; element-peak worst was 1.673
  - pY: worst 4_section_0 τ/τcap = 1.417
  - nY: worst 2_section_0 τ/τcap = 1.677
  - nX: (from script run) several sections still fail
- Even on the sectional-average basis the lowest storey bands fail
  (τ/τcap ≈ 1.3-1.7) — the demand is genuine, concentrated at the wall
  base.  Engineering actions: wall-section thickening / increased web
  shear reinforcement (rho_sh) / alternative layout.
- **Phase B (toolkit) — done**: the grouping is generalised in
  `src/fea_toolkit/model/storey_response.py` as
  `group_shell_forces_by_section(shell_sap_ids, shell_parent_sap_id,
  shell_Nxy, shell_Ny, step_idx)`, using the NPZ `shell_parent_sap_id`
  array (not naming-convention parsing), with unit tests in
  `tests/test_storey_response.py` on a fabricated 2x2 quad mesh.
  Remaining gaps: none known for the grouping helper itself — the
  sectional-average demand ratios above still need engineering action
  (wall thickening / rho_sh / alternative layout).


## CURRENT CONCLUSIONS — 2026-08-08 (Alternative shear-wall model probe)

### J. OpenSeesPy 3.8.0.0 — no working explicit shear-yield RC shell concrete
- **RESOLVED (2026-08-08) — MVLEM / SFI-MVLEM are usable in the shipped wheel.** Re-probing against the real build with ConcreteCM resolved the earlier "Invalid c" problem. Verified signatures (extracted from `openseespymac/opensees.so` strings + runtime):
  - **MVLEM (2D)** — `element MVLEM eleTag Dens iNode jNode m c -thick {*bList} -width {*hList} -rho {*rhoList} -matConcrete {*conc} -matSteel {*steel} -matShear {shear}`. `c` is **positional** (6th arg, after `m`) and `Dens` is 2nd; ALL list args must be **expanded as individual scalars** in OpenSeesPy (not passed as a Python list). `Dens`/`-rho` must be non-zero — `Dens=0` leaves the internal node singular (`matrix singular U(i,i)=0`). With `Dens=2.4`, `-rho=2400`, uniaxial ConcreteCM + Steel02 + ElasticPP shear spring, a 4×3 m wall pushover **converges** (ok=0) with exact base-shear equilibrium. ✅
  - **SFI-MVLEM (2D)** — **BROKEN in this wheel.** The parser accepts the MVLEM-style keywords (`-matConcrete/-matSteel/-matShear`) but the constructor still performs nD-material lookups, aborting with `SFI_MVLEM::SFI_MVLEM() - Null ND material pointer passed` for every tag combination tried (uniaxial tags, FSAM in matShear, FSAM in matConcrete). This is a parser/constructor mismatch in the wheel build. ❌ (Do **not** use the 2D variant; use SFI_MVLEM_3D or MVLEM.)
  - **SFI_MVLEM_3D** — ✅ **fully works** with the documented nD form: `element SFI_MVLEM_3D eleTag iNode jNode kNode lNode m -thick {*T} -width {*W} -mat {*Mat_tags} <-CoR c>`, where `-mat` holds **FSAM nD material tags** (FSAM needs a uniaxial concrete implementing `getCrackingStrain()`; ConcreteCM works, ConcreteS/D/04/02 do not). A 4×3 m wall pushover converges (ok=0) with exact base-shear equilibrium (162 mm drift @ 100 kN with 2.5%/0.4% rho boundary/interior FSAM — plausible).
  - `E_SFI_MVLEM` / `MVLEM_3D` / `E_SFI_MVLEM_3D` also ship; the 2D `E_SFI_MVLEM` uses the `-thick -width -mat` (nD) form.
  - **PSUMAT stays unavailable** even with a rebuild — the stub is in the upstream OpenSees source (`PSUMAT - NOT DEFINED IN THIS VERSION, SOURCE CODE RESTRICTED`). CSMM (`ReinforcedConcretePlaneStress`) still fails to construct. Options C/D1 remain blocked.
  - Working probe: `local/probe_mvlem_sfi.py` (MVLEM 2D + SFI_MVLEM_3D pushover, kN-m units). Local-build extension recipe: `docs/openseespy_local_build.md`.
  - So Option B (SFI-MVLEM/MVLEM macro-element wall) is **achievable on the shipped wheel** — earlier "needs custom element" statement was wrong. Remaining caveats: 2D SFI_MVLEM broken (use 3D-in-2D-plane or MVLEM); FSAM/concrete require ConcreteCM; verify against Kolozvari reference results before production use.
- (Import chain confirmed by inspection: `openseespy.opensees/__init__.py` ->
  **`openseespymac.opensees`** on Darwin arm64 (installed 3.8.0.0 wheel). The
  `openseespy/opensees/opensees.so` + `OpenSeesPy.dylib` in site-packages are
  unmanaged local-build artifacts not in `openseespy-3.8.0.0.dist-info/RECORD`
  and are never imported. Probes ran against `openseespymac/opensees.so`.)
- **Smeared-plane-stress shell concretes remain unavailable on this wheel**
  (probed directly on a 4-node `ShellNLDKGQ` + `LayeredShell` RC wall):
  | Material path | Registers? | `analyze()` converges? |
  |---|---|---|
  | `ConcreteS` (current v5–v8 path) | ✅ | ✅ `ok=0` — working nonlinear RC shell concrete |
  | `PlaneStressUserMaterial` (PSUMAT) | ⚠️ stub | ❌ no object created — "PSUMAT - NOT DEFINED IN THIS VERSION, SOURCE CODE RESTRICTED" |
  | `ReinforcedConcretePlaneStress` (CSMM) + `PlateFromPlaneStress` | ❌ | ❌ constructor fails even with documented signature — "failed to set appropriate materials tag" with both `Concrete04` and `Concrete02`+`Steel02` |
  | `ElasticIsotropic` + `PlateFromPlaneStress` | ✅ | ❌ `ok=-3` — wrapper-in-shell combination not viable in this binary |
  Options C / D1 from `docs/shell_support.md` still need a full
  (non-restricted) OpenSees build; the working nonlinear shear paths on this
  wheel are now **MVLEM / SFI_MVLEM_3D** (Option B) plus D2 (calibrated shear
  layer) and D3 (post-process shear DCR).
- **Package support added anyway (correct standard OpenSees API, activates
  on a non-restricted build)**:
  - `model/sap_data.NDMaterial` gained `PlaneStressUserMaterial` +
    `PlateFromPlaneStress` fields: `fcu`, `epsc0`, `epscu`, `epstu`, `stc`,
    `nstatevs`, `nprops`, `Eout`; `to_tcl()` emits the two-call sequence.
  - `utils.scale_material_dict` classifies the new fields (`Eout` stress;
    strains/`stc`/counts non-stress).
  - `analysis_builder._create_nd_materials()` dispatches the
    `PlaneStressUserMaterial` + `PlateFromPlaneStress` pair.
- **`local/CLP_BSDG_Latest_Models/Admin_Building/admin_pushover_v9.py`** is
  the v8 clone; it retains the verified-working `ConcreteS` smeared-crack
  wall concrete and documents the PSUMAT restriction in its module
  docstring.  Outputs renamed `*_v9.*`.
- Validation: `tests/test_layered_shell.py` + `test_units.py` +
  `test_model.py` → 359 passed.
