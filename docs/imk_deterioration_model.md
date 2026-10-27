---
title: "IMK (IMKBilin) Steel Hinge — Deterioration Model"
description: "The Ibarra–Medina–Krawinkler (IMK) deterioration model for steel moment frames: relationship to the ASCE 41 backbone and ETABS/SAP2000/Perform-3D, the OpenSees IMKBilin material, and the A0–A3 implementation plan and status."
status: "partial"
tags: [imk, ibarra-krawinkler, lignos-krawinkler, deterioration, collapse, hinge, imkbilin, bilin, steel, nonlinear-dynamic, moment-frame]
category: [planning]
related: [benchmark_roadmap.md, sac_nonlinear_benchmark.md, sac_trilinear_hinge_approaches.md, modeling_guidance.md]
---

# IMK (IMKBilin) Steel Hinge — Deterioration Model

⚠️ Partial — A0/A1/A2 landed; A3 (frame collapse) in progress — first increment
`enrich_imk` + 3-story `backbone="imk"` (2026-10-27).

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

## 4. The OpenSees material — `IMKBilin` (steel), not `ModIMKPeakOriented`

> **⚠️ `Bilin` is deprecated — do not use it.**  OpenSees emits
> ``WARNING: DO NOT USE THE "Bilin" MATERIAL, IT HAS BEEN REPLACED. Use "IMKBilin"
> or "HystereticSM" INSTEAD.``  The current material is **`IMKBilin`** (a
> rewrite of the same IMK-bilinear model, tagged ``IMK with Bilinear Response``
> in `IMKBilin.cpp`); the old `Bilin` spelling is a warning-emitting stub whose
> argument list no longer matches the old documentation.  **Always target
> `IMKBilin`** with the 21-argument signature below — never `Bilin`.

The modified IMK model has three OpenSees hysteretic variants, and the choice
matters:

| Material | Hysteresis | For |
|---|---|---|
| **`IMKBilin`** (alias `Bilin`) | bilinear | **steel** components (calibrated to >350 steel beam-column connections) |
| `ModIMKPeakOriented` / `IMKPeakOriented` | peak-oriented | **RC** flexural components |
| `ModIMKPinching` / `IMKPinching` | pinching | RC shear, connections, wood |

> **Correction to the roadmap wording:** "IMK" for the SAC *steel* frames means
> `IMKBilin` (the deprecated alias `Bilin`), not `ModIMKPeakOriented` (the
> RC-flexure variant).  Verified against the OpenSees source (`IMKBilin.cpp`)
> and Lignos' own `Modified_IMK_Models` README.

`IMKBilin` signature (verified from the OpenSees source `IMKBilin.cpp`):

```
uniaxialMaterial IMKBilin matTag Ke dp_pos dpc_pos du_pos Fy_pos FmaxFy_pos FresFy_pos
    dp_neg dpc_neg du_neg Fy_neg FmaxFy_neg FresFy_neg
    Lamda_S Lamda_C Lamda_K c_S c_C c_K D_pos D_neg
```

All strengths and deformations are **positive** magnitudes; `FmaxFy` = capping
ratio `Mc/My` (`mc_my`), `FresFy` = residual ratio `Mr/My` (`res`).  Two source
conventions the hinge layer absorbs: the post-capping stiffness is
`K_pc = −Fcap/dpc` (not `(Fres−Fcap)/θpc`), so the calibrated `θpc` is mapped to
`dpc = mc_my·θpc/(mc_my−res)`; and `θu = θp + θpc + 0.02` (a 0.02 rad residual
plateau before the strength drops to zero at `du`).

## 5. Implementation plan and status

From `benchmark_roadmap.md` §3 (priority 1):

| Phase | What | Status |
|---|---|---|
| **A0 — backbone data** | Beams: Lignos–Krawinkler (2011) θp/θpc/Λ; Columns: Lignos et al. (2019) My/a*/θp/θpc/Mr | ✅ |
| **A1 — material** | IMK (`IMKBilin`) uniaxial material behind `hinge_backbone="imk"` | ✅ landed |
| **A2 — single-member checks** | M-θ loops vs the component-database envelopes | ✅ landed (material-level) |
| **A3 — frame collapse** | SAC 3/9/20 matrices with IMK; IDA vs Ibarra–Krawinkler (2005) | 🚧 in progress |

