---
title: "Triple-Pendulum Backbone → OpenSees Geometry — Plan (P49 stretch)"
description: "Plan for mapping the CSI SAP2000/ETABS triple-pendulum isolator backbone (force–deformation) onto the OpenSees TripleFrictionPendulum (Dao) or TFP (Becker–Mahin) geometry parameters."
status: "draft"
tags: [plan, nonlinear-link, base-isolation, TPB, backbone, OpenSees]
related: ["nonlinear_link_plan.md", "e_defense_tpb_benchmark.md", "modeling_guidance.md"]
category: [plan]
---

# Triple-Pendulum Backbone → OpenSees Geometry — Plan

> 🚧 Draft — the mapping is understood at the *target* (OpenSees) end but not
> yet at the *source* (CSI export) end; see §6.

## 1. Problem

CSI's **triple-pendulum isolator** (ETABS v22+ *Analysis Reference Manual*
§15.18, "Triple-Pendulum Isolator Property") is authored through the bearing's
physical parameters — surface radii, slider displacement limits ("stops"),
per-surface friction coefficients, and the slider/plate heights — from which
CSI derives the **force–deformation backbone** curve.  The two OpenSees
elements that model a triple pendulum both need that *geometry* (radii, slider
diameters, displacement limits, per-interface friction), so the natural mapping
is geometry-to-geometry; the exact export columns are still unverified (§6).
This is the remaining P49 gap after the T/C Friction Isolator (single-FP) and
the `TripleFrictionPendulum` emitter landed.

## 2. Target — what OpenSees actually needs

| Element | Parameters |
|---|---|
| `TripleFrictionPendulum` (Dao et al. 2013 — **implemented**) | 3× `frictionModel`, `matP/T/My/Mz`, `L1 L2 L3` (effective radii `R−h`), `Ubar1 Ubar2 Ubar3` (slider displacement limits), `W`, `Uy`, `Kvt`, `minFv`, `tol` |
| `TFP` / `TripleFrictionPendulumBearing` (Becker & Mahin) | raw geometry: `R1..R4`, `D1..D4`, `d1..d4`, `mu1..mu4`, `h1..h4`, `H0`, `colLoad`, `K` |

The E-Defense TPB benchmark (`config="tpb"`) already exercises the Dao element
with published geometry (`L=(0.419, 3.848, 3.848) m`, `Ubar=(0.051, 0.514,
0.514) m`, `mu=(0.02, 0.08, 0.08)`) and reproduces the 0.275 W backbone at
1.13 m — so the *target* is validated; only the *source* (CSI backbone) is
unwired.

## 3. Source — what CSI exports

To be pinned down (see §6).  Known facts:

- Newer SAP2000/ETABS releases have a dedicated **Triple-Pendulum Isolator**
  link type (distinct from the T/C Friction Isolator).
- The property is authored through the bearing's physical parameters — surface
  radii, slider displacement limits (stops), per-surface friction coefficients
  and the slider/plate heights — and CSI derives the nonlinear
  **force–deformation backbone** (force vs displacement, per horizontal
  direction) from those inputs, alongside the usual `01 - GENERAL` /
  `02 - LINEAR` effective properties.
- Whether the export carries the `R`/`d`/`mu` geometry directly, or only the
  derived backbone, is **unverified** — do not assume the geometry is absent.
  Confirm the columns from a real export (see §6).

## 4. Mapping options

| Option | Approach | Cost / risk |
|---|---|---|
| **A. Backbone regime fit** | Recover `(L, Ubar, mu)` by fitting the backbone's three linear regimes to the series-model force law `F = mu_i·W + W/R_i·u` (composite radii `R_1 = 2·L_1`, `R_2 = 2·L_1 + L_2`, `R_3 = 2·L_1 + L_2 + L_3` — see §5) | cheap at runtime; ill-conditioned if the backbone is coarse/noisy; needs one reference `W` |
| **B. Geometry datasheet → `TFP`** | User supplies `R/D/d/mu/h` from the bearing datasheet; emit the Becker–Mahin `TFP` element | exact, but requires data the model file does not contain |
| **C. Linearised/bilinear fallback** | `02 - LINEAR` effective stiffness only (`link_model="elastic"`), warn | already available today; loses the multi-regime backbone |

Recommendation: **A as the automatic path** (with a warn-on-poor-fit guard),
**B as an explicit opt-in** for users who have the datasheet, **C as the
documented interim**.

## 5. Option A — the regime fit (sketch)

For a triple pendulum in series, the normalised backbone has three piecewise
regimes; in regime `i`:

```
F = b_i·W + (W / R_i) · u
```

where `b_i` is the regime's **radius-weighted composite friction intercept** —
not `mu_i` directly — and the composite radii accumulate as the mechanisms
engage: `R_1 = 2·L_1`, `R_2 = 2·L_1 + L_2`, `R_3 = 2·L_1 + L_2 + L_3` (the
inner mechanism slides on two surfaces, so its effective radius counts double;
this is the series model in `docs/nonlinear_link_plan.md`, mirrored by
`TriplePendulumParams.backbone`).  The intercepts are:

```
b_1 = mu_1
b_2 = (mu_1·R_1 + mu_2·L_2) / R_2
b_3 = (mu_1·R_1 + mu_2·L_2 + mu_3·L_3) / R_3
```

so the force is continuous at the friction-transition displacement
`u* = 2·L_1·(mu_2 − mu_1)`.
The normalised slopes are therefore `1/(2·L_1)`, `1/(2·L_1 + L_2)` and
`1/(2·L_1 + L_2 + L_3)`.

so, from a backbone of `(u, F)` points with a known axial load `W`:

1. **Slopes** → effective radii: `R_i = W/slope_i`, then `L_1 = R_1/2`,
   `L_2 = R_2 − R_1`, `L_3 = R_3 − R_2` — equivalently
   `L_1 = W/(2·slope_1)`, `L_2 = W/slope_2 − 2·L_1`,
   `L_3 = W/slope_3 − 2·L_1 − L_2`.
2. **Force intercepts** (extrapolated to `u=0`) → `b_i` (the radius-weighted
   composite intercept of each regime), solved back for the per-mechanism
   `mu_i` using the same intercept formula the forward model
   (`TriplePendulumParams.backbone`) evaluates.
3. **Terminal displacement** → capacity `2·Ubar_1 + Ubar_2 + Ubar_3`.  An
   intermediate kink is usually a *friction-transition* displacement
   (`u*_1 = 2·L_1·(μ_2 − μ_1)`, …), **not** a slider limit — so the individual
   `Ubar_i` are underdetermined from a monotonic backbone; only their weighted
   sum is recoverable (the fit splits it equally as a documented placeholder).
   But a kink can also be a *stop transition* — an outer sliding surface
   reaching its displacement limit `Ubar_i` before the terminal bearing
   displacement.  The three-regime fit above assumes every intermediate kink is
   a friction transition; a backbone whose intermediate kink is a stop
   transition is **not representable** by this fit.  Reject such a backbone
   with a warning (fall back to Option C) rather than misreporting it as a
   friction transition.

Fit as a piecewise-linear least-squares problem (1–3 segments), then
sanity-check:

- monotonic `L` and `mu` (the inner regime is softest, `L_1` smallest);
- the recovered terminal capacity ≈ the backbone's terminal displacement;
- reject the fit (fall back to C + warning) if any regime is non-monotonic or
  the residual exceeds a tolerance.

`W` comes from the link's tributary gravity (the same `W`/`minFv` convention
the Dao emitter already uses).  `Uy`/`Kvt`/`minFv`/`tol` keep the emitter
defaults.

## 6. Open questions (verify before coding)

1. **Exact CSI table name/columns** for the Triple-Pendulum Isolator backbone
   (the `.s2k` `LINK PROPERTY DEFINITIONS` number, and whether the backbone is
   a points table or coefficient columns).  Needs a real SAP2000/ETABS v22+
   export or the OAPI `GetTriplePendulumIsolator` signature.  **Status note —
   no SAP2000 access at the time of writing (2026-11-20); the user expects to
   obtain an export "next week".**  When it arrives, confirm the exact table
   titles + columns for `12 - TRIPLE PENDULUM ISOLATOR` and `14 - HIGH DAMPING
   RUBBER ISOLATOR` (and whether a `13` table exists at all), then add them to
   `sap_data.UNCOVERED_LINK_TYPES` (and drop them from
   `s2k_parser._LINK_TABLE_SKIP`).
2. Whether the backbone is **per-direction** or a single 2D curve (the Dao
   element is isotropic in `L`/`Ubar`/`mu` — a per-direction backbone would
   need a direction choice or an average).
3. Whether CSI exposes the **slider displacement limits** at all, or only
   force/deflection points from which `Ubar` must be *inferred*.

## 7. Sequencing

1. Obtain a real ETABS v22+ triple-pendulum export + the OAPI signature (§6.1).
2. ✅ **Done** — `TriplePendulumParams.from_backbone(points, W)` + a companion
   `backbone()` forward model (pure, model-layer, no OpenSees); unit tests in
   `tests/test_sap_data.py` cover the 3-regime round-trip and the symmetric
   2-regime E-Defense case (recovers `L = (0.419, 3.848, 3.848)` and
   `mu = (0.02, 0.08, 0.08)` exactly).  `Ubar` is recovered only in its
   weighted sum (see §5) — the split is a placeholder pending the datasheet.
3. Wire the CSI triple-pendulum table into the parser → build the backbone →
   `from_backbone` → `TriplePendulumParams` (Option A), with the poor-fit
   fallback (Option C).
4. Benchmark: fit → emit → reproduce the published backbone (mirror
   `test_e_defense_tpb.py::test_tpb_backbone_capacity`).
5. Optionally add the `TFP` (Becker–Mahin) emitter for Option B.
