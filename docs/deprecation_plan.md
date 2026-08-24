---
title: "Deprecation Removal Plan"
description: "Plan for removing deprecated APIs after the RC nonlinear static analysis release."
status: "complete"
tags: [planning, deprecation, cleanup]
category: [planning]
---

# Deprecation Removal Plan

**Status:** ✅ **Complete — implemented 2026-08-21; Phase B legacy-wrapper
removal completed 2026-08-24.**

## Summary of removal

All items in the inventory below have been actioned:

- **Plotting (`viz.py`)** — the 9 deprecated functions were removed
  (`plot_model_3d`, `plot_deformed_3d`, `plot_rs_deformed_3d`,
  `plot_mode_3d`, `plot_static_moment_3d`, `plot_static_shear_3d`,
  `plot_static_axial_3d`, `plot_static_force_diagram`,
  `plot_force_diagram`) together with the private helpers used only by
  them (`_get_local_end_forces`, `_plot_moment_flags`, `_plot_moment_tubes`,
  `_add_reaction_arrows`, `_build_shell_geometry`).  `_build_deformed_mesh`
  is retained — it is used by the replacement `plot_mode_animation()`.
- **`plot_force_diagram` → `plot_rs_force_diagram`** — the pre-unification
  2D CQC diagram implementation was removed and its behaviour folded into
  a modernised `plot_rs_force_diagram` (accepts the RS `element_results`
  list **or** the full `extract_element_rs_forces()` dict, plus
  `force_unit` / `length_unit` / `both_ends` options).  The
  `plot_force_diagram` **name** was later reclaimed in Phase B as the
  unified dispatcher — see the next section.
- **Exports** — `plotting/__init__.py` and the root `__init__.py` no
  longer expose the deprecated names; the canonical quick-start now uses
  `plot_mesh` / `plot_deformed_displacement_3d`.
- **`model/sap_data.py`** — the 4 deprecated unit-conversion aliases were
  removed.
- **Stale `OpenSeesBuilder` references** — corrected in `io/report.py`,
  `analysis_builder.py`, and `viz.py`.
- **RC pushover Tcl path** — reorganised: the orchestration moved to
  `analysis/pushover_tcl.py` (`run_rc_pushover_tcl`), `analysis/pushover.py`
  is a thin dispatcher, and `use_tcl_fallback` is kept as an **alternate
  backend** (its `DeprecationWarning` was removed).
- **CI** — `mkdocs build --strict` is restored as the gate in
  `.github/workflows/docs.yml`.

## Phase B — force-diagram unification (implemented 2026-08-24)

> Design and status → `docs/force_diagram_unification.md`; tracked in
> `docs/_pending_work.md` (P1, DONE).

Combined `plot_rs_force_diagram()`, `plot_force_diagram_3d()`,
`plot_npz_force_diagram()` and `plot_npz_moment_3d()` into a **single
unified, unit-aware** `plot_force_diagram()` entry point that:

1. Covers **all inputs** — `AnalysisBuilder`, in-memory result dicts
   (including RS `element_results`), and NPZ paths.
2. Is **unit-aware** — reads units from builder/result metadata/NPZ rather
   than hardcoding `kN`/`m`.
3. **Dispatches 2D vs 3D** and **static vs CQC-RS** from the input shape.

Naming decision: **`plot_force_diagram`** is the unified dispatcher.  The
four legacy names were kept as thin signature-preserving wrappers for one
release cycle, then **removed in a single cleanup PR on 2026-08-24** (same
pattern as the Phase 3 removal above).

**Follow-up hardening (2026-08-24 — same change-set as the doc
clarifications above):**

- `io/unified_writer.py::write_results` now validates
  `forces_coordinate_system` before serialising — only `"local"` /
  `"global"` accepted, casing variants (e.g. `"Local"`) rejected, so NPZ
  readers never interpret an invalid value as global forces.
- `plotting/force_diagram.py::_npz_unit` takes an explicit default;
  legacy NPZ files without `force_unit` / `length_unit` metadata now fall
  back to `"kN"` / `"m"` instead of `"?"`.
- `_render_rs` labels `Fx` / `Fy` / `Fz` with `force_unit` (previously an
  empty string), matching the 2D static renderer; moment/shear unchanged.
