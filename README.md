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
| **Pytest Suite** | ✅ Passing | [153 tests](tests/README.md): dataclass construction, geometry utilities, section enrichment, modal analysis, pushover, parser integration, and edge cases. |

#### 3. Notable Design Decisions

- **String IDs** – Node and frame IDs are kept as strings (SAP2000 labels), with numeric `tag` fields for OpenSees.
- **Relative Load Positions** – `FrameDistributedLoad` stores `rdist_a` and `rdist_b` (0..1) for child elements, matching OpenSees `aOverL`/`bOverL`.
- **Spatial Grid** – Efficient nearest‑neighbour search for splitting.
- **Trapezoidal Splitting** – Exact redistribution of varying loads using `trapezoidal_force_split`.
- **Configurable Builder** – Element type, integration points, splitting, verbosity can be set via `config` dict.
- **MASS SOURCE** – The `MASS SOURCE` table is parsed by `_get_mass_sources()` which groups rows by MassSource name, **accumulates** multipliers when the same LoadPat appears on multiple rows, and stores the result in `SAPModelData.mass_sources`. The builder's `compute_seismic_masses()` then uses this to derive nodal masses (self‑weight from `Elements=True`, load‑based from `Loads=True` + `LoadPat`/`Multiplier` pairs).
- **Modal & RS Analysis** – `run_modal_analysis()` uses `ops.eigen('-fullGenLapack', …)`. `run_response_spectrum_analysis()` and `extract_element_rs_forces()` call `ops.responseSpectrumAnalysis()` mode‑by‑mode and extract element forces via `ops.eleResponse(eid, 'forces')` (global system). CQC follows Der Kiureghian's formula. `add_missing_mass_correction()` computes the rigid response from residual mass at short‑period spectral acceleration.

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
   - **Nonlinear Time History** – add ground motion input and integration schemes.

5. **Joint Modeling** (for concrete frames)  
   - Extend parser to recognise joint elements (if present in SAP2000).  
   - Implement `Joint2D` and `beamColumnJoint` elements in `OpenSeesBuilder`.

6. **Rhino Importer Refactoring**  
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

Hovering over `ops.node(...)`, `ops.element(...)`, `ops.analyze(...)`, etc. will now show parameter names, types, and descriptions — and all false‑positive attribute‑access squiggles will disappear.