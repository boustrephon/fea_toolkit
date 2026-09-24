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

