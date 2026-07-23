# Report Generation — Design Proposal

## Overview

The goal is a **configuration-driven** report pipeline that:

1. Reads a Python dict config specifying which analyses to run and how
2. Runs the Preprocessor once, then creates lightweight AnalysisBuilder
   instances for each analysis case (two-stage pipeline)
3. Stores all results in a single NPZ or HDF5 archive
4. Generates a self-contained report (HTML or PDF) from a **Quarto template**

This separates **computation** (analysis execution + data storage) from
**presentation** (report rendering), so you can re-render a report from
cached results without re-running analyses.

The architecture has evolved significantly from the original proposal —
see §3.9 for the two-stage pipeline and §2 for the simplified HDF5
approach.

---

## 1. Configuration (Python dict)

The config defines the model source, **analysis mode** (which drives
default element/material/solver choices), analysis-specific parameters,
and report layout.  It is a Python dict (``_DEFAULT_CONFIG`` in each
report script) that can be overridden at the call site — no YAML parser
needed.

### Auto-Configuration by Analysis Mode

Instead of manually specifying element types and solver settings, the
config selects an **analysis mode** at the top level.  The system maps
this to sensible defaults:

| Mode | Element type | Section model | Geometric | Damping | Solver | Brace strategy | Execution |
|---|---|---|---|---|---|---|---|
| `linear_static` | `elasticBeamColumn` | Elastic (E, A, I) | Linear | None | Newton · 1e-8 · RCM | `beam` (elastic) | ✅ OpenSeesPy |
| `modal` | `elasticBeamColumn` | Elastic (E, A, I) | Linear | None | Eigen (lapack) | `beam` (elastic) | ✅ OpenSeesPy |
| `response_spectrum` | `elasticBeamColumn` | Elastic (E, A, I) | Linear | CQC (inherent) | Eigen + CQC | `beam` (elastic) | ✅ OpenSeesPy |
| `pushover` (steel) | `nonlinearBeamColumn` | Fiber (`Steel01`) | PDelta | Rayleigh (2%) | NewtonLineSearch · 1e-6 · RCM · auto-fallback | `truss` + `Hysteretic` | ⚠️ OpenSeesPy (steel OK) |
| `pushover` (RC) | `forceBeamColumn` | Fiber (`Concrete01`, `Steel02`) | PDelta | Rayleigh (2%) | Same | `beam` (elastic) | ❌ OpenSeesPy → **Tcl export** |
| `nonlinear_dynamic` | `forceBeamColumn` | Fiber (any) | PDelta | Rayleigh (2%) | Newmark · HHT · RCM | `truss` + `Hysteretic` | ❌ OpenSeesPy → **Tcl export** |

Individual overrides in the ``builder`` section take precedence over
these defaults.

> **Important: OpenSeesPy limitation for nonlinear RC analysis.**
> Nonlinear RC analysis with fiber sections (``Concrete01``, ``Steel02``,
> ``forceBeamColumn`` with ``Lobatto`` integration) is supported only
> when using the **pinned OpenSeesPy build** bundled with the toolkit.
> The stock ``pip install openseespy`` distribution does not include
> these nonlinear material formulations.  Users must ensure the pinned
> build is active in their environment.
>
> Analysis requiring nonlinear materials — pushover with RC fiber
> sections, nonlinear dynamic, or brace buckling — can be executed
> either directly in Python (with the pinned build) or via **Tcl export**
> to a standalone OpenSees executable.  The toolkit provides two export
> paths:
>
> - ``export_model_to_tcl()`` — translates ``SAPModelData`` directly to
>   Tcl commands, accepting ``tcl_prefix`` and ``tcl_suffix`` to inject
>   nonlinear material definitions, fiber sections, and custom analysis
>   commands.
> - ``RecordingOpenSees`` — records all ``ops.*`` calls during a Python
>   build, then saves them as a Tcl script.  For elastic builds only
>   (fiber sections cannot be created in Python then recorded, as the
>   pinned build is required for simulation).
>
> The practical pipeline for nonlinear analyses via Tcl is:
>
> ```
> SAPModelData  ──→  export_model_to_tcl()  ──→  model.tcl
>                                                     │
>                   User-provided Tcl snippets ───────┤
>                   (fiber sections, materials,        │
>                    analysis commands)                │
>                                                     ▼
>                                           XaraTclRunner.run()
>                                           (standalone OpenSees)
> ```

### Full Config Schema

```yaml
# ── Model ───────────────────────────────────────────────────────────
model:
  path: "path/to/model.s2k"
  # Units are read from the source model (SAP2000/ETABS .s2k file).
  # No manual override needed.  Future support for unit conversion
  # may add an optional ``output_units`` here.

# ── Analysis Mode ───────────────────────────────────────────────────
# The analysis mode drives default choices for element types, material
# models, solver settings, and brace modelling strategy.
#
#   "modal"              - same as linear_static (mass + stiffness only)
#   "response_spectrum"  - elastic (same as linear_static, plus CQC)
#   "pushover"           - nonlinearBeamColumn, fiber sections,
#                          corotational geometry,
#                          displacement-controlled loading
#   "nonlinear_dynamic"  - nonlinearBeamColumn, fiber sections,
#                          Newmark integrator,
#                          ground-motion input (future)
#
# Analysis-specific defaults are derived inside each per-analysis
# loop, not from a global analysis_mode.  Individual overrides can
# still be specified in ``builder`` below.
# analysis_mode: "pushover"       (removed — see analyses.*.builder)

# ── Builder Overrides ───────────────────────────────────────────────
# These override the default for the entire model.  Analysis-specific
# overrides go in the corresponding ``analyses.*.builder`` block.
builder:
  create_shells: true
  solver: null             # null = auto based on analysis block

# ── Storey Detection ─────────────────────────────────────────────────
storeys:
  method: "auto"                # "auto" | "s2k_table" | "diaphragm" | "area_elements" | "node_clustering"
  z_tolerance: 0.5

# ── Analyses ─────────────────────────────────────────────────────────
analyses:
  static:
    enabled: true
    cases: null                 # null = auto-detect from model; or ["DEAD", "LIVE", "WIND+X"]
    builder:                    # analysis-specific builder overrides
      element_type: "elasticBeamColumn"
      use_elastic_sections: true

  modal:
    enabled: true
    n_modes: 12
    builder:
      element_type: "elasticBeamColumn"
      use_elastic_sections: true

  response_spectrum:
    enabled: true

    # Spectrum source — one of:
    #
    #   source: "from_model"
    #     Read from the .s2k file.  Supports:
    #       - Code-based spectra (AUTO SEISMIC tables: GB 50011,
    #         UBC, IBC, Eurocode 8, etc.) — generated automatically
    #         from the code parameters.
    #       - User-defined spectra (FUNCTION - RESPONSE SPECTRUM
    #         tables) — period-acceleration pairs embedded in the
    #         .s2k export.
    #
    #   source: "explicit"
    #     Period-acceleration pairs provided directly in config
    #     (overrides any spectrum in the model).
    #
    #   source: "file"
    #     External file path containing period-acceleration pairs
    #     or a code reference.
    #
    source: "from_model"

    # Explicit spectrum (used when source: "explicit")
    explicit:
      periods: [0.0, 0.1, 0.5, 1.0, 2.0]
      accelerations: [2.5, 2.5, 1.0, 0.5, 0.2]

    # External file (used when source: "file")
    # file: "path/to/spectrum.txt"

    n_modes: 12
    damping: 0.05
    combination: "CQC"          # "CQC" | "SRSS"
    missing_mass: true
    builder:
      element_type: "elasticBeamColumn"
      use_elastic_sections: true

  pushover:
    enabled: false
    directions: ["X", "Y"]
    patterns: ["uniform", "triangular"]
    max_drift: 0.05
    n_steps: 100
    spectrum: null              # optional override for CSM
    builder:
      element_type: "forceBeamColumn"
      create_fiber_sections: true
      use_elastic_sections: false

  # Future:
  # dynamic:
  #   enabled: false
  #   # Ground-motion source — one of:
  #   #   "from_model"  — read FUNCTION - TIME HISTORY tables from .s2k
  #   #   "file"        — external accelerogram file (2-column: time, accel)
  #   source: "file"
  #   file: "path/to/accel.txt"
  #   dt: 0.01
  #   n_steps: 2000
  #   scaling: 1.0              # scale factor applied to accelerogram

# ── Storey Response ──────────────────────────────────────────────────
storey_response:
  enabled: true
  z_tolerance: 0.5
  outlier_threshold: 3.0

# ── Output ───────────────────────────────────────────────────────────
output:
  hdf5_path: "results.h5"       # HDF5 archive for all analysis results
  report:
    format: "html"              # "html" | "pdf"
    title: "Structural Evaluation Report"
    author: "Engineer Name"
    template: "report_template.qmd"   # Quarto template
    sections:
      - model_summary
      - storey_plan
      - modal_results
      - response_spectrum
      - static_results
      - pushover_results
      - csm_performance
      - storey_forces           # plot_storey_forces()
      - storey_displacements    # plot_storey_displacements()
      - storey_drifts           # per-storey drift profiles
      - conclusions
```

