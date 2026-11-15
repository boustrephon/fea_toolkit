"""Qt-free colour-by resolver: map a model attribute to per-element labels.

The Model Tree's right-click **Colour by Material / Section / Story** menus
resolve one label per drawn element (frame or shell), which the viewer turns
into a colour.  Pure data logic, Qt-free — unit-tested without Qt, like
:mod:`fea_toolkit.gui.controllers.selection`.

The palette is shared with the model viewer:
:func:`fea_toolkit.plotting.viz_common.categorical_palette`.  An element whose
field cannot be resolved (no assignment, no section material, no stories) gets
no label and the renderer draws it grey.
"""

from typing import Any, Optional

from ...plotting.viz_common import categorical_palette

__all__ = [
    "COLOUR_FIELDS",
    "FIELD_LABELS",
    "colour_by_field",
    "frame_labels",
    "shell_labels",
    "story_levels",
]

#: Colour-by field key -> the label its menu entry shows.
FIELD_LABELS = {
    "material": "Material",
    "section": "Section",
    "story": "Story",
}

#: Fields a menu can colour by, in menu order.
COLOUR_FIELDS = ("material", "section", "story")

#: A field that could not be resolved for an element — drawn grey by the renderer.
_UNLABELLED = ""


def _active_items(model: Any, attr: str) -> list[tuple[str, Any]]:
    """``[(id, entity), ...]`` for the *drawable* (non-inactive) elements of *attr*."""
    if model is None:
        return []
    bag = getattr(model, attr, {}) or {}
    return [(str(eid), elem) for eid, elem in bag.items() if not getattr(elem, "inactive", False)]


def story_levels(model: Any) -> list[tuple[str, float]]:
    """``[(name, elevation), ...]`` in model units, or ``[]`` when unknown.

    Prefers the imported ``stories`` table (``StoryDefinition``); falls back to
    detected ``story_levels`` (``StoryLevel``), matching
    :func:`fea_toolkit.gui.models.model_index.stories_group`.
    """
    if model is None:
        return []
    stories = getattr(model, "stories", None) or []
    if stories:
        return [(s.name, float(s.elevation)) for s in stories]
    levels = getattr(model, "story_levels", None) or []
    return [(level.name, float(level.elevation)) for level in levels]


def _nearest_story(z_mid: Optional[float], levels: list[tuple[str, float]]) -> str:
    """The story whose elevation is nearest *z_mid*, or ``""`` when unresolvable."""
    if z_mid is None or not levels:
        return _UNLABELLED
    return min(levels, key=lambda pair: abs(pair[1] - z_mid))[0]


def _section_of(model: Any, assignments: dict, eid: str) -> str:
    return str((assignments or {}).get(eid) or "") or _UNLABELLED


def _material_of(model: Any, section_name: str) -> str:
    if not section_name:
        return _UNLABELLED
    section = (getattr(model, "sections", {}) or {}).get(section_name)
    return str(getattr(section, "material", "") or "") or _UNLABELLED


def _frame_z_mid(model: Any, eid: str) -> Optional[float]:
    """Mid-height Z of a frame, or ``None`` when its nodes are absent."""
    elem = (getattr(model, "frame_elements", {}) or {}).get(eid)
    if elem is None:
        return None
    nodes = getattr(model, "nodes", {}) or {}
    ni = nodes.get(getattr(elem, "node_i", None))
    nj = nodes.get(getattr(elem, "node_j", None))
    if ni is None or nj is None:
        return None
    return (float(ni.z) + float(nj.z)) / 2.0


def _area_z_mid(model: Any, aid: str) -> Optional[float]:
    """Centroid Z of an area, or ``None`` when its nodes are absent."""
    elem = (getattr(model, "area_elements", {}) or {}).get(aid)
    if elem is None:
        return None
    nodes = getattr(model, "nodes", {}) or {}
    zs = [float(nodes[nid].z) for nid in getattr(elem, "node_ids", ()) if nid in nodes]
    return sum(zs) / len(zs) if zs else None


def frame_labels(model: Any, field: str) -> dict[str, str]:
    """``{frame_elem_id: label}`` for *field* over the model's active frames.

    Args:
        model: A ``SAPModelData`` or ``MeshModel``.
        field: ``"section"``, ``"material"`` or ``"story"``.

    Returns:
        ``{frame_elem_id: label}``; unresolved elements map to ``""``.
    """
    assignments = getattr(model, "frame_assignments", {}) or {}
    levels = story_levels(model) if field == "story" else []
    out: dict[str, str] = {}
    for eid, _elem in _active_items(model, "frame_elements"):
        if field == "section":
            out[eid] = _section_of(model, assignments, eid)
        elif field == "material":
            out[eid] = _material_of(model, _section_of(model, assignments, eid))
        else:  # story
            out[eid] = _nearest_story(_frame_z_mid(model, eid), levels)
    return out


def shell_labels(model: Any, field: str) -> dict[str, str]:
    """``{area_elem_id: label}`` for *field* over the model's active areas."""
    assignments = getattr(model, "area_assignments", {}) or {}
    levels = story_levels(model) if field == "story" else []
    out: dict[str, str] = {}
    for aid, _elem in _active_items(model, "area_elements"):
        if field == "section":
            out[aid] = _section_of(model, assignments, aid)
        elif field == "material":
            out[aid] = _material_of(model, _section_of(model, assignments, aid))
        else:  # story
            out[aid] = _nearest_story(_area_z_mid(model, aid), levels)
    return out


def colour_by_field(
    model: Any, field: str
) -> tuple[dict[str, tuple[float, float, float]], dict[str, tuple[float, float, float]]]:
    """``(frame_colors, shell_colors)`` RGB maps (0..1) for *field*.

    One palette is built across the frame and shell labels together, so a
    material (or section, or story) always carries the same colour whether it
    appears on a frame or a shell.

    Args:
        model: A ``SAPModelData`` or ``MeshModel``.
        field: ``"section"``, ``"material"`` or ``"story"``.

    Returns:
        ``({frame_elem_id: rgb}, {area_elem_id: rgb})`` — only resolvable
        elements are present; the renderer draws the rest grey.

    Raises:
        ValueError: If *field* is not one of :data:`COLOUR_FIELDS`.
    """
    if field not in FIELD_LABELS:
        raise ValueError(f"unknown colour-by field {field!r} (choices: {COLOUR_FIELDS})")
    frame_labels_ = frame_labels(model, field)
    shell_labels_ = shell_labels(model, field)
    palette = categorical_palette(
        {label for label in (*frame_labels_.values(), *shell_labels_.values()) if label}
    )
    frame_colors = {eid: palette[label] for eid, label in frame_labels_.items() if label}
    shell_colors = {aid: palette[label] for aid, label in shell_labels_.items() if label}
    return frame_colors, shell_colors
