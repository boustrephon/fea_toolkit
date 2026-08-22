---
title: "AnalysisBuilder Migration Plan"
description: "Migration plan for the two-stage pipeline, superseding monolithic OpenSeesBuilder."
status: "complete"
tags: [architecture, migration, planning]
category: [planning]
related: [layered_analysis_workflow.md, workflow.md, dev_notes.md]
---

# AnalysisBuilder Migration Plan

**Status:** ✅ **Complete — two-stage pipeline fully implemented**
**Last updated:** 2026-08-01
**Total test count:** 533

> **2026-08-21 follow-up:** the `analysis/` layer was subsequently simplified.
> The `Analysis` ABC and `AnalysisManager` were removed and replaced by
> module-level functions (`run_modal_analysis`, `run_static_analysis`,
> `run_response_spectrum_analysis`, `run_pushover_analysis`,
> `run_nonlinear_dynamic_analysis`) returning `AnalysisResult`.  Dependencies
> (modal → RS / pushover / NLD) are now explicit function arguments.  The
> table below is retained as the historical migration record.

## Overview

The `OpenSeesBuilder` class has been deleted from ``builder.py``.
All features that were part of the original builder have been ported to
``AnalysisBuilder`` or to standalone module-level functions.

This document serves as a reconciliation record: it lists what the plan
called for, what was implemented, and what remains as a known limitation.

---

## Summary of ported features

| Area | Methods / features | Location |
|------|-------------------|----------|
| **Core analysis** | `run_modal_analysis`, `run_response_spectrum_analysis`, `run_static_analysis`, `compute_seismic_masses`, `extract_mode_shapes`, `export_results`, `extract_static_element_forces` | `AnalysisBuilder` |
| **CSM** | `pushover_to_adrs`, `compute_performance_point` → `model/csm.py` | `AnalysisBuilder` + `csm.py` |
| **Pushover** | `run_pushover_analysis` — gravity + lateral + displacement-controlled push with algorithm fallback chain | `AnalysisBuilder` |
| **Rebuild** | `build_domain(config_overrides=...)`, `rebuild_with_fiber_sections`, `_reapply_edge_constraints` | `AnalysisBuilder` |
| **Edge constraints** | `apply_edge_constraints`, `apply_spring_edge_constraints`, `_apply_penalty_edge_constraints`, `detect_unconnected_edges` | `AnalysisBuilder` |
| **Brace subdivision** | `set_brace_selection`, `check_brace_buckling` (Approach A) | `AnalysisBuilder` + `model/checks.py` + `model/geometry.py` |
| **Lumped hinges** | `_create_lumped_hinges` — zero-lengthSection with Hysteretic backbone | `AnalysisBuilder` |
| **Hinge length** | `compute_hinge_length`, `compute_asce41_hinge_length` | `model/checks.py` |
| **Model checks** | `check_model_connectivity`, `check_self_weight_consistency` | `model/checks.py` |
| **Tcl export** | `export_model_to_tcl`, `tcl_materials_and_sections`, `pushover_tcl` | `builder.py` (standalone functions) |
| **Local axes** | `get_local_axes`, `_get_local_axes` | `model/geometry.py` + `AnalysisBuilder` |

---

## Plan vs. implementation reconciliation

### Architecture (from `layered_analysis_workflow.md` §11.5 / Task 2)

| Planned | Status | Location |
|---------|--------|----------|
| Keep `MeshModel` as the canonical shared state | ✅ **Done** | `model/mesh_model.py` — Preprocessor output, frozen topology |
| Add `AnalysisCaseSpec` to the analysis layer | ✅ **Done** | `analysis/base.py` — `name`, `analysis_type`, `config`, `kwargs` |
| Add `AnalysisManager` with dependency resolution | ✅ **Done** | `analysis/manager.py` — Kahn's algorithm topological sort, `_inject_dependencies()` for modal→RS / modal→pushover |
| Add `Analysis` ABC with `defaults()`, `run()`, `requires`, `provides` | ✅ **Done** | `analysis/base.py` |
| Add `analysis_variant_map` / `analysis_property_overrides` / `analysis_load_overrides` metadata to `MeshModel` | ⚠️ **Superseded** | Replaced by the simpler `AnalysisCaseSpec.config` dict — case-specific overrides live on the case spec, not on the shared model |

### Typed analysis classes

