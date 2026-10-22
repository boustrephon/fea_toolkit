---
title: "FEMA 355C SAC Buildings (Boston 3- & 9-Story)"
description: "The SAC Phase-2 prototype steel moment-frame buildings for Boston, from FEMA 355C — planar N-S frames and full 3D models, viewable in the FEA-GUI."
status: "complete"
tags: [benchmark, sac, fema355c, boston, moment-frame, boston3, boston9, 2d, 3d]
category: [benchmark]
related: [sac_benchmark.md, sac_nonlinear_benchmark.md, benchmark_register.md]
---

# FEMA 355C SAC Buildings (Boston 3- & 9-Story)

The SAC Steel Project's Phase-2 prototype buildings — reported in **FEMA 355C**
(*State of the Art Report on Systems Performance of Steel Moment Frames Subject
to Earthquake Ground Shaking*) — are reproduced here as loadable models: a
**planar N-S perimeter moment frame** (2D) and the **full building** (3D), for
the **Boston** 3-story and 9-story buildings.

The **Los Angeles** buildings are the Ohtori benchmark
([SAC 3-Story Frame Benchmark (T2)](sac_benchmark.md) /
[T4](sac_nonlinear_benchmark.md)) — the heavy W14x257 / W14x311 column schedule.
Boston uses the *lighter* as-designed members (W14x74 / W14x99 for the
3-story).  Same building geometry, different member sizes by region.

## Source

- FEMA 355C — the member schedules and building description (tables below).
- Ohtori et al. (2004), *J. Eng. Mech.* 130(4):366 — the building geometry
  (plan, bays, story heights, moment-frame layout), which FEMA 355C shares.
- AISC *Steel Construction Manual* — section properties (A, I, Z, depth, etc.),
  resolved from the project's section database and hardcoded in
  :mod:`fea_toolkit.benchmarks.sac_boston`.

## Building geometry

| | Boston 3-story | Boston 9-story |
|---|---|---|
| Plan (N-S × E-W) | 4 bays × 6 bays @ 30 ft (9.144 m) | 5 bays × 5 bays @ 30 ft (9.144 m) |
| Story heights | 3 × 13 ft (3.9624 m) | basement 12 ft, ground 18 ft, 8 × 13 ft |
| Lateral system | perimeter N-S moment frames | perimeter N-S moment frames |
| Moment frame | **3 bays** + 1 gravity bay | **4.5 bays** (4 full + 1 single-MR bay) |
| Base | **fixed** | **pinned at B-1**, laterally restrained at ground |
| Penthouse | C3–D5 | C3–E4 (not modelled — mass only) |

### The gravity column (not a "leaning column")

The far column line of the N-S frame (**E** for the 3-story, **F** for the
9-story) is an ordinary vertical column that is **not part of the moment
framing**.  Its inertial loads are passed directly to the moment-frame bays
through the floor beams, which are **released for major moment (M3)** at that
column's end — so the beam transfers axial load, not moment, into the gravity
column (a P-Δ "leaning" effect).  The moment frame proper is therefore 3 bays
(3-story) or 4.5 bays (9-story, the outer bay having only a single moment
connection — FEMA 355C note 2).

## Member schedules (FEMA 355C)

### Boston 3-story

| Story | Columns (ext / int) | Girder | Beams |
|---|---|---|---|
| 1/2 | W14x74 / W14x99 | W18x35 | W16x26 |
| 2/3 | W14x74 / W14x99 | W21x57 | W16x26 |
| 3/Roof | W14x74 / W14x99 | W21x62 | W14x22 |

Gravity column (grid E) = W12x58; penthouse columns W12x65 / W12x72 (not modelled).

### Boston 9-story

| Story | Columns (ext / int) | Girder |
|---|---|---|
| -1/1 | W14x211 / W14x283 | W24x68 |
| 1/2 | W14x211 / W14x283 | W36x135 |
| 2/3 | W14x211,W14x159 / W14x283,W14x233 | W33x118 |
| 3/4 | W14x159 / W14x233 | W30x116 |
| 4/5 | W14x159,W14x132 / W14x233,W14x211 | W30x116 |
| 5/6 | W14x132 / W14x211 | W30x108 |
| 6/7 | W14x132,W14x99 / W14x211,W14x176 | W30x99 |
| 7/8 | W14x99 / W14x176 | W27x94 |
| 8/9 | W14x99,W14x61 / W14x176,W14x120 | W24x76 |
| 9/Roof | W14x61 / W14x120 | W18x40 |

Column splices every two levels (FEMA 355C notes 3–6); the single-MR bay girder
follows note 2 (W24x68 … W18x40).  Gravity column (grid F) = exterior schedule,
W14x61 → W14x68 at the top (note 1).

## Using the models

```python
from fea_toolkit.benchmarks import boston3_2d, boston3_3d, boston9_2d, boston9_3d, write_snapshots

md = boston3_2d()                         # planar N-S frame, default seismic mass
md = boston3_2d(masses=[4.78e5, 4.78e5, 5.18e5])  # override per-level (half-building)
write_snapshots()  # -> docs/assets/sac_boston*.json
```

