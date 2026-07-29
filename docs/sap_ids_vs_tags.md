# SAP IDs vs OpenSees Tags — Identifier Systems

## Overview

The fea_toolkit pipeline crosses two distinct identifier systems. Understanding
the difference is essential for writing correct code and debugging visualisation
issues such as "shells not appearing" — which was caused by conflating the two
systems in the NPZ visualisation path.

## The Two Systems

| Aspect | SAP ID (string) | Node Tag / OpenSees Tag (int) |
|--------|-----------------|-------------------------------|
| **Origin** | SAP2000 model database (`.s2k` file) | Assigned by the Preprocessor / OpenSees domain builder |
| **Type** | `str` — e.g. `"158"`, `"area-1_sub_0_node_1"`, `"FRAME-45"` | `int` — e.g. `999`, `1001`, `2034` |
| **Uniqueness** | Unique within the parser output (`SAPModelData`) | Unique within the OpenSees domain and `MeshModel` |
| **Used in OpenSees?** | Never. OpenSees only accepts integer tags. | `ops.node(tag, x, y, z)`, `ops.element(tag, ...)` |
| **Used in SAP2000?** | Native SAP2000 identifier. | Never. SAP2000 doesn't know about OpenSees. |
| **Analysis results** | Mapped via node_tag arrays in NPZ output. | Key for displacement/force dicts. |
| **NPZ storage** | `node_sap_id`, `frame_sap_id`, `shell_sap_id` | `node_tag`, `frame_node_i`, `shell_node_1..4` |

## Who Creates Each

```
SAP2000 .s2k file
       │
       ▼
   SAP2000Parser  ──────────────────────────────────────────►  SAPModelData
   (io/s2k_parser.py)                                        All keys are SAP IDs (strings)
                                                              e.g. nodes["158"], area_elements["area-1"]
       │
       ▼
   Preprocessor  ────────────────────────────────────────────►  MeshModel
   (opensees/preprocessor.py)                                 New node SAP IDs for mesh-created
                                                              nodes embedded in the ID string:
                                                              "area-1_sub_0_node_1"
                                                              Each node also has node_tag (int).
       │
       ▼
   AnalysisBuilder  ──────────────────────────────────────────►  OpenSees domain
   (opensees/analysis_builder.py)                              Tags are the only identifiers used.
                                                               ops.node(tag, x, y, z)
       │
       ▼
   NPZ Export  ───────────────────────────────────────────────►  .npz file
   (io/npz_writer.py)                                          Both systems stored:
                                                                - node_sap_id = "area-1_sub_0_node_1"
                                                                - node_tag = 999
                                                                - shell_node_1..4 = 999 (tags!)
```

## How the NPZ Uses Each

The NPZ file stores **both** identifiers for every node:

| NPZ Array | Content | Example |
|-----------|---------|---------|
| `node_sap_id` | SAP ID (string) | `"158"`, `"area-1_sub_0_node_1"` |
| `node_tag` | Node tag (int) | `999`, `1001` |
| `frame_node_i` / `frame_node_j` | Frame endpoint tags (int) | `999` |
| `shell_node_1` .. `shell_node_4` | Shell vertex tags (int) | `1001` |

**Dual-keyed access**: Since the `_resolve_mesh_data()` fix, the node dict
stores **both** identifiers — nodes are keyed by both the SAP-ID string
**and** the integer tag:

```python
node_entry = {"tag": 999, "x": 0.0, "y": 0.0, "z": 3.0}
data["nodes"]["area-1_sub_0_node_1"] = node_entry
data["nodes"][999] = node_entry          # same entry, int key
```

This means both lookup paths immediately succeed:

* ``data["nodes"].get("area-1_sub_0_node_1")`` → the node dict
* ``data["nodes"].get(999)`` → the **same** node dict

Frame endpoints (``frame_node_i`` / ``frame_node_j``) are resolved by
``_resolve_frame_node()``, which tries ``ni_id``/``nj_id`` (string key)
first, then falls back to ``ni_tag``/``nj_tag`` (int key).  With the
dual-keyed dict the tag-based fallback now succeeds immediately.

Shell vertices (``shell_node_1..4``) are resolved by
``_resolve_shell_node()``, which tries the string key first, then int
conversion, then a tag-value scan.  With the dual-keyed dict the
tag-based access succeeds on the first attempt for valid nodes.

**Important**: When iterating ``data["nodes"]`` for rendering (point
clouds, markers, labels), callers must deduplicate by the ``tag`` field
to avoid rendering the same physical node twice (once via its SAP-ID
key and once via its integer-tag key).  Functions like ``_render_scene``
and ``plot_deformed_displacement_3d`` now perform this deduplication.

## The Naming Convention

| Context | Suffix | Example |
|---------|--------|---------|
| Variable or dict key | `*_id` means SAP ID | `elem.node_i`, `fr.get("ni_id")` |
| Variable or dict key | `*_tag` means OpenSees tag | `nd.node_tag`, `fr.get("ni_tag")` |
| NPZ array | `*_sap_id` means SAP ID | `frame_sap_id`, `shell_sap_id` |
| NPZ array | `*_tag` or bare `tag` means tag | `node_tag`, `frame_node_i` |

## Rule

> **Never use SAP IDs directly in OpenSees commands — always map through tags.**

And conversely, when looking up a node by an integer from an NPZ array
(such as `shell_node_1`), **search by tag value**, not by dict key.