- `_resolve_source` runs the RS branch only when `source` is a list or a
  dict carrying `element_results` — builder sources with `kind="rs"` now
  reach the documented Builder+RS handling later in the function.
- The NPZ array contract is clarified in
  `docs/force_diagram_unification.md`: arrays are indexed in frame-element
  order (`n_frame_elements` = active `mesh_model.frame_elements`).

## Trigger

The **next release** ships with RC nonlinear static analysis working
comfortably (fiber sections on RC frames, validated pushover).  The
current batch of `DeprecationWarning`-emitting functions have served
their purpose — downstream users have had one full release cycle to
migrate to the replacement APIs.

After that release, the items listed below will be removed in a
single cleanup PR, and `--strict` mode will be re-enabled in the
documentation CI.

---

## Inventory

### 1. Plotting — `src/fea_toolkit/plotting/viz.py`

| Remove | Replacement | Est. lines |
|---|---|---|
| `plot_model_3d` | `plot_mesh` | ~100 |
| `plot_deformed_3d` | `plot_deformed_displacement_3d` | ~80 |
| `plot_rs_deformed_3d` | `plot_deformed_displacement_3d` | ~70 |
| `plot_mode_3d` | `plot_mode_animation` | ~180 |
| `plot_static_moment_3d` | `plot_force_diagram_3d` | ~70 |
| `plot_static_shear_3d` | thin wrapper | ~5 |
| `plot_static_axial_3d` | thin wrapper | ~5 |
| `plot_static_force_diagram` | `plot_npz_force_diagram` | ~110 |
| `plot_force_diagram` (pre-unification 2D CQC impl) | (no direct replacement — 2D CQC diagram; name now the Phase B unified dispatcher) | ~50 |
| Private helpers: `_get_local_end_forces`, `_plot_moment_flags`, `_plot_moment_tubes`, `_add_reaction_arrows` (used only by deprecated fns) | — | ~150 |

**Total removed: ~820 lines**

### 2. Plotting — `src/fea_toolkit/plotting/__init__.py`

Remove 9 deprecated names from imports and `__all__`.  The replacements
(`plot_mesh`, `plot_deformed_displacement_3d`, `plot_mode_animation`,
`plot_force_diagram_3d`, `plot_npz_force_diagram`) are already exported.
Also remove `_build_deformed_mesh` from the public re-export list — it is
an internal helper, not part of the public API.

### 3. Utils — `src/fea_toolkit/utils.py`

Remove legacy underscore-prefixed aliases (e.g. `_deep_merge` → `deep_merge`).
Audit internal callers first; if any remain, update them to use the
non-underscore names.  **(Status: ✅ Done — removed 2026-08; all four
alias wrappers deleted, `local/` callers updated to use the plain names.)**

### 4. Opensees — `src/fea_toolkit/opensees/builder.py`

**(Status: ✅ Done — removed 2026-07.)**  The legacy `OpenSeesBuilder`
class no longer exists anywhere in `src/`.  The builder split is
complete: topology mutation lives in `opensees/preprocessor.py`,
OpenSees domain construction + analysis execution live in
`opensees/analysis_builder.py`, and `opensees/builder.py` now only
exports standalone Tcl-export functions (no analysis logic).

### 5. IO / Report — `src/fea_toolkit/io/report.py`

Several functions reference the legacy `OpenSeesBuilder`.  Audit for
remaining call paths; remove or migrate to `AnalysisBuilder`.

### 6. Model — `src/fea_toolkit/model/geometry.py`

The `split_elements` deprecation is informational — no code removal
needed here unless the underlying legacy split path is also being
retired.

---

## Implementation Steps

1. **Audit callers** — scan `local/`, `examples/`, and downstream
   scripts for remaining uses of deprecated names.  Update or notify
   downstream users.

2. **Single cleanup PR** removing all deprecated items listed above.

3. **Update `mkdocs.yml` nav** — regenerate the `api/plotting.md`
   page via `mkdocstrings` after the removals land.

4. **Re-enable `--strict` in CI** — after the PR lands, verify
   `mkdocs build --strict` passes with zero errors (the ~115
   griffe type-annotation warnings from deprecated functions will
   disappear).  Restore `--strict` to `.github/workflows/docs.yml`.

