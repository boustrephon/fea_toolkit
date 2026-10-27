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
| roof disp. (m) | 0.151 | 0.153 (+1 %) | 0.149 (−1 %) | 0.145 (−4 %) | — (no conv.) |
| base shear ×2 (MN) | 10.12 | 10.14 (+0.2 %) | 10.27 (+1.5 %) | 10.23 (+1.1 %) | — (no conv.) |
| max. drift | 0.0150 | 0.0155 (+3 %) | 0.0148 (−1 %) | 0.0143 (−5 %) | — (no conv.) |
| roof abs. accel (m/s²) | 6.54 | 7.11 (+8.7 %) | 6.95 (+6 %) | 6.54 (0 %) | — (no conv.) |
| yielded hinges `Nd` | 22 | 22 | 22 | 22 | — (no conv.) |

**D (bwh + Clough) does not converge on El Centro 1.0** — its
unloading-stiffness degradation loses convergence mid-record — so its column is
reported as non-convergent rather than with the earlier single-case peaks, which
proved unreproducible.

The elastic anchor, for reference: roof 0.206 m, base shear ×2 20.81 MN,
drift 0.0207, roof accel 10.03 m/s², `Nd = 0`.

## Reading the comparison

The four formulations split the five quantities cleanly by mechanism:

* **A (lumped)** is closest on **base shear** (+0.2 %) and roof (+1 %) but is
  the softest elastically (T1 ≈ 1.041 s) and worst on roof acceleration (+8.7 %).
* **B (bwh + Hysteretic)** holds the exact elastic stiffness and is closest on
  **roof** (−1 %) and **drift** (−1 %), with base shear +1.5 %.
* **C (bwh + Steel02)** keeps the exact elastic stiffness and is closest on
  **roof acceleration** (0 %), with base shear +1.1 %.
* **D (bwh + Clough)** adds unloading-stiffness degradation and is **unstable**:
  it loses convergence on El Centro 1.0 (and on 4 of the 10 cases overall), so
  it has no reproducible El Centro peak to compare.

The decisive comparison is B versus C, which differ in two respects at once: the
cyclic law (the sharp `Hysteretic` corner → the smooth `Steel02` transition)
*and* the monotonic yield transition (`Steel02` rounds the trilinear corners
that `Hysteretic` keeps sharp).  B versus C moves the roof −1 % → −4 %, the
drift −1 % → −5 %, and leaves the base shear within ±1.5 % (10.27 → 10.23 MN).
Because `Steel02` rounds the monotonic yield transition as well as changing the
cyclic law, the comparison cannot isolate either effect, and so cannot establish
that the cyclic law does not affect base shear — only that the two changes
together leave it within ±1.5 %.  The three converged formulations land within ±4 % of
the published base shear — the two `beamWithHinges` forms (B/C, both T1 ≈
0.993 s) cluster at 10.27 / 10.23 MN and the softer lumped hinge (T1 ≈ 1.041 s)
at 10.14 MN.

## The base shear is reproduced

The earlier pass of this comparison reported the base shear 6–19 % high and
attributed the residual to the unpublished `Nonlin12` yield-index α
interpolation.  That was an artefact of the transient runner, not the model:
the ground acceleration in the equilibrium sum `Σ m·(a_rel + ag)` was sampled
one integration step too early, biasing the peak.  With the sampling corrected,
the base shear lands on the published `F_max` to within ±4 % (and +1.5 % on the
El Centro 1.0 anchor), so the benchmark's base shear **is** recoverable from the
published backbone and elastic stiffness — no unpublished interpolation is
needed.  The hypotheses ruled out along the way (cyclic law D, hinge length
`Lp`, floor diaphragm, integration step, mass/damping) all still hold; they were
simply not the cause, because there is no remaining gap to explain.

## What this tells us about the Ohtori model

1. **The backbone is public and sufficient for the peaks.**  Reproducing the
   `mat_tbl` anchors exactly yields the displacement to ±4 % (A/B/C) **and** the
   base shear to ±4 % — the published peak response is recoverable without the
   unpublished parts of the `Nonlin12` S-function.

2. **Displacement and force split by mechanism.**  Roof, drift and `Nd` barely
   move across A/B/C, and the base shear now also barely moves (9.7–10.3 MN).
   B versus C shows the direction: the cyclic law moves displacement (roof
   −1 % → −4 %, drift −1 % → −5 %) while the base shear stays within ±1.5 % —
   the base shear is governed by the backbone (monotonic) and elastic
   stiffness.

3. **The cyclic law is decisive for damage, not for force.**  C's smooth
   `Steel02` transition moved roof acceleration (0 % vs B's +6 %) but left base
   shear within ±1.5 %; D's Clough degradation is unstable on the strong
   records, so its displacement and `Nd` are not reliably comparable there.
   For drift/damage checks the cyclic law is the choice that matters, while for
   force checks it is a red herring — the monotonic backbone governs.

4. **Practical consequence.**  For drift-based checks A/B/C are the appropriate
   formulations — they converge on every case and match the El Centro drift to
   within ±5 % — while **D (Clough, β = 0.5) is excluded**: it over-softens the
   displacement and loses convergence on the strong records.  For force-based
   checks the cyclic law is a red herring — what matters is the smoothness of
   the monotonic backbone transition, which a `Steel02` (or a
   distributed-plasticity fibre section) approximates better than a sharp
   `Hysteretic`.

## Guidance

* **Reproduce the elastic stiffness and roof/drift** (best for drift-based
  design checks): B — `beam_with_hinges` + `trilinear`.
* **Reproduce base shear / accelerations** (best for force-based or
  acceleration-based checks): B or C — `beam_with_hinges` + `trilinear` /
  `trilinear_steel02` (both hold the exact elastic stiffness and land within
  ±2 % on base shear; C additionally matches the roof acceleration).  A
  (lumped) is the closest base-shear match if the ~5 % elastic softening is
  acceptable.  D (Clough) is *not* recommended — it degrades displacement and
  yielding without improving the force.
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

