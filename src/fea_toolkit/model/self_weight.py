"""Self-weight of a model, or of a selection within it.

Computed purely from geometry and material properties, OpenSees-free, so it can
serve both a whole-model audit (as :func:`check_self_weight_consistency` does in
``checks.py``) and a right-click "what does this selection weigh" read-out in the
GUI.  The math matches the self-weight *application* path in ``opensees/_loads.py``:

* a frame weighs ``section.A × material.unit_weight × length``;
* an area weighs ``polygon_area_3d(vertices) × thickness × unit_weight``.

``unit_weight`` is a **weight** density (force / volume) in the model's own
units, so no ``g`` conversion is involved in reporting weight.  (Mass would be
``weight / g_from_units(units)``, which callers can do if they need it.)
"""

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Union

from .._unit_scaling import force_unit_label
from .geometry_core import polygon_area_3d
from .sap_data import ShellSection

if TYPE_CHECKING:
    from .mesh_model import MeshModel
    from .sap_data import SAPModelData
    from .selection import Selection

__all__ = ["SelectionWeight", "selection_weight"]


@dataclass
class SelectionWeight:
    """Self-weight of a set of frames and areas.

    Attributes:
        frame_weight: Total weight of the included frame elements.
        area_weight: Total weight of the included area (shell) elements.
        frame_count: Number of frame elements actually weighed.
        area_count: Number of area elements actually weighed.
        unit: Canonical force-unit label for the weights (``"kN"``, ``"kip"`` …).
    """

    frame_weight: float = 0.0
    area_weight: float = 0.0
    frame_count: int = 0
    area_count: int = 0
    unit: str = "kN"

    @property
    def total(self) -> float:
        """Frame plus area weight."""
        return self.frame_weight + self.area_weight

    @property
    def count(self) -> int:
        """Number of elements weighed (frames + areas)."""
        return self.frame_count + self.area_count


def selection_weight(
    model: Union["SAPModelData", "MeshModel"],
    selection: Optional["Selection"] = None,
) -> SelectionWeight:
    """Compute the self-weight of *selection* within *model* (the whole model
    when *selection* is ``None``).

    Elements are skipped when they are inactive (split/meshed parents), have no
    section assignment, or their material carries no ``unit_weight`` — the same
    guards ``check_self_weight_consistency`` applies, so the two sums cannot
    disagree about what a member weighs.

    Args:
        model: A ``SAPModelData`` or ``MeshModel`` (both expose ``frame_elements``,
            ``area_elements``, ``nodes``, ``sections``, ``materials`` and the
            ``frame_assignments`` / ``area_assignments`` maps).
        selection: Optional ``Selection`` narrowing the computation; ``None``
            weighs every element.

    Returns:
        A :class:`SelectionWeight` with the totals and the element counts.
    """
    if selection is not None:
        frame_ids = selection.get_frame_ids(model)
        area_ids = selection.get_area_ids(model)
    else:
        frame_ids = list(model.frame_elements)
        area_ids = list(model.area_elements)

    frame_weight = 0.0
    frame_count = 0
    for eid in frame_ids:
        elem = model.frame_elements.get(eid)
        if elem is None or getattr(elem, "inactive", False):
            continue
        sec_name = model.frame_assignments.get(eid)
        sec = model.sections.get(sec_name) if sec_name else None
        mat = model.materials.get(sec.material) if sec is not None else None
        if mat is None or abs(mat.unit_weight) < 1e-12:
            continue
        ni = model.nodes.get(elem.node_i)
        nj = model.nodes.get(elem.node_j)
        if ni is None or nj is None:
            continue
        length = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
        frame_weight += sec.A * mat.unit_weight * length
        frame_count += 1

    area_weight = 0.0
    area_count = 0
    for aid in area_ids:
        area = model.area_elements.get(aid)
        if area is None or getattr(area, "inactive", False):
            continue
        sec_name = model.area_assignments.get(aid)
        sec = model.sections.get(sec_name) if sec_name else None
        if not isinstance(sec, ShellSection):
            continue
        mat = model.materials.get(sec.material)
        if mat is None or abs(mat.unit_weight) < 1e-12:
            continue
        if sec.thickness < 1e-12:
            continue
        # True 3-D polygon area: a shoelace on x/y alone would project a
        # vertical panel to ~0 (see ``checks.check_self_weight_consistency``).
        verts = [
            (model.nodes[nid].x, model.nodes[nid].y, model.nodes[nid].z)
            for nid in area.node_ids
            if nid in model.nodes
        ]
        if len(verts) < 3:
            continue
        area_weight += polygon_area_3d(verts) * sec.thickness * mat.unit_weight
        area_count += 1

    return SelectionWeight(
        frame_weight=frame_weight,
        area_weight=area_weight,
        frame_count=frame_count,
        area_count=area_count,
        unit=force_unit_label(getattr(model, "units", {}) or {}),
    )
