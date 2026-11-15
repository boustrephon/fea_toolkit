"""Assemble a paste-able snapshot of the GUI's current state, Qt-free.

The ``Help ▸ Copy model context…`` action builds a plain-text summary — model
provenance, unit system, entity counts, the current selection, the recipe and
the open views — so a user can paste it into an LLM conversation or a bug
report.  Pure text assembly here keeps the formatter unit-testable without the
``[gui]`` extra; the main window gathers the pieces and hands them in.
"""

from typing import Optional

__all__ = ["build_context_snapshot"]


def build_context_snapshot(
    source: str = "",
    units: str = "",
    counts: Optional[dict] = None,
    selection: str = "",
    recipe_json: str = "",
    views: Optional[list] = None,
) -> str:
    """Return a plain-text snapshot of the application state.

    Args:
        source: Model provenance, e.g. the file name.  ``""`` renders "(none)".
        units: Human-readable unit system, e.g. ``"N · m · C"``.
        counts: Ordered ``{label: n}`` entity counts.
        selection: The current selection expression, or ``""``.
        recipe_json: The recipe serialised as JSON, or ``""``.
        views: ``[(name, kind), ...]`` of the open views.

    Returns:
        The snapshot text (trailing newline included).
    """
    lines = ["fea_toolkit model context", "=" * 48]
    lines.append(f"Source: {source or '(none)'}")
    lines.append(f"Units: {units or '(unknown)'}")
    if counts:
        lines.append("Model: " + ", ".join(f"{label}: {value}" for label, value in counts.items()))
    else:
        lines.append("Model: (none)")
    lines.append(f"Selection: {selection or '(none)'}")
    if views:
        lines.append("Views:")
        for name, kind in views:
            lines.append(f"  - {name} ({kind})")
    if recipe_json:
        lines.append("Recipe:")
        lines.append(recipe_json)
    return "\n".join(lines) + "\n"
