---
title: "ETABS (.E2K / .$ET) import"
description: "Implemented ETABS text-export parser (geometry, sections, materials, restraints, loads, combinations, mass source) plus the deferred-geometry model it is built on; M6 auto-meshing remains open."
status: "partial"
tags: [etabs, e2k, io, parser]
category: [import]
related: [json_serialization.md, parser_coverage.md, llm_guide.md, gui_roadmap.md]
---
# ETABS (`.E2K` / `.$ET`) import

> **Status: ⚠️ Partial.** The core parser (milestones M0–M5 plus the M2
> parametric-shape hardening) is **implemented and wired** through
> `load_model_data()`, the review CLI, the GUI file dialog and the examples
> (2026-10-13).  **M7** (v19+ `$ LOAD PATTERNS` / `$ LOAD CASES` / `$ MASS
> SOURCE`) landed 2026-10-14 against a real v19 `.$et` export.  Remaining:
> **M6** (object auto-meshing via `gmsh`), `AUTOSECTION` auto-select lists,
> multi-tower elevations, and the real-export section-name gaps in §6.

`fea_toolkit` historically parses SAP2000 text exports (`.s2k` / `.$2k`) only.
This document records the design for adding an ETABS text-export parser and the
milestone plan.  The parser is a **sibling** of `SAP2000Parser` — the two
formats differ enough (grammar and geometry model) that overloading one class
would blur them.

The reference implementation this design is informed by is the author's own
`E2K_utilities` (`github.com/arup-group/E2K_utilities`), which is free of any
licence constraint for reuse here.

## 1. The two formats differ in grammar and geometry

| | **S2K / $2K (SAP2000)** | **E2K / $ET (ETABS)** |
|---|---|---|
| Header | `File … was saved on … at …` | `$ File D:\…\$ET saved …` |
| Section marker | `TABLE:  "…"` | `$ SECTION NAME` |
| Row syntax | `KEY=VALUE`, strings quoted, `_` continuation | bare `KEY` + values; strings quoted, numbers bare |
| Continuation | trailing `_` wraps one record | **repeated leading `(type, name)`** on each line (see §2) |
| Geometry | explicit X/Y/Z joints + joint-to-joint connectivity | **deferred**: 2-D points + storey elevations, instantiated per storey (see §3) |
| Units | `CurrUnits="N, mm, C"` | `UNITS "KN" "M"` |
| End | (design/output tables trail) | `$ LOG` … `$ END OF MODEL FILE` |

## 2. Grammar

Every E2K data line is whitespace-separated tokens:

* an **unquoted token is a keyword** (e.g. `STORY`, `HEIGHT`, `COLUMN`, `SF`),
* a **quoted token is a string value** (e.g. `"STORY12"`, `"W4X13"`),
* a **number is a value**.

A keyword binds to every value token that follows it up to the next keyword.
Quoted strings survive intact even when they contain spaces
(`RESTRAINT "UX UY UZ"`).

**Multi-line data.** E2K has *no* trailing continuation marker.  Instead an
object spreads across physical lines by **repeating its leading `(type, name)`
token pair**; the parser merges the lines.  Three merge formats occur (all
handled per-section by the extractors):

| Pattern | Example | Semantics |
|---|---|---|
| property | `MATERIAL "STEEL" M… E…` then `MATERIAL "STEEL" DESIGNTYPE "STEEL" FY…` | union of keys |
| list | `AUTOSECTION "A-GravBm" "W12X26" …` × N lines | append values |
| sub-entry | `COMBO "DCON1" TYPE "ADD" …` then `COMBO "DCON1" LOAD "DEAD" SF 1.4` × N | collect into a list |

The tokeniser stores each section losslessly as grouped lines, so no
continuation line is dropped; the merge is implemented in the section
extractors as each section lands (a repeated keyword folds its values into a
list).

## 3. Deferred geometry

ETABS builds a model in five stages; 3-D coordinates only exist after stage 5:

1. `$ GRIDS` — a 2-D reference grid (no geometry derived from it).
2. `$ STORIES - IN SEQUENCE FROM TOP` — named storeys listed **top-first**,
   each `STORY "NAME" HEIGHT h` or `ELEV e`, plus `MASTERSTORY`/`SIMILARTO`
   and an optional `TOWER` prefix.  Elevations are *computed*: `base = Σ ELEV`,
   then `HEIGHT` accumulated bottom-up gives each storey an absolute Z.
