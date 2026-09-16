---
title: "SAP2000 Model Review & Checks"
description: "Standalone solver-free review of a parsed SAP2000 (.s2k) model: inventory, connectivity, element releases, data-integrity checks, analytical self-weight and Euler brace buckling, plus an optional OpenSees modal/static confirmation pass with load verification, a wind sanity check and a GB 50011 response-spectrum (CQC) pass."
status: "complete"
tags: [review, checks, s2k, sap2000, connectivity, integrity, self-weight, buckling, wind, response-spectrum, diagnostics, cli]
category: [model-features]
related: [workflow.md, element_classification.md, report_generation.md]
---
# SAP2000 Model Review & Checks

`fea_toolkit.model.review` provides a **first-pass review** of a parsed
SAP2000 model (`SAPModelData`) before any analysis is run.  It answers the
question *"is this model sensible, complete and fully connected?"* and
surfaces the modelling problems that typically precede a singular
stiffness matrix or a silently wrong result.

The review is **solver-free by default** — it never builds an OpenSees
domain.  An optional second phase runs a modal + linear-static analysis to
confirm static and dynamic behaviour.

## Quick start

The model file is always supplied by the caller — nothing is hard-coded.

```bash
# Console review (text)
python -m fea_toolkit.model.review path/to/model.s2k

# Write a Markdown report
python -m fea_toolkit.model.review model.s2k --format markdown --out review.md

# Include the optional OpenSees modal + static confirmation pass
python -m fea_toolkit.model.review model.s2k --analysis --num-modes 6

# Show only significant modes: >= 1 % mass participation, capped at 3 rows
python -m fea_toolkit.model.review model.s2k --analysis --min-participation 1 --max-modes 3

# Solver-free checks: analytical self-weight + Euler brace buckling
# (brace table capped at 2 rows; the brace check is skipped if the model
#  contains no brace sections)
python -m fea_toolkit.model.review model.s2k --self-weight --brace-buckling --num-braces 2

# Analysis-phase extra checks: applied-vs-reaction equilibrium per load
# pattern and the wind load sanity check (both imply --analysis)
python -m fea_toolkit.model.review model.s2k --load-verify --wind-check

# GB 50011 response-spectrum pass (CQC/SRSS base shear, overturning moment
# and roof displacement per direction, plus the per-mode base shear)
python -m fea_toolkit.model.review model.s2k --response-spectrum
python -m fea_toolkit.model.review model.s2k --response-spectrum \
    --spectrum-level rare --spectrum-intensity 7 --spectrum-site-class II

# Write a unified NPZ archive (geometry + modal + static results)
python -m fea_toolkit.model.review model.s2k --analysis --npz results.npz

# Per-element response-spectrum forces into the NPZ archive, for later
# viewing (implies --response-spectrum; costs an extra mode loop over
# every element, so it is opt-in)
python -m fea_toolkit.model.review model.s2k --analysis \
    --rs-element-forces --rs-combination cqc --npz results.npz
```

A console entry point is also installed as `fea-review` (after
`pip install -e .`):

```bash
fea-review model.s2k --analysis
```

The process exit code is `0` when the model is clean, `1` when blocking
issues were found, and `2` on a file/usage error — so the command can gate
a CI step.

### Element-level response-spectrum forces

`--rs-element-forces` records the **element-local** end forces of every mode
and combines them across modes — CQC by default, SRSS with
`--rs-combination srss` — into the `rs/elem_*` block of the NPZ archive.
OpenSees combines nothing itself: `responseSpectrumAnalysis -mode n` processes
one mode at a time, so the combination is performed by the toolkit.

Writing is the review's job; **rendering is not**.  `model.review` produces no
plots at all, so view the archive with `examples/view_model.py`:

```bash
python examples/view_model.py results.npz --result rs --quantity Mz
python examples/view_model.py results.npz --result rs --quantity fy --dimension 3d
```

The response-spectrum diagram is **2D by default** (quantity vs elevation);
`--dimension 3d` draws the per-element tubes/flags instead.  Both take
`--quantity` in either the local force names (`fx`, `fy`, `fz`, `mx`, `my`,
`mz`) or the moment names (`Mz`, `My`, …).

