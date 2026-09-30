"""How an element's section, material and groups read, for the Inspector.

The Inspector describes one *object*, but a frame/area's section assignment,
its material and the groups it belongs to all live on the **model** — keyed by
the element's id — not on the element dataclass.  This module is the Qt-free,
renderer-free lookup that turns those model facts into the rows the Inspector
appends, exactly as :mod:`~fea_toolkit.model.supports` does for a node's
restraints and joint constraint.

The group lookup is a **linear scan** of ``Group.objects`` rather than a
pre-built reverse (member → groups) index.  ``Group.objects`` is the single
source of truth and is *mutated after parsing* (area meshing appends child
references), so a cached reverse index would go stale.  The scan is O(total
group-object references) — a few milliseconds even for a model with hundreds of
thousands of members — which is acceptable for a once-per-click inspector.
A reverse index can be reintroduced later (built in
``PropertyInspector.set_source_model``) if that ever proves insufficient; see
the DONE register in ``docs/_pending_work.md``.
"""

from typing import Any

from .sap_data import AreaElement, FrameElement, Node

__all__ = ["element_rows", "groups_of"]


def groups_of(model: Any, etype: str, eid: str) -> list[str]:
    """Names of the groups that contain the ``"<etype>:<eid>"`` reference.

    Args:
        model: A ``SAPModelData`` or ``MeshModel``, or ``None``.
        etype: The reference kind — ``"Frame"``, ``"Area"`` or ``"Joint"``.
        eid: The element or node id.

    Returns:
        The group names, in model order (empty when the entity is ungrouped).
    """
    groups = getattr(model, "groups", None) or {}
    ref = f"{etype}:{eid}"
    return [name for name, group in groups.items() if ref in getattr(group, "objects", ())]


def _assignment(model: Any, etype: str, eid: str, obj: Any) -> str:
    """The section name assigned to *eid*, falling back to its parent's.

    A split child's own id is absent from the parsed source's
    ``frame_assignments`` / ``area_assignments`` (keyed by the original ids), so
    when the direct lookup misses and the element carries a ``parent_id``, the
    parent's assignment is used — the lineage that field tracks.
    """
    if etype == "Frame":
        assignments = getattr(model, "frame_assignments", None) or {}
    else:
        assignments = getattr(model, "area_assignments", None) or {}
    name = assignments.get(eid)
    if not name:
        parent = getattr(obj, "parent_id", None)
        if parent is not None:
            name = assignments.get(parent)
    return name or ""


def _section_label(model: Any, etype: str, eid: str, obj: Any) -> str:
    """The assigned section as ``"name (shape)"``, or ``""`` when unassigned."""
    name = _assignment(model, etype, eid, obj)
    if not name:
        return ""
    section = (getattr(model, "sections", None) or {}).get(name)
    shape = getattr(section, "shape", "") if section is not None else ""
    return f"{name} ({shape})" if shape else name


def _material_label(model: Any, etype: str, eid: str, obj: Any) -> str:
    """The assigned section's material as ``"name (type)"``, or ``""``."""
    name = _assignment(model, etype, eid, obj)
    if not name:
        return ""
    section = (getattr(model, "sections", None) or {}).get(name)
    material = getattr(section, "material", None)
    if not material:
        return ""
    mat = (getattr(model, "materials", None) or {}).get(material)
    kind = getattr(mat, "type", "") if mat is not None else ""
    return f"{material} ({kind})" if kind and kind != material else material


def element_rows(model: Any, obj: Any) -> list:
    """``[("Section", …), ("Material", …), ("Groups", …)]`` for *obj*.

    Rows with nothing to say are omitted, so an unassigned or ungrouped element
    gains no clutter.  A frame or area reports its section and material; every
    entity type reports its groups.  ``model is None`` (a results archive) adds
    nothing.

    Args:
        model: A ``SAPModelData`` or ``MeshModel``, or ``None``.
        obj: The selected :class:`FrameElement`, :class:`AreaElement` or
            :class:`Node`.

    Returns:
        One ``(name, value)`` pair per non-empty fact.
    """
    if model is None or obj is None:
        return []
    if isinstance(obj, FrameElement):
        etype, eid = "Frame", obj.elem_id
    elif isinstance(obj, AreaElement):
        etype, eid = "Area", obj.area_id
    elif isinstance(obj, Node):
        etype, eid = "Joint", obj.node_id
    else:
        return []

    rows = []
    if isinstance(obj, (FrameElement, AreaElement)):
        section = _section_label(model, etype, eid, obj)
        if section:
            rows.append(("Section", section))
        material = _material_label(model, etype, eid, obj)
        if material:
            rows.append(("Material", material))
    groups = groups_of(model, etype, eid)
    if groups:
        rows.append(("Groups", ", ".join(groups)))
    return rows
