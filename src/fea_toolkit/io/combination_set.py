"""JSON read/write for external combination-definition sets.

A **combination set** is the canonical ``{name: {"type", "entries", ...}}``
mapping that
:func:`fea_toolkit.model.load_combinations.combination_set_from_dict` builds —
the typed, hand-authorable counterpart of a model's own
``SAPModelData.load_combinations``.  Storing one as JSON lets both combination
tools consume a single definition:

* :func:`fea_toolkit.analysis.combinations.build_combination_results` reads it
  to expand a new results NPZ (``definitions=``), and
* :func:`fea_toolkit.plotting.force_diagram.plot_force_diagram` reads it to
  group and label the cases it renders (``combinations=``).

See ``docs/load_combinations.md`` → *External definition sets* for the format
and ``docs/results_schema.md`` for the metadata the expansion persists.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Union

from ..model.load_combinations import (
    combination_set_from_dict,
    combination_set_to_dict,
)
from ..model.sap_data import LoadCombination

__all__ = ["read_combination_set", "write_combination_set"]


def read_combination_set(path: Union[str, Path]) -> dict[str, LoadCombination]:
    """Load a combination set from a JSON file.

    Args:
        path: Path to a JSON object mapping each combination name to its
            definition — ``{"type": ..., "entries": [...], "design"?: {...}}``.
            The shorthand ``{"COMB1": [["DEAD", 1.3], ["RSX", 1.4]]}`` (a bare
            entry list, implicitly Linear Add) is accepted too.

    Returns:
        ``{name: LoadCombination}``.  Entries are still ``kind="case"`` until
        :func:`fea_toolkit.model.load_combinations.classify_combination_refs`
        (or :func:`fea_toolkit.model.load_combinations.merge_combination_sets`
        with ``load_cases=``) resolves them against a model.

    Raises:
        ValueError: If the file's top level is not a JSON object.
        TypeError: If a definition or entry has an unsupported shape.
        FileNotFoundError: If *path* does not exist.

    Examples:
        >>> combos = read_combination_set("combos.json")   # doctest: +SKIP
    """
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: a combination set must be a JSON object of name -> definition")
    return combination_set_from_dict(data)


def write_combination_set(
    path: Union[str, Path], load_combinations: dict[str, LoadCombination]
) -> str:
    """Write a combination set to a JSON file.

    The file is a valid input for :func:`read_combination_set`, so a parsed
    model's combinations can be exported, edited by hand and fed back in as an
    external ``definitions=`` layer.

    Args:
        path: Destination path (written with UTF-8 encoding).
        load_combinations: Flat ``{name: LoadCombination}`` mapping, or an
            already-canonical definition dict (normalised first, so a
            hand-written set can be written straight out).

    Returns:
        The absolute path written, for chaining/logging.
    """
    target = Path(path)
    target.write_text(
        json.dumps(combination_set_to_dict(load_combinations), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return str(target.resolve())