Because the extra pass is `O(modes × elements)`, an archive written *without*
the flag simply has no `rs/elem_*` block — the viewer reports that rather than
failing.  See [`results_schema`](results_schema.md) for the local-force
component ordering and the full key list.

### Python API

```python
from fea_toolkit.model import review_model, review_s2k_file

result = review_s2k_file("model.s2k")            # parse + review
# or, if you already have a SAPModelData:
result = review_model(md, include_analysis=False)

print(result["ok"])                              # True when no blocking issues
print(result["inventory"]["frame_elements"])

# Optional solver-free checks
result = review_model(md, self_weight=True, brace_buckling=True, brace_k=1.0)

# Optional analysis-phase extra checks (require openseespy)
result = review_model(
    md,
    include_analysis=True,
    analysis_config={"num_modes": 6, "load_verify": True, "wind_check": True},
)

# GB 50011 response-spectrum pass (requires openseespy)
result = review_model(
    md,
    include_analysis=True,
    analysis_config={
        "num_modes": 6,
        "response_spectrum": True,
        "spectrum": {"level": "rare", "intensity": 7, "site_class": "II"},
    },
)
rs = result["analysis"]["response_spectrum"]
print(rs["directions"]["X"]["base_shear_cqc"])   # CQC base shear, X direction
print(rs["directions"]["X"]["roof_disp_cqc"])    # CQC roof displacement, X

# Write a unified NPZ (geometry + modal + static results)
result = review_model(md, include_analysis=True, export_npz="results.npz")
print(result["npz"])                             # written path (or None)
print(result["npz_error"])                        # captured failure, or None
```

## What it checks

### Inventory
Counts of every object category: nodes, restraints, frame/area elements,
materials, sections, section assignments, frame releases, groups,
constraints, load cases, load patterns, joint / distributed / gravity /
area loads, mass sources, frame auto-mesh assignments and end offsets.

### Breakdown
Per-type frequency tables for restraint DOF patterns, materials, sections,
load cases and load patterns.

### Connectivity
- **Connected components** — union-find over the node↔element graph
  (element connectivity plus joint constraints) reports every independent
  sub-structure and whether it reaches a support.
- **Floating sub-structures** — components with elements but **no
  restraint** (the *"independent structures without supports"* check).
- **Orphan (loose) nodes** — nodes not attached to any element.
- **Duplicate coordinates** — coincident nodes that are not merged.
- **Shell-only base nodes** — base nodes carried only by shell elements.

### Element releases
Frames with end releases (partial or full), per local DOF (`P, V2, V3, T,
M2, M3`) at each end, parsed from SAP2000's *"FRAME RELEASE ASSIGNMENTS 1 -
GENERAL"* (or legacy *"FRAME RELEASES"*) table.

### Integrity
- Elements referencing **missing nodes**.
- Frames/areas with **no section assignment**.
- Sections referencing **missing materials**.
- **Zero-length** (degenerate) frames.
- **Duplicate / overlapping** frames (same node pair).
- **Unreferenced** sections, materials and load patterns.
- Loads pointing at **missing nodes/elements** and **undefined patterns**.

Counts for every category are returned under `result["integrity"]["counts"]`;
a non-zero count in a *blocking* category sets `result["ok"] = False`.

### Observations (not pass/fail)
Support fixity summary (e.g. all-pinned vs all-fixed), non-default
insertion (cardinal) points, auto-mesh usage, and the **mass source** —
its name, whether it draws on elements / added masses / load patterns, and
the **load patterns it includes** with their multipliers (e.g.
`Mass source load patterns: Pipe Dead Load x1`).

### Self-weight (analytical) — `--self-weight`
The expected self-weight derived from element geometry and material unit
weights (via `check_self_weight_consistency()`), presented as two tables:

- **By section** — one row per section (the resolved frame/area assignment).
- **By material** — the same weight regrouped by each section's material,
  so the total per material is visible.

A `Total` row closes each table (equal to the headline *Expected
self-weight*), and the two groupings always sum to the same total.  This is
**solver-free** and reported only: `applied`, `discrepancy` and `passed`
stay `None` because confirming the applied load against the reactions is
the analysis phase's job (see *Load verification* below).

