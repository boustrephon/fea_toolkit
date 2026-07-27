# Unified Results Schema — Design Proposal

## Motivation

Currently the toolkit has three incompatible output formats:

| Format | Stores | Used by |
|---|---|---|
| `*_forces.npz` | Element forces + node displacements | Rhino colouring, PyVista diagrams |
| `mode_shapes.npz` + `modal_results.pkl` | Modal eigenvectors + properties | PyVista animation, cache reload |
| opstool ODB (NetCDF/Zarr) | Full model + results | opstool post-processing |

A single unified NPZ schema would let any visualiser consume any result type
without format conversion, while remaining aligned with opstool's ODB structure
so NPZ ↔ ODB conversion is straightforward.

## Alignment with opstool ODB

opstool's ODB uses `xarray` Datasets with named dimensions.  Our NPZ schema
mirrors the same dimension names and array layout:

| opstool ODB dimension | NPZ equivalent | Meaning |
|---|---|---|
| `node` | `nid` (node_tag) | Node identifier |
| `elem` | `eid` (element index) | Frame/shell element |
| `mode` | `mid` (mode index) | Vibration mode |
| `dof` | `dof` (3) | Spatial component (x, y, z) |
| `end` | `end` (2) | Element I/J end |
| `step` | `step` (time step) | Load step or time increment |

This means every NPZ array can be mapped 1:1 to an xarray DataArray.

## Canonical runtime contract

The repository-owned general analysis/report engine should be the
`generate_report()` entry point in [src/fea_toolkit/report.py](../src/fea_toolkit/report.py).
That function is the canonical runtime contract for the v3 pipeline.

Its responsibilities are to:

1. accept already-parsed `SAPModelData` from the caller (parsing is caller-owned)
2. preprocess the provided data once into a reusable `MeshModel`
3. run the analysis cases through the `AnalysisManager` / `AnalysisBuilder`
   path
4. return a standard result dictionary
5. let the result writer / serializer package the final data into the
   unified NPZ schema

In other words:

- the shared `report` layer owns the generalised `run_all()` behaviour
- the local private Pumphouse wrapper in [local/CLP_BSDG_Latest_Models/Pumphouse](../local/CLP_BSDG_Latest_Models/Pumphouse) should call that
  shared entry point, not duplicate it
- the NPZ schema is the serialised output representation of that shared
  result dictionary

## NPZ Schema

### Geometry (always present)

These arrays describe the meshed model topology — stored once per `.npz` file.

**ID conventions:**

The NPZ schema uses a **mixed ID format** that reflects the two-stage
pipeline (SAP2000 parsing → OpenSees domain):

| ID type | Used for | Format | Example |
|---------|----------|--------|---------|
| **OpenSees node tag** (`node_tag`) | Node identity, connectivity | `int` | `1`, `2` |
| **SAP2000 string ID** (`*_sap_id`) | Element identity, traceability | `str` | `"1"`, `"1-0"`, `"5_af_0_1"` |
| **Element index** (`*_eid`) | Array position (0‑based) | `int` | `0`, `1` |

Node connectivity in frame and shell arrays always uses OpenSees tags
(the `frame_node_i/j` and `shell_node_1–4` fields).  This ensures
consistent cross-referencing: ``node_tag[idx]`` gives the same tag that
appears in ``frame_node_i``.

Static result arrays are ordered by **sorted SAP node ID** to preserve
traceability to the original SAP2000 model.  The sort key handles both
numeric IDs (``"1"``, ``"2"`` → numeric order) and non-numeric IDs
(``"5_af_0_1"`` → string order), so any valid SAP2000 node ID is
supported.

**Nodes:**

| Array | Shape | dtype | Description |
|---|---|---|---|
| `node_tag` | `(N_node,)` | `int` | OpenSees node tag (unique numeric id) |
| `node_sap_id` | `(N_node,)` | `str` | SAP2000 string node ID, e.g. ``"1"`` or ``"5_af_0_1"`` |
| `node_x` | `(N_node,)` | `float` | X coordinate (model units) |
| `node_y` | `(N_node,)` | `float` | Y coordinate |
| `node_z` | `(N_node,)` | `float` | Z coordinate |

**Frame elements (beam/column/brace):**

