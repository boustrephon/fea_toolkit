---
title: "CFT (concrete-filled steel tube) confinement — plan & recommendations"
description: "Design record for modelling the confined concrete core of concrete-filled steel tubes (CHS/RHS) in fiber sections: options, pros/cons, recommendations, and researched model choices."
status: "partial"
tags: [cft, confinement, fiber, composite, section, research]
category: [model-features]
related: [mander_confinement_validation.md, e2k_import_plan.md, pushover_analysis.md]
---
# CFT (concrete-filled steel tube) confinement

## 1. Status

- **Landed:** the role-based fiber contract, `ConcreteFilledTubeSection` (RHS),
  `ConcreteFilledPipeSection` (CHS), and the `Section.confinement_steel_fy`
  hook (see §2).  Both CFT sections currently model the concrete fill as
  **unconfined**.
- **Deferred:** the actual CFT confinement formula (this document records the
  decision and the researched recommendation; the formula is *not* implemented).

## 2. Context — the fiber-role platform

Section fiber patches are produced by `Section.to_fiber_patches(materials,
nfy, nfz)`, where `materials` is a *role → OpenSees tag* map.  The role also
fixes the uniaxial law, resolved once in
`opensees/_fiber_role_materials.py::resolve_fiber_role_material`:

| role | law | source |
|---|---|---|
| `"steel"` | `Steel01` | `Section.material` |
| `"rebar"` | `Steel02` | `rebar_material` / defaults |
| `"concrete"` | `Concrete01`/`Concrete02` (unconfined) | `material` / `fill_material` |
| `"core"` | `Concrete01`/`Concrete02` (confined, Mander) | `fiber_confinement(fc, tie_fy)` |

The `"core"` branch computes `f'cc`/`εcc`/`εcu` by calling
`sec.fiber_confinement(Fc, tie_fy)`, with `tie_fy` resolved via the new
`Section.confinement_steel_fy(materials)` hook (tie yield for RC sections;
a filled-tube section will override it to return the **tube** yield).  This
single resolver is shared by the OpenSeesPy domain and both Tcl exporters, so
any confinement change lands in all three paths at once.

## 3. Decision record

### A. Geometry scope

| Option | Pros | Cons |
|---|---|---|
| **A0 — none** | zero risk; conservative | under-predicts CHS strength/ductility |
| **A1 — CHS only** | CHS confinement is strong and well-established | RHS stays unconfined (inconsistent) |
| **A2 — CHS + RHS** | complete coverage | RHS confinement is weak/contested; a bad model over-predicts |

**Recommendation: A1.** Circular-tube confinement is robust; rectangular-tube
walls flex outward and only corners confine, so RHS needs a validated model of
its own.  Keep RHS unconfined until then.

### B. Confinement formulation

| Option | Pros | Cons |
|---|---|---|
| **B0 — unconfined** | nothing to validate; conservative (§12) | ignores real CHS confinement |
| **B1 — Mander via "equivalent spiral"** | reuses `mander_confined()` | Mander is tie/spiral-based; its `ke` (spacing, bar layout) doesn't map to a continuous tube, and the tube's axial load reduces its hoop capacity (von Mises) — an unvalidated, likely over-predicting mapping |
| **B2 — dedicated CFT model via ξ = As·fy/(Ac·fc)** | models the tube directly; well-published for CHS; gives `f'cc/fc` and `εcc/εc`; extensible to RHS | needs a specific sourced equation set |
| **B3 — code capacity (GB 50936 / AISC / EC4)** | authoritative for *design* | gives capacity, not a σ–ε curve → belongs in `capacity/`, not the fiber resolver |

**Recommendation: B2** for the fiber core; **B0** as the interim; **B3** as a
separate `capacity/` concern.

### C. Architecture

| Option | Pros | Cons |
|---|---|---|
| **C1 — new `model/cft_confinement.py`** | keeps Mander's module/docstring clean; `cft_confined(…) → {fcc, ecc, ecu}` mirrors `fiber_confinement`'s return shape | one more module |
| **C2 — extend `model/confinement.py`** | co-locates all confined-concrete | muddies the "Mander (1988)" identity |
| **C3 — fold into the section** | no new module | formula buried in dataclasses; hard to unit-test/validate |

**Recommendation: C1.**

### D. Role wiring

| Option | Pros | Cons |
|---|---|---|
| **D1 — reuse `"core"`**, generalize the confinement-steel-yield resolution | no new role; no `isinstance` re-introduced | widens `fiber_confinement`'s `transverse_fy` arg |
| **D2 — new `"cf_core"` role** | explicit; RC `"core"` untouched | new branch in the shared resolver + all three emitters |

**Recommendation: D1.** The `confinement_steel_fy` hook already landed; a CFT
section overrides it and implements `fiber_confinement` against the tube yield.

## 4. Overall recommendation (staged)

1. **Ship nothing confinement-related yet** — done (both CFT sections use an
   unconfined fill).
