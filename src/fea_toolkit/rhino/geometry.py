"""
Rhino geometry creation — ``rg``-based lightweight Extrusion version.

Uses ``import Rhino.Geometry as rg`` throughout (the standard CPython
convention for Rhino 8) and ``rg.Extrusion.Create()`` to produce true
lightweight Extrusion objects.
"""

from __future__ import annotations

import math
import typing as t

try:
    import Rhino  # pyright: ignore[reportMissingImports]
    import Rhino.DocObjects as rd  # pyright: ignore[reportMissingImports]
    import Rhino.Geometry as rg  # pyright: ignore[reportMissingImports]
    import scriptcontext as sc  # pyright: ignore[reportMissingImports]
except ImportError:
    Rhino = sc = rd = rg = None  # type: ignore

from ..model.geometry import frame_end_offsets_global
from ..model.sap_data import SAPModelData

# ========================================================================
# Section profile curves (Rhino wrapper over the shared geometry)
# ========================================================================
# The profile polygons themselves live in
# :mod:`fea_toolkit.model.section_profiles` so a section can be extruded to a
# solid in Rhino *and* a PyVista viewport from one source of truth.  This module
# only turns those outlines into ``rg`` curves for ``Extrusion.Create``, which
# extrudes along Z — so a profile is built in the XY plane, with the convention
# x = width (→ element z_axis), y = depth (→ element y_axis).


def _polyline_curve(pts) -> t.Any:
    """A closed ``rg.PolylineCurve`` through the ``(x, y)`` points, in the XY plane."""
    pts_3d = [rg.Point3d(x, y, 0) for x, y in pts]
    pl = rg.Polyline(pts_3d)
    pl.Add(pts_3d[0])
    return rg.PolylineCurve(pl)


def _build_profile_curve(sec) -> tuple[t.Any, t.Optional[t.Any]]:
    """Build the outer profile curve (and optional inner hole) for ``Extrusion.Create``.

    Returns:
        A tuple ``(outer, inner)``.  *outer* is a ``rg.PolylineCurve`` for
        polygonal sections or a ``("circle", radius)`` tuple for circular
        sections; *inner* is a ``rg.PolylineCurve`` tracing a hollow section's
        void (a box), reversed so it winds opposite the outer boundary, or
        ``None``.  ``(None, None)`` when the section cannot be profiled.
    """
    from ..model.section_profiles import section_profile_outline

    outline = section_profile_outline(sec)
    if outline is None:
        return None, None

    kind, value = outline
    if kind == "circle":
        return ("circle", value), None

    inner = None
    if isinstance(value, tuple):
        # Hollow section (e.g. a box): keep the outer boundary as the extrusion
        # profile and the inner boundary as a hole, added through
        # ``Extrusion.AddInnerProfile`` so the section's cavity survives.  The
        # inner loop is reversed so it winds opposite the outer boundary.
        value, inner_pts = value
        inner = _polyline_curve(inner_pts[::-1])

    return _polyline_curve(value), inner


# ========================================================================
# Local axes (mirrors ``get_SAP_vecxz``)
# ========================================================================