| Array | Shape | dtype | Description |
|---|---|---|---|
| `frame_eid` | `(N_frame,)` | `int` | Element index (0‑based) |
| `frame_sap_id` | `(N_frame,)` | `str` | Original SAP2000 FrameID, e.g. ``"1"`` or ``"1-0"`` for split children |
| `frame_parent_sap_id` | `(N_frame,)` | `str` | Parent SAP2000 ID for split children, empty string for originals |
| `frame_sec_name` | `(N_frame,)` | `str` | Section name, e.g. ``"UB300"`` |
| `frame_node_i` | `(N_frame,)` | `int` | I‑end node **tag** (OpenSees, matches ``node_tag``) |
| `frame_node_j` | `(N_frame,)` | `int` | J‑end node **tag** (OpenSees, matches ``node_tag``) |
| `frame_t_start` | `(N_frame,)` | `float` | Parametric start along parent [0,1] — optional, present for split children |
| `frame_t_end` | `(N_frame,)` | `float` | Parametric end along parent [0,1] — optional, present for split children |

**Shell elements (floor/wall/roof):**

| Array | Shape | dtype | Description |
|---|---|---|---|
| `shell_eid` | `(N_shell,)` | `int` | Element index (0‑based) |
| `shell_sap_id` | `(N_shell,)` | `str` | Original SAP2000 AreaID |
| `shell_sec_name` | `(N_shell,)` | `str` | Section name |
| `shell_node_1` | `(N_shell,)` | `int` | Corner node 1 **tag** (OpenSees, matches ``node_tag``) |
| `shell_node_2` | `(N_shell,)` | `int` | Corner node 2 **tag** |
| `shell_node_3` | `(N_shell,)` | `int` | Corner node 3 **tag** |
| `shell_node_4` | `(N_shell,)` | `int` | Corner node 4 **tag** |

### Static analysis results (per load case)

Each static case gets a group of arrays keyed by case name.

**Nodal displacements:**
Sorted by SAP node ID (see ID conventions above).

| Array | Shape | dtype | Description |
|---|---|---|---|
| `static/{case}/node_dx` | `(N_node,)` | `float` | Displacement in X |
| `static/{case}/node_dy` | `(N_node,)` | `float` | Displacement in Y |
| `static/{case}/node_dz` | `(N_node,)` | `float` | Displacement in Z |

**Element forces at each end (global coordinates):**

| Array | Shape | dtype | Description |
|---|---|---|---|
| `static/{case}/fx_i` | `(N_frame,)` | `float` | I‑end axial force |
| `static/{case}/fy_i` | `(N_frame,)` | `float` | I‑end shear Y |
| `static/{case}/fz_i` | `(N_frame,)` | `float` | I‑end shear Z |
| `static/{case}/mx_i` | `(N_frame,)` | `float` | I‑end torsion |
| `static/{case}/my_i` | `(N_frame,)` | `float` | I‑end moment Y |
| `static/{case}/mz_i` | `(N_frame,)` | `float` | I‑end moment Z |
| `static/{case}/fx_j` … `mz_j` | `(N_frame,)` | `float` | Same at J‑end |

**Load case labels:**

| Array | Shape | dtype | Description |
|---|---|---|---|
| `static_case_labels` | `(N_case,)` | `str` | e.g. ``["DEAD", "SL_X", "LL"]`` |

### Modal analysis results

| Array | Shape | dtype | Description |
|---|---|---|---|
| `modal/period` | `(N_mode,)` | `float` | Natural period per mode (s) |
| `modal/frequency` | `(N_mode,)` | `float` | Natural frequency per mode (Hz) |
| `modal/omega` | `(N_mode,)` | `float` | Circular frequency per mode (rad/s) |
| `modal/mx_ratio` | `(N_mode,)` | `float` | Modal participating mass ratio — X (%) |
| `modal/my_ratio` | `(N_mode,)` | `float` | Modal participating mass ratio — Y (%) |
| `modal/mz_ratio` | `(N_mode,)` | `float` | Modal participating mass ratio — Z (%) |
| `modal/mx_eff` | `(N_mode,)` | `float` | Effective modal mass — X (tonnes) |
| `modal/my_eff` | `(N_mode,)` | `float` | Effective modal mass — Y (tonnes) |
| `modal/mz_eff` | `(N_mode,)` | `float` | Effective modal mass — Z (tonnes) |
| `modal/mode_dx` | `(N_node, N_mode)` | `float` | Eigenvector X component per node × mode |
| `modal/mode_dy` | `(N_node, N_mode)` | `float` | Eigenvector Y component per node × mode |
| `modal/mode_dz` | `(N_node, N_mode)` | `float` | Eigenvector Z component per node × mode |

