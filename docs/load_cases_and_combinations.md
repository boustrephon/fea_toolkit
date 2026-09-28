---
title: "Load Cases and Combinations"
description: "The three-tier load model — patterns, cases, combinations — as an analysis input: which tier drives a solve, the two routes to combined results, and why only one of them survives nonlinearity."
status: "complete"
tags: [loads, load-case, combination, analysis, results]
category: [model-features, core-pipeline]
related: [load_combinations.md, analysis.md, results_schema.md, units_conversion.md, gui.md]
---
# Load Cases and Combinations

SAP2000 organises loading in three tiers, and the toolkit models all three:

| Tier | What it is | Toolkit type | Drives a solve? |
|---|---|---|---|
| **Load pattern** | A named spatial distribution of load — self-weight, a floor load, a wind pressure — with its own type and self-weight factor | `model.sap_data.LoadPattern` (`name`, `pattern_type`, `self_weight_factor`, `auto_data`) | No |
| **Load case** | An **analysis case**: it assigns one or more load patterns, each with a load scale factor, and running it solves the structure once | `model.sap_data.LoadCase` (`case_name`, `case_type`, `case_data`) | **Yes** — one case is one solve |
| **Load combination** | A reference graph over load cases (and other combinations) carrying factors, evaluated against **results** for design | `model.sap_data.LoadCombination` / `LoadCombinationEntry` | No — it reduces results |

[Load Combinations](load_combinations.md) documents the third tier in full:
parsing, reference classification, tree building, the five CSI operators, sign
forking and external definition sets.  This page covers the part that decides
**what gets solved** — the pattern → case → combination chain as an analysis
input, and how a combination's results end up beside a case's.

For CSI's own definitions of these terms see the links under *References*;
nothing from CSI's documentation is reproduced here.

## A load case is what drives a solve

`AnalysisBuilder.run_static_analysis()` accepts an optional **pattern → scale**
map:

```python
builder.run_static_analysis(pattern_scales={"Dead": 1.4, "Live": 1.6})
```

When `pattern_scales` is supplied the domain is rebuilt with **only those
patterns active** (`build_domain()` → `create_loads(pattern_scales=…)`), each
pattern's loads scaled by its own factor.  So the call above is a *single* solve
of a factored **ultimate** case — not two solves combined afterwards.  The scale
is applied per load family (frame distributed, joint, area gravity, area
uniform, and the edge loads derived from loaded areas), so every load belonging
to a pattern is factored together.

There are three ways to arrive at that map:

| You have | Use |
|---|---|
| A static case defined **in the model** | `patterns_from_case(lc)` — reads the case's `CASE - STATIC 1 - LOAD ASSIGNMENTS` block into `{pattern: factor}` (a record without an explicit `LoadSF` counts as `1.0`) |
| A case you want to **author in code** | the dict form below — no `.s2k` edit, no parser round-trip |
| A single pattern on its own | `{"Dead": 1.0}` |

The dict form is what makes a hand-defined case possible:

```python
run_linear_cases(md, mesh_model, linear_cfg={"cases": [{"ULT": {"Dead": 1.4, "Live": 1.6}}]})
```

`run_linear_cases()` auto-detects every `LinStatic` case in the model and merges
`linear_cfg["cases"]` over it: an entry that is a **string** names a model case,
an entry that is a **dict** defines one, keyed `{case_name: {pattern: factor}}`.
Cases whose patterns all turn out to carry no load (no self-weight factor and no
load records of any family) are dropped rather than run.

## Two routes to combined results

| Route | Mechanism | Valid when |
|---|---|---|
| **A — factored case, one solve** | `run_static_analysis(pattern_scales={…})` | Always — the **only** correct route for a nonlinear analysis |
| **B — combine case results afterwards** | solve the constituent cases, then `build_combination_results(cases, model=md)` (or `export_results(…, expand_combinations=True)`) | **Linear** analyses only |

