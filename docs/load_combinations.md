---
title: "Load Combinations"
description: "Parsing, tree building and composite generation for SAP2000/ETABS load combinations, including the five CSI combination operators."
status: "partial"
tags: [loads, combination, model, results, composite]
category: [model-features]
related: [analysis.md, builder_reference.md, results_schema.md, force_diagram_unification.md]
---
# Load Combinations

SAP2000 and ETABS share CSI's combination engine, so the toolkit treats a load
combination as a **reference graph over load cases**: each combination names
its constituents — load cases (leaves) or other combinations (branches) — and
each reference carries a scale factor. Storage is flat, depth is by reference.

A combination is expanded into one or more **composite load cases** which are
then evaluated against the per-case results and exported as ordinary
`static/{composite}/...` arrays, so a combination can be visualised exactly
like a load case.

## Pipeline

| Stage | API |
|---|---|
| **Parse** | `io/s2k_parser.py::_get_load_combinations()` — groups `COMBINATION DEFINITIONS` rows by `ComboName`, keeping every `CaseName` / `ScaleFactor` row in file order as a `LoadCombinationEntry(name, factor, kind, mode)`. Order and repeats are preserved. |
| **Classify** | `model.load_combinations.classify_combination_refs()` — resolves each reference by name (`"case"` / `"combo"` / `"unknown"`), because the `.s2k` table carries no reference-type flag. |
| **Tree / aggregate** | `build_combo_tree()`, `build_combo_tree_dict()`, `calculate_aggregate_factors()`, and the linear convenience `expand_linear_combination()`. Cycle-guarded. |
| **Generate** | `generate_combination_results()` — produces one or more `CompositeLoadCase` objects (operator table below), each tagged with its `family` and `coords`. |
| **Evaluate / export** | `analysis.combinations.build_combination_results()` evaluates composites against the run load cases; `AnalysisBuilder.export_results(..., model=md, expand_combinations=True)` merges them into the results NPZ. |
| **External set** | `combination_set_from_dict()` / `merge_combination_sets()` — a hand-authored typed definition layered onto the model's (`io.combination_set` for JSON). See *External definition sets* below. |
| **Senses / grouping** | `combination_case_meta()` — `{group, kind, family, coords}` per variant, the single owner of the sign rule; persisted by the NPZ writers as the `static_case_*` arrays. |

Only numeric result fields are combined (force series, nodal-displacement
vectors, numeric scalars); non-numeric leaves such as `converged` flags are
skipped.

## Combination operators

CSI defines five combination types. The toolkit's support status:

| Type | Combined maximum | Combined minimum | Status |
|---|---|---|---|
| Linear Add | Σ xᵢ | Σ xᵢ | ✅ implemented |
| Envelope | max(xᵢ) | min(xᵢ) | ✅ implemented |
| Absolute Add | Σ \|xᵢ\| | −Σ \|xᵢ\| | 🚧 not implemented |
| SRSS | √(Σxᵢ²) | −√(Σxᵢ²) | ✅ implemented |
| Range Add | Σ max(0, xᵢ⁺) | Σ min(0, xᵢ⁻) | 🚧 not implemented |

`xᵢ` is the scale-factor-multiplied result of contributing case *i*; `xᵢ⁺` /
`xᵢ⁻` are that case's own maximum and minimum when it spans a range.

Combination types that are recognised but not yet implemented raise a clear
`ValueError` from `generate_combination_results()` rather than being silently
treated as Linear Add.

### Linear Add

> All load case results are multiplied by their scale factor and added
> together. — *CSI SAP2000 help*

`max = min = Σ xᵢ`. A single signed composite is produced, unless a magnitude
term is mixed in (see *Sign forking* below), in which case one composite is
produced per sign permutation.

### Envelope

> A max/min Envelope of the defined load cases is evaluated for each frame
> output segment and object joint. The load cases that give the maximum and
> minimum components are used for this combo. — *CSI SAP2000 help*

`max = max(xᵢ)`, `min = min(xᵢ)` — the extremes are **selected**, not summed.
Two composites are emitted. The strategy is chosen by `envelope_mode`:

* `"maxmin"` (default) — a per-quantity maximum composite and a per-quantity
  minimum composite. For a magnitude constituent the maximum uses the `+`
  value and the minimum the `−` value.
* `"per_path"` — one composite per referenced constituent, each expanded as
  its own linear combination (the ETABS `build_combo_tree_dict` behaviour).  A
  constituent that itself expands to several variants (a nested Envelope, or a
  Linear Add forking on a magnitude) yields one composite **per variant**,
  named `"<combo> [<branch>] #n"` — the plain `"<combo> [<branch>]"` when the
  branch has only one, so a shared name can never collapse two variants in the
  name-keyed per-case metadata.

### Absolute Add

> The absolute of the individual load case results are summed and positive and
> negative values are automatically produced for each output segment and
> joint. Use this Combo Type for lateral loads. — *CSI SAP2000 help*

