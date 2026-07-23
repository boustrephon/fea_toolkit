# Analysis Workflow

This document describes the end‑to‑end analysis pipeline for a SAP2000
model, from parsing the `.s2k` export file through to extracting static,
modal, and response‑spectrum results.

---

## Overview

```mermaid
flowchart TD
    S2K[".s2k File"]
    
    P0["<b>Phase 0 — Parser</b><br/>SAP2000Parser.parse()<br/>SAP2000Parser.get_model_data()"]
    
    P1["<b>Phase 1 — Post‑processing</b><br/>script‑level (see examples/)<br/>• Fix base restraints<br/>• Compute supplemental masses<br/>• Define loads‑only selection"]
    
    P2A["<b>Phase 2a — Preprocessor</b><br/>Preprocessor(config).run(md)<br/><br/>• Detect diaphragm levels<br/>• Split frames at joints<br/>• Apply frame end offsets<br/>• Mesh area elements<br/>• Subdivide shells (N×N)<br/>• Merge coincident nodes<br/>• Detect constraint edges<br/>• Convert area loads to edges<br/><i>Pure data — no ops.* calls</i>"]
    
    P2B["<b>Phase 2b — AnalysisBuilder</b><br/>AnalysisBuilder(mesh_model, config)<br/>builder.build_domain()<br/>builder.create_loads()<br/><br/>• Create nodes (ops.node)<br/>• Apply restraints<br/>• Create materials + sections<br/>• Create shell elements<br/>• Create frame elements<br/>• Apply loads + diaphragms<br/><i>Consumes frozen topology</i>"]
    
    P3["<b>Phase 3 — Analyses</b><br/>• Static (run_static_analysis)<br/>• Modal (run_modal_analysis)<br/>• RS (run_response_spectrum)"]
    
    P4["<b>Phase 4 — Post‑processing</b><br/>• Save to .npz / .csv<br/>• 3D viewer (ModelViewer)<br/>• Plot deformed shape / capacity<br/>• Export to Xara Tcl"]
    
    MM["<b>MeshModel</b><br/>(frozen, serialisable topology)"]
    
    S2K --> P0
    P0 -->|SAPModelData| P1
    P1 -->|Modified SAPModelData| P2A
    P2A --> MM
    MM --> P2B
    P2B -->|OpenSees model| P3
    P3 -->|Results dicts| P4
```

---

## Phase 0 — Parsing (`SAP2000Parser`)

File: `src/fea_toolkit/io/s2k_parser.py`

```python
parser = SAP2000Parser("model.s2k")
parser.parse()                         # raw table data → raw_tables
md = parser.get_model_data()           # raw_tables → SAPModelData
```

### What `parse()` does

1. Read file content (tries `utf-8`, `cp1252`, `latin-1` fallback)
2. Split content into tab‑delimited tables (`TABLE: "..."` headers)
3. Parse each table row into `{column_name: value}` dicts
4. Store in `self._raw_tables[table_name] → List[Dict]`

### What `get_model_data()` does

Converts each raw table into dataclass instances:

| Raw table | Dataclass(es) |
|---|---|
| `"OBJECT GEOMETRY"` | `Node` |
| `"JOINT RESTRAINTS"` | `Restraint` |
| `"MATERIAL PROPERTIES"` | `Material` |
| `"FRAME SECTION PROPERTIES 01 - GENERAL"` | `RectangularSection`, `ISection`, … (also carries ``modifiers`` dict: AMod, I3Mod, I2Mod, JMod) |
| `"AREA SECTION PROPERTIES"` | `ShellSection` (stores `thickness`; `A=I33=I22=J=0`) |
| `"CONNECTIVITY - FRAME"` | `FrameElement` |
| `"FRAME SECTION ASSIGNMENTS"` | ``{frame_id: section_name}`` mapping + `cardinal_point` (1–11, default 10 = centroid) |
| `"AREA ASSIGNMENTS"` | `AreaElement` + ``{area_id: section_name}`` |
| `"AREA MESH ASSIGNMENTS"` | `AreaMesh` |
| `"FRAME END LENGTH OFFSETS"` | `FrameEndOffset` (``end_i``/``end_j`` + ``off_y_i``/``off_z_i``/``off_y_j``/``off_z_j`` from cardinal pt) |
| `"JOINT LOADS"` | `JointLoad` |
| `"FRAME DISTRIBUTED LOADS"` | `FrameDistributedLoad` |
| `"AREA UNIFORM LOADS"` | `AreaUniformLoad` |
| `"LOADS – AREA GRAVITY"` | `AreaGravityLoad` |
| `"LOAD PATHS"` | `LoadPattern` |
| `"LOAD CASES"` | `LoadCase` |
| `"MASS SOURCE"` | `MassSource` |
| `"GROUPS"` | `Group` |
| `"AREA EDGE CONSTRAINT"` | `AreaEdgeConstraint` |

