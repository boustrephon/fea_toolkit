---
title: "IMK (Bilin) Steel Hinge — Deterioration Model"
description: "The Ibarra–Medina–Krawinkler (IMK) deterioration model for steel moment frames: relationship to the ASCE 41 backbone and ETABS/SAP2000/Perform-3D, the OpenSees Bilin material, and the A0–A3 implementation plan and status."
status: "partial"
tags: [imk, ibarra-krawinkler, lignos-krawinkler, deterioration, collapse, hinge, bilin, steel, nonlinear-dynamic, moment-frame]
category: [planning]
related: [benchmark_roadmap.md, sac_nonlinear_benchmark.md, sac_trilinear_hinge_approaches.md, modeling_guidance.md]
---

# IMK (Bilin) Steel Hinge — Deterioration Model

⚠️ Partial — A1 machinery landed (2026-10-27); A0 calibration equations and
A2/A3 validation pending.

## 1. What it is

The **Ibarra–Medina–Krawinkler (IMK)** model is a *phenomenological*
(component-level, not fibre) concentrated-plasticity hinge with two parts:

1. **A monotonic backbone** — elastic (`My`, `θy`) → hardening to the
   **capping/peak** (`Mc`, reached after pre-capping plastic rotation `θp`) →
   **post-capping negative stiffness** (down to residual `Mr = κ·My`, after
   post-capping rotation `θpc`) → residual plateau.
2. **An energy-based cyclic-deterioration rule** keyed on a reference capacity
   **Λ** (`E_t = Λ·My`), which degrades three things as the member cycles:
   basic strength, post-capping strength, and unloading/reloading stiffness.

It replaces the SAC lineage's ad-hoc `Clough` hinge (trilinear `Hysteretic`
with β=0.5 unloading-stiffness degradation only — no strength loss, no
post-capping branch), which loses convergence on 4/10 strong SAC cases and
cannot represent collapse.

## 2. Relationship to the ASCE 41 backbone

ASCE 41 and IMK are the **same family** of phenomenological backbones — the
same curve, with different extras:

| ASCE 41 (FEMA 356) | IMK | Meaning |
|---|---|---|
| B | `My`, `θy` | yield |
| C (after `a`) | `Mc`, `θp` | capping/peak |
| C→D | `θpc` | negative-stiffness branch |
| D (`b`) | `Mr = κ·My` | residual |
| E | `θu` | collapse |
| `c` (hardening) | `as_pos`/`as_neg` | post-yield hardening |

The difference: **ASCE 41 is a monotonic backbone + acceptance criteria
(IO/LS/CP)** for pushover evaluation — it has **no cyclic-deterioration law**.
**IMK adds the energy-based deterioration**, which is what lets a nonlinear
time-history produce the strength/stiffness loss that defines collapse. The
Lignos–Krawinkler calibration is, in effect, a fully-deteriorating refinement
of the same component-test envelope ASCE 41 draws its Table 9-6/10-6
modelling parameters from.

## 3. ETABS / SAP2000 vs Perform-3D

- **ETABS / SAP2000 implement the ASCE 41 / FEMA 356 hinge** (auto-hinges,
  M3 for beams, P-M2-M3 coupled for columns) — **not** IMK. There is no
  "IMK" or "Lignos–Krawinkler" hinge type. One can approximate an IMK
  *envelope* by hand-entering the backbone points, but the full energy-based
  cyclic deterioration cannot be reproduced in the standard hinge dialog.
- **Perform-3D** implements an energy-based strength-loss model in the same
  spirit as IMK (collapse-oriented).
- **OpenSees** implements IMK natively — which is what this project targets.

## 4. The OpenSees material — `Bilin` (steel), not `ModIMKPeakOriented`

The modified IMK model has three OpenSees hysteretic variants, and the choice
matters:

| Material | Hysteresis | For |
|---|---|---|
| **`Bilin`** | bilinear | **steel** components (calibrated to >350 steel beam-column connections) |
| `ModIMKPeakOriented` / `IMKPeakOriented` | peak-oriented | **RC** flexural components |
| `ModIMKPinching` / `IMKPinching` | pinching | RC shear, connections, wood |