**A1 (landed).** `opensees/_elements.py` gained a `hinge_backbone="imk"` branch
in `_create_lumped_hinges` — a `IMKBilin` strong-axis hinge reading a stamped
`imk_params` dict off each section, with `K0 = stiff_factor × 6EI/L` (the
near-rigid lumped-hinge convention).  `benchmarks/sac.builder_config` accepts
`("imk", "lumped")`.  Tests: `tests/test_benchmark_sac_imk.py`.

**A0 (landed).** `benchmarks/imk_calibration.py` defines `SteelIMKParams` and
`lignos_krawinkler_2011(...)`, which computes the full `IMKBilin` parameter set
for **beams** (RBS and other-than-RBS, split at `d = 533 mm`; Lignos–Krawinkler
2011) and **columns** (wide-flange; Lignos et al. 2019 / ASCE 41-17).  The
column cyclic-deterioration `Λ` is not in the 2019 paper (first-cycle envelope
only), so `lamda_*` is left at 0 for columns.

**A3 (in progress).** The frame-collapse path.  First increment landed
(2026-10-27): `benchmarks/sac.enrich_imk()` stamps per-section `imk_params` from
each member's *physical* AISC geometry (read via
`fea_toolkit.model.steel_sections.get_section_properties`, with
`ry = sqrt(I22/A)`), wired into `load_model(..., backbone="imk")` for the
3-story model.  Columns use Lignos et al. (2019) with `column_axial_ratio`
(`Pg/Pye`); beams use Lignos–Krawinkler (2011); the leaning column stays elastic.
Tests: `tests/test_benchmark_sac_imk_frame.py`.

Open A3 items, in build order:

1. **Column axial ratio.**  `enrich_imk` takes `column_axial_ratio` (default 0 —
   pure flexure, the no-axial-load envelope).  Compute the per-column `Pg/Pye`
   from a gravity run of the elastic model (tributary + member self-weight) so
   the columns carry their real axial-load reduction.
2. **Beam unbraced length.**  `beam_unbraced_factor` defaults to 1.0 (`Lb = L`,
   no intermediate bracing).  The composite-slab bracing assumption (typically
   `Lb ≈ L/2` for the bottom flange in negative moment) is a calibration choice;
   sweep it against the benchmark response.
3. **Column cyclic-deterioration Λ.**  `lamda_*` is 0 for columns (the 2019 paper
   is first-cycle only).  Add the Lignos & Krawinkler (2011, Table 3) column Λ.
4. **NLTHA convergence.**  Run the 3-story SAC ground-motion suite
   (`SAC_DT_CAL = 0.005 s`) with `hinge_backbone="imk"` and confirm the IMKBilin
   hinges converge under the strong records where the `Clough` trilinear hinge
   failed.
5. **9/20-story matrices.**  Wire `enrich_imk` for building 9/20 (per-member
   unbraced lengths from the varying story heights; column splices).
6. **IDA vs Ibarra–Krawinkler (2005).**  Incremental dynamic analysis over the
   benchmark records; compare the median collapse capacity (`S_a(T1)`) against
   Ibarra–Medina–Krawinkler (2005).

## 6. Calibration equations

### Beams (transcribed)