### Brace buckling — `--brace-buckling`
The Euler buckling capacity `P_cr = π²EI₂₂/(KL)²` of the model's braces,
via `check_brace_buckling()`. Braces are auto-detected by section shape
(Pipe / Angle / Double Angle / Tee / Channel) — **the check is skipped
entirely when the model has no brace sections** (no Euler capacity is
fabricated for non-brace members). `--k-factor` sets the effective length
factor `K` (default 1.0) and `--num-braces N` caps the rows displayed.

### Optional analysis phase (`include_analysis=True`)
Builds the OpenSees domain via the normal
Preprocessor → AnalysisBuilder pipeline and reports:
- **Modal**: periods and the **6-DOF** mass-participation ratios —
  translational `Mx`/`My`/`Mz` **and** rotational `Rx`/`Ry`/`Rz` — set out
  in a table that ends with a `SUM` row (cumulative participation summed
  over **all** modes, even when the displayed rows are filtered), matching
  the `modal_table_enhanced()` presentation used by the report pipeline.
- **Static**: convergence (catches a singular stiffness matrix) and the
  summed support reactions as a table (`Fx`/`Fy`/`Fz` plus `Mx`/`My`/`Mz`),
  alongside the total **seismic mass and weight** derived from the model's
  MASS SOURCE (`total_mass` in the model's consistent mass unit — tonnes
  for a kN‑m model — and `total_weight` = mass × `g_from_units()`), broken
  down into its components: element self‑weight (material density), masses
  assigned to joints, and load‑pattern mass (each shown as both a mass and
  a weight).  Only the **global‑Z** component of a load pattern
  contributes, per SAP2000's mass‑source rule.
- **Load verification** (`analysis_config["load_verify"]` / `--load-verify`):
  per-pattern **applied vs reaction** equilibrium (`Applied`, `Reaction`
  and `Δ` components) via `static_load_verification()`, as a table.
- **Wind sanity check** (`analysis_config["wind_check"]` / `--wind-check`):
  wind loads against the bounding-box face areas, as a table — the
  structured `wind_sanity_data()` feeds the table, while
  `wind_sanity_check()` remains the Markdown form for the report pipeline.
  The report prints the basis for the numbers (see below).
- **Response spectrum** (`analysis_config["response_spectrum"]` /
  `--response-spectrum`): a GB 50011 mode-by-mode response-spectrum pass
  over the modal results already computed (via
  `AnalysisBuilder.run_response_spectrum_analysis()`), reported as two
  tables — a **per-direction** summary of the CQC/SRSS base shear and
  overturning moment plus the CQC/SRSS roof displacement, and the
  **per-mode** base shear so the modal make-up of the total is visible.
  The per-mode table ends with a footer row carrying the **combined** base
  shear for the active rule only, titled `CQC` or `SRSS` to match
  `--rs-combination` (default `cqc`).  Runs for `X` and `Y` by default.

  | CLI flag | Config key | Default | Meaning |
  |---|---|---|---|
  | `--spectrum-level` | `spectrum["level"]` | `rare` | GB 50011 level: `frequent` / `fortification` / `rare` |
  | `--spectrum-intensity` | `spectrum["intensity"]` | `7` | Seismic intensity 6–9 |
  | `--spectrum-site-class` | `spectrum["site_class"]` | `II` | Site class `I0`–`IV` (sets `T_g`) |
  | `--spectrum-acceleration` | `spectrum["acceleration"]` | `0.10` | Peak ground acceleration (g) for the fortification level |
  | `--spectrum-damping` | `spectrum["damping"]` | `0.05` | Damping ratio (drives the γ / η₁ / η₂ shape factors) |
  | `--num-modes` | `num_modes` | `12` | Modes included (clamped to the periods available) |
  | — | `spectrum["directions"]` | `["X", "Y"]` | Excitation directions |

  The demand spectrum comes from `spectrum._build_spectrum()` — the same
  builder the report pipeline and `run_linear_cases()` use — so review and
  report numbers agree.  Amplitudes are reported in the model's own units
  (the report pipeline passes `g_from_units(model.units)`; a standalone call
  with `g=None` falls back to the shared SI constant
  `fea_toolkit.utils.DEFAULT_GRAVITY_MS2`, 9.80665 m/s²).

  ```bash
  python -m fea_toolkit.model.review model.s2k --response-spectrum
  python -m fea_toolkit.model.review model.s2k --response-spectrum \
      --spectrum-level rare --spectrum-intensity 8 --spectrum-site-class III
  ```

