---
title: "Pending Work Log"
description: "Internal log of *pending* work items across the fea_toolkit (refactors, physics, features, housekeeping); completed work is archived in history/_done_log.md."
status: "draft"
tags: [planning, work-log, internal]
category: [planning]
---
# Pending work — fea_toolkit (2026-11-26)

## Strategy — the next steps

> This log is the single source of truth for pending work; this section is the
> *sequencing layer* over it (maintained 2026-11-26).  The standalone plan
> documents are historical records — `analysis_builder_migration_plan.md`,
> `csm_test_model_plan.md`, `deprecation_plan.md` and `linting_fix_plan.md`
> are ✅ complete and tagged `category: [archive]` (P9's Phase 3 triage resolved 2026-10-11 — baseline renewed,
> the remaining diagnostics accepted as known-benign per `.clinerules` §11).
> The workflow/GUI front (P31, P35, P46) is complete; P49 (link elements)
> landed 2026-11-22.

**Near term — finish the workflow layer.**

1. ~~**P32** — `generate_report` composes the registry (keeping its NPZ/HDF5
   storage and HTML-rendering stages, which are *not* verbs) and the greyed
   `Analysis ▸ Modal / Response spectrum / Pushover` entries become
   presets~~ ✅ **Landed 2026-10-02** — the `pushover` verb declares
   `gravity_patterns`, `brace_type`, `brace_sections` and `return_builders`;
   `generate_report` composes the modal / RS / pushover verbs (its storage and
   rendering stages stay report-only); and the `Analysis ▸ Modal / Response
   spectrum / Pushover` entries are recipe presets.
2. ~~**P30 charts** — the remaining `chart` plots (`storey_forces`, pushover,
   modal, CSM), consuming P32's results~~ ✅ **Landed 2026-10-11.**  All four
   `chart` kinds now draw from a preceding step's results: `storey_forces`
   (sums a case's frame end forces into global per-level shear/moment via
   `sum_storey_forces`), `pushover`, `modal` (reusing
   `io.report.modal_participation_df`) and `csm`.  The earlier P19 flag proved
   non-blocking — `sum_storey_forces`' `cut` mode already provided the member
   force transport.
3. ~~**P36** — shell (area) result display (repository seam → per-case
   collection → renderer → GUI toggle)~~ ✅ **Landed 2026-10-12** — see the
   P36 entry below.
4. ~~**P35** — named selections first, then the `open`/`close`/`save`/`select`
   document verbs.~~ ✅ **Landed 2026-11-14** — `NamedSelection` +
   `Selection.selections` (round-trips through `to_string`/`from_string`), the
   `select` / `open` / `close` / `save` verbs (``SESSION`` kind + ``StepContext``
   document state + ``next_version_path``), the **Selections** tree group and
   the `Model ▸ Name selection…` action (see the P35 entry below).
   ~~**P31** (command palette) is independent input ergonomics
   and can run in parallel with any of the above.~~ ✅ **Landed 2026-11-14** —
   the command grammar (`compile_command` / `step_to_command`), `satisfied_kinds`,
   the `Ctrl+K` palette, the command echo and `Recipe.from_python` (see the P31
   entry below).

**Next — physics / feature gaps (re-ranked 2026-11-26).**  The workflow/GUI
front is complete: the near-term workflow-layer items, the six-item GUI
follow-up batch and **P31 / P35 / P46** all landed, and **P49 — link-element
support — landed 2026-11-22** ahead of the physics tranche (GitHub issue #3:
parsing, per-DOF emitters, bearing elements, the `check_hysteretic_backbone` +
`isolate_base` verbs, and the E-Defense LRB-CLB / TPB + Morgan & Mahin FP
benchmarks — only its *deferred limitations* remain, see the P49 entry).  The
remaining physics/feature tranche, in order: **P19** (member-interior force
stations), **P6b / P39** (SD-section fidelity), **P7** (Python-native NLD),
then **P23 / P25** (GUI polish + classic support symbols).  **P43** (the
schema-v4 transient block) and **P42b** (the IK-2005 collapse investigation)
are now closed out — P42b leaves *collapse-capacity calibration* as the open
research question.  **P50** and **P25** each need a decision before
implementation.

**Opportunistic — Tier 4.**  ~~P9 (linting Phase 3 triage)~~ ✅ **resolved
2026-10-11** — fresh `npx pyright src/` baseline recorded, no new real bugs,
the remainder accepted as known-benign; only the `pd.Series.to_numpy()`
convention is left tracked (non-blocking).

**Guardrails that shape the work.**  Every verb delegates to the two-stage
pipeline (a thin wrapper, never a reimplementation); `Selection` stays the
scoping primitive (P35's `selections` field must round-trip through
`to_string()`/`from_string()`); `generate_report`'s public signature stays
unchanged through P32; heavy imports stay inside the verb functions so
`import fea_toolkit.workflow` never loads OpenSees.

### IDA promotion — follow-ups (2026-11-14)

The generic `analysis/ida.py` driver (promoted from `benchmarks/`, the
"IDA before P31/P35" tranche) carried two known follow-ups; both landed
2026-11-14:

1. ~~**Roof-drift proxy vs per-storey `d_max`.**~~ ✅ **Landed** — `run_ida`
   now takes a `stories` list (resolved by the `ida` verb via
   `identify_stories(model_data)`); with ≥ 2 levels the drift is the peak
   interstorey-drift ratio (`model/storey_response.peak_interstorey_drift`),
   otherwise the roof proxy, and the result records `drift_measure` /
   `storey_method` / `storey_confidence` / `n_storeys`.
2. ~~**NPZ serialisation of the generic IDA result.**~~ ✅ **Landed** —
   `npz_writer.collect_single_ida_arrays` writes a single-record result under
   the schema-v5 `ida/<record>/…` block, dispatched from `write_results_npz` /
   `write_results` / `AnalysisBuilder.export_results`, and the `ida` verb gains
   an `export_path` param.
3. ~~**Record ``Sa(T1)/g`` intensity measure.**~~ ✅ **Landed (2026-11-14)** —
   `run_ida` now computes the record's 5 %-damped ``Sa(T1)/g`` once from the
   unscaled record at the modal ``T1`` (the SDOF integrator promoted from the
   benchmark to ``fea_toolkit.spectrum.sdof_spectral_acceleration``), reports
   ``T1`` / ``Sa_T1`` / ``Sa_T1_g`` / ``collapse_Sa_T1_g`` / ``sa_method``
   (scale-as-IM fallback when no period), and ``plot_ida_curve`` / the
   ``chart`` verb's ``ida`` choice draw the curve on the physical ``Sa(T1)/g``
   axis — the GUI displays it through the existing figure view.
4. ~~**Collapse-rung transient block (full standalone plotting).**~~ ✅
   **Landed (2026-11-14)** — `run_ida` retains the full time-history response
   of the first rung that collapsed (or the highest rung when nothing
   collapsed) as its ``transient`` / ``transient_scale`` fields, and
   ``npz_writer.collect_ida_transient_arrays`` writes it under a
   ``transient/ida_<record>/…`` block (``analysis_types`` then carries both
   ``"ida"`` and ``"transient"``), so a standalone plotter can render the
   response at the extreme intensity — beyond the scalar drift ladder.

**Known limitation (multi-tower ETABS).**  A multi-tower ETABS model shares one
flat `STORY DATA` table whose levels sit at independent per-tower elevations;
`identify_stories` cannot associate a level with its tower, so levels from
different towers are merged and per-tower drift is not computed.  The detector
now warns on duplicate story names/elevations, and the guidance is documented
in `docs/modeling_guidance.md` §9 — split the towers into separate models for
per-tower results (see also the CSI *Edit > Towers, Stories and Grid Systems*
help and the ETABS Modelling FAQ).

**Not yet done (follow-up).**  When `identify_stories` resolved per-storey
`StoryLevel.node_ids` (the `area_elements` / `node_clustering` strategies),
`run_ida` could map those node IDs to node tags and use that membership for the
interstorey-drift reduction instead of re-clustering nodes by the default
elevation band in `peak_interstorey_drift` — today only the `.elevation` is
read, so a detected membership is discarded.

### GUI/workflow follow-ups — the nine-item batch (2026-10-12)

A review of the workflow/GUI layer produced a nine-item follow-up list.
Status as of 2026-10-12:

| # | Item | Status |
|---|---|---|
| 1 | RS frame + shell forces (record flags, recorder extraction, `rs/shell_*` block) | ✅ Landed 2026-10-12 |
| 2 | Load/reaction summary table (static sums, RS base shear, summed reactions) | ✅ Landed 2026-10-12 |
| 3 | Shell deformation — static / RS / modal (pushover shell animation deferred) | ✅ Landed 2026-10-12 |
| 4 | `results.animate` toggle on the view toolbar | ✅ Landed 2026-10-12 |
| 5 | Pushover profiling (part a) | ✅ Landed 2026-10-12 (measured — no change warranted) |
| 6 + 7 | Recipe run-state markers (`Step.enabled` / `Step.status`) | ✅ Landed 2026-10-12 |
| 8 | Document benign GUI console warnings (VTK / macOS) | ✅ Landed 2026-10-12 |
| 9 | Named modal analyses + RS/pushover `modal_case` auto-resolution | ✅ Landed 2026-10-12 |

**Item 1 detail (RS forces).**  The `response_spectrum` verb gained
`record_frame_forces` / `record_shell_forces` (default `True`);
`analysis/rs.py` wires `extract_element_rs_forces(extraction="recorder")` and
the new `AnalysisBuilder.extract_rs_shell_forces()` into the result's `data`
(`element_forces` / `shell_forces`), each best-effort; and the `rs/shell_*`
block (self-indexed by `rs/shell_sap_id`, combined with the rule named by
`rs/shell_combination`) joins `RS_ARRAYS` and the `write_results` /
`write_model_stages` writers via `collect_rs_shell_force_arrays`.  The GUI
project bundle threads both blocks through `collect_project_results`.

**Upcoming items — detailed plan (2026-10-12).**