2. **Add CHS-only confinement (A1 + B2 + C1 + D1):** `cft_confined(fc,
   steel_fy, D, t) → {fcc, ecc, ecu}` from a sourced ξ-model (see §5);
   `ConcreteFilledPipeSection.fiber_confinement(fc, tube_fy)` delegates to it;
   change its roles to `["steel","core","rebar"]`; add
   `docs/cft_confinement_validation.md`.
3. **Leave RHS unconfined** until a validated RHS model is chosen.

## 5. Researched open questions

### Q1 — which published CFT σ–ε model for the CHS core?

Reviewed models:

- **Han Lin-Hai (Han 2007; basis of the Chinese CFT code GB 50936-2014).**
  Parameterised by the confinement factor **ξ = As·fy / (Ac·fc)** (or, in Han's
  notation, α = As·fy / (Ac·fck)).  A concise closed-form skeleton curve
  (ascending + descending branch) for the confined core, calibrated against
  400+ short-column tests, applicable to C30–C90 concrete, and provided for
  **both circular and square** tubes — so a future RHS extension stays in the
  same family.  (This is the model Chinese engineers default to.)
- **Sakino et al. (2004)** — 114 centrally-loaded short columns (circular +
  square), a "unified" two-parameter σ–ε model (Sakino & Sun 1994) normalised
  by the confined peak (`f'cc`, `εcc0`); the circular strength-enhancement
  factor K = f'cc/fc is given in closed form.  Equally valid, but the full
  σ–ε is spread across tables rather than one equation set.
- **Giakoumelis & Lam (2004)** — ABAQUS FE calibration for circular CFT; the
  confined concrete is a three-part equivalent uniaxial curve (elastic →
  0.5·fcc, nonlinear to fcc, descending to ≈0.3·fcc at 11·εcc).  It is an FE
  calibration recipe, not a compact closed form.
- **Zhong (2003)** and **Susantha et al. (2001)** — both ξ-based, the latter
  for circular *and* rectangular tubes.

**Recommendation: Han's ξ-model (Han 2007 / GB 50936-2014 form),** with Sakino
(2004) as the cross-check for `f'cc/fc`.  Rationale: closed-form, ξ-parameterised
(inputs we already have — `Fc`, tube `Fy`, `D`, `t`), validated on the largest
dataset, and the only option that gives one family covering CHS now and RHS later.
The exact coefficients **must be transcribed from Han (2007) / GB 50936-2014** at
implementation time, not from this summary (guardrail §12).

### Q2 — Concrete01 or Concrete02 for the confined core?

**Concrete02.**  Both share the same compressive envelope (Kent–Scott–Park
backbone), but Concrete02 adds a genuinely descending post-crushing branch,
**linear tension softening**, and **compressive unloading/reloading loops**
(hysteretic energy dissipation); Concrete01 has none of these (OpenSees
comparison manual).  A CFT core is highly ductile (the tube provides continuing
confinement past peak), which is exactly the ductility Concrete02 models and
Concrete01 flattens out.  The resolver already supports `concrete_material =
"Concrete02"` (an opt-in config), so this is a default/selection choice, not new
code.

### Q3 — Steel01 or Steel02 for the tube?

**Steel01 for monotonic (pushover), Steel02 for cyclic/dynamic.**  Steel01 is a
bilinear kinematic material with 1 % hardening — sufficient for a monotonic
pushover, and consistent with the `"steel"` role everywhere else in the toolkit.
Steel02 (Giuffré–Menegotto–Pinto) adds the Bauschinger effect, which matters for
reversed/cyclic loading of the tube but buys nothing for a one-way push.  Keep
`"steel"` → Steel01 as the default; add a Steel02 option for cyclic CFT runs as a
follow-up.  Neither captures tube **local buckling** (a separate, out-of-scope
concern that would need a degrading material or a shell model).

## 6. What landed in this change

- `Section.confinement_steel_fy(materials)` — the confinement-steel-yield hook
  (refactor of the inline tie-yield resolution in the `"core"` branch; behaviour
  unchanged for RC sections).  This is the seam a CFT section will override.
- `ConcreteFilledPipeSection` (CHS) — annular pipe + unconfined concrete core +
  optional rebar ring, mirroring `ConcreteFilledTubeSection`.
- Re-export, tests, and doc cross-references.

## 7. Deferred / next steps

1. Transcribe Han's ξ-model (or the GB 50936-2014 equivalent) into
   `model/cft_confinement.py::cft_confined(fc, steel_fy, D, t) → {fcc, ecc, ecu}`.
2. `ConcreteFilledPipeSection.fiber_confinement` → delegate to `cft_confined`;
   roles → `["steel","core","rebar"]`.
3. `docs/cft_confinement_validation.md` — a *planned* validation doc (does not
   exist yet): hand-checks against Sakino/Han test data once the ξ-model lands.
4. Decide (separately) whether RHS confinement is worth a validated model.

