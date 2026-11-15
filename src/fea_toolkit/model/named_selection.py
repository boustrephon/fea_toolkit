"""Named selections — a saved Selection with a name, the workflow scoping store.

A named selection is a saved :class:`~fea_toolkit.model.selection.Selection`
(the workflow layer's scoping primitive), optionally pinned to explicit element
/ node ids from a graphical pick.  It is the unit a ``select`` verb stores and
a step's ``Selection.selections`` field references by name.
"""

from dataclasses import dataclass, field, fields, replace
from typing import Any, Optional

from .selection import Selection

__all__ = ["NamedSelection", "resolve_named_selections"]

#: The ``Selection`` dataclass fields a named selection round-trips, in
#: declaration order.  Serialising field-by-field (not via ``to_string``) keeps
#: an *empty* criterion list distinct from ``None``: ``None`` means "unrestricted"
#: while ``[]`` means "matches nothing", and ``to_string`` erases that difference.
_SELECTION_FIELDS: tuple[str, ...] = tuple(f.name for f in fields(Selection))


def _selection_to_payload(selection: Selection) -> Optional[dict]:
    """A JSON-native snapshot of *selection* that keeps empty lists.

    ``elevation_range`` / ``exclude_elevation_range`` tuples become plain lists so
    ``json.dumps`` can serialise them; every other set criterion is copied as a
    list.  An empty selection (every field ``None``) serialises as ``None``.
    """
    payload: dict = {}
    for name in _SELECTION_FIELDS:
        value = getattr(selection, name)
        if value is None:
            continue
        if name in ("elevation_range", "exclude_elevation_range"):
            payload[name] = [float(v) for v in value]
        else:
            payload[name] = list(value)
    return payload or None


