---
title: "IK-2005 Generic-Frame Collapse Benchmark"
description: "Reproduction of Ibarra & Krawinkler (2005, PEER 2005/06) generic single-bay frame collapse capacities: the Medina (2002) frame data, the Table 5.1 parameter matrix, the peak-oriented IMK material decision, and the validation plan."
status: "partial"
tags: [validation, benchmark, collapse, ida, imk, ibarra-krawinkler, medina, peak-oriented, moment-frame, ground-motion]
category: [planning]
related: [sac_imk_collapse_benchmark.md, imk_deterioration_model.md, benchmark_register.md, ground_motion_data.md]
---

# IK-2005 Generic-Frame Collapse Benchmark

> ⚠️ Partial — the 12 Medina (2002) generic frames (`generic_frame_data.py`),
> the peak-oriented `IMKPeakOriented` hinge (`hinge_backbone="imk_peak"`), the
> `generic_frame` generator (SI model + IMK backbone mapping + spring placement)
> and the IDA runner (`generic_frame_run.py`, P-Δ + held gravity) all landed;
> the modal-T1 check matches all 12 frames to ≤ 0.5 % and the pushover strength
> `γ = Vy/W` matches the Appendix-B design value 1.0 to ≤ 2 % (both
> record-free).  All 12 frames are now run over the 33 locally-fetched LMSR-N
> records (19 CESMD + 14 USGS NSMP; 7 PEER NGA-West2-only remain).  After the
> Rayleigh-damping + drift-proxy + spring-placement fixes, the `0909` median
> **overshoots** — `(Sa,c/g)/γ ≈ 7.9` vs the paper's ≈ 3.4.  The root-cause
> analysis (see "Collapse criterion") shows the collapse *detector* is only a
> minor factor; the dominant gap is the frame's **dynamic stability** (P-Δ /
> post-capping), still under investigation.

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

**What the papers are.**  Both are *theoretical prediction* studies, not
reproductions of measured buildings.  MK-2003 (PEER 2003/15) parametrically
derives the statistical seismic **drift demand** of an idealized "generic"
frame class; IK-2005 (PEER 2005/06) extends the same machinery to **collapse**
— the Fig 5.16 median `(Sa,c/g)/γ`-vs-period curves are the *predicted* collapse
capacity of that idealized class, not a measured building response.  Our
benchmark therefore reproduces a prediction pipeline (idealized frame + IMK
backbone + record set → median collapse capacity), i.e. a cross-code (OpenSees
vs DRAIN-2DX) T4 equivalence check, not an experimental match.

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
3. ✅ IDA collapse `(Sa,c/g)/γ ≈ 3.3` at `T1 ≈ 1 s` — median **3.41** over the
   33 fetched LMSR-N records (vs ≈ 3.3, +3 %); dispersion σ_ln = 0.55 vs
   IK-2005's ≈ 0.42 (Fig 5.18c).  See "Results" below.

First target: **`0909`** (N = 9, T1 = 0.9 s) — closest to the SAC 3-story's
period.

## Results — 33-record IDA (`0909`)

The 33 locally-available LMSR-N records (40 − 7 PEER-only) were run through
`collapse_capacity_sa("0909", …)` — P-Δ + held `1.4 × seismic mass` gravity,
5 % mass-proportional damping, peak-oriented IMK hinge, `max_scale = 128` (so
every record collapses; the five slow records resolve at scales 33–64):

| Statistic | Ours (33 records) | IK-2005 | Δ |
|---|---|---|---|
| Median `(Sa,c/g)/γ` | **3.41** | ≈ 3.3 (§5.5) | **+3 %** ✅ |
| Dispersion σ_ln | **0.55** | ≈ 0.42 (Fig 5.18c) | +31 % ⚠️ |

