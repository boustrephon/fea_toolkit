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

## `test_model.py` — Model layer tests (170 tests)

Tests for dataclasses, geometry utilities, section types, the Selection class,
CQC combination, pushover analysis, brace subdivision, and plotting imports.

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
| **Pushover analysis** | `TestPushoverBuild`, `TestPushoverRun` | Two-stage pushover (gravity + displacement-controlled lateral push) with fiber sections.  Tests correct results keys, zero lateral shear after gravity, linear monotonic pushover, and nonlinear pushover convergence. |
| **HingeRadau integration** | `TestHingeRadauIntegration` | 3 tests — plastic hinge length *Lp* computed correctly for I-sections, Pipe sections, and unknown section fallback. |
| **Brace subdivision** | `TestSubdivideElements` | 5 tests — element count after subdivision, internal node creation, sinusoidal imperfection offset, end-offset rigid link creation, and offset clamped to half-length. |
| **Euler buckling check** | `TestBraceBucklingCheck` | 3 tests — Euler formula match against analytical :math:`P_{cr}`, demand/capacity ratio with provided axial load, and ``Selection.from_brace_sections()`` detecting brace-type shapes (Pipe, Angle, etc.). |
| **Euler buckling benchmark** | `TestEulerBucklingBenchmark` | 1 test — independent SciPy eigenvalue buckling analysis of the subdivided column.  Assembles the global :math:`K` and :math:`K_g` matrices using Euler-Bernoulli beam elements, solves :math:`(K - \\lambda K_g)\\phi = 0` via ``scipy.linalg.eig``, and verifies :math:`P_{cr}` matches Euler within 5 %.  Requires ``scipy``. |
| **Brace pipeline** | `TestSubdividedBraceInPushover` | 2 tests — subdivided braces build and run a pushover without error; ``check_brace_buckling()`` works on model data (no OpenSees needed). |
| **Capacity Spectrum Method** | `TestCapacitySpectrumMethod` | 3 tests — ``pushover_to_adrs()`` returns expected keys with positive values; ``compute_performance_point()`` converges to the elastic spectral response for a linear cantilever. |

### What the buckling checks do and do not verify

#### ✅ What IS validated

| Test | What it confirms |
|---|---|
| ``test_euler_buckling_pinned`` | The analytical Euler formula :math:`P_{cr} = \\pi^2 EI / (KL)^2` is computed correctly from section properties, element length, and the *K* factor. |
| ``test_buckling_with_axial_demand`` | The demand/capacity ratio (`P_demand / P_cr`) is computed correctly when axial force data is provided. |
| ``test_subdivided_brace_builds_and_runs`` | The full builder pipeline — subdivision, imperfection, rigid end offsets → builds and runs a pushover analysis without crashing. |
| ``test_subdivide_creates_sub_elements`` | ``subdivide_elements()`` correctly splits a brace into *N* segments, marks the original as inactive, and assigns the same section. |
| ``test_end_offset_creates_rigid_links`` | When ``end_offset > 0``, offset nodes are created inside the brace and rigid-link tuples are returned. |
| ``test_imperfection_offsets_mid_node`` | The sinusoidal imperfection produces a measurable lateral offset at internal nodes. |
| ``test_from_brace_sections`` | ``Selection.from_brace_sections()`` correctly identifies Pipe, Angle, and other brace-type sections while excluding beam-type sections like I/Wide Flange. |
| ``test_eigenvalue_buckling_matches_euler`` | **Independent SciPy benchmark** — assembles the global stiffness matrix *K* and geometric stiffness *K_g* for the subdivided column, solves the generalised eigenvalue problem via ``scipy.linalg.eig``, and confirms :math:`P_{cr}` matches Euler within 5 %.  This does **not** depend on OpenSees. |

#### ❌ What is NOT validated

| Gap | Why | Practical impact |
|---|---|---|
| **Nonlinear buckling in OpenSees** | A subdivided column with ``Corotational`` + ``elasticBeamColumn`` does not show a clear buckling bifurcation — the elastic material carries load far beyond :math:`P_{cr}` without softening.  ``forceBeamColumn`` with fiber sections requires solver parameters that are too sensitive for a deterministic automated test.  However, the **SciPy eigenvalue benchmark** (:func:`TestEulerBucklingBenchmark`) independently verifies that the *discretised* subdivided column has the correct buckling load — only the *nonlinear* pushover verification is missing.  **Approach B** (truss + ``Hysteretic`` material) does not yet have dedicated unit tests; it is exercised end‑to‑end by the report module's pushover pipeline. | The subdivided imperfection approach follows established PEER OpenSees methodology.  Use ``check_brace_buckling()`` as a pre-pushover sanity check and validate against known examples for critical cases.  The eigenvalue benchmark gives confidence that the discretisation is correct.  For Approach B, validate the Hysteretic material parameters visually from the pushover curve. |
| **Configurable solver settings** | The new ``solver_test_tol``, ``solver_algorithm``, and ``gravity_num_substeps`` options are exercised by the pushover pipeline but do not yet have dedicated unit tests for each option. | The defaults preserve backward compatibility.  Custom solver settings should be validated per-model. |
| **Post-buckling softening** | Once a brace buckles, the rate of load redistribution and the residual capacity depend on mesh refinement, integration scheme, and material model — none of which are tested. | For frames where brace-softening governs system behaviour (e.g., concentrically braced frames), run mesh-sensitivity studies manually. |
| **Multi-brace interaction** | All tests use a single brace in isolation.  Braces in a real frame interact through beams, columns, and gusset plates. | System-level pushover testing with brace softening is outside the scope of unit tests.  Validate on a frame-by-frame basis. |
| **Cyclic / seismic loading** | The buckling check is static only.  Cyclic degradation (low-cycle fatigue, fracture) is not addressed. | ``check_brace_buckling()`` is a strength check, not a ductility check.  For seismic assessment, use the pushover capacity curve to evaluate ductility demands. |

### Summary

The analytical Euler check (`check_brace_buckling`) is exact and fully
tested.  The eigenvalue benchmark (`TestEulerBucklingBenchmark`) independently
verifies that the subdivided-column discretisation has the correct buckling
load via SciPy linear algebra (no OpenSees dependency).  The nonlinear
implementation (subdivided imperfection + ``Corotational``) follows standard
OpenSees practice but the automated test suite does not verify that a
specific brace buckles *exactly* at :math:`P_{cr}` in a nonlinear pushover
— this remains a manual verification step for critical applications.

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
