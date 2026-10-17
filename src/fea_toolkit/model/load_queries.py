"""Applied loads on an element, for the Inspector (Qt-free, OpenSees-free).

The Inspector describes one *object*, but loads live on the **model** — keyed
by element id — not on the element dataclass.  This module turns those model
facts into the ``(name, value)`` rows the Inspector appends, exactly as
:mod:`~fea_toolkit.model.element_context` does for an element's section /
material and :mod:`~fea_toolkit.model.supports` does for a node's restraints.

A load references an *authored* element id.  Splitting redistributes loads to
leaf sub-elements, and a meshed area's pressure stays on its authored panel id,
so a load is shown on a selected element when its id lies in the selected
element's whole split **family** — the root, the selected id itself and every
descendant leaf — rather than on the exact id alone.
"""

from typing import Any

from .sap_data import AreaElement, FrameElement, Node

__all__ = ["load_rows"]


def _family_ids(model: Any, etype: str, eid: str) -> set[str]:
    """The selected element's split family: root, self and every leaf descendant."""
    coll = (
        getattr(model, "frame_elements", None) or {}
        if etype == "Frame"
        else getattr(model, "area_elements", None) or {}
    )
    if not coll:
        return {eid}
    from .tree_utils import collect_descendants, get_root_parent

    root = get_root_parent(eid, coll) or eid
    ids = set(collect_descendants(root, coll))
    ids.add(root)
    ids.add(eid)
    return ids


def load_rows(model: Any, obj: Any) -> list:
    """``[(name, value), ...]`` for every applied load on *obj*, or ``[]``.

    A node reports its joint-load force components; a frame its distributed and
    point loads; an area its shell-face uniform pressures.  Each row's name is a
    ``"Load: <pattern> (…)"`` label and its value a compact magnitude string, so
    the Inspector can list several loads without key collisions.  Gravity loads
    (self-weight multipliers) are omitted — they carry no direct magnitude.

    Args:
        model: A ``SAPModelData`` or ``MeshModel``, or ``None``.
        obj: The selected :class:`FrameElement`, :class:`AreaElement` or
            :class:`Node`.

    Returns:
        One ``(name, value)`` pair per load, or an empty list when the element
        carries none (or *model* is ``None``, e.g. a results archive).
    """
    if model is None or obj is None:
        return []

    if isinstance(obj, Node):
        eid = obj.node_id
        rows = []
        for jl in getattr(model, "joint_loads", None) or []:
            if str(getattr(jl, "node_id", "")) != eid:
                continue
            for comp in ("fx", "fy", "fz"):
                value = float(getattr(jl, comp, 0.0) or 0.0)
                if value:
                    rows.append((f"Load: {jl.pattern} ({comp})", f"{value:g}"))
        return rows

    if isinstance(obj, FrameElement):
        family = _family_ids(model, "Frame", obj.elem_id)
        rows = []
        for dl in getattr(model, "frame_dist_loads", None) or []:
            if str(getattr(dl, "frame_id", "")) not in family:
                continue
            rows.append(
                (
                    f"Load: {dl.pattern} ({dl.direction} {dl.shape})",
                    f"{dl.val_a:g} → {dl.val_b:g}",
                )
            )
        for pl in getattr(model, "frame_point_loads", None) or []:
            if str(getattr(pl, "frame_id", "")) not in family:
                continue
            rows.append(
                (
                    f"Load: {pl.pattern} ({pl.direction} {pl.load_type})",
                    f"{pl.value:g} @ {pl.rdist:g}L",
                )
            )
        return rows

    if isinstance(obj, AreaElement):
        family = _family_ids(model, "Area", obj.area_id)
        rows = []
        for al in getattr(model, "area_uniform_loads", None) or []:
            if str(getattr(al, "area_id", "")) not in family:
                continue
            rows.append((f"Load: {al.pattern} ({al.direction})", f"{al.value:g}"))
        return rows

    return []
