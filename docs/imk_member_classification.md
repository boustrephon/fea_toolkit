---
title: "IMK Member Role & Type — Design Note"
description: "How member role (column/beam) and member type (moment/brace/fixed-pin) are identified for the per-member IMK (IMKBilin) calibration, reconciling benchmark-matching with the general element-classification path."
status: "partial"
tags: [imk, ibarra-krawinkler, lignos-krawinkler, deterioration, hinge, element-classification, member-classification, benchmark, sac]
category: [planning]
related: [imk_deterioration_model.md, element_classification.md, benchmark_roadmap.md]
---

# IMK Member Role & Type — Design Note

⚠️ Partial — steps 1–3 and (b) landed (2026-10-28): the `element_tbl` type column
is transcribed (`BEAM_TYPES`), `reconcile_member_types` applies the study
override with a `UserWarning` on mismatch, `enrich_imk` skips the study braces
(the 3-story link bay and the 20-story 1st-basement beams), and the axial ratio
is per member (`derive_column_axial_ratios_by_member`).  Items (c)/(e) are now
designed end-to-end: hinge-eligible **segments** (`frame_segments`) computed in
preprocessing, **one spring per hinge-eligible point**, and IMK parameters
computed at build time via `enrich_hinge` (a route, not a model enrichment) —
with a residual-stiffness floor and collapse detection for the IDA work.

## Goals and the tension

Two goals govern this work, and they do not fully align:

1. **Match the benchmark** — reproduce the member classification and analysis
   choices made in the original study (Ohtori et al. 2004; Ibarra–Krawinkler
   2005; Lignos 2008) so our numbers are comparable to the published ones.
2. **Be general** — identify role and member type programmatically, for arbitrary
   models, not just the SAC frames.

The preferred direction is **analytical first, override second**: the general
classifier is the *primary* derivation of role/type, and a study's own choices
are applied as an **override on top** — with a **warning wherever the override
disagrees with the analytical result**.  The warning is the signal that either
the analytical rule needs refining, or the study made a genuinely idiosyncratic
choice worth recording — so study-specific quirks are never silently baked into
the universal rule, and a divergence from the study is never silently ignored.

## Background

* The benchmark's `element_tbl` carries a **member-type flag** in its fourth
  column — `1` = moment (concentrated plasticity), `3` = brace (axial-only),
  `5` = fixed-pin — which `sac_member_data.py` does **not** transcribe today
  (the header notes "not transcribed here").  The exact codes must be verified
  against `struct#.m` when transcribing.
* The toolkit already has the **analytical-default + explicit-override** pattern
  (see `element_classification.md`): the geometry signal
  (`Preprocessor._classify_element_type` → `MeshModel.frame_element_types`,
  `beam`/`column`/`brace`) plus the section-type signal
  (`Selection.from_brace_sections()`), with authoritative overrides
  (`brace_sections` / `brace_selection`) applied on the builder.  Phase 2 there
  proposes a first-class `frame_design_roles` with the same default/override
  split.  This note reuses that pattern and adds the *warning* it currently
  lacks.
* `enrich_imk` currently uses a **benchmark-specific orientation heuristic**
  (`_frame_element_roles`: vertical → column, horizontal → beam) to choose the
  column-vs-beam equation, and models the brace/pin members as flexural beams
  (documented approximations).  Per-member length was landed in Step 3; this
  note covers the *role/type* layer on top of it.

## (a) Role & type derivation

**Proposal — analytical first, then a study override with a warning.**

Three steps, applied in order:

1. **Analytical.** The general classifier derives the role — the geometry signal
   (`frame_element_types`: `beam`/`column`/`brace`) + the section-type signal
   (`Selection.from_brace_sections()`), and member releases derive pinned ends —
   no benchmark data involved.
2. **Override.** Transcribe the `element_tbl` type column as a study override
   table (`MEMBER_TYPES_3/9/20`: `moment`/`brace`/`fixed-pin`), applied in the
   same spirit as the existing `brace_sections` / `brace_selection` overrides —
   authoritative, not gated by the analytical role.
