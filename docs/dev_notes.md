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
- **Bilinearizers assume non-negative `S_a`** — `bilinearize_*()` document
  non-negative ordinates and do not filter negatives; the caller
  (`compute_performance_point()`) folds -X/-Y pushes with `np.abs()` and masks
  ordinates below `-1e-12` before dispatch.  Measured 2026-09-17: with
  `S_a[3] = -5.0` passed raw, `bilinearize_stiffness_change()` adopts the
  negative sample verbatim as the yield point (`S_ay = -5.0`) while
  equal-energy / rc / composite stay positive by accident — inconsistent, not
  an error.  **Revisit tracked as P17 in `docs/_pending_work.md`**; guard test:
  `tests/test_csm.py::TestBilinearization::test_noisy_curve_with_negative_sa`.
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
the benchmark model (4 modes × 1263 elements, `[time, data]` per row, sizes exactly
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

## Reading results out of OpenSees — the query API, and the recorder escape hatch

Results are pulled into Python through OpenSees' **query API** — `ops.nodeDisp`,
`ops.nodeReaction`, `ops.eleResponse`, `ops.nodeEigenvector` — and the toolkit
then writes them itself (numpy → NPZ/H5).  No OpenSees `recorder` streams results
to a persistent file anywhere in the analysis path:

| Runner | Read with | Where |
|---|---|---|
| Static | `nodeDisp`, `nodeReaction`, `eleResponse(tag, "localForces")` | `_runner_static.py` |
| Pushover | `nodeDisp` (control node), `nodeReaction` (base), `eleResponse(tag, "section", …)` | `_runner_pushover.py` |
| Modal | `nodeEigenvector`, read immediately after `eigen` | `analysis_builder.py` |
| RS | `eleResponse` per mode per element, `nodeEigenvector`; **plus one `Element` recorder** | `_runner_rs.py` |

The single exception is RS element forces, whose `extraction="recorder"` mode is
documented and *measured* in the section above: there the per-element
`eleResponse` calls were the thing being optimised, and on the benchmark model the
query API still **won on wall time** — the recorder's advantage is *call count*,
not speed.  So the recorder path is not an automatic upgrade; it is a contingency.

Two other things called "recorder" here are unrelated to results.
`opensees/recorder.py::RecordingOpenSees` captures the **model-building** calls
for Tcl/Python replay.  The Xara/Tcl export path reads results from the
subprocess's own output files (`XaraTclRunner.read_recorder`) because a
subprocess has no in-memory API to query.

### Why the door stays open

The query API is the right default: numpy-ready values, no parsing, and exactly
what the results seam expects.  Its cost is **per-element, per-step Python↔C
round trips**, plus the **accumulation** of the returned values in Python
containers before the toolkit writes them.  That is fine at the scale the toolkit
runs (a pushover of a 500-frame building spends seconds, not minutes, in
extraction), and it is the wrong shape for two cases:

- **Very large element counts** — extraction is linear in elements × steps, and
  the round-trip overhead dominates once the solver gets fast.
- **Time-history with many steps** — per-step element forces held in a Python
  list are O(steps × elements) resident, while an OpenSees recorder writes each
  step *incrementally during* the analysis and keeps nothing resident.

### The constraint on any file-recorder ingest (decided 2026-09-28)

A recorder-based path may be added later, but it must be a **second producer for
the existing seam — never a second seam**:

1. It normalises into the **same per-case payload dict** the query API produces —
   `{case: {"nodal_displacements": {node_id: [...]}, "element_forces": {elem_id: {...}}}}`
   — plus the geometry arrays and the case labels/metadata that travel with an
   archive.
2. It reaches consumers **only** through `ResultsRepository` (in memory as
   `NpzResultsRepository(dict)`, or via the writers to NPZ/H5), so no consumer
   ever learns where a result came from.
3. It lives in `opensees/` (a runner concern) — not in `io/`, `plotting/` or
   `gui/`.  Readers may import it; nothing else may parse a recorder file.

Holding to those three means the GUI, the plotters and the reports need **no
change** when the ingest path is added: the escape hatch stays an implementation
detail behind the seam.  Tracked as **P26** in `docs/_pending_work.md`, with the
trigger for actually doing it.

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

## Optional dependencies in tests — the pandas policy

`pandas` is **not** a core dependency: it is the optional ``[report]`` extra
in `pyproject.toml` and is absent from Rhino 8's bundled CPython.  The policy
therefore splits in two — a *library* rule and a *test* rule.

**Library side — never import pandas eagerly.**  Every pandas-using module
(`io/report.py`, `model/storey_response.py`, `analysis/linear.py`) wraps the
import:

```python
try:
    import pandas as pd
except ImportError:  # pragma: no cover — pandas is optional (Rhino 8 CPython)

    class _MissingPandas:
        "Raise a clear error when a helper needs pandas."

        def __getattr__(self, _name: str):
            raise RuntimeError(
                "... require pandas, which is not installed in this Python "
                "environment (pip install pandas)."
            )

    pd = _MissingPandas()  # type: ignore[assignment]
```

The sentinel's docstring and message text vary per module (each names the
helper family it backs); the parts that matter are the guard, the
`_MissingPandas` name and the `# type: ignore[assignment]`.

Consequences to preserve:

* `import fea_toolkit` and every subpackage import work without pandas — only
  *calling* a pandas-backed helper raises, and the message carries the pip
  hint.
* pandas-backed public names stay behind the PEP 562 lazy re-exports
  (`fea_toolkit/__init__.py`, `io/__init__.py`, `model/__init__.py`), so a
  missing pandas never breaks `__all__` or `python -m fea_toolkit`.
* A new pandas-using module copies the pattern above; a bare module-scope
  `import pandas as pd` under `src/` is not allowed.

