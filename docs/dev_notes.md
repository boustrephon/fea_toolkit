---
title: "Development Notes"
description: "Miscellaneous development notes, design decisions, and technical context."
status: "draft"
tags: [architecture, development, notes]
category: [planning]
related: [analysis_builder_migration_plan.md]
---
# fea_toolkit development notes



## CSM (Capacity Spectrum Method)
- `pushover_to_adrs()`: Converts pushover curve to ADRS format.
  - Uses best_mode (max participation in push direction) from modal_props.
  - Gamma = sqrt(M_eff) because extract_mode_shapes returns mass-normalized eigenvectors.
  - Uses abs() on base_shear/control_disp since OpenSees sign convention may give negatives.
- `compute_performance_point()`: Secant-iteration CSM per ATC-40.
  - Falls back to elastic spectral response when iteration drops below first data point.
  - Uses `np.trapezoid` (renamed from `np.trapz` in NumPy 2.0; `np.trapz` is deprecated and emits a warning).
- `plot_capacity_spectrum()` in viz.py for ADRS visualisation.

## Key patterns
- MassSource uses `elements`, `masses`, `loads` kwargs (not `from_element` etc).
- `modalProperties -unorm` returns partiFactorMX as 1.0 for single-DOF, but eigenvectors from nodeEigenvector are mass-normalized.
- Gamma computation: Γ = √M_eff for mass-normalized eigenvectors.

## Brace modelling
### Approach A (subdivided braces) — convergence failure
- Subdivided dispBeamColumn + PDelta/Corotational fails at gravity even with no imperfection
- 100 sub-steps, NormUnbalance, KrylovNewton — still fails
- Root cause: shared-node connectivity with subdivided elements creates ill-conditioned system matrix
- Bugs fixed: E/A swap in rigid link section, node creation on rebuild (_created_node_tags tracking), split_elements conflict

### Approach B (truss + Hysteretic) — recommended for static
- Working for static pushover with directional asymmetry
- For dynamic: upgrade material (Steel02 + Fatigue or BraceMaterial)

### OpenSees CBF brace modelling (from Workshops/OpenSeesDays/Steel2dModels)
- HSSbrace proc: subdivided forceBeamColumn/dispBeamColumn, fiber sections, Corotational, L/1000 imperfection
- Steel02 + Fatigue wrapper for cyclic analysis
- CBF1.tcl..CBF4.tcl: diagonal, X-brace, V-brace, inverted-V configurations

## Key bugs found & fixed in builder.py
1. E/A swapped in rigid link `ops.section('Elastic', ...)` — fixed
2. Node creation on rebuild: `ops.nodeCoord` doesn't raise in OpenSeesPy — fixed with `_created_node_tags`
3. Frame self-weight: `_create_loads` used `self.model.frame_assignments.get(eid)` when iterating `self.split_elements`. Child elements from splitting are tracked in `self.split_assignments`, not the original dict. Fixed by using `self.split_assignments.get(eid)` when `self.split_elements` is active. Symptom: 189 child elements (3510 kN) silently excluded from self-weight.

