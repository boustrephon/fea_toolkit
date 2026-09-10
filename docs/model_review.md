---
title: "SAP2000 Model Review & Checks"
description: "Standalone solver-free review of a parsed SAP2000 (.s2k) model: inventory, connectivity, element releases, data-integrity checks and an optional OpenSees modal/static confirmation pass."
status: "complete"
tags: [review, checks, s2k, sap2000, connectivity, integrity, diagnostics, cli]
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
```

A console entry point is also installed as `fea-review` (after
`pip install -e .`):

```bash
fea-review model.s2k --analysis
```

The process exit code is `0` when the model is clean, `1` when blocking
issues were found, and `2` on a file/usage error — so the command can gate
a CI step.

### Python API

```python
from fea_toolkit.model import review_model, review_s2k_file

result = review_s2k_file("model.s2k")            # parse + review
# or, if you already have a SAPModelData:
result = review_model(md, include_analysis=False)

print(result["ok"])                              # True when no blocking issues
print(result["inventory"]["frame_elements"])
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
insertion (cardinal) points, auto-mesh usage and mass-source completeness.

### Optional analysis phase (`include_analysis=True`)
Builds the OpenSees domain via the normal
Preprocessor → AnalysisBuilder pipeline and reports:
- **Modal**: periods and mass-participation ratios (Mx/My/Mz).
- **Static**: convergence (catches a singular stiffness matrix) and
  summed support reactions for the applied gravity patterns.

Failures are captured into `result["analysis"]["error"]` rather than
raised, so a review of a broken model still completes.

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
`--max-modes` sets how many are **displayed**.

```python
from fea_toolkit.model import format_review_report

# only modes with >= 1 % participation, at most 3 of them
text = format_review_report(result, max_modes=3, min_participation=1.0)
```

## Result structure

```
result = {
  "file": str | None,
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
  "analysis": None | {ok, periods, mass_participation, static, error},
  "ok": bool,
}
```

## Notes

- The core review has **no pandas dependency** (plain dicts/lists), so it
  stays importable in dependency-light environments.
- The model layer stays OpenSees-free: `openseespy` is imported lazily,
  only when `include_analysis=True`.
- Gravitational/default material properties are applied by the parser
  before the review runs, so material checks read final values.