3. `$ POINT COORDINATES` — 2-D plan points: `POINT "1" X Y [DeltaZ]`.
4. **Element templates** — `$ LINE CONNECTIVITIES`
   (`LINE "C1" COLUMN "1" "1" 1`) and `$ AREA CONNECTIVITIES`
   (`AREA "W1" PANEL 4 "11" "17" "17" "11" 1 1 0 0`).  The type keyword
   (`COLUMN`/`BEAM`/`BRACE`, the null `LINE` type, `CABLE`/`TENDON`;
   `FLOOR`/`PANEL`/`WALL`/…) is followed by its point references
   **plus a "drop" count** = storeys that end sits below the assigned storey.
5. **Instances** — `$ POINT/LINE/AREA ASSIGNS` pair a template with a storey:
   * a point instance → a node at `Z = ABS_ELEV[storey] − DeltaZ`;
   * a line/area instance → an element whose ends resolve to `(point, storey)`
     pairs via the drop.

Each `(name, storey)` pair is one **instance**.  Because the same template is
instantiated once per storey, an instance's identity is a *tuple*, which the
parser encodes as a single string id (`name@storey`, with `@`/`\` escaped) and
a sequential integer tag.

## 4. Milestones

| Milestone | Content | Status |
|---|---|---|
| **M0** | Tokeniser + section dispatch; lossless grouped-line storage; `$ LOG` / `$ END OF MODEL FILE` handling | ✅ done |
| **M1** | Geometry resolution: storey elevations, node/element materialisation with drops, composite ids + tags | ✅ done |
| **M2** | Sections & materials & restraints | ✅ done |
| **M3** | Loads & combinations | ✅ done |
| **M4** | Diaphragm constraints (`$ DIAPHRAGM NAMES` + `DIAPH` assigns) | ✅ done |
| **M5** | Wiring (`model_loader`, CLI, GUI dialogs, examples) + fixtures + docs | ✅ done |
| **M6** | Object auto-meshing via `gmsh` (`FLOORMESH`, cookie-cut, `MAXMESHSIZE`) | ⬜ deferred |
| **M7** | v19+ load-case format split (`$ LOAD PATTERNS` + `$ LOAD CASES` + `$ MASS SOURCE`) | ✅ done |
| **M2 hardening** | Parametric shape-type dispatch + `FILE`-scoped catalogue resolution | ✅ done |

### M2 — sections, materials, restraints (implemented)

* **Materials** (`$ MATERIAL PROPERTIES`): each material spans two lines — an
  isotropic property line (`M`, `W`, `TYPE`, `E`, `U`, `A`) and a design line
  (`DESIGNTYPE`, `FY`, `FU`, `FC`, `FYS`) — merged by name.  Values are kept in
  **model units** (matching the S2K parser's no-scaling convention); `G_mod` is
  derived from `E`/`U`, and `apply_material_defaults()` fills the gaps.
* **Frame sections** (`$ FRAME SECTIONS` + `$ CONCRETE SECTIONS`): parametric
  `Rectangular` sections (concrete material) become
  `ConcreteRectangularSection` with the `COVER` from `$ CONCRETE SECTIONS`.
  Unlike SAP2000, the E2K `FRAMESECTION` line carries only `D`/`B` (no `Area` /
  `I33` / `I22` / `TorsConst`), so the elastic properties are *derived* from the
  rectangle (`A = D·B`, `I33 = B·D³/12`, `I22 = D·B³/12`, St. Venant `J`, and
  the elastic moduli `Z33`/`Z22`).
  Profile sections (name-only, e.g. `W4X13`, `HSS4X.237`, `PIPE4SCH40`) resolve
  through `data/section_dict.pkl`: the resolver honours each catalogue's
  `UNITS` (converting in/mm → model length via per-property length exponents)
  and maps `SECTION_TYPE` to the concrete `Section` subclass
  (`STEEL_I_SECTION` → `ISection`, `STEEL_PIPE` → `PipeSection`, …).
  `Auto Select List` sections are skipped (they reference `AUTOSECTION` lists,
  handled in M3).  An unknown profile becomes a logged `GeneralSection`
  placeholder — never a zero-property section that silently passes.
* **Shell sections** (`$ WALL/SLAB/DECK PROPERTIES`): multi-line `SHELLPROP`
  blocks merge by name into `ShellSection` (thickness from `TM`→`TB`→
  `SLABDEPTH`); assigned area thickness is populated from them.
* **Section Designer sections** (`$ SECTION DESIGNER SECTIONS`): `SHAPE "SD
  Section"` frame sections resolve to `SDSection` (no longer a zero-property
  placeholder).  The polygon corner loops (`SHAPETYPE "POLYGON"` +
  `POLYCORNER … X … Y …`) are read and their **gross elastic** properties — `A`,
  `I33`, `I22`, `J` — computed with the shoelace / Green's-theorem formulas
  (`polygon_props`), mapping `X` → local 3 and `Y` → local 2 (the ETABS
  frame-section default).  `REF LINE` shapes are skipped; `J` is the polar
  second moment (a solid-section upper bound).  This restores correct *elastic*
  stiffness for SD-defined wall piers.  **Fiber conversion remains deferred**:
  `SDSection.to_fiber_patches` raises `NotImplementedError` — the outstanding
  path is multi-material polygon meshing (concrete / steel / rebar
  triangulation).  The `ANGLE` rotation token and the wall-pier local-axis
  convention are not yet applied and must be pinned against a controlled ETABS
  export.  Null `LINE`/`AREA` objects assigned the built-in `"NONE"` section are
  materialised with a synthesised zero-stiffness `GeneralSection` (`"NONE"`), so
  their loads (none in this model, but generally present) can resolve.