5. **Bump `python-requires` to `>=3.10`** (optional) — this is an
   opportune moment since the removal touches the public API surface.

---

## What the Post-Cleanup Release Looks Like

- All public plotting functions accept `AnalysisBuilder` **or** NPZ data dicts
- No `DeprecationWarning` noise at import or runtime
- CI passes `mkdocs build --strict` cleanly
- ~820 fewer lines to maintain

---

## Prerequisites: RC Nonlinear Static Analysis

The deprecation cleanup ships **after** RC nonlinear pushover works
comfortably.  Below is a realistic assessment of what remains.

### Current Status

| Capability | Detail | Status |
|---|---|---|
| `ConcreteRectangularSection` / `ConcreteCircularSection` | Defined in `model/sap_data.py` with `to_fiber_patches()` | ✅ Defined |
| `to_fiber_patches` for RC sections | Generates confined-core + unconfined-cover patches; uses `Concrete01` uniaxial material | ✅ Implemented |
| Rebar auto-placement | S2K parser promotes `RectangularSection` → `ConcreteRectangularSection` with bar count estimate (`A × 1% / bar_area`, min 4) | ✅ Implemented |
| `to_fiber_patches` dispatch in builder | `_create_single_section` detects `mat.type == 'concrete'` and creates 3 material tags (cover, core, rebar) | ✅ Implemented |
| `create_fiber_sections=True` automatic promotion | Check on each section; falls back silently to elastic if `to_fiber_patches` raises `NotImplementedError` | ✅ Implemented |
| RC pushover smoke-test | `test_rc_pushover.py` exists | ⚠️ Partial |
| Confined concrete (Mander) | `model/confinement.py` has `mander_confined()` **and** `AnalysisBuilder._create_single_section()` wires it into the confined core `Concrete01` via `sec.fiber_confinement()` (falls back to 1.25× heuristic when tie data absent) | ✅ Wired |
| Shear-governed behaviour | Not available in standard OpenSees beam-column elements | ❌ Not planned |
| Layered-shell RC walls | Working via `LayeredShell` + `ShellMITC4` (validated in `test_layered_shell.py`) | ✅ Validated |

### Gaps to Close (assessed 2026-08)

1. **Mander confinement wiring** — **✅ Closed.** `_create_single_section()`
   already calls `sec.fiber_confinement(fc, tie_fy)` (with `tie_fy` resolved
   from `RebarMatT` → `RebarMatL` → framework defaults), reads the
   `fcc/ecc/ecu` result, and applies the configurable `confined_ecu_max`
   cap.  The no-tie-data fallback uses the shared
   `RC_NO_TIE_CONFINEMENT_FACTOR` (1.25×) heuristic.  **Parity confirmed:
   the Tcl export in `opensees/builder.py` also routes through
   `sec.fiber_confinement()` (line ~483) with the same shared factor —
   no further work needed.**

2. **Rebar layer offset from cover** — **🟢 De minimis.**  The current
   `to_fiber_patches()` places rebar at `±(half_d − cover)` — the standard
   SAP2000 convention.  Making top/bottom cover separately configurable
   (`top_cover` / `bot_cover` fields) would be a small extension but no
   concrete use case demands it yet.  Defer until needed.

3. **2D vs 3D model dispatch** — **✅ Resolved as designed — 3D-only.**
   Gap closed 2026-08-16.  The toolkit's analysis workflows are **3D-only**
   by design: ``AnalysisBuilder.build_domain()`` deliberately emits
   ``ops.model('basic', '-ndm', 3, '-ndf', 6)`` and there is no
   ``ndm``/``ndf`` detection or 2D dispatch in the Preprocessor (an earlier
   draft of this plan claimed the opposite — that was incorrect).  "2D"
   models in the toolkit are **planar 3D models** (nodes in a plane,
   out-of-plane DOFs restrained; e.g. ``make_rc_frame_model()``).  **2D
   OpenSees analyses may be used in tests only** (standalone ``ndm=2``
   hand-check benchmarks) — never as part of the main workflow.  The
   policy is recorded in ``.clinerules`` §3.11 and ``docs/llm_guide.md``
   §6.  Validation: the 3D pushover path is exercised for both planar RC
   frames (``make_rc_frame_model``) and genuinely 3D RC frames
   (``make_rc_frame_3d``, added 2026-08-16).

