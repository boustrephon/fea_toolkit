---
title: "Desktop GUI Roadmap"
description: "Framework decision and architecture for a Qt/PySide6 desktop application wrapping the import → view → query → analyse → results workflow."
status: "draft"
tags: [gui, qt, pyside6, pyvistaqt, architecture, planning, roadmap]
category: [planning]
related: [viewer.md, workflow.md, results_schema.md, rhino_export.md, report_generation.md]
---
# Desktop GUI Roadmap

## Status: 🚧 Design proposal — not yet implemented

This document records the **framework decision** and the **proposed
architecture** for a native desktop GUI that wraps the workflow already
implemented in the package.  Nothing here is built yet; it is the agreed
starting point for the work tracked as **P22** in
[Pending Work Register](_pending_work.md).

---

## 1. Goal

A native, document-centric desktop application — the classic CAE arrangement
that ParaView, Ansys Mechanical, ABAQUS/CAE and FreeCAD all converged on, and
the same layout as SAP2000 / ETABS / Salome-Meca — built around a central 3-D
visualisation screen.

### 1.1 Target layout

```
┌──────────────────────────────────────────────────────────────────────────┐
│ File   Edit   View   Model   Analysis   Results   Help                   │ ← menu bar
├──────────────────────────────────────────────────────────────────────────┤
│ [Open] [Save] │ [Run ▶] [Stop ■] │ [Mesh] [Deformed] [Forces] │ [Units]  │ ← toolbar
├────────────────────────────┬───────────────────────────────┬─────────────┤
│ ╭ Model Tree ╮ ╭ Property ╮│                               │ View        │
│ │ ▸ Geometry │ │ Tree     ││                               │  [Iso]      │
│ │ ▾ Mesh     │ │  Sections││                               │  [X][Y][Z]  │
│ │   ▸ Nodes  │ │ Materials││        3-D Viewport             │ Display     │
│ │   ▸ Elems  │ │  Loads   ││  (pyvistaqt QtInteractor)     │  ☑ Nodes    │
│ │ ▸ Loads    │ │  Cases   ││                               │  ☑ Labels   │
│ │ ▸ Results  │ │          ││                               │  ☑ Loads    │
│ ╰────────────╯ ╰──────────╯│                               │  ☐ Forces   │
│ ╭ Property Inspector ─────╮│                               │             │
│ │ Frame 4521  (SEC-BEAM)  ││                               │  [view cube]│
│ │   E  =  210 GPa         ││                               │             │
│ │   ν  =  0.30            ││                               │             │
│ │   A  =  8.4e-3 m²       ││                               │             │
│ ╰─────────────────────────╯│                               │             │
├────────────────────────────┴───────────────────────────────┴─────────────┤
│ [info]  Parsed model.s2k — 4 812 nodes, 7 233 frames  (0.8 s)            │ ← message
│ [warn]  12 joints share duplicate coordinates                            │   log
│ [error] Combination "EQ-X" references missing case "SX"                  │
├──────────────────────────────────────────────────────────────────────────┤
│ Ready │ x 12.40  y −3.10  z 0.00 │ Elem 4521 │ ██████░░░░ 47 %           │ ← status bar
└──────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Dock layout

Everything maps onto `QMainWindow` primitives:

| Region | Qt primitive | Purpose |
|---|---|---|
| **Menubar** | `QMainWindow.menuBar()` | File / Edit / View / Model / Analysis / Results / Help |
| **Toolbar** | `QToolBar` (top) | Open, save, run/stop, mesh/deformed/forces, units |
| **View toolbar** | `QToolBar` (right edge, vertical) | Camera (iso / X / Y / Z), display toggles |
| **Model Tree + Property Tree** | `QDockWidget` (`LeftDockWidgetArea`), top half — tabbed | Entity hierarchy (lazy) and object-*kind* groups |
| **Property Inspector** | `QDockWidget` (`LeftDockWidgetArea`), bottom half — `splitDockWidget` | Editable properties of the current selection |
| **3-D viewport** | `setCentralWidget(QWidget container)` | The `QtInteractor`, plus room for a future quad view |
| **Message Log** | `QDockWidget` (`BottomDockWidgetArea`) | Read-only, severity-coloured log (info / warn / error) |
| **Status Bar** | `QMainWindow.statusBar()` | Cursor coordinates, element ID, units, solve progress — sits *below* the bottom dock |

The left dock is split **vertically**: Model Tree and Property Tree share the
top half as tabs, the Property Inspector takes the bottom half — leaving the
right side free for a future colour-bar legend, result probes, or a second
viewport.  PyVista's in-render helpers (`add_axes()`,
`add_camera_orientation_widget()`) supply the axes triad and the view cube
inside the viewport itself.

The workflow it must support is the one the toolkit already encodes:

1. Import models from `*.s2k` (later `*.e2k`, PyRite, OpenSees).
2. Display geometry **and loading**.
3. Query objects for their properties / materials.
4. Initiate analyses.
5. View results.

---

## 2. Recommendation (TL;DR)

**Build the GUI on Qt for Python, using PySide6 as the binding (accessed
through `qtpy`) and `pyvistaqt` to embed the existing PyVista renderer into
the Qt window.  Ship it as a new optional `[gui]` extra — not a core
dependency.**

This is the only realistic way to obtain the exact chrome listed above while
reusing the 3-D rendering the package already provides.

The two layers are **complementary, not alternatives**: **PySide6 is the
application shell** (windows, menus, toolbars, docks, dialogs, widgets);
**`pyvistaqt` is a bridge** that provides no UI of its own and simply places a
PyVista/VTK canvas into a Qt window as an ordinary `QWidget`
(`QtInteractor`).  The existing `ModelViewer` geometry and rendering logic is
kept — it is pointed at the embedded `QtInteractor` instead of calling
`plotter.show()` on a standalone window.

---

## 3. Framework comparison

The outline describes a native engineering desktop app.  Qt maps onto every
requested element directly; the alternatives do not.

| Framework | Verdict |
|---|---|
| **Qt (PySide6 / PyQt6)** | ✅ **Recommended.** Native docking (`QDockWidget`), tree/property widgets (`QTreeView` + `QAbstractItemModel`), `QMenuBar` / `QToolBar` / `QStatusBar`, and **PyVista's official Qt integration** (`pyvistaqt`). |
| **Tkinter** | ❌ No first-class VTK embedding (only a weak `vtkTkRenderWindowInteractor`); poor docking and tree/property UI. |
| **wxPython** | ⚠️ Capable (wx + VTK), but no official PyVista backend, less modern, uncommon in FEA tooling — more work for no benefit. |
| **PyVista in-window widgets** (current `plot_interactive_viewer`) | ⚠️ Excellent for a single viewport, but `add_radio_button_widget` etc. render *inside* the VTK window. They cannot produce a menubar, dockable panels or a real status bar — this is a widget overlay, not a desktop app. |
| **Web (trame / Panel / `export_html`)** | ⚠️ Viable if browser delivery is ever wanted — PyVista ships a `trame` backend and the toolkit already has `export_html()`. But no native menubar/toolbar/docking, and packaging differs entirely. |
| **Dear PyGui** | ❌ Fast immediate-mode UI with a small widget set, but no docking/tree/property widgets worth the name and no native PyVista viewport (it needs an external OpenGL bridge). Integration effort is high, and `ModelViewer` logic would not carry over. |

### 3.1 Why Qt

- **It is the de-facto standard** for structural-engineering desktop tools,
  and every requested UI element has a first-class Qt widget.
- **`pyvistaqt` is the official PyVista Qt helper** (MIT, maintained by the
  PyVista team, current release 0.13.1).  It provides `QtInteractor`,
  `MainWindow`, `BackgroundPlotter` and `MultiPlotter`, and abstracts the
  binding through **`qtpy`** — so the code does not care whether the user has
  PySide6 or PyQt6 installed.
- **It reuses the entire existing plotting investment** — the
  `RenderBackend` ABC (`plotting/renderers/base.py`) was written precisely so
  the render *target* can be swapped.  A Qt application becomes a new
  `QtRenderBackend` rather than a rewrite.

### 3.2 How the two layers fit together

```
PySide6  (QMainWindow, menus, toolbars, docks, dialogs)
   └── central widget: QWidget container
          └── QtInteractor            (from pyvistaqt)
                 └── renders via PyVista / VTK
