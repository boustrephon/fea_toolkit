---
title: "SAC Trilinear Hinge — Four Modelling Approaches"
description: "Four OpenSees concentrated-plasticity formulations for the Ohtori et al. (2004) trilinear M-φ backbone — lumped Hysteretic, beamWithHinges + Hysteretic, beamWithHinges + two Steel02 springs in parallel, and beamWithHinges + Clough (degrading Hysteretic) — compared against the published unctrl3.mat El Centro 1.0 response."
status: "complete"
tags: [validation, benchmark, sac, moment-frame, nonlinear, nltha, hinge, trilinear, hysteretic, steel02, clough, beam-with-hinges]
category: [planning]
related: [benchmark_roadmap.md, sac_benchmark.md, sac_nonlinear_benchmark.md, ground_motion_data.md]
---

# SAC Trilinear Hinge — Four Modelling Approaches

## Purpose

The Ohtori et al. (2004) SAC models use a **concentrated-plasticity** member
with a **trilinear** moment–curvature (`M-φ`) backbone at each end.  OpenSees
has no element that reproduces that member one-to-one, so the toolkit ships
four distinct formulations, all fed by the same transcribed backbone.  This
document records the four, how they are built, and how each lands against the
published `unctrl3.mat` El Centro 1.0 response — the guidance this package
exists to provide: *which nonlinear-modelling choice moves which response
quantity, and by how much.*

The four formulations are selected through two config keys on
`build_builder()` (and `builder_config()`):

| Formulation | `hinge_model` | `hinge_backbone` |
|---|---|---|
| **A** — lumped `Hysteretic` spring | `lumped` | `trilinear` |
| **B** — `beamWithHinges` + `Hysteretic` | `beam_with_hinges` | `trilinear` |
| **C** — `beamWithHinges` + two-`Steel02`-in-parallel | `beam_with_hinges` | `trilinear_steel02` |
| **D** — `beamWithHinges` + Clough (degrading `Hysteretic`) | `beam_with_hinges` | `trilinear_clough` |

All four consume the same transcribed `mat_tbl` backbone
(`benchmarks/sac_member_data.py`), stamped onto the sections by
`sac.enrich_trilinear()`.

## The trilinear backbone

Each `mat_tbl` row gives three flexural slopes and two break curvatures —
elastic `EI1` (0 → `d1`), post-yield `EI2` (`d1` → `d2`), hardening `EI3`
(`d2` → ∞) — so the yield moment is `My = EI1·d1` and the second corner is
`M2 = My + EI2·(d2 − d1)`.  `EI3` is small (≈ 3 % of `EI1`), so the third
branch is nearly perfectly plastic.  For the W14×257 column (mat 1):

| | value |
|---|---|
| `EI1` | 2.8295e8 N·m² |
| `EI2` | 2.5519e8 N·m² |
| `EI3` | 8.4885e6 N·m² |
| `d1` | 9.2366e-3 m⁻¹ |
| `d2` | 9.7244e-3 m⁻¹ |

## Approach A — lumped `Hysteretic` hinge

Each member is split `node → hinge → elastic interior → hinge → node`, with a
zero-length rotational spring (`zeroLength`, `-mat`/`-dir`) carrying the
trilinear `Hysteretic` material (sharp corners, symmetric, no pinching or
damage).  The elastic interior carries `P/V/T` and the elastic `EI1` flexure;
the hinge's elastic stiffness is a configurable "nearly rigid" multiple of
`6EI1/L`, so the interior dominates the elastic deformation.

Consequence: the "nearly rigid" series spring softens the elastic response —
**T1 ≈ 1.041 s** against the elastic anchor's 0.994 s (~5 % softer).

## Approach B — `beamWithHinges` + `Hysteretic`

The benchmark's own one-component mapping: a single `beamWithHinges` element
per member — an **elastic interior** (`E, A, Iz, Iy, G, J`) with a trilinear
`M-φ` `Uniaxial` section (strong-axis `Mz`) at each end.  There is no "nearly
rigid" spring, so the elastic stiffness is the exact `6EI1/L` — **T1 ≈
0.993 s**, matching the elastic anchor.  The plastic hinge length converts
curvature to member-end rotation via the benchmark's θ = φ·L/6, so
`Lp = L/6` (`hinge_length_fraction`).

## Approach C — `beamWithHinges` + two `Steel02` in parallel