The result is a single `SAPModelData` object containing all parsed data.

---

## Phase 1 — Post‑processing (script level)

The user script (e.g. `local/admin_linear.py`) applies model‑specific
adjustments **before** handing the data to the builder.

```python
# ── Fix base restraints for shell‑only nodes ──────────────────────
# SAP2000 exports [1,1,1,0,0,0] for all base nodes, but ShellMITC4
# has no drilling DOF stiffness — shell‑only base nodes need full fixity.
min_z = min(nd.z for nd in md.nodes.values())
base_ids = {nd.node_id for nd in md.nodes.values() if nd.z == min_z}
# ... identify shell‑only nodes, set md.restraints[nid] = [1,1,1,1,1,1]

# ── Supplementary masses (masonry, finishes) ─────────────────────
# Compute from area × thickness × unit_weight / g
# Add to seismic mass dict

# ── Loads‑only selection ─────────────────────────────────────────
# Areas matching this selection are NOT turned into shell elements.
# Their loads are converted to equivalent frame edge loads instead.
sel = Selection(sections=["brick wall"], element_types=["Area"])

# ── Reduce loads‑only section properties ─────────────────────────
# Optional: set A, I33, I22 to near‑zero so they contribute negligible
# stiffness even if referenced by frame elements.
```

---

## Phase 2a — Preprocessor (`Preprocessor.run()`)

File: `src/fea_toolkit/opensees/preprocessor.py`

The Preprocessor performs **all topology mutations** as pure data operations
— no ``ops.*`` calls.  It consumes a ``SAPModelData`` and returns a frozen,
serialisable ``MeshModel``.

```python
from fea_toolkit.opensees.preprocessor import Preprocessor

preprocessor = Preprocessor(config)
mesh_model = preprocessor.run(md, selection=sel)
```

### What the Preprocessor does

| Step | Method | Description |
|------|--------|-------------|
| 1 | `_detect_diaphragm_levels()` | Identifies horizontal area Z‑levels for rigid diaphragms |
| 2 | `_classify_element_type()` | Classifies frames (beam/column/brace) and areas (slab/wall) |
| 3 | `_split_elements()` | Splits frames at intermediate joints; redistributes distributed loads |
| 4 | `_apply_frame_end_offsets()` | Creates offset nodes and rigid‑link records |
| 5 | `_convert_area_loads()` | Converts area uniform loads to equivalent frame edge loads |
| 6a | `_mesh_areas()` — wall‑slab detection | Finds wall‑edge nodes that lie inside slab areas — controlled by ``detect_wall_slab_intersections`` (default ``True``) |
| 6b | `_mesh_areas()` — wall‑slab split | Subdivides slabs at wall intersection lines so shell meshes share nodes — controlled by ``split_slabs_at_walls`` (default ``False``) |
| 6c | `_mesh_areas()` — regular meshing | Subdivides coarse areas per SAP2000 mesh assignments |
| 7 | `_merge_coincident_nodes()` | Deduplicates mesh‑created nodes at identical coordinates |
| 8 | `_subdivide_shells_in_model_data()` | N×N refinement of shell elements (if `subdivide_shells` config set) |
| 9 | `_split_frames_at_shell_subdiv()` | Splits frames at shell sub‑division edge nodes |
| 10 | `_detect_constraint_edges()` | Finds coarse‑fine mesh interfaces for edge constraints |
| 11 | orphan removal | Nodes only referenced by loads‑only areas moved to ``orphan_nodes`` (visualisation, not OpenSees) |

All methods operate on the data model only.  No OpenSees domain objects
are created.  The original ``SAPModelData`` is deep‑copied so it remains
untouched.

### MeshModel

File: `src/fea_toolkit/model/mesh_model.py`

A ``MeshModel`` is a frozen dataclass containing the fully prepared topology:

```python
@dataclass
class MeshModel:
    nodes: Dict[str, Node]                  # all nodes (original + mesh + split)
    frame_elements: Dict[str, FrameElement]  # split + offset children
    frame_assignments: Dict[str, str]
    area_elements: Dict[str, AreaElement]    # meshed + subdivided
    area_assignments: Dict[str, str]
    frame_dist_loads: List[FrameDistributedLoad]  # redistributed to children
    edge_loads_from_areas: List              # converted area loads
    detected_edge_pairs: List[tuple]         # detected coarse‑fine pairs (viz only)
    diaphragm_levels: List[float]            # detected storey Z‑levels
    offset_rigid_links: List[tuple]          # from frame end offsets
    frame_element_types: Dict[str, str]      # elem_id → beam/column/brace/…
    area_element_types: Dict[str, str]       # area_id → slab/wall
    materials: Dict[str, Material]
    sections: Dict[str, Section]
    groups: Dict[str, Group]
    restraints: Dict[str, Restraint]
    frame_tag_map: Dict[str, int]            # SAP ID → OpenSees tag
    material_tags: Dict[str, int]            # material name → Ops tag
    section_tags: Dict[str, int]
    shell_sec_tags: Dict[str, int]
    edge_constraint_args: List[tuple]        # for pushover re‑apply (currently unused)
    units: Dict[str, str]
    base_z: Optional[float]
    loads_only_area_ids: Set[str]            # areas excluded from shell creation
    orphan_nodes: Dict[str, Node]            # kept for visualisation, not in OpenSees
```

The MeshModel is serialisable (pickle or NPZ+JSON) and can be cached between
sessions, eliminating the need to re-run the Preprocessor.

---

## Phase 2b — Analysis Builder (`AnalysisBuilder`)

File: `src/fea_toolkit/opensees/analysis_builder.py`

The AnalysisBuilder consumes a ``MeshModel`` and creates the OpenSees domain.
No topology mutations occur here — only ``ops.*`` calls.

```python
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

builder = AnalysisBuilder(mesh_model, config)
builder.build_domain()           # create nodes, elements, sections
builder.create_loads({"DEAD": 1.0})  # apply load patterns
results = builder.run_static_analysis()
```

### What the AnalysisBuilder does

| Step | Method | Description |
|------|--------|-------------|
| 1 | `_create_nodes()` | ``ops.node()`` for all MeshModel nodes |
| 2 | `_apply_restraints()` | ``ops.fix()`` for boundary conditions |
| 3 | `_create_materials()` | ``ops.uniaxialMaterial()`` — elastic + fiber + brace truss |
| 4 | `_create_sections()` | ``ops.section('Elastic', …)`` — auto‑assigns tags |
| 5 | `_create_shell_elements()` | ``ops.element('ShellMITC4', …)`` |
| 6 | `_create_lumped_hinges()` | Zero‑length hinge elements |
| 7 | `_create_elements()` | ``ops.element('elasticBeamColumn', …)`` with geom transforms |
| 8 | `_create_loads()` | ``ops.pattern()`` + ``ops.eleLoad()`` / ``ops.load()`` |
| 9 | `_apply_rigid_diaphragms()` | ``ops.rigidDiaphragm()`` at detected levels |
| — | `compute_seismic_masses()` | Lumped mass from element self-weight + load patterns |
| — | `run_modal_analysis()` | Eigenvalue solve (4 solver strategies, Ritz pre-step) |
| — | `run_response_spectrum_analysis()` | Mode-by-mode RS with CQC/SRSS combination |
| — | `run_static_analysis()` | Static solver with auto-retry algorithm chain |

These analysis methods are also exposed on the ``OpenSeesBuilder`` facade and
are **identical** in behaviour to the legacy single‑stage builder.

---

## Phase 2 — Legacy Single‑Stage Build (`OpenSeesBuilder.build()`)

File: `src/fea_toolkit/opensees/builder.py`

The original single‑stage path bundles the Preprocessor and AnalysisBuilder
into one call.  The two‑stage path is the default (``use_preprocessor=True``).
The legacy path (``use_preprocessor=False``) is **deprecated** and will be
removed in a future release.

```python
b = OpenSeesBuilder(md, config)
b.build(selection=sel)                       # two‑stage (default)
# or explicitly
b2 = OpenSeesBuilder(md, {"use_preprocessor": True, …})
b2.build(selection=sel)                      # two‑stage
```