View in the FEA-GUI:

```bash
python examples/view_model.py docs/assets/sac_boston3_2d.json --result mesh
python examples/view_model.py docs/assets/sac_boston3_3d.json --result mesh
python examples/view_model.py docs/assets/sac_boston9_2d.json --result mesh
python examples/view_model.py docs/assets/sac_boston9_3d.json --result mesh
```

## Model sizes

| Model | Nodes | Elements | Releases |
|---|---|---|---|
| `boston3_2d` | 20 | 27 | 3 (gravity-bay beams) |
| `boston3_3d` | 140 | 279 | 6 |
| `boston9_2d` | 66 | 110 | 10 |
| `boston9_3d` | 396 | 960 | 20 |

The 2D frame node/element counts match the benchmark (20/27 for the 3-story,
66/110 for the 9-story).  The 2D frames are planar X-Z models with out-of-plane
restraints (UY/RX/RZ) on every node; the 9-story pins the base (RX/RY/RZ free)
and restrains the ground level laterally.

## Seismic mass

The **same massing applies to all three regions** (LA, Seattle, Boston) — FEMA
355C Appendix B states that *"the basic definitions for the buildings were kept
constant between the different regions"*; only the member sections differ.  The
seismic masses (entire structure) are tabulated below (1 kip-sec²/ft =
14593.9 kg); the 3-story values reconcile with the Ohtori benchmark's 9.57×10⁵
/ 1.04×10⁶ kg (65.5 / 71.0 kips-sec²/ft).

| Building | Level | kips-sec²/ft | kg (whole building) | kg (per frame) |
|---|---|---|---|---|
| 3-story | Roof | 70.90 | 1.035×10⁶ | 5.172×10⁵ |
| 3-story | Floor 2 & 3 | 65.53 | 9.563×10⁵ | 4.782×10⁵ |
| 9-story | Roof | 73.10 | 1.067×10⁶ | 5.334×10⁵ |
| 9-story | Floor 3 … 9 | 67.86 | 9.903×10⁵ | 4.952×10⁵ |
| 9-story | Floor 2 | 69.04 | 1.008×10⁶ | 5.038×10⁵ |
| 9-story | Ground | 66.0 | 9.65×10⁵ | 4.825×10⁵ |
| 20-story | Roof | 40.06 | 5.846×10⁵ | 2.923×10⁵ |
| 20-story | Floor 3 … 20 | 37.76 | 5.511×10⁵ | 2.755×10⁵ |
| 20-story | Floor 2 | 38.63 | 5.638×10⁵ | 2.819×10⁵ |

These are wired in as defaults (``MASS_3STORY`` / ``MASS_9STORY`` in the
module): the 3D builders use the whole-building values, the 2D builders half.
Masses are **lumped at the nodes** — *not* at a single diaphragm centroid —
proportional to tributary area, matching the benchmark's lumped-mass convention:

- **2D frame** — distributed across the column lines by tributary bay width
  (edge nodes half a bay, interior nodes a full bay).
- **3D building** — distributed across the grid by tributary slab area (corner
  nodes a quarter panel, edge nodes a half panel, interior nodes a full panel).

No lumped **rotational** inertia is applied — the benchmark's mass matrix is
translational (its ``1e-6`` rotational term is a numerical stabiliser, not a
physical rotary inertia), and the toolkit's ``added_masses`` is translational
only.

## Gravity loads (dead + live)

Dead and reduced live loads are applied as **nodal loads** (``joint_loads``) in
two patterns — ``DEAD`` and ``LIVE`` — distributed by the *same* tributary
split as the masses.  Pressures are the FEMA 355C floor-load distribution
(Ohtori 2004, *"steel weight assumed 13 psf for all designs"*):

| Load | psf | Pa (N/m²) |
|---|---|---|
| Floor dead (weight) | 96 | 4596.5 |
| Roof dead (excl. penthouse) | 83 | 3974.1 |
| Reduced live (floor *and* roof) | 20 | 957.6 |

The **steel frame self-weight (13 psf)** is *not* added as nodal loads — it is
carried by the section material density (``unit_weight``), so a self-weight
gravity case captures it.  The penthouse dead load (116 psf) is omitted, like
the penthouse itself.  The 2D builders use half the building (one perimeter
frame); the 3D builders the whole footprint.

## Floor panels (loads-only visualisation)

The **3D** builders also emit one **floor panel per bay per level** as
``area_elements``.  These are **loads-only** — they carry no section (empty
``area_assignments``), so the AnalysisBuilder creates no shell element and they
add no stiffness; they exist purely to render the floor-diaphragm surfaces in
the viewer.  The 2D planar frames have no floor surface and hence no panels.
(Per-face wind panels plus wind nodal loads are a planned follow-on.)

## Tests

- `tests/test_benchmark_sac_boston.py` — counts, codec round-trip, gravity-bay M3
  release, 3-story column assignment, 9-story basement/ground levels, seismic
  mass lumping (tributary + conservation), dead/live gravity totals and
  tributary split, floor-panel count/connectivity, snapshots.
