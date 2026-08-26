---
title: "Rhino 3-D Export"
description: "Export to Rhino 8: centreline and extrusion geometry, layers, colours, and Grasshopper metadata."
status: "complete"
tags: [export, rhino, visualisation, geometry]
category: [export-viz]
related: [viewer.md, results_schema.md, tcl_export.md]
---
# Rhino 3-D Export

Export a parsed SAP2000 model (from `.s2k` or JSON) into the active
Rhinoceros 3-D document as organised, metadata-rich geometry.

---

## Python version in Rhino 8

**You must use CPython 3.9+.**  The ``fea_toolkit`` module uses Python 3
features throughout (f-strings, ``dataclasses``, type hints) and will
**not** run under IronPython 2.7 (the default on Mac).

To switch Rhino to CPython:

1. `Tools → PythonScript → Options`
2. Set ``Python Interpreter`` to a Python 3.9+ installation
   (e.g. ``/usr/local/bin/python3`` on Mac)
3. Click ``OK`` and restart the PythonScript editor

All examples in this guide assume CPython 3.9+.

### Required packages

Rhino 8 ships NumPy with its bundled CPython, but the unified results
loader additionally uses **h5py** to read HDF5 (``.h5``) stage and
results files.  Install it once for the interpreter selected above::

    /path/to/python3 -m pip install "h5py==3.13.0" "numpy==2.0.2"

``h5py 3.13.0`` is the last release with macOS-arm64 wheels for CPython
3.9 — the fixed Python inside Rhino 8 — and ``numpy==2.0.2`` is pinned to
the same wheel set.

Scripts that need h5py declare it with ``# r:`` reference comments so the
Rhino editor installs them on demand:

```python
#! python 3
# r: numpy==2.0.2
# r: h5py==3.13.0
import sys
sys.path.insert(0, r"/Users/andrew/Projects/fea_toolkit/src")
```

---

## Quick Start

### Inside Rhino 8 (Mac or Windows) — file picker (recommended)

This script opens a native file dialog to pick a `.s2k` or `.json` file,
then imports it into the Rhino document.

IMPORTANT: edit the path to the fea_toolkit.

```python
#! python 3
"""
Import a SAP2000 model into Rhino via a native file dialog.

Opens a system file picker filtered for ``.s2k`` and ``.json`` files.
The selected model is parsed and imported with full layer structure,
UserText metadata (NodeID, FrameID, SAP_* properties), and Rhino groups.

Features
--------
- Native Rhino file dialog (no hard-coded paths in the script)
- Auto-detects ``.s2k`` vs JSON format
- Creates centreline geometry, 3-D extrusions, colour-coded joints,
  and Rhino groups matching SAP2000 group definitions
- Runs ``_Zoom _Extents`` after import

Requirements
------------
- Must run **inside Rhino 8** under CPython 3.9+
- ``fea_toolkit`` must be on ``sys.path`` (adjust the line below)
- A parsed SAP2000 model file (``.s2k`` or ``.json``)
"""

import sys
sys.path.append(r'/path/to/fea_toolkit/src')   # <-- adjust to your setup

import Rhino
from fea_toolkit.io.s2k_parser import SAP2000Parser
from fea_toolkit.rhino import RhinoImporter

# --- File picker (native Rhino dialog) -----------------------------------
dialog = Rhino.UI.OpenFileDialog()
dialog.Filter = "SAP2000 files (*.s2k;*.S2K;*.$2k;*.json)|*.s2k;*.S2K;*.$2k;*.json|All files (*.*)|*.*"
dialog.Title = "Select SAP2000 model file"

if not dialog.ShowDialog():
    print("Import cancelled.")
else:
    file_path = dialog.FileName

    # Load .s2k or JSON -- the parser auto-detects the format
    if file_path.lower().endswith('.json'):
        parser = SAP2000Parser.from_json(file_path)
    else:
        parser = SAP2000Parser(file_path)
        parser.parse()

    md = parser.get_model_data()

    importer = RhinoImporter(md)
    report = importer.run(
        create_centreline=True,    # points, lines, planar Breps
        create_extrusions=True,    # 3-D Brep extrusion solids
        color_code_joints=True,    # colour by restraint type
        create_groups=True,        # Rhino groups from SAP groups
        verbose=True,
    )
    print(report)
    Rhino.RhinoApp.RunScript("_Zoom _Extents", False)
```

