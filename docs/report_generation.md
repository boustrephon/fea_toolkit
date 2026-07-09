# Report Generation — Design Proposal

## Overview

The goal is a **configuration-driven** report pipeline that:

1. Reads a YAML config specifying which analyses to run and how
2. Runs the full pipeline (parse → enrich → build → analyse)
3. Stores all results in a single **HDF5** archive
4. Generates a self-contained report (HTML or PDF) from a **Quarto template**

This separates **computation** (analysis execution + data storage) from
**presentation** (report rendering), so you can re-render a report from
cached results without re-running analyses.

---

## 1. Configuration File (`config.yaml`)

The config defines the model source, **analysis mode** (which drives
default element/material/solver choices), analysis-specific parameters,
and report layout.  It can be edited directly or generated interactively.

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
> Nonlinear RC analysis (fiber sections with ``Concrete01/02``,
> ``Steel02``, or ``forceBeamColumn`` with ``HingeRadau``) is **not
> supported in OpenSeesPy** builds.  Any analysis requiring nonlinear
> materials — pushover with RC fiber sections, nonlinear dynamic, or
> brace buckling with ``Hysteretic`` materials — must be **exported to
> Tcl** and executed via the standalone OpenSees build bundled with
> Xara (``tclsh8.6``).  The toolkit provides two export paths:
>
> - ``RecordingOpenSees`` — records all ``ops.*`` calls during a
>   Python build, then saves them as a Tcl script.  This only works for
>   **elastic** builds in Python (nonlinear sections cannot be created).
> - ``export_model_to_tcl()`` — translates ``SAPModelData`` directly to
>   Tcl commands, skipping the Python build entirely.  Accepts
>   ``tcl_prefix`` and ``tcl_suffix`` to inject nonlinear material
>   definitions, fiber sections, and custom analysis commands.
>
> The practical pipeline for nonlinear analyses is therefore:
>
> ```
> SAPModelData  ──→  export_model_to_tcl()  ──→  model.tcl
>                                                     │
>                   User-provided Tcl snippets ───────┤
>                   (fiber sections, materials,        │
>                    analysis commands)                │
>                                                     ▼
>                                           XaraTclRunner.run()
>                                           (standalone tclsh8.6)

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
#   "linear_static"      - elasticBeamColumn, elastic sections, fast
#   "modal"              - same as linear_static (mass + stiffness only)
#   "response_spectrum"  - elastic (same as linear_static, plus CQC)
#   "pushover"           - nonlinearBeamColumn, fiber sections,
#                          corotational geometry,
#                          displacement-controlled loading
#   "nonlinear_dynamic"  - nonlinearBeamColumn, fiber sections,
#                          Newmark integrator,
#                          ground-motion input (future)
#
# Individual overrides can be specified in ``builder`` below.
analysis_mode: "pushover"

# ── Builder Overrides ───────────────────────────────────────────────
# These override the analysis-mode defaults.  Omit any key to accept
# the default for the chosen analysis_mode.
builder:
  element_type: null           # null = auto based on analysis_mode
  create_shells: true
  brace_type: null             # null = auto: "truss" for pushover,
                               #         "beam" for linear
  solver: null                 # null = auto based on analysis_mode

# ── Storey Detection ─────────────────────────────────────────────────
storeys:
  method: "auto"                # "auto" | "s2k_table" | "diaphragm" | "area_elements" | "node_clustering"
  z_tolerance: 0.5

# ── Analyses ─────────────────────────────────────────────────────────
analyses:
  static:
    enabled: true
    cases: null                 # null = auto-detect from model; or ["DEAD", "LIVE", "WIND+X"]

  modal:
    enabled: true
    n_modes: 12

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

  pushover:
    enabled: false
    directions: ["X", "Y"]
    patterns: ["uniform", "triangular"]
    max_drift: 0.05
    n_steps: 100
    spectrum: null              # optional override for CSM

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
      - conclusions
```

---

