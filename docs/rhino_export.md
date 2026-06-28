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

import sys
sys.path.append(r'/path/to/fea_toolkit/src')   # <-- adjust to your setup

import Rhino
from fea_toolkit.io.s2k_parser import SAP2000Parser
from fea_toolkit.rhino.importer import RhinoImporter

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
from fea_toolkit.rhino.importer import RhinoImporter

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
from fea_toolkit.rhino.importer import RhinoImporter

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
