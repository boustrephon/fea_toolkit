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

from typing import Any, Optional

from .sap_data import AreaElement, FrameElement, Node

__all__ = ["load_rows"]


def _family_ids(model: Any, etype: str, eid: str, elem: Any = None) -> set[str]:
    """The selected element's split family: its ancestors, itself and its descendants.

    The family is the root→leaf *path* through the split hierarchy, not the
    whole root subtree — a sibling of *eid* is deliberately excluded, so a load
    authored on one child is not reported on its brother.  When the element
    collection does not contain *eid* (the Inspector is handed the raw, unsplit
    model while the tree shows a meshed child), the element's own ``parent_id``
    chain supplies the ancestors the raw collection still recognises.
    """
    coll = (
        getattr(model, "frame_elements", None) or {}
        if etype == "Frame"
        else getattr(model, "area_elements", None) or {}
    )
    if not coll:
        return {eid}
    from .tree_utils import collect_descendants, get_element_chain

    ids = set(get_element_chain(eid, coll))
    ids.update(collect_descendants(eid, coll))
    ids.add(eid)
    if eid not in coll and elem is not None:
        # Follow the element's own parent chain (the raw collection lacks the
        # split child) so the authored root is included.
        current = elem
        while current is not None:
            pid = getattr(current, "parent_id", None)
            if not pid:
                break
            ids.add(str(pid))
            parent = coll.get(str(pid))
            current = parent if parent is not None else None
    return ids


def load_rows(
    model: Any,
    obj: Any,
    pattern_factors: Optional[dict[str, float]] = None,
) -> list:
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
        pattern_factors: Optional ``{pattern: factor}`` narrowing and scaling
            the loads, matching the viewport's active load source — only loads
            whose pattern is a key are kept, and their magnitudes are
            multiplied by the factor.  ``None`` (the default) reports every
            load at its authored value.

    Returns:
        One ``(name, value)`` pair per load, or an empty list when the element
        carries none (or *model* is ``None``, e.g. a results archive).
    """
    if model is None or obj is None:
        return []

    def _kept(pattern: str) -> bool:
        return pattern_factors is None or pattern in pattern_factors

    def _scale(pattern: str) -> float:
        return pattern_factors[pattern] if pattern_factors is not None else 1.0

    if isinstance(obj, Node):
        eid = obj.node_id
        rows = []
        for jl in getattr(model, "joint_loads", None) or []:
            if str(getattr(jl, "node_id", "")) != eid:
                continue
            if not _kept(getattr(jl, "pattern", "")):
                continue
            scale = _scale(getattr(jl, "pattern", ""))
            for comp in ("fx", "fy", "fz"):
                value = float(getattr(jl, comp, 0.0) or 0.0) * scale
                if value:
                    rows.append((f"Load: {jl.pattern} ({comp})", f"{value:g}"))
        return rows

    if isinstance(obj, FrameElement):
        eid = obj.elem_id
        family = _family_ids(model, "Frame", eid, elem=obj)
        rows = []
        for dl in getattr(model, "frame_dist_loads", None) or []:
            if str(getattr(dl, "frame_id", "")) not in family:
                continue
            if getattr(dl, "inactive", False) and str(getattr(dl, "frame_id", "")) != eid:
                continue
            if not _kept(getattr(dl, "pattern", "")):
                continue
            scale = _scale(getattr(dl, "pattern", ""))
            rows.append(
                (
                    f"Load: {dl.pattern} ({dl.direction} {dl.shape})",
                    f"{dl.val_a * scale:g} → {dl.val_b * scale:g}",
                )
            )
        for pl in getattr(model, "frame_point_loads", None) or []:
            if str(getattr(pl, "frame_id", "")) not in family:
                continue
            if getattr(pl, "inactive", False) and str(getattr(pl, "frame_id", "")) != eid:
                continue
            if not _kept(getattr(pl, "pattern", "")):
                continue
            scale = _scale(getattr(pl, "pattern", ""))
            rows.append(
                (
                    f"Load: {pl.pattern} ({pl.direction} {pl.load_type})",
                    f"{pl.value * scale:g} @ {pl.rdist:g}L",
                )
            )
        return rows

    if isinstance(obj, AreaElement):
        family = _family_ids(model, "Area", obj.area_id, elem=obj)
        rows = []
        for al in getattr(model, "area_uniform_loads", None) or []:
            if str(getattr(al, "area_id", "")) not in family:
                continue
            if not _kept(getattr(al, "pattern", "")):
                continue
            scale = _scale(getattr(al, "pattern", ""))
            rows.append((f"Load: {al.pattern} ({al.direction})", f"{al.value * scale:g}"))
        return rows

    return []
