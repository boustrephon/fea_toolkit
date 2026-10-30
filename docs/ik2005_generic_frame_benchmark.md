---
title: "IK-2005 Generic-Frame Collapse Benchmark"
description: "Reproduction of Ibarra & Krawinkler (2005, PEER 2005/06) generic single-bay frame collapse capacities: the Medina (2002) frame data, the Table 5.1 parameter matrix, the peak-oriented IMK material decision, and the validation plan."
status: "partial"
tags: [validation, benchmark, collapse, ida, imk, ibarra-krawinkler, medina, peak-oriented, moment-frame, ground-motion]
category: [planning]
related: [sac_imk_collapse_benchmark.md, imk_deterioration_model.md, benchmark_register.md, ground_motion_data.md]
---

# IK-2005 Generic-Frame Collapse Benchmark

> ⚠️ Partial — decisions recorded; `benchmarks/generic_frame_data.py` (the 12
> Medina Appendix-A tables) landed; the peak-oriented `IMKPeakOriented` hinge
> (`hinge_backbone="imk_peak"`) landed; the frame generator is next.

## Purpose

Reproduce Ibarra & Krawinkler (2005), *Global Collapse of Frame Structures
under Seismic Excitations* (PEER 2005/06) — the median MDOF collapse capacity
`(Sa,c/g)/γ ≈ 3.3` at `T1 ≈ 1 s` (§5.5).  This is a **like-for-like** match:
IK-2005's frames are the **generic single-bay moment frames** of Medina (2002),
not the SAC 4-bay frames — so a new generator (parallel to `sac.py`) is the
correct path.  The earlier SAC 3-story IDA (`sac_imk_collapse_benchmark.md`)
gave `Sa,c/g ≈ 2.2` on four records but does not map onto any IK-2005 frame
(the SAC 3-story is 3 storeys at `T1 ≈ 1 s`; IK-2005's `T1 ≈ 1 s` frames are
9–10 storeys).

## Key facts established

1. **Analysis package.**  IK-2005 (and Medina 2002) used a **modified DRAIN-2DX
   (1993)** with the IMK component-deterioration models; OpenSees appears only
   in a separate validation (Hao 2004).  Our OpenSees reproduction is therefore
   a cross-code match, not a same-code match.
2. **The frames.**  12 single-bay frames, N = 3/6/9/12/15/18 × stiff
   (`T1 = 0.1N`) / flexible (`T1 = 0.2N`), four-digit codes (`0909` = 9 storeys,
   0.9 s).  Storey height 12 ft (144 in), span 24 ft (288 in), steel
   E = 29 000 ksi (verified via `Kc = 3 E I1 / L` and `δr/θr = H = 144 N in`).
   Data: Medina & Krawinkler (2003), PEER 2003/15, Appendix A, Tables A.7–A.18
   → `benchmarks/generic_frame_data.py`.
3. **Backbone (§5.3.2).**  `αs = 0.03`, `αc = −0.10`, `δc/δy = 4`, peak-oriented,
   P-Δ `P = 1.4 × seismic mass`, 5 % Rayleigh damping on `T1` + the
   95 %-participation mode.

## Table 5.1 — 14 cases, 12 collapse onto one material

IK-2005's collapse-evaluation matrix (PDF p179) is **one model + four knobs**,
not 14 independent studies; every row is exercised in §5.5.1–5.5.6:

| §5.5 | Cases | Knob |
|---|---|---|
| Reference frames | 3 (+ Case 1 non-deteriorating baseline) | — |
| Post-capping stiffness | 8–11 | `αc = −0.1/−0.3/−0.5` |
| Ductility capacity | 2, 7 | `δc/δy = 2/4/6` |
| Cyclic deterioration | 4–6 | `γs,c,k,a = 100/50/25` |
| Hysteretic model | 12 pinching, 13 bilinear | model |
| P-Δ | 14 | off |

Cases **1–11 and 14 are all peak-oriented IMK**; only 12 (pinching) and
13 (bilinear) use a different material.

## Decisions

1. **Peak-oriented material.**  Implement `IMKPeakOriented` (the peak-oriented
   IMK; `ModIMKPeakOriented` is the deprecated spelling) on the existing lumped
   IMK path, with `αc`, `δc/δy`, `γs,c,k,a` (∞/100/50/25) and P-Δ as
   parameters.  This unlocks 12/14 cases *and* is the state-of-the-art collapse
   material.  `IMKBilin` (bilinear) stays as Case 13.
2. **Pinching deferred.**  `ModIMKPinching` (Case 12) is 1/14 cases and an RC
   bond-slip/connection phenomenon with no near-term steel-frame benefit —
   revisit only when RC joint work needs it.
3. **Lumped hinge, not `beam_with_hinges`.**  IK-2005/Medina specify the springs
   in **M-θ directly** (no plastic hinge length `Lp` anywhere in the data), so
   the `zeroLength` spring is the literal mapping; the near-rigid spring
   convention reproduces DRAIN-2DX's one-component decomposition exactly; and
   `lumped` is robust across the N = 3–18 stiffness range where the force-based
   element fails (`modeling_guidance.md` §1).
4. **Records.**  Collapse matching needs the 40-record **LMSR-N** set (Medina
   2002, IK-2005 Table 4.2) — PEER NGA, non-redistributable, fetched locally
   (`ground_motion_data.md`).  Modal and pushover matching are record-free.

## Validation ladder

1. Modal `T1` against the table (record-free, decisive).
2. Pushover `Vy/W = γ` (record-free).
3. IDA collapse `(Sa,c/g)/γ ≈ 3.3` at `T1 ≈ 1 s` — needs LMSR-N.

First target: **`0909`** (N = 9, T1 = 0.9 s) — closest to the SAC 3-story's
period.

## References

- Ibarra & Krawinkler (2005), PEER 2005/06 (global collapse).
- Medina & Krawinkler (2003), PEER 2003/15 (frame data, Appendix A).
- Ibarra, Medina & Krawinkler (2005), EESD 34(12). `10.1002/eqe.495`.