---

## 2. HDF5 Schema (`results.h5`)

All analysis results are stored under a single HDF5 file with a
hierarchical structure.  Each group contains the raw NumPy arrays
and Pandas DataFrames needed for post-processing and plotting.

```
/
├── meta/                           # Model metadata
│   ├── bounding_box               # JSON-serialised dict
│   ├── units                      # JSON-serialised dict
│   ├── n_nodes                    # scalar
│   ├── n_frames                   # scalar
│   └── n_areas                    # scalar
│
├── functions/                      # Parsed function definitions from .s2k
│   ├── response_spectra/          # One group per FUNCTION - RESPONSE SPECTRUM
│   │   ├── RS1/                   # function name
│   │   │   ├── periods           # 1-D array
│   │   │   ├── accelerations     # 1-D array
│   │   │   └── meta              # JSON: damping, type, etc.
│   │   └── ...
│   └── time_history/              # One group per FUNCTION - TIME HISTORY
│       ├── GM1/                   # function name
│       │   ├── time              # 1-D array
│       │   ├── acceleration      # 1-D array
│       │   └── meta              # JSON: dt, npts, scaling
│       └── ...
│
├── storeys/                        # Storey detection results
│   ├── summary                    # DataFrame: name, elevation, method, confidence
│   ├── centroids                  # DataFrame: storey, x_cm, y_cm
│   └── node_assignment            # DataFrame: storey -> [node_ids]
│
├── static/                         # Per-load-case static results
│   ├── DEAD/                      # one group per case
│   │   ├── displacements          # DataFrame: node_id, ux, uy, uz
│   │   ├── reactions              # DataFrame: node_id, fx, fy, fz, mx, my, mz
│   │   ├── element_forces         # DataFrame: elem_id, Fi, Fj (6 components each)
│   │   ├── load_totals            # JSON: Fx, Fy, Fz, Mx, My, Mz
│   │   └── storey_response        # DataFrame: storey displacement/drift/shear
│   ├── LIVE/
│   │   └── ...                    # same structure
│   └── WIND+X/
│       └── ...
│
├── modal/                          # Modal analysis results
│   ├── periods                    # 1-D array (n_modes,)
│   ├── frequencies                # 1-D array (n_modes,)
│   ├── participation              # DataFrame: mode, ux%, uy%, uz%, rx%, ry%, rz%
│   ├── shapes                     # dict of {mode: {node_tag: (dx, dy, dz)}}
│   └── storey_drifts             # DataFrame: CQC-combined modal drifts per storey
│
├── rs/                             # Response spectrum results
│   ├── base_shear                 # DataFrame: direction (X/Y), V_base, M_base
│   ├── element_forces             # DataFrame: elem_id, P, V2, V3, T, M2, M3
│   ├── nodal_displacements        # DataFrame: node_id, ux, uy, uz
│   ├── missing_mass_correction    # DataFrame: direction, dV, dM
│   └── storey_response            # DataFrame: storey displacement/drift (CQC)
│
├── pushover/                       # Pushover analysis results
│   ├── X/                         # one group per direction
│   │   ├── uniform/               # one group per pattern
│   │   │   ├── drift              # 1-D array (n_steps,)
│   │   │   ├── base_shear         # 1-D array (n_steps,)
│   │   │   ├── displacements      # DataFrame: step -> node displacements
│   │   │   ├── forces             # DataFrame: step -> element forces
│   │   │   └── adrs              # DataFrame: Sd, Sa for CSM
│   │   └── triangular/
│   │       └── ...
│   └── Y/
│       └── ...
│
└── report/                         # Cached computed quantities for report
    ├── storey_forces              # DataFrame: case, storey, Fx, Fy, Fz, Mx, My, Mz
    ├── storey_displacements       # DataFrame: case, storey, Ux, Uy, Rz, Peak_disp
    └── storey_drifts              # DataFrame: case, storey, Drift_X, Drift_Y, Drift_peak

# Future:
# ├── dynamic/                     # Nonlinear dynamic analysis results
# │   ├── GM1/                    # one group per ground-motion record
# │   │   ├── time                # 1-D array (time axis)
# │   │   ├── acceleration        # 1-D array (input accelerogram)
# │   │   ├── displacements       # DataFrame: step -> node displacements
# │   │   ├── forces              # DataFrame: step -> element forces
# │   │   └── storey_response     # DataFrame: storey displacement/drift/shear
# │   └── ...
```

### Python class for HDF5 storage — implemented (simplified)

The proposed ``HDF5Store`` class was not implemented.  Instead, the
toolkit uses a **flat dict-of-arrays** approach that is format-agnostic:

- **Writing**: ``unified_writer._write_h5(path, arrays)`` writes a flat
  dict of NumPy arrays to HDF5 with the same key schema as NPZ.
