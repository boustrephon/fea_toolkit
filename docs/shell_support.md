---
title: "Shell Element Support"
description: "Shell element types, meshing strategies, and layered shell support for nonlinear wall analysis."
status: "complete"
tags: [shell, area-elements, meshing, elements]
category: [model-features]
related: [constraint_detection.md, element_classification.md, element_properties_config.md, builder_reference.md]
---
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
b = AnalysisBuilder(mm, {
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

---

## Rigid diaphragms

Rigid diaphragms tie the in‑plane (X‑Y) translations of a storey's nodes to
a common plane motion via a master node.  The builder emits one
``ops.rigidDiaphragm(3, master, *slaves)`` constraint per diaphragm group.

### Sources

The Preprocessor populates ``mesh_model.diaphragm_levels`` and
``mesh_model.diaphragm_components`` from several sources, selected by the
``rigid_diaphragms`` config value:

| Config value | Behaviour |
|---|---|
| absent / ``None`` | Apply **only** explicit S2K Z‑axis DIAPHRAGM constraint groups.  Horizontal area‑element mean‑Z levels are detected but **never** auto‑applied — shell elements already provide in‑plane stiffness (bisect ``1cf374d``) |
| ``False`` | Explicitly **disable** all rigid diaphragms, even when the model declares its own constraints |
| ``True`` | Explicitly **create** rigid diaphragms — force storey‑based detection via ``identify_stories()`` (one component per identified storey, skipping S2K constraints); falls back to per‑elevation slab levels only when no components were detected |
| ``[z1, z2, ...]`` | Legacy override — use the explicit elevations and merge all nodes within the configurable ``diaphragm_z_tolerance`` (default ``0.01``) of each level into one diaphragm |
| ``[{name, nodes\|selection}, ...]`` | **Explicit named groups** — bypass all detection; each dict is one independent diaphragm |

### Explicit named groups

Each group dict must contain either a ``nodes`` or a ``selection`` key
(omitting both raises ``ValueError``):

```python
config = {
    "rigid_diaphragms": [
        {"name": "Tower A core", "nodes": ["101", "102", "103", "104"]},
        {"name": "Tower A slabs",
         "selection": {"element_types": ["Area"], "sections": ["Slab 200"]}},
    ],
}
```

- ``name`` — optional identifier, used only for verbose diagnostics.
- ``nodes`` — explicit SAP2000 joint ID list, filtered to nodes that survive
  preprocessing.
- ``selection`` — a selector dict with the usual
  :class:`~fea_toolkit.model.selection.Selection` keys (``element_types``,
  ``sections``, ``materials``, ``groups``, ``element_ids``) or an actual
  ``Selection`` instance:

  ```python
  from fea_toolkit.model.selection import Selection

  config = {
      "rigid_diaphragms": [
          {"name": "slabs", "selection": Selection(
              element_types=["Area"], sections=["Slab 200"])},
      ],
  }
  ```

  Matching **area** elements contribute their vertex nodes; matching
  **frame** elements contribute both end nodes.  The union (deduplicated,
  first‑seen order) becomes the group.

Each group produces **one independent ``rigidDiaphragm``**.  Groups at the
same elevation are **not** merged — this preserves separate wings or cores
separated by a seismic gap.  A group's elevation is taken as the mean Z of
its resolved nodes.

### Grouping behaviour

- **S2K joint constraints** — each Z‑axis ``DIAPHRAGM`` constraint in
  ``CONSTRAINT DEFINITIONS - DIAPHRAGM`` + ``JOINT CONSTRAINT ASSIGNMENTS``
  becomes one component, preserving the S2K constraint grouping.
- **Area-derived levels** — when no explicit constraints exist and
  ``rigid_diaphragms`` is absent, the Preprocessor records only mean‑Z
  levels (no components) and the builder applies **no** rigid diaphragms
  (shell elements already provide in‑plane stiffness).  Slab levels are
  applied only with an explicit ``rigid_diaphragms: True`` (or a legacy
  ``[z1, z2, ...]`` list): all nodes near each level form a single
  diaphragm.
- **Storey detection (``True``)** — each ``StoryLevel`` from
  ``identify_stories()`` becomes one component.