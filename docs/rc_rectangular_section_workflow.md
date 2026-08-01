---
title: "Rectangular Concrete Section Workflow"
description: "SAP2000 → OpenSees fiber workflow for rectangular RC beam/column sections: parser defaults, promotion, Mander confinement, and user overrides."
status: "complete"
tags: [rc, sections, fiber, mander, confinement, workflow]
category: [model-features]
related: [element_classification.md, element_properties_config.md, pushover_analysis.md, report_generation.md]
---
# Rectangular Concrete Sections: SAP2000 → OpenSees Fiber Workflow

This document traces how **rectangular reinforced-concrete (RC) sections** for beams and
columns flow through the toolkit, from SAP2000/ETABS `.s2k` data to OpenSees fiber
sections, including **defaults and user overrides**.

## Scope

- **Columns** → `ConcreteRectangularSection` (explicit rebar layout: top/bot bars).
- **Beams** → `RectangularSection` promoted to `ConcreteRectangularSection` by the parser
  when the section material is concrete (a common SAP2000 convention where beams are
  exported with shape `"Rectangular"` rather than `"Concrete Rectangular"`).

Both types implement `to_fiber_patches()`, producing the same 3-tag fiber convention:

| Tag | Material | Purpose |
|---|---|---|
| `mat_tag` | `Concrete01` | Unconfined cover (spalls at ε ≈ 0.006) |
| `mat_tag + 1` | `Concrete01` | Confined core (enhanced strength/ductility) |
| `mat_tag + 2` | `Steel02` | Longitudinal rebar |

## 1. Data sources (.s2k tables)

| Table | Fields consumed |
|---|---|
| `FRAME SECTION PROPERTIES 01 - GENERAL` | `SectionName`, `Shape`, `Material`, `Area`, `I33`, `I22`, `TorsConst`, `t3` (depth), `t2` (width), `cover`, `topBars`, `botBars`, `topBarDia`, `botBarDia`, stiffness modifiers (`AMod`/`I2Mod`/`I3Mod`/`JMod`) |
| `FRAME SECTION PROPERTIES 02 - CONCRETE COLUMN` | `RebarMatL` (longitudinal rebar material → `rebar_material`), `BarSizeL` (bar size → diameter), `TieSizeL` / `TieSizeT` (tie diameter), `TieSpacingL` / `TieSpacingT` (tie spacing), `RebarMatT` (tie material → `tie_rebar_mat`) |
| `FRAME SECTION PROPERTIES 03 - CONCRETE BEAM` | `RebarMatL`, `BarSizeTop` / `BarSizeBot`, tie columns as above |
| `REBAR SIZES` | `RebarID` → `Diameter` mapping (model length units) |
| `MATERIAL PROPERTIES 01 - GENERAL` | Material type (`Concrete`) used for shape promotion |

## 2. Parser: section construction

### 2.1 `ConcreteRectangularSection` (explicit columns)

When `Shape == "Concrete Rectangular"` the parser builds:

```python
ConcreteRectangularSection(
    name=..., shape=..., material=...,
    A=..., I33=..., I22=..., J=..., Z33=..., Z22=..., modifiers=...,
    depth=t3, bf=t2,
    cover=float(sec["cover"]),              # from FRAME SECTION PROPERTIES 01
    top_bars=int(sec["topBars"]),
    bot_bars=int(sec["botBars"]),
    top_bar_dia=..., bot_bar_dia=...,        # from BarSizeL / topBarDia / REBAR SIZES
    rebar_material=_reinf["rebar_mat"],      # RebarMatL from 02/03 table
    tie_diameter=...,                        # TieSizeL/T (via REBAR SIZES)
    tie_spacing=...,                         # TieSpacingL/T
    tie_rebar_mat=...,                       # RebarMatT
)
```

### 2.2 Promotion of `RectangularSection` beams

When a section is `RectangularSection` **and** its material is concrete, the parser
promotes it to `ConcreteRectangularSection` with **framework defaults**:

- **Cover**: `40 mm` converted to model units via `length_scale_factor(units)`.
- **Bar diameter**: `20 mm` converted via `length_scale_factor(units)`.
- **Bar count**: target **1 %** of gross area:
  `n_bars = max(4, int(A * 0.01 / bar_area))`.
- **Reinforcement ratio**: 1 % (0.01) of gross area, split top/bot.
- **Rebar material**: from the 02/03 table `RebarMatL` when present.
- **Tie data**: from 02/03 table when present (feeds Mander confinement).

These defaults are applied **only** for promoted beams; explicitly-defined
`ConcreteRectangularSection` columns keep their SAP2000 values.

## 3. Fiber patch generation (`to_fiber_patches()`)

`ConcreteRectangularSection.to_fiber_patches(mat_tag, nfy, nfz)`:

