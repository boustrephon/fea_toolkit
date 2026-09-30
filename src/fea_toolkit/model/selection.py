"""Flexible selection/filter criteria for SAP2000 model elements."""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional, Union

if TYPE_CHECKING:
    from .mesh_model import MeshModel
    from .sap_data import (
        AreaElement,
        AreaGravityLoad,
        AreaUniformLoad,
        FrameElement,
        Group,
        Node,
        SAPModelData,
    )
    from .stories import StoryLevel


#: ``KEY=VALUE`` aliases accepted by :meth:`Selection.from_string`.
SELECT_KEYS: dict[str, str] = {
    "type": "element_types",
    "types": "element_types",
    "element_types": "element_types",
    "section": "sections",
    "sections": "sections",
    "material": "materials",
    "materials": "materials",
    "group": "groups",
    "groups": "groups",
    "constraint": "constraints",
    "constraints": "constraints",
    "id": "element_ids",
    "ids": "element_ids",
    "element_ids": "element_ids",
    "node": "node_ids",
    "nodes": "node_ids",
    "node_ids": "node_ids",
    "z": "elevation_range",
    "elevation": "elevation_range",
    "elevation_range": "elevation_range",
    "story": "story",
    "stories": "story",
}

#: Human-readable key list, for CLI help and error messages.
SELECT_KEYS_HELP = "type, section, material, group, constraint, id, node, z"

#: Field → canonical expression key, in the order :meth:`Selection.to_string`
#: emits them.  The short forms are the ones the help text and the examples in
#: this file use, so an expression round-trips through ``from_string`` unchanged.
SELECT_FIELD_KEYS: dict[str, str] = {
    "element_types": "type",
    "sections": "section",
    "materials": "material",
    "groups": "group",
    "constraints": "constraint",
    "element_ids": "id",
    "node_ids": "node",
    "story": "story",
    "elevation_range": "z",
}

#: A clause key: an ASCII word starting with a letter or underscore.
_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

#: A ``KEY=`` fragment — what would open a new clause if written plainly.
_CLAUSE_FRAGMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\s*=")

#: The ``NOT`` keyword that negates the clause it precedes.  Whole-word and
#: case-insensitive, so ``NOTCH`` / ``NOTIONAL`` are values, not keywords.
_NOT_RE = re.compile(r"not(?![A-Za-z0-9_])", re.IGNORECASE)

#: A value ending in a standalone ``NOT`` token (``"COL NOT"``).  Quoting it
#: keeps the trailing ``NOT`` as data, not a negation keyword, when a later
#: clause follows it in the expression.
_TRAILING_NOT_RE = re.compile(r"\snot$", re.IGNORECASE)


def _clause_key_pos(expr: str, i: int) -> int:
    """The index of a clause key starting at *i*, skipping any ``NOT`` prefix.

    Returns the position where the ``KEY=`` fragment begins, or ``-1`` when
    neither a ``KEY=`` nor a ``NOT KEY=`` clause starts at *i*.
    """
    n = len(expr)
    j = i
    not_match = _NOT_RE.match(expr, j)
    if not_match is not None:
        j = not_match.end()
        while j < n and (expr[j].isspace() or expr[j] == ";"):
            j += 1
    ahead = _KEY_RE.match(expr, j)
    if ahead is not None and ahead.end() < n and expr[ahead.end()] == "=":
        return j
    return -1


def _needs_quoting(value: str) -> bool:
    """Whether *value* must be quoted to survive a round-trip through parsing.

    A comma or semicolon would be read as a delimiter, a ``KEY=`` fragment would
    open a new clause, a quote or backslash would be read as an escape, and
    leading or trailing whitespace would be trimmed.  An empty value is quoted
    too, so it is not silently dropped.  A value ending in a standalone ``NOT``
    token is quoted so a following clause is not read as a negation of itself.
    """
    if not value or value != value.strip():
        return True
    if any(ch in value for ch in (",", ";", '"', "\\")):
        return True
    if _TRAILING_NOT_RE.search(value):
        return True
    return _CLAUSE_FRAGMENT_RE.search(value) is not None


