---
title: "Analysis Helpers"
description: "The fea_toolkit.analysis subpackage: module-level analysis functions returning typed AnalysisResult containers, composed explicitly by the caller."
status: "complete"
tags: [analysis, orchestration, typed-results, architecture]
category: [core-pipeline]
related: [builder_reference.md, workflow.md, nonlinear_dynamic_analysis.md]
---

# Analysis Helpers (`fea_toolkit.analysis`)

The `fea_toolkit.analysis` subpackage provides **module-level functions** that
run the toolkit's analyses and return a typed `AnalysisResult` container.
There is no dependency-graph manager: the caller composes the steps in an
explicit, readable order (see `generate_report()` for the reference pipeline).

## Core containers (`base.py`)

- `AnalysisCaseSpec` — declarative case specification.
- `AnalysisResult` — typed result container: `name`, `analysis_type`, `data`, `metadata`.

## Analysis functions

| Function | Module | Requires | Returns (`data`) |
|---|---|---|---|
| `run_modal_analysis(mesh_model, n_modes=12, ...)` | `modal.py` | — | Periods, frequencies, mass participation, mode shapes |
| `run_static_analysis(mesh_model, md, spec_cfg, linear_cfg, ...)` | `static.py` | — | `df_linear` (displacements, reactions, element forces) |
| `run_response_spectrum_analysis(mesh_model, modal_result, direction, T_spec, Sa_spec, ...)` | `rs.py` | `run_modal_analysis` | CQC-combined RS results + per-mode `modal_base_shear` |
| `run_pushover_analysis(mesh_model, modal_result, material_type, ...)` | `pushover.py` | `run_modal_analysis` | Capacity curve, ADRS, performance point |
| `run_nonlinear_dynamic_analysis(mesh_model, modal_result, ground_motion_file, ...)` | `nonlinear_dynamic.py` | `run_modal_analysis` | Time histories, envelope, peak drift |

Only the response-spectrum, pushover, and nonlinear-dynamic functions depend
on a preceding modal result — that dependency is expressed as a
`modal_result` argument, not as an injected dependency graph.

## Relationship to the pipeline

```
SAP2000Parser → SAPModelData → Preprocessor → MeshModel → analysis functions → results
                                    (drives AnalysisBuilder)
```

The functions consume a frozen `MeshModel` (Preprocessor output) — they never
mutate topology.  Modal / static / RS / pushover run via `AnalysisBuilder`
(or `fea_toolkit.analysis.linear.run_linear_cases` for the static linear table); the
nonlinear-dynamic function uses the Tcl export + `XaraTclRunner` backend.

## Example — explicit pipeline

```python
from fea_toolkit.analysis import (
    run_modal_analysis,
    run_response_spectrum_analysis,
    run_pushover_analysis,
)

modal = run_modal_analysis(mesh_model, n_modes=6)
rs_x = run_response_spectrum_analysis(
    mesh_model,
    modal_result=modal,
    direction="X",
    T_spec=[0.05, 1.0, 2.0],
    Sa_spec=[2.0, 0.4, 0.1],
)
push = run_pushover_analysis(mesh_model, modal_result=modal, lateral_load_type="mode1")
```

See [Nonlinear Dynamic (Time-History) Analysis](nonlinear_dynamic_analysis.md)
for the time-history runner.
