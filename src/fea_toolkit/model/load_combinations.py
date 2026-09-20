"""Load-combination trees, composite load cases and result generation.

The parsed combination collection is a **flat dictionary**: each
:class:`~fea_toolkit.model.sap_data.LoadCombination` names its references,
which may be load cases (leaves) or other combinations (branches).  Depth is
expressed by reference, never by nesting the definitions.

Three layers sit on top of that dictionary:

``build_combo_tree`` / ``build_combo_tree_dict``
    Materialise the reference graph as a :class:`ComboTree` — branches are
    combinations, leaves are load cases.
``calculate_aggregate_factors`` / ``expand_linear_combination``
    Walk to the leaves multiplying factors, yielding the effective
    load-case factors of a purely linear combination.
``generate_combination_results``
    Turn a combination into one or more :class:`CompositeLoadCase`
    "composite load cases": a response-spectrum case (or an SRSS-generated
    result) mixed with non-spectrum terms forks into a ``+`` and a ``-``
    composite; an ``Envelope`` yields either a max/min pair
    (``envelope_mode="maxmin"``) or one composite per branch
    (``envelope_mode="per_path"``); ``SRSS`` yields a magnitude composite.

``apply_composite_load_case`` evaluates a composite against per-case arrays
and ``generate_composite_results`` packages the result for the NPZ writer,
so a combination can be visualised exactly like a load case.

This module is deliberately OpenSees-free — model / post-processing logic
only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Optional, Union

import numpy as np

from .sap_data import LoadCase, LoadCombination, LoadCombinationEntry

__all__ = [
    "ComboTree",
    "ComboTreeNode",
    "CompositeLoadCase",
    "apply_composite_load_case",
    "as_combination_mapping",
    "build_combo_tree",
    "build_combo_tree_dict",
    "calculate_aggregate_factors",
    "classify_combination_refs",
    "combination_case_meta",
    "combination_set_from_dict",
    "combination_set_to_dict",
    "expand_linear_combination",
    "generate_combination_results",
    "generate_composite_results",
    "merge_combination_sets",
    "to_e2k_combo_dict",
]

# ── Operator vocabulary ────────────────────────────────────────────────
#: Combination types that combine results with scale factors only.
_LINEAR_OPERATORS = frozenset({"", "add", "linear add"})
#: Combination types that select per-quantity extremes.
_ENVELOPE_OPERATORS = frozenset({"envelope", "env"})
#: Combination types that produce a magnitude (root-sum-square).
_SRSS_OPERATORS = frozenset({"srss"})

#: Valid ``envelope_mode`` values for :func:`generate_combination_results`.
ENVELOPE_MODES = ("maxmin", "per_path")


def _operator_of(combo: LoadCombination) -> str:
    """Normalise *combo*'s type string to an operator name.

    Returns:
        ``"linear"``, ``"envelope"``, ``"srss"`` or ``"unsupported"``.
    """
    op = (combo.combo_type or "").strip().lower()
    if op in _LINEAR_OPERATORS:
        return "linear"
    if op in _ENVELOPE_OPERATORS:
        return "envelope"
    if op in _SRSS_OPERATORS:
        return "srss"
    return "unsupported"


def _is_spectrum_case(case: Optional[LoadCase]) -> bool:
    """Whether *case* is a response-spectrum load case (a magnitude result)."""
    if case is None:
        return False
    case_type = (case.case_type or "").lower()
    return "respspec" in case_type or "spectrum" in case_type


# ═══════════════════════════════════════════════════════════════════════
# Composite load cases
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class CompositeLoadCase:
    """A generated result set derived from a combination definition.

    Attributes:
        name: Composite name, unique within one generation call.
        operator: ``"linear"`` (weighted sum), ``"max"`` / ``"min"``
            (per-quantity extreme selection) or ``"srss"``
            (per-quantity root-sum-square).
        cases: For ``"linear"`` — ``{load_case: combined_factor}``.
        children: Scaled sub-composites.  For ``"linear"`` these are extra
            signed terms; for ``"max"`` / ``"min"`` / ``"srss"`` they are
            the alternatives that get reduced.  Each element is
            ``(factor, composite)``.
        source: Name of the combination this composite was generated from.
        family: The collection the composite belongs to — ``"single"`` (one
            composite, no fork), ``"fork"`` (2ⁿ signed senses of a magnitude),
            ``"envelope"`` (a ``max`` / ``min`` pair), ``"path"`` (one
            composite per envelope branch) or ``"srss"``.  Empty when the
            composite was built by hand rather than by
            :func:`generate_combination_results`.
        coords: The composite's coordinate inside its family — the signed
            magnitude references of a fork (``("+RSX", "-RSY")``), the reduced
            extreme (``("max",)`` / ``("min",)``), the branch name of a
            ``per_path`` variant, else ``()``.  This is the stable identity of
            a variant; display names are derived from it, never the reverse.
    """

    name: str
    operator: str = "linear"
    cases: dict[str, float] = field(default_factory=dict)
    children: list[tuple[float, CompositeLoadCase]] = field(default_factory=list)
    source: str = ""
    family: str = ""
    coords: tuple = ()


# ═══════════════════════════════════════════════════════════════════════
# Combination trees
# ═══════════════════════════════════════════════════════════════════════


class ComboTreeNode:
    """A node in a load-combination tree.

    Branch nodes are combinations (``node_type`` is the combination
    operator) and leaves are load cases.  ``weight`` is the factor carried
    on the edge from the parent.

    Attributes:
        name: Node name (combination or load-case name).
        node_type: Combination operator for branches, ``"Load Case"`` for
            leaves.
        weight: Scale factor on the edge into this node.
        definition: Extra node data (the referenced combination object).
        children: Child nodes.
    """

    def __init__(
        self,
        name: str,
        node_type: str,
        weight: float,
        definition: Optional[dict] = None,
    ) -> None:
        self.name = name
        self.node_type = node_type
        self.weight = weight
        self.definition = definition if definition is not None else {}
        self.children: list[ComboTreeNode] = []

    def add_child(self, child_node: ComboTreeNode) -> None:
        """Append *child_node* to this node's children."""
        self.children.append(child_node)

    def get_children(self) -> list[ComboTreeNode]:
        """Return this node's children."""
        return self.children

    def __repr__(self) -> str:
        return (
            f"ComboTreeNode(name={self.name!r}, type={self.node_type!r}, "
            f"weight={self.weight}, children={len(self.children)})"
        )


