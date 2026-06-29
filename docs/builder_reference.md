# Builder Reference — ``OpenSeesBuilder``

General-purpose features of the ``OpenSeesBuilder`` class that are not
specific to pushover analysis.

---

## Shell edge constraints

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