* **Restraints** (`$ POINT ASSIGNS` `RESTRAINT "UX UY UZ"`): mapped to the six
  DOFs and keyed by the `point@storey` node id.

The catalogue is optional: `ETABSParser(..., section_db_path=…)` accepts an
explicit path (defaulting to the repo's `data/section_dict.pkl`, probed
automatically).  Without it, profile sections degrade to placeholders.

### M2 hardening — parametric shape-type dispatch & FILE-scoped catalogue resolution (implemented)

`_build_frame_section` had two defects (now fixed; summarised in §6): it routes every
shape *type* name into a rectangle-only builder, and it resolves catalogue
profiles by name across all catalogues without using `FILE`.  This sub-milestone
makes section resolution mirror `E2K_utilities.enhance_frame_properties` (the
reference): `FILE`-present → catalogue lookup **scoped to that file**; else a
parametric build dispatched on the canonical shape type.

**Discriminator** (replaces `shape in _PARAMETRIC_SHAPES`):

1. `FILE` present in the `FRAMESECTION` record → **catalogue**: resolve
   `catalogues[FILE]["SECTIONS"][shape]`, falling back to the existing global
   `_find_profile(shape)` when `FILE` names an unknown catalogue.  Here `shape`
   is the *profile name* (`"W4X13"`, `"HSS4X.237"`, `"PIPE4SCH40"`).
2. `SHAPE_NAMES.get(shape)` is a **parametric type** → build from dims (table
   below), dispatching a concrete material to the concrete subclass.
3. `SHAPE_NAMES.get(shape)` is `"GEN"` (or `shape` is `General`/`NA`) → read the
   explicit `AREA AS2 AS3 I33 I22 TORSION(=J) Z33 Z22` tokens straight into a
   `GeneralSection`.
4. Otherwise → a name-only profile (the current fixture's `SHAPE "W4X13"` with
   no `FILE`): global `_find_profile(shape)`, then warn + `GeneralSection`
   placeholder (the behaviour already in place for unresolvable names).

**Parametric dim tokens → Section** (canonical id from `SHAPE_NAMES`):

