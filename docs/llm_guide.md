---
title: "LLM & AI Assistant Guide"
description: "Canonical goal-to-code mapping, full public API surface by domain, and key technical constraints — structured for language model consumption."
status: "complete"
tags: [llm, ai, guide, api-reference, usage]
category: [core-pipeline]
---
# LLM / AI Assistant Guide for fea_toolkit

This document is structured for **language model consumption**. It provides
a canonical mapping from user goals to code, lists the full public API surface
by category, and documents key technical constraints.

---

## 1. Canonical Import Patterns

### One-liner: discover the full API

```python
import fea_toolkit
# Then inspect: fea_toolkit.__all__, fea_toolkit.io.__all__, etc.
```

### Quick start (4 lines — minimal pipeline)

```python
from fea_toolkit import SAP2000Parser, preprocess_model, AnalysisBuilder
md = SAP2000Parser("model.s2k").parse().get_model_data()
mesh = preprocess_model(md, {"element_type": "elasticBeamColumn"})
builder = AnalysisBuilder(mesh, {}).build_domain()
```

### Subpackage imports (by domain)

| Domain | Import | What you get |
|--------|--------|-------------|
| Core model | `from fea_toolkit import SAPModelData, Selection` | Dataclass types + filter |
| I/O | `from fea_toolkit.io import ...` | Parser, NPZ, reports, ground motion |
| OpenSees | `from fea_toolkit.opensees import ...` | Preprocessor, AnalysisBuilder, Tcl export |
| Plotting | `from fea_toolkit.plotting import ...` | 3D views, deformed shapes, force diagrams |
| Analysis | `from fea_toolkit.analysis import ...` | Typed analysis objects + manager |
| Model | `from fea_toolkit.model import ...` | Geometry, stories, checks, CSM |

---

## 2. Task → Function Reference

### Data Ingestion

```
"Parse a SAP2000 model"              → SAP2000Parser(path).parse().get_model_data()
"Parse an ETABS model"               → SAP2000Parser also works for .e2k
"Cache parsed data to JSON"          → parser.to_json(path)
"Load JSON cache"                    → parser.from_json(path)
"Enrich sections from catalogue"     → SectionLibrary(path).enrich_section(sec)
```

### Model Topology

```
"Split frames at intersecting nodes" → Preprocessor with config split_elements=True
"Split areas at frame edges"         → from fea_toolkit.model import split_areas_at_frame_edges
"Mesh area elements"                 → from fea_toolkit.model import mesh_area_elements
"Apply edge constraints"             → AnalysisBuilder.build_domain() handles this
"Detect unconnected shell edges"     → from fea_toolkit.model import find_constraint_edges
"Detect wall nodes inside slabs"     → from fea_toolkit.model import find_wall_nodes_inside_slabs
"Remove floating orphan nodes"       → from fea_toolkit.model import remove_floating_nodes
```

### Element Selection (for targeted display / export)

```
"Select all braces"                  → Selection.from_brace_sections(model)
"Select areas with specific section" → Selection(element_types=['Area'], sections=['Slab 200mm'])
"Filter by group"                    → Selection(groups=['Moment Frame'])
"Get a self-contained subset model"  → selection.filter_model(md)
```

### Analysis

```
"Run linear static"                  → AnalysisBuilder(...).run_static_analysis()
"Run modal / eigenvalue"             → AnalysisBuilder(...).run_modal_analysis()
"Run response spectrum (CQC)"        → AnalysisBuilder(...).run_response_spectrum_analysis()
"Run pushover"                       → AnalysisBuilder(...).run_pushover_analysis()
"Run 4-direction pushover"           → from fea_toolkit.opensees import run_pushover_4dir
"Compute seismic masses (from units)" → from fea_toolkit.utils import g_from_units; builder.compute_seismic_masses(g=g_from_units(md.units))
"Extract element RS forces"          → builder.extract_element_rs_forces()

Returned dict keys for static:
  nodal_displacements: Dict[node_tag, (dx, dy, dz, rx, ry, rz)]
  load_totals:         Dict[pattern_name, {fx, fy, fz, mx, my, mz}]
  summed_reactions:    {fx, fy, fz, mx, my, mz} (if extract_reactions=True)
  element_forces:      Dict[elem_tag, [N, Vy, Vz, Mz, My, T]] (global)
  shell_forces:        Dict[elem_tag, {...}] (if shells present)

Returned dict keys for modal:
  periods:     List[float]
  frequencies: List[float]
  modal_data:  pd.DataFrame with mode, omega, T, mx, my, mz, sum_mx, ...
  mode_shapes: Dict[mode_idx, Dict[node_tag, (dx, dy, dz)]]

Returned dict keys for pushover:
  pushover_curves: Dict[direction, {disp: [...], force: [...]}]
  pushover_results: Dict[direction, raw_step_data]
```

