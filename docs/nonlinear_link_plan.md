---
title: "Nonlinear link properties — plan (P49 Phase 2)"
description: "Plan for parsing SAP2000/ETABS 03+ link-property tables and emitting nonlinear OpenSees link elements; records the Hysteretic-backbone decision, the one-time cyclic check and its recipe requirement, and the ENT/ElasticPP comparison tests."
status: "draft"
tags: [links, isolators, dampers, nonlinear, opensees, parser, workflow, s2k, e2k]
category: [core-pipeline]
related: [_pending_work.md, modeling_guidance.md, e2k_import_plan.md, workflow_authoring.md]
---
# Nonlinear link properties — plan (P49 Phase 2)

> **Status: 🚧 Draft — plan only.** Phase 1 (the linear / effective-stiffness
> link representation) is live; see [`_pending_work.md` → P49](_pending_work.md).
> This documents the design, the decisions, and the sequencing for the
> nonlinear phase. Nothing in §2–§12 is implemented yet.

## 1. Scope and goal

Phase 1 parses `LINK PROPERTY DEFINITIONS 01 - GENERAL` / `02 - LINEAR` (and
the ETABS `$ LINK PROPERTIES` equivalent) and emits a `twoNodeLink` — or a
`zeroLength` for a grounded link — with one `Elastic` spring per active DOF,
using the effective stiffness.  That makes elastic static / modal /
response-spectrum analyses work for *all* link types, linear or not.
`link_model: "nonlinear"` currently raises `NotImplementedError` for any type
other than `Linear`.

Phase 2 adds the *nonlinear* representations, with two goals:

* **Nonlinear analysis** — emit the type-specific nonlinear material/element
  so a rubber/friction isolator, gap, hook, Wen link or damper responds
  nonlinearly.
* **Honest linearisation** — when `link_model: "elastic"` (the default) is used
  on a nonlinear link, **warn** that the effective-stiffness substitute is in
  use, instead of silently linearising.

**This pass = items 1–4** (parse → emit → dispatch → warnings,
component-tested).  Benchmarks and the stretch link types are a follow-up
(§9, §10).

## 2. Decisions

| # | Decision | Choice | Alternative (noted, not acted) |
|---|---|---|---|
| D1 | Hook (tension-only) | `Hysteretic` backbone | `ElasticPP` — rejected (§5) |
| D2 | Gap (compression-only) | `Hysteretic` backbone (uniform with D1) | `ENT`/`ElasticPPGap` — simpler, rejected for "one model" |
| D3 | Bilinear / multilinear / friction dampers | `Hysteretic` backbone | per-type materials — rejected (a "model zoo") |
| D4 | Friction isolator μ | `Coulomb` first | `VelDependent` — added later via a one-line branch (§6) |
| D5 | Tuning | pinching/damage pinned neutral; backbones auto-derived | expose knobs — rejected |
| D6 | Scope | items 1–4 (parse → emit → dispatch → warnings) | benchmarks + stretch types = follow-up |
| D7 | `Hysteretic` neutral defaults | guarded by a **one-time cyclic check** (§7) | none |
| D8 | That cyclic check | also exposed as a **workflow verb**, so a generated model's script re-runs it (§7) | a test-only check — rejected |
| D9 | Compression-only validation | `ENT` kept as a validation reference; `Hysteretic` compression-only compared to it in `tests/test_link_nonlinear.py` (§7) | none |
| D10 | Plastic (Wen) material | **`Steel02` default** (smooth, explicit `Fy`, `R0`↔`n`); `Hysteretic` opt-in (`wen_model`) | `BoucWen` — rejected: no explicit `Fy`, six unexported knobs (§4) |

## 3. Architecture — data model and parsing (Phase 2a)

The free-form `LinkProperty.nonlinear: dict[str, Any]` is replaced by a
**discriminated sub-dataclass**, one per link type, all fields optional so a
partial parse is still valid (dataclass-first, `.clinerules` §3.3):

* `RubberIsolatorParams` — `fy`, `k2_ratio` (plus the existing `ke`/`ce`).
* `FrictionIsolatorParams` — `mu`, `mu_fast`, `rate`, `radius` (plus `ke`).
* `GapParams` / `HookParams` — `ke`, `open`.
* `WenParams` — `ke`, `fy`, `ratio`, `n`.
* `DamperExponentialParams` — `ce`, `cexp`; `DamperLinearParams` — `ce`;
  `DamperViscoelasticParams` — `ke`, `ce`; `DamperBilinearParams` — `ke`,
  `fy`, `k2_ratio`; `DamperFrictionParams` — `mu`, `ke`, `fy`.