**Test side — two sanctioned forms, chosen by collection environment.**

| Collection environment | Form |
|---|---|
| The normal suite — CI installs `pip install -e ".[report,mesh-remesh]"`, so pandas is always present. | Plain module-level `import pandas as pd` beside `import numpy as np`, e.g. `tests/test_storey_response.py`. |
| A module that must *also* collect on a minimal `pip install -e .` (no extras). | `pd = pytest.importorskip("pandas")` at the point of use — the same idiom as `h5py`, `scipy`, `rhino3dm` and `IPython`. |

Not sanctioned in a test body: a bare function-local `import pandas` with no
skip guard, or `pd = __import__("pandas")`.  Six `__import__` calls were
removed from `tests/test_storey_response.py` on 2026-09-17 — five of `numpy`
(four tests shadowed the already-present module-level `import numpy as np` with
`np = __import__("numpy")`, harmless but invisible to isort/lint and to a
reader scanning the import block, plus one dead discarded call in
`test_basic_two_storey_drift`) and the unguarded pandas equivalent.  The
module-level `import pandas as pd` makes all six redundant; because CI installs
the extra, a missing pandas in a *local* minimal environment now fails at
collection rather than at the first test that touches pandas.  If pandas-free
collection is ever needed, switch the module to `pytest.importorskip("pandas")`
(the second sanctioned form) — do not reinstate `__import__`.


## Results repository and the NumPy-typed seam

The GUI, the plotting layer and the Rhino export all consume results as a
**`dict[str, numpy.ndarray]`**.  That habit is now a contract, because it is the
one boundary that keeps a columnar future open without committing to it today:

- NumPy is the right backbone for the *compute* tier — OpenSeesPy, PyVista and
  Matplotlib all speak `np.ndarray`, and results are dense float blocks, which is
  NumPy's home turf.
- Arrow and the tools built on it (pyarrow, Polars, DuckDB) live one tier up: a
  columnar data model for tabular/analytical work plus zero-copy interchange.
  They are **not** NumPy replacements, and they all hand back NumPy arrays
  cheaply — so a NumPy-typed boundary is what lets a Parquet/DuckDB backend drop
  in later, rather than what prevents it.

The seam is two small interfaces, both Qt-free, OpenSees-free and unit-tested
without extras:

| Object | Module | Role |
|---|---|---|
| `ModelHeader`, `model_header()`, `ModelStore`, `InMemoryModelStore` | `io/model_store.py` | model topology plus a counts-only header, so a view never walks (or copies) a graph |
| `ResultsRepository`, `NpzResultsRepository` | `io/results_repository.py` | cases, per-case metadata and per-case arrays, whatever the backing format |

### The four rules

1. **Results are read through the seam** — `io/npz_reader.py` or
   `io/results_repository.py`, never `np.load` in a consumer.
2. **One dtype policy for dense blocks.**  The writer emits `float64`
   (`dtype=float` throughout `unified_writer.py`).  Moving to `float32` is a
   *schema-level* decision (version bump plus round-trip tests), not a local
   optimisation — and never mix dtypes ad hoc.
3. **Case metadata is explicit columns** — `CASE_META_KEYS`
   (`static_case_group` / `_family` / `_coords`), not structure encoded into
   array names.  A columnar format maps onto columns, not onto `static/DEAD/fx_i`.
4. **Backends stay optional and lazy.**  The interfaces import `numpy` only;
   pyarrow / Polars / DuckDB / h5py come in *behind* them, inside a method,
   behind a `_Missing…`-style guard — exactly as pandas and h5py are handled
   today (see *Optional dependencies in tests* above).

`tests/test_storage_seam.py` enforces 1 and 4 by parsing `src/` with `ast` (so a
docstring that merely mentions `np.load()` does not count): `np.load` may appear
only in the readers, `np.savez*` only in the writers, and neither interface may
import anything heavier than `numpy`.

**Retired exception (2026-09-24, Slice C).**  `rhino/colour_from_npz.py` used to
call ``np.load(..., allow_pickle=True)`` to sniff whether a path was a unified
archive before delegating.  That decision now lives in
``npz_reader.is_results_archive(path)`` — a **header** test that reads the key
list and never an array — so the allow-list above is down to the two readers and
nothing else.

### What this buys — and what it does not

- It buys, as drop-ins with no consumer edits: an HDF5-backed `ModelStore` (lazy
  materialisation, header counts from array shapes, eviction on view switch), and
  a Parquet/Feather/DuckDB `ResultsRepository` for cross-case analytics (today
  pandas, in `analysis/linear.py`).
- It does **not** solve model-topology memory.  A million-element model is a
  million Python dataclasses; that is a *materialisation* problem, fixed by the
  store plus lazy loading, not by a different array format.

### The concrete accessors, and the invariants they encode

A backend answers `cases()` / `arrays_for()` / `display_geometry()` /
`metadata()`; two accessors every backend then **inherits** turn that into the
shape a view consumes — `nodal_displacements(case)` for a deformed shape and
`element_forces(case)` for a flag diagram.  Writing them once on the ABC is what
stops the GUI, the plotters and the Rhino export each re-deriving the same join,
and it puts three invariants in one place:

- **The ids are the display model's.**  `as_model()` names nodes `node_sap_id`
  (falling back to `node_tag`) and frames `frame_sap_id` (falling back to the
  `frame_eid` label); the accessors key the same way, taking the same fallback
  as the model build — so a viewer keyed by those ids finds every entry.
- **The join is positional.**  `static/<case>/mz_i[i]` belongs to frame `i` — the
  index `frame_sap_id[i]` sits at — *not* to a tag, and never sorted by id.
  Position is what the writers guarantee, and both readers in
  `plotting/viz_forces.py` already assumed it; the accessor turns the shared
  assumption into a tested contract.
