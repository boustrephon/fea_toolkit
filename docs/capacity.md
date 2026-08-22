---
title: "Code-Specific Member Capacity Modules"
description: "Naming convention for statutory-code capacity modules plus the GB 50010-2010 implementation (flexural, axial, shear and wall checks). The module-per-code layout allows additional codes (ASCE 41, Eurocode 2/8, ACI 318, ...) to be added without name clashes."
status: "complete"
tags: [capacity, gb50010, asce41, design, dcr, codes, unit-aware]
category: [model-features]
related: [shear_failure_modelling.md, pushover_analysis.md, workflow.md]
---
# Code-Specific Member Capacity Modules

## Overview

The toolkit hosts statutory building-code capacity and utilisation (DCR)
functions under a single namespace: `fea_toolkit.capacity`.  One module per
code keeps the formulas traceable to their clauses and lets codes coexist
without name clashes.

## Naming convention

* **Module name = the code identifier** (lowercase, no punctuation):
  `gb50010.py`, `gb50011.py`, `asce41.py`, `en1992.py`, `en1998.py`,
  `aci318.py`, `nzs3101.py`, ...
* **Function name = the structural action**, not the code — the module
  already scopes it:

  ```python
  from fea_toolkit.capacity.gb50010 import moment_capacity, shear_capacity
  from fea_toolkit.capacity.asce41 import moment_capacity, shear_capacity
  ```

  `gb50010.moment_capacity(...)` and `asce41.moment_capacity(...)` coexist
  with zero ambiguity, and adding a new code is just a new file.

Shared plumbing lives in `fea_toolkit.capacity._common`:
`CapacityResult` (dataclass), `capacity_dcr()` (demand/capacity ratio),
`safe_float()` and unit-label helpers.

## Unit convention

Material strengths are authored in **SI (Pa)** and converted to the model's
unit system with `fea_toolkit.utils.stress_scale_factor`; section dimensions
and demands are in model units.  Capacities are returned in the model's force
(or force × length) units — the same system as the analysis results they are
compared against.

```python
from fea_toolkit.capacity.gb50010 import moment_capacity

result = moment_capacity(section, concrete_mat, steel_mat, md.units)
print(result.value, result.units)          # e.g. "472.1 kN·m"
print(result.governing_clause)            # e.g. "GB 50010 §6.2.10"
dcr = capacity_dcr(demand_moment, result.value)
```

## GB 50010-2010 (`fea_toolkit.capacity.gb50010`)

| Function | Clause | Returns |
|---|---|---|
| `moment_capacity(section, concrete, steel, units, ...)` | §6.2.1–6.2.10 (under-reinforced rectangular; elastic `fy·Z33` fallback) | `CapacityResult` (force × length) |
| `axial_capacity(section, concrete, steel, units, *, rho=0.006)` | §6.2.15 | `CapacityResult` (force) |
| `shear_capacity(section, concrete, steel, units, ...)` | §6.3.4 + §6.3.1 strut cap | `CapacityResult` (force) |
| `wall_shear_check(Nxy, Ny, t, concrete, units, ...)` | §6.3.1 / §7.3 | `WallShearCheckResult` |

Formulas mirror the validated `admin_pushover_checks_v8.py` workflow but are
unit-aware; the concrete tensile strength `f_t` defaults to the §4.1.3-style
estimate `0.26·f_c^(2/3)` and can be overridden with `ft_pa=` for a specific
grade.

## ASCE 41-17 (`fea_toolkit.capacity.asce41`)

| Function | Clause | Returns |
|---|---|---|
| `hinge_length(md, sec_name, elem_length)` | §10.8 (Eqs. 10-1 / 10-2, ACI 440-style RC) | `float` (model length units) |

Migrated verbatim from `model.checks.compute_asce41_hinge_length`; the legacy
name is retained in `model/checks.py` as a delegating wrapper.  The formula
converts the model's material strengths (model stress → Pa → MPa) and section
dimensions (model length → m/mm) internally via `stress_to_si_factor` /
`length_to_si_factor`, and returns `L_p` in model length units, capped at
`0.33·L`.  It keeps the model-data-coupled `(md, sec_name, elem_length)`
signature for behaviour-preserving migration; aligning it to the unit-aware
`(section, concrete, steel, units, ...)` form is a documented follow-up.

## Adding a new code

1. Create `src/fea_toolkit/capacity/<code>.py` (e.g. `asce41.py`).
2. Import `CapacityResult` / `capacity_dcr` / `safe_float` from `._common`.
3. Name functions by structural action (`moment_capacity`, `shear_capacity`,
   `acceptance_criteria`, ...).
4. Add `tests/test_capacity_<code>.py` (no OpenSees needed).
5. Register the public names in `src/fea_toolkit/capacity/__init__.py`,
   add `docs/capacity.md` + `docs/api/capacity.md` and a nav entry in
   `mkdocs.yml`.

## Relationship to other modules

* `capacity/shear_capacity.py` — simplified-**MCFT** shear model (CSA/ACI,
  not code-specific); complementary to the code-specified formulas here.
* `capacity/elwood_limit_state.py` — Elwood (1994/2002) limit-state model.
* `model/checks.py::compute_asce41_hinge_length` — legacy alias delegating to
  `capacity.asce41.hinge_length` (migrated verbatim; kept for existing callers
  per the deprecation rules).
* `spectrum.py::_gb50011_spectrum` / `_iec_spectrum` — seismic spectrum
  generators; a future `capacity/gb50011.py` may reference them.

## Status

✅ Complete — `gb50010` module (flexure/axial/shear/wall) and `asce41`
`hinge_length` (migrated from `model.checks`); `CapacityResult`, `capacity_dcr`,
unit-matrix tests (`tests/test_capacity_gb50010.py`, `tests/test_capacity_asce41.py`).
🚧 Future — `asce41` `acceptance_criteria` (deformation-controlled rotation
limits), `en1992`, ... follow the same module-per-code layout.
