---
title: "Typed Analysis Orchestration"
description: "The fea_toolkit.analysis subpackage: typed, configurable, dependency-aware analysis objects composed via AnalysisManager."
status: "complete"
tags: [analysis, manager, orchestration, typed-results, architecture]
category: [core-pipeline]
related: [builder_reference.md, workflow.md, nonlinear_dynamic_analysis.md]
---

# Typed Analysis Orchestration (`fea_toolkit.analysis`)

The `fea_toolkit.analysis` subpackage provides a typed, dependency-aware layer above OpenSees domain construction. Each analysis type is a self-contained `Analysis` subclass that owns its configuration, knows its dependencies, and returns a typed `AnalysisResult`. Analyses are composed via `AnalysisManager`, which handles topological ordering and result passing.

## Core abstractions (`base.py`)

- `Analysis` (ABC) — base class: `run()`, `requires()`, `provides()`, `defaults()`.
- `AnalysisCaseSpec` — declarative case specification.
- `AnalysisResult` — typed result container: `name`, `analysis_type`, `data`, `metadata`.

## AnalysisManager (`manager.py`)

```python
from fea_toolkit.analysis import AnalysisManager, ModalAnalysis, ResponseSpectrumAnalysis

manager = AnalysisManager(mesh_model)
manager.add(ModalAnalysis(mesh_model, num_modes=6))
manager.add(ResponseSpectrumAnalysis(mesh_model))
results = manager.run_all()
```

Methods: `add()` / `run_all()` / `run_one()` / `_inject_dependencies()` / `_topological_sort()`.

## Analysis types

| Class | Module | Requires | Provides |
|---|---|---|---|
| `StaticAnalysis` | `static.py` | — | Displacements, reactions, element forces |
| `ModalAnalysis` | `modal.py` | — | Periods, frequencies, mass participation, mode shapes |
| `ResponseSpectrumAnalysis` | `rs.py` | `ModalAnalysis` | CQC-combined RS results |
| `PushoverAnalysis` | `pushover.py` | — | Capacity curve, ADRS, performance point |
| `NonlinearDynamicAnalysis` | `nonlinear_dynamic.py` | `ModalAnalysis` | Time histories, envelope, peak drift |

## Relationship to the pipeline

```
SAP2000Parser → SAPModelData → Preprocessor → MeshModel → AnalysisManager → results
                                    (drives AnalysisBuilder)
```

`AnalysisManager` consumes a frozen `MeshModel` (Preprocessor output) — it never mutates topology. It dispatches each analysis implementation (modal, static, RS, pushover, NLD) to its type-specific runner, wraps raw dict results in typed `AnalysisResult` objects, and — for builder-backed analyses (e.g. modal, static, RS) — the runner creates and configures an `AnalysisBuilder` internally for domain creation and execution.

See [Nonlinear Dynamic (Time-History) Analysis](nonlinear_dynamic_analysis.md) for the time-history runner.