# fea_toolkit

A toolkit for importing, analysing, and visualising structural
engineering models.  Parses SAP2000 (`.s2k` / JSON), builds OpenSees
models for structural analysis, and exports to Rhino 3-D for
visualisation and Grasshopper workflows.


## Project Summary

### Overview

The goal is to create a Python package `fea_toolkit` that:

- Parses SAP2000 `.s2k` text files (and JSON exports) into a common
  intermediate data model (`SAPModelData`).
- Enriches section properties using a manufacturer database.
- Splits frame elements at joints (and optionally at frame intersections)
  with parent‑child tracking.
- Splits distributed loads (uniform, linear, trapezoidal) to match the
  sub‑elements.
- Builds OpenSees models with configurable element types
  (`elasticBeamColumn`, `forceBeamColumn`, etc.), applies loads, and runs
  **linear static**, **modal**, **response spectrum**, and **pushover**
  analysis.
- **3D-only analysis engine** — all analysis runs in the OpenSees 3D
  domain (`ndm=3`, `ndf=6`); planar ("2D") frames are modelled as planar
  3D models with out-of-plane restraints.  2D OpenSees analyses are used
  only in tests, never in the main workflow.
- **Exports to Rhino 8** for 3-D visualisation with lightweight extrusion
  solids, section-based layers, and FEA metadata for Grasshopper.

---

## LLM / AI Assistant Quick Start

This section is designed for **language model (LLM) assistants** working with
this codebase.  For a full reference, see [docs/llm_guide.md](docs/llm_guide.md).

### Canonical 4-line pipeline

```python
from fea_toolkit import SAP2000Parser, preprocess_model, AnalysisBuilder
md = SAP2000Parser("model.s2k").parse().get_model_data()
mesh = preprocess_model(md, {"element_type": "elasticBeamColumn"})
builder = AnalysisBuilder(mesh, {}).build_domain()
```

### Discover the full API

```python
import fea_toolkit
# Inspect: fea_toolkit.__all__, fea_toolkit.io.__all__, fea_toolkit.opensees.__all__, etc.
```

### Task shortcuts

| Goal | Call |
|------|------|
| Parse a model | ``SAP2000Parser(path).parse().get_model_data()`` |
| Filter elements | ``Selection(element_types=['Frame'], groups=['Lateral'])`` |
| Plot 3D model | ``plot_mesh(builder)`` |
| Plot deformed shape | ``plot_deformed_displacement_3d(builder, results, scale=100)`` |
| Plot force diagram | ``plot_force_diagram(builder, results, quantity='Mz')`` |
| Export to NPZ | ``from fea_toolkit.io import write_results_npz`` |
| Export to Tcl | ``from fea_toolkit.opensees import export_mesh_model_to_tcl`` |

### Key constraints (do not violate)

- Never hardcode ``9.81`` — use ``g_from_units(units)`` from ``fea_toolkit.utils``.
- Never use 8-arg trapezoidal ``eleLoad`` — broken in OpenSeesPy 3.8.0.0.
- ``Corotational`` geomTransf + ``eleLoad`` does not work in 3D.
- Preprocessor mutates topology. AnalysisBuilder reads frozen ``MeshModel`` — no mutation.

---

### Current Implementation State

#### 1. Package Structure (Modern `src/` layout) — abbreviated; see
the "Package tree (abbreviated)" section below for the full subpackage list

```
~/Projects/fea_toolkit/
├── data/                     # (private) section_dict.pkl
├── examples/                 # [`examples/README.md`](examples/README.md)
├── src/fea_toolkit/
│   ├── __init__.py
│   ├── io/
│   │   ├── s2k_parser.py     # SAP2000Parser with string IDs, numeric tags
│   │   ├── npz_writer.py     # Analysis results → NPZ archives
│   │   ├── npz_reader.py     # NPZ / HDF5 readers (unified, auto-detect)
│   │   ├── unified_writer.py # Combined NPZ/HDF5 writer
│   │   ├── model_codec.py    # Lossless dataclass ⟷ JSON codec (round-trip)
│   │   ├── stage_writer.py   # Self-describing model-stage file (SAP + mesh)
│   │   ├── stage_reader.py   # read_model_stages() lossless round-trip
│   │   ├── results_schema.py # NPZ key layout + SCHEMA_VERSION
│   │   ├── report.py         # pandas summary tables
│   │   └── helper.py         # File‑chooser utilities (tkinter/macOS)
│   ├── model/
│   │   ├── sap_data.py       # Dataclasses: Node, FrameElement, LoadPattern, JointLoad, FrameDistributedLoad, SAPModelData, and others
│   │   ├── sections.py       # SectionLibrary with unit conversion (mm/in)
│   │   ├── mesh_model.py     # Frozen post-preprocessor topology
│   │   ├── source_resolver.py # SAPModelData / MeshModel / AnalysisBuilder / stage-file → ResolvedSource
│   │   └── geometry.py       # SpatialGrid, point_on_segment, trapezoidal_force_split, split_elements (joint splitting + load redistribution)
│   ├── opensees/
│   │   ├── preprocessor.py   # Preprocessor: topology mutations → MeshModel
│   │   ├── analysis_builder.py  # AnalysisBuilder: OpenSees domain + analysis execution
│   │   └── builder.py        # Tcl export functions (export_model_to_tcl, etc.)
│   └── rhino/
│       ├── __init__.py       # Public API (RhinoImporter)
│       ├── colors.py         # SAP2000 colour conversion
│       ├── groups.py         # Rhino group creation
│       ├── importer.py       # Main RhinoImporter orchestrator
│       ├── layers.py         # Section-based layer hierarchy
│       └── geometry.py       # Centreline + extrusion geometry
├── tests/                    # pytest suite — [`tests/README.md`](tests/README.md)
├── pyproject.toml
└── README.md
```

#### 2. Key Components Implemented