The dialog filters for `.s2k` and `.json` files.  Pick a file and the
model is imported with full layer structure, metadata, and groups.

### Direct path (alternative)

If you already know the file path, replace the file-picker section with:

```python
#! python 3

import sys
sys.path.append(r'/path/to/fea_toolkit/src')

from fea_toolkit.io.s2k_parser import SAP2000Parser
from fea_toolkit.rhino import RhinoImporter

# .s2k file (default):
parser = SAP2000Parser('/path/to/model.s2k')
parser.parse()
md = parser.get_model_data()

# Or JSON export:
# parser = SAP2000Parser.from_json('/path/to/model.json')
# md = parser.get_model_data()

importer = RhinoImporter(md)
report = importer.run()
print(report)
```

### Save as a persistent script

Save the script to your Rhino scripts folder to run it anytime via
`RunPythonScript`:

```
~/Library/Application Support/McNeel/Rhinoceros/8.0/scripts/
    └── fea_toolkit_import.py
```

### Without a file — use the sample model

To test without a real SAP2000 file, build a ``SAPModelData`` programmatically:

.. note::
   For frame **extrusions** to work, the section must be created as a
   typed section with dimensions (e.g. ``ISection``, ``PipeSection``,
   ``RectangularSection``).  A plain ``Section`` base class has no
   profile definition and will only produce centreline lines.

```python
import sys
sys.path.append(r'/path/to/fea_toolkit/src')   # <-- required inside Rhino

from fea_toolkit.model.sap_data import (
    SAPModelData, Node, Restraint, Material,
    ISection, ShellSection, AreaElement, FrameElement,
)
from fea_toolkit.rhino import RhinoImporter

nodes = {
    "1": Node("1", 1, 0, 0, 0),
    "2": Node("2", 2, 5, 0, 0),
    "3": Node("3", 3, 5, 4, 0),
    "4": Node("4", 4, 0, 4, 0),
}
restraints = {"1": Restraint([1,1,1,1,1,1])}
materials = {"Steel": Material("Steel", "Steel", E_mod=2e11)}

# Typed section with dimensions -> enables 3-D extrusion
sections = {
    "UB300": ISection(
        "UB300", "I/Wide Flange", "Steel",
        A=8e-3, I33=1.2e-4, I22=4e-5, J=2e-6,
        depth=0.3, bf=0.15, tf=0.01, tw=0.006,
    ),
}
frames = {"1": FrameElement("1", 1, "1", "2")}
frame_assignments = {"1": "UB300"}
areas = {"1": AreaElement("1", 1, ["1","2","3","4"])}
area_sections = {"Slab200": ShellSection("Slab200", "Shell", "Concrete", thickness=0.2)}
area_assignments = {"1": "Slab200"}

md = SAPModelData(
    nodes=nodes, restraints=restraints, materials=materials,
    sections=dict(sections, **area_sections),
    frame_elements=frames, area_elements=areas,
    frame_assignments=frame_assignments, area_assignments=area_assignments,
    groups={}, frame_auto_mesh={},
)

importer = RhinoImporter(md)
report = importer.run()
```

---

## Layer Structure

The importer derives a **stage-namespaced** root from the model's pipeline
stage (pass ``root_layer=...`` to :meth:`RhinoImporter.run` to override),
so geometry from the two stages never overlays in the same layers:

```
SAP2000/
├── SAP/                              ← unsplit SAP2000 geometry
│   ├── Joints                        ← point objects
│   ├── Frames/
│   │   ├── Centreline/               ← line objects
│   │   │   ├── UB300                (coloured by section)
│   │   │   └── ...
│   │   └── Extrusion/                ← 3-D solids
│   │       ├── UB300                (same colour as centreline)
│   │       └── ...
│   └── Shells/
│       ├── Centreline/               ← planar Brep surfaces
│       │   ├── Slab200
│       │   └── ...
│       └── Extrusion/                ← extruded by thickness
│           ├── Slab200
│           └── ...
└── Mesh/                             ← meshed stage (split frames, subdivided areas)
    ├── Joints
    ├── Frames/  ├── Centreline/  └── Extrusion/
    └── Shells/  ├── Centreline/  └── Extrusion/
```