**Item 2 detail (load/reaction summary).**  The table is driven from the solve
itself: `run_static_cases` gained a `summaries_out` side-channel capturing one
`{analysis, applied, summed}` entry per solved case (`applied` = the case's
factored applied-load totals summed over its patterns; `summed` = its summed
base reactions with overturning via `sum_reactions_with_overturning`), and
`run_case_set` threads those entries through its `"summaries"` return.
`analysis_load_reaction_summary` was split into the pure
`render_load_reaction_rows(md, static_summaries, rs_results, total_weight,
seismic_coefficient)` formatter plus the existing re-solving wrapper (which
delegates to it), and the `load_verification` verb now reads the solved
summaries from `context.results["static_summaries"]` plus
`context.results["rs"]` and emits the `Table`, falling back to the re-solving
wrapper only when no preceding `run_static` result exists.  The GUI's
`Analysis ▸ Run` path registers the same table by running a
`[run_static, load_verification]` recipe — one spelling, per anti-pattern 17
(the old `_show_load_reaction_summary` duplicate was removed).

**Item 3 — shell deformation (static / RS / modal).**  Modal shell deformation
already worked (`npz_to_pyvista_modal_mesh` / `plot_mode_animation`), and the NPZ
static path already supported `npz_to_pyvista_shell_mesh(deformed_case=…)`; the
gap was the **interactive `ModelViewer` overlay** the GUI drives, which drew
displaced frame centrelines only.  Landed 2026-10-12:
`ShellGeom` carries the SAP node IDs of its vertices (`node_ids`), populated by
`ModelViewer._extract_geometry` from `AreaElement.node_ids`; the
`RenderBackend.render_deformed` contract and its PyVista implementation take an
optional `shells` list and displace each shell face by the displacement of the
nodes it sits on (same `_polygon_cells` face-building as `render_shells`, so a
warped quad stays a quad); and `ModelViewer.overlay_deformed` passes the
extracted shells through — so static, RS and modal steps deform shells alongside
frames, and the `animate` toggle (which re-calls `overlay_deformed` each tick)
oscillates them too.  `_auto_amplification` now measures a shell-only model from
its own vertices instead of falling back to 1:1.  Tests:
`tests/test_renderers_pyvista.py::TestRenderDeformedShells` (per-vertex shift,
no-node-id fallback, replace-not-stack) and
`tests/test_gui_results.py::TestShellDeformation` (a slab with no frames deforms
its shell through the real action, auto-scaled).  Pushover shell animation is
**deferred**.

**Item 5(a) — pushover profiling.**  Measured 2026-10-12, no runner change.  A
mid-size elastic model (84 frames + 16 shells, 45 nodes, 30 steps) was profiled
with `cProfile` plus a wall-clock A/B (recording off / node-disp-only /
everything-on).  The hypothesis — that `_record_step`'s `ops.eleResponse` /
`ops.nodeDisp` is the hotspot — is **refuted**: `ops.analyze` (the solver) is
~54 % of the profile; `_record_step` is ~9 % (and `eleResponse`/`nodeDisp`
individually ~1.6 µs / ~0.7 µs).  Turning the force blocks on costs ~20 ms
(~25 %) at this size; node-disp-only (the verb default) is within noise of off.
Full write-up and the 5(b) conclusion (parallelization/thinning **not**
warranted — `pushover_step_stride` and the per-block flags are the answer):
`docs/dev_notes.md` → *Pushover profiling*.

## PENDING (active — not yet done)

> Priority-ordered register of **open** work (maintained 2026-11-26).  Every
> item is cross-referenced to its source document.  Completed work — the
> Tier 1–2 physics items, the Tier 3 workflow/GUI batch (P20–P38), and the
> deferred-housekeeping closures — is recorded in the
> [`history/_done_log.md`](history/_done_log.md) archive, so
> this list shows only what remains.  **P41** and **P48** landed and are fully
> relocated to the archive; **P42** and **P49** landed and keep only their
> *remaining* stubs below (P42's `enrich_hinge` route; P49's deferred
> limitations + Iwanuma follow-up) — their completed parts are archived too.

### Tier 3 — Feature gaps (placeholders / partial)

#### P42 — IMK calibration equations + A2/A3 (Krawinkler collapse)

Source: `docs/benchmark_roadmap.md` §3 (canonical); `docs/imk_deterioration_model.md`.

> **Status: ✅ Landed (2026-10-30)** — A0 (the Lignos–Krawinkler 2011 beam and
> Lignos et al. 2019 column calibration equations), A1 (the
> `hinge_backbone="imk"` `IMKBilin` material), A2 (single-member cyclic M-θ
> checks) and A3 (the IDA driver + collapse detection + bisection + the
> `Sa(T1)` collapse capacity) all landed and are archived in
> [`history/_done_log.md`](history/_done_log.md).  The **only remaining item**
> is the `enrich_hinge` build-time route (named at the end of this entry).

**Status (2026-10-27).** A1 machinery landed — `hinge_backbone="imk"` builds an
`IMKBilin` steel hinge (energy-based cyclic deterioration) in `opensees/_elements.py`.
A0 landed — beams (Lignos–Krawinkler 2011 θp/θpc/Λ) and columns (Lignos et al.
2019 — the monotonic-backbone parameters `My*`, `a`, `θp`, `θpc`, `Mr` +
cyclic-deterioration `Λ` Eq. 13, 2026-10-28).
**Remaining:**
1. ✅ Column cyclic-deterioration Λ — landed (2026-10-28): transcribed Lignos
   et al. (2019) Eq. 13 (`Λs` basic-strength deterioration, split at
   `Pg/Pye = 0.35`, capped at 3.0 rad; `Λc = Λk = 0.9·Λs`).  (Previously
   mis-attributed to Lignos & Krawinkler 2011 Table 3, which is beams-only.)
2. A3 frame collapse (SAC 3/9/20 with IMK; IDA vs Ibarra–Krawinkler 2005) —
   first increment landed (`enrich_imk` + 3-story `backbone="imk"`); the
   column axial ratio, NLTHA convergence, the 9/20-story matrices, the member
   role/type reconcile and the per-member axial ratio all landed 2026-10-28.
   ~~Remaining sub-items (beam unbraced length, IDA) tracked in
   `docs/imk_deterioration_model.md` §5 / `docs/imk_member_classification.md`.~~
   **IDA driver + collapse detection landed 2026-10-30** —
   `benchmarks/sac_run.py` gained `collapse_intensity` (the pure
   non-convergence / drift-threshold predicate), `_ida_run` (a single-builder
   IM ladder) and `run_ida` (record-based), tested by
   `tests/test_benchmark_sac_ida.py`.  **Bisection + `Sa(T1)` collapse
   capacity landed 2026-10-30** — `run_ida(..., tolerance=0.01)` bisects the
   first collapse intensity to a 1 % relative tolerance, and
   `sdof_spectral_acceleration` / `collapse_capacity_sa` report the median
   collapse capacity in `Sa(T1)` terms.  **Resolved 2026-10-30:** beam
   unbraced length (keep `Lb = L`, the bare-steel default) and Λ validation
   (Eq. 13 transcription + cap already unit-tested).  **Remaining:** the
   `enrich_hinge` build-time route (see
   `docs/imk_deterioration_model.md` §5 / `docs/imk_member_classification.md`).
   The residual-stiffness floor was **dropped 2026-10-30** — non-convergence is
   the canonical collapse criterion.

**Where.** `benchmarks/imk_calibration.py`; `opensees/_elements.py`
(`hinge_backbone="imk"`); `tests/test_benchmark_sac_imk.py`.

#### P42b — IK-2005 generic-frame collapse: collapse-capacity calibration (open)

Source: `docs/ik2005_generic_frame_benchmark.md`; `docs/benchmark_roadmap.md` §3.

**Status: ⚠️ Partial — investigation closed 2026-11-26.**  The 40-record ×
12-frame re-run is complete and the paper's median collapse curve is **not
reproducible from the OpenSees side** (a genuine DRAIN-2DX vs OpenSees
discrepancy).  The completed investigation — root-cause analysis, the close-out,
and the modal / pushover / drift-demand validation — is archived in
[`history/_done_log.md`](history/_done_log.md).  **Remaining:** the broader
*collapse-capacity calibration* question (a reliable collapse-capacity model
calibrated against benchmark data) stays open.

#### P25 — classic support symbols on top of the per-DOF glyphs

Source: this register, *DONE — support restraints*; `docs/dev_notes.md` →
*Support symbols*.

**What.**  Restraint display shipped as **per-DOF glyphs**: an arrow per
restrained translation, a curl per restrained rotation.  Per-DOF is correct for
*any* six-flag set.  The staged follow-up, chosen deliberately, is to draw the
textbook symbol for the patterns that have one:

- a hatched "ground" line under a fully **fixed** base;
- a **pin** triangle for `[1, 1, 1, 0, 0, 0]`;
- a **roller** symbol for the single-translation sets;
- the arrows above as the **fallback** for everything else, so no restraint set
  is ever left undrawn.

**Two decisions to settle first** — which is why this is not folded into the
glyph work:

1. *Which plane a symbol is drawn in.*  A ground hatch is unambiguous on paper
   and not in 3D.  SAP resolves it with **joint local axes**, and `Restraint`
   carries no coordinate system — so either draw the symbol in the plane normal to
   the restrained axis (fine for one-DOF sets, arbitrary for a fixed base) or
   default to the global X–Y plane and document that.
2. *Whether the rotation glyphs belong to a "fixed base" symbol* or stay
   separate: one hatch is quieter than three arrows plus three curls, but the DOF
   detail would then live only in the Inspector row.

**Where.**  One geometry builder per symbol in
`plotting/restraint_glyphs.py`, beside the arrows; the pattern table and its
names in `model/supports.py`, so the glyph and the Inspector label cannot
disagree about what a set is called.

#### P23 — GUI Milestone 4 remainder: interaction modes, element labels, macOS name
Source: `docs/gui_roadmap.md` milestone rows 3–4 and design rule 7;
`docs/dev_notes.md` ("macOS application-menu label", "PyVista picking contract",
"Mouse interaction: one policy, many mouse habits").

**What.** ✅ **Milestone 4 landed in both directions** — tree→viewport highlight,
viewport→tree select-and-scroll (`SelectionIndex` + the cell index) — and mouse
interaction became configurable: an `InteractionPolicy` (presets over knobs), the
click-versus-drag gesture, a tighter picking region, node priority and the
settings file.  The explicit Select / Orbit modes and modeless multi-select
(Shift/Ctrl-click add/toggle, a marquee drag) landed 2026-09-30 with the
multi-selection work (see [`history/_done_log.md`](history/_done_log.md)).  Two follow-ups remain:

1. **element-label display toggle** — `view.show_labels` is still greyed
   because nothing renders node/element labels; it needs a `labels` category
   in `PyVistaRenderer` before the toggle can exist.
2. **macOS application name** — the Application menu's `About` / `Hide` /
   `Quit` *items* are retitled at launch through AppKit
   (`app.rename_macos_application_menu`, now a declared macOS dependency), but
   the **bold menu-bar / Dock name stays `Python`**: it comes from the
   framework bundle the interpreter is built into, and survives Qt's names,
   AppKit retitles, `NSProcessInfo.processName` and two flavours of generated
   `.app` bundle (probes in `docs/dev_notes.md`).  A self-contained bundle that
   ships no OpenSees code is the route — recorded as the future option in
   **P22** — and `python -m fea_toolkit.gui` was added as the script-friendly
   entry point in the meantime.

#### P6 — Section fiber patches (P6a done — Channel/Angle/DoubleAngle/Tee; P6b deferred)
Source: repo-root `README.md` §5 "Section Types and Properties" table.

**What.** `to_fiber_patches()` is implemented for `ChannelSection`,
`AngleSection`, `DoubleAngleSection`, and `TeeSection` (P6a, 2026-08-25 -
centroid-shifted `patch('rect')` decompositions + per-shape tests); still
`🚧 Placeholder` for `SDSection` (needs polygon meshing); `EncasedSection`
(embedded section + concrete encasement) is implemented via the role-based
fiber contract - see P6b below.
"Frame Member Types (RC)" is `⚠️ Partial` in the README - materials, RC
section shapes, rebar auto-placement and Mander confinement are wired; the
remaining gaps are the P6b shape patches plus benchmark validation (see P5).

**Done (P6a, 2026-08-25).**
1. Channel / Angle / DoubleAngle / Tee decomposed into disjoint
   `patch('rect')` sub-regions mirroring the `ISection` (3-rect) /
   `BoxSection` (4-rect) pattern, re-centred on the elastic centroid via
   the shared `_rect_patches_from_regions()` helper so the fibre section
   has vanishing first moments (sum A*ybar = sum A*zbar = 0).
2. Per-shape tests in `tests/test_model.py`
   (`TestChannelSectionFiberPatches`, `TestAngleSectionFiberPatches`,
   `TestDoubleAngleSectionFiberPatches`, `TestTeeSectionFiberPatches`):
   patch count/type, `mat_tag` + `nfy`/`nfz` propagation, total area ≈
   section `A`, first moments ≈ 0.
3. README §5 table status flipped 🚧 → ✅ for the four shapes.

**Deferred (P6b).**
1. `SDSection`: mesh the multi-material `polygons` into fibre patches
   (arbitrary-polygon meshing) - blocked: the parser constructs
   `SDSection(**common)` with empty `polygons` (no `SECTION DESIGNER`
   table parsing), so only caller-provided polygons would be actionable.
2. `EncasedSection`: `to_fiber_patches()` is **implemented** via the
   role-based fiber contract (encasement rectangle + overlaid embedded-steel
   patches, steel counted twice — a documented simplification).  Remaining:
   no parser path populates the encasement dimensions (`encasement_depth` /
   `encasement_bf`), so it is inert on real files; see
   `docs/e2k_import_plan.md` §7.
3. `ConcreteFilledTubeSection` / `ConcreteFilledPipeSection` (CFT) fibre cores
   are modelled **unconfined**; the CFT confinement model is deferred — the
   decision and researched recommendation are recorded in
   `docs/cft_confinement_plan.md`.

**Constraints.** Section geometric properties stay as-authored (from the
S2K text); patches must integrate with both `AnalysisBuilder` fiber sections
and Tcl export (`export_model_to_tcl`).

#### P39 — Section tools: St. Venant J for arbitrary polygons (SD-section fidelity)

Source: `docs/sd_sections.md` (import and roadmap); placeholder in
`src/fea_toolkit/model/section_tools.py`.

**What.**  SD sections now import with gross elastic `A`/`I33`/`I22` from
`polygon_props()`, but `J` is the **polar second moment** `I33 + I22` — exact
for a circle, only an upper bound for an arbitrary solid section (it over-counts
the warping restraint of a non-circular cross-section).  The true St. Venant
torsional constant needs a section-tools computation.  A `st_venant_j()`
placeholder module (`section_tools.py`) marks the home for this work and raises
`NotImplementedError` so no caller can silently get the polar-moment value from
it.

**Planned toolkit** (each is an independent follow-up):
1. **`st_venant_j(vertices, thickness=…)`** — thin-wall `Σ b·t³/3` / Bredt
   `4·Am² / ∮ ds/t` for thin-walled shapes; a Prandtl / finite-difference /
   boundary-element solve for solid sections, generalising `rect_props()`'s
   rectangular-bar series.  Then wire `polygon_props()` to call it (replacing
   the `J = I33 + I22` line).
2. **Section moduli `Z33`/`Z22`** from extreme fibres, once the SD local-axis
   orientation is pinned down.
3. **`SDSection.to_fiber_patches()`** — triangulate the polygon into concrete
   patches plus rebar from the `REF LINE`/reinforcement records (unblocks P6b
   item 1; the SD polygon parsing that P6b flagged as missing is now
   implemented).
4. **Centroid/shear-centre offsets** (`cg_offset_2/3`, `ecc_v2/v3`) once the
   reference-point convention is confirmed.

**Resolved / remaining (same doc).**  The SD frame-section axis mapping
(`X` → local 2, `Y` → local 3) is now applied and confirmed against a real
ETABS export, so `I33`/`I22` are no longer swapped.  Still open: the
wall-pier (vs frame) local-axis default, and whether a non-90 `ANGLE` is
already baked into the exported `POLYCORNER` coordinates.  Each follow-up
above should be validated against such an export (a small model with
hand-computable SD sections, at `ANGLE 0` and a known rotation).

**Where.**  `src/fea_toolkit/model/section_tools.py` (pure geometry, no
OpenSees); wire `st_venant_j` into `polygon_props()` once item 1 lands.

#### P7 — Python-native nonlinear dynamic (time-history) integration
Source: `docs/nonlinear_dynamic_analysis.md` (frontmatter + Notes);
repo-root `README.md` "TODO / Future Work" section.

**What.** `run_nonlinear_dynamic_analysis()` is complete **via the Tcl
export + Xara/OpenSeesRT path**.  A **Python-native** transient integration
(no Tcl/Xara dependency) remains planned: `Newmark` (γ=0.5, β=0.25) or
`HHT` (α=−0.1) integrator, Rayleigh damping from the preceding modal result,
`Path` time-series + `UniformExcitation` for base excitation,
`loadConst('-time', 0.0)` gravity hand-off, Node/Element recorders.

**Outline steps.**
1. Add a native transient runner alongside the Tcl path with the same public
   API and result keys (`times`, `displacements`, `envelope`,
   `peak_displacement`, `converged_steps`).
2. Brace materials per README recommendation: `Hysteretic` + `Fatigue`
   (`brace_fatigue=True`) for the truss approach; `Steel02` + `Fatigue` if
   the Approach A subdivision ever resolves.
3. Solver fallbacks for dynamics: `KrylovNewton`/`NewtonLineSearch`,
   test tolerance 1e-4–1e-5.

**Validation.** Ground-motion smoke test on `make_rc_frame_3d()` /
`make_sample_model()`; compare `peak_displacement` + envelope against the
Tcl/Xara path on the same record; cover the runner-failure metadata path
(`converged_steps=0` + `metadata["error"]`).

#### P19 — Member-interior force stations (storey profiles are end-force only)

Source: `docs/force_diagram_unification.md` → *Storey profile (2D default)*;
internal benchmark notes.

**What.** Every element-force archive stores **two samples per member** — the
I- and J-end local forces (`eleResponse(tag, "localForces")`, 12 components) —
and **no span-load arrays**.  Two consequences, both measured on the benchmark
model:

1. The 2D storey profile's cut transport assumes force constant / moment linear
   along the member.  For the **2832 frame distributed loads** in that model
   (pipe dead/live, wind, pipe lateral) the internal force varies *between* the
   ends, so the reconstructed interior value is wrong mid-span.
2. "Where does the vertical load enter the structure" is not answerable at all:
   the load is distributed along members, so it has no level of entry — only the
   two integrated end values are visible.  (The `"end"` storey mode's base value
   is the support *reaction*, not arriving load; see the design note.)

The interpolation is exact for an unloaded member and approximate for a loaded
one, with the largest error exactly where the model is most interesting — the
long-span beams carrying pipe load.

**Outline steps.**