### Legacy build order
  2b  │ ops.wipe()               │ ops.wipe()                      │
      │                          │ ops.model('basic','-ndm',3,'-ndf',6)
  2c  │ _create_nodes()          │ ops.node(tag, x, y, z)          │ SAP2000 nodes only
  2d  │ _apply_restraints()      │ ops.fix(tag, ux,uy,uz, rx,ry,rz)
  2e  │ _create_materials()      │ ops.uniaxialMaterial(…)         │
  2f  │ _create_sections()       │ ops.section('Elastic', tag, …)  │ includes ShellSections
  2g  │ _split_elements()        │ geometry.split_elements()       │ 🔗 Check B
  2h  │ _apply_frame_end_offsets()│ rigid‑link records             │
  2i  │ _convert_area_loads()    │ area → frame edge loads         │
  2j  │ _mesh_areas()            │ geometry.mesh_area_elements()   │ 🔗 Check C
      │                          │ ops.fix() for base mesh nodes   │
  2k  │ _create_shell_elements() │ ops.element('ShellMITC4', …)    │
      │                          │ ops.section('ElasticMembranePlateSection', …)
  2l  │ _create_lumped_hinges()  │ zeroLengthSection hinges        │ optional
  2m  │ _create_elements()       │ ops.element('elasticBeamColumn')│ 🔗 Check D
      │                          │ ops.geomTransf('Linear', …)     │
  2n  │ _create_loads()          │ ops.pattern() / ops.load() / …  │ patterns & self‑weight
  2o  │ _setup_recorders()       │ optional opstool recorders      │
