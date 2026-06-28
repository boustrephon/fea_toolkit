"""
Rhino geometry creation helpers for SAP2000 model export.

Creates Rhino geometry from ``SAPModelData`` nodes and elements in two
representations:

* **Centreline**: points (joints), lines (frames), planar Breps (shells).
* **Extrusion**: 3‑D solids created by sweeping section profiles along
  frame lines or extruding shell Breps by their thickness.

All objects carry FEA metadata as Rhino UserStrings (``SAP_Type``,
``SAP_FrameID``, ``SAP_Section``, etc.) for Grasshopper consumption.
"""

import typing as t
import math

from ..model.sap_data import SAPModelData
from ..model.sap_data import (
    ISection, PipeSection, BoxSection, ChannelSection,
    RectangularSection, CircularSection, GeneralSection, ShellSection,
)

# ── Rhino API (lazy-loaded once) ─────────────────────────────────────────
_RHINO = None  # (Rhino, sc, rd, Point3d, Brep, Line, Polyline, Mesh, ...)


def _ensure_rhino():
    """Lazy-import Rhino modules — only works inside the Rhino process."""
    global _RHINO
    if _RHINO is not None:
        return _RHINO
    try:
        import Rhino  # noqa: F401
        import scriptcontext as sc
        import Rhino.DocObjects as rd
        from Rhino.Geometry import (
            Point3d, Brep, Line, Polyline, Mesh,
            Vector3d, Plane, Arc, Circle, Interval,
            PolylineCurve, Curve, Extrusion, Transform,
        )
    except ImportError as exc:
        raise RuntimeError(
            "Rhino modules are not available. This code must run inside "
            "Rhinoceros 3D (IronPython/CPython)."
        ) from exc

    _RHINO = (
        Rhino, sc, rd,
        Point3d, Brep, Line, Polyline, Mesh,
        Vector3d, Plane, Arc, Circle, Interval,
        PolylineCurve, Curve, Extrusion, Transform,
    )
    return _RHINO


def _local_axes(p_i, p_j, angle_deg=0.0):
    """Compute local (x, y, z) unit vectors for a frame element.

    Mirrors ``get_SAP_vecxz`` from ``fea_toolkit.model.geometry``:
        1. x = element direction (node I → node J)
        2. z = vecxz (computed per SAP2000 convention)
        3. y = cross(z, x)
    """
    (_Rhino, _sc, _rd,
     Point3d, Brep, Line, Polyline, Mesh,
     Vector3d, Plane, Arc, Circle, Interval,
     PolylineCurve, Curve, Extrusion, Transform) = _ensure_rhino()

    # 1. Local x-axis (element direction)
    x_axis = Vector3d(p_j.X - p_i.X, p_j.Y - p_i.Y, p_j.Z - p_i.Z)
    length = x_axis.Length
    if length < 1e-12:
        return None
    x_axis.Unitize()

    # 2. Compute vecxz (local z-axis) — same logic as get_SAP_vecxz
    global_y = Vector3d(0, 1, 0)
    global_z = Vector3d(0, 0, 1)
    cos_sim = x_axis * global_z  # dot product

    if abs(cos_sim) > 0.9999:
        # Vertical element: vecxz = global Y (pointing upward)
        vecxz = Vector3d(global_y.X, global_y.Y, global_y.Z)
        if cos_sim < 0:
            vecxz = -vecxz  # flip if element points downward
    else:
        # Non-vertical: vecxz = cross(x, global_Z), normalized
        vecxz = Vector3d.CrossProduct(x_axis, global_z)
        vecxz.Unitize()

    # Apply angle rotation about the x-axis
    if abs(angle_deg) > 1e-6:
        theta = math.radians(angle_deg)
        c, s = math.cos(theta), math.sin(theta)
        # Rodrigues' rotation of vecxz about x_axis
        vecxz = Vector3d(
            vecxz.X * c + (x_axis.Y * vecxz.Z - x_axis.Z * vecxz.Y) * s,
            vecxz.Y * c + (x_axis.Z * vecxz.X - x_axis.X * vecxz.Z) * s,
            vecxz.Z * c + (x_axis.X * vecxz.Y - x_axis.Y * vecxz.X) * s,
        )
        # Orthonormalise: component of vecxz perpendicular to x_axis
        proj = (vecxz * x_axis) * x_axis
        vecxz = vecxz - proj
        vecxz.Unitize()

    # 3. Local y-axis = cross(z, x)
    y_axis = Vector3d.CrossProduct(vecxz, x_axis)
    y_axis.Unitize()
    z_axis = Vector3d(vecxz.X, vecxz.Y, vecxz.Z)

    return x_axis, y_axis, z_axis