Per quantity `max = Σ|factorᵢ·xᵢ|` and `min = −max`. The result is a
**magnitude**, so when it is mixed with signed loads it forks `±` in the same
way a response-spectrum case does.

**Not yet implemented.** It is a drop-in operator — it needs no new result
representation, only a branch in `generate_combination_results()` and the
corresponding reduction in the evaluation layer.

### SRSS

> The Square Root Sum of the Squares calculation is performed on the load cases
> and positive and negative values are automatically produced for each output
> segment and joint. Use this Combo Type for lateral loads. — *CSI SAP2000 help*

`max = √(Σxᵢ²)`, `min = −max` — a magnitude, so it forks `±` when mixed with
signed loads. It is used to combine independent modal results.

### Range Add

> The combined maximum is the sum of the positive maximum values from each of
> the contributing cases (a case with a negative maximum does not contribute),
> and the combined minimum is the sum of the negative minimum values from each
> of the contributing cases (a case with a positive minimum does not
> contribute). This Combo Type is useful for pattern or skip-type loading
> where all permutations of the contributing load case must be considered.
> — *CSI SAP2000 help*

Per quantity `max = Σ max(0, xᵢ⁺)` and `min = Σ min(0, xᵢ⁻)`.

Range Add collapses the permutations of **pattern / skip ("checkerboard")**
loading into a single combination. CSI's knowledge-base article on the
range-add combination gives the worked example: eight pattern load cases yield
255 additive combinations (8×C₁ + 28×C₂ + 56×C₃ + … + 1), all replaced by one
range-add combo.

Like `Envelope`, Range Add emits **two** composites — a combined maximum and a
combined minimum — rather than a single signed value.

**Not yet implemented**, and unlike `Absolute Add` it is not a drop-in: its
definition assumes each contributing case carries its *own* maximum and minimum
(a range — what a moving-load or pattern-load case produces), whereas the
toolkit's current result model stores one signed value per case per quantity.
Implementing Range Add faithfully therefore needs a per-case min/max range
concept; the degenerate single-valued form (`max(0, xᵢ)` / `min(0, xᵢ)`) is an
option but would not deliver the pattern-loading behaviour that motivates the
combination type.

## Sign forking (magnitude vs signed)

A response-spectrum result is a **magnitude** — a peak whose sign is not
physically meaningful — so when it is superposed with a signed field (gravity)
the relative sign is unknown and both possibilities must be offered. The
toolkit therefore forks a magnitude term into a `+` composite and a `−`
composite whenever it is mixed with a non-magnitude term:

| Combination | Composites |
|---|---|
| all signed (`GRAV = DEAD + SDL`) | 1 (signed) |
| all magnitude (`RSX + RSY`, or `RS1 = 1.0·SPEC + 0.3·SPEC`) | 1 (a magnitude) |
| magnitude mixed with signed (`SW + RS1`) | 2ⁿ, where n = independent magnitude terms |

A result counts as a magnitude when it is a response-spectrum load case, an
`SRSS` combination, or a Linear-Add combination whose **every** term is itself
a magnitude — the last is exactly what ETABS writes for
`RS1 = 1.0·SPEC(EQIBC000) + 0.3·SPEC(EQIBC090)`. The property is derived
bottom-up through the reference graph (`_is_magnitude_combo`), not stored, and
anything unproven (an `unknown` reference, a cyclic path) is conservatively
treated as signed.

The sign applies to the whole sub-combination: `RS1` is negated in one piece,
not its individual spectrum constituents.

The prefix of each coordinate comes from the **reference's own factor**, not from
a fixed `+`: a term written `−1.4·QE` forks with `−QE` first (the sense it
actually carries) and `+QE` as its scaled opposite, so the name derived from the
coordinate always matches the sign applied.

## External definition sets

Combinations do not have to come from the `.s2k` table. A **combination set**
is the same flat `{name: definition}` mapping the parser produces, but authored
by hand — so it can live in JSON, a report config or a script and be layered
onto a model's own combinations:

```json
{
  "SEISM": {
    "type": "Linear Add",
    "entries": [
      {"ref": "DEAD", "factor": 1.3},
      {"ref": "RSX",  "factor": 1.4, "magnitude": true}
    ]
  },
  "ENV":  {"type": "Envelope", "entries": [["SEISM", 1.0], ["WINDX", 1.0]]},
  "GRAV": [["DEAD", 1.2], ["SDL", 1.5]]
}
```

| Field | Meaning |
|---|---|
| `type` | The combination operator. Shorthand spellings normalise to the canonical names — `"linear"` / `"add"` / omitted → `"Linear Add"`, `"env"` → `"Envelope"`, `"srss"` → `"SRSS"`, `"absolute"` → `"Absolute Add"`, `"range"` → `"Range Add"`. An unrecognised type raises a clear `ValueError` at expansion rather than silently becoming Linear Add. |
| `entries` | Ordered, duplicate-preserving references. Each is `{"ref", "factor"?, "mode"?, "magnitude"?}`, a `(ref, factor)` pair, or a bare reference string (factor `1.0`). |
| `design` | Optional SAP design-type overrides, keyed by SAP column. |
| *shorthand* | A bare `[[ref, factor], ...]` list is taken as Linear Add. |