Identical element to B, but the end `M-φ` section is two bilinear `Steel02`
springs summed at the shared curvature through a `Parallel` material.  The
trilinear slopes decompose exactly:

* spring **A** yields first (at `d1`) and hardens at `H_A = EI3`;
* spring **B** yields second (at `d2`) and is perfectly plastic (`H_B = 0`).

The combined slopes are `EI1 = E_A + E_B`, `EI2 = H_A + E_B`, `EI3 = H_A`,
which solves to `E_A = EI1 − EI2 + EI3`, `E_B = EI2 − EI3`.  The two
`Steel02` parameters are then

| spring | `fy` | `E0` | `b` |
|---|---|---|---|
| A | `E_A·d1` | `E_A` | `EI3 / E_A` |
| B | `E_B·d2` | `E_B` | `0` |

`Steel02` (Giuffré–Menegotto–Pinto) rounds the trilinear corners and reloads
toward the previous maximum — the closest OpenSees analogue to the benchmark
`Nonlin12` S-function's unpublished yield-index α interpolation, which the
sharp-break `Hysteretic` of A/B lacks.  The elastic stiffness is unchanged
(T1 ≈ 0.993 s).

## Approach D — `beamWithHinges` + Clough (degrading `Hysteretic`)

Identical element and backbone to B, but the end `Hysteretic` adds
**unloading-stiffness degradation** (`beta = 0.5`, the `Hysteretic` β) — the
Clough/Takeda signature.  The monotonic backbone and strength are unchanged, so
the elastic stiffness (T1 ≈ 0.993 s) and first-yield force are identical to B;
only the cyclic unloading path softens with ductility.  This isolates the
*cyclic* contribution: if the base-shear gap were a cyclic-law effect, D would
close it.

## El Centro 1.0 comparison (3-story, dt = 0.005 s)

`F_max` is the whole-building base shear, so the planar frame's base shear is
doubled (`Num_MRF = 2`); see `benchmark_roadmap.md` §2 A4.

| quantity | target | A (lumped) | B (bwh+Hyst) | C (bwh+Steel02) | D (bwh+Clough) |
|---|---|---|---|---|---|
| roof disp. (m) | 0.151 | 0.153 (+1 %) | 0.149 (−1 %) | 0.145 (−4 %) | 0.126 (−17 %) |
| base shear ×2 (MN) | 10.12 | 10.75 (+6 %) | 12.03 (+19 %) | 11.55 (+14 %) | 11.91 (+18 %) |
| max. drift | 0.0150 | 0.0155 (+3 %) | 0.0148 (−1 %) | 0.0143 (−5 %) | 0.0134 (−11 %) |
| roof abs. accel (m/s²) | 6.54 | 6.99 (+7 %) | 6.88 (+5 %) | 6.47 (−1 %) | 6.89 (+5 %) |
| yielded hinges `Nd` | 22 | 22 | 22 | 22 | 16 |

The elastic anchor, for reference: roof 0.206 m, base shear ×2 20.81 MN,
drift 0.0207, roof accel 10.03 m/s², `Nd = 0`.

## Reading the comparison

The four formulations split the five quantities cleanly by mechanism:

* **A (lumped)** is closest on **base shear** (+6 %) and roof (+1 %) but is
  the softest elastically (T1 ≈ 1.041 s) and worst on roof acceleration (+7 %).
* **B (bwh + Hysteretic)** holds the exact elastic stiffness and is closest on
  **roof** (−1 %) and **drift** (−1 %), but worst on **base shear** (+19 %).
* **C (bwh + Steel02)** keeps the exact elastic stiffness and is closest on
  **roof acceleration** (−1 %), with base shear midway (+14 %).
* **D (bwh + Clough)** adds unloading-stiffness degradation and *over-softens*
  the displacement — roof −17 %, drift −11 %, and under-yields (`Nd` = 16) —
  **yet leaves the base shear at +18 %**, essentially unchanged from B.

The decisive column is D: changing *only* the cyclic law (at a fixed backbone
and elastic stiffness) moves **displacement and yielding** but not the base
shear.  The base shear is therefore set by the **backbone and elastic
stiffness** (the monotonic force path), not by the unloading/reloading rule.
The three `beamWithHinges` forms (B/C/D, all T1 ≈ 0.993 s) cluster at
11.5–12.0 MN regardless of the cyclic detail, while the softer lumped hinge
(T1 ≈ 1.041 s) drops to 10.75 MN.