## 2. HDF5 Schema (`results.h5`)

All analysis results are stored under a single HDF5 file with a
hierarchical structure.  Each group contains the raw NumPy arrays
and Pandas DataFrames needed for post-processing and plotting.

```
/results.h5
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

### Python class for HDF5 storage

```python
# src/fea_toolkit/io/hdf5_store.py  (proposed)

class HDF5Store:
    """Read/write analysis results to a hierarchical HDF5 archive."""

    def __init__(self, path: str):
        self.path = path

    def write_dataframe(self, group: str, key: str, df: pd.DataFrame):
        """Store a DataFrame under ``/group/key``."""

    def read_dataframe(self, group: str, key: str) -> pd.DataFrame:
        """Restore a DataFrame from ``/group/key``."""

    def write_array(self, group: str, key: str, arr: np.ndarray):
        """Store a NumPy array."""

    def read_array(self, group: str, key: str) -> np.ndarray:
        """Restore a NumPy array."""

    def write_json(self, group: str, key: str, data: dict):
        """Store a JSON-serialisable dict."""

    def list_groups(self) -> List[str]:
        """List all top-level groups."""
```

Implementation uses `h5py` or `pandas.HDFStore` under the hood, with
JSON serialisation for dicts via `json.dumps` as string attributes.

### HDF5 ↔ Visualisation Tools

The HDF5 archive is **not intended as a direct input to visualisation
tools**.  Instead, it sits in the middle of the pipeline:

```
                          ┌──────────────────┐
                          │   HDF5 Archive   │  ← single source of truth
                          │   (results.h5)   │
                          └────────┬─────────┘
                                   │
                   ┌───────────────┼────────────────┐
                   │               │                │
                   ▼               ▼                ▼
           ┌───────────────┐ ┌──────────┐ ┌──────────────┐
           │   NPZ Export  │ │  Report  │ │  Adapters    │
           │ (Rhino/PyVista│ │  Gen.    │ │ (XDMF, ODB,  │
           │  exchange)    │ │ (Quarto) │ │  future)     │
           └───────┬───────┘ └──────────┘ └──────────────┘
                   │
                   ▼
           ┌───────────────┐
           │  plot_model_3d│  ← unchanged — still reads NPZ
           │  colour_from_ │
           │  npz()        │
           └───────────────┘