- **Reading**: ``npz_reader.read_results_hdf5(path)`` reads HDF5 and
  returns a plain ``dict[str, ndarray]``.
- **Dispatcher**: ``npz_reader.read_results(path)`` auto-detects format
  from the file extension (``.npz`` or ``.h5``) and calls the appropriate
  reader.

This is simpler than the proposed class-based design because:

* The same flat-key schema works for both NPZ and HDF5 — no separate
  schema translation needed.
* The reading side is just ``dict(np.load(...))`` or recursively walking
  HDF5 groups; no ``DataFrame``/``JSON`` convenience methods were needed
  in practice.
* All unified plotting functions (``plot_mesh``, ``plot_force_diagram_3d``,
  etc.) check ``isinstance(source, dict)``, so they consume HDF5-read
  dicts without any changes.

No ``hdf5_to_npz()`` converter was needed — the unified plotting
functions read dicts directly, regardless of source format.

**Opstool interoperability**: Opstool uses **Zarr** (default) or
**NetCDF** for its ODB format, not HDF5.  It is built on ``xarray`` for
labeled N-dimensional arrays.  Our flat-dict HDF5 is unrelated to
opstool's format.  If future interop is needed, a converter from our
schema to ``xarray.Dataset → Zarr`` could be added, but there is no
current demand.

### HDF5 ↔ Visualisation Tools — NPZ/dict path now unified

The HDF5 archive is **not a direct input to visualisation tools**.
Instead, visualisation tools consume dicts (from either NPZ or HDF5):

```
                          ┌──────────────────────┐
                          │  NPZ / HDF5 results  │
                          │  (flat dict of arrays)│
                          └──────────┬───────────┘
                                     │
                   ┌─────────────────┼─────────────────┐
                   │                 │                 │
                   ▼                 ▼                 ▼
           ┌───────────────┐ ┌─────────────┐ ┌──────────────┐
           │  Unified 3D   │ │   Report    │ │  Rhino       │
           │  plots        │ │  Gen.       │ │  colouring   │
           │  (PyVista)    │ │  (Quarto)   │ │  (NPZ path)  │
           │               │ │             │ │              │
           │ plot_mesh      │ │ pd.DataFrame│ │ colour_from_ │
           │ plot_deformed_ │ │ from reader │ │ npz()        │
           │ displacement_3d│ │ or builder  │ │ (NPZ only)   │
           │ plot_force_    │ │             │ │              │
           │ diagram_3d     │ │             │ │              │
           └───────┬───────┘ └─────────────┘ └──────────────┘
                   │
                   ▼
           ┌───────────────┐
           │  read_results │  ← auto-detects NPZ or HDF5
           │  (dispatcher) │     by file extension
           └───────────────┘
```

| Visualisation path | Current input | Unified? |
|---|---|---|
| **PyVista 3D** (``plot_mesh``, ``plot_deformed_displacement_3d``, etc.) | Builder, AnalysisBuilder, **or** dict from ``read_results()`` | ✅ Yes — accepts any source |
| **Rhino colouring** (``colour_from_npz``) | File path (NPZ) | ⚠️ File-path only; reads NPZ/HDF5 internally via ``read_results()`` |
| **Matplotlib report plots** (``plot_storey_forces``, ``plot_modal_participation``, etc.) | Pandas DataFrames from builder or reader | ✅ Works unchanged |
| **Opstool / opsVis** | Custom ODB format (Zarr/NetCDF) | ❌ Not directly compatible — future converter if needed |
| **ParaView** | VTK / XDMF + HDF5 | ❌ Future work

This layered approach avoids coupling the analysis storage format to any
specific visualisation tool while keeping the existing NPZ → PyVista/Rhino
pipeline fully functional.

---

## 3. SAP2000 → OpenSees Modelling Challenges

Translating SAP2000 models to OpenSees presents several structural
engineering challenges that the toolkit must address.  These are captured
here to guide implementation priorities.

### 3.1 Shell Element Meshing

SAP2000 shell elements are often **2D super-elements** — large polygons
(sometimes concave) that require sub-meshing before analysis.  The
challenges are:

- **Sub-meshing to nodes**: Super-elements must be subdivided into a
  regular mesh that connects to frame-element nodes and other shell
  edges.  The builder's ``_mesh_areas()`` performs bilinear quad
  subdivision, but complex floor plates with cut-outs or non-rectangular
  geometry need more robust handling.  The subdivision maintains a
  parent-child hierarchy: the original ``AreaElement`` is marked
  ``inactive`` and populated with ``child_ids``, while each sub-element
  carries a ``parent_id`` back to the original super-element.  This
  mirrors the same pattern used by ``FrameElement`` for frame splitting.
- **Line constraints between meshes**: Where floor slabs meet walls or
  where two super-elements of different mesh density meet (e.g. a
  finely-meshed core wall adjacent to a coarsely-meshed floor slab),
  edge nodes must be tied via ``equalDOF`` constraints.  The builder's
  :func:`~fea_toolkit.model.geometry.find_constraint_edges` detects
  incompatible edge meshes and
  :func:`~fea_toolkit.opensees.builder.AnalysisBuilder.apply_edge_constraints`
  emits the corresponding ``equalDOF`` commands.  Future work (P2) could
  store edge-constraint references directly on ``AreaElement``
  (e.g. ``edge_constraint_ids``) for easier lookup.
- **Gmsh-based remeshing**: The optional ``mesh/remesh.py`` module
  provides constrained quadrilateral remeshing for non-rectilinear
  geometries, but integration into the automated workflow is not yet
  complete.

### 3.2 Frame-to-Shell Integration

Frame elements bring concentrated stiffness to the nodes of distributed
shell elements, which creates two difficulties:

- **Drilling moment (Rz) compatibility**: ``ShellMITC4`` has 6 DOFs
  per node (UX, UY, UZ, RX, RY, RZ), but the drilling rotation (Rz)
  carries **zero stiffness** in the element's constitutive matrix.
  Where a frame element delivers a torsional moment to a shell node,
  the missing rotational stiffness causes local singularities or
  unrealistic force distributions.  Constraining all 6 DOFs via
  ``equalDOF`` is still correct — the constraint enforces kinematic
  compatibility without adding spurious stiffness.
- **SAP2000 workarounds** (not yet implemented):
  - **Embedded beam**: Extend the frame member one element-depth into
    the shell mesh, distributing the connection over several nodes.
  - **T‑arms / rigid cross‑arms**: Add rigid link elements perpendicular
    to the beam axis that connect to multiple shell nodes, spreading the
    concentrated moment over a zone rather than a single point.

### 3.3 Spandrel Beams in Walls and Lift Cores

Spandrel beams at openings in shear walls and lift cores can be modelled
in two ways, each with trade-offs:

- **As frame elements** (current approach): Simple but suffers from the
  frame-to-shell drilling stiffness problem.  Also concentrates force at
  a single node at the wall edge.
