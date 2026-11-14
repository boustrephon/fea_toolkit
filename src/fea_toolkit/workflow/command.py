"""The command palette's grammar — a command string compiles into a Step.

A command is ``<verb> [clause ...]``: the verb first, then ``KEY=VALUE``
clauses.  A clause is a **parameter** iff it is not negated and its key is a
declared parameter of the verb; every other ``[NOT] KEY=VALUE`` clause is a
**selection** clause, re-joined and parsed by
:meth:`~fea_toolkit.model.selection.Selection.from_string` — so the scope shares
one spelling with the recipe editor (no second selection language).

Parameter values parse via :func:`ast.literal_eval` (a bare word falls back to a
string), then :meth:`ParamSpec.coerce` validates type and choices.  This module
is Qt-free and stdlib-only, so the grammar can be tested without a GUI and
without OpenSees.
"""

import ast
import json
from typing import Any

from ..model.selection import Selection, scan_clauses
from .registry import STEP_SPECS
from .steps import Step

__all__ = ["compile_command", "missing_needs", "step_to_command"]


def _literal(value: str) -> Any:
    """Parse a parameter value as a Python literal, or a bare word as a string."""
    stripped = value.strip()
    try:
        return ast.literal_eval(stripped)
    except (ValueError, SyntaxError):
        return stripped


def _render_value(value: Any) -> str:
    """Render a parameter value for a command string.

    Strings are double-quoted with :func:`json.dumps` (the scanner's own quoting
    rule), so a value with spaces, a semicolon or a ``KEY=`` fragment survives
    the round-trip.  Containers are rendered recursively the same way — every
    nested string double-quoted — so a structured parameter such as
    ``{"Wind direction=Y": 1.0}`` stays a single clause instead of splitting on
    the inner space, and :func:`ast.literal_eval` still parses the result back
    exactly (bools and ``None`` keep their Python spellings).
    """
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool) or value is None:
        return repr(value)
    if isinstance(value, dict):
        return (
            "{"
            + ", ".join(f"{_render_value(k)}: {_render_value(v)}" for k, v in value.items())
            + "}"
        )
    if isinstance(value, (list, tuple)):
        items = ", ".join(_render_value(item) for item in value)
        if isinstance(value, tuple):
            return "(" + items + ("," if len(value) == 1 else "") + ")"
        return "[" + items + "]"
    return repr(value)


def compile_command(text: str) -> Step:
    """Compile a command string into a :class:`~fea_toolkit.workflow.steps.Step`.

    Grammar::

        <verb>  [<clause> ...]     # verb first; clauses are KEY=VALUE

    Disambiguation: a clause is a **parameter** iff it is not negated and its
    key is a declared parameter of the verb; every other ``[NOT] KEY=VALUE``
    clause is a **selection** clause, re-joined and passed to
    :meth:`Selection.from_string`.

    Args:
        text: The command, e.g. ``"mesh section=COL create_shells=True"``.

    Returns:
        The compiled step.

    Raises:
        ValueError: If the command is empty, the verb is unknown, a clause does
            not parse, or a parameter value fails its declared type/choices.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("empty command — start with a verb")

    verb, _, rest = text.partition(" ")
    verb = verb.strip()
    spec = STEP_SPECS.get(verb)
    if spec is None:
        raise ValueError(f"unknown verb {verb!r}; known verbs: {sorted(STEP_SPECS)}")

    params: dict[str, Any] = {}
    selection_parts: list[str] = []
    if rest.strip():
        clauses, leftover = scan_clauses(rest)
        if leftover:
            raise ValueError(f"could not parse {leftover!r} as KEY=VALUE clauses")
        for negated, key, value in clauses:
            if not negated and key in spec.params:
                params[key] = _literal(value)
            else:
                selection_parts.append(f"{'NOT ' if negated else ''}{key}={value}")

    for name, raw in params.items():
        spec.params[name].coerce(raw, verb=verb, name=name)

    selection = Selection.from_string(" ".join(selection_parts)) if selection_parts else None
    return Step(verb=verb, selection=selection, params=params)


def step_to_command(step: Step) -> str:
    """The command string that reproduces *step* — the inverse of :func:`compile_command`.

    Selection clauses come first (verbatim, from ``Selection.to_string``), then
    the step's explicit parameters as ``KEY=value``.  Only parameters the step
    actually sets are written (defaults are not echoed), so a step built by
    clicking reads back as the same command that built it.
    """
    parts: list[str] = [step.verb]
    if step.selection is not None:
        expression = step.selection.to_string()
        if expression:
            parts.append(expression)
    for name, value in step.params.items():
        parts.append(f"{name}={_render_value(value)}")
    return " ".join(parts)


def missing_needs(verb: str, produced: set) -> list[str]:
    """The kinds *verb* needs that *produced* does not yet satisfy.

    The palette's precursor hint and the validator share this read of a verb's
    ``needs`` declaration, so there is one ordering vocabulary, never two.

    Args:
        verb: A verb name (a key of ``STEP_SPECS``).
        produced: The kinds already satisfied (see
            :func:`~fea_toolkit.workflow.recipe.satisfied_kinds`).

    Returns:
        The unsatisfied ``needs`` in declaration order (``[]`` when the verb is
        unknown or every prerequisite is present).
    """
    spec = STEP_SPECS.get(verb)
    if spec is None:
        return []
    return [kind for kind in spec.needs if kind not in produced]
