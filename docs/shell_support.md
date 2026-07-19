# Shell element support

Shell elements (``ShellMITC4``) are created with
``ops.section('ElasticMembranePlateSection', tag, E, nu, thickness)``.

> ⚠️ **Important:** ``ElasticPlateSection`` does **not** work correctly
> in OpenSeesPy 3.x — it produces a singular stiffness matrix.  Always use
> ``ElasticMembranePlateSection`` instead.  The builder handles this
> automatically.

---

## Shell edge constraints

When two shell meshes of different densities meet along a common edge,
the finer mesh introduces unconnected nodes causing a singular stiffness
matrix.  See ``docs/constraint_detection.md`` for the detection algorithm.

### Detecting unconnected edges

Call after ``builder.build()``:

```python
reports = builder.detect_unconnected_edges(tolerance=1e-3)
```

Each report contains the slave node, master edge endpoints, interpolation
weights N₁/N₂, edge length, and perpendicular distance.  Full field
descriptions are in the ``detect_unconnected_edges()`` docstring.

### Applying edge constraints

Call **after** ``build()`` and **before** analysis:

```python
builder.apply_edge_constraints(
    coarse_edges=[(10, 11), (11, 12)],
    fine_nodes=[105, 106, 107],
)
```

Three input modes are supported — see the ``apply_edge_constraints()``
docstring for ``coarse_edges``, ``coarse_elements``, and
``detect_unconnected_edges`` output variants.

## Wall‑slab intersection handling

When a wall (vertical area — Z‑span > 0.5) and a slab (horizontal area —
all Z within 0.5) intersect at the same storey level, the wall's edge
nodes land *inside* the slab's area but are not connected to the slab's
mesh.  This is a common source of singular stiffness matrices.

### Detection

``find_wall_nodes_inside_slabs()`` identifies wall nodes that lie within each
slab's XY bounding box and at the slab's Z level:

```python
from fea_toolkit.model.geometry import find_wall_nodes_inside_slabs

findings = find_wall_nodes_inside_slabs(
    area_elements, area_assignments, nodes,
)
```

Returns a list of findings, each containing the slab ID, wall ID, the
offending nodes and their coordinates, and the slab bounding box.

### Splitting

``split_slabs_at_wall_intersections()`` subdivides each affected slab along
the wall's intersection lines, creating new sub-areas that share nodes
with the wall:

```python
from fea_toolkit.model.geometry import split_slabs_at_wall_intersections

areas, assigns, nodes, next_tag = split_slabs_at_wall_intersections(
    area_elements, area_assignments, nodes,
    groups=groups,
)
```

The algorithm:
1. Groups findings by slab ID.
2. For each slab with wall nodes, computes parametric ``(u, v)``
   coordinates of the interior wall edge nodes.
3. Creates a bilinear grid of new sub-areas along the split lines.
4. Marks the original slab area as ``inactive`` and adds child sub-areas.
5. Propagates section assignments and group memberships to children.

### Controlling in the builder

Both detection and automatic splitting are controlled by builder config
flags:

```python
b = OpenSeesBuilder(md, {
    ...,
    detect_wall_slab_intersections=True,   # detect & report (default True)
    split_slabs_at_walls=True,              # auto-split (default False)
})
```

### Visualising discontinuities in Rhino

Save detection results to JSON outside Rhino, then load and render inside
Rhino using ``fea_toolkit.rhino.mark_unconnected_edges()``.
See ``docs/rhino_export.md`` for the full workflow.

### How MPC constraints work

For each slave node on a coarse master edge, a multi‑point constraint ties
all 6 DOFs:

$$U_{\text{slave}} = N_1 \cdot U_{m1} + N_2 \cdot U_{m2}$$

where N₁, N₂ are linear interpolation weights.  This matches SAP2000's
Auto Edge Constraint approach.  The default ``"spring"`` method creates
``twoNodeLink`` elements visible to all solvers; ``"penalty"`` switches
the constraint handler to Penalty automatically.