- **As shell elements**: Avoids the frame-to-shell interface issue but
  requires a finer local mesh relative to the wall panel, which in turn
  needs line constraints at the mesh-density transition.

Recommendation: Support both, with ``apply_edge_constraints()`` handling
the mesh transition for the shell-only approach.  The config's
``analysis_mode`` can include a ``spandrel_as_shell`` flag for the
latter.

### 3.4 Joint Modelling — Levels of Fidelity

SAP2000 supports four levels of joint modelling, and the toolkit should
ideally support all of them:

| Level | Description | Toolkit status |
|---|---|---|
| **0 — Simple point** | Members meet at a node, flex from that point. Vertical offsets (storey-level) may apply. | ✅ Implemented via ``FrameEndOffset.end_i`` / ``end_j`` (longitudinal) + ``off_y_i`` / ``off_z_i`` / ``off_y_j`` / ``off_z_j`` (lateral from cardinal point) |
| **1 — Rigid offset** | Rigid link from node to column face; flexure starts at face. | ⚠️ Partially — ``frame_end_offsets`` create stiff elastic links |
| **2 — Spring offset** | Zero-length spring at the joint adds back some flexibility (% rigidity). | ❌ Not implemented |
| **3 — Joint element** | Explicit joint element allowing for cracking, reinforcement slip, or steel connection flexibility. | ❌ Not implemented |

The ``frame_end_offsets`` data (parsed from the ``FRAME END OFFSETS``
table) creates short stiff `elasticBeamColumn` segments between the
node and the member start.  This works for Level 1 but the rigid
segments can be too stiff — Level 2 would replace them with
zero-length springs of calibrated stiffness.

### 3.5 Cardinal Points and Section Insertion

SAP2000 uses **cardinal points** to position a section relative to its
reference line.  The numbering follows a 3×3 grid (bottom→top, left→right)
plus centroid and shear centre:

| Value | Position | Description | Offset (y, z) |
|---|---|---|---|
| 1 | Bottom left | Lower-left corner | (½B, ½D) |
| 2 | Bottom centre | Bottom edge midpoint | (0, ½D) |
| 3 | Bottom right | Lower-right corner | (−½B, ½D) |
| 4 | Middle left | Left edge midpoint | (½B, 0) |
| 5 | Middle centre | Bounding-box centroid | (0, 0) |
| 6 | Middle right | Right edge midpoint | (−½B, 0) |
| 7 | Top left | Upper-left corner | (½B, −½D) |
| 8 | Top centre | Top edge midpoint | (0, −½D) |
| 9 | Top right | Upper-right corner | (−½B, −½D) |
| 10 | Centroid | Section centroid (default) | (0, 0) |
| 11 | Shear centre | Section shear centre | (0, 0) |

*D* = section depth (local-3), *B* = section width (local-2).

Common settings:

- **RC beams**: cardinal point 8 (top‑centre) — the beam's top face aligns with the storey level (which is typically at the top-of-slab).
- **RC columns**: cardinal point 10 (centroid) or 11 (shear centre).
- **Steel beams**: cardinal point 2 (bottom‑centre) or 10 (centroid).

The insertion point combined with the section depth defines the
**offset** from the reference line to the member's extreme fibre.
These offsets affect:

- **Clear span** for flexure (shorter than centreline distance).
- **Gravity load take-down** — the slab sits on the beam's top flange,
  not at its centroid.
- **Clash detection** in visualisation — cardinal points determine
  where sections appear in 3D views.

**Current status**: ``FrameElement`` now has a ``cardinal_point`` field (default 10 = centroid).  The parser extracts cardinal point values from the ``FRAME SECTION ASSIGNMENTS`` table (columns ``CardinalPoint``, ``Cardinal``, ``CARDINALPT``, or ``InsertPoint``) and computes lateral (y, z) offsets from section dimensions.  These are merged into ``FrameEndOffset`` records at parse time, matching the E2K/ETABS approach.  ``FrameEndOffset`` has been expanded with ``off_y_i``, ``off_z_i``, ``off_y_j``, ``off_z_j`` fields for the lateral components.

### 3.6 Model Validation

Ideally the toolkit's OpenSees output should be validated against the
source SAP2000 model.  For nonlinear analysis, **Perform3D** is the
natural benchmark (it shares a common lineage with SAP2000/ETABS).

- **Linear validation**: Compare base reactions, nodal displacements,
  element forces between SAP2000 and OpenSees for identical linear
  models.  The existing ``check_self_weight_consistency()`` and
  ``load_pattern_totals()`` are a start, but full force-vector comparison
  is needed.
- **Nonlinear validation**: Published Drain3D / Perform3D → OpenSees
  validation test sets exist (e.g. the OpenSees validation examples
  distributed with the source, and research papers comparing Perform3D
  pushover and IDA results with OpenSees).  These should be collected
  and used as regression tests.
- **Modal validation**: Compare periods and mode shapes between
  SAP2000 and OpenSees.  Mass participation ratios from both should
  agree.
- **Response spectrum**: Compare CQC base shear and element forces.

A validation workflow would run the same model through both SAP2000 and
OpenSees and report discrepancies in a standardised format.

#### Key Validation Benchmarks & References

