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
| `c` (hardening) | `mc_my` (`FmaxFy`) | post-yield hardening (capping ratio) |

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
`dpc = mc_my·θpc/(mc_my−res)`; and the ultimate rotation `θu` is chosen by a
named `theta_u_model` convention (default `"plateau"` = `θp + θpc + 0.02`) —
see §6 "The ultimate rotation θu".

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
2011) and **columns** (wide-flange; Lignos et al. 2019 / ASCE 41-17), including
the column cyclic-deterioration `Λ` (Eq. 13: `Λs` basic-strength deterioration
and `Λc = Λk = 0.9·Λs`).

**A3 (in progress).** The frame-collapse path.  First increment landed
(2026-10-27): `benchmarks/sac.enrich_imk()` stamps per-section `imk_params` from
each member's *physical* AISC geometry (read via
`fea_toolkit.model.steel_sections.get_section_properties`, with
`ry = sqrt(I22/A)`), wired into `load_model(..., backbone="imk")` for the
3-story model.  Columns use Lignos et al. (2019) with `column_axial_ratio`
(`Pg/Pye`); beams use Lignos–Krawinkler (2011); the leaning column stays elastic.
Tests: `tests/test_benchmark_sac_imk_frame.py`.

Second increment landed (2026-10-28): the column axial ratio (item 1) is now
derived from the FEMA 355C seismic weight via `sac.derive_column_axial_ratios()`,
with `sac.gravity_axial_ratios()` as the full-elastic-solve cross-check
(agreement ≈ 0.15 %).

Open A3 items, in build order:

1. **Column axial ratio.**  ✅ **Landed 2026-10-28** — the benchmark collapses
   "seismic mass" and "gravity load" into one quantity (Lignos 2008, Stanford
   TR177 §5; the FEMA 355C dead + reduced-live + steel-self-weight seismic
   weight, already lumped as `added_masses`), so `Pg/Pye` is derived from that
   weight rather than a separate gravity load.  `sac.derive_column_axial_ratios()`
   computes the per-section `Pg = g·Σ(tributary mass)` / `Pye = A·Ry·Fy` (Ry = 1.1),
   and `sac.gravity_axial_ratios()` is the independent full elastic gravity solve
   that cross-checks it (agreement ≈ 0.15 %).  `enrich_imk(..., column_axial_ratios=…)`
   applies them per section; the 3-story governing ratios are W14x257 ≈ 0.195
   (gravity-bay line, full tributary bay) and W14x311 ≈ 0.162, both below the
   0.20 ASCE 41 force-controlled threshold.
2. **Beam unbraced length.**  `beam_unbraced_factor` defaults to 1.0 (`Lb = L`,
   no intermediate bracing).  The composite-slab bracing assumption (typically
   `Lb ≈ L/2` for the bottom flange in negative moment) is a calibration choice;
   sweep it against the benchmark response.