| Canonical | E2K `SHAPE` names | Dims | Subclass | Fields |
|---|---|---|---|---|
| `I` | `I/Wide Flange`, `WIDE FLANGE`, `Steel I/Wide Flange` | `D B TF TW` | `ISection` | depth / bf / tf / tw |
| `CH` | `Channel`, `Steel/Concrete Channel` | `D B TF TW` | `ChannelSection` | depth / bf / tf / tw |
| `CHS` | `Pipe`, `Steel/Concrete Pipe`, `Filled Steel Pipe` | `D`(OD) `T`(wall) | `PipeSection` | od / t |
| `RHS` | `Box/Tube`, `Tube`, `Steel/Concrete Tube`, `Filled Steel Tube` | `D B TF TW` | `BoxSection` | depth / bf / tf / tw |
| `A` | `Angle`, `Steel/Concrete Angle` | `D B TF TW` | `AngleSection` | depth / bf / tf / tw |
| `AA` | `Double Angle`, `Steel/Concrete Double Angle` | `D B TF TW DIS` | `DoubleAngleSection` | depth / bf / tf / tw / dis |
| `T` | `Tee` | `D B TF TW` | `TeeSection` | depth / bf / tf / tw |
| `R` | `Rectangular`, `Steel Plate`, `Concrete Rectangular` | `D B` | `RectangularSection` / `ConcreteRectangularSection` | depth / bf (+ `COVER`) |
| `C` | `Circle`, `Steel Rod/Circle`, `Concrete Circle` | `D` | `CircularSection` / `ConcreteCircularSection` | diameter (+ `COVER`) |

`Pipe`/`Circle` reuse the `D` token (outer diameter), *not* the `OD`/`TDES`
tokens the catalogue uses — the parametric builder maps `D`→`od`/`diameter` and
`T`→`t`.  The per-subclass field mapping already exists in `_resolve_profile`;
the parametric path reuses that same construction.

**Property computation.**  New `model/section_properties.py` (pure geometry,
alongside `model/section_profiles.py`), porting the reference's
`E2K_utilities.section_utilities` formulas (`A_props`, `AA_props`, `T_props`,
`R_props`, `RH_props`, `C_props`, `CHS_props`, `I_props`, `CH_props`), taking
the `ETABS=True` variant's `A`/`I33`/`I22`/`AS2`/`AS3` keys and adding `J` and
the elastic `Z33`/`Z22` where the reference omits them.  Asymmetric shapes
return the affected elastic modulus as `0.0` (the extreme fibre is not at
`D/2`/`B/2` from the centroid).  `_solid_rect_props` is promoted into this
module so every shape lives in one place.  Example signatures:

```python
def i_props(depth, bf, tf, tw) -> dict[str, float]
def pipe_props(od, t) -> dict[str, float]
def box_props(depth, bf, tf, tw) -> dict[str, float]
def channel_props(depth, bf, tf, tw) -> dict[str, float]
def angle_props(depth, bf, tf, tw) -> dict[str, float]
def double_angle_props(depth, bf, tf, tw, dis) -> dict[str, float]
def tee_props(depth, bf, tf, tw) -> dict[str, float]
def circle_props(diameter) -> dict[str, float]
```

**Deferred — centroid offsets.**  `cg_offset_2`/`cg_offset_3` are *not*
populated by the parametric builders: their sign depends on the section
reference-point convention, which must be pinned down against a real ETABS
export before being emitted (guardrail §12 — don't assert unverifiable
behaviour).  The stiffness quantities (`A`/`I33`/`I22`/`J`) are centroidal and
complete regardless; only the cardinal-point-10/11 insertion path would use the
offsets.


**Catalogue duplication to resolve.**  Two catalogue loaders already exist:
`model/sections.py::SectionLibrary` (name search; `enrich_section` mutates a
placeholder) and the richer `io/e2k_parser.py::_load_catalogues` /
`_find_profile` / `_resolve_profile` (maps `SECTION_TYPE` → subclass, builds
A/I/J from `D`/`BF`/`TF`/`TW`).  The `FILE`-scoping work should live in one
place, not both: either add a `catalogue=` argument to `SectionLibrary` and
have the parser delegate, or keep the parser's resolver and give
`_find_profile` an optional `file` parameter.  Decide as the first commit of
this milestone (anti-pattern 11 — don't write a second spelling of catalogue
resolution).

**Assumptions to verify against a real steel model:**
* Parametric `FRAMESECTION` dims are already in the model's length unit (the
  current rectangle path assumes this, and the fixture agrees).  Confirm against
  a kN-m / N-mm export and, if ETABS emits a per-section `UNITS` token, scale as
  `_resolve_profile` already does for catalogue props.
* Whether a parametric pipe is exported as `D`/`T` (reference) or `OD`/`TDES`
  (catalogue convention); read both with a precedence.

