# Workflow Phase D — the command palette and script interop (P31)

> **Status: 🚧 Planned** — design record; not yet implemented.
> Sources: `docs/_pending_work.md` (P31), `docs/workflow_authoring.md`
> (§ *Precursors*, roadmap row **D**).  Cross-cutting enabler: **Decision 0**,
> the `kind`/`needs` precursor mechanism.

## Scope

Authoring ergonomics, independent of P30 (which was about *output*).  Four
parts, all about *input*:

1. **Command palette** (`Ctrl+K`) — a command string compiles into a `Step`,
   reusing the existing `Selection.from_string` grammar (no second selection
   language), and is inserted into the recipe.
2. **Command echo** — every recipe-affecting action surfaces its equivalent
   step; the message log shows the recipe's JSON so a session is replayable.
3. **Import a Python script back** — the inverse of `Recipe.to_python()`.
4. **Precursor-aware completion** — the palette reads `kind`/`needs` and greys
   an unsatisfied step, or offers to insert its precursor.

## Recon findings

- `needs` is *declared but never validated*: `run_recipe` runs steps in order
  with no precursor check; a verb needing a mesh raises inside its own body.
  The `run_recipe` *validation* is P32's job, not this one.
- One declaration is wrong: `run_static.needs=("model",)` names the context
  *field* `model`, not the *kind* `geometry` (what `mesh` produces and what a
  static solve reads).  Reconciled here, because the palette greys from these
  fields.
- `Selection` already owns the exact tokenizer the palette needs —
  `_scan_clauses(expr) -> (clauses, leftover)` with quoting, `NOT`, `z=LO:HI`.
  It is private; promote it, do not re-tokenize.
- `ParamSpec.coerce` does not accept strings for `float`/`int`/`bool`, so a
  palette's text values need an `ast.literal_eval` pass before `coerce`.

## Shared foundation (the P31 ↔ P32 bridge)

**One ordering vocabulary, one home.**

- Reconcile `kind`/`needs`: fix `run_static.needs` → `("geometry",)`; add a
  registry self-consistency test that every `needs` entry names a kind some
  verb produces (or the reserved starting kind).
- `satisfied_kinds(recipe, *, initial=()) -> set[str]` — the order-based scan
  from § *Precursors* (no graph, no topo-sort).  P31 uses it to grey; P32 uses
  it to validate and to order `generate_report`'s analyses.

## Command grammar

```
<verb>  [<clause> ...]     # verb first; clauses are Selection-style KEY=VALUE
```

Disambiguation: a clause is a **parameter** iff it is not negated and its key
is a declared parameter of the verb; every other `[NOT] KEY=VALUE` clause is a
**selection** clause, re-joined and passed to `Selection.from_string`.
Parameter values parse via `ast.literal_eval` (a bare word falls back to a
string), then `ParamSpec.coerce` validates type + `choices`.  Parameter names
and selection keys are asserted disjoint.

Examples:

```
mesh section="brick wall" create_shells=True
run_static cases={"DEAD": {"DEAD": 1.0}}
wall_shear_check section=Core Nxy=5000 Ny=10000
chart chart=storey_displacements
check_brace_buckling NOT section=COL K=1.0
```

Selection values with spaces/semicolons/commas must be quoted — the existing
`Selection` grammar's own rule, reused verbatim.

## Work items

1. **Foundation** — fix `run_static.needs`; registry self-consistency test;
   `satisfied_kinds` in `workflow/recipe.py`.
2. **Compiler** — promote `Selection.scan_clauses` (public, shared);
   `workflow/command.py` (`compile_command`, `step_to_command`); export from
   `workflow/__init__.py`.
3. **Palette widget** — `gui/views/command_palette.py`; `Ctrl+K` +
   `&Recipe ▸ Command…`; `RecipePanel` gains insert-at-index for precursors.
4. **Echo** — log `recipe.to_json()` + `step_to_command(...)` after
   presets/palette/edits.
5. **Script import** — `Recipe.from_python(source)` via `ast.parse` +
   `ast.literal_eval` on the `RECIPE` literal (no `exec`); `&Recipe ▸ Import…`.

## Sequencing

Foundation → compiler → palette widget → echo → script import.  Each increment
is independently reviewable; nothing blocks on P32.

## Hand-off to P32

- `satisfied_kinds` — P32's `run_recipe` validator calls it and raises.
- Reconciled `kind`/`needs` — P32 adds the `modal` kind and
  `needs=("model","modal")` on RS/pushover, with zero new machinery.
- `compile_command` — P32's greyed `Analysis ▸ Modal/RS/Pushover` entries
  become presets (Decision 3).
- `Recipe.from_python` — re-imports `to_python()` output unchanged.

P31 does **not** add `run_recipe`'s precursor *raise* — that is P32's, and the
modal chain is what makes enforcement meaningful.

## Guardrails

- No second selection language (reuse `Selection`'s scanner).
- No second ordering vocabulary (`satisfied_kinds` is the one home).
- Qt-free core (grammar, validation, precursor logic, `from_python` are stdlib).
- Truthful declarations (hence the `run_static` fix before the palette ships).
- No `exec` in `from_python`.
- Parameter/selection key collision asserted at test time.

## Tests

- `tests/test_command.py` — grammar, disambiguation, quoting, `NOT`, `z`,
  value parsing, `choices`, errors, round-trip, `satisfied_kinds`,
  `from_python`.
- `tests/test_recipe.py` — extend the manifest self-consistency test to check
  `needs` ⊆ produced kinds.
- `tests/test_gui_command.py` — offscreen: Ctrl+K opens the palette; a typed
  command inserts the right step; a greyed verb offers its precursor; Enter
  logs the echo line.

## Open decisions (to confirm before implementation)

1. The grammar — `verb [clause]…` with "param key wins, else selection"
   (recommended), vs an explicit separator between scope and params.
2. Command-echo scope — recipe-affecting actions only (recommended), not
   literally every mouse action.
3. `from_python` scope — the `RECIPE = {…}` literal form only (recommended);
   no `exec`, no imperative-script import.
