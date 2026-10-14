"""Qt-free description of what the model tree contains.

The tree's *structure* -- which groups exist, how many entities each holds and
what each entity is called -- is pure data logic, so it lives here and is unit
-tested without Qt.  :mod:`fea_toolkit.gui.models.tree_model` is the thin Qt
adapter that materialises rows lazily as the user expands them.
"""

from dataclasses import dataclass
from typing import Any, Callable, Optional

__all__ = [
    "VIEWS_GROUP_KEY",
    "TreeGroup",
    "build_groups",
    "element_label",
    "load_label",
    "shell_uniform_load_sets_group",
    "views_group",
]

#: Group key of the synthetic **Views** group (UI state, not a model attribute).
VIEWS_GROUP_KEY = "views"


@dataclass(frozen=True)
class TreeGroup:
    """One logical group of model entities (Nodes, Materials, ...).

    Attributes:
        key: Source attribute name; also identifies the group's rows.
        label: Display label, e.g. ``"Frame Elements"``.
        count: Number of entities in the group.
        load_items: Returns ``[(label, entity), ...]``.  Called only when the
            group is first expanded, so a large group costs nothing until it
            is opened.
        child_items: Optional callable mapping a top-level *entity* to its own
            ``[(label, child_entity), ...]`` children.  Used by the **Views**
            group to nest a fan-out child (a mode shape, an RS direction, a
            pushover direction) under its parent view.  ``None`` for the flat
            model-entity groups.
    """

    key: str
    label: str
    count: int
    load_items: Callable[[], list]
    child_items: Optional[Callable[[Any], list]] = None


# Dictionary-valued groups: the key *is* the SAP identifier, so it labels the row.
_DICT_GROUPS = (
    ("nodes", "Nodes"),
    ("frame_elements", "Frame Elements"),
    ("area_elements", "Area Elements"),
    ("wall_elements", "Wall Elements"),
    ("materials", "Materials"),
    ("sections", "Sections"),
    ("groups", "Groups"),
    ("restraints", "Restraints"),
    ("load_patterns", "Load Patterns"),
    ("load_cases", "Load Cases"),
    ("load_combinations", "Load Combinations"),
    ("mass_sources", "Mass Sources"),
    ("response_spectrum_functions", "Response Spectra"),
)

# List-valued groups need a labeller (they carry no dictionary key).
_LIST_GROUPS = (
    ("joint_loads", "Joint Loads", "load"),
    ("frame_dist_loads", "Frame Distributed Loads", "load"),
    ("area_uniform_loads", "Area Uniform Loads", "load"),
    ("frame_gravity_loads", "Frame Gravity Loads", "load"),
    ("area_gravity_loads", "Area Gravity Loads", "load"),
)


def element_label(obj: Any) -> str:
    """Label an identified entity (element, area, node, or named object)."""
    for attr in ("elem_id", "area_id", "node_id", "name", "id"):
        value = getattr(obj, attr, None)
        if value:
            return str(value)
    return type(obj).__name__


def load_label(obj: Any) -> str:
    """Label an applied load as ``"<pattern> @ <target>"`` when available."""
    pattern = getattr(obj, "pattern", None)
    target: Optional[Any] = None
    for attr in ("frame_id", "area_id", "node_id"):
        target = getattr(obj, attr, None)
        if target:
            break
    parts = [str(part) for part in (pattern, target) if part]
    return " @ ".join(parts) if parts else type(obj).__name__


def _labeller(kind: str) -> Callable[[Any], str]:
    return load_label if kind == "load" else element_label


def _dict_loader(bag: dict) -> Callable[[], list]:
    def load() -> list:
        return [(str(key), obj) for key, obj in bag.items()]

    return load


def _list_loader(seq: list, labeller: Callable[[Any], str]) -> Callable[[], list]:
    def load() -> list:
        return [(labeller(obj), obj) for obj in seq]

    return load


