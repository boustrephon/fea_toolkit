---
title: "Force-Diagram Unification (Phase B)"
description: "Detailed design for unifying the four force-diagram plotting entry points into one unit-aware API."
status: "implemented"
tags: [planning, refactor, plotting, phase-b]
category: [planning]
---

# Force-Diagram Unification (deprecation-plan Phase B)

> **Status (2026-08-24):** implemented.  `plot_force_diagram()` is the
> single unit-aware dispatcher; the four legacy wrappers
> (`plot_force_diagram_3d`, `plot_rs_force_diagram`,
> `plot_npz_force_diagram`, `plot_npz_moment_3d`) were removed in the
> deprecation cleanup.  The sections below are the historical design
> record.

**Status:** implemented (2026-08-24) — milestones 1–4 landed; the four legacy
wrappers were removed in the deprecation cleanup.

## Goal

Replace the four overlapping force-diagram entry points with **one unified,
unit-aware** function that covers every input the toolkit already supports
(`AnalysisBuilder`, in-memory result dicts including RS `element_results`,
and NPZ paths) and dispatches 2D-vs-3D and static-vs-CQC-RS from the input
shape — so callers never have to pick the "right" function or know the
backend in advance.

## Current state

| Function | Location | Backend | Input | Kind |
|---|---|---|---|---|
| `plot_force_diagram_3d()` | `plotting/viz.py:4147` | PyVista 3D | Builder + static force dict, or NPZ data dict | static |
| `plot_rs_force_diagram()` | `plotting/viz.py:4532` | matplotlib 2D | RS `element_results` list or full dict | RS (CQC) |
| `plot_npz_force_diagram()` | `plotting/viz.py:5051` | matplotlib 2D | NPZ path only | static |
| `plot_npz_moment_3d()` | `plotting/viz.py:5120` | PyVista 3D | NPZ path only | static |

Shared helpers already present:

- `_resolve_mesh_data(source, collapse_to_parents=False)` — `plotting/viz.py:2774`.
  Resolves node coordinates + element connectivity from a Builder or an NPZ
  data dict.  This is the geometry half of the input layer.
- `_load_npz_for_plotting(npz_path, combo=None)` — `plotting/viz.py:4928`.
  Loads an NPZ and returns `elem_data` (per-element end forces at
  `z_i`/`z_j`) plus `force_unit` / `length_unit` from the file metadata.
  This is the unit-aware NPZ half of the input layer.

## Duplication map

1. **Three separate input-resolution paths** (Builder, result dicts, NPZ
   path) with no shared normalisation — each function re-implements
   unwrap/dispatch logic (`isinstance(dict)`, key lookup, combo handling).
2. **Two unit strategies** — `plot_rs_force_diagram` hardcodes
   `force_unit="kN"` / `length_unit="m"` defaults; the NPZ functions read
   units from file metadata; the Builder path has units on the model but
   `plot_force_diagram_3d` does not surface them.
3. **Static vs RS is implicit** in the caller's function choice, yet both
   end up as per-element end-force series (RS adds `z_mid` + CQC-combined
   quantities like `My_i`).
4. **2D vs 3D is a separate axis** (PyVista vs matplotlib) that callers
   must know about in advance.

## Design

### 1. Canonical intermediate — `ForceDiagramData`

A dataclass (in `plotting/force_diagram.py`) that any resolved input is
reduced to:

```python
@dataclass
class ForceDiagramData:
    nodes: dict           # node_tag -> (x, y, z)   (for 3D geometry)
    elements: list        # connectivity for centreline drawing
    series: list[dict]    # per element: {q_i, q_j, z_i, z_j, z_mid}
    quantity: str         # canonical quantity key ('My', 'Vz', ...)
    force_unit: str
    length_unit: str
    kind: str             # 'static' | 'rs'
```

### 2. Input resolution — `_resolve_source()`

`_resolve_source(source, force_data, combo)` normalises any accepted input:

- `source` is an `AnalysisBuilder` → read `mesh_model.units` + nodes +
  elements; use `force_data` (static) or `force_data["element_results"]`
  (RS).
- `source` is an NPZ **path or dict** → delegate to the existing
  `_load_npz_for_plotting` / `_resolve_mesh_data`; `combo` picks the
  `static/{combo}/` slice; RS arrays auto-detected from the presence of
  `rs/` keys.