1. **Option A (recommended first) — exact reconstruction from end forces +
   span loads.**
   * Store the per-element span loads in the NPZ.  They exist as
     `FrameDistributedLoad` on the mesh model; the archive currently omits them,
     so this is a schema addition + version bump (P18's marker).
   * Add `member_force_at_t(end_forces, span_loads, t)` in `model/` — the
     closed-form internal force/moment at parametric `t` (constant shear + linear
     moment with no load; linear shear + parabolic moment for a uniform load).
     This is the same superposition the element performs internally, so it is
     exact for linear-static and needs no extra OpenSees calls.
   * Point `sum_storey_forces(mode="cut")` and the 2D per-member diagram at it,
     replacing the "force constant, moment linear" transport.
2. **Option B (later, for nonlinear runs) — sample section forces in OpenSees.**
   `eleResponse(tag, "section", secTag, "force")` at `K` Gauss–Lobatto stations,
   stored as `force_stations (n_elements, K, 12)`.  Works for *any* analysis
   (including post-yield), but bloats the archive and is still only as fine as
   `K`.

**Validation.** Simply-supported beam under a uniform load → midspan
`M = wL²/8`; cantilever with a tip load → linear moment; a column under
self-weight → linear axial.  Benchmark re-check: the `DEAD` cut profile must stay
monotonic to 0 at the roof (the current invariant), and `COMB1` interior values
must move toward the beam's true mid-span moment.

**Constraints.** Keep the archive standalone (plotting must not require a model
object); a missing span-load array must degrade to today's
constant-force/linear-moment behaviour rather than raise.

#### P22 — Desktop GUI (Qt/PySide6) design & roadmap
Source: `docs/gui_roadmap.md`.

**Status: ⚠️ Partial — Milestones 1–4 landed (viewport spike, application chrome,
model tree + inspector, bidirectional selection), as have Milestone 5's static
run, Milestone 6's load rendering and Milestone 7's deformed shape and force
diagrams.  **P29 (workflow authoring) landed its Phases A and B on 2026-09-29** —
the Recipe panel and the Model-menu presets — see
`docs/workflow_authoring.md`.  Modal / spectrum / pushover analyses, storey
response and the quad view remain.**

**DONE (2026-10-02) — File ▸ Save model…** writes the preprocessed ``MeshModel``
to a JSON (codec) / NPZ / HDF5 (stage) file, gated on a preprocessed model
existing, and the same operation is available as the **`save_model`** recipe
verb (``path`` + ``fmt``) — both delegate to
``io.stage_writer.write_model_file``, so the menu and a recipe save identically.

**What.** A native, document-centric desktop application wrapping the
package's existing workflow (import `*.s2k` → display geometry and loading →
query objects → run analyses → view results), built around a central 3-D
visualisation screen with the classic CAE chrome — menubar, top toolbar,
right-edge view toolbar, Model Tree, Property Tree, Property Inspector, a
bottom Message Log dock, and a status bar (ASCII layout in
`docs/gui_roadmap.md` §1.1).

**Decision.** Build on **Qt for Python** — **PySide6** as the binding
(accessed via `qtpy`) plus **`pyvistaqt`** to embed the existing PyVista
renderer as a `QtInteractor` central widget, shipped as a new optional
`[gui]` extra (Python 3.10+, driven by `pyvistaqt`; the core stays at 3.9).
PySide6 is the application shell and `pyvistaqt` is the viewport bridge —
complementary layers, not alternatives.  Qt is the only candidate giving
native docking, tree/property widgets and PyVista's official Qt integration;
Tkinter, wxPython, Dear PyGui and PyVista's in-window widgets were rejected
(see `docs/gui_roadmap.md` §3).

**Scope note.** All five workflow steps already have package entry points
(`SAP2000Parser`, `ModelViewer`, the `plot_interactive_viewer` pick callback,
`AnalysisBuilder.run_*()`, the unified `plot_*` / `write_results_npz`).
The **only net-new visualisation capability** is **load rendering** —
`RenderBackend` has no `render_loads()` yet, so a model-layer load-glyph
extractor and a `render_loads()` backend method are required
(`docs/gui_roadmap.md` §4.1).  Three first-class UX requirements are designed
in from the start rather than retrofitted: **lazy tree population**
(`QTreeView` + `QAbstractItemModel`, never `QTreeWidget`), **bidirectional
tree ↔ viewport selection sync**, and **persistent dock layout** (`QSettings`).
Two stack caveats are recorded in `docs/gui_roadmap.md`: the
`PySide6` × `pyvistaqt` version pair, validated in Milestone 1 (§3.3), and batching
viewport geometry into a `MultiBlock` with render-once (§3.4).

**Outline steps.**
1. ✅ Scaffold the `[gui]` extra (`PySide6`, `pyvistaqt`, `qtpy`), verify the
   version pair, + lazy-import stub `src/fea_toolkit/gui/__init__.py`.
2. ✅ Viewport spike: refactor `PyVistaRenderer` to accept an injected plotter,
   add `QtRenderBackend`, give `setCentralWidget` a `QWidget` container
   (quad-view ready), embed `ModelViewer` in a bare `QMainWindow` (batched
   `MultiBlock` render-once deferred to the chrome milestone).
3. ✅ Main-window chrome: menubar, top toolbar, right-edge vertical view toolbar,
   the dock layout (left: tabbed trees over inspector; bottom: message log),
   status bar, axes triad + view cube.  Domain actions are greyed placeholders
   naming their milestone; ``Open`` is wired.
4. ✅ Model Tree (lazy) + Property Inspector driven from `SAPModelData`;
   the inspector populates from the selection's dataclass.  (The Property Tree
   tab and the `AppController` are still pending.)
5. Bidirectional tree ↔ viewport selection sync (`controllers/selection.py`),
   reusing the existing pick data.
6. Import + analysis `QAction`s on a worker thread (`QThread`), with progress
   and results marshalled to the status bar and message log.
7. `render_loads()` on `RenderBackend` + model-layer glyph extractor.
8. Results + export menus reusing the `plot_*` / `write_results_npz` surface.
9. Persistence (`QSettings` geometry + dock state).
10. Headless tests (`QT_QPA_PLATFORM=offscreen`, `ops.wipe()` hygiene, a
    `needs_gui` marker alongside `needs_pyvista`) — the marker, conftest
    probe and a Milestone-1 smoke test landed with steps 1-2; `ops.wipe()`
    hygiene follows with the analysis actions.

**Open decisions.** All resolved — native Qt, PySide6 (via `qtpy`), all three
left-dock panels (Option A), the message log included, the 3.10+ GUI floor,
the `PySide6` × `pyvistaqt` version pin (validated: `PySide6 6.11.2` ×
`pyvistaqt 0.13.1` × `qtpy 2.4.3`) and the first-milestone scope — see
`docs/gui_roadmap.md` §3.3 and §8.

**Future option (unscheduled) — a self-contained bundle that drives OpenSees
externally.**  The caveat in `docs/gui_roadmap.md` §5 — a *distributed*
application that imports OpenSeesPy needs OSU's commercial licence — applies
to **shipping** the software, not to invoking a copy the user installed.  A
py2app / PyInstaller bundle containing **no OpenSees code**, calling a
separately-installed OpenSees interpreter as an external program, is therefore
a viable route; it is also the only way to fix the macOS application name
(`docs/dev_notes.md` → *macOS application-menu label*).  Sketch:

1. an analysis-case Tcl writer for static / modal / spectrum / pushover --
   today `opensees/builder.py` exports the model deck and pushover only;
2. a subprocess runner that locates the external `OpenSees` executable,
   streams its stdout to the message log and the progress bar, and cancels by
   stopping the child process;
3. a recorder-output → NPZ importer, so the viewers, reports and
   `write_results_npz` consumers work unchanged (the GUI already reads NPZ);
4. a capability matrix: model features the Tcl deck cannot express need an
   explicit list, with the existing Tcl material/section export as the parity
   baseline;
5. packaging compliance for what *is* bundled -- Qt's LGPL-3.0 notices and the
   relink obligation, plus signing / notarisation for distribution.

The GUI's analysis actions would pick the external runner when OpenSeesPy is
absent, or by user choice.  Deferred until a packaged app is actually wanted;
the licensing position is recorded in `docs/licence.md`.

#### P31 — Workflow Phase D: the command palette and script interop
Source: `docs/workflow_authoring.md` § *Roadmap*.

**Status: ✅ Landed 2026-11-14** — design in [docs/workflow_palette.md](workflow_palette.md).
The command grammar (`compile_command` / `step_to_command` in
`workflow/command.py`, reusing `Selection.scan_clauses`), `satisfied_kinds` in
`workflow/recipe.py`, the `Ctrl+K` palette (`gui/views/command_palette.py`),
the command echo (presets and the palette log their `step_to_command` form),
and `Recipe.from_python` (the `RECIPE = {…}` literal, no `exec`).

**What.** Authoring ergonomics, independent of P30:

1. **A command palette** (`Ctrl+K`): a command string compiles into a `Step` —
   reusing the existing `Selection.from_string` grammar for the scope, so there
   is **no second selection language** — and is inserted into the recipe.
2. **A command echo.**  Every mouse action appends its equivalent step (the
   Model-menu presets already do this), and the log shows the recipe's JSON
   form, so a session is always replayable and a workflow built by clicking can
   be handed on as data.
3. **Importing a Python script back into a recipe**, where the script's steps are
   expressible as verbs — the inverse of `Recipe.to_python()`.
4. **Precursor-aware completion.**  The palette reads the same `kind` / `needs`
   metadata the recipe panel and the validator use (Decision 0, in
   `docs/workflow_authoring.md` → *Precursors*), so it can grey a step whose
   precursor is missing or offer to insert it — one ordering vocabulary, never a
   second one.

**Why it is separate from P30.** P30 is about *output*; P31 is about *input*.
Neither blocks the other, and a user gains from either alone.

#### P35 — Named selections, and the document-lifecycle verbs (open / close / save)

Source: the 2026-10-02 planning session (post-review); pairs with workflow
Phase E (P32) and the GUI model tree.