For a **linear** static analysis the two are numerically identical: the response
is linear in the load, so `1.4·R(Dead) + 1.6·R(Live)` equals
`R(1.4·Dead + 1.6·Live)`.  Superposition is what makes route B legal — and
cheap, since one set of case results then feeds any number of combinations.

Route B is **invalid** once the analysis is nonlinear — P-Delta, nonlinear
materials, staged construction — because the response is no longer linear in the
load, so the factored patterns have to be applied *together*, in one solve.
That asymmetry is why SAP2000 separates a **load case** (drives an analysis, may
hold several factored patterns) from a **load combination** (a linear or
enveloping reduction of case *results*).  The same three-tier split appears
across FEA generally: NASTRAN `LOAD` → `SUBCASE` → combination, Abaqus
loads/BCs → step with its own amplitude and scale, ANSYS load step with `FACT`,
OpenSees `pattern` + constant time series.

The toolkit mirrors the split exactly: routes A and B are both available and
neither replaces the other.

## API summary

| Call | Purpose |
|---|---|
| `model.sap_data.patterns_from_case(lc)` | A static case's `{pattern: factor}` map |
| `AnalysisBuilder.run_static_analysis(pattern_scales=…, extract_reactions=…)` | One solve → `{nodal_displacements, reactions, load_reaction_check}`; also cached on `_last_static_results` for the result-aware viewers |
| `AnalysisBuilder.static_element_force_arrays()` | Component-keyed element forces (`fx_i … mz_j`), aligned to the exported geometry |
| `AnalysisBuilder.extract_static_element_forces()` | The same forces keyed by element tag |
| `analysis.linear.run_linear_cases(md, mesh_model, linear_cfg=…, raw_out=…)` | Runs the model's static cases plus any overrides and returns a summary table; `raw_out` collects per-case `{nodal_displacements, element_forces}` |
| `analysis.static.run_static_analysis(…, collect_raw=True)` | The same run wrapped in an `AnalysisResult` whose `data["static_raw"]` holds the writer-shaped per-case results |
| `analysis.combinations.build_combination_results(cases, model=md, envelope_mode=…, return_meta=True)` | Route B: composites in the same payload shape, plus `{group, kind, family, coords}` metadata |
| `AnalysisBuilder.export_results(path, static_results=…, model=md, expand_combinations=True)` | Route B in one call, straight to a file |
| `AnalysisBuilder.export_static_results(path, results, case_name)` | Route A → one force-bearing archive for a single case |

Two behaviours worth knowing before you rely on them:

- **Element forces are read from the current OpenSees domain.**  Call
  `static_element_force_arrays()` (or `export_static_results()`) *immediately*
  after the solve that produced the results — a later static, modal or pushover
  run on the same builder overwrites the domain and the forces with it.
- **`envelope_mode`** chooses how an enveloping combination is expanded:
  `"maxmin"` (the default) yields one maximum and one minimum composite per
  quantity; `"per_path"` yields one composite per referenced branch.  The
  variants and their names are documented in
  [Load Combinations](load_combinations.md) → *Variant metadata*.

## Results: in memory first