- `force_data` is the full `extract_element_rs_forces()` dict → unwrap the
  `"element_results"` key (same rule as today's
  `plot_rs_force_diagram`).

#### RS element forces from an NPZ archive

`write_results()` stores per-element RS forces as the flat `rs/elem_*` block
(`collect_rs_element_force_arrays`) rather than the `element_results` list of
dicts the in-memory builder path yields.  `_extract_npz_rs_forces()`
(`plotting/viz_forces.py`) bridges the two: it zips `rs/elem_sap_id`,
`rs/elem_z_bot`, `rs/elem_z_mid` and the twelve `rs/elem_<component>` arrays
back into records, tolerating the deprecated alias spellings, and returns `[]`
when the block is absent (it is optional in the schema).

`_resolve_source()` then builds both representations:

- `series` — 2D quantity-vs-elevation data (via `_build_series_from_rs`), the
  historical RS view;
- `force_map` + `nodes` + `frames` — per-element geometry-matched forces
  (via `_build_rs_force_map`, matched on `frame["id"]` ↔ `elem_id`), which the
  3D renderer consumes.

Because the stored RS forces are already in the element **local** system, the
force-map entries expose them under the `*_i_local` / `*_j_local` variant keys,
so `_compute_local_forces` takes its verbatim fast path instead of rotating
already-local values a second time.

Dispatch: RS renders **2D by default**; pass `dimension="3d"` (CLI:
`--dimension 3d`) to get the per-element tube/flag view, which reuses the
shared `_render_static_3d` renderer.

Resolution order for units (first hit wins): explicit `force_unit` /
`length_unit` args → builder/model units → in-memory dict `"units"` key →
NPZ metadata.  **Never hardcode `kN`/`m`.**

### 3. Unified entry point — `plot_force_diagram()`

```python
def plot_force_diagram(
    source, force_data=None, *,
    quantity="My", kind=None, dimension=None,
    combo=None, force_unit=None, length_unit=None,
    use_local=True, both_ends=False, **kwargs,
):
```

Dispatch rules:

- `kind` is inferred when `None`: `"rs"` if `force_data` is a full RS
  results dict or a list of per-element RS records (any element carries
  the `z_mid` marker); `"static"` otherwise.
- `dimension` is inferred when `None`: `"3d"` if PyVista is available and
  geometry is present; `"2d"` (matplotlib) otherwise.  Callers may pin it.
- `quantity` accepts both the RS key style (`'My_i'`, `'Vz_i'`) and the
  plain style (`'My'`, `'Mz'`) and normalises internally.
- `kind == "rs"` → 2D matplotlib line plot (today's
  `plot_rs_force_diagram` rendering, unit-aware).
- `kind == "static"` + `"3d"` → today's `plot_force_diagram_3d` rendering
  (flag/tube modes preserved via `**kwargs`).
- `kind == "static"` + `"2d"` → today's `plot_npz_force_diagram` rendering.

### 4. Backward compatibility (deprecation convention §4.5)

- `plot_force_diagram_3d`, `plot_rs_force_diagram`,
  `plot_npz_force_diagram`, `plot_npz_moment_3d` become **thin wrappers**
  over the unified function for one release cycle.
- After the release, the wrappers are removed in a single cleanup PR (same
  pattern as the deprecation-removal Phase 3 PR).

## NPZ input — force-array orientation

Unified NPZ files store static frame forces as **component-keyed** arrays —
one array per force component, indexed in **frame-element order** — the
same order as the geometry arrays `frame_sap_id` / `frame_node_i` /
`frame_node_j` (the active `mesh_model.frame_elements` in iteration order):

```python
# n_frame_elements = number of active frame elements in
# builder.mesh_model.frame_elements (the writer's geometry order)
static/{case}/fx_i   # shape (n_frame_elements,) — Fx at the I-end
...
static/{case}/mz_j   # shape (n_frame_elements,) — Mz at the J-end
```

The in-memory extraction API, `AnalysisBuilder.extract_static_element_forces()`,
returns the same data **element-keyed** — one dict per element:

```python
{elem_tag: {"Fx": ..., "Fy": ..., "Mz": ..., "Fx_j": ..., "Mz_j": ...}}
```

`plot_force_diagram` reads the component-keyed arrays, so a caller writing a
force-bearing NPZ must **transpose** the element-keyed dict into
component-keyed arrays before export.