class ComboTree:
    """A load-combination tree rooted at a :class:`ComboTreeNode`."""

    def __init__(self, root_node: ComboTreeNode) -> None:
        self.root = root_node

    def add_node(self, parent_node: ComboTreeNode, child_node: ComboTreeNode) -> None:
        """Attach *child_node* under *parent_node*."""
        parent_node.add_child(child_node)

    def __repr__(self) -> str:
        return f"ComboTree(root={self.root})"


def classify_combination_refs(
    load_combinations: dict[str, LoadCombination],
    load_cases: dict[str, LoadCase],
) -> None:
    """Set every entry's :attr:`~LoadCombinationEntry.kind` in place.

    SAP2000 guarantees names are unique across load cases and combinations,
    so each reference resolves unambiguously to ``"combo"``, ``"case"`` or
    ``"unknown"`` (a dangling reference, e.g. in a partially exported model).

    The ``.s2k`` ``COMBINATION DEFINITIONS`` table carries no reference-type
    flag, so this resolution is the only way to tell a nested combination from
    a load case.

    Args:
        load_combinations: Flat ``{name: LoadCombination}`` mapping, mutated
            in place.
        load_cases: Flat ``{name: LoadCase}`` mapping.
    """
    combo_names = set(load_combinations)
    for combo in load_combinations.values():
        for entry in combo.entries:
            if entry.name in combo_names:
                entry.kind = "combo"
            elif entry.name in load_cases:
                entry.kind = "case"
            else:
                entry.kind = "unknown"


def _combo_branches(combo: LoadCombination) -> list[LoadCombinationEntry]:
    """Return *combo*'s references that point at other combinations."""
    return [e for e in combo.entries if e.kind == "combo"]


def _combo_leaves(combo: LoadCombination) -> list[LoadCombinationEntry]:
    """Return *combo*'s references that point at load cases (or are unknown)."""
    return [e for e in combo.entries if e.kind != "combo"]


def _build_children(
    node: ComboTreeNode,
    load_combinations: dict[str, LoadCombination],
    active: frozenset,
) -> None:
    """Recursively expand *node*, guarding against cyclic references."""
    combo = load_combinations.get(node.name)
    if combo is None:
        return
    for entry in _combo_branches(combo):
        if entry.name in active:
            raise ValueError(
                f"Cyclic load-combination reference: {entry.name!r} is already "
                f"on the path {sorted(active)}"
            )
        child_combo = load_combinations.get(entry.name)
        child_type = child_combo.combo_type if child_combo is not None else ""
        child_node = ComboTreeNode(entry.name, child_type, entry.factor, child_combo)
        node.add_child(child_node)
        _build_children(child_node, load_combinations, active | {entry.name})
    for entry in _combo_leaves(combo):
        node.add_child(ComboTreeNode(entry.name, "Load Case", entry.factor))


def build_combo_tree(
    load_combinations: dict[str, LoadCombination],
    start_key: str,
) -> ComboTree:
    """Build a :class:`ComboTree` rooted at *start_key*.

    Branches are followed recursively until every path terminates in a load
    case.  Cyclic references raise ``ValueError`` (the E2K original had no
    guard and would recurse forever).

    Args:
        load_combinations: Flat ``{name: LoadCombination}`` mapping.
        start_key: Combination to treat as the root — may be a combination
            lower down another combination's tree.

    Returns:
        The materialised tree.

    Raises:
        KeyError: If *start_key* is not a known combination.
        ValueError: On a cyclic reference.
    """
    if start_key not in load_combinations:
        raise KeyError(f"Unknown load combination {start_key!r}")
    root_combo = load_combinations[start_key]
    root = ComboTreeNode(start_key, root_combo.combo_type, 1.0, root_combo)
    _build_children(root, load_combinations, frozenset({start_key}))
    return ComboTree(root)


def build_combo_tree_dict(
    load_combinations: dict[str, LoadCombination],
    start_key: str,
) -> dict[str, ComboTree]:
    """Build one or more trees for *start_key*.

    An ``Envelope`` root is split into one subtree per referenced branch (the
    combination-set view); any other root yields a single tree.  Unlike the
    E2K original, children are expanded exactly once — no double-add.

    Args:
        load_combinations: Flat ``{name: LoadCombination}`` mapping.
        start_key: Root combination.

    Returns:
        ``{tree_name: ComboTree}`` — one entry for a non-envelope root, one
        per referenced constituent (combination or load case) for an
        envelope root.
    """
    tree = build_combo_tree(load_combinations, start_key)
    if _operator_of(load_combinations[start_key]) != "envelope":
        return {start_key: tree}
    return {child.name: ComboTree(child) for child in tree.root.children}