### Post-Processing

```
"Compute storey displacements"       → from fea_toolkit.model import storey_displacements
"Compute storey drifts"              → from fea_toolkit.model import storey_drifts
"Compute storey shears"              → from fea_toolkit.model import storey_shears
"Compute modal storey drifts (CQC)"  → from fea_toolkit.model import modal_storey_drifts
"Capacity Spectrum Method (ADRS)"    → from fea_toolkit.model import pushover_to_adrs, compute_performance_point
"Mander confined concrete"           → from fea_toolkit.model import mander_confined
```

### Checks & Diagnostics

```
"Check model connectivity"          → from fea_toolkit.model import check_model_connectivity
"Check split connectivity"          → from fea_toolkit.model import check_model_connectivity
  (pass after-split model)
"Check mesh connectivity"           → check with after-mesh model
"Check self-weight consistency"     → from fea_toolkit.model import check_self_weight_consistency
"Check brace buckling (Euler)"      → from fea_toolkit.model import check_brace_buckling
"Singularity diagnosis"             → builder.diagnose_singularity()
```

### Visualisation

```
"Plot 3D model (undisplaced)"       → plot_model_3d(builder)
"Plot deformed shape"               → plot_deformed_3d(builder, results, scale=100)
"Plot mode shape animation"         → plot_mode_animation(builder, modal_result)
"Plot force/moment diagrams"         → plot_force_diagram_3d(builder, results, quantity='Mz')
"Plot pushover capacity curves"     → plot_pushover_curve(results)
"Plot capacity spectrum (ADRS)"     → plot_capacity_spectrum(adrs_result)
"Plot storey forces"                → from fea_toolkit.plotting import plot_storey_forces
"Interactive browser viewer"        → plot_interactive_viewer(builder, results)
"Export 3D scene to HTML"           → ModelViewer(...).export_html(path)
"Compare two meshes"                → compare_meshes(mesh1, mesh2)
"Plot model from NPZ"               → from fea_toolkit.io import npz_to_pyvista_frame_mesh
"NPZ force diagram"                 → from fea_toolkit.plotting import plot_npz_force_diagram
```

### Export

```
"Export results to NPZ"             → from fea_toolkit.io import write_results_npz
"Export to HDF5"                    → from fea_toolkit.io import write_results
"Export model to Tcl script"        → from fea_toolkit.opensees import export_mesh_model_to_tcl
"Export to Rhino 8"                 → from fea_toolkit.rhino import RhinoImporter
"Colour Rhino from NPZ"             → from fea_toolkit.rhino import colour_from_npz
```

### Reporting

```
"Generate a full report"            → from fea_toolkit import generate_report
"Modal summary table"               → from fea_toolkit.io import modal_table
"Section summary"                   → from fea_toolkit.io import section_summary
"Load pattern totals"               → from fea_toolkit.io import load_pattern_totals
"Brace buckling table"              → from fea_toolkit.io import brace_buckling_check
```

---

## 3. Visualisation Dispatch Rules

| Input type | What to call |
|------------|-------------|
| `AnalysisBuilder` instance + results dict | `plot_model_3d(builder)`, `plot_deformed_3d(builder, results)` |
| NPZ file path | `read_results_npz(path)` → data dict → `npz_to_pyvista_frame_mesh(data)`, `plot_npz_force_diagram(data, ...)` |
| Results dict only (no builder) | Use NPZ path as intermediate — write to NPZ, then read back for plotting |
| `MeshModel` + `SAPModelData` | `plot_model_3d(builder)` (builder wraps both) |