Key mapping:

| `extract_static_element_forces()` key | NPZ array key |
|---|---|
| `Fx` … `Mz` (I-end) | `fx_i` … `mz_i` |
| `Fx_j` … `Mz_j` (J-end) | `fx_j` … `mz_j` |

Prefer the built-in convenience — `AnalysisBuilder.export_static_results()`
extracts the forces and performs this transpose for you in one call:

```python
results = builder.run_static_analysis(pattern_scales={"DEAD": 1.0})
builder.export_static_results("results.npz", results, case_name="DEAD")
```

The manual transpose (array order follows the active
`mesh_model.frame_elements` in iteration order — the same order the writer
emits the geometry arrays) is:

```python
results = builder.run_static_analysis(pattern_scales={"DEAD": 1.0})
elem_forces = builder.extract_static_element_forces()  # element-keyed

# The NPZ contract is strict: every active frame element must have an
# entry, and every component array must have the same length.  Failing
# fast beats silently misaligning forces with the geometry arrays.
expected_components = ["Fx", "Fy", "Fz", "Mx", "My", "Mz"]
force_arrays: dict[str, list[float]] = {f"{c.lower()}_i": [] for c in expected_components}
force_arrays.update({f"{c.lower()}_j": [] for c in expected_components})

# Skip inactive parents exactly like the writer, so each appended value
# lands at the frame-element index matching frame_sap_id / frame_node_i.
for eid, elem in builder.mesh_model.frame_elements.items():
    if getattr(elem, "inactive", False):
        continue
    tag = builder.frame_tag_map.get(eid, elem.elem_tag)
    fe = elem_forces.get(tag)
    if fe is None:
        # An active element with no force data would break index alignment
        # (the arrays are frame-element-ordered) — fail instead of skipping.
        raise ValueError(
            f"Active frame element {eid} (tag {tag}) has no force data — "
            "every active element must be present so the NPZ component "
            "arrays stay aligned with frame_sap_id / frame_node_i."
        )
    for key in expected_components:
        # I-end ("Fx") -> "fx_i"; J-end ("Fx_j") -> "fx_j"
        force_arrays[key.lower() + "_i"].append(float(fe[key]))
        force_arrays[key.lower() + "_j"].append(float(fe[f"{key}_j"]))

# All component arrays are built in the same loop, so their lengths are
# guaranteed equal; assert the contract defensively before export.
lengths = {k: len(v) for k, v in force_arrays.items()}
if len(set(lengths.values())) != 1:
    raise ValueError(f"Inconsistent force-array lengths: {lengths}")

case = dict(results)
case["element_forces"] = force_arrays
builder.export_results("results.npz", static_results={"DEAD": case})
```

Unit metadata: files written after the unit-canonicalisation work carry
top-level `force_unit` / `length_unit` arrays; older files without them still
plot, falling back to the documented `"kN"` / `"m"` axis labels.

## Rendering dimensions — 2D vs 3D

`dimension` and `kind` are independent axes.  `kind` says *where the numbers
come from* (a static case vs a response-spectrum result) and how they are
keyed; `dimension` says *how they are drawn*.  The same `quantity` and `combo`
render in either dimension — only the encoding changes.

| | 2D (Matplotlib) | 3D (PyVista) |
|---|---|---|
| What it is | chart: quantity (x) vs elevation `z` (y) | spatial model, quantity drawn on the members |
| Element drawn as | line segment `(v_i, z_i) → (v_j, z_j)` | ribbon offset from the member axis (`flag`) or tube (`tube`) |
| Quantity encoded as | position along the x-axis | offset distance, colour, tube radius |
| Sign change | curve crosses the zero axis; triangular fill switches side | ribbon flips to the opposite side of the member |
| RS specific | one point per element at `z_mid`; `both_ends=True` adds a `[v_i, v_j]` segment | same flag/tube geometry, driven by the RS force map |
| Availability | always (Matplotlib is core) | needs PyVista importable |

The 2D view is a **plot**, not a picture of the structure: it answers "how does
this quantity distribute up the building".  By default it draws the **storey
profile** — one line through the value summed at each distinct elevation, over
the elevation-changing members only (see below).  The 3D view is a **model**: it
answers "where in the structure is this quantity large" across the whole frame
at once.  Neither substitutes for the other, which is why `dimension` is an
explicit axis rather than something inferred from the data alone.