| Source | Type | What it covers |
|---|---|---|
| **OpenSees verification suite** (`EXAMPLES/verification/`) | Regression tests | ``PlanarTruss.tcl`` (truss forces), ``PortalFrame2d.tcl`` (elastic frame), ``EigenFrame.tcl`` / ``EigenFrame.Extra.tcl`` (modal vs Lapack/Arpack), ``AISC25.tcl`` (25 P-Delta buckling examples per AISC), ``PinchedCylinder.tcl`` (shell elements vs exact solution), ``PlanarShearWall.tcl`` (continuum elements), ``sdofTransient.tcl`` / ``NewmarkIntegrator.tcl`` (transient dynamics vs Chopra), ``SmallEigen.tcl`` (small eigenvalue problem). |
| **OpenSees Example Library** (`EXAMPLES/ExampleScripts/`) | Workflow examples | ``RCFrame5.tcl`` (RC frame with fiber sections, validated pushover), ``RCFrame3D_ASDA.tcl`` (3D RC frame), steel and brace models. |
| **Portwood Digital** (`openseesdigital.com/verifications/`) | Blog + examples | Michael H. Scott's verified models: brace buckling, P-Delta columns, steel moment frames, soil-structure interaction. Includes convergence studies and solver recommendations. |
| **PEER Center reports** (`peer.berkeley.edu`) | Research reports | Structural performance database, column calibration, wall validation. **PEER 2017/03**: direct Perform3D vs OpenSees comparison for RC frames (pushover + ground motion). **PEER 2015/01**: DRAIN-3D, Perform3D, and OpenSees modelling comparison. |
| **NHERI SimCenter** (`simcenter.designsafe-ci.org`) | Cross-platform validation | EE-UQ and PBE applications use both OpenSees and SAP2000 as backend solvers — maintained cross-platform validation test sets internally. Also hosts experimental datasets (NEEShub/DesignSafe) for validating fiber-section RC column behaviour. |
| **Scott & Fenves (2006)** — "Plastic Hinge Integration Methods for Force-Based Beam-Column Elements" | Journal paper | Validates `forceBeamColumn` with `HingeRadau` integration against experimental data. ASCE JSE 132(2), DOI `10.1061/(ASCE)0733-9445(2006)132:2(244)`. |
| **Scott & Ryan (2013)** — "Moment-Rotation Behavior of Force-Based Plastic Hinge Elements" | Journal paper | Extends validation to cyclic loading, compares with experimental column tests. Earthquake Spectra 29(2), DOI `10.1193/1.4000136`. |
| **Neuenhofer & Filippou (1998)** — "Geometrically Nonlinear Flexibility-Based Frame Finite Element" | Journal paper | Foundational validation of force-based frame elements. ASCE JSE 124(6). |
| **Schellenberg et al.** — "OpenSees-Software Framework for Nonlinear Analysis" | Framework paper | Various validation examples distributed with OpenSees source. |
| **Haselton & Deierlein (2007)** — PEER Report 2007/03 | Research report | RC column calibration for nonlinear models — standard reference for fiber-section column parameters. |
| **Ibarra & Krawinkler (2005)** — "Global Collapse of Frame Structures under Seismic Excitations" | Research report (+ PEER 2005/06) | Deterioration models for steel and RC components. Foundation for collapse assessment methodology. |
| **Lignos & Krawinkler (2011)** — "Deterioration Modeling of Steel Components in Support of Collapse Prediction" | Journal paper | Steel component deterioration models validated against experimental database. Earthquake Spectra 27(3), DOI `10.1193/1.3602826`. |
| **SAC Steel Project** (FEMA 355 / SAC/BD-00) | Benchmark frames | 3-, 9-, and 20-story steel moment frame models extensively validated against nonlinear dynamic analysis. Standard benchmark for steel frame assessment. |
| **FEMA P695** | Ground-motion sets | Far-field (44 records) and near-field (28 records) sets for IDA. Standard input for collapse assessment. |
| **OpenSeesDays workshops** (`WORKSHOPS/`) | Tutorial models | Steel2dModels (CBF1–CBF4: diagonal, X-brace, V-brace, inverted-V) — used in this project for brace buckling modelling. |

### 3.7 Other Common Modelling Gaps (from Literature)

Research across the OpenSees community (Portwood Digital blog, PEER
reports, OpenSees verification suite, NHERI SimCenter) reveals several
additional frequently-discussed issues not yet covered above:

#### Damping Modelling

*Relevance: static/pushover/modal — none; RS — as parameter only; dynamic — essential.*

SAP2000 and OpenSees handle damping differently, but it is important to
recognise where damping actually affects results:

| Analysis type | Does damping affect results? | Mechanism |
|---|---|---|
| Linear static | ❌ No | Static equilibrium, no time dependence |
| Pushover | ❌ No | Displacement-controlled static loading |
| Modal (eigenvalue) | ❌ No | Eigenvalues are undamped by definition |
| Response spectrum | ⚠️ Partial | CQC coefficients use ζ; spectrum scaling uses η₂ damping reduction factor. Both are **parameters**, not Rayleigh damping in the model. |
| Nonlinear dynamic | ✅ Yes | Rayleigh damping (α₀, α₁) directly affects acceleration, velocity, and displacement response |

The toolkit currently applies **no damping** for any analysis type.
This is correct for static, pushover, and modal analyses.  For response
spectrum analysis, damping is handled parametrically (CQC formula +
spectrum scaling).  For future dynamic analysis, Rayleigh damping
coefficients must be computed from target damping ratios at two
characteristic frequencies (typically the first-mode frequency and a
higher mode, e.g. ω₁ and ω₃).

#### P-Delta Effects

The builder already uses `geomTransf PDelta` for pushover analysis (``push_config['geom_transf_type'] = 'PDelta'``).  The default for other analysis types remains ``'Linear'``, configurable via the ``geom_transf_type`` config key.

#### Effective Stiffness Modifiers (ASCE 41)

SAP2000 stores stiffness modifiers in `FRAME SECTION PROPERTIES 01 - GENERAL`:
- AMod (axial), I3Mod (major bending), I2Mod (minor bending), JMod (torsion)
- Cracked beams: `0.35EI` (ASCE 41 Table 10-5)
- Cracked columns: `0.70EI` (depending on axial load)
- Cracked walls: `0.50EI`

The parser now extracts modifiers from the section properties table and stores them
on each ``Section.modifiers`` dict.  The builder applies them for elastic builds
(``use_elastic_sections=True``) by scaling ``A``, ``I33``, ``I22``, and ``J`` before
calling ``ops.section('Elastic', ...)``.  Modifiers are **skipped** for nonlinear
analyses (``create_fiber_sections=True``) since material/element formulations model
cracking directly.

#### Rigid Diaphragms

SAP2000 creates rigid floor diaphragms via `CONSTRAINT DEFINITIONS -
DIAPHRAGM` + `JOINT CONSTRAINT ASSIGNMENTS`.  The parser stores
diaphragm data in `SAPModelData.constraints`, but the builder does not
call `ops.rigidDiaphragm()`.  This means lateral load distribution
depends on in-plane slab stiffness, which:
- Is stiffer than a rigid diaphragm for thick slabs.
- Is softer than a rigid diaphragm for thin slabs with fine meshes.
- Does not match SAP2000 results for models where SAP2000 uses rigid
  diaphragms by default.

#### Convergence Fallback Strategies

The builder supports `Newton`, `ModifiedNewton`, `NewtonLineSearch`,
and `KrylovNewton` solvers, configurable via `config.solver.algorithm`.
However, there is **no automatic fallback** — if Newton fails, the
analysis stops.

Best practice (used by Perform3D and recommended in the OpenSees
community) is to try increasingly robust algorithms:

1. Newton (fastest)
2. NewtonLineSearch (if Newton fails)
3. ModifiedNewton (if LineSearch fails)
4. KrylovNewton (most robust)

The ``analysis_mode`` config should include an optional
``auto_fallback: true`` flag that implements this chain.

#### Modal Pushover Load Pattern

The toolkit supports ``'uniform'``, ``'triangular'``, and ``'mode1'``
pushover patterns.  The ``'mode1'`` pattern applies loads proportional
to mass × eigenvector, implemented via ``_compute_mode_shape_lateral_loads()``.
This is the ASCE 41 recommended modal pattern.

#### Concrete Confinement (Cover vs. Core)

The fiber sections created for pushover analysis use a single material
across the entire section.  RC columns require separation between
**confined core** (Concrete01/02 with enhanced strength/ductility from
transverse steel) and **unconfined cover** (Concrete01/02 with spalling
at εc ≈ 0.004).  This is implemented via concentric ``patch``
commands in the fiber section definition — the toolkit does not yet
do this, which overestimates column ductility.

#### Equation Numbering and Solver Selection

