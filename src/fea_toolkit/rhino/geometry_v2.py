"""
Rhino geometry creation — ``rg``-based lightweight Extrusion version.

Uses ``import Rhino.Geometry as rg`` throughout (the standard CPython
convention for Rhino 8) and ``rg.Extrusion.Create()`` to produce true
lightweight Extrusion objects.

All functions have the same signatures as their counterparts in
``geometry.py`` so they can be swapped via the importer.
"""

import typing as t
import math
import sys

import Rhino  # noqa: F401
import scriptcontext as sc
import Rhino.DocObjects as rd
import Rhino.Geometry as rg

from ..model.sap_data import SAPModelData
from ..model.sap_data import (
    ISection, PipeSection, BoxSection, ChannelSection,
    RectangularSection, CircularSection, GeneralSection, ShellSection,
)


# ========================================================================
# Section profile builders (XY plane for Extrusion.Create)
# ========================================================================
# Extrusion.Create() extrudes along Z, so profiles are in the XY plane.
# The convention: x = width (→ element y_axis), y = depth (→ element z_axis)

def _profile_rect(depth: float, bf: float) -> t.List[t.Tuple[float, float]]:
    """Solid rectangular profile in the XY plane."""
    h, w = depth / 2.0, bf / 2.0
    return [(-w, -h), (-w, h), (w, h), (w, -h)]


def _profile_i(depth: float, bf: float, tf: float, tw: float
               ) -> t.List[t.Tuple[float, float]]:
    """I/Wide-flange profile in the XY plane."""
    h = depth / 2.0
    w = bf / 2.0
    wi = tw / 2.0
    fi = h - tf
    return [
        (-w, -h), (w, -h),
        (w, -fi), (wi, -fi),
        (wi, fi), (w, fi),
        (w, h), (-w, h),
        (-w, fi), (-wi, fi),
        (-wi, -fi), (-w, -fi),
    ]


def _profile_box(depth: float, bf: float, tf: float, tw: float
                 ) -> t.List[t.Tuple[float, float]]:
    """Box/RHS profile in the XY plane."""
    h = depth / 2.0
    w = bf / 2.0
    hi = h - tf
    wi = w - tw
    return [
        (-w, -h), (w, -h), (w, h), (-w, h),
        (-w, hi), (wi, hi), (wi, -hi), (-w, -hi),
    ]


def _profile_channel(depth: float, bf: float, tf: float, tw: float
                     ) -> t.List[t.Tuple[float, float]]:
    """Channel/C-section profile in the XY plane."""
    h = depth / 2.0
    w = bf / 2.0
    fi = h - tf
    wi = tw / 2.0
    return [
        (-w, -h), (w, -h),
        (w, -fi), (wi, -fi),
        (wi, fi), (w, fi),
        (w, h), (-w, h),
    ]


def _build_profile_curve(sec) -> t.Optional[t.Any]:
    """Build a closed profile curve for ``Extrusion.Create``.

    Returns:
        A ``rg.PolylineCurve`` for polygonal sections,
        a ``("circle", radius)`` tuple for circular sections,
        or ``None`` if the section cannot be profiled.
    """
    pts_2d = None
    is_circle = False
    radius = 0.0

    if isinstance(sec, RectangularSection):
        pts_2d = _profile_rect(sec.depth, sec.bf)
    elif isinstance(sec, CircularSection):
        is_circle = True
        radius = sec.diameter / 2.0
    elif isinstance(sec, ISection):
        if sec.depth > 0 and sec.bf > 0:
            pts_2d = _profile_i(sec.depth, sec.bf, sec.tf, sec.tw)
    elif isinstance(sec, PipeSection):
        if sec.od > 0:
            is_circle = True
            radius = sec.od / 2.0
    elif isinstance(sec, BoxSection):
        if sec.depth > 0 and sec.bf > 0:
            pts_2d = _profile_box(sec.depth, sec.bf, sec.tf, sec.tw)
    elif isinstance(sec, ChannelSection):
        if sec.depth > 0 and sec.bf > 0:
            pts_2d = _profile_channel(sec.depth, sec.bf, sec.tf, sec.tw)

    # Fallback for catalogue sections with zero dimensions
    if pts_2d is None and not is_circle:
        side = (sec.A ** 0.5) if sec.A > 0 else 0.1
        pts_2d = _profile_rect(side, side)

    if is_circle:
        if radius <= 0:
            radius = ((sec.A / 3.14159) ** 0.5) if sec.A > 0 else 0.05
        return ("circle", radius)

    if pts_2d is None:
        return None

    pts_3d = [rg.Point3d(x, y, 0) for x, y in pts_2d]
    pl = rg.Polyline(pts_3d)
    pl.Add(pts_3d[0])
    return rg.PolylineCurve(pl)