def calculate_aggregate_factors(tree: Union[ComboTree, ComboTreeNode]) -> dict[str, float]:
    """Aggregate leaf load-case factors over all root-to-leaf paths.

    The aggregate factor of a leaf is the product of the weights along the
    path to it; a load case reachable by several paths has its contributions
    **summed**, so a repeated case is never silently dropped.

    Args:
        tree: A :class:`ComboTree` or a :class:`ComboTreeNode` root.

    Returns:
        ``{load_case_name: combined_factor}``.
    """
    root = tree.root if isinstance(tree, ComboTree) else tree
    factors: dict[str, float] = {}

    def traverse(node: ComboTreeNode, current: float) -> None:
        if not node.children:
            factors[node.name] = factors.get(node.name, 0.0) + current * node.weight
            return
        for child in node.children:
            traverse(child, current * node.weight)

    traverse(root, 1.0)
    return factors


def to_e2k_combo_dict(load_combinations: dict[str, LoadCombination]) -> dict:
    """Project the flat combination mapping onto the E2K dictionary shape.

    Produces the ``{"TYPE", "DESIGN", "LOADCOMBO", "LOADCASE"}`` structure
    consumed by the ETABS-side ``build_combo_tree``, for parity / cross-check
    with that code base.

    This is a *projection*: the E2K shape cannot carry
    :attr:`LoadCombinationEntry.mode`, and ``kind="unknown"`` references are
    parked under ``"OTHER"`` so nothing is silently dropped.  The canonical
    representation remains :class:`LoadCombination`.

    Args:
        load_combinations: Flat ``{name: LoadCombination}`` mapping.

    Returns:
        ``{combo_name: {"TYPE": str, "DESIGN": {...}, "LOADCOMBO": [...],
        "LOADCASE": [...], "OTHER": [...]}}``.
    """
    out: dict[str, dict] = {}
    for name, combo in load_combinations.items():
        out[name] = {
            "TYPE": combo.combo_type,
            "DESIGN": dict(combo.design),
            "LOADCOMBO": [(e.name, e.factor) for e in combo.entries if e.kind == "combo"],
            "LOADCASE": [(e.name, e.factor) for e in combo.entries if e.kind == "case"],
            "OTHER": [(e.name, e.factor) for e in combo.entries if e.kind == "unknown"],
        }
    return out


# ═══════════════════════════════════════════════════════════════════════
# External definition sets (typed, serialisable)
# ═══════════════════════════════════════════════════════════════════════
#
# A **combination set** is the same flat ``{name: definition}`` mapping the
# parser produces — but authored by hand, so it can live in JSON, a report
# config or a script and be merged onto a model's own combinations.  Each
# definition is::
#
#     {
#         "type": "Linear Add",            # any of the five CSI operators
#         "entries": [                     # ordered; duplicates preserved
#             {"ref": "DEAD", "factor": 1.3},
#             {"ref": "RSX",  "factor": 1.4},   # optional "mode": 5
#         ],
#         "design": {},                    # optional SAP design overrides
#     }
#
# ``type`` is the field the ``.s2k`` ``COMBINATION DEFINITIONS`` table carries
# but a bare ``{name: [(case, factor), ...]}`` shorthand cannot — without it
# every hand-authored set would silently collapse to Linear Add.  ``ref``
# names either a load case or another combination in the set, so nesting is
# expressed by reference exactly as in :class:`LoadCombination`.  An entry may
# also carry ``"magnitude": true`` to declare that its reference is a
# response-spectrum (or SRSS-style) result — the one thing a definition cannot
# infer without the model's ``load_cases``, and what decides the ± fork.

#: Shorthand spellings accepted in a definition's ``"type"`` field, mapped onto
#: the canonical CSI operator name :func:`_operator_of` recognises.
_COMBO_TYPE_ALIASES: dict[str, str] = {
    "": "Linear Add",
    "linear": "Linear Add",
    "linear add": "Linear Add",
    "add": "Linear Add",
    "envelope": "Envelope",
    "env": "Envelope",
    "srss": "SRSS",
    "absolute": "Absolute Add",
    "absolute add": "Absolute Add",
    "range": "Range Add",
    "range add": "Range Add",
}


def _entry_from_spec(spec) -> LoadCombinationEntry:
    """Build a :class:`LoadCombinationEntry` from one definition entry.

    Args:
        spec: A :class:`LoadCombinationEntry` (passed through), a mapping
            ``{"ref"/"name"/"case", "factor"?, "mode"?}``, a ``(ref, factor)``
            pair, or a bare reference string (factor ``1.0``).

    Returns:
        The entry, with ``kind`` left at its default ``"case"``.

    Raises:
        TypeError: If *spec* is not one of the accepted shapes.
        ValueError: If a mapping names no reference.
    """
    if isinstance(spec, LoadCombinationEntry):
        return spec
    if isinstance(spec, str):
        return LoadCombinationEntry(name=spec, factor=1.0)
    if isinstance(spec, Mapping):
        name = spec.get("ref") or spec.get("name") or spec.get("case")
        if not name:
            raise ValueError(f"Combination entry {spec!r} names no case or combination")
        mode = spec.get("mode")
        return LoadCombinationEntry(
            name=str(name),
            factor=float(spec.get("factor", 1.0)),
            mode=None if mode is None else int(mode),
            magnitude=bool(spec.get("magnitude", False)),
        )
    if isinstance(spec, (list, tuple)):
        if not spec:
            raise ValueError("Empty combination entry — expected (ref, factor)")
        return LoadCombinationEntry(
            name=str(spec[0]),
            factor=float(spec[1]) if len(spec) > 1 else 1.0,
        )
    raise TypeError(f"Unsupported combination entry {spec!r}")


