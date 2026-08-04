---
title: "Pushover Results Storage & Nonlinear Visualization"
description: "Implementation plan and status for per-element pushover result recording (frame end forces, shell stress resultants, plasticity indicators) and nonlinear visualization."
status: "partial"
tags: [pushover, results, storage, visualization, npz, planning]
category: [planning]
related: [results_schema.md, pushover_analysis.md, viewer.md]
---
# Pushover Results Storage & Nonlinear Visualization — Implementation Plan

> **Status**: Phases 1–4 implemented; Phases 5+ pending
> **Created**: 2026-07-30
> **Related**: `docs/results_schema.md`, `docs/pushover_analysis.md`, `docs/viewer.md`

## Motivation

The current pushover analysis captures only global-level data (control displacement, base shear, step count). No per-element results are recorded at any push step — no frame end forces, no shell stress resultants, no plasticity indicators. This makes it impossible to:

- Identify which elements yield first
- Track plastic hinge formation sequence
- Visualize shell wall damage progression
- Export per-step data for Rhino or standalone plotting

This plan defines a unified approach for per-step pushover recording, NPZ storage, Selection-based filtering, and nonlinear visualization, consistent with the existing project architecture.

---

## Architecture Alignment

All components follow existing project patterns:

| Component | Existing pattern | Pushover extension |
|---|---|---|
| **Schema** | `static/`, `modal/`, `rs/` namespaces in `results_schema.py` | `pushover/{direction}/` namespace |
| **Writer** | `_collect_static()`, `_collect_modal()`, `_collect_rs()` in `npz_writer.py` | `_collect_pushover()` |
| **Selection** | `Selection` class with `element_types`, `sections`, `materials`, `groups`, `element_ids` | Add `elevation_range`, `story` fields |
| **Engine** | `AnalysisBuilder.run_pushover_analysis()` returns global dict | Optional per-step `eleResponse()` recording |
| **Plotting** | `plot_pushover_curve()`, `plot_npz_force_diagram()` accept Builder/dict/NPZ | New functions accept same three sources |
| **Viewer** | `ModelViewer` with `RenderBackend` abstraction (PyVista) | Animated deformation via timer callback + HTML export |

---

## Phase 1 — Selection Extensibility

### 1.1 Add `elevation_range` field

```python
@dataclass
class Selection:
    # ... existing fields ...
    elevation_range: Optional[Tuple[float, float]] = None
    story: Optional[List[str]] = None
```

**Filter semantics**:
- `elevation_range` = `(z_min, z_max)` — element is included if its mid-height Z coordinate falls within `[z_min, z_max]`. For frame elements, mid-height = `(z_i + z_j) / 2`. For area elements, mid-height = centroid Z.
- `story` = `["Roof", "Level 2"]` — element is included if it belongs to one of the named storeys (resolved via `MeshModel` storey data or by Z-level matching against known floor elevations).
- Both follow the same AND/OR logic as existing fields: AND across criteria, OR within a list.

**Backward compatibility**: Both default to `None` (no filter), so existing Selection usage is unchanged. No existing method signatures change.

### 1.2 Add `resolve_to_mesh_sets()` method

```python
def resolve_to_mesh_sets(
    self, mesh_model: "MeshModel"
) -> Tuple[Set[str], Set[str]]:
    """Resolve this Selection against a MeshModel.

    Returns:
        (record_frame_ids, record_area_ids) — sets of SAP2000 IDs
        for elements matching this selection.
    """
```

This mirrors the existing `_frame_matches()` / `_area_matches()` logic but reads from `MeshModel` instead of `SAPModelData`. The `MeshModel` has identical dict structures (`frame_assignments`, `area_assignments`, `sections`, `materials`, `groups`), so the resolution logic is a near copy-paste.

---

## Phase 2 — Engine Recording

### 2.1 New config keys