# ========================================================================
# Joint / Node geometry
# ========================================================================

def create_joint_points(
    md: SAPModelData,
    joint_layer_index: int,
) -> t.Tuple[int, t.List[str]]:
    """Create Rhino point objects for every node in the model.

    Each point stores the following UserString metadata:
        ``SAP_Type``, ``SAP_JointID``, ``SAP_X``, ``SAP_Y``, ``SAP_Z``,
        ``SAP_Restraints``, ``SAP_Constraint`` (if applicable).

    Args:
        md: The parsed SAP2000 model data.
        joint_layer_index: Rhino layer index for joint objects.

    Returns:
        Tuple of ``(count, object_id_list)``.
    """
    (Rhino, sc, rd,
     Point3d, Brep, Line, Polyline, Mesh,
     Vector3d, Plane, Arc, Circle, Interval,
     PolylineCurve, Curve, Extrusion, Transform) = _ensure_rhino()
    doc = sc.doc

    count = 0
    obj_ids: t.List[str] = []

    for nid, node in md.nodes.items():
        try:
            point = Point3d(node.x, node.y, node.z)
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

            # Restraint metadata
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
# Frame element geometry
# ========================================================================

def create_frame_lines(
    md: SAPModelData,
    frame_section_layers: t.Dict[str, int],
) -> int:
    """Create Rhino line objects for frame elements.

    Each line stores:
        ``SAP_Type``, ``SAP_FrameID``, ``SAP_Section``,
        ``SAP_JointI``, ``SAP_JointJ``,
        ``SAP_Material``, ``SAP_Shape`` (from section properties).

    Args:
        md: The parsed SAP2000 model data.
        frame_section_layers: Dict mapping section name → layer index.

    Returns:
        Number of frame lines created.
    """
    (Rhino, sc, rd,
     Point3d, Brep, Line, Polyline, Mesh,
     Vector3d, Plane, Arc, Circle, Interval,
     PolylineCurve, Curve, Extrusion, Transform) = _ensure_rhino()
    doc = sc.doc

    count = 0
    default_layer = frame_section_layers.get("Default", 0)

    for eid, elem in md.frame_elements.items():
        try:
            ni = md.nodes.get(elem.node_i)
            nj = md.nodes.get(elem.node_j)
            if ni is None or nj is None:
                continue

            # Determine layer from section assignment
            sec_name = md.frame_assignments.get(eid, "")
            layer_index = frame_section_layers.get(sec_name, default_layer)

            # Create line
            p_i = Point3d(ni.x, ni.y, ni.z)
            p_j = Point3d(nj.x, nj.y, nj.z)
            line = Line(p_i, p_j)

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
            attrs.SetUserString("SAP_Angle", str(elem.angle))

            obj.CommitChanges()
        except Exception:
            continue

    return count


# ========================================================================
# Shell / Area element geometry
# ========================================================================

