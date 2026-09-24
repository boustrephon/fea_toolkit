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
configurable mouse interaction.  Analysis actions, load and result overlays,
persistence and the quad view are still ahead; see
[What is still missing](#what-is-still-missing).

## Installing and launching

The GUI is an **opt-in extra** and needs **Python 3.10 or newer** (the core
toolkit stays on 3.9 for Rhino 8's embedded interpreter):

```bash
pip install -e ".[gui]"      # PySide6 + pyvistaqt + qtpy (+ pyobjc on macOS)
fea-gui                      # launch with a built-in demo portal frame
fea-gui path/to/model.s2k    # or open a SAP2000 model / JSON export
python -m fea_toolkit.gui    # same entry point, for launchers and scripts
```

`File ▸ Open` (`Ctrl+O`) parses a `.s2k` or JSON file and displays it.  The
GUI renders the **parsed** model — the preprocessing steps (splitting at joints,
meshing) are not wired up yet, so split sub-elements and their parent/child
links do not appear ([P24](_pending_work.md)).

## The window

| Area | What it is |
|---|---|
| Central viewport | Embedded PyVista view; the model is coloured by section |
| **Model** dock (left, top) | Tabs: **Model Tree** (live) and **Property Tree** (not yet implemented) |
| **Inspector** dock (left, bottom) | Every field of the selected object, read-only |
| **Messages** dock (bottom) | Log of what the application did, including any settings-file problems |
| Status bar | Unit system, cursor coordinates, and read-outs reserved for the analysis milestone |
| Toolbars | Top: Open, Save/Export, Run, Mesh, results.  Right edge: camera views and display toggles |

The Model Tree is **lazy**: groups (Nodes, Frame Elements, Materials, …) list
their entities only when expanded, so a model with hundreds of thousands of
elements stays responsive.

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
  **node**, not the member passing through it.
* **A left-*drag* orbits** and never selects — a press that travels more than
  `drag_threshold_px` is treated as camera movement.
* **Clicking empty space clears** the selection (highlight and Inspector).

Selectable entities are the ones with geometry of their own: **frames, area
elements and nodes**.  Materials, sections and load definitions have no
geometry, so selecting them clears the highlight.

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
| `preset` | `"click_drag"` | Named starting point (`click_drag`, `right_click`) |
| `pick_button` | `"left"` | Which button selects — `"left"` or `"right"` |
| `drag_threshold_px` | `5` | Pointer travel (logical pixels) above which a press is a *drag*, not a click |
| `pick_tolerance` | `0.010` | Size of the invisible picking region around an element, as a fraction of the viewport diagonal.  Raise it if clicking feels unforgiving, lower it if nearby members steal your clicks |
| `node_priority` | `true` | Prefer a node over the member passing through it |
| `node_snap_tolerance` | `0.012` | Tolerance for that node-first pick |

The file is read at start-up; the Message Log states which policy is in force
("Left-click an element to select it; drag to orbit") and **reports anything it
did not understand** — an unknown key, an out-of-range value or unreadable JSON
leaves the defaults in place and says so, rather than failing silently.

Explicit **Select / Orbit modes** (the SAP2000 arrangement) are not available
yet: a mode has to be able to disable rotation, which needs the interactor style
switched.  It is recorded as [P23](_pending_work.md).

## Display toggles

| Control | State |
|---|---|
| **View ▸ Display ▸ Show nodes** | Live — show or hide the node markers |
| **View ▸ Display ▸ Show shells** | Live — show or hide area elements |
| **View ▸ Display ▸ Clear highlights** | Live — drop the selection highlight |
| **View ▸ Display ▸ Show element labels** | Not yet — nothing draws labels, so the toggle waits for that renderer ([P23](_pending_work.md)) |
| **View ▸ Display ▸ Show loads** | Pending the load-rendering milestone (6) |
| **View ▸ Display ▸ Show force diagrams** | Pending the results milestone (7) |

The toggles are re-checked whenever a model is displayed, so what the menu says
always matches what is on screen.

## Menus at a glance

Live items work today; the rest are present but greyed, each naming the
milestone or backlog item that will wire it — a menu that appears later is more
jarring than a greyed-out one.

| Menu | Live | Greyed |
|---|---|---|
| **File** | Open (`Ctrl+O`), Quit | Save results, Export Tcl, Export screenshot |
| **Edit** | — | Copy, Preferences |
| **View** | Zoom to fit, Camera (Isometric / Top / Front / Side), Display (Show nodes, Show shells, Clear highlights) | Show element labels, Show loads, Show force diagrams, Reset layout |
| **Model** | — | Mesh, Split elements, Selections, Units |
| **Analysis** | — | Run, Static, Modal, Response spectrum, Pushover, Stop |
| **Results** | — | Deformed shape, Force diagrams, Storey response, Pushover curve, Clear results |
| **Help** | Documentation, About | — |
| Toolbars | Open; camera views; display toggles | Save/Export, Run, Mesh, results |

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
| Preprocessing — split at joints, meshing ([P24](_pending_work.md)) | The tree lists the members as drawn: no split children, no `parent_id` / `child_ids` fields |
| Analysis actions (milestone 5) | `Analysis ▸ …` is greyed; analyse from a script or notebook instead |
| Load, deformed-shape and force overlays (milestones 6–7) | Use the [Visualisation Toolkit](viewer.md) and the `plot_*` functions for results |
| Element labels, the Property Tree tab, explicit interaction modes ([P23](_pending_work.md)) | The corresponding menu item and tab are greyed |
| Window-layout persistence (milestone 8) | Docks and window geometry are not remembered between sessions |
| Quad view (milestone 10) | A single viewport; use the Camera menu and the view cube |

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

**The model shows fewer elements than expected.**  The GUI displays the parsed
model, without splitting or meshing ([P24](_pending_work.md)).

## For contributors

* The `gui` subpackage is imported lazily and must not import Qt at package
  import time: `gui/views/__init__.py` and `gui/models/__init__.py` re-export
  their Qt classes through PEP 562 `__getattr__` for exactly this reason, and
  `tests/test_lazy_imports.py` pins it.
* Qt tests carry the `needs_gui` marker (skipped without the `[gui]` extra) and
  run head-less under `QT_QPA_PLATFORM=offscreen`.  Picking itself cannot run
  there — the offscreen platform has no GL context — so its tests use a plain
  off-screen `pv.Plotter`.
* The platform facts worth knowing before touching the viewport — what PyVista's
  pickers actually return, why the gesture comes from Qt events rather than VTK
  observers, the logical-versus-device pixel conversion and the calibrated pick
  tolerance — are written up in [Development Notes](dev_notes.md).