| Component | Status | Notes |
| :--- | :--- | :--- |
| **`Selection`** | ✅ Complete | Flexible element filter: select by type (Frame/Area/Node), section, material, group membership, or ID. AND across criteria, OR within lists. Used to control which area loads are converted to edge loads in the builder. |
| **`SAP2000Parser`** | ✅ Complete | Parses .s2k into raw tables; converts to `SAPModelData` with string IDs; assigns numeric tags; extracts materials, sections, frame connectivity, restraints, load patterns, joint loads, distributed loads, auto‑mesh settings. |
| **`SAPModelData`** | ✅ Complete | Contains all model data with mutable defaults via `field(default_factory=...)`; includes `units` dict with default `{'F':'N','L':'m','T':'C'}`. |
| **`SectionLibrary`** | ✅ Complete | Loads section catalogue pickle; converts units to match model (`mm` or `in`); enriches `Section` objects with `Z33`, `Z22`, dimensions, etc. |
| **`geometry.split_elements`** | ✅ Complete | Splits elements at joints when `AtJoints=True`; marks parent as `inactive`; creates child elements with new numeric tags; redistributes distributed loads using `trapezoidal_force_split`; stores relative positions (`rdist_a`, `rdist_b`) in child loads. |
| **`Preprocessor`** | ✅ Complete | Topology-only mutations: element splitting, auto-meshing, end offsets, edge constraints — produces a frozen `MeshModel`. |
| **`AnalysisBuilder`** | ✅ Complete | Reads the frozen `MeshModel`, builds the OpenSees domain (nodes, restraints, elastic/nonlinear sections, elements, loads) and runs static/modal/RS/pushover analyses. |
| **Modal Analysis** | ✅ Complete | `run_modal_analysis()` — eigenvalue extraction (`ops.eigen`), modal properties table with frequencies, periods, participating masses & ratios.
| **Response Spectrum** | ✅ Complete | `run_response_spectrum_analysis()` — mode‑by‑mode RS analysis using GB50011 (or user‑supplied) spectrum, CQC/SRSS combination, base shear + moment.
| **Element‑Level RS Forces** | ✅ Complete | `extract_element_rs_forces()` — CQC‑combined moments/shears per element, sorted by elevation.
| **Missing Mass Correction** | ✅ Complete | `add_missing_mass_correction()` — rigid response from residual modal mass, adds to CQC base shear/moment.
| **Seismic Masses** | ✅ Complete | `compute_seismic_masses()` — lumps element self‑weight and load‑based masses per MASS SOURCE (Elements/Loads flags). |
| **Rhino Export** | ✅ Complete | Centreline + lightweight Extrusion geometry with section profiles, section-based layers, UserString metadata, groups. See [`docs/rhino_export.md`](docs/rhino_export.md). |
| **Frame Member Types (Steel)** | ✅ Complete | All steel section shapes (I, Box, Pipe, Channel, Angle, etc.) with `Steel01` fiber sections or elastic sections. |
| **Frame Member Types (RC)** | ⚠️ Partial | Concrete materials and section shapes supported; rebar auto-placement implemented; confined concrete (Mander) wired via `fiber_confinement()`. See [`docs/pushover_analysis.md`](docs/pushover_analysis.md). |
| **Code-Specific Capacity Modules** | ✅ Complete | `fea_toolkit.capacity` — GB 50010-2010 flexure/axial/shear/wall checks and ASCE 41-17 plastic hinge length, unit-aware (strengths authored in SI Pa, scaled to model units exactly once). See [`docs/capacity.md`](docs/capacity.md). |
| **Storey Identification** | ✅ Complete | `identify_stories()` — 4 strategies: S2K story table → diaphragm constraints → horizontal area elements → node Z clustering. Returns sorted `StoryLevel` list with confidence ratings. See [`docs/storey_response.md`](docs/storey_response.md). |
| **Storey Response Analysis** | ✅ Complete | `storey_displacements()` (rigid-body fit with outlier rejection), `storey_drifts()` (inter-storey drift ratios), `storey_shears()` (summed element-end forces), `modal_storey_drifts()` (CQC-combined modal drifts). |
| **Rigid Diaphragms** | ✅ Complete | `ops.rigidDiaphragm()` per group — S2K Z‑axis constraints, forced storey detection, or explicit named groups; slab‑derived levels are detected but applied only when explicitly requested (`rigid_diaphragms: True`/Z‑list), and `False` always disables. See [`docs/shell_support.md`](docs/shell_support.md). |
| **Connectivity Diagnostics** | ✅ Complete | `check_model_connectivity()` (orphan nodes, shell-only base), `check_split_connectivity()` (zero-length elements), `check_mesh_connectivity()` (unrestrained mesh), `diagnose_singularity()` (tree-plot distribution). |
| **Self-Weight Consistency** | ✅ Complete | `check_self_weight_consistency()` — compares analytical self-weight (element volumes × material unit weight) against applied load-pattern multipliers. |
| **Model Export to Tcl** | ✅ Complete | `export_model_to_tcl()` — direct SAPModelData → Tcl script for standalone OpenSees execution; `RecordingOpenSees` proxy captures all `ops.*` calls as Python/Tcl; `xara_build()` classmethod for Xara/OpenSeesRT workflow. |
| **Mesh Quality Checks** | ✅ Complete | `mesh/checks.py` — aspect ratios, skew, flatness diagnostics for quad shell elements. |
| **Constrained Remeshing** | ✅ Complete | `mesh/remesh.py` — Gmsh-based constrained quadrilateral remeshing with line constraints from frame edges. |
| **NDMaterial / LayeredShell** | ✅ Complete | Data model for nonlinear shear walls: `NDMaterial` (uniaxial/multiaxial), `LayeredShellSection`, `ShellFiberLayer` — parsed from SAP2000 area sections. |
| **Backend-Agnostic Viewer** | ✅ Complete | `ModelViewer` + `RenderBackend` abstraction — renders via PyVista or exports to standalone HTML. `plot_interactive_viewer()` adds radio buttons, sliders, click-to-inspect. |
| **NPZ/H5 Rhino Colouring** | ✅ Complete | `apply_results()`, `colour_from_npz()`, `colour_shells_from_results()`, `create_deformed_geometry()` — colour frames/shells and overlay deformed shapes from `.npz`/`.h5` results or stage files. `colour_members=False` keeps the Mesh geometry in its section/layer colours; `scale_mode="stepped"` colours by discrete bands; `clip_pct` clips the scale to the inner percentiles so outliers don't wash the bulk out. |
| **Brace Buckling (Approach A)** | ✅ Implemented | Subdivided element with imperfection + `Corotational`. |
| **Brace Buckling (Approach B)** | ✅ Implemented | Truss + `Hysteretic` material — robust for pushover. |
| **Brace Fatigue** | ✅ Implemented | `Fatigue` material wrapper for cyclic degradation. |
| **Load Handling** | ✅ Complete | Supports uniform and linear/trapezoidal distributed loads with global direction (gravity, X, Y, Z); projects onto local axes using `get_SAP_vecxz`; handles split loads. |
| **Parent‑Child Tracking** | ✅ Complete | Each split element stores `parent_id`, `child_ids`, `t_locations`; inactive flag prevents building of parent. |
| **Unit Conversion** | ✅ Complete | `SectionLibrary` converts lengths, areas, inertias between `in` and `mm` based on catalogue metadata. |
| **Rhino 3-D Export** | ✅ Complete | Full Rhino 8 module (`fea_toolkit.rhino`): centreline geometry (points/lines/Breps) and lightweight Extrusion solids (I/Box/Pipe/Channel/Rect/Circular) with section-based layers, FEA metadata (UserStrings), SAP group → Rhino group mapping, and joint colour-coding by restraint. See [`docs/rhino_export.md`](docs/rhino_export.md). |
| **Brace Buckling (Approach A)** | ✅ Implemented | Subdivided element with sinusoidal imperfection + `Corotational` geometry. Converges for pushover with HingeRadau integration. |
| **Brace Buckling (Approach B)** | ✅ Implemented | Truss element with `Hysteretic` material (asymmetric tension/compression) — numerically robust. |
| **Brace Fatigue** | ✅ Implemented | Optional `Fatigue` material wrapper for cyclic degradation. |
| **Pytest Suite** | ✅ Passing | [211 tests](tests/README.md): dataclass construction, geometry utilities, section enrichment, modal/RS/pushover analysis, CSM, brace buckling (Euler + eigenvalue FEA benchmark), parser, Rhino colour + layer utilities. |
| **Load Cases (SAP2000)** | ✅ Complete | `get_load_cases()` parses LOAD CASE DEFINITIONS, CASE - RESPONSE SPECTRUM (general + load assignments), CASE - MODAL, CASE - STATIC into `LoadCase.case_data`. |
| **Auto Load Data** | ✅ Complete | AUTO SEISMIC and AUTO WIND tables are parsed and attached to `LoadPattern.auto_data`. |
| **Material Damping** | ✅ Complete | Damping parameters from MATERIAL PROPERTIES 06 are captured in `Material.extra`. |
| **Area Element Mass** | ✅ Complete | `compute_seismic_masses()` now includes area element self-weight, area gravity loads (MultiplierZ), and area uniform loads. |
| **Brace Fatigue** | ✅ Complete | Optional `Fatigue` material wrapper for cyclic degradation (`brace_fatigue` config). |

#### 3. Notable Design Decisions