```

### Detailed step descriptions

#### 2a–2b — Reset

Restores pristine frame/area/node data from snapshots taken when
the `OpenSeesBuilder` was constructed.  Ensures repeated `build()`
calls always start from the same original geometry.

#### 2c — `_create_nodes()`

Creates an OpenSees node for every `Node` in the SAP2000 model data.
Mesh nodes created later (step 2j) are added to OpenSees as they
are generated.

#### 2d — `_apply_restraints()`

Applies `ops.fix()` for every `Restraint` defined in the SAP2000 model.

#### 2e — `_create_materials()`

Creates `uniaxialMaterial` (e.g. `Concrete01`, `Steel01`) for each
`Material` that has an `E_mod > 0`.  Materials are used both for
frame sections and for `ElasticMembranePlateSection` shell sections (which
extract `E_mod` and Poisson's ratio).

> ⚠️ **Note on shell sections:** `ElasticPlateSection` creates a singular
> stiffness matrix for `ShellMITC4` in OpenSeesPy 3.x.  The builder uses
> `ElasticMembranePlateSection` instead, which provides correct membrane
> + bending stiffness.  See the [Builder Reference](builder_reference.md).

#### 2f — `_create_sections()`

Creates `ops.section('Elastic', tag, E, A, I33, I22, G, J)` for
**every** section, including `ShellSection` types.  For `ShellSection`,
A/I33/I22/J are computed from thickness (since the parsed values are zero).
Shell elements **do not** use these Elastic sections — they use a separate
`ElasticPlateSection` created in step 2k.

#### 2g — `_split_elements()`  *(Connectivity Check B)*

Calls `geometry.split_elements()` to subdivide frame elements at any
intermediate SAP2000 node that lies exactly on the element's segment
(i.e. `AtJoints=True` in the SAP2000 auto‑mesh settings).  Uses a
`SpatialGrid` for fast bounding‑box lookup.

- Original element is marked `.inactive = True`
- Child elements get new element IDs, carry the parent's section assignment
- Distributed loads are split proportionally across children
- Results stored in `self.split_elements`, `self.split_assignments`,
  `self.split_dist_loads`

Frame elements that do NOT have `AtJoints=True` are left unchanged.

#### 2h — `_apply_frame_end_offsets()`

Creates offset nodes and records rigid‑link entries that connect
the physical member ends to the structural nodes.  The rigid links
are created as `elasticBeamColumn` elements with a very stiff section
in step 2m.

#### 2i — `_convert_area_loads()`

For loads‑only areas (those matching the selection), area uniform
loads are converted to equivalent beam‑edge distributed loads.
This is done by finding the frame element that shares each area edge
and allocating the load proportionally.

#### 2j — `_mesh_areas()`  *(Connectivity Check C)*

Calls `geometry.mesh_area_elements()` to subdivide area elements
per the `AreaMesh` settings (`max_size`, `MeshType`).

- Creates sub‑area elements with unique IDs (e.g. `"3_sub_1"`)
- Creates mesh nodes at subdivision points (unique tags)
- Updates `self.model.nodes` with new mesh nodes
- Creates OpenSees nodes for all mesh nodes
- **Supported‑edge restraint propagation**: any mesh‑created node that lies on
  a base frame edge whose endpoints are fully fixed will inherit
  ``ops.fix(1,1,1,1,1,1)``.  Interior mesh nodes and nodes on unsupported
  edges are **not** automatically restrained — they remain free unless
  explicitly fixed elsewhere.

#### 2k — `_create_shell_elements()`

Creates `ShellMITC4` elements for all meshed area elements that are
**not** in the loads‑only selection.

- Shell sections use `ops.section('ElasticPlateSection', tag, E, nu, thickness, rho)`
  — a separate section tag space from the frame sections.  Uses
  `ElasticMembranePlateSection` (not `ElasticPlateSection` — see note above)
- `ShellMITC4` (quad) or `ShellMITC4` with a repeated last node (tri)
- Creates `frame_tag_map` mapping element IDs to OpenSees tags

#### 2l — `_create_lumped_hinges()`

Optional (activated by `config['hinge_model'] = 'lumped'`).  Replaces
selected frame elements with a three‑part assembly:

```
structural_node_i → hinge_i → elastic_mid → hinge_j → structural_node_j
```

Coincident hinge nodes have translation DOFs tied with `equalDOF` so
only rotations are released.  Hinge backbones use `Hysteretic` material
matched to ASCE 41 rotation limits.

#### 2m — `_create_elements()`  *(Connectivity Check D)*

Creates OpenSees frame elements using either `split_elements` (if
splitting occurred) or the original `frame_elements`.

- Geometric transformation via `ops.geomTransf('Linear', tag, ref_x, ref_y, ref_z)`
  using the SAP2000 angle → reference vector mapping
- Optional brace subdivision with initial imperfections
- Creates rigid‑link elements for end offsets and brace offsets
- Finally calls `ops.element('elasticBeamColumn', tag, node_i, node_j, transf, sec)`

#### 2n — `_create_loads()`

Creates load patterns, time series, and applies loads:

- Joint loads (`ops.load(tag, *components)`)
- Frame distributed loads (converted to nodal loads at integration points)
- Self‑weight (`ops.eleLoad` for each element)
- Area gravity loads (converted to equivalent nodal loads)
- If `pattern_scales` is provided, only the specified patterns
  are created at the given scale factors

#### 2o — `_setup_recorders()`

Optional: configures `opstool` recorders for extracting detailed
element forces and section responses during analysis.

---

## Phase 3 — Analyses

### 3a — Static analysis

```python
res = b.run_static_analysis(pattern_scales={"DEAD": 1.0, "LL": 1.0})
```

Flow:

1. If `pattern_scales is not None` → **rebuilds** the model via
   `self.build(pattern_scales=..., selection=sel)`, which triggers
   the full Phase 2 pipeline with only the specified load patterns
2. Sets up the solver:
   - `ops.constraints('Transformation')`
   - `ops.numberer('RCM')`
   - `ops.system('BandGeneral')` (or `SparseGeneral` via config)
   - `ops.test('NormDispIncr', ...)`
   - `ops.algorithm('Newton')`
   - `ops.integrator('LoadControl', ...)`
3. `ops.analyze(n_sub)` — solves the system
4. Extracts results:
   - `nodal_displacements`: `{node_tag: (dx, dy, dz)}`
   - `nodal_reactions`: `{node_tag: (fx, fy, fz, mx, my, mz)}`
   - `summed_reactions`: total force/moment vector
   - `load_totals`: applied load totals per pattern

### 3b — Modal analysis

```python
modal = b.run_modal_analysis(num_modes=6, eigen_solver="default")
```

- Rebuilds model with the mass‑associated load pattern
- `ops.eigen(num_modes)` via ARPACK (default) or fullGenLapack fallback
- Returns periods, frequencies, mode shapes

### 3c — Response‑spectrum analysis

```python
rs = b.run_response_spectrum_analysis(
    num_modes=12,
    modal_periods=periods,
    spectrum_periods=T_sp,
    spectrum_accels=Sa_sp,
    direction="X",
    damping_ratio=0.05,
)
```

- CQC modal combination
- Returns shear, moment, drift ratios (CQC/SRSS combined)

### 3d — Pushover analysis

```python
push = b.run_pushover_analysis(
    gravity_patterns={"DEAD": 1.0},
    lateral_load_type="uniform",   # "uniform", "triangular", or "mode1"
    lateral_direction="X",
    mode_shapes=shapes,            # required for "mode1"
    mode_index=0,
    max_disp=0.3, num_steps=50,
)
```

Three lateral load patterns (config `pushover.patterns`):

| Pattern | Formula | Description |
|---------|---------|-------------|
| `uniform` | `Fᵢ = mᵢ` | Mass-proportional (uniform acceleration) |
| `triangular` | `Fᵢ = mᵢ · hᵢᵏ` | ASCE 7 ELF, k from period |
| `mode1` | `Fᵢ = mᵢ · \|φᵢ\|` | Modal (absolute mode shape) |

Solver settings (v1-compatible): 1e-4 tolerance, 20 iterations, energy
norm.  Yield detection uses **stiffness-change** (primary: secant k < 50%
of initial or max relative drop ≥ 30%) with **equal-energy** fallback.

The mode for `mode1` pushover is auto-selected as the one with highest
mass participation.  An optional RS-based check warns when the mass-based
mode differs from the RS-dominant mode.

---

## Connectivity Checks

The following check points are built into the workflow to catch modelling
errors early:

| Label | Location | What it checks | Method |
|---|---|---|---|
| **A** | After Phase 1 (pre‑build) | Orphan SAP2000 nodes, shell‑only base nodes, zero‑area sections | `OpenSeesBuilder.check_model_connectivity()` |
| **B** | After `_split_elements()` 2g | Zero‑length split children, duplicate coordinate nodes | `OpenSeesBuilder.check_split_connectivity()` |
| **C** | After `_mesh_areas()` 2j | Base mesh nodes without restraint, perimeter nodes with ≤2 shells | `OpenSeesBuilder.check_mesh_connectivity()` |
| **D** | Before `_create_elements()` 2m | Unassigned frame elements, missing sections | (validated during element creation) |
| **E** | After `build()` before analysis | Full node‑element connectivity summary, tree plot | `OpenSeesBuilder.diagnose_singularity()` |

For details on each check method and its output, see the
[Builder Reference](builder_reference.md).

---

## Investigating Model Issues (Diagnostic Workflow)

When mode shapes, forces, or displacements show unexpected behaviour, the
following investigative pattern has proven effective:

1. **Quantitative detection** — Run automated diagnostics like
   `find_disconnected_nodes()` (Z-score outlier detection on eigenvectors) or
   `find_constraint_edges()` (report shared-edge tears).
2. **Probe with code** — Write a short Python script querying `ops.nodeCoord()`,
   element tags, and area assignments around the anomaly to gather raw data.
3. **Visualise** — Generate a PyVista colour-coded 3D scene (e.g. wall nodes in
   red, slab nodes in blue) to make geometric misalignments visible.
4. **Confirm with the user** — Share the visual explanation so the root cause is
   agreed before coding a fix.
5. **Decide on fix scope** — Generic fix, case-specific fix, or accepted limitation.

> **Concrete example**: The Z=13.28 wall-slab misalignment in the Admin
> Building was found this way. Mode shapes showed "tears" despite node merging.
> `find_constraint_edges()` showed no tear at X=55. A probe revealed slab mesh
> nodes at X=54,56,58,60 — the wall at X=55 had no slab-side counterpart because
> the slab's `max_size=2.0` mesh happened to fall on even X coordinates. The
> visual confirmed the issue before implementing a geometric coincidence scan.

The diagnostic workflow pattern is documented above in this section.

---

## Data flow summary

```
  .s2k ──→ SAP2000Parser ──→ SAPModelData ──→ Preprocessor ──→ MeshModel ──→ AnalysisBuilder ──→ OpenSees model
                │                  │                  │              │                │                  │
            raw tables        dataclass tree     pure data ops   frozen,         Ops domain        in‑memory
                                                  (no Ops)      serialisable    creation          analysis

                         ┌──────────────────────────────────────────────────┐
                         │           ModelViewer (plotting/viewer.py)      │
                         │  show_model · overlay_deformed · overlay_forces │
                         │  highlight_elements · highlight_nodes · annotate│
                         │  screenshot · export_html · show               │
                         └──────────────────────────────────────────────────┘
```

The :doc:`ModelViewer </viewer>` can be pointed at either the
``SAPModelData`` or the builder to produce interactive 3D views
for discussion and debugging.  See ``docs/viewer.md`` for the full API.