# ========================================================================
# Local axes (mirrors ``get_SAP_vecxz``)
# ========================================================================

def _local_axes(p_i: rg.Point3d, p_j: rg.Point3d,
                angle_deg: float = 0.0
                ) -> t.Optional[t.Tuple[rg.Vector3d, rg.Vector3d, rg.Vector3d]]:
    """Compute local (x, y, z) unit vectors — matches OpenSees convention."""
    x_axis = rg.Vector3d(p_j.X - p_i.X, p_j.Y - p_i.Y, p_j.Z - p_i.Z)
    length = x_axis.Length
    if length < 1e-12:
        return None
    x_axis.Unitize()

    global_y = rg.Vector3d(0, 1, 0)
    global_z = rg.Vector3d(0, 0, 1)
    cos_sim = x_axis * global_z

    if abs(cos_sim) > 0.9999:
        vecxz = rg.Vector3d(global_y.X, global_y.Y, global_y.Z)
        if cos_sim < 0:
            vecxz = -vecxz
    else:
        vecxz = rg.Vector3d.CrossProduct(x_axis, global_z)
        vecxz.Unitize()

    if abs(angle_deg) > 1e-6:
        theta = math.radians(angle_deg)
        c, s = math.cos(theta), math.sin(theta)
        vecxz = rg.Vector3d(
            vecxz.X * c + (x_axis.Y * vecxz.Z - x_axis.Z * vecxz.Y) * s,
            vecxz.Y * c + (x_axis.Z * vecxz.X - x_axis.X * vecxz.Z) * s,
            vecxz.Z * c + (x_axis.X * vecxz.Y - x_axis.Y * vecxz.X) * s,
        )
        proj = (vecxz * x_axis) * x_axis
        vecxz = vecxz - proj
        vecxz.Unitize()

    y_axis = rg.Vector3d.CrossProduct(vecxz, x_axis)
    y_axis.Unitize()
    z_axis = rg.Vector3d(vecxz.X, vecxz.Y, vecxz.Z)
    return x_axis, y_axis, z_axis


# ========================================================================
# Joint / Node geometry
# ========================================================================

def create_joint_points(
    md: SAPModelData,
    joint_layer_index: int,
) -> t.Tuple[int, t.List[str]]:
    """Create point objects for each node — same API as ``geometry.py``."""
    doc = sc.doc
    count = 0
    obj_ids: t.List[str] = []

    for nid, node in md.nodes.items():
        try:
            point = rg.Point3d(node.x, node.y, node.z)
            attr = rd.ObjectAttributes()
            attr.LayerIndex = joint_layer_index
            attr.Name = "SAP_Joint_{}".format(nid)

            obj_id = doc.Objects.AddPoint(point, attr)
            if obj_id is None:
                continue

            count += 1
            obj_ids.append(obj_id)
            obj = doc.Objects.Find(obj_id)
            if obj is None:
                continue

            attrs = obj.Attributes
            attrs.SetUserString("SAP_Type", "Joint")
            attrs.SetUserString("SAP_JointID", str(nid))
            attrs.SetUserString("SAP_X", str(node.x))
            attrs.SetUserString("SAP_Y", str(node.y))
            attrs.SetUserString("SAP_Z", str(node.z))

            restraint = md.restraints.get(nid)
            if restraint is not None:
                dof_names = ["U1", "U2", "U3", "R1", "R2", "R3"]
                active = []
                for i, dof in enumerate(dof_names):
                    if i < len(restraint.dofs) and restraint.dofs[i]:
                        active.append(dof)
                        attrs.SetUserString(
                            "SAP_Restraint_{}".format(dof), "True"
                        )
                if active:
                    attrs.SetUserString(
                        "SAP_Restraints", ",".join(active)
                    )
            obj.CommitChanges()
        except Exception:
            continue

    return count, obj_ids