A case's **payload** is a plain dict — `{"nodal_displacements": {node_id: [dx…rz]},
"element_forces": {tag: {fx_i … mz_j}}}` — and a composite produced by route B has
exactly the same shape, so the two merge into one mapping
(`{**cases, **combined}`).  That payload is the real boundary; the archive is its
serialization:

| Step | API | Touches disk? |
|---|---|---|
| Solve | `run_static_analysis(…)`, `run_linear_cases(…)` | No |
| Combine (route B) | `build_combination_results(…)` | No |
| Serialize | `write_results_npz(path, md, static_results=…, case_meta=…)`, `export_results(…)` | Yes |
| Read | `read_results(path)` → the same dict | Yes |

The read side is deliberately format-independent: `ResultsRepository` — and its
`NpzResultsRepository` implementation — serves cases, per-case arrays, file-level
metadata and display geometry from a `{name: array}` dict, so **a repository can
be built over results that were never written to disk**.  `NpzResultsRepository(dict)`
is the in-memory form, and everything a view needs hangs off it: `cases()`,
`case_meta()`, `as_model()` for the drawable geometry, `nodal_displacements(case)`
and `element_forces(case)` for the deformed-shape and force-diagram overlays, plus
the cheap `has_displacements()` / `has_forces()` pre-checks a UI consults before
offering an action.  The reasoning behind a NumPy-typed boundary is in
[Development Notes](dev_notes.md) → *Results repository and the NumPy-typed seam*.

One piece is still internal: the archive dict is assembled by the writer's own
`_collect_geometry()` / `_collect_static()` steps and handed straight to
`np.savez_compressed`.  Exposing that assembly as a function is the one small
library addition the GUI work needs, so that **the same dict** can be registered
in memory *or* written — instead of writing an archive and reading it back just to
look at a result.

**Composites are ordinary cases.**  A combination's composites are stored as
`static/{composite}/…` blocks with the usual per-case arrays, so every plotter and
viewer treats them exactly like a load case.  What distinguishes them is the
identity columns — `static_case_group` / `_family` / `_coords` — which record
which combination a composite came from and which variant it is, so
`SEISM [+RSX]` and `SEISM [-RSX]` cannot be confused.  See
[Results Schema](results_schema.md).

**Units.**  Scale factors are dimensionless and loads are in the model's own unit
system throughout ([Units](units_conversion.md)).  Nothing in this layer converts
units: a case's factors multiply loads that are already in model units, and the
results come back in the same system.

## Worked example — an ultimate case, both routes

```python
from fea_toolkit.analysis.combinations import build_combination_results
from fea_toolkit.analysis.linear import run_linear_cases
from fea_toolkit.io.npz_writer import write_results_npz
from fea_toolkit.io.s2k_parser import SAP2000Parser
from fea_toolkit.opensees.preprocessor import preprocess_model

md = SAP2000Parser("model.s2k").parse().get_model_data()
mesh = preprocess_model(md)

cases: dict = {}

# Route A — a case authored in code: the factored ultimate case, solved once.
# `run_linear_cases` also runs the model's own static cases, so this single call
# yields ULT *and* the model cases (plus their displacements and element forces)
# in `cases`.  Drop the `linear_cfg` to run the model's cases alone.
run_linear_cases(
    md, mesh, linear_cfg={"cases": [{"ULT": {"Dead": 1.4, "Live": 1.6}}]}, raw_out=cases
)

# Route B — expand every combination from those case results.
combined, meta = build_combination_results(cases, model=md, return_meta=True)

# Persistence is a separate step; `{**cases, **combined}` is already the results.
write_results_npz(
    "results.npz", md, static_results={**cases, **combined}, mesh_model=mesh, case_meta=meta
)
```

Note that `ULT` here is a *case*, not a combination: it is one solve of factored
patterns, which is what makes it valid for a nonlinear analysis.  The model's own
combinations come back from `build_combination_results` instead, because they
reduce case results.

## The GUI is the planned consumer

The Analysis menu (milestone 5 in the [GUI Roadmap](gui_roadmap.md)) is where
this becomes clickable: list the model's load cases and combinations, choose one,
run it on a worker, and see the result as a case view.  Because the read seam is
dict-based, the in-memory path above means **no archive is needed to look at a
result** — `File ▸ Save results` writes one when persistence is wanted.

## References

- CSI SAP2000 help — *Load Patterns*, *Load Cases*, *Load Combinations*:
  help.csiamerica.com.  CSI's knowledge base (wiki.csiamerica.com) covers the
  combination operators.  Both are **linked, not reproduced** — see
  [Licence](licence.md).
- [Load Combinations](load_combinations.md) — the combination engine: operators,
  tree building, sign forking, external definition sets, variant metadata.
- [Analysis Helpers](analysis.md) — the `analysis` subpackage's function API.
- [Results Schema](results_schema.md) — array names, case labels and the
  identity columns.
- `docs/_pending_work.md` → **P12** for combination implementation status.