Michael H. Scott's OpenSees blog (Portwood Digital) emphasises that
equation numbering order can significantly impact solver performance.
The builder uses ``numberer RCM`` (Reverse Cuthill-McKee) throughout —
no ``numberer Plain`` usage remains in the codebase.

## 3.8 Storey Force Reconstruction (Implemented)

A general section-cut approach was designed but the toolkit currently implements
two complementary methods for storey-level force and moment profiles:

**Method 1 — Nodal summation (`storey_shears` in `storey_response.py`)**
Groups element-end nodes by storey (via `assign_nodes_to_storeys`) and sums
element-end forces at all nodes belonging to each level.  This is essentially
**Option B** — the free-body diagram of the portion above each storey.
Implemented and used for per-load-case force profiles.

**Method 2 — Trapezoidal reconstruction (`plot_storey_forces` in `plotting/report.py`)**
Reconstructs an equivalent distributed load from base shear and base moment,
then evaluates V(z) and M(z) analytically at 100 interior points for smooth
continuous curves.  This is **Option C** — fast, no additional analysis pass,
and automatically satisfies V = dM/dz.

**Not implemented** — Option A (element-end force interpolation at an arbitrary
cut elevation) was explored but not needed because Method 2 provides
sufficient accuracy for global reporting.  If element-level verification at a
specific elevation is required, use `extract_static_element_forces()` +
`storey_shears()` directly.

---

## 3.9 Two-Stage Pipeline (Preprocessor + AnalysisBuilder)

This is the single most significant architectural decision **not captured
in the original proposal**.  It was implemented in Phase C of the toolkit
development.

### Motivation

The original ``AnalysisBuilder`` (legacy single-stage path) did all
topology work (frame splitting, area meshing, node merging, edge
detection) **every time** a builder was created.  Each analysis case
(static, modal, pushover) repeated the same expensive operations.

The two-stage pipeline splits this:

```
Stage 1 — Preprocessor (runs ONCE):
    SAPModelData ──→ Preprocessor ──→ MeshModel
                          │
                          ├── split_elements
                          ├── mesh_area_elements
                          ├── merge_coincident_nodes
                          ├── split_frames_at_shell_nodes
                          ├── subdivide_shells
                          └── detect_constraint_edges (opt-in)

Stage 2 — AnalysisBuilder (runs per analysis case):
    MeshModel ──→ AnalysisBuilder ──→ OpenSees domain
                          │
                          ├── build_domain()   ← lightweight, fast
                          ├── run_static_analysis()
                          ├── run_modal_analysis()
                          ├── run_pushover_analysis()
                          └── run_response_spectrum_analysis()
```

### Key design points

- **``MeshModel``** is a frozen, serialisable dataclass holding all
  topology, materials, sections, loads, and results metadata.  It is
  created once by the Preprocessor and reused by every AnalysisBuilder.
- **``AnalysisBuilder``** assumes the MeshModel is fully pre-processed.
  It reads ``split_elements``, ``create_shells``, etc. from config but
  only to control which subsets of the MeshModel to realise in OpenSees
  — it never repeats topology work.
- **``build_domain()``** calls ``ops.wipe()`` then recreates all nodes,
  elements, materials, and sections from the MeshModel.  Each
  AnalysisBuilder owns its own OpenSees domain; multiple builders can
  exist concurrently with different configs (e.g. elastic vs. fiber).
- **The legacy ``AnalysisBuilder``** is still present for backward
  compatibility but is deprecated for new development.

### Current status

| Component | Location | Status |
|---|---|---|
| ``Preprocessor`` | ``opensees/preprocessor.py`` | ✅ Complete |
| ``MeshModel`` | ``model/mesh_model.py`` | ✅ Complete |
| ``AnalysisBuilder`` | ``opensees/analysis_builder.py`` | ✅ Complete |
| ``run_modal()`` (standalone) | ``opensees/analysis_builder.py`` | ✅ Complete |
| ``run_pushover_4dir()`` | ``opensees/pushover.py`` | ✅ Complete |
| ``run_linear_cases()`` | ``io/report.py`` | ✅ Complete |
| ``run_all()`` (example orchestration) | ``local/…/pumphouse_report_v2.py`` | ⚠️ Project-specific, not generalised |

---

## 4. Implementation Roadmap

The issues identified in sections 3.1–3.7 are prioritised below.  The
priority reflects impact on correctness, frequency of occurrence, and
dependency on other items.

| Priority | Issue | Section | Effort | Why now / later | Status |
|----------|-------|---------|--------|-----------------|--------|
| **P0** | Cardinal point + offset merging | 3.5, 3.4 | Small | Sections positioned incorrectly for RC beams; affects loads, spans, visualisation. Cardinal point parsed from FRAME SECTION ASSIGNMENTS; offsets combined with longitudinal end offsets at parse time per E2K approach. | ✅ Done |
| **P1** | Frame-to-shell drilling DOF | 3.2 | Medium (builder change) | Causes stiffness singularities in combined frame-shell models. Multiple well-known workarounds exist. | ❌ Pending |
| **P2** | Auto line constraints | 3.1/3.2 | Medium (builder change) | Mesh-density transitions (wall ↔ slab, frame ↔ slab) need automatic detection and MPC application. Implemented as `find_constraint_edges()` — sorted-tuple edge registry + sweep-line chain following. Returns 46 edges for the Admin Building. | ✅ Done |
| **P3** | Joint modelling — Level 2 | 3.4 | Medium (builder change) | Enables semi-rigid connection modelling. Level 1 (rigid offset) exists; Level 2 replaces stiff links with calibrated zero-length springs (flexibility %). | ❌ Pending |
| **P4** | Effective stiffness modifiers | 3.7 | Small | ASCE 41 cracked sections: 0.35EI beams, 0.70EI columns. AMod/I3Mod/I2Mod/JMod parsed from section properties; applied in builder for elastic builds only (skipped for nonlinear fiber sections). | ✅ Done |
| **P5** | Rigid diaphragms | 3.7 | Medium (builder change) | Lateral load distribution differs from SAP2000. Parser stores constraint data, builder never calls `ops.rigidDiaphragm()`. | ❌ Pending |
| **P6** | P-Delta geom. transformation | 3.7 | Small | Pushover config sets `geom_transf_type = 'PDelta'` via `rebuild_with_fiber_sections()`, but the `brace_selection` argument is never wired from `run_pushover_4dir`.  The code path is **dormant** — see docstring in `analysis_builder.py` for details. | ⚠️ Dormant |
| **P7** | Convergence fallback | 3.7 | Medium (builder change) | Auto-retry chain (Newton → LineSearch → ModifiedNewton → KrylovNewton). Prevents analysis failure on marginally nonlinear models. | ❌ Pending |
| **P8** | Concrete confinement | 3.7 | Medium (builder change) | Fiber sections overestimate column ductility without unconfined cover layer. Mander confinement formula fixed: effective lateral stress `f_l` now factored by confinement effectiveness coefficient `ke`. | ✅ Done |
| **P9** | Modal pushover pattern | 3.7 | Small | Implemented as `lateral_load_type='mode1'` with `_compute_mode_shape_lateral_loads()`. | ✅ Done |
| **P10** | Equation numbering (RCM) | 3.7 | Small | `ops.numberer('RCM')` used throughout — no `numberer Plain` remaining. | ✅ Done |
| **P11** | Damping for dynamic | 3.7 | Medium (when added) | Rayleigh coefficients from target ζ at two frequencies. Not needed until `nonlinear_dynamic`. | ❌ Pending |
| **—** | CI pipeline (verification suite) | 3.6 | Medium (CI setup) | Run `runVerificationSuite.tcl` automatically to catch regressions from toolkit changes. | ❌ Pending |
| **R1** | Generalised orchestrator `generate_report()` | 5 | Medium | Created ``fea_toolkit/report.generate_report(md, mesh_model, config, out_dir, **overrides) → dict``.  Extracts the generic pipeline orchestration from ``pumphouse_report_v2.run_all()``.  The project-specific script is now a thin wrapper. | ✅ Done |
| **R2** | Tcl export from MeshModel | 8 | Medium | ``export_mesh_model_to_tcl()`` added to ``recorder.py`` — emits topology + fiber sections directly from ``MeshModel``, using pre-computed tag maps.  Supports elastic, steel fiber, and RC fiber sections. | ✅ Done |
| **R3** | HPC job submission + result ingest | — | Large | Helper to submit Tcl to Xara (OpenSeesMP), wait for completion, parse output back into the unified NPZ/HDF5 schema.  Required for `admin_nonlinear.py`. | ❌ Pending |
| **P0-dyn** | Tcl export for nonlinear RC | — | Medium | Nonlinear RC cannot run in OpenSeesPy. `export_model_to_tcl()` emits fiber sections + analysis commands. | ⚠️ Partial |
| **P1-dyn** | Nonlinear dynamic analysis | — | Large | Ground-motion input, Newmark/HHT integrator, time-history output. Requires P0-dyn. | ❌ Pending |
| **P2-dyn** | Validation suite | 3.6 | Ongoing | Collect PEER 2017/03, PEER 2015/01, Scott & Fenves (2006), SAC Steel benchmarks as regression tests. | ❌ Pending |

