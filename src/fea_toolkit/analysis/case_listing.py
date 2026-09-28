"""What a model can be analysed for — a Qt-free listing (P27 / I2).

The ``Analysis ▸ Run`` dialog needs to know **what it can offer** before it can
run anything:

* the model's **analysable static cases**, each as the ``{pattern: factor}`` map
  a solve applies (see :func:`fea_toolkit.model.sap_data.patterns_from_case`);
* the model's **load combinations**, each with the load cases it references, so
  a combination can be run by first running those cases and then reducing them;
* the model's **load patterns**, so a case can be authored in the dialog
  (``{"ULT": {"Dead": 1.4, "Live": 1.6}}``).

Nothing here touches OpenSees or Qt — it is a read over the parsed
:class:`~fea_toolkit.model.sap_data.SAPModelData`, so the GUI renders it on the
GUI thread and it is unit-tested without the ``[gui]`` extra.  The *running* of
what this lists is :func:`fea_toolkit.analysis.linear.run_static_cases`.

The auto-detection and the zero-load filter here are the **same ones**
:func:`~fea_toolkit.analysis.linear.run_linear_cases` applies, because this
module is where they now live: the runner calls :func:`list_static_cases` rather
than keeping a second, drifting copy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from ..model.sap_data import patterns_from_case

if TYPE_CHECKING:
    from ..model.sap_data import SAPModelData

__all__ = [
    "CombinationSpec",
    "case_has_load",
    "list_combinations",
    "list_patterns",
    "list_static_cases",
    "pattern_has_load",
]


#: The ``case_type`` values a static (solveable) load case carries.  SAP2000's
#: ``.s2k`` spells a linear-static case ``LinStatic``; the lowercase ``static``
#: is the ETABS spelling and appears in hand-built models.  Compared
#: case-insensitively, which is what
#: :meth:`SAPModelData.auto_detect_static_cases` does — a model authored by hand
#: is judged on the same rule as a parsed one.
_STATIC_CASE_TYPES = ("linstatic", "static")


@dataclass(frozen=True)
class CombinationSpec:
    """One load combination and the load cases it needs.

    Attributes:
        name: Combination name, as the model defines it.
        combo_type: The combination operator — ``"Linear Add"``, ``"Envelope"``,
            ``"SRSS"`` …  For a *static-linear* run only the linear-add family is
            runnable; the rest are still listed, so the dialog can show what the
            model holds and grey the operators this slice cannot reduce.
        leaves: ``{load_case_name: aggregate_factor}`` — every load case the
            combination references, however deep, with its factors multiplied
            along the path and summed when a case is reachable more than once
            (:func:`~fea_toolkit.model.load_combinations.calculate_aggregate_factors`).
            These are the cases a run must solve before the combination can be
            reduced.  Empty when the combination could not be expanded (a cyclic
            or dangling reference), in which case :attr:`error` says why.
        error: ``""`` when the combination expanded cleanly, otherwise a short
            reason it could not — the dialog reports it rather than the listing
            raising, because a single bad combination must not hide the rest.
    """

    name: str
    combo_type: str = ""
    leaves: dict[str, float] = field(default_factory=dict)
    error: str = ""


# ── Load patterns ───────────────────────────────────────────────────


def pattern_has_load(md: SAPModelData, pattern: str, mesh_model: Any = None) -> bool:
    """Whether *pattern* actually applies any load to the model.

    A pattern is "loaded" when any of the model's load families names it, or
    when it carries a self-weight factor.  Edge loads derived from loaded areas
    live on the *preprocessed* model, so when *mesh_model* is given they are
    consulted too; without it (a listing made before preprocessing) they are
    simply not counted.

    Args:
        md: Parsed model data.
        pattern: Load-pattern name to test.
        mesh_model: Optional preprocessed ``MeshModel`` supplying
            ``edge_loads_from_areas``.

    Returns:
        ``True`` when the pattern contributes load.
    """
    lp = md.load_patterns.get(pattern)
    if lp is not None and lp.self_weight_factor > 0:
        return True
    if (
        any(ld.pattern == pattern for ld in md.frame_dist_loads)
        or any(ld.pattern == pattern for ld in md.joint_loads)
        or any(ld.pattern == pattern for ld in md.area_gravity_loads)
        or any(ld.pattern == pattern for ld in md.area_uniform_loads)
    ):
        return True
    edge_loads = getattr(mesh_model, "edge_loads_from_areas", None) or []
    return any(ld.pattern == pattern for ld in edge_loads)


def case_has_load(md: SAPModelData, patterns: dict[str, float], mesh_model: Any = None) -> bool:
    """Whether any pattern in a case's ``{pattern: factor}`` map carries load.

    Args:
        md: Parsed model data.
        patterns: ``{pattern: factor}`` for one case.
        mesh_model: Optional preprocessed model (see :func:`pattern_has_load`).

    Returns:
        ``True`` when at least one of the case's patterns contributes load.
    """
    return any(pattern_has_load(md, name, mesh_model) for name in patterns)


def list_patterns(md: SAPModelData) -> list[str]:
    """Load-pattern names, in model order.

    The list a dialog offers when authoring a case by hand.  Order is the
    model's own, so a pattern's position matches the model tree.

    Args:
        md: Parsed model data.

    Returns:
        Pattern names as a list.
    """
    return list(md.load_patterns)


# ── Static cases ────────────────────────────────────────────────────


def list_static_cases(
    md: SAPModelData,
    mesh_model: Any = None,
    *,
    config_cases: Optional[list] = None,
) -> dict[str, dict[str, float]]:
    """The model's analysable static cases as ``{case: {pattern: factor}}``.

    Auto-detects every static case the model defines, merges any *config_cases*
    entries over them (a **string** names a model case, a **dict** defines one as
    ``{case_name: {pattern: factor}}``), and drops cases whose patterns carry no
    load.  That is exactly the set
    :func:`~fea_toolkit.analysis.linear.run_linear_cases` would solve.

    Args:
        md: Parsed model data.
        mesh_model: Optional preprocessed model, consulted for edge loads when
            deciding whether a pattern carries load.
        config_cases: Optional ``linear_cfg["cases"]`` list — user overrides,
            merged *over* the auto-detected cases (an entry replaces one of the
            same name).

    Returns:
        ``{case_name: {pattern: factor}}``, filtered to cases that carry load.
    """
    cases: dict[str, dict[str, float]] = {}
    for cname, lc in md.load_cases.items():
        if (getattr(lc, "case_type", "") or "").lower() not in _STATIC_CASE_TYPES:
            continue
        pats = patterns_from_case(lc)
        if pats:
            cases[cname] = dict(pats)

    for entry in config_cases or []:
        if isinstance(entry, str):
            lc = md.load_cases.get(entry)
            if lc is None:
                continue
            pats = patterns_from_case(lc)
            if pats:
                cases[entry] = dict(pats)
        elif isinstance(entry, dict):
            for cname, pat_dict in entry.items():
                cases[cname] = dict(pat_dict)

    return {c: p for c, p in cases.items() if case_has_load(md, p, mesh_model)}


# ── Combinations ────────────────────────────────────────────────────


def list_combinations(md: SAPModelData) -> list[CombinationSpec]:
    """The model's load combinations, each with the load cases it needs.

    A combination is a reference graph over load cases (and other combinations);
    each one is expanded through
    :func:`~fea_toolkit.model.load_combinations.build_combo_tree` and its leaves
    aggregated by
    :func:`~fea_toolkit.model.load_combinations.calculate_aggregate_factors`.
    The parsed model already classified each entry's ``kind``
    (``io/s2k_parser.py``), so nothing is mutated here.

    A combination that cannot be expanded — a cycle, or a reference to a name
    the model does not define — is returned with empty :attr:`CombinationSpec.leaves`
    and its :attr:`CombinationSpec.error` set, rather than failing the whole
    listing: one bad combination must not hide every good one.

    Args:
        md: Parsed model data.

    Returns:
        One :class:`CombinationSpec` per combination, in model order.
    """
    from ..model.load_combinations import build_combo_tree, calculate_aggregate_factors

    combos = md.load_combinations or {}
    out: list[CombinationSpec] = []
    for name, combo in combos.items():
        combo_type = getattr(combo, "combo_type", "") or ""
        try:
            tree = build_combo_tree(combos, name)
            leaves = calculate_aggregate_factors(tree)
        except (KeyError, ValueError) as exc:
            out.append(CombinationSpec(name=name, combo_type=combo_type, error=str(exc)))
            continue
        out.append(CombinationSpec(name=name, combo_type=combo_type, leaves=dict(leaves)))
    return out