### Storey profile (2D default)

`plot_force_diagram(..., by_storey=True)` (the default) sums each case by
elevation instead of drawing one segment per element.  The legacy per-element
form is still available via `by_storey=False` for single-member checks.  The
summation is `sum_storey_forces()` in `model/storey_response.py`:

* only **elevation-changing** members contribute — a horizontal member's two
  ends share one elevation and cancel out of a level profile;
* the archive's stored forces are **local**, so each member end is rotated to
  **global** with `get_local_axes(axis, frame_angle)` before summing;
* each level's `Mx`/`My` (overturning) and `Mz` (torsion) include the
  force x lever-arm term about the level's reference point, sharing
  `_cqc.lever_arm_moment()` with the base overturning calculation;
* `cm_method` selects that reference point — `"bbox"` (bounding-box midpoint of
  the nodes at the level, **default**) or `"mass"` (mass-weighted centre of
  mass).

#### Why a bare response-spectrum case must not be summed

An RS case read from an archive is **not usable as a signed sum**, and the
failure is easy to mistake for a rendering bug.  The stored values are
**magnitudes**: the modal responses are combined with SRSS/CQC
(`srss_combine_matrix` / `cqc_combine_matrix`, both `sqrt(sum of squares)`), so
the sign of a component — and with it the equilibrium relation
`F_j = -F_i` — is destroyed.  Measured on the piperack archive:

| case | `fx_i` negatives | `fx_i + fx_j` |
|---|---|---|
| `DEAD` (static) | 299 of 1102 | exactly `0` for every member |
| `RSX` (spectrum) | **0** of 1102 | generally non-zero |

Summing such a case with a sign is meaningless twice over: the i- and j-end
contribution no longer cancel, and the 55 vertical members whose local frame
points *down* (their `i` end is the higher node) rotate to the opposite global
direction from the other 479.  The level sum then flips sign from level to level
— which is exactly the "switching sides" pattern an RS storey profile shows.

Correct treatments, in order of preference: plot the RS case as **two signed
diagrams** (`#1` / `#2`, the ±fork a design combination already stores), or as a
**symmetric ±M envelope**; or derive a sign from the dominant mode's
participation before summing.  Never draw a single signed sum of magnitudes.
The RS-specific path (`kind == "rs"`) plots one point per element at `z_mid`
rather than a storey sum, so it is unaffected; the hazard is a *spectrum case
stored as a static case* in the archive, which reaches the storey summation
above.

#### `storey_mode` — two different quantities

A level's sum depends on which members are counted there, and the two readings
differ by the level's own applied load.  This is *not* a detail:

| `storey_mode` | Counts | Reads as | DEAD check |
|---|---|---|---|
| `"cut"` (default) | every member whose span contains the level **from below to above** (`z_lo <= z_c < z_hi`), once, from its lower end | the force **transmitted across the plane just above the level** — the storey shear / axial above, i.e. what a storey-shear or overturning diagram reports | monotonic 1199.9 kN → **0.0** at the roof |
| `"end"` | every member **end**, credited to its own node's level | the **load path**: minus the applied nodal load, and the support **reaction** at a restrained level | oscillates about **0** — except the base, which reads the whole reaction |

Both were measured on the piperack model; `cut` reproduces the cumulative weight
above at every level.  Neither is wrong — they answer different questions — but
only `cut` is a storey profile in the structural-engineering sense.

Two things follow that are easy to get wrong:

* **Only the lower side is used in `cut` mode.**  Crediting a member's upper end
  as well would add a delivered end force *and* a transmitted internal force for
  the same member at the same level; those are opposite in sign, so the sum
  silently collapses toward the `"end"` value (measured: 63 kN of spurious
  lateral force on a gravity-only case that must be exactly zero).
* **A member that spans a level with no node there must still contribute.**  On
  the piperack model, 20 levels exist and **17 of them are crossed by members
  with no node there** — 521 (level, member) crossings in total, worst case 104
  members at z = 3.65 m.  Only the **lower** end of such a member carries a node,
  so an end-based sum loses its force entirely at every level in between.  `cut`
  mode evaluates the member's internal force at the cut by transporting the
  lower end force along the member — force constant, moment linear.  That
  transport is exact for a member carrying no span load; for a loaded member the
  resultant applied between its end and the cut is not in the archive and is
  therefore omitted.

