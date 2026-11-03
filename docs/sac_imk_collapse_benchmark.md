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
The model is a **planar 3-D** N–S perimeter moment frame (5 column lines ×
3 storeys, six DOFs per node with the out-of-plane DOFs restrained) carrying
**half** the building's seismic mass — one of the two
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

**No gravity / no P-Δ — faithful to the SAC benchmark.**  The absence of gravity
is **not** a gap in the transcription: the Ohtori et al. (2004) benchmark itself
is a no-gravity, no-P-Δ model.  Its incremental equation of motion (Eq. 1) has
loading vectors only for ground acceleration, control forces and the unbalanced
hysteresis force; the tangent stiffness is the *material* (concentrated-
plasticity) stiffness with no geometric-stiffness term; `struct3.m` defines only
`Element_mass` (seismic mass) with no load data; and the paper never mentions
gravity / P-Δ / dead / live load.  This is why the transcription reproduces the
published El Centro 1.0× response to ~1–4 % without gravity.  The moment-frame
columns' gravity *axial ratio* `Pg/Pye` is still reflected in the IMK backbone
(`derive_column_axial_ratios_by_member`).

For the **P-Δ-enabled comparison to Ibarra–Krawinkler (2005)**, whose generic
frames *do* include global P-Δ (gravity `P = 1.4 × seismic mass`, i.e. dead +
40 % live, held via a constant geometric-stiffness matrix), the benchmark
decision is to **adopt IK-2005's `1.4 × seismic mass` convention** verbatim
(their exact method) so the comparison is direct — the SAC data specifies no
gravity load of its own.

### With P-Δ (IK-2005 `1.4 × seismic mass`, held)

Re-running the pushover and IDA with `pdelta=True` + `pdelta_gravity=True`
(`geom_transf_type="PDelta"`, gravity `P = 1.4 × W = 20.24 MN` held via
`loadConst`):

| Quantity | no P-Δ | P-Δ |
|---|---|---|
| `Vy` (EPP yield base shear) | ≈ 4.3 MN | ≈ 4.08 MN |
| `γ = Vy / W` | ≈ 0.30 | **≈ 0.282** |
| `Sa,c(T1)/g` (4-record median) | 2.21 | **2.20** |
| `(Sa,c/g) / γ` | ≈ 7.4 | **≈ 7.8** |

The P-Δ `γ` is the **equal-energy** (EPP) idealisation
(`bilinearize_equal_energy`) — the same definition as the "γ equal-energy"
column of the 9/20-story table below (0.282); the first-yield alternative
(5 % secant-stiffness drop) is 0.236, which raises `(Sa,c/g)/γ` to ≈ 9.3.

The pushover shows the expected P-Δ softening (peak at ≈ 2.4 % roof drift then a
negative post-yield slope, versus ≈ 7.5 % without), and the held gravity is
verified (vertical reaction = 20.24 MN).  But the **IDA collapse capacity barely
moves**: P-Δ lowers `γ` (~13 %) yet leaves `Sa,c` almost unchanged, so the
*normalised* capacity actually rises.  This is consistent with Ibarra–Krawinkler
(2005) §5.5.1 — *"the trend is reversed for the three-story frames because P-∆
effects are small"*.  P-Δ therefore does **not** close the gap to their
`(Sa,c/g)/γ ≈ 3.3` at `T1 ≈ 1 s`.

**Structural mismatch, not a P-Δ gap.**  IK-2005's frames obey `T1 = 0.1N`
(stiff) or `0.2N` (flexible), so the frames at `T1 ≈ 1 s` are **9–10 storeys**
(`0909` at 0.9 s, `0612` at 1.2 s) — tall frames whose collapse is P-Δ-dominated
and hence low.  The SAC 3-storey is **3 storeys at `T1 ≈ 1.0 s`** (an unusually
flexible perimeter frame with a leaning column), which IK-2005 never modelled;
its P-Δ is small by comparison.  A like-for-like collapse match therefore needs
either the SAC **9/20-storey** buildings (whose `T1`/`N` map onto IK-2005's tall
frames) or a comparison at the SAC 3-storey's *own* period/strength.

## Acceptance

The median collapse capacity `Sa,c(T1)/g ≈ 2.2` is in the expected order of
magnitude for a ductile 3-story steel moment frame at `T1 ≈ 1 s` (a
deteriorating frame of this period collapses at several times its yield
strength).  A **point** comparison is not available — IK-2005 publishes an SDOF
`(Sa,c/g)/η` spectrum over the 40-record LMSR-N set, not a SAC-specific number
— so the 40-record LMSR-N median (decision 5) is the follow-on for a
statistically meaningful, quantitative comparison; the four-record value above
is the benchmark-scale first step.