Parsing normalises two formats that diverge:

* **S2K** spreads nonlinear data across numbered `LINK PROPERTY DEFINITIONS
  03+` tables (the registry already confirms `03 - PLASTIC (WEN)` and
  `05 - GAP`); the exact numbering/columns for the isolator, hook and damper
  tables are confirmed from a real `.s2k` export at implementation time — we
  do not invent column names.
* **E2K** packs the same data into per-DOF `LINKPROP` records; the tokens
  `NONLIN/K/OPEN/F/YIELD/R/MU/RADIUS` are already stashed in
  `prop.nonlinear[dof]` and are interpreted into the same sub-dataclasses.

**Units.** These values come *from the model file*, so they are already in
model units — like frame loads, not like our SI-authored material constants.
μ is dimensionless; `radius`/`open` are length; `fy` is force; `ke` is
force/length.  **No `scale_material_dict()`** — the §4.6 "author in SI, scale
once" convention does not apply to values read from the model.

## 4. OpenSees emission (Phase 2b) — the mapping

`AnalysisBuilder._create_link_elements` branches on `link_type` into two
families:

* **A. Per-DOF uniaxial materials** (Gap, Hook, Wen, dampers, multilinear) —
  the existing `twoNodeLink`/`zeroLength` loop, with `_link_spring_mats`
  returning a *material kind* per DOF instead of always `Elastic`.
* **B. Dedicated bearing elements** (isolators) — replace the link element
  entirely, since these couple U2/U3 shear and need a friction model.

| Link type | OpenSees target | Source → target params | Fidelity |
|---|---|---|---|
| Linear | `twoNodeLink` + `Elastic` (unchanged) | `Ke`, `Ce` | exact |
| Gap | `twoNodeLink` + `Hysteretic` (compression branch = `Ke`, tension ≈ 0) | `Ke`→slope, `open`→backbone shift | good |
| Hook | `twoNodeLink` + `Hysteretic` (tension branch = `Ke`, compression ≈ 0) | `Ke`→slope, `open`→backbone shift | good |
| Plastic (Wen) | `twoNodeLink` + `Steel02` (default) or `Hysteretic` | `Fy`→`Fy`, `Ke`→`E0`, `ratio`→`b`, `n`↔`R0` (Steel02); `Ke`/`fy`/`ratio` (Hysteretic, sharp corner) | good |
| Rubber Isolator | `elastomericBearingBoucWen` (lead/HDR) or `elastomericBearingPlasticity` (bilinear) | `Ke`→`kInit`, `fy`→`qd`, `k2_ratio`→`α1`; `-P/-T/-My/-Mz` `Elastic` | good |
| Friction Isolator | `singleFPBearing` + `frictionModel Coulomb` (or `VelDependent`) | `radius`→`Reff`, `Ke`→`kInit`, `mu`→`μ` | good |
| Damper – Linear | `twoNodeLink` + `Viscous(C, 1.0)` | `Ce`→`C` | exact |
| Damper – Exponential | `twoNodeLink` + `Viscous(C, α)` | `Ce`→`C`, `Cexp`→`α` | exact |
| Damper – Viscoelastic | `twoNodeLink` + `Parallel(Elastic(Ke), Viscous(Ce, α))` | `Ke`, `Ce` | good (freq/temp ignored) |
| Damper – Bilinear | `twoNodeLink` + `Hysteretic` (pinch = neutral) | `Ke`, `fy`, `k2_ratio` | good |
| Damper – Friction | no clean 1:1 — see §9 | — | gap |
| Multilinear Elastic/Plastic | `twoNodeLink` + `Hysteretic` backbone | force-deflection points | good (monotonic) |

## 5. The Hook decision — why `Hysteretic`

A Hook is **tension-only** with an initial gap `open`: `F = Ke·(u − open)` in
tension, `F = 0` in compression.  It is one-sided.