| Planned | Status | Location |
|---------|--------|----------|
| `StaticAnalysis` | ✅ **Done** | `analysis/static.py` |
| `ModalAnalysis` | ✅ **Done** | `analysis/modal.py` |
| `ResponseSpectrumAnalysis` | ✅ **Done** | `analysis/rs.py` |
| `PushoverAnalysis` | ✅ **Done** | `analysis/pushover.py` |
| `NonlinearDynamicAnalysis` | ✅ **Done** | `analysis/nonlinear_dynamic.py` |

All five classes are re-exported from `analysis/__init__.py` and registered on
the old `AnalysisDefaults` deprecation path.

### Per-type config defaults

| Planned | Status | Location |
|---------|--------|----------|
| `_STATIC_LINEAR_DEFAULTS` | ✅ **Done** | `analysis/base.py` |
| `_MODAL_DEFAULTS` | ✅ **Done** | `analysis/base.py` |
| `_RESPONSE_SPECTRUM_DEFAULTS` | ✅ **Done** | `analysis/base.py` |
| `_PUSHOVER_STEEL_DEFAULTS` | ✅ **Done** | `analysis/base.py` — `nonlinearBeamColumn`, `Steel01` |
| `_PUSHOVER_RC_DEFAULTS` | ✅ **Done** | `analysis/base.py` — `forceBeamColumn`, `Concrete01`/`Steel02` fibers |
| `_NONLINEAR_DYNAMIC_DEFAULTS` | ✅ **Done** | `analysis/base.py` — `forceBeamColumn`, Rayleigh damping, Newmark |

### RC nonlinear workflow (was §14.1 — "requires a pinned OpenSeesPy build")

> **Update (2026-08-01):** RC fiber sections are now available in the direct
> OpenSeesPy path. The claim in the original plan that the stock ``pip``
> OpenSeesPy distribution does not include RC material formulations has been
> disproven — ``Concrete01`` and ``Steel02`` work with ``forceBeamColumn`` +
> ``Lobatto`` integration directly.

| Planned | Status | Location |
|---------|--------|----------|
| RC fiber sections (`Concrete01`, `Steel02` via `forceBeamColumn`) | ✅ **Done** | `ConcreteRectangularSection.to_fiber_patches()` / `ConcreteCircularSection.to_fiber_patches()` → `AnalysisBuilder._create_single_section()` (3 material tags: cover `Concrete01`, core `Concrete01`, rebar `Steel02`) |
| Automatic `create_fiber_sections=True` promotion | ✅ **Done** | `rebuild_with_fiber_sections()` — if `to_fiber_patches()` raises `NotImplementedError`, the builder emits a `UserWarning` and falls back to an elastic section (no silent fallback) |
| Rebar material resolution (config override → SAP2000 lookup → framework defaults) | ✅ **Done** | `AnalysisBuilder` + `export_mesh_model_to_tcl` (covered by `tests/test_rebar_material.py`) |
| Mander confinement wiring | ✅ **Done** | `AnalysisBuilder._create_single_section()` calls `fiber_confinement(Fc, tie_fy)` when the section exposes it, and uses the returned `fcc/ecc/ecu` for the core `Concrete01` patch when confinement data is present; a 1.25× strength / 2.0× strain heuristic is only used as a fallback when no tie data is available. `model/confinement.py` provides `mander_confined()` |
| End-to-end RC validation benchmark | ✅ **Done** | `examples/sample_model.py` `make_rc_frame_model()` (single-storey RC frame, kN-m units, 3 materials) + `tests/test_workflows.py` `test_compute_performance_point` (strengthened assertions: `mu > 1`, converged, plausible `M_eff`) — see `docs/csm_test_model_plan.md` |
| Pushover solver tuning for RC | ✅ **Done** | `_PUSHOVER_RC_DEFAULTS` uses the validated RC contract: `NormDispIncr 1e-4` / 20 iter / `Newton` primary + per-step `NormUnbalance`/`ModifiedNewton`(1000 iter) fallback, 10 gravity substeps (updated 2026-08-16 per `docs/_pending_work.md` — the strict 1e-6/10/NewtonLineSearch settings stalled RC fibre pushovers) |

### Tcl / Xara nonlinear workflow (was §14.2)