All plot functions gracefully degrade to a warning if PyVista is not installed.

---

## 4. Known OpenSeesPy Constraints

### Broken features
- **8-argument trapezoidal `eleLoad`**: broken in OpenSeesPy 3.8.0.0.
  Toolkit decomposes non-uniform loads into 4 partial-span uniform segments.
- **`Corotational` geomTransf + `eleLoad`**: does not work in 3D. Warning emitted.

### Recommended brace modelling
- **Static pushover**: `Truss` + `Hysteretic` (Approach B) — robust, asymmetric.
- **Dynamic**: `Truss` + `Hysteretic` + `Fatigue` — wrap with `brace_fatigue=True`.
- **Do NOT use** Approach A (subdivided beam-column) — gravity convergence fails.

### Element type → load support

| Element | Uniform load | Partial uniform | Trapezoidal |
|---------|-------------|----------------|-------------|
| `elasticBeamColumn` ✅ | ✅ | ✅ | ❌ (decomposed) |
| `forceBeamColumn` ✅ | ✅ | ✅ | ❌ (decomposed) |
| `dispBeamColumn` ✅ | ✅ | ✅ | ❌ (decomposed) |
| `nonlinearBeamColumn` ✅ | ✅ | ✅ | ❌ (decomposed) |

### Other constraints
- Always derive `g` from units via `fea_toolkit.utils.g_from_units(units)`.
  Never hardcode `9.81`.
- Type stubs for OpenSeesPy live in `typings/openseespy/opensees/__init__.pyi`.
  Pylance must have `python.analysis.stubPath` pointed there.

### Solver convergence (RC models with LayeredShell walls)

RC models with LayeredShell walls (resolved via the Preprocessor's
``shell_layers`` config) can fail the **gravity stage** with two distinct
OpenSees error signatures:

| Symptom | Root cause | Practical fix |
|---|---|---|
| `Norm ≈ 1.000e-4` vs tol `1e-4` — repeated "analyze failed" | Near-miss tolerance: the residual sits just above `solver_test_tol` due to floating-point noise at the `LayeredShell` stiffness floor | Relax `solver_test_tol` to `2e-4` |
| `Norm = NaN` during the gravity step | Sudden LayeredShell stiffness shock when shells activate in a single `LoadControl` step | Ramp gravity with `gravity_num_substeps: 10` |

**Theory**

The LayeredShell elements used for nonlinear RC walls (via
``nd_materials``) have a much lower stiffness at zero load than
elastic elements.  When the full gravity combination (dead + live) is
applied in a single ``LoadControl(1.0)`` step, the strain jumps from 0
to the full service level in one increment — the tangent stiffness
evaluation at the unloaded state produces a near-singular system and
the Newton iteration diverges to NaN.  Substepping (``gravity_num_substeps``
> 1) replaces the single big jump with a sequence of small, stable
increments that each converge on the first Newton iteration.

Separately, the solver convergence test compares the residual norm
against an absolute tolerance.  For full-building models with kN-m
units, a residual of exactly 1.000e-4 can sit marginally *above* a
tolerance of 1e-4 due to round-off — OpenSees reports this as a
failure even though the state is physically converged.  Relaxing the
tolerance by a factor of 2 eliminates these near-miss failures without
affecting accuracy.

**Practical points (LLMs: follow these rules)**

1. **Always** include ``"solver_test_tol": 2e-4`` and
   ``"solver_test_max_iter": 1000`` in the config for RC + LayeredShell
   pushover runs.
2. **Do NOT** set ``gravity_num_substeps`` manually for LayeredShell
   models — the ``AnalysisBuilder`` **auto-detects** the layered shell
   sections and sets ``gravity_num_substeps = 10`` automatically
   (``AnalysisBuilder.LAYERED_SHELL_GRAVITY_SUBSTEPS``).  An explicit
   config value always wins.