Mode shape arrays (``modal/mode_d{x,y,z}``) are stored as 2D matrices where
column *j* is the eigenvector for mode *j* (0‑based) and row *i* matches
``node_tag[i]``.  This layout is compatible with PyVista::

    import pyvista as pv
    import numpy as np

    # Load
    data = np.load("results.npz")
    nodes = np.column_stack([data["node_x"], data["node_y"], data["node_z"]])
    mesh = pv.PolyData(nodes)

    # Animate mode 0
    for phase in np.linspace(0, 2*np.pi, 60):
        deformed = nodes + data["modal/mode_dx"][:, 0:1] * np.sin(phase) * scale
        mesh.points = deformed

### Response-spectrum results

| Array | Shape | dtype | Description |
|---|---|---|---|
| `rs/period` | `(N_mode,)` | `float` | Modal periods used for RS analysis (s) |
| `rs/v_base_x` | `(N_mode,)` | `float` | Per‑mode base shear in X (kN) |
| `rs/v_base_y` | `(N_mode,)` | `float` | Per‑mode base shear in Y (kN) |
| `rs/v_cqc_x` | `()` | `float` | CQC‑combined base shear X (kN) |
| `rs/v_cqc_y` | `()` | `float` | CQC‑combined base shear Y (kN) |
| `rs/v_srss_x` | `()` | `float` | SRSS‑combined base shear X (kN) |
| `rs/v_srss_y` | `()` | `float` | SRSS‑combined base shear Y (kN) |
| `rs/elem_sap_id` | `(N_frame,)` | `str` | SAP2000 frame element ID |
| `rs/elem_z_bot` | `(N_frame,)` | `float` | Z‑coordinate of element bottom node (m) |
| `rs/elem_z_mid` | `(N_frame,)` | `float` | Z‑coordinate of element mid‑height (m) |
| `rs/elem_Vy_i` | `(N_frame,)` | `float` | I‑end local Vy (kN) |
| `rs/elem_Vz_i` | `(N_frame,)` | `float` | I‑end local Vz (kN) |
| `rs/elem_My_i` | `(N_frame,)` | `float` | I‑end local My (kN·m) |
| `rs/elem_Mz_i` | `(N_frame,)` | `float` | I‑end local Mz (kN·m) |
| `rs/elem_Vy_j` | `(N_frame,)` | `float` | J‑end local Vy (kN) |
| `rs/elem_Vz_j` | `(N_frame,)` | `float` | J‑end local Vz (kN) |
| `rs/elem_My_j` | `(N_frame,)` | `float` | J‑end local My (kN·m) |
| `rs/elem_Mz_j` | `(N_frame,)` | `float` | J‑end local Mz (kN·m) |
| `rs/node_tag` | `(N_node,)` | `int` | OpenSees node tag (see ID conventions) |
| `rs/node_dx` | `(N_node,)` | `float` | CQC‑combined nodal displacement X (m) |
| `rs/node_dy` | `(N_node,)` | `float` | CQC‑combined nodal displacement Y (m) |
| `rs/node_dz` | `(N_node,)` | `float` | CQC‑combined nodal displacement Z (m) |

### Metadata

| Array | Shape | dtype | Description |
|---|---|---|---|
| `force_unit` | `()` | `str` | e.g. ``"kN"``, ``"N"`` |
| `length_unit` | `()` | `str` | e.g. ``"m"``, ``"mm"`` |
| `created` | `()` | `str` | ISO‑8601 timestamp |
| `analysis_types` | `(N_analysis,)` | `str` | e.g. ``["static", "modal", "rs"]`` |

## File naming

```
{model_stem}_results.npz
```

For the admin building: `Admin_0.7E_short term_results.npz`

## Immediate implementation checklist

