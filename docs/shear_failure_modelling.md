---
title: "RC Shear-Failure Modelling (MCFT Capacity + Nonlinear Shear Backbone)"
description: "Two-layer RC shear-failure modelling: a simplified-MCFT member shear-capacity model and mode-of-failure reporter (ASCE 41 force-controlled check), plus a nonlinear shear backbone (SectionAggregator) for reproducing shear-critical frame failures. Validated against the Duong et al. (2007) frame."
status: "complete"
tags: [validation, shear, mcf, capacity, backbone, duong, force-controlled, reporter]
category: [analysis-types]
related: [vecchio_emara_benchmark.md, pushover_analysis.md, deprecation_plan.md]
---
# RC Shear-Failure Modelling

## Overview

Frame members in the toolkit's fibre pushover path are shear-rigid
(Euler–Bernoulli): the plain fibre section has axial-flexure response only.
This document describes the two complementary layers added to model **RC
shear**, driven by the shear-critical Duong et al. (2007) benchmark:

1. **Phase 1 — capacity model + mode-of-failure reporter.** A simplified
   Modified Compression Field Theory (MCFT) shear capacity per member, and
   a reporter that compares the shear demand history of a pushover against
   that capacity and returns the **failure sequence**.  This is the ASCE 41
   **force-controlled** action check, automated over the pushover.
2. **Phase 2 — nonlinear shear backbone in the element.** A trilinear
   force–shear-strain backbone (cracking → peak → degrading → residual)
   aggregated onto the fibre section's ``Vy``/``Vz`` DOFs so a
   flexibility-based ``forceBeamColumn`` actually *softens* when the member
   reaches its shear capacity — reproducing the post-peak strength loss and
   force redistribution of a shear-governed frame.

Both layers share one physics module:
`fea_toolkit/analysis/shear_capacity.py`.

---

## The Duong et al. (2007) frame — why this benchmark

Duong, Sheikh & Vecchio (2007), *ACI Structural Journal* 104(3) 304–313,
tested a one-span, two-storey, deliberately **shear-critical** frame at the
University of Toronto:

- c-c span 1900 mm; storey height 2100 mm; members 300 × 400 mm; fixed
  base.
- 4 No.20 top + 4 bottom; No.10 closed ties ∅11.3 @ 125 mm; cover 50 mm.
- f′꜀ = 42.9 MPa; longitudinal fy = 447 MPa; ties fy = 455 MPa.
- 420 kN axial per column (constant); monotonic lateral displacement at the
  top beam.
- **Stage 1: the lateral load reached ≈ 220 kN when the first-storey beam
  failed in diagonal tension at mid-span** — loss of load-carrying capacity
  and force redistribution.  Guner (2008, §4.8) located the two beam shear
  failures at 48 mm (beam 1S) and 68 mm (beam 2S); Kotsovos & Zygouris
  (2019) reproduced the stage-1 220 kN peak and the lower-beam failure
  location with their mode-of-failure method.

The fixture ``make_duong_frame()`` lives in
``tests/test_duong_benchmark.py`` (kN-m units, planar X–Z per the 3D-only
policy).

---

## Phase 1 — simplified-MCFT capacity and the reporter

### The capacity model

`member_shear_capacity(section, concrete, rebar, tie, units, axial, shear, moment, ...)`
implements the **simplified MCFT** (Bentz, Vecchio & Collins 2006; the CSA
A23.3-04 General Method, Clause 11, with a fixed θ = 35°):

- Mid-depth longitudinal strain
  εₓ = [M/dᵥ + 0.5·(−N) + 0.5·V·cotθ] / (2·Eₛ·Aₛ)   (N compression +ve)
- Crack-spacing parameter s_ze = s_z·35/(15 + a_g)   (a_g = 19 mm)
- β = [0.4/(1 + 1500·εₓ)] · [1300/(1000 + s_ze)]
- V꜀ = β·√f′꜀·b_w·d_v        (f′꜀ in MPa, b/d in mm, rescaled to model units)
- V_s = A_v·f_yv·d_v/s
- V_n = min(V꜀ + V_s, 0.25·f′꜀·b_w·d_v)
- V_cr = 0.33·√f′꜀·b_w·d_v   (diagonal-cracking limit)

