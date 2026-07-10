# `fea_toolkit.io` — Results Storage & Exchange

This subpackage defines a **unified NPZ schema** for storing structural
analysis results, plus readers and writers for PyVista, Rhino, and
opstool ODB.

## Files

| File | Purpose |
|---|---|
| `results_schema.py` | Array name constants, shape descriptors, `validate_npz()` |
| `npz_writer.py` | `write_results_npz()` — assemble geometry + results into `.npz` |
| `npz_reader.py` | `read_results_npz()` + PyVista/Rhino adapter helpers |
| `s2k_parser.py` | SAP2000 `.s2k` / JSON model parser |
| `report.py` | Tabular report formatting (modal table, linear results, etc.) |
| `helper.py` | Misc I/O utilities |
| `npz_parent_map.md` | Parent-child hierarchy docs |

## The NPZ Schema (`results.npz`)

A single compressed NumPy archive holding model geometry plus all
analysis results.  Format auto-detection allows both legacy
`*_forces.npz` files and the new unified format.

### Geometry arrays

| Array | Shape | dtype | Description |
|---|---|---|---|
| `node_tag` | `(N_node,)` | `int` | OpenSees node tag |
| `node_sap_id` | `(N_node,)` | `str` | SAP2000 node ID string |
| `node_x` / `y` / `z` | `(N_node,)` | `float` | Node coordinates |
| `frame_eid` | `(N_frame,)` | `int` | Frame element index (0‑based) |
| `frame_sap_id` | `(N_frame,)` | `str` | SAP2000 FrameID |
| `frame_parent_sap_id` | `(N_frame,)` | `str` | Parent SAP ID (empty if unsplit) |
| `frame_sec_name` | `(N_frame,)` | `str` | Section name |
| `frame_node_i` / `j` | `(N_frame,)` | `int` | I/J node tags |
| `shell_eid` | `(N_shell,)` | `int` | Shell element index |
| `shell_sap_id` | `(N_shell,)` | `str` | SAP2000 AreaID |
| `shell_node_1..4` | `(N_shell,)` | `int` | Corner node tags |

### Static results (per load case)

Keys follow the pattern `static/{case_name}/{array_name}`.

| Array | Shape | dtype | Description |
|---|---|---|---|
| `static/{case}/fx_i` | `(N_frame,)` | `float` | I‑end axial force |
| `static/{case}/fy_i` | `(N_frame,)` | `float` | I‑end shear Y |
| … | … | … | All 6 components × 2 ends |
| `static/{case}/fx_i_local` | `(N_frame,)` | `float` | Local‑coordinate variant |
| `static/{case}/node_dx` | `(N_node,)` | `float` | Nodal displacement X |
| `static_case_labels` | `(N_case,)` | `str` | e.g. `["DEAD", "SL_X"]` |

### Modal results

| Array | Shape | dtype | Description |
|---|---|---|---|
| `modal/period` | `(N_mode,)` | `float` | Natural period (s) |
| `modal/frequency` | `(N_mode,)` | `float` | Frequency (Hz) |
| `modal/mx_ratio` | `(N_mode,)` | `float` | X‑mass participation ratio |
| `modal/my_ratio` | `(N_mode,)` | `float` | Y‑mass participation ratio |
| `modal/mx_eff` | `(N_mode,)` | `float` | X effective modal mass (t) |
| `modal/mode_dx` | `(N_node, N_mode)` | `float` | Eigenvector X component |
| `modal/mode_dy` | `(N_node, N_mode)` | `float` | Eigenvector Y component |
| `modal/mode_dz` | `(N_node, N_mode)` | `float` | Eigenvector Z component |

### Metadata

| Array | Shape | dtype | Description |
|---|---|---|---|
| `analysis_types` | `(N_analysis,)` | `str` | e.g. `["static", "modal", "rs"]` |
| `force_unit` | `()` | `str` | `"kN"` |
| `length_unit` | `()` | `str` | `"m"` |
| `created` | `()` | `str` | ISO‑8601 timestamp |

## ID / Tag Mapping

| Concept | Type | Example | Stored in |
|---|---|---|---|
| SAP2000 Node ID | `str` | `"1"` | `node_sap_id` |
| OpenSees node tag | `int` | `1` | `node_tag` |
| SAP2000 Frame ID | `str` | `"1"` or `"1-0"` (split) | `frame_sap_id` |
| Parent SAP ID | `str` | `"1"` (empty = unsplit) | `frame_parent_sap_id` |

Use helpers from `npz_reader.py`:

```python
from fea_toolkit.io.npz_reader import (
    npz_build_id_tag_map,     # {"1": 1, "2": 2, ...}
    npz_build_child_map,      # {"1": ["1-0", "1-1"], ...}
    npz_build_parent_map,     # {"1-0": "1", "1-1": "1", ...}
)
```

## Pipeline

```
SAP2000 .s2k
    │
    ▼
SAP2000Parser ──→ SAPModelData
    │
    ├── OpenSeesBuilder ──→ analysis ──→ write_results_npz() ──→ results.npz
    │
    ├── RhinoImporter ──→ Rhino geometry (SAP_FrameID UserStrings)
    │
    └── read_results_npz() ──→ colour_from_npz() / plot_npz_moment_3d()
                                (matching by frame_sap_id or parent_id)
```

## Usage (end‑to‑end)

```python
from fea_toolkit.io.s2k_parser import SAP2000Parser
from fea_toolkit.opensees.builder import OpenSeesBuilder
from fea_toolkit.io.npz_writer import write_results_npz
from fea_toolkit.io.npz_reader import read_results_npz, npz_to_pyvista_frame_mesh

# 1. Parse
md = SAP2000Parser("model.s2k").parse().get_model_data()

# 2. Analyse
builder = OpenSeesBuilder(md, {"element_type": "elasticBeamColumn"})
builder.build()
static = builder.run_static_analysis(pattern_scales={"DEAD": 1.0, "WIND": 1.0})
modal = builder.run_modal_analysis(num_modes=6)
shapes = builder.extract_mode_shapes(6)

# 3. Save to unified NPZ
write_results_npz("results.npz", md, static_results=static,
                   modal_result=modal, mode_shapes=shapes)

# 4. Load and visualise
data = read_results_npz("results.npz")
points, lines, disp, sap_ids = npz_to_pyvista_frame_mesh(
    data, deformed_case="DEAD", scale=20.0)
```

See also the `local/admin_linear.py` script for a complete workflow
(parse → mesh → analyse → save → plot → animate).
