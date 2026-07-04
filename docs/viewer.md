# Visualisation toolkit

The ``fea_toolkit.plotting`` package provides two complementary
visualisation stacks:

| Stack | Backend | Purpose |
|---|---|---|
| **``ModelViewer``** | PyVista (pluggable) | Backend‑agnostic 3D model viewer — show model, overlay deformed shape / force flags, highlight elements, annotate. |
| **Standalone 3D plots** (`plot_*_3d`) | PyVista | Direct‑call 3D plots for specific result types — deformed shape, mode shapes, force/moment diagrams, RS deformation. |
| **Standalone 2D plots** (`plot_*`) | Matplotlib | Elevation‑based force diagrams, pushover capacity curves, ADRS spectra. |
| **Interactive viewer** (`plot_interactive_viewer`) | PyVista + widgets | Widget‑driven viewer with radio buttons, combo selector, click‑to‑inspect elements. |
| **NPZ standalone** (`plot_npz_*`) | PyVista / Matplotlib | Plot from a saved ``.npz`` file without needing the original builder. |

---

## Quick reference

| Function | Backend | What it shows | Best for |
|---|---|---|---|
| `ModelViewer(builder).show_model()` | PyVista | 3D model, coloured by section | General model inspection, highlighting problem elements |
| `ModelViewer(...).overlay_deformed()` | PyVista | Deformed shape overlay | Static / modal / RS displacement results |
| `ModelViewer(...).overlay_forces()` | PyVista | Force/moment flag diagram | Static element forces |
| `ModelViewer(...).highlight_elements()` | PyVista | Highlighted elements | Marking braces, issues, discussion topics |
| `ModelViewer(...).annotate()` | PyVista | Text annotation | Labelling features |
| `plot_model_3d(builder)` | PyVista | 3D model (nodes, labels, section colours) | Quick model preview |
| `plot_deformed_3d(builder, results)` | PyVista | Deformed + undeformed overlay | Static analysis displacement |
| `plot_rs_deformed_3d(builder, disp)` | PyVista | RS CQC‑combined deformed shape | Response‑spectrum results |
| `plot_mode_3d(builder, shapes, mode)` | PyVista | Mode shape (with animation) | Modal analysis |
| `plot_static_moment_3d(builder, forces)` | PyVista | 3D force/moment flag or tube diagram | Static element forces |
| `plot_static_shear_3d(builder, forces)` | PyVista | 3D shear force diagram | Shear results |
| `plot_static_axial_3d(builder, forces)` | PyVista | 3D axial force diagram | Axial results |
| `plot_static_force_diagram(builder, forces)` | Matplotlib | 2D force vs elevation | Column / wall forces |
| `plot_force_diagram(elem_results)` | Matplotlib | 2D CQC‑combined force vs elevation | RS element results |
| `plot_pushover_curve(results)` | Matplotlib | Capacity curve | Pushover summary |
| `plot_pushover_curve_enhanced(results)` | Matplotlib | Capacity curve + stiffness lines | Detailed pushover review |
| `plot_capacity_spectrum(adrs, spec, pt)` | Matplotlib | ADRS format capacity + demand + performance point | CSM (ATC‑40) |
| `plot_interactive_viewer(builder, forces)` | PyVista + widgets | Full interactive viewer | Exploration, demos |
| `plot_npz_force_diagram(path)` | Matplotlib | 2D force from saved NPZ | Post‑hoc analysis |
| `plot_npz_moment_3d(path)` | PyVista | 3D force from saved NPZ | Post‑hoc analysis |

---

## 1. ``ModelViewer`` — backend‑agnostic 3D viewer

The :class:`~fea_toolkit.plotting.viewer.ModelViewer` provides a
backend‑agnostic 3D viewer for structural models and analysis results.
It is designed for both interactive exploration and **LLM-assisted
discussions** — you (or an AI assistant) can call it to display a model,
overlay results, highlight problem areas, and annotate specific elements.

### Quick start

```python
from fea_toolkit.plotting import ModelViewer

# From a built OpenSeesBuilder
viewer = ModelViewer(builder)
viewer.show_model(show_nodes=True, color_by_section=True)
viewer.show()

# Or from raw model data (no builder needed)
viewer = ModelViewer(model_data=md)
viewer.show_model()
viewer.show()
```

### Constructor

```python
ModelViewer(builder=None, model_data=None, backend="pyvista", **kwargs)
```

