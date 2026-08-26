---
title: "Rhino Attributes Reference"
description: "The SAP_*, FEA_* and RES_* Rhino UserString namespaces stamped on imported geometry and results overlays."
status: "complete"
tags: [rhino, metadata, userstrings, attributes]
category: [export-viz]
related: [rhino_export.md, results_schema.md]
---
# Rhino Attributes Reference

Every object the toolkit creates in a Rhino document carries **UserText /
UserString** attributes in three namespaces:

| Namespace | Meaning | Set by |
|---|---|---|
| `SAP_*` | SAP2000 model properties (IDs, sections, materials) | geometry creation |
| `FEA_*` | Pipeline stage & OpenSees topology (tags, parents) | geometry creation |
| `RES_*` | Analysis results overlays (deformed shapes, flags) | results helpers |

They are readable from Rhino's `Properties → Notes` panel, from Python
(`rh_obj.Attributes.GetUserString("SAP_FrameID")`), and from Grasshopper
(`rs.GetUserText(obj, "SAP_FrameID")`).

---

## Stage namespacing

Importing the two pipeline stages produces two independent layer trees
(`SAP2000/SAP/...` and `SAP2000/Mesh/...`).  The `FEA_Stage` attribute
records which stage an object belongs to (`"sap"` / `"mesh"`), so
filters never confuse e.g. an unsplit frame with its meshed children.

The **ID namespaces** are also stage-aware:

| Attribute | SAP stage | Mesh stage |
|---|---|---|
| `SAP_*` IDs | Original SAP2000 labels (`SAP_FrameID` = `"1"`) | Child labels (`SAP_FrameID` = `"1-0"`) |
| `FEA_*` tags | OpenSees tags before splitting | OpenSees tags after splitting |

---

## `SAP_*` attributes

### Joints

| Key | Example | Notes |
|---|---|---|
| `SAP_Type` | `Joint` | object kind |
| `SAP_JointID` | `1` | SAP node label |
| `SAP_X` / `SAP_Y` / `SAP_Z` | `0.0` | node coordinates |
| `SAP_Restraints` | `U1,U2,U3` | active DOF restraints |
| `SAP_Restraint_{dof}` | `True` | one per DOF (`U1`…`U3`, `R1`…`R3`) |
| `SAP_Constraint` | `BODY` | body constraint name, if constrained |

### Frames

| Key | Example | Notes |
|---|---|---|
| `SAP_Type` | `Frame` or `FrameExtrusion` | centreline vs solid |
| `SAP_FrameID` | `42` | **match key** for result colouring |
| `SAP_Section` | `UB300` | section name |
| `SAP_JointI` / `SAP_JointJ` | `1`, `2` | end-node SAP labels |
| `SAP_Material` | `Steel` | material name |
| `SAP_Shape` | `I/Wide Flange` | section shape string |
| `SAP_Area` | `0.008` | cross-section area |
| `SAP_Angle` | `0.0` | local-axis roll angle |

### Shells

| Key | Example | Notes |
|---|---|---|
| `SAP_Type` | `Shell` or `ShellExtrusion` | centreline vs solid |
| `SAP_AreaID` | `1` | **match key** for shell result colouring |
| `SAP_Section` | `Slab200` | section name |
| `SAP_NodeCount` | `4` | number of corners |
| `SAP_JointIDs` | `1,2,3,4` | corner-node SAP labels |
| `SAP_Thickness` | `0.2` | shell thickness |
| `SAP_Material` | `C30/37` | material name |

### Groups

| Key | Example | Notes |
|---|---|---|
| `SAP_Groups` | `Moment Frame,Lateral` | comma-separated SAP2000 groups |
| `SAP_Group` | `Moment Frame` | set on objects that own a group colour |

---

## `FEA_*` attributes

Set on **every** geometry object (joints, frames, shells):

| Key | Example | Notes |
|---|---|---|
| `FEA_Stage` | `sap` / `mesh` | pipeline stage of the object |
| `FEA_Kind` | `Joint` / `Frame` / `Shell` | object kind |
| `FEA_NodeTag` | `3` | OpenSees node tag (joints) |
| `FEA_ElemTag` | `7` | OpenSees element tag (frames/shells) |
| `FEA_ParentID` | `1` | parent element ID (meshed children only) |

`FEA_ParentID` is only present on split/meshed elements; combined with
`FEA_Stage == "mesh"` it distinguishes child elements from their parents.

---

## `RES_*` attributes

Set by the results helpers in
[`fea_toolkit.rhino.results`](rhino_export.md#results-visualisation-opensees--rhino)
and `colour_from_npz`.

### Deformed-shape overlays (`create_deformed_geometry`)

| Key | Example | Notes |
|---|---|---|
| `RES_Kind` | `Frame` / `Shell` | overlay object kind |
| `RES_Deformed` | `static/DEAD` | displacement source label |
| `SAP_FrameID` / `SAP_AreaID` | `1-0` | original element ID (also stamped) |

Source labels follow `static/{case}`, `modal/{n}` (1-based),
`rs`, or `pushover/{direction}/step{n}`.

### Result flags (`create_result_flags`)

| Key | Example | Notes |
|---|---|---|
| `SAP_FrameID` | `1` | element the flag belongs to |
| `{quantity}_i` / `{quantity}_j` | `-12.34` | I- and J-end values (e.g. `Mz_i`) |

### Coloured objects

Colouring (`colour_from_npz`, `colour_shells_from_results`) does **not**
add new attributes — it sets `ObjectColor` + `ColorSource` on the
existing geometry, keyed off the `SAP_FrameID` / `SAP_AreaID` attributes.

---

## Filtering examples

```python
# Inside Rhino — select all meshed child frames
objs = [o for o in sc.doc.Objects
        if o.Attributes.GetUserString("FEA_Stage") == "mesh"
        and o.Attributes.GetUserString("FEA_Kind") == "Frame"
        and o.Attributes.GetUserString("FEA_ParentID")]

# Grasshopper — select shells by section
import rhinoscriptsyntax as rs
walls = [o for o in rs.AllObjects()
         if rs.GetUserText(o, "SAP_Section") == "Slab200"]
```