**Status: ✅ Landed 2026-11-14.**  `NamedSelection` +
`Selection.selections` / `exclude_selections` (round-tripping through
`to_string` / `from_string`), `resolve_named_selections`, the `select` / `open`
/ `close` / `save` verbs (``SESSION`` kind + ``StepContext`` document state +
``next_version_path``), the **Selections** tree group and `Model ▸ Name
selection…`.  The two follow-ups landed too: (1) **graphical-pick authoring** —
`Name selection…` now offers to save the current viewport / tree selection (a
pick) as the named selection's ids, falling back to the expression prompt when
there is no pick; (2) `resolve_named_selections` is wired into the verb layer
(`run_recipe` resolves a step's `selections` / `exclude_selections` against the
document's store before the verb runs), so a `mesh` / `scale_sections` / check
step that references a named selection now sees concrete ids.

**What.** Two related additions, both agreed:

1. **Named selections.**  Define a selection **by text** (the existing
   ``Selection.from_string`` grammar) or **by graphical pick** (the viewport /
   tree selection), save it under a name, and show it in the Model Tree under a
   dedicated **Selections** node.  A named selection is a saved ``Selection`` —
   the workflow layer's scoping primitive — so it is **not** folded into SAP
   ``Groups`` (file-derived, already the ``Selection(groups=...)`` filter);
   instead it follows the synthetic-``Views``-group precedent
   (``gui/models/model_index.py`` ``views_group``).  **Middle path (agreed):** a
   step may also *reference* a named selection as its scope — a new
   ``Selection.selections=[...]`` field, serialised through
   ``to_string()``/``from_string()`` — so it composes with the existing grammar
   without conflating storage.

2. **Document-lifecycle verbs.**  ``open`` / ``close`` / ``save`` as *recipe*
   verbs that mutate the working document (agreed), with default versioned
   filenames — a ``next_version_path(path)`` helper inserts ``_v1``/``_v2``…
   before the extension.  ``close`` takes ``save`` (bool) and ``versioned``
   (bool); ``save`` takes ``path``/``versioned``; ``open`` takes ``path`` (format
   auto-detected via ``io.model_loader.load_model_data``).  A ``select`` verb
   makes (or applies) a named selection (``name`` + ``expression`` or ``ids``).

**Design decisions (settled 2026-10-02).**
- ``open``/``close``/``save`` are recipe verbs that mutate document state:
  extend ``StepContext`` with a document handle (current path + dirty flag +
  named-selections store) and a new ``SESSION`` result kind; ``open``/``close``
  replace/clear ``model_data``/``model``.  A recipe beginning with ``open`` is a
  *document recipe* — the honest generalisation of ``run_recipe``.
- Named selections render under a dedicated **Selections** node (not SAP
  Groups), with the middle-path step-scope reference provided as well.

**Sequencing.** Foundation first (``NamedSelection`` + the Selections node +
text/graphical authoring), then the document verbs once the ``StepContext``
extension is decided.

**Touches (anticipated).** ``model/selection.py`` (``selections`` field),
``model/named_selection.py`` (new), ``gui/models/model_index.py`` (Selections
group), ``workflow/steps.py`` (``SESSION`` kind + ``StepContext`` document
state), ``workflow/verbs/{open,close,save,select}.py``, ``io/`` (versioned-path
helper), and the GUI's tree + selection-authoring surface.

**Follow-up (document-recipe GUI completion).** ✅ **Landed 2026-11-16** —
`run_recipe` records the final document state on `RecipeRun` (`document` +
`document_reset`), and `RecipeController._finished` adopts it (via
`RecipeController._adopt_document`) *before* registering results — so a recipe beginning with
`open` replaces the window's `_store` and its results attach to the document
the user is now looking at, instead of the stale one.

#### P46 — Benchmark library panel (validated example models)

Source: `docs/benchmark_register.md` (index/status) and
`docs/benchmark_roadmap.md` (plan); the existing
`gui/views/benchmark_dialog.py` / `generic_frame_dialog.py` entry points
(`MainWindow._on_open_benchmark` / `_on_open_generic_frame`).

**What.**  The benchmark models in `fea_toolkit.benchmarks` (SAC / FEMA 355C,
the 12 Medina generic frames) are validated in headless scripts
(`scripts/run_*.py` → `run_matrix` / `collapse_capacity_sa`); that harness
stays **outside the GUI**.  This item gives the GUI a **library of example
models**: a dock/tab listing the benchmark studies (tier, coverage, status)
that lets a user open one to *follow* the documented research process, compare
against the paper if they want, or just use it as a trusted reference model.

**Status: ✅ Landed 2026-11-16.**  The `build_model` verb, the structured
catalogue + sync test, the builder mapping (`builder` / `builder_params`) and
`benchmark_recipe(id)`, and the **Open example…** chooser (the example builders
then the catalogue listed with tier/status badges; "Open" presets a
`build_model` + `mesh` recipe) are
all in — the built model displays through the existing `document_reset` →
`RecipeController._adopt_document` path, so no `MODEL`-result branch is needed.  The two legacy dialogs
(`benchmark_dialog.py` / `generic_frame_dialog.py`) are retired: their
region/stories/design/direction and frame-code pickers folded into the
`build_model` step's `choices` (rendered as dropdowns), the IK-2005 frame is
opened from the chooser, and FEMA 355C (supporting code, not a §3 row) is
offered as a builder in the same *File ▸ Open example…* chooser.

**Two decisions (settled 2026-11-16).**

1. **Benchmarks enter as recipes, not a harness.**  "Open a benchmark" presets
   a `Recipe` into the Recipe dock — a new `build_model` verb (delegating to
   `build_fema355c` / `generic_frame.build_model` / `sac.load_model`, which
   already return `SAPModelData`) followed by the existing single-run verbs
   (`run_static` / `modal` / `pushover` / `ida`) and `chart`.  One spelling
   (anti-pattern 17: the panel *presets* a recipe and runs it); follow /
   modify / discard comes free from the Recipe dock.  The batch validation
   sweep is **out of scope** and stays in `scripts/`.
2. **Structured catalogue + hand-edited register, guarded by a sync test.**  A
   thin `BenchmarkEntry` dataclass catalogue (`benchmarks/catalogue.py`,
   OpenSees-free so the panel renders before any model is open) holds
   `id / title / type / tier / status / doc / test / recipe` plus optional
   `paper_reference` + `expected` scalars.  The register's §3 table stays
   **hand-authored** and gains a Status + Notes column; a sync test parses §3
   and fails CI when `title / tier / status` disagree with the catalogue.  One
   owner per fact (§7.4): the register owns the documentation facts, the
   catalogue owns the code facts (the recipe reference, which no Markdown
   table can hold).

**Status is a badge, not a filter.**  Every benchmark is listed, validated or
not: in-progress studies (e.g. the IK-2005 generic-frame collapse, P42b) appear
with a ⚠️/🚧 badge and a
Notes pointer to the per-benchmark doc, never hidden.

**Deferred (future consideration).**  A ground-motion *suite* / repeated NLTHA
step is not in the recipe now, but it is the natural extension — seismic
validation of a design runs one model over a suite of records — so record it
as a future *analysis* step, not a sweep verb today.

**Where.**  `benchmarks/catalogue.py` (new, OpenSees-free), a `build_model`
verb in `workflow/verbs/`, per-benchmark recipes, a
`gui/views/benchmark_library_panel.py` replacing the two dialogs (their
region/n_stories/design and frame-code pickers fold into the `build_model`
step's parameters), a sync test, and the register §3 Status/Notes columns.
`benchmark_register.md` §3 stays hand-edited.

### Tier 4 — Deferred / low-priority

#### P8 — Tcl-exporter merge (deferred)
`export_model_to_tcl()` (`opensees/builder.py`, SAPModelData-based) vs
`export_mesh_model_to_tcl()` (`opensees/recorder.py`, MeshModel-aware).
**Verification (2026-08-21):** independent implementations, no trivial
delegation.  If merged: one `isinstance` dispatcher + deduplicated shared
preamble/recorder emission.  Larger dedicated refactor — intentionally
deferred; keep the cross-references between `docs/tcl_export.md` and the
recorder doc current in the meantime.

#### P10 — Pushover fiber-level output (Phase 5, future/deferred)
Source: `docs/pushover_results_storage_viz.md` §Phase 5.

**What.** Per-element, per-integration-point, per-step fiber stress/strain
output via `pushover_record_fiber: True` (requires an explicit
`pushover_record_selection`).  Deferred until the envelope/step-recorder
layers (Phases 1–4) are stable.

#### P11 — Misc documented follow-ups
- ~~`asce41.hinge_length()` signature~~ ✅ **done 2026-10-14** — aligned to the
  unit-aware `(section, concrete, steel, units, *, elem_length)` form
  (strengths in SI Pa, consistent with `capacity.gb50010`); the legacy
  `(md, sec_name, elem_length)` entry point survives as
  `model.checks.compute_asce41_hinge_length`, which resolves the
  section/material and converts model → Pa before delegating.  Source:
  `docs/capacity.md`.
- **PSUMAT / CSMM smeared-plane-stress shell concretes** — ✅ **closed
  2026-10-14 (blocked).**  Package support (`PlaneStressUserMaterial` +
  `PlateFromPlaneStress` fields, `_create_nd_materials()` dispatch, `to_tcl()`
  emission) is in place and activates on a non-restricted build; the shipped
  wheel's PSUMAT is a stub and CSMM construction fails, so it stays inert
  until a full (non-restricted) build is available.  See
  `docs/shell_support.md` Options C/D1.
- **Archive / retire the historical plan documents (2026-10-14).**  ✅ **Done
  2026-10-27** — the four complete plan docs (`analysis_builder_migration_plan.md`,
  `deprecation_plan.md`, `csm_test_model_plan.md`, `linting_fix_plan.md`) are
  tagged `category: [archive]`, moved to a dedicated "Archive (completed plans)"
  nav section, and the "DONE" register was split out of this log into
  [`history/_done_log.md`](history/_done_log.md).  The docs' unique tables are
  preserved whole in the archived pages, so no further folding is required.

#### P12 — LoadCombination table parsing (README §3 reconciliation)
Source: repo-root `README.md` §3 "Load Combinations and Analysis Types";
`LoadCombination` dataclass + `TestLoadCombination` in `model/sap_data.py` /
`tests/test_sap_data.py`.

**What.** Steps 1–2 are **done**, and the model is now lossless and
tree-driven:

- `io/s2k_parser.py::_get_load_combinations()` parses the
  `COMBINATION DEFINITIONS` table into `LoadCombination` instances —
  `combo_type` and design overrides from the first row, plus an ordered,
  **duplicate-preserving** `entries` list of `LoadCombinationEntry`
  (name, factor, `kind`, optional `Mode`).  Exposed as
  `SAPModelData.load_combinations`; the table is registered as *handled* in
  `io/table_registry.py`.
- Because the `.s2k` rows carry no reference-type flag,
  `model.load_combinations.classify_combination_refs()` resolves each
  reference name against the load cases and combinations, labelling it
  `"case" | "combo" | "unknown"`.
- `model/load_combinations.py` adds the tree layer (`build_combo_tree` /
  `build_combo_tree_dict` / `calculate_aggregate_factors`), the linear
  aggregator (`expand_linear_combination`) and composite generation
  (`generate_combination_results`).  A response-spectrum case — or an SRSS
  result — mixed with gravity forks into `+`/`−` composites (2ⁿ for n
  independent spectrum terms); an `Envelope` yields a max/min pair
  (`envelope_mode="maxmin"`) or one composite per branch *variant*
  (`"per_path"` — a branch that itself forks contributes one per variant), with
  the max envelope using the `+` magnitude and the min the `−`.
  `apply_composite_load_case()` / `generate_composite_results()` evaluate
  composites into NPZ-ready `static/{composite}/...` arrays, so a combination
  visualises exactly like a load case.
- `to_e2k_combo_dict()` projects the flat mapping onto the ETABS `E2K`
  dictionary shape (`TYPE` / `LOADCOMBO` / `LOADCASE` / `OTHER`) for parity
  with `E2K_utilities`.

**Step 3 — done.**  `analysis.combinations.build_combination_results()` expands
the model's combinations against the per-case result payloads and returns
composite payloads in the same shape, and
`AnalysisBuilder.export_results(..., model=md, expand_combinations=True)`
merges them into ``static_results`` in one call, so the composites land in the
NPZ as ordinary ``static/{composite}/...`` cases.  Only the load cases the
combinations reference need to have been run; a missing case raises a clear
``KeyError``.  `Envelope` handling is selected by ``envelope_mode``.  See
`docs/load_combinations.md` for the full operator reference and the sign-fork
rules.

**Step 4 — done (Absolute Add).**  `_generate_absolute()` expands an
`Absolute Add` into a single `operator="absolute"` magnitude composite
(`max = Σ|factorᵢ·xᵢ|`, `min = −max`), and `apply_composite_load_case()` reduces
it as an element-wise sum of absolute values.  A reference that itself expands
to several variants is first grouped into one `operator="abs_max"` child, so its
alternatives contribute a single element-wise maximum absolute value rather than
one term each.  Like SRSS it forks `±` when a Linear-Add parent mixes it with
signed terms, and its per-case metadata carries `family="absolute"` with no
coordinate.

**Remaining.**

- **Bulk run driver** — run exactly the load cases a requested set of
  combinations references (ergonomic, not structural).
- **`Range Add` operator** — per quantity ``max = Σ max(0, xᵢ⁺)``,
  ``min = Σ min(0, xᵢ⁻)``; emits **two** composites like `Envelope`.  **Not
  implemented.**  Its CSI definition assumes each contributing case carries its
  own maximum and minimum (a range — what a moving-load / pattern-load case
  produces), which the single-valued result model does not yet represent, so a
  per-case min/max range concept is needed first.  See
  `docs/load_combinations.md`.

**Nested combinations — verified, not hypothetical (checked 2026-09-18).**  A
combination may itself contain other combinations.  CSI's `cCombo` interface
documents this explicitly: `SetCaseList` / `GetCaseList` / `DeleteCase` each
take an `eCNameType` argument (`LoadCase = 0`, `LoadCombo = 1`) which
"indicates whether the `CName` item is an analysis case (LoadCase) or a load
combination (LoadCombo)"; the Load Combination Data form adds "more than one
instance of the same load case (or combination) can be used in a load
combination".  (Retrieved from CSI's API help files, which are published for
ETABS 2015/2016; SAP2000 exposes the same `cCombo` class through
`SapModel.RespCombo`.  A SAP2000-specific page was not retrieved, so treat the
SAP2000 *wording* as strongly indicated rather than directly quoted.  The
SAP2000 *Input File Format Manual* also documents a separate `COMBO` data
block, which has not been inspected.)

Consequences — all now handled:

- Reference kind is resolved **by name** (`classify_combination_refs`)
  rather than from a column, since names are unique across load cases and
  combinations.
- Both tree building and generation carry a **cycle guard** (`ValueError`).
- Linear factor expansion is offered only where the result really is linear
  (`expand_linear_combination` raises otherwise); `Envelope` and `SRSS` are
  handled as operators by `generate_combination_results`, while
  `Absolute Add` / `Range Add` raise as unsupported (a future extension).
- `entries` is an ordered list, so repeated references — legal per the quote
  above — are preserved; the ETABS-side `combo_func` stores the same
  `(name, factor)` shape.

#### P13 — Joint Level 3 elements (Joint2D / beamColumnJoint)
Source: repo-root `README.md` §5 "Joint Modeling";
`docs/vecchio_emara_benchmark.md` §6.6.

**What.** Level 1 rigid end zones are implemented (`rigid_end_zones` /
`rigid_link_mpc`); **Level 3** joint elements (`Joint2D`,
`beamColumnJoint`) remain unimplemented.  `joint_extents` already composes
with Level 1, so the two features will not double-count regions once Level
3 lands.  Level 2 (zero-length springs restoring a fraction of the rigid
connection) also remains open.

**Outline steps.** 1) Extend the parser to recognise joint elements if
present in SAP2000; 2) implement `Joint2D` / `beamColumnJoint` emission in
the builder; 3) verify interaction with `joint_extents` and Level-1
offsets.