- **Absent is absent, never zero.**  A component the archive did not write, or
  wrote for fewer elements than it draws, is left out of the entry: a fabricated
  `0.0` draws a plausible zero-force diagram, which is the failure nobody spots.

The switch from "the arrays are global" to "these are `*_local` keys" happens
**at the seam** too, from the file-level `forces_coordinate_system` metadata:
when it says local, the bare values are mirrored under the `_local` keys
`overlay_forces(use_local=True)` reads first, so they are drawn verbatim instead
of rotated a second time.  That metadata is the reason the interface grew a
`metadata(name, default)` method — file-level arrays are not case-namespaced, so
`arrays_for()` cannot reach them.  `viz_forces.py` synthesises the same aliases
in two places of its own; those predate the accessor and can now be migrated onto
it rather than extended.

## Selection: two resolutions, deliberately different

`Selection` answers two different questions, and conflating them would be a bug:

| Method | Question | Used by |
|---|---|---|
| `get_frame_ids` / `get_area_ids` / `get_node_ids` | *which entities match this criterion, exactly* | analysis — `resolve_to_mesh_sets` (pushover recording), `filter_model` (subset builds), `from_brace_sections` |
| `resolve_connected` | *what should a view draw* | the GUI's views, via `ModelViewer(selection=...)` |

`resolve_connected` differs in two ways, both deliberate:

- A criterion that opts into nodes (`element_types` names ``Node``, or
  `constraints` is set) **expands one hop**: the members framing into the
  selected joints join the result, and their far ends join the node set so an
  attached member draws complete rather than as a dangling stub.  A joint on a
  three-member chain shows that joint and the one member incident on it, never
  the chain.
- The node set is otherwise the **joints of the shown elements**, not every node
  the element criteria ignore — nodes match a `section=` criterion trivially, so
  the element-resolution node set would drag the whole cloud in.

The expansion tests against a **frozen seed set**, which is what keeps it one hop
whatever order the elements happen to be stored in: testing membership against
the growing set would silently walk the whole structure, order-dependently.

`Selection.to_string()` is the exact inverse of `from_string()`, which is what
lets the Edit-view dialog pre-fill an expression and re-parse it losslessly (the
`story=` key was added so the expression grammar covers every field).

## The macOS GUI segfault — pyvistaqt threads every render

`MainWindow` embeds a `pyvistaqt.QtInteractor`, and **on macOS only** pyvistaqt
wraps `QtInteractor.render` in a `threading.Thread`
(`pyvistaqt/plotting.py`):

```python
@conditional_decorator(threaded, platform.system() == "Darwin")
def render(self) -> None: ...
```

The thread exists to `emit()` `render_signal` from another thread, which Qt then
delivers to the GUI thread — and the render itself already runs *on the Qt
thread*, from that signal.  Linux and Windows create no thread at all, so macOS
was paying one thread per render for a deferred emit.

It is not free.  Creating a thread while the **cyclic collector** is running
segfaults this process: shiboken/VTK objects are not safe to traverse from
another thread, nor during thread bootstrap.  Measured, not inferred:

- the crash stack ends in `threading._bootstrap` → `_maintain_shutdown_locks` →
  *Garbage-collecting*, with the `threaded` wrapper (`pyvista
  core/utilities/misc.py`) → `Thread.start`, called from `reset_camera` →
  `self.parent.render()`;
- on the affected machine `platform.system() == "Darwin"` **and**
  `QtInteractor.render.__name__ == "wrapper"` — the two facts that identify the
  decorator (the wrapper is not ``functools.wraps``-decorated, so it keeps that
  name);
- three consecutive GUI-suite runs aborted with exit 139 and no summary before
  the fix; the same suite ran clean afterwards.

`gui/render_backend.py::MainThreadQtInteractor` is the fix: it emits
`render_signal` on the calling thread — exactly what pyvistaqt does on Linux — so
no thread is created.  Rendering here is only ever driven from the GUI thread, in
response to user actions, so the deferred emit is not needed.
`tests/test_gui_app.py::test_the_embedded_interactor_renders_without_a_thread`
pins the contract, and fails loudly if upstream drops its workaround (so the
subclass can then be deleted rather than kept on faith).

**CI cannot catch this**: every job, the GUI one included, runs
`ubuntu-latest`, where the decorator is never applied.

### Validated on the real backend (2026-09-24)

The offscreen suite cannot make this claim: under `QT_QPA_PLATFORM=offscreen`
there is **no GL context**, so `ViewportInteraction.install()` degrades to
"picking unavailable" and the crash was never exercised on the platform it
actually happened on.  A driver (`local/gui_real_run.py`, private — it needs a
display) runs the same `MainWindow` on **cocoa**:

```bash
unset QT_QPA_PLATFORM
PYTHONPATH=<repo> python local/gui_real_run.py 4     # arg = rounds
```

Each round triggers a camera action and both display toggles (twice each, so the
checkable state returns to start), runs `model.split` and `model.mesh` awaiting
`window._worker`, switches **every** registered view, selects a tree row, and
clicks the viewport three times with real Qt events — `QTest.mouseClick` → the
`qt_mouse` filter → the live picker, i.e. the whole chain, unstubbed.

Result — **six runs (one 6-round, then five 4-round), every one exit 0**, no
signal and no traceback:

| evidence | value |
|---|---|
| Qt platform | `cocoa` — the platform that crashed |
| viewport | 613×430 logical @ dpr 2.0 → render window 1226×860 |
| views live per round | `Unprocessed`, `Processed`, `Meshed` |
| picks that selected a tree row | **2 per 4-round run** (`Selected N in the tree from the viewport.`) |

Two findings worth keeping:

