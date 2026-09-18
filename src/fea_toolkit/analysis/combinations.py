"""Static load-case results → combination (composite) result sets.

A SAP2000 / ETABS load combination is a *reference graph* over load cases —
see :mod:`fea_toolkit.model.load_combinations`.  This module closes the loop
from the analysis side: given the per-case result payloads produced by
``AnalysisBuilder.run_static_analysis`` it expands every combination with
:func:`~fea_toolkit.model.load_combinations.generate_combination_results` and
evaluates the resulting composites, returning payloads in exactly the shape
``AnalysisBuilder.export_results`` expects::

    md = SAP2000Parser(path).parse().get_model_data()
    cases = {name: builder.run_static_analysis(...) for name in required}
    combined = build_combination_results(cases, model=md)
    builder.export_results("results.npz", static_results={**cases, **combined})

or, in one call::

    builder.export_results(
        "results.npz", static_results=cases, model=md, expand_combinations=True
    )

The composites then appear as ordinary ``static/{composite}/...`` entries in
the NPZ, so ``plot_force_diagram(..., combo="COMB1")`` and the interactive
viewers work unchanged.

Only numeric payload fields are combined (force series, nodal-displacement
vectors, numeric scalars).  Non-numeric leaves — flags such as ``converged``,
status strings — are skipped and logged at debug level, because a max/min or
SRSS reduction of a boolean is meaningless.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import numpy as np

from ..model.load_combinations import (
    generate_combination_results,
    generate_composite_results,
)
from ..model.sap_data import SAPModelData

logger = logging.getLogger(__name__)

__all__ = ["build_combination_results"]

_ARRAY_LIKE = (list, tuple, np.ndarray)


def _is_number(value: Any) -> bool:
    """Whether *value* is a real numeric leaf (booleans excluded)."""
    return isinstance(value, (int, float, np.number)) and not isinstance(value, bool)


def _flatten(payload: dict[str, Any], prefix: tuple = ()) -> dict[tuple, np.ndarray]:
    """Flatten a result payload to ``{key-path tuple: ndarray}``.

    Dicts are recursed (so ``nodal_displacements`` and ``element_forces``
    contribute one array per tag / component); list-like values become a
    single array leaf; numeric scalars become 0-d arrays.  Anything else is
    skipped with a debug log.

    Keys are kept as **tuple paths** rather than dotted strings so the
    original key types survive — ``nodal_displacements`` stays keyed by node
    tag (``int``), not by ``"1"``.
    """
    flat: dict[tuple, np.ndarray] = {}
    for key, value in payload.items():
        path = (*prefix, key)
        if isinstance(value, dict):
            flat.update(_flatten(value, path))
        elif isinstance(value, _ARRAY_LIKE):
            array = np.asarray(value, dtype=float)
            if array.ndim:
                flat[path] = array
        elif _is_number(value):
            flat[path] = np.asarray(float(value))
        else:
            logger.debug("combination: skipping non-numeric field %r", ".".join(map(str, path)))
    return flat


def _unflatten(flat: dict[tuple, np.ndarray]) -> dict[str, Any]:
    """Rebuild the nested payload shape from :func:`_flatten` output."""
    payload: dict[str, Any] = {}
    for path, array in flat.items():
        node = payload
        for part in path[:-1]:
            node = node.setdefault(part, {})
        node[path[-1]] = array.tolist() if array.ndim else float(array)
    return payload


def build_combination_results(
    case_results: dict[str, dict[str, Any]],
    *,
    model: Optional[SAPModelData] = None,
    load_cases: Optional[dict] = None,
    load_combinations: Optional[dict] = None,
    combinations: Optional[list[str]] = None,
    envelope_mode: str = "maxmin",
) -> dict[str, dict[str, Any]]:
    """Expand load combinations into composite result payloads.

    Args:
        case_results: ``{load_case_name: payload}`` — the result dicts from
            ``run_static_analysis`` (or an equivalent extraction).  Every load
            case referenced by a requested combination must be present.
        model: Parsed model data supplying ``load_cases`` and
            ``load_combinations``.  Alternatively pass those two mappings
            directly.
        load_cases: Explicit ``{name: LoadCase}`` (overrides ``model``).
        load_combinations: Explicit ``{name: LoadCombination}`` (overrides
            ``model``).
        combinations: Combination names to expand.  Defaults to every
            combination in *load_combinations*.
        envelope_mode: Envelope strategy — ``"maxmin"`` (per-quantity
            maximum/minimum) or ``"per_path"`` (one composite per branch).

    Returns:
        ``{composite_name: payload}`` in the same shape as *case_results*, so
        it can be merged straight into ``static_results``.

    Raises:
        ValueError: If neither *model* nor *load_combinations* is supplied, or
            a combination is unsupported / cyclic / carries a bad
            *envelope_mode*.
        KeyError: If a requested combination is unknown, or a combination
            references a load case missing from *case_results*.
    """
    if model is not None:
        if load_cases is None:
            load_cases = model.load_cases
        if load_combinations is None:
            load_combinations = model.load_combinations
    if load_combinations is None:
        raise ValueError("build_combination_results() needs model= or load_combinations=")
    load_cases = load_cases if load_cases is not None else {}

    case_arrays = {name: _flatten(payload) for name, payload in case_results.items()}
    names = list(combinations) if combinations is not None else list(load_combinations)

    out: dict[str, dict[str, Any]] = {}
    for name in names:
        combo = load_combinations.get(name)
        if combo is None:
            raise KeyError(f"Unknown load combination {name!r}")
        composites = generate_combination_results(
            combo, load_cases, load_combinations, envelope_mode=envelope_mode
        )
        evaluated = generate_composite_results(composites, case_arrays)
        for composite_name, arrays in evaluated.items():
            out[composite_name] = _unflatten(arrays)
    return out