def combination_set_from_dict(data: Mapping) -> dict[str, LoadCombination]:
    """Build :class:`LoadCombination` objects from a canonical definition set.

    Args:
        data: ``{name: definition}``.  A definition is a mapping carrying
            ``"type"`` (aliases accepted — ``"linear"``, ``"envelope"``,
            ``"srss"``, ``"Absolute Add"``, ``"Range Add"``), ``"entries"``
            and an optional ``"design"``.  Shorthand: a bare
            ``[(ref, factor), ...]`` list is taken as a Linear Add, and a
            :class:`LoadCombination` value is passed straight through.

    Returns:
        ``{name: LoadCombination}``.  Every entry's
        :attr:`~LoadCombinationEntry.kind` stays ``"case"`` until
        :func:`classify_combination_refs` resolves it against a model's load
        cases and combinations.

    Raises:
        TypeError: If a definition or entry is not a supported shape.
        ValueError: If an entry names no reference.

    See Also:
        :func:`fea_toolkit.io.combination_set.read_combination_set` for the
        file-level loader.
    """
    if not data:
        return {}
    out: dict[str, LoadCombination] = {}
    for raw_name, raw in data.items():
        name = str(raw_name)
        if isinstance(raw, LoadCombination):
            out[name] = raw
            continue
        if isinstance(raw, (list, tuple)):
            spec = {"type": "Linear Add", "entries": list(raw)}
        else:
            spec = raw
        if not isinstance(spec, Mapping):
            raise TypeError(f"Combination definition {name!r} must be a mapping, got {raw!r}")
        raw_type = spec.get("type") or spec.get("combo_type") or ""
        combo_type = _COMBO_TYPE_ALIASES.get(str(raw_type).strip().lower(), str(raw_type))
        out[name] = LoadCombination(
            name=name,
            combo_type=combo_type,
            entries=[_entry_from_spec(e) for e in spec.get("entries") or ()],
            design={str(k): str(v) for k, v in (spec.get("design") or {}).items()},
        )
    return out


def combination_set_to_dict(load_combinations: dict[str, LoadCombination]) -> dict:
    """Serialise a flat combination mapping to the canonical definition shape.

    The inverse of :func:`combination_set_from_dict` — every authored field
    round-trips (``kind`` is derived by :func:`classify_combination_refs` and
    therefore never written).

    Args:
        load_combinations: Flat ``{name: LoadCombination}`` mapping — or an
            already-canonical definition dict, which is normalised first so a
            hand-written set can be re-serialised without being built by hand.

    Returns:
        ``{name: {"type": str, "entries": [{"ref", "factor", "mode"?}],
        "design"?}}`` — ``"design"`` is omitted when empty.

    See Also:
        :func:`to_e2k_combo_dict` for the ETABS-side projection; this is the
        canonical export.
    """
    out: dict[str, dict] = {}
    for name, combo in as_combination_mapping(load_combinations).items():
        entries: list[dict] = []
        for entry in combo.entries:
            spec: dict = {"ref": entry.name, "factor": entry.factor}
            if entry.mode is not None:
                spec["mode"] = entry.mode
            if entry.magnitude:
                spec["magnitude"] = True
            entries.append(spec)
        definition: dict = {"type": combo.combo_type, "entries": entries}
        if combo.design:
            definition["design"] = dict(combo.design)
        out[name] = definition
    return out


def as_combination_mapping(definitions) -> dict[str, LoadCombination]:
    """Normalise any accepted definition input to ``{name: LoadCombination}``.

    Args:
        definitions: ``None``, a mapping of :class:`LoadCombination` objects
            (already built, e.g. ``SAPModelData.load_combinations``), or a
            canonical definition dict for :func:`combination_set_from_dict`.

    Returns:
        A ``{name: LoadCombination}`` mapping (``{}`` for ``None``).
    """
    if definitions is None:
        return {}
    if isinstance(definitions, Mapping) and all(
        isinstance(v, LoadCombination) for v in definitions.values()
    ):
        return dict(definitions)
    return combination_set_from_dict(definitions)


def merge_combination_sets(
    *sets,
    load_cases: Optional[dict[str, LoadCase]] = None,
) -> dict[str, LoadCombination]:
    """Merge combination sets left-to-right; a later definition wins.

    Layers a hand-authored external set onto the combinations a model was
    parsed with (or vice versa)::

        combos = merge_combination_sets(md.load_combinations, external,
                                        load_cases=md.load_cases)

    An external set may therefore *extend* the model's combinations, *override*
    one by re-using its name, or stand alone — the caller picks the argument
    order.

    Args:
        *sets: Mappings to merge.  Each is a canonical definition dict, an
            already-built ``{name: LoadCombination}`` mapping, or ``None``
            (skipped).  A set may reference a name defined by an earlier set.
        load_cases: When given, every merged entry's
            :attr:`~LoadCombinationEntry.kind` is re-resolved against the
            merged combination names and these load cases, so a reference
            introduced by the external set classifies correctly.

    Returns:
        A fresh merged ``{name: LoadCombination}`` mapping.  The inputs are
        not mutated.
    """
    merged: dict[str, LoadCombination] = {}
    for layer in sets:
        if layer is None:
            continue
        merged.update(as_combination_mapping(layer))
    # Deep-copy every combination before classification: ``classify_combination_refs``
    # and the combo-name resolution below mutate entries in place, which must
    # never touch the caller's own combinations or entries.
    merged = {
        name: replace(
            combo,
            entries=[replace(entry) for entry in combo.entries],
            design=dict(combo.design),
        )
        for name, combo in merged.items()
    }
    if load_cases is not None:
        classify_combination_refs(merged, load_cases)
    else:
        # Nested combination references always resolve from the merged mapping
        # alone; a case reference keeps whatever ``kind`` its entry carried
        # (``"case"`` for a freshly built definition).
        combo_names = set(merged)
        for combo in merged.values():
            for entry in combo.entries:
                if entry.name in combo_names:
                    entry.kind = "combo"
    return merged


