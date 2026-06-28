# fea_toolkit
A toolkit for importing and exporting FEA information


## Project Summary: SAP2000 → OpenSees Converter

### Overview

The goal is to create a Python package `fea_toolkit` that:

- Parses SAP2000 `.s2k` text files (and eventually ETABS) into a common intermediate data model.
- Enriches section properties using a manufacturer database.
- Splits frame elements at joints (and optionally at frame intersections) with parent‑child tracking.
- Splits distributed loads (uniform, linear, trapezoidal) to match the sub‑elements.
- Builds OpenSees models with configurable element types (`elasticBeamColumn`, `forceBeamColumn`, etc.), applies loads, and runs linear static analysis (modal, pushover, and time‑history are planned).
- Exports to Rhino for visualisation (via a separate module).

---

### Current Implementation State

#### 1. Package Structure (Modern `src/` layout)

```
~/Projects/fea_toolkit/
├── data/                     # (private) section_dict.pkl
├── examples/                 # [`examples/README.md`](examples/README.md)
├── src/fea_toolkit/
│   ├── __init__.py
│   ├── io/
│   │   ├── s2k_parser.py     # SAP2000Parser with string IDs, numeric tags
│   │   └── helper.py         # File‑chooser utilities (tkinter/macOS)
│   ├── model/
│   │   ├── sap_data.py       # Dataclasses: Node, FrameElement, LoadPattern, JointLoad, FrameDistributedLoad, SAPModelData, and others
│   │   ├── sections.py       # SectionLibrary with unit conversion (mm/in)
│   │   └── geometry.py       # SpatialGrid, point_on_segment, trapezoidal_force_split, split_elements (joint splitting + load redistribution)
│   ├── opensees/
│   │   └── builder.py        # OpenSeesBuilder: creates nodes, restraints, sections, splits elements, builds elements, applies loads (using relative positions), runs linear static analysis
│   └── rhino/                # Rhino visualisation (placeholder – to be refactored)
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
| **`OpenSeesBuilder`** | ✅ Complete | Builds OpenSees model: nodes, restraints, elastic sections, elements (skips inactive), loads (patterns, joint loads, distributed loads with N‑segment decomposition), linear static analysis; returns nodal displacements, reactions, load totals.
| **Modal Analysis** | ✅ Complete | `run_modal_analysis()` — eigenvalue extraction (`ops.eigen`), modal properties table with frequencies, periods, participating masses & ratios.
| **Response Spectrum** | ✅ Complete | `run_response_spectrum_analysis()` — mode‑by‑mode RS analysis using GB50011 (or user‑supplied) spectrum, CQC/SRSS combination, base shear + moment.
| **Element‑Level RS Forces** | ✅ Complete | `extract_element_rs_forces()` — CQC‑combined moments/shears per element, sorted by elevation.
| **Missing Mass Correction** | ✅ Complete | `add_missing_mass_correction()` — rigid response from residual modal mass, adds to CQC base shear/moment.
| **Seismic Masses** | ✅ Complete | `compute_seismic_masses()` — lumps element self‑weight and load‑based masses per MASS SOURCE (Elements/Loads flags). |
| **Load Handling** | ✅ Complete | Supports uniform and linear/trapezoidal distributed loads with global direction (gravity, X, Y, Z); projects onto local axes using `get_SAP_vecxz`; handles split loads. |
| **Parent‑Child Tracking** | ✅ Complete | Each split element stores `parent_id`, `child_ids`, `t_locations`; inactive flag prevents building of parent. |
| **Unit Conversion** | ✅ Complete | `SectionLibrary` converts lengths, areas, inertias between `in` and `mm` based on catalogue metadata. |
| **Visualisation (opsvis)** | ✅ Quick test | `basic_usage.py` can show line‑based model; extrusion not implemented. |
| **Pytest Suite** | ✅ Passing | [170 tests](tests/README.md): dataclass construction, geometry utilities, section enrichment, modal analysis, pushover, CSM performance point, brace buckling, SciPy eigenvalue benchmark, parser integration, and edge cases. |
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
- **Brace fatigue** – Optional `Fatigue` material wrapper for cyclic degradation, controlled via `brace_fatigue`, `brace_fatigue_E0`, `brace_fatigue_m` config options.
- **Pushover spectrum override** – The pushover/CSM analysis can use a different spectrum from the response spectrum analysis via `pushover.spectrum` in the CONFIG. Falls back to the top-level `spectrum` if not specified.
- **Linear case auto-detection** – `run_linear_cases()` now reads LinStatic load cases from the SAP2000 model automatically. Users can override via `linear.cases` in the CONFIG.
- **Mass computation includes area elements** – `compute_seismic_masses()` now includes area element self-weight, area gravity loads (MultiplierZ), and area uniform loads. Previously only frame elements were included.
- **Spectrum damping fix** – The GB50011 spectrum now applies the damping reduction factor `η₂` to the ascending branch (T ≤ 0.1s) as well as the plateau and descending branch, matching the reference `GB_spectrum()` function.
- **SAP2000 data extraction** – The parser now extracts: load case definitions, response spectrum case data (general + load assignments), modal case data, AUTO seismic/wind table data, and material damping parameters.

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
| **`ChannelSection`** | `Channel`, `Steel Channel`, `Concrete Channel` | `depth`, `bf`, `tf`, `tw` | 🚧 Placeholder |
| **`AngleSection`** | `Angle`, `Steel Angle`, `Concrete Angle` | `depth`, `bf`, `tf`, `tw` | 🚧 Placeholder |
| **`DoubleAngleSection`** | `Double Angle`, `Steel Double Angle` | `depth`, `bf`, `tf`, `tw`, `dis` | 🚧 Placeholder |
| **`TeeSection`** | `Tee` | `depth`, `bf`, `tf`, `tw` | 🚧 Placeholder |
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

#### High Priority

1. **Frame‑Frame Intersection Splitting**  
   - Implement `AtFrames=True` splitting at intersections between frames.  
   - Requires finding intersection points (grid‑based) and inserting new nodes, then splitting both elements and redistributing loads.

2. **ETABS Parser**  
   - Add `ETABSParser` class (following `SAP2000Parser` interface) to parse `.$ET` / `.E2K` files.  
   - Map ETABS‑specific table names to `SAPModelData` fields.  
   - ETABS uses different load nomenclature – adapt accordingly.

3. **Load Combinations and Analysis Types**  
   - ~~`MassSource`~~ ✅ Parsed by `_get_mass_sources()` and stored in `SAPModelData.mass_sources`.  
   - `LoadCase`, `LoadCombination` dataclasses defined in `sap_data.py` — parsing of `LOAD CASES` and `LOAD COMBINATIONS` tables still needed.  
   - In `OpenSeesBuilder`, allow the user to select which load cases/combinations to run with combination factors (e.g., `1.2 DL + 1.6 LL`).

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
   - **Nonlinear Time History** – add ground motion input and integration schemes.

5. **Joint Modeling** (for concrete frames)  
   - Extend parser to recognise joint elements (if present in SAP2000).  
   - Implement `Joint2D` and `beamColumnJoint` elements in `OpenSeesBuilder`.

6. **Brace gusset plates / joint offsets**  
   - Model gusset plate flexibility as rotational springs at brace ends.  
   - Add rigid offset segments between working point and brace physical end.  
   - See `docs/pushover_analysis.md` for discussion of approaches.

7. **Rhino Importer Refactoring**  
   - The `rhino/` package stub exists at `src/fea_toolkit/rhino/`.  
   - Move `sap2000_import_v8.py` into `src/fea_toolkit/rhino/importer.py`.  
   - Adapt it to read `SAPModelData` (instead of raw JSON) and use the split data for visualisation.

### Getting started

See [`examples/README.md`](examples/README.md) for quick-start examples.

#### Medium Priority

7. **Improved Load Handling**  
   - Support for **point loads** on frames (`FRAME LOADS - POINT`).  
   - Support for **temperature loads** (if needed).  
   - Option to convert linear loads to uniform (simplification) via config flag.

8. **Result Extraction**  
   - Extend `run_analysis` to return reactions, internal forces, and mode shapes.  
   - Integrate `opstool` more fully for result post‑processing.

9. **Documentation**  
   - Write full API docs (Google style already in code).  
   - Create a user guide (examples, how to run different analyses).

10. **Testing**  
    - `test_parser.py` covers basic parsing; `test_model.py` is yet to be populated.  
    - Add tests for `split_elements` with trapezoidal loads.  
    - Add unit tests for `SectionLibrary`, `SAPModelData` dataclasses, and geometry utilities.  
    - Add integration tests for `OpenSeesBuilder` using small test models.

#### Low Priority

11. **Parallel Processing** – For large models, consider splitting/analysis parallelisation.
12. **Graphical User Interface** – Not planned, but could be added later.
13. **Other FEA Formats** – Abaqus `.inp`, Ansys `.cdb` – future extensions.

---

### Conclusion

The **SAP2000 → OpenSees pipeline** is now **largely functional**. You can parse a model, split elements and loads, build an OpenSees model, and run a linear static analysis. The code is modular, well‑structured, and ready for the next phases: frame‑frame intersections, ETABS support, and advanced analyses.

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

Hovering over `ops.node(...)`, `ops.element(...)`, `ops.analyze(...)`, etc. will now show parameter names, types, and descriptions — and all false‑positive attribute‑access squiggles will disappear.

---

## Refactoring Roadmap

The following items are the highest-impact improvements identified during a codebase-wide review:

### High Priority

1. **Create `fea_toolkit/spectrum.py`**
   Extract `_gb50011_spectrum()`, `_build_spectrum()`, `_interp_sa()`, and `plot_seismic_spectrum()` from `local/pumphouse_report.py` into a new reusable module. Then refactor `builder.py` to use these shared functions instead of computing gamma/η₁/η₂ inline in `run_pushover_analysis()` and `pushover_to_adrs()`.

2. **Create `fea_toolkit/io/report.py`**
   Move the generic SAP2000→pandas summary functions (`summarise_load_cases`, `summarise_load_patterns`, `summarise_mass_sources`, `load_pattern_totals`, `section_summary`, `area_section_summary`, `material_summary`, `modal_table`, `modal_table_enhanced`, `format_linear_table`, `bounding_box`) out of `local/pumphouse_report.py`. This shrinks the 2,177-line report module by ~50 % and makes the utilities importable by any project.

3. **Create `fea_toolkit/utils.py`**
   Extract `_deep_merge()`, `_infer_loads()`, `_build_gravity_patterns()`, `_pick_wind()`, and `brace_buckling_check()` from `pumphouse_report.py`. The Euler buckling check is an analytical computation independent of OpenSees and should be importable without a builder instance.

### Medium Priority

4. **Create `fea_toolkit/plotting/report.py`**
   Move `plot_pushover_curves()`, `plot_modal_participation()`, `plot_rs_modal_analysis()`, and `plot_csm_4panel()` from `pumphouse_report.py` into the plotting subpackage alongside the existing `viz.py`.

5. **Split `builder.py` (3,306 lines)**
   `OpenSeesBuilder` is a single class handling model construction, element creation, loads, pushover, modal analysis, response spectrum, CQC, mass computation, ADRS conversion, and CSM performance points. Extract analysis methods into focused modules:
   - `opensees/analysis.py` — `run_static_analysis`, `run_pushover_analysis`, `run_modal_analysis`, `run_response_spectrum_analysis`, `extract_element_rs_forces`, etc.
   - `opensees/csm.py` — `pushover_to_adrs`, `compute_performance_point`
   - `opensees/builder.py` — keep model construction (`build`, `_create_nodes`, `_create_sections`, `_create_elements`, `_create_loads`, `compute_seismic_masses`)

6. **Add unit tests**
   Critical paths with zero coverage: `compute_seismic_masses()` area-element paths, truss brace pushover (Approach B), response spectrum CQC combination, ADRS conversion, CSM performance point iteration, and all extracted `spectrum.py` / `io/report.py` functions.

### Completed

- ✅ **Deleted stale files**: `src/fea_toolkit/opensees/builder_ss.py` and `src/fea_toolkit/model/geometry_ss.py` — old versions, never imported anywhere.