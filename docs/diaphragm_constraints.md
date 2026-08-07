---
title: "Storey Identification & Diaphragm Constraints"
description: "How storey levels are identified from SAP2000 models and applied as rigid diaphragm constraints in OpenSees."
status: "complete"
tags: [storey, diaphragm, constraints, rigidDiaphragm, z-tolerance]
category: [model-features]
related: [storey_response.md, shell_support.md, workflow.md, constraint_detection.md]
---
# Storey Identification & Diaphragm Constraints

This document describes the full workflow from a parsed SAP2000 model
through to `ops.rigidDiaphragm()` constraints in the OpenSees domain —
covering the three architectural layers involved:

1. **`SAPModelData`** — the structured record of what the `.s2k` declares.
2. **`fea_toolkit.model.stories`** — storey-level *inspection* (model layer).
3. **Preprocessor / AnalysisBuilder** — diaphragm *application* (analysis layer).

## 1. Constraint Data on `SAPModelData`

`SAPModelData` carries two structured attributes describing the joint
constraints declared in the `.s2k` file:

| Attribute | Type | Description |
|---|---|---|
| `md.constraints` | `dict[str, Constraint]` | Constraint definitions, keyed by name. `Constraint.constraint_type` distinguishes `DIAPHRAGM`, `BODY`, `EQUAL`, `WELD`, etc. The constraint axis (`"Z"`, `"X"`, …) lives in `constraint_data["Axis"]`. |
| `md.constraint_assignments` | `dict[str, str]` | `{joint_id → constraint_name}` — which joints belong to which constraint. |

These are pure data — faithful to the `.s2k` file with no interpretation.
The S2K parser populates them from the
`CONSTRAINT DEFINITIONS - <TYPE>` and `JOINT CONSTRAINT ASSIGNMENTS`
tables.

**The rule for new code:** read constraint data from these structured
attributes, never from the parser's raw `_raw_tables` dict.

## 2. Storey Identification (`model/stories.py`)

`identify_stories(md, raw_tables=None, method="auto", z_tolerance=0.5)`
tries four strategies in priority order:

| # | Strategy | Source | Confidence |
|---|---|---|---|
| 1 | `s2k_table` | `STORY DATA` / `STORY` table (requires `raw_tables`) | high |
| 2 | `diaphragm` | `md.constraints` / `md.constraint_assignments` (Z-axis `DIAPHRAGM` only) | high |
| 3 | `area_elements` | Nearly-horizontal floor/roof slabs | medium |
| 4 | `node_clustering` | Fallback Z-clustering for bare frame models | low |

### Diaphragm strategy reads from `SAPModelData`

`_try_diaphragms(md)` filters `md.constraints` for
`constraint_type == "DIAPHRAGM"` **and** `constraint_data["Axis"] == "Z"`.
Joints sharing a Z-axis diaphragm constraint are grouped; each group's
storey elevation is the mean Z of its joints.

**Non-diaphragm constraints** (`BODY`, `EQUAL`, `WELD`, …) are simply
skipped by the type filter — they never contribute storey levels.

> **Historical note:** an earlier version read `raw_tables` directly
> (`CONSTRAINT DEFINITIONS - DIAPHRAGM`). It now reads the structured
> `SAPModelData` attributes, so a caller does not need the parser's
> internal tables. Only the `s2k_table` strategy still requires
> `raw_tables`.

## 3. Diaphragm Level Detection (Preprocessor)

`Preprocessor._detect_diaphragm_levels(md)` produces two outputs stored
on the `MeshModel`:

* `diaphragm_levels: list[float]` — sorted Z elevations with horizontal
  diaphragm behaviour.
* `diaphragm_components: list[(mean_z, [node_id, ...])]` — one component
  per **explicit** Z-axis DIAPHRAGM constraint, preserving the S2K
  constraint grouping.  Independent diaphragms at the same elevation
  (e.g. two building wings separated by a seismic gap) stay separate.

Four sources feed the method, selected by the `rigid_diaphragms` config:

| Source | Condition | Behaviour |
|---|---|---|
| Explicit groups | `rigid_diaphragms: [{name, nodes\|selection}, ...]` | Bypass all detection; one component per group |
| S2K joint constraints | `rigid_diaphragms` absent or not `True` | Parse `md.constraints`/`md.constraint_assignments` |
| Horizontal area elements | No components found | Each nearly-horizontal slab's mean Z |
| Forced storey detection | `rigid_diaphragms: True` | Call `identify_stories(md)`; one component per storey |

## 4. Diaphragm Application (AnalysisBuilder)

