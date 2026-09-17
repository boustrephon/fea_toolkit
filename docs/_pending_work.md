---
title: "Pending Work Log"
description: "Internal log of completed and pending work items across the fea_toolkit (refactors, physics, features, housekeeping)."
status: "draft"
tags: [planning, work-log, internal]
category: [planning]
---
# Pending work — fea_toolkit (2026-09-17)

## PENDING (active — not yet done)

> Priority-ordered register (maintained 2026-09-17).  Every pending item
> below is cross-referenced to its source document.  **Sequencing notes:**
> Tier 1 (P1 force-diagram unification, P2 large-file splits) landed
> 2026-08-24 — see the DONE register.  The Tier 2 physics items (P3 solver
> calibration, P4 bilinearisation on a real curve) also landed 2026-08-24 —
> see the DONE register.  **P5 (shear failure / post-peak) closed
> 2026-08-25 as "documented, not reproduced"** via the documented-partial
> fallback: the V&E descent is a *flexure-softening* phenomenon (the
> nonlinear-shear mechanism is validated on the shear-critical Duong frame,
> not on the shear-strong V&E frame), and the P4 re-check plus the V&B
> (1990) variant are deferred alongside it.  Tiers 3–4 are independent
> feature gaps and deferred housekeeping.

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
3. ~~**P4 re-check**~~ — **DEFERRED (2026-08-25, with the P5 closure)**:
   re-validating `bilinearize_rc()` against a *real peak* requires a
   post-peak peak on the V&E curve, which the fibre + `Bond_SP01`
   mechanism set could not produce (the peak stays stuck near the push
   end).  Re-run once the recommended degrading mechanism (a lumped hinge
   with a descending post-cap branch, or a shear-flexible element with
   degrading shear) gives the curve a real peak.
4. ~~Re-run the V&B (1990) cut-back-top-reinforcement variant~~ —
   **DEFERRED (2026-08-25, with the P5 closure)**: the variant is
   transcribed (Guner & Vecchio 2010b — near-identical frame to V&E with
   the first-story top bars cut back over the central 500 mm and a
   midspan vertical-load protocol); it needs the same degrading mechanism
   before a re-run adds signal.
5. ~~Update `docs/shear_failure_modelling.md` + the benchmark doc~~ —
   **DONE (2026-08-25)**: the closure and both deferrals are recorded in
   `docs/shear_failure_modelling.md` (Phase-2 scope note) and
   `docs/vecchio_emara_benchmark.md` (known limitation #5).

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

#### P6 — Section fiber patches (P6a done — Channel/Angle/DoubleAngle/Tee; P6b deferred)
Source: repo-root `README.md` §5 "Section Types and Properties" table.

**What.** `to_fiber_patches()` is implemented for `ChannelSection`,
`AngleSection`, `DoubleAngleSection`, and `TeeSection` (P6a, 2026-08-25 -
centroid-shifted `patch('rect')` decompositions + per-shape tests); still
`🚧 Placeholder` for `SDSection` (needs polygon meshing) and
`EncasedSection` (embedded section + concrete encasement) - see P6b below.
"Frame Member Types (RC)" is `⚠️ Partial` in the README - materials, RC
section shapes, rebar auto-placement and Mander confinement are wired; the
remaining gaps are the P6b shape patches plus benchmark validation (see P5).

**Done (P6a, 2026-08-25).**
1. Channel / Angle / DoubleAngle / Tee decomposed into disjoint
   `patch('rect')` sub-regions mirroring the `ISection` (3-rect) /
   `BoxSection` (4-rect) pattern, re-centred on the elastic centroid via
   the shared `_rect_patches_from_regions()` helper so the fibre section
   has vanishing first moments (sum A*ybar = sum A*zbar = 0).
2. Per-shape tests in `tests/test_model.py`
   (`TestChannelSectionFiberPatches`, `TestAngleSectionFiberPatches`,
   `TestDoubleAngleSectionFiberPatches`, `TestTeeSectionFiberPatches`):
   patch count/type, `mat_tag` + `nfy`/`nfz` propagation, total area ≈
   section `A`, first moments ≈ 0.
3. README §5 table status flipped 🚧 → ✅ for the four shapes.

**Deferred (P6b).**
1. `SDSection`: mesh the multi-material `polygons` into fibre patches
   (arbitrary-polygon meshing) - blocked: the parser constructs
   `SDSection(**common)` with empty `polygons` (no `SECTION DESIGNER`
   table parsing), so only caller-provided polygons would be actionable.
2. `EncasedSection`: emit the embedded steel-section patches + concrete
   encasement patches - blocked: no parser path populates the section,
   and the single-`mat_tag` `to_fiber_patches()` signature cannot carry
   two materials (steel + encasement concrete) without an API change.

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

#### P12 — LoadCombination table parsing (README §3 reconciliation)
Source: repo-root `README.md` §3 "Load Combinations and Analysis Types";
`LoadCombination` dataclass + `TestLoadCombination` in `model/sap_data.py` /
`tests/test_model.py`.

**What.** The `LoadCombination` dataclass exists and is tested, but
`io/s2k_parser.py` reads **nothing** from the `LOAD COMBINATIONS` table —
load cases (`CASE - STATIC` / `CASE - MODAL` / `CASE - RESPONSE SPECTRUM`)
are parsed, but combinations are not.  The `AnalysisBuilder` step to run a
named combination with factors (e.g. `1.2 DL + 1.6 LL`) also remains open.

**Outline steps.** 1) Parse `LOAD COMBINATIONS` rows into `LoadCombination`
instances (combo type + case/factor pairs); 2) expose
`SAPModelData.load_combinations`; 3) builder support to run a named
combination with combination factors.

#### P13 — Joint Level 3 elements (Joint2D / beamColumnJoint)
Source: repo-root `README.md` §5 "Joint Modeling";
`docs/vecchio_emara_benchmark.md` §6.6.

**What.** Level 1 rigid end zones are implemented (`rigid_end_zones` /
`rigid_link_mpc`); **Level 3** joint elements (`Joint2D`,
`beamColumnJoint`) remain unimplemented.  `joint_extents` already composes
with Level 1, so the two features will not double-count regions once Level
3 lands.  Level 2 (zero-length springs restoring a fraction of the rigid
connection) also remains open.

**Outline steps.** 1) Extend the parser to recognise joint elements if
present in SAP2000; 2) implement `Joint2D` / `beamColumnJoint` emission in
the builder; 3) verify interaction with `joint_extents` and Level-1
offsets.

