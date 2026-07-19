# AnalysisBuilder Migration Plan

**Status:** Phase 1 (CSM) in progress  
**Last updated:** 2026-07-20  
**Total test count:** 485

## Overview

Port remaining legacy-only features from `OpenSeesBuilder` to `AnalysisBuilder`.
The two-stage path (`use_preprocessor=True`) is the default.

---

## ✅ Completed

| Feature | AnalysisBuilder | Builder facade |
|---------|---------------|---------------|
| `run_modal_analysis` | L1387 | delegates |
| `run_response_spectrum_analysis` | L1584 | delegates |
| `run_static_analysis` | L992 | delegates + summed_reactions |
| `compute_seismic_masses` | L1095 | delegates |
| `extract_mode_shapes` | L1717 | delegates |
| `export_results` | L1780 | delegates |
| `extract_static_element_forces` | L1745 | delegates |

## ❌ Not Yet Ported

| Feature | Est. lines | Target | Phase |
|---------|-----------|--------|-------|
| `pushover_to_adrs` / `compute_performance_point` | ~380 | Standalone `model/csm.py` | **Phase 1** |
| `_compute_uniform_lateral_loads` | ~30 | AnalysisBuilder | **Phase 3a** |
| `_compute_triangular_lateral_loads` | ~50 | AnalysisBuilder | **Phase 3a** |
| `_compute_mode_shape_lateral_loads` | ~40 | AnalysisBuilder | **Phase 3a** |
| `_compute_fallback_masses` | ~40 | AnalysisBuilder | **Phase 3a** |
| `build_domain(config_overrides=...)` | ~10 | AnalysisBuilder | **Phase 3b** |
| `rebuild_with_fiber_sections()` | ~190 | AnalysisBuilder | **Phase 3b** |
| `run_pushover_analysis()` | ~500 | AnalysisBuilder | **Phase 3c** |
| `apply_edge_constraints()` | ~250 | AnalysisBuilder | **Phase 2** |
| `compute_rs_nodal_displacements` | ~100 | AnalysisBuilder | P2 |
| `extract_element_rs_forces` | ~100 | AnalysisBuilder | P2 |

---

## Phase 1: CSM Methods (P2)

**Files affected:** `src/fea_toolkit/model/csm.py` (new), `analysis_builder.py`, `builder.py`

Both `pushover_to_adrs()` and `compute_performance_point()` are **pure data-flow
functions** — no access to `self.model` / `self.mesh_model`. All inputs come via
function arguments. They can be moved to a standalone utility module.

### Steps
1. Create `src/fea_toolkit/model/csm.py` with `pushover_to_adrs()` and
   `compute_performance_point()` as standalone functions
2. Add `AnalysisBuilder.pushover_to_adrs()` / `compute_performance_point()` that
   delegate to `csm.py`
3. Add `Builder` facade delegation to `_analysis`
4. Add tests for edge cases (zero mass, single mode, non-convergence)

---

## Phase 2: Edge Constraints (P1)

**Files affected:** `analysis_builder.py`, `builder.py`

The constraint application methods (`apply_spring_edge_constraints`,
`_apply_penalty_edge_constraints`) use `ops.twoNodeLink` and
`ops.equationConstraint` on the OpenSees domain. The AnalysisBuilder already
has `_edge_constraint_method` / `_saved_edge_constraints` state.

### Key change
Port the constraint *creation* methods, not just the handler selection.
Data source: `self.mesh_model` (shell sections for spring stiffness).

---

## Phase 3a: Lateral Load Helpers (P0)

**Files affected:** `analysis_builder.py`

Port these small helpers — they only access node coordinates + masses:

- `_compute_uniform_lateral_loads(direction, node_masses)` — mass-proportional
- `_compute_triangular_lateral_loads(direction, node_masses, period)` — ELF
- `_compute_mode_shape_lateral_loads(direction, node_masses, shapes, mode)` — modal
- `_compute_fallback_masses()` — self-weight mass estimate

`self.model.nodes` → `self.mesh_model.nodes`
`self.model.sections` → `self.mesh_model.sections`

---

## Phase 3b: config_overrides + rebuild (P0)

**Files affected:** `analysis_builder.py`

Add `config_overrides: Optional[Dict] = None` parameter to `build_domain()`.
When provided, merge on top of `self.config` for that build cycle only.

Then add:
```python
def rebuild_with_fiber_sections(self, brace_selection=None):
    overrides = {
        'element_type': 'dispBeamColumn',
        'create_fiber_sections': True,
        'use_elastic_sections': False,
    }
    if brace_selection:
        overrides['geom_transf_type'] = 'PDelta'
    self._reapply_edge_constraints(pushover_spring_scale)
```

---

## Phase 3c: Pushover Main Method (P0)

**Files affected:** `analysis_builder.py`, `builder.py`

Full `run_pushover_analysis()` port (~500 lines). Builds on 3a + 3b.

### Algorithm
1. Validate inputs, map direction→DOF
2. `rebuild_with_fiber_sections(...)`
3. `compute_seismic_masses()`
4. Apply gravity via `run_static_analysis()`
5. Choose control node (topmost un-restrained)
6. Lock gravity, create lateral pattern
7. Build lateral load vector via helpers (3a)
8. Configure `DisplacementControl` integrator
9. Push loop with algorithm fallback chain
10. Return results dict