Axial compression raises V꜀ (lowers εₓ); tension lowers it.  All results are
in **model force units** — the empirical √f′꜀·b·d term is rescaled from the
MPa/mm convention via `stress_scale_factor` / `length_scale_factor` /
`force_scale_factor`.

### The reporter

`report_shear_failure(builder, results)` consumes a pushover result recorded
with `record_element_forces=True` and, for every concrete member and step,
reduces the local end forces to `V` (max transverse shear), `M` (max
moment), `N` (compression +ve) and computes DCR = V / V_n.  It returns a
`ShearFailureReport`:

- `entries` — chronological list of `ShearFailureEntry` (member, step,
  control displacement, demand, capacity, DCR).
- `max_dcr` — peak DCR per member.
- `governing_elem` / `governing_step` / `governing_dcr` — the first
  exceedance (or the highest-DCR member if none).

```python
results = builder.run_pushover_analysis(
    {"DEAD": 1.0}, lateral_load_type="point",
    lateral_point_nodes=[tip], control_node_tag=tip,
    max_disp=0.08, num_steps=40, record_element_forces=True,
)
report = report_shear_failure(builder, results)
print(report.governing_elem, report.entries)
```

### Phase-1 validation

- **Duong frame** — the reporter flags the **first-storey beam as the
  shear-governing member** (DCR ≥ 1.0), columns stay < 1.0, and the failure
  sequence is beam 5 → beam 6 — matching the experimental mid-span diagonal
  failure and Guner's beam-1S-then-2S sequence.
- **Vecchio & Emara frame** — flexure-governed: **no** member reaches
  DCR = 1.0 (max ≈ 0.96 on a column).

### ASCE 41 relationship

Shear is a **force-controlled** action in ASCE 41: it is checked as a force
against a lower-bound strength (knowledge factor κ) rather than given a
deformation acceptance criterion.  Phase 1 is exactly that check executed at
each pushover step; the DCR is the ASCE 41 demand-to-capacity ratio.

---

## Phase 2 — nonlinear shear backbone in the element

### Configuration

`aggregate_shear` (AnalysisBuilder config) now accepts:

| Value | Effect |
|---|---|
| `False` | No shear aggregation (default; Euler–Bernoulli). |
| `True` / `"elastic"` | Elastic `GA_v` on `Vy`/`Vz` (`shear_area_factor` × gross A). |
| `"nonlinear"` | Trilinear `Hysteretic` backbone (cracking → peak → degrading → residual) built from the MCFT capacity model. |

Additional knobs:

- `shear_backbone` — explicit dict override `{v_cr, g_cr, v_n, g_n, v_r,
  g_r}` (model units), applied to every aggregated section.  `None`
  (default) auto-derives the backbone per section.
- `shear_area_factor` (default 5/6) — rectangular shear area `A_v = f·A`.

The `Hysteretic` material is emitted by
`_wrap_fiber_section_with_shear()`; the backbone values come from
`shear_backbone()` in `shear_capacity.py`.  The aggregation is only engaged
by flexibility-based elements — set `fiber_element_type = "forceBeamColumn"`
(the builder warns otherwise).

### The auto-derived backbone

`shear_backbone()` evaluates the capacity at a **cracked-state strain**
(default εₓ = f_y/Eₛ, the longitudinal steel at first yield — the
flexure-shear interaction state), giving e.g. **V_n ≈ 295 kN** for the Duong
beam, with:

```
g_cr = v_cr / GA_v          (elastic → cracking)
g_n  = g_cr + (v_n − v_cr) / (0.03·GA_v)     (post-crack branch)
g_r  = g_n + (v_n − v_r) / (0.05·GA_v)       (degrading branch)
v_r  = 0.20·v_n                              (residual)
```

A global `shear_backbone` dict overrides the auto values — this is the
"user-supplied backbone" path (Guner 2008 §2.3.7 describes the equivalent
expert pre-calculation with MCFT/Response-2000).

### Phase-2 validation (Duong frame)

Three pushovers (single point load at the top beam, `forceBeamColumn`,
rigid end zones, PΔ):

