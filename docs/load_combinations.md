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
| **Generate** | `generate_combination_results()` — produces one or more `CompositeLoadCase` objects (operator table below). |
| **Evaluate / export** | `analysis.combinations.build_combination_results()` evaluates composites against the run load cases; `AnalysisBuilder.export_results(..., model=md, expand_combinations=True)` merges them into the results NPZ. |

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
  its own linear combination (the ETABS `build_combo_tree_dict` behaviour).

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

## References

- CSI SAP2000 help — *Load Combination* (Linear Add / Envelope / Absolute Add /
  SRSS / Range Add definitions), help.csiamerica.com.
- CSI ETABS help — *Load Combination Data Form* (combination-type table),
  docs.csiamerica.com.
- CSI Knowledge Base — *Range-add load combination* (pattern-loading
  permutation example).
- `docs/_pending_work.md` → **P12** for implementation status and follow-ups.