> **Correction to the roadmap wording:** "IMK" for the SAC *steel* frames means
> `Bilin`, not `ModIMKPeakOriented` (which is the RC-flexure variant). Verified
> against the OpenSeesPy documentation and Lignos' own `Modified_IMK_Models`
> README.

`Bilin` signature (verified):

```
uniaxialMaterial('Bilin', matTag, K0, as_pos, as_neg, My_pos, My_neg,
    Lamda_S, Lamda_C, Lamda_A, Lamda_K, c_S, c_C, c_A, c_K,
    theta_p_pos, theta_p_neg, theta_pc_pos, theta_pc_neg,
    Res_pos, Res_neg, theta_u_pos, theta_u_neg, D_pos, D_neg, nFactor=0.0)
```

`My_neg` is **negative**; `theta_p_neg`/`theta_pc_neg`/`Res_neg`/`theta_u_neg`
are **positive** magnitudes; `Lamda_A`/`c_A` (accelerated reloading) are not a
deterioration mode for a bilinear response (forced 0/1).

## 5. Implementation plan and status

From `benchmark_roadmap.md` §3 (priority 1):

| Phase | What | Status |
|---|---|---|
| **A0 — backbone data** | Lignos–Krawinkler equations (θp, θpc, Λ, My, Mr, κ) for RBS/non-RBS beams and columns | 🚧 scaffolding only |
| **A1 — material** | IMK (`Bilin`) uniaxial material behind `hinge_backbone="imk"` | ✅ landed |
| **A2 — single-member checks** | M-θ loops vs the component-database envelopes | ⏳ pending |
| **A3 — frame collapse** | SAC 3/9/20 matrices with IMK; IDA vs Ibarra–Krawinkler (2005) | ⏳ pending |

**A1 (landed).** `opensees/_elements.py` gained a `hinge_backbone="imk"` branch
in `_create_lumped_hinges` — a `Bilin` strong-axis hinge reading a stamped
`imk_params` dict off each section, with `K0 = stiff_factor × 6EI/L` (the
near-rigid lumped-hinge convention).  `benchmarks/sac.builder_config` accepts
`("imk", "lumped")`.  Tests: `tests/test_benchmark_sac_imk.py`.

**A0 (scaffolding only).** `benchmarks/imk_calibration.py` defines
`SteelIMKParams` (the parameter container, with `to_dict()`/`as_bilin_args()`)
and the `lignos_krawinkler_2011(...)` entry point — which currently raises
`NotImplementedError`, because the regression coefficients must be transcribed
from the primary source.

## 6. Calibration equations to transcribe

From Lignos & Krawinkler (2011), DOI `10.1061/(ASCE)ST.1943-541X.0000376`
(Table 2 / Eqs. 4–14), as functions of `h/tw` (clear web-depth ratio),
`bf/(2·tf)` (flange slenderness), `Lb/ry` (unbraced length / weak-axis radius
of gyration), `L/d` (member length / depth), `d` (depth), `Fy`, and `P/Pye`
(columns).  Unit factors: `c1_unit = 1` for `d` in mm (×25.4 if inches),
`c2_unit = 1` for `Fy` in MPa (×6.895 if ksi).

- **θp (pre-capping)** — RBS beams; other-than-RBS beams; columns.
- **θpc (post-capping)** — other-than-RBS (full set); other-than-RBS (d ≥ 533 mm); RBS.
- **Λ (cyclic deterioration)** — beams; columns.
- **`Mr = κ·My`** residual ratio and **`as`** hardening ratio conventions.

> The regression *signs* are unreliable in secondary OCR — transcribe from the
> paper (or the EPFL `resslabtools.epfl.ch/component` tool), do not guess.

## 7. References

- Ibarra, Medina & Krawinkler (2005), *Earthq. Eng. Struct. Dyn.* 34(12):1489–1511, DOI `10.1002/eqe.495`.
- Lignos & Krawinkler (2011), *J. Struct. Eng.* 137(11):1291–1302, DOI `10.1061/(ASCE)ST.1943-541X.0000376`.
- Ibarra & Krawinkler (2005), PEER Report 2005/06 (collapse methodology).
- OpenSeesPy `Bilin` documentation (`openseespydoc.readthedocs.io`).

