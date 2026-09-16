---
title: "SAP2000 Parser — Table & Keyword Coverage"
description: "Table-level coverage registry (handled / known-gap / ignored / unhandled) with runtime detection for unhandled SAP2000 tables, plus the keyword register of parsed-but-unconsumed fields (e.g. transform_stiffness, rigid_factor)."
status: "partial"
tags: [parser, sap2000, data-model, traceability, internal]
category: [model-features]
related: [workflow.md, model_review.md, _pending_work.md]
---
# SAP2000 Parser — Table & Keyword Coverage

## Purpose

This document answers a specific question: **which `.s2k` / `.json` tables
and keywords does the parser read and store, but which no downstream
consumer actually uses?**

That matters because a parsed-but-unconsumed keyword is easy to mistake for
"implemented".  The value round-trips through `SAPModelData`, is written to
the stage file, appears in tests, and therefore *looks* authoritative —
while the analysis and/or the Rhino export silently ignore it.  Keeping the
gap in one explicit place stops it being an implied, invisible one.

The complementary mapping — *what the parser reads* and which dataclass each
raw table populates — lives in
[`workflow.md`](workflow.md#what-get_model_data-does).  **This page
covers the other half: whether the parsed value is consumed.**

## Method (reproducible)

"Consumed" here means **referenced by name outside the two files that define
the data**: `model/sap_data.py` (declares the fields) and
`io/s2k_parser.py` (populates them).  The audit is:

1. Import every `@dataclass` in `fea_toolkit.model.sap_data`.
2. For each field name, regex-search `\b<field>\b` across
   `src/fea_toolkit/**/*.py`, excluding `model/sap_data.py` and
   `io/s2k_parser.py`.
3. Report every field with **zero** hits.

**Caveat — "zero hits" means *no semantic consumer*.**  Every field still
round-trips through the introspection-driven serialisers
(`io/model_codec.py`, `io/stage_writer.py`) and the unit scaler
(`model/units.py`), all of which walk `dataclasses.fields()`.  A field
appearing in a stage file is therefore **not** evidence that anything uses
it.  Conversely, consumption via a string key or `getattr(obj, "name")` will
be missed by this scan — the register below was spot-checked against the
code rather than trusted blindly.

## Table coverage — which tables are handled

The *table*-level counterpart of the keyword register below is machine-readable.
:mod:`fea_toolkit.io.table_registry` classifies every table in a parsed model
into one of four buckets:

| Bucket | Meaning |
|---|---|
| **handled** | the toolkit consumes it |
| **known-gap** | recognised as structurally relevant, not yet parsed (a *tracked* hole) |
| **ignored** | deliberately skipped (design preferences, output stations, display options, bookkeeping) — not a hole |
| **unhandled** | **unrecognised** — SAP2000 added a table, or the toolkit has never seen it |

### Runtime detection

Three surfaces, all backed by the same registry:

**1. Parser warning** — opt-in, at the point of parsing:

```python
from fea_toolkit.io.s2k_parser import SAP2000Parser

parser = SAP2000Parser("model.s2k").parse(warn_unhandled=True)
# WARNING:fea_toolkit.io.s2k_parser:SAP2000 table 'NEW TABLE' (3 rows) is not
# recognised by fea_toolkit — it may be a table SAP2000 has newly introduced...
```

**2. Review report** — included whenever the review runs on a file, with
`--tables` to expand it and list the ignored bucket too:

```bash
python -m fea_toolkit.model.review model.s2k --tables
```

**3. Standalone command** — the cheapest check (parse + diff + print), usable
as a CI gate because it exits `1` when unrecognised tables are present:

```bash
python -m fea_toolkit.io.table_registry model.s2k          # or: fea-tables model.s2k
python -m fea_toolkit.io.table_registry model.s2k --json   # machine-readable
```

### Drift guards

`tests/test_table_registry.py` enforces both directions:

* every table name the parser reads (extracted from `s2k_parser.py` by AST) must
  be registered — the *same* class of bug that let the parser read the cardinal
  point from the wrong table for several releases;
* every table present in the committed fixtures must be known — so a table the
  toolkit has never seen cannot creep into a fixture unnoticed.

### Registry

<!-- BEGIN GENERATED: registry -->

*Generated from `src/fea_toolkit/io/table_registry.py` — do not edit by hand.  Regenerate with `python docs/_generate_parser_tables.py`.*

### Handled (43 exact names)

- `AREA AUTO MESH ASSIGNMENTS`
- `AREA EDGE CONSTRAINT ASSIGNMENTS`
- `AREA LOADS - GRAVITY`
- `AREA LOADS - UNIFORM`
- `AREA LOADS - UNIFORM TO FRAME`
- `AREA MESH ASSIGNMENTS`
- `AREA SECTION ASSIGNMENTS`
- `AREA SECTION PROPERTIES`
- `AREA SECTION PROPERTY DESIGN PARAMETERS`
- `CONNECTIVITY - AREA`
- `CONNECTIVITY - FRAME`
- `FRAME AUTO MESH ASSIGNMENTS`
- `FRAME END LENGTH OFFSETS`
- `FRAME END OFFSET ASSIGNMENTS`
- `FRAME INSERTION POINT ASSIGNMENTS`
- `FRAME LOADS - DISTRIBUTED`
- `FRAME LOADS - GRAVITY`
- `FRAME LOADS - OPEN STRUCTURE WIND`
- `FRAME LOCAL AXES ASSIGNMENTS 1 - TYPICAL`
- `FRAME OFFSET ALONG LENGTH ASSIGNMENTS`
- `FRAME RELEASE ASSIGNMENTS`
- `FRAME RELEASE ASSIGNMENTS 1 - GENERAL`
- `FRAME RELEASE ASSIGNMENTS 2 - PARTIAL FIXITY`
- `FRAME RELEASES`
- `FRAME SECTION ASSIGNMENTS`
- `FRAME SECTION PROPERTIES 01 - GENERAL`
- `FRAME SECTION PROPERTIES 02 - CONCRETE COLUMN`
- `FRAME SECTION PROPERTIES 03 - CONCRETE BEAM`
- `GROUPS 1 - DEFINITIONS`
- `GROUPS 2 - ASSIGNMENTS`
- `JOINT CONSTRAINT ASSIGNMENTS`
- `JOINT COORDINATES`
- `JOINT LOADS - FORCE`
- `JOINT RESTRAINT ASSIGNMENTS`
- `LOAD CASE DEFINITIONS`
- `LOAD PATTERN DEFINITIONS`
- `MASS SOURCE`
- `MASSES 1 - MASS SOURCE`
- `MATERIAL PROPERTIES 01 - GENERAL`
- `PROGRAM CONTROL`
- `REBAR SIZES`
- `STORY`
- `STORY DATA`

### Handled prefix families

- `MATERIAL PROPERTIES*`
- `CONSTRAINT DEFINITIONS - *`
- `AREA LOADS - *`
- `CASE -*`
- `AUTO*`

### Known gaps (3 exact names)

- `COMBINATION DEFINITIONS` — load combinations not parsed — see _pending_work.md P12
- `JOINT PATTERN DEFINITIONS` — joint patterns (thickness / offset overwrites) not consumed
- `SOLID PROPERTY DEFINITIONS` — solid (brick) elements not supported

### Known-gap prefix families

- `SECTION DESIGNER PROPERTIES*` — SD section geometry not parsed

### Ignored (11 exact names)

- `ACTIVE DEGREES OF FREEDOM` — analysis DOF configuration
- `ANALYSIS OPTIONS` — solver options
- `AREA SECTION PROPERTY - TIME DEPENDENT` — creep / shrinkage (area sections)
- `COORDINATE SYSTEMS` — named coordinate systems (only GLOBAL is used)
- `FRAME DESIGN PROCEDURES` — design configuration
- `FRAME LOAD TRANSFER OPTIONS` — load-transfer settings
- `FRAME OUTPUT STATION ASSIGNMENTS` — output station locations
- `FRAME SECTION PROPERTIES 13 - TIME DEPENDENT` — creep / shrinkage
- `GRID LINES` — grid geometry
- `METADATA` — parser-generated for JSON round-trip; never read back
- `PROJECT INFORMATION` — project metadata

### Ignored prefix families

- `PREFERENCES - *` — design-code preference values
- `OVERWRITES - *` — design-code overwrite values
- `OPTIONS - *` — display / output options
- `FUNCTION - *` — SAP-side function definitions (the toolkit builds its own)
- `DATABASE *` — export bookkeeping (documentation / format types)

<!-- END GENERATED: registry -->

## Register — parsed but not yet consumed

| Item | Source table / column | Populated field(s) | Why it matters |
|---|---|---|---|
| Insertion-point mirror flags | `FRAME INSERTION POINT ASSIGNMENTS` — `Mirror2`, `Mirror3` | `FrameElement.mirror_2`, `FrameElement.mirror_3` | Section mirroring is not modelled; the extrusion is never mirrored, so an asymmetric section mirrored in SAP2000 is drawn the wrong way round. |
| Stiffness transformation flag | `FRAME INSERTION POINT ASSIGNMENTS` — `Transform` | `FrameElement.transform_stiffness` | SAP2000's *"do not transform frame stiffness for offsets from centroid"*, inverted. The builder always constructs the member at the reference line; the 6×6 transform for the centroid offset is not applied. |
| Rigid-zone factor | `FRAME END OFFSET ASSIGNMENTS` — `RigidFactor` (also `FRAME END LENGTH OFFSETS` / `FRAME OFFSET ALONG LENGTH ASSIGNMENTS`) | `FrameEndOffset.rigid_factor` | The builder treats every end offset as **fully rigid** (stiff links / `rigid_link_mpc`). Partial rigidity (`0 < RigidFactor < 1`) is not applied. |
| Area edge constraints | `AREA EDGE CONSTRAINT ASSIGNMENTS` | `SAPModelData.area_edge_constraints` | Parsed and stored, but nothing reads it: the preprocessor derives its own edge pairs via `find_constraint_edges()` instead. |
| Area auto-mesh flags | `AREA AUTO MESH ASSIGNMENTS` — `NoAutoMeshAtEdges`, `NoSubMesh`, `MinSize` | `AreaMesh.no_auto_mesh_at_edges`, `AreaMesh.no_sub_mesh`, `AreaMesh.min_size` | `AreaMesh.auto_mesh` and `AreaMesh.max_size` **are** consumed (`mesh/remesh.py`); the other three are not. |
| Encased-section data | `FRAME SECTION PROPERTIES 01 - GENERAL` | `EncasedSection.embedded_section`, `.encasement_material`, `.encasement_depth`, `.encasement_bf` | Encased composite sections are not modelled. |
| Circular concrete bar count | `FRAME SECTION PROPERTIES 02 - CONCRETE COLUMN` / `03 - CONCRETE BEAM` | `ConcreteCircularSection.bar_count` | Parsed; circular-column reinforcement is not yet built. |
| Double-angle spacing | `FRAME SECTION PROPERTIES 01 - GENERAL` — `DIS` | `DoubleAngleSection.dis` | Parsed; the double-angle fibre mesh is deferred (`_pending_work.md` P6b). |
| Stress-strain curve options | `MATERIAL PROPERTIES 03x` | `StressStrainCurve.ss_curve_opt`, `.ss_hys_type`, `.s_hard`, `.s_max`, `.s_rup`, `.final_slope`, `.coup_mod_type`, `.d_angle`, `.use_ct_def` | Only a subset of the curve parameters (`s_fc`, `s_cap`, `f_angle`) drive the OpenSees uniaxial materials. |
| Coordinate-system geometry | (model layer, not parser-populated) | `CoordSys.coord_type`, `.xx`, `.yy`, `.zz` | Only `default_coord_sys` is used. |
| Load-combination container | (parser reads nothing — see `_pending_work.md` P12) | `LoadCombination.combo_type` (+ the whole dataclass) | The `LOAD COMBINATIONS` table is not parsed at all, so the dataclass is unreachable from a real model. |
| Load-case design metadata | `LOAD CASE DEFINITIONS` | `LoadCase.design_type_option`, `.design_action_option`, `.initial_condition`, `.modal_case`, `.run_case` | Parsed; only the case name/type drive the analysis runners. |

## Consumed only *inside* the parser

These are flagged by the audit as "no external consumer" but are in fact
used — the consumer is `io/s2k_parser.py` itself, which is excluded from the
scan.  They are listed so the register is not misread.

| Item | Source table / column | Where it is consumed |
|---|---|---|
| Section centroid offset | `FRAME SECTION PROPERTIES 01 - GENERAL` — `CGOffset2`, `CGOffset3` | `SAP2000Parser._merge_cardinal_into_offsets()` → lateral `FrameEndOffset.off_y_i/z_i/j/z_j` for cardinal point 10 (centroid).  The resulting lateral offsets are then consumed by the Rhino export (`rhino/geometry.py`). |
| Shear-centre eccentricity | `FRAME SECTION PROPERTIES 01 - GENERAL` — `EccV2`, `EccV3` | Same path, for cardinal point 11 (shear centre).  **Effectively dormant in practice** because CP 11 is rare — every model inspected so far uses CP 2, 8 or 10. |

## Consumed fields reached indirectly

Some frame-offset data is consumed by the Rhino export only, never by the
analysis builder.  This is expected (the offsets are a *visual* concern —
the analytical line is built at the node-to-node reference line) but worth
stating explicitly, because a reader may assume the analysis uses them:

| Field | Consumer |
|---|---|
| `FrameElement.cardinal_point` | `io/_serial.py`, `model/review.py`, `model/source_resolver.py` (reporting / round-trip / mesh conversion) |
| `FrameEndOffset.off_y_i`, `off_z_i`, `off_y_j`, `off_z_j` | `rhino/geometry.py` only |
| `FrameEndOffset.end_i`, `end_j` | `opensees/preprocessor.py` (rigid links) |

## Keeping this current

This register is a snapshot, not a generated artifact.  Re-run the audit
whenever a parser field is added or a consumer is wired up:

```python
# /tmp/audit_consumption.py (see "Method" above)
import dataclasses, pathlib, re, sys
sys.path.insert(0, "src")
from fea_toolkit.model import sap_data

SRC = pathlib.Path("src/fea_toolkit")
sources = {p: p.read_text() for p in SRC.rglob("*.py")
           if p.name not in {"sap_data.py", "s2k_parser.py"}}
for name, cls in vars(sap_data).items():
    if not (isinstance(cls, type) and dataclasses.is_dataclass(cls)):
        continue
    if cls.__module__ != sap_data.__name__:
        continue
    for f in dataclasses.fields(cls):
        pat = re.compile(r"\b" + re.escape(f.name) + r"\b")
        if not any(pat.search(t) for t in sources.values()):
            print(f"{name}.{f.name}")
```

Follow-up work items are tracked in
[`_pending_work.md`](_pending_work.md) (see **P16**).  When an item here is
wired up, remove its row and move the corresponding P16 bullet to the DONE
register.