| Argument | Default | Description |
|---|---|---|
| `builder` | `None` | An :class:`~fea_toolkit.opensees.builder.OpenSeesBuilder` that has been built. Uses split elements if available. |
| `model_data` | `None` | A :class:`~fea_toolkit.model.sap_data.SAPModelData`. Ignored if *builder* is given. |
| `backend` | `"pyvista"` | Render backend name. Currently supports ``"pyvista"``. |
| `**kwargs` | — | Passed to the backend constructor (e.g. ``off_screen=True``). |

### Model display

```python
viewer.show_model(show_nodes=True, show_shells=True,
                  color_by_section=True, opacity=1.0, node_size=0.02)
```

Draws the structural model — frame elements as coloured lines, shell
elements as triangulated surfaces, nodes as points.  Elements are
coloured by section name by default.

### Results overlay

```python
viewer.overlay_deformed(displacements=None, scale=1.0,
                        color=(0.3, 0.6, 1.0))
```

Overlays the deformed shape.  If *displacements* is ``None``, reads from
the builder's ``_last_static_results``.

```python
viewer.overlay_forces(elem_forces=None, quantity="Mz",
                      use_local=True, scale_factor=None)
```

Overlays force/moment flag diagrams.  Quantities: ``'Mz'``, ``'My'``,
``'Mx'``, ``'Fx'``, ``'Fz'``, ``'Fy'``.  Auto-scales flags to ~10 %
of the model diagonal.

### Highlighting

```python
viewer.highlight_elements(
    frame_ids=["1", "5", "12"],      # frame element IDs
    area_ids=None,                     # area element IDs
    color=(1.0, 0.0, 0.0),            # RGB 0..1
    label="Buckled braces",            # optional text label
    radius=0.03,                       # tube radius
)

viewer.highlight_nodes(
    node_ids=["10", "47"],
    color=(0.0, 1.0, 0.0),
    label="High displacement",
)
```

### Annotation

```python
viewer.annotate(
    text="Check this joint",
    node_id="5",                       # attach to a node
    # or use explicit position:
    # position=np.array([10.0, 0.0, 5.0]),
    color=(1.0, 1.0, 0.0),
    font_size=14,
)
```

### Output

```python
viewer.show()                          # open interactive window
viewer.screenshot("view.png")          # save image
viewer.export_html("view.html")        # save interactive HTML
viewer.clear()                         # remove all actors
```

The following display, overlay, highlight, and annotation methods
return ``self`` for chaining:

* ``show_model()``
* ``overlay_deformed()``
* ``overlay_forces()``
* ``highlight_elements()``
* ``highlight_nodes()``
* ``annotate()``

```python
viewer.show_model().highlight_elements(["1"], label="Issue").show()
```

Methods that open, export, or clear the plot (``show()``, ``screenshot()``,
``export_html()``, ``clear()``) do **not** return ``self`` and terminate
the chain.

### Backend architecture

```
ModelViewer (backend-agnostic)
    │
    ├── extracts geometry from builder / model data
    │   into FrameGeom, ShellGeom, NodeGeom
    │
    └── delegates rendering to a RenderBackend
            │
            ├── PyVistaRenderer   ← macOS, Linux, Windows
            └── RhinoRenderer     ← planned (Windows + Rhino 8)
```

To add a new backend, implement :class:`~fea_toolkit.plotting.renderers.base.RenderBackend`
and register it in ``viewer._resolve_backend()``.

---

## 2. Standalone 3D plots (PyVista)

These are direct‑call functions in :mod:`fea_toolkit.plotting.viz` that
create a PyVista window (or return a plotter for Jupyter) for a specific
result type.  All accept a ``notebook=True`` keyword for Jupyter, and
a ``selection`` keyword to restrict visible elements.

### 2a. 3D model view

```python
from fea_toolkit.plotting import plot_model_3d

plot_model_3d(
    builder,
    show_nodes=True,
    show_labels=False,
    color_by_section=True,
    selection=None,       # restrict to a Selection
    notebook=False,
)
```

### 2b. Deformed shape

```python
from fea_toolkit.plotting import plot_deformed_3d

plot_deformed_3d(
    builder,
    results,              # from builder.run_static_analysis()
    scale=10.0,
    show_original=True,   # grey undeformed overlay
    selection=None,
)
```

### 2c. RS deformed shape (CQC‑combined)

```python
from fea_toolkit.plotting import plot_rs_deformed_3d

disp = builder.compute_rs_nodal_displacements(...)
plot_rs_deformed_3d(builder, disp, scale=10.0)
```

Coloured by displacement magnitude (blue–white–red scale).