## Area import from JSON
- `SAP2000Parser.from_json()` loads SAP2000 JSON exports (same table structure as .S2K)
- `_get_area_elements()` now consolidates multi-row area connectivity (old SAP2000 format where one area's joints span multiple rows). Duplicate joint IDs are avoided.
- `_create_shell_elements()` in builder creates ShellMITC4 elements for areas not in the loads-only selection. Uses `ElasticMembranePlate` section with material properties.
- Config `create_shells=True` enables shell creation. Areas matching `selection` remain loads-only; all others become shells.

## Rhino module (`src/fea_toolkit/rhino/`)
- 6 files: `__init__.py`, `colors.py`, `layers.py`, `geometry.py`, `groups.py`, `importer.py`
- All Rhino API calls are lazy-imported — raises `RuntimeError` outside Rhino
- **Layer hierarchy**: `SAP2000/Joints`, `SAP2000/Frames/{Centreline,Extrusion}/{Section}`, `SAP2000/Shells/{Centreline,Extrusion}/{Section}`
- **Centreline**: points (joints), lines (frames), planar Breps (shells)
- **Extrusion**: lightweight `Extrusion` objects — section profiles (I/Box/Pipe/Channel/Rect/Circular) for frames, thickness offset for shells
- Objects added via `doc.Objects.AddExtrusion()` to stay lightweight
- **Metadata**: all objects carry `SAP_*` UserStrings for Grasshopper
- **Groups**: SAP2000 groups → Rhino groups; `SAP_All_Frames/Shells/Joints` via doc scan
- **Joint colour-coding**: Red (fixed), Blue (pinned), Green (roller), LightGray (free), Purple (constrained)
- **Usage**: `RhinoImporter(md).run(create_centreline=True, create_extrusions=True)`
- **Tests**: 23 tests in `tests/test_rhino.py` — colour conversion, layer name sanitisation, RuntimeError without Rhino API

## Xara OpenSeesRT (tclsh8.6) Compatibility
- **`nodalLoad`** exists OUTSIDE `pattern` blocks, but not inside — use `load` inside pattern blocks.
- **`pattern`** requires braced body: `pattern Plain $tag $tsTag { load ... }` — RecordingOpenSees flat output doesn't group.
- **`beamIntegration Lobatto`** needs explicit section tags per integration point: `beamIntegration Lobatto $tag 5 $s $s $s $s $s` (not the abbreviated `$tag 5 $s` form).
- **`UmfPack`** segfaults on large models → use `ProfileSPD` instead.
- **Area-only nodes** (not connected to frames) cause singular stiffness → filter them out (29 orphans in Project B building).
- **Query commands** (`nodeCoord`, `getNodeTags`, etc.) produce errors if nodes don't exist → skip in Tcl output.
- **Fiber sections**: `section Fiber`, `uniaxialMaterial Concrete01/Steel02`, `patch`, `layer ALL work`.
- **`ElasticMembranePlateSection`** NOT supported in Xara's OpenSeesRT.
- **Library**: auto-detected by `export_model_to_tcl()`; falls back to `"libOpenSeesRT.dylib"`. Override via `lib_path` argument or set `OPENSEESRT_LIB` environment variable.
- **Docs**: `docs/rhino_export.md` — quick start, layer structure, geometry types, metadata reference, joint colour coding
- **Frame member docs**: Added steel + planned RC section documentation to `docs/pushover_analysis.md`

## NPZ export (export_results_to_npz in builder.py)
- Stores metadata_json (JSON string with created timestamp, model stats, config, has_local_forces flag)
- Stores local force arrays: sub_fx_i_local, sub_fy_i_local, ..., sub_mx_j_local, etc.
- Stores element connectivity: sub_node_i_tag, sub_node_j_tag (OpenSees node tags)
- metadata_json key in NPZ arrays

## Standalone NPZ plotting (plotting/force_diagram.py)
- plot_force_diagram() — unified 2D/3D force/moment diagram from Builder, dict, or NPZ
- _load_npz_for_plotting() helper — loads NPZ, builds element-centric dict with coordinates and forces

## NPZ → Rhino colouring (rhino/colour_from_npz.py)
- colour_from_npz() matches SAP_FrameID UserStrings to NPZ sap_ids, colours by force quantity
- colour_frame_by_npz_ratio() colours by ratio of two quantities
- Runs inside Rhino CPython environment

## Force diagrams (plotting/force_diagram.py)
- plot_force_diagram renders flags or tubes for any M* or F* quantity
  (builder, AnalysisBuilder, or NPZ sources)
- Force flags use world-perpendicular direction (not local axes)

## PyVista widgets available (viz.py)
- Radio buttons (`add_radio_button_widget`) — mutually exclusive selection, good for switching result quantities (Mz/My/Fx etc.)
- Checkboxes (`add_checkbox_button_widget`) — toggle independent layers (undeformed, force diagram, labels, reactions)
- Sliders (`add_slider_widget`) — continuous params (disp scale, threshold)
- Text slider (`add_text_slider_widget`) — discrete text choices (mode shapes, load cases)
- Plane widget (`add_mesh_clip_plane` / `add_mesh_slice`) — interactive clipping/slicing
- Box widget (`add_mesh_clip_box`) — interactive box crop
- Spline slice (`add_mesh_slice_spline`) — slice along drawn polyline
- Picking: `enable_mesh_picking`, `enable_point_picking`, `enable_cell_picking`, `enable_element_picking`, `enable_surface_point_picking` — click to inspect element/node data
- Sphere widget (`add_sphere_widget`) — draggable control points
- Line widget (`add_line_widget`) — draw seed line (streamlines etc.)
- Measurement (`add_measurement_widget`) — interactive distance tool
- Animation timer (`add_timer_event`) — animate mode shapes, pushover
- Key events (`add_key_event`) — keyboard shortcuts
- Labels: `add_point_labels(..., always_visible=True)` — permanent overlay labels
- Export to interactive HTML: `export_html()`

## OpenSees RS element-force extraction — two strategies, measured

`responseSpectrumAnalysis` processes **all modes** unless `-mode n` restricts it
to one, and it calls every previously-defined recorder after each mode step
("When the i-th analysis step is complete, all previously defined recorders
will be called").  So there are two documented ways to get per-mode element
forces, and `extract_element_rs_forces` supports both via `extraction=`:

| Strategy | How | 1263 elems × 20 modes |
|---|---|---|
| `per_mode` (default) | one `responseSpectrumAnalysis -mode n` per mode + one `eleResponse` per element per mode | **0.09 – 0.47 s** |
| `recorder` | one all-modes pass behind an `Element` recorder, read from one file | 0.72 s (write 0.57, parse 0.15) |

Both are **bit-identical** (asserted in
`tests/test_workflows.py::TestElementRsForceExtraction`), so the choice is only
about call count.  `per_mode` wins on these models: 25 260 `eleResponse` calls
cost ~0.09 s (~7 µs each), while the recorder's 6.9 MB of 17-digit ASCII costs
0.57 s to *write*.  The recorder's advantage is call count, not wall time — it
only pays off if per-call overhead grows (e.g. a Python-free / remote API).

### Verified recorder contract (OpenSeesPy 3.8.0.0)

Do not re-derive these from the docs; they were pinned empirically.

* Response argument is **`localForce`** (singular) for the recorder, while
  `eleResponse` uses **`localForces`** (plural).  Minefield.
* `-precision` must be an **`int`** and must appear **before** the response
  argument.  A *string* raises `OpenSeesError`; a **trailing** `-precision` is
  **silently ignored** (6 digits still).
* Precision → max abs error vs `eleResponse` on a 3-element frame:
  `6` (default) → 3.4e-5, `14` → 3.3e-13, `16` → 2.8e-17, **`17` → 0.0
  (bit-identical)**.  Hence `_RS_RECORDER_PRECISION = 17`.
* The file holds **one line per mode** (row *i* ↔ mode *i+1*), **not** one line
  per element.  Each element's vector is written **contiguously** in `-ele`
  order, so the column count is `Σ length(elem)` — 12 for a beam-column, but
  **6** (3D truss) or **1** (axial truss) for others.  `localForce` widths must
  be probed with `eleResponse(tag, "localForces")` first, which is why
  `_rs_forces_recorder` does exactly that.
* With `-time` the leading column is domain time, which
  `responseSpectrumAnalysis` never advances — always `0.0`, no mode info.

### Binary recorder: DO NOT USE for RS

`-binary` is tempting — 2.4 MB and **0.029 s** to write (20× faster than
ASCII) — but it is **broken under `responseSpectrumAnalysis`**.  Verified on
the pipe rack (4 modes × 1263 elements, `[time, data]` per row, sizes exactly
`n × (12·n_elems + 1)`):

* **mode 1: bit-exact** (0 differing entries);
* **modes 2, 3, 4: uninitialized memory** — values like `-1.3e-152`, diffs up
  to `1.8e+308`, 15 149 / 15 149 / 15 150 bad entries.

So the fast path silently returns garbage — the worst possible failure mode.
Use the text recorder or `per_mode`.  Verify the layout with
`np.fromfile(path, dtype=np.float64)` if this is ever revisited.



The `modal/*` block was built by **two near-verbatim copies** of the same
collector, and they drifted:

| Collector | Reached by |
|---|---|
| `unified_writer.collect_modal_arrays` | `write_results` — the model-review export (`analysis_builder`, `_runner_static`, `stage_writer`) |
| `npz_writer._collect_modal` | `write_results_npz`, `stage_writer` |

Both mapped only `partiMassRatiosMX/MY/MZ` → `modal/{mx,my,mz}_ratio`, mirroring
the console modal table, which printed just `%X %Y %Z`.  OpenSees had always
returned the rotational trio as well (`partiMassRatiosRMX/RMY/RMZ`), so the
omission was in the **extraction map, not the data**: six-DOF participation was
missing from *every* archive, and the mode-shape annotation had no RX/RY/RZ row
to read.  Patching only `npz_writer` left the review path still writing three
columns — which is how the duplication surfaced.

`npz_writer._collect_modal` is now a **thin delegate** to
`unified_writer.collect_modal_arrays`, which owns the mapping;
`tests/test_stage_file.py::TestUnifiedWriterSchemaCoverage` pins the two in step.
If you add a modal key, add it to `collect_modal_arrays` only.

General lesson: when a "missing field" bug appears, grep for **every** writer of
that field before concluding the data is unavailable.  Here
`grep -rn 'partiMassRatiosMX' src/` would have found both copies immediately.

## PyVista animation timer — verified contract (`_add_animation_timer`)

The toolkit has twice shipped a wrong assumption about `add_timer_event`.
The facts below were read off the upstream source, not inferred:

| Fact | Evidence |
|---|---|
| Signature is `add_timer_event(max_steps, duration, callback)` — the interval keyword is **`duration`** | PyVista 0.43.1 API docs (method introduced in PR #4839); still `duration` on `main` |
| **`interval` was never a parameter name** in any release | same |
| The callback receives **exactly one** argument, `step` (`Timer.execute` is `self.callback(self.step)`) | `render_window_interactor.py` at v0.43, v0.44 and `main` |
| PyVista **renders each frame itself** — `Timer.execute` ends in `iren.GetRenderWindow().Render()` | PR #5618 |
| The project floors PyVista at `>=0.44`, two releases after the method was added | `pyproject.toml` |

Consequences:

- A callback registered through `add_timer_event` must **not** call
  `plotter.render()`.  Only the low-level VTK `AddObserver("TimerEvent")`
  fallback needs the callback to render, so `_add_animation_timer` returns
  `True` when PyVista took the timer (it renders) and `False` on the VTK
  path (the caller must render).  `plot_mode_animation` reads that flag.
- Toolkit callbacks are not uniform: `plot_mode_animation` declares
  `(step)` while `animate_pushover_deformation`'s `_timer_callback`
  declares `()`.  The adapter truncates the `step` PyVista always supplies
  so the zero-argument one does not raise `TypeError`.  That truncation is
  the adapter's only load-bearing rule; the "supply a missing step" and
  "pad with `None`" rules are defensive against behaviour no supported
  PyVista exhibits (see `TestAnimationTimerCallbackArity`).

**The bug this documents (fixed in `f1f092f`).**  `_add_animation_timer`
called `add_timer_event(max_steps=..., interval=..., callback=...)`.  Since
no release ever accepted `interval`, the call raised `TypeError` on *every*
version, the fallback chain swallowed it, and the non-rendering VTK
observer was used silently — so mode-shape geometry updated in memory but
the window only repainted when the user clicked or dragged.  Two lessons:

1. **Never invent a keyword name to satisfy a `TypeError`.**  A chain of
   speculative spellings hides the failure it is meant to absorb and lands
   on a degraded path instead.  Check the upstream signature first — the
   installed source, the version-specific docs, or the gallery example.
2. **A green test suite does not mean the API call was right.**  The arity
   tests used fakes that accepted any keyword, so they passed while the
   real call raised.  Asserting *which keyword was passed*
   (`test_modern_pyvista_uses_duration_kwarg`,
   `test_pyvista_signature_mismatch_falls_back_to_vtk`) is what catches
   this class of bug.

## Interactive viewer (plotting/interactive_viewer.py)
- `plot_interactive_viewer(builder, combo_forces, combo_results, ...)` — PyVista widget-driven viewer
- Radio buttons for quantity (Mz/My/Mx/Fz/Fy/Fx)
- Text slider for load combo selection
- Checkboxes: Centreline, Labels, Reactions
- Click element → info overlay with elem_tag, SAP ID, section, material
- Click flag → shows numeric value with unit
- Structure built as coloured tubes (section-colour mapped)
- Force flags use RdBu diverging colormap, merged PolyData with `col_val` + `elem_tag` point data
- Caches flag meshes per combo+quantity pair
- Import: `from fea_toolkit.plotting import plot_interactive_viewer`
- Strategy: use radio buttons to switch result quantity, checkboxes for overlay toggles, sliders for continuous params
- Callbacks can swap mesh by name (`add_mesh(new, name='actor')`), toggle visibility, or update coords in-place

## view_project_b_model.py — standalone model viewer (local/view_project_b_model.py)
- Created for visualising project_b.s2k model with Original/Meshed toggle
- **View toggle** (checkbox bottom-left): switches between unsplit wireframe and split+shell views
- **Label toggles** (3 checkboxes above view toggle): show/hide numeric tags for Nodes/Frames/Areas
- **Click-to-identify** (yellow dot at click position, info text at top-centre): intended to identify elements, but unreliable after camera rotation/zoom
- **Picking limitation**: VTK's mesh picking with `enable_mesh_picking` + `find_closest_cell` loses accuracy after orbit/zoom transforms. Custom centroid-distance search improved but still inconsistent. Root cause: VTK interactor `picked_point` doesn't reliably map to correct cell after perspective changes.
- **Shrink factors**: `AREA_SHRINK=0.9`, `FRAME_SHRINK=0.9` — elements shrunk toward centroid/midpoint for visual gaps at joints
- **Node spheres**: original (size 12), meshed (size 10), split nodes orange (size 20)
- **Terrain style**: `enable_terrain_style()` locks Z as vertical during rotation
- **Render order**: shells bottom → frames middle → nodes top
- **Materials coloured**: concrete (blue), brick (red)
- Text positioning with `add_text(..., position=tuple)` uses top-left origin on macOS (not bottom-left as documented)

## CLI — `python -m fea_toolkit` API listing (`src/fea_toolkit/__main__.py`)
- **Lazy by default.** Names come from `fea_toolkit.__all__`; lazy names
  (declared in `_LAZY_IMPORTS`) are classified by parsing module source with
  `ast`, so the default listing never imports `openseespy` or `pyvista`.
  `--source` stays lazy too — it prints the definition text from the module
  file (decorators included) without importing anything.
- **`--details` is the one exception** — it imports each name to report the
  exact `inspect.signature()` and the docstring's first line. Those imports can
  pull in optional backends (`openseespy`, `pandas`, ...), so a
  `ModuleNotFoundError` is caught **per row**: the failed import is reported
  inline (`import failed: No module named ...`) and the loop continues.  This
  is deliberate — one absent optional dependency must not hide the rest of the
  public API, which keeps `--details` usable as an inventory on a partial
  install.
- Only `ModuleNotFoundError` is caught, not `AttributeError`: every row's name
  comes from `__all__`, so the only realistic failure mode is a missing module
  behind a lazy re-export, never an unknown attribute.
- `NAME` filtering (exact / substring / glob) is case-insensitive and applies
  to every mode; `--source` requires a `NAME`.
