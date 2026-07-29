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

**Critical point**: Shell connectivity arrays (`shell_node_1..4`) store
**integer tags**, not SAP IDs.  The node coordinate dict in the mesh data
is indexed by **string SAP ID**.  This creates a lookup mismatch that must
be resolved by searching through node tag values.

## Why This Bugged (the Shell Visualisation Fix)

The `_resolve_mesh_data()` function in `plotting/viz.py` builds a node dict
keyed by SAP ID:

```python
data["nodes"]["area-1_sub_0_node_1"] = {"tag": 999, "x": 0.0, "y": 0.0, "z": 3.0}
```

When the NPZ shell arrays contain `shell_node_1 = 999`, a direct lookup
`data["nodes"].get(999)` returns `None` — the key is `"area-1_sub_0_node_1"`,
not `999`.

Frame elements avoid this because `_resolve_frame_node()` has a tag-search
fallback.  Shell elements had no such fallback — hence the fix:
`_resolve_shell_node()`.

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