# ═══════════════════════════════════════════════════════════════════════
# Composite generation
# ═══════════════════════════════════════════════════════════════════════


def _is_magnitude_combo(
    combo: LoadCombination,
    load_cases: dict[str, LoadCase],
    load_combinations: dict[str, LoadCombination],
    _seen: frozenset = frozenset(),
) -> bool:
    """Whether *combo*'s result is a magnitude rather than a signed field.

    True for an SRSS combination, and for a Linear-Add combination whose every
    term is itself a magnitude — e.g. a combination of response-spectrum cases
    (``RS1 = 1.0*SPEC + 0.3*SPEC``), whose result is a magnitude and therefore
    forks when it is mixed with gravity loads.
    """
    if combo.name in _seen:
        return False
    seen = _seen | {combo.name}
    operator = _operator_of(combo)
    if operator == "srss":
        return True
    if operator != "linear" or not combo.entries:
        return False
    return all(_is_magnitude_entry(e, load_cases, load_combinations, seen) for e in combo.entries)


def _is_magnitude_entry(
    entry: LoadCombinationEntry,
    load_cases: dict[str, LoadCase],
    load_combinations: dict[str, LoadCombination],
    _seen: frozenset = frozenset(),
) -> bool:
    """Whether *entry* refers to a magnitude result (spectrum case / SRSS).

    An explicit :attr:`~LoadCombinationEntry.magnitude` hint wins, so an
    external definition can declare the fork semantics without the caller
    having to supply the model's ``load_cases``; otherwise a ``"combo"``
    reference is resolved recursively and a ``"case"`` reference is classed by
    its load-case type.
    """
    if entry.magnitude:
        return True
    if entry.kind == "combo":
        sub = load_combinations.get(entry.name)
        return sub is not None and _is_magnitude_combo(sub, load_cases, load_combinations, _seen)
    if entry.kind == "case":
        return _is_spectrum_case(load_cases.get(entry.name))
    return False


def _has_non_magnitude_terms(
    combo: LoadCombination,
    load_cases: dict[str, LoadCase],
    load_combinations: dict[str, LoadCombination],
) -> bool:
    """Whether *combo* mixes a magnitude with any non-magnitude term."""
    return any(not _is_magnitude_entry(e, load_cases, load_combinations) for e in combo.entries)


def _scaled(composite: CompositeLoadCase, factor: float) -> CompositeLoadCase:
    """Return *composite* scaled by *factor*.

    A plain linear composite has its case factors scaled in place; anything
    else (max/min/srss, or a linear composite with children) is wrapped in a
    linear composite carrying a signed child term.  The child's coordinates
    travel with it either way.
    """
    if composite.operator == "linear" and not composite.children:
        return replace(composite, cases={k: v * factor for k, v in composite.cases.items()})
    return CompositeLoadCase(
        name=composite.name,
        operator="linear",
        children=[(factor, composite)],
        source=composite.source,
        coords=composite.coords,
    )


def _coord_tag(name: str, factor: float) -> str:
    """Return *name* prefixed with the sign of *factor*.

    The signed coordinate name for one forked magnitude reference — ``"+"``
    for a non-negative factor, ``"-"`` for a negative one — so a negative
    ``entry.factor`` yields its first sense with the matching sign instead of a
    hard-coded ``"+"``.
    """
    return ("+" if factor >= 0 else "-") + name


def _tag_coord(composite: CompositeLoadCase, tag: str) -> CompositeLoadCase:
    """Return *composite* relabelled with *tag* as its single coordinate.

    **Sets** rather than appends: at a fork point the tagged composite is one
    *term* of the parent combination, and the dimension being forked is that
    term's sign.  A sub-composite's own coordinates describe its internal
    family and must not leak in as extra dimensions — concatenating is what
    :func:`_merge_linear` does when it brings sibling terms together.

    Recording the coordinate here (where the dimension being forked is known
    exactly) is why it need not be re-derived later from the signed factors: a
    ``.s2k`` spectrum case can be recognised by its load-case type, but an
    external definition's magnitude reference only by its own hint.
    """
    return replace(composite, coords=(tag,))


def _merge_linear(a: CompositeLoadCase, b: CompositeLoadCase) -> CompositeLoadCase:
    """Sum two linear composites (case factors added, child terms appended)."""
    cases = dict(a.cases)
    for name, factor in b.cases.items():
        cases[name] = cases.get(name, 0.0) + factor
    return CompositeLoadCase(
        name=a.name,
        operator="linear",
        cases=cases,
        children=[*a.children, *b.children],
        source=a.source or b.source,
        coords=(*a.coords, *b.coords),
    )


def _name_variants(combo_name: str, composites: list[CompositeLoadCase]) -> list[CompositeLoadCase]:
    """Assign unique names to the variants of one combination."""
    if len(composites) == 1:
        composites[0].name = combo_name
    else:
        for i, composite in enumerate(composites):
            composite.name = f"{combo_name} #{i + 1}"
    return composites


