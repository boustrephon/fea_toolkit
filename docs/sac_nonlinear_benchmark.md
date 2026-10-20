---
title: "SAC 3-Story Nonlinear Benchmark (T4)"
description: "SAC 3-story steel moment frame (Ohtori et al. 2004): nonlinear NLTHA — bilinear steel hinges, a Krawinkler & Gupta panel zone, and the M1 vs M2 evaluation-model comparison."
status: "complete"
tags: [validation, benchmark, sac, moment-frame, nonlinear, nltha, hinge, panel-zone, ground-motion]
category: [planning]
related: [benchmark_register.md, sac_benchmark.md, ground_motion_data.md, nonlinear_dynamic_analysis.md]
---

# SAC 3-Story Nonlinear Benchmark (T4)

## Purpose

The **T4** (published-FE-model) anchor for the nonlinear NLTHA path.  The same
three-story SAC steel moment frame used for the elastic modal anchor
([SAC 3-Story Frame Benchmark (T2)](sac_benchmark.md)) is built as a
**concentrated-plasticity** model — bilinear steel hinges at the member ends,
with an optional Krawinkler & Gupta panel zone at each beam-column joint — and
driven through the native Newmark transient runner against the published peak
roof displacement.

## Model

Geometry, members and masses follow the official benchmark model data
(`struct3.m`): 4 bays × 9.144 m, 3 stories × 3.9624 m, fixed bases; W14×257 /
W14×311 columns (plus a leaning column), per-level beams (W33×118 / W30×116 /
W24×68 with a W21×44 fourth bay); seismic mass L1 = L2 = 4.7835e5 kg,
L3 = 5.1779e5 kg, half per frame.  The model is built and enriched by
:mod:`fea_toolkit.benchmarks.sac` (single source of truth), which bundles the
Tcl transcription (`sac_3story.tcl`) and adds the plastic section moduli and
yield strengths.

![SAC 3-story frame](assets/sac3_model.png)

### Evaluation-model variants

| Variant | Joint | Member nonlinearity |
|---|---|---|
| **M1** | centreline | bilinear steel hinges |
| **M2** | Krawinkler & Gupta panel zone | bilinear steel hinges |

The two variants share the same enriched `SAPModelData` but differ in the
**preprocessor config** (`panel_zone_model`): M2 is preprocessed into a
separate `MeshModel` whose beam-column joints are split, and the builder then
emits the panel-zone springs from the frozen `MeshModel.panel_zones` spec.

## Member nonlinearity — bilinear steel hinge (D1)

Each frame member is split into `node → hinge → elastic interior → hinge →
node`; the zero-length hinges use a `Steel01` backbone with yield moment
`My = Zx·fy` and a 2 % hardening ratio (`hinge_model='lumped'`,
`hinge_backbone='bilinear'`).

The plastic section moduli are from AISC tables (the benchmark's exact hinge
data are not redistributable):

| Member | Zx (in³ → m³) | fy (MPa) | My = Zx·fy |
|---|---|---|---|
| W14×257 column | 448 → 7.34e-3 | 345 | ≈ 2530 kN·m |
| W14×311 column | 603 → 9.88e-3 | 345 | ≈ 3410 kN·m |
| W33×118 beam | 415 → 6.80e-3 | 248 | ≈ 1690 kN·m |
| W30×116 beam | 378 → 6.194e-3 | 248 | ≈ 1540 kN·m |
| W24×68 beam | 154 → 2.524e-3 | 248 | ≈ 626 kN·m |
| W21×44 beam | 95.4 → 1.563e-3 | 248 | ≈ 388 kN·m |

The leaning column stays elastic (no plastic hinge).

![Bilinear hinge backbone](assets/sac3_hinge_backbone.png)

## Joint modelling — Krawinkler & Gupta panel zone (D2)

`panel_zone_model='krawinkler_gupta'` (a **preprocessor** option) splits each
beam-column joint into a column node and a coincident beam node, coupled by a
zero-length bilinear shear-rotation spring with the K&G backbone:

```
My  = 0.55 · fy · dc · tp · db
Kθ  = 0.95 ·  G · tp · dc · db
```

where `dc` / `db` are the column / beam depths and `tp` the panel thickness
(column web thickness); post-yield hardening is 3 %.

![Panel-zone backbone](assets/sac3_panel_zone.png)

Panel-zone softening is verified independently by the elastic-model period
shift (T1 0.99 → 1.13 s) and the subassemblage test
`test_analysis_csm.py::TestPanelZone`.

