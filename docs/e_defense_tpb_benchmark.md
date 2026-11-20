---
title: "E-Defense Base-Isolated Frame (TPB) Benchmark (T4)"
description: "Full-model shake-table benchmark: E-Defense 5-storey base-isolated steel frame with triple friction pendulum bearings (TPB) — bearing backbone, first-slope period, and JMA-Kobe NLTHA."
status: "complete"
tags: [validation, benchmark, base-isolation, TPB, NLTHA, shake-table]
category: [planning]
related: [benchmark_register.md, nonlinear_link_plan.md, modeling_guidance.md, e_defense_lrb_benchmark.md]
---

# E-Defense base-isolated frame (TPB) benchmark

**Status: ✅ Complete** — `tests/test_e_defense_tpb.py` reproduces the published
triple-friction-pendulum bearing backbone and first-slope period of the 2011
NEES/E-Defense full-scale shake-table test building (the TPB configuration).

## Reference

- Dao, N.D., Ryan, K.L. (2015). *Seismic Response of a Full-scale 5-story Steel
  Frame Building Isolated by Triple Pendulum Bearings under 3D Excitations.*
  CCEER 15-01, University of Nevada, Reno.
- Dao, N.D., Ryan, K.L., Sato, E., Sasaki, T. (2013). *Predicting the
  displacement of triple pendulum bearings in a full-scale shaking experiment
  using a three-dimensional element.* Earthquake Engng Struct. Dyn. 42(11).
  DOI 10.1002/eqe.2293.

## Bearing design parameters (CCEER 15-01 Table 3-8)

Nine identical EPS triple friction pendulum bearings under the 3×3 column grid
(`config="tpb"`); length in m.

| Stage | μ | Effective radius `L = R − h` | Slider limit `d` |
|---|---|---|---|
| 1 (inner) | 0.02 | 0.419 | 0.051 |
| 2 (outer) | 0.08 | 3.848 | 0.514 |
| 3 (outer) | 0.08 | 3.848 | 0.514 |

- Total displacement capacity **1.13 m** at a base-shear coefficient of
  **0.275** (F/W).
- First-slope (inner-slider) period **1.84 s** (`Leff1 = 2·L1 = 0.838 m`).

## Element

OpenSees' `TripleFrictionPendulum` element (Dao et al. 2013) — **not**
`tripleFPBearing`, which is an unknown element type in OpenSeesPy (verified
against the installed library).  The element takes `L` (effective radii),
`Ubar` (slider limits), three `frictionModel` objects (`Coulomb` here, the
design coefficients) and four *positional* uniaxial materials; it ignores
`-orient` and orients its axial axis along I→J.

## Validation targets

| Quantity | Published | Test |
|---|---|---|
| Backbone base-shear | 0.275 W at 1.13 m | `test_tpb_backbone_capacity` |
| First-slope period | 1.84 s (isolator) | `test_tpb_isolated_period` (1.7–2.2 s) |
| Peak isolator disp. | 38.36 cm (100 % Iwanuma, Table 5-2) | `test_tpb_nltha_kobe` (stand-in sanity check) |