```python
config = {
    # ... existing keys ...

    # ── Pushover recording (opt-in) ──
    "record_pushover_steps": False,              # bool — enable per-step recording
    "pushover_record_selection": None,           # Selection or None → record all
    "pushover_record_shell_strains": False,      # bool — also record shell layer strains

    # ── Fiber-level recording (future) ──
    "pushover_record_fiber": False,              # bool — requires Selection, no --all
}
```

### 2.2 `run_pushover_analysis()` changes

In `AnalysisBuilder.run_pushover_analysis()`:

```python
def run_pushover_analysis(self, ...):
    # ... existing setup (gravity, lateral loads, displacement control) ...

    # ── Prepare recording ──
    record = self.config.get("record_pushover_steps", False)
    record_sel = self.config.get("pushover_record_selection", None)
    record_frames: Set[str] = set()
    record_areas: Set[str] = set()
    if record:
        if record_sel is not None:
            record_frames, record_areas = record_sel.resolve_to_mesh_sets(self.mesh_model)
        else:
            # Record all active elements
            record_frames = {eid for eid, e in self.mesh_model.frame_elements.items()
                             if not getattr(e, 'inactive', False)}
            record_areas = {eid for eid, a in self.mesh_model.area_elements.items()
                            if not getattr(a, 'inactive', False)}

    # ── Per-step loop ──
    step_results = []
    for step in range(1, num_steps + 1):
        # ... existing analysis step ...
        if ok == 0 and record:
            step_data = _record_step(
                self, step, record_frames, record_areas, dof_idx, direction
            )
            step_results.append(step_data)

    # Store on builder for later use
    self.pushover_step_results = step_results

    return {**global_results, "step_results": step_results}
```

### 2.3 `_record_step()` helper

```python
def _record_step(builder, step, frame_ids, area_ids, dof_idx, direction):
    """Query ops.eleResponse() for selected elements at current step."""
    data = {"step": step}

    # ── Frame elements ──
    frame_forces = {}  # eid -> {fx_i, fy_i, ..., mz_j}
    for eid in frame_ids:
        ops_tag = builder.frame_tag_map.get(eid)
        if ops_tag is None:
            continue
        try:
            f = ops.eleResponse(ops_tag, 'forces')  # 12 values
        except Exception:
            continue
        frame_forces[eid] = {
            'fx_i': f[0], 'fy_i': f[1], 'fz_i': f[2],
            'mx_i': f[3], 'my_i': f[4], 'mz_i': f[5],
            'fx_j': f[6], 'fy_j': f[7], 'fz_j': f[8],
            'mx_j': f[9], 'my_j': f[10], 'mz_j': f[11],
        }
    data["frame_forces"] = frame_forces

    # ── Shell elements (stress resultants) ──
    shell_forces = {}  # area_id -> {Nx, Ny, Nxy, Mx, My, Mxy}
    for aid in area_ids:
        ops_tag = builder._shell_tag_map.get(aid)
        if ops_tag is None:
            continue
        try:
            f = ops.eleResponse(ops_tag, 'forces')  # Shell forces
        except Exception:
            continue
        # ShellNLDKGQ returns 12 forces (similar layout)
        if len(f) >= 6:
            shell_forces[aid] = {
                'Nx': f[0], 'Ny': f[1], 'Nxy': f[2],
                'Mx': f[3], 'My': f[4], 'Mxy': f[5],
            }
    data["shell_forces"] = shell_forces

    return data
```

### 2.4 `export_pushover_results()` convenience method

```python
def export_pushover_results(self, path: str, direction: str) -> str:
    """Export recorded pushover step results to NPZ."""
    from ..io.npz_writer import write_pushover_results_npz
    return write_pushover_results_npz(
        path, self.mesh_model, self.pushover_step_results,
        direction=direction,
    )
```

---

## Phase 3 — NPZ Schema Extension

### 3.1 New arrays in `results_schema.py`

