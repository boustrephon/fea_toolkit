---
title: "Force-Diagram Unification (Phase B)"
description: "Detailed design for unifying the four force-diagram plotting entry points into one unit-aware API."
status: "draft"
tags: [planning, refactor, plotting, phase-b]
category: [planning]
---

# Force-Diagram Unification (deprecation-plan Phase B)

**Status:** draft — no code yet.

## Goal

Replace the four overlapping force-diagram entry points with **one unified,
unit-aware** function that covers every input the toolkit already supports
(`AnalysisBuilder`, in-memory result dicts including RS `element_results`,
and NPZ paths) and dispatches 2D-vs-3D and static-vs-CQC-RS from the input
shape — so callers never have to pick the "right" function or know the
backend in advance.

## Current state

| Function | Location | Backend | Input | Kind |
|---|---|---|---|---|
| `plot_force_diagram_3d()` | `plotting/viz.py:4147` | PyVista 3D | Builder + static force dict, or NPZ data dict | static |
| `plot_rs_force_diagram()` | `plotting/viz.py:4532` | matplotlib 2D | RS `element_results` list or full dict | RS (CQC) |
| `plot_npz_force_diagram()` | `plotting/viz.py:5051` | matplotlib 2D | NPZ path only | static |
| `plot_npz_moment_3d()` | `plotting/viz.py:5120` | PyVista 3D | NPZ path only | static |

Shared helpers already present:

- `_resolve_mesh_data(source, collapse_to_parents=False)` — `plotting/viz.py:2774`.
  Resolves node coordinates + element connectivity from a Builder or an NPZ
  data dict.  This is the geometry half of the input layer.
- `_load_npz_for_plotting(npz_path, combo=None)` — `plotting/viz.py:4928`.
  Loads an NPZ and returns `elem_data` (per-element end forces at
  `z_i`/`z_j`) plus `force_unit` / `length_unit` from the file metadata.
  This is the unit-aware NPZ half of the input layer.

## Duplication map

1. **Three separate input-resolution paths** (Builder, result dicts, NPZ
   path) with no shared normalisation — each function re-implements
   unwrap/dispatch logic (`isinstance(dict)`, key lookup, combo handling).
2. **Two unit strategies** — `plot_rs_force_diagram` hardcodes
   `force_unit="kN"` / `length_unit="m"` defaults; the NPZ functions read
   units from file metadata; the Builder path has units on the model but
   `plot_force_diagram_3d` does not surface them.
3. **Static vs RS is implicit** in the caller's function choice, yet both
   end up as per-element end-force series (RS adds `z_mid` + CQC-combined
   quantities like `My_i`).
4. **2D vs 3D is a separate axis** (PyVista vs matplotlib) that callers
   must know about in advance.

## Design

### 1. Canonical intermediate — `ForceDiagramData`

A dataclass (in `plotting/force_diagram.py`) that any resolved input is
reduced to:

```python
@dataclass
class ForceDiagramData:
    nodes: dict           # node_tag -> (x, y, z)   (for 3D geometry)
    elements: list        # connectivity for centreline drawing
    series: list[dict]    # per element: {q_i, q_j, z_i, z_j, z_mid}
    quantity: str         # canonical quantity key ('My', 'Vz', ...)
    force_unit: str
    length_unit: str
    kind: str             # 'static' | 'rs'
```

### 2. Input resolution — `_resolve_source()`

`_resolve_source(source, force_data, combo)` normalises any accepted input:

- `source` is an `AnalysisBuilder` → read `mesh_model.units` + nodes +
  elements; use `force_data` (static) or `force_data["element_results"]`
  (RS).
- `source` is an NPZ **path or dict** → delegate to the existing
  `_load_npz_for_plotting` / `_resolve_mesh_data`; `combo` picks the
  `static/{combo}/` slice; RS arrays auto-detected from the presence of
  `rs/` keys.
