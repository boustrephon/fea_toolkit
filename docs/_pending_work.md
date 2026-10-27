---
title: "Pending Work Log"
description: "Internal log of *pending* work items across the fea_toolkit (refactors, physics, features, housekeeping); completed work is archived in history/_done_log.md."
status: "draft"
tags: [planning, work-log, internal]
category: [planning]
---
# Pending work — fea_toolkit (2026-10-27)

## Strategy — the next steps

> This log is the single source of truth for pending work; this section is the
> *sequencing layer* over it (maintained 2026-10-27).  The standalone plan
> documents are historical records — `analysis_builder_migration_plan.md`,
> `csm_test_model_plan.md`, `deprecation_plan.md` and `linting_fix_plan.md`
> are ✅ complete and tagged `category: [archive]` (P9's Phase 3 triage resolved 2026-10-11 — baseline renewed,
> the remaining diagnostics accepted as known-benign per `.clinerules` §11).
> The **active front** is the workflow/GUI layer (P31, P35).

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
4. **P35** — named selections first, then the `open`/`close`/`save`/`select`
   document verbs.  **P31** (command palette) is independent input ergonomics
   and can run in parallel with any of the above.

**Then — physics / feature gaps.**  P6b, P7 (fibre patches, Python-native NLD),
then P19 / P23 / P25.

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
`Analysis ▸ Run` path registers the same table via
`_show_load_reaction_summary` — one spelling, per anti-pattern 17.

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

> Priority-ordered register of **open** work (maintained 2026-10-14).  Every
> item is cross-referenced to its source document.  Completed work — the
> Tier 1–2 physics items, the Tier 3 workflow/GUI batch (P20–P38), and the
> deferred-housekeeping closures — is recorded in the
> [`history/_done_log.md`](history/_done_log.md) archive, so
> this list shows only what remains.

### Tier 3 — Feature gaps (placeholders / partial)

#### P41 — A4 damage normalization (φj = θ/θ_y, dEj = E/(My·θ_y))

Source: `docs/sac_nonlinear_benchmark.md`;
`src/fea_toolkit/opensees/_runner_dynamic.py`.

**What.** The peak extraction and `.mat` comparison landed 2026-10-26 (see
`history/_done_log.md`).  **Remaining:** the runner still returns the *raw*
damage quantities — normalize them to the benchmark's φj = θ/θ_y and
dEj = E/(My·θ_y).

**Where.** `opensees/_runner_dynamic.py`; `opensees/_elements.py`
(`_create_lumped_hinges` hinge instrumentation); tests
`tests/test_benchmark_sac3_nltha.py` (`test_transient_recorders_*`).

#### P42 — IMK calibration equations + A2/A3 (Krawinkler collapse)

Source: `docs/benchmark_roadmap.md` §3 (canonical); `docs/imk_deterioration_model.md`.

**Status (2026-10-27).** A1 machinery landed — `hinge_backbone="imk"` builds a
`Bilin` steel hinge (energy-based cyclic deterioration) in `opensees/_elements.py`.
A0 landed — beams (Lignos–Krawinkler 2011 θp/θpc/Λ) and columns (Lignos et al.
2019 / ASCE 41-17 first-cycle envelope).
**Remaining:**
1. (Optional) Column cyclic-deterioration Λ — not in the 2019 paper (Lignos &
   Krawinkler 2011, Table 3; paywalled).  Columns currently use Λ = 0.
2. A3 frame collapse (SAC 3/9/20 with IMK; IDA vs Ibarra–Krawinkler 2005) —
   first increment landed (`enrich_imk` + 3-story `backbone="imk"`); the
   remaining sub-items (column axial ratio, beam unbraced length, column Λ,
   NLTHA convergence, 9/20 matrices, IDA) are tracked in
   `docs/imk_deterioration_model.md` §5.

**Where.** `benchmarks/imk_calibration.py`; `opensees/_elements.py`
(`hinge_backbone="imk"`); `tests/test_benchmark_sac_imk.py`.

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

**Status: 🚧 Planned** — design in [docs/workflow_palette.md](workflow_palette.md).

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

**Remaining.**

- **Bulk run driver** — run exactly the load cases a requested set of
  combinations references (ergonomic, not structural).
- **`Absolute Add` operator** — per quantity ``max = Σ|factor·xᵢ|``,
  ``min = −max`` (a magnitude, so it forks `±` when mixed with signed loads).
  **Not implemented** — `generate_combination_results()` currently raises it as
  unsupported.  Drop-in: no new result representation required.
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
into ``frame_point_loads`` and carried through the Preprocessor and
visualised by ``extract_load_glyphs``; the only remaining gap is propagating
it to OpenSees ``eleLoad`` point loads (display-only for now).  Temperature
loads are closed (no current demand).  Note: the pushover
`lateral_load_type='point'` is a synthetic unit point load for benchmark
pushes — distinct from parsing user-supplied `FRAME LOADS - POINT` data.

**Outline steps.** 1) ✅ **done 2026-10-16** — `FRAME LOADS - POINT` parsing into
a new `frame_point_loads` field (carried through the Preprocessor and
visualised by ``extract_load_glyphs``); 2) propagate to OpenSees `eleLoad`
point loads — **still open** (display-only for now, see the load-display
work); 3) ~~temperature loads~~ ✅ **closed 2026-10-14 — no current demand**
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


#### Closed items (README reconciliation, 2026-08-25)
- **Deeper opstool result-post-processing integration** — closed as **no
  current demand** (`docs/report_generation.md`); NPZ ↔ opstool ODB
  converter deferred until demand exists.

## DONE — archived

> Completed-work entries (the "DONE" register) have moved to
> [`history/_done_log.md`](history/_done_log.md) to keep this live plan short.
