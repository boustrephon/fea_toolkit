"""How a node / line / area's soil springs read, for the Inspector.

SAFE models carry foundation support as *spring* definitions plus assignments:
a point spring sits on one node, a line spring on a frame line, and an area
spring on a floor area.  A node is therefore "connected to a spring" not only
when it carries a direct point-spring assignment, but also when it is an
endpoint of a spring-supported line or a corner of a spring-supported area.
This module is the Qt-free, renderer-free lookup that turns those model facts
into the rows the Inspector appends, exactly as
:mod:`~fea_toolkit.model.supports` does for a node's restraints and
:mod:`~fea_toolkit.model.element_context` does for an element's section.

A processed :class:`~fea_toolkit.model.mesh_model.MeshModel` replaces the three
definition/assignment dicts with a resolved ``point_springs`` *list* of
:class:`~fea_toolkit.model.mesh_model.NodalSpring` (line and area springs are
already distributed to their supporting nodes), so the node lookup also reads
that shape.
"""

from __future__ import annotations

from typing import Any

from .sap_data import AreaElement, FrameElement, Node

__all__ = ["spring_rows"]

#: OpenSees DOF labels for a resolved ``NodalSpring.stiffness`` list, which is
#: stored in DOF order (index 0 → DOF 1).
_DOF_ORDER = ("UX", "UY", "UZ", "RX", "RY", "RZ")


def _definition(model: Any, container: str, name: str) -> Any:
    """The spring definition named *name*, or ``None``.

    ``SAPModelData`` keeps definitions in a dict keyed by name; a ``MeshModel``
    has no definitions (its ``point_springs`` is a resolved list), so a non-dict
    container is simply reported as "no definition".
    """
    definitions = getattr(model, container, None)
    return definitions.get(name) if isinstance(definitions, dict) else None


def _point_spring_label(model: Any, name: str) -> str:
    spring = _definition(model, "point_springs", name)
    if spring is None:
        return name
    parts = [f"{dof}={k:g}" for dof, k in getattr(spring, "stiffness", {}).items() if k]
    return f"{name} ({', '.join(parts)})" if parts else name


def _line_spring_label(model: Any, name: str) -> str:
    spring = _definition(model, "line_springs", name)
    if spring is None:
        return name
    k = getattr(spring, "vertical_stiffness", 0.0)
    return f"{name} (vert {k:g})" if k else name


def _area_spring_label(model: Any, name: str) -> str:
    spring = _definition(model, "area_springs", name)
    if spring is None:
        return name
    parts = [f"{dof}={k:g}" for dof, k in getattr(spring, "stiffness", {}).items() if k]
    return f"{name} ({', '.join(parts)})" if parts else name


def _nodal_spring_label(spring: Any) -> str:
    """A resolved :class:`~fea_toolkit.model.mesh_model.NodalSpring` compactly.

    DOFs flagged ``compression_only`` are labelled *candidates*: whether they
    actually resist compression-only depends on the builder's
    ``soil_spring_compression_only`` config (off by default, which emits a
    linear ``Elastic`` material), so the flag records SAFE's intent rather
    than an active nonlinear support.

    Args:
        spring: The resolved :class:`~fea_toolkit.model.mesh_model.NodalSpring`
            to describe.

    Returns:
        A compact, human-readable description (e.g. ``"UX=1e6, UZ=2e6"``), or
        ``"spring"`` when no stiffness or compression flag is set.
    """
    stiffness = getattr(spring, "stiffness", None) or []
    parts = [
        f"{dof}={stiffness[i]:g}"
        for i, dof in enumerate(_DOF_ORDER)
        if i < len(stiffness) and stiffness[i]
    ]
    if getattr(spring, "compression_only", None):
        parts.append("compression-only candidate")
    return ", ".join(parts) if parts else "spring"


def _node_spring_rows(model: Any, node_id: str, processed: Any = None) -> list:
    """Every spring that touches *node_id* — point, line, area, then resolved.

    Assignment details (point/line/area definitions and their assignments) read
    from *model* — the raw ``SAPModelData`` — while the resolved
    ``NodalSpring`` list reads from *processed* (the ``MeshModel``) when one is
    given, else from *model*.

    Args:
        model: The raw ``SAPModelData`` (or ``None`` when only the resolved
            *processed* springs are needed).
        node_id: The node whose spring rows are collected.
        processed: Optional ``MeshModel`` supplying the resolved
            ``point_springs`` list; when ``None`` it reads from *model*.

    Returns:
        ``[(label, value), …]`` — one pair per spring type that touches
        *node_id* (point, line, area, and resolved nodal springs).
    """
    rows: list[tuple[str, str]] = []

    point = (getattr(model, "point_spring_assignments", None) or {}).get(node_id)
    if point:
        rows.append(("Point Spring", _point_spring_label(model, point)))

    line_assignments = getattr(model, "line_spring_assignments", None) or {}
    if line_assignments:
        frames = getattr(model, "frame_elements", None) or {}
        labels = [
            f"{line_id}: {_line_spring_label(model, name)}"
            for line_id, name in line_assignments.items()
            if (frame := frames.get(line_id)) is not None
            and node_id in (frame.node_i, frame.node_j)
        ]
        if labels:
            rows.append(("Line Spring", ", ".join(labels)))

    area_assignments = getattr(model, "area_spring_assignments", None) or {}
    if area_assignments:
        areas = getattr(model, "area_elements", None) or {}
        labels = [
            f"{area_id}: {_area_spring_label(model, name)}"
            for area_id, name in area_assignments.items()
            if (area := areas.get(area_id)) is not None and node_id in area.node_ids
        ]
        if labels:
            rows.append(("Area Spring", ", ".join(labels)))

    resolved = getattr(processed if processed is not None else model, "point_springs", None)
    if isinstance(resolved, list):
        for spring in resolved:
            if getattr(spring, "node_id", None) == node_id:
                rows.append(("Soil Spring", _nodal_spring_label(spring)))

    return rows


def spring_rows(model: Any, obj: Any, processed: Any = None) -> list:
    """``[(label, value), …]`` describing *obj*'s soil/foundation springs.

    A node reports every spring it connects to — its direct point spring, any
    line spring on a frame that ends at it, any area spring on an area it is a
    corner of, and (on a processed :class:`MeshModel`) the resolved nodal
    spring.  A frame reports its line spring; an area reports its area spring.
    Only rows with something to say are returned, so an unspringed entity gains
    no clutter.

    Args:
        model: A ``SAPModelData`` (the raw assignment source) or a
            ``MeshModel``, or ``None``.
        obj: The selected :class:`Node`, :class:`FrameElement` or
            :class:`AreaElement`.
        processed: Optional ``MeshModel`` supplying the resolved
            ``point_springs`` list.  When ``None``, resolved springs read from
            *model* (so a bare ``MeshModel`` still reports its nodal springs).

    Returns:
        One ``(name, value)`` pair per non-empty fact.
    """
    if obj is None:
        return []
    if model is None and processed is None:
        return []
    if isinstance(obj, Node):
        return _node_spring_rows(model, obj.node_id, processed=processed)
    if isinstance(obj, FrameElement):
        name = (getattr(model, "line_spring_assignments", None) or {}).get(obj.elem_id)
        return [("Line Spring", _line_spring_label(model, name))] if name else []
    if isinstance(obj, AreaElement):
        name = (getattr(model, "area_spring_assignments", None) or {}).get(obj.area_id)
        return [("Area Spring", _area_spring_label(model, name))] if name else []
    return []