3. **Reconcile.** Map the analytical result onto the study taxonomy, compare, and
   **warn on every mismatch** — following the codebase convention:
   `warnings.warn(<f-string>, UserWarning)`, asserted in tests with
   `pytest.warns(UserWarning, match=…)`.  The override wins.

| Analytical (general) | Study type (override) | IMK treatment |
|---|---|---|
| brace section / truss | brace (3) | axial-only — **no** flexural IMK hinge |
| beam or column, no release | moment (1) | IMK hinge at both member ends |
| beam or column, pinned end | fixed-pin (5) | IMK hinge at the moment end only |

Mismatches are expected and informative — they are exactly the members where the
study diverges from the analytical rule (see (d)).  A test asserts the **set** of
mismatches per SAC model, so the warnings are a checked invariant rather than
noise.

## (b) Per-member axial ratio

**Landed (2026-10-28) — `Pg/Pye` per column *segment*, not per base section.**

The benchmark's axial ratio decreases up the building (each column segment
carries only the tributary mass *above* it).  `derive_column_axial_ratios_by_member`
computes the per-member ratio:

* `Pg = g · Σ(tributary mass above the segment's base)`,
* `Pye = A_physical · Ry · Fy` (the segment's own physical section, below-splice
  for a splice row).

`derive_column_axial_ratios` keeps the per-section (governing, base) summary;
the Lignos-2019 branch splits at `Pg/Pye = 0.20` and `0.35` now apply per
member.  This is both benchmark-matching (each column segment has its own ratio)
and general (it works for any column stack, spliced or not).

**Where it lands.**  In the route-agnostic design the **raw** gravity axial
`P_g` (model units, compression +) is stored on `FrameSegment.axial` (see
(c)/(e)); the IMK ratio `Pg/Pye = P_g / (A·Ry·Fy)` is derived at build time by
`enrich_hinge`, which knows the overstrength `Ry` (an AISC/SAC convention, not a
model property).  This keeps the model convention-free and converges with the
existing `_stack_gravity_axial` (raw `P_g` for the Elwood limit-state path) —
one gravity-axial quantity, two consumers.

**Transfer columns — warn, don't model the transfer.**  The tributary stack is
exact only on a continuous vertical support chain down to a restraint.  A column
whose bottom node is neither restrained nor continued by a column below — it
lands on a beam / girder, including a cantilever's single support — has its load
redistributed laterally, so the closed-form stack is wrong there.  Detecting the
full transfer is out of scope; instead the derivation walks each column's
support chain and **warns** (`UserWarning`) naming every transfer column.  The
fallback is a static analysis — `gravity_axial_ratios` already does this, and
the deferred `derive_column_axial` verb is the solved-case route that re-stamps
`frame_segments`.  The warning is the trigger: tributary for continuous stacks,
solve for transfers.

## (c) Where the per-member parameters live

**Current:** `elem.imk_params` rides on the `FrameElement`; it survives
preprocessing only because the SAC model is one-element-per-member (no
splitting).

**Proposal — attach the IMK hinge at *hinge-eligible* points; keep the interior
elastic.**

The IMK hinge is a zero-length spring at a point that can plastify.  A member's
hinge-eligible points are:

1. **Member ends** (its two joints) — always.
2. **Framing points** — interior nodes where *another member frames in* (e.g. a
   secondary beam landing on a girder at mid- or third-span).  That point
   carries the framing member's end moment/shear, so the girder can hinge there
   too.  These are the ``AtJoints`` split nodes — SAP/ETABS auto-mesh a member
   at a secondary member's end node, and ``split_elements`` already records
   them as ``parent_id`` children.
3. **Pure discretization points** — never.  A sub-element created only for
   distributed-load resolution or P-Δ (no framing member) must stay elastic.

Consequence — the IMK calibration unit is the **segment between consecutive
hinge-eligible points**, not the full member: a girder with a secondary beam at
third-span is *three* segments, each calibrated with its own length (``Lb``,
``L/2d``).  This is the ``elasticBeamColumn + zeroLength`` equivalent of
OpenSees' ``beamWithHinges -subdivide`` (elastic interior, hinges at the ends),
extended to also permit hinges at framing points.

