# Builder Reference — ``OpenSeesBuilder``

General-purpose features of the ``OpenSeesBuilder`` class that are not
specific to pushover analysis.

---

## Two-stage build (``use_preprocessor``)

The builder supports two execution modes controlled by the
``use_preprocessor`` config flag (default ``False`` for backward
compatibility).  See ``docs/workflow.md`` for the full pipeline
description.

```
SAPModelData ──→ Preprocessor ──→ MeshModel ──→ AnalysisBuilder ──→ Results
                    (once)         (frozen)       (per analysis)
```

| Component | File | Role |
|-----------|------|------|
| ``MeshModel`` | ``model/mesh_model.py`` | Frozen dataclass with fully prepared topology |
| ``Preprocessor`` | ``opensees/preprocessor.py`` | Topology mutations — no ``ops.*`` calls |
| ``AnalysisBuilder`` | ``opensees/analysis_builder.py`` | OpenSees domain creation + analysis |

Usage:

```python
b = OpenSeesBuilder(md, {"use_preprocessor": True, …})
b.build(selection=sel)        # runs Preprocessor → AnalysisBuilder
```

The facade copies state (``frame_tag_map``, ``section_tags``,
``material_tags``, ``split_elements``, etc.) back to the builder so
existing code reading these attributes continues to work unchanged.

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
