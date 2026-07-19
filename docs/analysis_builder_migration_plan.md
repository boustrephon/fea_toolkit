# AnalysisBuilder Migration Plan

**Status:** ✅ Phase 1, 3a, 3b, CQC, P2 RS, Phase 2 Edge Constraints, **Phase 3c Pushover**  
**Last updated:** 2026-07-20  
**Total test count:** 501

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
| **✅ Phase 3c** | Pushover main method | `run_pushover_analysis` — gravity + lateral + displacement-controlled push loop with algorithm fallback chain. Supports `uniform`, `triangular`, `mode1`, and `pattern` load types. |

## ✅ All features ported to AnalysisBuilder

All functional features have been ported. The two-stage build path now supports
the full workflow: static, modal, response-spectrum, pushover, edge constraints,
CSM, and result export.

### Phase 4: Cleanup / Deprecation (P2)
| Task | Reason |
|------|--------|
| Remove pushover guard (`save/restore _analysis` in Builder) | No longer needed — AnalysisBuilder handles pushover directly |
| Remove FutureWarning for `use_preprocessor=False` | Legacy path will be removed |
| Deprecate `OpenSeesBuilder._rs_base_shear()` | Moved to `spectrum.py` |