def _entry_variants(
    entry: LoadCombinationEntry,
    parent: LoadCombination,
    load_cases: dict[str, LoadCase],
    load_combinations: dict[str, LoadCombination],
    envelope_mode: str,
    active: frozenset,
    fork_magnitudes: bool = True,
) -> list[CompositeLoadCase]:
    """Return the composites contributed by one reference, scaled by its factor.

    ``fork_magnitudes`` controls the ± rule: when set (and the parent mixes a
    magnitude with non-magnitude terms) a spectrum case or an SRSS result is
    emitted twice, once positive and once negative.
    """
    if entry.kind == "combo":
        sub = load_combinations.get(entry.name)
        if sub is None:
            raise ValueError(
                f"Load combination {entry.name!r} referenced by {parent.name!r} is not defined"
            )
        sub_results = generate_combination_results(
            sub, load_cases, load_combinations, envelope_mode=envelope_mode, _active=active
        )
        variants = [_scaled(c, entry.factor) for c in sub_results]
        if (
            fork_magnitudes
            and _is_magnitude_combo(sub, load_cases, load_combinations)
            and _has_non_magnitude_terms(parent, load_cases, load_combinations)
        ):
            variants = [_tag_coord(c, _coord_tag(entry.name, entry.factor)) for c in variants]
            variants += [
                _tag_coord(_scaled(c, -entry.factor), _coord_tag(entry.name, -entry.factor))
                for c in sub_results
            ]
        return variants

    variants = [
        CompositeLoadCase(
            name=entry.name,
            operator="linear",
            cases={entry.name: entry.factor},
            source=entry.name,
        )
    ]
    if (
        fork_magnitudes
        and _is_magnitude_entry(entry, load_cases, load_combinations)
        and _has_non_magnitude_terms(parent, load_cases, load_combinations)
    ):
        variants[0].coords = (_coord_tag(entry.name, entry.factor),)
        variants.append(
            CompositeLoadCase(
                name=entry.name,
                operator="linear",
                cases={entry.name: -entry.factor},
                source=entry.name,
                coords=(_coord_tag(entry.name, -entry.factor),),
            )
        )
    return variants


def _generate_linear(
    combo: LoadCombination,
    load_cases: dict[str, LoadCase],
    load_combinations: dict[str, LoadCombination],
    envelope_mode: str,
    active: frozenset,
) -> list[CompositeLoadCase]:
    """Expand a Linear-Add combination into its composite variants."""
    cartesian = [CompositeLoadCase(name="", operator="linear", source=combo.name)]
    for entry in combo.entries:
        variants = _entry_variants(
            entry, combo, load_cases, load_combinations, envelope_mode, active
        )
        cartesian = [_merge_linear(base, v) for base in cartesian for v in variants]
    variants = _name_variants(combo.name, cartesian)
    family = "single" if len(variants) == 1 else "fork"
    for variant in variants:
        variant.family = family
    return variants


def _generate_envelope(
    combo: LoadCombination,
    load_cases: dict[str, LoadCase],
    load_combinations: dict[str, LoadCombination],
    envelope_mode: str,
    active: frozenset,
) -> list[CompositeLoadCase]:
    """Expand an Envelope combination per *envelope_mode*."""
    if envelope_mode == "per_path":
        out: list[CompositeLoadCase] = []
        for entry in combo.entries:
            variants = _entry_variants(
                entry,
                combo,
                load_cases,
                load_combinations,
                envelope_mode,
                active,
                fork_magnitudes=False,
            )
            for variant in variants:
                variant.name = f"{combo.name} [{entry.name}]"
                variant.source = combo.name
                variant.family = "path"
                variant.coords = (entry.name,)
                out.append(variant)
        return out

    max_children: list[tuple[float, CompositeLoadCase]] = []
    min_children: list[tuple[float, CompositeLoadCase]] = []
    for entry in combo.entries:
        variants = _entry_variants(
            entry,
            combo,
            load_cases,
            load_combinations,
            envelope_mode,
            active,
            fork_magnitudes=False,
        )
        magnitude = _is_magnitude_entry(entry, load_cases, load_combinations)
        for variant in variants:
            max_children.append((1.0, variant))
            min_children.append((-1.0, variant) if magnitude else (1.0, variant))
    return [
        CompositeLoadCase(
            name=f"{combo.name} [max]",
            operator="max",
            children=max_children,
            source=combo.name,
            family="envelope",
            coords=("max",),
        ),
        CompositeLoadCase(
            name=f"{combo.name} [min]",
            operator="min",
            children=min_children,
            source=combo.name,
            family="envelope",
            coords=("min",),
        ),
    ]


def _generate_srss(
    combo: LoadCombination,
    load_cases: dict[str, LoadCase],
    load_combinations: dict[str, LoadCombination],
    envelope_mode: str,
    active: frozenset,
) -> list[CompositeLoadCase]:
    """Expand an SRSS combination into a single magnitude composite."""
    children: list[tuple[float, CompositeLoadCase]] = []
    for entry in combo.entries:
        for variant in _entry_variants(
            entry,
            combo,
            load_cases,
            load_combinations,
            envelope_mode,
            active,
            fork_magnitudes=False,
        ):
            children.append((1.0, variant))
    return [
        CompositeLoadCase(
            name=combo.name,
            operator="srss",
            children=children,
            source=combo.name,
            family="srss",
            coords=("srss",),
        )
    ]