```python
PUSHOVER_GLOBAL_ARRAYS: Dict[str, Tuple] = {
    "pushover/{direction}/step":       ("N_step", "int"),
    "pushover/{direction}/control_disp": ("N_step", "float"),
    "pushover/{direction}/base_shear": ("N_step", "float"),
}

PUSHOVER_FRAME_ARRAYS: Dict[str, Tuple] = {
    "pushover/{direction}/frame_sap_id": ("N_recorded_frame", "str"),
    "pushover/{direction}/frame_fx_i":   ("N_step N_recorded_frame", "float"),
    # ... all 12 force components, global + local ...
    "pushover/{direction}/frame_mz_j":   ("N_step N_recorded_frame", "float"),
}

PUSHOVER_SHELL_ARRAYS: Dict[str, Tuple] = {
    "pushover/{direction}/shell_sap_id": ("N_recorded_shell", "str"),
    "pushover/{direction}/shell_Nx":     ("N_step N_recorded_shell", "float"),
    "pushover/{direction}/shell_Ny":     ("N_step N_recorded_shell", "float"),
    "pushover/{direction}/shell_Nxy":    ("N_step N_recorded_shell", "float"),
    "pushover/{direction}/shell_Mx":     ("N_step N_recorded_shell", "float"),
    "pushover/{direction}/shell_My":     ("N_step N_recorded_shell", "float"),
    "pushover/{direction}/shell_Mxy":    ("N_step N_recorded_shell", "float"),
}
```

**Sparse storage**: When a Selection is used, `N_recorded_frame` and `N_recorded_shell` are the counts of selected elements (not all elements). Each array includes an accompanying `frame_sap_id` / `shell_sap_id` index array so consumers can map back to the full model.

**Direction keying**: `+X`, `-X`, `+Y`, `-Y` — matching `run_pushover_4dir()`.

### 3.2 `write_pushover_results_npz()` in `npz_writer.py`

```python
def write_pushover_results_npz(
    path: str,
    mesh_model: "MeshModel",
    step_results: List[Dict],
    direction: str = "+X",
    force_unit: str = "kN",
    length_unit: str = "m",
) -> str:
    """Write pushover step results to NPZ.

    Args:
        path: Output .npz file path.
        mesh_model: MeshModel for geometry arrays.
        step_results: List of per-step dicts from
            AnalysisBuilder.pushover_step_results.
        direction: Push direction label (e.g. "+X").
        force_unit, length_unit: Metadata.
    """
```

### 3.3 File naming convention

```
{model_stem}_pushover_{direction}.npz
```

For the admin building: `Admin_0.7E_short term_pushover_+X.npz`

---

## Phase 4 — Nonlinear Visualization

**Status**: ✅ Complete — all visualization functions implemented including polish items (M2 color legends, M3 `use_biaxial`, M4 `animation_interval_ms`).

### 4.1 `plot_plastic_hinge_formation()`

**Status**: ✅ Complete (M1 unified data source, M2 scalar bar legend added, M3 `use_biaxial` parameter available)

**Purpose**: Show which frame elements yield and when, as the push progresses.

**Display**: 2D map.
- **X-axis**: Push step (or roof drift %)
- **Y-axis**: Element elevation (Z coordinate, sorted)
- **Color**: Demand-relative level per element per step
  - Gray = no data (element not recorded at that step)
  - Low demand (ratio < 0.5) = sampled at cmap position 0.0
  - Moderate demand (0.5 ≤ ratio < 1.0) = sampled at cmap position 0.5
  - High demand (ratio ≥ 1.0) = sampled at cmap position 1.0
- Each horizontal slice is one element's state evolution.
- The three sampled colours come from the named matplotlib colormap
  (`colormap="plasma"` by default, see §4.5) — the same 0.5 threshold and
  palette used by the 3D hinge view.

**Yield detection**: The hinge ratio is **range-normalised**: for each
element it is `ratio = |Mz| / peak|Mz|` across all push steps (and the SRSS
biaxial variant combining `My`/`Mz` when `use_biaxial=True`), *not*
`Fy × S`.  The ratio therefore measures how far an element has progressed
toward its own peak demand, not an absolute yield-capacity check.  The
hinge classification ratios are normalised against each element's peak
recorded demand, not a physical yield capacity.  For RC and
axially-loaded members, true yielding requires the force-deformation
response from fiber-section analysis (`forceBeamColumn` + fiber sections);
hinge classification output is demand-relative, so it must not be
confused with an absolute yield-capacity check.

