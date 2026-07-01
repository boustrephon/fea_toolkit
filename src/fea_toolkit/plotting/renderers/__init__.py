"""Render backend for ModelViewer.

Each backend implements :class:`RenderBackend` — the abstract interface
that :class:`~fea_toolkit.plotting.viewer.ModelViewer` delegates to.

Available backends
------------------
* ``pyvista`` — interactive 3D via PyVista (macOS, Windows, Linux).
* ``rhino`` — Rhino document objects (Windows, requires Rhino).
"""

from .base import (
    RenderBackend, FrameGeom, ShellGeom, NodeGeom,
    HighlightDef, AnnotationDef,
)

__all__ = [
    "RenderBackend", "FrameGeom", "ShellGeom", "NodeGeom",
    "HighlightDef", "AnnotationDef",
]