- **String IDs** – Node and frame IDs are kept as strings (SAP2000 labels), with numeric `tag` fields for OpenSees.
- **Relative Load Positions** – `FrameDistributedLoad` stores `rdist_a` and `rdist_b` (0..1) for child elements, matching OpenSees `aOverL`/`bOverL`.
- **Spatial Grid** – Efficient nearest‑neighbour search for splitting.
- **Trapezoidal Splitting** – Exact redistribution of varying loads using `trapezoidal_force_split`.
- **Configurable Builder** – Element type, integration points, splitting, verbosity can be set via `config` dict.
- **MASS SOURCE** – The `MASS SOURCE` table is parsed by `_get_mass_sources()` which groups rows by MassSource name, **accumulates** multipliers when the same LoadPat appears on multiple rows, and stores the result in `SAPModelData.mass_sources`. The builder's `compute_seismic_masses()` then uses this to derive nodal masses (self‑weight from `Elements=True`, load‑based from `Loads=True` + `LoadPat`/`Multiplier` pairs).
- **Modal & RS Analysis** – `run_modal_analysis()` uses `ops.eigen('-fullGenLapack', …)`. `run_response_spectrum_analysis()` and `extract_element_rs_forces()` call `ops.responseSpectrumAnalysis()` mode‑by‑mode and extract element forces via `ops.eleResponse(eid, 'forces')` (global system). CQC follows Der Kiureghian's formula. `add_missing_mass_correction()` computes the rigid response from residual mass at short‑period spectral acceleration.
- **Brace buckling — two approaches** – The builder supports two buckling modelling strategies. **Approach A** (experimental) subdivides braces into segments with a sinusoidal imperfection and uses `Corotational` geometric transformation — has element-level convergence issues. **Approach B** (recommended) replaces braces with `Truss` elements using a `Hysteretic` material with asymmetric tension/compression. Approach B is numerically robust and captures directional asymmetry correctly. Controlled via `brace_type="truss"` (default) / `brace_type="beam"` (experimental).
- **Configurable solver settings** – The builder's `run_static_analysis()` and `run_pushover_analysis()` read solver parameters from config: `solver_test_tol`, `solver_test_max_iter`, `solver_algorithm` (`'Newton'`, `'ModifiedNewton'`, `'NewtonLineSearch'`, `'KrylovNewton'`), and `gravity_num_substeps` for gravity load ramping.
- **Per-type stiffness factors (ACI 318 cracked sections)** – The `stiffness_factors` config option maps structural types to E_mod reduction factors for elastic analysis.  Keys: `'beam'`, `'column'`, `'brace'`, `'wall'`, `'slab'`.  Typical RC values per ACI 318-19 Table 6.6.3.1.1(a): beams=0.35, columns=0.70, walls=0.70, slabs=0.25.  Set to ``None`` (default) for gross/uncracked stiffness.  Classification: columns have |Δz| > 4× Δh; braces are diagonal; areas with all corner nodes at the same Z are slabs.  Separate OpenSees section tags are created per (section_name, type) pair so the same SAP2000 section used for both beams and columns gets different modifiers.
- **Brace fatigue** – Optional `Fatigue` material wrapper for cyclic degradation, controlled via `brace_fatigue`, `brace_fatigue_E0`, `brace_fatigue_m` config options.
- **Pushover spectrum override** – The pushover/CSM analysis can use a different spectrum from the response spectrum analysis via `pushover.spectrum` in the CONFIG. Falls back to the top-level `spectrum` if not specified.
- **Linear case auto-detection** – `run_linear_cases()` now reads LinStatic load cases from the SAP2000 model automatically. Users can override via `linear.cases` in the CONFIG.
- **Mass computation includes area elements** – `compute_seismic_masses()` now includes area element self-weight, area gravity loads (MultiplierZ), and area uniform loads. Previously only frame elements were included.
- **Spectrum damping fix** – The GB50011 spectrum now applies the damping reduction factor `η₂` to the ascending branch (T ≤ 0.1s) as well as the plateau and descending branch, matching the reference `GB_spectrum()` function.
- **SAP2000 data extraction** – The parser now extracts: load case definitions, response spectrum case data (general + load assignments), modal case data, AUTO seismic/wind table data, and material damping parameters.

---

## Pipeline Overview

A typical analysis flows through five stages.  Each stage produces or
consumes a well-defined data structure, making it easy to pick up at
any point (e.g. load a cached NPZ and jump straight to visualisation).

```
SAP2000 .s2k / .json
       │
       ▼  ┌──────────────────────┐
       │  │  SAP2000Parser       │  Stage 1 — Data Ingestion
       │  │  → SAPModelData      │
       │  └──────────────────────┘
       │
       ▼  ┌──────────────────────┐
       │  │  Preprocessor        │  Stage 2 — Preprocess
       │  │  → MeshModel         │  (frozen topology)
       │  └──────────────────────┘
       │
       ▼  ┌──────────────────────┐
       │  │  AnalysisBuilder     │  Stage 3 — Analysis
       │  │  → static results    │
       │  │  → modal results     │
       │  │  → RS results        │
       │  │  → pushover results  │
       │  └──────────────────────┘
       │
       ▼  ┌──────────────────────┐
       │  │  write_results_npz() │  Stage 4 — Storage
       │  │  → results.npz       │  (unified NPZ schema)
       │  └──────────────────────┘
       │
       ├──┬──────────────────────┐
       │  │  read_results_npz()  │  Stage 5 — Visualisation
       │  │  → npz_to_pyvista_*  │     (PyVista 3D plots)
       │  │  → npz_to_rhino_*    │     (Rhino colouring)
       │  │  → plot_npz_*        │     (force diagrams, moments)
       │  └──────────────────────┘
       │
       └──┬──────────────────────┐
          │  local/              │  Real‑world scripts
          │  admin_linear.py     │  Full pipeline: parse → analyse
          │  admin_report.py     │  → NPZ → plots → reports
          └──────────────────────┘
```

**Key data structures across the pipeline:**

| Stage | Data structure | Location |
|---|---|---|
| 1 — Parsed model | `SAPModelData` (dataclass tree) | `fea_toolkit.model.sap_data` |
| 2 — Preprocessed topology | `MeshModel` (frozen) | `fea_toolkit.model.mesh_model` |
| 3 — Analysis output | `dict` with `nodal_displacements`, `periods`, etc. | Returned by `AnalysisBuilder` methods |
| 4 — NPZ archive | `Dict[str, np.ndarray]` (keyed by schema) | `fea_toolkit.io.results_schema` |
| 5 — Visualisation | `pyvista.Plotter` / Rhino objects | `fea_toolkit.plotting` / `fea_toolkit.rhino` |

The unified NPZ schema (Stage 4) is the **canonical exchange format** —
it is what the visualisers consume.  You can save it once and reuse
for PyVista animations, Rhino colouring, and report plots without
re-running the analysis.

```python
# Minimal end-to-end pipeline
from fea_toolkit.io.s2k_parser import SAP2000Parser
from fea_toolkit.opensees.preprocessor import preprocess_model
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
from fea_toolkit.io.npz_writer import write_results_npz
from fea_toolkit.io.npz_reader import read_results_npz, npz_to_pyvista_frame_mesh
from fea_toolkit.plotting import plot_deformed_displacement_3d, plot_force_diagram

# 1. Parse
md = SAP2000Parser("model.s2k").parse().get_model_data()

# 2. Preprocess — frozen MeshModel topology
mm = preprocess_model(md, {"element_type": "elasticBeamColumn"})

# 3. Analyse — run each static case separately
b = AnalysisBuilder(mm, {})
b.build_domain()
static_dead = b.run_static_analysis(pattern_scales={"DEAD": 1.0})
b.build_domain()
static_wind = b.run_static_analysis(pattern_scales={"WIND": 1.0})

# 4. Save to NPZ as case-keyed mapping
cases = {"DEAD": static_dead, "WIND": static_wind}
write_results_npz("results.npz", md, static_results=cases)

# 5. Visualise from NPZ
data = read_results_npz("results.npz")
plot_force_diagram(data, quantity="Fx", combo="DEAD")  # axial force diagram
```

See also:
- `local/admin_linear.py` — full end‑to‑end workflow (parse → mesh → split →
  static → modal → RS → plots → animate)
- `src/fea_toolkit/io/README.md` — NPZ schema reference (also indexed from [`docs/documentation_index.md`](docs/documentation_index.md))

## Available Workflows

The toolkit exposes a set of workflows covering the full pipeline from model
parsing through analysis to visualisation and reporting.

### 1. Data Ingestion

| Workflow | Entry point | What it does |
|---|---|---|
| **Parse SAP2000 `.s2k`** | `SAP2000Parser.parse()` → `get_model_data()` | Read SAP2000 text export into structured model data |
| **Save/load JSON cache** | `.to_json()` / `.from_json()` | Serialise raw parsed tables for reuse without re-parsing |
| **Enrich from catalogue** | `SectionLibrary.enrich_section()` | Populate section dimensions from a manufacturer database |

### 2. Model Building