### 4.2 `plot_shell_damage_map()`

**Purpose**: 3D view of shell elements colored by damage index at a selected push step.

**Display**: PyVista 3D view.
- Shell faces colored by damage index (max principal strain / yield strain)
- Color map: green (elastic) → yellow (yielding) → red (crushing)
- Step slider widget for interactive exploration
- Option to overlay deformed shape

**Damage index**: For layered shells (ShellNLDKGQ), computed from nD material stress/strain at Gauss points. For elastic shells (ShellMITC4), stress resultants normalized by section capacity.

### 4.3 `plot_pushover_envelope()`

**Purpose**: 3D force envelope on model geometry.

**Display**: PyVista 3D view with force flag diagrams at the peak push step.
- Frame elements: moment flag diagram (Mz or My) using `overlay_forces()` pattern
- Shell elements: principal stress vectors or contour fill
- Shows the extreme state across all push steps

### 4.4 `animate_pushover_deformation()`

**Purpose**: Animated 3D view of the structure deforming through push steps.

**Display**: PyVista interactive or HTML export.
- Deformed shape at each step (amplified displacement)
- Frame coloring by yield state (elastic → plastic)
- Shell coloring by damage index
- Forward/backward controls (PyVista slider widget)
- HTML export for standalone sharing

**Implementation**:
```python
def animate_pushover_deformation(
    data,           # Builder, dict, or NPZ path
    step: int = None,         # static view at single step
    export_html: str = None,  # path for HTML export
    scale: float = 50.0,      # displacement amplification
):
    # ... extract per-step displacements and forces ...
    # ... build PyVista mesh ...
    # ... use plotter.add_time_set() or timer callback ...
```

### 4.5 Colour Legend Helpers

Two private helper functions generate PyVista colour legends for pushover visualizations:

**`_add_hinge_color_legend()`** — adds a scalar bar for the frame hinge
demand-relative ratio scale.  The scalar-bar title is
``"Relative Moment Demand (peak-normalized)"`` — the ratios plotted are the
demand-relative hinge ratios (moment demand normalised by the observed
peak capacity), **not** a physical yield-capacity ratio:
- **Colour 1** (ratio < 0.5) — low demand (cmap position 0.0)
- **Colour 2** (0.5 ≤ ratio < 1.0) — moderate demand (cmap position 0.5)
- **Colour 3** (ratio ≥ 1.0) — high demand (cmap position 1.0)

The three colours are sampled from the `colormap` parameter (default
`"plasma"`, a perceptually-uniform colour-blind-safe map) and interpolated
continuously between them.  The 0.5 threshold is shared with the 2D
heatmap (`plot_plastic_hinge_heatmap`) so both views always agree.
Accessible alternates: `"viridis"`, `"cividis"`, `"turbo"`.  Unknown
colormap names fall back to blue/yellow/red.

**`_add_shell_color_legend()`** — adds a scalar bar for the shell damage index scale:
- **Green** (ratio < 0.7) — elastic
- **Yellow** (0.7 ≤ ratio < 1.0) — yielding
- **Red** (ratio ≥ 1.0) — damaged / crushed
- **Gray** — no data (NaN)

Both build a ``pyvista.LookupTable`` and call ``plotter.add_scalar_bar()``.  They handle PyVista version differences with a ``TypeError`` fallback:

- ``LookupTable.n_values`` is used instead of the removed ``number_of_colors`` attribute.
- ``LookupTable.values`` is set directly (RGB ``uint8`` array) instead of the removed ``table`` attribute.
- The ``lookup_table`` keyword argument to ``add_scalar_bar()`` is provided when supported; a ``TypeError`` fallback omits it for older PyVista (< v0.44).

### 4.6 `plot_frame_force_evolution()`

