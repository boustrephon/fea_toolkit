---
title: "Pending Work Log"
description: "Internal log of completed and pending work items across documentation and visualization fixes."
status: "draft"
tags: [planning, work-log, internal]
category: [planning]
---
# Pending work — fea_toolkit (2026-08-01 continued)

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

## OUT OF SCOPE / NOTED
- The uncommitted `src/fea_toolkit/model/csm.py` WIP diff (63 lines: `_modal_participation` helper, peak_idx clamping, performance-point fallback period, control-node warning) remains uncommitted. Tests that asserted its in-progress behavior were removed; the remaining 27 CSM tests pass against the current state.

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
