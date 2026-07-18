# Builder Reference — ``OpenSeesBuilder``

General-purpose features of the ``OpenSeesBuilder`` class that are not
specific to pushover analysis.

---

## Two-stage build (``use_preprocessor``)

The builder supports two execution modes controlled by the
``use_preprocessor`` config flag (default ``False`` for backward
compatibility):

### Legacy single-stage (default)

``OpenSeesBuilder.build()`` bundles topology preparation and OpenSees
domain creation into a single call.  Every rebuild repeats splitting,
meshing, and constraint detection — see the detailed step list in
``docs/workflow.md``.

### Two-stage (``use_preprocessor: True``)

Splits the build into a **Preprocessor** (pure data, no ``ops.*``) and
an **AnalysisBuilder** (creates the OpenSees domain from a prepared
``MeshModel``).  The topology is prepared once and reused across
analyses.

```
SAPModelData ──→ Preprocessor ──→ MeshModel ──→ AnalysisBuilder ──→ Results
                    (once)         (frozen)       (per analysis)
```

| Component | File | Role |
|-----------|------|------|
| ``MeshModel`` | ``model/mesh_model.py`` | Frozen dataclass with fully prepared topology |
| ``Preprocessor`` | ``opensees/preprocessor.py`` | Topology mutations — splitting, meshing, subdividing, constraint detection |
| ``AnalysisBuilder`` | ``opensees/analysis_builder.py`` | OpenSees domain creation, load application, analysis execution |

Usage:

```python
b = OpenSeesBuilder(md, {"use_preprocessor": True, …})
b.build(selection=sel)        # → runs Preprocessor internally,
                              #   then builds Ops domain from MeshModel
```

The facade copies all state (``frame_tag_map``, ``section_tags``,
``material_tags``, ``split_elements``, etc.) back to the builder so
existing code that reads these attributes continues to work unchanged.

See ``docs/workflow.md`` for the full pipeline description and component
details.

---

## Shell element support

### Section type

Shell elements (``ShellMITC4``) are created with
``ops.section('ElasticMembranePlateSection', tag, E, nu, thickness)``.

> ⚠️ **Important:** ``ElasticPlateSection`` does **not** work correctly
> in OpenSeesPy 3.x — it produces a singular stiffness matrix even for
> a simple fixed‑edge plate with a centre point load.  Always use
> ``ElasticMembranePlateSection`` instead.  This is handled automatically
> by the builder since commit 988ef43.

### Shell edge constraints

When two shell meshes of different densities meet along a common edge,
the finer mesh introduces extra nodes that are not connected to the
coarser mesh.  In OpenSees these unconnected degrees of freedom make
the stiffness matrix singular.

The builder provides two methods to handle this.

---

### 1. Detecting unconnected edges

Call **after** :meth:`~fea_toolkit.opensees.builder.OpenSeesBuilder.build`:

```python
reports = builder.detect_unconnected_edges(tolerance=1e-3)
for r in reports:
    print(f"Node {r['slave_node']} on edge "
          f"{r['master_node_i']}–{r['master_node_j']}  "
          f"(N1={r['N1']:.3f}, N2={r['N2']:.3f})")
```

Each report entry contains:

| Field | Description |
|---|---|
| `slave_node` | Node ID of the unconnected fine-mesh node |
| `master_node_i`, `master_node_j` | The two corner nodes of the coarse edge |
| `master_coords_i`, `master_coords_j` | (x, y, z) coordinates of the master nodes |
| `coords` | (x, y, z) of the slave node |
| `N1`, `N2` | Linear interpolation weights (N1 + N2 = 1.0) |
| `edge_length` | Length of the coarse edge |
| `distance` | Perpendicular distance from the slave node to the edge |

---

### 2. Applying edge constraints

Use :meth:`~fea_toolkit.opensees.builder.OpenSeesBuilder.apply_edge_constraints`
**after** ``build()`` and **before** ``run_static_analysis()`` /
``run_pushover_analysis()``.