#### P14 — FRAME LOADS - POINT point loads + temperature loads
Source: repo-root `README.md` §7 (Medium) "Improved Load Handling".

**What.** The parser handles frame distributed and gravity loads but not
the `FRAME LOADS - POINT` table (point loads on frames) or temperature
loads.  Note: the pushover `lateral_load_pattern='point'` is a synthetic
unit point load for benchmark pushes — distinct from parsing user-supplied
`FRAME LOADS - POINT` data.

**Outline steps.** 1) Add `FRAME LOADS - POINT` parsing to a new
`frame_point_loads` field; 2) propagate to OpenSees `eleLoad` point loads;
3) temperature loads only if needed (no current demand).

#### P15 — Docs toolchain: MkDocs 2.0 / ProperDocs decision
Source: `mkdocs.yml` header comment; `.github/workflows/docs.yml`.

**What.** The docs site builds from a pinned MkDocs 1.x toolchain
(`mkdocs==1.6.1`, `mkdocs-material==9.7.7`, `mkdocstrings[python]==1.0.6`,
`mkdocstrings-python==2.0.5`, `mkdocs-gen-files==0.6.1`).  Two independent
packages now print advisory "MkDocs 2.0" notices at build start (neither is
strict-failing):
- `mkdocs-gen-files` via its `properdocs` dependency → silenced with
  `DISABLE_MKDOCS_2_WARNING=true`.
- the `mkdocs-material` theme → silenced with `NO_MKDOCS_2_WARNING=true`.
Both are suppressed in CI and documented for local builds; the exact pins keep
the build reproducible, so no action is needed today.

