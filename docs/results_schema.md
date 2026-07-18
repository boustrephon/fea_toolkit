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

## NPZ Schema

### Geometry (always present)

These arrays describe the meshed model topology — stored once per `.npz` file.

**Nodes:**

| Array | Shape | dtype | Description |
|---|---|---|---|
| `node_tag` | `(N_node,)` | `int` | OpenSees node tag |
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
| `frame_node_i` | `(N_frame,)` | `int` | I‑end node tag |
| `frame_node_j` | `(N_frame,)` | `int` | J‑end node tag |
| `frame_t_start` | `(N_frame,)` | `float` | Parametric start along parent [0,1] — optional, present for split children |
| `frame_t_end` | `(N_frame,)` | `float` | Parametric end along parent [0,1] — optional, present for split children |

**Shell elements (floor/wall/roof):**

| Array | Shape | dtype | Description |
|---|---|---|---|
| `shell_eid` | `(N_shell,)` | `int` | Element index (0‑based) |
| `shell_sap_id` | `(N_shell,)` | `str` | Original SAP2000 AreaID |
| `shell_sec_name` | `(N_shell,)` | `str` | Section name |
| `shell_node_1` | `(N_shell,)` | `int` | Corner node 1 tag |
| `shell_node_2` | `(N_shell,)` | `int` | Corner node 2 tag |
| `shell_node_3` | `(N_shell,)` | `int` | Corner node 3 tag |
| `shell_node_4` | `(N_shell,)` | `int` | Corner node 4 tag |

### Static analysis results (per load case)

Each static case gets a group of arrays keyed by case name.

**Nodal displacements:**

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
| `modal/period` | `(N_mode,)` | `float` | Natural period (s) |
| `modal/frequency` | `(N_mode,)` | `float` | Natural frequency (Hz) |
| `modal/omega` | `(N_mode,)` | `float` | Circular frequency (rad/s) |
| `modal/mx_ratio` | `(N_mode,)` | `float` | X‑mass participation ratio |
| `modal/my_ratio` | `(N_mode,)` | `float` | Y‑mass participation ratio |
| `modal/mz_ratio` | `(N_mode,)` | `float` | Z‑mass participation ratio |
| `modal/mx_eff` | `(N_mode,)` | `float` | X effective modal mass (tonnes) |
| `modal/my_eff` | `(N_mode,)` | `float` | Y effective modal mass |
| `modal/mz_eff` | `(N_mode,)` | `float` | Z effective modal mass |
| `modal/mode_dx` | `(N_node, N_mode)` | `float` | Eigenvector X component |
| `modal/mode_dy` | `(N_node, N_mode)` | `float` | Eigenvector Y component |
| `modal/mode_dz` | `(N_node, N_mode)` | `float` | Eigenvector Z component |

### Response-spectrum results

| Array | Shape | dtype | Description |
|---|---|---|---|
| `rs/sa_x` | `(N_mode,)` | `float` | Spectral acceleration at each period — X direction (m/s²) |
| `rs/sa_y` | `(N_mode,)` | `float` | Spectral acceleration at each period — Y direction (m/s²) |
| `rs/eff_mass_x` | `(N_mode,)` | `float` | Effective modal mass in X |
| `rs/eff_mass_y` | `(N_mode,)` | `float` | Effective modal mass in Y |
| `rs/v_base_x` | `(N_mode,)` | `float` | Per‑mode base shear in X (kN) |
| `rs/v_base_y` | `(N_mode,)` | `float` | Per‑mode base shear in Y (kN) |
| `rs/v_cqc_x` | `()` | `float` | CQC‑combined base shear X (kN) |
| `rs/v_cqc_y` | `()` | `float` | CQC‑combined base shear Y (kN) |
| `rs/v_total_x` | `()` | `float` | Total base shear X incl. missing mass (kN) |
| `rs/v_total_y` | `()` | `float` | Total base shear Y incl. missing mass (kN) |

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
