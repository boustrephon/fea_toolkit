---
title: "AnalysisBuilder Migration Plan"
description: "Migration plan for the two-stage pipeline, superseding monolithic OpenSeesBuilder."
status: "draft"
tags: [architecture, migration, planning]
category: [planning]
related: [layered_analysis_workflow.md, workflow.md, dev_notes.md]
---
# AnalysisBuilder Migration Plan

**Status:** ✅ **`OpenSeesBuilder` removal complete; ⚠️ remaining migration items pending**  
**Last updated:** 2026-07-23  
**Total test count:** 533

## Overview

The `OpenSeesBuilder` class has been deleted from ``builder.py``.
All features that were part of the original builder have been ported to
``AnalysisBuilder`` or to standalone module-level functions.

**Note:** While the legacy ``OpenSeesBuilder`` deletion is complete,
several migration items described later in this document (``AnalysisCaseSpec``
for nonlinear OpenSeesPy cases, ``NonlinearDynamicAnalysis`` workflow
alignment, and full ``AnalysisBuilder`` coverage for all nonlinear analysis
types) remain pending. The table below summarises the ported features.

Summary of ported features:

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