#### P14 — FRAME LOADS - POINT point loads + temperature loads
Source: repo-root `README.md` §7 (Medium) "Improved Load Handling".

**What.** `FRAME LOADS - POINT` (point loads on frames) is now **parsed**
into ``frame_point_loads``, carried through the Preprocessor, visualised by
``extract_load_glyphs``, and **applied** to OpenSees (transverse *and* axial
forces via ``eleLoad -beamPoint`` — the ``Py Pz xL Px`` form; point moments via
their consistent nodal loads — bending through the cubic-Hermite shape-function
derivatives, torsion via a linear ``rdist`` split — warned; under
``Corotational`` the forces fall back to the same consistent nodal loads,
warned).
Temperature loads are closed (no current demand).
Note: the pushover `lateral_load_type='point'` is a synthetic unit point
load for benchmark pushes — distinct from parsing user-supplied
`FRAME LOADS - POINT` data.

**Outline steps.** 1) ✅ **done 2026-10-16** — `FRAME LOADS - POINT` parsing into
a new `frame_point_loads` field (carried through the Preprocessor and
visualised by ``extract_load_glyphs``); 2) ✅ **done 2026-11-23** — propagate
to OpenSees: transverse + axial forces via `eleLoad -beamPoint` (``Py Pz xL
Px``), point moments via consistent nodal loads (bending via shape-function
derivatives, torsion via a linear ``rdist`` split), and a `Corotational`
fallback to consistent nodal loads (warned once per member+type);
3) ~~temperature loads~~ ✅ **closed 2026-10-14 — no current demand**
(do not implement until a model actually needs them).


#### P16 — Parsed-but-unconsumed parser keywords
Source: `docs/parser_coverage.md` (register + reproducible audit method;
re-checked 2026-09-17).

**What.** Several `.s2k` values are parsed and stored on the model but reach
no consumer, so they look implemented while being inert.  The full register,
with the method to re-derive it, is `docs/parser_coverage.md`.

**Open items.**
1. `FrameElement.mirror_2` / `mirror_3` — mirror the drawn extrusion (and
   fibre mesh) for mirrored asymmetric sections.
2. `FrameElement.transform_stiffness` — apply, or explicitly refuse,
   SAP2000's stiffness transformation for the centroid offset in the builder.
3. `FrameEndOffset.rigid_factor` — support partial rigid-zone rigidity
   (`0 < RigidFactor < 1`) instead of always fully rigid.
4. ~~`SAPModelData.area_edge_constraints`~~ ✅ **closed 2026-10-14 — accepted as
   inert.**  The field round-trips through `Selection.filter_model` but no
   geometry/meshing consumer reads it; `find_constraint_edges()` stays the
   single derivation path.
5. ~~`AreaMesh.no_auto_mesh_at_edges` / `no_sub_mesh` / `min_size`~~ ✅ **closed
   2026-10-14 — accepted as inert.**  Parsed but deliberately not honoured by
   the area-meshing path; not on any roadmap.
6. Encased / circular-concrete / double-angle section fields — consume once
   those section types are implemented (overlaps P6b).

**Outline steps.** The items are independent; take them opportunistically.
When one is wired up, delete its row from `docs/parser_coverage.md` and record
the change in [`history/_done_log.md`](history/_done_log.md).

**Tooling.** The *table*-level companion to this register is
`docs/parser_coverage.md` § *Table coverage* +
`src/fea_toolkit/io/table_registry.py`: `fea-tables model.s2k` (or
`python -m fea_toolkit.io.table_registry model.s2k`) reports tables the toolkit
does not consume, and the review report includes the same section.


#### P44 — Profile the full B3 SAFE preprocess

Source: `docs/safe_import.md` § Performance.

**What.** Floor meshing itself is fast (~5 s on B3), but a *full*
`Preprocessor.run()` on the B3 `.$sf` model (> 5000 nodes, 324 areas) took
**> 2 min** — the cost is in the rest of the pipeline (frame splitting,
wall-slab intersection detection, coincident-node merging, edge detection),
not the new floor meshing.  Profile the run end-to-end, identify the hot
stages, and apply targeted fixes (spatial-index reuse, removing O(n²) scans)
before SAFE models become a first-class workflow.

**Where.** `src/fea_toolkit/opensees/preprocessor.py` (`run()`),
`src/fea_toolkit/model/geometry*.py`, `src/fea_toolkit/mesh/floors.py`.

**Outline steps.** 1) time each `run()` stage on B3 with `cProfile`; 2) rank by
cumulative cost; 3) fix the top offenders without changing the frozen-topology
contract (§3.2); 4) re-measure and record the numbers in
`docs/safe_import.md` § Performance.


#### P45 — Structured (rectangular) meshing for SAFE walls

Source: `docs/safe_import.md` § Floor meshing; raised 2026-03-11 (Trial v1.0.f2k).

**What.** SAFE's "Auto Rectangular" wall meshing is a *structured* grid — walls
are meant to stay rectangles.  The `.f2k` export normally bakes that grid in
(small ≈0.23 m² quads), but a few large wall objects (e.g. areas 49 / 62, 46 /
40 m² in Trial v1.0) are exported *unmeshed*, and `mesh_floors` deliberately
skips walls (`area_roles == "wall"`), so those parents reach the builder as a
single oversized shell.  Floors, by contrast, are arbitrary `n`-gons that must
go through gmsh's general mesher.

**Plan.** Keep the two meshing paths separate and role-gated:

* **walls → structured subdivision, never gmsh**: for each wall area whose edge
  length exceeds `floor_mesh_options["wall_max_size"]`, synthesize an
  `AreaMesh(auto_mesh=True, max_size=wall_max_size)` and run the existing
  `mesh_area_elements` (u×v rectangular grid, already honours openings and
  edge-restraint propagation).  Walls already ≤ `wall_max_size` (SAFE's baked
  sub-elements) are left as-is.