| Workflow | Entry point | What it does |
|---|---|---|
| **Build OpenSees model** | `preprocess_model()` → `AnalysisBuilder(mm, cfg).build_domain()` | Preprocess topology into a frozen `MeshModel`, then construct nodes, restraints, materials, sections, elements, loads |
| **Build with shells** | `preprocess_model(create_shells=True)` → `AnalysisBuilder(mm, cfg).build_domain()` | Preprocessor creates `ShellMITC4` for area elements (with optional loads-only selection); builder then constructs the domain |
| **Split elements at joints** | `preprocess_model(split_elements=True)` → `AnalysisBuilder(mm, cfg).build_domain()` | Preprocessor subdivides frame elements at intermediate nodes (SAP2000 auto-mesh); builder then constructs the domain |
| **Apply frame end offsets** | `preprocess_model(frame_end_offsets=...)` → `AnalysisBuilder(mm, cfg).build_domain()` | Preprocessor creates rigid zones at joints via stiff link elements; builder then constructs the domain |
| **Mesh area elements** | `preprocess_model(create_shells=True)` → `AnalysisBuilder(mm, cfg).build_domain()` | Preprocessor performs bilinear subdivision of quad areas per SAP2000 auto-mesh settings; builder then constructs the domain |
| **Apply edge constraints** | `apply_edge_constraints()` | Tie fine-mesh nodes to coarse edges via `equationConstraint` |
| **Detect unconnected edges** | `detect_unconnected_edges()` | Diagnostic: find shell nodes on coarse edges not yet connected |
| **Record as script** | `RecordingOpenSees` proxy | Capture all `ops.*` calls as standalone Python or Tcl script |
| **Detect storeys** | `identify_stories()` | 4 strategies to find storey elevations (S2K table → diaphragm → area elements → node clustering) |
| **Model connectivity check** | `check_model_connectivity()` | Pre-build scan for orphan nodes, shell-only base fixity, duplicate coordinates |
| **Split connectivity check** | `check_split_connectivity()` | After splitting: zero-length elements, duplicate nodes |
| **Mesh connectivity check** | `check_mesh_connectivity()` | After meshing: unrestrained base mesh, low-connectivity nodes |
| **Singularity diagnosis** | `diagnose_singularity()` | After build: scan OpenSees node DOF, tree-plot distribution |
| **Self-weight consistency** | `check_self_weight_consistency()` | Compare analytical vs applied weight per section |
| **Export to Tcl** | `export_model_to_tcl()` | Direct SAPModelData → standalone OpenSees Tcl script |

### 3. Analysis Types

| Workflow | Entry point | What it does |
|---|---|---|
| **Linear static** | `run_static_analysis()` | Nodal displacements, reactions, element forces |
| **Modal (eigenvalue)** | `run_modal_analysis()` | Periods, frequencies, mass participation, mode shapes |
| **Response spectrum (CQC)** | `run_response_spectrum_analysis()` | CQC-combined base shear, moment, element RS forces |
| **Pushover (non-linear)** | `run_pushover_analysis()` | Gravity → displacement-controlled lateral push with fiber sections |
| **CSM performance point** | `compute_performance_point()` | ATC-40 capacity spectrum method |
| **Storey displacement** | `storey_displacements()` | Rigid-body fit (Ux, Uy, Rz) per storey with outlier rejection |
| **Storey drifts** | `storey_drifts()` | Inter-storey drift ratios from rigid-body displacements |
| **Modal storey drifts (CQC)** | `modal_storey_drifts()` | CQC-combined modal drifts using spectral displacement scaling |
| **Storey shears/moments** | `storey_shears()` | Summed element-end forces per storey level |

### 4. Pushover Sub-workflows

| Workflow | Entry point | What it does |
|---|---|---|
| **Brace buckling check** | `check_brace_buckling()` | Euler $P_{cr}$ + demand/capacity ratios |
| **Set brace subdivision** | `set_brace_selection()` | Mark braces for imperfection + offset (Approach A) |
| **ADRS conversion** | `pushover_to_adrs()` | Convert capacity curve to acceleration-displacement format |
| **Capacity spectrum plot** | `plot_capacity_spectrum()` | ADRS plot with bilinear yield and performance point |

### 5. Export & Visualisation

| Workflow | Entry point | What it does |
|---|---|---|
| **Export to NPZ** | `export_results_to_npz()` | Compressed NumPy archive for Rhino/post-processing |
| **3D model (PyVista)** | `plot_mesh()` | Interactive structural model view |
| **Deformed shape (PyVista)** | `plot_deformed_displacement_3d()` | Displaced shape with colour-mapped displacements |
| **Mode shape (PyVista)** | `plot_mode_animation()` | Animate eigenvector displacements per mode |
| **Moment/force flags (PyVista)** | `plot_force_diagram()` | Flag or tube diagrams in 3D |
| **Pushover curve** | `plot_pushover_curve()` | Pushover capacity curve (base shear vs control displacement) |
| **CSM 4-panel** | `plot_csm_4panel()` | 2×2 ADRS plots per push direction |
| **Rhino import (centreline)** | `RhinoImporter.run(create_centreline=True)` | Joint points, frame lines, shell Breps with SAP metadata |
| **Rhino import (extrusion)** | `RhinoImporterV2.run()` | Lightweight Extrusion solids (I/Box/Pipe/Channel/Rect/Circular) |
| **Rhino colour from results** | `apply_results()` / `colour_from_npz()` | Colour frames/shells and overlay deformed shapes from `.npz`/`.h5`/stage files |
| **Rhino result flags** | `create_result_flags()` | 3D flag annotations for peak response values |
| **Interactive 3D viewer** | `plot_interactive_viewer()` | Radio buttons, sliders, click-to-inspect in browser |
| **Backend-agnostic HTML export** | `ModelViewer.export_html()` | Self-contained HTML with 3D scene |

### 6. GB 50011 Seismic Spectrum

| Workflow | Entry point | What it does |
|---|---|---|
| **Design spectrum** | `plot_seismic_spectrum()` | GB 50011 elastic spectrum from intensity, site class, level (returns periods, accelerations, and a Matplotlib figure) |
| **3-level plot** | `plot_seismic_spectrum()` | Frequent / fortification / rare spectra overlay |

### 7. Reporting

| Workflow | Entry point | What it does |
|---|---|---|
| **Model summaries** | `report.py` functions | Bounding box, materials, sections, masses, load totals |
| **Modal table (6-DOF)** | `modal_table_enhanced()` | Periods + rotational mass participation |
| **Brace buckling table** | `brace_buckling_check()` | $P_{cr}$, slenderness, D/C ratios |
| **Storey force plots** | `plot_storey_forces()` | Shear/moment profiles with analytical trapezoidal curves |
| **Storey displacement plots** | `plot_storey_displacements()` | Storey displacement & drift side-by-side |
| **Modal participation plot** | `plot_modal_participation()` | Bar chart of mass participation ratios |
| **RS modal analysis plot** | `plot_rs_modal_analysis()` | Periods, base shear, storey forces combined |
| **CSM 4-panel plot** | `plot_csm_4panel()` | 2×2 ADRS plots per push direction |
| **Seismic spectrum plot** | `plot_seismic_spectrum()` | GB 50011 elastic spectrum (1- or 3-level) |
| **Storey story plot (3D)** | `plot_stories()` | PyVista render with semi-transparent storey planes |

### 8. End-to-End Scripts

| Script | Purpose |
|---|---|
| `examples/basic_usage.py` | File chooser → parse → enrich → build → static analysis |
| `examples/static_analysis.py` | Parse → build → static → 2D/3D force diagrams |
| `examples/modal_rs_analysis.py` | Parse → build → masses → modal → GB 50011 RS → element RS forces |
| `examples/pushover_analysis.py` | Parse → fiber build → pushover → deformed shape plots |
| `local/pumphouse_csm.py` | Full pipeline: parse → buckling → modal → RS → pushover 4-dir → CSM → NPZ |
| `local/detect_edges.py` | Standalone unconnected shell edge detection |

### 9. Report Generation Architecture (Proposed)

**Coming:** A `generate_report()` function driven by a YAML config file
that orchestrates the entire analysis pipeline, stores results in HDF5, and
produces a self-contained report. See `docs/report_generation.md` for the
detailed design.

---

### 10. Optional Mesh Utilities

Two optional subpackages provide mesh quality diagnostics and Gmsh-based
constrained quadrilateral remeshing.  Neither is imported by the core
workflow — they must be explicitly imported after a build.

```python
# Mesh quality checks (NumPy only — no extra install needed)
from fea_toolkit.mesh import checks as mesh_check

# After building with shells:
quality = mesh_check.report(builder.model.area_elements, builder.model.nodes)
if not quality["passed"]:
    for w in quality["warnings"]:
        print(w)
```

```python
# Constrained remeshing via Gmsh: pip install fea_toolkit[mesh-remesh]
from fea_toolkit.mesh import remesh

# Build line constraints from frame elements that connect to area edges
constraints = {}
for fid, fe in model.frame_elements.items():
    for aid, ae in model.area_elements.items():
        if fe.node_i in ae.node_ids and fe.node_j in ae.node_ids:
            remesh.constrain_line(aid, fid, fe.node_i, fe.node_j,
                                  model.nodes, constraints)

# Remesh via Gmsh (replaces built-in structured subdivision)
areas, assign, nodes, ntag = remesh.remesh_areas(...)
```

#### 9.1 Mesh Quality Checks (`fea_toolkit.mesh.checks`)

