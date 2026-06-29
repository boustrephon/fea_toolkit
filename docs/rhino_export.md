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
   (e.g. ``/usr/local/bin/python3`` on Mac, or the Python from your
   ``venv_opensees`` at
   ``/Users/andrew/Projects/OpenSeesPy/venv_opensees/bin/python3``)
3. Click ``OK`` and restart the PythonScript editor

All examples in this guide assume CPython 3.9+.

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

```
SAP2000/
├── Joints                          ← point objects
├── Frames/
│   ├── Centreline/                 ← line objects
│   │   ├── Default
│   │   ├── UB300                  (coloured by section)
│   │   └── ...
    └── Extrusion/                  ← 3-D Brep solids
│       ├── Default
│       ├── UB300                  (same colour as centreline)
│       └── ...
└── Shells/
    ├── Centreline/                 ← planar Brep surfaces
    │   ├── Default
│   │   ├── Slab200
│   │   └── ...
    └── Extrusion/                  ← extruded by thickness
        ├── Default
│       ├── Slab200
│       └── ...
```

The centreline and extrusion layers let you toggle between a schematic
view and a detailed solid model by turning layer groups on/off.

---

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

Every object carries `SAP_*` attributes accessible via Rhino's
`Properties → Notes` panel, Grasshopper's `Hops` component, or Python:

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

Analysis results from `OpenSeesBuilder` (end forces, section forces,
fiber stresses, nodal displacements) can be exported to a **NumPy .npz**
file and loaded back into Rhino for colour-coding geometry.

### Workflow

```
1. PARSING             SAP2000Parser → SAPModelData
2. SPLITTING           geometry.split_elements() → sub-elements at intermediate nodes
3. ANALYSIS            OpenSeesBuilder.build() + run_static_analysis()
4. EXPORT (.npz)       builder.export_results_to_npz("results.npz", results)
5. RHINO IMPORT        RhinoImporterV2(md).run()  ← unsplit geometry with SAP_* UserStrings
6. RHINO VISUALISE     np.load("results.npz") → colour by force/stress/displacement
```

### Step-by-step

**Outside Rhino** (CPython with OpenSees):

```python
from fea_toolkit.io.s2k_parser import SAP2000Parser
from fea_toolkit.opensees.builder import OpenSeesBuilder

parser = SAP2000Parser("model.s2k")
parser.parse()
md = parser.get_model_data()

builder = OpenSeesBuilder(md, {"verbose": True})
builder.build()
results = builder.run_static_analysis()

# Basic export (end forces + displacements)
builder.export_results_to_npz("results.npz", results)

# With section and fiber data (requires fiber sections)
builder.export_results_to_npz("results_fiber.npz", results,
    section_responses={
        "section_forces": True,   # N, Mz, My, Vz, Vy, T at each IP
        "fiber_stress": True,     # max/min fiber stress per IP
        "fiber_strain": True,     # max/min fiber strain per IP
    })
```

**Inside Rhino** (load .npz and colour by results):

```python
#! python 3

import sys
sys.path.append(r'/path/to/fea_toolkit/src')

import numpy as np
import Rhino
import scriptcontext as sc
import Rhino.DocObjects as rd

data = np.load("results.npz")

# ── Colour extrusions by major-axis moment (Mz) ──────────────────────
# Each row in data['sub_sap_ids'] belongs to one sub-element.
# Match by SAP_FrameID on Rhino objects.

# Build a lookup: SAP FrameID → list of (t_start, t_end, Mz)
from collections import defaultdict
elem_map = defaultdict(list)
for i in range(len(data["sub_sap_ids"])):
    sap_id = data["sub_sap_ids"][i]
    mz = data["sub_mz_j"][i]  # J-end moment (or use sub_mx_i etc.)
    t0 = data["sub_t_start"][i]
    t1 = data["sub_t_end"][i]
    elem_map[sap_id].append((t0, t1, mz))

# Scan Rhino objects and colour by moment
for obj in sc.doc.Objects:
    attrs = obj.Attributes
    sap_id = attrs.GetUserString("SAP_FrameID")
    if sap_id is None or sap_id not in elem_map:
        continue

    segs = elem_map[sap_id]
    # Use the absolute max moment across sub-elements
    max_mz = max(abs(s[2]) for s in segs)

    # Map to a colour gradient (blue=0 → red=max)
    # (simplified — use a proper colour map for real work)
    t = min(max_mz / 1e5, 1.0)  # normalise; adjust scale to your model
    r = int(255 * t)
    b = int(255 * (1 - t))
    colour = System.Drawing.Color.FromArgb(r, 0, b)

    attrs.ObjectColor = colour
    attrs.ColorSource = rd.ObjectColorSource.ColorFromObject
    obj.CommitChanges()

sc.doc.Views.Redraw()
```