1. Scales geometry: cover → core `(y1, z1, y2, z2)`.
2. Emits one **confined core** `patch rect` (tag `mat_tag + 1`).
3. Emits up to four **unconfined cover** `patch rect`s (tag `mat_tag`).
4. Emits top/bot rebar `layer straight` (tag `mat_tag + 2`) from
   `top_bars`/`bot_bars` × bar area (π·d²/4).

Validation: negative cover or cover ≥ half-dimension raises `ValueError`.

## 4. Mander confinement (core strength/ductility)

`ConcreteRectangularSection` and `ConcreteCircularSection` implement
`fiber_confinement(fc, tie_fy)`:

- Uses the Mander et al. (1988) model (`model/confinement.py`) with the section's
  transverse-reinforcement fields (`tie_diameter`, `tie_spacing`, `tie_fy` /
  `tie_rebar_mat`, `tie_config`, longitudinal bar counts).
- Core dimensions are to the **hoop centreline**: `core_bc = bf − 2·cover − tie_diameter`
  (and similarly for depth).
- Returns `{"fcc", "ecc", "ecu"}` when tie data is complete and geometrically valid,
  else `None`.

**Both `AnalysisBuilder._create_single_section()` and the Tcl export
(`builder.py`) use the same priority** for the confined core `Concrete01`:

1. **Mander** — `fiber_confinement()` result when tie data present (uses `tie_rebar_mat`
   → `RebarMatT` Fy, falling back to `rebar_material` → `RebarMatL` Fy).
2. **Heuristic (backward compatible)** — `1.25 × f'c` (shared by both paths via
   `RC_NO_TIE_CONFINEMENT_FACTOR` / `RC_NO_TIE_EPSC_FACTOR` in `utils.py`) when
   no tie data — consistent with the OpenSees Berkeley comparison manual default
   and Mander-model approximation.

## 5. Rebar material resolution (3-level)

`AnalysisBuilder._create_single_section()` and the Tcl export resolve the rebar
`Steel02` `Fy`/`Es` in strict priority order:

1. **Config override** — `rebar_Fy_override` / `rebar_Es_override`, authored in **SI
   (Pa)** and scaled to model units via `stress_scale_factor(units)`.
2. **SAP2000 lookup** — `section.rebar_material` (RebarMatL) material entry's `Fy` /
   `E_mod`, used in model units as-is.
3. **Framework defaults** — `DEFAULT_FY_REBAR_PA` / `DEFAULT_E_S_PA` scaled to model
   units.

> **Unit convention**: material strengths and moduli are authored in SI (Pa) and scaled
> by the framework.  Do **not** hand-convert to model units — always route through
> `scale_material_dict()` (or the Preprocessor's `nd_materials` flow) so the scaling is
> applied exactly once.

## 6. Example: parsing + pushover

```python
from fea_toolkit.io.s2k_parser import SAP2000Parser
from fea_toolkit.opensees.preprocessor import preprocess_model
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

parser = SAP2000Parser("model.s2k")
parser.parse()
md = parser.get_model_data()          # applies apply_material_defaults()

config = {
    "element_type": "forceBeamColumn",
    "create_fiber_sections": True,
    "use_elastic_sections": False,
    "rebar_Fy_override": 400e6,       # SI Pa → scaled to model units
}
mesh = preprocess_model(md, config)
builder = AnalysisBuilder(mesh, config)
builder.build_domain()
```

The builder creates, per RC fiber section:

```text
uniaxialMaterial Concrete01 <mat>      -fc   -epsc   -0.2·fc   -0.006    # cover
uniaxialMaterial Concrete01 <mat+1>    -fcc  -ecc    -0.2·fcc  -ecu      # core (Mander or heuristic)
uniaxialMaterial Steel02 <mat+2>       Fy    Es      0.01     18 0.925 0.15
section Fiber <sec> -GJ J { patch rect ... ×N  layer straight ... }
```

## 7. Summary of defaults vs overrides

| Quantity | Default | Override |
|---|---|---|
| Cover (promoted beams) | 40 mm → model units | `cover` from `.s2k` frame table |
| Bar diameter (promoted beams) | 20 mm → model units | `BarSizeL` / `topBarDia` / `botBarDia` |
| Bar count (promoted beams) | 1 % of gross | `topBars` / `botBars` from `.s2k` |
| Confined core strength | Mander (tie data) else 1.25–1.3 × f'c | `tie_diameter`/`tie_spacing`/`tie_fy` fields |
| Rebar Fy/Es | framework defaults (SI) | `rebar_Fy_override` / `rebar_Es_override` (SI) |
| Concrete fc/epsc | `apply_material_defaults()` (SI→model) | section material entry (`Fc`) |