**Tests:**
* Unit tests per `*_props` against the reference's doctest values (e.g.
  `I_props(15,12,3,2) → A 90, I33 2767.5, I22 870.0`) plus hand-checked
  `J`/`Z33`/`Z22`.
* Parser tests on new fixture lines (one parametric section of each type)
  asserting the returned `Section` **subclass** (the regression this removes is
  a steel I-section arriving as `RectangularSection`), its dimensions, and
  `A`/`I33`/`I22`/`J`.
* A `FILE`-scoping test: two catalogues both containing a section of the same
  name resolve to the `FILE`-named one.
* Keep `test_profile_section_resolved_with_catalogue` and
  `test_solid_rect_props_square_torsion` green.

### M3 — loads & combinations (implemented)

* **Load cases & patterns** (`$ STATIC LOADS`): each `LOADCASE` line yields both
  a `LoadCase` (`Linear Static`, `design_type` from `TYPE`) and a `LoadPattern`
  (`pattern_type` + `self_weight_factor` from `SELFWEIGHT`).  A `WIND` line
  (`WIND "WX" "EUROCODE1 2005" ANG … VB …`) attaches the auto-generation
  parameters to the matching case/pattern (`auto_data["WIND"]` /
  `case_data["WIND"]`) — captured but **not** expanded into element loads.
* **Load combinations** (`$ LOAD COMBINATIONS`): a combo spans a header line
  (`TYPE` → `combo_type` via `ADD`→`Linear Add`, `ENVE`→`Envelope`, …; `DESIGN`
  → `design`) plus one `LOAD … SF …` line per reference.  The explicit `LOAD` /
  `SPEC` / `COMBO` / `LOADCASE` token sets each entry's `kind` (`case` vs
  `combo`), so no name-resolution pass is needed (unlike SAP2000).
* **Point loads** (`$ POINT OBJECT LOADS` `TYPE "FORCE"`) → `JointLoad`, keyed
  by `point@storey`.
* **Frame loads** (`$ LINE OBJECT LOADS` / `$ FRAME OBJECT LOADS`) →
  `FrameDistributedLoad`: `UNIFF` → `Uniform`; `TRAPF`
  (`FSTART`/`FEND`/`RDSTART`/`RDEND`) → `Linear`/`Trapezoidal` (linear when the
  span is 0–1); `UNIFM` (`MVAL`, a uniform distributed *moment* in [F·L/L] =
  force units) → a `Moment`-type `Uniform` load.  Numeric `DIR` `1`/`2`/`3` map
  to `LocalX`/`LocalY`/`LocalZ` (local 1/2/3 axes), so a torsion `UNIFM` with
  `DIR "1"` lands on the local 1-axis.  ``Moment`` loads reach OpenSees as
  statically-equivalent **nodal end moments** ``m·L/2`` about the load axis
  (``beam_moment_to_nodal_moments``) — OpenSees ``eleLoad`` has no
  distributed-moment form — with the sign following the right-hand rule of the
  resolved axis.
* **Area loads** (`$ AREA OBJECT LOADS` / `$ SHELL OBJECT LOADS`): `TYPE "UNIFF"`
  → `AreaUniformLoad`; `DIR "GRAV"` maps to `direction="Gravity"`.
  `TYPE "UNIFLOADSET" "name"` references a named shell uniform load set
  (`$ SHELL UNIFORM LOAD SETS`: `SHELLUNIFORMLOADSET "name" LOADPAT "pattern"
  VALUE value`) and expands to one gravity-direction `AreaUniformLoad` per
  `(pattern, value)` pair in the set.  The original definitions and per-area
  assignments are *also* retained as `SAPModelData.shell_uniform_load_sets`
  (``{name: ShellUniformLoadSet}``) and
  `SAPModelData.shell_uniform_load_set_assignments` (``{area_id: set_name}``)
  for introspection and editability.  Each expanded load carries its set name
  in `AreaUniformLoad.source_set` (`None` for a direct `UNIFF` pressure), so
  `SAPModelData.reexpand_uniform_load_sets()` can drop and regenerate exactly
  the set-derived loads after the definitions are edited.  Both fields are
  carried onto `MeshModel`, and the GUI Model Tree surfaces them under a
  **Shell Uniform Load Sets** group (one row per set, expanding to its assigned
  shell objects).