### .npz file contents

When loaded with ``np.load("results.npz", allow_pickle=True)``, the file
contains:

**Frame sub‑elements** (one row per sub‑element):

| Array | Type | Description |
|---|---|---|
| `sub_elem_tags` | int32 | OpenSees `elem_tag` |
| `sub_sap_ids` | object (str) | Original SAP FrameID, matches `SAP_FrameID` UserString |
| `sub_t_start`, `sub_t_end` | float64 | Parametric position `t ∈ [0,1]` along the original SAP frame |
| `sub_fx_i` … `sub_mz_i` | float64 | I‑end forces in **global** coordinates (Fx, Fy, Fz, Mx, My, Mz) |
| `sub_fx_j` … `sub_mz_j` | float64 | J‑end forces in **global** coordinates |

**Nodes** (one row per model node):

| Array | Type | Description |
|---|---|---|
| `node_tags` | int32 | OpenSees `node_tag` |
| `node_sap_ids` | object (str) | SAP node ID, matches `SAP_JointID` UserString |
| `node_x`, `node_y`, `node_z` | float64 | Original nodal coordinates |
| `node_dx`, `node_dy`, `node_dz` | float64 | Nodal displacements |

**Section responses** (one row per integration point, only present when
`section_responses` is provided):

| Array | Description |
|---|---|
| `sec_ip` | Integration point index (1‑based) |
| `sec_sub_idx` | Index into the sub-element arrays above |
| `sec_N` … `sec_T` | Section forces in **local** coordinates (N, Mz, My, Vz, Vy, T) |
| `sec_sig_max` / `sec_sig_min` | Max/min fiber stress at this IP |
| `sec_eps_max` / `sec_eps_min` | Max/min fiber strain at this IP |

**Metadata**:

| Array | Description |
|---|---|
| `force_unit` | e.g. `"N"`, `"kN"` |
| `length_unit` | e.g. `"m"`, `"mm"` |

### Displaced shape

To visualise the displaced shape, move Rhino joint points by their
displacements and update frame objects accordingly:

```python
import Rhino.Geometry as rg

data = np.load("results.npz")

# Build node lookup: SAP node ID → (dx, dy, dz)
disp_map = {}
for i in range(len(data["node_sap_ids"])):
    disp_map[data["node_sap_ids"][i]] = (
        data["node_dx"][i],
        data["node_dy"][i],
        data["node_dz"][i],
    )

# Move joint points
for obj in sc.doc.Objects:
    if obj.Attributes.GetUserString("SAP_Type") != "Joint":
        continue
    sap_id = obj.Attributes.GetUserString("SAP_JointID")
    if sap_id not in disp_map:
        continue
    dx, dy, dz = disp_map[sap_id]
    pt = obj.Geometry.Location
    new_pt = rg.Point3d(pt.X + dx, pt.Y + dy, pt.Z + dz)
    sc.doc.Objects.ModifyGeometry(obj.Id, lambda g: g.Morph([(pt, new_pt)]))
```

### Scaling factor for visualisation

Structural displacements are often millimetres on a metre-scale model.
Apply a scale factor for visibility:

```python
scale = 50  # exaggerate displacements 50×
dx, dy, dz = disp_map[sap_id]
new_pt = rg.Point3d(pt.X + dx * scale, pt.Y + dy * scale, pt.Z + dz * scale)
```

### Notes

- NumPy ships with Rhino 8 CPython — no additional packages required.
- The `.npz` is a compressed ZIP file; use `allow_pickle=True` when
  loading because string arrays are stored as Python objects.
- Section responses (`sec_*` arrays) are only present when
  `section_responses` is passed to `export_results_to_npz`.
- For pushover results, export the final step's forces/displacements
  using the same method after `run_pushover_analysis()` returns.
- **End forces in the NPZ are in global coordinates**. To get local
  forces for colour‑coding (e.g. local Mz for major‑axis bending,
  independent of member orientation), transform using the element's
  local axes — see ``_get_local_end_forces()`` in ``viz.py`` for the
  rotation matrix approach, or use the ``plot_static_moment_3d`` and
  ``plot_static_force_diagram`` functions which handle this automatically.

---

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
