"""How an element's meshing settings read, for the Inspector.

SAFE exports an *auto-mesh intent* rather than a ready mesh for its floor and
wall objects: a global mesh option plus a maximum element size, with per-area
overrides (``Mesh Option`` / ``Add Restraints``) stored on the model as
``floor_mesh_options`` (``per_area`` for floors, ``wall_per_area`` for walls).
This module is the Qt-free, renderer-free lookup that turns those model facts
into the rows the Inspector appends, exactly as
:mod:`~fea_toolkit.model.supports` does for a node's restraints.
"""

from __future__ import annotations

from typing import Any, Optional

from .sap_data import AreaElement, FrameElement, Node, is_null_section

__all__ = ["frame_role", "meshing_rows"]


def frame_role(model: Any, frame: FrameElement) -> Optional[str]:
    """Non-structural role of a frame that carries no section assignment.

    CSI draws *null* (``"NONE"``) frames for purposes other than contributing
    member stiffness.  They are the non-structural companions to the null areas
    of :func:`~fea_toolkit.model.sap_data.is_null_section`: a frame *with* a
    real section assignment is structural (its geometric type —
    beam/column/brace — is
    :class:`~fea_toolkit.model.mesh_model.MeshModel.frame_element_types`), but a
    frame *without* one — or assigned the built-in ``"NONE"`` section — is
    classified here:

    * ``"line spring"`` — the frame carries a SAFE line-spring assignment
      (its stiffness distributes to the frame's endpoints; the frame itself is
      not built as a member);
    * ``"patch boundary"`` — the frame is a boundary edge of a null (load-patch
      / meshing-guide) area, so it is a meshing constraint rather than a member;
    * ``"mesh guide"`` — any other assignment-free frame (a bare line the mesh
      is meant to conform to).

    Args:
        model: A ``SAPModelData`` or ``MeshModel``, or ``None``.
        frame: The :class:`FrameElement` to classify.

    Returns:
        The role string, or ``None`` when the frame has a section assignment
        (structural) or *model* carries none of the lookup collections.
    """
    if model is None or frame is None:
        return None

    assignments = getattr(model, "frame_assignments", None) or {}
    section = assignments.get(frame.elem_id)
    if section and not is_null_section(section):
        return None  # structural frame (beam/column/brace)

    line_springs = getattr(model, "line_spring_assignments", None) or {}
    if frame.elem_id in line_springs:
        return "line spring"

    areas = getattr(model, "area_elements", None) or {}
    for area in areas.values():
        if not getattr(area, "null", False):
            continue
        nids = area.node_ids
        for k in range(len(nids)):
            if {frame.node_i, frame.node_j} == {nids[k], nids[(k + 1) % len(nids)]}:
                return "patch boundary"

    return "mesh guide"


def _area_meshing_rows(model: Any, area: AreaElement) -> list:
    """Role, per-area mesh option / restraints / size, opening and inactive."""
    rows: list[tuple[str, str]] = []

    role = (getattr(model, "area_roles", None) or {}).get(area.area_id)
    if role:
        rows.append(("Role", role))

    opts = getattr(model, "floor_mesh_options", None) or {}
    if role == "wall":
        per_area = opts.get("wall_per_area", {})
        size = opts.get("wall_max_size") or opts.get("max_size")
    else:
        per_area = opts.get("per_area", {})
        size = opts.get("max_size") or opts.get("wall_max_size")
    # When the role is unknown, surface either override so the setting is not
    # hidden behind a missing role.
    override = per_area.get(area.area_id) if per_area else None
    if override is None:
        override = (opts.get("per_area", {}) or opts.get("wall_per_area", {})).get(area.area_id)

    mesh_option = (override or {}).get("mesh_option")
    if mesh_option:
        rows.append(("Mesh Option", mesh_option))
    if (override or {}).get("add_restraints"):
        rows.append(("Add Restraints", "Yes"))
    if size:
        rows.append(("Mesh Size", f"{float(size):g}"))

    edge = opts.get("auto_edge_constraints", {}).get(area.area_id)
    if edge is not None:
        rows.append(("Auto Edge Constraint", "Yes" if edge else "No"))

    if getattr(area, "opening", False):
        rows.append(("Opening", "Yes"))
    if getattr(area, "null", False):
        rows.append(("Null (Geometric)", "Yes"))
    if getattr(area, "inactive", False):
        rows.append(("Meshed Away", "Yes"))

    return rows


def _frame_meshing_rows(model: Any, frame: FrameElement) -> list:
    rows: list[tuple[str, str]] = []
    role = frame_role(model, frame)
    if role:
        rows.append(("Role", role))
    auto = (getattr(model, "frame_auto_mesh", None) or {}).get(frame.elem_id)
    if not auto:
        return rows
    for key, label in (
        ("Auto Mesh", "Auto Mesh"),
        ("At Intermediate Joints", "Auto Mesh At Joints"),
        ("At Intersections", "Auto Mesh At Intersections"),
    ):
        value = auto.get(key)
        if value is not None:
            rows.append((label, _yes_no(value)))
    return rows


def _node_meshing_rows(model: Any, node: Node) -> list:
    opts = getattr(model, "floor_mesh_options", None) or {}
    include = opts.get("include_in_mesh", {}).get(node.node_id)
    if include is None:
        return []
    return [("Include In Mesh", "Yes" if include else "No")]


def _yes_no(value: Any) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return "Yes" if str(value).strip().lower() in ("yes", "true", "1") else "No"


def meshing_rows(model: Any, obj: Any) -> list:
    """``[(label, value), …]`` describing *obj*'s meshing settings.

    An area reports its role (floor/wall), its per-area mesh option and
    add-restraints flag, the governing maximum mesh size, whether its edges are
    auto-constrained, and whether it is an opening or has been meshed away
    (``inactive``).  A frame reports its non-structural role (via
    :func:`frame_role`) and its auto-mesh settings; a node reports whether it is
    included in the floor mesh.  Rows with nothing to say are omitted.

    Args:
        model: A ``SAPModelData`` or ``MeshModel``, or ``None``.
        obj: The selected :class:`AreaElement`, :class:`FrameElement` or
            :class:`Node`.

    Returns:
        One ``(name, value)`` pair per non-empty fact.
    """
    if model is None or obj is None:
        return []
    if isinstance(obj, AreaElement):
        return _area_meshing_rows(model, obj)
    if isinstance(obj, FrameElement):
        return _frame_meshing_rows(model, obj)
    if isinstance(obj, Node):
        return _node_meshing_rows(model, obj)
    return []
