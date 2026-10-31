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
> Appendix-B design value 1.0 to ≤ 2 % — both record-free.  The collapse (IDA)
> runner `generic_frame_run.py` landed (P-Δ + held gravity + 5 % mass-proportional
> damping, smoke-tested on a synthetic pulse).  33 of the 40 LMSR-N records are
> now fetched locally (19 CESMD + 14 USGS NSMP); the remaining 7 are PEER
> NGA-West2-only.  `scripts/run_ik2005_collapse.py` drives the 33-record IDA on
> `0909` — median `(Sa,c/g)/γ = 3.41` vs the ≈ 3.3 target (see "Results").

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
3. ✅ IDA collapse `(Sa,c/g)/γ ≈ 3.3` at `T1 ≈ 1 s` — median **3.41** over the
   33 fetched LMSR-N records (vs ≈ 3.3, +3 %); dispersion σ_ln = 0.56 vs
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
| Dispersion σ_ln | **0.56** | ≈ 0.42 (Fig 5.18c) | +33 % ⚠️ |

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
instability records alone gives 0.56, unchanged.  The 0.56-vs-0.42 gap therefore
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

## Cross-checks (landed)

* **First-mode shape linearity** — the table's `Ii/I1` ratios are tuned for a
  straight-line first mode; `extract_mode_shapes` confirms `φ_i/φ_r = i/N` to
  < 0.1 % for the spot-checked frames (`tests/test_generic_frame_modal.py`).
* **Mass-proportional damping** — IK-2005 §4.3 uses ξ = 5 % proportional to mass
  only.  `run_transient_analysis` gained a `damping_type="mass_proportional"`
  option (`a1 = 0`, `a0 = 4πξ/T1`), the collapse runner uses it, and
  `rayleigh_coefficients` is unit-tested (`tests/test_runner_dynamic.py`).

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
recordings CESMD never digitised.  Their NGA-West2 RSNs (verify against the
flatfile): `SH87bra`≈718, `SH87icc`≈719, `SH87pls`≈720, `LP89agw`≈758,
`LP89sjw`≈762, `NR94lv6`≈1045, `NR94pic`≈1119.

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
2. **Search** by earthquake + station from the table above (e.g. event
   "Imperial Valley-06", station "Calipatria Fire Station").
3. **Download** the `.AT2` waveform into `data/ground_motion/local/<record_id>.AT2`
   (the runner reads `.AT2` natively via `read_peer_record`; `.csv` also works).
   Mind the download caps (~200 records / 2 weeks, 400 / month).

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
`scripts/run_ik2005_collapse.py`, and for the CESMD records the single ``CH1``
channel — rather than silently assuming a component.  (Two CESMD ``CH1`` records,
`LP89gmr` and `NR94stc`, landed on the *other* horizontal than Table 4.2; the
rest match within ~5 %.)

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
