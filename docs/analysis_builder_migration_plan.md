# AnalysisBuilder Migration Plan

**Status:** Phase 1 ✅, 3a ✅, 3b ✅, CQC ✅, P2 RS ✅, **Phase 2 Edge Constraints ✅**  
**Last updated:** 2026-07-20  
**Total test count:** 499

## Overview

Port remaining legacy-only features from `OpenSeesBuilder` to `AnalysisBuilder`.
The two-stage path (`use_preprocessor=True`) is the default.

Summary of completed work:

| Phase | What | Methods ported |
|-------|------|----------------|
| **✅ Core facades** | 7 analysis methods | `run_modal_analysis`, `run_response_spectrum_analysis`, `run_static_analysis`, `compute_seismic_masses`, `extract_mode_shapes`, `export_results`, `extract_static_element_forces` |
| **✅ Phase 1** | CSM | `pushover_to_adrs`, `compute_performance_point` → `model/csm.py` + tests |
| **✅ Phase 3a** | Lateral load helpers | `_compute_fallback_masses`, `_compute_uniform/triangular/mode_shape_lateral_loads` |
| **✅ Phase 3b** | Rebuild infrastructure | `build_domain(config_overrides=...)`, `rebuild_with_fiber_sections`, `_reapply_edge_constraints` |
| **✅ Phase 2** | Edge constraints | `apply_edge_constraints`, `apply_spring_edge_constraints`, `_apply_penalty_edge_constraints`, `_get_shell_area_ids`, `detect_unconnected_edges` |

## ❌ Remaining

### Phase 3c: Pushover Main Method (P0)
Port `run_pushover_analysis()` — the largest single method remaining (~500 lines in builder.py).

- **Depends on:** Phase 2 (edge constraints must be re-applied on rebuild)
- **Builder facade:** Add delegation (removes the pushover guard `save/restore _analysis`)
- **Key ops commands:** `ops.integrator`, `ops.analyze`, convergence fallback chain
- **Config keys:** `push_direction`, `control_node`, `max_drift`, `num_steps`, `lateral_load_type`

### Phase 4: Cleanup / Deprecation (P2)
| Task | Triggers |
|------|----------|
| Remove pushover guard (`save/restore _analysis` in Builder) | After Phase 3c |
| Remove FutureWarning for `use_preprocessor=False` | After all features ported |
| Deprecate `OpenSeesBuilder._rs_base_shear()` | Moved to `spectrum.py` |

### Phase 4: Cleanup / Deprecation (P2)
- Remove pushover guard (`save/restore _analysis`) once Phase 3c is done
- Remove FutureWarning for `use_preprocessor=False` once all features ported
- Deprecate `OpenSeesBuilder._rs_base_shear()` (moved to spectrum.py)