A ``SAPModelData`` import lands under ``SAP2000/SAP``; a ``MeshModel``
under ``SAP2000/Mesh``.  Pass ``root_layer="SAP2000"`` to ``run()`` for
the legacy flat tree.  Results overlays (deformed shapes, flags) live
under ``SAP2000/Results`` — see
[Rhino Attributes](rhino_attributes.md) for the full UserString
reference.

The centreline and extrusion layers let you toggle between a schematic
view and a detailed solid model by turning layer groups on/off.

## Geometry Representations

| Element | Centreline | Extrusion |
|---|---|---|
| **Joints** | `Point` | *(none)* |
| **Frames** | `Line` | Brep solid (swept section profile) |
| **Shells** | Planar `Brep` (tri/quad/N-gon) | Brep solid (face offset by thickness) |

### Frame Section Profiles Supported

| Section type | Extrusion profile |
|---|---|
| `I/Wide Flange` | I‑shape (web + flanges) |
| `Box/Tube` | Rectangular hollow section |
| `Pipe` | Circular tube |
| `Channel` | C‑shape |
| `Rectangular` | Solid rectangle |
| `Circle` | Solid cylinder |
| `General` / `SD Section` | *(not extruded — centreline only)* |

---

## Metadata (Rhino UserStrings)

Every object carries `SAP_*` (model) / `FEA_*` (stage) / `RES_*`
(results) attributes accessible via Rhino's `Properties → Notes` panel,
Grasshopper's `Hops` component, or Python.  The tables below summarise
the `SAP_*` set; the complete cross-stage reference (including `FEA_*`
and `RES_*`) lives in [Rhino Attributes](rhino_attributes.md).

### Joints

| Key | Example |
|---|---|
| `SAP_Type` | `Joint` |
| `SAP_JointID` | `1` |
| `SAP_X`, `SAP_Y`, `SAP_Z` | `0.0`, `0.0`, `0.0` |
| `SAP_Restraints` | `U1,U2,U3` |
| `SAP_Restraint_U1` | `True` |
| `SAP_Constraint` | `BODY` *(if constrained)* |

### Frames

| Key | Example |
|---|---|
| `SAP_Type` | `Frame` or `FrameExtrusion` |
| `SAP_FrameID` | `42` |
| `SAP_Section` | `UB300` |
| `SAP_JointI`, `SAP_JointJ` | `1`, `2` |
| `SAP_Material` | `Steel` |
| `SAP_Shape` | `I/Wide Flange` |
| `SAP_Area` | `0.008` |
| `SAP_Angle` | `0.0` |

### Shells

| Key | Example |
|---|---|
| `SAP_Type` | `Shell` or `ShellExtrusion` |
| `SAP_AreaID` | `1` |
| `SAP_Section` | `Slab200` |
| `SAP_NodeCount` | `4` |
| `SAP_JointIDs` | `1,2,3,4` |
| `SAP_Thickness` | `0.2` |
| `SAP_Material` | `C30/37` |

---

## Groups

### SAP2000 Groups

SAP2000 group definitions and assignments are recreated as Rhino groups.
Objects are coloured with the group colour from SAP2000.

Each object also stores a ``SAP_Groups`` UserString listing every
SAP2000 group it belongs to (comma-separated).  This allows filtering
by group in Grasshopper without using Rhino's group API:

```python
# Grasshopper Python: select objects in a specific SAP group
group_filter = "Moment Frame"
objects = [o for o in rs.AllObjects()
           if group_filter in (rs.GetUserText(o, "SAP_Groups") or "")]
```

### Selection Groups

A set of type-based, section-based, and shape-based groups is created
automatically by scanning the document for SAP metadata:

