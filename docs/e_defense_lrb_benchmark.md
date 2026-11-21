---
title: "E-Defense Base-Isolated Frame (LRB-CLB) Benchmark (T4)"
description: "Full-model shake-table benchmark: E-Defense 5-storey base-isolated steel frame (LRB-CLB) — fixed-base and isolated periods and JMA-Kobe NLTHA."
status: "partial"
tags: [validation, benchmark, base-isolation, LRB, NLTHA, shake-table]
category: [planning]
related: [benchmark_register.md, nonlinear_link_plan.md, modeling_guidance.md]
---

# E-Defense base-isolated frame (LRB-CLB) benchmark

**Status: ⚠️ Partial** — the fixed-base and isolated modal-period checks are
complete and reproduce the published periods, but the NLTHA response is
validated only against a JMA-Kobe stand-in (the 100 % Iwanuma record is still
pending K-NET download), so the combined modal/NLTHA entry is not fully closed.

## Reference

- Sato, E., Furukawa, S., Kakehi, A., Ryan, K.L. (2011). *Full-scale shaking
  table test for the examination of safety and functionality of base-isolated
  medical facilities.* Earthquake Engng Struct. Dyn. 40(13):1435–1453.
  DOI 10.1002/eqe.1116.
- Sasaki, T., Sato, E., Ryan, K.L., Okazaki, T., Mahin, S.A., Kajiwara, K.
  (2012). *NEES/E-Defense Base-Isolation Tests: Effectiveness of Friction
  Pendulum and Lead-Rubber Bearings Systems.* WCEE 2012, paper 2054 (also NRC
  ML13127A019).
- Higashino, M. (2018). *Response of hybrid isolation system during a shake
  table experiment of a full-scale isolated building.* Earthquake Engng Struct.
  Dyn., DOI 10.1002/eqe.3065.
- Huang, N.Y., Whittaker, A.S., Kennedy, R.P., Mayes, R.L. (2009). *Assessment
  of base-isolated nuclear structures for design and beyond-design basis
  earthquake shaking.* Tech. Report MCEER-09-0008, University at Buffalo.
- Qin Dong, Okazaki, T., Midorikawa, M., Ryan, K.L., Sato, E., Sasaki, T.
  (2013). *Analysis of shake-table tests of a full-scale building isolated by
  lead-rubber bearings.* hdl.handle.net/2115/54236.
- Lignos, D.G., et al. (2012). *Modeling and experimental validation of a full
  scale 5-story steel building equipped with triple friction pendulum
  bearings: E-Defense blind analysis competition.* 9th CUEE / 4th ACEE, Tokyo.
- Dao, N.D., Ryan, K.L. (2015). *Seismic response of a full-scale 5-story steel
  frame building isolated by triple pendulum bearings under 3D excitations.*
  Report No. CCEER 15-01, University of Nevada, Reno.

## Test building

A full-scale, **2×2-bay, 5-storey steel moment frame**, 10 m (X) × 12 m (Y) in
plan, 15.8 m tall.  Fixed-base fundamental period **0.68–0.70 s** (measured
0.70 s via white-noise excitation; analysis 0.69 s).  The member schedule is
from Lignos et al. (2012, Tables 1–2) and CCEER 15-01 (Dao & Ryan 2015):

- Columns: **HSS 350×350**, thickness tapering **22 → 12 mm** down the height
  (three column lines C1/C2/C3 at 12/16/19/22 mm per storey).
- Girders: primary beams are a rolled **H-400×200×9×12** middle segment with
  built-up **BH-400×200×12×16/19/22** haunched end segments (all 400 mm deep);
  modelled here as a single built-up **BH-400×200×12×19** section with the
  composite action (flexural stiffness ×2 for the slab on both sides).
- Base girders: **BH-900×500×16×28** (the rigid base grillage above the
  isolators).
- All girder-to-column and column-base connections fully restrained.

Seismic weight: the measured total is **W = 5420 kN** (Qin Dong 2013; Lignos
2012 quotes 4467 kN fixed-base / 5529 kN isolated, the difference being the
~1 MN base grillage above the isolators).  This model lumps the full 5420 kN
at the floor levels (the base-grillage mass is not separated out — see the
notes).