**`ElasticPP` is a poor fit.** `uniaxialMaterial ElasticPP $E $epsyP <$epsyN
$eps0>` is elastic-perfectly-plastic and symmetric; faking one-sidedness needs
`epsyP` "large" and `epsyN ≈ 0`, which (a) is not actually one-sided — the
"zero compression" is a plastic plateau at F≈0, so unloading from compression
develops spurious stiffness and energy; (b) makes the tangent go `E → 0` the
moment the inactive side loads, a Newton-convergence risk; (c) shoehorns the
gap through `eps0`; (d) needs a magic "large enough to never yield" value; and
(e) does **not** reduce the model count — Gap, Wen, bilinear, multilinear and
friction dampers would each still need their own material.

**`Hysteretic` is the right fit — and it is the "one model".** It is a general
one-sided-capable backbone (independent tension/compression branches), so one
emitter covers Gap + Hook + bilinear + multilinear + friction dampers:

| Behaviour | `Hysteretic` backbone |
|---|---|
| Hook (tension-only + gap) | tension = `Ke` after `open`; compression = 0 |
| Gap (compression-only + gap) | compression = `Ke` after `open`; tension = 0 |
| Damper – Bilinear | bilinear (`Ke`, `fy`, `k2_ratio`) |
| Multilinear Elastic / Plastic | the force-deflection point list |
| Damper – Friction (PP) | bilinear with zero post-yield |

The tuning knobs (`pinchX`, `pinchY`, `damage1`, `damage2`, `beta`) are **not
exposed** — the emitter pins them neutral (`1, 1, 0, 0, 0`) and derives the
backbone from `Ke`/`open`/`fy`/`ratio`.  There is nothing to tune.

Disadvantages, on the record: more parameters *written* (all auto-derived);
the neutral defaults must be pinned by a test (D7); marginally heavier than
`ENT` for the Gap alone.

## 6. Friction model — Coulomb first, `VelDependent` noted

Start with `frictionModel Coulomb $frnTag $mu`.  The alternative
`frictionModel VelDependent $frnTag $muSlow $muFast $transRate` models the
velocity dependence of μ; it is added later, without rework, as a one-line
branch in the emitter: *if `mu_fast`/`rate` are present, emit `VelDependent`
(`muSlow`=μ, `muFast`, `transRate`), else `Coulomb`.*  Same `singleFPBearing`
either way.

## 7. The one-time cyclic check, and its recipe requirement

The `Hysteretic` choice rests on the neutral defaults (`pinchX = pinchY = 1`,
`damage1 = damage2 = 0`, `beta = 0`) being *exactly* what turns `Hysteretic`
into a plain backbone.  A wrong pinch/damage value would silently introduce
pinching or degradation that no monotonic test can see.  Therefore:

* **D7 — a one-time cyclic check.** Drive a `Hysteretic` link material through
  a full load–unload–reverse–reload cycle and assert the response follows the
  specified backbone exactly (no pinching, no strength loss, symmetric
  reload).  This is a component test now and a regression guard forever.
* **D8 — it must be a recipe step, not just a test.** The check is exposed as a
  **workflow verb** (`workflow/verbs/`), so a model generated from a recipe
  re-runs the verification in the generated script — matching the "one
  operation, one spelling" rule (`.clinerules` §3.12, anti-pattern 17) and the
  check-verb pattern already in `workflow/verbs/checks.py`.  A menu action or
  report preset may *preset* the step; it must not grow a private copy of it.

**D9 — the compression-only comparison.** `ENT` is kept as a validation
reference, not a production emitter.  `tests/test_link_nonlinear.py` compares
`Hysteretic`-as-compression-only against `ENT` (and `Hysteretic`-as-tension-only
against `ElasticPP`, monotonically), so the "one model" backbone is pinned to
the purpose-built references by evidence.  This is the test that the
compression-only special case exists to support.

## 8. Runner dispatch and the warning (Phase 2c)