* **Picking is live on the real backend — and only there.**  A 25-point probe
  grid (`local/gui_pick_probe.py`) hits 5 points, tracing the sample model's
  single thin column.  So a near-zero hit count says something about the
  *model*, not about the conversion — do not read it as a wiring fault.
* **`highlight changed` is a weak signal** for the same reason: a click on
  nothing *clears* the selection, which changes the highlight actors too.  Count
  the log's "from the viewport" line instead when re-running this.

The coordinate conversion is sound on Retina, which is worth stating because it
is the one thing a headless run cannot check: `to_device` maps the widget centre
(306, 215) to (613, 430) inside a 1226×860 render window — exactly its centre.

### The mechanism, finally fixed (2026-09-24)

Adding the Milestone 7 results overlay made this crash reproducible again, and
the attribution came out cleanly — same eleven-file GUI set, three states:

| state | runs | exit codes |
|---|---|---|
| `HEAD`, before M7 | 3 | 0, 0, 0 |
| M7 overlay, no worker fix | 2 | **139, 139** |
| M7 + the worker fix | 4 | 0, 0, 0, 0 |

Small samples, but consistent: the overlay work creates and destroys VTK actors,
which is *more* collectable Qt/VTK state, and that is what the race needs.

**The fix.**  `TaskWorker.run()` now holds the **cyclic collector off for the
task's duration** (`gc.disable()`, restored in a `finally`), and
`MainWindow._preprocess_ended` — wired to the worker's `finished`, which fires on
both outcomes — collects on the GUI thread once it has stopped.

Why this is the mechanism rather than another mitigation: the crash needs a
collection to *run on the worker thread* while shiboken objects are alive.
`gc.freeze()` made everything that existed at the first freeze immune, but every
window, actor and model created **afterwards** stayed collectable — so more GUI
churn meant more chances for a worker-thread collection to walk one. Disabling
the collector removes the *collection*, not the object: reference counting still
frees as usual, so only cycles wait, and they are reclaimed where traversal is
safe.

Pinned by `tests/test_gui_worker.py::test_the_collector_is_held_off_while_the_task_runs`
and `::test_a_failing_task_still_restores_the_collector` — a worker that left the
collector off would be a silent leak, so the error path is pinned too.

**`gc.freeze()` stays.**  The two are complementary: the freeze protects the
objects that predate the first task, the disable protects each task's window.
Neither suffices alone, and the rule for future work is simply: **never run a
collection on a worker thread** — no `gc.collect()` in a task, no leaving the
collector on around one.

**Still open.**  Long interactive sessions were not re-measured on the real
backend beyond two driven runs; if the crash is ever seen again, the first thing
to check is whether something new is allocating Qt/VTK objects *inside* a task.



Related: the Preprocessor's `copy.deepcopy` ran on the GUI's worker thread and
hit the same race.  `main_window._freeze_gc_once()` (a `gc.freeze()` before the
handoff) is the stop-gap there; the deepcopy itself has since been replaced by
copy-on-write — see the next section.

## Copy-on-write replaces the model deepcopy (Preprocessor)

`Preprocessor.run()` has always promised not to mutate the caller's
`SAPModelData`.  It delivered that promise with `copy.deepcopy(model_data)`,
which was both the peak-memory spike (original + full copy + `MeshModel` all
alive at once) and the allocation burst behind the worker-thread GC crash
above.  It was also almost entirely wasted work: the copy changed a handful of
objects.

The fence is now `_copy_for_preprocessing()`, which copies the **containers**
(new dicts holding the *same* object references) and relies on every mutation
site being copy-on-write.  Untouched nodes and elements are shared with the
source model rather than duplicated.

### The contract

*   **Helpers may extend the dicts they are handed** — `split_elements()` adds
    split nodes to `nodes`, the mesh helpers add children to `area_elements` —
    but they must never write to the **objects** inside them.  A parent that is
    split, meshed or offset is *replaced* (`dataclasses.replace(...)`) and the
    replacement written back into the dict under the same key.
*   `dataclasses.replace` is **shallow**: a field that will subsequently be
    appended to must be passed a *fresh* list at the replacement
    (`child_ids=[]`, `child_ids=list(elem.child_ids)`).  Otherwise the copy
    aliases the caller's list and every append leaks through.
*   Three collections are copied one level deeper, because something mutates
    the objects they hold: `materials` (scalar fields only, filled by
    `apply_material_defaults`), `groups` (meshing appends `"Area:<child>"` to
    `Group.objects`), and the load-object lists.  `restraints`, `sections` and
    the remaining load collections need only the container copy — the pipeline
    adds new entries (restraints for mesh nodes, per-type section variants) but
    never rewrites an existing restraint or section.
*   **Consequence: the `MeshModel` shares element objects with the source
    `SAPModelData`.**  Both are read-only to their consumers, which is what
    makes the sharing safe — the `AnalysisBuilder` never mutates the
    `MeshModel` (frozen-topology rule), and views are lenses.  The one builder
    path that *does* need to mutate (`subdivide_elements` for brace buckling)
    already deep-copies the dicts it works on into `_brace_canonical`.

### What it buys — and what it does not

*   Peak memory drops by roughly one whole model graph.  Most real models split
    little or not at all, so the deepcopy was near-100 % waste.
*   The worker thread no longer allocates a second copy of the model.  It still
    allocates new children and mesh nodes, so `gc.freeze()`
    (`main_window._freeze_gc_once()`) **stays** as the safety net: this removes
    the largest allocation burst, not the whole class of race.  Do not retire
    the freeze on the strength of this change.

### Verified by

`tests/test_preprocessor.py`: snapshot-equality of the source model after
`run()` (frame splitting + end offsets, area meshing, group membership),
object-identity checks proving untouched nodes/elements are *shared*, and a
`copy.deepcopy` spy proving the model itself is never deep-copied.


## PySide6 item models - never call `internalPointer()`

