---
title: "Unified Results Schema"
description: "The canonical unified NPZ results schema — the on-disk exchange format implemented by fea_toolkit.io.npz_writer."
status: "stable"
tags: [schema, npz, results, io]
category: [export-viz]
related: [report_generation.md, viewer.md, rhino_export.md, storey_response.md]
---
# Unified Results Schema

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

The repository-owned general analysis/report engine is the
`generate_report()` entry point in `src/fea_toolkit/report.py`.
That function is the canonical runtime contract for the two‑stage pipeline.

Its responsibilities are to:

1. accept already-parsed `SAPModelData` from the caller (parsing is caller-owned)
2. run precondition checks (units, material defaults) on the input data
3. preprocess the data once into a reusable `MeshModel` via the `Preprocessor`
4. create an `AnalysisBuilder` for each analysis case from that `MeshModel`
5. build the OpenSees domain via `build_domain()`, apply loads via
   `create_loads()`, and run the analysis via the case‑specific runner
6. return a standard result dictionary that the NPZ writer can consume

In other words:

- the shared ``report`` layer owns the generalised ``run_all()`` behaviour
- the local private Project A wrapper (a project-specific script in the
  private ``local/`` directory) should call that shared entry point, not
  duplicate it
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
| `shell_parent_sap_id` | `(N_shell,)` | `str` | Parent SAP2000 ID for split/meshed children (from ``area.parent_id``), empty string for original areas. Aligned 1:1 with ``shell_sap_id`` — the authoritative source for `group_shell_forces_by_section()` child grouping (never parsed from element names). |
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

**Element forces at each end (local coordinates):**

The static force arrays are recorded in the **local** element coordinate
system (the OpenSees ``localForces`` response), matching the
``forces_coordinate_system`` metadata entry (``"local"``) that the NPZ
writer hard-validates before writing.  This contract is shared by the
static and pushover producers — both query ``ops.eleResponse(tag,
"localForces")``.  Visualisers must not interpret these arrays as global;
use the element geometry to transform to global coordinates if needed.

| Array | Shape | dtype | Description |
|---|---|---|---|
| `static/{case}/fx_i` | `(N_frame,)` | `float` | I‑end axial force |
| `static/{case}/fy_i` | `(N_frame,)` | `float` | I‑end shear Y |
| `static/{case}/fz_i` | `(N_frame,)` | `float` | I‑end shear Z |
| `static/{case}/mx_i` | `(N_frame,)` | `float` | I‑end torsion |
| `static/{case}/my_i` | `(N_frame,)` | `float` | I‑end moment Y |
| `static/{case}/mz_i` | `(N_frame,)` | `float` | I‑end moment Z |
| `static/{case}/fx_j` … `mz_j` | `(N_frame,)` | `float` | Same at J‑end |

**Explicit `*_local` alias arrays (optional):**

The 12 arrays above are the required payload.  A producer *may* additionally
write `static/{case}/fx_i_local` … `mz_j_local` as explicit aliases of the
same local-frame quantities.  These are **optional**: producers that rely on
the ``forces_coordinate_system`` metadata do not write them, and visualisers
(``_extract_npz_frame_forces``) synthesise the ``*_local`` keys on read.
`validate_npz()` therefore only shape-checks them when present.

> **Not yet computed by the model review.**  The review exports the 12
> required force arrays; it does not write the explicit `*_local` aliases
> (the metadata flag makes them derivable).  See ``docs/model_review.md``.

**Load case labels:**

| Array | Shape | dtype | Description |
|---|---|---|---|
| `static_case_labels` | `(N_case,)` | `str` | e.g. ``["DEAD", "SL_X", "LL"]`` |

**Scalar entries (incl. pushover performance-point scalars):**

| Array | Shape | dtype | Description |
|---|---|---|---|
| `static/{case}/{key}` | `(1,)` | same as value | Any JSON-scalar (`int`, `float`, `str`, `bool`) or `{"value": scalar}` entry in a static case dict is persisted as a shape-`(1,)` array. |

For a 4-direction pushover run the performance-point scalars from
:func:`~fea_toolkit.model.csm.compute_performance_point` are stored under
a single ``"pp"`` static case with flattened ``{direction}/{field}``
keys:

| Array | Shape | dtype | Description |
|---|---|---|---|
| `static/pp/{direction}/D_roof` | `(1,)` | `float` | Roof displacement at PP (model length units) |
| `static/pp/{direction}/V_base` | `(1,)` | `float` | Base shear at PP (model force units) |
| `static/pp/{direction}/control_disp` | `(1,)` | `float` | **Optional** mirror of the capacity-curve control displacement at the PP step (model length units).  The capacity-curve row is located **by matching ``D_roof``** to the pushover capacity curve (same as ``V_base``); ``control_disp`` is then validated as ``abs(control_disp[that_row])``.  Used for validation only. |
| `static/pp/{direction}/S_dp` | `(1,)` | `float` | Spectral displacement at PP (model length units) |
| `static/pp/{direction}/S_ap` | `(1,)` | `float` | Spectral acceleration at PP (model acceleration units) |
| `static/pp/{direction}/S_dy`, ``S_ay`` | `(1,)` | `float` | Bilinear yield point (model length units, model acceleration units) |
| `static/pp/{direction}/mu` | `(1,)` | `float` | Ductility `S_dp / S_dy` |
| `static/pp/{direction}/T_eq` | `(1,)` | `float` | Equivalent period at PP (s) |
| `static/pp/{direction}/beta_eq`, ``B`` | `(1,)` | `float` | Equivalent damping / reduction factor |
| `static/pp/{direction}/converged` | `(1,)` | `bool` | CSM convergence flag |
| `static/pp/{direction}/bilinearize_method` | `(1,)` | `str` | Yield-point detection method |

Example:

```
data["static/pp/+X/D_roof"]   # array([0.0191])
data["static/pp/+X/V_base"]   # array([3532.74])
```

Authoring rule: pass the PP payload as one ``"pp"`` static case with
flattened ``f"{direction}/{field}"`` keys and plain scalar values (no
``{"value": ...}`` wrappers, no nested per-direction cases).

**D_roof sign convention**: ``D_roof`` is a **non-negative magnitude** —
the single canonical convention.  The CSM engine folds the capacity curve
into the positive quadrant; the push-direction sign is carried by
``V_base`` only.  Consumers matching ``D_roof`` against recorded pushover
steps must always compare against ``abs(control_disp)``, never the signed
``control_disp`` array.

**Consumers locating the capacity-curve row**: no step index is
persisted in the PP payload — the canonical ``"pp"`` static case is
exactly the flattened ``f"{direction}/{field}"`` output of
:func:`~fea_toolkit.model.csm.compute_performance_point` (one scalar per
field; the keys listed in the table above).  ``control_step`` is *not*
part of the schema.  Consumers that need the recorded pushover step at
(or nearest to) the performance point match by value: find the index of
``pushover/{direction}/control_disp`` closest to ``D_roof`` (or to
``control_disp`` when present) and use the same index into
``pushover/{direction}/base_shear``.  Because ``D_roof`` is a magnitude,
always compare against ``abs(control_disp)``.

### Modal analysis results

| Array | Shape | dtype | Description |
|---|---|---|---|
| `modal/period` | `(N_mode,)` | `float` | Natural period per mode (s) |
| `modal/frequency` | `(N_mode,)` | `float` | Natural frequency per mode (Hz) |
| `modal/omega` | `(N_mode,)` | `float` | Circular frequency per mode (rad/s) |
| `modal/mx_ratio` | `(N_mode,)` | `float` | Modal participating mass ratio — X (%) |
| `modal/my_ratio` | `(N_mode,)` | `float` | Modal participating mass ratio — Y (%) |
| `modal/mz_ratio` | `(N_mode,)` | `float` | Modal participating mass ratio — Z (%) |
| `modal/rx_ratio` | `(N_mode,)` | `float` | Modal participating mass ratio — RX (%) |
| `modal/ry_ratio` | `(N_mode,)` | `float` | Modal participating mass ratio — RY (%) |
| `modal/rz_ratio` | `(N_mode,)` | `float` | Modal participating mass ratio — RZ (%) |
| `modal/mx_eff` | `(N_mode,)` | `float` | Effective modal mass — X (tonnes) |
| `modal/my_eff` | `(N_mode,)` | `float` | Effective modal mass — Y (tonnes) |
| `modal/mz_eff` | `(N_mode,)` | `float` | Effective modal mass — Z (tonnes) |
| `modal/mode_dx` | `(N_node, N_mode)` | `float` | Eigenvector X component per node × mode |
| `modal/mode_dy` | `(N_node, N_mode)` | `float` | Eigenvector Y component per node × mode |
| `modal/mode_dz` | `(N_node, N_mode)` | `float` | Eigenvector Z component per node × mode |
| `modal/node_tag` | `(N_node,)` | `int` | Authoritative node-tag mapping for the mode-shape rows — row *i* of `mode_d{x,y,z}` belongs to the node with this tag |

