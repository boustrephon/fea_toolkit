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
non-underscore names.

### 4. Opensees — `src/fea_toolkit/opensees/builder.py`

Verify whether the legacy `OpenSeesBuilder` class still exists.  If only
the Tcl-export functions remain (as the module docstring claims), the
class can be removed.  The Tcl functions are standalone and do not
depend on it.

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
| Confined concrete (Mander) | `model/confinement.py` has `mander_confined()` but the builder does not yet wire it into fiber patches | ⚠️ Partial |
| Shear-governed behaviour | Not available in standard OpenSees beam-column elements | ❌ Not planned |
| Layered-shell RC walls | Working via `LayeredShell` + `ShellMITC4` (validated in `test_layered_shell.py`) | ✅ Validated |

### Gaps to Close

1. **Mander confinement wiring** — `ConcreteRectangularSection.to_fiber_patches()`
   already knows about confined vs. unconfined concrete, but currently
   uses `Concrete01` for both.  Integrate the existing `mander_confined()`
   function so the core patch uses a confined stress–strain curve.

2. **Rebar layer offset from cover** — the current `to_fiber_patches`
   places rebar at a hard-coded offset from the section edge.  Make this
   configurable via section properties or a config dict.

3. **2D vs 3D model dispatch** — RC pushover should work for both
   `-ndm 2 -ndf 3` (frame models) and `-ndm 3 -ndf 6` (full models
   with shells).  The `forceBeamColumn` path works for both; verify
   the `Lobatto` integration and `PDelta` transform converge
   reliably with RC fiber sections.

4. **End-to-end validation benchmark** — a simple 2-storey RC frame
   with known pushover curve (compare against literature or SAP2000
   staged-construction pushover).  This gives confidence before
   running on production models.

5. **Pushover solver tuning** — RC fibers produce softer response than
   steel, especially after cracking.  The default solver settings
   (`Newton`, `NormDispIncr 1e-6`, 10 iters) may need relaxation for
   RC models (e.g. `KrylovNewton`, `NormUnbalance`, higher sub-step
   count).  Expose via the builder config dict rather than hard-coding.

6. **CSM bilinearisation validation** — verify that the
   `bilinearize_composite` default produces sensible yield points for
   RC capacity curves (which are more curved than steel SDOF backbones).
   The existing test suite covers synthetic curves but not real RC.

### Recommended Validation Sequence

1. **2D RC cantilever** — single column, 1-element, `forceBeamColumn`
   + `Lobatto` with `Concrete01`/`Steel01` fibers.  Push to 5 %
   drift and compare peak base shear against hand-calculated
   plastic moment.

2. **2-storey 2-bay RC frame** — `ConcreteRectangularSection` beams
   and columns auto-promoted from S2K parser.  Gravity + pushover.
   Compare yield drift and base shear against literature benchmark
   (e.g. PEER RC frame, Vecchio & Emara 1992).

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