## NLTHA benchmark (T4)

Reference — Ohtori, Christenson, Spencer & Dyke (2004),
[*ASCE J. Eng. Mech.* 130(4):366](https://doi.org/10.1061/(ASCE)0733-9399(2004)130:4(366)),
Table 3: the 3-story nonlinear evaluation model reaches a peak roof
displacement of **~0.151 m** under the El Centro record at intensity 1.0.

The gated `@pytest.mark.needs_ground_motion("ELCENTRO")` test drives M1 and M2
under the El Centro record (from the benchmark archive, 2 % damping); **M1 lands
at ≈ 0.145 m** — within ~4 % of the reference.  A second gated
`@pytest.mark.needs_ground_motion("LA21")` test is a smoke check only — LA21 is
the 1995 Kobe (Takatori) record, not El Centro.

![Roof displacement time history (synthetic pulse)](assets/sac3_nltha_synthetic.png)

The always-on figure above uses a synthetic 1 Hz pulse (the real LA21 history
is generated only when the record mirror is populated).  The modal comparison
below shows how each modelling choice shifts the fundamental period.

![Fundamental periods by model](assets/sac3_periods.png)

## Panel zone + hinge composition

Combining the Krawinkler & Gupta panel zone with the bilinear hinge in the full
3-story frame initially produced a degenerate, over-stiff modal response
(T1 ≈ 0.20 s; the sway modes were locked).  The root cause was a **chained
`equalDOF`** at the joint: the panel zone ties the beam's ``*_pz`` node to the
column (`equalDOF(col, beam, 1, 3)`), and the lumped hinge then tied its hinge
node to that ``*_pz`` node (`equalDOF(beam, hinge, 1, 2, 3)`), making the hinge
a slave-of-a-slave that the `Transformation` constraint handler mis-resolved —
locking the frame's lateral DOFs.  The fix ties the hinge node **directly to the
column node** (star topology — `equalDOF(col, hinge, 1, 3)` + out-of-plane fix),
leaving the panel-zone spring and the hinge in series.  M2 now has
**T1 ≈ 1.13 s**, correctly softer than both the elastic model (0.99 s) and M1
(1.04 s).  Regression-guarded by `test_sac3_m2_panel_zone_modal_period`.

## Loadable artifact

The benchmark is a **loadable artifact**, not a private test helper:

- **Model** — :mod:`fea_toolkit.benchmarks.sac` exposes `load_model()`
  (enriched `SAPModelData`), `preprocess_config(panel_zone=…)`,
  `builder_config()` and `build_builder(panel_zone=…)`.
- **Committed model-codec snapshot** — `docs/assets/sac3_model.json` (written by
  `docs/_generate_sac_figures.py`), openable in the viewer:

  ```bash
  python examples/view_model.py docs/assets/sac3_model.json --result mesh
  ```

  > The model-codec snapshot reproduces the geometry, enriched sections
  > (`Z33`, `fy`) and thus the **M1** bilinear-hinge model.  It drops the
  > I-section dimensions (`depth`/`tw`), so the **M2** panel zone must be built
  > from the package builder rather than the JSON snapshot.

## Figures

`docs/_generate_sac_figures.py` regenerates the five figures and the JSON
artifact (each only when its file is absent), writing into `docs/assets/`.

## Research links

- Ohtori et al. (2004) — https://doi.org/10.1061/(ASCE)0733-9399(2004)130:4(366)
- Krawinkler (1978), "Shear in Beam-Column Joints in Seismic Design of Steel
  Frames", *AISC Engineering Journal* 15(3) — the panel-zone backbone.
- Krawinkler & Gupta (1998) — SAC steel-frame design / panel-zone modelling.
- AISC *Steel Construction Manual* — the `Zx` tables behind the derived yield
  moments (W24×68, W30×116).
- SAC ground motions (LA21) — see [Ground-Motion Data & Sources](ground_motion_data.md).

## Tests

- `tests/test_benchmark_sac3_nltha.py` — elastic + nonlinear NLTHA (synthetic
  pulse always-on, M1 + M2; LA21 gated), M1 band assertion, and
  `test_sac3_m2_panel_zone_modal_period` (M2 fundamental-period regression guard).
- `tests/test_analysis_csm.py::TestBuilderHingeModel` — bilinear hinge unit
  tests (config defaults, cantilever yield).
- `tests/test_analysis_csm.py::TestPanelZone` — panel-zone subassemblage.

