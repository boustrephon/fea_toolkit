"""Named selections — a saved Selection with a name, the workflow scoping store.

A named selection is a saved :class:`~fea_toolkit.model.selection.Selection`
(the workflow layer's scoping primitive), optionally pinned to explicit element
/ node ids from a graphical pick.  It is the unit a ``select`` verb stores and
a step's ``Selection.selections`` field references by name.
"""

from dataclasses import dataclass, field, replace
from typing import Any

from .selection import Selection

__all__ = ["NamedSelection", "resolve_named_selections"]

#: Every criterion field of :class:`Selection` — used to tell an *unrestricted*
#: (match-everything) selection from one that scopes to something.
_CRITERIA_FIELDS = (
    "element_types",
    "sections",
    "materials",
    "groups",
    "constraints",
    "element_ids",
    "node_ids",
    "elevation_range",
    "story",
    "exclude_element_types",
    "exclude_sections",
    "exclude_materials",
    "exclude_groups",
    "exclude_constraints",
    "exclude_element_ids",
    "exclude_node_ids",
    "exclude_elevation_range",
    "exclude_story",
)


def _is_unrestricted(selection: "Selection") -> bool:
    """Whether *selection* carries no criteria at all (matches everything)."""
    return all(getattr(selection, field) is None for field in _CRITERIA_FIELDS)


@dataclass
class NamedSelection:
    """A saved selection, keyed by name.

    Attributes:
        name: The selection's name (its key in the document's store).
        selection: The criteria, authored as a ``Selection`` (empty when only
            explicit ids were picked).
        element_ids: Explicit frame/area ids from a graphical pick.
        node_ids: Explicit node ids from a graphical pick.
    """

    name: str
    selection: Selection = field(default_factory=Selection)
    element_ids: list[str] = field(default_factory=list)
    node_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """A JSON-native mapping — the ``select`` verb's serialised form."""
        return {
            "name": self.name,
            "selection": self.selection.to_string() or None,
            "element_ids": list(self.element_ids),
            "node_ids": list(self.node_ids),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NamedSelection":
        """Rebuild a named selection from :meth:`to_dict` output."""
        expression = data.get("selection")
        return cls(
            name=str(data.get("name", "")),
            selection=Selection.from_string(expression) if expression else Selection(),
            element_ids=list(data.get("element_ids") or []),
            node_ids=list(data.get("node_ids") or []),
        )


def resolve_named_selections(
    selection: Selection,
    named: dict[str, "NamedSelection"],
    model: Any,
) -> Selection:
    """Expand a selection's named-selection references into explicit ids.

    The ``Selection.selections`` field names saved selections whose members are
    *included*, and ``Selection.exclude_selections`` names those whose members
    are *excluded*; this is the single place the verb layer resolves them,
    merging each named selection's criteria and picked ids into the caller's
    ``element_ids`` / ``node_ids`` (and ``exclude_element_ids`` /
    ``exclude_node_ids``).  The returned selection has ``selections`` and
    ``exclude_selections`` cleared so the reference is consumed, never passed on
    unresolved.

    Args:
        selection: The selection to resolve (its ``selections`` and
            ``exclude_selections`` fields are read).
        named: The document's ``{name: NamedSelection}`` store.
        model: The model each named selection's criteria resolve against.

    Returns:
        A new :class:`Selection` with ``selections`` / ``exclude_selections``
        cleared and the named selections' ids merged in.  References to unknown
        names are ignored, except that a *positive* reference resolving to
        nothing (with no other criterion) becomes an explicit empty scope —
        ``element_ids=[]`` / ``node_ids=[]`` — so a consumer like
        ``scale_sections`` never reads it as an unrestricted (match-everything)
        selection.
    """
    if not selection.selections and not selection.exclude_selections:
        return selection

    def _ids(ns: "NamedSelection") -> tuple[list[str], list[str]]:
        """A named selection's (elements, nodes) — explicit ids plus criteria."""
        elements = list(ns.element_ids)
        nodes = list(ns.node_ids)
        if ns.selection.to_string():
            elements.extend(ns.selection.get_frame_ids(model))
            elements.extend(ns.selection.get_area_ids(model))
            nodes.extend(ns.selection.get_node_ids(model))
        return elements, nodes

    extra_elements: list[str] = []
    extra_nodes: list[str] = []
    for name in selection.selections or []:
        ns = named.get(name)
        if ns is None:
            continue
        elements, nodes = _ids(ns)
        extra_elements.extend(elements)
        extra_nodes.extend(nodes)

    extra_exclude_elements: list[str] = []
    extra_exclude_nodes: list[str] = []
    for name in selection.exclude_selections or []:
        ns = named.get(name)
        if ns is None:
            continue
        elements, nodes = _ids(ns)
        extra_exclude_elements.extend(elements)
        extra_exclude_nodes.extend(nodes)

    resolved = replace(
        selection,
        selections=None,
        exclude_selections=None,
        element_ids=((selection.element_ids or []) + extra_elements) or None,
        node_ids=((selection.node_ids or []) + extra_nodes) or None,
        exclude_element_ids=((selection.exclude_element_ids or []) + extra_exclude_elements)
        or None,
        exclude_node_ids=((selection.exclude_node_ids or []) + extra_exclude_nodes) or None,
    )
    # A positive reference that resolves to nothing must not degrade into an
    # unrestricted (match-everything) scope — a consumer like ``scale_sections``
    # reads "no criteria" as "every section".  Only positive references are
    # guarded: an exclusion-only selection resolving to nothing is legitimately
    # everything.
    if selection.selections and _is_unrestricted(resolved):
        return replace(
            selection, selections=None, exclude_selections=None, element_ids=[], node_ids=[]
        )
    return resolved