def _quote(value: str) -> str:
    """Wrap *value* in double quotes, escaping ``\\`` and ``"``."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _render_value(value: str, *, follows_value: bool = False) -> str:
    """Format a value for an expression, quoting it only when it must be.

    ``follows_value`` is ``True`` when *value* is not the first item of its
    comma-separated list.  A standalone ``NOT`` there must be quoted even
    though :func:`_needs_quoting` passes it, so the ``"…, NOT"`` tail of the
    list is not read by :func:`_scan_clauses` as the negation of a following
    clause.  ``_NOT_RE.fullmatch`` matches exactly the keyword — ``NOTCH`` /
    ``NOTIONAL`` stay unquoted values.

    Args:
        value: The value to format.
        follows_value: ``True`` when *value* is not the first item of its
            comma-separated list, in which case a standalone ``NOT`` value is
            quoted.

    Returns:
        The value as it appears in the expression, double-quoted only when
        required to survive a round-trip through parsing.
    """
    text = str(value)
    needs_quoting = _needs_quoting(text) or (follows_value and _NOT_RE.fullmatch(text) is not None)
    return _quote(text) if needs_quoting else text


def _scan_clauses(expr: str) -> tuple[list[tuple[bool, str, str]], str]:
    """Split *expr* into ``(negated, key, raw value)`` clauses plus any leftover.

    Quote-aware: a ``;``, ``,`` or `` word=`` inside a double-quoted value is
    data, not a delimiter, so a value written by :meth:`Selection.to_string`
    survives the round-trip.  The first run of text that is not part of a
    ``KEY=VALUE`` clause is returned as the *leftover*, for the caller to report
    rather than silently ignore.

    A ``NOT`` token immediately before a clause negates it (``NOT section=COL``);
    the ``negated`` flag is ``True`` for that clause.  ``NOT`` is recognised only
    at clause start, so a *value* named ``NOT`` (``section=NOT``) is untouched.

    Args:
        expr: The expression to scan.

    Returns:
        ``(clauses, leftover)`` — the parsed ``(negated, key, raw value)``
        triples, and any text the scanner could not read as a clause (``""`` when
        it consumed everything).

    Raises:
        ValueError: If a quoted value is left unclosed at the end of *expr*.
    """
    clauses: list[tuple[bool, str, str]] = []
    i, n = 0, len(expr)
    while i < n:
        while i < n and (expr[i].isspace() or expr[i] == ";"):
            i += 1
        if i >= n:
            break
        negated = False
        not_match = _NOT_RE.match(expr, i)
        if not_match is not None:
            k = not_match.end()
            while k < n and (expr[k].isspace() or expr[k] == ";"):
                k += 1
            ahead = _KEY_RE.match(expr, k)
            if ahead is not None and ahead.end() < n and expr[ahead.end()] == "=":
                negated = True
                i = k
            else:
                # NOT with no clause after it — report it rather than ignore it.
                return clauses, expr[not_match.start() :].strip().strip(";").strip()
        match = _KEY_RE.match(expr, i)
        if match is None or match.end() >= n or expr[match.end()] != "=":
            start = i if match is None else match.start()
            return clauses, expr[start:].strip().strip(";").strip()
        key = match.group(0)
        i = match.end() + 1  # past the '='

        value_start = i
        quote = False
        while i < n:
            ch = expr[i]
            if quote:
                if ch == "\\":
                    i += 2
                    continue
                if ch == '"':
                    quote = False
                i += 1
                continue
            if ch == '"':
                quote = True
                i += 1
                continue
            if ch == ";":
                break
            if ch.isspace():
                # A `` word=`` or ``NOT word=`` ahead means the current clause
                # ends here.
                j = i
                while j < n and expr[j].isspace():
                    j += 1
                if _clause_key_pos(expr, j) != -1:
                    break
            i += 1
        if quote:
            raise ValueError(
                f"unterminated quote in selection expression {expr!r} "
                "(missing closing double-quote)"
            )
        clauses.append((negated, key, expr[value_start:i]))
    return clauses, ""


def _split_values(raw: str) -> list[str]:
    """Split a clause's raw value on unquoted commas and unquote each item.

    A quoted item keeps its content verbatim (``\\`` and ``"`` unescaped); an
    unquoted item is trimmed, matching the grammar's tolerance for
    ``section=Slab 200mm``.

    Args:
        raw: The text after ``KEY=`` up to the clause boundary.

    Returns:
        The item values, in order, with empty items dropped.

    Raises:
        ValueError: If a quoted item is followed by anything other than
            whitespace, a comma or the end of *raw*.
    """
    values: list[str] = []
    i, n = 0, len(raw)
    while i < n:
        # Skip the padding around an item (``", "`` between items).
        while i < n and raw[i].isspace():
            i += 1
        if i < n and raw[i] == '"':
            # Quoted item: content verbatim, ``\`` and ``"`` unescaped.
            i += 1
            buf: list[str] = []
            while i < n and raw[i] != '"':
                if raw[i] == "\\" and i + 1 < n:
                    i += 1
                buf.append(raw[i])
                i += 1
            i += 1  # closing quote
            values.append("".join(buf))
            # Only whitespace may follow a quoted item before the next comma or
            # the end of the value.  Anything else (``"foo"bar``) is malformed
            # input and is rejected rather than silently discarded.
            j = i
            while j < n and raw[j].isspace():
                j += 1
            if j < n and raw[j] != ",":
                raise ValueError(f"unexpected text after quoted value in {raw!r}")
        else:
            start = i
            while i < n and raw[i] != ",":
                i += 1
            item = raw[start:i].strip()
            if item:
                values.append(item)
        # Skip to the next comma.
        while i < n and raw[i] != ",":
            i += 1
        if i < n:
            i += 1  # consume the comma
    return values


def _canonical_element_type(value: str) -> str:
    """Return the canonical element-type name for *value*.

    ``Selection`` matches ``Frame`` / ``Area`` / ``Node`` exactly, so the
    expression form accepts any casing and normalises here.

    Raises:
        ValueError: If *value* is not one of those three types.
    """
    for known in ("Frame", "Area", "Node"):
        if value.lower() == known.lower():
            return known
    raise ValueError(f"unknown element type {value!r} (expected Frame, Area or Node)")


@dataclass
class Selection:
    """Flexible criteria for selecting elements from a SAP2000 model.

    **Logic rules**

    *AND across criteria* — every non-``None`` field narrows the selection
    further.  An element must satisfy **all** of them to be included:

        Selection(element_types=['Area'], sections=['Roof slab'])
        # → element must be an Area AND have section "Roof slab"

    *OR within a list* — multiple values in the same field are alternatives.
    An element matching **any** of them passes that criterion:

        Selection(element_types=['Frame', 'Area'])
        # → element can be a Frame OR an Area (or both)

        Selection(sections=['Roof slab', 'Floor slab'])
        # → element section can be "Roof slab" OR "Floor slab"

    *Exclusion* — each ``exclude_*`` field is the mirror of its positive
    counterpart: an element that matches **any** set exclusion rule is
    removed.  In the expression language this is spelled ``NOT KEY=VALUE``:

        Selection(exclude_sections=['COL'])
        # → everything except elements whose section is "COL"

        Selection(element_types=['Area'], exclude_sections=['Roof slab'])
        # → areas, except those with section "Roof slab"

    ``NOT`` applies to the single clause it precedes, so ``NOT section=COL
    NOT type=Area`` removes both the COL sections and the areas — the union
    of the excluded sets, not their intersection.

    *Type-specific behaviour*

    - **Frame** and **Area** elements check ``section`` and ``material``
      criteria via their respective assignment maps
      (:attr:`SAPModelData.frame_assignments` /
      :attr:`SAPModelData.area_assignments`).
    - **Node** elements ignore ``section`` and ``material`` (they have
      none).  They match on ``element_types``, ``groups``, ``node_ids``,
      and ``constraints``.  ``element_ids`` / ``exclude_element_ids`` name
      frames and areas only, so a node selection that sets either without the
      corresponding ``node_ids`` / ``exclude_node_ids`` is rejected.
    - **Joint constraints** (:attr:`constraints`) apply to **Node**
      elements only — a joint either carries one of the named constraint
      assignments or it does not.  A frame or area can never carry a
      *joint* constraint, so setting this criterion **excludes** them
      (ignoring it instead would select the whole model whenever the
      constraint set is the only criterion).
    - **Group** membership is tested against :class:`Group` objects, which
      store references like ``"Frame:123"``, ``"Area:456"``, ``"Joint:1"``.
    - When ``element_types`` is ``None`` (default), **all** element types
      are eligible — use this to filter by section / material / group alone
      regardless of type.

    *Source coverage*

    Not every model representation carries every attribute, so which source a
    criterion can resolve against varies:

    ========================  ==============================================
    criterion                 resolvable against
    ========================  ==============================================
    ``element_types``,        ``SAPModelData``, ``MeshModel``, resolved
    ``element_ids``, groups   sources
    ``node_ids``              ``SAPModelData``, ``MeshModel``, resolved
                              sources
    ``sections``, materials   ``SAPModelData``, ``MeshModel``
    ``elevation_range``       ``SAPModelData``, ``MeshModel`` (node geometry)
    ``constraints``           ``SAPModelData`` / ``ResolvedSource`` only — a
                              ``MeshModel`` and an NPZ archive carry no
                              ``constraint_assignments``
    ``story``                 needs ``storey_data`` passed to
                              :meth:`resolve_to_mesh_sets`
    ========================  ==============================================

    *Failure policy*

    The query methods here are **permissive**: a criterion the source cannot
    resolve simply matches nothing — ``get_node_ids()`` on a ``MeshModel``
    with :attr:`constraints` set returns ``[]`` rather than raising, matching
    the existing ``story`` behaviour.  Consumers that *act* on the result are
    **strict**: the viewer raises a ``ValueError`` for a criterion the source
    cannot resolve, so nothing is silently swallowed where it would have
    produced a misleading picture.

    Parameters
    ----------
    element_types:
        Filter by element type(s) — ``'Frame'``, ``'Area'``, ``'Node'``.
        ``None`` means all types are eligible.
    sections:
        Filter by section/property name(s).  Applies to **Frame** and
        **Area** elements (checks :attr:`SAPModelData.frame_assignments`
        / :attr:`SAPModelData.area_assignments`).  ``None`` means all.
    materials:
        Filter by material name(s).  An element matches if its assigned
        section's material is in this list.  ``None`` means all.
    groups:
        Filter by group name(s).  An element matches if it belongs to at
        least one of the named groups.  ``None`` means all.
    constraints:
        Filter by SAP2000 joint-constraint name(s) — e.g. ``["Fix"]``.  A
        **Node** matches when :attr:`SAPModelData.constraint_assignments`
        maps it to one of these names, so ``BODY`` rigid bodies,
        ``DIAPHRAGM``, ``EQUAL``, ``WELD``, … are all selectable without
        knowing the constraint type.  Applies to Node elements only —
        frames and areas are excluded when it is set.  Resolution needs
        ``constraint_assignments``, which ``SAPModelData`` carries but
        ``MeshModel`` / NPZ archives do not — those sources match no nodes.
        ``None`` means all.
    element_ids:
        Filter by specific frame/area element ID(s).  Joints are named through
        :attr:`node_ids`, never this field — a node selection that sets
        ``element_ids`` without ``node_ids`` raises.  ``None`` means all.
    node_ids:
        Filter by specific node (joint) ID(s).  ``None`` means all.
    elevation_range:
        ``(z_min, z_max)`` tuple in model length units.  An element is
        included if its **mid-height Z** coordinate falls within
        ``[z_min, z_max]``.  For frame elements, mid-height = ``(z_i + z_j)
        / 2``.  For area elements, mid-height = centroid Z of all vertex
        nodes.  ``None`` (default) means no elevation filter.
    story:
        Filter by storey name(s) — e.g. ``["Roof", "Level 2"]``.  An
        element is included if its mid-height Z is within ``story_z_tolerance``
        of the named storey's elevation.  Requires ``storey_data`` to be
        passed to :meth:`resolve_to_mesh_sets`.  ``None`` (default) means
        no storey filter.

    Examples
    --------
    Select all frame members in a lateral-resisting group:

        >>> sel = Selection(element_types=['Frame'], groups=['Moment Frame'])
        >>> frame_ids = sel.get_frame_ids(model)

    Select the joints of a SAP2000 constraint group (any constraint type):

        >>> sel = Selection(constraints=['Fix'])
        >>> joint_ids = sel.get_node_ids(model)

    Select all areas made of a specific material:

        >>> sel = Selection(
        ...     element_types=['Area'],
        ...     materials=['C30/37'],
        ... )
        >>> areas = sel.filter_areas(model)

    Select areas with specific slab sections and inspect their loads:

        >>> sel = Selection(
        ...     element_types=['Area'],
        ...     sections=['Slab 200mm', 'Roof 150mm'],
        ... )
        >>> uni = sel.filter_area_uniform_loads(model)
        >>> grav = sel.filter_area_gravity_loads(model)

    Use in the builder to control which area loads become edge loads:

        >>> builder.build(selection=sel)
        >>> len(builder.edge_loads_from_areas)
        0   # no uniform loads on those sections

    Record frame elements between Z = 0 and Z = 3 m during pushover:

        >>> sel = Selection(
        ...     element_types=['Frame'],
        ...     elevation_range=(0.0, 3.0),
        ... )

    Record shear walls on a specific storey (requires storey_data):

        >>> from fea_toolkit.model.stories import identify_stories
        >>> stories = identify_stories(model, raw_tables)
        >>> sel = Selection(
        ...     element_types=['Area'],
        ...     sections=['Shear Wall'],
        ...     story=['Level 2'],
        ... )
        >>> frame_ids, area_ids = sel.resolve_to_mesh_sets(
        ...     mesh_model, storey_data=stories,
        ... )
    """

    element_types: Optional[list[str]] = None
    sections: Optional[list[str]] = None
    materials: Optional[list[str]] = None
    groups: Optional[list[str]] = None
    constraints: Optional[list[str]] = None
    element_ids: Optional[list[str]] = None
    node_ids: Optional[list[str]] = None
    elevation_range: Optional[tuple[float, float]] = None
    story: Optional[list[str]] = None
    # Negated counterparts — each ``exclude_*`` field removes the elements that
    # match the corresponding positive criterion (an element is excluded when it
    # matches *any* set ``exclude_*`` field).
    exclude_element_types: Optional[list[str]] = None
    exclude_sections: Optional[list[str]] = None
    exclude_materials: Optional[list[str]] = None
    exclude_groups: Optional[list[str]] = None
    exclude_constraints: Optional[list[str]] = None
    exclude_element_ids: Optional[list[str]] = None
    exclude_node_ids: Optional[list[str]] = None
    exclude_elevation_range: Optional[tuple[float, float]] = None
    exclude_story: Optional[list[str]] = None

    def __post_init__(self) -> None:
        """Validate invariants after construction."""
        for attr in ("elevation_range", "exclude_elevation_range"):
            rng = getattr(self, attr)
            if rng is not None and rng[0] > rng[1]:
                raise ValueError(f"Invalid {attr} {rng}: lower bound must not exceed upper bound")

        # ``element_ids`` / ``exclude_element_ids`` name frames and areas only.
        # On an explicit node selection they would otherwise be silently ignored
        # (matching every joint, or excluding nothing), so reject them and point
        # at the dedicated joint fields instead.
        node_scoped = self.element_types is not None and "Node" in self.element_types
        if node_scoped and self.element_ids is not None and self.node_ids is None:
            raise ValueError(
                "element_ids names frames and areas only; for a node selection use "
                "node_ids (element_types includes 'Node' but node_ids is unset)"
            )
        if node_scoped and self.exclude_element_ids is not None and self.exclude_node_ids is None:
            raise ValueError(
                "exclude_element_ids names frames and areas only; for a node selection "
                "use exclude_node_ids (element_types includes 'Node' but exclude_node_ids "
                "is unset)"
            )

    # ── Constructors ─────────────────────────────────────────────────────────

    @classmethod
    def from_string(cls, expr: str) -> "Selection":
        """Build a ``Selection`` from a ``KEY=VALUE`` expression.

        Grammar — clauses are separated by a semicolon **or whitespace**,
        values within a clause by commas::

            KEY=VALUE[,VALUE ...][; KEY=VALUE ...]

        A value may be double-quoted (with ``\\`` and ``"`` escaped) when it
        itself contains a comma, a semicolon or a ``KEY=`` fragment, so a section
        name like ``"S, 200"`` round-trips through :meth:`to_string` unchanged.

        A clause may be **negated** by preceding it with ``NOT`` (any casing):
        ``NOT section=COL`` selects everything *except* the elements whose
        section is ``COL``.  ``NOT`` applies to the single clause that follows
        it, and is recognised only at clause start — a value named ``NOT`` is
        written after ``=`` and is never confused with the keyword.

        Recognised keys (case-insensitive; the plural and the
        :class:`Selection` field name are accepted aliases):

        ==================  ====================================================
        ``type``            ``element_types`` — ``Frame`` / ``Area`` / ``Node``
        ``section``         ``sections``
        ``material``        ``materials``
        ``group``           ``groups``
        ``constraint``      ``constraints`` — SAP2000 joint constraints
        ``id``              ``element_ids``
        ``node``            ``node_ids``
        ``z``               ``elevation_range`` — ``LO:HI``
        ==================  ====================================================

        Examples:

            >>> Selection.from_string("type=Frame; section=2xR3,2xR4")
            >>> Selection.from_string("constraint=Fix")
            >>> Selection.from_string("z=3.4:4.5")
            >>> Selection.from_string("NOT section=COL")       # everything except COL
            >>> Selection.from_string("type=Area NOT section=Roof slab")

        Args:
            expr: The expression to parse.

        Returns:
            The corresponding ``Selection``.

        Raises:
            ValueError: If a clause has no ``=``, names an unknown key, gives a
                ``z`` value that is not a pair of numbers, names an unknown
                element type, leaves a quoted value unclosed, appends text to
                a quoted value, or leaves a ``NOT`` without a clause after it.
        """
        kwargs: dict = {}
        clauses, leftovers = _scan_clauses(expr)
        # Anything the scanner could not read as a clause is malformed input —
        # most often a bare value with no ``KEY=``.
        if leftovers:
            if leftovers.lower() == "not":
                raise ValueError(
                    "NOT must be followed by a KEY=VALUE clause, e.g. 'NOT section=COL'"
                )
            if leftovers.lower().startswith("not="):
                raise ValueError(
                    f"{leftovers!r}: write NOT before a KEY=VALUE clause, e.g. 'NOT section=COL'"
                )
            raise ValueError(f"expected KEY=VALUE in {leftovers!r} (keys: {SELECT_KEYS_HELP})")

        for negated, key, value in clauses:
            field = SELECT_KEYS.get(key.lower())
            if field is None:
                raise ValueError(f"unknown selection key {key!r} (keys: {SELECT_KEYS_HELP})")
            target = f"exclude_{field}" if negated else field
            if field == "elevation_range":
                if target in kwargs:
                    raise ValueError(
                        f"selection key {key!r} appears more than once "
                        "(only one elevation interval is supported)"
                    )
                bounds = [b for b in re.split(r"[:,]", value) if b.strip()]
                if len(bounds) != 2:
                    raise ValueError(
                        "selection key 'z' takes exactly two numbers, e.g. "
                        f"z=3.4:4.5 — got {value.strip()!r}"
                    )
                try:
                    kwargs[target] = (float(bounds[0]), float(bounds[1]))
                except ValueError as exc:
                    raise ValueError(
                        f"selection key 'z' takes two numbers — got {value.strip()!r}"
                    ) from exc
            else:
                values = _split_values(value)
                if not values:
                    raise ValueError(f"selection key {key!r} has no values")
                if field == "element_types":
                    values = [_canonical_element_type(v) for v in values]
                if negated and target in kwargs:
                    kwargs[target].extend(values)
                else:
                    kwargs[target] = values
        return cls(**kwargs)

    def to_string(self) -> str:
        """The ``KEY=VALUE`` expression that reproduces this selection.

        The inverse of :meth:`from_string`, using the canonical short keys, so a
        selection can be displayed, edited and re-parsed without loss::

            >>> Selection(sections=["Roof slab"], elevation_range=(3.0, 6.0)).to_string()
            'section=Roof slab z=3.0:6.0'
            >>> Selection(exclude_sections=["COL"]).to_string()
            'NOT section=COL'

        Positive clauses are emitted first, then the ``NOT``-prefixed ones, each
        in the same canonical key order.  A value that itself contains a comma, a
        semicolon or a ``KEY=`` fragment is double-quoted (with ``\\`` and ``"``
        escaped) so it survives the re-parse, and the elevation bounds are
        written in full precision.

        Returns:
            The expression (``""`` for an empty selection).
        """
        clauses = []
        for negate in (False, True):
            for field_name, key in SELECT_FIELD_KEYS.items():
                attr = f"exclude_{field_name}" if negate else field_name
                values = getattr(self, attr)
                if not values:
                    continue
                if field_name == "elevation_range":
                    # ``str`` (not ``:g``) keeps the bound exact: ``:g`` rounds to
                    # six significant figures, so ``3.0000001`` would come back as
                    # ``3`` and the round-trip would not be lossless.
                    clause = f"{key}={values[0]}:{values[1]}"
                else:
                    rendered = ", ".join(
                        _render_value(v, follows_value=i > 0) for i, v in enumerate(values)
                    )
                    clause = f"{key}={rendered}"
                clauses.append(f"NOT {clause}" if negate else clause)
        return " ".join(clauses)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _match_element_type(self, etype: str) -> bool:
        if self.element_types is not None and etype not in self.element_types:
            return False
        return self.exclude_element_types is None or etype not in self.exclude_element_types

    def _match_section(self, sec_name: Optional[str]) -> bool:
        if self.sections is not None and (sec_name is None or sec_name not in self.sections):
            return False
        return (
            self.exclude_sections is None
            or sec_name is None
            or sec_name not in self.exclude_sections
        )

    def _match_material(
        self,
        model: Union["SAPModelData", "MeshModel"],
        sec_name: Optional[str],
    ) -> bool:
        """Check the material criterion against either model type.

        Both :class:`SAPModelData` and :class:`MeshModel` expose the same
        ``sections`` mapping, so the same lookup serves both.
        """
        if self.materials is None and self.exclude_materials is None:
            return True
        if sec_name is None:
            # No section → no material to test: it cannot satisfy a positive
            # criterion, nor be excluded by a negative one.
            return self.materials is None
        sec = model.sections.get(sec_name)
        material = sec.material if sec is not None else None
        if self.materials is not None and (material is None or material not in self.materials):
            return False
        return (
            self.exclude_materials is None
            or material is None
            or material not in self.exclude_materials
        )

    def _match_groups(
        self,
        model: Union["SAPModelData", "MeshModel"],
        etype: str,
        eid: str,
    ) -> bool:
        """Check the group membership criterion against either model type.

        Both :class:`SAPModelData` and :class:`MeshModel` expose the same
        ``groups`` mapping, so the same lookup serves both.
        """
        # Groups store references as "Frame:123", "Area:456", "Joint:1"
        ref = f"{etype}:{eid}"
        if self.groups is not None:
            matched = False
            for gname in self.groups:
                grp = model.groups.get(gname)
                if grp is not None and ref in grp.objects:
                    matched = True
                    break
            if not matched:
                return False
        if self.exclude_groups is not None:
            for gname in self.exclude_groups:
                grp = model.groups.get(gname)
                if grp is not None and ref in grp.objects:
                    return False
        return True

    def _match_constraints(self, model: Union["SAPModelData", "MeshModel"], eid: str) -> bool:
        """Check the joint-constraint criterion against either model type.

        ``constraint_assignments`` maps a *joint* ID to its constraint name
        and is carried by :class:`SAPModelData` (and the resolved-source
        wrapper) but not by :class:`MeshModel` or an NPZ archive — a source
        without the mapping therefore matches no joint.
        """
        if self.constraints is None and self.exclude_constraints is None:
            return True
        assignments = getattr(model, "constraint_assignments", None) or {}
        assigned = assignments.get(eid)
        if self.constraints is not None and assigned not in self.constraints:
            return False
        return self.exclude_constraints is None or assigned not in self.exclude_constraints

    def _match_id(self, eid: str) -> bool:
        if self.element_ids is not None and eid not in self.element_ids:
            return False
        return self.exclude_element_ids is None or eid not in self.exclude_element_ids

    def _match_node_id(self, nid: str) -> bool:
        if self.node_ids is not None and nid not in self.node_ids:
            return False
        return self.exclude_node_ids is None or nid not in self.exclude_node_ids

    def _selects_nodes_explicitly(self) -> bool:
        """Whether the node criterion selects joints in its own right,
        rather than nodes merely being *eligible*.

        True when nodes are opted into (:attr:`element_types` names
        ``Node``), a joint-only criterion is set (:attr:`constraints` /
        :attr:`exclude_constraints`), or a type is *excluded* without naming
        ``Node`` (:attr:`exclude_element_types`) — a type exclusion that does
        not list ``Node`` leaves nodes in the result.  With ``element_types``
        unset, a section / material / elevation criterion would match every
        node trivially (nodes ignore those criteria), which would drag the
        whole node set into :meth:`filter_model` subsets.
        """
        if self.element_types is not None and "Node" in self.element_types:
            return True
        if self.constraints is not None or self.exclude_constraints is not None:
            return True
        if self.node_ids is not None or self.exclude_node_ids is not None:
            return True
        return self.exclude_element_types is not None and "Node" not in self.exclude_element_types

    def _frame_matches(
        self,
        model: Union["SAPModelData", "MeshModel"],
        eid: str,
        story_elevations: Optional[dict[str, float]] = None,
        story_z_tolerance: float = 0.5,
    ) -> bool:
        """Check all selection criteria against either model type.

        When ``story_elevations`` is supplied (e.g. from
        :func:`~fea_toolkit.model.stories.identify_stories`), the
        :attr:`elevation_range` and :attr:`story` filters are also applied
        using the frame's mid-height Z.
        """
        if not self._match_element_type("Frame"):
            return False
        if self.constraints is not None:
            # Joint constraints attach to joints only — no frame can carry
            # one, so this criterion *excludes* every frame.  Ignoring it
            # instead would select the whole model when the constraint set
            # is the only criterion.
            return False
        if not self._match_id(eid):
            return False
        sec_name = model.frame_assignments.get(eid)
        if not self._match_section(sec_name):
            return False
        if not self._match_material(model, sec_name):
            return False
        if not self._match_groups(model, "Frame", eid):
            return False
        # Elevation and story filters (only if any is set)
        if (
            self.elevation_range is not None
            or self.story is not None
            or self.exclude_elevation_range is not None
            or self.exclude_story is not None
        ):
            z_mid = self._get_frame_z_mid(model, eid)
            if not self._match_z_filter(z_mid, story_elevations, story_z_tolerance):
                return False
        return True

    def _area_matches(
        self,
        model: Union["SAPModelData", "MeshModel"],
        eid: str,
        story_elevations: Optional[dict[str, float]] = None,
        story_z_tolerance: float = 0.5,
    ) -> bool:
        """Check all selection criteria against either model type.

        When ``story_elevations`` is supplied (e.g. from
        :func:`~fea_toolkit.model.stories.identify_stories`), the
        :attr:`elevation_range` and :attr:`story` filters are also applied
        using the area's centroid Z.
        """
        if not self._match_element_type("Area"):
            return False
        if self.constraints is not None:
            # See _frame_matches — a joint constraint excludes every area.
            return False
        if not self._match_id(eid):
            return False
        sec_name = model.area_assignments.get(eid)
        if not self._match_section(sec_name):
            return False
        if not self._match_material(model, sec_name):
            return False
        if not self._match_groups(model, "Area", eid):
            return False
        # Elevation and story filters (only if any is set)
        if (
            self.elevation_range is not None
            or self.story is not None
            or self.exclude_elevation_range is not None
            or self.exclude_story is not None
        ):
            z_mid = self._get_area_z_mid(model, eid)
            if not self._match_z_filter(z_mid, story_elevations, story_z_tolerance):
                return False
        return True

    def _node_matches(
        self,
        model: Union["SAPModelData", "MeshModel"],
        eid: str,
    ) -> bool:
        if not self._match_element_type("Node"):
            return False
        if not self._match_node_id(eid):
            return False
        # Nodes have no section/material, so those criteria are skipped
        if not self._match_groups(model, "Joint", eid):
            return False
        return self._match_constraints(model, eid)

    # ── Public query methods ─────────────────────────────────────────────────

    def get_frame_ids(self, model: Union["SAPModelData", "MeshModel"]) -> list[str]:
        """Return frame element IDs matching this selection.

        Both :class:`SAPModelData` and :class:`MeshModel` expose the same
        ``frame_elements`` mapping, so the same lookup serves both.
        """
        return [eid for eid in model.frame_elements if self._frame_matches(model, eid)]

    def get_area_ids(self, model: Union["SAPModelData", "MeshModel"]) -> list[str]:
        """Return area element IDs matching this selection.

        Both :class:`SAPModelData` and :class:`MeshModel` expose the same
        ``area_elements`` mapping, so the same lookup serves both.
        """
        return [eid for eid in model.area_elements if self._area_matches(model, eid)]

    def get_node_ids(self, model: Union["SAPModelData", "MeshModel"]) -> list[str]:
        """Return node IDs matching this selection.

        Both :class:`SAPModelData` and :class:`MeshModel` expose the same
        ``nodes`` mapping, so the same lookup serves both.
        """
        return [nid for nid in model.nodes if self._node_matches(model, nid)]

    # ── Dict filters ─────────────────────────────────────────────────────────

    def filter_frames(self, model: Union["SAPModelData", "MeshModel"]) -> dict[str, "FrameElement"]:
        """Return filtered frame elements as ``{id: FrameElement}``.

        Both :class:`SAPModelData` and :class:`MeshModel` store
        :class:`FrameElement` objects, so the same lookup serves both.
        """
        return {eid: model.frame_elements[eid] for eid in self.get_frame_ids(model)}

    def filter_areas(self, model: Union["SAPModelData", "MeshModel"]) -> dict[str, "AreaElement"]:
        """Return filtered area elements as ``{id: AreaElement}``.

        Both :class:`SAPModelData` and :class:`MeshModel` store
        :class:`AreaElement` objects, so the same lookup serves both.
        """
        return {eid: model.area_elements[eid] for eid in self.get_area_ids(model)}

    def filter_nodes(self, model: Union["SAPModelData", "MeshModel"]) -> dict[str, "Node"]:
        """Return filtered nodes as ``{id: Node}``.

        Both :class:`SAPModelData` and :class:`MeshModel` store
        :class:`Node` objects, so the same lookup serves both.
        """
        return {nid: model.nodes[nid] for nid in self.get_node_ids(model)}

    # ── Display resolution ───────────────────────────────────────────────────

    def resolve_connected(
        self, model: Union["SAPModelData", "MeshModel"], include_parents: bool = False
    ) -> tuple[set[str], set[str], set[str]]:
        """Resolve this selection for **display**: ``(frames, areas, nodes)``.

        Unlike :meth:`get_frame_ids` / :meth:`get_area_ids` / :meth:`get_node_ids`
        — which return exactly what matches a criterion, and are what the
        analysis callers want — this is the *view* semantics: **a joint selection
        shows the members framing into it**, as FEA preprocessors do.  A lone node
        marker is not a useful thing to look at; the connection is.

        The node set is the joints of the elements shown, plus any nodes selected
        in their own right.  Expansion is **one hop**: selecting a joint on a
        three-member chain shows that joint and the one member incident on it,
        never the whole chain.

        Expansion happens **only** when the selection opts into nodes
        (:attr:`element_types` names ``Node``, or :attr:`constraints` is set), so
        a pure ``section=`` / ``material=`` / ``z=`` filter never drags the node
        set in.  A member is never reintroduced through that expansion when its
        type is excluded (:attr:`exclude_element_types`) — a retained node does
        not make an excluded area eligible.

        Args:
            model: The ``SAPModelData`` or ``MeshModel`` to resolve against.
            include_parents: Keep superseded (inactive) split parents, which a
                viewport in collapse-to-parents mode draws.  The default drops
                them, matching what a view draws — and keeping a view's reported
                counts equal to what it actually shows.

        Returns:
            ``(frame_ids, area_ids, node_ids)`` as sets of SAP labels.
        """
        frames = getattr(model, "frame_elements", None) or {}
        areas = getattr(model, "area_elements", None) or {}
        excluded_types = self.exclude_element_types or ()

        def visible(elem: Any) -> bool:
            """Whether a view would draw *elem* at all."""
            return include_parents or not getattr(elem, "inactive", False)

        # ``get_*_ids`` match on the criteria alone (and nodes ignore section /
        # material criteria trivially), so the visibility of a matched element is
        # this method's own business.
        frame_ids = {eid for eid in self.get_frame_ids(model) if visible(frames.get(eid))}
        area_ids = {aid for aid in self.get_area_ids(model) if visible(areas.get(aid))}
        node_ids = set(self.get_node_ids(model))

        if self._selects_nodes_explicitly():
            # Frozen snapshot of the selected joints.  Testing against this --
            # rather than the growing set -- keeps the expansion exactly one hop,
            # whatever order the elements happen to be stored in.
            seeds: Optional[set[str]] = set(node_ids)
        else:
            # Nodes are not selected in their own right, so start empty and let
            # the joints of the elements that *do* match accumulate below.
            seeds = None
            node_ids = set()

        for eid, elem in frames.items():
            if not visible(elem):
                continue
            node_i = getattr(elem, "node_i", None)
            node_j = getattr(elem, "node_j", None)
            incident = seeds is not None and (node_i in seeds or node_j in seeds)
            if not (eid in frame_ids or (incident and "Frame" not in excluded_types)):
                continue
            frame_ids.add(eid)
            node_ids.update(nid for nid in (node_i, node_j) if nid is not None)

        for aid, elem in areas.items():
            if not visible(elem):
                continue
            corners = tuple(getattr(elem, "node_ids", None) or ())
            incident = seeds is not None and any(nid in seeds for nid in corners)
            if not (aid in area_ids or (incident and "Area" not in excluded_types)):
                continue
            area_ids.add(aid)
            node_ids.update(corners)

        return frame_ids, area_ids, node_ids

    # ── Load filters ─────────────────────────────────────────────────────────

    def filter_area_uniform_loads(
        self, model: Union["SAPModelData", "MeshModel"]
    ) -> list["AreaUniformLoad"]:
        """Return area uniform loads for areas matching this selection.

        Only checks membership (element type ``'Area'`` plus any
        section / material / group / id filters).  If the selection
        has ``element_types`` set, it must include ``'Area'``.

        Both :class:`SAPModelData` and :class:`MeshModel` expose the same
        ``area_uniform_loads`` list, so the same lookup serves both.
        """
        selected_ids: set[str] = set(self.get_area_ids(model))
        return [ld for ld in model.area_uniform_loads if ld.area_id in selected_ids]

    def filter_area_gravity_loads(
        self, model: Union["SAPModelData", "MeshModel"]
    ) -> list["AreaGravityLoad"]:
        """Return area gravity loads for areas matching this selection.

        Both :class:`SAPModelData` and :class:`MeshModel` expose the same
        ``area_gravity_loads`` list, so the same lookup serves both.
        """
        selected_ids: set[str] = set(self.get_area_ids(model))
        return [ld for ld in model.area_gravity_loads if ld.area_id in selected_ids]

    # ── Self-contained subset ────────────────────────────────────────────────

    def filter_model(self, model: "SAPModelData") -> "SAPModelData":
        """Create a new, self-contained ``SAPModelData`` for this selection.

        The returned model contains only the entities needed by the selected
        elements — their nodes, sections, materials, restraints, and loads.
        The original model is **not** modified.

        This is useful for:

        * **Plotting** — show only a structural subsystem with all its
          dependencies resolved.
        * **Export** — create a clean subset for exchange or debugging.
        * **Verification** — confirm the selection is self-consistent.

        **Node-scoped selections** are supported too.  A selection is
        node-scoped when it opts into nodes (``element_types`` names
        ``Node``) or sets a joint-only criterion (:attr:`constraints`,
        which no frame or area can satisfy); the subset then holds those
        joints with their restraints, joint loads and constraint
        assignments — the joints are the payload, not just element
        endpoints.  When nodes are merely *eligible* (``element_types`` is
        ``None`` and only element criteria are set) they enter the subset
        as the endpoints of the selected frames / areas, as before.

        The subset keeps :attr:`SAPModelData.constraint_assignments`
        (pruned to the selected joints) and the constraint definitions they
        reference, so a constraint-based selection still resolves on it —
        ``plot_mesh(subset, highlight_selection=sel)`` highlights the same
        joints in the context of that subset.

        Returns:
            A new ``SAPModelData`` instance containing only the entities
            required by this selection.
        """
        from .sap_data import SAPModelData

        # 1. Collect element IDs that match
        frame_ids = set(self.get_frame_ids(model))
        area_ids = set(self.get_area_ids(model))

        # 2. Collect referenced node IDs — every joint the selection names
        #    in its own right (a node-scoped selection) plus the endpoints
        #    of the selected frames / areas.
        node_ids: set[str] = set()
        if self._selects_nodes_explicitly():
            node_ids.update(self.get_node_ids(model))
        for fid in frame_ids:
            fe = model.frame_elements.get(fid)
            if fe is not None:
                node_ids.add(fe.node_i)
                node_ids.add(fe.node_j)
        for aid in area_ids:
            ae = model.area_elements.get(aid)
            if ae is not None:
                node_ids.update(ae.node_ids)

        # 3. Collect section names referenced by selected elements
        sec_names: set[str] = set()
        for fid in frame_ids:
            s = model.frame_assignments.get(fid)
            if s:
                sec_names.add(s)
        for aid in area_ids:
            s = model.area_assignments.get(aid)
            if s:
                sec_names.add(s)

        # 4. Collect material names from those sections
        mat_names: set[str] = set()
        for sn in sec_names:
            sec = model.sections.get(sn)
            if sec is not None:
                mat_names.add(sec.material)

        # 5. Constraint data — assignments on the selected joints and the
        #    selected areas (edge constraints), plus the definitions they
        #    reference, so the subset stays self-contained.
        assignments_in = getattr(model, "constraint_assignments", None) or {}
        constraint_assignments = {
            nid: cname for nid, cname in assignments_in.items() if nid in node_ids
        }
        definitions_in = getattr(model, "constraints", None) or {}
        referenced = set(constraint_assignments.values())
        constraints = {name: con for name, con in definitions_in.items() if name in referenced}
        edge_in = getattr(model, "area_edge_constraints", None) or {}
        area_edge_constraints = {aid: edge_in[aid] for aid in area_ids if aid in edge_in}

        # 6. Build filtered dicts
        subset = SAPModelData(
            # Nodes
            nodes={nid: model.nodes[nid] for nid in node_ids if nid in model.nodes},
            # Restraints on those nodes
            restraints={nid: model.restraints[nid] for nid in node_ids if nid in model.restraints},
            # Materials used by selected sections
            materials={mn: model.materials[mn] for mn in mat_names if mn in model.materials},
            # Sections used by selected elements
            sections={sn: model.sections[sn] for sn in sec_names if sn in model.sections},
            # Frame & area elements
            frame_elements={
                fid: model.frame_elements[fid] for fid in frame_ids if fid in model.frame_elements
            },
            area_elements={
                aid: model.area_elements[aid] for aid in area_ids if aid in model.area_elements
            },
            # Assignments
            frame_assignments={
                fid: model.frame_assignments[fid]
                for fid in frame_ids
                if fid in model.frame_assignments
            },
            area_assignments={
                aid: model.area_assignments[aid]
                for aid in area_ids
                if aid in model.area_assignments
            },
            # Auto-mesh for selected frames
            frame_auto_mesh={
                fid: model.frame_auto_mesh[fid] for fid in frame_ids if fid in model.frame_auto_mesh
            },
            # Groups — keep those that contain selected elements, with only
            # the matching references
            groups=self._filter_groups(model, frame_ids, area_ids, node_ids),
            # Load definitions — keep all (harmless)
            load_cases=model.load_cases,
            load_patterns=model.load_patterns,
            mass_sources=model.mass_sources,
            # Loads on selected elements / nodes
            joint_loads=[jl for jl in model.joint_loads if jl.node_id in node_ids],
            frame_dist_loads=[ld for ld in model.frame_dist_loads if ld.frame_id in frame_ids],
            frame_gravity_loads=[
                gl for gl in model.frame_gravity_loads if gl.frame_id in frame_ids
            ],
            area_uniform_loads=self.filter_area_uniform_loads(model),
            area_gravity_loads=self.filter_area_gravity_loads(model),
            # Joint constraints on the selected nodes / areas
            constraints=constraints,
            constraint_assignments=constraint_assignments,
            area_edge_constraints=area_edge_constraints,
            # Units
            units=dict(model.units),
        )
        return subset

    def _filter_groups(
        self,
        model: Union["SAPModelData", "MeshModel"],
        frame_ids: set[str],
        area_ids: set[str],
        node_ids: set[str],
    ) -> dict[str, "Group"]:
        """Return groups that have at least one selected element, pruned
        to only those references.

        Both :class:`SAPModelData` and :class:`MeshModel` expose the same
        ``groups`` mapping, so the same lookup serves both.
        """
        from .sap_data import Group

        result: dict[str, Group] = {}
        for gname, grp in model.groups.items():
            kept: list[str] = []
            for obj in grp.objects:
                # Object references are "Frame:123", "Area:456", "Joint:1"
                if obj.startswith("Frame:"):
                    eid = obj.split(":", 1)[1]
                    if eid in frame_ids:
                        kept.append(obj)
                elif obj.startswith("Area:"):
                    eid = obj.split(":", 1)[1]
                    if eid in area_ids:
                        kept.append(obj)
                elif obj.startswith("Joint:"):
                    nid = obj.split(":", 1)[1]
                    if nid in node_ids:
                        kept.append(obj)
                else:
                    # Unknown type — keep it (conservative)
                    kept.append(obj)
            if kept:
                result[gname] = Group(
                    name=gname,
                    color=grp.color,
                    objects=kept,
                )
        return result

    # ── MeshModel resolution (for pushover per-step recording) ──────────────

    def resolve_to_mesh_sets(
        self,
        mesh_model: "MeshModel",
        storey_data: "Optional[list[StoryLevel]]" = None,
        story_z_tolerance: float = 0.5,
    ) -> tuple[set[str], set[str]]:
        """Resolve this Selection against a ``MeshModel``.

        Returns the set of frame and area SAP2000 IDs that match all
        non-``None`` criteria in this selection.  This is used to determine
        which elements to record during pushover per-step analysis.

        Unlike :meth:`get_frame_ids` / :meth:`get_area_ids` which work on
        ``SAPModelData``, this method reads from a ``MeshModel`` (which has
        the same ``frame_assignments``, ``area_assignments``, ``sections``,
        ``materials``, and ``groups`` structures) and additionally supports
        the :attr:`elevation_range` and :attr:`story` filters.

        Args:
            mesh_model:
                The processed ``MeshModel`` to resolve against.
            storey_data:
                Output of :func:`~fea_toolkit.model.stories.identify_stories`,
                i.e. ``List[StoryLevel]``.  Required only when :attr:`story`
                is set; ignored otherwise.
            story_z_tolerance:
                Tolerance (in model length units) for matching an element's
                mid-height Z to a storey elevation from *storey_data*.
                Default 0.5 (half-metre in metre-based models).

        Returns:
            ``(record_frame_ids, record_area_ids)`` — two :class:`set` of
            SAP2000 element ID strings for frame and area elements matching
            this selection.

        Raises:
            ValueError:
                If :attr:`story` is set but ``storey_data`` is ``None``.

        Examples::

            # Select base-level columns for pushover recording
            sel = Selection(
                element_types=['Frame'],
                elevation_range=(0.0, 3.0),
            )
            frame_ids, area_ids = sel.resolve_to_mesh_sets(mesh_model)

            # Select shear walls on a specific storey
            stories = identify_stories(md, raw_tables)
            sel = Selection(
                element_types=['Area'],
                sections=['Shear Wall'],
                story=['Level 2'],
            )
            frame_ids, area_ids = sel.resolve_to_mesh_sets(
                mesh_model, storey_data=stories,
            )
        """
        # ── Build story name → elevation lookup ──
        story_elevations: Optional[dict[str, float]] = None
        if self.story is not None:
            if storey_data is None:
                raise ValueError(
                    "story filter requires storey_data. "
                    "Call identify_stories(md, raw_tables) and pass the "
                    "result as storey_data to resolve_to_mesh_sets()."
                )
            story_elevations = {s.name: s.elevation for s in storey_data}

        frame_ids: set[str] = set()
        area_ids: set[str] = set()

        for eid, fe in mesh_model.frame_elements.items():
            if getattr(fe, "inactive", False):
                continue
            if self._frame_matches(
                mesh_model,
                eid,
                story_elevations,
                story_z_tolerance,
            ):
                frame_ids.add(eid)

        for aid, ae in mesh_model.area_elements.items():
            if getattr(ae, "inactive", False):
                continue
            if self._area_matches(
                mesh_model,
                aid,
                story_elevations,
                story_z_tolerance,
            ):
                area_ids.add(aid)

        return frame_ids, area_ids

    # ── MeshModel matching helpers ──────────────────────────────────────────

    def _match_z_filter(
        self,
        z_mid: Optional[float],
        story_elevations: Optional[dict[str, float]] = None,
        story_z_tolerance: float = 0.5,
    ) -> bool:
        """Apply the elevation-range and storey filters to a resolved Z.

        The elevation and storey criteria are applied only when set.  A
        ``None`` ``z_mid`` — element geometry unavailable — excludes the
        element when a *positive* criterion needs it, but keeps it when only
        an exclusion is set (nothing to test means nothing to exclude).
        """
        if z_mid is None:
            has_positive = self.elevation_range is not None or self.story is not None
            return not has_positive
        if not self._match_elevation(z_mid):
            return False
        return self._match_story(z_mid, story_elevations, story_z_tolerance)

    def _match_elevation(self, z_mid: float) -> bool:
        """Check if a Z coordinate falls within *elevation_range* (or not)."""
        if self.elevation_range is not None:
            z_min, z_max = self.elevation_range
            if not (z_min <= z_mid <= z_max):
                return False
        if self.exclude_elevation_range is not None:
            z_min, z_max = self.exclude_elevation_range
            if z_min <= z_mid <= z_max:
                return False
        return True

    def _match_story(
        self,
        z_mid: float,
        story_elevations: Optional[dict[str, float]],
        story_z_tolerance: float,
    ) -> bool:
        """Check if a Z coordinate matches (or is excluded from) a storey."""
        if self.story is not None:
            if story_elevations is None:
                return False  # no elevation data to match against — exclude
            matched = False
            for story_name in self.story:
                elev = story_elevations.get(story_name)
                if elev is None:
                    continue  # unknown story name — skip conservatively
                if abs(z_mid - elev) <= story_z_tolerance:
                    matched = True
                    break
            if not matched:
                return False
        if self.exclude_story is not None:
            if story_elevations is None:
                return True  # no elevation data — cannot tell whether to exclude
            for story_name in self.exclude_story:
                elev = story_elevations.get(story_name)
                if elev is not None and abs(z_mid - elev) <= story_z_tolerance:
                    return False
        return True

    @staticmethod
    def _get_frame_z_mid(model: Union["SAPModelData", "MeshModel"], eid: str) -> Optional[float]:
        """Return the mid-height Z of a frame element, or None."""
        fe = model.frame_elements.get(eid)
        if fe is None:
            return None
        node_i = model.nodes.get(fe.node_i)
        node_j = model.nodes.get(fe.node_j)
        if node_i is None or node_j is None:
            return None
        return (node_i.z + node_j.z) / 2.0

    @staticmethod
    def _get_area_z_mid(model: Union["SAPModelData", "MeshModel"], aid: str) -> Optional[float]:
        """Return the centroid Z of an area element, or None."""
        ae = model.area_elements.get(aid)
        if ae is None:
            return None
        z_vals = []
        for nid in ae.node_ids:
            nd = model.nodes.get(nid)
            if nd is not None:
                z_vals.append(nd.z)
        if not z_vals:
            return None
        return sum(z_vals) / len(z_vals)

    # ── Brace detection ──────────────────────────────────────────────────────

    @staticmethod
    def from_brace_sections(model: Union["SAPModelData", "MeshModel"]) -> "Selection":
        """Create a ``Selection`` targeting brace‑type sections.

        Identifies frame elements whose section shape is one of the common
        brace profiles: ``Pipe``, ``Angle``, ``Double Angle``, ``Tee``,
        or ``Channel``.  This is the **section-type** signal used in
        element classification (see ``docs/element_classification.md``).

        The classification combines two independent signals:

        **1. Section type (this method)**
           Checks the Python dataclass type of each section in the model.
           Brace-shaped sections (Pipe, Angle, Double Angle, Tee, Channel)
           are candidates regardless of their orientation.

        **2. Geometry**
           A frame element whose chord angle from vertical exceeds ~20\u00b0
           is geometrically diagonal.  Handled by
           ``Preprocessor._classify_element_type()``.

        **Merge rule**
           A frame element is treated as a brace for pushover (Truss +
           Hysteretic) only if **both** conditions hold: its section is a
           brace shape AND it is geometrically diagonal.  This prevents
           horizontal pipes (e.g. handrails) or vertical tees from being
           misclassified as braces.

        Args:
            model: The parsed ``SAPModelData`` or ``MeshModel`` (both expose
                the same ``sections`` mapping).

        Returns:
            A ``Selection`` with ``element_types=['Frame']`` and
            ``sections`` populated from the model's brace-shape sections.
            Returns an empty Selection if no brace-shaped sections exist.
        """
        from .sap_data import (
            AngleSection,
            ChannelSection,
            DoubleAngleSection,
            PipeSection,
            TeeSection,
        )

        brace_shape_types = (
            PipeSection,
            AngleSection,
            DoubleAngleSection,
            TeeSection,
            ChannelSection,
        )
        brace_sec_names = [
            name for name, sec in model.sections.items() if isinstance(sec, brace_shape_types)
        ]
        if not brace_sec_names:
            # Return an empty selection — no braces to find
            return Selection(
                element_types=[],
                sections=[],
            )
        return Selection(
            element_types=["Frame"],
            sections=brace_sec_names,
        )
