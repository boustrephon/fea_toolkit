---
title: "Recipe CLI — Headless Batch Runs"
description: "The headless recipe runner: python -m fea_toolkit run. Load any model file (.s2k/.e2k/.f2k/.json), run a recipe of workflow steps, and emit a results archive (NPZ), a model snapshot (JSON) or a plain-text report — with no GUI."
status: "complete"
tags: [cli, recipe, workflow, batch, npz, report, headless]
category: [core-pipeline]
related: [workflow_authoring.md, load_cases_and_combinations.md, results_schema.md, json_serialization.md, gui.md, analysis.md]
---
# Recipe CLI — Headless Batch Runs

> **Status: ✅ Complete.** Solves a model through a recipe of workflow steps,
> headless and batch-friendly, emitting an NPZ results archive and/or a
> plain-text report.  This is the *generic* counterpart of the GUI's
> "Analysis ▸ Run": both drive the same verbs; only the entry point differs.

The toolkit's analyses are composed from **verbs** — the same vocabulary the
GUI's recipe panel uses — assembled into a **recipe** (an ordered list of
steps).  `python -m fea_toolkit run` runs a recipe against a model file with no
GUI, so the same solve that a user clicks through can be scripted and batched.

## Two artifacts, two purposes

A run can emit two different files, and they are **not interchangeable**:

| | `.npz` — results archive | `.json` — model snapshot |
|---|---|---|
| What it holds | display geometry + nodal displacements + member end forces | the full `SAPModelData`: nodes, restraints, materials, sections, loads |
| Produced by | the `save_results` verb (`save_results_arrays`) | the `save` verb (`write_document`) |
| Re-opened by | `open_results` / **File ▸ Open results…** | `open` / **File ▸ Open…** |
| GUI shows | result views (deformed shape, force diagrams, tables) | the full model tree — editable, re-analysisable |
| Re-analysis? | ❌ no | ✅ yes — it *is* the model |

For a batch flow you usually want the **NPZ** (viewable in the GUI later).  If
you want the GUI to be able to *re-open and re-run* the model, also emit the
**JSON** — which is exactly what the `save` verb does, so a text input
(`.e2k` etc.) can be converted to the toolkit's own JSON snapshot as one step.

## Quick start

```bash
# 1. Author a model (a bespoke builder, or a parsed CSi file)
python examples/build_portal_frame.py --out model.json

# 2. Solve it through a recipe, emitting an NPZ + a text report
python -m fea_toolkit run examples/recipes/solve_report.json --model model.json
```

That second command loads `model.json`, runs `mesh → run_static →
save_results → report`, and prints:

```
Static analysis report
Units: force = KN, length = m
Cases: 1

── Case WX ──
  applied  fx=+57.94  fy=+0  fz=+0
  reacted  fx=-57.94  fy=+0  fz=+0
  ...
  Nodal displacements (ux, uy, uz in m):
         N12  ux=+0.01501  ...
  Member end forces (local; force in KN, moment in KN·m):
      C11-12  i: N=-4.465 Vy=-35.51 ...  j: ...
```

while writing `results.npz` beside it.

## The CLI

```
python -m fea_toolkit run RECIPE [--model PATH] [--verbose]
```

* `RECIPE` — a recipe JSON file (see [Authoring a recipe](#authoring-a-recipe)).
* `--model PATH` — the model file to load as the working document.  Any
  `load_model_data` format: `.s2k` / `.e2k` / `.f2k` / `.$sf`, a raw-table
  `.json`, or a model-codec `.json`.
* `--verbose` — also print each step's completion status.

Exit codes are honest, so a shell loop can batch over many models:

| Code | Meaning |
|---|---|
| `0` | clean run |
| `1` | a step failed (or the run was cancelled) |
| `2` | usage / input error (missing `--model`, unreadable file, bad recipe) |

```bash
for m in models/*.e2k; do
  python -m fea_toolkit run solve.json --model "$m" || echo "FAILED: $m"
done
```

The default `python -m fea_toolkit` (no subcommand) is unchanged: it is the
*lazy* public-API listing, and it still loads no OpenSees/PyVista.  The heavy
imports live inside the `run` handler, so listing stays cheap.

## Stopping at any stage

A recipe is just an ordered list — "stop here" means "don't add the next
step".  Three shipped presets in `examples/recipes/` show the common stops:

| Preset | Steps | Stops after |
|---|---|---|
| `convert.json` | `save` | convert a text model → the toolkit's JSON snapshot |
| `solve.json` | `mesh → run_static → save_results` | write the NPZ |
| `solve_report.json` | `mesh → run_static → save_results → report` | also print the text report |

## Authoring a recipe

A recipe JSON is `{"name": …, "steps": [{verb, params?}, …]}` — the editable
form `Recipe.to_dict` / `Recipe.to_json` round-trips losslessly (see
`workflow_authoring.md`).  Every step's `params` are validated on load, so a
typo is an error, not a silent no-op.

```json
{
  "name": "solve",
  "steps": [
    {"verb": "mesh", "params": {}},
    {"verb": "run_static", "params": {}},
    {"verb": "save_results", "params": {"path": "results.npz"}}
  ]
}
```

The verbs a static batch flow uses:

| Verb | Produces | Notes |
|---|---|---|
| `mesh` | `geometry` | split frames at joints; area meshing is opt-in (see below) |
| `run_static` | `cases` | auto-detects the model's static cases unless `cases` is given |
| `combine` | `cases` | reduce solved cases into load combinations |
| `save` | `session` | write the working **document** (model JSON) |
| `save_results` | `session` | write the solved **cases** to an NPZ |
| `report` | `session` | render the solved static cases as text (static-only for now) |
| `open_results` | `cases` | load an existing NPZ archive's cases |

The analysis verbs themselves (`modal`, `response_spectrum`, `pushover`,
`nonlinear_dynamic`, `ida`, `chart`, `checks`, `load_verification`, …) already
exist — so a recipe can already run the full analysis chain.  Only the `report`
*renderer* is static-only today; extending it is adding renderers, not adding
analysis plumbing.

### Cases: auto-detected unless explicit

`run_static` with no `cases` parameter solves every static case the model
defines; pass `cases` to restrict or name them explicitly:

```json
{"verb": "run_static", "params": {"cases": {"DEAD": {"DEAD": 1.0}}}}
```

### Meshing: split frames by default, mesh areas on request

The `mesh` verb's defaults split frames at their joints (required for analysis)
but leave the heavier options off unless asked:

```json
{"verb": "mesh", "params": {"create_shells": false, "split_slabs_at_walls": true}}
```

`create_shells` meshes area elements into shells (on by default — a no-op for a
frame-only model); `split_slabs_at_walls`, `panel_zone_model` and the rest are
off unless set.  See `workflow/verbs/mesh.py` for the full parameter surface.

## Why not a new "solver" command?

This is deliberately **not** a bespoke `solve` command with its own code path.
The recipe runner is a thin wrapper over
:func:`fea_toolkit.workflow.recipe.run_recipe`, and every operation is a verb
that delegates to the existing two-stage pipeline — so the CLI, the GUI's
recipe panel and a report script all share one spelling (`.clinerules` §3.12).
The `save_results` and `report` verbs are the only genuinely new pieces, and
each is a handful of lines of delegation.
