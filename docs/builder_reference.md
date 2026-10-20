---
title: "Builder Reference — Two-stage Pipeline"
description: "Reference for the Preprocessor + AnalysisBuilder two-stage architecture."
status: "complete"
tags: [architecture, preprocessor, analysis-builder, reference]
category: [core-pipeline]
related: [workflow.md, layered_analysis_workflow.md, element_classification.md]
---
# Builder Reference — Two-stage Pipeline

The two-stage pipeline is the standard way to create an OpenSees model
from parsed SAP2000 data.

```
SAPModelData ──→ Preprocessor ──→ MeshModel ──→ AnalysisBuilder ──→ Results
                    (once)         (frozen)       (per analysis)
```

| Component | File | Role |
|-----------|------|------|
| ``MeshModel`` | ``model/mesh_model.py`` | Frozen dataclass with fully prepared topology + declarative modelling specs |
| ``Preprocessor`` | ``opensees/preprocessor.py`` | Topology mutations — no ``ops.*`` calls |
| ``AnalysisBuilder`` | ``opensees/analysis_builder.py`` | OpenSees domain creation + analysis — read-only with respect to the ``MeshModel`` |

## Data-flow contract (one-way)

```
SAPModelData ──▶ Preprocessor ──▶ MeshModel (frozen) ──▶ AnalysisBuilder ──▶ OpenSees
  (source)     (only topology    (topology + declarative  (read-only:      (domain)
                mutations)         modelling specs)         one-way emission)
```

1. **`SAPModelData` is the shared, never-mutated source.**  The `Preprocessor`
   operates on a copy (``run()`` never touches the original).
2. **The `Preprocessor` is the only thing that changes connectivity.**  Its
   output `MeshModel` is a frozen snapshot of **one** prepared model — the
   connectivity graph plus the declarative modelling specs the builder needs:
   element roles (`frame_element_types`), rigid links (`offset_rigid_links`),
   constraint groups (`rigid_body_components` / `diaphragm_components`), wall
   macro-elements (`wall_elements`) and panel-zone springs (`panel_zones`).
3. **The `AnalysisBuilder` is a one-way function `MeshModel → OpenSees domain`.**
   It *reads* the frozen mesh, *selects* which configured modelling approach to
   emit, and *creates* OpenSees objects.  It **never writes back** — no adding
   nodes, no re-pointing elements, no ``_restore_*`` undo.  A modelling choice
   that changes topology (panel zones, hinges, bond-slip springs, member
   releases) is computed by the Preprocessor and stored as a `MeshModel` field;
   the builder only selects and emits.

### When two analyses share a `MeshModel`

The sharing point is decided by one question: **does the variant change the
connectivity graph** (which node an element connects to, or which
nodes/elements exist)?

| Variant pair | Changes connectivity? | Sharing point |
|---|---|---|
| Two meshing densities | Yes | Different `MeshModel`, share `SAPModelData` |
| Linear static vs nonlinear dynamic (element/solver only) | No | Same `MeshModel`, different builder config |
| Linear static vs nonlinear dynamic (with hinges/panel zones) | Yes | Different `MeshModel` |
| Diaphragm applied vs not | No (a constraint, not topology) | Same `MeshModel`, builder toggle |
| Two beam-column joint models (centreline vs K&G) | Yes | Different `MeshModel` |
| Two wall macro-element families | Yes | Different `MeshModel` |

In short: **share the `MeshModel` exactly when the two variants differ only in
build-level choices** (element type, sections, loads, solver, constraint
application); **share only the `SAPModelData` when they differ in topology**
(meshing, joint models, wall families, hinges, releases).  When in doubt,
separate the `MeshModel`s — an accidentally-shared mesh that a builder then
mutates is worse than a redundant preprocess run.

Usage:

```python
from fea_toolkit.opensees.preprocessor import preprocess_model
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

mm = preprocess_model(model_data)
builder = AnalysisBuilder(mm, config)
builder.build_domain()
builder.create_loads({"DEAD": 1.0})
results = builder.run_static_analysis()
```

The legacy ``OpenSeesBuilder`` class has been removed.  Brace
subdivision is now handled by :class:`~fea_toolkit.opensees.preprocessor.Preprocessor`
(performed **once** before the builder is created).  Lumped hinges,
Tcl export, and buckling checks are available directly on
:class:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder`
or as standalone functions.

---

## Feature topics

| Topic | Document | Description |
|-------|----------|-------------|
| Shell support | ``docs/shell_support.md`` | Shell elements, edge constraint detection, MPC application, Rhino visualisation |
| Stiffness factors | ``docs/stiffness_factors.md`` | ACI 318 cracked-section factors, classification rules, SAP2000 modifier interaction |
| Element splitting | ``docs/element_splitting.md`` | AtJoints / AtFrames splitting, builder integration, limitations |
| Tcl export | ``docs/tcl_export.md`` | Nonlinear analysis Tcl export, pushover Tcl, confinement data, limitations |
| Constraint detection | ``docs/constraint_detection.md`` | Edge constraint algorithm and detection logic |
| Rhino export | ``docs/rhino_export.md`` | Rhino visualisation module, layer structure, metadata |
| Workflow | ``docs/workflow.md`` | End-to-end pipeline from .s2k to results |
