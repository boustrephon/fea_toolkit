# AnalysisBuilder Migration Plan

**Status:** Phase 1 ✅, 3a ✅, 3b ✅, CQC ✅, **P2 RS Methods ✅**  
**Last updated:** 2026-07-20  
**Total test count:** 495

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

### Phase 1 — CSM ✅
| Feature | Location | Builder facade |
|---------|----------|---------------|
| `pushover_to_adrs` | `model/csm.py` + AnalysisBuilder | delegates |
| `compute_performance_point` | `model/csm.py` + AnalysisBuilder | delegates |
| Unit tests (`TestCsmModule`) | `test_model.py` | 5 tests |

### Phase 3a — Lateral load helpers ✅
| Method | AnalysisBuilder |
|--------|----------------|
| `_compute_fallback_masses()` | L1920 |
| `_compute_uniform_lateral_loads()` | L1955 |
| `_compute_triangular_lateral_loads()` | L1984 |
| `_compute_mode_shape_lateral_loads()` | L2034 |

### Phase 3b — Rebuild infrastructure ✅
| Method | AnalysisBuilder |
|--------|----------------|
| `build_domain(config_overrides=...)` | L118 (try/finally restore) |
| `rebuild_with_fiber_sections()` | L163 |
| `_reapply_edge_constraints()` | L194 (placeholder) |

### CQC Standardisation ✅
| Change | Detail |
|--------|--------|
| Removed `builder._cqc_combine` static method | Was a thin wrapper delegating to `utils.cqc_combine` |
| `builder.py` uses `utils.cqc_combine` directly | 7 call sites updated (L7376, 7378, 7495-7498, 7637) |
| `spectrum.py` uses `utils.cqc_combine` internally | Replaced inline `_cqc_coeff` + `rho` double-sum with single call |
| `analysis_builder.py` already imported it | L25, used at L1749, 1751 |
| Tests updated | `TestCqcCombine` → `TestCqcCombineUtils`, 493 passing |

### Builder Facade Delegation Methods ✅
| Method | AnalysisBuilder | Notes |
|--------|---------------|-------|
| `run_static_analysis` | L992 | delegates + summed_reactions aggregation |
| `compute_seismic_masses` | L1095 | delegates |
| `extract_mode_shapes` | L1717 | delegates |
| `export_results` | L1780 | delegates |
| `extract_static_element_forces` | L1745 | delegates |

## ❌ Remaining by Phase

### Phase 2: Edge Constraints (P1)
- `apply_edge_constraints()` dispatcher
- `apply_spring_edge_constraints()` — `twoNodeLink` elements
- `_apply_penalty_edge_constraints()` — `equationConstraint` MPCs
- `_get_shell_area_ids()` helper
- `detect_unconnected_edges()` diagnostic

### Phase 3c: Pushover Main Method (P0)
- `run_pushover_analysis()` — full ~500 line port
- Requires Phase 2 (edge constraints) for constraint re-application
- Builder facade delegation (removes pushover guard)

### P2 — RS Methods ✅
| Feature | AnalysisBuilder | Builder facade |
|---------|---------------|---------------|
| `extract_element_rs_forces` | L1792 | delegates |
| `compute_rs_nodal_displacements` | L1900 | delegates |
| Tests (`TestTwoStageBuild`) | `test_element_rs_forces_via_two_stage_path` + `test_rs_nodal_displacements_via_two_stage_path` | 2 tests |

## ❌ Remaining

### Phase 2: Edge Constraints (P1)
- `apply_edge_constraints()` dispatcher
- `apply_spring_edge_constraints()` — `twoNodeLink` elements
- `_apply_penalty_edge_constraints()` — `equationConstraint` MPCs
- `_get_shell_area_ids()` helper
- `detect_unconnected_edges()` diagnostic

### Phase 3c: Pushover Main Method (P0)
- `run_pushover_analysis()` — full ~500 line port
- Requires Phase 2 (edge constraints) for constraint re-application
- Builder facade delegation (removes pushover guard)

### Phase 4: Cleanup / Deprecation (P2)
- Remove pushover guard (`save/restore _analysis`) once Phase 3c is done
- Remove FutureWarning for `use_preprocessor=False` once all features ported
- Deprecate `OpenSeesBuilder._rs_base_shear()` (moved to spectrum.py)
