"""How an element's meshing settings read, for the Inspector.

SAFE exports an *auto-mesh intent* rather than a ready mesh for its floor and
wall objects: a global mesh option plus a maximum element size, with per-area
overrides (``Mesh Option`` / ``Add Restraints``) stored on the model as
``floor_mesh_options`` (``per_area`` for floors, ``wall_per_area`` for walls).
This module is the Qt-free, renderer-free lookup that turns those model facts
into the rows the Inspector appends, exactly as
:mod:`~fea_toolkit.model.supports` does for a node's restraints.
"""

from __future__ import annotations

from typing import Any

from .sap_data import AreaElement, FrameElement

__all__ = ["meshing_rows"]


def _area_meshing_rows(model: Any, area: AreaElement) -> list:
    """Role, per-area mesh option / restraints / size, opening and inactive."""
    rows: list[tuple[str, str]] = []

    role = (getattr(model, "area_roles", None) or {}).get(area.area_id)
    if role:
        rows.append(("Role", role))

    opts = getattr(model, "floor_mesh_options", None) or {}
    if role == "wall":
        per_area = opts.get("wall_per_area", {})
        size = opts.get("wall_max_size") or opts.get("max_size")
    else:
        per_area = opts.get("per_area", {})
        size = opts.get("max_size") or opts.get("wall_max_size")
    # When the role is unknown, surface either override so the setting is not
    # hidden behind a missing role.
    override = per_area.get(area.area_id) if per_area else None
    if override is None:
        override = (opts.get("per_area", {}) or opts.get("wall_per_area", {})).get(area.area_id)

    mesh_option = (override or {}).get("mesh_option")
    if mesh_option:
        rows.append(("Mesh Option", mesh_option))
    if (override or {}).get("add_restraints"):
        rows.append(("Add Restraints", "Yes"))
    if size:
        rows.append(("Mesh Size", f"{float(size):g}"))

    if getattr(area, "opening", False):
        rows.append(("Opening", "Yes"))
    if getattr(area, "inactive", False):
        rows.append(("Meshed Away", "Yes"))

    return rows


def _frame_meshing_rows(model: Any, frame: FrameElement) -> list:
    auto = (getattr(model, "frame_auto_mesh", None) or {}).get(frame.elem_id)
    if not auto:
        return []
    rows: list[tuple[str, str]] = []
    for key, value in auto.items():
        if value:
            rows.append((f"Auto Mesh {key}", _format(value)))
    return rows


def _format(value: Any) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def meshing_rows(model: Any, obj: Any) -> list:
    """``[(label, value), …]`` describing *obj*'s meshing settings.

    An area reports its role (floor/wall), its per-area mesh option and
    add-restraints flag, the governing maximum mesh size, and whether it is an
    opening or has been meshed away (``inactive``).  A frame reports its
    auto-mesh settings.  Rows with nothing to say are omitted.

    Args:
        model: A ``SAPModelData`` or ``MeshModel``, or ``None``.
        obj: The selected :class:`AreaElement` or :class:`FrameElement`.

    Returns:
        One ``(name, value)`` pair per non-empty fact.
    """
    if model is None or obj is None:
        return []
    if isinstance(obj, AreaElement):
        return _area_meshing_rows(model, obj)
    if isinstance(obj, FrameElement):
        return _frame_meshing_rows(model, obj)
    return []
