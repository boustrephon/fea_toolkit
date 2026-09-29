---
title: "Workflow Authoring — Steps and Recipes"
description: "The declarative workflow layer: a recipe is an ordered list of steps, each a verb applied to a Selection, saved as data. Architecture, verbs, and the two ways to make an element non-structural."
status: "partial"
tags: [workflow, recipe, steps, architecture, gui, selection, verbs, declarative]
category: [core-pipeline]
related: [gui.md, gui_roadmap.md, analysis.md, workflow.md, report_generation.md]
---
# Workflow Authoring — Steps and Recipes

> **Status: ⚠️ Partial.** Phase A (the `fea_toolkit.workflow` package) and
> Phase B (the GUI's Recipe panel and the Model-menu presets) have landed.
> Phases C and D — the `check` and `chart` verbs, and the command palette —
> are planned; see [Roadmap](#roadmap).

## Why this layer exists

The toolkit's analysis primitives — `Preprocessor`, `run_case_set`,
`build_combination_results`, `capacity.*`, `plotting.report.*` — are composed
**explicitly by the caller** (`docs/analysis.md`). A script composes them with
Python control flow. That is powerful, but it is *code*: it cannot be inspected
as a workflow, edited in a GUI, or diffed as a document, and it cannot be reused
without copying it.

The workflow layer is the **declarative counterpart**. The unit is a **step** —
a verb, a :class:`~fea_toolkit.model.selection.Selection`, and parameters — and a
**recipe** is an ordered list of steps:

```python
from fea_toolkit.io.s2k_parser import SAP2000Parser
from fea_toolkit.model.selection import Selection
from fea_toolkit.workflow import Recipe, run_recipe

md = SAP2000Parser("model.s2k").parse().get_model_data()

masonry = Selection(sections=["brick wall"], element_types=["Area"])

recipe = Recipe(name="Masonry building")
recipe.add("scale_sections", masonry, {"factor": 0.01})
recipe.add("mesh", masonry, {"split_slabs_at_walls": True})
recipe.add("run_static", params={"cases": {"Self weight": {"Self weight": 1.0}}})
recipe.add("combine", params={"envelope_mode": "maxmin"})

run = run_recipe(recipe, md, log=print)
```

A recipe is **pure data**, so it can be saved, reviewed, replayed and handed on
— and exported back to a script when code is what is wanted. What it cannot
express is the one thing code can: a loop, a conditional or a computed value.
That is the deliberate trade (configuration-as-data, not configuration-as-code):
in exchange for giving up control flow, the workflow becomes inspectable,
editable and reproducible.

## The two concepts

| Concept | What it is | Lives in |
|---|---|---|
| **Step** | `(verb, Selection, params, optional)` — one operation applied to a selection | `workflow/steps.py` |
| **Recipe** | An ordered list of steps, plus a name | `workflow/recipe.py` |
| **Verb** | The operation itself: an implementation plus the parameters it declares | `workflow/verbs/` |
| **Spec** | The declarative description of a verb — name, parameters, help, prerequisites | `workflow/registry.py` |

## Package layout, and why it is split this way

```
src/fea_toolkit/workflow/
├── __init__.py         # the public surface: Recipe, run_recipe, STEP_SPECS, …
├── steps.py            # the vocabulary — Step, StepResult, StepSpec, ParamSpec,
│                       #   StepContext, StepError.  No I/O, no OpenSees, no Qt.
├── registry.py         # STEP_SPECS — the manifest, as pure data
├── recipe.py           # Recipe: ordered steps, dict/JSON/Python, run_recipe()
└── verbs/
    ├── mesh.py             # one module per verb: its implementation AND its
    ├── scale_sections.py   #   declared parameters, so the two cannot drift
    ├── run_static.py
    └── combine.py
```

Three properties of this shape are load-bearing:

1. **A verb's implementation and its parameter list live together** (in
   `verbs/`), so a parameter the implementation reads but does not declare
   cannot exist — there is no second place to forget to update.
2. **The manifest is separate and is pure data** (`registry.py`). A caller that
   wants to know the vocabulary — the GUI building its "Add step" menu and its
   parameter forms, a validator, a help listing — imports the registry and
   nothing else.
3. **Every verb keeps its heavy imports inside the function body.** So reading
   the manifest does **not** load OpenSees, and a parameter form can be rendered
   before a model is open. `tests/test_recipe.py` asserts this in a subprocess.

## The verbs

Registration order in `registry.py` is the order a menu should offer them, and
the order they make sense in: prepare properties, prepare topology, solve,
reduce.

| Verb | Kind | What it wraps | Needs |
|---|---|---|---|
| `check_connectivity` | `table` | `model.checks.check_model_connectivity` | — |
| `check_self_weight` | `table` | `model.checks.check_self_weight_consistency` | — |
| `check_brace_buckling` | `table` | `model.checks.check_brace_buckling` | — |
| `scale_sections` | `model` | edits the matched sections' stiffness properties in place | — |
| `mesh` | `geometry` | `Preprocessor.run(md, load_shell_selection=…)` | — |
| `run_static` | `cases` | `analysis.linear.run_case_set` | a topology |
| `combine` | `cases` | `analysis.combinations.build_combination_results` | solved cases |

The three checks are **model-only**: they run on the parsed model with no
OpenSees domain and no results, which is why they come first and why they are the
first `table` steps. A `chart` verb is *not* in the same position — every
`plotting.report.*` function needs results (a DataFrame, a result dict), so the
figure verb follows the results plumbing rather than preceding it.

There is **one verb per check**, not one `check` verb with a "which check"
parameter: the checks take different arguments, so a single verb would have to
declare every check's parameters and leave most of them inapplicable — a
parameter surface that lies.

A verb's parameter surface is **exactly** what its `ParamSpec` list declares; an
undeclared key is rejected rather than ignored, so a typo cannot silently change
a model. Defaults are filled at run time, not written into the recipe file, so a
saved recipe stays readable and round-trips faithfully.

### What each step sees

`run_recipe` threads one `StepContext` through the steps, so each sees what the
previous produced:

* `scale_sections` replaces `model_data` with an edited **copy** — the caller's
  parsed model is never mutated;
* `mesh` replaces `model` (the prepared topology), and the following
  `run_static` solves *that*;
* `run_static` fills `case_results`, which `combine` reduces.

### Failure, and cancellation

* A **non-optional** step that raises stops the run with a `StepError` carrying
  the step's index, its verb and the original exception. The default is strict
  because a silently-skipped step is a silently-different model.
* A step marked **`optional=True`** is the exception: its failure is logged, it
  contributes no outputs, and the run continues. The consequence is deliberate:
  if a *later* step needed the failed step's output it will fail in turn — the
  recipe being honest about its own dependency, rather than auto-skipping
  dependents (which would need the dependency-graph machinery this layer exists
  to avoid).
* `cancel()` is polled **between** steps, never mid-step — matching the GUI
  worker's cooperative cancellation.

## Recipes as data

| Method | Purpose |
|---|---|
| `to_dict` / `from_dict` | the editable form; JSON-native, round-trips losslessly |
| `to_json(path)` / `from_json(path)` | save and load |
| `to_python()` | the same recipe as a runnable script, for version control |

An **absent** selection (`None`, "act on everything") and an **empty** selection
(`""`, "matches everything") stay distinct through a round-trip, because a verb
reports them differently even though both select all.

## The two ways to make an element non-structural

Masonry walls are the case that motivates both, and they are **independent** — a
recipe may use either, or both:

| | **1. Reduce the stiffness in place** | **2. Leave the elements out** |
|---|---|---|
| Verb | `scale_sections` | `mesh` with a `selection` (loads-only) |
| Built in OpenSees? | yes | no — never written |
| Loads | stay on the element | reassigned to supporting frames as edge loads |
| Mass | retained through the element | must be injected explicitly |
| The knob | a stiffness property | the selection itself |
| Why you would | the wall should still stiffen the frame a little | the wall must not enter the stiffness system at all |

**The knob differs per element kind.** Stiffness lives in `A`/`I33`/`I22`/`J` for
a frame and in `thickness` for a shell, so `scale_sections`' default
`attributes="auto"` scales whichever of those a matched section actually carries,
and reports what it changed. A shell wall in a real SAP model often has `A/I/J`
left at zero — inherited defaults that nothing populates — so a frame-shaped
attribute list genuinely changes nothing on it, and the verb says so rather than
appearing to work:

```
scale_sections: factor 0.01 on ['A', 'I33', 'I22', 'J']
    — 0 section(s) scaled, 1 unchanged (every named property is zero)
```

One caveat worth knowing: a shell's `thickness` drives its **mass** as well as
its stiffness, so scaling it lightens the wall too. A wall whose weight must be
preserved exactly is better served by strategy 2, with the mass supplied
explicitly.

## The GUI surface

The Recipe panel (`gui/views/recipe_panel.py`) is a **view over a `Recipe`** — it
keeps no second copy of the workflow state, and it builds every parameter field
from the verb's `ParamSpec`, so a form cannot drift from the schema and a new
verb appears in its Add menu without touching the panel.

* **Recipe dock** (bottom, beside the Message Log): the step list, Add / Remove /
  Up / Down, the selected step's selection editor and parameter form, and the
  Optional flag.
* **`&Recipe` menu**: Run recipe, Open / Save / Export as Python, Clear.
* **Model ▸ Split elements / Mesh areas** are **presets over the recipe**: each
  writes the step it stands for and runs the recipe, so one click still does what
  it always did — while the work is now visible, editable and saveable instead of
  hidden in a handler. Its result is still registered under the GUI's own view
  names (`Processed`, `Meshed`).

Steps are frozen dataclasses, so an edit **replaces** the step in the recipe
rather than mutating it; the recipe object stays the single source of truth.

## Roadmap

| Phase | Register item | Content | Status |
|---|---|---|---|
| **A** | [P29](_pending_work.md) | the `workflow` package: steps, recipes, registry, and the four verbs | ✅ landed |
| **B** | [P29](_pending_work.md) | the Recipe panel, the `&Recipe` menu, the Model-menu presets | ✅ landed |
| **C** | **[P30](_pending_work.md)** | **computed results become visible** — `check` (`capacity.*`, `model.checks`, `mesh.checks`) and `chart` (`plotting.report.*`) as steps, with the GUI's **first figure view** and a check table; `combine` gains its view; a recipe's solved cases register as result views (closing the gap noted in *The GUI surface*) | 🚧 planned |
| **D** | **[P31](_pending_work.md)** | **authoring ergonomics** — the command palette (`Ctrl+K`) compiling a command string into a step, a command-echo log recording every action as its equivalent step, and importing a Python script back into a recipe | 🚧 planned |
| **E** | **[P32](_pending_work.md)** | **the report pipeline joins the vocabulary** — the *analysis* verbs the reconciliation needs (modal, response spectrum, pushover), and `generate_report` composing the same registry; see *Resolving the `generate_report` duality* below | 🚧 planned |

**C, D and E are deliberately separate register items** (P30, P31, P32): C makes
computed results *visible*, D makes authoring *fast*, and E reconciles an
existing public pipeline with the new vocabulary.  None depends on another, so
they can be scheduled, reviewed and delivered independently.

**Why E is not part of C.**  C's verbs (`check`, `chart`) are ones the report
pipeline barely uses.  The *real* overlap between `Recipe` and
`generate_report` is the **modal / response-spectrum / pushover** analyses — which
are not verbs yet — together with the report's storage and HTML-rendering
stages, which are not model operations at all and should not become verbs.  That
is three new verbs plus a change to a stable public entry point: a different
scope and a different risk from "draw a result", so it is its own item.

### Resolving the `generate_report` duality — its own item (P32)

`generate_report(config)` is a fixed, complete pipeline with a stable public
signature; a recipe is the same idea, editable.  The two currently coexist
(`docs/report_generation.md` records the position from its side), and that
coexistence is **deliberate and temporary**.

Reconciling them means one of:

* **an adapter** — `generate_report` stays exactly as it is, and internally
  builds and runs a `Recipe`, so the report config becomes a shorthand for one;
* **verbs for the stages** — the report's stages are exposed as verbs, and both
  the report script and the GUI compose them the same way.

Surveying the report's stages makes the choice much narrower than it looks.  Its
stages are *modal → response spectrum → pushover → combination reduction → chart
rendering → archive write → HTML report*.  The first three are model operations
and belong in the vocabulary; the last three are storage and presentation and do
**not** — forcing a Quarto render into a verb would be a category error.  So the
practical reconciliation is:

1. add the missing analysis verbs (`modal`, `response_spectrum`, `pushover`);
2. have `generate_report` compose those same verbs rather than calling the
   analysis functions directly, keeping its own storage and rendering stages;
3. leave the report's public signature untouched throughout.

Deciding formally between the adapter and the verb route is the first task of
**P32**, though the survey above already points at a blend of the two.  What must
**not** happen is a piecemeal merge: `generate_report` is a public entry point, so
a half-migrated pipeline is worse than two clearly-separated ones.



Phase C also introduces the GUI's first **figure view** — nothing in the
application renders a chart today — which is what makes `chart` a step like any
other.

## Boundaries

* **No analysis logic, no topology logic** in this layer — every verb delegates
  to the two-stage pipeline (see *Package layout*, above).
* **No dependency graph.** Steps run in the order written. Composition is
  explicit, exactly as it is in `fea_toolkit.analysis`.
* **No new required dependencies.** The layer is stdlib plus the package.
* **The Qt-free half stays Qt-free.** `steps.py`, `registry.py` and `recipe.py`
  import neither Qt nor OpenSees, which is what lets the vocabulary be tested and
  rendered without either.
* **Non-breaking by construction.** The layer is additive: no existing module's
  behaviour changes, so a script such as `admin_linear_v3.py` runs unchanged.

**What this layer must never do.** It owns no analysis logic and no topology
mutation of its own: every verb delegates to the existing two-stage pipeline. A
recipe *describes* work; it never reimplements it. This is the project rule that
reusable logic belongs in the package and not in the entry point
(`.clinerules` §9, anti-pattern 14) applied to the GUI.