def _create_brep_from_points(points, area_id: str, layer_index: int, doc):
    """Create a planar Brep (or mesh fallback) from corner points.

    Args:
        points: List of ``Point3d``.
        layer_index: Rhino layer index.

    Returns:
        Object ID or ``None``.
    """
    (Rhino, sc, rd,
     Point3d, Brep, Line, Polyline, Mesh,
     Vector3d, Plane, Arc, Circle, Interval,
     PolylineCurve, Curve, Extrusion, Transform) = _ensure_rhino()
    try:
        from System.Collections.Generic import List as NetList
    except ImportError:
        NetList = list

    n = len(points)

    # Triangle
    if n == 3:
        brep = Brep.CreateFromCornerPoints(
            points[0], points[1], points[2], 0.001
        )
        if brep:
            attr = rd.ObjectAttributes()
            attr.LayerIndex = layer_index
            attr.Name = "SAP_Shell_{}".format(area_id)
            return doc.Objects.AddBrep(brep, attr)
        return None

    # Quadrilateral
    if n == 4:
        brep = Brep.CreateFromCornerPoints(
            points[0], points[1], points[2], points[3], 0.001
        )
        if brep:
            attr = rd.ObjectAttributes()
            attr.LayerIndex = layer_index
            attr.Name = "SAP_Shell_{}".format(area_id)
            return doc.Objects.AddBrep(brep, attr)
        return None

    # N-gon (5+ points) — try planar Brep, fall back to mesh
    try:
        polyline = Polyline()
        for pt in points:
            polyline.Add(pt)
        polyline.Add(points[0])  # close

        planar = Brep.CreatePlanarBreps(polyline.ToPolylineCurve(), 0.001)
        if planar and len(planar) > 0:
            attr = rd.ObjectAttributes()
            attr.LayerIndex = layer_index
            attr.Name = "SAP_Shell_{}_Ngon".format(area_id)
            return doc.Objects.AddBrep(planar[0], attr)
    except Exception:
        pass

    # Mesh fallback
    try:
        mesh = Mesh()
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
    """Create Rhino Brep surfaces for shell / area elements.

    Each shell stores:
        ``SAP_Type``, ``SAP_AreaID``, ``SAP_Section``,
        ``SAP_NodeCount``, ``SAP_JointIDs``,
        ``SAP_Thickness``, ``SAP_Material``.

    Args:
        md: The parsed SAP2000 model data.
        shell_section_layers: Dict mapping section name → layer index.

    Returns:
        Number of shell Breps created.
    """
    (Rhino, sc, rd,
     Point3d, Brep, Line, Polyline, Mesh,
     Vector3d, Plane, Arc, Circle, Interval,
     PolylineCurve, Curve, Extrusion, Transform) = _ensure_rhino()
    doc = sc.doc

    count = 0
    default_layer = shell_section_layers.get("Default", 0)

    for aid, area in md.area_elements.items():
        try:
            # Collect point coordinates
            points = []
            for nid in area.node_ids:
                node = md.nodes.get(nid)
                if node is None:
                    break
                points.append(Point3d(node.x, node.y, node.z))

            if len(points) < 3:
                continue

            # Determine layer from section assignment
            sec_name = md.area_assignments.get(aid, "")
            layer_index = shell_section_layers.get(sec_name, default_layer)

            # Create geometry
            obj_id = _create_brep_from_points(
                points, aid, layer_index, doc
            )
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
            attrs.SetUserString(
                "SAP_JointIDs", ",".join(area.node_ids)
            )

            if sec_name and sec_name in md.sections:
                sec = md.sections[sec_name]
                thickness = getattr(sec, "thickness", 0.0)
                attrs.SetUserString("SAP_Thickness", str(thickness))
                attrs.SetUserString("SAP_Material", sec.material)

            obj.CommitChanges()
        except Exception:
            continue

    return count


# ========================================================================
# Section profile builders (used for frame extrusions)
# ========================================================================

def _section_profile_rect(depth, bf):
    """Closed corner points of a solid rectangular profile in the y-z plane."""
    h, w = depth / 2.0, bf / 2.0
    return [(-h, -w), (-h, w), (h, w), (h, -w)]


def _section_profile_i(depth, bf, tf, tw):
    """Closed corner points of an I/Wide-flange profile in the y-z plane."""
    h = depth / 2.0
    w = bf / 2.0
    wi = tw / 2.0
    fi = h - tf
    return [
        (-h, -w), (-h, w),
        (-fi, w), (-fi, wi),
        (fi, wi), (fi, w),
        (h, w), (h, -w),
        (fi, -w), (fi, -wi),
        (-fi, -wi), (-fi, -w),
    ]


def _section_profile_box(depth, bf, tf, tw):
    """Closed corner points of a Box/RHS profile in the y-z plane."""
    h = depth / 2.0
    w = bf / 2.0
    hi = h - tf
    wi = w - tw
    return [
        (-h, -w), (-h, w), (h, w), (h, -w),
        (h, -wi), (-hi, -wi), (-hi, wi), (h, wi),
    ]


def _section_profile_channel(depth, bf, tf, tw):
    """Closed corner points of a Channel/C-section profile in the y-z plane."""
    h = depth / 2.0
    w = bf / 2.0
    fi = h - tf
    wi = tw / 2.0
    return [
        (-h, -w), (-h, w),
        (-fi, w), (-fi, wi),
        (fi, wi), (fi, w),
        (h, w), (h, -w),
    ]


