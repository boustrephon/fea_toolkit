"""
Rhino group creation from SAP2000 group definitions and assignments.

Three kinds of groups are created:

* **SAP2000 groups** -- named groups from the SAP2000 model.  Each
  object also gets a ``SAP_Groups`` UserString listing all group names.
* **Selection groups** -- ``SAP_All_Frames``, ``SAP_All_Shells``,
  ``SAP_All_Joints`` for quick type-based selection.
* **Section / shape groups** -- ``SAP_Section_{name}``,
  ``SAP_Shape_{type}`` for filtering by cross-section.
"""

import typing as t

from ..model.sap_data import SAPModelData
from .colors import get_sap2000_color


def _ensure_rhino():
    """Lazy-import Rhino modules."""
    try:
        import Rhino  # noqa: F401
        import scriptcontext as sc
        import Rhino.DocObjects as rd
        return sc, rd
    except ImportError:
        raise RuntimeError(
            "Rhino modules are not available. This code must run inside "
            "Rhinoceros 3D (IronPython)."
        )


def _sanitise_group_name(name: str) -> str:
    """Replace characters that are invalid in Rhino group names."""
    for ch in [":", "/", "\\", ".", "*", "?", "\"", "<", ">", "|"]:
        name = name.replace(ch, "_")
    return name


def create_rhino_group(group_name: str, object_ids: t.List[str],
                       color=None) -> bool:
    """Create or replace a Rhino group with the given objects. ..."""
    sc, rd = _ensure_rhino()
    doc = sc.doc
    group_index = doc.Groups.Find(group_name)
    if group_index >= 0:
        doc.Groups.Delete(group_index)
    if not object_ids:
        return False
    group_index = doc.Groups.Add(group_name)
    added = 0
    for oid in object_ids:
        obj = doc.Objects.Find(oid)
        if obj is None:
            continue
        doc.Groups.AddToGroup(group_index, obj.Id)
        added += 1
        if color is not None:
            attrs = obj.Attributes
            attrs.ObjectColor = color
            attrs.ColorSource = rd.ObjectColorSource.ColorFromObject
            attrs.SetUserString("SAP_Group", group_name)
            obj.CommitChanges()
    return added > 0


def _stamp_group_membership(object_ids: t.List[str], group_name: str) -> None:
    """Append *group_name* to the ``SAP_Groups`` UserString on each object."""
    sc, rd = _ensure_rhino()
    doc = sc.doc
    for oid in object_ids:
        obj = doc.Objects.Find(oid)
        if obj is None:
            continue
        attrs = obj.Attributes
        existing = attrs.GetUserString("SAP_Groups") or ""
        names = [n.strip() for n in existing.split(",") if n.strip()]
        if group_name not in names:
            names.append(group_name)
            attrs.SetUserString("SAP_Groups", ",".join(names))
            obj.CommitChanges()


# ========================================================================
# SAP2000 group creation
# ========================================================================

def create_sap_groups(md: SAPModelData, joint_object_ids: t.List[str],
                      frame_object_ids: t.List[str],
                      shell_object_ids: t.List[str]) -> int:
    """Create Rhino groups from SAP2000 group definitions and assignments.

    Each object also gets a ``SAP_Groups`` UserString listing every
    SAP2000 group it belongs to (comma-separated).
    """
    sc, rd = _ensure_rhino()
    doc = sc.doc

    # Build lookups: SAP element ID -> list of Rhino object IDs
    sap_frames: t.Dict[str, t.List[str]] = {}
    sap_shells: t.Dict[str, t.List[str]] = {}
    sap_joints: t.Dict[str, t.List[str]] = {}

    for oid in frame_object_ids:
        obj = doc.Objects.Find(oid)
        if obj is None:
            continue
        fid = obj.Attributes.GetUserString("SAP_FrameID")
        if fid:
            sap_frames.setdefault(fid, []).append(oid)
    for oid in shell_object_ids:
        obj = doc.Objects.Find(oid)
        if obj is None:
            continue
        aid = obj.Attributes.GetUserString("SAP_AreaID")
        if aid:
            sap_shells.setdefault(aid, []).append(oid)
    for oid in joint_object_ids:
        obj = doc.Objects.Find(oid)
        if obj is None:
            continue
        jid = obj.Attributes.GetUserString("SAP_JointID")
        if jid:
            sap_joints.setdefault(jid, []).append(oid)

    groups_created = 0
    for gname, group in md.groups.items():
        member_ids: t.List[str] = []
        group_color = get_sap2000_color(group.color, None)
        for ref in group.objects:
            parts = ref.split(":", 1)
            if len(parts) != 2:
                continue
            obj_type, obj_label = parts
            oids = None
            if obj_type.lower() == "frame":
                oids = sap_frames.get(obj_label)
            elif obj_type.lower() == "area":
                oids = sap_shells.get(obj_label)
            elif obj_type.lower() == "joint":
                oids = sap_joints.get(obj_label)
            if oids:
                member_ids.extend(oids)
        if member_ids:
            create_rhino_group(gname, member_ids, color=group_color)
            _stamp_group_membership(member_ids, gname)
            groups_created += 1
    return groups_created


# ========================================================================
# Selection groups
# ========================================================================

def create_selection_groups() -> None:
    """Create type, section, and shape groups by scanning SAP metadata.

    Groups: ``SAP_All_Frames``, ``SAP_All_Shells``, ``SAP_All_Joints``,
    ``SAP_Section_{name}``, ``SAP_Shape_{type}``.
    """
    sc, rd = _ensure_rhino()
    doc = sc.doc

    frames: t.List[str] = []
    shells: t.List[str] = []
    joints: t.List[str] = []
    by_section: t.Dict[str, t.List[str]] = {}
    by_shape: t.Dict[str, t.List[str]] = {}

    for obj in doc.Objects:
        try:
            attrs = obj.Attributes
            sap_type = attrs.GetUserString("SAP_Type")
            if sap_type is None:
                continue
            if "Frame" in sap_type:
                frames.append(obj.Id)
            elif "Shell" in sap_type:
                shells.append(obj.Id)
            elif sap_type == "Joint":
                joints.append(obj.Id)

            sec = attrs.GetUserString("SAP_Section")
            if sec:
                by_section.setdefault(sec, []).append(obj.Id)
            shape = attrs.GetUserString("SAP_Shape")
            if shape:
                by_shape.setdefault(shape, []).append(obj.Id)
        except Exception:
            continue

    if frames:
        create_rhino_group("SAP_All_Frames", frames)
    if shells:
        create_rhino_group("SAP_All_Shells", shells)
    if joints:
        create_rhino_group("SAP_All_Joints", joints)
    for sec_name, oids in by_section.items():
        create_rhino_group("SAP_Section_{}".format(_sanitise_group_name(sec_name)), oids)
    for shape_name, oids in by_shape.items():
        create_rhino_group("SAP_Shape_{}".format(_sanitise_group_name(shape_name)), oids)