IK-2005 reports σ_ln only *graphically* (Fig 5.18c, "Dispersion of Collapse
Capacity Spectra", y-axis "Standard Dev. ln[Sa,c(T1)/g]/γ") — ≈ 0.42 is read
off the base-case (αc = −0.1) curve at T1 = 0.9 s; there is no tabulated
value.  The median matches the target within the known caveats (7 PEER-only
records absent, `LP89gmr`/`NR94stc` on the non-Medina horizontal, CESMD Vol2
vs PEER NGA processing).  The dispersion is higher, most plausibly from the
**collapse definition** below.

### Collapse definition — drift proxy vs dynamic instability

`collapse_intensity` (`benchmarks/sac_run.py`) flags collapse at the **first**
of two tripwires per intensity:

1. **non-convergence** to the end of the record — the Ibarra–Krawinkler
   dynamic-instability criterion (the IDA curve going flat), or
2. **peak interstorey-drift ratio > 10 %** — the FEMA P-695 sidesway proxy
   (`_COLLAPSE_DRIFT = 0.10`).

IK-2005's ≈ 3.3 median and ≈ 0.4 σ_ln were computed with the
**dynamic-instability** definition only (the relative-intensity–EDP curve
becoming flat), not a fixed 10 % drift threshold.  The drift proxy fires
*earlier* than instability for any record whose peak drift reaches 10 % while
the frame is still numerically stable, which lowers that record's collapse
capacity.  *A priori* this could also inflate σ_ln — but in practice it does
not (see the split below).

**Decision.**  Keep the 10 % drift proxy as a failure definition (a clean,
deterministic lower bound), but make the IK-2005 comparison on the **same
dynamic-instability measure**.  The detector was instrumented to report which
tripwire fired for each record (`collapse_criterion`, exposed as the
`"collapse_criterion"` field of the `_ida_run` result).

### Per-record criterion split (33 records)

The instrumented detector was re-run over the 33 records (no bisection — the
criterion is read off the first collapsed rung, which bisection does not
change):

| Criterion | n | Records |
|---|---|---|
| non-convergence (instability) | **28** | `IV79cal`, `IV79cmp`, `IV79e01`, `IV79e12`, `IV79e13`, `IV79nil`, `IV79pls`, `IV79qkp`, `IV79wsm`, `LP89g03`, `LP89g04`, `LP89gmr`, `LP89hch`, `LP89hvr`, `LP89slc`, `LP89svl`, `NR94cen`, `NR94cnp`, `NR94fle`, `NR94glp`, `NR94hol`, `NR94lh1`, `NR94lv2`, `NR94nya`, `NR94stn`, `NR94ver`, `SF71pel`, `SH87wsm` |
| drift (10 % IDR, still converged) | **5** | `IV79chi` (4.5), `LP89cap` (2.0), `LP89hda` (5.1), `NR94far` (5.9), `NR94stc` (5.0) |

*Parentheses are `Sa,c/g` at the recorded (drift-clipped) collapse.*

**Reading.**  Collapse is dominated by dynamic instability (28/33), so the
detector largely *is* the IK-2005 criterion.  The 10 % drift proxy only clips 5
records — and it is **not** what inflates σ_ln: recomputing σ_ln over the 28
instability records alone gives 0.55, unchanged.  The 0.55-vs-0.42 gap therefore
comes from elsewhere (the 7 absent PEER records, the two wrong-component CESMD
records `LP89gmr`/`NR94stc`, CESMD Vol2-vs-PEER processing, and/or modelling
differences).  The drift proxy also does **not** drag the median below target:
its five clipped capacities sit mostly *above* the median (4 of 5 > 3.41), so
the instability-only median would be ≥ 3.41 — i.e. the recorded 3.41 is a lower
bound and already matches IK-2005's 3.3 within +3 %.

### Results archive (NPZ, schema v5)

The IDA is serialised into the unified NPZ as a first-class `ida` block
(schema v5 — see `docs/results_schema.md`).  `scripts/run_ik2005_npz.py` runs
`collapse_ida_curves()` (which now keeps the per-record drift-vs-IM curves, not
just the collapse scalars) and writes `output/ik2005_<code>.npz` carrying:

* the **model geometry** (nodes + frame members, IMK-enriched) — drawable in 3-D;
* the **per-record IDA curves** (`ida/<record>/{scale,drift,converged}`) and the
  collapse capacities (`ida/collapse_Sa_T1_g`) — the median (Sa,c/g)/γ and σ_ln.

Plots are one call away from the archive: `plot_ida_curves()` (IDA curves +
median) and `plot_collapse_fragility()` (P(collapse) vs Sa(T1)/g), re-exported
from `fea_toolkit.plotting`.  In the **FEA-GUI**, *File ▸ Open generic-frame
benchmark…* builds and displays the frame in 3-D, and *File ▸ Open results…* on
the archive registers an "IDA collapse" figure view (the two-panel summary).

### IDA plot markers — how to read the `plot_ida_curves` figure

`plot_ida_curves()` draws each record through its **converged** rungs only and
**stops the curve at its collapse intensity** — no rung at or past collapse is
joined to the line (a few frames that re-converge *after* non-convergence, e.g.
`LP89hch`, would otherwise overshoot their collapse and draw a spurious
"reversal").  The collapse is then marked **once per record**, by whichever
criterion tripped first:

| Marker | Criterion | Meaning |
|---|---|---|
| `×` (dark grey) | non-convergence | the **first** discrete ladder rung where the NLTH failed to converge — Ibarra–Krawinkler *dynamic instability*.  The recorded drift there is a **lower bound** (peak over converged steps only), not the true flatline response. |
| `– – >` horizontal flatline (dark grey) | non-convergence | the classic IDA **flatline**: the IM caps out at `Sa,c(T1)/g` and drift runs to infinity (the `>` = "drift → ∞").  The idealised, textbook form of the same `×`. |
| `⋮ ^` vertical dotted line (green) | drift > `collapse_drift` | the FEMA P-695 **sidesway proxy**: peak interstorey drift crossed `θ = 0.10` while the frame was *still converging*.  A **vertical** limit, drawn at `drift = 0.10` rising to the collapse IM. |

In one sentence: the **horizontal** markers (flatline / `×`) mean *"the IM could
not be pushed higher — the frame became dynamically unstable"*, while the
**vertical** marker means *"the drift limit was reached — the frame swayed past
θ = 0.10"*.  Both are read off `plot_ida_curves`'s per-record `collapse_criterion`
(see the split table above: 28 non-convergence / 5 drift for `0909`), and the
choice of `×` vs flatline is the `flatline=` flag (default `False` = `×`).

## Results — all 12 frames (33 LMSR-N records)

`scripts/run_ik2005_all_frames.py` ran `collapse_capacity_sa()` for every frame
(33 records each, P-Δ + held gravity, peak-oriented IMK, `max_scale = 128`),
archived per frame as `output/ik2005_<code>.npz` and summarised in
`output/ik2005_all_frames_summary.{json,md}` + `ik2005_median_vs_period.png`.
Paper values are read from IK-2005 **Fig 5.16** (median `(Sa,c/g)/γ` vector
markers; ≈ ±0.1 reading precision):

| code | N | T1 (s) | ours `(Sa,c/g)/γ` | σ_ln | paper (Fig 5.16) | Δ |
|---|---|---|---|---|---|---|
| 0303 | 3 | 0.3 | 4.11 | 0.38 | ≈ 4.0 | +3 % ✓ |
| 0306 | 3 | 0.6 | 5.52 | 0.42 | ≈ 5.6 | −1 % ✓ |
| 0606 | 6 | 0.6 | 6.12 | 0.49 | ≈ 5.9 | +4 % ✓ |
| 0612 | 6 | 1.2 | 2.44 | 0.47 | ≈ 2.8 | −13 % |
| 0909 | 9 | 0.9 | 3.41 | 0.55 | ≈ 3.4 | 0 % ✓ |
| 0918 | 9 | 1.8 | 1.19 | 0.51 | ≈ 5.2 | −77 % ✗ |
| 1212 | 12 | 1.2 | 2.57 | 0.64 | ≈ 6.0 | −57 % ✗ |
| 1224 | 12 | 2.4 | 0.64 | 0.57 | ≈ 3.0 | −79 % ✗ |
| 1515 | 15 | 1.5 | 1.90 | 0.59 | ≈ 6.2 | −69 % ✗ |
| 1530 | 15 | 3.0 | 0.40 | 0.66 | ≈ 2.8 | −86 % ✗ |
| 1818 | 18 | 1.8 | 1.31 | 0.51 | ≈ 6.2 | −79 % ✗ |
| 1836 | 18 | 3.6 | 0.26 | 0.62 | ≈ 2.8 | −91 % ✗ |

**Reading.**  The short-period frames (`T1 ≤ 0.9 s`: `0303`, `0306`, `0606`,
`0909`) reproduce IK-2005 to within ±4 %.  Every `T1 ≥ 1.2 s` frame undershoots,
and the shortfall grows with period.  The split is *by period, not by the
0.1N/0.2N stiffness label*: `1212` (0.1N) and `0612` (0.2N) both have
`T1 = 1.2 s`, yet `1212` undershoots 57 % while `0612` is within 13 %.

### Known deviations (why the long-period frames undershoot)

Two modelling deviations are now identified, both biting long-period frames
harder than short-period ones.  **These numbers record the pre-fix state, not a
validated match.**  Both are now fixed in the code (Rayleigh damping + the
drift proxy dropped) — see "Next steps".

1. **10 % drift proxy.**  `_COLLAPSE_DRIFT = 0.10` trips the FEMA P-695 sidesway
   proxy *before* true dynamic instability for the flexible (`0.2N`) frames —
   the `drift` criterion dominates the `T1 ≥ 1.2 s` flexible rows (`0918`,
   `1224`, `1530`, `1836`).  IK-2005's collapse is **dynamic instability
   (non-convergence) only**.  Disabling the proxy on a 4-record `0918` subset
   raised its median from 1.05 → ≈ 2.6 — real, but still below the paper's
   ≈ 5.2, so the proxy is not the whole story.  *(Fixed — `_COLLAPSE_DRIFT` is
   now `None`.)*

2. **Mass-proportional damping (wrong).**  The runner uses
   `damping_type="mass_proportional"` (`a1 = 0`, `a0 = 4πξ/T1`), but IK-2005's
   reference model uses **5 % Rayleigh damping on T1 + the mode at which
   cumulative mass participation exceeds 95 %** (§5.3.1; Appendix B.4 Eq B.8:
   `C = αM + βK₀`).  Mass-proportional damping under-damps every higher mode
   (`ξ_n = ξ·Tn/T1 < 5 %`), which grows the higher-mode drift — and the effect
   grows with period and storey count.  This is the leading suspect for the
   tall **stiff** frames (`1212`, `1515`, `1818`), which collapse via
   non-convergence (the *correct* criterion) yet still undershoot 57–79 %.

### Next steps

1. **IK-2003 first** — ✅ done (subset `0909`/`0918`/`1224`): the elastic anchor
   holds and the flexible frames no longer collapse prematurely.  See
   "IK-2003 validation (subset)" below.
2. **Fix the damping** to Rayleigh on `T1` + `T95 %` — ✅ landed
   (`_rayleigh_periods`, `_ida_run` now uses `damping_type="rayleigh"`).
3. **Drop the drift proxy** for the IK-2005 path — ✅ landed
   (`_COLLAPSE_DRIFT = None` → non-convergence only).  The collapse IDA was
   re-run on `0909` → `output/ik2005_rerun/ik2005_0909.npz`: median **7.94**
   (σ_ln 0.45) vs the paper's ≈ 3.4 — the `0909` overshoot that motivated the
   "Collapse criterion" analysis below.

### 40-record re-run (pending NGA-West2)

The medians above are 33-record numbers (7 PEER-only records absent).  Once the
7 NGA-West2 records are fetched (RSNs in "Acquisition status" above), re-run:

```bash
python scripts/run_ik2005_collapse.py --code 0909 --max-scale 128 \
    --json output/ik2005_40record/ik2005_0909.json
python scripts/run_ik2005_all_frames.py --out-dir output/ik2005_40record
```

No code change is needed — `fetched_ids()` returns 40 once the 7 ``.AT2`` files
are in `data/ground_motion/local/`.  Record the 40-record `(Sa,c/g)/γ` here:

| code | N | T1 (s) | ours `(Sa,c/g)/γ` (40 rec.) | σ_ln | paper (Fig 5.16) | Δ |
|---|---|---|---|---|---|---|
| *(pending)* | — | — | — | — | — | — |

## IK-2003 validation (subset)

`scripts/run_ik2003_validation.py` (via `generic_frame_run.drift_demands`)
reproduces IK-2003 (PEER 2003/15) §4.3 on three frames — 33 LMSR-N records,
Rayleigh damping, P-Δ + held gravity, no collapse detection:

| code | T1 (s) | PF1 | R=1 `θr,max/[Sd/H]` | R=2 | R=4 |
|---|---|---|---|---|---|
| 0909 (stiff) | 0.90 | 1.421 | **1.451** (33/33) | 1.365 (32/33) | 1.286 (32/33) |
| 0918 (flex) | 1.81 | 1.421 | **1.543** (33/33) | 1.412 (33/33) | 1.179 (27/33) |
| 1224 (flex) | 2.41 | 1.440 | **1.630** (33/33) | 1.380 (32/33) | 1.053 (20/33) |

*Reading.*  The elastic anchor (`θr,max/[Sd/H] ≈ PF1` at `[Sa/g]/γ ≈ 1`) holds
(+2 %/+9 %/+13 %; the offset grows with flexibility — the P-Δ signature).  The
key symptom the two bugs caused — `0918`/`1224` collapsing right after yield —
is gone: both stay converged through R=2 and mostly R=4 (27/33, 20/33), so
their dynamic-instability capacity is back in the right ballpark (paper ≈ 5.2
and 3.0, previously 1.19 and 0.64).  The normalised roof drift is roughly
constant, drifting modestly downward with intensity (the IK-2003
equal-displacement behaviour; dispersion, not the median, grows).

## Cross-checks (landed)

* **First-mode shape linearity** — the table's `Ii/I1` ratios are tuned for a
  straight-line first mode; `extract_mode_shapes` confirms `φ_i/φ_r = i/N` to
  < 0.1 % for the spot-checked frames (`tests/test_generic_frame_modal.py`).
* **Full modal match (Tables A.1–A.6)** — the MK-2003 Appendix-A modal tables
  (period ratios `Ti/T1`, participation factors `PFi`, mass participation `MPi`,
  modal damping `ξi`, mode shapes) are transcribed in
  `benchmarks/generic_frame_modal_data.py` and asserted in
  `tests/test_generic_frame_modal.py`: `Ti/T1` and `MPi` reproduce the paper to
  ≤ 0.002 (3 sig. figs), `|PFi|` to ≤ 0.01, and the Rayleigh `ξi` to ≤ 2 % over
  the lowest 3–4 modes (the top mode drifts ~10 % from the paper's rounded
  `Ti/T1` + discretised 95 %-participation anchor).
* **Damping** — IK-2005 assigns **5 % Rayleigh damping to the first mode and to
  the mode at which cumulative mass participation exceeds 95 %** (§5.3.1;
  Appendix B.4 Eq B.8, `C = αM + βK₀`), applied as `ops.rayleigh(a0, 0, a1, 0)`
  (initial-stiffness).  The plastic-hinge `zeroLength` springs need no βK
  exclusion: OpenSees `ZeroLength` elements ignore stiffness-proportional
  Rayleigh damping by default (`doRayleighDamping = 0`), so βK₀ lands on the
  elastic members only — the same zero-βK-on-springs rule Appendix B.4 imposes
  manually in DRAIN-2DX ("MODELING OF PLASTIC HINGES TO AVOID SPURIOUS DAMPING
  MOMENTS").  Appendix B.4 fixes the spring stiffness at **n = 10× the member**
  and boosts `β` by **×1.1** to compensate for the spring's missing βK; our
  `hinge_stiffness_factor = 100` makes the spring near-rigid so the member
  carries ≈ the full `Ke`, which is damping-equivalent to n = 10 + β×1.1 (both
  land βK₀ on the elastic members only).  `damping_type="mass_proportional"`
  (`a1 = 0`, `a0 = 4πξ/T1`) remains available for sensitivity runs.

## Collapse criterion — root-cause analysis

The `0909` rerun (Rayleigh + no drift proxy) gives median 7.94 (σ_ln 0.45) —
2.3× the paper's ≈ 3.4.  The NPZ archive (`output/ik2005_rerun/ik2005_0909.npz`)
was re-analysed **without re-running** to test whether the collapse *detector*
(non-convergence on a ×2 ladder + bisection) explains the overshoot.  Replaying
each record's stored `(scale, drift, converged)` ladder under different
detectors:

| Detector | median `(Sa,c/g)/γ` | σ_ln |
|---|---|---|
| non-convergence, coarse ladder (no bisection) | 11.77 | 0.51 |
| non-convergence + 1 % bisection (the run) | **7.94** | 0.45 |
| drift threshold 2 % | 2.12 | — |
| drift threshold 3 % | 3.13 | — |
| drift threshold 5 % | 4.23 | — |
| drift threshold 10 % (FEMA P-695 proxy) | 7.45 | 0.38 |
| flatline threshold (drift > 1.0) | 11.70 | 0.45 |

*Reading.*  The detector is **not** the main cause of the overshoot:

1. **Coarse ladder.**  The ×2 ladder over-reports ~1.5× (11.77); bisection
   recovers most of it (7.94) but anchors to the *blow-up* (drift→∞,
   `d_max` ≈ 2·10⁴ at the non-converged rung) rather than the instability
   *onset*.
2. **"Converged flatline" gap.**  Three records (`IV79e13`, `IV79e01`,
   `NR94stn`) carry a **converged** rung with `d_max` ≈ 7000–12000 — the frame
   is already a mechanism, but `converged_steps == num_steps` counts it as
   surviving, so collapse is reported one ladder rung late (`IV79e13`: flatline
   at scale 64, reported 75.75).  A large flatline threshold catches these but
   barely moves the median (11.77 → 11.70).
3. **The drift threshold dominates.**  The collapse intensity is set almost
   entirely by what drift defines "collapsed" (2 % → 2.12, 10 % → 7.45, ∞ →
   7.94), because the frame's IDA curve is **gradual**, not a sharp flatline —
   the drift grows smoothly to ~8 % at `Sa(T1)/g` ≈ 3.6 and only runs away
   (blow-up) near `Sa(T1)/g` ≈ 6–8.

**Conclusion.**  Even the most generous detector (10 % drift) reaches only
**7.45**, still 2.2× the paper.  The frame genuinely sustains 8–19 %
interstorey drift at `Sa(T1)/g` ≈ 3.6–6.7 and destabilises only near
`θu ≈ 15 %` member rotation, whereas IK-2005's frame flattens at ~5–10 % drift
(`Sa,c/g)/γ ≈ 3.4`.  The dominant gap is therefore the frame's **dynamic
stability**, not the detector — the leading suspects are the **P-Δ geometric
stiffness** (gravity `1.4 × seismic mass` held, `Kg` constant) and/or the
**post-capping softening** (`αc = −0.10`) not destabilising the frame as early
as IK-2005.  *(The backbone itself was verified correct — effective
member-level `αs`/`αc`/`δc/δy` reproduce 3 % / −10 % / 4 — so the search is in
the geometric-stiffness and gravity-load path, not the hinge mapping.)*

**Solver step-size and ladder-resolution checks (re-run `IV79e13`, `IV79cal`).**
To close the integrator side of the enquiry, the transient was re-run with
(i) `dt = 0.0025` and (ii) a √2-ratio IM ladder, at identical bisection
(`tolerance = 0.01`) and `max_scale = 128`:

| `IV79e13` config | collapse scale | `d_max` @ scale 64 |
|---|---|---|
| `dt = 0.005`, ×2 ladder (baseline) | 77.75 | 9195 (converged) |
| `dt = 0.0025`, ×2 ladder | 77.75 | 9152 (converged) |
| `dt = 0.005`, √2 ladder | 77.46 | 9195 (converged) |

- **Halving `dt` changes nothing** — the collapse scale is identical (77.75)
  and the flatline drift identical to 0.5 % (9195 vs 9152): the fixed 0.005 s
  Newmark step is already converged in time (40–180 steps/period).
- **The √2 ladder changes nothing either** (77.46 vs 77.75, 0.4 % — inside the
  1 % bisection tolerance): bisection already recovers the ladder resolution.
- The √2 ladder *does* expose the non-monotonic boundary: scale 45.25
  converges at `d_max = 127` and scale 64 at `d_max = 9195` (mechanism), yet
  scale 90.5 **fails early** (`d_max = 0.075`, non-converged) — confirming the
  `EnergyIncr` test "converges" a mechanism and that the collapse intensity is
  set by a fragile Newton failure, not the instability onset.
- A clean non-convergence record (`IV79cal`) repeats the null: `dt = 0.0025`
  gives 59.25 vs the archived `dt = 0.005` 57.25 (≈ 3 %, run-to-run noise).

**Ruling.** Step size and ladder resolution are *ruled out* as causes of the
overshoot.  The residual gap is the frame's dynamic stability (P-Δ /
post-capping), not the integrator or the detector's granularity.

**P-Δ and post-capping sensitivity (re-run `IV79e13`, `IV79cal`).**  The two
model levers were bracketed by re-running the same two records under overrides
(identical `dt` / ladder / bisection / `max_scale = 128`):

| config | `IV79e13` collapse scale | `IV79cal` collapse scale |
|---|---|---|
| baseline (P-Δ 1.4×, αc −0.1) | 77.75 | 57.25 |
| **P-Δ off** | **no collapse** (drift 1.05 @128, converged) | **no collapse** (0.74 @128) |
| P-Δ gravity 2.0× | 66.25 | — |
| αc = −0.3 | 31.875 | 36.875 |
| αc = −0.5 | 26.875 | — |

- **P-Δ is the destabiliser.**  With it off the frame never non-converges: it
  forms a mechanism (drift ≈ 1, converged) but has no negative geometric
  stiffness to push it into instability.  Its *magnitude* is roughly right,
  though — doubling the held gravity (1.4 → 2.0) moves the collapse only ~15 %.
- **Post-capping `αc` is the dominant lever.**  Steepening `−0.1 → −0.3` drops
  the collapse 2.4× (`IV79e13`) to 1.55× (`IV79cal`) — single-record capacities
  land at ≈ 2.9–4.0 `(Sa,c/g)/γ`, right around the paper's 3.4; `−0.5` goes a
  little further (≈ 2.4).  `IV79cal` is the cleaner read (its `αc = −0.3` rung
  blows up, drift → 3·10⁵); `IV79e13` fails *early* (drift 0.07, non-converged),
  so its exact value carries Newton fragility.

**Reading.**  A ~2.4× response to `αc` means the base `αc = −0.1` behaves ~3×
shallower than IK-2005's in the **dynamic** response (the static backbone was
already verified 3 %/−10 %/4).  Two live explanations: (i) the hinges do not
actually enter the post-capping branch — they yield, ride the `αs = 0.03`
plateau, and the frame only destabilises near `θu` — or (ii) the
mass-proportional → Rayleigh damping change (the same switch that, with the
drift-proxy drop, lifted 3.41 → 7.94) over-damps the frame enough to hold it off
the post-capping branch.  Resolved by hinge M–θ instrumentation below.

**Hinge M–θ instrumentation (done).**  `run_transient_analysis` gained an opt-in
`transient_record_hinge_trace` that records each hinge's moment–rotation per
step (`hinge_traces`), plus per-hinge `hinge_peak_rotations` /
`hinge_damage_rotations`.  At `IV79e13` im = 32 (6.8 % drift — the last
physically-meaningful rung before the flatline), the **roof beam `B9` is at
7.2·θy** (member-end) with its moment fallen to **0.66·My** (≈ 40 % down the
post-capping descent), and the **column bases are at 4.45·θy** (just past
capping, M = Mc = 1.09·My).  The moment *drops*, so the hinges genuinely enter
the post-capping branch — hypothesis (i) is **refuted** and the material /
backbone is correct.

**Reading (revised).**  The hinges yield → cap → post-cap exactly as calibrated,
but the frame only destabilises near `θu ≈ 15·θy` (complete hinge exhaustion):
it rides the post-capping descent from ≈ im 16 (first capping) to ≈ im 78
(non-convergence), because the post-capping negative stiffness + P-Δ only
overcomes the remaining elastic stiffness once the hinges are nearly exhausted.
Since `θu ∝ 1/|αc|`, the `αc` sweep is explained as `θu` halving (14.9 → 7.6·θy)
rather than a slope error.

**Damping sensitivity (done).**  `IV79e13` under damping overrides (same ladder
/ bisection / non-convergence):

| damping | collapse scale | `(Sa,c/g)/γ` |
|---|---|---|
| Rayleigh ξ = 5 % (baseline) | 77.75 | 6.96 |
| Rayleigh ξ = 2.5 % | 73.0 | 6.53 |
| Rayleigh ξ = 1.0 % | 16.0 | 1.43 |
| mass-proportional ξ = 5 % | 15.5 | 1.39 |

The frame sits on a **damping cliff**: collapse ≈ 77 at ξ ≥ 2.5 %, but crashes
to ≈ 16 at ξ ≤ 1 % (mass-proportional 5 % behaves like ~1 % Rayleigh because it
under-damps every higher mode, ξ_n = ξ·Tn/T1).  The collapse capacity is
**~5×-sensitive to the higher-mode damping** — the dominant lever, far bigger
than P-Δ or `αc` — and the frame's stability is ill-conditioned w.r.t. it.
This is the likeliest home of the 3.4-vs-7.94 gap: a residual damping- or
criterion-implementation difference between DRAIN-2DX (event-to-event stall at
the tangent-stiffness singularity) and OpenSees (Newton "converging" the
flatline) can flip the frame across the cliff.

**Mass-proportional 33-record median (done).**  The full 33-record LMSR-N run
was repeated with `damping_type="mass_proportional"` (ξ = 5 %,
`a0 = 4πξ/T1`, `a1 = 0`) and no drift proxy:

- median `(Sa,c/g)/γ` = **4.67** (σ_ln = 0.605), vs **7.94** (Rayleigh 5 %) and
  the paper's ≈ 3.4.

Mass-proportional damping *halves* the overshoot but does **not** reproduce 3.4
— the "DRAIN-2DX is effectively mass-proportional" hypothesis is **refuted**.

**Missing-record bound (done).**  The 33 records are 40 − 7.  The 40-record
median is the mean of the 20th/21st sorted values, so the 7 missing records
shift the median position by at most ~3.5 places (17 → 20.5), *not* 7:

- all 7 missing **below** the 33 → median = (a13 + a14)/2 = **4.27**;
- all 7 missing **above** the 33 → median = (a20 + a21)/2 = **5.61**.

The full 40-record mass-proportional median is therefore **bounded in
[4.27, 5.61]** — the paper's 3.4 sits *below the lower bound*.  **The missing
records cannot explain the gap**: no assignment of the 7 unknowns brings the
median to 3.4.

**DRAIN-2DX stiffness-singularity probe (done — refuted).**  The last live
criterion hypothesis was that DRAIN-2DX's event-to-event scheme detects
collapse as a *tangent-stiffness singularity* (the §5.2 "P-∆ = first-order
shear" state) that occurs at a **lower** IM than OpenSees Newton's
non-convergence, because Newmark's mass term ``(1/βdt²)M`` keeps the effective
stiffness positive and lets Newton "converge" the flatline.  Two diagnostics,
both under mass-proportional damping (the 4.67 baseline):

1. **Lowest-eigenvalue trace** (``ops.eigen("-fullGenLapack", 1)`` during the
   time loop).  For the low-capacity cliff record ``IV79e13`` the tangent
   *never* goes non-positive: ω² ≈ 47.8 (elastic) → 44.8 at im = 15.4
   (d_max 4.08 %, converged) → 45.4 at im = 16 (Newton fails at d_max 4.3 %) —
   a dynamic runaway with a positive-definite tangent.  For the median-central
   ``IV79wsm`` the tangent *does* go singular: at im = 64 the lowest ω² drops
   to **−86.8** at step ≈ 1000 and the drift blows up to 1.75·10⁴ before
   Newton finally fails ~5000 steps later — Newton integrates *through* the
   singularity (the mass term keeps ``K_eff`` SPD), but here the blow-up rung
   is *non-converged* (conv=False), not a silent survivor.
2. **Ladder inspection** (9 records, no bisection): every record collapses by
   clean non-convergence; the Rayleigh-run artefact of a *converged* rung at
   d_max ≈ 7000–12000 does **not** recur under mass-proportional damping.

**Refinement — the singularity is *not* earlier, and "tangent ≤ 0" is not a
collapse tripwire.**  Probing ``IV79wsm`` between 32 and 64 shows the three
candidate tripwires disagree:

| tripwire | ``IV79wsm`` IM |
|---|---|
| first *transient* negative tangent | 36 (ω² = −7.4 at step 3800, yet **fully converges**, d_max 8.8 % — a false positive) |
| Newton non-convergence (the run, bisected) | **38.9** (the im = 40 rung fails *early*, d_max 1.1 %, tangent positive — Newton fragility) |
| persistent negative tangent + unbounded drift (the paper's "drift without bounds") | **44** (ω² = −269, d_max 2.0·10⁴) |

The tangent singularity, where it occurs, sits at a **higher** IM (44) than the
bisected non-convergence (38.9); the transient-negative signal (36) is a false
positive because the frame recovers.  A DRAIN-2DX-style detector would
therefore *raise* the median, not lower it.

**Conclusion.**  The collapse criterion is **exonerated**.  Under
mass-proportional damping the non-convergence detector already implements the
paper's "drift grows without bounds" definition (the blow-up and the failure
coincide); a stiffness-singularity detector is either a no-op (``IV79e13``) or
reports collapse *later* (``IV79wsm``: 44 vs 38.9).  The 4.67 vs 3.4 gap is a
**genuine response difference**, not a detector artifact: the frame's physical
collapse IM is ~4.67, and the only lever found that moves it by more than ~15 %
is the **higher-mode damping content** (the 5× cliff) — the leading remaining
candidate, not the collapse criterion, the record set, P-∆ magnitude, αc, the
hinge material or the integrator.

## LMSR-N record set (IK-2005 Table 4.2)

The collapse comparison needs the 40-record **LMSR-N** set ("Large Magnitude
Small Distance – New", Medina 2002) — ordinary California records, Mw 6.5–6.9,
source-to-site 13–40 km, NEHRP site class D, no pulse-type near-fault
characteristics.  Transcribed verbatim from Ibarra & Krawinkler (2005), PEER
2005/06, **Table 4.2**:

| Record ID | Event | Year | Mw | Station | R (km) | PGA (g) |
|---|---|---|---|---|---|---|
| IV79cal | Imperial Valley | 1979 | 6.5 | Calipatria Fire Station | 23.8 | 0.078 |
| IV79chi | Imperial Valley | 1979 | 6.5 | Chihuahua | 28.7 | 0.270 |
| IV79cmp | Imperial Valley | 1979 | 6.5 | Compuertas | 32.6 | 0.186 |
| IV79e01 | Imperial Valley | 1979 | 6.5 | El Centro Array #1 | 15.5 | 0.139 |
| IV79e12 | Imperial Valley | 1979 | 6.5 | El Centro Array #12 | 18.2 | 0.116 |
| IV79e13 | Imperial Valley | 1979 | 6.5 | El Centro Array #13 | 21.9 | 0.139 |
| IV79nil | Imperial Valley | 1979 | 6.5 | Niland Fire Station | 35.9 | 0.109 |
| IV79pls | Imperial Valley | 1979 | 6.5 | Plaster City | 31.7 | 0.057 |
| IV79qkp | Imperial Valley | 1979 | 6.5 | Cucapah | 23.6 | 0.309 |
| IV79wsm | Imperial Valley | 1979 | 6.5 | Westmorland Fire Station | 15.1 | 0.110 |
| LP89agw | Loma Prieta | 1989 | 6.9 | Agnews State Hospital | 28.2 | 0.172 |
| LP89cap | Loma Prieta | 1989 | 6.9 | Capitola | 14.5 | 0.443 |
| LP89g03 | Loma Prieta | 1989 | 6.9 | Gilroy Array #3 | 14.4 | 0.367 |
| LP89g04 | Loma Prieta | 1989 | 6.9 | Gilroy Array #4 | 16.1 | 0.212 |
| LP89gmr | Loma Prieta | 1989 | 6.9 | Gilroy Array #7 | 24.2 | 0.226 |
| LP89hch | Loma Prieta | 1989 | 6.9 | Hollister City Hall | 28.2 | 0.247 |
| LP89hda | Loma Prieta | 1989 | 6.9 | Hollister Differential Array | 25.8 | 0.279 |
| LP89hvr | Loma Prieta | 1989 | 6.9 | Halls Valley | 31.6 | 0.134 |
| LP89sjw | Loma Prieta | 1989 | 6.9 | Salinas - John & Work | 32.6 | 0.112 |
| LP89slc | Loma Prieta | 1989 | 6.9 | Palo Alto - SLAC Lab. | 36.3 | 0.194 |
| LP89svl | Loma Prieta | 1989 | 6.9 | Sunnyvale - Colton Ave. | 28.8 | 0.207 |
| NR94cen | Northridge | 1994 | 6.7 | LA - Centinela St. | 30.9 | 0.322 |
| NR94cnp | Northridge | 1994 | 6.7 | Canoga Park - Topanga Can. | 15.8 | 0.420 |
| NR94far | Northridge | 1994 | 6.7 | LA - N Faring Rd. | 23.9 | 0.273 |
| NR94fle | Northridge | 1994 | 6.7 | LA - Fletcher Dr. | 29.5 | 0.240 |
| NR94glp | Northridge | 1994 | 6.7 | Glendale - Las Palmas | 25.4 | 0.206 |
| NR94hol | Northridge | 1994 | 6.7 | LA - Hollywood Stor FF | 25.5 | 0.231 |
| NR94lh1 | Northridge | 1994 | 6.7 | Lake Hughes #1 | 36.3 | 0.087 |
| NR94lv2 | Northridge | 1994 | 6.7 | Leona Valley #2 | 37.7 | 0.063 |
| NR94lv6 | Northridge | 1994 | 6.7 | Leona Valley #6 | 38.5 | 0.178 |
| NR94nya | Northridge | 1994 | 6.7 | La Crescenta-New York | 22.3 | 0.159 |
| NR94pic | Northridge | 1994 | 6.7 | LA - Pico & Sentous | 32.7 | 0.186 |
| NR94stc | Northridge | 1994 | 6.7 | Northridge - 17645 Saticoy St. | 13.3 | 0.368 |
| NR94stn | Northridge | 1994 | 6.7 | LA - Saturn St | 30.0 | 0.474 |
| NR94ver | Northridge | 1994 | 6.7 | LA - E Vernon Ave | 39.3 | 0.153 |
| SF71pel | San Fernando | 1971 | 6.6 | LA - Hollywood Stor Lot | 21.2 | 0.174 |
| SH87bra | Superstition Hills | 1987 | 6.7 | Brawley | 18.2 | 0.156 |
| SH87icc | Superstition Hills | 1987 | 6.7 | El Centro Imp. Co. Cent | 13.9 | 0.358 |
| SH87pls | Superstition Hills | 1987 | 6.7 | Plaster City | 21.0 | 0.186 |
| SH87wsm | Superstition Hills | 1987 | 6.7 | Westmorland Fire Station | 13.3 | 0.172 |

*Station-ID provenance.*  The IDs are the Medina (2002) record labels as
printed in IK-2005 Table 4.2, verified against the report's PDF text layer
(not an image OCR).  Three OCR misreads circulate in image-derived copies of
the table: "Agnes" → **Agnews** State Hospital, ``NR94cmp`` → **``NR94cnp``**,
``NR94flc`` → **``NR94fle``**.

### Acquisition status — 33/40 fetched

33 records are in the local mirror (`data/ground_motion/local/`), fetched from
two open archives; the other 7 exist only in PEER NGA-West2:

| Source | Count | Records |
|---|---|---|
| USGS NSMP archive (SMC / DDS-7) | 14 | `IV79cal` `IV79chi` `IV79cmp` `IV79e01` `IV79e12` `IV79e13` `IV79nil` `IV79pls` `IV79qkp` `IV79wsm` `LP89hch` `LP89hda` `LP89slc` `LP89svl` |
| CESMD (Vol2) | 19 | `LP89cap` `LP89g03` `LP89g04` `LP89gmr` `LP89hvr` `NR94cen` `NR94cnp` `NR94far` `NR94fle` `NR94glp` `NR94hol` `NR94lh1` `NR94lv2` `NR94nya` `NR94stc` `NR94stn` `NR94ver` `SF71pel` `SH87wsm` |
| **PEER NGA-West2 only** | 7 | `LP89agw` `LP89sjw` `NR94lv6` `NR94pic` `SH87bra` `SH87icc` `SH87pls` |

The 7 PEER-only records were confirmed absent from both open archives: CESMD's
Superstition Hills 1987 coverage is only the CDMG stations (not the USGS
`Brawley` / `El Centro Imp. Co. Cent` / `Plaster City`), the USGS NSMP online
archive has digitised only 3 of its ~20 Superstition Hills stations, and the
Agnews / Salinas / Leona Valley #6 / Pico & Sentous records are CSMIP/CDMG
recordings CESMD never digitised.  The NGA-West2 RSNs were guessed by hand and
are **unreliable — do not search by them** (RSN 1045 is Newhall – W Pico Canyon
Rd, RSN 1119 is Kobe Takarazuka — neither is a Northridge CSMIP station).
Search by **event + station** instead (names below are unambiguous) and pick the
component whose PGA matches Table 4.2: `LP89agw` (Agnews, PGA 0.172 g),
`LP89sjw` (Salinas – John & Work, 0.112 g), `NR94lv6` (Leona Valley #6,
0.178 g), `NR94pic` (LA – Pico & Sentous, 0.186 g), `SH87bra` (Brawley,
0.156 g), `SH87icc` (El Centro Imp. Co. Cent, 0.358 g), `SH87pls` (Plaster
City, 0.186 g).

**40-record re-run is automatic.**  Drop the 7 waveforms into
`data/ground_motion/local/` as ``<record_id>.AT2`` (e.g. ``LP89agw.AT2``) and
the runners pick them up with no code change — `fetched_ids()` returns 40 once
the files are present.  See "40-record re-run (pending NGA-West2)" under
"Results".

### Downloading them (you, not the repo)

The waveforms are **PEER NGA-West2** data, non-redistributable, so they are
fetched into the local-only mirror by whoever runs the benchmark — never
committed (`ground_motion_data.md` §3, §5).  Two routes:

**Route A — CESMD (free, no academic email — the recommended route).**  The
same 40 California recordings are in the **CESMD** archive
(<https://www.strongmotioncenter.org>), the merged USGS (NSMP) + California
Geological Survey (CSMIP) strong-motion data centre.  Its registration form is
*optional* ("not required as a condition of your access…"), so it needs no
academic/corporate account, and the records are US Government data (public
domain).  Search by event + station from the table above, download the
*processed* (Vol2) horizontal component, and write it as
`data/ground_motion/local/<record_id>.csv` (two columns ``time,accel`` in m/s²)
— the runner reads ``.csv`` directly.  (CESMD Vol2 files are header + ASCII
columns, not PEER ``.AT2``; convert the acceleration column to a plain CSV.)

This is automated by `scripts/fetch_cesmd_records.py` (19 of the 40 records —
see the acquisition table above: ``NOT_IN_CESMD`` skips the 7 PEER-only records
and ``VIA_USGS`` skips the 14 already fetched from the USGS NSMP archive).  Run
``--list`` first to verify the station-name mapping, then
``--download --email you@example.com`` (the client lives in
`fea_toolkit.io.cesmd`).  The 14 USGS records CESMD lacks (all of Imperial
Valley 1979 plus four Loma Prieta stations) come from the USGS NSMP archive via
`scripts/fetch_usgs_records.py`.

**Route B — PEER NGA-West2 (academic/corporate email).**
1. **Register** at <https://ngawest2.berkeley.edu> with a *university or
   corporate* email (personal addresses are rejected; sign-up is open Mon–Fri
   8 AM–8 PM PST; membership ~1 year).
2. **Search** by **event + station** (the two are unambiguous; do *not* trust
   hand-guessed RSNs).  Events: "Loma Prieta" (1989), "Northridge" (1994),
   "Superstition Hills" (1987).  Stations: Agnews State Hospital, Salinas –
   John & Work, Leona Valley #6, LA – Pico & Sentous, Brawley, El Centro Imp.
   Co. Cent, Plaster City.
3. **Download both horizontal components** (the two `.AT2` files, e.g.
   `RSN###_LOMAP_AGW000.AT2` / `…_AGW090.AT2`; skip the vertical "UP"
   component).  For each station, keep the component whose **PGA matches
   Table 4.2** (the NGA-West2 flatfile / record-catalog lists PGA per
   component; it is *not* the two horizontals' average).  Rename the kept file
   to `data/ground_motion/local/<record_id>.AT2` (e.g. `LP89agw.AT2`) — the
   runner reads `.AT2` natively via `read_peer_record`.  The two horizontals of
   a station can differ by ~70 % in `Sa(T1)`, so matching PGA (not just any
   horizontal) is essential.  Mind the download caps (~200 records / 2 weeks,
   400 / month) — 7 records ≈ 14 files is well inside.

**Route C — FEMA P-695 far-field (public-domain fallback).**  If CESMD stays
down, the **44-record FEMA P-695 far-field set** (ATC-63) is the documented
public-domain alternative — free, widely mirrored, and a standard
"ordinary far-field" collapse set with ~4 records in common with LMSR-N
(Capitola, Gilroy Array #3, El Centro Imp. Co., LA Hollywood Stor).  Its median
``(Sa,c/g)/γ`` differs slightly from IK-2005's ``≈ 3.3`` (different records),
so it validates the collapse *machinery* rather than reproducing the exact
number.  The **USGS NSMP archive** (`escweb.wr.usgs.gov/nsmp-data/`) holds only
the USGS-operated stations — it is missing most of the CSMIP California stations
that dominate the LMSR-N Loma Prieta / Northridge records — so it is **not** a
complete substitute.

Either way, run the collapse driver over the available records:

```bash
python scripts/run_ik2005_collapse.py --code 0909
```

This wraps `collapse_capacity_sa("0909", <33 ids>, ..., record_channels=USGS_CHANNELS)`
— the channel map selects, for each multi-component USGS record, the horizontal
component whose PGA matches Table 4.2.

**Processing caveat.**  CESMD's Vol2 processing differs slightly from PEER's
NGA re-processing (different band-pass corners); for these well-recorded events
the difference is small, but exact NGA reproduction needs the NGA files.

**Component caveat.**  Table 4.2 lists no component azimuth: Medina (2002)
selected *one* horizontal component per station at random.  Exact reproduction
needs that per-record component (Medina 2002 thesis); until it is transcribed,
the runner picks the horizontal component whose PGA matches Table 4.2 — for the
multi-component USGS records via ``USGS_CHANNELS`` in
`fea_toolkit.benchmarks.ik2005_records`, and for the CESMD records the single ``CH1``
channel — rather than silently assuming a component.  (Two CESMD ``CH1`` records,
`LP89gmr` and `NR94stc`, landed on the *other* horizontal than Table 4.2; the
rest match within ~10 %, a handful at 10–20 %.)

**Record-identity verification (done 2026-11-02).**  The 33 fetched records were
reduced to the same scalar properties IK-2005 Table 4.2 lists and compared
column-by-column:

- **PGA** — within ~2 % for the 14 PGA-selected USGS records; the CESMD ``CH1``
  records within ~5–15 % except the two known wrong components (`LP89gmr`
  +42 %, `NR94stc` +23 %) and a few processing outliers (`NR94glp` −17 %,
  `LP89hvr` −16 %, `NR94nya` −13 %).  The component selection is therefore
  *right* (PGA is a faithful proxy for Medina's random pick) for ~31/33 records.
- **PGV** — within ~10–20 % for most, with a few large outliers (`NR94stc`
  +110 %, `NR94far` +56 %) that point at processing differences (different
  band-passing), not the wrong record.
- **Record length** — systematically **longer** than Table 4.2's ``Rec. Length``
  for the USGS/NSMP Imperial Valley records (51–92 s vs 36–40 s) and the
  Northridge / San Fernando / Superstition CESMD records (37–79 s vs 25–40 s);
  the Loma Prieta CESMD records match (~40 s).  The paper's records are
  truncated to the strong-motion window, ours retain the coda.

**Truncation test (done).**  Re-running four records with the acceleration
truncated to Table 4.2's ``Rec. Length`` shows the length difference is **real
but wrong-direction** for explaining the gap:

| record | full-record collapse | truncated collapse |
|---|---|---|
| IV79wsm (57→40 s) | 38.9 | **49.8** |
| IV79qkp (92→40 s) | 15.7 | **19.2** |
| IV79cmp (57→36 s) | 35.9 | 35.9 (no change) |
| SF71pel (79→28 s) | 15.7 | 15.7 (no change) |

The coda *lowers* the collapse (extra energy → more cyclic deterioration), so
our longer records already sit *closer* to the paper's 3.4 than a faithful
truncation would.  **The record set is therefore essentially correct, and is
not the explanation for the 4.67-vs-3.4 gap** — correcting the length would push
the median the wrong way, and the two wrong-component records are only 2/33.

### Other IK-2005 record sets (Tables 4.3–4.4)

IK-2005 defines **three** record sets (List of Tables), but only **LMSR-N**
feeds the generic-frame collapse comparison (§5).  The other two are SDOF
parameter-study sets (Chapter 4) — recorded here so the lists are not lost
when the PDF is gone:

**NF-11** (Table 4.3) — 11 **near-fault** records with velocity pulses
(`Tp` ≈ 1–4 s), mostly NEHRP C/D:

| ID | Event | R (km) | Tp (s) |
|---|---|---|---|
| KB95obj | Kobe 1995 | 0.6 | 0.9 |
| NR94newh | Northridge — Newhall | 7.1 | 1.3 |
| NR94rrs | Northridge — Rinaldi RS | 7.5 | 1.0–1.2 |
| EZ92erzi | Erzincan 1992 | 2.0 | 2.1 |
| NR94sylm | Northridge — Sylmar | 6.4 | 2.1 |
| NR94wpi | Northridge — W Pico | 7.2 | 2.0–2.2 |
| IV79melo | Imperial Valley — Meloland | 0.0 | 2.6 |
| IV79ar06 | Imperial Valley — Array #6 | 1.2 | 3.4 |
| TW99tcu076 | Chi-Chi 1999 — TCU076 | 2.0 | 3.0–3.3 |
| LN92luc | Landers 1992 — Lucerne | 1.1 | 4.5 |
| TW99tcu075 | Chi-Chi 1999 — TCU075 | 1.5 | 4.0–4.1 |

**LD-13** (Table 4.4) — 13 **long-duration** records:

| Record | Mw |
|---|---|
| Western Washington, Olympia 1949 | 6.5 |
| Kern County, Taft 021, 1952 | 7.7 |
| Kern County, Hollywood 090, 1952 | 7.7 |
| Cerro Prieto, Imperial Valley 1979 cp237 | 6.5 |
| Llolleo, Chile 1985 | 8.0 |
| Viña del Mar, Chile 1985 | 8.0 |
| CHY015E / CHY025W / CHY046W / CHY087W / TCU120W / TCU123W / TCU138E, Chi-Chi 1999 | 7.6 |

IK-2005 also uses the synthetic **Pulse P2** (Alavi & Krawinkler 2001) as a
near-fault pulse idealisation — not a record set.  The **44-record FEMA P-695
far-field** set (ATC-63) is the public-domain *ordinary*-set alternative
("Route C" above); the **SAC** benchmark sets (LA 2 %-in-50 / 10 %-in-50,
Seattle, Boston) are the separate steel-moment-frame record sets, of which this
repo commits only the four Ohtori signals + `LA21` (see `ground_motion_data.md`
§6).

## Spring placement (landed)

The IK-2005 Appendix B splits each member-end spring's elastic stiffness
between the spring and the elastic beam-column, *arbitrarily* — the member-end
``Kmem`` is what matters.  The generator uses:

* **beam ends** — near-rigid ``imk_peak`` springs (``hinge_stiffness_factor =
  100`` so the elastic beam carries the full ``6EI/L``); the paper uses ``n = 10``
  (Appendix B.2, ``Ks = n·Kbc``) — a ~1 % difference, inside the modal-T1
  tolerance;
* **column bases** — a *total*-M-θ spring with ``k0 = Kc`` (the base has no
  elastic member below it), placed only at the base end via the per-end
  ``FrameElement.imk_params_i`` override;
* **upper columns** — elastic, **no springs** (MK-2003/IK-2005 model the columns
  as continuous elastic above the base spring).  The 34 near-rigid elastic
  column-end springs the lumped-hinge route used to stamp were a bookkeeping
  artefact and are now skipped (`_create_lumped_hinges` emits a spring only
  where an IMK calibration exists) — 20 springs total (18 beam + 2 base),
  matching the paper.

The per-end override (``imk_params_i`` / ``imk_params_j``) and the ``k0`` field
in ``imk_params`` are the only new machinery; the IK-2005 backbone maps to
``θp = 3θy``, ``mc_my = 1.09``, ``θpc = 10.9θy``, ``res = 0`` (§5.3.2), expressed
at the member end so the series spring+beam-column reproduces the member-level
backbone (Appendix B.3, Eq B.4–B.7).

## References

- Ibarra & Krawinkler (2005), PEER 2005/06 (global collapse).
- Medina & Krawinkler (2003), PEER 2003/15 (frame data, Appendix A).
- Ibarra, Medina & Krawinkler (2005), EESD 34(12). `10.1002/eqe.495`.