Implementation shape (open):

1. Compute **`frame_segments`** in preprocessing (route-agnostic): for each
   member, the ordered hinge-eligible points and the segments between them —
   elastic sub-element chain, length, per-segment axial (see (e)).
2. Teach ``_create_lumped_hinges`` to place **one spring per hinge-eligible
   point** — at a member end the spring joins the structural node to the first
   segment's end; at a framing point it joins ``segment_{i-1}`` to
   ``segment_i`` (the standard ``elastic → spring → elastic``
   concentrated-plasticity topology) — and emit **elastic** interior elements
   everywhere else.
3. Compute the IMK parameters **at build time** via ``enrich_hinge`` (see (e)):
   per segment, from section + material + axial + length through the existing
   ``lignos_krawinkler_2011`` calibration — nothing stamped on the model, and no
   recalculation per split (the segment *is* the calibration unit).

**One spring per point, not two back-to-back.**  At a framing point the girder
is *cut* there, so a naïve per-element emission would leave two back-to-back
springs through a shared middle node — ``[G-0] -(s0j)- M -(s1i)- [G-1]``.  M is
then connected only through springs; when both reach the residual plateau (zero
*tangent* stiffness) and the framing beam is pinned, M's rotation is restrained
by nothing — a **spurious** singular mode that is *not* the physical collapse
(the elastic segments still bend and anchor M).  Placing **one** spring between
the two segments — ``[G-0] -(sM)- [G-1]`` — keeps each side anchored to its own
elastic segment, so the only soft mode left is the genuine plastic hinge (the
relative rotation).  The residual plateau still reaches zero tangent at full
degradation, so a residual-stiffness floor plus clean collapse detection are
still needed (decision 7) — but the spurious mode is gone.

## (d) Special members (brace / fixed-pin)

Three members are currently given flexural IMK hinges that the benchmark treats
differently:

| Member | Benchmark | Repo today |
|---|---|---|
| 3-story 4th-bay link bay | brace (`type 3`, axial-only) | flexural W21×44 beam with a hinge |
| 20-story basement beams | brace (`type 3`) | flexural beams |
| 9-story gravity-bay beams (bay 4) | fixed-pin (`type 5`, M3 released at far end) | release transcribed but **skipped** under `hinge_model='lumped'` |

These are the members the reconcile step in (a) distinguishes.  The 3-story link
bay and the 20-story basement beams are **classification overrides**: the study
tags them brace while the analytical rule (an I-section, horizontal, no release)
would call them a moment beam — so the warning fires and the override wins,
removing the flexural hinge.  The 9-story gravity-bay is *not* a classification
mismatch: "beam + pin release" already maps to `fixed-pin` analytically, but the
lumped-hinge build currently **skips the release**, so the pin is lost at build
time — a separate release-honouring issue, tracked apart from the type column.

## (e) Switchable rules, applied to a `Selection`

**Assessment — right direction, simplified form.**  `Selection`
(`model/selection.py`) is already the scoping primitive the workflow layer hands
every verb (§3.12), so "a list of rules applied to a `Selection`" is the natural
shape.  The first draft over-built it (a rule registry with `ParamSpec`-style
specs, a JSON DSL, a verb); the practical form is lighter and reuses what already
exists.

**The split.**  Two levels, each with an existing home:

1. **`Selection` scopes the members** — which members to consider (section,
   material, group, storey, elevation, id, …).  The existing `Selection`, passed
   as-is — no bespoke `scope` key.
2. **A rule list classifies the points** — within each selected member, which
   nodes are hinge-eligible.

A rule is **not** itself a `Selection` (a `Selection` has no "member degree" or
"endpoint" criterion); the list is *applied to* the members the `Selection`
returns.  `None` (the default) means **all frame members** — the brace /
leaning-column / release filtering is the *rules'* job, not the `Selection`'s,
so a default `Selection(element_types=["beam","column"])` would be a second
spelling of `exclude_braces` (anti-pattern 17).  A `Selection` narrows for user
scoping (storeys, sections, groups, ids).