**Purpose**: For selected elements, plot force component vs. roof drift.

**Display**: 2D subplot grid.
- One subplot per selected element
- X-axis: roof drift (%)
- Y-axis: force quantity (M, V, N)
- Shows full force-deformation history, not just envelope

---

## Phase 5 — Fiber-Level Output (Future, Deferred)

**Config**: `pushover_record_fiber: True` — requires an explicit `pushover_record_selection` (no `None` = all allowed).

**Data recorded**: Per selected element, per integration point, per step:
- Section axial strain and curvature (ε, κy, κz)
- Fiber stress and strain at each fiber (for fiber sections)
- nD material stress/strain state at Gauss points (for layered shells)

**NPZ namespace**: `pushover/{direction}/fiber/...` — separate arrays due to high data volume.

**Enforcement**:
```python
if config.get("pushover_record_fiber") and config.get("pushover_record_selection") is None:
    raise ValueError(
        "Fiber-level recording requires an explicit pushover_record_selection. "
        "No '--all' option is supported for fiber output."
    )
```

---

## Implementation Order

| Step | Description | Files touched |
|---|---|---|
| **1a** | Add `elevation_range`, `story` fields to `Selection` | `model/selection.py` |
| **1b** | Add `resolve_to_mesh_sets()` to `Selection` | `model/selection.py` |
| **1c** | Tests for new Selection fields | `tests/test_model.py` |
| **2a** | Add `record_pushover_steps` + `pushover_record_selection` config keys | `opensees/analysis_builder.py` |
| **2b** | Implement `_record_step()` and per-step loop | `opensees/analysis_builder.py` |
| **2c** | Add `export_pushover_results()` convenience method | `opensees/analysis_builder.py` |
| **3a** | Define pushover NPZ arrays in schema | `io/results_schema.py` |
| **3b** | Implement `write_pushover_results_npz()` | `io/npz_writer.py` |
| **3c** | Test NPZ round-trip | `tests/test_io.py` |
| **4a** | `plot_plastic_hinge_formation()` | `plotting/viz.py` |
| **4b** | `plot_shell_damage_map()` (3D) | `plotting/viz.py` |
| **4c** | `plot_pushover_envelope()` (3D) | `plotting/viz.py` |
| **4d** | `animate_pushover_deformation()` (interactive + HTML) | `plotting/viz.py` |
| **4e** | `plot_frame_force_evolution()` (2D) | `plotting/viz.py` |
| **4f** | Re-export from `plotting/__init__.py` | `plotting/__init__.py` |
| **5** | Wire into `admin_pushover_v4.py` as Phase E | `local/.../admin_pushover_v4.py` |

---

## Selection Examples

```python
# Record only base-level columns (Z 0–3m)
sel = Selection(
    element_types=["Frame"],
    elevation_range=(0.0, 3.0),
)

# Record only shear walls
sel = Selection(
    element_types=["Area"],
    sections=["Shear Wall"],
)

# Record coupling beams on a specific storey
sel = Selection(
    element_types=["Frame"],
    sections=["CB30x60", "CB40x80"],
    story=["Level 3"],
)

# Record everything (default when pushover_record_selection is None)
# → records all active elements
```

---

## Notes

- **Memory**: Per-step recording of all elements for 100 steps with 500 frames + 170 shells ≈ 100 × (500 × 12 + 170 × 6) × 8 bytes ≈ 5.6 MB. Negligible for modern systems. Fiber-level output (deferred Phase 5) would be 10–100× larger and requires Selection.
- **Performance**: `ops.eleResponse()` calls per element per step add overhead proportional to selection size. For full-model recording, expect ~10–20% increase in pushover runtime.
- **Compatibility**: All additions are opt-in via config flags. Existing pushover workflows are unaffected.
- **No OpenSees `recorder` command**: We use `ops.eleResponse()` queries rather than OpenSees recorders because (a) it integrates cleanly with the per-step Python loop, (b) avoids file I/O during analysis, and (c) works with the existing Builder pattern.