# ========================================================================
# Frame element geometry — lightweight Extrusion
# ========================================================================

def create_frame_lines(
    md: SAPModelData,
    frame_section_layers: t.Dict[str, int],
) -> int:
    """Create line objects for frame centrelines."""
    doc = sc.doc
    count = 0
    default_layer = frame_section_layers.get("Default", 0)

    for eid, elem in md.frame_elements.items():
        try:
            ni = md.nodes.get(elem.node_i)
            nj = md.nodes.get(elem.node_j)
            if ni is None or nj is None:
                continue

            sec_name = md.frame_assignments.get(eid, "")
            layer_index = frame_section_layers.get(sec_name, default_layer)

            p_i = rg.Point3d(ni.x, ni.y, ni.z)
            p_j = rg.Point3d(nj.x, nj.y, nj.z)
            line = rg.Line(p_i, p_j)

            attr = rd.ObjectAttributes()
            attr.LayerIndex = layer_index
            attr.Name = "SAP_Frame_{}".format(eid)

            obj_id = doc.Objects.AddLine(line, attr)
            if obj_id is None:
                continue

            count += 1
            obj = doc.Objects.Find(obj_id)
            if obj is None:
                continue

            attrs = obj.Attributes
            attrs.SetUserString("SAP_Type", "Frame")
            attrs.SetUserString("SAP_FrameID", str(eid))
            attrs.SetUserString("SAP_Section", sec_name)
            attrs.SetUserString("SAP_JointI", str(elem.node_i))
            attrs.SetUserString("SAP_JointJ", str(elem.node_j))
            attrs.SetUserString("SAP_Angle", str(elem.angle))

            if sec_name and sec_name in md.sections:
                sec = md.sections[sec_name]
                attrs.SetUserString("SAP_Material", sec.material)
                attrs.SetUserString("SAP_Shape", sec.shape)
                attrs.SetUserString("SAP_Area", str(sec.A))

            obj.CommitChanges()
        except Exception:
            continue

    return count


