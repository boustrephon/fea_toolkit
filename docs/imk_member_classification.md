---
title: "IMK Member Role & Type — Design Note"
description: "How member role (column/beam) and member type (moment/brace/fixed-pin) are identified for the per-member IMK (IMKBilin) calibration, reconciling benchmark-matching with the general element-classification path."
status: "partial"
tags: [imk, ibarra-krawinkler, lignos-krawinkler, deterioration, hinge, element-classification, member-classification, benchmark, sac]
category: [planning]
related: [imk_deterioration_model.md, element_classification.md, benchmark_roadmap.md]
---

# IMK Member Role & Type — Design Note

⚠️ Partial — steps 1–3 landed (2026-10-28): the `element_tbl` type column is
transcribed (`BEAM_TYPES`), `reconcile_member_types` applies the study override
with a `UserWarning` on mismatch, and `enrich_imk` skips the study braces (the
3-story link bay and the 20-story 1st-basement beams).  Items (b) (per-member
axial ratio) and (c) (splitting hazard) remain open.

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

**Proposal — `Pg/Pye` per column *segment*, not per base section.**

The benchmark's axial ratio decreases up the building (each column segment
carries only the tributary mass *above* it).  Today
`derive_column_axial_ratios` returns the governing **base** section and every
upper column falls back to `axial = 0` (pure flexure).  Replace with a
per-member ratio:

* `Pg = g · Σ(tributary mass above the segment's base)`,
* `Pye = A_physical · Ry · Fy` (the segment's own physical section).

The base section keeps the reported governing (maximum) ratio; the Lignos-2019
branch splits at `Pg/Pye = 0.20` and `0.35` then apply per member.  This is both
benchmark-matching (each column segment has its own ratio) and general (it works
for any column stack, spliced or not).

## (c) Where the per-member parameters live

**Current:** `elem.imk_params` rides on the `FrameElement`; it survives
preprocessing only because the SAC model is one-element-per-member (no
splitting).

**Proposal — treat the IMK hinge as a member-*end* property and keep it on the
member.**

The IMK hinge is a zero-length spring at the member's joints; a member split for
interior integration must **not** multiply hinges.  So for the benchmark
(one element per member) the current `elem.imk_params` is correct and stays.  For
general models, the rule is: calibrate per **member** (between joints), and
forbid interior splitting for IMK-calibrated members — the `dataclasses.replace`
used by the preprocessor silently drops `imk_params` on split children, so that
combination must be rejected (or the params recomputed on the frozen
`MeshModel`) before any general use.

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
4. Extend the axial ratio to per-member (b), keeping the base-section value as
   the reported summary.
5. Keep `elem.imk_params` on the member for the benchmark; document/reject the
   splitting hazard (c) rather than solve it now.