4. **End-to-end validation benchmark** — **🟡 Implemented — flexure-only
   bias documented.**  The reference case is the **Vecchio & Emara (1992)**
   large-scale one-bay, two-storey RC frame (University of Toronto):

   - Flexure-critical with well-confined cross-sections; span-depth ratio
     ~8.75; columns under constant 700 kN axial; pushed to 155 mm at the
     second-storey beam then unloaded.
   - Reference: Vecchio, F.J. & Emara, M.B. (1992). "Shear Deformations in
     Reinforced Concrete Frames." *ACI Structural Journal* 89(1) 46–56
     (full geometry, rebar layouts, and material data are published).
   - Independent replication benchmark: Guner & Vecchio (2010),
     "Pushover Analysis of Shear-Critical Frames," *ACI Structural Journal*
     — reported peak-capacity ratio **calc/obs = 0.98** (324 vs ≈ 330 kN),
     energy dissipation 44.6 vs 44.4 kN·m, and P-Δ ≈ 12 % of overturning
     moment at ultimate.

   **Implemented (2026-08-16):**

   1. Programmatic `SAPModelData` builder `make_vecchio_emara_frame()` in
      `tests/test_rc_benchmark.py` (transcribed from Guner 2008 §2.3.5 /
      §4.7 and PEER 2006/04 §4.5.1 — one-bay × two-storey, 3500 mm span,
      2000 mm storeys, 300 × 400 mm members, 4 No. 20M top/bottom,
      No. 10M @ 125 mm ties, f'c = 30 MPa / fy = 418 MPa, 700 kN/column).
   2. **Bug found and fixed: joint loads were never applied.**  SAP2000
      "JOINT LOADS - FORCE" were parsed and carried through the
      Preprocessor but ``create_loads()`` never emitted them to the
      OpenSees domain — the benchmark's 700 kN column loads were silently
      dropped.  ``create_loads()`` now applies joint loads (and the
      gravity load/reaction sanity check now includes them + self-weight).
      Regression test: ``test_gravity_joint_loads_applied``.
   3. Pushover via the toolkit pipeline (`preprocess_model` →
      `AnalysisBuilder`, `PDelta`, fiber `dispBeamColumn`) converges
      monotonically to the full 155 mm protocol.
   4. **Acceptance criteria were NOT met at ±10 % — the flexure-only
      fiber path overestimates systematically.**  Measured: peak ≈ 495 kN
      vs ≈ 330 kN (calc/obs ≈ 1.5), secant stiffness @ 50 mm ≈ 8.6 kN/mm
      vs 6.1 kN/mm.  The elastic-to-first-yield response is in the right
      range (the hand-calc first-yield load of 312 kN is exceeded), the
      BEAM section alone reproduces the published Response-2000 capacity
      (M-φ peak ≈ 195 kN·m vs 206), and the model shows the expected
      post-peak P-Δ descent.  The overestimate is a **member-level**
      effect: the fiber model has no bond-slip, no shear deformation
      (~20 % share in the experiment) and no distributed-cracking
      effective-stiffness reduction, so it is stiffer in the cracked
      range; the higher column shears then inflate the frame-action axial
      in the beams (P ≈ −750 kN), raising their confined/hardening moment
      capacity (M ≈ 255 kN·m at P ≠ 0 vs ≈ 195 at P = 0).  Tests
      therefore **bracket** the experiment (peak ratio ∈ [1, 2], secant
      stiffness ∈ [0.5, 2] × experimental, first-yield load exceeded) and
      report the bias.  **Second pass (2026-08-16):** the element
      formulation was part of the bias — re-running the fibre rebuild on
      ``forceBeamColumn`` (flexibility-based) instead of
      ``dispBeamColumn`` (displacement-based) drops the peak to ≈ 291 kN
      (0.88 × experimental) and the secant @ 50 mm to ≈ 5.6 kN/mm
      (0.93 × experimental), inside the original ±10–15 % band with no
      calibration.  ``dispBeamColumn`` (Euler-Bernoulli) over-stiffens as
      the fibre sections soften *and* never engages section shear DOFs, so
      the new ``aggregate_shear`` / ``SectionAggregator`` option (elastic
      ``GA_v`` on Vy/Vz, config keys ``aggregate_shear``,
      ``shear_area_factor``, ``fiber_element_type``) is inert for it and
      the builder now warns.  The elastic shear term itself contributes
      only ≈ 0.2 % for these members (the experimental ~20 % shear share is
      a *cracked*-shear phenomenon).  The forceBeamColumn variant is
      covered by ``TestVecchioEmaraShearFlexibleVariant`` (peak ratio
      [0.75, 1.15], secant [0.8, 1.2] × experimental).
      **Third pass (2026-08-16): rigid joint end zones close the
      strength/stiffness gap.**  The 0.88 × result was partly a
      centreline-modelling artefact — the members span full node-to-node
      length with no rigid joint zones.  Adding ``rigid_end_zones``
      (offset = 0.5 × intersecting depth) + ``rigid_link_mpc`` (MPC
      links — the stiff elastic links ill-condition under PDelta) shortens
      the flexible members to the joint faces and lifts the peak to
      ≈ 353 kN (1.07 ×) and the secant @ 50 mm to ≈ 6.3 kN/mm (1.03 ×) —
      inside the original ±10–15 % acceptance band.  The post-peak shape
      limitation remains (the model keeps rising instead of descending
      after ≈ 50 mm), which needs nonlinear cracked-shear / bond-slip
      modelling — tracked in `docs/_pending_work.md`.

   5. Vecchio & Balopoulou (1990) variant (cut-back top reinforcement):
      **not re-run** — deferred with the shear-flexible follow-up.

5. **Pushover solver tuning** — **🟡 Priority: high — concrete recommended
   changes identified.**  `PUSHOVER_SOLVER_DEFAULTS` currently has a
   primary `NormDispIncr 1e-6 / 10 iter / Newton / 1 gravity substep`,
   the pushover run path uses `NormDispIncr 1e-4 / 20 / Newton`, and —
   when a step fails — the implemented per-step fallback switches to
   `NormUnbalance` + `ModifiedNewton -initial` with a **runtime-scaled
   relaxed tolerance** (derived from total mass × g × 1e-6 or 10 × the
   primary tolerance, whichever is larger) and 1000 iterations, then
   restores the primary settings for subsequent steps.  LayeredShell
   models automatically ramp gravity over 10 substeps.  The documented
   analysis guidance (see `docs/pushover_analysis.md`) reflects this
   implemented contract: `NormUnbalance`/`ModifiedNewton` fallback flow,
   automatic LayeredShell substeps, and the relaxed (2e-4-equivalent
   for kN-m full-building models) fallback tolerance, not a hard 1e-12.
   Two refinement findings drive further tuning:

   **(a) `NormUnbalance` is safer than `NormDispIncr` for force-based
   elements with `eleLoad` member loads.**  Michael Scott (OpenSeesDigital,
   2026) documents a pathological-convergence case: `forceBeamColumn`
   carries member loads internally (not as equivalent nodal loads), so
   `NormDispIncr` can report a zero displacement increment while the
   residual is still large — appearing converged when out of equilibrium.
   `NormUnbalance` correctly detects the residual.  The pushover pipeline
   applies self-weight via `eleLoad -type -beamUniform`, so this applies
   directly.

   **(b) The OpenSeesWiki RC-frame-pushover fallback pattern** is the
   battle-tested approach for RC softening: try `Newton` first; on failure
   relax the tolerance and switch to `ModifiedNewton -initial` for the step;
   then restore the primary solver.

   **Measures required (confirming/refining the implemented contract):**
   1. ~~Confirm the documented flow: the pushover path's primary test is
      `NormDispIncr 1e-4 / 20`, and on failure the per-step fallback
      switches to `NormUnbalance` + `ModifiedNewton -initial` with the
      relaxed runtime-scaled tolerance and 1000 iterations, then restores
      the primary settings~~ ✅ **Resolved (2026-08-24, P3) — the empirical
      pass REJECTED the looser contract.**  The documented `1e-4 / 20` was
      never actually effective (`PUSHOVER_SOLVER_DEFAULTS` 1e-6/10
      pre-fills the config dict, so the `.get(key, 1e-4)` fallback never
      fired) and is **NOT universally safe**: 1e-4/20 breaks the Duong
      flexure-only forceBeamColumn pushover (element state-determination
      divergence), while 1e-6/10 converges every validated benchmark (V&E,
      Duong, RC/steel/LayeredShell).  The validated pushover default is
      therefore the general `NormDispIncr 1e-6 / 10 / Newton`; looser
      tolerances (e.g. 2e-4/1000) remain an explicit per-model opt-in.
   2. `PUSHOVER_FALLBACK_DEFAULTS` carries
      `{"solver_test_type": "NormUnbalance", "solver_test_max_iter": 1000,
      "solver_algorithm": "ModifiedNewton"}` — the tolerance is computed
      at runtime from the model's characteristic weight (via
      `g_from_units`), so the stale **1e-12** value must not re-appear;
      the relaxed tolerance for a kN-m full-building model is ~2e-4.  ✅
      **Verified (2026-08-24):** audit of the pushover path found only
      benign geometry/magnitude guards at `1e-12` (uniform-load detection,
      element-length guards), never a fallback test tolerance.
   3. ~~Confirm per-step fallback logic in `run_pushover_analysis()`:
      on a failed step with primary settings, retry with the fallback dict,
      then restore primary settings for subsequent steps~~ ✅ **Done** — the
      fallback + restore chain (including honouring a configured
      `solver_test_type` on restore) was verified in the review-round-2
      M3 fix (commit `4b53a0f`).
   4. ~~Keep the automatic `gravity_num_substeps` = 10 ramping for models
      with LayeredShell sections (explicit config value always wins)~~ ✅
      **Verified** — `LAYERED_SHELL_GRAVITY_SUBSTEPS` auto-detection with
      explicit-value precedence.
   5. ~~Keep the `RC_PUSHOVER_SOLVER_DEFAULTS` convenience preset combining
      all of the above; `docs/pushover_analysis.md` documents the
      recommended RC settings~~ ✅ **Reconciled (2026-08-24, P3).**  The RC
      preset exists as `_PUSHOVER_RC_DEFAULTS` in `analysis/base.py`
      (NormDispIncr 1e-4 / 20 / Newton, gravity ramping, fallback dict) for
      the typed-runner path.  **Empirical caveat (2026-08-24):** 1e-4/20 is
      not universally safe — it breaks the Duong flexure-only
      forceBeamColumn pushover; the direct `AnalysisBuilder` path defaults
      to the general 1e-6/10.  `docs/pushover_analysis.md` documents the
      recommended RC/LayeredShell settings (`solver_test_tol: 2e-4`,
      `solver_test_max_iter: 1000`).
   6. ~~The Gap 4 Vecchio & Emara benchmark is complete (peak 1.07×, secant
      1.03× — `docs/vecchio_emara_benchmark.md`), so tune these defaults
      empirically against it and record the final values in the plan
      (tracked as P3 in `docs/_pending_work.md`)~~ ✅ **Done (2026-08-24,
      P3).**  Empirical comparison: on the V&E benchmark `1e-4/20` ≡
      `1e-6/10` (peak 353 kN = 1.07×, secant 6.29 kN/mm = 1.03×, all steps
      converged); on the Duong benchmark `1e-4/20` **fails** the
      flexure-only forceBeamColumn pushover while `1e-6/10` converges.
      Final recorded pushover primary values: `NormDispIncr 1e-6 / 10 /
      Newton` (the general default — looser tolerances opt-in per model).