* **Deferred load types** — `TEMP`, `GROUNDDISP`, `POINTF`/`POINTM` (mid-span
  frame point loads) have no target dataclass yet; they are logged at debug
  level rather than dropped silently.

### M5 — wiring (implemented)

* **`load_model_data()`** routes `.e2k` / `.$et` to `ETABSParser` (alongside
  `.s2k` / `.$2k` → `SAP2000Parser`), so every existing consumer picks up
  ETABS input with no code change: `examples/view_model.py`, the three
  analysis examples (`modal_rs`, `static`, `pushover`), the GUI `open_path`,
  and `python -m fea_toolkit.model.review`.
* **`review_s2k_file`** dispatches on suffix; for ETABS it passes no
  `raw_tables` (the grouped-line sections do not map to SAP table names, so
  the table-coverage report is simply omitted).
* **GUI** — the open dialog filter offers `*.s2k *.$2k *.e2k *.$et *.json`, and
  `open_path` / `app._load_model` go through `load_model_data`.
* **Examples** — `basic_usage.py` accepts `.e2k`/`.$et` (routed to
  `ETABSParser` with the probed section catalogue); the other three examples
  now call `load_model_data()` and drop their `SAP2000Parser` import.
* **Docs** — `README.md`, `docs/llm_guide.md`, `docs/json_serialization.md`
  and `docs/gui_roadmap.md` flipped from "ETABS planned / not yet supported"
  to supported.

### M4 — diaphragm constraints (implemented)

* **`$ DIAPHRAGM NAMES`** — each `DIAPHRAGM "D1" TYPE RIGID|SEMIRIGID` is a
  diaphragm *definition*.  `RIGID` is a rigid in-plane constraint (no flexural
  stiffness); `SEMIRIGID` is a grouping with **no** constraint (in-plane
  stiffness comes from the shells), used to apply inertial loads in seismic
  analysis.  `TYPE RIGID` is written **unquoted**, so the tokeniser records
  `RIGID` / `SEMIRIGID` as a bare keyword group; `_get_diaphragm_names` reads
  that form (plus the quoted `TYPE "RIGID"` form defensively).
* **Assignment** — `DIAPH "D1"` appears on **both** `$ POINT ASSIGNS` and
  `$ AREA ASSIGNS`; `DIAPH "DISCONNECTED"` on a point opts that joint out of
  the diaphragm.  A point can carry `RESTRAINT` and `DIAPH` together.
* **Per-storey instancing** — ETABS assigns one named diaphragm at many storeys,
  yet each storey's floor is a separate rigid plane.  Each `(name, storey)`
  pair becomes its own `Constraint` keyed `name@storey` (the composite-id
  convention).  The node set is the corner nodes of `DIAPH`-assigned areas
  plus the assigned points, minus `DISCONNECTED` points.
* **RIGID** → `constraints` + `constraint_assignments`, so the existing
  `_detect_diaphragm_levels` → `ops.rigidDiaphragm` path applies one rigid MPC
  per storey.  **SEMIRIGID** → `constraints` (with
  `constraint_data["Rigidity"]`) but **no** assignments, so it never becomes a
  rigid MPC (the zero-assignment constraint is skipped by
  `_detect_diaphragm_levels`).

### M4b — cardinal point & frame angle (implemented)

* **`ANG`** and **`CARDINALPT`** on `$ LINE ASSIGNS` are read and stored on each
  `FrameElement` (`angle`, `cardinal_point`), matching the S2K path.
* The insertion point is folded into per-end **lateral offsets** —
  `_build_frame_end_offsets()` reuses `SAP2000Parser._get_section_depth_width` /
  `_cardinal_point_offset` (one spelling of the 1–11 table) and writes
  `FrameEndOffset.off_y_i/z_i/j/z_j`, so `CARDINALPT 8` (top centre) lands the
  section half a depth below the reference line exactly as SAP2000 does.  The
  offsets are consumed by the builder (`-jntOffset`), the GUI viewer and the
  Rhino export through the shared `frame_end_offsets_global()` helper.

## 5. Decisions recorded

* **Sibling `ETABSParser`** in `src/fea_toolkit/io/e2k_parser.py`, same API as
  `SAP2000Parser` (`.parse().get_model_data() → SAPModelData`), no OpenSees
  import.