1. Lock the shared per-case runtime contract in [src/fea_toolkit/analysis/base.py](../src/fea_toolkit/analysis/base.py) with a minimal `AnalysisCaseSpec` dataclass.
2. Re-export that contract from [src/fea_toolkit/analysis/__init__.py](../src/fea_toolkit/analysis/__init__.py) so all callers use the same package-level API.
3. Keep [src/fea_toolkit/report.py](../src/fea_toolkit/report.py) as the canonical repository-owned `generate_report()` / `run_all()` entry point.
4. Make the report result dictionary the stable bundle consumed by [src/fea_toolkit/io/npz_writer.py](../src/fea_toolkit/io/npz_writer.py).
5. Keep [src/fea_toolkit/model/mesh_model.py](../src/fea_toolkit/model/mesh_model.py) as the shared frozen `MeshModel` handoff object between preprocessing and analysis.
6. Treat [src/fea_toolkit/opensees/builder.py](../src/fea_toolkit/opensees/builder.py) as a Tcl-export helper only; do not route the active v3 runtime through it.
7. Keep private Pumphouse files under [local/CLP_BSDG_Latest_Models/Pumphouse](../local/CLP_BSDG_Latest_Models/Pumphouse) as thin wrappers that call the shared report engine.

## Mapping to opstool ODB

Every NPZ array maps directly to an xarray DataArray:

```python
# NPZ → xarray (for opstool ODB export)
import xarray as xr
import numpy as np

data = np.load("results.npz")

# Model data
ds_model = xr.Dataset(
    coords={"nid": data["node_tag"]},
    data_vars={
        "x": ("nid", data["node_x"]),
        "y": ("nid", data["node_y"]),
        "z": ("nid", data["node_z"]),
    },
)

# Modal eigenvectors
ds_modal = xr.Dataset(
    coords={"nid": data["node_tag"], "mid": np.arange(len(data["modal/period"]))},
    data_vars={
        "period": ("mid", data["modal/period"]),
        "mode_dx": ("nid", "mid", data["modal/mode_dx"]),
        "mode_dy": ("nid", "mid", data["modal/mode_dy"]),
        "mode_dz": ("nid", "mid", data["modal/mode_dz"]),
    },
)
```

## Repository implementation mapping

The schema should therefore be implemented with this ownership split:

1. **Shared report orchestration** — [src/fea_toolkit/report.py](../src/fea_toolkit/report.py)
   * owns the result dict contract and the `run_all()` pipeline
2. **Shared preprocessing and analysis realization** —
   [src/fea_toolkit/opensees/preprocessor.py](../src/fea_toolkit/opensees/preprocessor.py),
   [src/fea_toolkit/model/mesh_model.py](../src/fea_toolkit/model/mesh_model.py),
   [src/fea_toolkit/opensees/analysis_builder.py](../src/fea_toolkit/opensees/analysis_builder.py)
   * provide the data objects consumed by the report engine
3. **Result serialisation** — [src/fea_toolkit/io/npz_writer.py](../src/fea_toolkit/io/npz_writer.py)
   * turns the standard dict output into the unified NPZ arrays
4. **Private local wrappers** — [local/CLP_BSDG_Latest_Models/Pumphouse](../local/CLP_BSDG_Latest_Models/Pumphouse)
   * supply private paths and presentation-specific wrappers
   * do not contain the general analytical heart

## Implementation plan

1. **`io/results_schema.py`** — TypedDict or dataclass defining the schema,
    plus validation helpers (``validate_npz()`` that checks required arrays
    and shapes).
2. **`io/npz_writer.py`** — ``write_results_npz(path, md, static_results, modal_results, rs_results)``
    that assembles the arrays and calls ``np.savez_compressed()``.
    Replaces ``export_results_to_npz()`` and ``save_cache()``.
3. **`io/npz_reader.py`** — ``read_results_npz(path)`` that returns a
    dict of numpy arrays (Rhino‑friendly, no xarray dependency).
4. **Adapters** — thin functions ``npz_to_opstool_odb()`` and
    ``npz_to_pyvista_mesh()`` that wrap the reader and convert to the
    target format.
5. **Deprecation** — ``export_results_to_npz()`` delegates to the new writer
    with a compatibility shim.