- `force_data` is the full `extract_element_rs_forces()` dict → unwrap the
  `"element_results"` key (same rule as today's
  `plot_rs_force_diagram`).

Resolution order for units (first hit wins): explicit `force_unit` /
`length_unit` args → builder/model units → in-memory dict `"units"` key →
NPZ metadata.  **Never hardcode `kN`/`m`.**

### 3. Unified entry point — `plot_force_diagram()`

```python
def plot_force_diagram(
    source, force_data=None, *,
    quantity="My", kind=None, dimension=None,
    combo=None, force_unit=None, length_unit=None,
    use_local=True, both_ends=False, **kwargs,
):
```

Dispatch rules:

- `kind` is inferred when `None`: `"rs"` if `force_data` is a full RS
  results dict or a list of per-element RS records (any element carries
  the `z_mid` marker); `"static"` otherwise.
- `dimension` is inferred when `None`: `"3d"` if PyVista is available and
  geometry is present; `"2d"` (matplotlib) otherwise.  Callers may pin it.
- `quantity` accepts both the RS key style (`'My_i'`, `'Vz_i'`) and the
  plain style (`'My'`, `'Mz'`) and normalises internally.
- `kind == "rs"` → 2D matplotlib line plot (today's
  `plot_rs_force_diagram` rendering, unit-aware).
- `kind == "static"` + `"3d"` → today's `plot_force_diagram_3d` rendering
  (flag/tube modes preserved via `**kwargs`).
- `kind == "static"` + `"2d"` → today's `plot_npz_force_diagram` rendering.

### 4. Backward compatibility (deprecation convention §4.5)

- `plot_force_diagram_3d`, `plot_rs_force_diagram`,
  `plot_npz_force_diagram`, `plot_npz_moment_3d` become **thin wrappers**
  over the unified function for one release cycle.
- After the release, the wrappers are removed in a single cleanup PR (same
  pattern as the deprecation-removal Phase 3 PR).

## Test plan

- **Table-driven input equivalence**: the same model plotted via Builder,
  in-memory result dict, and NPZ path must produce identical series data.
- **RS list-vs-dict**: `element_results` list and the full
  `extract_element_rs_forces()` dict give the same figure.
- **Unit propagation**: NPZ metadata `kN`/`m` vs `N`/`mm` produce the
  correct axis labels; explicit `force_unit` overrides metadata.
- **Dispatcher**: static vs RS classification (covering both the RS
  `element_results` list form and the full `extract_element_rs_forces()`
  dict form), 2D vs 3D selection, and manual overrides behave as specified.
- **Wrappers**: each legacy name still passes its current call patterns.

## Milestones

1. Add `plotting/force_diagram.py` with `ForceDiagramData` +
   `_resolve_source`; rewire `plot_npz_force_diagram` +
   `plot_npz_moment_3d` through it (NPZ-only slice).
2. Add Builder/dict resolvers; rewire `plot_force_diagram_3d` +
   `plot_rs_force_diagram` through the same layer.
3. Land `plot_force_diagram()`; convert the four legacy names to wrappers.
4. (Next release) remove the wrappers in a cleanup PR.

## Out of scope

- Splitting `plotting/viz.py` itself — do that *after* this lands, so the
  split operates on the smaller post-unification module (see
  `docs/_pending_work.md`).
- 2D matplotlib rendering of RS results in 3D — keep RS as the 2D line plot
  unless a concrete request appears.
- Changing the NPZ schema (`io/results_schema.py`) — the unification reads
  the existing schema only.

## Risks

- `_resolve_mesh_data` assumes Builder or NPZ-dict input; in-memory
  *static* result dicts from `extract_static_element_forces()` carry no
  geometry, so Builder geometry is required (or an optional `nodes`/
  `elements` argument on the unified function).
- RS `element_results` key names (`z_mid`, quantity suffixes) are a
  de-facto contract — document them as such in the module docstring.
- PyVista stays optional: the 2D path must never import it.