```

`QtInteractor` is a normal widget: drop it into a layout beside the trees and
inspector like any other.  Ansys' TBROM GUI uses exactly this stack (PySide6
shell + PyVista/PyVistaQt viewport), so the combination is well-trodden.

### 3.3 Version compatibility (verify at scaffold time)

The binding and `pyvistaqt` versions must be pinned **as a verified pair**, not
assumed compatible.  A historical incompatibility — `pyvistaqt` against
**PySide 6.7**, where `QtPrintSupport` was missing `QDragEnterEvent` — is the
precedent: the resolution was to pin the binding to an older release until
`pyvistaqt` caught up.  The current `pyvistaqt` (0.13.1) post-dates that issue,
but milestone 1 must confirm the exact `PySide6` × `pyvistaqt` combination
before any UI code is written (the project rule is to verify third-party APIs,
never to guess a version pairing).

### 3.4 Performance characteristics of the chosen stack

Two properties of the embedded renderer shape the `QtRenderBackend` design:

1. **Bulk `add_mesh` is slower on `QtInteractor` than on a standalone
   `Plotter`.**  Adding many separate meshes one at a time is the bottleneck
   (one report measured thousands of meshes at ~30 s on `QtInteractor` versus
   ~1 s on `Plotter`).  The mitigation is to batch geometry into a
   **`MultiBlock`** composite and pass **`render=False`** during the bulk
   adds, then trigger a single `render()` at the end.  A typical FEA model is
   a few large meshes rather than thousands of small ones, but the toolkit's
   split mesh can be heavily fragmented, so batching is a design requirement,
   not an optimisation.
2. **Realtime streaming has limits.**  Continuously-updated point clouds
   beyond roughly one million points degrade, and volume/glyph refreshes
   require full regeneration.  Static post-processing (load once, rotate,
   slice, probe) is unaffected; **frame-by-frame transient animation** (mode
   shapes, pushover playback) must manage updates deliberately.

---

## 4. Fit with the existing codebase

Every workflow item already has a package entry point; the GUI is largely a
**thin orchestration and state layer** over them.

| Workflow step | Existing API |
|---|---|
| Import `*.s2k` | `SAP2000Parser(path).parse().get_model_data()` |
| Display geometry | `ModelViewer` / `plot_mesh` (backend-agnostic `RenderBackend`) |
| Display loading | ⚠️ **Gap** — see §4.1 |
| Query objects | `plot_interactive_viewer` already does click → element ID / SAP label / section / material; plus `Selection`, `model.review`, and the `Section` / `Material` dataclasses |
| Initiate analyses | `AnalysisBuilder.run_static_analysis()` / `run_modal_analysis()` / `run_response_spectrum_analysis()` / `run_pushover_analysis()` |
| View results | `plot_deformed_displacement_3d`, `plot_force_diagram`, `plot_pushover_curve`, `write_results_npz` |

### 4.1 The one net-new capability: load rendering

`RenderBackend` currently exposes `render_frames / render_shells /
render_nodes / render_highlights / render_annotations / render_deformed /
render_force_flags` — but **no `render_loads()`**.  The model layer already
carries the data (`FrameDistributedLoad`, `JointLoad`, `LoadPattern` in
`model/sap_data.py`, plus `build_gravity_patterns` / `infer_loads` in the
utils facade), so "display loading" means two additions:

1. A model-layer extractor resolving applied loads into glyphs — arrows for
   joint/point loads, distributed arrows for line loads on frames, planar
   arrows for area loads.
2. A new `render_loads(...)` method on `RenderBackend` plus its PyVista/Qt
   implementations.

This is the only genuinely new visualisation capability the GUI requires;
everything else is wiring against existing APIs.

---

## 5. Licensing & packaging notes

- **PySide6 is LGPL-3.0 / GPL-2.0 / commercial**; **PyQt6 is GPL-3.0 /
  commercial**.  This project is **GPL-3.0-or-later**, so *both are
  compatible*.  **PySide6 is the recommended default**: it is the official
  Qt Company binding (no Riverbank/sip licensing politics) and its LGPL
  terms make the "is my app a derivative work?" question moot.  Writing
  against `qtpy` keeps the choice reversible.
- **`pyvistaqt` ≥ 0.12 requires Python ≥ 3.10.**  The package floor is 3.9
  (the Rhino 8 embedded-interpreter syntax floor), but the GUI is a *desktop
  app*, not something that runs inside Rhino — and PyVista itself cannot be
  installed in Rhino's embedded interpreter (see `tests/conftest.py`).  The
  clean separation is therefore a **new `[gui]` extra that requires Python
  3.10+**, leaving the core untouched at 3.9.  The CI matrix (3.10 / 3.12)
  already satisfies it.
- **OpenSeesPy redistribution caveat already stands** (see §5.8 of the
  project guardrails) and becomes more visible once a packaged GUI exists: a
  shipped application that imports OpenSeesPy requires OSU's commercial
  licence for commercial use.  A status-bar / About disclaimer is
  appropriate.

---

## 6. Proposed architecture

A new optional subpackage, imported lazily so the core never depends on Qt:

```
src/fea_toolkit/gui/                 # new subpackage (optional [gui] extra)
├── __init__.py                      # lazy re-exports; guards the Qt import
├── app.py                           # QApplication bootstrap + run_gui() entry
├── main_window.py                   # QMainWindow: menubar, top + vertical toolbars,
│                                    #   docks (left: trees + inspector, bottom: log), statusbar
├── controllers/
│   ├── session.py                   # MVC state: SAPModelData → MeshModel → AnalysisBuilder → results
│   ├── selection.py                 # bidirectional tree ↔ viewport selection sync
│   └── persistence.py               # QSettings: save/restore window geometry + dock state
├── panels/
│   ├── model_tree.py                # QTreeView + QAbstractItemModel (lazy) over SAPModelData
│   ├── property_tree.py             # QTreeView grouped by object kind (sections/materials/loads/cases)
│   ├── property_inspector.py        # selected object's dataclass → editable form
│   └── message_log.py               # bottom dock: read-only QPlainTextEdit, severity-coloured
├── actions/                         # QAction handlers wired to the pipeline
│   ├── import_model.py              # → SAP2000Parser
│   ├── run_analysis.py              # → AnalysisBuilder (on a worker thread)
│   └── export_results.py            # → write_results_npz
└── viewport/
    ├── container.py                 # central QWidget holding 1..n QtInteractor viewports
    └── qt_renderer.py               # QtRenderBackend → pyvistaqt.QtInteractor (MultiBlock, render-once)