def views_group(views: list) -> TreeGroup:
    """The synthetic **Views** group: one row per top-level view, children nested.

    Views are UI state rather than model entities, so they are handed in rather
    than derived from the model — but they present exactly like any other group
    (a label, a count, lazily-materialised rows), so the tree needs no special
    case beyond placing this group first.

    A view whose :attr:`View.parent` names another registered view is nested
    under that parent rather than listed at the top, so the fan-out from one
    step (mode shapes, RS directions, pushover directions) collapses into a
    parent → children bundle instead of a flat soup.

    Args:
        views: The registered :class:`~fea_toolkit.gui.controllers.view_registry.View` objects.

    Returns:
        A :class:`TreeGroup` whose rows are the top-level views, with
        ``child_items`` supplying each parent's nested children.
    """
    by_key = {view.key: view for view in views}
    top_level = [view for view in views if not view.parent or view.parent not in by_key]
    children_by_parent: dict[str, list] = {}
    for view in views:
        if view.parent and view.parent in by_key:
            children_by_parent.setdefault(view.parent, []).append(view)

    def child_items(parent_view: Any) -> list:
        return [(child.name, child) for child in children_by_parent.get(parent_view.key, ())]

    return TreeGroup(
        key=VIEWS_GROUP_KEY,
        label="Views",
        count=len(views),
        load_items=lambda: [(view.name, view) for view in top_level],
        child_items=child_items,
    )


def shell_uniform_load_sets_group(model: Any) -> Optional[TreeGroup]:
    """Describe the **Shell Uniform Load Sets** group, or ``None`` when empty.

    One row per named set (``SAPModelData.shell_uniform_load_sets``), labelled by
    its name; the row's entity is the
    :class:`~fea_toolkit.model.sap_data.ShellUniformLoadSet` itself, so the
    inspector can show its per-pattern values.  Expanding a row lists the shell
    objects assigned to that set (``SAPModelData.shell_uniform_load_set_assignments``),
    surfacing the assignment in the tree rather than only as a string map.
    """
    sets = getattr(model, "shell_uniform_load_sets", None) or {}
    if not sets:
        return None
    assignments = getattr(model, "shell_uniform_load_set_assignments", None) or {}
    areas_by_set: dict[str, list] = {name: [] for name in sets}
    for area_id, set_name in assignments.items():
        bucket = areas_by_set.get(set_name)
        if bucket is not None:
            bucket.append(area_id)
    area_elements = getattr(model, "area_elements", None) or {}

    def load_items() -> list:
        return [(name, sets[name]) for name in sorted(sets)]

    def child_items(set_obj: Any) -> list:
        assigned = sorted(areas_by_set.get(set_obj.name, []))
        return [(aid, area_elements.get(aid, aid)) for aid in assigned]

    return TreeGroup(
        key="shell_uniform_load_sets",
        label="Shell Uniform Load Sets",
        count=len(sets),
        load_items=load_items,
        child_items=child_items,
    )


def build_groups(model: Any, views: Optional[list] = None) -> list:
    """Describe the tree groups for *model*.

    Args:
        model: A ``SAPModelData``, a ``MeshModel`` or an ``AnalysisBuilder``
            (whose ``.model`` is used).
        views: Registered views; rendered as a **Views** group ahead of the
            model's own groups.

    Returns:
        One :class:`TreeGroup` per **non-empty** group, in a stable order.
        Empty groups are omitted, so the tree never shows an empty row.
    """
    source = getattr(model, "model", model)  # AnalysisBuilder -> MeshModel
    groups: list = []

    if views:
        groups.append(views_group(list(views)))

    for attr, label in _DICT_GROUPS:
        bag = getattr(source, attr, None)
        if isinstance(bag, dict) and bag:
            groups.append(
                TreeGroup(key=attr, label=label, count=len(bag), load_items=_dict_loader(bag))
            )

    uniform_sets_group = shell_uniform_load_sets_group(source)
    if uniform_sets_group is not None:
        groups.append(uniform_sets_group)

    for attr, label, kind in _LIST_GROUPS:
        seq = getattr(source, attr, None)
        if isinstance(seq, (list, tuple)) and seq:
            groups.append(
                TreeGroup(
                    key=attr,
                    label=label,
                    count=len(seq),
                    load_items=_list_loader(list(seq), _labeller(kind)),
                )
            )

    return groups