## 9/20-story extension — approaches record

*Working record of the 9/20-story collapse runs, the strength-ratio (γ) yield
definitions and the damping/criterion choices, so the approaches can be
revisited and discussed.  Headline numbers below; curves are in
`docs/assets/sac_pushover_curves.png` and `output/sac_pushover/*.json`.*

### Collapse capacity — 4-record median (P-Δ + held 1.4×mass gravity, dt 0.01, non-convergence)

| building | T1 (s) | mass-proportional 5 % | Rayleigh 5 % on (T1, T95) | Δ |
|---|---|---|---|---|
| 9-story | 2.26 | 0.867 g | **0.913 g** | +5 % |
| 20-story | 3.83 | 0.176 g | **0.179 g** | +2 % |

**Finding — damping formulation is a small lever here.**  The
mass-proportional → Rayleigh-on-(T1,T95) switch moves the median only 2–5 %.
These flexible SAC frames collapse in a first-mode / P-Δ-dominated way, so the
higher modes the two formulations damp differently barely matter.  (This
contrasts with IK-2005's single-bay generic frames, where the damping
formulation was flagged as a suspect for the long-period undershoot — see
`ik2005_generic_frame_benchmark.md` §"Known deviations".)

### Strength ratio γ — two yield definitions

Both from a triangular-pattern displacement pushover of the *same* IMK model
under P-Δ + held gravity (`sac_run.pushover_strength_ratio`).

| building | W (one frame) | γ equal-energy | γ first-yield (5 % drop) | drift at first yield |
|---|---|---|---|---|
| 3-story | 14.46 MN | 0.282 | **0.236** | 0.87 % |
| 9-story | 48.86 MN | 0.116 | **0.106** | 0.83 % |
| 20-story | 59.52 MN | 0.049 | **0.047** | 0.51 % |

- **Equal-energy EPP** (`bilinearize_equal_energy`): the IMK curves harden
  post-yield, so this degenerates to the *peak* base shear — an upper bound on
  yield strength (and hence a lower bound on `(Sa,c/g)/γ`).
- **First-yield** (`_first_yield`, first 5 % secant-stiffness drop): first
  hinge formation, matching IK-2005's first-yield `min(2·Myb1/h, 4·Myc/h)/W`.
  It is 4–16 % below the equal-energy value.

### Normalised collapse `(Sa,c/g)/γ` vs IK-2005 Fig 5.16

Using the first-yield γ (the conservative choice):

| building | T1 (s) | `(Sa,c/g)/γ` | IK-2005 Fig 5.16 | ratio |
|---|---|---|---|---|
| 3-story | 1.0 | ≈ 9.3 (doc 2.20 / 0.236) | ≈ 3.3 | ~2.8× |
| 9-story | 2.26 | **8.6** | ≈ 3.0 | ~2.9× |
| 20-story | 3.83 | **3.8** | ≈ 2.8 | ~1.4× |

**Finding — structural overshoot, shrinking with height.**  The SAC buildings
overshoot IK-2005's normalised collapse capacity by ~2.8× (3/9-story) and
~1.4× (20-story).  The overshoot is robust to the γ definition (first-yield vs
peak differ only 4–16 %), so it is *not* a γ or damping or detector artefact —
it is the same structural mismatch the 3-story result flagged: the SAC
perimeter frames (with their leaning column and redundancy) are unusually
flexible-and-strong for their period, whereas IK-2005's single-bay γ = 1.0
frames are not.  The 20-story, the most P-Δ-dominated (T1 = 3.83 s ≈ 0.19·N,
near IK-2005's flexible 0.2·N frames), is the closest match.

### Open questions (to revisit)

1. **γ definition** — first-yield vs peak.  First-yield is IK-2005-faithful and
   conservative; the peak is the codebase's existing equal-energy default.
   The 4–16 % spread propagates straight into `(Sa,c/g)/γ`.
2. **20-story pushover is fragile** — its P-Δ collapse is abrupt (the
   displacement-controlled push fails to converge past ~0.5 % drift), so
   γ₂₀ = 0.047 is approximate.  An arc-length or force-controlled push may be
   needed.
3. **4 records only** — the SAC set (El Centro / Hachinohe / Northridge /
   Kobe), not the 40-record LMSR-N median IK-2005 uses.
4. **dt = 0.01** (not the full-fidelity 0.005) — within ~3 % run-to-run noise
   (see the archived comparison in `ik2005_generic_frame_benchmark.md`).

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