def _selection_from_payload(raw: Any) -> Selection:
    """Rebuild a ``Selection`` from a field payload, an expression, or ``None``.

    The field-payload form is what :meth:`NamedSelection.to_dict` writes; the
    expression form (``str``) and ``None`` accept documents written before the
    field-payload form existed.
    """
    if raw is None:
        return Selection()
    if isinstance(raw, str):
        return Selection.from_string(raw) if raw else Selection()
    kwargs: dict = {}
    for name, value in raw.items():
        if name in ("elevation_range", "exclude_elevation_range"):
            kwargs[name] = tuple(float(v) for v in value)
        else:
            kwargs[name] = list(value)
    return Selection(**kwargs)


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
        """A JSON-native mapping — the ``select`` verb's serialised form.

        The ``Selection`` is snapshotted field-by-field (not via ``to_string``)
        so an empty criterion list stays distinct from ``None`` — see
        :func:`_selection_to_payload`.
        """
        return {
            "name": self.name,
            "selection": _selection_to_payload(self.selection),
            "element_ids": list(self.element_ids),
            "node_ids": list(self.node_ids),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NamedSelection":
        """Rebuild a named selection from :meth:`to_dict` output.

        Accepts the field-payload form written by :meth:`to_dict` and, for
        documents written before that form existed, a bare expression string or
        ``None``.
        """
        return cls(
            name=str(data.get("name", "")),
            selection=_selection_from_payload(data.get("selection")),
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
        cleared and the named selections' ids merged in.  A named selection
        whose own criteria reference further selections is resolved recursively
        before its ids are read.  Criteria-derived frame/area ids are
        type-qualified (``"Frame:…"`` / ``"Area:…"``) so a frame and an area
        that share a SAP label stay distinct, while explicitly picked ids are
        kept as authored.  Criteria-derived **node** ids are merged only when
        the resolved sub-selection explicitly selects nodes (a section /
        material / elevation criterion would otherwise trivially match every
        joint).  References to unknown names are ignored, except that
        a *positive* reference resolving to no members keeps an empty list for
        that entity kind (``element_ids=[]`` / ``node_ids=[]``) — never
        ``None`` — so a consumer like ``scale_sections`` reads it as an empty
        scope, not an unrestricted (match-everything) selection.

    Raises:
        ValueError: If a named selection (transitively) references itself.
    """
    if not selection.selections and not selection.exclude_selections:
        return selection

    story_cache: dict[str, dict[str, float]] = {}

    def _story_elevations() -> dict[str, float]:
        """The model's ``{story name: elevation}`` map, computed lazily.

        ``identify_stories`` reads the imported ETABS story levels from
        ``SAPModelData.stories`` (parsed from ``$ STORIES - IN SEQUENCE FROM
        TOP``), so authored ``story`` / ``exclude_story`` criteria match the
        imported names; for a SAP2000 / SAFE model it falls back to the
        generated names (``Storey N`` / ``Roof``).
        """
        if "elevations" not in story_cache:
            from .stories import identify_stories

            story_cache["elevations"] = {s.name: s.elevation for s in identify_stories(model)}
        return story_cache["elevations"]

    def _expand(ns: "NamedSelection", seen: tuple) -> tuple[list[str], list[str]]:
        """A named selection's (elements, nodes) with its own references resolved.

        ``seen`` is the reference chain walked to reach *ns*; a name already on
        it is a cycle (a selection that transitively references itself) and is
        rejected rather than recursed into forever.
        """
        if ns.name in seen:
            raise ValueError(
                f"named selection {ns.name!r} references itself "
                f"(cycle: {' -> '.join((*seen, ns.name))})"
            )
        elements = list(ns.element_ids)
        nodes = list(ns.node_ids)
        sub = ns.selection
        if sub is not None and sub.to_string():
            resolved_sub = _resolve(sub, (*seen, ns.name))
            # Story-aware matching: a named selection carrying a ``story`` /
            # ``exclude_story`` criterion resolves against the model's storey
            # elevations, so the saved story scope is honoured instead of being
            # dropped (``get_frame_ids`` without elevations returns nothing for a
            # story criterion).
            story_elevations = (
                _story_elevations()
                if resolved_sub.story is not None or resolved_sub.exclude_story is not None
                else None
            )
            # Type-qualify the criteria-derived ids so a frame and an area that
            # share a SAP label stay distinct (``Selection`` matches both the
            # bare ``"1"`` and the ``"Frame:1"`` / ``"Area:2"`` forms); ids the
            # caller picked explicitly are kept as authored.
            elements.extend(
                f"Frame:{eid}" for eid in resolved_sub.get_frame_ids(model, story_elevations)
            )
            elements.extend(
                f"Area:{aid}" for aid in resolved_sub.get_area_ids(model, story_elevations)
            )
            # Node ids come only from an explicit node selection: a section /
            # material / elevation criterion matches every node trivially (nodes
            # ignore those criteria), so reading ``get_node_ids`` there would
            # drag unrelated joints into the scope.
            if resolved_sub.selects_nodes_explicitly():
                nodes.extend(resolved_sub.get_node_ids(model))
        return elements, nodes

    def _resolve(sel: Selection, seen: tuple) -> Selection:
        """Resolve *sel*'s direct references (transitively) into id lists."""
        if not sel.selections and not sel.exclude_selections:
            return sel
        extra_elements: list[str] = []
        extra_nodes: list[str] = []
        for name in sel.selections or []:
            ns = named.get(name)
            if ns is None:
                continue
            elements, nodes = _expand(ns, seen)
            extra_elements.extend(elements)
            extra_nodes.extend(nodes)
        extra_exclude_elements: list[str] = []
        extra_exclude_nodes: list[str] = []
        for name in sel.exclude_selections or []:
            ns = named.get(name)
            if ns is None:
                continue
            elements, nodes = _expand(ns, seen)
            extra_exclude_elements.extend(elements)
            extra_exclude_nodes.extend(nodes)
        # A node-inclusive selection cannot carry ``element_ids`` /
        # ``exclude_element_ids`` without the matching node field (its validation
        # invariant).  ``exclude_element_ids`` names frames/areas only, so when a
        # named exclusion contributes element ids but the caller's selection is
        # node-*only*, drop them — they cannot scope a node selection — rather
        # than setting an exclusion field that violates the invariant.  A mixed
        # Node/Frame (or Node/Area) selection still has frames/areas eligible,
        # so the element exclusions are kept — and the node field is forced to an
        # empty list when needed so the invariant holds.
        node_scoped = sel.element_types is not None and "Node" in sel.element_types
        node_only = node_scoped and not (
            "Frame" in sel.element_types or "Area" in sel.element_types
        )
        merged_exclude_elements = (sel.exclude_element_ids or []) + extra_exclude_elements
        merged_exclude_nodes = (sel.exclude_node_ids or []) + extra_exclude_nodes
        if node_only:
            exclude_element_ids = sel.exclude_element_ids
            exclude_node_ids = merged_exclude_nodes or None
        else:
            exclude_element_ids = merged_exclude_elements or None
            exclude_node_ids = merged_exclude_nodes or None
            if node_scoped and exclude_element_ids is not None and exclude_node_ids is None:
                exclude_node_ids = []
        return replace(
            sel,
            selections=None,
            exclude_selections=None,
            # A positive reference must never degrade into "match everything":
            # when the caller named selections but they resolve to no members,
            # keep the empty list so the entity kind is scoped to nothing, not
            # every entity of that kind.  An exclusion-only selection leaves its
            # positive ids untouched (``None`` = unrestricted).
            element_ids=(
                (sel.element_ids or []) + extra_elements if sel.selections else sel.element_ids
            ),
            node_ids=(sel.node_ids or []) + extra_nodes if sel.selections else sel.node_ids,
            exclude_element_ids=exclude_element_ids,
            exclude_node_ids=exclude_node_ids,
        )

    return _resolve(selection, ())
