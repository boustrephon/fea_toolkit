---
title: "Mander Confinement Model Validation"
description: "Formula-by-formula conformance of the Mander confinement engine to Mander et al. (1988), documented simplifications, and comparison against NZSEE C5, OpenSees Concrete07, and TSC2018."
status: "complete"
tags: [mander, confinement, validation, theory, reference]
category: [model-features]
related: [rc_rectangular_section_workflow.md, element_properties_config.md, pushover_analysis.md]
---
# Mander Confinement Model Validation

This document records the research audit of the confined-concrete engine in
`fea_toolkit.model.confinement`, which implements the **Mander, Priestley &
Park (1988)** theoretical stress-strain model for confined concrete.  It lists
the formula-by-formula conformance to the original paper, the two documented
simplifications introduced by the toolkit, and a comparison against the
practitioner models used in NZSEE C5, OpenSees Concrete07, and TSC2018
Annex 5-A.

## 1. Scope

The engine (`mander_confined()`) computes the confined compressive strength
`f'cc`, the strain at confined peak stress `εcc`, the effective confinement
coefficient `ke`, the volumetric transverse reinforcement ratio `ρs`, the
effective lateral confining stress `f'l`, and the ultimate (spalling) strain
`εcu` from:

* unconfined concrete strength `f'c`
* tie/spiral geometry (`tie_diameter`, `tie_spacing`, `tie_config`)
* transverse reinforcement yield strength `f_yh`
* core geometry (`core_bc`, `core_dc`)
* longitudinal bar layout (`long_diameter`, `long_count_x/y`)

## 2. Formula-by-formula conformance to Mander (1988)

### 2.1 Confined strength — Mander Eq. 29 (1988)

The toolkit evaluates the five-parameter triaxial failure surface of Mander
Eq. 2 through the closed-form uniaxial curve given by **Mander Eq. 29**:

```text
f'cc = f'c * ( 2.254 * sqrt(1 + 7.94 * f'l / f'c) - 2 * f'l / f'c - 1.254 )
```

This matches the paper's algebraic solution of the triaxial failure surface
(`A = 1.254`, `B = 2.254`, `C = 7.94`).  The implementation is unit-agnostic:
stress inputs (`f'c`, `f_yh`, `f'l`) must be in consistent units.  See the
code path in `model/confinement.py` under `# Mander Eq. 3` (the numbering in
the source follows the common Eq. 3 / Eq. 4 numbering used in later papers;
Eq. 29 in the original is the same closed-form expression).

### 2.2 Effective confinement coefficient — circular, Mander Eq. 14

For `tie_config = "spiral"` (circular sections), the toolkit uses the Mander
equation for `ke` with a circular arching action:

```text
ke = (1 - s' / (2 * Ds))^2 / (1 - ρcc)
```

where `s' = s - d_b` is the clear spacing between hoops, `Ds` is the core
diameter to the hoop centreline, and `ρcc` is the longitudinal-to-core area
ratio.  This is Mander Eq. 14 (the spiral equation from the 1988 paper).

### 2.3 Effective confinement coefficient — rectangular, Mander Eq. 22

For rectangular sections (`tie_config = "standard"` / `"cross_tie"`) the
toolkit computes `ke` as the product of three factors, which is Mander
Eq. 22:

```text
ke = (1 - Σwi'^2 / (6 * bc * dc))
     * (1 - s' / (2 * bc))
     * (1 - s' / (2 * dc))
     / (1 - ρcc)
```

where `Σwi'^2` is the sum of the squares of the *clear* distances between
adjacent longitudinal bars (restrained by cross-ties or corners) and `bc`,
`dc` are core dimensions to the hoop centreline.

### 2.4 Strain at peak confined stress — Mander Eq. 4

```text
εcc = εc * (1 + 5 * (f'cc / f'c - 1))
```

with `εc = 0.002` (a commonly accepted unconfined peak strain for normal
weight concrete; Mander uses the symbol `εco`).

### 2.5 Volumetric transverse reinforcement ratio — ρx / ρy and ρs

For rectangular sections the toolkit computes per-direction volumetric ratios:

```text
ρx = Ash_x / (s * dc)
ρy = Ash_y / (s * bc)
ρs = ρx + ρy
```

with `Ash_x` / `Ash_y` the total transverse steel area in each direction
(two perimeter legs plus any cross-tie legs).  For spirals:

```text
ρs = 4 * A_b / (s * Ds)
```

### 2.6 Cross-ties

When `tie_config = "cross_tie"`, cross-tie legs are added to `Ash_x` and
`Ash_y`:

```text
Ash_x = 2 * A_b + n_ct_x * A_b
Ash_y = 2 * A_b + n_ct_y * A_b
```

This follows the Mander convention that the effective `wi'` clear spacing
between restrained longitudinal bars is set by the cross-tie spacing.

## 3. Documented simplifications

### 3.1 Biaxial confinement via `min(f'lx, f'ly)`

Mander's full solution for rectangular sections requires iterating on the
triaxial failure surface with two unequal lateral stresses `f'lx ≠ f'ly`
(the "two-invariant" approach).  The toolkit instead applies the conservative
simplification recommended by **Priestley, Calvi & Kowalsky (2007)**,
*Displacement-Based Seismic Design of Structures*, IUSS Press: the weaker
lateral confining stress governs, so