Verified against PySide6 / Qt 6.11 on 2026-09-23, while building the GUI's
Model Tree:

* `QAbstractItemModel.createIndex(row, column, value)` stores `value` as the
  index's **internal id**; read it back with `QModelIndex.internalId()`.
* `QModelIndex.internalPointer()` **segfaults** for an index created from an
  integer (PySide6 resolves the integer as an address), and returns a
  **half-constructed instance** when `createIndex` was handed an arbitrary
  Python object - touching that instance's attributes crashes the interpreter.
  In practice this surfaced as `Fatal Python error: Segmentation fault` inside
  *pytest's traceback formatter*, which hid the real exception.

Consequence for this codebase: custom `QAbstractItemModel`s pass an **int id**
to `createIndex` and resolve it through a registry, reading it back with
`internalId()`.  See `src/fea_toolkit/gui/models/tree_model.py`.

## macOS application-menu label — not settable at runtime

**Verified 2026-09-23 on macOS 15 / PySide6 6.11.2 / PyObjC present** — five
Qt-name variants, `NSProcessInfo.processName`, and a symlinked interpreter
were each probed in a fresh process (window + `QMainWindow.menuBar()` shown,
then `NSApp.mainMenu()` read back).

`fea-gui`'s bold application menu showed **“Python”**.  Nothing reachable from
**Qt** changes it (AppKit, driven directly, does — see the update below):

| Probe | Result |
|---|---|
| `QCoreApplication.setApplicationName("FEA Toolkit")` — before *and* after `QApplication` construction | `applicationName()` returns the new value; menu label unchanged |
| `QGuiApplication.setApplicationDisplayName("FEA Toolkit")` | unchanged |
| `NSProcessInfo.processInfo().setProcessName_("FEA Toolkit")` | `processName` changes; menu label unchanged |
| launched through a symlink named `FEA Toolkit` | unchanged — `sys.executable` resolves to the framework binary |
| `NSApp.mainMenu()` after each variant | item 0 is the app menu; its **submenu title stayed `Python` in every variant** |

Qt *does* build that menu from `qt_mac_applicationName()`
(`qtbase/src/plugins/platforms/cocoa/qcocoamenuloader.mm`: `appItem.title =
appName`, and `About …` / `Hide …` / `Quit …` all interpolate it), but the
helper resolves through the **process bundle** — here the Python framework's
`CFBundleName` — which no runtime call can change.

**Consequence.** `fea_toolkit.gui.app.APP_NAME` (`"FEA Toolkit"`) drives the
window title, the About box and Qt's own naming — everything Qt controls, set
by `configure_application()` *before* `QApplication` exists.

**Update — the *items* can be retitled; the name cannot.**  Four mechanisms
were then driven directly (each probe read its results back; the last two
launched a real `.app` through LaunchServices):

| Mechanism | Result |
|---|---|
| `QAction.setMenuRole(AboutRole / QuitRole)` on the menubar, window shown | **no merge** in a non-frontmost process — the Application menu kept Qt's items, File/Help kept ours |
| `NSMenuItem.setTitle_(…)` on the Application-menu item, its submenu, and every `About` / `Hide` / `Quit` item | persists — `About Python` → `About FEA Toolkit` (and `Hide …` / `Quit …` likewise); the **bold label still painted `Python`** |
| `NSProcessInfo.processInfo().setProcessName_("FEA Toolkit")` | `processName` changes, but `NSRunningApplication.localizedName()` — the name macOS *displays* in the Dock, Cmd-Tab and menu bar — stays `Python` |
| a generated `.app` whose launcher `exec`s the interpreter | `NSBundle.mainBundle()` → `…/Python.framework/…/Resources/Python.app`, `CFBundleName = Python`; menu bar still `Python` |
| a generated `.app` with the interpreter **hardlinked into** `Contents/MacOS`, plus `pyvenv.cfg` and a site-packages `.pth` | the interpreter does run from inside the bundle (`sys.prefix` = the bundle, imports work) **but `mainBundle` is still `Python.app`** |