Transcribed verbatim from Lignos' EPFL component tool
(`resslabtools.epfl.ch/component/`) and cross-checked against PEER/ATC-72-1
§3.2.2 (Eqs. 3-1–3-6).  Variables: `h/tw = (d − 2tf)/tw`, `bf/(2tf)`,
`Lb/ry`, `L/(2d)` (the tool's double-curvature member-length term), `d` in mm,
`Fy` in MPa.

| | θp (pre-capping) | θpc (post-capping) | Λ (cyclic deterioration) |
|---|---|---|---|
| **RBS** | `0.19 (h/tw)^-0.314 (bf/2tf)^-0.1 (Lb/ry)^-0.185 (L/2d)^0.113 (d/533)^-0.76 (Fy/355)^-0.07` | `9.52 (h/tw)^-0.513 (bf/2tf)^-0.863 (Lb/ry)^-0.108 (Fy/355)^-0.36` | `585 (h/tw)^-1.14 (bf/2tf)^-0.632 (Lb/ry)^-0.205 (Fy/355)^-0.391` |
| **non-RBS, d < 533 mm** | `0.0865 (h/tw)^-0.365 (bf/2tf)^-0.14 (L/2d)^0.34 (d/533)^-0.721 (Fy/355)^-0.23` | `5.63 (h/tw)^-0.565 (bf/2tf)^-0.8 (d/533)^-0.28 (Fy/355)^-0.43` | `495 (h/tw)^-1.34 (bf/2tf)^-0.595 (Fy/355)^-0.36` |
| **non-RBS, d ≥ 533 mm** | `0.318 (h/tw)^-0.55 (bf/2tf)^-0.345 (Lb/ry)^-0.023 (L/2d)^0.09 (d/533)^-0.33 (Fy/355)^-0.13` | `7.5 (h/tw)^-0.61 (bf/2tf)^-0.71 (Lb/ry)^-0.11 (d/533)^-0.161 (Fy/355)^-0.32` | `536 (h/tw)^-1.26 (bf/2tf)^-0.525 (Lb/ry)^-0.13 (Fy/355)^-0.291` |

`My = Zx·Fy` (nominal; the PEER 1.1 expected-strength factor is a caller
option), `Mc = mc_my·My` (default 1.05), `Mr = κ·My` (default κ = 0.4),
`θu = θp + θpc + 0.02` (default).  The hardening ratio `as = (mc_my − 1)·My/(K0·θp)`
is derived in the hinge machinery so `Mc` is reproduced exactly regardless of
the `K0` convention.

### Columns (transcribed — Lignos et al. 2019 / ASCE 41-17)

Wide-flange steel columns, from Lignos, Hartloper, Elkady, Deierlein &
Hamburger (2019), DOI `10.1061/(ASCE)ST.1943-541X.0002353` (Eqs. 6–10; adopted
verbatim in the ASCE/SEI 41-17 Errata).  Variables: `h/tw = (d − 2tf)/tw`,
`Lb/ry`, `Pg/Pye` (gravity axial load / expected axial yield).

| Parameter | Equation |
|---|---|
| yield moment `My*` | `1.15·Z·Ry·Fy·(1 − Pg/Pye)` if `Pg/Pye ≤ 0.20`; `1.15·Z·Ry·Fy·(9/8)(1 − Pg/Pye)` otherwise |
| capping ratio `a* = Mc/My` | `9.5 (h/tw)^-0.4 (Lb/ry)^-0.16 (1 − Pg/Pye)^0.2 ≥ 1.0` |
| pre-capping `θp` | `15 (h/tw)^-1.6 (Lb/ry)^-0.3 (1 − Pg/Pye)^2.3 ≤ 0.10` |
| post-capping `θpc` | `14 (h/tw)^-0.8 (Lb/ry)^-0.5 (1 − Pg/Pye)^3.2 ≤ 0.10` |
| residual `Mr` | `(0.4 − 0.4·Pg/Pye)·My*` |

`Ry` = AISC expected-yield ratio (1.1 for A992/A572-50).  These are the
*first-cycle envelope* parameters; the column cyclic-deterioration `Λ` (for the
monotonic `IMKBilin` backbone) is not in this paper — it lives in Lignos &
Krawinkler (2011, Table 3), so `lamda_* = 0` for columns.

## 7. References

- Ibarra, Medina & Krawinkler (2005), *Earthq. Eng. Struct. Dyn.* 34(12):1489–1511, DOI `10.1002/eqe.495`.
- Lignos & Krawinkler (2011), *J. Struct. Eng.* 137(11):1291–1302, DOI `10.1061/(ASCE)ST.1943-541X.0000376`.
- Ibarra & Krawinkler (2005), PEER Report 2005/06 (collapse methodology).
- OpenSees `IMKBilin` material (`IMKBilin.cpp`; the deprecated alias is `Bilin`).