#### `"end"` mode is a load path, not "load arriving"

`"end"` is nodal equilibrium:

```
sum(member end forces at a level) = -(applied nodal load) - (support reaction)
```

so at a **restrained** level it reads the support **reaction** — the load
*leaving* the structure — not the load arriving.  Measured on `DEAD`: the base
reads **1200 kN**, the entire gravity reaction, while the levels above oscillate
about zero.  That is correct for the definition, and a useful sanity check, but
it means the base value is *not* "the vertical load coming in at the base".
For a self-equilibrated case, every **unrestrained** component sums to zero at
every level while a **restrained** one sums to the reaction — which is why the
`DEAD` lateral profile is machine-zero everywhere yet its base `Fz` is 1200 kN.

The genuinely "where does the load enter" picture is the continuous
distributed-load field along each member, which two end values per member cannot
show.  See **P19** in [`_pending_work.md`](_pending_work.md) for the
member-interior station sampling that would.

### Two-sided / multi-fork results — 2D implemented, 3D design note

A response-spectrum result is a *magnitude*, and a combination that mixes one
with signed gravity/wind is two-sided **and asymmetric** (its `+QE` and `-QE`
composites differ).  Such a result is drawn as **separate signed diagrams**,
never as one filled band: collapsing the curves into a `(lo, hi)` band loses the
zero crossings that give a force diagram its triangles, and is only honest in
the symmetric case (`lo == -hi`) — i.e. a bare RS magnitude.