`AnalysisBuilder._apply_rigid_diaphragms()` is called during
`build_domain()` and `create_loads()`.  It emits one `ops.rigidDiaphragm`
per component, picking the **centroid node** of each group as the master
and the remaining nodes as slaves.

### 4.1 Per-group path (preserves S2K identity)

When the Preprocessor recorded `diaphragm_components` (explicit S2K
constraints, explicit named groups, or forced storey detection), each
component becomes one `rigidDiaphragm`.  This is the recommended path —
it preserves the S2K constraint grouping so independent diaphragms at
the same elevation are **not** merged.

### 4.2 Per-elevation fallback (merges by Z)

When no explicit components exist (area-only fallback) or when a legacy
`rigid_diaphragms: [z1, z2, ...]` list overrides the levels, all nodes
near each detected elevation are merged into a single diaphragm.
Nodes are matched with a Z tolerance — see §5.

### 4.3 `rigid_diaphragms` tri-state config

| Value | Effect |
|---|---|
| *absent* | Apply constraints detected from the S2K file / area elements |
| `False` | Explicitly disable all rigid diaphragms, even when levels are detected |
| `[z1, z2, ...]` | Legacy explicit Z-list override — forces per-elevation merging |
| `[{name, nodes\|selection}, ...]` | Explicit named groups — one `rigidDiaphragm` per component |

> **Warning:** using the legacy `[z1, z2, ...]` list when the model has
> per-group components silently merges independent diaphragms at the
> same elevation.  The builder now logs a warning in this situation and
> recommends explicit group dicts instead.

## 5. Z-Tolerances

Diaphragm detection uses **two independent** config keys, both
unit-consistent:

| Config key | Default | Location | Meaning |
|---|---|---|---|
| `area_diaphragm_z_tolerance` | `0.5` | Preprocessor source 3 (area elements) | Max Z-span for an area element to count as horizontal |
| `diaphragm_z_tolerance` | `0.01` | AnalysisBuilder per-elevation matching | Max absolute difference between node_z and the elevation for a node to join a diaphragm |

The Preprocessor consumes `area_diaphragm_z_tolerance` when classifying
horizontal area elements; the resulting tolerance is stored on
`MeshModel.diaphragm_z_tolerance` (default `0.01`) for the
AnalysisBuilder's per-elevation matching.  The two keys are independent
and can be set separately in the config dict.  Because models are built
in the same length units as the `.s2k` input, both tolerances are
unit-agnostic — they scale with the model's unit system.

## 6. Error Handling

If a node referenced by a diaphragm component does not exist in the
OpenSees domain, `_apply_rigid_diaphragms` raises a `RuntimeError`
identifying the node ID, tag, and component Z.  This surfaces
preprocessing mistakes (e.g. a node removed as an orphan that should
have been retained) instead of silently building a reduced diaphragm.

## 7. Interplay with Edge Constraints

Rigid diaphragms and edge constraints (see `constraint_detection.md`)
are distinct mechanisms but share the same OpenSees constraint handler
selection:

* `rigidDiaphragm` constraints require the `Transformation` or `Penalty`
  handler.
* Spring-element edge constraints (`constraint_method: "spring"`, default)
  create physical `twoNodeLink` elements and work with any handler.
* Penalty-MPC edge constraints (`constraint_method: "penalty"`) require
  the `Penalty` handler — the builder auto-selects it.

## 8. Usage Example

```python
from fea_toolkit.io.s2k_parser import SAP2000Parser
from fea_toolkit.opensees.preprocessor import preprocess_model
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
from fea_toolkit.model.stories import identify_stories

parser = SAP2000Parser("admin.s2k")
parser.parse()
md = parser.get_model_data()

# Storey inspection (no raw_tables needed for diaphragm strategy)
stories = identify_stories(md)
print([s.name for s in stories])

# Preprocess with a configurable diaphragm tolerance
config = {
    "rigid_diaphragms": True,              # force storey-based detection
    "diaphragm_z_tolerance": 0.05,         # unit-consistent Z matching
}
mm = preprocess_model(md, config)
print(mm.diaphragm_levels)
print(mm.diaphragm_components)

# Analysis — rigidDiaphragm constraints applied automatically
builder = AnalysisBuilder(mm, config)
builder.build_domain()
```

## 9. Related

* `docs/storey_response.md` — storey displacement/drift/shear post-processing
* `docs/constraint_detection.md` — edge constraint tears and spring/penalty MPCs
* `docs/shell_support.md` — shell element types and layered sections
* `docs/workflow.md` — the end-to-end two-stage analysis pipeline