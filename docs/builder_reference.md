# Builder Reference — Two-stage Pipeline

The two-stage pipeline is the standard way to create an OpenSees model
from parsed SAP2000 data.

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
from fea_toolkit.opensees.preprocessor import preprocess_model
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

mm = preprocess_model(model_data)
builder = AnalysisBuilder(mm, config)
builder.build_domain()
builder.create_loads({"DEAD": 1.0})
results = builder.run_static_analysis()
```

The legacy ``OpenSeesBuilder`` class has been removed.  All features
(brace subdivision, lumped hinges, Tcl export, buckling checks) are
available directly on ``AnalysisBuilder`` or as standalone functions.

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