**Collections, not pairs.**  A magnitude superposed with signed terms forks
once per independent magnitude, so the general case is a **family** of 2ⁿ
variants, and an `Envelope` contributes a *max*/*min* pair instead (whose
members are **not** negatives of each other).  Each variant therefore carries a
`family` and a `coords` tuple — its stable identity — populated at the fork
point in `generate_combination_results()` and persisted per case by
`combination_case_meta()` → `static_case_family` / `static_case_coords`:

| `family` | `coords` | Members |
|---|---|---|
| `"single"` | `()` | 1 |
| `"fork"` | `(±ref, …)` | 2ⁿ |
| `"envelope"` | `("max",)` / `("min",)` | 2 |
| `"path"` | `(branch,)` | one per branch |
| `"srss"` | `("srss",)` | 1 |

A `+QE` / `-QE` marker alone cannot express a 2ⁿ corner or an envelope extreme,
so `static_case_kind` stays `"+QE"` / `"-QE"` only for a **single**-sense fork
and is `""` otherwise; the coordinates carry the detail.

* **2D — implemented.**  `plot_force_diagram(..., dimension="2d")` draws
  **every** member of the requested case's group on one set of axes — the
  primary curve in blue circles, the rest through an orange-square /
  green-triangle / … cycle — with a legend when there is more than one.  Each
  keeps its own triangles, so a two-sided result reads as the SAP2000/ETABS
  "range" display and a 2² fork reads as its four corners.  `both_sides=False`
  draws one curve.  Scope: the **storey profile** (the 2D default); the legacy
  per-element `by_storey=False` path is left single-curve, since overlaid
  per-element diagrams are unreadable.

  *Grouping.*  Not guessed from the plot — resolved by `_group_members()` /
  `_case_pairs()`, in priority order:

  1. an explicit `combinations=` definition
     (`plot_force_diagram(..., combinations=…, load_cases=…)`), expanded through
     the same code path that generated the archive so its variants resolve to
     the archive's case names — filtered to the names the archive actually
     holds,
  2. the archive's `static_case_group` (with `static_case_kind` /
     `static_case_family` / `static_case_coords`), else
  3. the `"<combo> #1"` / `"<combo> #2"` label convention that
     `load_combinations._name_variants()` emits.

  The label fallback is trusted **only when a base appears exactly twice**: two
  forked spectrum entries give four cartesian variants in which `#1` and `#2`
  are *not* the two senses of one magnitude.  A longer group is therefore left
  unpaired by the **sense** helper `_companion_case()` — which returns a single
  opposite-sense companion — while `_group_members()` groups it correctly from
  the metadata.  Metadata therefore outranks labels for grouping, and a
  definition outranks both.

  A consequence worth stating: a variant whose *case name* does not follow the
  generation convention can still be grouped, because `group` and `coords` are
  recorded rather than parsed.

  Verified on the piperack archive as `profile(#1) - profile(#2) == 2 × 1.4 ×
  profile(RS)` for `Fx`, `My` and `Mz`, in both the X and Y spectrum
  directions (residual ≤ 2e-11 kN·m) — which pins the sibling *and* the sign
  convention (`#1` = `+QE`), not merely that two curves were drawn.
* **3D — one case at a time.**  The 3D paths render the *selected* case's force
  map; `combinations=` is accepted (and resolves the grouping/legend the same
  way) but the multi-member overlay is a design note, not implemented.  Two
  ribbons would sit on the same member axis and coincide wherever the diagrams
  agree.  Offset each ribbon along the **binormal** `b = axis × vn` (`vn` being
  the flag's existing offset direction) — the `+` case by `+δ`, the `-` case by
  `-δ` — so that rotating the model separates them.  The value offset inside
  each ribbon stays along `vn`, so the triangle/sign-flip geometry is unchanged.
  Applies to `tube` mode too.  A 2ⁿ fork would need 2ⁿ offsets, which is why the
  family is part of the case metadata rather than a two-member special case.

`δ` is a **layout** offset: derived from geometry and units — a small fraction
of a characteristic model length, so it scales with the model's length unit —
and **never** from the force magnitude.  It separates the two ribbons; it does
not encode data.

## Test plan

- **Table-driven input equivalence**: the same model plotted via Builder,
  in-memory result dict, and NPZ path must produce identical series data.
- **RS list-vs-dict**: `element_results` list and the full
  `extract_element_rs_forces()` dict give the same figure.
- **Unit propagation**: NPZ metadata `kN`/`m` vs `N`/`mm` produce the
  correct axis labels; explicit `force_unit` overrides metadata.
- **Dispatcher**: static vs RS classification (covering both the RS
  `element_results` list form and the full `extract_element_rs_forces()`
  dict form), 2D vs 3D selection, and manual overrides behave as specified.
- **Unified dispatcher**: `plot_force_diagram` covers all input forms —
  Builder + static force dict, in-memory result dict, NPZ path (2D and
  3D), and the RS `element_results` list / full
  `extract_element_rs_forces()` dict.  The legacy wrapper names
  (`plot_force_diagram_3d`, `plot_rs_force_diagram`,
  `plot_npz_force_diagram`, `plot_npz_moment_3d`) were removed in the
  2026-08-24 cleanup — no legacy call patterns remain.
- **Multi-member grouping** (`TestMultiMemberGrouping`): a 2² fork's four
  corners group and label from `static_case_coords` — `_group_members()`
  returns all four while `_companion_case()` still refuses the ambiguous sense
  pair; a definition with a `"magnitude": true` hint supplies the same grouping
  for an archive carrying **no** `static_case_*` arrays; and `_render_static_2d`
  draws one curve per member (legend texts asserted) with no legend for a
  single curve.

## Milestones

1. ✅ Add `plotting/force_diagram.py` with `ForceDiagramData` +
   `_resolve_source`; rewire `plot_npz_force_diagram` +
   `plot_npz_moment_3d` through it (NPZ-only slice).
2. ✅ Add Builder/dict resolvers; rewire `plot_force_diagram_3d` +
   `plot_rs_force_diagram` through the same layer.
3. ✅ Land `plot_force_diagram()`; convert the four legacy names to wrappers.
4. ✅ (2026-08-24) wrappers removed in the deprecation cleanup PR.

## Out of scope

- Splitting `plotting/viz.py` itself — do that *after* this lands, so the
  split operates on the smaller post-unification module (see
  `docs/_pending_work.md`).
- 2D matplotlib rendering of RS results in 3D — keep RS as the 2D line plot
  unless a concrete request appears.
- Changing the NPZ schema (`io/results_schema.py`) — the unification reads
  the existing schema only.

## Risks

- `_resolve_mesh_data` assumes Builder or NPZ-dict input; in-memory
  *static* result dicts from `extract_static_element_forces()` carry no
  geometry, so Builder geometry is required (or an optional `nodes`/
  `elements` argument on the unified function).
- RS `element_results` key names (`z_mid`, quantity suffixes) are a
  de-facto contract — document them as such in the module docstring.
- PyVista stays optional: the 2D path must never import it.