def create_frame_extrusions(
    md: SAPModelData,
    frame_extrusion_layers: t.Dict[str, int],
) -> int:
    """Create lightweight ``Extrusion`` objects for frame elements.

    Uses ``rg.Extrusion.Create()`` which produces true lightweight
    Extrusion objects (not Brep polysurfaces).
    """
    doc = sc.doc
    count = 0
    default_layer = frame_extrusion_layers.get("Default", 0)

    for eid, elem in md.frame_elements.items():
        try:
            ni = md.nodes.get(elem.node_i)
            nj = md.nodes.get(elem.node_j)
            if ni is None or nj is None:
                continue

            sec_name = md.frame_assignments.get(eid, "")
            layer_index = frame_extrusion_layers.get(sec_name, default_layer)
            if not sec_name or sec_name not in md.sections:
                continue

            sec = md.sections[sec_name]
            p_i = rg.Point3d(ni.x, ni.y, ni.z)
            p_j = rg.Point3d(nj.x, nj.y, nj.z)

            axes = _local_axes(p_i, p_j, elem.angle)
            if axes is None:
                continue
            x_axis, y_axis, z_axis = axes

            dx = nj.x - ni.x
            dy = nj.y - ni.y
            dz = nj.z - ni.z
            length = math.hypot(dx, dy, dz)

            # Build profile
            profile = _build_profile_curve(sec)
            if profile is None:
                continue

            extrusion = None

            if isinstance(profile, tuple) and profile[0] == "circle":
                r = profile[1]
                # Circle at origin in the XY plane
                plane = rg.Plane(rg.Point3d(0, 0, 0), rg.Vector3d(0, 0, 1))
                circle = rg.Circle(plane, r)
                profile_curve = circle.ToNurbsCurve()
                extrusion = rg.Extrusion.Create(profile_curve, length, True)
            else:
                # Polyline profile in XY plane — extrude along Z
                extrusion = rg.Extrusion.Create(profile, length, True)

            if extrusion is None:
                continue

            # Transform: Z→x_axis, X→z_axis, Y→y_axis
            #   profile X (width/bf)  → z_axis → horizontal for beams
            #   profile Y (depth/t3)  → y_axis → vertical for beams
            #   extrusion Z (length)  → x_axis → along member
            xform = rg.Transform.Identity
            xform.M00 = z_axis.X; xform.M01 = y_axis.X; xform.M02 = x_axis.X
            xform.M10 = z_axis.Y; xform.M11 = y_axis.Y; xform.M12 = x_axis.Y
            xform.M20 = z_axis.Z; xform.M21 = y_axis.Z; xform.M22 = x_axis.Z
            xform.M03 = p_i.X
            xform.M13 = p_i.Y
            xform.M23 = p_i.Z
            xform.M33 = 1.0
            extrusion.Transform(xform)

            attr = rd.ObjectAttributes()
            attr.LayerIndex = layer_index
            attr.Name = "SAP_FrameExt_{}".format(eid)
            obj_id = doc.Objects.AddExtrusion(extrusion, attr)
            if obj_id is None:
                continue

            count += 1
            obj = doc.Objects.Find(obj_id)
            if obj is None:
                continue

            attrs = obj.Attributes
            attrs.SetUserString("SAP_Type", "FrameExtrusion")
            attrs.SetUserString("SAP_FrameID", str(eid))
            attrs.SetUserString("SAP_Section", sec_name)
            attrs.SetUserString("SAP_JointI", str(elem.node_i))
            attrs.SetUserString("SAP_JointJ", str(elem.node_j))
            attrs.SetUserString("SAP_Material", sec.material)
            attrs.SetUserString("SAP_Shape", sec.shape)
            attrs.SetUserString("SAP_Angle", str(elem.angle))
            obj.CommitChanges()
        except Exception:
            continue

    return count


# ========================================================================
# Shell / Area element geometry
# ========================================================================

def _create_brep_from_points(points, area_id: str, layer_index: int, doc):
    """Create a planar Brep (or mesh fallback) from corner points."""
    n = len(points)

    if n == 3:
        brep = rg.Brep.CreateFromCornerPoints(
            points[0], points[1], points[2], 0.001
        )
        if brep:
            attr = rd.ObjectAttributes()
            attr.LayerIndex = layer_index
            attr.Name = "SAP_Shell_{}".format(area_id)
            return doc.Objects.AddBrep(brep, attr)
        return None

    if n == 4:
        brep = rg.Brep.CreateFromCornerPoints(
            points[0], points[1], points[2], points[3], 0.001
        )
        if brep:
            attr = rd.ObjectAttributes()
            attr.LayerIndex = layer_index
            attr.Name = "SAP_Shell_{}".format(area_id)
            return doc.Objects.AddBrep(brep, attr)
        return None

    # N-gon
    try:
        polyline = rg.Polyline()
        for pt in points:
            polyline.Add(pt)
        polyline.Add(points[0])
        planar = rg.Brep.CreatePlanarBreps(polyline.ToPolylineCurve(), 0.001)
        if planar and len(planar) > 0:
            attr = rd.ObjectAttributes()
            attr.LayerIndex = layer_index
            attr.Name = "SAP_Shell_{}_Ngon".format(area_id)
            return doc.Objects.AddBrep(planar[0], attr)
    except Exception:
        pass

    # Mesh fallback
    try:
        import System.Collections.Generic as NetList
        mesh = rg.Mesh()
        for pt in points:
            mesh.Vertices.Add(pt)
        face_verts = NetList[int]()
        for idx in range(n):
            face_verts.Add(idx)
        mesh.Faces.AddFace(face_verts)
        mesh.Normals.ComputeNormals()
        mesh.Compact()
        attr = rd.ObjectAttributes()
        attr.LayerIndex = layer_index
        attr.Name = "SAP_Shell_{}_Ngon".format(area_id)
        return doc.Objects.AddMesh(mesh, attr)
    except Exception:
        return None