Failures are captured into `result["analysis"]["error"]` rather than
raised, so a review of a broken model still completes.  The optional
analysis-phase checks are captured individually
(`result["analysis"]["load_verification_error"]` / `["wind_error"]` /
`["response_spectrum_error"]`) and never abort the modal/static pass.

#### Wind sanity check — basis

The wind check is a **plausibility test that the applied wind load scales
with the building envelope** — it is *not* a code or wind-tunnel pressure.
Every number comes from the model itself:

| Quantity | Source |
|---|---|
| `x_face` | bounding-box `y_span × z_span` — the face normal to global X (windward for the +X wind case) |
| `y_face` | bounding-box `x_span × z_span` — the face normal to global Y |
| `fx`, `fy` | absolute base reaction (`Fx` of the `Wind+X` case, `Fy` of `Wind+Y`) from the linear analysis results |
| `p_x`, `p_y` | `fx / x_face`, `fy / y_face` — the implied **mean pressure** on each face |

The check passes when `|p_x − p_y| / max(p_x, p_y) < 0.1` — the two implied
pressures agree, the signature of a consistent wind load set.  A large
mismatch usually flags a missing/duplicated wind area or a load applied to
the wrong face.  All quantities are in the model's own unit system.

### NPZ export (`--npz`)

Writes a single compressed NumPy archive through the canonical
[`write_results()`](results_schema.md) unified writer, so a reviewed model
can be re-plotted standalone (PyVista / Rhino) without the `.s2k` or a
re-run:

- **Without `--analysis`** — model geometry only (nodes, frames, shells,
  sections).  This path is **solver-free** and fast.
- **With `--analysis`** — the **meshed** geometry plus the modal result
  (periods, mass ratios, mode shapes) and the static gravity result
  (nodal displacements, frame end forces) per the unified NPZ schema.
- **With `--response-spectrum` too** — the canonical `rs/*` block:
  `rs/period`, the per-mode `rs/v_base_x` / `rs/v_base_y`, and the
  combined `rs/v_cqc_*` / `rs/v_srss_*` (base shear), `rs/m_cqc_*` /
  `rs/m_srss_*` (overturning moment) and `rs/roof_disp_cqc_*` /
  `rs/roof_disp_srss_*` (roof displacement).  `rs/period` is trimmed to
  the modes the RS pass actually used, so it stays aligned with
  `rs/v_base_*`.  The single-direction CQC displacement field is written
  as `rs/node_*` (see below).

***Element-level RS forces (opt-in)***

Passing `--rs-element-forces` (API: `analysis_config["spectrum"]["element_forces"] = True`)
additionally records the per-element combined forces as the `rs/elem_*` block —
one row per element, the full local end-force set (`rs/elem_fx_i` …
`rs/elem_mz_j`), the rule used (`rs/elem_combination`) and the direction
(`rs/elem_direction`).  This is what enables per-element RS force diagrams:

```bash
python -m fea_toolkit.model.review model.s2k --response-spectrum \
    --rs-element-forces --rs-combination cqc --npz review.npz
python examples/view_model.py review.npz --result rs --quantity Mz          # 2D
python examples/view_model.py review.npz --result rs --quantity Mz --dimension 3d
```

`--rs-combination` selects `cqc` (default) or `srss`.  OpenSees does not combine
modes for us — `responseSpectrumAnalysis -mode n` processes one mode at a time —
so the toolkit performs the combination itself.

`--rs-element-extraction` selects how those per-mode forces are read:

| Value | Mechanism | Cost (1263 elements × 20 modes) |
|---|---|---|
| `per_mode` (default) | one `responseSpectrumAnalysis -mode n` per mode, then one `eleResponse` per element per mode | **0.09 – 0.47 s** |
| `recorder` | one all-modes `responseSpectrumAnalysis` behind an `Element` recorder, read from a single file | 0.72 s (0.57 s writing a 6.9 MB text file) |

Both are **bit-identical** — the option exists to make that verifiable, not
because one is more accurate.  The per-mode loop is currently the faster of the
two on real models because `eleResponse` is cheap (~7 µs) while the recorder's
ASCII output is not.  `recorder` is worth measuring again on much larger models,
or if `eleResponse` ever becomes an expensive call.

It is **off by default** because the extraction costs an extra mode loop over
every element (`O(n_modes × n_elements)`), which would slow down the default
quality-control pass on large models.

**What the review still does not export**

| Not exported | Why | Consumer that needs it |
|---|---|---|
| `static/{case}/*_local` | The 12 required force arrays already hold local-frame forces, flagged by the `forces_coordinate_system` metadata; visualisers synthesise the `*_local` aliases on read | none — derivable |
| `rs/storey_*` | Per-storey RS aggregation is not yet implemented | storey-level RS force plots |

> **Possible future enhancement — an "enhanced QC" stage.**  With
> `--rs-element-forces` the review now carries element demand, so a later stage
> could compare demand/capacity per element and flag overstressed members
> directly.  Summing the `rs/elem_*` rows by elevation into a persisted
> `rs/storey_*` block (per-storey RS forces) is the natural next step.

The written path is reported under `result["npz"]`; an export failure is
captured under `result["npz_error"]` and never aborts the review.

The archive is consumable by the standard visualisation helpers — read it
with `read_results_npz()` / `read_results()` and pass the dict to
`npz_to_pyvista_frame_mesh()`, `npz_to_rhino_colour_data()`,
`plot_force_diagram()` or `_load_deformed_arrays()`.  The unified writer
emits the row-alignment key `modal/node_tag` (mode-shape animation) and the
`frame_parent_node_i` / `frame_parent_node_j` endpoints
(`collapse_to_parents`) that those helpers rely on.  The `rs/node_*` block is
**single-direction**: it carries the CQC displacement field of the first
configured direction (X by default), because the Rhino RS deformed-shape
overlay takes no direction argument.

When an archive is written the report also prints an **NPZ archive
contents** manifest, and the same summary is available under
`result["npz_contents"]` (produced by `describe_results_npz()`):

| Item | Detail |
|---|---|
| Geometry | 754 nodes, 1263 frames, 0 shells |
| Results | static [DEAD]; modal (12 modes) |
| Units | kN, m |
| Arrays | 57 |
| Created | 2026-09-14T15:21:30 |

This states whether **geometry** is present and **which analysis results**
were recorded — static case labels and the number of modal modes (plus
`rs` / `pushover` / `shell_forces` when present) — so the archive is
self-describing without the reader having to open it.

```bash
# Geometry only (solver-free)
python -m fea_toolkit.model.review model.s2k --npz geometry.npz

# Geometry + modal + static results
python -m fea_toolkit.model.review model.s2k --analysis --npz results.npz
```

```python
from fea_toolkit.io.npz_reader import read_results_npz

data = read_results_npz("results.npz")
print(list(data["analysis_types"]))   # e.g. ['static', 'modal', 'rs']
```

#### Mode display limits

Both formatters print **every** computed mode by default.  Two optional
knobs (CLI flags and formatter keyword arguments) trim the listing:

| CLI flag | Formatter kwarg | Effect |
|---|---|---|
| `--max-modes N` | `max_modes=N` | Show at most the first `N` modes (applied after filtering). `0` = all. |
| `--min-participation PCT` | `min_participation=PCT` | Hide modes whose largest translational mass participation (max of `Mx`/`My`/`Mz`, in percent) is below `PCT`. |

When rows are suppressed the report prints a
`… N further mode(s) not shown` note, so a filtered listing is never
ambiguous.  `--num-modes` sets how many modes are **computed**;
`--max-modes` sets how many are **displayed**.  The `SUM` row always sums
**all** computed modes, even when rows are filtered.