The six ``modal/*_ratio`` arrays are **OpenSees's own values**, copied verbatim
from ``ops.modalProperties("-return", "-unorm")``
(``partiMassRatiosMX/MY/MZ`` and ``partiMassRatiosRMX/RMY/RMZ``).  The mapping
lives in
:func:`fea_toolkit.io.unified_writer.collect_modal_arrays`, which is the
**single source of truth**: both ``write_results`` (the model-review export)
and ``npz_writer.write_results_npz`` reach it.  Those two collectors were once
verbatim copies, and they drifted — the rotational ratios were added to one and
not the other — so a key is now added in exactly one place.  The toolkit does
not recompute participation factors, and the plotting layer only reads these
arrays — see ``fea_toolkit.plotting.mass_participation_ratios()``, which turns
the live ``modal_props`` dict into the per-mode ``(X, Y, Z, RX, RY, RZ)`` tuples
drawn on the mode-shape animation.  Archives written before the rotational keys
existed simply omit ``modal/{rx,ry,rz}_ratio``; readers must treat them as
optional.

Mode shape arrays (``modal/mode_d{x,y,z}``) are stored as 2D matrices where
column *j* is the eigenvector for mode *j* (0‑based) and row *i* matches
``modal/node_tag[i]`` — the dedicated ``(N_node,)`` tag list written alongside
them.  Row order is the **sorted** node-tag order, so do **not** assume it
matches the geometry ``node_tag`` array (written in model dict order); always
pair against ``modal/node_tag``.  This layout is compatible with PyVista::

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
| `rs/m_cqc_x` | `()` | `float` | CQC‑combined base overturning moment X (force·length) |
| `rs/m_cqc_y` | `()` | `float` | CQC‑combined base overturning moment Y (force·length) |
| `rs/m_srss_x` | `()` | `float` | SRSS‑combined base overturning moment X (force·length) |
| `rs/m_srss_y` | `()` | `float` | SRSS‑combined base overturning moment Y (force·length) |
| `rs/roof_disp_cqc_x` | `()` | `float` | CQC‑combined roof displacement X (length) |
| `rs/roof_disp_cqc_y` | `()` | `float` | CQC‑combined roof displacement Y (length) |
| `rs/roof_disp_srss_x` | `()` | `float` | SRSS‑combined roof displacement X (length) |
| `rs/roof_disp_srss_y` | `()` | `float` | SRSS‑combined roof displacement Y (length) |
| `rs/elem_sap_id` | `(N_frame,)` | `str` | SAP2000 frame element ID — **optional block** |
| `rs/elem_z_bot` | `(N_frame,)` | `float` | Z‑coordinate of element bottom node (m) |
| `rs/elem_z_mid` | `(N_frame,)` | `float` | Z‑coordinate of element mid‑height (m) |
| `rs/elem_combination` | `()` | `str` | Modal combination rule used: `"cqc"` (default) or `"srss"` |
| `rs/elem_direction` | `()` | `str` | Excitation direction the block refers to (`"X"` / `"Y"` / `"Z"`) |
| `rs/elem_fx_i` … `rs/elem_mz_i` | `(N_frame,)` | `float` | I‑end **local** forces/moments: `Fx`, `Fy`, `Fz`, `Mx`, `My`, `Mz` |
| `rs/elem_fx_j` … `rs/elem_mz_j` | `(N_frame,)` | `float` | Same at the J‑end |
| `rs/elem_Vy_i` / `_j`, `rs/elem_Vz_i` / `_j` | `(N_frame,)` | `float` | **Deprecated aliases** of `fy` / `fz` (see below) |
| `rs/elem_My_i` / `_j`, `rs/elem_Mz_i` / `_j` | `(N_frame,)` | `float` | **Deprecated aliases** of `my` / `mz` (see below) |
| `rs/node_tag` | `(N_node,)` | `int` | OpenSees node tag (see ID conventions) — **optional block** |
| `rs/node_dx` | `(N_node,)` | `float` | CQC‑combined nodal displacement X (m) |
| `rs/node_dy` | `(N_node,)` | `float` | CQC‑combined nodal displacement Y (m) |
| `rs/node_dz` | `(N_node,)` | `float` | CQC‑combined nodal displacement Z (m) |

**Local end-force layout**

`rs/elem_*` values come from `ops.eleResponse(tag, "localForces")`, which
returns the 12-component vector in the **grouped** order

```
[Fx_i, Fy_i, Fz_i, Mx_i, My_i, Mz_i, Fx_j, Fy_j, Fz_j, Mx_j, My_j, Mz_j]
```

— three forces then three moments per end.  **`Fy` and `Fz` are the member
shears** along the element's local y and z axes (paired with the bending
moments `Mz` and `My` respectively); they are taken straight from the response
rather than derived from a moment gradient.  This ordering is *not* the
interleaved `[Fx, Fy, Mz, Fz, My, Mx]` arrangement some older OpenSees sources
show; it is pinned by
`tests/test_workflows.py::TestResponseSpectrumWorkflow::test_local_forces_component_order`.

**Modal combination is performed by the toolkit, not by OpenSees**

OpenSees' ``responseSpectrumAnalysis`` command "computes only the modal
displacements, any modal combination is up to the user" — the ``-mode $n`` flag
processes **one mode at a time**.  Per-element RS forces are therefore built by
running that mode loop, reading ``ops.eleResponse(tag, "localForces")`` for each
mode, and combining across modes ourselves (CQC by default, SRSS on request).
The rule actually used is recorded in `rs/elem_combination`.

**Combination is vectorised; the OpenSees calls are not**

The ``n_modes x n_elements`` extraction loop is a cost floor that numpy cannot
remove: it is one OpenSees call per element per mode, and OpenSees combines
nothing itself.  The *combination* step, however, is pure array arithmetic and
is done with numpy — :func:`~fea_toolkit._cqc.cqc_rho_matrix` builds the
``n_modes x n_modes`` correlation matrix **once** and
:func:`~fea_toolkit._cqc.cqc_combine_matrix` evaluates ``sqrt(vᵀ ρ v)`` for
every element and component in a single ``einsum``.  The scalar
:func:`~fea_toolkit._cqc.cqc_combine` previously rebuilt ρ inside every call,
i.e. ``n_elements x 12`` redundant rebuilds; on a 2 000-element / 20-mode
workload the vectorised path is ~950× faster and agrees with the scalar path to
machine precision (~4e-16).  The vectorised helpers are the same rule, not an
approximation — ``tests/test_cqc.py`` pins them against ``cqc_combine``.

**Deprecated per-element aliases**

`rs/elem_Vy_*` / `rs/elem_Vz_*` / `rs/elem_My_*` / `rs/elem_Mz_*` predate the
full-component block.  `Vy`/`Vz` were originally *derived* from the moment
gradient (`Vy = dMz/dx`); they now simply mirror the local shears
(`Vy == rs/elem_fy_*`, `Vz == rs/elem_fz_*`).  They are retained only because
the 2D RS renderer and archives written before this change read them.  They are
**scheduled for deletion** — see `docs/deprecation_plan.md`.

**Which RS arrays are optional**

`rs/period`, `rs/v_base_*`, the combined `rs/v_*`, `rs/m_*` and
`rs/roof_disp_*` scalars are always written by ``collect_rs_arrays()``
(missing moment/roof inputs default to `0.0`), so they are **required**
whenever ``"rs"`` appears in `analysis_types`.  Two blocks are **optional**
and appear only when the producer supplies the corresponding data:

* `rs/elem_*` — written when `rs_element_forces` (from
  ``AnalysisBuilder.extract_element_rs_forces()``) is passed to
  ``write_results()`` / ``write_model_stages()``, or when the model review is
  run with ``--rs-element-forces``.  Also **single‑direction** (the first
  configured direction), recorded in `rs/elem_direction`.
* `rs/node_*` — written when `rs_nodal_displacements` (from
  ``AnalysisBuilder.compute_rs_nodal_displacements()``) is passed.  The block
  is **single‑direction** by schema: the Rhino RS deformed‑shape overlay
  (``_load_deformed_arrays(data, "rs")``) takes no direction argument, so
  multi‑direction producers should export one direction (the model review
  exports the first configured direction, X by default).

The legacy per-mode `rs/sa_*` (spectral acceleration), `rs/eff_mass_*`
(effective mass) and `rs/v_total_*` (total base shear: SRSS of CQC + rigid +
missing) arrays are still written for compatibility with the pre-unification
`npz_writer` key set.  They are **not** declared in `RS_ARRAYS`, so
`validate_arrays()` neither requires nor shape-checks them; a producer that
omits the source fields gets empty per-mode arrays and a zero total.

> **Model review support is opt-in.**  The review's RS export always writes the
> combined scalars and the single‑direction `rs/node_*` block.  Element‑level
> forces (`rs/elem_*`) are recorded only with `--rs-element-forces`, because the
> extraction is `O(n_modes × n_elements)` on top of the base-shear pass.  See
> ``docs/model_review.md``; per-storey RS aggregation remains a future
> *enhanced QC* item.

**Validation dimensions**

`validate_npz()` / `validate_arrays()` resolve `N_node`, `N_frame`,
`N_shell`, `N_mode` and `N_analysis` from the arrays present, then check each
declared array against the **correct** dimension: nodal arrays (`node_*`,
`rs/node_*`) against `N_node`, element arrays (`fx_i` … `mz_j`,
`rs/elem_*`) against `N_frame`.  A *missing* required array is reported as
`Missing … array`; optional arrays are only shape‑checked when present.

### Metadata

| Array | Shape | dtype | Description |
|---|---|---|---|
| `schema_version` | `()` | `int` | File-level results-layout version (`results_schema.SCHEMA_VERSION`, currently 2).  Absent in legacy files — read as `SCHEMA_VERSION_LEGACY` (1) via `fea_toolkit.io.get_schema_version()`. |
| `force_unit` | `()` | `str` | e.g. ``"kN"``, ``"N"`` |
| `length_unit` | `()` | `str` | e.g. ``"m"``, ``"mm"`` |
| `created` | `()` | `str` | ISO‑8601 timestamp |
| `analysis_types` | `(N_analysis,)` | `str` | e.g. ``["static", "modal", "rs"]`` |

## File naming

```
{model_stem}_results.npz
```

For the Project B building: `project_b_results.npz`

## Immediate implementation checklist

1. Lock the shared per-case runtime contract in `src/fea_toolkit/analysis/base.py` with a minimal `AnalysisCaseSpec` dataclass.
2. Re-export that contract from `src/fea_toolkit/analysis/__init__.py` so all callers use the same package-level API.
3. Keep `src/fea_toolkit/report.py` as the canonical repository-owned `generate_report()` / `run_all()` entry point.
4. Make the report result dictionary the stable bundle consumed by `src/fea_toolkit/io/npz_writer.py`.
5. Keep `src/fea_toolkit/model/mesh_model.py` as the shared frozen `MeshModel` handoff object between preprocessing and analysis.
6. Treat `src/fea_toolkit/opensees/builder.py` as a Tcl-export helper only; do not route the active v3 runtime through it.
7. Keep private Project A files under `local/<private_project>/` as thin wrappers that call the shared report engine.

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

1. **Shared report orchestration** — `src/fea_toolkit/report.py`
   * owns the result dict contract and the `run_all()` pipeline
2. **Shared preprocessing and analysis realization** —
   `src/fea_toolkit/opensees/preprocessor.py`,
   `src/fea_toolkit/model/mesh_model.py`,
   `src/fea_toolkit/opensees/analysis_builder.py`
   * provide the data objects consumed by the report engine
3. **Result serialisation** — `src/fea_toolkit/io/npz_writer.py`
   * turns the standard dict output into the unified NPZ arrays
4. **Private local wrappers** — `local/<private_project>/`
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