1. `link_model: "elastic"` (default) — keep the effective-stiffness springs,
   but **`logger.warning`** whenever a non-`Linear` link is linearised
   ("isolator `ISO_1` represented by effective stiffness; set
   `link_model='nonlinear'` for nonlinear response").
2. `link_model: "nonlinear"` — the emitters from §4.
3. The nonlinear runners (`_runner_pushover.py`, NLD) set
   `link_model: "nonlinear"` by default (via `_set_defaults`, not a hard
   override, so a user can force `elastic` for a quick linearised check).
4. The policy lives in one registry — `_SUPPORTED_NONLINEAR_LINK_TYPES` and
   `_LINEARIZED_LINK_TYPES` — so "supported vs linearised vs rejected" has a
   single source of truth.

## 9. Gaps (no clean 1:1)

| Feature | Options | Recommendation |
|---|---|---|
| T/C Friction Isolator | `singleFPBearing` (loses uplift capacity) vs defer | warn + `singleFPBearing`, or defer |
| Triple Friction Pendulum | `tripleFPBearing` (full geometry backbone) | defer to last — the SAP2000 TFP table may not expose the 3-radius/3-μ backbone 1:1 |
| Damper – friction directional/orthotropic | `frictionModel` + bearing in the damper DOF, or `Hysteretic` plastic | defer; linearised-elastic interim |
| Viscoelastic freq/temp dependence | `Parallel(Elastic, Viscous)` is rate-independent | ship it, flag the approximation |
| Non-axis-aligned `CoordDir2` | honour arbitrary `CoordDir2` | fold into this phase (§4 of `_link_orient`) |

## 10. Benchmarks for validation

* **Base isolators** — NEES/E-Defense full-scale 5-storey base-isolated
  building (E-Defense shake table; fixed / LRB+CLB / triple-FP): Dao, Ryan,
  Sato & Sasaki (2013), *Earthquake Engng Struct. Dyn.* 42(11),
  DOI `10.1002/eqe.2293`.  Morgan & Mahin friction-pendulum characterisation
  (basis of `singleFPBearing`): Morgan & Mahin (2010), *EESD* 39(13),
  DOI `10.1002/eqe.1046`.  Fenz & Constantinou triple/double FP backbone
  (basis of `tripleFPBearing`): *MCEER-08-0018*.  Constantinou et al. bearing
  test database: *MCEER-11-0004* / *MCEER-07-0012*.
* **Dampers** — viscous: Pekcan, Mander & Chen (1999), *EESD* 28(11):1405–1425,
  DOI `10.1002/(SICI)1096-9845(199911)28:11<1405::AID-EQE862>3.0.CO;2-3`; plus
  Constantinou & Symans (1993) damper component tests.  Viscoelastic: Zhou,
  Chen & Gong (2019), *Frontiers in Materials*, DOI `10.3389/fmats.2019.00033`.
* **Gap/Hook** — closed-form one-DOF contact problems (engagement gap, contact
  stiffness, zero force in the released direction); Anagnostopoulos (1988) for
  pounding/gap behaviour.

## 11. Validation strategy

1. **Component tests** (deterministic, no benchmark): a one-DOF link driven
   through a known history and its force response asserted (gap opens at
   `open`, isolator yields at `fy`, FP post-yield stiffness = `W/R`, damper
   force = `C·v^α`).  These catch sign/unit/orientation bugs first.
2. **Reference-benchmark tests** (§10): reproduce a published backbone /
   hysteresis loop within tolerance, marked `slow`/`needs_pyvista` as
   appropriate.
3. **Full-model shake-table** (stretch): a subset of the E-Defense 5-storey
   building (periods, peak isolator displacement, storey drift).

## 12. Sequencing (work items, one commit each)

1. **P49-P2a** — structured `LinkProperty.nonlinear` sub-dataclasses + S2K
   `03+` parser + E2K token interpretation (+ tests, table-registry `handled`
   flips).
2. **P49-P2b(i)** — per-DOF nonlinear emitters: Gap/Hook/Wen/dampers (+ the
   D7 cyclic check, §7) (+ component tests).  ✅ **Done** — `Steel02` (default)
   / `Hysteretic` Wen, `Hysteretic` Gap/Hook/bilinear, `Viscous`/`Parallel`
   dampers, shared backbone helpers, `wen_model` knob, cyclic + component
   tests in `tests/test_link_nonlinear.py`.
3. **P49-P2b(ii)** — bearing elements: `elastomericBearing*`,
   `singleFPBearing` + `frictionModel` (+ component tests).
4. **P49-P2c** — `link_model` dispatch, warnings, runner defaults, type
   registry.
5. **P49-P2d** — the D8 check verb + recipe preset, benchmark tests (§10), and
   `docs/modeling_guidance.md` bearing/damper guidance.
6. **P49-P2e (stretch)** — `tripleFPBearing`, T/C FP, friction-damper family,
   non-axis-aligned `CoordDir2`.
