---
title: "IMK Member Role & Type — Design Note"
description: "How member role (column/beam) and member type (moment/brace/fixed-pin) are identified for the per-member IMK (IMKBilin) calibration, reconciling benchmark-matching with the general element-classification path."
status: "draft"
tags: [imk, ibarra-krawinkler, lignos-krawinkler, deterioration, hinge, element-classification, member-classification, benchmark, sac]
category: [planning]
related: [imk_deterioration_model.md, element_classification.md, benchmark_roadmap.md]
---

# IMK Member Role & Type — Design Note

🚧 Draft — proposals only; no code has changed.

## Goals and the tension

Two goals govern this work, and they do not fully align:

1. **Match the benchmark** — reproduce the member classification and analysis
   choices made in the original study (Ohtori et al. 2004; Ibarra–Krawinkler
   2005; Lignos 2008) so our numbers are comparable to the published ones.
2. **Be general** — identify role and member type programmatically, for arbitrary
   models, not just the SAC frames.

The ideal is a **single programmatic rule that happens to reproduce the
benchmark's choices**. Where the two diverge, the benchmark's own data wins for
the benchmark models, and the general rule is *validated against* that data (a
test asserting agreement) rather than left as an un-checked heuristic.

## Background

* The benchmark's `element_tbl` carries a **member-type flag** in its fourth
  column — `1` = moment (concentrated plasticity), `3` = brace (axial-only),
  `5` = fixed-pin — which `sac_member_data.py` does **not** transcribe today
  (the header notes "not transcribed here").  The exact codes must be verified
  against `struct#.m` when transcribing.
* The toolkit already has a **general classifier**:
  `Preprocessor._classify_element_type` → `MeshModel.frame_element_types`
  (`beam`/`column`/`brace`/…), from section type + geometry (see
  `element_classification.md`).
* `enrich_imk` currently uses a **benchmark-specific orientation heuristic**
  (`_frame_element_roles`: vertical → column, horizontal → beam) to choose the
  column-vs-beam equation, and models the brace/pin members as flexural beams
  (documented approximations).  Per-member length was landed in Step 3; this
  note covers the *role/type* layer on top of it.

## (a) Role & type derivation

**Proposal — transcribe the `element_tbl` type column and act on it.**

`COLUMN_MATERIALS` / `BEAM_MATERIALS` already give the role implicitly (columns
and beams are separate tables), but not the type.  Transcribe the type column as
`MEMBER_TYPES_3/9/20` (or fold it into the `element_tbl` transcription), and let
`enrich_imk` consume it:

| Type | Treatment for the IMK model |
|---|---|
| moment (1) | IMK hinge at both member ends |
| brace (3) | axial-only — **no** flexural IMK hinge |
| fixed-pin (5) | IMK hinge at the moment end only; the released end has none |

The general path is the existing `frame_element_types` classifier.  The
reconciliation is: use the **transcribed type for the SAC models** (benchmark
matching), and add a **test that the general classifier reproduces the
transcribed type** for every SAC member — so the "universal" rule is checked
against the "benchmark" data rather than trusted.

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

These fold into (a): once the type column is transcribed, `enrich_imk` stops
placing a collapse-level moment hinge on a brace or a pin end.  The 9-story
pin additionally needs the lumped-hinge path to honour the release (or a
gravity-bay treatment) — tracked separately from the type column.

## Decisions (proposed order)

1. Transcribe the `element_tbl` member-type column; make `enrich_imk` act on it
   (moment / brace / fixed-pin) — resolves (a) and (d) together.
2. Add a test that `frame_element_types` (the general classifier) reproduces the
   transcribed SAC types — the "universal rule matches the benchmark" check.
3. Extend the axial ratio to per-member (b), keeping the base-section value as
   the reported summary.
4. Keep `elem.imk_params` on the member for the benchmark; document/reject the
   splitting hazard (c) rather than solve it now.
