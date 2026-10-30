---
title: "SAC 3-Story IMK Collapse Benchmark (IDA)"
description: "SAC 3-story IMK-hinge steel moment frame: incremental-dynamic-analysis collapse capacity — the four-record median Sa(T1) compared against Ibarra–Krawinkler (2005)."
status: "complete"
tags: [validation, benchmark, sac, moment-frame, imk, collapse, ida, nonlinear-dynamic, ground-motion]
category: [planning]
related: [benchmark_register.md, imk_deterioration_model.md, sac_nonlinear_benchmark.md, ground_motion_data.md]
---

# SAC 3-Story IMK Collapse Benchmark (IDA)

## Purpose

The **T4** (published-FE-model) anchor for the **collapse / IDA** path: the
three-story SAC steel moment frame ([SAC 3-Story Frame Benchmark (T2)](sac_benchmark.md))
is built with **IMK** (`IMKBilin`) plastic hinges and driven through incremental
dynamic analysis (IDA) to **sidesway collapse**, reproducing the
Ibarra–Krawinkler (2005) collapse-capacity methodology.  This is the headline
A3 result of [the IMK deterioration-model plan](imk_deterioration_model.md).

## Model

The SAC 3-story frame is built by `fea_toolkit.benchmarks.sac.build_builder`
with `nonlinear=True`, `backbone="imk"`, `hinge_model="lumped"`.  Beam hinges
use the Lignos–Krawinkler (2011) backbone; column hinges use Lignos et al.
(2019) with FEMA-355C axial ratios derived member-by-member
(`derive_column_axial_ratio_by_member=True`); the leaning column stays elastic.
The model is a **2-D** N–S perimeter moment frame (5 column lines × 3 storeys,
`ndm=2`/`ndf=3`) carrying **half** the building's seismic mass — one of the two
symmetric frames — with a leaning column (line 5) that models the interior
gravity framing for P-Δ.  The transcription carries **no gravity loads** (see
the strength-ratio section below).  The model is SI
(`{"F": "N", "L": "m", "T": "C"}`), so the m/s² records apply directly.

## Method

1. **IM ladder.**  Each record is scaled over an ascending ladder of scale
   factors; the ladder is extended geometrically (doubled, capped at `16`) so
   the collapse scale factor need not be known a priori
   (`sac_run.run_ida(..., max_scale=16)`).
2. **Collapse criterion.**  A run collapses when it fails to converge to the
   end of the record (the Ibarra–Krawinkler non-convergence criterion) **or**
   its peak interstorey-drift ratio exceeds `0.10` (the FEMA P-695 sidesway
   proxy).  Only the **first** collapse intensity is taken.
3. **Bisection.**  The first collapse intensity is refined to a **1 %**
   relative tolerance between the last non-collapsed and first collapsed rung
   (Ibarra–Krawinkler 2005, PEER 2005/06 p. 37).
4. **`Sa(T1)` capacity.**  The collapse scale factor is converted to a
   spectral-acceleration capacity `Sa,c(T1) = scale × Sa(T1)` of the unscaled
   record (`Sa(T1)` from a 5 %-damped Newmark SDOF at the elastic `T1`).  The
   median over the collapsed records is the benchmark collapse capacity.

## Comparison target — Ibarra–Krawinkler (2005)

Ibarra & Krawinkler (2005, PEER 2005/06) report collapse capacity as a
**normalised spectrum** `(Sa,c/g)/η` vs period `T` for SDOF systems with the
peak-oriented IMK backbone (reference case `αs = 0.03`, `αc = −0.10`,
`δc/δy = 4`, `γs,c,k = 100`, no P-Δ), median over the 40-record LMSR-N set
(§4.3.2, Fig. 4.15a).  The **§5.7 procedure** maps that SDOF capacity to an
MDOF collapse capacity via the effective-mass coefficient and an auxiliary
backbone that carries the P-Δ stability coefficient.  They do **not** publish a
SAC-specific collapse number.

The comparison here is therefore a **band / order-of-magnitude check**, not a
point match:

- a **4-record** median (El Centro, Hachinohe, Northridge, Kobe — the SAC
  benchmark's `CASE_ORDER` records) versus IK-2005's **40-record** LMSR-N
  median;
- the **Lignos-calibrated** IMK backbone versus IK-2005's reference backbone;
- an **MDOF** IDA value versus an SDOF spectrum (differing by the
  effective-mass coefficient).

The ~40-record LMSR-N (Medina 2002) / 44-record FEMA P-695 far-field set is the
documented follow-on for a statistically meaningful median.

## Results

`T1 = 0.994 s` (elastic).  `Sa(T1)` is the unscaled record's spectral
acceleration at `T1`; `Sa,c(T1)` is the collapse capacity (collapse scale
factor × `Sa(T1)`).  `g = 9.81 m/s²`.

| Record | `Sa(T1)` [g] | collapse scale | `Sa,c(T1)` [g] |
|---|---|---|---|
| ELCENTRO | 0.521 | 4.52 | 2.35 |
| HACHINOHE | 0.338 | 5.23 | 1.77 |
| NORTHRIDGE | 0.864 | 2.38 | 2.06 |
| KOBE | 1.551 | 2.01 | 3.11 |
| **median** | | | **2.21** |

The four-record dispersion (std of ln `Sa,c` ≈ 0.24) is dominated by the
near-field Kobe record (highest) and the far-field Hachinohe record (lowest) —
the expected pattern for a 4-record sample.

## Strength ratio `γ` and normalised collapse capacity

IK-2005 normalises the MDOF collapse capacity by the **strength ratio**
`γ = Vy / W` (yield base shear / seismic weight), so the four-record median is
re-expressed as `(Sa,c/g)/γ` for the like-for-like comparison.  `Vy` comes from
a displacement-controlled pushover of the *same* IMK model (triangular lateral
pattern, X direction, `gravity_patterns={}`).  The curve is nearly
elastic-perfectly-plastic after first yield (yield ≈ 1 % roof drift; peak
`Vmax ≈ 4.4 MN` at ≈ 4–7 % roof drift, `Vmax/Vy ≈ 1.03` — the small post-yield
hardening is characteristic of the modified-bilinear IMK backbone), so the
equal-energy EPP idealisation gives a stable `Vy`.  With the one-frame seismic
weight `W = 14.46 MN` (`1.4745e6 kg × 9.80665 m/s²`):

| Quantity | Value |
|---|---|
| `W` (seismic weight, one frame) | 14.46 MN |
| `Vy` (EPP yield base shear) | ≈ 4.3 MN |
| `γ = Vy / W` | **≈ 0.30** |
| `Sa,c(T1)/g` (4-record median) | 2.21 |
| `(Sa,c/g) / γ` | **≈ 7.4** |

**No gravity / no P-Δ.**  The SAC transcription carries **no gravity loads at
all** — neither on the moment-frame members nor on the leaning column.  The
moment-frame columns' gravity *axial ratio* `Pg/Pye` **is** already reflected in
the IMK backbone (`derive_column_axial_ratios_by_member`), so their yield
moments account for gravity; but the *geometric* P-Δ (second-order moment from
axial load × drift) needs a held gravity load and is absent.  The leaning column
is the 2-D P-Δ device that would carry the interior gravity framing's weight —
also unloaded.  Both this pushover and the IDA runs are therefore **without**
P-Δ, while IK-2005's frames include it.  Applying gravity (tributary dead load on
the frame + the gravity-framing weight on the leaning column) would lower both
`Vy` and the collapse capacity; the no-gravity pairing above is internally
consistent and an upper bound on the P-Δ-inclusive normalised capacity.

## Acceptance

The median collapse capacity `Sa,c(T1)/g ≈ 2.2` is in the expected order of
magnitude for a ductile 3-story steel moment frame at `T1 ≈ 1 s` (a
deteriorating frame of this period collapses at several times its yield
strength).  A **point** comparison is not available — IK-2005 publishes an SDOF
`(Sa,c/g)/η` spectrum over the 40-record LMSR-N set, not a SAC-specific number
— so the 40-record LMSR-N median (decision 5) is the follow-on for a
statistically meaningful, quantitative comparison; the four-record value above
is the benchmark-scale first step.

## References

- Ibarra & Krawinkler (2005), *Global Collapse of Frame Structures under
  Seismic Excitations*, PEER 2005/06.
- Ibarra, Medina & Krawinkler (2005), *Hysteretic models that incorporate
  strength and stiffness deterioration*, EESD 34(12). `10.1002/eqe.495`.
- Lignos & Krawinkler (2011), *Deterioration modeling of steel components in
  support of collapse prediction of steel moment frames under earthquake
  loading*, J. Struct. Eng. `10.1061/(ASCE)ST.1943-541X.0000376`.
- Lignos et al. (2019), *Proposed updates to the ASCE 41 nonlinear modeling
  parameters for wide-flange steel columns*, J. Struct. Eng.
