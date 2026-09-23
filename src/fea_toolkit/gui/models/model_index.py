"""Qt-free description of what the model tree contains.

The tree's *structure* -- which groups exist, how many entities each holds and
what each entity is called -- is pure data logic, so it lives here and is unit
-tested without Qt.  :mod:`fea_toolkit.gui.models.tree_model` is the thin Qt
adapter that materialises rows lazily as the user expands them.
"""

from dataclasses import dataclass
from typing import Any, Callable, Optional

__all__ = ["TreeGroup", "build_groups", "element_label", "load_label"]


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
    """

    key: str
    label: str
    count: int
    load_items: Callable[[], list]


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


def build_groups(model: Any) -> list:
    """Describe the tree groups for *model*.

    Args:
        model: A ``SAPModelData``, a ``MeshModel`` or an ``AnalysisBuilder``
            (whose ``.model`` is used).

    Returns:
        One :class:`TreeGroup` per **non-empty** group, in a stable order.
        Empty groups are omitted, so the tree never shows an empty row.
    """
    source = getattr(model, "model", model)  # AnalysisBuilder -> MeshModel
    groups: list = []

    for attr, label in _DICT_GROUPS:
        bag = getattr(source, attr, None)
        if isinstance(bag, dict) and bag:
            groups.append(
                TreeGroup(key=attr, label=label, count=len(bag), load_items=_dict_loader(bag))
            )

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