def _build_section_profile(sec, shape_id):
    """Build section profile data.

    Returns a dict with keys:
        ``pts_2d`` — list of ``(y, z)`` tuples for polygonal profiles
        ``is_circular`` — True for pipe/circle sections
        ``radius`` — radius for circular sections
        ``curve`` — ``PolylineCurve`` for polygonal, ``("circle", r)``
                    for circular, or ``None``.
    """
    (Rhino, sc, rd,
     Point3d, Brep, Line, Polyline, Mesh,
     Vector3d, Plane, Arc, Circle, Interval,
     PolylineCurve, Curve, Extrusion, Transform) = _ensure_rhino()

    pts_2d = None
    is_circular = False
    radius = 0.0

    if isinstance(sec, RectangularSection):
        pts_2d = _section_profile_rect(sec.depth, sec.bf)
    elif isinstance(sec, CircularSection):
        is_circular = True
        radius = sec.diameter / 2.0
    elif isinstance(sec, ISection):
        if sec.depth > 0 and sec.bf > 0:
            pts_2d = _section_profile_i(sec.depth, sec.bf, sec.tf, sec.tw)
    elif isinstance(sec, PipeSection):
        if sec.od > 0:
            is_circular = True
            radius = sec.od / 2.0
    elif isinstance(sec, BoxSection):
        if sec.depth > 0 and sec.bf > 0:
            pts_2d = _section_profile_box(sec.depth, sec.bf, sec.tf, sec.tw)
    elif isinstance(sec, ChannelSection):
        if sec.depth > 0 and sec.bf > 0:
            pts_2d = _section_profile_channel(sec.depth, sec.bf, sec.tf, sec.tw)

    # Fallback for catalogue sections with zero dimensions
    if pts_2d is None and not is_circular:
        side = (sec.A ** 0.5) if sec.A > 0 else 0.1
        pts_2d = _section_profile_rect(side, side)

    if is_circular:
        if radius <= 0:
            radius = ((sec.A / 3.14159) ** 0.5) if sec.A > 0 else 0.05

    curve = None
    if pts_2d:
        pts_3d = [Point3d(0, z, y) for y, z in pts_2d]
        # NOTE: (z, y) swap matches OpenSees convention:
        #   depth → Rhino Y → y_axis → vertical for beams
        #   width → Rhino Z → z_axis → horizontal for beams
        pl = Polyline(pts_3d)
        pl.Add(pts_3d[0])
        curve = PolylineCurve(pl)
    elif is_circular:
        curve = ("circle", radius)

    return {
        "pts_2d": pts_2d,
        "is_circular": is_circular,
        "radius": radius,
        "curve": curve,
    }


# ========================================================================
# Frame extrusions — 3-D solids via Extrusion
# ========================================================================

def create_frame_extrusions(
    md: SAPModelData,
    frame_extrusion_layers: t.Dict[str, int],
) -> int:
    """Create 3-D extrusion solids for frame elements using section profiles.

    Uses ``Extrusion`` (lightweight Rhino geometry) converted to Brep.
    Circular sections create cylinders; polygonal sections sweep the
    profile along the centreline.

    Each solid stores UserString metadata (``SAP_Type``, ``SAP_FrameID``,
    ``SAP_Section``, etc.).

    Args:
        md: The parsed SAP2000 model data.
        frame_extrusion_layers: Dict mapping section name → layer index.

    Returns:
        Number of extrusion solids created.
    """
    (Rhino, sc, rd,
     Point3d, Brep, Line, Polyline, Mesh,
     Vector3d, Plane, Arc, Circle, Interval,
     PolylineCurve, Curve, Extrusion, Transform) = _ensure_rhino()

    doc = sc.doc
    count = 0
    default_layer = frame_extrusion_layers.get("Default", 0)

    # Debug: draw local axes arrows at first few elements
    _debug_axes = False  # set True to enable

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
            p_i = Point3d(ni.x, ni.y, ni.z)
            p_j = Point3d(nj.x, nj.y, nj.z)
            axes = _local_axes(p_i, p_j, elem.angle)
            if axes is None:
                continue
            x_axis, y_axis, z_axis = axes

            # ── Debug: draw local axes at element start ──────────────
            if _debug_axes and count < 5:
                _draw_axis_arrow(doc, p_i, x_axis, (255, 0, 0))    # red = X
                _draw_axis_arrow(doc, p_i, y_axis, (0, 255, 0))    # green = Y
                _draw_axis_arrow(doc, p_i, z_axis, (0, 0, 255))    # blue = Z
            dx = nj.x - ni.x
            dy = nj.y - ni.y
            dz = nj.z - ni.z
            length = math.hypot(dx, dy, dz)

            profile = _build_section_profile(sec, sec.shape_id)
            if profile is None or profile["curve"] is None:
                continue

            pts_2d = profile["pts_2d"]
            is_circ = profile["is_circular"]

            # Build profile curve directly at p_i in element's local axes
            if is_circ:
                r = profile["radius"]
                # Circle in the Y-Z plane (perpendicular to element direction)
                plane = Plane(p_i, y_axis, z_axis)
                circle = Circle(plane, r)
                profile_curve = circle.ToNurbsCurve()
            else:
                pts_3d = []
                for y_comp, z_comp in pts_2d:
                    pt = Point3d(
                        p_i.X + y_axis.X * y_comp + z_axis.X * z_comp,
                        p_i.Y + y_axis.Y * y_comp + z_axis.Y * z_comp,
                        p_i.Z + y_axis.Z * y_comp + z_axis.Z * z_comp,
                    )
                    pts_3d.append(pt)
                pl = Polyline(pts_3d)
                pl.Add(pts_3d[0])
                profile_curve = PolylineCurve(pl)

            rail = Line(p_i, p_j).ToNurbsCurve()
            swept = Brep.CreateFromSweep(rail, profile_curve, True, 0.001)
            if not swept or len(swept) == 0:
                continue
            brep_solid = swept[0]

            attr = rd.ObjectAttributes()
            attr.LayerIndex = layer_index
            attr.Name = "SAP_FrameExt_{}".format(eid)
            obj_id = doc.Objects.AddBrep(brep_solid, attr)

            attr = rd.ObjectAttributes()
            attr.LayerIndex = layer_index
            attr.Name = "SAP_FrameExt_{}".format(eid)
            obj_id = doc.Objects.AddBrep(brep_solid, attr)
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
# Shell extrusions — 3-D solids by thickness offset
# ========================================================================