3. **Column cyclic-deterioration Λ.**  ✅ Landed — `lignos_2019_wide_flange_column`
   transcribes Lignos et al. (2019) Eq. 13 (`Λs` basic-strength deterioration,
   split at `Pg/Pye = 0.35`, capped at 3.0 rad; `Λc = Λk = 0.9·Λs`).  Remaining:
   validate the `≤ 3.0` cap and `0.9×` factor against the column test hysteresis.
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
§3.2.2 (Eqs. 3-1–3-6).  Variables: $h/t_w = (d - 2t_f)/t_w$, $b_f/(2t_f)$,
$L_b/r_y$, $L/(2d)$ (the tool's double-curvature member-length term), $d$ in mm,
$F_y$ in MPa.

**RBS**

$$\theta_p = 0.19\,(h/t_w)^{-0.314}\,(b_f/2t_f)^{-0.1}\,(L_b/r_y)^{-0.185}\,(L/2d)^{0.113}\,(d/533)^{-0.76}\,(F_y/355)^{-0.07}$$

$$\theta_{pc} = 9.52\,(h/t_w)^{-0.513}\,(b_f/2t_f)^{-0.863}\,(L_b/r_y)^{-0.108}\,(F_y/355)^{-0.36}$$

$$\Lambda = 585\,(h/t_w)^{-1.14}\,(b_f/2t_f)^{-0.632}\,(L_b/r_y)^{-0.205}\,(F_y/355)^{-0.391}$$

**non-RBS, d < 533 mm**

$$\theta_p = 0.0865\,(h/t_w)^{-0.365}\,(b_f/2t_f)^{-0.14}\,(L/2d)^{0.34}\,(d/533)^{-0.721}\,(F_y/355)^{-0.23}$$

$$\theta_{pc} = 5.63\,(h/t_w)^{-0.565}\,(b_f/2t_f)^{-0.8}\,(d/533)^{-0.28}\,(F_y/355)^{-0.43}$$

$$\Lambda = 495\,(h/t_w)^{-1.34}\,(b_f/2t_f)^{-0.595}\,(F_y/355)^{-0.36}$$

**non-RBS, d ≥ 533 mm**

$$\theta_p = 0.318\,(h/t_w)^{-0.55}\,(b_f/2t_f)^{-0.345}\,(L_b/r_y)^{-0.023}\,(L/2d)^{0.09}\,(d/533)^{-0.33}\,(F_y/355)^{-0.13}$$

$$\theta_{pc} = 7.5\,(h/t_w)^{-0.61}\,(b_f/2t_f)^{-0.71}\,(L_b/r_y)^{-0.11}\,(d/533)^{-0.161}\,(F_y/355)^{-0.32}$$

$$\Lambda = 536\,(h/t_w)^{-1.26}\,(b_f/2t_f)^{-0.525}\,(L_b/r_y)^{-0.13}\,(F_y/355)^{-0.291}$$

$M_y = Z_x F_y$ (nominal; the PEER 1.1 expected-strength factor is a caller
option), $M_c = a M_y$ (default $a = 1.05$), $M_r = \kappa M_y$ (default
$\kappa = 0.4$), $\theta_u$ from a `theta_u_model` convention (default
`"plateau"` $= \theta_p + \theta_{pc} + 0.02$).
`mc_my` maps directly to `IMKBilin`'s `FmaxFy` (capping ratio) and
`κ`/`res` to `FresFy` (residual ratio); there is no derived hardening ratio
`as` — the post-yield hardening is folded into the capping point
$(\theta_p, M_c)$.

### Columns (transcribed — Lignos et al. 2019)

Wide-flange steel columns, from Lignos, Hartloper, Elkady, Deierlein &
Hamburger (2019), DOI `10.1061/(ASCE)ST.1943-541X.0002353`.  Variables:
$h/t_w = (d - 2t_f)/t_w$, $L_b/r_y$, $P_g/P_{ye}$ (gravity axial load /
expected axial yield).

The paper publishes **two** backbone curves per cross-section, distinguished by
a superscript asterisk:

* the **first-cycle envelope** (`*`) — the curve traced by the *peak* response
  of a column under a symmetric reversed-cyclic loading protocol, used for
  **nonlinear static (pushover)** analysis and the ASCE 41 acceptance criteria;
  and
* the **monotonic backbone** (no asterisk) — the *monotonic* moment–rotation
  curve, used for **nonlinear response-history analysis** with a component
  model that explicitly tracks cyclic deterioration (the `IMKBilin` hinge).

The monotonic backbone is systematically **more ductile** than the first-cycle
envelope (larger $\theta_p$, $\theta_{pc}$, a higher residual $M_r$ and a
higher capping $a$): a column cycled to its first-cycle limit has already
accumulated damage from repeated unloading, whereas the monotonic curve
reaches the same rotations without intermediate unloading.  Because `IMKBilin`
models the monotonic backbone, the toolkit uses the no-asterisk parameters
throughout:

| Parameter | Equation (monotonic backbone) |
|---|---|
| yield moment $M_y^*$ (shared) | $M_y^* = 1.15\,Z\,R_y\,F_y\,(1 - P_g/P_{ye})$ if $P_g/P_{ye} \le 0.20$; $M_y^* = 1.15\,Z\,R_y\,F_y\,(9/8)\,(1 - P_g/P_{ye})$ otherwise |
| capping ratio $a = M_c/M_y$ | $a = 12.5\,(h/t_w)^{-0.2}\,(L_b/r_y)^{-0.4}\,(1 - P_g/P_{ye})^{0.4}$, $1.0 \le a \le 1.3$ |
| pre-capping $\theta_p$ | $\theta_p = 294\,(h/t_w)^{-1.7}\,(L_b/r_y)^{-0.7}\,(1 - P_g/P_{ye})^{1.6} \le 0.20$ |
| post-capping $\theta_{pc}$ | $\theta_{pc} = 90\,(h/t_w)^{-0.8}\,(L_b/r_y)^{-0.8}\,(1 - P_g/P_{ye})^{2.5} \le 0.30$ |
| residual $M_r$ | $M_r = (0.5 - 0.4\,P_g/P_{ye})\,M_y^*$ |

$R_y$ = AISC expected-yield ratio (1.1 for A992/A572-50).  For reference, the
*first-cycle envelope* (ASCE 41) curve — **not** used by the `IMKBilin` path —
replaces the capping/rotation/residual rows above with:

| Parameter | Equation (first-cycle envelope) |
|---|---|
| capping ratio $a^* = M_c/M_y$ | $a^* = 9.5\,(h/t_w)^{-0.4}\,(L_b/r_y)^{-0.16}\,(1 - P_g/P_{ye})^{0.2} \ge 1.0$ |
| pre-capping $\theta_p^*$ | $\theta_p^* = 15\,(h/t_w)^{-1.6}\,(L_b/r_y)^{-0.3}\,(1 - P_g/P_{ye})^{2.3} \le 0.10$ |
| post-capping $\theta_{pc}^*$ | $\theta_{pc}^* = 14\,(h/t_w)^{-0.8}\,(L_b/r_y)^{-0.5}\,(1 - P_g/P_{ye})^{3.2} \le 0.10$ |
| residual $M_r^*$ | $M_r^* = (0.4 - 0.4\,P_g/P_{ye})\,M_y^*$ |

The cyclic-deterioration $\Lambda$ of the monotonic `IMKBilin` backbone is in
the same paper (Eq. 13):

| Parameter | Equation |
|---|---|
| $\Lambda_s$ (basic-strength deterioration) | $\Lambda_s = 25500\,(h/t_w)^{-2.14}\,(L_b/r_y)^{-0.53}\,(1 - P_g/P_{ye})^{4.92} \le 3.0$ if $P_g/P_{ye} \le 0.35$; $\Lambda_s = 268000\,(h/t_w)^{-2.30}\,(L_b/r_y)^{-1.30}\,(1 - P_g/P_{ye})^{1.19} \le 3.0$ otherwise |
| $\Lambda_c$, $\Lambda_k$ (post-capping / unloading-stiffness) | $\Lambda_c = \Lambda_k = 0.9\,\Lambda_s$ |

### The ultimate rotation $\theta_u$

The `IMKBilin` material's `du` is the rotation at which the flexural strength
drops to zero.  The papers do not give a single flexural `du` regression
(PEER/ATC-72-1 notes $\theta_u$ can be *smaller* than the residual point, i.e.
fracture before the post-capping branch is exhausted), so the toolkit exposes
the choice as a **named convention** — `theta_u_model` — plus an explicit
`theta_u` float override (which always wins):

| `theta_u_model` | $\theta_u$ | Meaning |
|---|---|---|
| `"plateau"` *(default)* | $\theta_p + \theta_{pc} + 0.02$ | flexural fracture + residual plateau |
| `"none"` | $\theta_p + \theta_{pc}$ | flexural fracture, no plateau |
| `"lignos"` | $0.15$ rad | Lignos et al. (2019) Eq. 11 — monotonic axial collapse |
| `"hybrid"` | $\max(\theta_p + \theta_{pc} + 0.02,\ 0.15)$ | floor at the paper's collapse point |

The chosen convention is recorded as run-level provenance
(`md.imk_theta_u_model`) so a benchmark run and an assessment run stay
comparable.  For **benchmark matching** use the default `"plateau"` (or an
explicit `theta_u` to hit a target collapse capacity); for **assessments**
`"hybrid"` is recommended — it never under-predicts below the paper's 0.15 rad
and stays material-valid for stocky sections.

Lignos et al. (2019) give the paper's ultimate chord rotation —
$\theta_{ult} = 0.15\ \text{rad}$ (monotonic, Eq. 11) and
$\theta_{ult}^* = 0.08\,(1 - 0.6\,P_g/P_{ye})$ (first-cycle, Eq. 12) — as the
rotation at which a column loses its **axial-load-carrying capacity** (severe
local buckling / axial shortening).  `IMKBilin` models the monotonic backbone,
so the cyclic reduction is carried by the $\Lambda$ deterioration parameters,
not by $\theta_u$.

When `du` is missing from a hand-built `imk_params` dict, `_imk_bilin_args`
falls back to the **no-plateau** value $\theta_u = \theta_p + \theta_{pc}$ —
deliberately the conservative choice: a smaller `du` drops the hinge to zero
strength *earlier* (predicts collapse sooner) than the `"plateau"` default, so
an uncalibrated hinge never overstates its ductility.  (The calibration path
resolves $\theta_u$ explicitly via `theta_u_model`, so this fallback only fires
for hand-built dicts.)

## 7. References

BibTeX keys in `papers/references.bib` (the entries this document draws on):

```bibtex
@article{ibarra2005hysteretic,
  title = {Hysteretic Models that Incorporate Strength and Stiffness Deterioration},
  author = {Ibarra, Luis F. and Medina, Ricardo A. and Krawinkler, Helmut},
  journal = {Earthquake Engineering \& Structural Dynamics},
  volume = {34},
  number = {12},
  pages = {1489--1511},
  year = {2005},
  doi = {10.1002/eqe.495},
}

@article{lignos2011,
  title = {Deterioration Modeling of Steel Components in Support of Collapse Prediction of Steel Moment Frames under Earthquake Loading},
  author = {Lignos, Dimitrios G. and Krawinkler, Helmut},
  journal = {Journal of Structural Engineering},
  volume = {137},
  number = {11},
  pages = {1291--1302},
  year = {2011},
  doi = {10.1061/(ASCE)ST.1943-541X.0000376},
}

@article{lignos2019asce41columns,
  title = {Proposed Updates to the {ASCE} 41 Nonlinear Modeling Parameters for Wide-Flange Steel Columns in Support of Performance-Based Seismic Engineering},
  author = {Lignos, Dimitrios G. and Hartloper, Alexander R. and Elkady, Ahmed and Deierlein, Gregory G. and Hamburger, Ronald O.},
  journal = {Journal of Structural Engineering},
  volume = {145},
  number = {9},
  year = {2019},
  doi = {10.1061/(ASCE)ST.1943-541X.0002353},
}

@inproceedings{hartloper2017eurosteel,
  title = {Updates to the {ASCE-41-13} Provisions for the Nonlinear Modeling of Steel Wide-Flange Columns for Performance-Based Earthquake Engineering},
  author = {Hartloper, Alexander R. and Lignos, Dimitrios G.},
  booktitle = {Proceedings of EUROSTEEL 2017},
  year = {2017},
}

@inproceedings{lignos2018ncee,
  title = {Revised {ASCE-41} Modeling Recommendations for Moment-Resisting Frame Systems},
  author = {Lignos, Dimitrios G. and Hartloper, Alexander R. and Elkady, Ahmed and Hamburger, Ronald O. and Deierlein, Gregory G.},
  booktitle = {Proceedings of the 11th U.S. National Conference on Earthquake Engineering},
  year = {2018},
}

@techreport{ibarra2005collapse,
  title = {Global Collapse of Frame Structures under Seismic Excitations},
  author = {Ibarra, Luis F. and Krawinkler, Helmut},
  institution = {Pacific Earthquake Engineering Research Center, University of California, Berkeley},
  number = {PEER Report 2005/06},
  year = {2005},
  url = {https://peer.berkeley.edu/publications/2005-06},
}
```

- OpenSees `IMKBilin` material (`IMKBilin.cpp`; the deprecated alias is `Bilin`).