`ref` names a load case **or** another combination in the set, so nesting is by
reference exactly as in `LoadCombination`. A set may therefore extend the model's
combinations, override one by re-using its name, or stand alone.

### `"magnitude": true`

A definition cannot tell whether a referenced case is a response spectrum — the
parser learns that from the load case's `CASE - RESPONSE SPECTRUM` definition,
which an external set has no access to. The per-entry hint declares it, and it is
what decides the ± fork (see *Sign forking* above):

```json
"SEISM": {"type": "Linear Add",
          "entries": [{"ref": "DEAD", "factor": 1.3},
                      {"ref": "RSX",  "factor": 1.4, "magnitude": true}]}
```

Without the hint, supply the model's `load_cases` (`definitions=` +
`load_cases=` on the expansion API, or `combinations=` + `load_cases=` when
rendering) and a spectrum case is recognised by its type instead. With neither,
the reference counts as signed and no fork is produced.

### API

| API | Purpose |
|---|---|
| `combination_set_from_dict()` | Canonical dict → `{name: LoadCombination}`. |
| `combination_set_to_dict()` | The inverse. Supersedes `to_e2k_combo_dict()`, which remains the ETABS-side projection. |
| `merge_combination_sets(*sets, load_cases=…)` | Layer sets left-to-right, later winning; re-resolves every reference's `kind` on a deep copy, so the inputs are never mutated. |
| `io.combination_set.read_combination_set()` / `write_combination_set()` | JSON file round-trip. |

Both consumers read the same definition:

* **expansion** — `build_combination_results(..., definitions=set)` or
  `AnalysisBuilder.export_results(..., combinations=set, expand_combinations=True)`
  writes the composites into a results NPZ, and
* **rendering** — `plot_force_diagram(..., combinations=set)` groups and labels
  the cases from the definition that generated them.

```python
from fea_toolkit.io.combination_set import read_combination_set

combos = read_combination_set("combos.json")
builder.export_results("out.npz", static_results=cases, combinations=combos,
                       expand_combinations=True)
plot_force_diagram("out.npz", combo="SEISM", dimension="2d", combinations=combos)
```

### Variant metadata

Every generated composite carries a `family` and a `coords` tuple — the **stable
identity** of a variant.  The display **name is derived from that identity**
(`"<combo> [<coords>]"`), so the two can never disagree and nothing ever has to
parse a name back:

| `family` | `coords` | Members |
|---|---|---|
| `"single"` | `()` | 1 — nothing varies |
| `"fork"` | `(±ref, …)` | 2ⁿ — one signed coordinate per independent magnitude |
| `"envelope"` | `("max",)` / `("min",)` | 2 — the per-quantity extremes |
| `"path"` | `(branch, ±ref, …)` | one per referenced branch (`envelope_mode="per_path"`); a branch that itself forks adds one signed coordinate per variant |
| `"srss"` | `()` | 1 — a magnitude |

`coords` is empty for a family with a single member: there is no axis position to
record.  `family` records the **variant axis**, not the operator that produced
it — a `Linear Add` that references an `Envelope` propagates that family, so
`DEAD + ENV` is an `envelope` pair (`max` / `min`), not a fork, while a genuine
± fork alongside an inherited family still reports `"fork"`.

For example `DEAD + RSX + RSY` (two independent spectra) is a 2² fork whose four
corners are `("+RSX", "+RSY")`, `("+RSX", "-RSY")`, `("-RSX", "+RSY")` and
`("-RSX", "-RSY")` — not a ± pair; the same four are named
`FLAT4 [+RSX, +RSY]` … `FLAT4 [-RSX, -RSY]`.

`combination_case_meta()` turns those into the per-case `{group, family, coords}`
mapping the NPZ writers persist as `static_case_group` / `static_case_family` /
`static_case_coords` — the single owner of the variant identity.

A composite built **by hand** (rather than by `generate_combination_results`)
carries no `family`, so `combination_case_meta()` derives one from its
operator — `"max"` / `"min"` → `"envelope"`, `"srss"` → `"srss"`, a signed
coordinate → `"fork"`, else `"single"`.

## References

- CSI SAP2000 help — *Load Combination* (Linear Add / Envelope / Absolute Add /
  SRSS / Range Add definitions), help.csiamerica.com.
- CSI ETABS help — *Load Combination Data Form* (combination-type table),
  docs.csiamerica.com.
- CSI Knowledge Base — *Range-add load combination* (pattern-loading
  permutation example).
- `docs/_pending_work.md` → **P12** for implementation status and follow-ups.


