# fea_toolkit
A toolkit for importing and exporting FEA information


## Project Summary: SAP2000 → OpenSees Converter

### Overview

The goal is to create a Python package `fea_toolkit` that:

- Parses SAP2000 `.s2k` text files (and eventually ETABS) into a common intermediate data model.
- Enriches section properties using a manufacturer database.
- Splits frame elements at joints (and optionally at frame intersections) with parent‑child tracking.
- Splits distributed loads (uniform, linear, trapezoidal) to match the sub‑elements.
- Builds OpenSees models with configurable element types (`elasticBeamColumn`, `forceBeamColumn`, etc.), applies loads, and runs analyses (static, modal, pushover, time‑history).
- Exports to Rhino for visualisation (via a separate module).

We have made substantial progress on the core pipeline, with a focus on correctness and extensibility.

---

### Current Implementation State

#### 1. Package Structure (Modern `src/` layout)

```
~/Projects/fea_toolkit/
├── data/                     # (private) section_dict.pkl
├── examples/                 # basic_usage.py, README
├── src/fea_toolkit/
│   ├── __init__.py
│   ├── io/
│   │   └── s2k_parser.py     # SAP2000Parser with string IDs, numeric tags
│   ├── model/
│   │   ├── sap_data.py       # Dataclasses: Node, FrameElement, LoadPattern, JointLoad, FrameDistributedLoad (with rdist_a/b), SAPModelData
│   │   ├── sections.py       # SectionLibrary with unit conversion (mm/in)
│   │   └── geometry.py       # SpatialGrid, point_on_segment, trapezoidal_force_split, split_elements (joint splitting + load redistribution)
│   ├── opensees/
│   │   └── builder.py        # OpenSeesBuilder: creates nodes, restraints, sections, splits elements, builds elements, applies loads (using relative positions), runs linear static analysis
│   └── rhino/                # (placeholder – to be refactored)
├── tests/                    # pytest suite (test_parser, test_model, test_dummy)
├── pyproject.toml
└── README.md
```

#### 2. Key Components Implemented

| Component | Status | Notes |
| :--- | :--- | :--- |
| **`SAP2000Parser`** | ✅ Complete | Parses .s2k into raw tables; converts to `SAPModelData` with string IDs; assigns numeric tags; extracts materials, sections, frame connectivity, restraints, load patterns, joint loads, distributed loads, auto‑mesh settings. |
| **`SAPModelData`** | ✅ Complete | Contains all model data with mutable defaults via `field(default_factory=...)`; includes `units` dict (`{'F','L','T'}`) with default `{'F':'N','L':'mm','T':'C'}`. |
| **`SectionLibrary`** | ✅ Complete | Loads section catalogue pickle; converts units to match model (`mm` or `in`); enriches `Section` objects with `Z33`, `Z22`, dimensions, etc. |
| **`geometry.split_elements`** | ✅ Complete | Splits elements at joints when `AtJoints=True`; marks parent as `inactive`; creates child elements with new numeric tags; redistributes distributed loads using `trapezoidal_force_split`; stores relative positions (`rdist_a`, `rdist_b`) in child loads. |
| **`OpenSeesBuilder`** | ✅ Complete | Builds OpenSees model: nodes (with numeric tags), restraints, elastic sections, elements (skips inactive), loads (patterns, joint loads, distributed loads with 3‑ or 8‑argument `eleLoad`), linear static analysis; returns nodal displacements. |
| **Load Handling** | ✅ Complete | Supports uniform and linear/trapezoidal distributed loads with global direction (gravity, X, Y, Z); projects onto local axes using `get_SAP_vecxz`; handles split loads. |
| **Parent‑Child Tracking** | ✅ Complete | Each split element stores `parent_id`, `child_ids`, `t_locations`; inactive flag prevents building of parent. |
| **Unit Conversion** | ✅ Complete | `SectionLibrary` converts lengths, areas, inertias between `in` and `mm` based on catalogue metadata. |
| **Visualisation (opsvis)** | ✅ Quick test | `basic_usage.py` can show line‑based model; extrusion not implemented. |
| **Pytest Suite** | ✅ Passing | Basic tests for parser, dummy, and model. |

#### 3. Notable Design Decisions

- **String IDs** – Node and frame IDs are kept as strings (SAP2000 labels), with numeric `tag` fields for OpenSees.
- **Relative Load Positions** – `FrameDistributedLoad` stores `rdist_a` and `rdist_b` (0..1) for child elements, matching OpenSees `aOverL`/`bOverL`.
- **Spatial Grid** – Efficient nearest‑neighbour search for splitting.
- **Trapezoidal Splitting** – Exact redistribution of varying loads using your proven `trapezoidal_force_split`.
- **Configurable Builder** – Element type, integration points, splitting, verbosity can be set via `config` dict.

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
   - Parse `LOAD CASES` and `LOAD COMBINATIONS` tables.  
   - In `OpenSeesBuilder`, allow the user to select which load cases/combinations to run.  
   - Implement combination factors (e.g., `1.2 DL + 1.6 LL`).

4. **Advanced Analyses**  
   - **Modal Analysis** – implement eigenvalue extraction (`ops.eigen`).  
   - **Response Spectrum** – apply spectral loads using modal combination rules.  
   - **Nonlinear Static Pushover** – implement with `forceBeamColumn` and `HingeRadau` integration.  
   - **Nonlinear Time History** – add ground motion input and integration schemes.

5. **Joint Modeling** (for concrete frames)  
   - Extend parser to recognise joint elements (if present in SAP2000).  
   - Implement `Joint2D` and `beamColumnJoint` elements in `OpenSeesBuilder`.

6. **Rhino Importer Refactoring**  
   - Move your `sap2000_import_v8.py` into `src/fea_toolkit/rhino/importer.py`.  
   - Adapt it to read `SAPModelData` (instead of raw JSON) and use the split data for visualisation.

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
    - Add tests for `split_elements` with trapezoidal loads.  
    - Add integration tests for `OpenSeesBuilder` using small test models.

#### Low Priority

11. **Parallel Processing** – For large models, consider splitting/analysis parallelisation.
12. **Graphical User Interface** – Not planned, but could be added later.
13. **Other FEA Formats** – Abaqus `.inp`, Ansys `.cdb` – future extensions.

---

### Conclusion

The **SAP2000 → OpenSees pipeline** is now **largely functional**. You can parse a model, split elements and loads, build an OpenSees model, and run a linear static analysis. The code is modular, well‑structured, and ready for the next phases: frame‑frame intersections, ETABS support, and advanced analyses.

The project is well on track to meet your original goals. Let me know which of the remaining tasks you would like to tackle next, and I will provide the necessary code and guidance.