### Damping-specific note

Damping is deliberately **not a priority** until nonlinear dynamic
analysis (P0-dyn or P1-dyn) is started, because:
- Static and pushover analyses are unaffected by damping.
- Modal analysis computes undamped eigenvalues — damping is irrelevant.
- Response spectrum analysis handles damping parametrically via CQC
  coefficients and spectrum scaling factors.
- Only transient dynamic analysis needs Rayleigh damping in the model.

When dynamic analysis is implemented, Rayleigh damping coefficients
will be computed as:

```
α₀ = 2ζ · ω₁ω₂ / (ω₁ + ω₂)
α₁ = 2ζ / (ω₁ + ω₂)
```

where ω₁ and ω₂ are characteristic frequencies (typically the
first-mode and third-mode frequencies from the preceding modal
analysis), and ζ is the target damping ratio (typically 0.02 for
steel, 0.05 for concrete).

---

## 5. Orchestrator: `generate_report()`

```python
# src/fea_toolkit/report.py  (proposed)

def generate_report(config_path: str):
    """Run the full pipeline defined in *config_path* and produce a report.

    Steps
    -----
    1. Load and validate the YAML config.
    2. Parse the model (``SAP2000Parser``).
    3. Detect storeys (``identify_stories``).
    4. For each enabled analysis:
       a. Build the OpenSees model (``AnalysisBuilder.build_domain``).
       b. Run the analysis (static / modal / RS / pushover).
       c. Post-process storey responses.
       d. Write results to HDF5.
    5. Render the report from a Quarto template.
    """
```

### Pseudocode

```python
def generate_report(config_path: str):
    import yaml
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    store = HDF5Store(cfg["output"]["hdf5_path"])

    # ── Phase 0: Parse ────────────────────────────────────────────
    parser = SAP2000Parser(cfg["model"]["path"])
    parser.parse()
    md = parser.get_model_data()
    # Units come from the .s2k file (e.g. N, mm, C).  No conversion
    # is applied at this stage — the model stays in its native units.
    # Future: optional output_units in config would trigger conversion
    # here or at the builder boundary.

    # ── Phase 1: Storeys ──────────────────────────────────────────
    stories = identify_stories(md, raw_tables=parser.raw_tables,
                               method=cfg["storeys"]["method"],
                               z_tolerance=cfg["storeys"]["z_tolerance"])
    store.write_dataframe("storeys", "summary", stories_dataframe(stories))

    # ── Phase 2: Analyses ─────────────────────────────────────────
    # Global builder defaults (applied to every analysis unless overridden)
    global_builder = cfg.get("builder", {})

    for analysis_name, analysis_cfg in cfg["analyses"].items():
        if not analysis_cfg.get("enabled", False):
            continue

        # Merge per-analysis builder overrides on top of global defaults
        analysis_builder = dict(global_builder)
        analysis_builder.update(analysis_cfg.get("builder", {}))
        builder = AnalysisBuilder(mm, analysis_builder)
        builder.build()

        if analysis_name == "static":
            results = run_linear_cases(builder, cfg)
            store_static_results(store, results)
            if cfg["storey_response"]["enabled"] and results:
                for case_name, case_data in results.items():
                    sr = compute_linear_storey_responses(
                        builder, stories, case_data, ...)
                    store.write_dataframe(
                        f"static/{case_name}", "storey_response", sr)

        elif analysis_name == "modal":
            modal = builder.run_modal_analysis(
                n_modes=analysis_cfg.get("n_modes", 12))
            store_modal_results(store, modal)
            if cfg["storey_response"]["enabled"]:
                md_drifts = modal_storey_drifts(md, stories, modal, ...)
                store.write_dataframe("modal", "storey_drifts", md_drifts)

        elif analysis_name == "response_spectrum":
            rs = builder.run_response_spectrum_analysis(
                spectrum=analysis_cfg.get("spectrum"),
                damping=analysis_cfg.get("damping", 0.05),
                n_modes=analysis_cfg.get("n_modes", 12))
            store_rs_results(store, rs)

        elif analysis_name == "pushover":
            po = run_pushover_workflow(builder, md, analysis_cfg)
            store_pushover_results(store, po)

    # ── Phase 3: Render report ────────────────────────────────────
    render_report(cfg["output"]["report"], store)
```

---

## 6. Report Rendering

The rendering step takes the HDF5 archive and a Quarto template and
produces the final output.

### Option A: Quarto Parameterised Report (Recommended for now)

A single `report_template.qmd` with YAML parameters that point to the
HDF5 file and select which sections to include:

```yaml
---
title: "Structural Evaluation Report"
params:
  hdf5_path: "results.h5"
  sections: ["model_summary", "storey_plan", "modal_results"]
  author: "Engineer Name"
format:
  html:
    toc: true
    code-fold: true
---
```

