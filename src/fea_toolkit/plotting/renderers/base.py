"""Abstract interface and data types for ModelViewer render backends."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

import numpy as np

if TYPE_CHECKING:
    from ...model.load_glyphs import LoadGeom

# ── Intermediate geometry representations ──────────────────────────────


@dataclass
class FrameGeom:
    """A single frame/beam/column element ready for rendering."""

    elem_id: str
    section: str
    node_i: str
    node_j: str
    start: np.ndarray  # shape (3,) — global coordinates
    end: np.ndarray  # shape (3,) — global coordinates
    angle: float = 0.0  # SAP2000 section rotation about local x-axis (degrees)


@dataclass
class ShellGeom:
    """A single shell/area element ready for rendering."""

    area_id: str
    section: str
    vertices: np.ndarray  # shape (N, 3) — polygon vertices in order
    node_ids: tuple[str, ...] = ()  # SAP node IDs of the vertices, in order


@dataclass
class NodeGeom:
    """A single node ready for rendering."""

    node_id: str
    position: np.ndarray  # shape (3,)


def polygon_face_count(n_vertices: int) -> int:
    """How many rendered faces an *n_vertices* area element becomes.

    One for a triangle or a **quad** — a quad is drawn as a quad, not fanned into
    two triangles — and ``n - 2`` for a 5+ sided element, which has no quad to
    preserve.  Fewer than three vertices contributes nothing.

    The single source of truth for that rule.  ``PyVistaRenderer.render_shells``
    builds this many faces per element, and the GUI's ``SelectionIndex`` walks the
    same counts to turn a picked *cell* back into an element: if the two ever
    disagree, clicking a slab resolves to the wrong element — or to none at all,
    which is silently indistinguishable from "nothing happened"
    (``tests/test_gui_selection_index.py`` pins the pair).
    """
    if n_vertices < 3:
        return 0
    return 1 if n_vertices <= 4 else n_vertices - 2


@dataclass
class RestraintGeom:
    """One node's support restraint, ready for rendering.

    A restraint is the six DOF flags [U1, U2, U3, R1, R2, R3], ``1`` = fixed —
    the model's own encoding, carried through unchanged so the renderer and the
    Inspector describe the same set.
    """

    node_id: str
    position: np.ndarray  # shape (3,)
    dofs: tuple  # [U1, U2, U3, R1, R2, R3], 1 = fixed


@dataclass
class HighlightDef:
    """A set of elements or nodes to highlight.

    The ``frames``, ``shells``, and ``nodes`` fields carry resolved
    geometry so the backend can render them directly without needing
    to look up data from the model.
    """

    frame_ids: list[str] = field(default_factory=list)
    area_ids: list[str] = field(default_factory=list)
    node_ids: list[str] = field(default_factory=list)
    color: tuple[float, float, float] = (1.0, 0.0, 0.0)  # RGB 0..1
    label: Optional[str] = None
    radius: Optional[float] = None  # tube radius for frames
    # Resolved geometry payloads (populated by ModelViewer)
    frames: list["FrameGeom"] = field(default_factory=list)
    shells: list["ShellGeom"] = field(default_factory=list)
    nodes: list["NodeGeom"] = field(default_factory=list)


@dataclass
class AnnotationDef:
    """A text annotation attached to a position."""

    text: str
    position: np.ndarray  # shape (3,)
    color: tuple[float, float, float] = (1.0, 1.0, 0.0)  # RGB 0..1
    font_size: int = 14


# ── Abstract backend ──────────────────────────────────────────────────


class RenderBackend(ABC):
    """Abstract interface for a 3D render backend.

    Subclasses implement each method for their target environment
    (PyVista, Rhino, gmsh, etc.).
    """

    @abstractmethod
    def render_frames(
        self,
        frames: list[FrameGeom],
        colors: dict[str, tuple[float, float, float]],
        opacity: float = 1.0,
        shrink: float = 1.0,
    ) -> None:
        """Draw frame elements as lines or tubes.

        Args:
            frames: List of frame geometries.
            colors: ``{section_name: (r, g, b)}`` — RGB in 0..1 range.
            opacity: Opacity (0 = transparent, 1 = opaque).
            shrink: Display shrink factor toward each element's midpoint —
                ``1.0`` draws true length, ``0.9`` leaves a gap at every joint
                (SAP2000's *shrink elements*).  Purely a display transform: the
                geometry passed in is never modified.
        """
        ...

    @abstractmethod
    def render_extruded_frames(
        self,
        frames: list[FrameGeom],
        sections: dict,
        colors: dict[str, tuple[float, float, float]],
        opacity: float = 1.0,
        shrink: float = 1.0,
        detail: str = "plates",
    ) -> None:
        """Draw frame elements as extruded solids (their cross-section swept
        along each member axis).

        Args:
            frames: List of frame geometries.
            sections: ``{section_name: Section}`` — the model's sections.
            colors: ``{section_name: (r, g, b)}`` — RGB in 0..1 range.
            opacity: Opacity (0 = transparent, 1 = opaque).
            shrink: Display shrink factor toward each element's midpoint.
            detail: ``"plates"`` (the efficient open-surface representation) or
                ``"solid"`` (the closed perimeter, with end caps).
        """
        ...

    @abstractmethod
    def render_shells(
        self,
        shells: list[ShellGeom],
        colors: dict[str, tuple[float, float, float]],
        opacity: float = 1.0,
        shrink: float = 1.0,
        scalar_field: Optional[dict[str, float]] = None,
        scalar_bar_title: Optional[str] = None,
    ) -> None:
        """Draw shell elements as planar surfaces.

        Args:
            shells: List of shell geometries.
            colors: ``{section_name: (r, g, b)}`` — RGB in 0..1 range, used
                when *scalar_field* is ``None``.
            opacity: Opacity (0 = transparent, 1 = opaque).
            shrink: Display shrink factor toward each element's centroid, so a
                joint or a node can be seen between the elements (SAP2000's
                *shrink elements*).  Display only — the caller's geometry is
                never modified.
            scalar_field: Optional ``{area_id: value}`` that recolours the
                faces on a diverging per-face scale (positive → warm, negative
                → cool) instead of the section palette; a shell without a value
                is drawn grey.  This recolours the shells in place — no
                separate overlay is added, so the colour-fill hides and shows
                with the shells themselves.
            scalar_bar_title: When set (and *scalar_field* has a non-zero
                value), add a scalar bar mapping the colour scale back to the
                value — used by the applied-load colour-fill.
        """
        ...

    @abstractmethod
    def render_nodes(
        self,
        nodes: list[NodeGeom],
        color: tuple[float, float, float] = (0.3, 0.3, 0.3),
        radius: float = 0.02,
        pickable: bool = True,
    ) -> None:
        """Draw node markers.

        Args:
            nodes: List of node geometries.
            color: Marker colour, RGB in 0..1 range.
            radius: Marker size relative to model scale.
            pickable: Whether a click may land on these markers.  The **main**
                node cloud is pickable — a pick reports an index into the picked
                actor's own point list, which is what the selection index maps back
                to a node id — so any *additional* marker drawn over the same nodes
                (a support marker, say) must pass ``False``, or it would shift that
                index away from the cloud the picker is expected to hit.
        """
        ...

    @abstractmethod
    def render_restraints(
        self,
        restraints: list[RestraintGeom],
        size: float = 1.0,
        color: tuple[float, float, float] = (0.1, 0.4, 0.6),
        style: str = "block",
    ) -> None:
        """Draw support symbols at restrained nodes.

        One glyph per restrained DOF — by default a plate for a translation and
        a disc for a rotation — so **any** restraint set is drawn, not only the
        textbook ones (:mod:`fea_toolkit.plotting.restraint_glyphs`).

        Args:
            restraints: The restrained nodes to draw.
            size: Glyph length in **model units** — the caller scales it to the
                model, as it does the highlight radius.
            color: Glyph colour, RGB in 0..1 range.
            style: ``"block"`` (plates and discs) or ``"arrow"`` (arrows and
                curls, the earlier representation).
        """
        ...

    @abstractmethod
    def render_highlights(
        self,
        highlights: list[HighlightDef],
        shrink: float = 1.0,
    ) -> None:
        """Draw highlighted elements/nodes on top of the model.

        Args:
            highlights: List of highlight definitions.
            shrink: The same display shrink the model was drawn with.  A
                highlight must match what it points at, so a shrunk display
                needs a shrunk highlight — otherwise the highlight sticks out
                past the element it marks.
        """
        ...

    @abstractmethod
    def render_annotations(
        self,
        annotations: list[AnnotationDef],
    ) -> None:
        """Draw text annotations in 3D space.

        Args:
            annotations: List of annotation definitions.
        """
        ...

    @abstractmethod
    def render_deformed(
        self,
        frames: list[FrameGeom],
        displacements: dict[str, np.ndarray],  # node_id → (dx, dy, dz)
        scale: float = 1.0,
        color: tuple[float, float, float] = (0.3, 0.6, 1.0),
        shells: Optional[list[ShellGeom]] = None,
    ) -> None:
        """Draw deformed frame elements and shell faces.

        Args:
            frames: List of frame geometries (undeformed).
            displacements: ``{node_id: (dx, dy, dz)}``.
            scale: Amplification factor.
            color: Deformed shape colour, RGB in 0..1 range.
            shells: List of shell geometries (undeformed).  Each shell's
                vertices are displaced by the node they belong to
                (:attr:`ShellGeom.node_ids`); a shell without per-vertex node
                ids is drawn undisplaced.
        """
        ...

    @abstractmethod
    def render_force_flags(
        self,
        frames: list[FrameGeom],
        forces: dict[str, tuple[float, float]],  # elem_id → (val_i, val_j)
        quantity: str = "Mz",
        scale_factor: float = 1.0,
    ) -> None:
        """Draw force/moment flag diagrams.

        The flag plane is the member's local transverse axis, so ``forces``
        must carry **local** force/moment components.

        Args:
            frames: List of frame geometries.
            forces: ``{elem_id: (value_at_i, value_at_j)}`` — local force/
                moment components.
            quantity: Local component name (e.g. ``'Mz'``, ``'Fx'``).  Also
                selects the flag extrusion direction.
            scale_factor: Scaling for flag size.
        """
        ...

    @abstractmethod
    def render_loads(
        self,
        loads: list["LoadGeom"],
        size: float = 1.0,
        area_style: str = "arrows",
        arrows_per_shell: int = 9,
    ) -> None:
        """Draw applied-load glyphs — flags for line loads, arrows for points.

        Glyphs are resolved by
        :func:`fea_toolkit.model.load_glyphs.extract_load_glyphs`; magnitudes
        are in model units and are scaled here for display only.  Each glyph
        carries its ``owner_kind`` / ``owner_id`` (the element or node it acts
        on), so a backend can group glyphs under their host element's category
        and tie their visibility to that element.

        Args:
            loads: Load glyphs (``kind`` ``"point"``, ``"line"`` or
                ``"area"``).
            size: Target glyph length in model units.  The peak value of each
                kind — a line-load *intensity*, a point-load *force* and an
                area *pressure* are all different units — is drawn at this
                length, so every kind stays legible on one model.
            area_style: How an ``"area"`` glyph is drawn — ``"arrows"`` (a grid
                of arrows over the face, the default) or ``"extrusion"`` (a
                one-sided prism along the pressure direction).
            arrows_per_shell: Number of arrows drawn over each area face when
                ``area_style == "arrows"``.
        """
        ...

    @abstractmethod
    def render_shell_scalar_field(
        self,
        shells: list[ShellGeom],
        values: dict[str, float],  # area_id → scalar
    ) -> None:
        """Draw a per-shell scalar field as a colour overlay on area elements.

        The shell counterpart of :meth:`render_force_flags`: each shell face is
        coloured by its resultant on the same diverging scale (positive → warm,
        negative → cool, saturated by ``|value| / max_abs``); a shell the
        archive does not cover is left grey.

        Args:
            shells: List of shell geometries (undeformed).
            values: ``{area_id: scalar}`` — one value per shell to colour.
        """
        ...

    @abstractmethod
    def clear(self) -> None:
        """Remove all rendered geometry from the scene."""
        ...

    def clear_highlights(self) -> None:
        """Remove the highlights drawn by :meth:`render_highlights`.

        Selection highlighting is redrawn on every tree click, so a backend
        that appends highlight actors must be able to drop the previous set
        without rebuilding the scene.  Backends whose highlights are
        transient overlays -- removed with :meth:`clear` and never
        accumulated -- inherit this no-op; :class:`PyVistaRenderer` overrides
        it and removes the actors it added.
        """
        return None

    @abstractmethod
    def show(self) -> None:
        """Display the interactive view."""
        ...

    @abstractmethod
    def screenshot(self, path: str) -> None:
        """Save a screenshot to disk.

        Args:
            path: Output path (e.g. ``'view.png'``).
        """
        ...

    @abstractmethod
    def export_html(self, path: str) -> None:
        """Export the scene to a standalone interactive HTML file.

        Args:
            path: Output path (e.g. ``'view.html'``).
        """
        ...