* **Composite ids** use `@` as the delimiter with `@`/`\` escaping; tags come
  from sequential counters in `_resolve_geometry` (one each for nodes, frames
  and areas), so correctness never depends on the id string encoding.
* **`to_json` / `from_json` deferred** — the model-codec path
  (`model_codec.model_to_json`) already persists a built `SAPModelData`, so an
  E2K model can be saved/reloaded without a raw-table cache.  The raw-table
  cache (`.s2k`-specific) may never be needed for E2K.
* **IDs stay strings** — the reference parser numeric-coerces `"1"` → `1`; this
  parser deliberately keeps quoted ids as strings to fit `SAPModelData`
  (§4.1 of `.clinerules`).

## 6. Known limitations (M0–M5)

* **Multi-tower** — `TOWER` is carried forward but all stories share one
  elevation ladder; per-tower elevation stacks are not yet implemented.
* **Auto-select (`AUTOSECTION`) frame-section lists** are deferred, so the
  corresponding `Auto Select List` sections are skipped (they reference the
  list, not a concrete section).
* **Deferred load types** — `TEMP`, `GROUNDDISP`, `POINTF`/`POINTM` object loads
  are logged but not mapped (no target dataclass).
* **`$ TABLE SETS` / `$ DATABASE TABLE NAMED SETS`** are database-table
  export/display named sets (which tables and which loads to export), not
  structural loads — they are deliberately ignored by the parser.
* The profile catalogue (`data/section_dict.pkl`) is optional: without it,
  profile sections become `GeneralSection` placeholders (zero A/I/J) rather
  than failing the parse.
* **Parametric frame-section dispatch & `FILE`-scoped catalogue resolution**
  are implemented (M2 hardening, §4): every parametric shape type resolves to
  its typed `Section` with computed `A`/`I33`/`I22`/`J`, and catalogue profiles
  resolve within their `FILE`-named catalogue.  Only `cg_offset_2`/`cg_offset_3`
  remain unpopulated for parametric sections (pending a real-export reference
  check).
* **Filled steel tubes are approximated as hollow steel sections.**
  `Filled Steel Tube` / `Filled Steel Pipe` map to `BoxSection` /
  `PipeSection` (the `FILLMATERIAL` token is parsed into `dims` and ignored),
  matching the S2K parser's dispatch.  Correct for an unfilled tube, but a
  *steel-tube / concrete-core composite* for a genuinely filled section — see
  the staged composite-section plan (§7).
* **Object auto-meshing is deferred (M6)** — `AUTOMESH` / `MESHATINTERSECTIONS`
  / `FLOORMESH` / `MINNUMSTA` / `MAXSTASPC` (line) and `OBJMESHTYPE` / `MESHAT`
  / `MAXMESHSIZE` / `LINECONSTRAINT` (area) are parsed into each assignment's
  attribute dict but **not** materialised: cookie-cut meshing at
  beams/walls/grids and multi-node floor slabs (`FLOORMESH`) need a
  `gmsh`-based remeshing path (`mesh/remesh.py`), which is not yet wired to the
  parser.  `frame_auto_mesh` / `area_mesh` therefore stay empty for E2K input.
* **v19+ load-case format (M7, implemented)** — ETABS v19 replaced
  `$ STATIC LOADS` with `$ LOAD PATTERNS` + `$ LOAD CASES` and added
  `$ MASS SOURCE` (the plan's original "v21" label; the split is a v19 change,
  verified against a v19 `.$et` export).  `_get_load_patterns` /
  `_get_load_cases` / `_get_mass_sources` read these, mapping ``TYPE
  "Linear Static"`` → ``case_type="LinStatic"`` (so
  ``auto_detect_static_cases`` works) and the ``LOADPAT … SF`` lines to the
  S2K ``CASE - STATIC 1 - LOAD ASSIGNMENTS`` key.  Two real-export fixes ride
  along: load-combination ``LOADCOMBO`` entries (→ ``kind="combo"``) and the
  real object-load section names ``$ FRAME OBJECT LOADS`` / ``$ SHELL OBJECT
  LOADS`` (the synthetic fixtures' ``$ LINE`` / ``$ AREA OBJECT LOADS`` are
  still accepted).

* **Real-export section-name gaps (not yet fixed)** — a v19 `.$et` export
  revealed section-name mismatches the synthetic fixtures do not cover, in the
  geometry/property (not load) path: the storey section is exported as
  ``$ STORIES - IN SEQUENCE FROM TOP - FOR EACH TOWER`` (with ``$ TOWERS`` and
  per-``STORY`` ``TOWER`` / ``SIMILARTO`` tokens) rather than the fixture's
  ``$ STORIES - IN SEQUENCE FROM TOP``, and shell properties are split into
  ``$ SLAB PROPERTIES`` / ``$ DECK PROPERTIES`` / ``$ WALL PROPERTIES`` rather
  than ``$ WALL/SLAB/DECK PROPERTIES``.  Until these are matched (and
  multi-tower elevation stacks are computed), geometry is empty on real
  exports — the loads parse, but nodes/elements do not materialise.
* **Section Designer sections are elastic-only.**  `SHAPE "SD Section"` frames
  resolve to `SDSection` with gross `A`/`I33`/`I22` computed from the polygon
  loop (`polygon_props`); the three open fidelity items below are tracked in
  `docs/sd_sections.md`:
  1. `J` is the **polar second moment** (`I33 + I22` — a solid-section upper
     bound), not the St. Venant torsional constant.
  2. `to_fiber_patches()` is **not implemented** for `SDSection`, so nonlinear
     analysis of SD piers falls back to elastic sections.
  3. The `ANGLE` rotation token and the wall-pier (vs frame) local-axis
     convention are **not applied** — `I33`/`I22` follow the ETABS
     frame-section default (`X` → local 3, `Y` → local 2).
* **Uniform distributed moments (`UNIFM`)** are converted to statically
  equivalent nodal end moments `m·L/2` (`beam_moment_to_nodal_moments`); the
  sign follows the right-hand rule of the resolved local axis and should be
  validated once against a controlled ETABS export (only *uniform* moments are
  decomposed).

## 7. Composite sections (filled tubes & encased) — implemented

`Filled Steel Tube` / `Filled Steel Pipe` are still *parsed* as hollow **steel**
sections (`BoxSection` / `PipeSection`, §6) — the parser has no concrete-fill
token path yet.  The model layer now supports the genuinely composite cases:

* `EncasedSection.to_fiber_patches()` is **implemented** — concrete encasement
  rectangle + overlaid embedded-steel patches (the steel region is counted
  twice; a documented simplification).
* `ConcreteFilledTubeSection` (RHS) and `ConcreteFilledPipeSection` (CHS) are
  **added** — steel tube walls / pipe ring + a concrete core (modelled
  **unconfined** — the tube's confinement is not Mander-tie confinement, so a
  conservative unconfined core is used until a CFT confinement model lands,
  see `docs/cft_confinement_plan.md`, guardrail §12) + optional rebar
  layers / ring.

The generalised multi-material fiber contract is in place:

1. **`to_fiber_patches(materials, nfy, nfz)`** takes a *role → tag* map
   (`"steel"`, `"concrete"`, `"core"`, `"rebar"`) instead of the ad-hoc
   `mat_tag` / `mat_tag + 1` / `mat_tag + 2` offset convention.  Each role
   fixes its OpenSees law: `"steel"` → `Steel01`, `"concrete"`/`"core"` →
   `Concrete01`, `"rebar"` → `Steel02`.
2. **`Section.fiber_roles()`** declares the roles a section needs, and
   **`Section.fiber_role_material(role)`** maps each role to its SAP2000
   material reference — the one hook the builders (`_sections.py`, the Tcl
   export, the recorder) use to create materials for *any* section, steel,
   RC, encased or filled tube.
3. **Lumped-property complement** — still open: a transformed-section helper
   in `model/section_properties.py` (steel + concrete via modular ratio
   `n = E_s / E_c`) returning composite `A`/`I33`/`I22`/`J`/`Z33`/`Z22` for
   the elastic (`section('Elastic')`) path.

Still open (recorded as follow-ups, not implemented):

* The `Filled Steel Pipe` (CHS) concrete-fill *parser* path (§6 still parses
  filled pipes as hollow `PipeSection`).
* The CFT confinement formula — decision + researched recommendation recorded
  in `docs/cft_confinement_plan.md`; not yet implemented.

Decision point settled (2026-10-13): the filled-tube warning fix shipped as the
`SHAPE_NAMES` alias; the composite fiber model above is now implemented as the
role-based contract.