## The remaining gap

The base shear stays high (+19 % B, +14 % C, +18 % D) after ruling out, in
order:

* **Cyclic law** — D (Clough unloading-stiffness degradation) left the base
  shear at +18 %: the cyclic path does not set the peak force (hypothesis
  refuted).
* **Hinge length `Lp`** — a L/10 → L/3 sweep moved base shear < 1 %
  (hypothesis retracted).
* **Floor diaphragm** — the explicit `struct3.m` `equalDOF` master-slave
  (`fidelity='faithful'`) changed nothing: the rigid-axial floor beams already
  tie the floors' X DOFs exactly.
* **Integration step** — 0.005 s is non-negotiable (0.02 s inflated base shear
  ~55 %).
* **Mass / damping / elastic stiffness** — all match (FEMA 355C storey mass ×2;
  the same uncapped `a0·M + a1·K` Rayleigh; T1 exact).

What remains is the **effective backbone in the plastic range**, not the cyclic
law: the benchmark's `Nonlin12` applies a yield-index α interpolation that
smooths the elastic→plastic transition, lowering the secant stiffness once a
member yields — which lowers the peak force while leaving the elastic T1 (and
the displacement, set by the first mode) almost unchanged.  That interpolation
is neither published nor redistributable.

## What this tells us about the Ohtori model

1. **The backbone is public and matched, but not sufficient.**  Reproducing the
   `mat_tbl` anchors exactly yields the displacement to ±4 % (A/B/C) but the
   force 6–19 % high.  The model's identity lives in the unpublished parts of
   the `Nonlin12` S-function.

2. **Displacement is robust to the hinge choice; force is not — but the
   sensitivity splits by mechanism.**  Roof, drift and `Nd` barely move across
   A/B/C, while the base shear spreads 10.75–12.03 MN.  The D column shows the
   *direction*: the cyclic law governs displacement and yielding, while the
   base shear is governed by the backbone (monotonic) and elastic stiffness.

3. **The base-shear gap is a *transition* effect, not a cyclic effect.**  D's
   stiffness degradation moved displacement and `Nd` but not force; C's smooth
   `Steel02` transition moved force (19 % → 14 %).  So Ohtori's member softens
   *monotonically* (the α interpolation through the elastic→plastic transition)
   rather than *cyclically*.  That is the one ingredient we cannot reproduce,
   and it is why the softest formulation — the lumped hinge's "nearly rigid"
   series compliance, not degradation — lands closest on base shear.

4. **Practical consequence.**  For drift-based checks any of the four will do;
   for force-based checks the cyclic law is a red herring — what matters is the
   smoothness of the monotonic backbone transition, which a `Steel02` (or a
   distributed-plasticity fibre section) approximates better than a sharp
   `Hysteretic`.

## Guidance

* **Reproduce the elastic stiffness and roof/drift** (best for drift-based
  design checks): B — `beam_with_hinges` + `trilinear`.
* **Reproduce base shear / accelerations** (best for force-based or
  acceleration-based checks): C — `beam_with_hinges` + `trilinear_steel02`
  (the smooth transition is what moves the force); A (lumped) is the closest
  base-shear match if the ~5 % elastic softening is acceptable.  D (Clough) is
  *not* recommended — it degrades displacement/yielding without touching the
  force.
* **Cheapest, most robust** for scoping studies: A — `lumped` + `trilinear`.

## API

```python
from fea_toolkit.benchmarks.sac import build_builder

b_a = build_builder(nonlinear=True, backbone="trilinear",          hinge_model="lumped")
b_b = build_builder(nonlinear=True, backbone="trilinear",          hinge_model="beam_with_hinges")
b_c = build_builder(nonlinear=True, backbone="trilinear_steel02",  hinge_model="beam_with_hinges")
b_d = build_builder(nonlinear=True, backbone="trilinear_clough",   hinge_model="beam_with_hinges")
```

The decomposition helper is
`fea_toolkit.opensees._elements._parallel_steel02_trilinear`.

## Tests

- `tests/test_benchmark_sac_trilinear.py` — the trilinear backbone stamp,
  the four formulations' build/period/NLTHA smoke tests, and the two-`Steel02`
  decomposition (slopes reproduced exactly; non-decomposable backbones raise).
- `tests/test_benchmark_sac3_nltha.py` — elastic + nonlinear NLTHA anchors.