| Model | Peak base shear | Notes |
|---|---|---|
| Flexure-only (no shear) | ≈ 347 kN | 1.58 × experimental — ignoring shear overestimates |
| Nonlinear, **auto** MCFT backbone (V_n ≈ 295) | ≈ 345 kN | mechanism engaged, peak barely reduced |
| Nonlinear, **explicit** backbone (V_n ≈ 200) | **≈ 227 kN** | **1.03 ×** experimental (220 kN) |

With the explicit (empirical) backbone the model reproduces the
experimental **stage-1 response**: a peak of ≈ 227 kN at ≈ 12 mm, then the
classic **two-step shear-failure descent** (≈ 148 kN after the first beam
fails, ≈ 98 kN after the second) — the same beam-1S-then-2S sequence Guner
predicted.  All steps converge.

### Known limitation — MCFT vs Kotsovos capacity

The simplified-MCFT capacity of the Duong first-storey beam (≈ 295 kN at
the cracked state) is **~1.5 × the effective capacity implied by the
stage-1 experiment** (the beam's shear demand at 220 kN base shear is
≈ 180 kN).  This is the documented difference between MCFT-family models
(ours and Guner's DSFM both peak in the 300–350 kN range) and the
Kotsovos compressive-force-path model (which matched the 220 kN stage-1
peak).  Consequences:

1. **The reporter's failure MODE is correct** (first-storey beam first), but
   its absolute load level is conservative-high on this frame.
2. **The auto backbone cannot reproduce the 220 kN peak**; the explicit
   (empirical) backbone does.  Supplying the backbone is the documented
   expert step (Guner's "user-defined shear hinges").

Post-peak *shape* also depends on the degrading branch slope — a future
nonlinear cracked-shear (DSFM-style) constitutive would close the gap.

---

## Phase 3 — Elwood & Moehle column limit states (planned 🚧)

Targets the **shear-failure → axial-failure collapse sequence** of
high-axial-load RC columns (e.g. core / outrigger columns of 500 m towers,
where a shear failure at moderate drift can shed the gravity load and
precipitate progressive collapse).  Phase 0 (prototype) is complete and the
physics is validated; builder integration is next.

### Physics (PEER 2003/01)

Two empirical drift-capacity models bound the column response.  The
**drift at shear failure** is a function of the current shear `V` and axial
`P`; OpenSees solves it for the force on the limit surface
(`ShearCurve::findLimit`):

```
V = 500·(0.03 + δ + 4·ρ″ − DR − 0.025·P/(b·h·(f′c/1000)))·(b·d·√f′c/1000)   [kip, in, psi]
```

with a 1%-drift floor.  The **drift at axial failure** comes from the
shear-friction model (`AxialCurve::findLimit`), crack angle θ = 65°:

```
P(DR) = ((1+tan²θ)/(25·DR) − tanθ)·F_sw·tanθ,   F_sw = A_st·f_yt·d_c/s
```

With `ρ″ = A_st/(b·s)` and a **positive** shear `K_deg` (the flexural
unloading stiffness), the Shear curve sets the post-failure degrading slope
so the shear capacity reaches zero exactly at the axial-failure drift —
this is the shear→axial coupling.  After axial failure the axial spring
degrades along `K_deg = −0.02·E_c·A_g/L` to the residual `F_res`.

### Implementation status

| Piece | Status |
|---|---|
| `analysis/elwood_limit_state.py` — parameters (`F_sw`, `ρ″`, `d_c`), unit-agnostic drift equations, ThreePoint surface fit, `LimitState` envelope, spring slopes | ✅ Complete (tests: `tests/test_elwood_limit_state.py`) |
| `convert_mesh_units()` kip-in enabler (`model/units.py`; OpenSees `limitCurve` is imperial-embedded) | ✅ Complete (round-trip test: `tests/test_mesh_units.py`; workflow: `docs/units_conversion.md`) |
| Builder centre-spring emission (`limitCurve Shear` + `ThreePoint` + `LimitState` at column mid-height) | 🚧 Planned |
| Column selection (8 RC outrigger columns only) | 🚧 Planned |
| Dynamic (transient) driver | 🚧 Planned (after the material model) |

### OpenSeesPy 3.8.0 binding constraints (validated in Phase 0)

1. **`ops.limitCurve("Axial", ...)` is broken** — silent `OpenSeesError` for
   every arg layout (the binding can never produce the C++ handler's
   required `argc ∈ {9,12,14,15}`).  Hard constraint.
2. **`ops.limitCurve("Shear", ...)` and `limitCurve("ThreePoint", ...)`
   work** (the exact arg forms used by the prototype).
3. **Workaround** — the axial capacity surface is emitted as a ThreePoint
   curve pinned to the operating gravity load:
   `(0, plateau) → (DR_a(P_g), P_g) → (8%, P(8%))`, with `forType=2`
   (beam-column axial force).  Two non-obvious requirements discovered from
   the C++ source: `ThreePointCurve::findLimit` returns **0 for x < x1**
   (so `x1` must be 0), and the ordinate must be the **column axial force**
   (forType=2), not the spring force.

### Validation (Phase-0 prototype, `local/elwood_prototype.py`)

Single-column pushover in kip-in reproducing Elwood's `CenterCol` example
(Specimen-2 configuration: 9×9 in, L=58 in, f′c=3.52 ksi, P=70 kip):

| Quantity | Prototype | Published (PEER 2003/01) |
|---|---|---|
| Drift at shear failure | **2.10%** | **2.1%** (test *and* analytical model) |
| Drift at axial failure | **4.64%** | 5.2% (3-column frame, live axial redistribution) |

The axial-failure gap is the frame effect: Elwood's beam sheds centre-column
axial load between 2.1% and 5.2% drift; the isolated column (constant
P ≈ 69 kip) triggers at 4.64%, matching the constant-P shear-friction value
(4.58%).  Cyclic response shows the same shear-failure trigger and
post-failure degradation to the shear residual.

### Units

The toolkit's unit-agnostic layer rescales to the OpenSees **kip-in-ksi**
basis internally for the drift equations (the drift ratios returned are
dimensionless and unit-invariant across kip-in, N-m, kN-m, ...).  At
``limitCurve Shear`` emission the builder feeds ``fc`` in **psi** (×1000 from
the model's ksi value) — the only stress input the empirical curve expects.
For SI models the domain is built in kip-in-ksi via
:func:`fea_toolkit.model.units.convert_mesh_units`; the complete kN·m·s →
kip·in workflow (conversion factors, step-by-step, results back-conversion,
round-trip guarantee) is documented in ``docs/units_conversion.md``.

### References (additional)

7. Elwood, K.J. & Moehle, J.P. (2003). *Shake Table Tests and Analytical
   Studies on the Gravity Load Collapse of Reinforced Concrete Frames*,
   PEER 2003/01, UC Berkeley.
8. OpenSees source: `SRC/material/uniaxial/limitState/` (`ShearCurve.cpp`,
   `AxialCurve.cpp`, `ThreePointCurve.cpp`, `LimitStateMaterial.cpp`).
9. Implementation: `fea_toolkit/analysis/elwood_limit_state.py`,
   `tests/test_elwood_limit_state.py`, `local/elwood_prototype.py`,
   `local/elwood_phase0_checkpoint.md`.

---

## References


1. Duong, K.V., Sheikh, S.A. & Vecchio, F.J. (2007). “Seismic Behavior of
   Shear-Critical Reinforced Concrete Frame: Experimental Investigation.”
   *ACI Structural Journal* 104(3) 304–313.
2. Bentz, E.C., Vecchio, F.J. & Collins, M.P. (2006). *ACI Structural
   Journal* 103(1) 50–59.
3. CSA A23.3-04, Clause 11 — General Method.
4. Guner, S. (2008). PhD thesis, University of Toronto (§2.3.6/§2.3.7,
   §4.8, Table 2.7).
5. Kotsovos, G.M. & Zygouris, N.S. (2019). *Magazine of Concrete Research*
   71(3) 109–125.
6. Implementation: `fea_toolkit/analysis/shear_capacity.py`,
   `tests/test_duong_benchmark.py`.