| Planned | Status | Location |
|---------|--------|----------|
| `export_model_to_tcl()` — solver-ready Tcl translation incl. fiber sections + nonlinear materials | ✅ **Done** | `opensees/recorder.py` + `builder.py` |
| `pushover_tcl()` / `dynamic_time_history_tcl()` — analysis suffix | ✅ **Done** | `opensees/recorder.py` |
| `XaraTclRunner` — Tcl runtime execution | ✅ **Done** | `opensees/recorder.py` |
| `recorder.parse_pushover_results()` — output records → Python dicts | ✅ **Done** | `opensees/recorder.py` |

### What remains

| Item | Status | Notes |
|------|--------|-------|
| Brace buckling with nonlinear beam-column elements in the direct OpenSeesPy path | ❌ **Not implemented** | Tcl path supports it (corotational truss + `Hysteretic`); the direct-path equivalent is an open enhancement |

Everything else in the original migration plan has been implemented or
superseded by a simpler design.

---

## Nonlinear analysis roadmap (updated)

The project now supports both nonlinear workflows:

### 14.1 OpenSeesPy nonlinear workflow (implemented)

Nonlinear analyses in OpenSeesPy directly are supported for:

- **Steel pushover** — `nonlinearBeamColumn`, `Steel01`, `Lobatto` integration
- **RC pushover** — `forceBeamColumn`, `Concrete01` (cover + confined core) +
  `Steel02` (rebar) fiber sections, auto-promoted from
  `ConcreteRectangularSection` / `ConcreteCircularSection`
- **Nonlinear dynamic** — `NonlinearDynamicAnalysis` with `forceBeamColumn`,
  Rayleigh damping, Newmark integration

### 14.2 Tcl / Xara nonlinear workflow (implemented)

RC and nonlinear dynamic workflows can also be executed via the Tcl export +
Xara runtime path:

1. ``export_model_to_tcl()`` — translates a preprocessed ``MeshModel``
   into a solver-ready Tcl script, including fiber sections and nonlinear
   material definitions.
2. ``pushover_tcl()`` / ``dynamic_time_history_tcl()`` — generate the
   analysis suffix (recorders, solver, post-processing commands).
3. ``XaraTclRunner`` — runs the Tcl script in the OpenSees/Xara runtime.
4. ``recorder.parse_pushover_results()`` — collects output records and
   maps them back into Python-native result dicts.

Entry points: ``AnalysisBuilder.run_pushover_analysis()`` and the
``NonlinearDynamicAnalysis`` class.

---

## Task list — migration status

| Task | Status |
|------|--------|
| **Task 1** — Define the runtime boundary (Preprocessor + AnalysisBuilder only active stages) | ✅ **Done** |
| **Task 2** — Add the analysis-case contract (`AnalysisCaseSpec` + `AnalysisManager`) | ✅ **Done** — `analysis/base.py` + `analysis/manager.py` |
| **Task 3** — Make nonlinear OpenSeesPy cases explicit (RC pushover, nonlinear dynamic) | ✅ **Done** — `PushoverAnalysis` + `NonlinearDynamicAnalysis` with per-type defaults |
| **Task 4** — Tcl/Xara workflow | ✅ **Done** — Tcl export + `XaraTclRunner` + result parsing |
| **Task 5** — Align the local v3 scripts to the shared `MeshModel` / per-case `AnalysisBuilder` architecture | ✅ **Done** — model-specific drivers are thin wrappers over `generate_report()` |
| **Task 6** — Verify the helper-consumer boundary (plotting/report/Rhino consume results) | ✅ **Done** |

---

## Answer to the incidental question: does `MeshModel` still contain the original SAP elements?

Yes — in the current implementation, the original super-elements are preserved in the element dictionaries and are marked inactive once the mesh/subdivision process creates children.

This is already reflected in the dataclasses:

- `FrameElement` in `src/fea_toolkit/model/sap_data.py`
- `AreaElement` in `src/fea_toolkit/model/sap_data.py`

and in the subdivision logic:

- the original frame element is marked `inactive = True` and the child elements are created with `inactive = False` in `src/fea_toolkit/model/geometry.py`
- the same pattern is used for subdivision of the original area super-elements in the mesh routines

So the intended meaning is:

- the parent / original SAP element remains present for traceability and hierarchy
- the active FE-ready child elements are the ones used for analysis
- the parent is kept as an inactive historical / superelement record, not as an active solver object

That is exactly the right structure for the "SAP geometry as superelements, then subdivided into FEA-ready children" formulation.