### 2d. Mode shape

```python
from fea_toolkit.plotting import plot_mode_3d

shapes = builder.extract_mode_shapes(num_modes=6)
plot_mode_3d(
    builder, shapes,
    mode=0,               # 0‑based mode index
    scale=10.0,
    animate=True,         # oscillating amplitude
    periods=modal["periods"],
)
```

### 2e. 3D force / moment diagram

```python
from fea_toolkit.plotting import plot_static_moment_3d

plot_static_moment_3d(
    builder,
    elem_forces,          # from builder.extract_static_element_forces()
    quantity="Mz",        # Mz, My, Mx, Fx, Fy, Fz
    mode="flag",          # "flag" or "tube"
    show_original=True,
    show_reactions=False,
    static_results=None,  # required for reactions
)
```

Convenience wrappers:

```python
from fea_toolkit.plotting import plot_static_shear_3d, plot_static_axial_3d

plot_static_shear_3d(builder, elem_forces, quantity="Fz")
plot_static_axial_3d(builder, elem_forces)
```

---

## 3. Standalone 2D plots (Matplotlib)

These produce publication‑quality 2D figures and return the
``matplotlib.figure.Figure`` so the caller can ``.savefig()`` or
``.show()``.

### 3a. Force / moment vs elevation

```python
from fea_toolkit.plotting import plot_static_force_diagram

fig = plot_static_force_diagram(
    builder,
    elem_forces,
    quantity="Mz",        # Fx, Fy, Fz, Mx, My, Mz
    use_local=True,       # local or global coordinates
    selection=None,
    figsize=(6, 8),
)
fig.savefig("moment.png")
```

For CQC‑combined results:

```python
from fea_toolkit.plotting import plot_force_diagram

rs_forces = builder.extract_element_rs_forces(...)
fig = plot_force_diagram(
    rs_forces["element_results"],
    quantity="My_i",
)
```

### 3b. Pushover capacity curve

```python
from fea_toolkit.plotting import plot_pushover_curve

fig = plot_pushover_curve(pushover_results)
```

Enhanced version with stiffness indicators and design drift marker:

```python
from fea_toolkit.plotting import plot_pushover_curve_enhanced

fig = plot_pushover_curve_enhanced(
    pushover_results,
    design_disp=0.15,     # optional vertical marker (m)
)
```

### 3c. Capacity spectrum (ADRS)

```python
from fea_toolkit.plotting import plot_capacity_spectrum

adrs = builder.pushover_to_adrs(...)
fig = plot_capacity_spectrum(
    adrs,
    spectrum_periods,      # from design spectrum
    spectrum_accels,
    performance_point=pp,  # optional, from compute_performance_point()
)
```

---

## 4. Interactive widget‑driven viewer

:func:`~fea_toolkit.plotting.interactive_viewer.plot_interactive_viewer`
opens a PyVista window with on‑screen controls for exploring results.

```python
from fea_toolkit.plotting import plot_interactive_viewer

plot_interactive_viewer(
    builder,
    combo_forces={
        "Dead":  forces_dead,
        "Live":  forces_live,
        "Wind":  forces_wind,
        "RS-X":  forces_rs_x,
    },
    combo_results={
        "Dead":  res_dead,
        "Live":  res_live,
        "Wind":  res_wind,
    },
    initial_combo="Dead",
    initial_quantity="Mz",
)
```

### Controls

| Widget | Location | Purpose |
|---|---|---|
| **Radio buttons** (x6) | Left side | Switch between Mz / My / Mx / Fz / Fy / Fx |
| **Text slider** | Top centre | Cycle through load combos (Dead → Live → Wind → …) |
| Checkbox 1 | Left (below radios) | Toggle centreline overlay |
| Checkbox 2 | Left | Toggle element labels (tag + section name) |
| Checkbox 3 | Left | Toggle reaction arrows (red = horizontal, green = vertical) |
| **Click on element** | — | Floating overlay: element tag, SAP ID, section, material |
| **Click on flag** | — | Floating overlay: numeric force/moment value with unit |

### Data flow

```python
# 1. Run static analyses
forces_dead = builder.extract_static_element_forces()
res_dead    = builder.run_static_analysis()

# 2. Change pattern scales, re-run
forces_wind = builder.extract_static_element_forces()
res_wind    = builder.run_static_analysis(pattern_scales={...})

# 3. Launch viewer
plot_interactive_viewer(
    builder,
    combo_forces={"Dead": forces_dead, "Wind": forces_wind},
    combo_results={"Dead": res_dead, "Wind": res_wind},
)
```