```python
builder.build()

# Option A — explicit edge and node lists
builder.apply_edge_constraints(
    coarse_edges=[(10, 11), (11, 12)],
    fine_nodes=[105, 106, 107],
)

# Option B — auto-extract edges from coarse shell elements
builder.apply_edge_constraints(coarse_elements=[1001, 1002, 1003])

# Option C — use detect_unconnected_edges output
reports = builder.detect_unconnected_edges()
master_edges = {(r["master_node_i"], r["master_node_j"])
                for r in reports}
slaves = [r["slave_node"] for r in reports]
builder.apply_edge_constraints(
    coarse_edges=list(master_edges),
    fine_nodes=slaves,
)

results = builder.run_static_analysis()   # automatically uses Penalty handler
```

---

### 3. Visualising discontinuities in Rhino

After detecting unconnected edges outside Rhino, save the reports to a
JSON file and load them in Rhino to draw red edge lines on a dedicated
layer.

**Step 1 — Outside Rhino** (in your analysis script):

```python
import json

# ... parse, build, detect ...
reports = builder.detect_unconnected_edges(tolerance=1e-3)

# Save for later use in Rhino
with open("unconnected_edges.json", "w") as f:
    json.dump(reports, f, indent=2)
```

**Step 2 — Inside Rhino** (in Rhino's Python editor):

```python
#! python 3
import sys
sys.path.append(r"/path/to/fea_toolkit/src")   # <-- adjust path

import json
from fea_toolkit.rhino import mark_unconnected_edges

# Load the detection results
with open("/path/to/unconnected_edges.json") as f:
    reports = json.load(f)

# Draw red lines and dots on a dedicated debug layer
mark_unconnected_edges(reports, mark_slave_nodes=True)
```

This creates a layer ``SAP2000/Debug/UnconnectedEdges`` with:
- **Red lines** — each coarse edge that has unconnected slave nodes
- **Red dots** — each slave node location (if ``mark_slave_nodes=True``)

The ``mark_unconnected_edges`` function uses the ``master_coords_i`` /
``master_coords_j`` fields from the detection report, so it works
without needing OpenSees installed in Rhino.

---

### 4. How it works

For each slave node that lies on a coarse master edge, the builder
creates a **multi-point constraint (MPC)** using OpenSees's
``equationConstraint`` command:

$$U_{\text{slave}} = N_1 \cdot U_{m1} + N_2 \cdot U_{m2}$$

where N₁, N₂ are the linear interpolation weights based on the slave
node's position along the edge.  This is the same approach SAP2000 uses
for its Auto Edge Constraint feature.

All six DOFs (Ux, Uy, Uz, Rx, Ry, Rz) are constrained.

The default ``"spring"`` method creates physical ``twoNodeLink`` elements
that are visible to every solver, so no constraint-handler change is
needed.  When ``constraint_method: "penalty"`` is used, the builder
automatically switches the constraint handler to **Penalty** (1.0e12, 1.0e12).

---

---

## Per-type stiffness factors (ACI 318 cracked-section simulation)

The ``stiffness_factors`` config option lets you apply different
Young's modulus reduction factors to different structural element
types, simulating cracked-section stiffness per **ACI 318-19
Table 6.6.3.1.1(a)**.

### Usage

```python
config = {
    'stiffness_factors': {
        'beam':   0.35,  # flexural members
        'column': 0.70,  # compression members
        'brace':  0.50,  # diagonal braces (no ACI guidance — conservative)
        'wall':   0.35,  # cracked structural walls (use 0.70 for uncracked)
        'slab':   0.25,  # two-way slabs
    },
}
```

Set to ``None`` (default) or an empty dict for gross (uncracked)
stiffness on all elements.

### What it does

1. **Classifies** every frame element as ``beam``, ``column``, or ``brace``
   based on its end-node geometry (see below).
2. **Classifies** every area element as ``slab`` or ``wall`` based on
   whether all corner nodes share the same Z coordinate.
3. **Creates separate OpenSees section definitions** for each
   ``(section_name, element_type)`` pair, with ``E_mod`` scaled by the
   type's factor.  This means the same SAP2000 section (e.g. ``400*500``)
   used for both beams and columns gets two different OpenSees section
   tags with different ``E_mod`` values.

   * For **frame sections**, the SAP2000 section modifiers (I2Mod, I3Mod,
     etc.) are applied **on top** of the scaled E_mod.
   * For **shell sections**, the factor is applied when creating the
     ``ElasticMembranePlateSection``.

### Interaction with SAP2000 stiffness modifiers

SAP2000 section-level modifiers (I3Mod, I2Mod, AMod, JMod) scale the
section's geometric properties **independently** of the material E_mod.
The two effects stack multiplicatively:

$$EI_{\text{effective}} = E_{\text{gross}} \times \underbrace{\text{ACI factor}}_{\text{stiffness\_factors}} \times I_{\text{gross}} \times \underbrace{\text{I3Mod}}_{\text{SAP2000 modifier}}$$

| Component | Source | Example |
|-----------|--------|---------|
| $E_{\text{gross}}$ | Material property (C30 → 30 GPa) | 30 GPa |
| ACI factor | ``stiffness_factors`` dict | 0.35 (beam) |
| $I_{\text{gross}}$ | Section property (400×500 → 4.17e9 mm⁴) | 4.17e9 |
| I3Mod | SAP2000 FRAME SECTION PROPERTIES 01 table | 0.50 |

For a concrete beam with I3Mod = 0.5 and ``beam: 0.35``:

$$EI_{\text{effective}} = 30\,\text{GPa} \times 0.35 \times I_{\text{gross}} \times 0.50
= 5.25\,\text{GPa} \cdot I_{\text{gross}}$$

Each OpenSees section variant is an independent ``ops.section()`` call
with its own tag — there is **no double-counting**.  The base section
(gross stiffness) and the variant (reduced stiffness) are separate
definitions; elements reference one or the other.

.. note::
   Scaling ``E_mod`` is a **broad stiffness approximation**, not a true
   implementation of ACI 318 cracked-section provisions.  ACI-style
   behaviour requires **component-specific section modifiers** that
   reduce flexural inertia (I3Mod, I2Mod) while retaining gross axial
   (AMod) and shear (JMod) areas.  The ``stiffness_factors`` dict
   applies a uniform E_mod reduction to all properties of the selected
   element type, which over‑softens axial and shear response.

**Material-type filtering** — the factor is applied **only** to
sections whose material type is ``'Concrete'``.  Steel, rebar, tendon,
and brick elements retain their gross stiffness regardless of the
``stiffness_factors`` dict.  This matches the intent of ACI 318
cracked-section provisions, which apply to reinforced concrete members
only.

### Classification rules

| Type | Criterion |
|------|-----------|
| **Column** | Frame where vertical span > 4× the resultant horizontal span: \\|Δz\\| > 4 · √(Δx² + Δy²) |
| **Brace** | Diagonal frame that is neither beam nor column (both Δh > 0.01 m and Δz > 0.01 m in model units) |
| **Beam** | Any other frame element |
| **Slab** | Area element whose *all* corner nodes lie within 0.02 m of the mean Z (model units) |
| **Wall** | Any other area element |

### Typical ACI 318-19 factors

| Type | Factor | Notes |
|------|--------|-------|
| Beams | 0.35 | Table 6.6.3.1.1(a) — beams |
| Columns | 0.70 | Table 6.6.3.1.1(a) — columns |
| Walls (cracked) | 0.35 | Table 6.6.3.1.1(a) — walls (cracked); some practitioners use 0.50 |
| Walls (uncracked) | 0.70 | Table 6.6.3.1.1(a) — walls (uncracked) |
| Slabs (two-way) | 0.25 | Table 6.6.3.1.1(a) — flat plates / slabs |

### 5. Solver requirements

The ``equationConstraint`` command requires the **Penalty** (or
**Lagrange**) constraint handler.  The builder handles this
automatically — after ``apply_edge_constraints()``, subsequent calls to
``run_static_analysis()`` or ``run_pushover_analysis()`` will use
``ops.constraints("Penalty", 1.0e12, 1.0e12)`` regardless of the
``solver_constraints`` config setting.

Do **not** set ``solver_constraints`` to ``"Transformation"`` when edge
constraints exist.

## Element splitting

The `geometry.split_elements()` function supports two independent auto-mesh
flags.  They can be enabled separately or together:

| `AtJoints` | `AtFrames` | Splits at |
|:----------:|:----------:|-----------|
| `True`     | `False`    | Existing joint nodes lying on the element |
| `False`    | `True`     | Frame-frame intersections only (new `split_n_*` nodes created) |
| `True`     | `True`     | Both — joints AND frame intersections |
| `False`    | `False`    | No splitting |

A third splitting mode — **storey-level splitting** (splitting elements at
identified storey elevations) — is logically independent and would be
controlled by a separate config option (e.g. `split_at_storeys`).  It is
not affected by the `AtJoints` / `AtFrames` flags.

### `AtJoints` — split at existing nodes

When enabled in the SAP2000 frame auto-mesh assignments, elements are split
wherever an existing node lies on the element line segment.  A spatial grid
is used to find intermediate nodes.  This flag does **not** create new nodes
— it only splits at nodes that already exist in the model.

### `AtFrames` — split at frame-frame intersections

When `AtFrames` is `True` in the frame auto-mesh assignments, elements are
split at **intersection points** with other frame elements, regardless of
whether a node already exists there.  A new `Node` is created at each
intersection.

**Process** (collect-first-then-split):
1. All elements with `AtFrames=True` are collected.
2. **Every pair** of AtFrames elements is tested for 3D line-segment
   intersection using `_segment_intersection_3d()`.
3. If the intersection is interior to **at least one** element (i.e. not at
   an endpoint for that element), a new `Node` with a unique `node_id` and
   `node_tag` is created at the intersection point.  The other element may
   have the intersection at its endpoint (T-junction) — no `t_location` is
   recorded for that element, but the node is still created so the crossing
   element can split.
4. The node is added to the model `nodes` dict and the spatial grid.  If
   either element needs splitting, both elements reference the **same**
   shared node.
5. The `t_locations` attribute of each crossing element records the parametric
   position.
6. During the main splitting pass, intermediate nodes are filtered:
   - If `AtJoints=True`: all intermediate nodes (joints + AtFrames) are accepted.
   - If `AtJoints=False`: only AtFrames-tracked nodes (newly created
     `split_n_*` nodes or reused joint nodes at intersection locations)
     are accepted — other existing joints are ignored.
7. The element is split at all accepted locations.

**  Pairing rule**: Only elements that both have `AtFrames=True` are paired.
If element *A* has `AtFrames=True` but intersecting element *B* has
`AtFrames=False`, the pair is **never checked** — no node is created and
neither element is split at that crossing.  Both must opt in for either
to be split.

**Node ID/tag management**: New nodes get string IDs like ``split_n_1``,
``split_n_2``, etc., avoiding conflicts with existing node IDs.  Each new
node also receives a unique numeric ``node_tag`` for OpenSees.  The
node‑reuse check searches all existing nodes by coordinate proximity
(within tolerance), not just ``split_n_*`` IDs — if an intersection
coincides with an existing joint node, that joint node's ID is tracked
for the splitting pass rather than creating a duplicate.  Both elements
then reference the same ID regardless of whether it was newly created or
reused.

**Builder integration**: In `OpenSeesBuilder._split_elements()`, after calling
`geometry.split_elements()`, any new nodes are registered in OpenSees via
`ops.node()` so subsequent element creation succeeds.

**Limitations**:
- Only coplanar line segments can intersect in 3D — skew lines (non-coplanar, non-parallel) are correctly ignored.
- Elements that already share a node are skipped (they are already connected).
- The split is performed once (not recursively) — all intersection points are collected before any splitting occurs, avoiding nested parent-child chains.
- AtFrames splitting does **not** trigger AtJoints splitting, and vice versa.
- If a storey-level split (future feature) creates a node at the same position
  as an AtFrames node, the two nodes are deduplicated by their parametric
  ``t`` value (within ``tol``).  Only one split occurs, preventing zero-length
  child elements.
- If `AtJoints=False` but a storey-level split node lies on an element, that
  node will **not** cause a split when `AtJoints=False` **unless** the
  storey-level filter is explicitly added to the intermediate node acceptance
  logic.  Each splitting criterion must be independently enabled.
- Near-identical ``t`` values from different sources (e.g. an AtFrames node
  at ``t=0.5`` and a storey node at ``t=0.5000001``) are merged into a single
  split point.  The deduplication uses ``abs(t_i - t_{i-1}) <= tol``.  Only
  the first node at each ``t`` is kept.

---

## Tcl Export for Nonlinear Analysis

### Background

Nonlinear RC analysis (fiber sections with ``Concrete01/02``,
``Steel02``, or ``forceBeamColumn`` with ``HingeRadau``) **does not
work in OpenSeesPy** builds.  Any analysis requiring nonlinear materials
must be exported to Tcl and run via the standalone OpenSees bundled with
Xara (``tclsh8.6``).

The builder provides two export paths:

| Path | Method | How it works |
|---|---|---|
| Recording | ``RecordingOpenSees`` proxy | Records all ``ops.*`` calls during a Python build, saves as Tcl. Only for **elastic** builds (nonlinear sections can't be created in Python). |
| Direct | ``export_model_to_tcl()`` | Translates ``SAPModelData`` directly to Tcl commands, skipping the Python build. Can inject nonlinear materials/sections via ``config``. |

### Direct Tcl export (recommended)

```python
from fea_toolkit.opensees.builder import OpenSeesBuilder

# Step 1: Define the nonlinear config
config = {
    "create_fiber_sections": True,        # emit Concrete01/Steel02 + fiber sections
    "geom_transf_type": "PDelta",         # or "Corotational" for braces
}

# Step 2: Generate analysis commands (tcl_suffix)
gravity_loads = {node_tag: (0, -weight, 0) for node_tag, weight in ...}
lateral_loads = {node_tag: (mode_force_x, 0, 0) for node_tag, ...}

tcl_suffix = OpenSeesBuilder.pushover_tcl(
    control_node=top_node_tag,
    dof=1,                                 # 1=X, 2=Y
    max_disp=0.15,
    lateral_loads=lateral_loads,
    gravity_loads=gravity_loads,
    adaptive=True,                         # auto-fallback algorithm chain
)

# Step 3: Export complete Tcl file
OpenSeesBuilder.export_model_to_tcl(
    md, "rc_pushover.tcl",
    config=config,
    tcl_suffix=tcl_suffix,
)

# Step 4: Run via Xara's standalone tclsh
from fea_toolkit.opensees.recorder import XaraTclRunner
runner = XaraTclRunner()
ret, output = runner.run("rc_pushover.tcl")
```

### What the Tcl export generates

With ``create_fiber_sections=True``, ``export_model_to_tcl()``
automatically appends:

1. **Nonlinear materials** — ``Concrete01`` (unconfined cover),
   ``Concrete01`` (confined core via ``eFc`` or 1.3×Fc), ``Steel02``
   (rebar), or ``Steel01`` (steel fiber sections).
2. **Fiber sections** — ``section Fiber`` with ``patch rect/circ/quad``
   and ``layer straight/circ`` commands from each section's
   ``to_fiber_patches()`` method, wrapped in brace-delimited blocks.
3. **Config-driven ``geomTransf``** — ``Linear``, ``PDelta``, or
   ``Corotational`` as specified in the config.

> **⚠️ Current limitations**
>
- **Section tag collision** — Nonlinear materials and fiber sections
   (``section Fiber`` with ``patch``/``layer`` commands) are emitted
   **before** the ``section Elastic`` block, matching
   ``export_model_to_tcl()`` and ``_tcl_materials_and_sections()``.
   However, mixed-type section groups (e.g. steel beams using Elastic +
   RC columns using fibre) share the same tag range.  Verify tags when
>   combining elastic and fibre sections in the same model.
>
> - **Element type** — ``export_model_to_tcl()`` emits
>   ``forceBeamColumn`` elements with ``beamIntegration Lobatto`` for
>   sections that have fibre patches, but only when
>   ``create_fiber_sections=True``.  All other elements still use
>   ``elasticBeamColumn``.  This mixed formulation is suitable for
>   pushover analysis where only RC members are expected to yield.
>
> These limitations are resolved incrementally in the builder code;
> check the ``_tcl_materials_and_sections()`` and the frame-element
> emission section of ``export_model_to_tcl()`` for the current
> behaviour before relying on the full nonlinear workflow.

### Pushover analysis Tcl

The ``pushover_tcl()`` static method returns a string suitable for
``tcl_suffix``:

```python
tcl_suffix = OpenSeesBuilder.pushover_tcl(
    control_node=42,        # node tag for displacement control
    dof=1,                  # X-direction
    max_disp=0.15,          # target displacement (m)
    num_steps=100,
    lateral_loads={
        5: (10000, 0, 0),   # node_tag: (Fx, Fy, Fz)
        6: (20000, 0, 0),
    },
    gravity_loads={          # optional — applied first, then locked
        5: (0, -50000, 0),
    },
    adaptive=True,           # Newton → KrylovNewton → ModifiedNewton fallback
)
```

The generated Tcl includes:

- **Gravity step** (if ``gravity_loads`` provided) — ramped over 10
  sub-steps, then ``loadConst -time 0.0`` to lock.
- **Lateral pushover** — ``DisplacementControl`` integrator with
  ``numberer RCM``, auto-fallback algorithm chain when not converging.

### Confinement from stirrup data

The ``model/confinement.py`` module implements the **Mander et al.
(1988)** model to compute confined concrete properties from transverse
reinforcement:

```python
from fea_toolkit.model.confinement import ConfinementData, mander_confined

data = ConfinementData(
    fc=32e6,                    # unconfined strength (Pa)
    tie_diameter=0.012,         # stirrup bar diameter (m)
    tie_spacing=0.150,          # centre-to-centre spacing (m)
    tie_fy=500e6,               # stirrup yield stress (Pa)
    overall_b=0.600,            # section width (m)
    overall_h=0.600,            # section depth (m)
    cover=0.040,                # clear cover (m)
    long_diameter=0.032,        # longitudinal bar diameter (m)
    long_count_x=3,             # bars along x
    long_count_y=3,             # bars along y
    tie_config="cross_tie",     # "standard" | "cross_tie" | "spiral"
)
result = mander_confined(data)

print(f"f'cc = {result.fcc/1e6:.1f} MPa")
print(f"εcc = {result.ecc:.4f}")
print(f"ecu = {result.ecu:.4f}")
print(f"ke  = {result.ke:.3f}")     # effective confinement coefficient
print(f"ρs  = {result.rho_s:.4f}")  # volumetric ratio
```

The result can be used to set ``Material.eFc`` and ``Material.extra['SCap']``
on the concrete material before export, or to verify the confinement
parameters read from SAP2000.

### Important notes

- **Nonlinear shell sections** are not yet supported in the Tcl export.
  Slabs and walls remain elastic (``ElasticMembranePlateSection``).
- **Modal pushover pattern**: Run modal analysis in Python (elastic),
  extract eigenvectors, compute ``load = mass × eigenvector``, and pass
  as ``lateral_loads`` to ``pushover_tcl()``.  Modal analysis is
  unaffected by the nonlinear material issue since it uses initial
  stiffness.
- **`numberer RCM`** is used in ``pushover_tcl()`` for better solver
  performance on large models.
