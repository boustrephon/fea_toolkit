# Model Viewer — 3D visualisation & discussion tool

The :class:`~fea_toolkit.plotting.viewer.ModelViewer` provides a
backend-agnostic 3D viewer for structural models and analysis results.
It is designed for both interactive exploration and **LLM-assisted
discussions** — you (or an AI assistant) can call it to display a model,
overlay results, highlight problem areas, and annotate specific elements.

---

## Quick start

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

---

## API reference

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

All display/overlay methods return ``self`` for chaining:

```python
viewer.show_model().highlight_elements(["1"], label="Issue").show()
```

---

## Use cases for discussion

### 1. Diagnose a singular stiffness matrix

```python
issues = builder.diagnose_singularity()
problem_tags = [i["node_tag"] for i in issues if i["n_elements"] == 0]

# Find the node IDs
problem_ids = [nid for nid, nd in md.nodes.items()
               if nd.node_tag in problem_tags]

viewer = ModelViewer(builder)
viewer.show_model(show_nodes=True)
viewer.highlight_nodes(problem_ids, color=(1, 0, 0), label="Orphan")
viewer.show()
```

### 2. Show buckled braces after pushover

```python
buckling = builder.check_brace_buckling(braces)
if buckling["buckled"]:
    viewer = ModelViewer(builder)
    viewer.show_model()
    viewer.highlight_elements(
        buckling["buckled_ids"],
        color=(1, 0, 0),
        label=f"{len(buckling['buckled_ids'])} buckled",
    )
    viewer.show()
```

### 3. Self-weight consistency failure — highlight affected sections

```python
report = builder.check_self_weight_consistency()
if not report["passed"]:
    viewer = ModelViewer(builder)
    viewer.show_model(color_by_section=True)
    for sec, info in report["by_section"].items():
        if abs(info["diff"]) > 0.01 * info["expected"]:
            ids = [f.elem_id for f in viewer._frames
                   if f.section == sec]
            viewer.highlight_elements(ids, label=f"{sec}: {info['diff']:.0f}")
    viewer.show()
```

### 4. Compare mode shapes

```python
modal = builder.run_modal_analysis(num_modes=3)
for m in range(min(3, modal["num_modes"])):
    shapes = builder.extract_mode_shapes(num_modes=3)
    viewer = ModelViewer(builder)
    viewer.show_model(show_nodes=False)
    viewer.overlay_deformed(displacements=shapes[m], scale=30,
                            color=(0.3, 0.6, 1.0))
    viewer.screenshot(f"mode_{m}.png")
```

### 5. Share a discussion view

```python
viewer = ModelViewer(builder)
viewer.show_model()
viewer.highlight_elements(ids, label="Check these")
viewer.annotate("Max moment", node_id="47")
viewer.export_html("discussion_view.html")   # send to colleagues
```

---

## Backend architecture

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

## LLM instructions

When you are an AI assistant helping a user discuss a structural model:

1. **Import the viewer** from ``fea_toolkit.plotting import ModelViewer``.
2. **Create a viewer** from the builder or model data.
3. **Show the model** with ``viewer.show_model()``.
4. **Overlay results** if available (deformed shape, force flags).
5. **Highlight elements** under discussion using their frame/area/node IDs.
6. **Annotate** specific locations with explanatory text.
7. **Export** a screenshot or HTML for sharing.

All methods return ``self``, so calls can be chained:

```python
ModelViewer(builder).show_model().highlight_elements(
    ["1"], label="Issue"
).annotate("Check this", node_id="2").show()
```