def _local_axes(
    p_i: rg.Point3d, p_j: rg.Point3d, angle_deg: float = 0.0
) -> t.Optional[tuple[rg.Vector3d, rg.Vector3d, rg.Vector3d]]:
    """Compute local (x, y, z) unit vectors — matches the CSI/OpenSees convention.

    Mirrors :func:`fea_toolkit.model.geometry.get_local_axes` (the SAP2000/ETABS
    default frame local axes: non‑vertical ``vecxz = x × global_Z``, vertical
    ``vecxz = x × global_X`` ≡ ±global_Y); the returned triplet is always
    orthonormal.  Returns ``None`` for a zero-length member.
    """
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
        # A slightly-leaning axis is not exactly perpendicular to global Y;
        # project out the axial component so the frame stays orthonormal
        # (mirrors get_SAP_vecxz).
        vecxz = vecxz - (vecxz * x_axis) * x_axis
        vecxz.Unitize()
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
    stage: str = "sap",
) -> tuple[int, list[str]]:
    """Create point objects for each node — same API as ``geometry.py``.

    Args:
        md: Model source (``SAPModelData`` / ``MeshModel`` /
            :class:`~fea_toolkit.model.source_resolver.ResolvedSource`).
        joint_layer_index: Rhino layer index for the points.
        stage: Pipeline stage label stamped as ``FEA_Stage``.
    """
    doc = sc.doc
    count = 0
    obj_ids: list[str] = []

    for nid, node in md.nodes.items():
        try:
            point = rg.Point3d(node.x, node.y, node.z)
            attr = rd.ObjectAttributes()
            attr.LayerIndex = joint_layer_index
            attr.Name = f"SAP_Joint_{nid}"

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
            attrs.SetUserString("FEA_Stage", stage)
            attrs.SetUserString("FEA_Kind", "Joint")
            attrs.SetUserString("FEA_NodeTag", str(node.node_tag))

            restraint = md.restraints.get(nid)
            if restraint is not None:
                dof_names = ["U1", "U2", "U3", "R1", "R2", "R3"]
                active = []
                for i, dof in enumerate(dof_names):
                    if i < len(restraint.dofs) and restraint.dofs[i]:
                        active.append(dof)
                        attrs.SetUserString(f"SAP_Restraint_{dof}", "True")
                if active:
                    attrs.SetUserString("SAP_Restraints", ",".join(active))

            constraint_name = getattr(md, "constraint_assignments", {}).get(nid)
            if constraint_name:
                attrs.SetUserString("SAP_Constraint", str(constraint_name))
            obj.CommitChanges()
        except Exception:
            continue

    return count, obj_ids


# ========================================================================
# Frame element geometry — lightweight Extrusion
# ========================================================================


def _get_frame_points(md: SAPModelData, eid: str, elem):
    """Compute node I/J positions adjusted for frame end offsets.

    Returns ``(p_i, p_j, x_axis, y_axis, z_axis, length,
               elastic_i, elastic_j,
               off_y_i, off_z_i, off_y_j, off_z_j)``
    where ``elastic_i`` / ``elastic_j`` are the full centroid endpoints
    (``p_i + off_i`` / ``p_j + off_j`` — longitudinal + lateral), or
    ``None`` if the element has invalid nodes.
    """
    ni = md.nodes.get(elem.node_i)
    nj = md.nodes.get(elem.node_j)
    if ni is None or nj is None:
        return None

    p_i = rg.Point3d(ni.x, ni.y, ni.z)
    p_j = rg.Point3d(nj.x, nj.y, nj.z)
    dx = nj.x - ni.x
    dy = nj.y - ni.y
    dz = nj.z - ni.z
    length = math.hypot(dx, dy, dz)
    if length < 1e-12:
        return None

    axes = _local_axes(p_i, p_j, elem.angle)
    if axes is None:
        return None
    x_axis, y_axis, z_axis = axes

    # Longitudinal rigid offsets
    offs = md.frame_end_offsets.get(eid)
    end_i = offs.end_i if offs else 0.0
    end_j = offs.end_j if offs else 0.0
    off_y_i = offs.off_y_i if offs else 0.0
    off_z_i = offs.off_z_i if offs else 0.0
    off_y_j = offs.off_y_j if offs else 0.0
    off_z_j = offs.off_z_j if offs else 0.0

    total_offset = end_i + end_j
    if total_offset > length * 0.99:
        scale = length * 0.99 / max(total_offset, 1e-12)
        end_i *= scale
        end_j *= scale

    # Full I/J offset vectors in global coordinates — the same helper the
    # OpenSees builder feeds ``-jntOffset``, so drawn == analysed == exported.
    off_i, off_j = frame_end_offsets_global(
        [dx, dy, dz],
        elem.angle,
        end_i,
        end_j,
        off_y_i,
        off_z_i,
        off_y_j,
        off_z_j,
    )
    elastic_i = rg.Point3d(p_i.X + off_i[0], p_i.Y + off_i[1], p_i.Z + off_i[2])
    elastic_j = rg.Point3d(p_j.X + off_j[0], p_j.Y + off_j[1], p_j.Z + off_j[2])

    return (
        p_i,
        p_j,
        x_axis,
        y_axis,
        z_axis,
        length,
        elastic_i,
        elastic_j,
        off_y_i,
        off_z_i,
        off_y_j,
        off_z_j,
    )