6. **CSM bilinearisation validation** — **🟢 Implemented + synthetic
   validation done; real-benchmark validation pending.**  The existing
   `bilinearize_composite()` uses equal-area / equal-energy criteria
   tuned for steel yield plateaus.  RC capacity curves are highly curved
   (gradual cracking → rebar yield → softening), so the fitted yield
   point may snap to the **cracking transition** rather than the
   **rebar-yield drift**.  Research:

   - De Luca, Vamvatsikos & Iervolino (2013, *Earthquake Engineering &
     Structural Dynamics*), "Near-optimal piecewise linear fits of static
     pushover capacity curves": code-mandated bilinear fits (FEMA 60 %
     secant, EC8 equal-area) are **highly biased for curved RC backbones**;
     the FEMA 60 %-secant rule can overestimate displacement demand by
     ~25 %.  They propose a near-optimal **"10 % rule"**: elastic secant at
     10 % of peak strength (instead of 60 %), with post-elastic slope
     chosen to minimise the absolute area discrepancy.

   **Measures required:**
   1. ~~Run `bilinearize_composite()` against synthetic RC-shaped curves
      (gradual softening, no sharp yield plateau) and compare the fitted
      yield point against the De Luca 10 %-secant near-optimal fit~~ ✅ **Done (2026-08-16).**
      `tests/test_model.py::TestBilinearization::test_de_luca_rc_curve_yield_not_at_cracking`
      validates the new method against a tanh saturation + post-peak
      softening backbone (yield lands in the rebar-yield band, equal-area
      exact) and `test_de_luca_recovers_exact_bilinear_knee` verifies it
      recovers the true knee of a bilinear curve.
   2. ~~Implement a `bilinearize_rc()` variant (or a configurable
      `csm_bilinear_method` key) implementing the 10 %-secant rule~~ ✅ **Done (2026-08-16).**
      `bilinearize_rc()` added to `model/csm.py`, registered in
      `compute_performance_point(..., bilinearize_method=...)` under the
      names `"rc"` and `"de_luca_10pct"`; config key `elastic_fraction`
      (default 0.10).  Closed-form equal-area solution (no iteration);
      exported via `fea_toolkit.model.__all__`.
   3. ~~Validate against the real RC pushover curve from the Gap 4 benchmark —
      the yield point should sit near the expected rebar-yield drift
      (~0.5–1 % roof drift), not at the premature cracking transition.
      **(Pending — blocked on Gap 4.)**~~ ✅ **Done (2026-08-24, P4).**  Applied
      `bilinearize_rc()` to the real V&E capacity curve
      (`tests/test_rc_benchmark.py::test_bilinearize_rc_real_curve`).
      **Result: S_dy ≈ 14 mm (spectral displacement, model length units),
      equal-area exact.**  The key claim holds — the yield does **not** snap
      to the cracking transition (~2 mm spectral displacement) — but the
      0.5–1 % rebar-yield threshold is a **roof-drift** band and is not
      compared directly against the spectral `S_dy`; the yield sits below the
      model's own first-yield roof drift (≈ 31 mm) in the corresponding
      roof-displacement coordinates
      because the current model curve keeps hardening to the 155 mm end
      (no post-peak peak, see P5): the equal-area constraint on a
      hardening-only backbone pushes the yield earlier, in the
      conservative direction.  Re-validate the rebar-yield band once the
      P5 post-peak descent gives the curve a real peak.
   4. ~~Update `docs/csm_bilinearization.md` documenting the method choice
      and the calibration results~~ ✅ **Done (2026-08-16)** — §4 documents the
      method, config keys, references, and the comparison table row.

### Recommended Validation Sequence

1. **2D RC cantilever** — single column, 1-element, `forceBeamColumn`
   + `Lobatto` with `Concrete01`/`Steel01` fibers.  Push to 5 %
   drift and compare peak base shear against hand-calculated
   plastic moment.

2. **2-storey 2-bay RC frame** — `ConcreteRectangularSection` beams
   and columns auto-promoted from S2K parser.  Gravity + pushover.
   Compare yield drift and base shear against the Vecchio & Emara
   (1992) benchmark described in Gap 4 above.

3. **RC frame with shell slabs** — verify interaction between
   `forceBeamColumn` frame elements and `ShellMITC4` slab elements
   under gravity + lateral push.

4. **Mander-confined pushover** — re-run benchmark 2 against the
   same model with confined concrete; verify that ductility
   capacity increases as expected.

---

## Risk Assessment

| Risk | Mitigation |
|---|---|
| Downstream scripts use deprecated names | Audit `local/` and `examples/` first |
| External users haven't migrated | One-release grace period already served |
| Test coverage gap for deprecation path | Tests exercise replacement functions; remove deprecated-path tests |
| `--strict` still fails after cleanup | Run `mkdocs build --strict` locally before re-enabling in CI |