## Isolation system (LRB-CLB configuration)

| Bearing | Count | Model |
|---|---|---|
| Lead-rubber (LRB) | 4 (edge columns) | `Rubber Isolator` → `elastomericBearingPlasticity` |
| Cross-linear (CLB) | 5 (corners + centre) | bidirectional roller → `Linear` link (vertical only) |

The LRBs use the manufacturer bilinear model — K0 = 6.5 kN/mm, KF = 0.65 kN/mm,
FU = 73 kN, vertical KC = 1500 kN/mm.  The CLBs have negligible horizontal
stiffness and very large vertical stiffness (μ = 0.0019, ~0.9 kN friction —
two orders below the LRB yield, so omitted).  Horizontal restoring force comes
from the four LRBs only.

## Published targets vs. this model

| Quantity | Published (measured) | This model |
|---|---|---|
| Fixed-base period | 0.70 s | 0.70 / 0.73 s (X / Y) |
| Isolated period | 2.86 s (post-yield) | 2.98 s |
| Peak isolator displacement | **347 mm** (100 % Iwanuma); ≤ **550 mm** overall | 0.26 m (JMA-Kobe stand-in) |
| Peak base shear | **1187 kN = 0.224 W** (100 % Iwanuma) | — (stand-in) |
| Peak storey drift ratio | < 1/200 | 0.0015 |

The measured isolator-displacement / base-shear values are from Ryan et al.
(2012, WCEE paper 2054) for the **100 % Iwanuma** motion; the ≤ 550 mm overall
peak is from Higashino et al. (DOI 10.1002/eqe.3065).  The JMA-Kobe stand-in
is less intense than Iwanuma, so its 0.26 m is a lower-bound sanity check, not
an exact match to the 347 mm target.

## Ground motion

The two isolation systems were driven by **different** motion sets (Ryan et al.
2012, paper 2054; CCEER 15-01 §4):

- **TPB** (Phase B): a wide variety of real records — JMA Kobe (25/50/100 %),
  JR-Takatori (40/60 %), Rinaldi (88 %), ChiChi TCU065, Tabas, K-NET Iwanuma.
- **LRB-CLB** (this benchmark): a mix — recorded motions (**80 % Westmorland**,
  **100 % Iwanuma**, **88 % Rinaldi**, analysed in Ryan et al. 2012 paper 2054)
  plus design-basis **synthetic Vogtle / Diablo Canyon** nuclear-site motions
  (Huang et al. 2009).  Measured LRB-CLB response: peak base shear
  **1187 kN = 0.224 W** (100 % Iwanuma) and peak isolator displacement up to
  **~550 mm** (Higashino et al., DOI 10.1002/eqe.3065).

Those records are not freely redistributable (and the synthetic Vogtle/Diablo
motions are site-specific), so this benchmark drives the LRB-CLB model with the
**JMA Kobe Observatory N-S** record (`data/ground_motion/local/KOBE.csv`,
`dt = 0.02 s`, m/s², manifest `KOBE`) as a stand-in of comparable intensity —
the modal targets (fixed-base 0.70 s, isolated 2.9 s) are motion-independent;
the NLTHA peak displacement is a sanity check against the published
~0.55 m / 0.224 W, not an exact match.

## Notes / approximations

- The superstructure is linear-elastic (as in Qin Dong et al. 2013).
- The CLB is modelled as a vertical roller, not a friction slider — its
  friction is negligible relative to the LRB yield strength.  The friction
  pendulum path is exercised by the TPB configuration (`config="tpb"`), which
  uses OpenSees' `TripleFrictionPendulum` element (see
  `tests/test_e_defense_tpb.py`).
- The three haunched girder segments are represented by a single built-up
  `BH-400×200×12×19` section; the column lines C1/C2/C3 are collapsed to a
  single 22→12 mm taper.  Together with the composite ×2 factor these
  reproduce the published fixed-base period to ~4 %.
- The full 5420 kN is lumped at the floor levels; the ~1 MN base-grillage mass
  is not separated onto the base level (it does not affect the isolation
  period, which depends only on the total mass).
- The isolated model has a soft rocking mode (isolators release all rotations)
  at ~3.4 s that carries no translational mass; the benchmark selects the
  horizontal modes by mass participation.