def create_frame_lines(
    md: SAPModelData,
    frame_section_layers: dict[str, int],
    stage: str = "sap",
) -> int:
    """Create line objects for frame centrelines (elastic portion)."""
    doc = sc.doc
    count = 0
    default_layer = frame_section_layers.get("Default", 0)

    for eid, elem in md.frame_elements.items():
        try:
            pts = _get_frame_points(md, eid, elem)
            if pts is None:
                continue
            _, _, _, _, _, _, ei, ej, *_ = pts

            sec_name = md.frame_assignments.get(eid, "")
            layer_index = frame_section_layers.get(sec_name, default_layer)

            line = rg.Line(ei, ej)
            attr = rd.ObjectAttributes()
            attr.LayerIndex = layer_index
            attr.Name = f"SAP_Frame_{eid}"

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
            attrs.SetUserString("FEA_Stage", stage)
            attrs.SetUserString("FEA_Kind", "Frame")
            attrs.SetUserString("FEA_ElemTag", str(getattr(elem, "elem_tag", "")))
            if getattr(elem, "parent_id", None):
                attrs.SetUserString("FEA_ParentID", str(elem.parent_id))

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
    frame_extrusion_layers: dict[str, int],
    stage: str = "sap",
) -> int:
    """Create lightweight ``Extrusion`` objects for frame elements.

    Accounts for longitudinal rigid offsets and lateral cardinal‑point
    offsets.  Uses ``rg.Extrusion.Create()`` which produces true
    lightweight Extrusion objects; when the I/J lateral offsets differ
    the element cannot be a lightweight Extrusion, so a swept Brep solid
    (``doc.Objects.AddBrep``) is created instead — both are stamped with
    the same ``SAP_*`` / ``FEA_*`` metadata.

    Args:
        md: Model source.
        frame_extrusion_layers: ``{section_name: layer_index}``.
        stage: Pipeline stage label stamped as ``FEA_Stage``.

    Returns:
        Number of Rhino objects created for the frame elements.
    """
    doc = sc.doc
    count = 0
    default_layer = frame_extrusion_layers.get("Default", 0)

    for eid, elem in md.frame_elements.items():
        try:
            pts = _get_frame_points(md, eid, elem)
            if pts is None:
                continue
            (
                _p_i,
                _p_j,
                x_axis,
                y_axis,
                z_axis,
                _length,
                ei,
                ej,
                off_y_i,
                off_z_i,
                off_y_j,
                off_z_j,
            ) = pts

            # Centroid line the section sweeps along (elastic_i → elastic_j):
            # the flexible length the ``-jntOffset`` analysis uses, not the
            # full reference line.
            flex_vec = rg.Vector3d(ej.X - ei.X, ej.Y - ei.Y, ej.Z - ei.Z)
            flex_length = flex_vec.Length

            sec_name = md.frame_assignments.get(eid, "")
            layer_index = frame_extrusion_layers.get(sec_name, default_layer)
            if not sec_name or sec_name not in md.sections:
                continue

            sec = md.sections[sec_name]

            # Build profile
            profile, inner_profile = _build_profile_curve(sec)
            if profile is None:
                continue

            # Interpolate lateral offset at I-end
            oy_i = off_y_i
            oz_i = off_z_i

            extrusion = None

            if isinstance(profile, tuple) and profile[0] == "circle":
                r = profile[1]
                plane = rg.Plane(rg.Point3d(0, 0, 0), rg.Vector3d(0, 0, 1))
                circle = rg.Circle(plane, r)
                profile_curve = circle.ToNurbsCurve()
                extrusion = rg.Extrusion.Create(profile_curve, flex_length, True)
            else:
                signed_area = 0.0
                n = profile.PointCount  # pyright: ignore[reportAttributeAccessIssue]
                for j in range(n):
                    p0 = profile.Point(j)  # pyright: ignore[reportAttributeAccessIssue]
                    p1 = profile.Point((j + 1) % n)  # pyright: ignore[reportAttributeAccessIssue]
                    signed_area += p0.X * p1.Y - p1.X * p0.Y
                signed_area *= 0.5
                if signed_area < 0:
                    extrusion = rg.Extrusion.Create(profile, -flex_length, True)
                else:
                    extrusion = rg.Extrusion.Create(profile, flex_length, True)

            if extrusion is None:
                continue

            if inner_profile is not None:
                extrusion.AddInnerProfile(inner_profile)

            # Check if J-end offsets differ from I-end
            off_diff = abs(oy_i - off_y_j) > 1e-6 or abs(oz_i - off_z_j) > 1e-6

            if off_diff:
                # Different lateral offsets at ends — can't use lightweight
                # Extrusion. Fall back to swept Brep between shifted endpoints.
                # ``ei`` / ``ej`` already carry the full I/J offsets, so the
                # sweep runs centroid-to-centroid between the shifted endpoints.
                origin_i = rg.Point3d(ei.X, ei.Y, ei.Z)
                origin_j = rg.Point3d(ej.X, ej.Y, ej.Z)

                # Create a extrusion at I-end then morph to J-end
                xform_i = rg.Transform.Identity
                xform_i.M00 = z_axis.X
                xform_i.M01 = y_axis.X
                xform_i.M02 = x_axis.X
                xform_i.M10 = z_axis.Y
                xform_i.M11 = y_axis.Y
                xform_i.M12 = x_axis.Y
                xform_i.M20 = z_axis.Z
                xform_i.M21 = y_axis.Z
                xform_i.M22 = x_axis.Z
                xform_i.M03 = origin_i.X
                xform_i.M13 = origin_i.Y
                xform_i.M23 = origin_i.Z
                xform_i.M33 = 1.0
                extrusion.Transform(xform_i)

                path_vec = rg.Vector3d(
                    origin_j.X - origin_i.X, origin_j.Y - origin_i.Y, origin_j.Z - origin_i.Z
                )
                if path_vec.Length > 1e-6:
                    cap0 = extrusion.PathBottom.Copy()
                    cap1 = extrusion.PathTop.Copy()
                    if cap0 and cap1:
                        cap1.Translate(path_vec)
                        breps = rg.Brep.CreateFromSweep(
                            rg.Line(origin_i, origin_j).ToNurbsCurve(), cap0, True, 0.001
                        )
                        if breps:
                            extrusion.Dispose()
                            # Differing I/J lateral offsets cannot be expressed
                            # as a lightweight Extrusion.  CreateFromSweep
                            # returns a Brep[] array, so add each swept Brep
                            # solid individually (documented "Brep solid"
                            # geometry) — AddBrep must never receive the array.
                            for brep in breps:
                                attr_local = rd.ObjectAttributes()
                                attr_local.LayerIndex = layer_index
                                attr_local.Name = f"SAP_FrameExt_{eid}"
                                obj_id = doc.Objects.AddBrep(brep, attr_local)
                                if obj_id:
                                    count += 1
                                    obj = doc.Objects.Find(obj_id)
                                    if obj:
                                        attrs = obj.Attributes
                                        attrs.SetUserString("SAP_Type", "FrameExtrusion")
                                        attrs.SetUserString("SAP_FrameID", str(eid))
                                        attrs.SetUserString("SAP_Section", sec_name)
                                        attrs.SetUserString("SAP_JointI", str(elem.node_i))
                                        attrs.SetUserString("SAP_JointJ", str(elem.node_j))
                                        attrs.SetUserString("SAP_Material", sec.material)
                                        attrs.SetUserString("SAP_Shape", sec.shape)
                                        attrs.SetUserString("SAP_Angle", str(elem.angle))
                                        attrs.SetUserString("FEA_Stage", stage)
                                        attrs.SetUserString("FEA_Kind", "FrameExtrusion")
                                        attrs.SetUserString(
                                            "FEA_ElemTag", str(getattr(elem, "elem_tag", ""))
                                        )
                                        if getattr(elem, "parent_id", None):
                                            attrs.SetUserString("FEA_ParentID", str(elem.parent_id))
                                        obj.CommitChanges()
                            continue

            # Sweep along the centroid line from the (full-offset) I endpoint.
            origin_x = ei.X
            origin_y = ei.Y
            origin_z = ei.Z

            xform = rg.Transform.Identity
            xform.M00, xform.M01, xform.M02 = z_axis.X, y_axis.X, x_axis.X
            xform.M10 = z_axis.Y
            xform.M11 = y_axis.Y
            xform.M12 = x_axis.Y
            xform.M20 = z_axis.Z
            xform.M21 = y_axis.Z
            xform.M22 = x_axis.Z
            xform.M03 = origin_x
            xform.M13 = origin_y
            xform.M23 = origin_z
            xform.M33 = 1.0
            extrusion.Transform(xform)

            attr = rd.ObjectAttributes()
            attr.LayerIndex = layer_index
            attr.Name = f"SAP_FrameExt_{eid}"
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
            attrs.SetUserString("FEA_Stage", stage)
            attrs.SetUserString("FEA_Kind", "FrameExtrusion")
            attrs.SetUserString("FEA_ElemTag", str(getattr(elem, "elem_tag", "")))
            if getattr(elem, "parent_id", None):
                attrs.SetUserString("FEA_ParentID", str(elem.parent_id))
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
        brep = rg.Brep.CreateFromCornerPoints(points[0], points[1], points[2], 0.001)
        if brep:
            attr = rd.ObjectAttributes()
            attr.LayerIndex = layer_index
            attr.Name = f"SAP_Shell_{area_id}"
            return doc.Objects.AddBrep(brep, attr)
        return None

    if n == 4:
        brep = rg.Brep.CreateFromCornerPoints(points[0], points[1], points[2], points[3], 0.001)
        if brep:
            attr = rd.ObjectAttributes()
            attr.LayerIndex = layer_index
            attr.Name = f"SAP_Shell_{area_id}"
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
            attr.Name = f"SAP_Shell_{area_id}_Ngon"
            return doc.Objects.AddBrep(planar[0], attr)
    except Exception:
        pass

    # Mesh fallback
    try:
        from System.Collections.Generic import List  # pyright: ignore[reportMissingImports]

        mesh = rg.Mesh()
        for pt in points:
            mesh.Vertices.Add(pt)
        face_verts = List[int]()
        for idx in range(n):
            face_verts.Add(idx)
        mesh.Faces.AddFace(face_verts)
        mesh.Normals.ComputeNormals()
        mesh.Compact()
        attr = rd.ObjectAttributes()
        attr.LayerIndex = layer_index
        attr.Name = f"SAP_Shell_{area_id}_Ngon"
        return doc.Objects.AddMesh(mesh, attr)
    except Exception:
        return None


