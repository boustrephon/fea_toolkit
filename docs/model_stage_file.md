---
title: "Model Stage File"
description: "Self-describing NPZ/HDF5 export of SAPModelData + MeshModel stages, with lossless round-trip and Rhino/analysis consumption."
status: "complete"
tags: [io, serialisation, rhino, round-trip, hdf5, npz]
category: [io, export-viz]
related: [results_schema.md, rhino_export.md, workflow.md]
---

# Model Stage File

A single `.h5` or `.npz` file that carries **both** pipeline stages
(raw SAP model **and** post-preprocessor mesh) together with their
full attribute dictionaries — a self-describing, archival, round-trippable
deliverable.

```python
from fea_toolkit.io import write_model_stages, read_model_stages

write_model_stages("model.h5",
                   sap=md, mesh=mesh, config=config, fmt="h5")

mesh2, config2 = read_model_stages("model.h5", "mesh", return_config=True)
assert mesh2 == mesh                     # lossless dataclass equality
AnalysisBuilder(mesh2, config2)          # re-analysable
```

## Why

* **Round-trip guarantee** — every one of the 40 `MeshModel` / 26
  `SAPModelData` fields is serialised (see §Codec below).  `==` equality
  is asserted by tests, and the tag maps (`frame_tag_map`,
  `material_tags`, …) round-trip exactly so **existing result arrays
  remain valid** against the re-read model.
* **Self-describing** — section shape + dimensions, materials, groups,
  element types/properties, nD materials, layered shell sections, units,
  config and provenance are all in the file.  A consumer needs nothing
  else to interpret it.
* **Two access levels** — a full codec payload for lossless round-trips,
  and lightweight geometry arrays (incl. ragged shell connectivity) for
  fast Rhino / PyVista consumption without decoding the model.
* **Rhino 8 ready** — the `io` package and the model layer never import
  `openseespy`, and `fea_toolkit` / `fea_toolkit.opensees` are now
  lazy-importing (PEP 562).  Inside Rhino 8 (CPython 3.9 — where
  openseespy cannot install), only `numpy` + `h5py==3.13.0` are needed.

## File layout

```
/  attrs/datasets: schema_version (int), metadata_json
/meta               provenance: created, toolkit_version, source_file,
                    model_name, force_unit, length_unit, units,
                    forces_coordinate_system, config, stages {...}

/stage/sap/
    model_json               full codec payload → SAPModelData
    sections_json            {name: {type, shape_id, ...dims}}
    materials_json  groups_json  restraints_json  units_json
    frame_element_types_json  area_element_types_json
    frame_element_properties_json  area_element_properties_json
    nd_materials_json  layered_shell_sections_json  model_name
    node_tag/node_sap_id/node_x/y/z  frame_*  shell_* (geometry arrays)
/stage/mesh/
    model_json               full codec payload → MeshModel
    ... (same blocks; shell_node_ids_flat + shell_node_offsets = ragged)
```

NPZ and HDF5 carry **identical key names**; `read_results()` and the
stage reader expose one flat, format-independent view.

## Public API (`fea_toolkit.io`)

| Function | Purpose |
|---|---|
| `write_model_stages(path, sap=None, mesh=None, config=None, static_results=None, modal_result=None, fmt="npz", geometry=True, dictionaries=True, model_json=True, ...)` | Write both stages + optional results. |
| `read_model_stages(path, stage, cls=None, return_config=False)` | Lossless round-trip → `SAPModelData` / `MeshModel` (+ config). |
| `read_stage_arrays(path, stage)` | Geometry arrays only (fast path). |
| `read_dictionary_arrays(path, stage)` | Decoded dictionary blocks. |
| `read_metadata(path)` | Provenance / config / units. |
| `get_schema_version(data)` | 2 for stage files; 1 for legacy files. |

## The codec (`fea_toolkit.io.model_codec`)

* **Introspection-driven** — `dataclasses.fields()` + `get_type_hints()`
  drive both directions; new fields serialise automatically.  The guard
  test (`check_round_trip_types()`) fails loudly if a new field's type
  has no codec rule.
* **Polymorphism** — `Section` and its 15 subclasses are tagged with a
  `__type__` discriminator and dispatched through the registry.
* **Explicit rules** — `MeshModel.detected_edge_pairs`,
  `offset_rigid_links`, `edge_constraint_args`, `diaphragm_components`
  are re-tupled on decode (JSON destroys tuples); `loads_only_area_ids`
  (set) is written sorted and re-set; `edge_loads_from_areas` (bare
  `list` annotation) is typed as `FrameDistributedLoad`.
* **Deterministic** — `sort_keys=True` → identical models yield
  byte-identical JSON (files can be diffed).
* **Forward-compatible** — unknown payload keys are ignored; missing
  fields fall back to dataclass defaults.

## Consuming in Rhino

```python
from fea_toolkit.rhino import RhinoImporter

# From a stage file — no toolkit model objects, no openseespy:
RhinoImporter("model.h5", stage="sap").run()
RhinoImporter("model.h5", stage="mesh").run()
```

`RhinoImporter` accepts `SAPModelData` / `MeshModel` / `AnalysisBuilder`
/ stage-file path / stage-file dict via
`fea_toolkit.model.source_resolver.resolve_model_source`, which filters
`inactive` (split/meshed parent) elements — the imported mesh view now
matches what is actually analysed.  Geometry objects carry `FEA_Stage`,
`FEA_Kind`, `FEA_NodeTag` / `FEA_ElemTag` and `FEA_ParentID` attributes
in addition to the existing `SAP_*` keys, so result colouring can filter
by stage.

## Performance notes

* HDF5 (`fmt="h5"`) is the recommended transport when results are large;
  `h5py==3.13.0` is the last release with CPython 3.9 macOS arm64 wheels
  (Rhino 8 runtime).
* `model_json=False` drops the codec payload for huge models — geometry
  arrays remain available, but the lossless round-trip is disabled.
