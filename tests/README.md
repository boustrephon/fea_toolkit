# Test Suite — `fea_toolkit`

Run all tests from the project root:

```bash
pytest tests/ -q
```

Or a specific file:

```bash
pytest tests/test_model.py -q
pytest tests/test_parser.py -q
```

---

## `test_model.py` — Model layer tests

Tests for dataclasses, geometry utilities, section types, the Selection class,
CQC combination, and plotting imports.

### Organisation

| Section | Classes | What it covers |
|---|---|---|
| **Dataclasses** | `TestNode`, `TestRestraint`, `TestMaterial`, `TestSection`, `TestFrameElement`, `TestAreaElement`, `TestLoadPattern`, `TestJointLoad`, `TestFrameDistributedLoad`, `TestGravityLoad`, `TestAreaGravityLoad`, `TestMassSource`, `TestCoordSys`, `TestConstraint`, `TestLoadCase`, `TestLoadCombination`, `TestGroup` | Construction with defaults and non-default values for each dataclass in `sap_data.py`. |
| **Section subclasses** | `TestSectionSubclasses` | 14 section types (`ISection`, `PipeSection`, `BoxSection`, … `ShellSection`), `to_fiber_patches()`, `shape_id` mapping, and base-class error handling. |
| **SAPModelData** | `TestSAPModelData` | Creation, default units, custom units, and new load fields (`area_gravity_loads`, `frame_gravity_loads`). |
| **Geometry** | `TestGetSAPVecxz`, `TestRotateAboutAxis`, `TestPointOnSegment`, `TestComputeTLocation`, `TestInterp`, `TestListInterp`, `TestTrapezoidalForceSplit`, `TestSpatialGrid`, `TestBeamLoadToNodalLoads`, `TestConvertAreaLoadsToEdgeLoads` | Vector utilities, interpolation, load splitting, spatial grid, and area-to-edge load conversion. |
| **Section enrichment** | `TestSectionLibrary`, `TestEnrichProgress` | `SectionLibrary` unit conversion and catalogue enrichment. |
| **Selection** | `TestSelection` | 20 tests — filtering by `element_types`, `sections`, `materials`, `groups`, `element_ids`; AND/OR logic; dict and load filter methods. |
| **Selection filter_model** | `TestSelectionFilterModel` | 12 tests — self-contained subset creation for frames, areas, combined types; group pruning; empty results; immutability. |
| **CQC** | `TestCqcCombine` | Single-mode, two uncorrelated modes, and identical-mode CQC combination. |
| **Plotting imports** | `TestPlottingImports` | Import resolution and graceful fallback for plotting functions. |
| **Mass source parsing** | `TestMassSourceParser` | Integration test verifying MASS SOURCE parsing from the sample `.s2k` file. |
| **Builder integration** | `TestChimneyStatic`, `TestChimneyModal`, `TestChimneyRS`, `TestMissingMass`, `TestSeismicMasses`, `TestChimneyPlotting` | End-to-end tests using the chimney model: static analysis equilibrium, modal properties, response spectrum, missing mass correction, and force extraction. |

---

## `test_parser.py` — Parser tests

Tests for `SAP2000Parser` — parsing `.s2k` files and converting to `SAPModelData`.

| Test | What it covers |
|---|---|
| `test_parse_sample` | Parses the sample `.s2k` fixture and checks raw tables exist. |
| `test_get_model_data` | Conversion to `SAPModelData` from the sample fixture. |
| `test_parse_from_example` | Inline s2k content with `JOINT COORDINATES`. |
| `test_parse_area_gravity_loads` | `AREA LOADS - GRAVITY` table → `AreaGravityLoad` objects. |
| `test_parse_frame_gravity_loads` | `FRAME LOADS - GRAVITY` table → `GravityLoad` objects. |
| `test_parse_area_section_properties` | `AREA SECTION PROPERTIES` → `ShellSection` with thickness; area element thickness populated. |
| `test_parse_area_uniform_generic_dispatch` | Generic `AREA LOADS - UNIFORM` dispatch still works. |
| `test_unknown_area_load_type_skipped` | Unknown `AREA LOADS - MYSTERY` silently skipped. |

---

## Fixtures

| File | Purpose |
|---|---|
| `fixtures/sample.s2k` | A real SAP2000 export of a lattice frame model (used by parser integration tests). |
| `fixtures/sample.json` | Pre-parsed raw table dump for faster test loading. |
| `fixtures/sample_2.s2k` | Second model variant for additional parser coverage. |
| `fixtures/sample.split.json` | Split-element version of the sample model. |

---

## Adding tests

1. Follow the existing pattern — one `Test*` class per feature area, with
   descriptive method names.
2. Add docstrings to test classes and methods explaining what each test
   verifies.
3. Prefer inline s2k content (`tmp_path`) for parser tests over external
   fixtures where possible.
4. Run the full suite before committing:

   ```bash
   pytest tests/ -q
   ```