**Open decision (not urgent).** Revisit when a docs-package bump is needed or
MkDocs 1.x becomes unmaintained: (a) stay pinned on 1.x; (b) migrate to
*ProperDocs* (`properdocs build` — a drop-in 1.x continuation that
`mkdocs-gen-files` already depends on); (c) follow `mkdocs-material`'s 2.0
guidance
(https://squidfunk.github.io/mkdocs-material/blog/2026/02/18/mkdocs-2.0/).

#### P16 — Parsed-but-unconsumed parser keywords
Source: `docs/parser_coverage.md` (register + reproducible audit method).

**What.** Several `.s2k` values are parsed and stored on the model but reach
no consumer, so they look implemented while being inert.  The full register,
with the method to re-derive it, is `docs/parser_coverage.md`.

**Open items.**
1. `FrameElement.mirror_2` / `mirror_3` — mirror the drawn extrusion (and
   fibre mesh) for mirrored asymmetric sections.
2. `FrameElement.transform_stiffness` — apply, or explicitly refuse,
   SAP2000's stiffness transformation for the centroid offset in the builder.
3. `FrameEndOffset.rigid_factor` — support partial rigid-zone rigidity
   (`0 < RigidFactor < 1`) instead of always fully rigid.
4. `SAPModelData.area_edge_constraints` — consume it or drop it (the
   preprocessor currently derives edges via `find_constraint_edges()`).
5. `AreaMesh.no_auto_mesh_at_edges` / `no_sub_mesh` / `min_size` — honour in
   the area-meshing path, or drop.
6. Encased / circular-concrete / double-angle section fields — consume once
   those section types are implemented (overlaps P6b).

**Outline steps.** The items are independent; take them opportunistically.
When one is wired up, delete its row from `docs/parser_coverage.md` and record
the change in the DONE register below.

**Tooling.** The *table*-level companion to this register is
`docs/parser_coverage.md` § *Table coverage* +
`src/fea_toolkit/io/table_registry.py`: `fea-tables model.s2k` (or
`python -m fea_toolkit.io.table_registry model.s2k`) reports tables the toolkit
does not consume, and the review report includes the same section.

#### P17 — Bilinearizer negative-ordinate contract (revisit)
Source: `docs/csm_bilinearization.md` § *Edge Cases*;
`tests/test_csm.py::TestBilinearization::test_noisy_curve_with_negative_sa`.

**What.** The four bilinearizers document `S_a_arr` as **non-negative**
(`Args` in `model/csm.py`) and do not screen negatives themselves.  The only
production caller, `compute_performance_point()`, folds a -X/-Y push with
`np.abs()` and masks ordinates below `-1e-12` before dispatch, so **live CSM
results are unaffected** — the open question is whether the *methods* should
tolerate negative ordinates, or whether the non-negative input stays a
documented caller obligation.

**Evidence (measured 2026-09-17).** Bilinear+hardening curve
(`S_d = linspace(0, 0.08, 41)`, knee at 0.02) carrying one sentinel negative
ordinate `S_a[3] = -5.0` at `S_d = 0.006`, passed **raw**:
1. `bilinearize_stiffness_change` → `(0.006, -5.0)`: the negative sample wins
   criterion A (secant `-833 < 0.5 * K_init`) and is adopted **verbatim as the
   yield point**, i.e. a negative yield acceleration.
2. `bilinearize_composite` → `(0.008, 40.0)` — positive only because the 10 %
   clamp re-interpolates just past the negative sample.
3. `bilinearize_equal_energy` → `(0.08, 130.0)` and `bilinearize_rc` →
   `(0.0195, 97.4)` — positive, but their area integrals silently include the
   negative ordinate (slightly biased `A_cap`).

So a raw negative ordinate yields *inconsistent* results across the four
methods rather than a clean error.

**Options.** (a) **Keep the contract caller-side** (current state): the `Args`
precondition stands and docs/test describe it — wording already updated in
`docs/csm_bilinearization.md` and the test docstring.  (b) **Make the methods
negative-tolerant**: skip `S_a <= 0` samples in the stiffness-change
criterion A/B scan and mask negatives before the area integrals, which makes
the former docs claim ("negative values skipped") true and lets the test feed
`S_a` unfiltered.

**Next step.** Decide (a) vs (b).  If (b): add the skip/mask in
`src/fea_toolkit/model/csm.py`, flip `test_noisy_curve_with_negative_sa` to
pass raw `S_a` and drop its out-of-contract guard, then re-run the CSM suite.

#### Closed items (README reconciliation, 2026-08-25)
- **Deeper opstool result-post-processing integration** — closed as **no
  current demand** (`docs/report_generation.md`); NPZ ↔ opstool ODB
  converter deferred until demand exists.

## DONE (2026-09-17 — results NPZ schema-version marker (P18))

**What.** The **stage file** stamped itself with a top-level
`schema_version` array, and the model-codec payload carried
`__schema_version__` — but a **plain `.npz` results archive** was
unversioned, so a consumer had to infer the layout from the presence of
optional arrays.

- **Writers.** `write_results_npz()` and `write_pushover_results_npz()`
  (`npz_writer.py`) plus `write_results()` (`unified_writer.py`) now write
  `arrays["schema_version"] = np.array([SCHEMA_VERSION], dtype=int)` — the
  same key/dtype the stage file already used.  All three plain-results
  writers are stamped, so the marker is uniform across writers.
- **Reader.** `get_schema_version(data)` moved from `stage_reader.py` to
  `npz_reader.py` (its natural home — it reads a `schema_version` array
  from any flat results dict) and is still exported from `fea_toolkit.io`;
  `stage_reader` no longer owns it.  Files with no marker still read as
  `SCHEMA_VERSION_LEGACY` (1).
- **Distinction kept.** The *model-object* marker
  (`model_codec.MODEL_SCHEMA_VERSION` / `__schema_version__`) stays distinct
  from the *file-level* marker (`results_schema.SCHEMA_VERSION` /
  `schema_version`).  `SCHEMA_VERSION` was **not** bumped — the change is
  additive; bump only on a backward-incompatible array-layout change.

**Tests.** `tests/test_stage_file.py::TestUnifiedWriterSchemaCoverage`:
the marker is stamped and read back for all three writers, `validate_npz`
stays clean, and the legacy / empty / unreadable-marker defaults.

**Validation.** `tests/test_stage_file.py` 34 passed;
`tests/test_stage_file.py tests/test_rhino_results.py` 79 passed; ruff clean.

## DONE (2026-09-17 — split the `examples/view_model.py` tests into their own file)

**What.** `tests/test_viz_model.py` had grown past 1700 lines and hosted tests
for `examples/view_model.py` (mode index, constraint highlighting, `--select`
warnings, input policy) alongside tests for `plotting/viz_model.py` — the test
file no longer mirrored the source it was named after (`.clinerules` §1.4).

- `TestViewModelModeIndex`, `TestViewModelConstraintHighlight`,
  `TestViewModelSelectionExpression` and `TestViewModelInputPolicy` moved to
  the new `tests/test_view_model_cli.py`, which mirrors
  `examples/view_model.py`.  `tests/test_viz_model.py` again covers only the
  plotting layer.  Both files' docstrings now state the split and cross-
  reference `tests/test_model_loader.py` for model-file dispatch.
- The moved file carries its own local `_selection_model()` scenario builder
  (scenario data stays with the test that owns it) and drops the `pathlib`
  import the pre-split file needed only for its fixtures path.

**Validation.** `tests/test_view_model_cli.py` 19 passed and
`tests/test_viz_model.py` 61 passed; full suite 1707 passed, 2 skipped,
2 xfailed; ruff clean.  Pure move — no behaviour change.

## DONE (2026-09-17 — one constraint resolution path + the source-coverage policy)

**What.** Two follow-ups from the architecture review of the constraint work.

- **One resolution path.** `--highlight-constraint` (red) and `--select
  "constraint=..."` (yellow) resolved constraint names to joints independently.
  `constraint_node_colors` now resolves the highlighted set through
  `Selection(constraints=[...]).get_node_ids(md)`, so the two paths cannot
  drift; its per-name report still reads `constraint_assignments` directly,
  because it counts *assigned* joints (including any the model dropped), not
  selected ones.  The `--highlight-constraint` help points at the Selection
  equivalent.
- **Policy documented.** The `Selection` docstring gains a *Source coverage*
  table (which criteria resolve against which representations — `constraints`
  is `SAPModelData` / `ResolvedSource` only, `story` needs `storey_data`,
  sections / materials / elevation work on `MeshModel`) and a *Failure policy*
  paragraph making the permissive-vs-strict split explicit: the query layer
  matches nothing for a criterion the source cannot resolve, while consumers
  that *act* on the result (the viewer) raise.

**Tests.** `TestViewModelConstraintHighlight::test_red_and_yellow_paths_agree`
pins the two paths together.

**Validation.** Full suite 1707 passed, 2 skipped, 2 xfailed; ruff clean.
CLI on the BPPS pipe-rack: `--highlight-constraint Fix` still reports
`65 of 65 assigned joint(s) present in the model`.

## DONE (2026-09-17 — promote the reusable viewer pieces into the package)

**What.** Reviewing the constraint-selection work flagged that reusable logic
had accumulated in `examples/view_model.py` (an 878-line script), where no
other entry point could use it — `model/review.py` still accepted only `.s2k`.

- **`io/model_loader.py`** (new): `load_model_data(path)` dispatches
  `.s2k` / `.$2k` text, raw-table JSON (`SAP2000Parser.to_json`) and
  model-codec JSON (`model_codec.model_to_json`).  It *raises*
  (`ValueError` / `FileNotFoundError`) instead of exiting, and rejects an
  unsupported suffix rather than falling through to a text parse that would
  silently yield an empty model.  Exported from `fea_toolkit.io`.
- **`Selection.from_string(expr)`** (new classmethod): the `KEY=VALUE`
  grammar (clauses separated by `;` or whitespace, values by commas; `type`
  values case-insensitive; `z=LO:HI`) now lives with the class it builds,
  along with `SELECT_KEYS` / `SELECT_KEYS_HELP`.  An unknown `type=` value is
  now an error instead of a warning plus a selection that matches nothing.
- **`examples/view_model.py`** shrank accordingly: `parse_selection`,
  `_canonical_element_type`, the key tables and the JSON dispatch are gone;
  `load_model` is a thin wrapper turning loader exceptions into `sys.exit`.
  Its only remaining policy helper is `check_result_supported`.

**Tests.** New `tests/test_model_loader.py` (text, raw-table JSON, codec
`SAPModelData` / `MeshModel`, newer schema, malformed / unrecognised JSON,
unsupported suffix, missing file) mirrors the new module;
`TestSelectionFromString` in `tests/test_sections_selection.py` takes over the
grammar tests, leaving `TestViewModelSelectionExpression` with the CLI
warnings / exit behaviour only.

**Validation.** Full suite 1706 passed, 2 skipped, 2 xfailed; ruff clean.
CLI verified on the BPPS
pipe-rack: `.s2k`, raw-table JSON and codec JSON all resolve
`--select "constraint=Fix"` to 65 joints; `--select "type=bogus"` and an
unsupported suffix both exit with a clear message.

## DONE (2026-09-17 — `Selection.filter_model`: node-scoped subsets + constraint data)

**What.** Reviewing the constraint-selection work surfaced two gaps in
`filter_model`:

1. **Node-only selections returned an empty model** (a documented
   limitation).  Because `constraints` is inherently joint-only, that
   limitation became a silent trap: `Selection(constraints=['Fix'])`
   `.get_node_ids(md)` returned the joints while `.filter_model(md)`
   returned nothing.
2. **Constraint data was dropped from the subset**, so the "self-contained
   subset" was not self-contained: a subset exported to Rhino lost its
   `SAP_Constraint` user strings, and re-selecting by constraint on the
   subset found nothing.

- **Node-scoped selections.** `filter_model` now includes the joints the
  selection names in its own right — `element_types` naming `Node`, or a
  joint-only criterion (`constraints`).  Nodes merely *eligible*
  (`element_types is None` with only element criteria) still enter as the
  endpoints of the selected frames / areas, so a section filter cannot drag
  in every node (`_selects_nodes_explicitly()`).
- **Constraint data carried.** The subset keeps `constraint_assignments`
  pruned to the selected joints, the `constraints` definitions they
  reference, and `area_edge_constraints` for the selected areas.
- **Workflow.** `plot_mesh(subset, highlight_selection=Selection(
  constraints=[...]))` now highlights the same joints inside a subset —
  verified on the BPPS pipe-rack: a 12-frame subset around the `Fix` group
  highlights 11 of its joints, and the node-only subset (65 joints, 0
  frames) round-trips.

**Tests.** `TestConstraintSelection`: joint-only subset, restraints /
joint loads carried, constraint data pruned, re-selection on the subset,
node-scoping by element type.  `TestSelectionFilterModel`: element criteria
do not drag in every node.  Full suite 1700 passed, 2 skipped, 2 xfailed.

## DONE (2026-09-17 — codec payload is stamped with a schema-version marker)

**What.** The model-codec JSON was identifiable only by its `__type__` key and
carried no version, so a future layout change could be silently mis-decoded —
and the `MODEL_SCHEMA_VERSION` docstring claimed a version that was never
actually embedded.  This stamps the codec payload and validates it on read.

- **`io/model_codec.py`**: new `SCHEMA_KEY = "__schema_version__"`;
  `model_to_dict()` adds it (top level only, so nested dataclasses stay
  clean), and `dict_to_model()` / `json_to_model()` validate it via
  `_check_schema_version()` — absent → treated as v1 (layout unchanged);
  invalid (non-int / < 1) or newer than `MODEL_SCHEMA_VERSION` → `ValueError`
  naming the newer version.  The key is dunder-prefixed so it can never
  collide with a real model field in the flat payload.
- **Layering**: the codec `__schema_version__` versions the *model-object*
  layout; the stage/results `schema_version` array versions the *file*
  layout.  Documented in `docs/json_serialization.md` and
  `docs/model_stage_file.md` (and the codec module docstring).
- **`view_model.load_model()`** now routes to the codec on
  `__schema_version__` **or** `__type__` (legacy codec files keep working).
- **Follow-up flagged as P18** (resolved 2026-09-17 — see the *results NPZ
  schema-version marker* DONE entry): the plain `.npz` results archive carried
  no version marker (the stage file did).

**Tests.** `tests/test_model_codec.py::TestSchemaVersioning` (marker present,
absent on nested dataclasses, legacy decodes, newer + invalid rejected);
`tests/test_viz_model.py` (header in a codec JSON, newer snapshot refused by
`load_model`).

**Validation.** Full suite 1694 passed, 2 skipped, 2 xfailed; ruff clean.


**What.** The viewer ingested only `.s2k` (text) and `.npz` (archived results),
so the two JSON model representations the toolkit already produces were
unreachable from the CLI.  Handing one to `view_model` was worse than a
refusal: it fell through the text path, found no `TABLE:` lines and produced an
**empty model silently** — the trap `docs/json_serialization.md` warns about.

- **`examples/view_model.py`**: new `load_model(path)` recognises
  - `.s2k` / `.$2k` — parsed as before;
  - **raw-table JSON** (`SAP2000Parser.to_json()`), whose tables are restored
    and the model rebuilt exactly as from text, so constraints, sections,
    loads and every viewer option behave identically;
  - **model-codec JSON** (`model_codec.model_to_json()`), decoded with
    `json_to_model()`, which selects `SAPModelData` or `MeshModel` from the
    payload's top-level `__type__` key (no `cls=` needed).
  The payload shape is validated before dispatch: a dict that is neither a
  `__type__` snapshot nor a `{table: [rows]}` cache exits with a message
  naming both writers, instead of silently loading nothing.
- **`check_result_supported()`**: a `MeshModel` snapshot is already meshed, so
  it is accepted for `--result mesh` and refused (with an explanatory message)
  for the analyses, which need SAP2000 input to build from.
- CLI help, the module docstring and `examples/README.md` document the three
  model inputs; `docs/json_serialization.md` gains the viewer row.

**Tests.** `tests/test_viz_model.py::TestViewModelJsonInput`: raw-table cache
(loads the `sample.json` fixture), codec round-trip of an `SAPModelData`
(loads back `==`), codec `MeshModel` snapshot, unrecognised JSON, malformed
JSON, and the mesh-only restriction.

**Validation.** Full suite 1687 passed, 2 skipped, 2 xfailed; ruff clean.
End-to-end off-screen on the BPPS pipe-rack: the raw-table JSON, the
`SAPModelData` snapshot and the `.s2k` all report
`Selection overlay (yellow): 0 frame(s), 65 node(s), 0 area(s).` for
`--select "constraint=Fix"`; the `MeshModel` snapshot renders the 1261-element
mesh and refuses `--result static`.


**What.** Constraint groups were viewable only through the bespoke
`--highlight-constraint` (red), not through the toolkit's general query
language — so a constraint could not be *selected* the way sections, materials,
groups and elevation bands can.  This adds `constraints` to
:class:`~fea_toolkit.model.selection.Selection`, which makes constraint
membership a first-class criterion for the viewer **and** for every other
`Selection` consumer (analysis filtering, reporting).

- **Query.** `model/selection.py`: new `constraints: Optional[list[str]]`
  field, matched by `_match_constraints()` against
  `model.constraint_assignments` (joint id → constraint name), so `BODY`
  rigid bodies, `DIAPHRAGM`, `EQUAL` and `WELD` groups all resolve through
  the same table and the constraint type never needs to be known.
- **Semantics.** A *joint* constraint is a Node-only attribute, so setting
  the criterion **excludes every frame and area** (`_frame_matches` /
  `_area_matches` return `False`).  This matters: ignoring it for
  elements — the treatment `section` / `material` get on nodes — would make
  `Selection(constraints=['Fix'])` select the *whole* model whenever the
  constraint set was the only criterion.  AND/OR composition is unchanged
  (multiple names are alternatives; other criteria narrow further).
- **Source dependence.** Resolution needs `constraint_assignments`, which
  `SAPModelData` (and `ResolvedSource`) carries but `MeshModel`, a builder
  and NPZ archives do not.  `get_node_ids()` on such a source simply matches
  nothing, while the viewer's `_selection_id_sets()` **raises** a clear
  `ValueError` rather than overlaying nothing — the same contract the
  `story` criterion already uses.  All three input forms that build an
  `SAPModelData` (`.s2k`, raw-table JSON via `from_json()`, model-codec JSON
  via `json_to_model()`) therefore support it unchanged.
- **CLI.** `examples/view_model.py`: `--select` gains the `constraint` /
  `constraints` keys, e.g. `--select "constraint=Fix"`.  Two honesty
  warnings: `constraint=` combined with `type=` excluding `Node` ("matches
  nothing"), and `constraint=` combined with `section` / `material` / `z`
  (ignored for a node-only selection).  `--highlight-constraint` is
  unchanged and remains the red-ink convenience for the same joints.

**Tests.** `tests/test_sections_selection.py::TestConstraintSelection`
(resolution by name, type-agnostic DIAPHRAGM, OR across names, AND with
element ids / groups, unassigned + unknown names, frames/areas excluded,
MeshModel matches no joints, `filter_nodes`).  `tests/test_viz_model.py`:
overlay of the assigned joints as yellow dots, the resolver `ValueError` on a
MeshModel source, and the CLI key / alias / warnings.

**Validation.** `tests/test_sections_selection.py` 77 passed;
`tests/test_viz_model.py` 87 passed; ruff clean.  End-to-end off-screen render
of the BPPS pipe-rack `.s2k`:
`--result mesh --select "constraint=Fix" --node-labels` →
`Selection overlay (yellow): 0 frame(s), 65 node(s), 0 area(s).` — the same 65
joints `--highlight-constraint Fix` reports, now through the general
Selection path.


**What.** ``examples/view_model.py`` could highlight *sections* and *constraint
groups*, but there was no way to pick elements/nodes with the toolkit's own
:class:`~fea_toolkit.model.selection.Selection` criteria (sections, materials,
groups, IDs, elevation band) and see them in the 3D view.

- **Plotting.** ``plot_mesh()`` (and ``compare_meshes()``, per source) gains
  ``highlight_selection`` — a ``Selection``, a sequence of them, or an explicit
  ``{"frames": [...], "nodes": [...], "areas": [...]}`` mapping for a
  model-less NPZ dict.  Matches are **overdrawn**, never removed: wide
  (``line_width=10``) half-opaque (``0.5``) lines over frame elements, large
  (``point_size=18``) translucent dots over nodes, translucent faces over
  areas — so frame-only, node-only and combined selections all work.  Colour is
  overridable via ``selection_color`` (default ``"yellow"``).
  ``plotting/viz_model.py``: new ``_selection_id_sets`` (resolve, model-backed
  or explicit IDs), ``_draw_selection_overlay``, ``_unique_nodes`` (extracted
  tag-dedup, shared with the ``node_colors`` path), ``_source_model`` and
  ``_expand_split_frames``.
- **Split-element IDs.** A parent ID reaches its children and a child ID its
  parent + siblings, so an ID selection works whichever space it names — and
  keeps working under ``collapse_to_parents=True``, where the children the
  selection resolves to are drawn as their parent.
- **Failure modes are loud.** A ``Selection`` on a data-dict source raises
  ``ValueError`` ("pass explicit IDs instead"); a non-``Selection`` entry raises
  ``TypeError``; the ``story`` criterion raises (it needs storey data this
  resolver is not given — use ``elevation_range``); a selection that matches
  nothing *rendered* warns instead of silently doing nothing.  Node matching
  uses the mapping key only — no OpenSees-tag fallback, since tags and SAP
  joint labels are different numbering spaces (a tag fallback would colour a
  wrong joint).
- **CLI.** ``examples/view_model.py``: new repeatable
  ``--select "KEY=VALUE[,VALUE ...][; KEY=VALUE ...]"`` (mesh view) with keys
  ``type`` / ``section`` / ``material`` / ``group`` / ``id`` / ``z``.
  ``type`` values are canonicalised (``frame`` → ``Frame``); clauses may be
  space- or semicolon-separated and values may contain spaces
  (``section=Slab 200mm``); bad keys/values exit with the list of valid keys.
  A Node-only selection with ``section`` / ``material`` / ``z`` warns that
  those criteria are ignored (``Selection`` matches nodes on type/id/group
  only).  NPZ archives say that ``--select`` needs a ``.s2k``.

**Tests.** ``tests/test_viz_model.py``: ``TestSelectionOverlay`` (frame lines,
node dots, combined, group-based node selection, area faces, colour override,
empty-selection warning, NPZ raises, explicit IDs, ``story`` rejected,
non-``Selection`` rejected), ``TestSplitElementSelectionExpansion``
(parent↔child expansion, resolved ID sets) and
``TestViewModelSelectionExpression`` (grammar, aliases, case-insensitivity,
space-separated clauses, error messages, node-only criterion warning).

**Validation.** Full suite ``1662 passed, 2 skipped, 2 xfailed``; ruff clean.
End-to-end off-screen render of the BPPS pipe-rack `.s2k`:
`--select "type=Frame; section=2xR3"` → ``Selection overlay (yellow): 40 frame(s),
0 node(s), 0 area(s).``; `--select "id=298"` → ``40 frame(s), 1 node(s)``
(frame 298 is 2xR3; joint 298 is a separate label space).

## DONE (2026-09-17 — `view_model`: highlight the joints of a SAP2000 constraint group)

**What.** `examples/view_model.py` could highlight *sections*
(`--highlight-section`), but had no way to show which joints belong to a
SAP2000 constraint group — the gap that made the BPPS pipe-rack `Fix` BODY
constraint (65 upper-deck joints tied as one rigid body) invisible in the
toolkit's own viewer.

- **Plotting.** `plotting/viz_model.py`: `plot_mesh()` /
  `_render_scene()` gain `node_colors` — an optional ``{node_id: color}``
  mapping drawn as a second, larger point cloud on top of the plain black
  markers.  It mirrors the existing `section_colors` API, but matches the
  **mapping key** (SAP joint label for an `SAPModelData` / NPZ source)
  rather than a tag, so no informal numeric fallback can colour the wrong
  node.  With `show_node_labels`, a highlighted node is labelled with that
  key and the others keep their OpenSees tag — the tag is a different
  number from the joint label a `.s2k` constraint table lists.  The
  tag-deduplication loop now keeps the mapping key alongside each unique
  node (`unique_keys`) to make the match possible.
- **CLI.** `examples/view_model.py`: new `--highlight-constraint NAME [NAME
  ...]` (mesh view) resolves each name against `md.constraints` and paints
  every joint in `md.constraint_assignments` for it red, reporting
  `Name (TYPE): n of m assigned joint(s) present in the model`.  The
  resolution is deliberately **type-agnostic** — `BODY`, `DIAPHRAGM`,
  `EQUAL`, `WELD`, … all read the same assignment table.  `--node-labels`
  was added at the same time (there was previously no way to label nodes
  from the CLI).  NPZ archives carry no constraint tables, so the option
  says so instead of silently doing nothing.

**Tests.** `tests/test_viz_model.py`: `TestNodeHighlighting` (spy on
`pv.Plotter.add_mesh` / `add_point_labels` — asserts the plain/coloured
split, that keys are node IDs not tags, and that a highlighted node is
labelled with its key) and `TestViewModelConstraintHighlight` (unused
option, BODY group resolution, multi-group union, unknown name, joints
absent from the model).

**Validation.** `tests/test_viz_model.py` 53 passed;
`tests/test_plotting.py` + `tests/test_main.py` +
`tests/test_renderers_pyvista.py` 52 passed.  End-to-end off-screen render
of `BPPS_Pipe_Rack_SAP2000_v25_1_0_Pipe Dead Load Update.s2k` with
`--result mesh --highlight-constraint Fix --node-labels`:
`Highlighting constraint 'Fix' (BODY): 65 of 65 assigned joint(s) present
in the model.`

## DONE (2026-09-17 — `model`: `remove_floating_nodes` drops the removed joints' own loads)

Review of `tests/test_sap_data.py` (the three floating-node tests) surfaced a
producer leak, not a test-only problem:

- **Bug.** `remove_floating_nodes()` appended the transferred `JointLoad`
  copies on the nearest connected node but left the floating node's *own*
  entries in `md.joint_loads` after deleting the node.  Load **application**
  skips unknown node ids (`opensees/_loads.py` → `if node is None: continue`),
  so the domain was correct, but `_mass_from_joint_loads()` keys by node id,
  so the same physical load was counted twice — once on the neighbour and once
  on the phantom id — inflating the reported seismic mass.  Measured with one
  -100 kN `DEAD` joint load on a floating node and `g = 9.81`: total
  20.39 → 10.19 model mass units after the fix.
- **Fix.** `model/geometry_mesh.py`: filter `md.joint_loads` for the removed
  node ids after the removal loop; docstring step 5 + `Modifies` line updated.
- **Tests.** `test_remove_floating_nodes_transfers_joint_loads_per_pattern`
  now asserts the exact final load list (nothing references the removed node)
  instead of filtering the stale entries out of the assertion; the same test's
  docstring records the deletion contract.  Opportunistic weakenings fixed
  alongside: `TestTrapezoidalForceSplit.test_split_at_midpoint` dropped a
  vacuous `or` (both segment ends are 10, so the OR was always true), and
  `test_concrete_rect_no_rebar` now asserts the exact patch count (5) rather
  than `>= 3`.
- **Validation.** `tests/test_sap_data.py` 109 passed; full suite 1626 passed,
  2 skipped, 2 xfailed.

## DONE (2026-09-16 — SAP2000 table-coverage detection: registry + drift guards)

**What.** The parser reads *every* table in a ``.s2k`` / ``.json`` into
``raw_tables`` and silently ignored anything it did not know — so a table
SAP2000 newly introduces (or one the toolkit has never handled) left no trace.
There was also no registry of which tables are deliberately skipped versus
genuinely missing.

**Delivered.**

1. **`io/table_registry.py`** — the registry and triage engine.  Every table is
   classified ``handled`` / ``known-gap`` / ``ignored`` / ``unhandled``;
   ``table_coverage()`` and ``unhandled_tables()`` expose it, and
   ``format_table_coverage()`` renders it.
2. **Three surfaces** (per the "Option 3" decision):
   - **parser warning** — ``SAP2000Parser.parse(warn_unhandled=True)`` logs
     each unrecognised table (off by default);
   - **review report** — a ``Table coverage`` section in both the text and
     Markdown reports, expanded with ``fea-review model.s2k --tables``;
   - **standalone command** — ``python -m fea_toolkit.io.table_registry
     model.s2k`` (console script ``fea-tables``); exits ``1`` when
     unrecognised tables are present, so it works as a CI gate.
3. **Drift guards** (`tests/test_table_registry.py`, 35 tests):
   - every table name the parser reads — extracted from `s2k_parser.py` by AST,
     covering exact reads, variant tuples, prefix families and inline tuples —
     must be registered.  *This is the same class of bug that let the parser
     read the cardinal point from the wrong table for several releases*;
   - every table in the committed fixtures must be known to the registry;
   - the generated registry block in `docs/parser_coverage.md` must match the
     code (`docs/_generate_parser_tables.py --check`).
4. **Docs** — `docs/parser_coverage.md` gained the table-coverage half
   (bucket definitions, the three surfaces, the drift guards) alongside the
   existing keyword register; its registry block is generated from the module.

**Found immediately.** Running it on the local models surfaced
`AREA SECTION PROPERTY - TIME DEPENDENT` as unhandled on the first pass — now
triaged as *ignored* (creep / shrinkage), which is exactly the intended
workflow: run the tool, triage the surprise, registry stays current.

**Verification.** All committed ``.s2k`` / ``.json`` fixtures are CLEAN
(``sample.split.json`` excluded — it is a toolkit-internal split dump, not an
SAP table export).  Full suite 1577 passed / 2 skipped / 4 xfailed.

## DONE (2026-09-15 — RS element-force extraction: the bulk recorder alternative, measured)

**Question asked.** Is there a bulk alternative to the per-mode extraction loop
(``n_modes × n_elements`` ``eleResponse`` calls), and what does the current one
actually cost?

**Answer — it exists, and it is slower.**  ``ops.responseSpectrumAnalysis``
processes **all** modes when ``-mode`` is omitted, and invokes every previously
defined recorder after each mode step, so one ``Element`` recorder captures the
whole ``mode × element × component`` block in a single pass.  That is the
documented path (OpenSees manual, "Example 3", first variant: *"called for all
modes.  Results are obtained from a recorder after the analysis."*).

Implemented as ``extract_element_rs_forces(..., extraction="per_mode"|"recorder")``,
also selectable through the builder config key ``element_extraction`` and the CLI
flag ``--rs-element-extraction``.  **``per_mode`` remains the default.**

**Measured** (pipe rack, 1263 active frame elements × 20 modes):

| Strategy | Time | Notes |
|---|---|---|
| ``per_mode`` | **0.09 – 0.47 s** | 25 260 ``eleResponse`` calls at ~7 µs each |
| ``recorder`` (text) | 0.72 s | 0.57 s writing 6.9 MB of 17-digit ASCII, 0.15 s parsing |

**Bit-identical.**  Parity is asserted by
``tests/test_workflows.py::TestElementRsForceExtraction`` (exact equality, both
``elasticBeamColumn`` and ``forceBeamColumn``), and the block-expansion rules are
pinned against ``_normalise_frame_response`` for 1/6/12/14-value responses so
truss models are handled too.

**The earlier "the per-mode extraction loop is the dominant cost" note was
wrong** — it had never been measured.  At ~0.1–0.5 s the extraction is not a
bottleneck on these models; the modal (eigen) pass dominates.  The recorder's
advantage is *call count*, not wall time, so it only pays off if per-call
overhead grows.

**The ``-binary`` recorder is broken — do not use it.**  20× faster to write
(0.029 s) but mode 1 is bit-exact while modes 2+ return **uninitialized
memory** (``-1.3e-152``, diffs to ``1.8e+308``).  Full contract and evidence:
``docs/dev_notes.md``.

Output is unchanged (same ``rs/elem_*`` block), so no archive re-export is
needed; the option only changes how the numbers are obtained.

## DONE (2026-09-14 — modal annotation: period + six-DOF participation, 1-based `--mode`)

**On-plot annotation.**  `plot_mode_animation` now draws the mode period and the
six-DOF mass-participation ratios as percentages, alongside the existing
`Mode N  T = … s` title:

```
Mass participation (%):
  X   0.03%    Y  25.30%    Z   0.00%
 RX   1.50%   RY   3.50%   RZ   5.50%
```

**Source of the numbers — OpenSees, never recomputed.**  `run_modal_analysis()`
stores `ops.modalProperties("-return", "-unorm")` as
`modal_result["modal_props"]`; the new public
`plotting.mass_participation_ratios()` reads the six `partiMassRatiosMX/MY/MZ`
and `partiMassRatiosRMX/RMY/RMZ` entries straight out of it.  No participation
factor, effective modal mass or `phiᵀMι` is computed anywhere in the display
path, and a test pins verbatim pass-through — a wrong reading here would be a
physics bug, not a cosmetic one.

**NPZ archives now carry all six ratios — but the mapping was duplicated.**
The `modal/*` block was produced by **two near-verbatim copies** of the same
collector: `unified_writer.collect_modal_arrays` (reached by `write_results`,
i.e. the model-review export that wrote the pipe-rack archive) and
`npz_writer._collect_modal` (reached by `write_results_npz` and
`stage_writer`).  Both mapped only `partiMassRatiosMX/MY/MZ`.  OpenSees had
always returned the rotational trio as well, so the omission was in the
extraction map, not the data.  Fixing only `npz_writer` left the review path
writing three columns, which is how the duplication was found; the two are now
one — `npz_writer._collect_modal` delegates to
`unified_writer.collect_modal_arrays`, and a test pins them in step.
`results_schema.MODAL_ARRAYS` lists the new keys.
**Archives written before this change omit `modal/{rx,ry,rz}_ratio`** — the
annotation then shows only X/Y/Z (absent DOFs render `--`, never a misleading
`0.00%`).  Re-export through the review path to get all six in the archive;
viewing a `.s2k` directly reads live `modalProperties()` and always shows six.

**1-based `--mode`.**  `examples/view_model.py` now presents modes 1-based
(`--mode 1` = first mode, and the default), matching the `Mode N` label and the
1-based `mode` field of the mass-participation records.  `mode_index()` converts
at the CLI boundary and rejects `--mode 0`; the Python API stays 0-based.

**Validation.**  Full suite `1519 passed, 1 skipped, 4 xfailed`; `ruff check` +
`ruff format --check` clean; `mkdocs build --strict` exit 0.  Verified against
the real pipe-rack archive: `--mode 1` → `Mode 1  T = 0.3552 s` with
`X 0.03% / Y 25.30% / Z 0.00%`; `--mode 5` → `T = 0.2272 s`.

## DONE (2026-09-14 — mode-shape amplitude + frozen animation fix, PyVista API audit)

**Two viewer bugs reported together.**  The `84d747b` mode-shape normalisation
left the exaggeration too large, and the mode-shape animation only repainted
when the user clicked or dragged.

**Amplitude** (`f1f092f`).  `plot_mode_animation`'s `scale` is a percentage of
the model's largest bounding-box dimension; the default moves **10 → 5 %**
(3.9 m peak on the 78 m pipe rack, previously 7.8 m).  `--mode-scale`,
`show_modal` and `show_rs` follow.  Verified exactly: the default gives a
0.200 peak on the 4-unit test span (5.00 %) versus 0.400 (10.00 %) before.

**Frozen animation** (`f1f092f`).  `_add_animation_timer` called
`plotter.add_timer_event(max_steps=..., interval=..., callback=...)`.  No
PyVista release ever accepted `interval` — the keyword has been `duration`
since `add_timer_event` was introduced in 0.43 (PR #4839) — so the call raised
`TypeError` on every version, the fallback chain swallowed it, and the helper
silently landed on the low-level VTK `AddObserver("TimerEvent")` path.
PyVista's own `Timer.execute` calls `iren.GetRenderWindow().Render()` after
each tick (PR #5618); the raw VTK observer does not, so mesh geometry was
updated in memory but never repainted — hence "redraws only on click".

Fix: call the documented `duration=` keyword, and return whether PyVista's
timer took over (`True`) or the non-rendering VTK path was used (`False`) so
`plot_mode_animation` renders explicitly only in the latter case — no
redundant render at 60 Hz on the happy path.  Verified against the installed
PyVista 0.48.1: a single `add_timer_event` call with kwargs
`['callback', 'duration', 'max_steps']` succeeds and the helper returns `True`.

**Upstream API audit (this is the second time this API bit us).**  Read the
real source rather than inferring: the callback receives **exactly one**
argument, `step`, in 0.43, 0.44 and `main` — the `(step, plotter)` claim in
`viz_common.py` and `TestAnimationTimerCallbackArity` was **never true**, and
the two phantom strategies (`interval`, and "no duration kwarg") defended
against APIs that do not exist.  Both are removed; the verified contract,
with citations, is now recorded in `docs/dev_notes.md`.  The arity adapter is
kept but re-documented honestly — its only load-bearing rule is truncating
the `step` PyVista always supplies so `animate_pushover_deformation`'s
zero-argument `_timer_callback` does not raise `TypeError`.

**Prevention (round 2).**  The fix was only half the job — the contract also
had to land where contributors and agents actually look.  `docs/dev_notes.md`
holds the evidence table and the two lessons; `.clinerules` gains anti-pattern
13 (*never guess a keyword to satisfy a `TypeError`*) plus a new §12
*Third-Party API Assumptions*, whose §12.1 pins the timer contract; and
`docs/llm_guide.md` gains the same summary under Visualisation plus a
Quick-Debugging entry for "animation only redraws on click".  Two tests now
exercise the contract on the **installed PyVista** rather than a fake
(`test_real_pyvista_native_timer_is_used`,
`test_real_pyvista_timer_passes_step_and_renders_each_frame`): they register the
adapter on a real `pv.Plotter`, fire a real `TimerEvent`, and assert that the
callback receives the step count and that a frame was rendered per step — so a
renamed `duration` keyword or a dropped `Render()` fails loudly instead of
silently degrading onto the VTK path.

**Validation.**  Full suite `1508 passed, 1 skipped, 4 xfailed`;
`mkdocs build --strict` exit 0; ruff clean.  New tests assert *which keyword*
is passed (`test_modern_pyvista_uses_duration_kwarg`,
`test_pyvista_signature_mismatch_falls_back_to_vtk`) — the gap that let the
original bug through, since the old fakes accepted any keyword.

## DONE (2026-09-13 — CLI API listing, rhino/io/plotting hardening)

**CLI (`python -m fea_toolkit`).** Added a lazy API listing that enumerates
`__all__` across the package and subpackages without importing the optional
backends (`8131293`, `ca2fb82`); alias re-exports are resolved by their
original imported name (`5637ac5`), filtered `--details` listings stay
lazily loaded (`7a326e4`, `0094683`), and a failed `--details` import is
reported inline and skipped rather than aborting the run (`ed25e90`).

**Rhino export.** Added swept-Brep handling, docstrings and ETABS guards
(`c785738`) and corrected the model-import data flow (`be0dbaa`).

**I/O.** File choosers now offer the accepted model formats and no longer
clobber the JSON cache (`2750dac`); docs/CLI help aligned with the accepted
formats (`533e253`) and covered by tests (`34b623b`).

**Plotting.** `view_model()` gained mesh-view options and a
`section_colors` passthrough (`15afbd8`); mesh-only options now warn when
ignored for static/modal NPZ inputs (`aa7df29`).

**Docs.** Silenced both MkDocs 2.0 notices and fixed a broken
`rhino_export` anchor (`e0acae0`); recorded the pinned docs toolchain and
the MkDocs 2.0 / ProperDocs decision as P15 (`5df3144`).

## DONE (2026-08-26 — area-load distribution fix + load-verification restore)

**Project A v3 report / static-load verification.** The two-stage
migration broke `load_pattern_totals()`/`static_load_verification()`:
`load_totals` became a scalar magnitude populated only by
`create_loads()`, which was never called → the verification always raised
`KeyError: 'Load Pattern'` and the self-weight check passed vacuously
(0/0).  Restored v1 semantics (per-pattern per-component applied totals
= self-weight + gravity + joint + distributed) and re-verified against
the legacy builder's formula.  Also fixed a stale-`ni`/`nj` bug that
made the distributed-load applied totals omit the element-length factor.

**Area-load distribution (nearest-supported-edge partition).**
`convert_area_loads_to_edge_loads()` used a centroid rule that produced
exactly **2×** the panel load on fully-supported panels and silently
dropped load on panels with unmatched edges (Project A Wind +X was
carried at +78 %).  Replaced with a Voronoi / nearest-supported-edge
partition that reproduces the standard 45° yield-line pattern for
rectangles (long edges 75 % / short edges 25 % on a 2:1 panel), reduces
to the one-way rule for two opposite edges, honours SAP's `OneWay` flag,
conserves the total exactly (Σ tributary areas ≡ panel area) for any
combination of supported edges, and warns when a panel has no supported
edge.  Area gravity loads are now accumulated into the applied-load
totals (they were applied via `ops.load` but never recorded → spurious
2.1 % / 19.7 % gravity "mismatch" warnings); DEAD/DEAD SDL/LL now show
Δ = 0.0 and the Project A wind loads are exact `P × A`
(2953 kN = 3.04 kN/m² × 971 m²).  SAP "AREA LOADS - UNIFORM TO FRAME"
(OneWay/TwoWay) is now parsed and routed to the edge partition;
plain "AREA LOADS - UNIFORM" on shell elements goes to the panel's own
nodes (create_shells=True), matching SAP's Uniform-(Shell) semantics.

**Impact on published results:** Project A Wind ±X 5265 → 2953 kN,
Wind ±Y 587 → 395 kN; Project B LL (all 124 loads are area-uniform
`Gravity`) reduces accordingly.  `project_a_v3.h5` and `project_b_v13.h5`
re-exported.  Tests: `tests/test_area_load_distribution.py`
(conservation for 4/3/2/1 supported edges, 45° split, OneWay flag,
no-edge warning, shell nodal-pressure path).

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
  classification, wrapper call patterns) in `tests/test_force_diagram.py`.
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
- v4/v5 hand-rolled push loop for the Project B stalls at ~0.006 m control
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
- Controlled probe (`local/probe_layered_shear_correct.py`), exact Project B
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
- `project_b_pushover_checks_v8.py` now supports the sectional-average wall basis:
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
- **`local/<private_project>/<driver>.py`** is
  the v8 clone; it retains the verified-working `ConcreteS` smeared-crack
  wall concrete and documents the PSUMAT restriction in its module
  docstring.  Outputs renamed `*_v9.*`.
- Validation: `tests/test_layered_shell.py` + `test_units.py` +
  `test_model.py` → 359 passed.
