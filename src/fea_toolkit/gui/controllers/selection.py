"""Map a viewport pick back to the model entity behind it.

The *forward* half of selection sync (``docs/gui_roadmap.md`` design rule 7): a
click in the 3-D view identifies a *rendered* cell, and this module turns that
into the SAP label of the entity -- ``("frame_elements", "12")`` -- which the
tree can then select and scroll to.

Qt-free on purpose, like :mod:`fea_toolkit.gui.models.model_index`: the mapping
is pure data logic, so it is unit-tested without Qt
(``tests/test_gui_selection_index.py``).

**Why the cell id, and where it comes from.**  PyVista's
``enable_mesh_picking`` hands its callback the picked *actor* (``use_actor=True``)
and nothing else -- verified against pyvista 0.48.1; the cell index lives on the
scene picker (``plotter.iren.picker.GetCellId()``), which ``enable_mesh_picking``
assigns itself.  The actor identifies the *batch* (which category was hit) and
the cell index identifies the element *within* it; because every backend builds
its meshes in geometry order, the lookup is a plain list index.  See
``docs/dev_notes.md`` -> *PyVista picking contract*.
"""

from dataclasses import dataclass, field
from typing import Any, Optional

# The face-counting rule is a fact about the *renderer* — how many faces it builds
# per element — so it is imported rather than restated here, where a stale copy is
# invisible until a click selects the wrong element.
from ...plotting.renderers.base import polygon_face_count

__all__ = ["CATEGORY_GROUPS", "SelectionIndex"]

#: Render category -> the model tree group key holding its entities
#: (keys as defined by ``models/model_index.py``).
CATEGORY_GROUPS = {
    "frames": "frame_elements",
    "shells": "area_elements",
    "nodes": "nodes",
}

#: Render category -> the identity attribute its entities carry.
_LABEL_ATTRS = {
    "frames": "elem_id",
    "shells": "area_id",
    "nodes": "node_id",
}


@dataclass
class SelectionIndex:
    """Forward map: ``(render category, cell index) -> SAP label``.

    Attributes:
        frames: Frame geometries **in render order**.
        shells: Shell geometries in render order.
        nodes: Node geometries in render order.
    """

    frames: list = field(default_factory=list)
    shells: list = field(default_factory=list)
    nodes: list = field(default_factory=list)

    @classmethod
    def from_viewer(cls, viewer: Any) -> "SelectionIndex":
        """Build the index from a displayed ``ModelViewer``.

        Args:
            viewer: A ``ModelViewer``; its geometry is extracted if needed.

        Returns:
            An index matching the batches the backend rendered.
        """
        frames, shells, nodes = viewer.geometry()
        return cls(frames=list(frames), shells=list(shells), nodes=list(nodes))

    # ── Lookup ──────────────────────────────────────────────────────

    def group_key(self, category: str) -> Optional[str]:
        """Tree group key that holds *category*'s entities, or ``None``."""
        return CATEGORY_GROUPS.get(category)

    def label(self, category: str, cell_id: int) -> Optional[str]:
        """SAP label of the entity that was rendered as *cell_id*.

        Args:
            category: ``"frames"``, ``"shells"`` or ``"nodes"``.
            cell_id: Cell index reported by the scene picker.

        Returns:
            The SAP label, or ``None`` when the category is unknown or the
            index falls outside the batch.
        """
        if cell_id is None or cell_id < 0:
            return None
        if category == "shells":
            return self._shell_label(cell_id)
        geometries = {"frames": self.frames, "nodes": self.nodes}.get(category)
        if geometries is None or cell_id >= len(geometries):
            return None
        return self._identify(geometries[cell_id], category)

    # ── Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _identify(entity: Any, category: str) -> Optional[str]:
        """Read *entity*'s SAP label for *category* (``None`` when blank)."""
        attr = _LABEL_ATTRS.get(category)
        if attr is None:
            return None
        return str(getattr(entity, attr, "") or "") or None

    def _shell_label(self, cell_id: int) -> Optional[str]:
        """Resolve a rendered *cell* index to its area element.

        ``PyVistaRenderer.render_shells`` draws each element as its own polygon —
        one face for a triangle or a quad, and a fan of ``n - 2`` for a 5+ sided
        element — so a cell index has to be walked down that cumulative count.
        The count comes from :func:`~fea_toolkit.plotting.renderers.base.polygon_face_count`
        rather than being recomputed here: when the renderer emitted fan triangles
        and this assumed one face per quad, a click on a slab resolved to the wrong
        element (or to none), which looks exactly like the click doing nothing.
        """
        offset = 0
        for shell in self.shells:
            faces = polygon_face_count(len(shell.vertices))
            if cell_id < offset + faces:
                return self._identify(shell, "shells")
            offset += faces
        return None
