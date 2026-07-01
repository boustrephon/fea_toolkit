"""Abstract interface and data types for ModelViewer render backends."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import numpy as np


# ── Intermediate geometry representations ──────────────────────────────

@dataclass
class FrameGeom:
    """A single frame/beam/column element ready for rendering."""
    elem_id: str
    section: str
    node_i: str
    node_j: str
    start: np.ndarray          # shape (3,) — global coordinates
    end: np.ndarray            # shape (3,) — global coordinates


@dataclass
class ShellGeom:
    """A single shell/area element ready for rendering."""
    area_id: str
    section: str
    vertices: np.ndarray       # shape (N, 3) — polygon vertices in order


@dataclass
class NodeGeom:
    """A single node ready for rendering."""
    node_id: str
    position: np.ndarray       # shape (3,)


@dataclass
class HighlightDef:
    """A set of elements or nodes to highlight.

    The ``frames``, ``shells``, and ``nodes`` fields carry resolved
    geometry so the backend can render them directly without needing
    to look up data from the model.
    """
    frame_ids: List[str] = field(default_factory=list)
    area_ids: List[str] = field(default_factory=list)
    node_ids: List[str] = field(default_factory=list)
    color: Tuple[float, float, float] = (1.0, 0.0, 0.0)   # RGB 0..1
    label: Optional[str] = None
    radius: Optional[float] = None      # tube radius for frames
    # Resolved geometry payloads (populated by ModelViewer)
    frames: List['FrameGeom'] = field(default_factory=list)
    shells: List['ShellGeom'] = field(default_factory=list)
    nodes: List['NodeGeom'] = field(default_factory=list)


@dataclass
class AnnotationDef:
    """A text annotation attached to a position."""
    text: str
    position: np.ndarray               # shape (3,)
    color: Tuple[float, float, float] = (1.0, 1.0, 0.0)  # RGB 0..1
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
        frames: List[FrameGeom],
        colors: Dict[str, Tuple[float, float, float]],
        opacity: float = 1.0,
    ) -> None:
        """Draw frame elements as lines or tubes.

        Args:
            frames: List of frame geometries.
            colors: ``{section_name: (r, g, b)}`` — RGB in 0..1 range.
            opacity: Opacity (0 = transparent, 1 = opaque).
        """
        ...

    @abstractmethod
    def render_shells(
        self,
        shells: List[ShellGeom],
        colors: Dict[str, Tuple[float, float, float]],
        opacity: float = 1.0,
    ) -> None:
        """Draw shell elements as planar surfaces.

        Args:
            shells: List of shell geometries.
            colors: ``{section_name: (r, g, b)}`` — RGB in 0..1 range.
            opacity: Opacity (0 = transparent, 1 = opaque).
        """
        ...

    @abstractmethod
    def render_nodes(
        self,
        nodes: List[NodeGeom],
        color: Tuple[float, float, float] = (0.3, 0.3, 0.3),
        radius: float = 0.02,
    ) -> None:
        """Draw node markers.

        Args:
            nodes: List of node geometries.
            color: Marker colour, RGB in 0..1 range.
            radius: Marker size relative to model scale.
        """
        ...

    @abstractmethod
    def render_highlights(
        self,
        highlights: List[HighlightDef],
    ) -> None:
        """Draw highlighted elements/nodes on top of the model.

        Args:
            highlights: List of highlight definitions.
        """
        ...

    @abstractmethod
    def render_annotations(
        self,
        annotations: List[AnnotationDef],
    ) -> None:
        """Draw text annotations in 3D space.

        Args:
            annotations: List of annotation definitions.
        """
        ...

    @abstractmethod
    def render_deformed(
        self,
        frames: List[FrameGeom],
        displacements: Dict[str, np.ndarray],   # node_id → (dx, dy, dz)
        scale: float = 1.0,
        color: Tuple[float, float, float] = (0.3, 0.6, 1.0),
    ) -> None:
        """Draw deformed frame elements.

        Args:
            frames: List of frame geometries (undeformed).
            displacements: ``{node_id: (dx, dy, dz)}``.
            scale: Amplification factor.
            color: Deformed shape colour, RGB in 0..1 range.
        """
        ...

    @abstractmethod
    def render_force_flags(
        self,
        frames: List[FrameGeom],
        forces: Dict[str, Tuple[float, float]],   # elem_id → (val_i, val_j)
        quantity: str = "Mz",
        scale_factor: float = 1.0,
    ) -> None:
        """Draw force/moment flag diagrams.

        Args:
            frames: List of frame geometries.
            forces: ``{elem_id: (value_at_i, value_at_j)}``.
            quantity: Display name (e.g. ``'Mz'``, ``'Fx'``).
            scale_factor: Scaling for flag size.
        """
        ...

    @abstractmethod
    def clear(self) -> None:
        """Remove all rendered geometry from the scene."""
        ...

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