```

| Visualisation path | Current format | HDF5 relationship |
|---|---|---|
| **PyVista 3D** (`plot_model_3d`, `plot_deformed_3d`, etc.) | NPZ (via `_load_npz_for_plotting`) | A lightweight `hdf5_to_npz()` converter exports HDF5 → NPZ for the existing plotting functions. No plotting code changes needed. |
| **Rhino colouring** (`colour_from_npz`) | NPZ | Same converter path. Rhino's CPython doesn't bundle `h5py`, so NPZ remains the Rhino exchange format. |
| **Matplotlib report plots** (`plot_storey_forces`, `plot_modal_participation`, etc.) | Pandas DataFrames | The `HDF5Store.read_dataframe()` method returns the same structures — report plotting functions work unchanged. |
| **Opstool / opsVis** | Custom ODB format | Not directly compatible. A future `export_to_opstool_odb()` could convert if needed. |
| **ParaView** | VTK / XDMF + HDF5 | A future `export_to_xdmf()` could write an XDMF descriptor pointing to the HDF5 arrays. |

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
  geometry need more robust handling.
- **Line constraints between meshes**: Where floor slabs meet walls or
  where two super-elements of different mesh density meet (e.g. a
  finely-meshed core wall adjacent to a coarsely-meshed floor slab),
  edge nodes must be tied via ``equationConstraint`` so the mesh can
  remain rectangular on each zone independently.  The builder's
  ``apply_edge_constraints()`` addresses this.
- **Gmsh-based remeshing**: The optional ``mesh/remesh.py`` module
  provides constrained quadrilateral remeshing for non-rectilinear
  geometries, but integration into the automated workflow is not yet
  complete.

### 3.2 Frame-to-Shell Integration

Frame elements bring concentrated stiffness to the nodes of distributed
shell elements, which creates two difficulties:

- **Drilling moment (Rz) compatibility**: ``ShellMITC4`` has no drilling
  DOF stiffness — it constrains only 5 DOFs (ux, uy, uz, rx, ry).
  Where a frame element delivers a torsional moment to a shell node,
  the missing rotational stiffness causes local singularities or
  unrealistic force distributions.
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
| **0 — Simple point** | Members meet at a node, flex from that point. Vertical offsets (storey-level) may apply. | ✅ Implemented via ``FrameElement.offset_a`` / ``offset_b`` |
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
reference line.  Common settings:

- **RC beams**: cardinal point 5 (top‑centre) — the beam's top face
  aligns with the storey level (which is typically at the top-of-slab).
- **RC columns**: cardinal point 5 (top‑centre) or 11 (centroid).
- **Steel beams**: cardinal point 4 (bottom‑centre) or 10 (centroid).

The insertion point combined with the section depth defines the
**offset** from the reference line to the member's extreme fibre.
These offsets affect:

- **Clear span** for flexure (shorter than centreline distance).
- **Gravity load take-down** — the slab sits on the beam's top flange,
  not at its centroid.
- **Clash detection** in visualisation — cardinal points determine
  where sections appear in 3D views.

**Current status**: ``FrameElement`` has ``offset_a`` / ``offset_b``
fields (parsed from the `FRAME END OFFSETS` table), but cardinal point
data is not yet extracted from the `FRAME SECTION PROPERTIES` /
`FRAME ASSIGNMENTS` tables.  This needs to be addressed.

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

The builder currently uses `geomTransf Linear` for all frame elements,
meaning **no P-Delta effects** are captured.  SAP2000 offers P-Delta as
a configurable analysis-case option.

- `geomTransf PDelta` — captures second-order (P-Δ) effects via
  geometric stiffness (recommended for most pushover analyses).
- `geomTransf Corotational` — large-displacement formulation (needed
  for buckling or highly flexible structures).

The ``analysis_mode`` auto-config should select `PDelta` for pushover
and `Corotational` for brace buckling.

#### Effective Stiffness Modifiers (ASCE 41)

SAP2000 stores stiffness modifiers in `FRAME ASSIGNMENTS`:
- Cracked beams: `0.35EI` (ASCE 41 Table 10-5)
- Cracked columns: `0.70EI` (depending on axial load)
- Cracked walls: `0.50EI`

The `.s2k` parser extracts `FrameAssignments.modifiers` but the builder
**does not apply them** — all sections use full elastic stiffness.

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

The toolkit currently supports `uniform` and `triangular` pushover
patterns.  SAP2000 and ASCE 41 also recommend a **modal** pattern
proportional to the fundamental mode shape times mass.

In OpenSees this is done via:
```
pattern Plain 2 1 {
    load $nodeTag [expr $mass * $eigenvector_x]
    load $nodeTag [expr $mass * $eigenvector_y]
}
```

This should be added as a third pushover pattern option.

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
The builder uses `numberer Plain` (sequential by node tag).  For large
models, `numberer RCM` (Reverse Cuthill-McKee) reduces matrix bandwidth
and can improve solver speed by 2–10×.  The ``analysis_mode`` config
should auto-select `RCM` for models with >1000 nodes.

---

## 4. Implementation Roadmap

The issues identified in sections 3.1–3.7 are prioritised below.  The
priority reflects impact on correctness, frequency of occurrence, and
dependency on other items.

| Priority | Issue | Section | Effort | Why now / later |
|---|---|---|---|---|
| **P0** | Cardinal point parsing | 3.5 | Small (parser only) | Sections positioned incorrectly for RC beams; affects loads, spans, visualisation. Data already in .s2k, just not read. |
| **P1** | Frame-to-shell drilling DOF | 3.2 | Medium (builder change) | Causes stiffness singularities in combined frame-shell models. Multiple well-known workarounds exist. |
| **P2** | Effective stiffness modifiers | 3.7 | Small (builder change) | ASCE 41 cracked sections: 0.35EI beams, 0.70EI columns. Parser has the data, builder ignores it. |
| **P3** | Rigid diaphragms | 3.7 | Medium (builder change) | Lateral load distribution differs from SAP2000. Parser stores constraint data, builder never calls `ops.rigidDiaphragm()`. |
| **P4** | P-Delta geometric transformation | 3.7 | Small (config change) | Pushover and buckling need `PDelta`/`Corotational`. Currently only `Linear` is used. |
| **P5** | Convergence fallback | 3.7 | Medium (builder change) | Auto-retry chain (Newton → LineSearch → ModifiedNewton → KrylovNewton). Prevents analysis failure on marginally nonlinear models. |
| **P6** | Concrete confinement (cover vs. core) | 3.7 | Medium (builder change) | Fiber sections overestimate column ductility without unconfined cover layer. |
| **P7** | Modal pushover pattern | 3.7 | Small (workflow change) | Third pattern option: `load = mass × eigenvector`. Required by ASCE 41. |
| **P8** | Equation numbering (RCM) | 3.7 | Small (builder change) | `numberer RCM` gives 2–10× speedup for >1000 nodes. Single flag change. |
| **P9** | Damping for dynamic analysis | 3.7 | Medium (when dynamic is added) | Rayleigh coefficients from target ζ at two frequencies. Not needed until `nonlinear_dynamic` mode is implemented. |
| **P0-dyn** | Tcl export for nonlinear RC | — | Medium | Nonlinear RC analysis cannot run in OpenSeesPy. ``export_model_to_tcl()`` exists but needs to emit fiber sections, ``Concrete01/02``, ``Steel02``, and analysis commands. |
| **P1-dyn** | Nonlinear dynamic analysis | — | Large | Ground-motion input, Newmark/HHT integrator, time-history output. Requires P0-dyn first. |
| **P2-dyn** | Validation suite | 3.6 | Ongoing | Collect PEER reports, OpenSees verification suite, Perform3D benchmarks as regression tests. |

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
       a. Build the OpenSees model (``OpenSeesBuilder.build``).
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
    stories = identify_stories(md, raw_tables=parser._raw_tables,
                               method=cfg["storeys"]["method"],
                               z_tolerance=cfg["storeys"]["z_tolerance"])
    store.write_dataframe("storeys", "summary", stories_dataframe(stories))

    # ── Phase 2: Analyses ─────────────────────────────────────────
    analysis_mode = cfg.get("analysis_mode", "linear_static")
    builder_cfg = build_builder_config(analysis_mode, cfg.get("builder", {}))

    for analysis_name, analysis_cfg in cfg["analyses"].items():
        if not analysis_cfg.get("enabled", False):
            continue

        builder = OpenSeesBuilder(md, builder_cfg)
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
])
```

This can be wrapped into `render_report()`.

---

## 7. Migration Path from Current QMD Prototype

The existing `local/` reports (e.g. `admin_report.qmd`, `pumphouse_report.qmd`)
contain inline Python cells that combine computation and presentation.
The migration strategy:

| Step | What changes |
|---|---|
| **1. Separate config** | Extract model path, analysis options, and report settings into a standalone `config.yaml` |
| **2. Add HDF5 storage** | Write a lightweight `HDF5Store` class; save results after each analysis |
| **3. Refactor QMD** | Replace inline computation cells with HDF5 read + plot/table cells |
| **4. Parametrise template** | Add `params` block to QMD header; make it read `params.hdf5_path` |
| **5. Build orchestrator** | Create `generate_report()` that calls YAML → parse → analyse → store → render |
| **6. CLI entry point** | Add `fea-toolkit report config.yaml` console script |

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
