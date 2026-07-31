# CSM Test Model Development Plan

## Status: ⚠️ Partially implemented — operations 1 & 2 need rewriting

**Date**: 2026-07-31  
**Last updated**: 2026-07-31  
**Context**: The existing CSM integration test `test_compute_performance_point` in
`tests/test_workflows.py` originally used an ISection cantilever which gave degenerate ADRS
conversion (`Gamma = 1.0`, `M_eff = 1.0`, `mu = 1.0`).  The test and model were partially
migrated to an RC frame, but the model still uses SI units (N, m) instead of the planned
kN-m system, and has a single material instead of the intended three-material setup.  This
blocks meaningful nonlinear response — the plan below fixes this.

---

## Current Status (2026-07-31)

| Item | File | Status |
|---|---|---|
| `make_rc_frame_model()` — single-storey, 1-bay RC frame, kN-m units, 3 materials (C30/Rebar/Q355), `MassSource` self-weight + DEAD floor load | `examples/sample_model.py` | ✅ Complete (rewritten per Operation 1) |
| `sample_rc_md` / `sample_rc_ab` fixtures | `tests/test_workflows.py` | ✅ Complete (docstrings updated for single-storey) |
| `test_compute_performance_point` — strengthened assertions per plan table | `tests/test_workflows.py` | ✅ Complete (Operation 2); demand spectrum scaled 13.0× so the frame yields |
| 3-material plan (C30, Rebar, Q355) | `examples/sample_model.py` | ✅ Complete |
| kN-m unit system | `examples/sample_model.py` | ✅ Complete (`{"F": "KN", "L": "m", "T": "C"}`) |
| Plan-specified test assertions (units, S_dp/S_ap, mu>1, converged, M_eff range, base-shear range) | `tests/test_workflows.py` | ✅ Complete |
| Capacity curve plot (Operation 3) | `local/plot_rc_capacity_curve.py` | ✅ Complete (saves `local/output/rc_capacity_curve.png`) |

### Additional fixes landed during implementation

| Fix | File | Rationale |
|---|---|---|
| `run_modal_analysis()` now returns `'nodal_masses'` (keyed by node tag) via new `_query_nodal_masses()` helper | `src/fea_toolkit/opensees/analysis_builder.py` | The ADRS conversion in `csm.pushover_to_adrs()` reads `modal_results['nodal_masses']`; without it the conversion degenerated to `Gamma = M_eff = 1.0` |
| Best-mode selection rejects degenerate modes (`M_star < 1e-8·M_total`) | `src/fea_toolkit/model/csm.py` | For the symmetric 1-storey frame, the orthogonal mode's machine-noise ratio `L²/M_star` spuriously out-ranked the true push-direction mode, giving `Gamma ≈ 1.8e16` |
| Control-node lookup accepts `'control_node'` (and `'control_node_tag'` fallback) | `src/fea_toolkit/model/csm.py` | `run_pushover_analysis()` emits `'control_node'`; the lookup previously missed it so `phi_control` fell back to 1.0 |
| ADRS `S_d` uses `abs(Gamma)` | `src/fea_toolkit/model/csm.py` | Eigenvector sign is arbitrary; a negative `Gamma` flipped `S_d` negative, masking all capacity points |

---

## Unit Handling Decision

The model is authored directly in `SAPModelData` (not via .s2k), so values set on `Material`
objects bypass the framework's SI→model scaling path — `apply_material_defaults()` only
fills missing defaults and never overwrites explicitly-set values.  The plan uses
**kN-m model units**: `{"F": "KN", "L": "m", "T": "C"}`.

All material values below are therefore in **kPa** (model stress units for the kN-m system):

| Material | E_mod (kPa) | Fc (kPa) | Fy (kPa) | G_mod (kPa) | unit_weight (kN/m³) |
|---|---|---|---|---|---|
| C30 | 15.54e6 | 20.1e3 | — | 6.475e6 | 25.0 |
| Rebar | 199.95e6 | — | 413.685e3 | derived from E_mod via ν | 77.0 |
| Q355 | 206e6 | — | 355e3 | 79.23e6 | 77.0 |

