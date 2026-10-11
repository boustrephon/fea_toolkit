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

### Load–reaction summary (`analysis_load_reaction_summary`)

A **pandas-free** summary of applied loads vs base reactions across every
analysis, in one table.  Unlike the pandas-backed `static_load_verification()`
(static patterns only), it also folds in the response-spectrum CQC base shear
and overturning moment, and returns a plain `list[dict]` — one row per
analysis — instead of a `DataFrame`, so it runs without the optional `pandas`
dependency (e.g. inside Rhino 8's embedded CPython).

```python
from fea_toolkit.analysis import analysis_load_reaction_summary

rows = analysis_load_reaction_summary(
    md,
    mesh_model,
    rs_results={"X": rs_x, "Y": rs_y},   # optional — RS base shear/moment rows
    total_weight=W,                       # optional — powers Expected V
    seismic_coefficient=alpha,            # optional — α = Sa(T1) / g
)
```

Each row carries unit-labelled keys: `Analysis`, `Type`
(`Static` / `Response Spectrum`), `Applied Fx/Fy/Fz`, `Reaction Fx/Fy/Fz`,
`Δ Fx/Fy/Fz` (signed applied + reaction, ~0 when equilibrated), and
`Reaction Mx/My/Mz` (with overturning).  RS rows add `Base shear V`,
`Base moment M`, and — when `total_weight` and `seismic_coefficient` are both
supplied — `Expected V = W × α` and `V ratio = |V| / Expected V`.  Cells that
don't apply to a row's analysis type are `None`.

The static rows reproduce `static_load_verification()`'s equilibrium check
(build the domain once, apply every pattern, solve per pattern); RS rows read
`base_reactions_cqc`, `base_shear_cqc` and `base_moment_cqc` from each
direction's result of
`AnalysisBuilder.run_response_spectrum_analysis()`.  The function never
hardcodes a force unit — labels come from `md.units`, and the expected-shear
inputs are supplied by the caller (who derives them from the model mass and
spectrum via `g_from_units()`).

### Spectrum units

`T_spec` / `Sa_spec` are **in the model's own unit system** — the toolkit is
unit-agnostic (see [Units](units_conversion.md)).  When a spectrum is built
from a design code rather than supplied explicitly, `spectrum._build_spectrum()`
takes a `g` argument for exactly this reason:

```python
from fea_toolkit.spectrum import _build_spectrum
from fea_toolkit.utils import g_from_units

T_spec, Sa_spec, *_ = _build_spectrum(spec_cfg, g=g_from_units(md.units))
```

`g_from_units()` scales 9.80665 m/s² to the model's length unit, so a
millimetre model gets `Sa` in mm/s².  `_build_spectrum()` also accepts the
model's `units` dict directly, so callers need not import `g_from_units`
themselves — the two forms are equivalent:

```python
T_spec, Sa_spec, *_ = _build_spectrum(spec_cfg, units=md.units)
```

`ResponseSpectrum.from_gb50011()` and `plot_seismic_spectrum()` accept the
same `g` / `units` pair, which keeps the plotted ordinates in the model's
unit system too.  The report pipeline, the review's `--response-spectrum`
pass and `run_linear_cases()` all derive `g` from the model;
omitting both `g` and `units` falls back to the shared SI gravity constant
(`fea_toolkit.utils.DEFAULT_GRAVITY_MS2`, 9.80665 m/s²) for standalone use.
Precedence is explicit `g` → `units` → SI constant, and an explicit `g` is
used verbatim (never re-derived).

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
