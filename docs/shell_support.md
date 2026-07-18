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
