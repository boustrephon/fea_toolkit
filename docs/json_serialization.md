---
title: "JSON Serialization: Raw Tables vs Model Codec"
description: "The two independent JSON representations in fea_toolkit — the parser raw-table cache and the dataclass model codec — and when to use each."
status: "stable"
tags: [json, io, serialization, parser, model-codec]
category: [export-viz]
related: [model_stage_file.md, results_schema.md, dev_notes.md, llm_guide.md]
---
# JSON Serialization: Raw Tables vs Model Codec

`fea_toolkit` has **two independent JSON representations**, plus a third
(NPZ) for *analysis results*. Both are "JSON", but they serialise
different things through different entry points, and they are easy to
confuse.

## Scope

The parser reads **SAP2000 text exports** — `.s2k`, and the `.$2k` variant
of the same text format. **ETABS `.e2k` / `.$et` parsing is not implemented
yet**; those extensions are offered by the CLI help and file dialogs as the
intended target, but no ETABS-specific handling exists in the parser.

## 1. Parser raw-table cache — `SAP2000Parser.to_json()` / `from_json()`

**What it is** — the parsed SAP2000 tables as a plain
`dict[str, list[dict[str, int | float | str | bool | None]]]`
(`SAP2000Parser._raw_tables`). Every cell is already coerced to a
JSON-native scalar, so the round-trip is lossless.

**Purpose** — cache the *parse output* so a model can be rebuilt without
re-parsing the SAP2000 text file.

```python
from fea_toolkit.io.s2k_parser import SAP2000Parser

parser = SAP2000Parser("model.s2k").parse()
parser.to_json("model.json")              # writes the raw tables

parser2 = SAP2000Parser.from_json("model.json")
md = parser2.get_model_data()             # SAPModelData — same as from parse()
```

**Notes**

- The `.json` file holds the **raw tables, not `SAPModelData`**. The
  "enhanced" model is always derived by `get_model_data()` at load time,
  identically whether the tables came from `parse()` (text) or
  `from_json()` (JSON). Because the tables are the only input,
  `get_model_data()` is deterministic and gives the same result from
  either source.
- `from_json()` is the *only* correct way to read one of these files.
  `SAP2000Parser("model.json").parse()` treats the JSON as a text table
  export, finds no `TABLE: "…"` lines, and returns an empty model
  **silently** — it does not raise.
- `to_json()` applies `default=str` as a defensive fallback; in practice
  every parsed value is already JSON-native, so it never triggers.

## 2. Model codec — `fea_toolkit.io.model_codec`

**What it is** — serialises the **dataclass model objects themselves**
(`SAPModelData` and `MeshModel`), including every nested dataclass and the
polymorphic `Section` hierarchy, tagged with a `__type__` discriminator so
reconstruction dispatches to the correct subclass.

**Purpose** — deterministic, JSON-safe snapshots of a fully-built model:
stage files, model diffing, and hand-off between processes. Safe to import
inside Rhino 8 (imports nothing from `opensees`).

```python
from fea_toolkit.io.model_codec import model_to_json, json_to_model
from fea_toolkit.model.sap_data import SAPModelData

payload = model_to_json(md)                          # compact, sort_keys=True
clone = json_to_model(payload, cls=SAPModelData)     # field-for-field equal
```

**Notes**

- `model_to_dict()` / `dict_to_model()` are the dict-level equivalents;
  `model_to_json()` / `json_to_model()` wrap them with `json.dumps` /
  `json.loads`.
- See [`model_stage_file.md`](model_stage_file.md) for the codec design and
  the stage-file format. The payload carries `MODEL_SCHEMA_VERSION`
  (`model_codec.py`, currently `1`).
- `check_round_trip_types()` guards against silently lossy round-trips: it
  reports any `SAPModelData` / `MeshModel` field whose type has no codec
  rule, and the corresponding test asserts the list is empty.

## Which one do I want?

| Need | Use | Serialises |
|---|---|---|
| Rebuild a model without re-parsing `.s2k` | `parser.to_json()` / `parser.from_json()` | raw tables |
| Snapshot / diff a built model object | `model_to_json()` / `json_to_model()` | `SAPModelData` / `MeshModel` |
| Archive analysis *results* | `write_results_npz()` — not JSON | NPZ, see [`results_schema.md`](results_schema.md) |

## Related

- [`results_schema.md`](results_schema.md) — the NPZ results schema, the
  third and non-JSON mechanism.
- [`model_stage_file.md`](model_stage_file.md) — model-codec design and
  stage-file layout.
- [`dev_notes.md`](dev_notes.md) — "Area import from JSON", for
  `from_json()` specifics on area elements.
- [`rhino_export.md`](rhino_export.md) — shows the correct
  `.json` → `from_json()` / else → text constructor branch.
