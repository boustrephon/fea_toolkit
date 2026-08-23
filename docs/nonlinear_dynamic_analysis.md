---
title: "Nonlinear Dynamic (Time-History) Analysis"
description: "Ground-motion-driven transient analysis via the complete Tcl export + Xara/OpenSeesRT path (with Rayleigh damping from a preceding modal analysis). Additional Python-native integration schemes remain planned."
status: "complete"
tags: [analysis-type, nonlinear, dynamic, time-history, ground-motion, tcl, xara]
category: [analysis-types]
related: [modal_analysis.md, tcl_export.md, xara_tcl_runtime_guide.md, analysis.md]
---

# Nonlinear Dynamic (Time-History) Analysis

The :func:`~fea_toolkit.analysis.nonlinear_dynamic.run_nonlinear_dynamic_analysis`
function runs a ground-motion-driven transient analysis through the **Tcl export +
Xara/OpenSeesRT** path. It requires the result of a preceding
:func:`~fea_toolkit.analysis.modal.run_modal_analysis` to supply periods for
Rayleigh damping.

## Usage

```python
from fea_toolkit.analysis import run_modal_analysis, run_nonlinear_dynamic_analysis

modal = run_modal_analysis(mesh_model, n_modes=6)
result = run_nonlinear_dynamic_analysis(
    mesh_model,
    modal_result=modal,
    ground_motion_file="gm_accel.txt",
    dt=0.005,
    num_steps=1000,
    direction="X",
    damping_ratio=0.05,
)
```

## Parameters

| Parameter | Default | Description |
|---|---|---|
| `ground_motion_file` | required | Path to acceleration record - one value per line, no header. Values must be authored in **model acceleration units** (length-unit per s²); the Tcl path applies them with `-factor 1.0` and performs **no g-division**. For metre models, SI m/s² values are used directly (×1.0); for millimetre models (e.g. kN-mm), multiply SI m/s² values by 1000 to obtain mm/s². |
| `dt` | `0.005` | Time step of the record (s) |
| `num_steps` | `1000` | Number of analysis steps |
| `direction` | `"X"` | Excitation direction (`"X"`, `"Y"`, `"Z"`) |
| `damping_ratio` | `0.05` | Rayleigh damping ratio |
| `modal_result` | required | Result of a preceding `run_modal_analysis()` (supplies periods for Rayleigh damping) |

## Result keys

All result keys below live under `result.data` (the `AnalysisResult`
contract; see `docs/analysis.md` for the typed container):

| Key | Type | Description |
|---|---|---|
| `times` | `ndarray` | Time vector |
| `displacements` | `ndarray` | Nodal displacement time histories (from recorder) |
| `envelope` | `ndarray` | Envelope displacement output (if recorded) |
| `peak_displacement` | `float` | Max absolute nodal displacement across nodes (not inter-storey drift) |
| `converged_steps` | `int` | Steps converged by the solver |
| `gm_file` | `str` | Original ground motion path |
| `direction` | `str` | Excitation direction |
| `output_raw` | `str` | Raw XaraTclRunner output |

On a non-zero runner exit status the result is returned with `converged_steps=0`,
`times=np.array([])` (an empty `ndarray`, preserving the container type declared
above), and an **`error`** entry under `result.metadata` (not `result.data`).
In this failure path `peak_displacement` is set to `0.0` as a **runner-failure
sentinel** — it is **not** a measured value.  Consumers must check
`result.metadata["error"]` before interpreting `peak_displacement`.  On a
successful run `peak_displacement` represents the **maximum absolute nodal
displacement**, not inter-storey drift.

## How it works

1. Gravity loads are derived from sections/materials via `mesh_model_to_gravity_loads()`.
2. Rayleigh damping periods come from the preceding modal analysis (first and last mode; single-mode and fallback cases are handled).
3. The model is exported to Tcl via `export_mesh_model_to_tcl()` with `dynamic_time_history_tcl()` appended.
4. Execution uses `XaraTclRunner`.
5. Output files (`dyn_disp.out`, `dyn_env_disp.out`) are parsed; `peak_displacement` is derived from the displacement recorder.

## Notes

- Requires **Xara/OpenSeesRT** for Tcl execution - see [Xara/OpenSeesRT Tcl Runtime Guide](xara_tcl_runtime_guide.md).
- Composed via `AnalysisManager` for dependency injection; can also run standalone with an explicit `modal_result`.
- `config` overrides are applied on top of class defaults, with `create_fiber_sections=True` forced for nonlinear material response.