These are the effective post-reduction values (C30 with 0.7× short-term modulus factor, per
the `Admin_0.7E_short term.s2k` reference).  The plan calls `md.apply_material_defaults()`
after construction to auto-derive `G_mod` where missing (e.g. `G_mod` for Rebar is computed
from `E_mod` via Poisson's ratio).

### Section hierarchy & fiber dispatch

- **Columns** (`ConcreteRectangularSection`): `to_fiber_patches()` emits three tags —
  `mat_tag` (unconfined cover, `Concrete01`), `mat_tag+1` (confined core, `Concrete01`),
  `mat_tag+2` (rebar `Steel02`).  References `"C30"` as primary material and
  `rebar_material="Rebar"` so the builder's three-level rebar `Fy`/`Es` resolution
  (config override → SAP2000 lookup → framework defaults) engages.
- **Beams** (`RectangularSection`): also implements `to_fiber_patches()` with the same
  3-tag concrete convention (40 mm hardcoded cover, 0.6 % steel ratio).  References
  `"C30"`.  This is an approximation (no explicit rebar layout like the columns), but
  acceptable for a test model.

Both section types therefore trigger `rebuild_with_fiber_sections()` automatically.

---

## Operation 1: Rewrite `make_rc_frame_model()` in `examples/sample_model.py`

### Changes from the current implementation

1. **Set units** — `SAPModelData.units = {"F": "KN", "L": "m", "T": "C"}`.
2. **Replace the single material** with 3 materials using the exact values from the
   unit-handling table above.
3. **Wire `rebar_material`** — the `ConcreteRectangularSection` columns get
   `rebar_material="Rebar"`.
4. **Apply material defaults** — call `md.apply_material_defaults()` after construction
   to auto-derive `G_mod`, `nu` and any other missing properties from the framework
   defaults.

### What stays the same

- Geometry: 6 nodes (2-storey, 1-bay, 4 m bay, 3 m storey)
  - Nodes: 1:(0,0,0), 2:(4,0,0), 3:(0,0,3), 4:(4,0,3), 5:(0,0,6), 6:(4,0,6)
  - Elements: C1–C4 columns (tags 1–4), B1–B2 beams (tags 5–6)
- Column sections: `ConcreteRectangularSection` 300×300 mm, cover=40 mm, 4φ16 top/bot
- Beam sections: `RectangularSection` 300×500 mm, referencing `"C30"`
- Loads: DEAD self-weight only; `MassSource(elements=True)`
- No `frame_dist_loads` — lateral loads generated at pushover time by `lateral_load_type='uniform'`

## Operation 2: Strengthen `test_compute_performance_point` in `tests/test_workflows.py`

### Changes

- **Fixture**: `make_rc_frame_model()` → `preprocess_model()` → `AnalysisBuilder` (already correct)
- **Uniform lateral pushover** (not modal-based): `lateral_load_type='uniform'` (already correct)
- **Modal analysis**: still run (needed for CSM ADRS conversion) but assertions don't check mode details
- **Pushover params**: `max_disp=0.3`, `num_steps=50` (was 40), `control_node_tag=6`
- **CSM params**: use defaults (`max_iter=50`, `tol=0.01`) — don't override to looser values

### Assertions

| Assertion | Rationale |
|---|---|
| `pushover_results["units"] == {"F": "KN", "L": "m", "T": "C"}` | Verify unit system propagated through the pipeline |
| `S_dp > 1e-6`, `S_ap > 1e-6` | Non-trivial performance point |
| `pp['mu'] > 1.0` (strict) | RC frame must yield — elastic response (`mu = 1`) is a test failure |
| `pp['converged'] == True` | CSM iteration must converge |
| First non-zero base shear < 1000 (kN range) | Sanity check on base shear magnitude |
| Total seismic mass ~5–10 tonnes | Verified via `M_eff` (effective modal mass must be a plausible fraction of the physical frame mass, not 4917 t) |

## Operation 3: Generate capacity-curve plot for visual review

- Script: `local/plot_rc_capacity_curve.py` (new, under gitignored `local/`)
- Pipeline: `make_rc_frame_model()` → `preprocess_model()` → `AnalysisBuilder`
- Plots `base_shear` vs `control_disp` (capacity curve only — no ADRS, no CSM)
- Saves as PNG for user review

---

## Files Changed

| File | What | Operation |
|---|---|---|
| `examples/sample_model.py` | Rewrite `make_rc_frame_model()`: kN-m units, 3 materials, plan values | 1 |
| `tests/test_workflows.py` | Strengthen `test_compute_performance_point` assertions per table above | 2 |
| `local/plot_rc_capacity_curve.py` | New: capacity curve generation + plot script | 3 |

---

## Relationship to the Deprecation Plan

`docs/deprecation_plan.md` lists "RC nonlinear static analysis working comfortably" as
the trigger for the cleanup PR.  Its prerequisites identify 6 gaps; **gap #6 (CSM
bilinearisation validation)** is directly addressed by this work — a real RC capacity
curve with meaningful ductility validates that `bilinearize_composite` produces sensible
yield points.  The other gaps (Mander confinement wiring, rebar layer offset, 2D/3D
dispatch, pushover solver tuning, validation benchmarks) are separate work items.

---

## Design note: rebar-material support — ✅ RESOLVED (2026-07-31)

The framework now resolves the rebar `Steel02` material independently of the
concrete material.  `ConcreteRectangularSection`, `ConcreteCircularSection` and
`ShellSection` carry a `rebar_material` field (wired from `RebarMatL`/`RebarMat`
by the S2K parser).  The `AnalysisBuilder` (`_create_single_section()`) and
`export_mesh_model_to_tcl` resolve rebar `Fy`/`Es` with three-level priority:

1. **Config override** — `rebar_Fy_override` / `rebar_Es_override` (authored in
   SI (Pa), scaled to model units via `stress_scale_factor()`).
2. **SAP2000 lookup** — `section.rebar_material` material entry's `Fy`/`E_mod`
   (model units, used as-is).
3. **Framework defaults** — `DEFAULT_FY_REBAR_PA` / `DEFAULT_E_S_PA` scaled to
   model units.

Covered by `tests/test_rebar_material.py` (parser + Tcl export).