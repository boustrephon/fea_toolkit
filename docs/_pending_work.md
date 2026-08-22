---
title: "Pending Work Log"
description: "Internal log of completed and pending work items across documentation and visualization fixes."
status: "draft"
tags: [planning, work-log, internal]
category: [planning]
---
# Pending work — fea_toolkit (2026-08-01 continued)

## PENDING (active — not yet done)

### Phase B — force-diagram unification
Detailed design → `docs/force_diagram_unification.md`.
- Unify `plot_rs_force_diagram()`, `plot_force_diagram_3d()`,
  `plot_npz_force_diagram()`, `plot_npz_moment_3d()` into one unit-aware
  `plot_force_diagram()` covering all inputs (AnalysisBuilder, in-memory
  result dicts incl. RS `element_results`, NPZ paths), dispatching 2D vs 3D
  and static vs CQC-RS from the input shape.
- Legacy names become thin wrappers, removed after one release cycle.
- Do this *before* splitting `plotting/viz.py` so the split works on the
  smaller post-unification module.

### Large-file splits (suggested order: viz → geometry → analysis_builder)
- `plotting/viz.py` (~5.7k lines) — PyVista viewers.  Proposed split (after
  Phase B lands):
  - `viz.py` — model / deflected-shape viewers, section viewers.
  - `viz_forces.py` — force diagrams (the Phase B family).
  - `viz_modal.py` — mode shapes, animations (`plot_mode_animation`).
  - `viz_mesh.py` — mesh-quality views if distinct from model viewers.
- `model/geometry.py` (~3.9k) — split into `geometry.py` (core vector/line
  math), `geometry_sections.py` (section geometric properties: area,
  inertia, torsion constants), `geometry_mesh.py` (meshing/refinement
  helpers).
- `opensees/analysis_builder.py` (~7.4k, last) — keep the class as the
  public facade; extract private helper modules:
  - `_sections.py` — frame/shell/fiber section creation.
  - `_materials.py` — uniaxial + nD material creation (incl. the
    `PlaneStressUserMaterial` pair).
  - `_elements.py` — frame/wall/shell element creation.
  - `_runners.py` — per-analysis-type runners (modal, RS, pushover, ND).
- Each split keeps `__all__` + re-exports stable; no public-name churn.

### Tcl-exporter merge — deferred
- `export_model_to_tcl()` (`opensees/builder.py`, SAPModelData-based) vs
  `export_mesh_model_to_tcl()` (`opensees/recorder.py`, MeshModel-aware).
  **Verification (2026-08-21):** independent implementations, no trivial
  delegation.  If merged: one `isinstance` dispatcher + deduplicated shared
  preamble/recorder emission.  Cross-refs added; larger dedicated refactor,
  deferred.

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