def generate_combination_results(
    combo: LoadCombination,
    load_cases: dict[str, LoadCase],
    load_combinations: dict[str, LoadCombination],
    *,
    envelope_mode: str = "maxmin",
    _active: Optional[frozenset] = None,
) -> list[CompositeLoadCase]:
    """Generate the composite load cases for one combination.

    Args:
        combo: Combination to expand.
        load_cases: Flat ``{name: LoadCase}`` mapping (leaf lookup).
        load_combinations: Flat ``{name: LoadCombination}`` mapping.
        envelope_mode: Envelope strategy — ``"maxmin"`` produces a
            per-quantity maximum and minimum composite; ``"per_path"``
            produces one composite per referenced branch.
        _active: Internal cycle-guard set.

    Returns:
        The composites, in a deterministic order.  A pure Linear-Add
        combination with no magnitude term yields exactly one.

    Raises:
        ValueError: On a cyclic reference, an undefined referenced
            combination, an unsupported combination type, or a bad
            *envelope_mode*.
    """
    if envelope_mode not in ENVELOPE_MODES:
        raise ValueError(f"envelope_mode must be one of {ENVELOPE_MODES}, got {envelope_mode!r}")
    active = frozenset() if _active is None else _active
    if combo.name in active:
        raise ValueError(
            f"Cyclic load-combination reference: {combo.name!r} is already on "
            f"the path {sorted(active)}"
        )
    active = active | {combo.name}

    operator = _operator_of(combo)
    if operator == "linear":
        return _generate_linear(combo, load_cases, load_combinations, envelope_mode, active)
    if operator == "envelope":
        return _generate_envelope(combo, load_cases, load_combinations, envelope_mode, active)
    if operator == "srss":
        return _generate_srss(combo, load_cases, load_combinations, envelope_mode, active)
    raise ValueError(f"Unsupported combination type {combo.combo_type!r} for {combo.name!r}")


def expand_linear_combination(
    combo: LoadCombination,
    load_cases: dict[str, LoadCase],
    load_combinations: dict[str, LoadCombination],
) -> dict[str, float]:
    """Return ``{load_case: combined_factor}`` for a single linear combination.

    Convenience wrapper for the common case (no spectrum fork, no envelope,
    no SRSS).  Use :func:`generate_combination_results` when the combination
    may yield several composites.

    Raises:
        ValueError: If the combination does not resolve to exactly one
            purely linear composite.
    """
    results = generate_combination_results(combo, load_cases, load_combinations)
    if len(results) != 1 or results[0].operator != "linear" or results[0].children:
        raise ValueError(
            f"{combo.name!r} does not expand to a single linear factor list "
            f"({len(results)} composite(s), operators "
            f"{sorted({r.operator for r in results})})"
        )
    return dict(results[0].cases)


# ═══════════════════════════════════════════════════════════════════════
# Variant metadata — the single owner of the sense rule
# ═══════════════════════════════════════════════════════════════════════


def _is_magnitude_shape(
    composite: CompositeLoadCase,
    load_cases: dict[str, LoadCase],
    load_combinations: dict[str, LoadCombination],
    _seen: frozenset = frozenset(),
) -> bool:
    """Whether *composite* evaluates to a magnitude rather than a signed field.

    True for an ``srss`` / ``max`` / ``min`` composite, and for a linear one
    whose every term is a magnitude — e.g. the wrapper :func:`_scaled` builds
    around an SRSS result, or the single-sense variant of a forked spectrum
    case.
    """
    if composite.operator in ("srss", "max", "min"):
        return True
    if composite.operator != "linear" or id(composite) in _seen:
        return False
    seen = _seen | {id(composite)}
    if not all(
        _is_magnitude_shape(child, load_cases, load_combinations, seen)
        for _, child in composite.children
    ):
        return False
    return all(_is_spectrum_case(load_cases.get(case)) for case in composite.cases)


def fork_coords(
    composite: CompositeLoadCase,
    load_cases: Optional[dict[str, LoadCase]] = None,
    load_combinations: Optional[dict[str, LoadCombination]] = None,
) -> tuple:
    """Return the signed coordinate tuple identifying *composite* in its family.

    The coordinates are the composite's stable identity: a reader groups
    variants by them and derives any display label from them, never the
    reverse (``generate_combination_results`` names forks ``"<combo> #n"``,
    which carries no sign information at all).

    Args:
        composite: A composite from :func:`generate_combination_results`.
        load_cases: Flat ``{name: LoadCase}`` mapping — needed to tell a
            response-spectrum **magnitude** reference from a signed one.
        load_combinations: Flat ``{name: LoadCombination}`` mapping — needed
            for the same determination when the reference is a nested
            combination.

    Returns:
        * ``("max",)`` / ``("min",)`` for an Envelope reduction,
        * ``("srss",)`` for an SRSS magnitude,
        * the signed magnitude references of a fork, in generation order —
          ``("+RSX",)`` / ``("-RSX",)``, or the four ``("±RSX", "±RSY")``
          corners of two independent magnitudes,
        * ``()`` when the composite has no forked dimension.
    """
    load_cases = load_cases or {}
    load_combinations = load_combinations or {}
    if composite.operator in ("max", "min", "srss"):
        return (composite.operator,)
    coords: list[str] = []
    for case, factor in composite.cases.items():
        if _is_spectrum_case(load_cases.get(case)):
            coords.append(("+" if factor >= 0 else "-") + case)
    for factor, child in composite.children:
        if _is_magnitude_shape(child, load_cases, load_combinations):
            coords.append(("+" if factor >= 0 else "-") + (child.source or child.name))
    return tuple(coords)


