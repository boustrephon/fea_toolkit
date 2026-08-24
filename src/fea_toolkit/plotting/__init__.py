"""Visualisation helpers for fea_toolkit models and results.

Modules
-------
viz — Re-export facade over the viewer modules (kept for import stability).
viz_common — Shared low-level helpers (isometric view, colour, legends, timers).
viz_model — Model/mesh/deformed/modal/building/comparison viewers.
viz_pushover — Pushover hinge/damage/envelope/animation/capacity-curve plots.
viz_forces — 3D force-diagram renderer and legacy 3D entry points.
force_diagram — Unified 2D/RS force-diagram dispatcher (:func:`plot_force_diagram`).
viewer — Backend-agnostic :class:`ModelViewer`.
renderers — Render-backend abstraction (PyVista backend).
diagnostics — Connectivity diagnostics.
interactive_viewer — Browser/interactive viewer.
report — Storey force/displacement plots.
"""

from .diagnostics import (
    find_disconnected_nodes,
    plot_disconnected_nodes,
    print_disconnect_report,
)
from .force_diagram import plot_force_diagram
from .interactive_viewer import plot_interactive_viewer
from .report import plot_storey_forces
from .viewer import ModelViewer
from .viz import (
    animate_pushover_deformation,
    compare_meshes,
    plot_building_views,
    plot_capacity_spectrum,
    # Deformed shape (unified replacement for the legacy static/RS viewers)
    plot_deformed_displacement_3d,
    plot_frame_force_evolution,
    # Unified functions (builder or NPZ data)
    plot_mesh,
    plot_mode_animation,
    plot_model_comparison,
    # Pushover visualisation (Phases 4a–4e)
    plot_plastic_hinge_formation,
    plot_plastic_hinge_heatmap,
    plot_pushover_curve,
    plot_pushover_curve_enhanced,
    plot_pushover_envelope,
    plot_shell_damage_map,
)

__all__ = [
    # Viewer
    "ModelViewer",
    "animate_pushover_deformation",
    "compare_meshes",
    # Diagnostics
    "find_disconnected_nodes",
    "plot_building_views",
    "plot_capacity_spectrum",
    "plot_deformed_displacement_3d",
    "plot_disconnected_nodes",
    "plot_force_diagram",
    "plot_frame_force_evolution",
    # Interactive
    "plot_interactive_viewer",
    "plot_mesh",
    "plot_mode_animation",
    # Viz
    "plot_model_comparison",
    # Pushover visualisation (Phases 4a–4e)
    "plot_plastic_hinge_formation",
    "plot_plastic_hinge_heatmap",
    "plot_pushover_curve",
    "plot_pushover_curve_enhanced",
    "plot_pushover_envelope",
    "plot_shell_damage_map",
    # Reports
    "plot_storey_forces",
    "print_disconnect_report",
]