```python
from fea_toolkit.model import format_review_report

# only modes with >= 1 % participation, at most 3 of them
text = format_review_report(result, max_modes=3, min_participation=1.0)
```

## Result structure

```
result = {
  "file": str | None,
  "table_coverage": dict | None,
  "units": {"F": ..., "L": ..., "T": ...},
  "inventory": {category: count},
  "breakdown": {category: {key: count}},
  "bounds": {x_min, x_max, x_span, ...} | None,
  "connectivity": {orphan_nodes, duplicate_coords, shell_only_base_nodes,
                   zero_area_sections, n_components, components,
                   floating_components},
  "releases": {n_frames_with_releases, by_dof, releases},
  "integrity": {..., "counts": {category: count}},
  "observations": {restraint_patterns, all_translation_only,
                   all_fully_fixed, non_default_cardinal_points,
                   auto_mesh_assigned, mass_source},
  "self_weight": None | {expected, by_section, by_material, applied,
                         discrepancy, passed},
  "brace_buckling": None | {detected, k_factor, members},
  "analysis": None | {ok, periods, mass_participation, static,
                      load_verification, wind, response_spectrum,
                      mass_source, error, load_verification_error?,
                      wind_error?, response_spectrum_error?},
  "npz": str | None,
  "npz_error": str | None,
  "npz_contents": dict | None,
  "ok": bool,
}
```

`self_weight` is populated by `--self-weight`; `brace_buckling` by
`--brace-buckling` (with `detected=False` and empty `members` when the
model has no braces).  Each `analysis["mass_participation"]` entry carries
`mode`, `period`, `frequency` and the six ratios `mx`/`my`/`mz`/`rx`/`ry`/`rz`.
`analysis["mass_source"]` reports the seismic mass totals
(`name`, `total_mass`, `total_weight`, `gravity`, `n_nodes_with_mass`, plus
the `from_elements` / `from_masses` / `from_loads` flags and the
`load_patterns` it includes) and a `components` breakdown of that mass by
source — `elements` (material-density self-weight), `masses` (masses
assigned directly to joints; not yet parsed, so currently `0.0`) and
`loads` (load-pattern mass, global-Z component only, per SAP2000).
The `analysis` sub-dict's `load_verification` (a list of per-pattern
applied-vs-reaction records) and `wind` (structured data from
`wind_sanity_data()`: `rows` and `within_10pct`, plus the raw values) are
populated by `--load-verify` / `--wind-check` respectively.  A failure in
either check leaves the corresponding `load_verification_error` /
`wind_error` string in place instead (both keys are optional and appear
only when that check raised).
`response_spectrum` is populated by `--response-spectrum`.  It carries the
resolved `spectrum` descriptor (`code`, `label`, `level`, `intensity`,
`site_class`, `acceleration`, `damping`, `alpha_max`, `tg`, `n_modes`,
`directions`) and a `directions` dict keyed `"X"` / `"Y"`; each entry holds
`base_shear_cqc`, `base_shear_srss`, `base_moment_cqc`, `base_moment_srss`,
`base_reactions_cqc` (the 6-DoF combined reactions), the per-mode
`modal_base_shear` / `modal_base_moment` lists and the roof displacement
(`roof_disp_cqc` / `roof_disp_srss`).  A failure leaves
`response_spectrum_error` in place instead.
`npz` holds the path written by `--npz` (with any failure in `npz_error`);
`npz_contents` holds the archive manifest (geometry + analysis results)
from `describe_results_npz()`.  `table_coverage` holds the triage of the
parsed SAP2000 tables (handled / known-gap / ignored / unhandled) as a
dict when the caller supplied ``raw_tables``, and ``None`` otherwise (e.g.
for a hand-built ``SAPModelData``).

## Notes

- The core review has **no pandas dependency** (plain dicts/lists), so it
  stays importable in dependency-light environments.  Tables are rendered
  with `tabulate` when it is installed (the `[report]` extra); otherwise a
  dependency-free fixed-width / pipe-table fallback is used.
- The model layer stays OpenSees-free: `openseespy` is imported lazily,
  only when `include_analysis=True`.
- Gravitational/default material properties are applied by the parser
  before the review runs, so material checks read final values.