| Function | What it measures | Warning threshold |
|---|---|---|
| `aspect_ratios()` | Longest / shortest edge per quad | > 4.0 |
| `skew()` | Max deviation from 90° interior angles | > 30° |
| `flatness()` | True non-planarity (tet height / avg edge) | > 0.02 |
| `report()` | Combined report with warnings | Configurable |

All functions work with the same ``{area_id: AreaElement}`` / ``{node_id: Node}``
dicts used throughout the builder.  No external dependencies (pure NumPy).

#### 9.2 Constrained Remeshing (`fea_toolkit.mesh.remesh`)

| Function | Purpose |
|---|---|
| `remesh_areas()` | Replace structured subdivision with Gmsh-generated quads |
| `constrain_line()` | Register a frame edge as a mesh constraint |

**Line constraints** ensure that frame-element edges crossing an area
boundary are preserved in the Gmsh mesh — the mesh conforms to those
lines automatically, eliminating the need for post-process
``equationConstraint``.  Powered by the **Gmsh Python API** (GPL v2+).

Constrained curves are embedded in the OCC model before mesh generation
so the mesher respects them natively.

**Relation to built-in meshing:**

| Aspect | Built-in ``mesh_area_elements`` | ``remesh_areas()`` |
|---|---|---|
| Mesh type | Structured (grid) | Unstructured (Gmsh) |
| Element size | Uniform ``max_size`` | Graded via characteristic length |
| Edge constraints | Post-process ``equationConstraint`` | Native via embedded curves |
| Dependencies | None (pure NumPy) | ``gmsh`` (GPL v2+) |
| Recommended for | Simple rectangular areas | Warped quads, complex boundaries |

---

### 10. Pushover Hinge Models

The builder supports two hinge modelling strategies for non-linear pushover
analysis, controlled by the ``hinge_model`` config option.

#### 10.1 Distributed Plasticity (``hinge_model: 'fiber'`` — default)

Uses ``forceBeamColumn`` elements with **fiber sections**.  Each cross-section
is discretised into fibres (concrete patches + steel rebar layers for RC,
flange/web rectangles for steel).  P-M-M interaction is captured implicitly
as individual fibres yield.

**Steel sections** — the existing ``to_fiber_patches()`` on ``ISection``,
``PipeSection``, ``BoxSection``, etc. generates rectangular ``patch`` definitions.

**RC sections** — two new section types support concrete fibre modelling:

| Section class | SAP2000 shape | Concrete layering |
|---|---|---|
| ``ConcreteRectangularSection`` | ``Concrete Rectangular`` | Confined core + unconfined cover + steel rebar layers |
| ``ConcreteCircularSection`` | ``Concrete Circular`` | Confined core ring + unconfined cover ring + rebar ring |

Material tags are allocated in groups of three::

    mat_tag     → unconfined concrete (Concrete01)
    mat_tag + 1 → confined concrete (Concrete01, Mander enhanced)
    mat_tag + 2 → steel rebar (Steel02)

#### 10.2 Lumped Plasticity (``hinge_model: 'lumped'``)

Uses ``zeroLengthSection`` elements at member ends with an elastic interior.
This path directly replicates SAP2000's ASCE 41 hinge assignment::

    structural_node_i → hinge_i → elastic_mid → hinge_j → structural_node_j

Key implementation details:

* **Coincident nodes** are created at each element end with the same coordinates.
* **``equalDOF``** ties translation DOFs (1,2,3) between structural and hinge
  nodes so only rotations are released across the zero-length hinge.
* **``Aggregator`` section** couples axial (P) and moment (Mz, My) responses
  using a ``Hysteretic`` backbone material calibrated from the section's yield
  moment and ASCE 41 rotation limits.
* **ASCE 41-17 §10.8 hinge length**: ``fea_toolkit.capacity.asce41.hinge_length``
  implements Eq 10-1 / 10-2 / ACI formulas for steel, brace, and RC
  members.

Configure with::

    config = {
        'hinge_model': 'lumped',           # default: 'fiber'
        # ... other builder options ...
    }

#### 10.3 ASCE 41 Hinge Lengths

``fea_toolkit.capacity.asce41.hinge_length(md, sec_name, elem_length)``
(legacy alias: ``model.checks.compute_asce41_hinge_length``) returns the
plastic hinge length per ASCE 41-17:

| Member type | Formula (Lp) | Reference |
|---|---|---|
| Steel moment frame | 0.08L + 0.022 · db · fy | ASCE 41 Eq 10-1 |
| Steel brace | 0.08L + 0.015 · db · fy | ASCE 41 Eq 10-2 |
| RC beam/column | 0.05L + 0.1 · db · fy / √fc | ACI 318 |
| Fallback (no data) | max(0.05, 0.1L) | — |

All formulas are capped at 0.33L per ASCE 41.

#### 10.4 Pushover Workflows

**Fiber pushover (steel)**::

    mm = preprocess_model(md, {'split_elements': True})
    builder = AnalysisBuilder(mm, {
        'create_fiber_sections': True,
        'element_type': 'forceBeamColumn',
        'hinge_model': 'fiber',
    })
    builder.build_domain()
    builder.compute_seismic_masses()
    modal = builder.run_modal_analysis(num_modes=3)
    shapes = builder.extract_mode_shapes(3)
    results = builder.run_pushover_analysis(
        gravity_patterns={'DEAD': 1.0},
        lateral_load_type='uniform',
    )

**Lumped plasticity pushover (steel or RC)**::

    mm = preprocess_model(md, {'split_elements': True})
    builder = AnalysisBuilder(mm, {
        'use_elastic_sections': True,
        'hinge_model': 'lumped',
    })
    # ... same analysis pipeline ...

**RC fiber pushover:** parser automatically creates
``ConcreteRectangularSection`` or ``ConcreteCircularSection`` for
``Concrete Rectangular`` / ``Concrete Circular`` SAP2000 shapes::

    mm = preprocess_model(md, {'split_elements': True})
    builder = AnalysisBuilder(mm, {
        'create_fiber_sections': True,
        'element_type': 'forceBeamColumn',
    })
    builder.build_domain()  # confined/unconfined/steel materials created automatically

---

#### 4. Distributed Load Support by Element Type

Not all OpenSees element types support the same `eleLoad -type -beamUniform` argument forms. The builder handles this automatically:

| Element type | 3-arg uniform `(wy, wz, wx)` | 5-arg partial `(wy, wz, wx, aL, bL)` | Trapezoidal / linear varying |
|---|---|---|---|
| **`elasticBeamColumn`** | ✅ Native | ✅ Native | ❌ Decomposed to equivalent uniform |
| **`forceBeamColumn`** | ✅ Native | ✅ Native | ❌ Decomposed to equivalent uniform |
| **`dispBeamColumn`** | ✅ Native | ✅ Native | ❌ Decomposed to equivalent uniform |
| **`nonlinearBeamColumn`** | ✅ Native | ✅ Native | ❌ Decomposed to equivalent uniform |

**Notes:**

- The 8‑argument trapezoidal form `(wy1, wz1, wx1, aL, bL, wy2, wz2, wx2)` is
  **broken in OpenSeesPy 3.8.0.0** — the end values (`wy2` etc.) are silently
  ignored.  The builder therefore decomposes non‑uniform loads into **4 partial‑
  span uniform segments** (using the working 5‑argument form), which preserves
  both the total force and the moment distribution.