3. **Verify** the run log shows zero failures:
   `grep -c "analyze failed" run.log  # → 0`.
4. If a run still fails after these settings, the builder's built-in
   fallback chain (NormUnbalance + ModifiedNewton + adaptive
   substepping) automatically kicks in — no manual intervention needed.
5. Use the ``venv_opensees`` Python environment for these models — the
   stock interpreter produced NaN results in the LayeredShell gravity
   stage.

Full theory and worked example: `docs/pushover_analysis.md`
→ *Gravity convergence (LayeredShell RC models)*.

---

## 5. Selection Patterns (for targeted visualisation)

### Basic filtering
```python
from fea_toolkit import Selection

# Combine criteria: AND across fields, OR within lists
sel = Selection(
    element_types=['Frame'],
    sections=['IPE 300', 'IPE 400'],
    groups=['Moment Frame'],
)
frame_ids = sel.get_frame_ids(model)
```

### Create a standalone subset model for plotting
```python
subset = sel.filter_model(model)
# subset now contains only selected elements + their dependencies
# Can be parsed into a new AnalysisBuilder or exported
```

### Typical use cases
- Show only lateral system: `Selection(groups=['Lateral'])`
- Show only braces: `Selection.from_brace_sections(model)`
- Show only areas: `Selection(element_types=['Area'])`

---

## 6. Architecture Rules (Do NOT Violate)

| Rule | Why |
|------|-----|
| Never import `ops` in `model/` subpackage | Model code must be OpenSees-free |
| Never mutate `MeshModel` from `AnalysisBuilder` | Builder reads frozen topology only |
| Never put analysis logic in `builder.py` (Tcl export only) | Legacy; use `analysis_builder.py` |
| Never use 8-arg trapezoidal `eleLoad` | Broken in OpenSeesPy |
| Never hardcode `9.81` for gravity | Must derive from model units |
| Always call `ops.wipe()` in test teardown | OpenSees global state persists |
| Use `field(default_factory=...)` for mutable defaults in dataclasses | Prevents shared mutable state |
| Use `from typing import ...` for type annotations | Python 3.9 compatibility |

---

## 7. Quick Debugging

### ImportError
```python
# If fea_toolkit is not installed via pip:
import sys; sys.path.insert(0, "src")
from fea_toolkit import SAP2000Parser  # now works from repo root
```

### Document missing
```
# docs/llm_guide.md  ← you are here
# docs/README.md     ← full documentation index with tags
# examples/README.md ← example scripts index
```

### OpenSeesPy not found
```python
from fea_toolkit import ops_version
print(ops_version())
# Returns e.g. "3.8.0.0"
```

### Plotting fails silently
```python
# Install optional deps:
pip install pyvista matplotlib
```

---

## 8. TODO — Add usage examples to function docstrings

The following modules have Google-style docstrings with ``Args:`` and
``Returns:`` but **no usage examples**.  Adding a ``Examples:`` block to
key public functions would dramatically improve LLM output quality
(pattern-matching from docstring examples is one of the most reliable
ways LLMs produce correct code).

Priority order for adding docstring examples:

1. ``src/fea_toolkit/opensees/analysis_builder.py`` — all ``run_*()`` methods
2. ``src/fea_toolkit/plotting/viz.py`` — all ``plot_*()`` functions
3. ``src/fea_toolkit/model/storey_response.py`` — storey-level methods
4. ``src/fea_toolkit/io/npz_writer.py`` — ``write_results_npz()``
5. ``src/fea_toolkit/model/selection.py`` — ``filter_model()``, ``from_brace_sections()``
6. ``src/fea_toolkit/model/geometry.py`` — ``split_elements()``, ``mesh_area_elements()``

Example format to follow (from existing docstrings):

```python
def plot_model_3d(builder, ...):
    \"\"\"Plot the structural model in 3D.

    Args:
        builder: ...

    Examples:
        >>> from fea_toolkit import AnalysisBuilder, plot_model_3d
        >>> builder = AnalysisBuilder(mesh, {}).build_domain()
        >>> plot_model_3d(builder)
    \"\"\"