---

## 5. NPZ standalone plots

These functions load a ``.npz`` file created by
:meth:`~fea_toolkit.opensees.builder.OpenSeesBuilder.export_results_to_npz`
and produce plots **without** needing the original ``OpenSeesBuilder``
or model objects.

```python
from fea_toolkit.plotting import plot_npz_force_diagram, plot_npz_moment_3d

# 2D force vs elevation (Matplotlib)
fig = plot_npz_force_diagram(
    "results.npz",
    quantity="Mz",
    use_local=True,
    combo=None,           # combo prefix, or None for primary
)

# 3D force diagram (PyVista)
plot_npz_moment_3d(
    "results.npz",
    quantity="Mz",
    mode="flag",          # "flag" or "tube"
    use_local=True,
    combo=None,
)
```

These are particularly useful for:
- Generating views of results exported from a headless / batch run.
- Sharing visualisations with colleagues who don't have the original model.
- Quick post‑processing in a separate script.

---

## 6. LLM decision guide

When you are an AI assistant and the user asks about visualising the
structural model or results, use this guide to choose the right tool:

### "Show me the model"
→ ``ModelViewer(builder).show_model().show()``
Best for general inspection, getting a feel for the structure.

### "Show me the model with section colours"
→ ``plot_model_3d(builder, color_by_section=True)``
Uses the same colour palette as ``ModelViewer`` but with automatic isometric view.

### "Show me the deformed shape"
- **Static:** ``plot_deformed_3d(builder, results)`` or
  ``ModelViewer(builder).overlay_deformed()``
- **RS/CQC:** ``plot_rs_deformed_3d(builder, rs_displacements)``
- **Modal:** ``plot_mode_3d(builder, shapes, mode=0, animate=True)``

### "Show me the forces / moments"
- **3D flags on structure:** ``plot_static_moment_3d(builder, elem_forces, quantity="Mz")``
- **3D shear:** ``plot_static_shear_3d(builder, elem_forces)``
- **3D axial:** ``plot_static_axial_3d(builder, elem_forces)``
- **2D vs elevation (columns):** ``plot_static_force_diagram(builder, elem_forces, quantity="Mz")``
- **CQC‑combined 2D:** ``plot_force_diagram(rs_results["element_results"], quantity="My_i")``

### "Show me the pushover curve"
- **Basic:** ``plot_pushover_curve(pushover_results)``
- **Enhanced (stiffness, drift):** ``plot_pushover_curve_enhanced(pushover_results, design_disp=0.15)``

### "Show me the capacity spectrum"
→ ``plot_capacity_spectrum(adrs, periods, accels, performance_point=pp)``

### "I want to explore interactively"
→ ``plot_interactive_viewer(builder, combo_forces={...}, combo_results={...})``
Best for demos, exploration, and when the user wants to switch between
quantities / combos themselves.

### "I have a .npz file from an earlier run"
- **2D:** ``plot_npz_force_diagram("path.npz", quantity="Mz")``
- **3D:** ``plot_npz_moment_3d("path.npz", quantity="Mz")``

### "Highlight specific elements"
→ ``ModelViewer(builder).show_model().highlight_elements(
    frame_ids=["1","5"], label="Braces"
).show()``

### "Show me reactions"
→ ``plot_static_moment_3d(builder, forces, show_reactions=True,
    static_results=results)``

### "Export a view for sharing"
→ ``ModelViewer(builder).show_model().export_html("view.html")``
Saves a self‑contained interactive HTML file — viewable in any browser.

---

## Import summary

```python
# Backend‑agnostic viewer
from fea_toolkit.plotting import ModelViewer

# Standalone 3D plots (PyVista)
from fea_toolkit.plotting import (
    plot_model_3d,
    plot_deformed_3d,
    plot_rs_deformed_3d,
    plot_mode_3d,
    plot_static_moment_3d,
    plot_static_shear_3d,
    plot_static_axial_3d,
)

# Standalone 2D plots (Matplotlib)
from fea_toolkit.plotting import (
    plot_static_force_diagram,
    plot_force_diagram,
    plot_pushover_curve,
    plot_pushover_curve_enhanced,
    plot_capacity_spectrum,
)

# Interactive widget viewer (PyVista)
from fea_toolkit.plotting import plot_interactive_viewer

# NPZ standalone plots
from fea_toolkit.plotting import (
    plot_npz_force_diagram,
    plot_npz_moment_3d,
)
```