- `Corotational` geometric transformation does **not** support `eleLoad` in 3D
  (per the [OpenSees documentation](https://opensees.ist.berkeley.edu/wiki/index.php?title=EleLoad_Command)).
  If you use `'geom_transf_type': 'Corotational'`, the builder will emit a warning.
  Use :func:`beam_load_to_nodal_loads` from `fea_toolkit.model.geometry` to convert
  distributed loads into equivalent nodal loads as a workaround.

---

#### 5. Section Types and Properties

SAP2000/ETABS models use a variety of cross‑section shapes. The `Section` dataclass in `sap_data.py` has been refactored into a polymorphic hierarchy so that each shape stores only its relevant geometric parameters:

| Class | SAP2000 shape names | Shape‑specific fields | Fiber patches |
|---|---|---|---|
| **`Section`** (base) | (generic / unknown) | — | ❌ `NotImplementedError` |
| **`ISection`** | `I/Wide Flange`, `WIDE FLANGE`, `Steel I/Wide Flange` | `depth`, `bf`, `tf`, `tw` | ✅ 3 rect patches (bot flange → web → top flange) |
| **`ChannelSection`** | `Channel`, `Steel Channel`, `Concrete Channel` | `depth`, `bf`, `tf`, `tw` | ✅ 3 rect patches (web + 2 flanges, centroid-shifted) |
| **`AngleSection`** | `Angle`, `Steel Angle`, `Concrete Angle` | `depth`, `bf`, `tf`, `tw` | ✅ 2 rect patches (2 legs, centroid-shifted) |
| **`DoubleAngleSection`** | `Double Angle`, `Steel Double Angle` | `depth`, `bf`, `tf`, `tw`, `dis` | ✅ 4 rect patches (2 back-to-back angles, centroid-shifted) |
| **`TeeSection`** | `Tee` | `depth`, `bf`, `tf`, `tw` | ✅ 2 rect patches (flange + stem, centroid-shifted) |
| **`PipeSection`** | `Pipe`, `Steel Pipe`, `Concrete Pipe`, `Filled Steel Pipe` | `od`, `t` | ✅ 1 annular `circ` patch |
| **`BoxSection`** | `Box/Tube`, `Steel Tube`, `Concrete Tube`, `Filled Steel Tube` | `depth`, `bf`, `tf`, `tw` | ✅ 4 `rect` patches (flanges + webs) |
| **`RectangularSection`** | `Rectangular`, `Rectangle`, `Steel Plate`, `Concrete Rectangular` | `depth`, `bf` | ✅ 1 `rect` patch |
| **`CircularSection`** | `Circle`, `Steel Rod`, `Concrete Circle` | `diameter` | ✅ 1 solid `circ` patch |
| **`GeneralSection`** | `General`, `NA` | — | ❌ Requires a known shape |
| **`SDSection`** | `SD Section` | `polygons` (multi‑material) | 🚧 Placeholder (needs meshing) |
| **`EncasedSection`** | `Concrete Encasement Rectangle/Circle` | `embedded_section`, `encasement_depth/bf` | 🚧 Placeholder |
| **`ShellSection`** | `Shell` | `thickness` | ❌ Not applicable |

All section classes inherit the common derived properties (`A`, `I33`, `I22`, `J`) directly from the SAP2000 text file, which includes pre‑computed values. The `to_fiber_patches()` method on each class generates OpenSees `patch('rect', …)` definitions for nonlinear fiber‑section analysis.

When the parser encounters a `FRAME SECTION PROPERTIES 01 - GENERAL` table, it dispatches to the correct subclass based on the `Shape` field, extracting SAP2000 dimension keys (`t3` → depth / OD, `t2` → width, `tw`, `tf`) into the appropriate fields.

---

#### 6. Selection — Filtering Model Elements

The :class:`Selection` class (``src/fea_toolkit/model/selection.py``) provides a
flexible, composable way to pick subsets of model elements for targeted
operations.  It is used, for example, to control **which area uniform loads**
get converted to equivalent frame edge loads during model building.

**Logic rules**

- **AND across criteria** — every non-``None`` field narrows the selection.
  An element must satisfy *all* of them to be included.
- **OR within a list** — multiple values in the same field are alternatives;
  an element matching *any* of them passes that criterion.

**Available criteria**

| Field | Scope | Description |
|---|---|---|
| ``element_types`` | All | ``'Frame'``, ``'Area'``, ``'Node'`` (or a list) |
| ``sections`` | Frame, Area | Section/property name(s) — checked via assignment maps |
| ``materials`` | Frame, Area | Material name(s) — resolved through the assigned section |
| ``groups`` | All | Group name(s) — matched against ``Group.objects`` (e.g. ``"Frame:123"``) |
| ``element_ids`` | All | Specific ID(s) for exact targeting |

**Key methods**

| Method | Returns | Purpose |
|---|---|---|
| ``get_frame_ids(model)`` / ``get_area_ids(model)`` / ``get_node_ids(model)`` | ``List[str]`` | Get matching element IDs |
| ``filter_frames(model)`` / ``filter_areas(model)`` / ``filter_nodes(model)`` | ``Dict[str, Element]`` | Get matching element objects |
| ``filter_area_uniform_loads(model)`` | ``List[AreaUniformLoad]`` | Uniform loads on selected areas |
| ``filter_area_gravity_loads(model)`` | ``List[AreaGravityLoad]`` | Gravity loads on selected areas |

**Example — convert only slab area loads**

```python
from fea_toolkit.model.selection import Selection

sel = Selection(
    element_types=['Area'],
    sections=['Slab 200mm', 'Roof 150mm'],
)
builder.build(selection=sel)
```

Only area loads on the two slab sections are converted to frame edge loads;
all other area loads are ignored.

---

### What Remains to Be Done (Next Steps)

> The canonical, priority-ordered backlog lives in
> [`docs/_pending_work.md`](docs/_pending_work.md).  Items below are
> reconciled with that register; P-item cross-references are in brackets.

#### High Priority

1. **Frame‑Frame Intersection Splitting** ✅  
   - `AtFrames=True` splitting at frame-frame intersections is implemented (`model/geometry_frames.py` `split_elements()`), parser-wired (`FRAME AUTO MESH ASSIGNMENTS` → `SAPModelData.frame_auto_mesh`), and unit-tested (`TestSplitElementsAtFrames` + parser round-trip tests).  

2. **ETABS `.E2K` input**  
   - ✅ `SAP2000Parser` already reads E2K table conventions (concrete
     column/beam tables, load/mass tables) — see `io/s2k_parser.py`.  
   - Open: validate a full `.e2k` export end-to-end and add ETABS-specific
     load-nomenclature mapping if needed.

3. **Load Combinations and Analysis Types**  
   - ~~`MassSource`~~ ✅ Parsed by `_get_mass_sources()` and stored in `SAPModelData.mass_sources`.  
   - ~~`LoadCase`~~ ✅ Parsed by `get_load_cases()` — `LOAD CASE DEFINITIONS`, `CASE - RESPONSE SPECTRUM` (general + load assignments), `CASE - MODAL`, `CASE - STATIC` (see the "Key Components Implemented" table above).  \
   - `LoadCombination` dataclass defined in `sap_data.py` — parsing of the `LOAD COMBINATIONS` table still needed (tracked as **P12** in `docs/_pending_work.md`).  
   - In `AnalysisBuilder`, allow the user to select which load cases/combinations to run with combination factors (e.g., `1.2 DL + 1.6 LL`).

4. **Advanced Analyses**  
   - ~~Modal Analysis~~ ✅ `run_modal_analysis()` implemented — eigenvalue extraction with modal properties table.  
   - ~~Response Spectrum~~ ✅ `run_response_spectrum_analysis()` + `extract_element_rs_forces()` + `add_missing_mass_correction()` implemented.  
   - ~~Nonlinear Static Pushover~~ ✅ `run_pushover_analysis()` implemented — see [`docs/pushover_analysis.md`](docs/pushover_analysis.md).  
   - ~~HingeRadau integration~~ ✅ `beam_integration` config option (`'Lobatto'` / `'HingeRadau'`).  
   - ~~Brace subdivision (Approach A)~~ ✅ `subdivide_elements()` in `geometry.py`, `set_brace_selection()` / `check_brace_buckling()` in builder.  
   - ~~Brace buckling (Approach B — truss + Hysteretic)~~ ✅ `brace_truss` config option, `Hysteretic` material with asymmetric tension/compression. See [`docs/pushover_analysis.md`](docs/pushover_analysis.md).  
   - ~~Configurable solver settings~~ ✅ `solver_test_tol`, `solver_test_max_iter`, `solver_algorithm`, `gravity_num_substeps` builder config options.  
   - ~~Brace detection~~ ✅ `Selection.from_brace_sections()`.  
   - ~~Buckling eigenvalue benchmark~~ ✅ SciPy-based independent validation — subdivided column buckling matches Euler within 0.01 %.  
   - ~~Capacity Spectrum Method~~ ✅ `pushover_to_adrs()` + `compute_performance_point()` + `plot_capacity_spectrum()` — see [`docs/pushover_analysis.md`](docs/pushover_analysis.md).  
   - ~~Nonlinear Time History (Tcl/Xara ground-motion path)~~ ✅ `run_nonlinear_dynamic_analysis()` implemented — ground-motion input + transient analysis via Tcl export and Xara/OpenSeesRT, with Rayleigh damping from a preceding modal analysis (see [`docs/nonlinear_dynamic_analysis.md`](docs/nonlinear_dynamic_analysis.md)).  
   - **Nonlinear Time History (additional integration schemes)** – Python-native integration schemes (e.g. direct OpenSeesPy transient analysis) remain planned.

5. **Joint Modeling** (for concrete frames)  
   - Level 1 (rigid joint end zones) ✅ — `rigid_end_zones` auto-generates offsets (0.5 x intersecting depth) with `rigid_link_mpc` MPC links.  
   - Extend parser to recognise joint elements (if present in SAP2000) — tracked as **P13** in `docs/_pending_work.md`.  
   - Implement `Joint2D` and `beamColumnJoint` elements in `AnalysisBuilder` (Level 3) — P13.

6. **Brace gusset plates / joint offsets**  
   - ✅ Rigid offset segments between working point and brace physical end (`brace_end_offset` / `subdivide_elements(end_offset=...)`).  
   - Open: gusset plate flexibility as rotational springs at brace ends.  
   - See `docs/pushover_analysis.md` for discussion of approaches.

7. **Rhino Importer Refactoring** ✅ — `rhino/importer.py` (`RhinoImporter`)
   reads `SAPModelData` directly and creates lightweight Extrusions;
   `rhino/layers.py` + `rhino/colors.py` handle section-based layers and
   FEA metadata.

### Getting started

See [`examples/README.md`](examples/README.md) for quick-start examples.

### Documentation index

All feature‑specific documentation (analysis types, model features, export,
visualisation, tool‑specific workflows) is indexed in
[`docs/documentation_index.md`](docs/documentation_index.md) — grouped by category with a tag‑based
cross‑reference section.

#### Medium Priority

7. **Improved Load Handling**  
   - **Point loads** on frames (`FRAME LOADS - POINT`) — tracked as **P14** in `docs/_pending_work.md`.  
   - **Temperature loads** — only if needed (no current demand; P14).  
   - Option to convert linear loads to uniform (simplification) via config flag.

8. **Result Extraction**  
   - Extend the analysis runners to return reactions, internal forces, and mode shapes where not already returned.  
   - Deeper `opstool` integration — closed as **no current demand** (see `docs/report_generation.md`; NPZ ↔ opstool ODB converter deferred).

9. **Documentation**  
   - Write full API docs (Google style already in code).  
   - Create a user guide (examples, how to run different analyses).

10. **Testing**  
    - ~~`test_model.py` is yet to be populated~~ ✅ Populated — CSM/bilinearization, Euler buckling benchmark, load dataclasses, Mander confinement wiring, mesh edge-restraint propagation.  
    - ~~Add unit tests for `SectionLibrary`, `SAPModelData` dataclasses, and geometry utilities~~ ✅ Covered in `test_model.py`, `test_geometry.py`, `test_mesh_units.py`, `test_confinement.py`.  
    - ~~Add integration tests for the two-stage pipeline~~ ✅ Covered in `test_workflows.py`, `test_rc_pushover.py`, `test_layered_shell.py`, `test_wall_pushover.py`.  
    - ~~Add tests for `split_elements`~~ ✅ `TestParserModelIntegration::test_split_elements{,_tracking}` and `TestBuildWorkflow::test_build_with_split_elements`; trapezoidal-load decomposition remains an open sub-item.

#### Low Priority

11. **Parallel Processing** – Not planned (large-model splitting/analysis parallelisation).
12. **Graphical User Interface** – Not planned.
13. **Other FEA Formats** – Abaqus `.inp`, Ansys `.cdb` – not planned.

---

### Conclusion

The **SAP2000 → OpenSees pipeline** is now **largely functional**. You can parse a model, split elements and loads (including frame-frame intersections), build an OpenSees model, and run linear static, modal, response-spectrum, and pushover analyses. The code is modular and well‑structured; the priority-ordered backlog lives in `docs/_pending_work.md`.

The project is well on track to meet your original goals. Let me know which of the remaining tasks you would like to tackle next, and I will provide the necessary code and guidance.

---

## Troubleshooting

### Pylance false‑positive squiggles for `openseespy` / `opstool`

`openseespy.opensees` and `opstool` are **C extensions** (compiled `.so` files). Pylance cannot statically inspect C extensions, so it flags every `ops.xxx()` call as `"xxx" is not a known attribute` — even though the calls work fine at runtime.

The fix is to provide **type stubs** that tell Pylance these modules are dynamically typed.

#### Step 1 — Type stubs (already created)

The project ships with detailed type stubs covering every OpenSees and opstool function used in the source code:

**`typings/openseespy/opensees/__init__.pyi`** — 22 functions with named parameters and docstrings:

| Category | Functions |
|---|---|
| Domain/model | `wipe()`, `model()`, `node()`, `fix()`, `nodeCoord()`, `nodeDisp()` |
| Section | `section()` |
| Geometry | `geomTransf()` |
| Elements | `element()`, `beamIntegration()`, `eleNodes()`, `eleResponse()` |
| Loads | `timeSeries()`, `pattern()`, `load()`, `eleLoad()` |
| Analysis | `constraints()`, `numberer()`, `system()`, `test()`, `algorithm()`, `integrator()`, `analyze()` |
| Recorder | `recorder()` |
| Material | `uniaxialMaterial()` |

Plus a `__getattr__` fallback for any undocumented functions.

**`typings/opstool/__init__.pyi`** + **`typings/opstool/post/__init__.pyi`** — `CreateODB()`, `save_model_data()`, `get_model_data()` with typed parameters.

#### Step 2 — Point Pylance at the stubs

In `.vscode/settings.json` (already created):
```json
{
    "python.analysis.stubPath": "typings"
}
```

#### Step 3 — Reload the window

Run `Developer: Reload Window` in VS Code so Pylance picks up the changes.

---

## TODO / Future Work

### Nonlinear Dynamic (Time‑History) Analysis

> **Status note (2026-08-23):** `run_nonlinear_dynamic_analysis()` is now
> implemented via the **Tcl export + Xara/OpenSeesRT** path (see
> [`docs/nonlinear_dynamic_analysis.md`](docs/nonlinear_dynamic_analysis.md)).
> The outstanding item is a **Python-native** transient integration (no
> Tcl/Xara dependency); the building blocks below describe that native path.

A `run_time_history_analysis()` method is needed.  Below are the building blocks required, along with recommendations based on published OpenSees practice.

| Item | Detail | Priority |
| :--- | :--- | :--- |
| **Transient integrator** | `Newmark` (constant acceleration, $\gamma=0.5,\ \beta=0.25$) is the most robust for seismic analysis. `HHT` ($\alpha=-0.1$) adds numerical damping for higher modes. | High |
| **Damping** | Rayleigh damping (`ops.rayleigh`) from mass‑ and stiffness‑proportional coefficients ($a_0, a_1$) tuned to the first-mode and a high-mode frequency. | High |
| **Ground motion input** | `ops.timeSeries('Path', …)` + `ops.pattern('UniformExcitation', …)` for uniform base excitation. Multi‑support excitation requires `ImposedMotion`. | High |
| **Dynamic recorders** | `ops.recorder('Node', …)` for displacement/velocity/acceleration at control nodes; `ops.recorder('Element', …)` for brace axial forces. | High |
| **Material improvements** | The `Hysteretic` material in Approach B lacks cyclic degradation. OpenSees offers better alternatives for braces under cyclic loading (see below). | Medium |
| **Convergence under dynamics** | Transient analysis may require `KrylovNewton` or `NewtonLineSearch` for brace buckling cycles. Test tolerance should be $10^{-4}$–$10^{-5}$. | Medium |

#### Recommended brace materials for dynamic analysis (Approach B evolution)

Based on the OpenSees workshop examples (`Workshops/OpenSeesDays/Steel2dModels/`) and published research:

| Material | Use case | Cyclic degradation? | Fatigue? | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`Hysteretic`** (current) | Static pushover only | ❌ No | ❌ No | Simple backbone, no cycle‑to‑cycle change. Adequate for monotonic pushover only. |
| **`Hysteretic` + `Fatigue`** | Cyclic dynamic | ❌ No (Hysteretic) | ✅ Yes (Coffin‑Manson) | **Implemented.** Wrap `Hysteretic` with `Fatigue` via `brace_fatigue=True`. Asymmetric buckling + low-cycle fracture. Recommended for dynamic analysis. |
| **`Steel02` + `Fatigue`** | Cyclic dynamic | ✅ Yes (Bauschinger) | ✅ Yes (Coffin‑Manson) | Preferred for subdivided beam-column elements (fiber section buckling). Used in OpenSees Day CBF examples (`CBFbase.tcl`). Not suitable for truss approach (Steel02 is symmetric). |
| **`BraceMaterial`** | Cyclic dynamic | ✅ Yes (damage) | ✅ Yes (energy‑based) | Specialised uniaxial brace model with pinching + damage. Not available in OpenSeesPy (tested). |
| **`Pinching4`** | Cyclic dynamic | ✅ Yes (degradation) | ❌ No | Not available in OpenSeesPy with this version. |

**Recommendation:** For nonlinear dynamic analysis with the truss approach, use **`Hysteretic` + `Fatigue`** (`brace_fatigue=True`).  For subdivided beam-column elements (if the convergence issue is resolved), use `Steel02` + `Fatigue`.

#### Additional solver considerations for dynamics

- Use **`Transformation`** constraints (already the default) — `Plain` is unreliable for large 3D models.
- Use **`BandGen`** system (already the default) — `ProfileSPD` or `SparseSYM` are alternatives for larger models but slower.
- Consider a **two‑stage analysis**: `Transient` (ground motion) → `Static` (residual gravity check).  OpenSees `loadConst('-time', 0.0)` separates stages naturally.
- For gravity + earthquake, apply gravity first with `LoadControl`, then `loadConst('-time', 0.0)` before starting the transient analysis.

### Brace Modelling — Other Approaches Not Yet Investigated

These approaches are documented in the OpenSees literature but have not been implemented in `fea_toolkit`:

| Approach | Element type | Material | Geometry | Works for static? | Works for dynamic? | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **A — Subdivided beam‑column** | `dispBeamColumn` | Fiber (Steel01) | PDelta / Corotational | ❌ Gravity convergence fails | ❌ Not tested | The subdivided elements + PDelta create an ill‑conditioned stiffness matrix. No imperfection also fails. See `docs/pushover_analysis.md`. |
| **B — Truss + Hysteretic** (current) | `Truss` | `Hysteretic` | None (axial only) | ✅ Works | ⚠️ Needs fatigue wrapper | Recommended for static. For dynamic, enable `brace_fatigue=True` to wrap with `Fatigue` for low-cycle fracture. |
| **C — `beamWithHinges`** | `beamWithHinges` | Fiber (Steel01) | Corotational | ❓ Not tested | ❓ Not tested | Plastic hinge ends + elastic interior. More stable than full‑length fiber subdivision. Used in some OpenSees examples. |
| **D — `corotTruss`** | `corotTruss` | `Hysteretic` / `Steel02` | Built‑in corotational | ❓ Not tested | ❓ Not tested | Truss element with corotational formulation. Captures large‑displacement axial response. |
| **E — `Pinching4` truss** | `Truss` | `Pinching4` | None (axial only) | ✅ Should work | ✅ Should work | Good for braces with pinched hysteresis and cyclic degradation. |
| **F — Steel02+Fatigue truss** | `Truss` | `Steel02` + `Fatigue` | None (axial only) | ✅ Should work | ❌ Not suitable | Steel02 is symmetric — cannot capture asymmetric tension/compression buckling. Use `Hysteretic` + `Fatigue` (Approach B) instead. |

**Overall recommendation for brace modelling:**

| Analysis type | Recommended approach |
| :--- | :--- |
| Static pushover (current) | **Approach B** (`Truss` + `Hysteretic`) — already working |
| Nonlinear dynamic (future) | **Approach B** (`Truss` + `Hysteretic` + `Fatigue`) — already implemented (`brace_fatigue=True`) |

### Approach A — Remaining Roadblocks (for reference)

The subdivided element approach (subdivided `dispBeamColumn` + fiber sections + PDelta/Corotational) fails at the gravity stage even after fixing:

- ✅ Missing `set_brace_selection()` call
- ✅ `split_elements` conflict (`split_elements=False` now used)
- ✅ Double subdivision on rebuild (inactive‑element check)
- ✅ `forceBeamColumn` → `dispBeamColumn` element‑level fix
- ✅ Node creation during rebuild (tracked via `_created_node_tags`)
- ✅ Rigid‑link section parameter order (E/A swapped)
- ❌ **Gravity convergence still fails** — subdivided `dispBeamColumn` elements with PDelta geometry cannot converge under ~7.5 MN of gravity load, even with 100 sub‑steps, no imperfection, NormUnbalance, and KrylovNewton.

The root cause appears to be the shared‑node connectivity between subdivided braces and existing frame elements — the PDelta geometric stiffness contributions from multiple subdivided elements at the same node create an ill‑conditioned system matrix.

---

## Refactoring Roadmap

The following items are the highest-impact improvements identified during a codebase-wide review:

### High Priority

1. **Create `fea_toolkit/spectrum.py`** — ✅ **Done**: `_gb50011_spectrum()`,
   `_build_spectrum()`, `_interp_sa()`, and `plot_seismic_spectrum()` live in
   `src/fea_toolkit/spectrum.py`; `AnalysisBuilder` consumes them for RS and CSM.

2. **Create `fea_toolkit/io/report.py`** — ✅ **Done**: the generic
   SAP2000→pandas summary functions live in `src/fea_toolkit/io/report.py`.

3. **Create `fea_toolkit/utils.py`** — ✅ **Done**: `deep_merge()`, `infer_loads()`,
   `build_gravity_patterns()`, `pick_wind()`, `g_from_units()` etc. live in
   `src/fea_toolkit/utils.py`.  The Euler buckling check was consolidated into
   `model/checks.py` (`check_brace_buckling()` + DataFrame variant
   `brace_buckling_check()`), importable without an OpenSees builder.

4. **Create `fea_toolkit/plotting/report.py`** — ✅ **Done**: matplotlib report
   figures live in `src/fea_toolkit/plotting/report.py`.

5. **Split `builder.py`** — ✅ **Done**: replaced by the two-stage pipeline.
   Topology mutation lives in `opensees/preprocessor.py` (produces a frozen
   `MeshModel`); OpenSees domain construction + analysis execution live in
   `opensees/analysis_builder.py`; `opensees/builder.py` now only exports
   standalone Tcl-export functions.

6. **Add unit tests** — ✅ **Largely done**: `tests/` mirrors `src/` with
   1,000+ tests covering the pipeline, pushover, CSM, capacity, storey
   response, meshing, and the extracted modules.

### Remaining consolidation (pending)

The **priority-ordered pending-work register** lives in
[`docs/_pending_work.md`](docs/_pending_work.md) ("PENDING — active"), with
detailed designs in `docs/force_diagram_unification.md` and the deprecation
plan.  In summary:

- **Phase B — force-diagram unification** — the four legacy entry points
  (`plot_rs_force_diagram()`, `plot_force_diagram_3d()`,
  `plot_npz_force_diagram()`, `plot_npz_moment_3d()`) are now a single
  unit-aware `plot_force_diagram()` dispatcher (detailed design →
  `docs/force_diagram_unification.md`); the legacy wrappers were removed
  2026-08-24.  **Prerequisite** for the `viz.py` split.
- **Split the large modules** — `opensees/analysis_builder.py` (~7.4k
  lines), `plotting/viz.py` (~5.7k), `model/geometry.py` (~3.9k).
- **Pushover solver tuning (empirical pass)** + **CSM Gap-4 benchmark
  validation** + **shear-failure / post-peak modelling** — see
  `docs/deprecation_plan.md` §5–6 and the Tier-2 items in
  `docs/_pending_work.md`.
- **Section fiber patches** (`Channel`/`Angle`/`DoubleAngle`/`Tee`/`SD`/
  `Encased`) and **Python-native nonlinear dynamic integration** — see the
  Tier-3 items in `docs/_pending_work.md`.
### Completed

- ✅ **Deleted stale files**: `src/fea_toolkit/opensees/builder_ss.py` and `src/fea_toolkit/model/geometry_ss.py` — old versions, never imported anywhere.

# Reference Materials

## OpenSees Verification

* [OpenSeesDigital / Portwood](https://openseesdigital.com/verifications/)
* [ASDEA](https://asdea.eu/en/home/) ([Scientific Toolkit for Opensees, STKO](https://asdea.eu/hardware/monstr-stko/))

## OpenSees Models

* [OpenSeesPy Examples](https://openseespydoc.readthedocs.io/en/latest/src/examples.html)
* [Tutorial](https://github.com/cslotboom/OpenSeesPyTutorials)
* [AmirHosseinNamadchi - OpenSeesPy-Examples](https://github.com/AmirHosseinNamadchi/OpenSeesPy-Examples)
* [Brainery Examples (Silvia Mazzoni)](https://github.com/silviamazzoni/OpenSeesPy_ExamplesManual/)