```text
f'l = ke * min(f'lx, f'ly)
```

This avoids the triaxial iteration while remaining conservative (the
predicted `f'cc` is lower than the full biaxial interaction solution).

### 3.2 Ultimate spalling strain — Priestley (1996), capped by `ecu_max`

The ultimate (spalling) strain is computed with the simplified Priestley
form:

```text
εcu = 0.004 + 1.4 * ρs * f_yh * εsu / f'cc
```

(Priestley, 1996; as quoted in Priestley, Seible & Calvi, *Seismic Design and
Retrofit of Bridges*, Wiley 1996).  This formula can predict very large
strains for heavily confined sections.  The toolkit therefore caps the result
with a **configurable upper bound** `ecu_max`:

```text
εcu = min(εcu_formula, ecu_max)
```

`ecu_max` defaults to **0.025** (a common upper limit for ductile detailing).
NZSEE C5 uses **0.05**; the value is configurable (see §5).

## 4. Comparison against practitioner models

| Model | εcc at peak | εcu (spalling) | Strength | Notes |
|---|---|---|---|---|
| **Mander 1988 (this toolkit)** | `0.002 * (1 + 5(f'cc/f'c - 1))` | `0.004 + 1.4 ρs f_yh εsu / f'cc`, capped at `ecu_max` (default 0.025) | Closed-form Eq. 29 | Full Mander with documented simplifications (min biaxial, capped εcu) |
| **NZSEE C5 Modified Mander** | `0.002 * (1 + 5(f'cc/f'c - 1))` | `0.004 + 1.4 ρs f_yh (0.6 εsu) / f'cc`, capped at **0.05** | `f'cc = f'c (2.254 √(1+7.94 f'l/f'c) - 2 f'l/f'c - 1.254)` | Uses a reduced ultimate strain `0.6 εsu` for the transverse (tie) steel — paired with the tie yield strength `f_yh` and the transverse volumetric ratio `ρs` (C5 Table C4.1) — and a 0.75 factor on `ρst` where noted; εcu cap 0.05 for conventional ductile detailing |
| **OpenSees Concrete07** | Stress-strain curve fitted to Mander for cyclic loading | Degradation beyond peak; no explicit spalling cap | Mander | Material model for fiber sections; εcu is implicitly bounded by the user-provided curve endpoints |
| **TSC2018 Annex 5-A** | Based on Turkish code provisions | Code-prescribed ultimate strain limits | Code formula | Conservative; ductility limits from TSC 2018 |

### Key differences

* **NZSEE C5** reduces the ultimate transverse steel strain (`0.6 εsu`) and,
  in some provisions, applies a `0.75` factor to the volumetric ratio `ρst`
  to account for strain-hardening uncertainty and bar-slip effects.  The
  toolkit currently uses the full `εsu` and full `ρs` (with the optional
  cross-tie contribution), which is the raw Mander/Priestley formula.
* **εcu cap**: NZSEE C5 caps confined compression strain at 0.05; the
  toolkit defaults to 0.025 and exposes `ecu_max` (and the builder config
  key `confined_ecu_max`) so users can match their local code.
* **Concrete07** is a full cyclic uniaxial material; the toolkit's
  `ConfinementResult.ecu` is used to define the *spalling* endpoint of the
  descending branch of `Concrete01`.

## 5. Configuration: the `confined_ecu_max` builder key

When the Mander engine is consumed by the builders, the `ecu_max` cap is
exposed through a **builder-scoped config key**:

```python
config = {
    # ... other builder options ...
    "confined_ecu_max": 0.05,   # Upper bound for confined spalling strain
}
```

* **Default**: `0.025`.
* **Location**: `AnalysisBuilder._set_defaults()` applies the default;
  `_create_materials()` caps `ecu_core = min(ecu_core, confined_ecu_max)`
  for **both** the Mander (confinement dict) path and the 1.25 × f'c
  heuristic path.
* **Tcl export**: `opensees/builder.py::tcl_materials_and_sections()` reads
  the same `confined_ecu_max` key when generating `Concrete01` commands,
  replacing the previous hard-coded `ecu_cc ≤ 0.025 → else 0.02` logic.
* **Section-level**: `ConcreteRectangularSection.ecu_max` and
  `ConcreteCircularSection.ecu_max` propagate directly into
  `ConfinementData.ecu_max` when the section's `fiber_confinement()` method
  runs.

## 6. Validation status

* ✅ Closed-form Mander Eq. 29 verified against hand calculations.
* ✅ Spiral and rectangular `ke` verified against Mander Eq. 14 / Eq. 22.
* ✅ `εcc` (Eq. 4) verified against hand calculations.
* ✅ ρx/ρy and cross-tie contributions verified.
* ✅ `ecu_max` configurability covered by tests
  (`test_ecu_max_configurable`, `test_ecu_max_larger_than_default`,
  `test_invalid_ecu_max_raises`).
* ✅ Builder and Tcl export apply the configurable cap consistently.