* **floors → gmsh `mesh_floors`** at `floor_mesh_options["max_size"]`
  (unchanged).

The Inspector now surfaces the inputs (`Role`, `Mesh Option`, `Mesh Size`,
`Auto Edge Constraint`, frame `Auto Mesh …`, node `Include In Mesh`) so the two
target sizes can be checked against the produced mesh before the build.

**Where.** `src/fea_toolkit/opensees/preprocessor.py` (`_mesh_areas` /
`_mesh_floors`), `src/fea_toolkit/model/geometry_mesh.py`
(`mesh_area_elements`), `src/fea_toolkit/mesh/floors.py`.

**Outline steps.** 1) after the gmsh pass in `_mesh_floors`, collect wall areas
with an edge length > `wall_max_size`; 2) build `AreaMesh` stubs and call
`mesh_area_elements` (or add a thin `mesh_walls` wrapper that reuses it); 3)
mark the parent walls `inactive` and merge the new wall sub-elements + nodes
into `md`; 4) re-run the SAFE integration test and inspect a large wall in the
GUI — it should subdivide into ≈0.25 m rectangles.



#### P47 — Pickable extruded frame skeleton (optional, future)

Source: quad-view selection fix (2026-11-16); `render_extruded_frames`
docstring and `docs/dev_notes.md` → *PyVista picking contract*.

**What.** The extruded frame solids drawn by `render_extruded_frames` are
deliberately **non-pickable** decoration: the line skeleton (`render_frames`)
is the pickable layer because it has exactly **one cell per frame**, so
`vtkCellPicker.GetCellId()` *is* the frame index.  The extruded mesh instead
has several faces per frame (~3 for `plates` detail, more for `solid`), so its
cell ids do not map 1:1 to frames.  This is exactly the bug class the
quad-view selection fix closed — the mirror path silently re-armed the
non-pickable extruded actor and picks began returning *face* ids that resolved
to the wrong element or to none.

**Why it might be worth doing.** A larger, "click-what-you-see" target.  The
cell picker tolerates ~13 px around the member *centreline*; for deep beams /
columns the visible solid surface is further than that, so clicking the edge
of a flange can miss even though it is clearly on the member.  A pickable
extruded skeleton would make the click target match the drawn volume.

**Design (if ever pursued).**

1. Make the extruded actor pickable again (drop the `SetPickable(False)` in
   `render_extruded_frames`; the mirror path's pickable-flag preservation is
   already in place).
2. Extend `SelectionIndex` with a face-count walk for the extruded mesh —
   mirroring `_shell_label` — so a face index maps back to its frame.  The
   face-per-frame count varies by section shape and `extruded_detail`, so it
   must come from the renderer (or a shared helper), never be hard-coded.
3. The solid surface sits in front of the centreline, so the extruded actor
   would win most ray-casts; decide whether the line skeleton stays pickable
   as a fallback or is demoted to decoration too.

**Constraints / risk.** A pickable extruded actor with a stale face→frame map
silently selects the wrong element — the very failure mode already fixed.
Never flip the flag without the mapping step, plus a regression test that picks
a frame *through the extruded surface* and asserts the correct label (the
real-backend probe pattern in `local/gui_pick_probe.py`).

**Where.** `plotting/renderers/pyvista.py` (`render_extruded_frames`),
`gui/controllers/selection.py` (`SelectionIndex`).

**Status.** **Optional** — no demand yet.  Do not schedule until click-target
accuracy on large sections is reported as a real problem.

#### P49 — Link (connection / isolator / damper) element support
Source: GitHub issue #3 — "Link objects are ignored: link-only nodes are dropped
while their restraints are kept, producing an unsupported (singular) model".

> **Status: ✅ Landed 2026-11-22** — Phases 1 and 2a–2e (parsing, per-DOF
> emitters, bearing elements, dispatch/warnings/runner defaults, the
> `check_hysteretic_backbone` + `isolate_base` verbs, and the E-Defense
> LRB-CLB / TPB + Morgan & Mahin FP benchmarks) are complete and archived in
> [`history/_done_log.md`](history/_done_log.md).  The **active** remainder is
> the *Deferred — known limitations* list, the *Interim import policy*, and the
> *Follow-up (pending external access)* below.

**What.** Link objects are parsed and, since **Phase 1**, **emitted** as
OpenSees elements.  `LINK PROPERTY DEFINITIONS 01 - GENERAL` / `02 - LINEAR`
and `LINK PROPERTY ASSIGNMENTS` parse into `SAPModelData.link_properties` /
`link_assignments`; the Preprocessor retains link-endpoint nodes; and
`AnalysisBuilder._create_link_elements` (`opensees/_links.py`) emits a
`twoNodeLink` with one `Elastic` uniaxial material per active DOF (a grounded
link becomes a `zeroLength` to a coincident fixed ground node).  ``Fixed``
DOFs get a near-rigid spring; `link_model: "elastic"` (default) uses the
effective stiffness for *all* link types, so elastic static / modal / RS
analyses work for linear and isolator links alike.

**Remaining (Phase 2).** The plan lives in
[`docs/nonlinear_link_plan.md`](nonlinear_link_plan.md).  **P2a (parse), P2b(i)
(per-DOF emitters) and P2b(ii) (bearing elements) are done**: `link_model:
"nonlinear"` now emits Gap/Hook (one-sided `Hysteretic`), Plastic (Wen)
(`Steel02` by default, `Hysteretic` via `wen_model`), the damper subtypes with a
clean 1:1 (`Viscous`/`Parallel`), the bilinear/friction dampers (`Hysteretic`,
post-yield ratio parsed), Rubber Isolator (`elastomericBearing*`) and
Friction Isolator (`singleFPBearing` + `Coulomb`/`VelDependent`), T/C Friction
Isolator (`singleFPBearing` + `VelDependent`, compression friction; the
tension-friction half is dropped with a warning) and Triple Friction Pendulum
(`TripleFrictionPendulum`).  Uncovered link types (multilinear, the S2K
triple-pendulum / high-damping-rubber isolator tables, the damper friction
directional/orthotropic subtypes, and unknown types) now **warn** at import
time (``NotImplementedWarning`` — see ``src/fea_toolkit/_warnings.py``) with a
request to report them, and are linearised on their effective stiffness; they
raise ``NotImplementedError`` only when there is nothing to linearise onto.
**Decisions (recorded in the plan):** D1–D3 the one-sided / backbone family
(Gap, Hook, bilinear, friction dampers) uses a single `Hysteretic`
backbone with neutral pinch/damage — not `ENT`/`ElasticPPGap`/`ElasticPP`;
**D10** Plastic (Wen) defaults to `Steel02` (smooth, explicit `Fy`, `R0`↔`n`),
with `Hysteretic` as the opt-in sharp-bilinear alternative and `BoucWen`
rejected (no explicit `Fy`); D4 friction isolator starts `Coulomb`,
`VelDependent` when `mu_fast`/`rate` present; D6 scope items 1–4,
benchmarks/stretch as follow-up.  The `Hysteretic` neutral defaults carry a
**one-time cyclic check**, to be exposed as a **workflow verb** so a generated
model's script re-runs it (plan §7, D7/D8).  `tests/test_link_nonlinear.py`
pins the compression-only backbone to `ENT`, the tension-only backbone to
`ElasticPP` (monotonic, D9), the `Steel02` smooth yield, the neutral-defaults
cyclic check, and the bearing/friction-model emitter args.  Link *local axes*
are parsed, including an arbitrary (non-axis-aligned) `CoordDir2`.

**Outline steps.** 1) ✅ parse connectivity (incl. grounded links).  2) ✅
`LinkProperty` + `link_assignments` + parse `LINK PROPERTY ASSIGNMENTS` /
`01 - GENERAL` / `02 - LINEAR`.  3) ✅ retain link nodes + emit
`twoNodeLink`/`zeroLength` in `AnalysisBuilder`.  4) ✅ P2a parse the `03+`
tables + E2K tokens into `nonlinear_params` sub-dataclasses.  5) ✅ P2b(i) emit
per-DOF nonlinear materials (Gap/Hook/Wen/dampers).  6) ✅ P2b(ii) emit bearing
elements (rubber/FP isolators).  7) ✅ P2c dispatch/warnings/runner-defaults +
registry (`_LINK_EMISSION_FAMILY`).  8) ✅ P2d check verb (`check_hysteretic_
backbone`) + base-isolation verb (`isolate_base`).  9) ✅ P2d benchmarks (§10) —
all four in `tests/test_link_benchmarks.py` (E-Defense LRB, Morgan & Mahin FP,
viscous damper (Pekcan/Mander/Chen), gap/pounding (Anagnostopoulos)); + ✅
`modeling_guidance.md` §10 (gravity-before-dynamic, bearing/material choices,
base isolation).  10) ✅ P2e bilinear/friction damper
family (`Hysteretic`, `Ratio` parsed) + non-axis-aligned `CoordDir2`; 11) ✅
full-model shake-table check (Phase A) — `benchmarks/e_defense.py` +
`tests/test_e_defense_lrb.py` reproduce the E-Defense 5-storey LRB-CLB building
(fixed-base 0.70 s, isolated ~2.9 s, JMA-Kobe NLTHA, drift < 1/200).  This
surfaced and fixed an emitter bug: `elastomericBearingPlasticity` was emitted
with `mu = 0.0` (NaN under static gravity); it now emits `mu = 1.0` (the
repo's LRB backbone benchmark value).  12) ✅ TPB configuration (Phase B) —
`TripleFrictionPendulum` (Dao et al. 2013) emitter in `opensees/_links.py` +
`benchmarks/e_defense.py` `config="tpb"` + `tests/test_e_defense_tpb.py`
(backbone 0.275 W at 1.13 m, first-slope period ~1.96 s, Kobe NLTHA).  The
element is `TripleFrictionPendulum` — `tripleFPBearing` is an unknown element
type in OpenSeesPy (verified against the installed library).
13) ✅ T/C Friction Isolator (singleFPBearing + VelDependent) — parse the
``09 - TC SLIDING ISOLATOR`` table (``Slow``/``Fast``/``Rate``) and the E2K
``ISOLATOR3`` tokens into ``FrictionIsolatorParams``; emit with a warning that
the tension-friction half (``SlowT``/``FastT``/``RateT``) is dropped.
☐ multilinear, damper directional/orthotropic.