def create_shell_breps(
    md: SAPModelData,
    shell_section_layers: dict[str, int],
    stage: str = "sap",
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
            attrs.SetUserString("FEA_Stage", stage)
            attrs.SetUserString("FEA_Kind", "Shell")
            attrs.SetUserString("FEA_ElemTag", str(getattr(area, "area_tag", "")))
            if getattr(area, "parent_id", None):
                attrs.SetUserString("FEA_ParentID", str(area.parent_id))

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
    shell_extrusion_layers: dict[str, int],
    stage: str = "sap",
) -> int:
    """Create solid Breps for shell elements by offsetting the face.

    Args:
        md: Model source.
        shell_extrusion_layers: ``{section_name: layer_index}``.
        stage: Pipeline stage label stamped as ``FEA_Stage``.

    Returns:
        Number of Rhino objects created for the shell elements.
    """
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

            brep_solid = rg.Brep.CreateFromOffsetFace(face, thickness, 0.001, True, True)
            if brep_solid is None:
                continue

            doc.Objects.Delete(obj_id, True)

            attr = rd.ObjectAttributes()
            attr.LayerIndex = layer_index
            attr.Name = f"SAP_ShellExt_{aid}"
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
            attrs.SetUserString("FEA_Stage", stage)
            attrs.SetUserString("FEA_Kind", "ShellExtrusion")
            attrs.SetUserString("FEA_ElemTag", str(getattr(area, "area_tag", "")))
            if getattr(area, "parent_id", None):
                attrs.SetUserString("FEA_ParentID", str(area.parent_id))
            obj.CommitChanges()
        except Exception:
            continue

    return count
