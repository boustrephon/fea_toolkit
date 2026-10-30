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
> Medina Appendix-A tables), the peak-oriented `IMKPeakOriented` hinge
> (`hinge_backbone="imk_peak"`), and the `generic_frame` generator (SI model +
> IMK backbone mapping + spring placement) landed; the modal-T1 check matches
> all 12 frames to ≤ 0.5 % and the pushover strength `γ = Vy/W` matches the
> Appendix-B design value 1.0 to ≤ 2 % — both record-free.  The LMSR-N collapse
> (IDA) runner is next.

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

## Units

The Medina tables are in **US customary units** (kips, inches, kip-in, ksi).
The generator builds every frame in **SI (N, m, kg)** — the same system as the
SAC benchmark and the PEER records (m/s²) — so each table value is converted
exactly once, at the module boundary, through exact factors:

| Quantity | US → SI factor |
|---|---|
| length | 1 in = **0.0254 m** (exact) |
| force | 1 kip = **4448.2216152605 N** (exact; 1000 lbf) |
| stress | 1 ksi = **6.894757293168361e6 Pa** (exact) |
| moment / rotational stiffness | 1 kip·in = **112.9848290276 N·m** |
| second moment of area | 1 in⁴ = **(0.0254)⁴ m⁴** |

Rules applied (`.clinerules` §4.6):

- **No hard-coded gravity.**  Lumped masses use
  `g_from_units({"F": "N", "L": "m", "T": "C"})` = 9.80665 m/s²; the P-Δ load
  (`1.4 × seismic mass`) will use the same.
- **No hand-converted strengths/moduli.**  `E = 29000 ksi → 1.99948e11 Pa` and
  every spring strength `My` / stiffness `Kc`, `Kb` scale through the kip·in →
  N·m factor above.
- **Axial area is a placeholder.**  The tables carry no axial member data (the
  benchmark frames neglect axial flexibility), so `A = 100 m²`
  (`AXIAL_AREA_M2`) makes the members axially rigid — verified: the modal T1 is
  insensitive to `A` beyond this value.
- The modal check reproduces all 12 periods to **≤ 0.5 %**, so the conversion
  is validated end-to-end (geometry, E, I, Kc and masses all consistent).

## Validation ladder

1. ✅ Modal `T1` against the table — all 12 frames within **0.5 %**, through the
   full pipeline (`tests/test_generic_frame_modal.py`).
2. ✅ Pushover `Vy/W = γ` — the Appendix-B design `γ = 1.0` matches to **≤ 2 %**
   analytically (first-yield `min(2·Myb1/h, 4·Myc/h)/W`, all 12 frames) and the
   pipeline pushover peak base-shear coefficient lands in `[0.9, 1.4]`
   (`tests/test_generic_frame.py`, `tests/test_generic_frame_pushover.py`).
3. IDA collapse `(Sa,c/g)/γ ≈ 3.3` at `T1 ≈ 1 s` — needs LMSR-N.

First target: **`0909`** (N = 9, T1 = 0.9 s) — closest to the SAC 3-story's
period.

## Spring placement (landed)

The IK-2005 Appendix B splits each member-end spring's elastic stiffness
between the spring and the elastic beam-column, *arbitrarily* — the member-end
``Kmem`` is what matters.  The generator uses:

* **beam ends** — near-rigid ``imk_peak`` springs (``hinge_stiffness_factor =
  100`` so the elastic beam carries the full ``6EI/L``);
* **column bases** — a *total*-M-θ spring with ``k0 = Kc`` (the base has no
  elastic member below it), placed only at the base end via the per-end
  ``FrameElement.imk_params_i`` override, with the column top left a rigid
  near-rigid elastic spring;
* **upper columns** — elastic (no ``imk_params``).

The per-end override (``imk_params_i`` / ``imk_params_j``) and the ``k0`` field
in ``imk_params`` are the only new machinery; the IK-2005 backbone maps to
``θp = 3θy``, ``mc_my = 1.09``, ``θpc = 10.9θy``, ``res = 0`` (§5.3.2).

## References

- Ibarra & Krawinkler (2005), PEER 2005/06 (global collapse).
- Medina & Krawinkler (2003), PEER 2003/15 (frame data, Appendix A).
- Ibarra, Medina & Krawinkler (2005), EESD 34(12). `10.1002/eqe.495`.