def create_shell_extrusions(
    md: SAPModelData,
    shell_extrusion_layers: t.Dict[str, int],
) -> int:
    """Create 3-D solid Breps for shell elements by extruding the planar
    face along its normal by the section thickness.

    Uses ``Brep.CreateFromOffsetFace`` to produce capped solids.

    Each solid stores UserString metadata (``SAP_Type``, ``SAP_AreaID``,
    ``SAP_Section``, ``SAP_Thickness``, etc.).

    Args:
        md: The parsed SAP2000 model data.
        shell_extrusion_layers: Dict mapping section name → layer index.

    Returns:
        Number of shell extrusion solids created.
    """
    (Rhino, sc, rd,
     Point3d, Brep, Line, Polyline, Mesh,
     Vector3d, Plane, Arc, Circle, Interval,
     PolylineCurve, Curve, Extrusion, Transform) = _ensure_rhino()

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
                points.append(Point3d(node.x, node.y, node.z))
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

            # Create the planar Brep first (centreline surface)
            obj_id = _create_brep_from_points(points, aid, layer_index, doc)
            if obj_id is None:
                continue
            shell_obj = doc.Objects.Find(obj_id)
            if shell_obj is None:
                continue

            shell_brep = shell_obj.Geometry
            face = shell_brep.Faces[0]
            u_dom = face.Domain(0)
            v_dom = face.Domain(1)
            u_mid = u_dom.ParameterAt(0.5)
            v_mid = v_dom.ParameterAt(0.5)
            normal = face.NormalAt(u_mid, v_mid)
            normal.Unitize()

            # Extrude the outer edge curve along the face normal
            outer_loops = face.OuterLoop
            if outer_loops is None:
                continue
            edge_curve = outer_loops.To3dCurve()
            if edge_curve is None:
                continue

            # Offset the face along its normal by the section thickness
            # to create a solid Brep.
            brep_solid = Brep.CreateFromOffsetFace(
                face, thickness, 0.001, True, True
            )
            if brep_solid is None:
                continue

            # Remove the planar surface, keep only the solid
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


# ========================================================================
# Debug helpers
# ========================================================================

def _draw_axis_arrow(doc, origin, direction, rgb, scale=1.0):
    """Draw a small coloured line showing an axis direction."""
    try:
        from System.Drawing import Color
        import Rhino.DocObjects as rd
        tip = Point3d(
            origin.X + direction.X * scale,
            origin.Y + direction.Y * scale,
            origin.Z + direction.Z * scale,
        )
        line = Line(origin, tip)
        attr = rd.ObjectAttributes()
        attr.ObjectColor = Color.FromArgb(rgb[0], rgb[1], rgb[2])
        attr.ColorSource = rd.ObjectColorSource.ColorFromObject
        attr.Name = "SAP_Axis"
        doc.Objects.AddLine(line, attr)
    except Exception:
        pass