**Deferred — known limitations (P49), recorded 2026-11-22 so they read as
tracked follow-ups, not fresh bugs:**

* **Multilinear Elastic/Plastic backbones** — the `03`/`11` force-deformation
  tables are *recognised but not imported*: the backbone points are dropped and
  the link linearises on its effective `Ke` (warned).  No OpenSees uniaxial
  material reproduces an arbitrary piecewise-linear backbone cleanly, so this
  stays a warn-and-linearise path (plan §4, §9).
* **Damper friction directional/orthotropic subtypes** — no clean 1:1 per-DOF
  uniaxial emitter; linearised on `Ke` (warned).
* ~~**Nonlinear `K` for Gap / Hook / Wen**~~ ✅ resolved 2026-11-22 —
  `GapParams`/`HookParams`/`WenParams` now carry a per-DOF nonlinear ``k``
  (distinct from the effective ``Ke``), populated by both parsers and used as the
  backbone slope by the emitters (falling back to ``Ke`` only when no nonlinear
  ``K`` was stored).
* **Named coordinate systems in `CoordDir1`/`CoordDir2`** — `_direction_vector`
  resolves global-axis tokens (`+X`, `-Y`, …) and coordinate triples only; a
  *named* CSYS (or any unparseable token) returns `None` and the link falls
  back to its I→J geometry axes (pinned by `test_direction_vector_parsing`).
  Resolving SAP2000 named CSYS would need a `COORDINATE SYSTEMS`-table parser.
* **Grounded links / bearings emitted in the builder, not the Preprocessor** —
  the coincident fixed ground node and the `zeroLength`/bearing element are
  created at emission time (`AnalysisBuilder._create_link_elements`), not as a
  topology mutation; that is deliberate (the builder owns the OpenSees domain),
  so "move grounded links into preprocessing" would only make sense if the
  ground node had to participate in topology (it does not).
* **Exponential damper → `Viscous` (approximation, not a Maxwell `ViscousDamper`)**
  — the emitted `Viscous(C, α)` is a pure dashpot, which *omits the source
  model's series flexibility*: CSI's exponential damper couples the dashpot
  with a series elastic spring, so its force-relaxation transient is not
  reproduced.  Recorded in plan §4 as a fidelity limitation.
* ~~**Damper `C` vs `Ce` conflation (units bug)**~~ ✅ resolved 2026-11-22 —
  `DamperParams` now carries a per-DOF nonlinear dashpot coefficient ``c``
  distinct from the effective ``Ce``; `_scale_link_properties` scales ``ce``
  linearly (F/L) and ``c`` by ``F/L^cexp`` (previously ``ce`` was scaled by
  ``F/L^cexp`` — a units error for the *effective* coefficient), and
  `_damper_dof_mat` uses ``c`` (falling back to ``ce`` with exponent ``1.0``
  when ``c`` is unset — only a supplied nonlinear ``c`` uses ``cexp``).  The
  parser does not yet populate ``c`` from the damper
  table's ``C`` column — blocked on a real export (interim note below).
* ~~**`nonlinear_params` codec round-trip**~~ ✅ resolved 2026-11-22 —
  `io/model_codec` now decodes the ``__type__`` discriminator to the concrete
  `LinkNonlinearParams` subclass; previously a saved/reloaded model degraded
  `nonlinear_params` to a plain dict.  Test:
  `tests/test_model_codec.py::test_nonlinear_link_params_round_trip`.
* ~~**Fixed-DOF penalty fallback**~~ ✅ resolved 2026-11-22 —
  `_link_fixed_stiffness` scales a fully-rigid link's penalty off the stiffest
  effective stiffness across *all* link properties (warning + dimensionless
  `1.0` only when the model has none), instead of a bare `1.0e6` magic number.
* **`check_hysteretic_backbone` runs a 1-DOF truss rig** — the cyclic check
  drives the *material* through a cycle in a 1-DOF domain (mirroring the
  component test) rather than re-building the full 3D `AnalysisBuilder` domain;
  that keeps the check cheap and material-focused, at the cost of not
  exercising the assembled element.
* **Bearing/spring orientation & validation gaps** — the TPB
  (`TripleFrictionPendulum`) and single-FP shear orientation, coincident-node
  default axes, and the rubber shear-law / nonlinear-params validation are
  exercised by the benchmark tests (`test_e_defense_tpb.py`,
  `test_bearing_behavior.py`, `test_link_benchmarks.py`) rather than by
  dedicated emitter-arg assertions; adding those, plus bearing/spring
  docstrings, is a documentation/validation polish item, not a correctness gap.

**Interim import policy for unverified CSI columns (2026-11-22).**  Until a real
SAP2000/ETABS export confirms the remaining token/column names (Wen ``ratio``/``n``,
the damper ``C``/``Cexp`` columns, E2K per-DOF ``NONLIN``, E2K link axes, S2K
``PropMod`` modifiers, the link-axis ``Plane``/``Axis`` columns), the parser must
keep working on what it *does* know and degrade honestly on what it does not:
parse the columns whose names are already confirmed, and when a source carries a
field the importer does not yet interpret, emit a targeted
``NotImplementedWarning`` ("…is not parsed yet; please send this export to …")
and continue on the effective-stiffness linearisation — never raise for a
recognised-but-unparsed column, and never invent a column name (`.clinerules`
§12).  This keeps import usable for isolation-bearing models *today* while the
fidelity gaps remain tracked here.

**Follow-up (pending external access).**  The Phase-A NLTHA currently drives
the model with a JMA-Kobe stand-in; the measured LRB-CLB targets are the
**100 % Iwanuma** (2011 Tohoku, K-NET station MYG015) record — peak isolator
displacement **347 mm** and base shear **1187 kN = 0.224 W** (Ryan et al.
2012, WCEE paper 2054).  Waiting on K-NET user-registration confirmation
(`kyoshin.bosai.go.jp`) to download the `.NS`/`.EW`/`.UD` waveforms.  Once
available: add a K-NET ASCII reader to `io/ground_motion.py`, add bidirectional
(two-component `UniformExcitation`) support to `run_transient_analysis`, and
replace the stand-in with the real Iwanuma case pinned to the published
347 mm / 0.224 W targets.

#### P50 — Recover member-force / fiber demands for the dispBeamColumn distributed-load fallback
Source: review of `opensees/_loads.py` / `opensees/_runner_pushover.py`
(2026-11-20).

**What.** `dispBeamColumn` (the default fiber pushover element) silently drops
partial-span `-beamUniform` loads, so both load paths fall back to the
statically-equivalent nodal pair (`beam_load_to_nodal_loads`).  That recovers
the correct resultant (support reactions) but **not** the member's interior
force diagram: the span is left unloaded, so mid-span moment and fiber demands
are approximate.  The nodal pair is the equivalent load of a *fixed-end* beam,
so it is only exact when the member ends are actually fixed.

**Options (not yet chosen — do not implement without discussion).**
1. **Element subdivision + full-span per-segment `-beamUniform`** — subdivide
   the member (Preprocessor, frozen topology) and apply a piecewise-constant
   `-beamUniform` per sub-element.  `dispBeamColumn` accepts full-span uniform
   loads, so this recovers the force diagram to mesh resolution.  Cost: more
   elements, and `rdist` positions must be re-mapped onto the children.
2. **Switch the fiber pushover to `forceBeamColumn` / `nonlinearBeamColumn`**,
   which accept partial-span `-beamUniform`, eliminating the fallback.  Cost:
   force-based elements carry the conditioning risks in `.clinerules` §3.13.
3. **Post-processing reconstruction** — keep the nodal fallback but add the
   fixed-end force vector back to the reported `localForces` / section forces.
   **This does not work for nonlinear elements that need the internal forces
   during the analysis** (the domain still solves with nodal loads), so it is
   deferred until that constraint is lifted.
4. **Scope + document/warn** — use the fallback only where member-demand
   fidelity is irrelevant and warn otherwise.

The code comment at both fallback sites points here.

#### P51 — Download the 7 PEER NGA-West2-only LMSR-N records (IK-2005 / MK-2003)

Source: `docs/nga_west2_download_guidance.md`;
`docs/ik2005_generic_frame_benchmark.md` §"Acquisition status".

**What.**  The 40-record LMSR-N set is 33/40 fetched; the 7 remaining records
exist only in PEER NGA-West2 (`LP89agw` `LP89sjw` `NR94lv6` `NR94pic` `SH87bra`
`SH87icc` `SH87pls`).  Fetching them unblocks the 40-record IK-2005 / MK-2003
collapse re-run.  **S1 (this item)** fetches those 7; **S2 (staged)** replaces
all 40 with NGA-West2 originals for full provenance.

**Landed so far (2026-11-23).**  The offline core is in place and tested:
`read_peer_record` hardened (scientific-notation / leading-dot `DT`, `NPTS`
truncation warning), `io.ground_motion.select_component_by_pga`, the
`scripts/fetch_peer_records.py` manifest + vertical filter + RSN grouping +
PGA-based component selection, and the `[nga-download]` extra.  Remaining is
the **live run** — look up the 7 RSNs by event+station, then
`--download --rsns …`, which needs a human to complete NGA-West2 login and any
verification.

**Where.** `scripts/fetch_peer_records.py`;
`src/fea_toolkit/io/ground_motion.py`; `tests/test_fetch_peer_records.py`,
`tests/test_ground_motion.py`.

**S2 follow-up (staged).**  A full-40 provenance upgrade needs (a) a validated
40-record RSN/station mapping (the hand-guessed RSNs are unreliable), (b) a
decision on how the 14 USGS multi-component records are stored — `_load_record`
appends the channel suffix, so a single `<record_id>.AT2` is ignored for those
(store `<record_id>_<channel>.AT2` or drop them from `USGS_CHANNELS`).

#### Closed items (README reconciliation, 2026-08-25)
- **Deeper opstool result-post-processing integration** — closed as **no
  current demand** (`docs/report_generation.md`); NPZ ↔ opstool ODB
  converter deferred until demand exists.

## DONE — archived

> Completed-work entries (the "DONE" register) have moved to
> [`history/_done_log.md`](history/_done_log.md) to keep this live plan short.