| Group pattern | Example | Contents |
|---|---|---|
| ``SAP_All_Frames`` | — | All frame centreline + extrusion objects |
| ``SAP_All_Shells`` | — | All shell centreline + extrusion objects |
| ``SAP_All_Joints`` | — | All joint point objects |
| ``SAP_Section_{name}`` | ``SAP_Section_UB300`` | Objects with that section name |
| ``SAP_Shape_{type}`` | ``SAP_Shape_I_Wide_Flange`` | Objects with that shape type |

These groups can be used in Rhino's ``SelectGroup`` command or in
Grasshopper's ``Group`` component for quick filtering.

---

## Selecting by UserString

Every object stores FEA metadata as Rhino UserStrings (see the
[Metadata](#metadata-rhino-userstrings) section above).  You can select
objects by their UserString values:

### In Rhino (command line)

```
SelUserText
Key: SAP_Section
Value: UB300
```

### In Grasshopper Python

```python
import rhinoscriptsyntax as rs

# Select all I-beam sections
ibeams = [o for o in rs.AllObjects()
          if rs.GetUserText(o, "SAP_Shape") == "I/Wide Flange"]
rs.SelectObjects(ibeams)

# Select objects in multiple SAP groups
groups_filter = {"Moment Frame", "Lateral"}
result = [o for o in rs.AllObjects()
          if set((rs.GetUserText(o, "SAP_Groups") or "").split(","))
             & groups_filter]
```

### In the Rhino Properties panel

1. Select an object
2. Open ``Properties → Notes``
3. All ``SAP_*`` keys and values are listed under User Text

---

## Joint Colour Coding

When `color_code_joints=True`, joint points are coloured by their
restraint type:

| Condition | Colour |
|---|---|
| Fully fixed (6 DOFs restrained) | Red |
| Pinned (3 translations restrained) | Blue |
| Roller (vertical translation only) | Green |
| Constrained (BODY constraint) | Purple |
| Free (no restraints) | LightGray |

Points that belong to a SAP2000 group are skipped (group colour takes
precedence).

---

## Configuring the Import

The `RhinoImporter.run()` method accepts these keyword arguments:

| Argument | Default | Description |
|---|---|---|
| `create_centreline` | `True` | Create points / lines / planar Breps |
| `create_extrusions` | `True` | Create 3‑D extrusion solids |
| `color_code_joints` | `True` | Colour joints by restraint type |
| `create_groups` | `True` | Create Rhino groups from SAP groups |
| `create_meshed` | `False` | Also import meshed geometry under a `Meshed` sub-tree |
| `root_layer` | `None` | Root layer path (default derived from stage: `SAP2000/Mesh` / `SAP2000/SAP`; pass `"SAP2000"` for the legacy flat tree) |
| `verbose` | `True` | Print progress to command line |

---

## Tip: Preserving Extrusions as Lightweight Objects

Rhino's `UseExtrusions` system setting controls whether extrusion
operations create lightweight objects or convert to polysurfaces:

- `UseExtrusions=Yes` (default) — shapes remain lightweight extrusions
  (recommended — smaller files, faster display).
- `UseExtrusions=No` — forces conversion to Brep polysurfaces
  (only needed if you plan to heavily manipulate sub-faces).

The `RhinoImporter` creates Brep polysurfaces (not lightweight
`Extrusion` objects) due to Rhino 8 Mac API differences.

---

## Results Visualisation (OpenSees → Rhino)

Analysis results (frame end forces, shell membrane forces, nodal
displacements) are written to a unified results file (``.npz`` **or**
``.h5`` — optionally a **stage file** that also stores the model) and
applied inside Rhino with a single call.

### Workflow

```
1. PARSING             SAP2000Parser → SAPModelData
2. SPLITTING           preprocess_model() → MeshModel
3. ANALYSIS            AnalysisBuilder.build_domain() + run_static_analysis()
4. EXPORT (unified)    write_model_stages("model.h5", sap=md, mesh=mm)
5. RHINO IMPORT        RhinoImporter(mesh_model).run()  ← SAP_*/FEA_* UserStrings
6. RHINO VISUALISE     apply_results("model.h5", stage="mesh") → colour + deform
```

### Step-by-step

**Outside Rhino** (CPython with OpenSees):

```python
from fea_toolkit.io.s2k_parser import SAP2000Parser
from fea_toolkit.opensees.preprocessor import preprocess_model
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
from fea_toolkit.io.stage_writer import write_model_stages

parser = SAP2000Parser("model.s2k")
parser.parse()
md = parser.get_model_data()

mm = preprocess_model(md, {"verbose": True})
builder = AnalysisBuilder(mm, {"verbose": True})
builder.build_domain()
builder.create_loads({"DEAD": 1.0})
static = builder.run_static_analysis()

write_model_stages("model_results.h5", sap=md, mesh=mm, fmt="h5")
```

The stage file holds **both** model stages, so the Rhino side reads a
single file for geometry and results.

**Inside Rhino** — one call for frame colouring, shell colouring and a
deformed-shape overlay:

```python
#! python 3
# r: numpy==2.0.2
# r: h5py==3.13.0
import sys
sys.path.insert(0, r"/path/to/fea_toolkit/src")

from fea_toolkit.rhino.results import apply_results

summary = apply_results(
    r"C:/models/model_results.h5",
    stage="mesh",          # geometry stage the imported objects match
    frames=True,           # colour frame objects by static Mz (local)
    quantity="Mz",
    case="DEAD",
    shells=True,           # colour shell objects by pushover Nx (last step)
    shell_quantity="Nx",
    shell_direction="+X",
    deformed=True,         # deformed-shape overlay (static, auto-scaled)
    deformed_source="static",
    layer_filter="SAP2000/Mesh/*",   # only touch the meshed geometry
)
print(summary)
```

The individual helpers are also public:

| Function | Purpose |
|---|---|
| ``colour_from_npz(path, quantity=...)`` | Colour frame objects by a static quantity |
| ``colour_shells_from_results(path, quantity="Nx", ...)`` | Colour shell objects by a pushover in-plane quantity |
| ``create_deformed_geometry(path, source_type=...)`` | Overlay deformed frame lines + shell quads |
| ``create_result_flags(path, quantity=...)`` | 3-D flag annotations for peak response values |
| ``colour_frame_by_npz_ratio(path, numerator, denominator)`` | Colour by a force ratio |

All of these accept a **plain results file, a stage file, or an HDF5
file** — format and stage are auto-detected.  ``aggregate_parents=True``
maps child-element results back to parent SAP IDs, so SAP-stage geometry
can be coloured from meshed-stage results.

### Controlling result colouring

The imported Mesh geometry is recoloured *in place* by default.  Because
the diverging scale is auto-normalised to the data's own min/max, only
the global extreme members saturate while the bulk maps to a pale/white
tint — which often reads as "uncoloured".  Two options control this:

| Option | Values | Effect |
|---|---|---|
| ``colour_members`` | ``True`` (default) / ``False`` | **Master switch** for recolouring the frame/shell geometry.  ``False`` leaves the Mesh members in their section/layer colours; the deformed overlay and the result flags are still created. |
| ``scale_mode`` | ``"continuous"`` (default) / ``"stepped"`` | ``"continuous"`` — smooth diverging blue→white→red ramp (only the global min/max saturate).  ``"stepped"`` — ``n_steps`` discrete bands so mid-range magnitudes get clearly distinct colours. |
| ``n_steps`` | odd int ≥ 3 (default ``9``) | Band count for ``scale_mode="stepped"``.  Zero is always the central (white) band; even values are bumped up to the next odd count. |
| ``clip_pct`` | ``0`` (default) / ``0 < p < 50`` | Percent clipped off each end of the value range.  ``0`` uses the raw min/max — a few extreme members then wash the bulk out to near-white.  Clipping to the inner percentiles spreads the scale over the bulk (values beyond the clip saturate at the anchors); genuinely-zero members stay white. |

Leave the Mesh geometry in its section/layer colours:

```python
summary = apply_results(
    r"C:/models/model_results.h5",
    stage="mesh",
    deformed=True,
    deformed_source="pushover",
    colour_members=False,   # keep section/layer colours on the Mesh members
)
```

Stepped (discrete) colour scale when member colouring is on:

```python
summary = apply_results(
    r"C:/models/model_results.h5",
    stage="mesh",
    frames=True,
    shells=True,
    deformed=True,
    colour_members=True,    # (default) colour the Mesh members
    scale_mode="stepped",   # discrete bands instead of a smooth ramp
    n_steps=9,              # band count (odd ≥ 3)
    clip_pct=10,            # clip the range to the 10th–90th percentiles
)
```

The same ``scale_mode`` / ``n_steps`` / ``clip_pct`` arguments are accepted
by the individual helpers — ``colour_from_npz()``,
``colour_frames_from_results()``, ``colour_shells_from_results()``, and
``colour_frame_by_npz_ratio()``.
The moment-diagram flags (``create_result_flags()``) always use the
symmetric ``±max|value|`` diverging scale and are unaffected.

### Unified file contents

The full unified schema is documented in
[Results Schema](results_schema.md).  Key arrays for Rhino colouring:

| Array | Description |
|---|---|
| ``node_x``, ``node_y``, ``node_z`` | Node coordinates (N_node) |
| ``frame_sap_id`` | Frame SAP IDs — matches the ``SAP_FrameID`` UserString |
| ``frame_node_i``, ``frame_node_j`` | Frame endpoint node indices |
| ``shell_sap_id`` | Shell/area SAP IDs — matches the ``SAP_AreaID`` UserString |
| ``static/{case}/mz_i_local`` | Local I-end moments (also fx/fy/fz/mx/my, _j) |
| ``static/{case}/node_dx`` … ``node_dz`` | Nodal displacements |
| ``pushover/{dir}/shell_Nx`` … ``shell_Mxy`` | Pushover shell membrane forces (N_step × N_shell) |
| ``pushover/{dir}/node_disp_x`` … ``node_disp_z`` | Pushover nodal displacements |

### Displaced shape

``create_deformed_geometry()`` builds a displaced copy of the frame
lines and shell quads on a dedicated
``SAP2000/Results/Deformed/{label}`` layer.  Use ``deformed_source=
"static"`` (default, ``case=...``), ``"modal"`` (``mode=...``),
``"rs"``, or ``"pushover"`` (``direction=...``, ``step=...``).  *scale*
defaults to an automatic value (5 % of the largest model dimension per
unit displacement):

```python
from fea_toolkit.rhino.results import create_deformed_geometry

n = create_deformed_geometry(
    "model_results.h5",
    source_type="modal",
    mode=1,
    scale=50,          # exaggerate 50× (None → auto)
)
```

### Notes

- NumPy ships with Rhino 8 CPython; **h5py** is required only for
  ``.h5`` files (see [Required packages](#required-packages)).
- Section responses (``sec_*`` arrays) are only present when the analysis
  runner is asked to collect them.
- **End forces are stored in global coordinates**.  For local forces
  (e.g. local ``Mz`` for major-axis bending, independent of member
  orientation) the colouring helpers prefer the ``*_i_local`` /
  ``_j_local`` arrays automatically when ``use_local=True`` (the default).
- The deformed overlay is **file-driven**: it works whether or not the
  original geometry was imported into the document.

## Technical Notes

### Profile Winding Convention

All section profiles in the ``geometry_v2`` module (**Rectangular**, **I**,
**Box**, **Channel**) use a **clockwise (CW)** winding order when viewed
from the +Z direction. This is required for ``Extrusion.Create`` to
extrude in the correct direction after the axis transform.

The signed area of each profile must be **negative**:

```python
area = sum(xi * yj - xj * yi for i, j in pairs) / 2.0
# area < 0  ⟹  CW winding
```

If a profile is wound counter-clockwise (CCW, positive signed area),
the extrusion direction is reversed because ``TryGetPlane()`` returns
a plane normal in the −Z direction, causing ``Extrusion.Create`` to
place the profile at the *top* instead of the *bottom* of the extrusion
path.

The tests in ``tests/test_rhino.py::TestProfilePoints`` verify that all
profiles maintain CW winding — they will fail if a profile's winding
direction is inadvertently changed.