```

### 6.1 Design rules

The GUI must respect the existing architectural contracts:

1. **Backend-agnostic.**  Add `QtRenderBackend` alongside `PyVistaRenderer`;
   `ModelViewer` stays as-is and targets whichever backend.  No new 2-D
   dispatch — the 3-D-only rule (`ndm=3`, `ndf=6`) stands.
2. **Model-view separation.**  The GUI never mutates `MeshModel` after
   preprocessing and never adds analysis logic; it only *calls* the
   `Preprocessor` / `AnalysisBuilder`.  This preserves the frozen-topology
   contract and the two-stage pipeline.
3. **Threading.**  OpenSees solves are long-running and would freeze the Qt
   event loop if run on the main thread.  Analyses run on a
   `QThread` / `ThreadPoolExecutor`; progress and completion are marshalled
   back to the UI via Qt signals (status bar + optional progress dialog).

   **Stop / cancellation.**  The toolbar's **Stop** action requests
   cancellation *cooperatively* — it sets a `threading.Event` (or a
   `QAtomicInt` flag) owned by the worker; it never kills the thread.  The
   worker checks the flag between OpenSees `analyze()` steps (the pushover
   loop is step-wise and therefore interruptible) and between analysis
   stages.  On seeing the request it stops issuing further `analyze()` calls,
   calls `ops.wipe()` to release the OpenSees domain, emits
   `finished(cancelled=True)`, and re-enables the run action, leaving the
   status bar and message log to record the cancellation.  A single atomic
   solve (a one-shot `analyze()` for static/modal) is **not** interruptible
   mid-call: there Stop takes effect only at the next step/stage boundary, so
   the GUI disables the Stop control during an atomic solve and re-enables it
   once a step-wise loop is reached.  A forced thread kill is deliberately
   avoided — it would leave OpenSees global state inconsistent and break the
   `ops.wipe()` hygiene the rest of the toolkit relies on.
4. **Flexible input pattern.**  The viewport accepts `SAPModelData`,
   `MeshModel`, `AnalysisBuilder`, **or** an NPZ path — exactly as the
   existing plot functions already do.
5. **Optional-dependency guard.**  No bare `import PySide6` / `import
   pyvistaqt` at package import time; the whole `gui` subpackage is imported
   lazily, mirroring the existing `pandas` / `h5py` policy.
6. **Lazy tree population.**  The Model Tree is a `QTreeView` driven by a
   custom `QAbstractItemModel` with **lazy population** — logical groups
   (parts, materials, load sets, result sets) expand into individual entities
   only on demand.  **Never `QTreeWidget`**, and never populate every node: a
   large model would produce hundreds of thousands of items and the UI would
   crawl.
7. **Bidirectional selection sync.**  The highest-value UX feature, designed in
   from the start rather than retrofitted: a **tree** selection highlights the
   matching region in the 3-D view, and a **viewport pick** (PyVista's
   `enable_mesh_picking` / `enable_point_picking`) selects and scrolls to the
   matching tree node.  `controllers/selection.py` owns this, and both
   directions ride on **one stable mapping** built when the viewport batches
   its geometry (§3.4):

   * **forward** — render identity → SAP label: `cell_id -> SAP frame/shell
     label` (the value PyVista's `enable_mesh_picking` callback supplies) and
     `point_id -> SAP node id` (from `enable_point_picking`);
   * **reverse** — SAP label → render identity: `SAP label -> [cell_id, …]`,
     for highlighting.

   The viewport pick callback resolves the picked cell/point id through the
   **forward** map to a SAP label and selects + scrolls to that tree node; the
   tree-selection callback resolves the tree node's SAP label through the
   **reverse** map to the cell ids and highlights exactly those cells.  The
   index is rebuilt whenever the geometry is re-batched, so pick ids never
   drift from the displayed `MultiBlock`.
8. **GPU-friendly viewport updates.**  Batch geometry into a `MultiBlock` and
   render once (see §3.4); never `add_mesh` in a loop with rendering left on.
9. **Persistent layout.**  Save window geometry and dock state with
   `QSettings("fea_toolkit", "gui")` (`saveGeometry()` / `saveState()`) and
   restore them in `__init__` — users rearrange docks and expect it to stick.
10. **Quad-view ready.**  `setCentralWidget()` takes a `QWidget` **container**
    from day one, even though it currently holds a single viewport, so a grid
    of `QtInteractor`s (iso / front / top / side) can be added later with no
    refactor.

---

## 7. Phased plan

1. **Scaffold the extra.**  Add `[gui]` to `pyproject.toml` (`PySide6`,
   `pyvistaqt`, `qtpy`), document the 3.10+ requirement for the extra, verify
   the binding/`pyvistaqt` version pair (§3.3), and create a stub
   `gui/__init__.py` with lazy import guards.
2. **Embed the viewport.**  Refactor `PyVistaRenderer` to accept an injected
   plotter, add `QtRenderBackend` (batched `MultiBlock`, render-once), give
   `setCentralWidget` a `QWidget` container, and prove `ModelViewer` renders
   into a `QtInteractor` inside a bare `QMainWindow`.  *(Lowest-risk spike — it
   proves the whole stack.)*
3. **Main-window chrome.**  Menubar + top `QToolBar` + right-edge vertical
   view toolbar + the dock layout (left: tabbed trees over inspector; bottom:
   message log) + status bar (coordinates / element ID / progress) + the
   in-render axes triad and view cube.
4. **Trees + inspector.**  Model Tree (lazy) and Property Tree driven from
   `SAPModelData`; Property Inspector populating from the selected object's
   dataclass (`Section` / `Material` / load / case).
5. **Bidirectional selection sync.**  Tree ↔ viewport in both directions
   (`controllers/selection.py`), including the existing pick data (element ID /
   SAP label / section / material).
6. **Import + analysis actions.**  `QAction`s calling `SAP2000Parser` and
   `AnalysisBuilder.run_*()` on a worker thread, with progress and results
   marshalled to the status bar and message log.
7. **Load rendering.**  New `render_loads()` on `RenderBackend` plus the
   model-layer glyph extractor (§4.1) — the only net-new viz feature.
8. **Results + export.**  Reuse the `plot_*` / `write_results_npz` surface for
   the "View results" and "Export" menus.
9. **Persistence.**  `QSettings` geometry + dock-state save/restore.
10. **Tests.**  Headless `QApplication` with the `offscreen` platform plugin
    (`QT_QPA_PLATFORM=offscreen`), `ops.wipe()` hygiene, a `needs_gui` marker
    alongside the existing `needs_pyvista` marker.

---

## 8. Open decisions

### Resolved (2026-09-23)

- **Native desktop (Qt) vs web (trame/Panel)** — **native Qt**.  The requested
  chrome (menubar, dockable trees, inspector, status bar) is a native-desktop
  idiom; native Qt reuses the existing PyVista/`RenderBackend` stack
  in-process and is the most responsive and resource-efficient option for a
  single-user local tool.  A web front-end cannot move the compute into the
  browser — OpenSeesPy is a CPython C extension — so "web" would mean a
  client-server application (browser client + Python/VTK server), not a pure
  web app.  It is therefore **deferred until a concrete zero-install, cloud or
  multi-user requirement appears**, at which point it becomes an *additional*
  `trame` render backend reusing the same analysis core rather than a rewrite.
  **iOS is out of scope**: neither PySide6 nor the PyVista/VTK stack ports to
  iOS, and the only iOS route (a hosted server with a thin client) runs into
  the OpenSeesPy commercial-licence constraint (§5.8 of the project
  guardrails).
- **Binding default** — **PySide6**, written against `qtpy` so a PyQt6 switch
  stays possible.
- **Panels (Option A)** — all three are kept: Model Tree and Property Tree
  share the left dock's top half as tabs, with the Property Inspector below.
- **Message Log** — **included**, as the bottom dock.
- **Python floor** — the `[gui]` extra requires **3.10+** (driven by
  `pyvistaqt`); the core stays at 3.9.

### Still open

1. **Version pinning.**  Confirm the exact `PySide6` × `pyvistaqt` pair in
   milestone 1 (§3.3) before any UI code is written.
2. **First milestone scope.**  Start with the viewport-embedding spike +
   main-window chrome (lowest risk), or go straight for the full scaffold?