**Rules are named switches, not a DSL.**  A rule set is a `list[str]` of rule
names — a rule is "on" when its name is in the list, off otherwise.  The
name → predicate mapping lives in a module-level dict (the `SHAPE_NAMES`
pattern), so it is data, switchable, and trivially JSON-serialisable — no
per-rule parameter specs, no registry machinery, no verb.

| Rule | Effect |
|---|---|
| `member_ends` | the member's two endpoints are candidates (joints, supports) |
| `framing_points` | interior nodes with `member_degree ≥ 2` are candidates (another member frames in) |
| `exclude_braces` | consume `imk_member_types` / `frame_element_types` — drop axial-only members |
| `honour_releases` | consume `FrameRelease` — drop a pinned (`M3`-released) end |
| `include_leaning_columns` | gravity / leaning columns get hinges (omit to keep them elastic) |

Composition: only `member_ends` and `framing_points` *add* candidates (their
union); the other three *veto* — at three different granularities (whole-member
for braces and leaning columns, per-end for releases).  A formal detector/filter
abstraction is more machinery than five rules justify.  The member-type rules
must **consume** the classification already in place (`imk_member_types` from
(a), `frame_element_types`) rather than re-derive it — one spelling of "is this
a brace" (anti-pattern 17).

**The core test.**  `member_degree` = the number of distinct top-level
`parent_id` identities incident to a node.  A node is a candidate unless
`member_degree == 1` *and* interior to its member (a pure discretization node);
"endpoint" and "framing point" both fall out of this one count.  The classified
points then **cut each member into segments**, stored as first-class records
(Option C — the segment is what the builder emits, so there is no parallel
point/axial list to keep aligned):

```python
@dataclass(frozen=True)
class FrameSegment:
    node_i: str                      # start hinge-eligible node
    node_j: str                      # end hinge-eligible node
    sub_element_ids: tuple[str, ...] # frozen chain of elastic sub-elements
    axial: float = 0.0               # raw column gravity axial P_g (0.0 = none)
    length: float = 0.0              # straight-line length (== sum of sub-elements)

frame_segments: dict[str, list[FrameSegment]]  # on MeshModel
```

Section stays member-level (one original element = one section), so it remains
in `frame_assignments`; the hinge-eligible points stay derivable as segment
boundaries for audit.

**Wiring (open).**

1. A segment-cutting helper in `model/` (OpenSees-free, beside `Selection` /
   `geometry_frames`): classify hinge-eligible points via the rule map, then cut
   segments and attach `sub_element_ids` / `length` / `axial`.
   ``selection=None`` (the default) means all frame members.
2. The Preprocessor calls it after splitting + shell meshing and stores
   `frame_segments` on the `MeshModel` — alongside `frame_element_types` /
   `panel_zones` (§3.2).
3. `enrich_hinge(mesh, backbone=…)` computes the per-segment parameters at build
   time (IMK first; trilinear/bilinear later); `_create_lumped_hinges` consumes
   `frame_segments` and emits **one spring per hinge-eligible point**, never
   re-deriving eligibility (anti-pattern 17).  A verb is deferred until a
   GUI/recipe action needs to *set* the rules — it is not needed to build the
   domain.

**A route, not a model enrichment.**  The parameters are computed at build time
because they depend on the final segment length, which is only fixed after
splitting *and* shell meshing (2D areas mesh together with the frames, and
frames split at shell nodes).  So `backbone="imk"` is one build-time route
beside elastic/modal, fiber and the other hinge backbones; the model and the
`MeshModel` stay route-agnostic, and `enrich_imk` becomes
`enrich_hinge(backbone=…)`, invoked by the builder, not a recipe step.
`frame_segments` is computed regardless (route-agnostic topology); *assigning*
hinges is the route — a model with no hinges simply never selects
`hinge_model="lumped"`, and elastic/modal/fiber don't consume the segments.