The displayed name is therefore pinned to the **framework bundle the
interpreter is built into** — `CFBundleGetMainBundle` resolves the framework's
`Resources/Python.app` — and it survives every runtime lever.  Only a
py2app/PyInstaller-style build (a compiled launcher that embeds or `dlopen`s
`libpython`, so the process image is the app's own binary) changes it.  That is
deliberately **not** implemented here: it is real build tooling, and §5.8
(OpenSeesPy is not redistributable commercially) rules out bundling OpenSeesPy
itself.  A bundle that ships **no** OpenSees code and drives a
separately-installed OpenSees as an external program is a different case and is
recorded as a future option — **P22** in `docs/_pending_work.md`, sketched in
`docs/gui_roadmap.md` §5, licence position in `docs/licence.md`.

So `rename_macos_application_menu()` (PyObjC, called from `main()` once the
window is shown, a quiet no-op elsewhere) buys the visible part —
`About` / `Hide` / `Quit` read *FEA Toolkit* — while the bold menu-bar label,
the Dock entry and the Cmd-Tab name say `Python`, and will keep saying it.

**Lesson.** Same shape as the PySide6 `internalPointer()` finding above: a
platform-owned string that *looks* settable through a Qt API which is not on
the path that actually produces it.  Probe the whole chain before writing the
fix into a docstring.

## PyVista picking contract (`enable_mesh_picking`) — verified

**Verified 2026-09-23 against pyvista 0.48.1**, by reading the installed source
(`pyvista/plotting/picking.py`) *and* by picking a real off-screen render
(`tests/test_picking.py`).  Viewport→tree selection rests on three facts, none
of which is visible in the method signature:

| Fact | Detail |
|---|---|
| The callback receives **one** argument | `enable_mesh_picking` wraps it as `_poked_context_callback(plotter, callback, component._picked_actor)` with `use_actor=True` (`..._picked_mesh` otherwise).  **No cell id is passed.** |
| The cell index lives on the scene picker | `enable_mesh_picking` does `self._plotter.iren.picker = picker` (a `vtkCellPicker`, tolerance 0.025); read it via `plotter.iren.picker.GetCellId()`. |
| The picked actor is identity-comparable | `picker.GetActor()` is the object `add_mesh` returned, so `PyVistaRenderer.category_of_actor()` resolves the batch with `is`.  Measured: a member's midpoint gives `cell=0` for the first frame; a joint position resolves to either the member's line cell or the node cloud's vertex cell. |

Consequences — and why ``enable_mesh_picking`` was replaced:

- Reading ``iren.picker`` worked, but the *gesture* could not be expressed: the
  pick fires on the raw press, so "a click selects, a drag orbits" is
  impossible, and a trackpad tap mid-orbit selects.  ``ViewportInteraction``
  (`gui/views/interactor.py`) now owns the picking, driven by a **Qt event
  filter** (`gui/views/qt_mouse.py`) rather than by VTK observers — see the
  "Mouse interaction" section below for why, and for the logical-versus-device
  pixel trap that comes with it.
- **The picking region was fat.**  ``vtkPicker``'s default tolerance is 0.025 of
  the viewport diagonal: measured on the off-screen render, a click **8 px** to
  the side of a member still selected it, and the region was fat enough to
  shadow every joint (the member is hit before the node marker).  The policy's
  calibrated default (0.010) selects at 6 px and misses at 30 px — asserted in
  ``tests/test_picking.py``.
- **Nodes win at joints** by picking the node cloud *first*, with a
  ``vtkPointPicker`` restricted by ``AddPickList`` / ``PickFromListOn`` and a
  slightly larger ``node_snap_tolerance``.  ``GetPointId()`` is the node index,
  because a point cloud's vertex cells index exactly like the node list.
- **Overlays must not be pickable.**  A highlight tube is drawn *over* its
  element, so a pickable overlay stole the click aimed at the element beneath;
  ``PyVistaRenderer._add_overlay`` sets ``SetPickable(False)`` on every
  decoration (highlight, label, deformed shape, force flag).
- **Node markers were sub-pixel.**  ``render_nodes`` used
  ``point_size=radius * 20`` → **0.4 px** at the default ``node_size=0.02``:
  invisible on screen and effectively unclickable.  ``node_point_size()`` now
  scales to pixels with a 6 px floor.
- ``PickerType`` is not exported from the ``pyvista`` namespace (it lives in
  ``pyvista.plotting.opts``), so nothing here names the enum.
- An embedded ``QtInteractor`` cannot pick under the ``offscreen`` Qt platform
  at all (no GL context → ``iren is None``), so real-pick tests use a plain
  ``pv.Plotter(off_screen=True)``.

**Roadmap correction.**  Design rule 7 previously said the forward map comes
from "the value PyVista's `enable_mesh_picking` callback supplies".  There is no
such value; the map is built from the scene picker plus the actor, and the rule
now says so.

## Mouse interaction: one policy, many mouse habits

**Added 2026-09-23.**  Whether a click means "select" and a drag means "orbit"
depends on the program someone came from (SAP2000 and ETABS use explicit modes,
Blender and Fusion use click-versus-drag, others use the right button).  That
makes it **configuration, not code**:

| Piece | Where | Nature |
|---|---|---|
| `InteractionPolicy` (the knobs) | `gui/controllers/interaction.py` | frozen dataclass, validates itself |
| `PRESETS` (`click_drag`, `right_click`) | same module | data, so a new habit is a new entry |
| `ClickGesture` (press → move → release) | same module | Qt/VTK-free state machine |
| settings loader (`load_policy`) | same module | JSON, never fatal |
| `ViewportInteraction` (gesture + pickers) | `gui/views/interactor.py` | Qt-free: pure logic plus the VTK pickers |
| `QtMouseFilter` (the event hook) | `gui/views/qt_mouse.py` | Qt event filter; logical → device conversion |

**Where the events come from — Qt, not VTK.**  Measured on macOS (pyvistaqt
0.13.1 / pyvista 0.48.1) against the real widget: a click reaches the widget's
`mousePressEvent` **and** the interactor's `LeftButtonPressEvent`, but
`mouseReleaseEvent` never delivers `LeftButtonReleaseEvent` to the interactor.
A release-driven gesture written as a VTK observer therefore **never fires** —
which is exactly what "clicking does nothing" looked like, and why the first
version of this feature was dead on arrival.  An event filter on the widget sees
press, move and release reliably, so the gesture lives there instead.

**Logical versus device pixels.**  Qt reports *logical* pixels from the top left;
VTK's pickers take *device* pixels from the **bottom** left.  On a Retina display
(2x here) the two differ by the device pixel ratio, so the filter converts
(`x * dpr`, `height_device - y * dpr`) and the gesture's `drag_threshold_px` is
deliberately in *logical* pixels — that is the distance a person judges.  A
calibration that mixed the two missed by exactly the ratio.

**Calibrated tolerance.**  `pick_tolerance` is a fraction of the viewport
diagonal.  Measured on a 1026x860 render window (device pixels; offsets to the
side of a member's midpoint, clicked through Qt):

| tolerance | ≈ device px | selects at |
|---|---|---|
| 0.003 | 4 | 0, 3 px |
| 0.006 | 8 | 0, 3, 6 px |
| **0.010** (default) | 13 | 0, 3, 6, 10 px |
| 0.015 | 20 | up to 16 px |
| VTK's default 0.025 | 33 | — (fat enough to shadow the joints) |

0.010 leaves a comfortable click while staying 2.5x tighter than VTK's default;
0.003 turned out to be unclickable in practice, which is what sent the first
release of this feature back to the drawing board.

Settings file: `$FEA_TOOLKIT_GUI_CONFIG`, else `~/.config/fea_toolkit/gui.json`
(the user-facing version of this section lives in [`gui.md`](gui.md)):

```json
{"preset": "click_drag", "drag_threshold_px": 4, "pick_tolerance": 0.004}
```

Unknown keys, an unreadable file or an out-of-range value leave the defaults in
place and are **reported in the message log** — a typo in a config file is a
silent failure otherwise.

The default preset is `click_drag`: a clean left click selects (a press that
travels further than `drag_threshold_px` before release is a drag and never
picks), left-drag keeps rotating, and a click that meets nothing clears the
selection.

**Explicit Select / Orbit modes** (the SAP2000 arrangement) are deliberately not
in the policy yet: a mode has to be able to *disable* rotation, which means
switching the VTK interactor style, so it is a larger change than a knob.  The
preset table and the adapter are the places for it — recorded in
`docs/_pending_work.md` P23.

## Quads stay quads — area elements are not fan-triangulated

Two shell-mesh builders disagreed.  The standalone viewers emitted true
`[4, i, j, k, l]` quad faces (`npz_to_pyvista_shell_mesh`, `_build_deformed_mesh`,
the pushover viewer), while the GUI's backend fan-split every polygon into
triangles (`PyVistaRenderer.render_shells`, and the shell branch of
`render_highlights`).  Because `show_edges=True` draws **every** cell edge, the
fan's invented diagonal was drawn across every slab in the GUI — that was the
visible symptom.

Both GUI sites now share a module-level `_polygon_cells`: three vertices stay a
triangle, four become **one** quad face, and only 5+ sided polygons are fanned
(from vertex 0, so that diagonal is at least deterministic).  The per-cell colour
arrays are built from the returned face counts, so a quad contributes one colour
entry rather than two.

### Why it matters beyond cosmetics — the deformed case

- A quad whose corners are displaced by *different* vectors is generally
  **non-planar**.  A non-planar quad has no unique surface (its bilinear patch is
  a hyperbolic paraboloid), so VTK has to split it along a diagonal — and *which*
  one is arbitrary, varying per cell and per camera angle, so neighbouring warped
  quads can crease inconsistently.
- The deformed-shape display multiplies the displacements by the scale factor, so
  it multiplies the **warp** too: a quad that is imperceptibly warped at 1:1 can
  visibly fold at 50–100×.

Keeping quads does not make a warped element planar — nothing can — but it stops
the renderer *inventing* geometry the element does not have, and leaves the split
to VTK's single consistent choice per cell rather than a fan this code imposes on
every element.

Worth stating plainly, because it is easy to assume otherwise: the GUI's deformed
overlay (`render_deformed`) draws **frames only** — area elements are not
displaced there yet, so deformed shells are a *forward-looking* concern for the
GUI.  The standalone deformed viewers already emit quads, so no change was needed
there; only 5+ sided area elements are fanned anywhere.

Pinned by `tests/test_renderers_pyvista.py::TestShellFaces`: one quad shell is one
cell with four points, a triangle stays a triangle, a pentagon fans to three
deterministic triangles, a mixed mesh keeps each cell's own shape, the colour
array matches the cell count, a highlighted quad is a single face, and an
end-to-end `ModelViewer` run over archive-shaped geometry agrees.

## Display transforms — shrink and shell opacity

Two display-only knobs (`render_*`'s `shrink`, and `shell_opacity` /
`set_category_opacity`), surfaced as the View toolbar's **Shells** and **Shrink**
boxes.  The design points that are easy to get wrong later:

- **They transform at draw time, never the geometry.**  `render_frames` /
  `render_shells` scale a *copy* as they build the mesh, so `ModelViewer._frames`
  and `._shells` keep the true coordinates.  Selection, picking, results and the
  deformed overlay all read that true geometry — the overlay especially, which
  must **not** inherit a display shrink (a deformed shape drawn from shrunken
  endpoints would be wrong, not merely odd).
- **Highlights take the same shrink** —
  `render_highlights(highlights, shrink=…)`.  A highlight is drawn *over* the
  element it marks, so if only the model shrank the highlight would stick out
  past it.  `ModelViewer` remembers `self._shrink` and passes it on, which is why
  `highlight_elements` needs no new argument.
- **Opacity changes in place; shrink re-renders.**  Opacity is a VTK actor
  property (`set_category_opacity`, mirroring `set_category_visible`), so a
  transparency control never rebuilds the mesh and stays smooth on a large model.
  Shrink is geometry, so the GUI re-renders the active view through
  `MainWindow._refresh_display`, which uses `reset_view=False` and
  `rebuild_tree=False` — the same model drawn differently must not move the
  camera or drop the tree selection.
- **`shell_opacity` is deliberately separate from `opacity`.**  `opacity` still
  governs everything (the pre-existing behaviour) and `shell_opacity=None`
  inherits it, so nothing changed for existing callers.  The GUI passes a value
  because slabs want transparency while frame lines want to stay crisp; 70 % is
  the default — enough to see through a slab, not so little that its section
  colour stops reading.

Pinned by `tests/test_renderers_pyvista.py::TestShrinkAndOpacity` (the transform
arithmetic, the untouched caller geometry, the shrunk highlight, in-place
opacity) and `tests/test_gui_views.py::TestDisplayQuality` (the 70 % default, the
in-place update, the survival of a view switch, and a shrink re-render that keeps
the view).




## Support symbols — one glyph per restrained DOF

`render_restraints` draws a support at every restrained node.  The choice of
**per-DOF glyphs** over textbook symbols was deliberate:

- A restraint is an arbitrary six-flag set.  A pattern table covers fixed / pin /
  roller and then has to guess at everything else; one glyph per restrained DOF is
  correct for *any* set, so nothing is ever left undrawn.  The classic symbols stay
  a planned *refinement* (`_pending_work.md` P25) that falls back to these arrows
  for the patterns it does not recognise.
- Translations become `pv.Arrow`s starting a *size* away and pointing **at the
  joint** — the ground pushing the node back — and rotations become curls about
  their axis.  Verified by rendering three bases side by side: the fixed base read
  as arrows plus curls, the pin as three arrows, the roller along Z as one arrow.
- **Sizing is 4 % of the model's bounding-box diagonal** — self-scaling like the
  highlight radius.  It began at 6 %, which measured right in a close-up render and
  was reported as too large in use; 3 % had been too small at whole-model zoom.  The
  lesson is the one this project keeps relearning: judge a display default on the
  real viewport, not on a favourable screenshot — and pin the *band* (a test asserts
  3–5 % of the diagonal) rather than the number, so tuning does not churn tests.
- **A restrained node is drawn larger, in green**, over the full node cloud rather
  than by splitting it in two — and that distinction earned its keep.  A pick
  reports an index into the actor it hit, which the selection index maps back to a
  node id, so a second **pickable** node layer shifts that index:
  `tests/test_picking.py` immediately caught a click on a restrained node selecting
  the wrong one.  The marker is therefore an **overlay**
  (`render_nodes(..., pickable=False)`), which is also why the cloud and the marker
  must share the ``"nodes"`` category — the display toggle then hides the marker
  with the cloud it annotates.
- It also has to stay unmistakable from *selection*: the selection overlay is a
  **blue sphere of a fixed 15 px**, drawn over the nodes, so the green marker is
  capped below it (14.4 px at the default) and uses a colour the selection never
  uses.  A test asserts the cap explicitly, because those two cues drifting into
  each other is exactly the failure this design exists to prevent.
- The glyphs are a **non-pickable overlay**.  A support is not an entity a user
  picks — the click belongs to the node behind it — and a pickable glyph would
  steal that click (the *PyVista picking contract*, above).
- `plotting/restraint_glyphs.py` is Qt-free and plotter-free, so the shapes are
  measurable in a unit test; `model/supports.py` holds the naming and is shared
  with the Inspector, so a glyph and the row describing it cannot disagree about
  what a set is called.
- A results archive carries **no** restraints (`mesh_model_from_geometry` does not
  populate them), so supports appear on an open model only.  Adding them to the
  archive schema is a separate decision, not a bug in this one.

## Selection feedback — a selected shell needs *volume*

Frames and nodes always showed selection; area elements did not, and it took two
fixes — the second one the real one.

**Why the cue vanished.**  `render_highlights` drew a selected shell as a
translucent fill (opacity 0.7) **coincident** with the model's own shell, with
**grey** edges — the same grey as every unselected element.  Against an opaque slab
that was merely subtle; once shells defaulted to 70 % opacity the two translucent
surfaces cancelled out.  A first attempt then outlined the fill's boundary in the
selection colour, but rendered against 70 % shells the *fill* survived and the
outline did not, so the cue was still unreliable.

**Why nothing appeared to happen at all** — the more important finding.  A click on
a slab resolved to the **wrong element, or to none**:
`SelectionIndex._shell_label` walked the renderer's cells assuming the old *fan*
rule — `len(vertices) - 2` triangles per element — while `render_shells` had begun
drawing quads as single quad faces.  Every cell index therefore mapped one element
too far.  A click that resolves to nothing is indistinguishable from a click that
did nothing, which is why this read as "selection does not work" rather than as a
mapping bug.

Both rules now come from one function, `polygon_face_count`, used by the renderer
*and* the index, and the tests are tied to it instead of restating it:

- `test_renderers_pyvista.py::TestShellFaces::test_the_renderer_builds_exactly_the_faces_the_rule_predicts`
  — the rule must describe what `render_shells` really emits;
- `test_gui_selection_index.py::test_shell_cells_follow_the_renderers_own_faces`
  — the index must walk those same faces;
- `test_picking.py::test_a_slab_picks_the_element_under_the_cursor` — a real pick
  on the *second* of two elements resolves to the second.

All three were checked to fail against the old rule.  The latter two deliberately
use **two** elements: with a single quad every cell maps to the same element, so a
wrong rule hides — which is how the original test (and the renderer, which had no
shell-pick test at all) missed this.

**The cue is now volume.**  `_selection_slab` extrudes each selected element
slightly along its normal — two faces offset to either side joined by a rim — so the
highlight *surrounds* the element instead of lying on it, exactly as a selected
frame is a tube rather than a recoloured line.  It is drawn at **85 % opacity** —
solid enough to read as a cue over any section colour (the frame tube stays fully
opaque), translucent enough to see what it covers — at the same model-scaled
half-thickness the tube uses, so the two cues read as one gesture.  Translucency
costs the slab no clarity, because it is volume and not a coincident surface.
Verified by rendering a 2 × 2 slab at 70 % shell opacity with one element selected:
a solid orange panel against the grey remainder.

Two smaller lessons from the same work, both of which bit here and are now pinned
in tests: PyVista's `mapper.dataset` returns a **new Python wrapper** on every
access, so mesh identity cannot be asserted with `is` (assert the *actor* identity
instead); and VTK stores colours at **8-bit** precision, so a colour assertion needs
`abs=0.01`, not the default tolerance.


Two smaller lessons from the same work, both of which bit here and are now pinned
in tests: PyVista's `mapper.dataset` returns a **new Python wrapper** on every
access, so mesh identity cannot be asserted with `is` (assert the *actor* identity
instead); and VTK stores colours at **8-bit** precision, so a colour assertion needs
`abs=0.01`, not the default tolerance.

