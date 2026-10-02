---
title: "Desktop GUI"
description: "User guide to the fea_toolkit desktop GUI: launching it, the panels and menus, selection in both directions, camera controls, display toggles, and the mouse-interaction settings file."
status: "partial"
tags: [gui, pyside6, pyvistaqt, desktop, selection, interaction, settings]
category: [export-viz]
related: [gui_roadmap.md, dev_notes.md, viewer.md, _pending_work.md]
---
# Desktop GUI

An optional PySide6 desktop application (`fea-gui`) that shows a model in an
embedded PyVista viewport with a browsable Model Tree, a property inspector, a
message log and two-way selection between the tree and the 3-D view.

**Status: partially complete.**  Milestones 1–4 of the
[Desktop GUI Roadmap](gui_roadmap.md) have landed — viewport, application
chrome, lazy Model Tree + inspector, and bidirectional selection with
configurable mouse interaction — and milestone 5's **static-linear** slice is
in: **Analysis ▸ Run…** solves the model's own load cases in the application and
shows each as a results view, and **File ▸ Save results…** writes one out.  Load
glyphs, storey and pushover plots, persistence and the quad view are still
ahead; see [What is still missing](#what-is-still-missing).

## Installing and launching

The GUI is an **opt-in extra** and needs **Python 3.10 or newer** (the core
toolkit stays on 3.9 for Rhino 8's embedded interpreter):

```bash
pip install -e ".[gui]"      # PySide6 + pyvistaqt + qtpy (+ pyobjc on macOS)
fea-gui                      # launch with a built-in demo portal frame
fea-gui path/to/model.s2k    # or open a SAP2000 model / JSON export
python -m fea_toolkit.gui    # same entry point, for launchers and scripts
```

`File ▸ Open` (`Ctrl+O`) parses a `.s2k` or JSON file and displays it.  What you
see first is the **parsed** model, as drawn in SAP2000; `Model ▸ Mesh…` then
runs the Preprocessor and replaces the display with the prepared topology (see
[Preprocessing](#preprocessing-split-and-mesh)).

## The window

| Area | What it is |
|---|---|
| Central viewport | Embedded PyVista view; the model is coloured by section |
| **Model** dock (left, top) | Tabs: **Model Tree** (live — a **Views** group, then the model's own groups) and **Property Tree** (not yet implemented) |
| **Inspector** dock (left, bottom) | Every field of the selected object, read-only |
| **Messages** dock (bottom) | Log of what the application did, including any settings-file problems |
| Status bar | Unit system, cursor coordinates, a busy indicator while preprocessing runs, and read-outs reserved for the analysis milestone |
| Toolbars | Top: Open, Save results, Run, Stop, Mesh.  Right edge: camera views, display toggles and results controls (Deform %, Flags %) |

The Model Tree is **lazy**: groups (Nodes, Frame Elements, Materials, …) list
their entities only when expanded, so a model with hundreds of thousands of
elements stays responsive.

## Views

The Model Tree opens with a **Views** group: named lenses over the model, one
row per view, added as you work.

| View | Added by | Shows |
|---|---|---|
| **Unprocessed** | opening a file | the members exactly as drawn in SAP2000 |
| **Processed** | `Model ▸ Mesh…` (create shells off) | the split sub-elements an analysis would build |
| **Meshed** | `Model ▸ Mesh…` | the above, plus the shell elements for areas |

Selecting a view redraws the scene with that view's geometry and **keeps the
camera**, so comparing the drawn model with the prepared one does not move your
viewpoint.  The Inspector reports the selected view's **counts**, never its
geometry: nodes, frame elements (the total, including superseded split parents,
and the analysis-ready count), area elements, materials and sections — plus what
produced it.

```
name             Processed
kind             geometry
source           Recipe: Split
units            N · m · T
n_nodes          128
n_frames         96
n_frames_active  90
```

### Derived views

**Edit ▸ Duplicate view** copies the active view and asks for a **selection
expression** — the same language the command line and scripts use:

```
section=Roof slab z=3:6     # a section, between two elevations
type=Node id=12             # a joint
material=C30                # by material
group=Walls                 # by group
```

The duplicate shares its parent's geometry — no second copy of the model — so a
dozen refinements cost one model between them, and **Edit ▸ Edit view
selection…** re-filters it later.  An empty expression means "everything the
parent shows", and the view is labelled `… · unfiltered`.

The Inspector lists a derived view's `parent` and `selection` beside its counts,
and those counts are the **filtered** ones — what you are actually looking at,
not the parent's totals.

A selection that names **joints** shows the members framing into them, as FEA
preprocessors do: select joint `12` and you see that joint and the members
attached to it (one hop), not a lone marker.  A selection that names only
elements shows those elements and their joints.  Those two readings are
deliberately different — the reasoning is in
[Development Notes](dev_notes.md) → *Selection: two resolutions*.

### Results views

**File ▸ Open results…** reads an `.npz` / `.h5` results archive and adds a
**view per load case**, labelled with the case name (qualified by its
combination when the two differ).  An archive carries the geometry it was
written from, so this works with **no model open**: the views draw the analysed
geometry, and the case views share one display model between them.

Those views are display-only — an archive has no materials, loads or restraints
and no unit system — so `Model ▸ Mesh…` stays
disabled while one is displayed.  Open the `.s2k` when you want to preprocess or
re-analyse (`File ▸ Open`), and the results views are replaced by that model's
own views.

**Results ▸ Deformed shape** draws the active case's **deformed shape** over that
geometry, auto-scaled so the largest displacement is **10 % of the model's
size** — the **Deform %** box on the main toolbar overrides that (the value is a
percentage of the model diagonal, so a millimetre model and a metre model read
the same way).  The action is enabled only for a case view whose archive
actually carries nodal displacement — an archive written without it leaves the
button greyed — and the scale is a *display* factor: the displacements are read
in model units and never modified.

Switching views clears the overlay and unchecks the action, because a deformed
shape belongs to **one** case's geometry: drawing case A's shape over case B's
members would be a lie.  **Results ▸ Clear results** does the same on demand.

**Results ▸ Force diagrams** draws the active case's **member end forces** as
flag diagrams, in whichever component the **Force** selector names.  The
selector offers SAP2000's local-DOF vocabulary — `P` (axial),
`V2` / `V3` (the local-2 / local-3 shears), `T` (torsion), `M2` / `M3` (the
local-2 / local-3 moments) — and opens on `M3`.  Those are the same six local
DOFs a member-end release frees, and they map onto the schema's `Fx … Mz` result
keys (`FORCE_QUANTITY_LABELS` in `model/sap_data.py`), so the label is a
*display* name and the key underneath is unchanged.  The labels name the **axis**
a component acts along or about; the values remain the recorded OpenSees
`localForces`, and so do not adopt SAP2000's output sign convention.

The diagram follows the shape's rules: enabled only for a case view whose archive
carries end forces, with the selector greyed alongside it.  Its size is
**auto-scaled to 10 % of the model** — the **Flags %** box (a percentage of the
model diagonal, like the deformed shape's) overrides it.  The two overlays have
**separate** knobs, because a displacement amplification and a
length-per-force factor are different quantities; both are unit-agnostic, so a
whole-building flag diagram no longer overwhelms the scene.  Switching views and
**Results ▸ Clear results** drop it.  The two overlays are independent, so
turning off the shape leaves the diagram drawn and vice versa.

### Running an analysis

**Analysis ▸ Run…** solves the model's own load cases inside the application.

1. **Model ▸ Mesh…** must have been run first.  Run
   deliberately does *not* preprocess for you — a topology change is something
   to see reported, not something to happen silently — so until then Run stays
   greyed and its tooltip names the step it needs.  **The shells matter:**
   in a building-shell model the walls and slabs are what tie the members
   together, so *create shells* must be on (the dialog's default) — a split-only mesh
   leaves those areas as loads-only, members that frame into them stay detached,
   and the run reports a non-convergence rather than quietly solving something
   else (the Model Review's *floating sub-structures* count warns of this before
   you run).
2. The dialog lists the model's **static cases**, each with a **load
   multiplier** (default `1.0`, meaning *exactly as the `.s2k` defines it*).
   Tick several to run several solves.  The model's **combinations** are listed
   too — ticking one runs the cases it needs and then reduces them to
   composites — and a combination that needs a case this slice cannot run (a
   response-spectrum leaf, say) is greyed with the reason rather than quietly
   skipped.
3. Optionally author a **custom case**: name it, then tick load patterns and set
   each one's factor — the `{"ULT": {"Dead": 1.4, "Live": 1.6}}` form as a UI.
   It is one more solve, of factored patterns.
4. **Run** solves on a worker, with a progress bar, a **Stop** that takes effect
   at the next case boundary, and a Message Log line per case.  The factor is a
   **load** multiplier, not a post-scaling of results — scaling results is what a
   *combination* does.

Each solved case becomes a **view**, exactly like a case from an opened archive,
so **Results ▸ Deformed shape** and **Force diagrams** work on it immediately.
**Nothing is written to disk**: the result is held in memory and served through
the same read seam an archive uses.  Re-running a case replaces its view; a
different case is added beside it.

**File ▸ Save results…** writes the displayed case's archive when persistence is
wanted — always asking for a path, with a suggested name, so variants that differ
by a small change can be kept side by side.

Modal, response-spectrum and pushover runs are not in this slice; their menu
entries stay greyed.

### A view is a lens

A view is a **lens, not a copy**: every view shares the one model in memory, and
re-running `Split` refreshes the `Processed` view rather than piling up
duplicates.  That holds for results views too — a dozen cases cost one model
between them.

## Camera controls

The viewport uses VTK's *terrain* interaction style, so the model's **Z axis
stays vertical** while you orbit:

| Gesture | Effect |
|---|---|
| Left-drag | Orbit (hold `Shift` to constrain to one axis) |
| Middle-drag | Pan |
| Right-drag, or the wheel | Zoom (dolly) |
| `R` | Reset the camera (same as **View ▸ Zoom to fit**) |
| View cube (top right) | Click a face, edge or corner to snap to that view |
| **View ▸ Camera** | Isometric / Top (XY) / Front (XZ) / Side (YZ) |

## Selecting

Selection works in **both directions**, and both sides share one Qt-free
identity index (`gui/controllers/selection.py`), so they cannot drift apart:

* **Tree → viewport.**  Clicking a row in the Model Tree highlights that entity
  in the 3-D view (orange for a frame or area element, blue for a node) and
  fills the Inspector.  Each selection *replaces* the previous highlight.
* **Viewport → tree.**  **Left-click** an element in the viewport: its entry in
  the Model Tree is selected, the containing group is expanded and scrolled to,
  and the Inspector and highlight follow.  Clicking a joint selects the
  **node**, not the member passing through it.  The message log names what was
  picked in full — *Selected node 146 in the tree from the viewport.* — so a
  selection can be identified from the log alone.
* **A left-*drag* orbits** and never selects — a press that travels more than
  `drag_threshold_px` is treated as camera movement.
* **Clicking empty space clears** the selection (highlight and Inspector).

Selectable entities are the ones with geometry of their own: **frames, area
elements and nodes**.  Materials, sections and load definitions have no
geometry, so selecting them clears the highlight.

What the highlight looks like depends on the entity: a frame gets a **tube**, a
node a **blue sphere**, and an area element a **thin orange slab straddling it**.
The slab carries volume rather than lying on the element, for the same reason the
frame cue is a tube: a highlight drawn coincident with a translucent slab can wash
out or z-fight, and a selection cue has to read.

## Mouse interaction is configurable

People arrive with different expectations, so the mouse behaviour is not
hard-coded — a **preset** or any individual knob can be set in a settings file.

| Preset | Behaviour |
|---|---|
| `click_drag` *(default)* | A clean left click selects; left-drag orbits; nothing is modal |
| `right_click` | The right button selects; the left button stays pure camera control |

Settings file — `$FEA_TOOLKIT_GUI_CONFIG` if set, else
`~/.config/fea_toolkit/gui.json`:

```json
{
  "preset": "click_drag",
  "drag_threshold_px": 4,
  "pick_tolerance": 0.010
}
```

| Key | Default | Meaning |
|---|---|---|
| `preset` | `"click_drag"` | Named starting point (`click_drag`, `right_click`, `select_orbit`) |
| `pick_button` | `"left"` | Which button selects — `"left"` or `"right"` |
| `drag_threshold_px` | `5` | Pointer travel (logical pixels) above which a press is a *drag*, not a click |
| `pick_tolerance` | `0.010` | Size of the invisible picking region around an element, as a fraction of the viewport diagonal.  Raise it if clicking feels unforgiving, lower it if nearby members steal your clicks |
| `node_priority` | `true` | Prefer a node over the member passing through it |
| `node_snap_tolerance` | `0.012` | Tolerance for that node-first pick |
| `add_modifier` | `"shift"` | Modifier key held to add a pick to the current selection |
| `toggle_modifier` | `"control"` | Modifier key held to toggle a pick |
| `marquee_modifier` | `"alt"` | Modifier key that turns a drag into a rubber-band selection |
| `select_mode` | `false` | Explicit Select / Orbit mode — a drag marquee-selects instead of orbiting |

The **display-quality knobs** (Shells opacity, Shrink, Deform %, Flags % and the
Force selector) are also remembered between sessions, in a `display` section of
the same file:

```json
{
  "preset": "click_drag",
  "display": { "shrink": 0.9, "shell_opacity": 0.7 }
}
```

| Key | Default | Meaning |
|---|---|---|
| `shell_opacity` | `0.7` | Opacity of area elements (shells) |
| `shrink` | `1.0` | Fraction of true size elements are drawn at (`1.0` = off) |
| `deformed_scale` | `10.0` | Deformed-shape size, % of the model diagonal |
| `force_scale` | `10.0` | Force-diagram size, % of the model diagonal |
| `force_quantity` | `"M3"` | End-force component drawn as a flag diagram (a SAP local-DOF label) |

The display section is written when the window closes; the interaction keys and
anything else in the file are left untouched.

The file is read at start-up; the Message Log states which policy is in force
("Left-click an element to select it; drag to orbit") and **reports anything it
did not understand** — an unknown key, an out-of-range value or unreadable JSON
leaves the defaults in place and says so, rather than failing silently.

Selecting supports **multiple** entities: a clean click replaces, Shift-click
adds, Ctrl/Cmd-click toggles, and a drag with the marquee modifier (Alt/Opt, or
any drag in Select mode) rubber-band-selects.  A left-to-right marquee is a
*window* (only elements fully inside the box); a right-to-left marquee is a
*crossing* (everything the box touches).  **View ▸ Select mode** toggles the
explicit Select / Orbit arrangement, in which a drag marquee-selects instead of
orbiting.  The viewport selection can be read back as
a ``Selection`` (``MainWindow.current_selection()``) for a workflow step.

**Right-clicking** the viewport opens a context menu: **Copy selection** puts the
selection on the clipboard as a selection expression (`type=Frame …`), **Mass /
weight of selection…** reports the selected elements' self-weight, and **Hide
selected** / **Isolate selected** / **Show all** dig into a crowded model.  The
menu appears only when the right button is *not* the selecting button — under the
`right_click` preset the right button still selects, and the menu is not shown.

## Display toggles

| Control | State |
|---|---|
| **View ▸ Display ▸ Show nodes** | Live — show or hide the node markers |
| **View ▸ Display ▸ Show beams** | Live — show or hide frame elements |
| **View ▸ Display ▸ Show shells** | Live — show or hide area elements |
| **View ▸ Display ▸ Show restraints** | Live — support symbols at restrained nodes (one glyph per restrained DOF) |
| **View ▸ Display ▸ Clear highlights** | Live — drop the selection highlight |
| **View ▸ Display ▸ Show element labels** | Not yet — nothing draws labels, so the toggle waits for that renderer ([P23](_pending_work.md)) |
| **View ▸ Display ▸ Show loads** | Pending the load-rendering milestone (6) |
| **View ▸ Display ▸ Show force diagrams** | Pending the results milestone (7) |

### Display quality

The results controls (Deformed shape + **Deform %**, Force diagrams + **Flags %** + the Force selector) and two display-quality knobs sit on the **View toolbar**:

| Knob | What it does | Default |
|---|---|---|
| **Shells** | Opacity of area elements.  Applied in place, so turning it stays interactive; frame lines are untouched | `0.70` |
| **Shrink** | Draw every element at this fraction of its true size, centred, which opens up the joints (SAP2000's *shrink elements*) | `1.00` — off |

Both are *display* transforms: the model, the selection and any results are
unaffected, and a value set here is kept while you switch views.  Shrink
re-draws the model in place, keeping the camera and the tree selection.

### Supports

**View ▸ Display ▸ Show restraints** draws a support symbol at every restrained
node — **one glyph per restrained DOF**: an arrow pointing at the joint for each
restrained translation (U1/U2/U3) and a curl for each restrained rotation
(R1/R2/R3).  That covers any restraint set, so a fixed base shows arrows plus
curls, a pin shows three arrows, and a roller one arrow.  They scale with the
model and need no setup; turning the toggle off hides them.

A **restrained node is itself drawn green and a little larger** than a free one,
so a model's supports read at a glance without counting arrows.  The green marker
is deliberately smaller than the blue selection sphere, and a different colour, so
"restrained" and "selected" are never confused.

Selecting a **node** also reports its support conditions in the Inspector:

| Property | Value |
|---|---|
| Restraints | `Fixed (U1 U2 U3 R1 R2 R3)`, `Pinned (U1 U2 U3)`, or just `U2 U3` for an unconventional set |
| Constraint | the joint constraint assigned to the node, e.g. `D1 (DIAPHRAGM)` |

Only rows with something to say appear, and a results archive — which carries no
restraints — adds none.

Selecting a **frame or area** reports, beyond its own fields, its assignment and
group memberships (a node reports groups too):

| Property | Value |
|---|---|
| Section | the assigned section, e.g. `UB300 (I/Wide Flange)` |
| Material | that section's material, e.g. `Steel` |
| Groups | the groups the element belongs to, comma-joined |

These are looked up from the model at click time (see the DONE register in
`_pending_work.md` for why this is a scan, not a pre-built index).



The toggles are re-checked whenever a model is displayed, so what the menu says
always matches what is on screen.

## Preprocessing (split and mesh)

Opening a file shows the model as it was drawn.  One Model-menu action —
**Mesh…** — runs the package's Preprocessor over it and swaps the display for the
prepared topology.  It is a **preset over a recipe**: the dialog writes the step
it stands for into the Recipe dock and then runs the recipe, so one click still
prepares the model while the work it performed becomes visible and editable —
see [Workflows (recipes)](#workflows-recipes).

| Action | What it does |
|---|---|
| **Model ▸ Mesh…** | Composes the `mesh` step in a dialog — split members at joints (`split_elements`), create shell elements for areas (`create_shells`), split slabs at walls — and runs it on OK |

Both run on a **worker thread**, so the window keeps repainting, and the Message
Log reports the outcome:

```
Preprocessed: 4 frame elements (2 split sub-elements, 1 superseded parents), 0 area elements.
```

Splitting is **opt-in per element in the model itself**: it happens where the
SAP2000 auto-mesh flags (`AtJoints`, `AtFrames`) ask for it.  A model that asks
for nothing is reported as *"no element requested splitting"* — that is a
result, not a failure.

Whenever a member is split, the original is kept (flagged `inactive`, carrying
its `child_ids`) and each sub-element records its `parent_id`, so the whole
hierarchy is browsable: the sub-elements are rows of their own in the Model Tree
and the Inspector shows the parent/child fields.  Each run also registers its
result as a **view** ([Views](#views)), so the members as drawn and the
analysis-ready sub-elements are one click apart.  Preprocessing does not move the
camera.

The actions are enabled only while a **parsed** model is loaded.  The
Preprocessor consumes a `SAPModelData` and returns a `MeshModel`, so every run
starts again from the file's own model rather than from an already-split one.

## Workflows (recipes)

A **recipe** is the sequence of operations a model is put through — mesh it,
soften a section, solve the cases, reduce the combinations — held as data rather
than buried in a handler.  The **Recipe** dock at the bottom of the window shows
it as an ordered list of **steps**.

Each step is a *verb* applied to a *selection*, with its own parameters:

| Verb | What it does |
|---|---|
| `scale_sections` | Scales a selection's section stiffness **in place** — the masonry "soften the wall" option, keeping the elements, their loads and their mass |
| `mesh` | Splits frames and meshes areas into shells.  Areas matching the step's selection are held back as **loads-only**: no shell element is created, and their loads become edge loads on the supporting frames |
| `run_static` | Solves the model's static load cases |
| `combine` | Reduces the solved cases into load combinations |

The panel offers **Add** (a menu of verbs), **Remove**, **Up / Down** to reorder,
and a one-line summary of the selected step.  Double-clicking a step, or the
**Edit step…** button, opens a **Step dialog** — the same shape as
**Analysis ▸ Run…** — with the step's **selection** (the same expression language
as a derived view, e.g. `section=brick wall type=Area`, with a `…` button that
opens the selection composer) and a **parameter form built from the verb's own
declaration**, so the form cannot drift from what the verb accepts.  **Add**
opens the same dialog pre-filled for the chosen verb.  A `dict`-typed parameter
whose verb declares a *manifest* — today `run_static`'s **config**, the OpenSees
builder overrides — renders as a **collapsible Configuration** group (one widget
per option, its help as a hover tooltip, in a height-capped scroll area, closed
by default), not a raw literal; a step's parameters are emitted only when they
differ from their declared default, so the builder applies its own.  Tick
**Optional** when a step's failure should be logged and the run continue instead
of stopping.

Right-click a step in the list and choose **Help** to open a read-only dialog for
that step's verb: its `StepSpec.help`, every parameter's name / type / default /
choices / help, and its `needs` prerequisite — all rendered from the Qt-free
`STEP_SPECS`, so the dialog cannot drift from what the verb accepts.  The same
**Configuration (optional)** group appears at the foot of the **Analysis ▸ Run…**
dialog.

**Recipe ▸ Run recipe** (`Ctrl+Shift+R`) runs the steps in order on a worker
thread, registering a prepared model as a geometry view, reporting each step in
the Message Log, and registering each solved case — a static case or a load
combination — as a **result view**.  Nothing is written to disk: the views read
the run's in-memory archive through the same `ResultsRepository` an opened `.npz`
is served from.

> **Which one to use.**  **Analysis ▸ Run…** solves a case set against the model
> as currently displayed, and is the quicker route once the model's *preparation*
> is settled.  Use a **recipe** when the preparation is itself part of the answer:
> it records the mesh, the loads-only selection and the section changes *beside*
> the solve, so the whole workflow is visible, saveable and replayable rather
> than living in your memory of which menu items you clicked.  Both register
> their cases as views.

**Two masonry options, both available and independent.**  `scale_sections`
*keeps* the wall elements and reduces their stiffness, so their weight stays with
them; a `mesh` step whose selection matches the walls *leaves them out* of the
OpenSees model entirely, reassigning their loads to the supporting frames.  The
right one depends on whether the wall should still stiffen the frame a little or
must not enter the stiffness system at all.

**Recipe ▸ Save recipe…** writes the recipe as JSON, **Open recipe…** reads one
back, and **Export as Python…** writes it as a runnable script — so a workflow
built by clicking can be kept, reviewed, diffed and re-run from a terminal.

Design notes and the full verb reference:
[`workflow_authoring.md`](workflow_authoring.md).

## Menus at a glance

Live items work today; the rest are present but greyed, each naming the
milestone or backlog item that will wire it — a menu that appears later is more
jarring than a greyed-out one.

| Menu | Live | Greyed |
|---|---|---|
| **File** | Open (`Ctrl+O`), Open results…, Save results…, Quit | Export Tcl, Export screenshot |
| **Edit** | Duplicate view, Edit view selection… | Copy, Preferences |
| **View** | Zoom to fit, Camera (Isometric / Top / Front / Side), Display (Show nodes, Show beams, Show shells, Show restraints, Clear highlights) | Show element labels, Show loads, Show force diagrams, Reset layout |
| **Model** | Mesh… (composes a `mesh` step in a dialog and runs it on OK) | Selections, Units |
| **Analysis** | Run… (`Ctrl+R`), Stop | Modal analysis, Response spectrum, Pushover |
| **Recipe** | Run recipe (`Ctrl+Shift+R`), Open recipe…, Save recipe…, Export as Python…, Clear recipe | — |
| **Results** | Deformed shape (+ Deform %), Force diagrams (+ Force selector + Flags %), Clear results | Storey response, Pushover curve |
| **Help** | Documentation, About | — |
| Toolbars | Open; Save results; Run / Stop; Mesh | Save results (until a result is shown), Export Tcl / screenshot |
| View toolbar | Zoom/camera; display toggles (nodes, beams, shells, restraints); Deformed shape + Deform %; Force diagrams + Force selector + Flags %; **Shells** opacity and **Shrink** | Show labels, Show loads, Show force diagrams |

## Application identity

The window title, the About box and Qt's own naming read **FEA Toolkit**
(`gui/app.py` → `APP_NAME`), and on macOS the Application menu's *About* /
*Hide* / *Quit* items are retitled accordingly at launch.

The **bold menu-bar label, the Dock entry and the Cmd-Tab name still say
`Python`**, because macOS paints those from the interpreter's framework bundle
and no runtime Qt or Python call changes that.  A self-contained bundle that
drives a *separately installed* OpenSees is the recorded route
([P22](_pending_work.md)); the probe evidence is in
[Development Notes](dev_notes.md).

## What is still missing

| Missing | Effect today |
|---|---|
| Modal / spectrum / pushover runs (milestone 5, the rest) | `Analysis ▸ Run…` does static-linear cases and combinations; those three menu entries are greyed |
| Loads, storey and pushover plots (milestones 6–7) | A case's deformed shape *and* its member end forces **are** drawn in the GUI; load glyphs, storey plots and pushover curves still need the [Visualisation Toolkit](viewer.md) and the `plot_*` functions |
| Element labels, the Property Tree tab, explicit interaction modes ([P23](_pending_work.md)) | The corresponding menu item and tab are greyed |
| Window-layout persistence (milestone 8) | The mouse policy and the display-quality knobs **are** remembered (see the settings file above); dock positions and window geometry are not |
| Shell (area) result display ([P36](_pending_work.md)) | Shell force resultants are extracted and stored in NPZ archives, but the GUI draws no per-shell scalar field |
| Quad view (milestone 10) | A single viewport; use the Camera menu and the view cube |
| `check` and `chart` verbs, and the command palette ([P30](_pending_work.md) phase C, [P31](_pending_work.md)) | The Recipe panel runs the verbs that exist (`scale_sections`, `mesh`, `run_static`, `combine`, the three model checks, the `capacity.*` checks and `mesh_checks`, and `chart`).  A recipe's solved cases and combinations **are** registered as result views, its checks as a check-table view, and its `chart` step as an embedded figure; the remaining charts (storey forces, pushover, modal, CSM) have no step yet |

## Troubleshooting

**Clicking in the viewport does nothing.**  The click has to land *on*
geometry: a left-*drag* (further than `drag_threshold_px`) belongs to the
camera and a click on empty space deliberately clears the selection.  If
clicking still feels unforgiving, raise `pick_tolerance` (say `0.016`); if
neighbouring members steal clicks, lower it.  Decoration — highlights, labels,
force flags — is deliberately **not** pickable, so clicking a highlighted member
selects the member underneath it.

**Selection uses the wrong button.**  The first lines of the Message Log state
the policy in force; a `preset` of `right_click` (or a `pick_button`) in the
settings file moves selection to the other button.

**The settings file seems to be ignored.**  Unrecognised keys, invalid values
and malformed JSON are reported in the Message Log together with the file path
— look there first.

**The model shows fewer elements than expected.**  Members that were split are
displayed as their sub-elements.  Switch to the **Unprocessed** view to see the
members exactly as drawn in SAP2000; a model that was never preprocessed already
shows them that way.

**Preprocessing reports no change.**  Splitting happens only where the model asks
for it — the SAP2000 auto-mesh flags (`AtJoints`, `AtFrames`) — and meshing needs
area elements.  The Message Log says *"no element requested splitting"* when the
model asks for none.

## For contributors

* The `gui` subpackage is imported lazily and must not import Qt at package
  import time: `gui/views/__init__.py` and `gui/models/__init__.py` re-export
  their Qt classes through PEP 562 `__getattr__` for exactly this reason, and
  `tests/test_lazy_imports.py` pins it.
* Nothing expensive runs on the GUI thread: `gui/controllers/worker.py`
  (`TaskWorker`) is the one worker, and cancellation is **cooperative** — an
  atomic `preprocess_model` call cannot be interrupted mid-call, so a cancel only
  takes effect at the next task boundary.
* Qt tests carry the `needs_gui` marker (skipped without the `[gui]` extra) and
  run head-less under `QT_QPA_PLATFORM=offscreen`.  Picking itself cannot run
  there — the offscreen platform has no GL context — so its tests use a plain
  off-screen `pv.Plotter`.
* The platform facts worth knowing before touching the viewport — what PyVista's
  pickers actually return, why the gesture comes from Qt events rather than VTK
  observers, the logical-versus-device pixel conversion and the calibrated pick
  tolerance — are written up in [Development Notes](dev_notes.md).