**Where chained analyses fit.**  Three kinds of chaining already have homes:
analysis-sequence (gravity → transient, modal → Rayleigh damping) stays inside
the runner; pipeline (modal → response spectrum) is the recipe `kind`/`needs`
chain; property-derivation (static solve → column axial → calibration) is a
`static` step whose `CASES` feed a `derive_column_axial` step that re-stamps
`frame_segments`.  The IMK calibration itself is *not* a step — it reads the
segments and computes parameters inside the analysis verb's `backbone` route.
*(The solved-case `derive_column_axial` verb is deferred; the tributary axial
ships with the segmentation.)*

**Generality, honestly.**  What generalizes is the *pattern* — a `Selection`
scope, a switchable name list, and a per-member segment list — not the hinge
rule names.  Panel zones, rigid links and wall interfaces are already computed
by the Preprocessor and stored as their own `MeshModel` fields (`panel_zones`,
`offset_rigid_links`, `wall_elements`); re-expressing them through a generic
point engine would be a second spelling of existing logic, not a shared one.
The reusable piece is the signature, not a universal rule vocabulary.

## Decisions (proposed order)

1. ✅ Run the analytical classifier first — geometry signal
   (`frame_element_types`) + section-type signal, as in
   `element_classification.md` — extending it only if needed to express the
   three study types (moment / brace / fixed-pin) via section + geometry +
   releases.  *(Landed: `_analytical_member_type` mirrors the geometry +
   section + release signals.)*
2. ✅ Transcribe the `element_tbl` type column as a study override table
   (`BEAM_TYPES_3/9/20`), applied in the same spirit as `brace_sections` /
   `brace_selection` (authoritative).  *(Landed.)*
3. ✅ Reconcile in a helper in `benchmarks/sac.py` that wraps the general
   classifier: `warnings.warn(<f-string>, UserWarning)` on every mismatch
   (override wins), asserted with `pytest.warns(UserWarning, match=…)` — resolves
   (a) and (d).  *(Landed: `reconcile_member_types` records
   `md.imk_member_types` / `md.imk_member_type_mismatches`; `enrich_imk` skips
   braces.)*
4. ✅ Extend the axial ratio to per-member (b), keeping the base-section value as
   the reported summary.  *(Landed: `derive_column_axial_ratios_by_member`.)*
5. Attach IMK hinges at hinge-eligible points (member ends + framing points),
   **one spring per point** (not two back-to-back), and keep the interior
   elastic (c).  *(Design refined 2026-10-28; open.)*
6. Express hinge-eligibility as a switchable rule list applied to a `Selection`
   (`member_ends` / `framing_points` add candidates; `exclude_braces` /
   `honour_releases` / `include_leaning_columns` veto), consuming the existing
   `imk_member_types` / `frame_element_types` / release classification rather
   than re-deriving it.  *(Designed 2026-10-28, simplified; open.)*
7. Add a **residual-stiffness floor** to the hinge backbones (a small positive
   tangent on the residual branch) and **clean collapse detection** in the IDA
   driver, so a fully-degraded hinge runs to the collapse criterion instead of a
   premature local singularity — the one-spring-per-point rule (5) removes the
   *spurious* mode; this handles the *genuine* plastic mechanism.  *(Designed
   2026-10-28; open.)*
8. Store the classification as **segments, not points** (`frame_segments`, each
   a `FrameSegment` with its sub-element chain, length and axial — Option C), and
   compute the IMK parameters **at build time** via `enrich_hinge(backbone=…)`
   rather than stamping `imk_params` on the model (a route, not an enrichment;
   `enrich_imk` is removed).  *(Designed 2026-10-28; open.)*
9. Store the **raw** column gravity axial `P_g` on `FrameSegment.axial`
   (convention-free; the IMK `Pg/Pye` ratio is derived at build time via
   `enrich_hinge`), converging with the existing `_stack_gravity_axial` (Elwood).
   **Warn on transfer columns** (a column bottom on a beam, no column below)
   rather than modelling load transfer; the solved-case `derive_column_axial`
   verb is the fallback.  *(Designed 2026-10-28; open.)*
