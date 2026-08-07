---
title: "Deprecation Removal Plan"
description: "Plan for removing deprecated APIs after the RC nonlinear static analysis release."
status: "draft"
tags: [planning, deprecation, cleanup]
category: [planning]
---

# Deprecation Removal Plan

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
| `plot_force_diagram` | (no direct replacement — 2D CQC diagram) | ~50 |
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

3. **2D vs 3D model dispatch** — **🟡 Code complete, needs validation.**
   The Preprocessor detects model dimensionality from SAP geometry and
   records `MeshModel.ndm` / `.ndf` (2 or 3); AnalysisBuilder reads
   those values and applies them to the OpenSees domain (`model Basic
   -ndm {ndm} -ndf {ndf}`) when constructing the model.
   `forceBeamColumn` + `Lobatto` + `PDelta` is the default pushover path.
   The remaining work is running real RC models through both dims and
   verifying convergence — a validation task, not a code gap.

4. **End-to-end validation benchmark** — **🔴 Not started — concrete plan
   identified.**  The recommended reference case is the **Vecchio & Emara
   (1992)** large-scale 2-storey, 2-bay RC plane frame (University of
   Toronto).  Its characteristics make it ideal:

   - Flexure-critical with well-confined cross-sections
   - Beam span-depth ratio 8.75; columns under axial load
   - Pushed to 155 mm lateral displacement, then unloaded to zero net load
   - Reference: Vecchio, F.J. & Emara, M.B. (1992). "Shear Deformations in
     Reinforced Concrete Frames." *ACI Structural Journal* (full geometry,
     rebar layouts, and material data are published)
   - Independent replication benchmark: Guner & Vecchio (2010),
     "Pushover Analysis of Shear-Critical Frames," *ACI Structural Journal*
     — reported peak-capacity ratio **calc/obs = 0.98**, energy dissipation
     44.6 vs 44.4 kN·m, and P-Δ ≈ 12% of overturning moment at ultimate.

   **Measures required:**
   1. Extract published geometry, rebar layouts, and material strengths for
      the Vecchio & Emara frame as a new `tests/fixtures/` `.s2k` file or a
      programmatic `SAPModelData` builder in `tests/test_rc_benchmark.py`.
   2. Run `preprocess_model()` → `AnalysisBuilder` with
      `create_fiber_sections=True` and `forceBeamColumn` + `Lobatto`.
   3. Compare the computed base-shear vs roof-displacement envelope against
      the published experimental curve (digitise Guner & Vecchio's Fig. 11).
   4. **Acceptance criteria:** peak base shear within ±10 % of experiment,
      initial stiffness within ±15 %, and the softening/ductility trend
      qualitatively matches.  Because ``forceBeamColumn`` fiber sections
      model **flexure only** (no shear deformation), the comparison must
      target a **flexure-only experimental basis**.  If the published
      peak-shear / stiffness / deformation targets include the frame's
      ~20 % shear contribution, model the expected shear deformation with
      explicit shear-flexible components (e.g. a shear spring or section
      aggregation) so the modeled response matches the validation target.
      Otherwise, restrict the acceptance criteria to flexure-only
      quantities and note that the ~20 % shear share is not captured by
      the fiber model.
   5. Optionally re-run the Vecchio & Balopoulou (1990) variant (cut-back
      top reinforcement) to verify sensitivity to reinforcement details.

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
   1. Confirm the documented flow: the pushover path's primary test is
      `NormDispIncr 1e-4 / 20`, and on failure the per-step fallback
      switches to `NormUnbalance` + `ModifiedNewton -initial` with the
      relaxed runtime-scaled tolerance and 1000 iterations, then restores
      the primary settings.
   2. `PUSHOVER_FALLBACK_DEFAULTS` carries
      `{"solver_test_type": "NormUnbalance", "solver_test_max_iter": 1000,
      "solver_algorithm": "ModifiedNewton"}` — the tolerance is computed
      at runtime from the model's characteristic weight (via
      `g_from_units`), so the stale **1e-12** value must not re-appear;
      the relaxed tolerance for a kN-m full-building model is ~2e-4.
   3. Confirm per-step fallback logic in `run_pushover_analysis()`:
      on a failed step with primary settings, retry with the fallback dict,
      then restore primary settings for subsequent steps.
   4. Keep the automatic `gravity_num_substeps` = 10 ramping for models
      with LayeredShell sections (explicit config value always wins).
   5. Keep the `RC_PUSHOVER_SOLVER_DEFAULTS` convenience preset combining
      all of the above; `docs/pushover_analysis.md` documents the
      recommended RC settings.
   6. After the Vecchio & Emara benchmark (Gap 4) is running, tune these
      defaults empirically and record the final values in the plan.

6. **CSM bilinearisation validation** — **🟡 Validation gap — concrete
   plan identified.**  The existing `bilinearize_composite()` uses
   equal-area / equal-energy criteria tuned for steel yield plateaus.
   RC capacity curves are highly curved (gradual cracking → rebar yield →
   softening), so the fitted yield point may snap to the **cracking
   transition** rather than the **rebar-yield drift**.  Research:

   - De Luca, Vamvatsikos & Iervolino (2013, *Earthquake Engineering &
     Structural Dynamics*), "Near-optimal piecewise linear fits of static
     pushover capacity curves": code-mandated bilinear fits (FEMA 60 %
     secant, EC8 equal-area) are **highly biased for curved RC backbones**;
     the FEMA 60 %-secant rule can overestimate displacement demand by
     ~25 %.  They propose a near-optimal **"10 % rule"**: elastic secant at
     10 % of peak strength (instead of 60 %), with post-elastic slope
     chosen to minimise the absolute area discrepancy.

   **Measures required:**
   1. Run `bilinearize_composite()` against synthetic RC-shaped curves
      (gradual softening, no sharp yield plateau) and compare the fitted
      yield point against the De Luca 10 %-secant near-optimal fit.
   2. Implement a `bilinearize_rc()` variant (or a configurable
      `csm_bilinear_method` key: `"equal_area"` / `"equal_energy"` current
      default / `"de_luca_10pct"` new) implementing the 10 %-secant rule.
   3. Validate against the real RC pushover curve from the Gap 4 benchmark —
      the yield point should sit near the expected rebar-yield drift
      (~0.5–1 % roof drift), not at the premature cracking transition.
   4. Update `docs/csm_bilinearization.md` documenting the method choice
      and the calibration results.

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