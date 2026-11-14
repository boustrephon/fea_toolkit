"""The ``select`` verb — make (or replace) a named selection.

Stores a :class:`~fea_toolkit.model.named_selection.NamedSelection` on the
context's ``named_selections`` store — the workflow layer's scoping primitive —
from either a :meth:`~fea_toolkit.model.selection.Selection.from_string`
expression or explicit element / node ids (a graphical pick).  A later step
references it through its ``Selection.selections`` field.
"""

from typing import Any

from ..steps import TABLE, ParamSpec, Step, StepContext, StepResult, Table, validate_params

__all__ = ["SELECT_PARAMS", "run_select"]

SELECT_PARAMS: dict[str, ParamSpec] = {
    "name": ParamSpec(default="", type=str, help="The selection's name (required)."),
    "expression": ParamSpec(
        default="",
        type=str,
        help="A Selection.from_string expression, e.g. 'section=COL'.",
    ),
    "pick_elements": ParamSpec(
        default=[],
        type=list,
        help="Explicit frame/area ids (a graphical pick).",
    ),
    "pick_nodes": ParamSpec(
        default=[],
        type=list,
        help="Explicit node ids (a graphical pick).",
    ),
}


def run_select(context: StepContext, step: Step) -> list[StepResult]:
    """Make (or replace) a named selection on the context's store.

    Args:
        context: The run's working state; its ``named_selections`` store is
            written and the document is marked dirty.
        step: The step to run.  ``params`` carry ``name`` and either
            ``expression`` or explicit picked ids.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.TABLE` result summarising the
        stored selection.

    Raises:
        ValueError: If ``name`` is empty.
    """
    from ...model.named_selection import NamedSelection
    from ...model.selection import Selection

    params: dict[str, Any] = validate_params("select", SELECT_PARAMS, step.params)
    name = params["name"]
    if not name:
        raise ValueError("select: 'name' is required (the selection's name)")

    selection = Selection.from_string(params["expression"]) if params["expression"] else Selection()
    named = NamedSelection(
        name=name,
        selection=selection,
        element_ids=list(params["pick_elements"]),
        node_ids=list(params["pick_nodes"]),
    )
    context.named_selections[name] = named
    context.document_dirty = True
    context.log(f"select: named selection {name!r} stored")

    rows = (
        ("name", name),
        ("expression", selection.to_string() or "\u2014"),
        ("elements", str(len(named.element_ids))),
        ("nodes", str(len(named.node_ids))),
    )
    return [
        StepResult(
            kind=TABLE,
            label=f"Selection {name}",
            payload=Table(title=f"Selection {name}", columns=("quantity", "value"), rows=rows),
        )
    ]