```python
#| label: load-results
import pandas as pd
store = HDF5Store(params.hdf5_path)
df_modal = store.read_dataframe("modal", "participation")
df_storeys = store.read_dataframe("storeys", "summary")
```

Python cells read directly from HDF5 and produce figures/tables using the
existing `plotting/report.py` and `io/report.py` functions.

### Option B: Programmatic HTML Generation (Future)

For a fully automated pipeline, use `quarto render` programmatically:

```python
import subprocess
subprocess.run([
    "quarto", "render", "report_template.qmd",
    "-P", f"hdf5_path={hdf5_path}",
    "-P", "sections=model_summary,modal_results",
    "--to", "html",
], check=True)
```

This can be wrapped into `render_report()`.

---

## 7. Migration Path from Current QMD Prototype

The existing `local/` reports (e.g. `admin_report.qmd`, `pumphouse_report_v2.qmd`)
contain inline Python cells that combine computation and presentation.
The migration strategy:

| Step | What changes | Status |
|---|---|---|
| **1. Adopt Python-dict config** | The proposed YAML config was replaced by a Python ``_DEFAULT_CONFIG`` dict in each report script.  This is more flexible for programmatic use and avoids a YAML dependency. | ✅ Done (see ``pumphouse_report_v2.py``) |
| **2. Add HDF5 storage** | ``unified_writer._write_h5()`` + ``npz_reader.read_results_hdf5()`` implemented.  Uses flat dict-of-arrays (same schema as NPZ). | ✅ Done |
| **3. Generalise orchestration** | Extract pipeline orchestration from ``pumphouse_report_v2.run_all()`` into ``fea_toolkit/report.py`` as ``generate_report()``. | ✅ Done (R1) |
| **4. Refactor QMD** | Replace inline computation cells with HDF5 read + plot/table cells. | ⚠️ Partially done (``pumphouse_report_v2.qmd`` reads cached results) |
| **5. Parametrise template** | Add ``params`` block to QMD header; make it read ``params.hdf5_path``. | ❌ Pending |
| **6. Build orchestrator** | Create ``generate_report()`` that calls parse → preprocess → analyse → store → render. | ✅ Done (R1) |
| **7. CLI entry point** | Add ``fea-toolkit report config.yaml`` console script. | ❌ Pending |

---

## 8. Parser Expansion: FUNCTION Tables

The current parser (`s2k_parser.py`) does **not** parse `FUNCTION - *` tables,
which contain the actual curve data for response spectra and ground motions.
These need to be added before the config-driven pipeline can fully use
spectrum/ground-motion data from the .s2k file.

### Tables to parse

| SAP2000 table | Content | Purpose |
|---|---|---|
| `FUNCTION - RESPONSE SPECTRUM` | Period-acceleration pairs for user-defined spectra | Spectrum definition for RS analysis |
| `FUNCTION - TIME HISTORY` | Time-acceleration pairs for ground-motion records | Input for nonlinear dynamic analysis |
| `FUNCTION - RESPONSE SPECTRUM - USER DEFINED` | Alternative format for custom spectra | Same as above, different table structure |

### Data model (proposed additions to `sap_data.py`)

```python
@dataclass
class ResponseSpectrumFunction:
    """A response spectrum function definition from the .s2k file."""
    name: str
    function_type: str           # "USER_DEFINED" | "GB_50011" | "UBC" | etc.
    periods: List[float]         # period axis (s)
    accelerations: List[float]   # spectral acceleration (in model accel units)
    damping: float = 0.05        # damping ratio associated with the function
    parameters: Dict[str, Any] = field(default_factory=dict)
    # For code-based spectra: SeismicCoeff, SiteClass, Importance, etc.


@dataclass
class TimeHistoryFunction:
    """A ground-motion time-history function from the .s2k file."""
    name: str
    time: List[float]            # time axis (s)
    acceleration: List[float]    # acceleration (in model accel units)
    dt: float = 0.01
    npts: int = 0
```

### Parser integration

Add extraction calls in `get_model_data()`:

```python
def _get_response_spectrum_functions(self) -> Dict[str, ResponseSpectrumFunction]:
    ...

def _get_time_history_functions(self) -> Dict[str, TimeHistoryFunction]:
    ...
```

Store results as `SAPModelData.response_spectrum_functions` and
`SAPModelData.time_history_functions` (both defaulting to empty dicts).

### Config → parser flow

```
.s2k file
  ├── TABLE: "FUNCTION - RESPONSE SPECTRUM"  ──→  ResponseSpectrumFunction  ──→  config source: "from_model"
  ├── TABLE: "FUNCTION - TIME HISTORY"        ──→  TimeHistoryFunction       ──→  config source: "from_model"
  └── TABLE: "AUTO SEISMIC ..."              ──→  LoadPattern.auto_data     ──→  code-based spectrum generation
```

When the config says `source: "from_model"`, the orchestrator:
1. Looks for a `ResponseSpectrumFunction` matching the RS case's function name
2. If found, uses its period-acceleration pairs directly
3. If not found, checks for AUTO SEISMIC data and generates the code-based spectrum
4. Falls back to the explicit config spectrum if neither is available

---

## 9. Dependency Considerations

| Component | Dependency | Notes |
|---|---|---|
| Config parsing | `pyyaml` or `tomli` | YAML is more human-readable for structural engineers |
| HDF5 storage | `h5py` or `pandas.HDFStore` | `h5py` gives more control over array layout |
| Quarto rendering | `quarto` CLI | Must be installed separately (already in workflow) |
| Report assembly | `nbformat` + `nbconvert` | Optional — for programmatic notebook assembly |

---

## 10. Open Questions

1. **Incremental re-analysis** — If the config changes only the report
   layout (not the analysis parameters), should we skip re-analysis and
   just re-render from the existing HDF5?  (A hash of the analysis
   config stored in HDF5 metadata would detect changes.)

2. **Multiple models** — Should a single config support comparing
   multiple model variants (e.g. as-built vs. retrofitted)?

3. **Dynamic analysis** — The HDF5 schema reserves a ``/dynamic/``
   group.  What ground-motion input format should we target
   (PEER NGA, simple 2-column text, etc.)?

4. **Report templates** — Should there be a "quick" template (single
   HTML file) vs. a "detailed" template (PDF with appendix)?  Or one
   template with conditional sections?

5. **Unit conversion** — Units are read from the source model's `.s2k`
   file and no conversion is applied.  If a user needs results in
   different units (e.g. model in N·mm, report in kN·m), should an
   optional ``output_units`` config key trigger automatic conversion
   at the HDF5-write or report-read stage?

6. **FUNCTION table format variants** — SAP2000 exports response-spectrum
   and time-history functions in several table formats depending on the
   version and whether they are code-based or user-defined.  Are there
   known format variations we need to handle (e.g. `FUNCTION - RESPONSE
   SPECTRUM` vs. `FUNCTION - RESPONSE SPECTRUM - USER DEFINED`)?
