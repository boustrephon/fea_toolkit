# Builder Reference — ``OpenSeesBuilder``

General-purpose features of the ``OpenSeesBuilder`` class that are not
specific to pushover analysis.

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

The analysis methods automatically switch the constraint handler to
**Penalty** (1.0e12, 1.0e12) when edge constraints are present — no
manual config change is needed.

---

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
3. If the intersection is interior to both elements (not at an endpoint), a
   new `Node` with a unique `node_id` and `node_tag` is created at the
   intersection point.
4. The node is added to the model `nodes` dict and the spatial grid — both
   elements now reference the **same** shared node.
5. The `t_locations` attribute of each crossing element records the parametric
   position.
6. During the main splitting pass, intermediate nodes are filtered:
   - If `AtJoints=True`: all intermediate nodes (joints + AtFrames) are accepted.
   - If `AtJoints=False`: only AtFrames-created nodes (`split_n_*` IDs) are
     accepted — existing joints are ignored.
7. The element is split at all accepted locations.

**  Pairing rule**: Only elements that both have `AtFrames=True` are paired.
If element *A* has `AtFrames=True` but intersecting element *B* has
`AtFrames=False`, the pair is **never checked** — no node is created and
neither element is split at that crossing.  Both must opt in for either
to be split.

**Node ID/tag management**: New nodes get IDs like `split_n_1`, `split_n_2`,
etc. — auto-incrementing beyond any existing node IDs.  Tags start from
`max(existing_tag) + 1`.

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