def create_shell_breps(
    md: SAPModelData,
    shell_section_layers: t.Dict[str, int],
) -> int:
    """Create planar Brep surfaces for shell elements."""
    doc = sc.doc
    count = 0
    default_layer = shell_section_layers.get("Default", 0)

    for aid, area in md.area_elements.items():
        try:
            points = []
            for nid in area.node_ids:
                node = md.nodes.get(nid)
                if node is None:
                    break
                points.append(rg.Point3d(node.x, node.y, node.z))
            if len(points) < 3:
                continue

            sec_name = md.area_assignments.get(aid, "")
            layer_index = shell_section_layers.get(sec_name, default_layer)

            obj_id = _create_brep_from_points(points, aid, layer_index, doc)
            if obj_id is None:
                continue

            count += 1
            obj = doc.Objects.Find(obj_id)
            if obj is None:
                continue

            attrs = obj.Attributes
            attrs.SetUserString("SAP_Type", "Shell")
            attrs.SetUserString("SAP_AreaID", str(aid))
            attrs.SetUserString("SAP_Section", sec_name)
            attrs.SetUserString("SAP_NodeCount", str(len(points)))
            attrs.SetUserString("SAP_JointIDs", ",".join(area.node_ids))

            if sec_name and sec_name in md.sections:
                sec = md.sections[sec_name]
                thickness = getattr(sec, "thickness", 0.0)
                attrs.SetUserString("SAP_Thickness", str(thickness))
                attrs.SetUserString("SAP_Material", sec.material)

            obj.CommitChanges()
        except Exception:
            continue

    return count


def create_shell_extrusions(
    md: SAPModelData,
    shell_extrusion_layers: t.Dict[str, int],
) -> int:
    """Create solid Breps for shell elements by offsetting the face."""
    doc = sc.doc
    count = 0
    default_layer = shell_extrusion_layers.get("Default", 0)

    for aid, area in md.area_elements.items():
        try:
            points = []
            for nid in area.node_ids:
                node = md.nodes.get(nid)
                if node is None:
                    break
                points.append(rg.Point3d(node.x, node.y, node.z))
            if len(points) < 3:
                continue

            sec_name = md.area_assignments.get(aid, "")
            layer_index = shell_extrusion_layers.get(sec_name, default_layer)
            if not sec_name or sec_name not in md.sections:
                continue

            sec = md.sections[sec_name]
            thickness = getattr(sec, "thickness", 0.0)
            if thickness <= 0:
                continue

            # Create centreline Brep, then offset
            obj_id = _create_brep_from_points(points, aid, layer_index, doc)
            if obj_id is None:
                continue

            shell_obj = doc.Objects.Find(obj_id)
            if shell_obj is None:
                continue

            shell_brep = shell_obj.Geometry
            face = shell_brep.Faces[0]

            brep_solid = rg.Brep.CreateFromOffsetFace(
                face, thickness, 0.001, True, True
            )
            if brep_solid is None:
                continue

            doc.Objects.Delete(obj_id, True)

            attr = rd.ObjectAttributes()
            attr.LayerIndex = layer_index
            attr.Name = "SAP_ShellExt_{}".format(aid)
            new_id = doc.Objects.AddBrep(brep_solid, attr)
            if new_id is None:
                continue

            count += 1
            obj = doc.Objects.Find(new_id)
            if obj is None:
                continue

            attrs = obj.Attributes
            attrs.SetUserString("SAP_Type", "ShellExtrusion")
            attrs.SetUserString("SAP_AreaID", str(aid))
            attrs.SetUserString("SAP_Section", sec_name)
            attrs.SetUserString("SAP_Thickness", str(thickness))
            attrs.SetUserString("SAP_Material", sec.material)
            attrs.SetUserString("SAP_JointIDs", ",".join(area.node_ids))
            obj.CommitChanges()
        except Exception:
            continue

    return count