def combination_case_meta(
    composites: list[CompositeLoadCase],
    load_cases: Optional[dict[str, LoadCase]] = None,
    load_combinations: Optional[dict[str, LoadCombination]] = None,
) -> dict[str, dict[str, str]]:
    """Describe *composites* as per-case metadata for the NPZ writers.

    This is the **single owner** of the sign rule: the ``"+QE"`` / ``"-QE"``
    marker a plotter pairs on, the ``family`` it groups by and the ``coords``
    it labels with are all derived here from a composite's own signed factors,
    so no consumer needs to re-derive them (or scan for an ``"RS…"`` key).

    Args:
        composites: Composites from :func:`generate_combination_results`.
        load_cases: Flat ``{name: LoadCase}`` mapping.
        load_combinations: Flat ``{name: LoadCombination}`` mapping.

    Returns:
        ``{composite_name: {"group", "kind", "family", "coords"}}`` — ready to
        hand to ``write_results(..., case_meta=...)``.  ``group`` is the
        combination the variant came from; ``kind`` is ``"+QE"`` / ``"-QE"``
        for a **single**-sense fork and ``""`` otherwise (a multi-fork's sense
        lives in ``coords``); ``family`` is ``"single"`` / ``"fork"`` /
        ``"envelope"`` / ``"path"`` / ``"srss"``; ``coords`` is the
        :func:`fork_coords` tuple joined with ``"|"`` so it fits one string
        array.

    See Also:
        ``docs/force_diagram_unification.md`` → *Two-sided (envelope) results*.
    """
    meta: dict[str, dict[str, str]] = {}
    for composite in composites:
        # Coordinates are recorded at the fork point; the geometric extraction
        # is a fallback for hand-built composites.
        coords = composite.coords or fork_coords(composite, load_cases, load_combinations)
        family = composite.family
        if not family:
            if composite.operator in ("max", "min"):
                family = "envelope"
            elif composite.operator == "srss":
                family = "srss"
            elif any(coord[:1] in ("+", "-") for coord in coords):
                family = "fork"
            else:
                family = "single"
        if family == "single":
            # No fork dimension — a pure magnitude sum has no coordinate.
            coords = ()
        kind = ""
        if family == "fork" and len(coords) == 1 and coords[0][:1] in ("+", "-"):
            kind = "+QE" if coords[0][0] == "+" else "-QE"
        meta[composite.name] = {
            "group": composite.source or composite.name,
            "kind": kind,
            "family": family,
            "coords": "|".join(coords),
        }
    return meta


# ═══════════════════════════════════════════════════════════════════════
# Numerical evaluation
# ═══════════════════════════════════════════════════════════════════════


def apply_composite_load_case(
    composite: CompositeLoadCase,
    case_values: dict[str, np.ndarray],
) -> np.ndarray:
    """Evaluate *composite* against per-case arrays.

    Args:
        composite: Composite to evaluate.
        case_values: ``{load_case_name: ndarray}`` — the array being combined
            for each referenced load case.

    Returns:
        The combined array: a weighted sum (``"linear"``), an element-wise
        extreme (``"max"`` / ``"min"``) or an element-wise root-sum-square
        (``"srss"``).

    Raises:
        KeyError: If a referenced load case has no supplied array.
        ValueError: If the composite has no terms, or an unknown operator.
    """
    if composite.operator == "linear":
        total: Optional[np.ndarray] = None
        for case, factor in composite.cases.items():
            if case not in case_values:
                raise KeyError(
                    f"Composite {composite.name!r} references load case "
                    f"{case!r} with no supplied values"
                )
            term = factor * np.asarray(case_values[case], dtype=float)
            total = term if total is None else total + term
        for factor, child in composite.children:
            term = factor * apply_composite_load_case(child, case_values)
            total = term if total is None else total + term
        if total is None:
            raise ValueError(f"Composite {composite.name!r} has no terms")
        return total

    parts = [
        factor * apply_composite_load_case(child, case_values)
        for factor, child in composite.children
    ]
    if not parts:
        raise ValueError(f"Composite {composite.name!r} has no children")
    if composite.operator == "max":
        return np.maximum.reduce(parts)
    if composite.operator == "min":
        return np.minimum.reduce(parts)
    if composite.operator == "srss":
        return np.sqrt(np.sum([part * part for part in parts], axis=0))
    raise ValueError(f"Unknown composite operator {composite.operator!r}")


def generate_composite_results(
    composites: list[CompositeLoadCase],
    case_results: dict[str, dict[str, np.ndarray]],
) -> dict[str, dict[str, np.ndarray]]:
    """Evaluate *composites* into per-array result sets.

    The returned mapping has the same shape as ``write_results_npz``'s
    ``static_results`` argument, so composites are serialised as ordinary
    cases under ``static/{composite_name}/...`` and are therefore visible to
    every NPZ consumer (force diagrams, viewers, the report) without any
    schema change::

        combined = generate_composite_results(composites, case_results)
        write_results_npz(path, md=md, static_results={**case_results, **combined})

    Args:
        composites: Composites from :func:`generate_combination_results`.
        case_results: ``{load_case_name: {array_name: ndarray}}``.

    Returns:
        ``{composite_name: {array_name: ndarray}}``.

    Raises:
        KeyError: If a composite references a load case absent from
            *case_results*, or one that lacks a needed array.
    """
    array_names: list[str] = []
    for per_case in case_results.values():
        for array_name in per_case:
            if array_name not in array_names:
                array_names.append(array_name)

    out: dict[str, dict[str, np.ndarray]] = {}
    for composite in composites:
        per_array: dict[str, np.ndarray] = {}
        for array_name in array_names:
            values = {
                case: per_case[array_name]
                for case, per_case in case_results.items()
                if array_name in per_case
            }
            per_array[array_name] = apply_composite_load_case(composite, values)
        out[composite.name] = per_array
    return out
