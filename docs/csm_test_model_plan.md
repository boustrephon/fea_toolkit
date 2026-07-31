# CSM Test Model Development Plan

## Status: ✅ Approved — not yet implemented

**Date**: 2026-07-31  
**Context**: The existing CSM integration test `test_compute_performance_point` in
`tests/test_workflows.py` uses an ISection cantilever which gives degenerate ADRS
conversion (`Gamma = 1.0`, `M_eff = 1.0`, `mu = 1.0`).  A proper RC frame
model with `ConcreteRectangularSection` columns and `RectangularSection` beams
(capable of `to_fiber_patches()`) must replace it.

---

## Operation 1: Rewrite `make_rc_frame_model()` in `examples/sample_model.py`

- **Units**: `{"F": "KN", "L": "m", "T": "C"}` stored in `SAPModelData.units`
- **3 Material entries** (following S2K convention from `Admin_0.7E_short term.s2k`):
  ```python
  "C30": Material(type="Concrete", E_mod=15.54e6, G_mod=6.475e6, nu=0.2,
                  unit_weight=25.0,          # kN/m³
                  Fc=20.1e3,                 # 20.1 MPa → kPa
                  Fy=413.685e3,              # rebar yield (per A615Gr60) — framework convention
                  )
  "Rebar": Material(type="Rebar", E_mod=199.95e6, unit_weight=77.0,
                    Fy=413.685e3)            # for reference completeness
  "Q355": Material(type="Steel", E_mod=206e6, G_mod=79.23e6, nu=0.3,
                   unit_weight=77.0, Fy=355e3)
  ```
- **Sections**:
  - Columns: `ConcreteRectangularSection` 300×300 mm, cover=40 mm, 4φ16 top/bot
  - Beams: `RectangularSection` 300×500 mm
  - Both reference `"C30"` so `to_fiber_patches()` engages → `rebuild_with_fiber_sections()`
- **Geometry**: 2‑storey, 1‑bay, 4 m bay, 3 m storey
  - Nodes: 1:(0,0,0), 2:(4,0,0), 3:(0,0,3), 4:(4,0,3), 5:(0,0,6), 6:(4,0,6)
  - Elements: C1–C4 columns (tags 1–4), B1–B2 beams (tags 5–6)
- **Loads**: DEAD self‑weight only; `MassSource(elements=True)`
- No `frame_dist_loads` — lateral loads generated at pushover time by `lateral_load_type='uniform'`

## Operation 2: Rewrite `test_compute_performance_point` in `tests/test_workflows.py`

- Fixture uses `make_rc_frame_model()` → `preprocess_model()` → `AnalysisBuilder`
- **Uniform lateral pushover** (not modal‑based): `lateral_load_type='uniform'`
- Modal analysis still run (needed for CSM ADRS conversion) but assertions don't check mode details
- Pushover params: `max_disp=0.3`, `num_steps=50`, `control_node_tag=6`
- Assertions:
  - `S_dp > 1e-6`, `S_ap > 1e-6`, `mu >= 1.0`, `converged == True`
  - `pushover_results["units"] == {"F": "KN", "L": "m", "T": "C"}`
  - Base shear in kN range (first non‑zero shear < 1000)
  - Total seismic mass is ~5–10 tonnes (not 4917)

## Operation 3: Generate capacity‑curve plot for visual review

- Script in `local/` runs the full RC pushover pipeline
- Plots `base_shear` vs `control_disp` (capacity curve only — no ADRS, no CSM)
- Saves as PNG for user review

---

## Design note: rebar‑material support — ✅ RESOLVED (2026‑07‑31)

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
