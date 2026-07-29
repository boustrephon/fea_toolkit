"""Visualisation helpers for fea_toolkit models and results."""

from .viewer import ModelViewer

from .viz import (
    _build_deformed_mesh,
    plot_model_3d,
    plot_deformed_3d,
    plot_rs_deformed_3d,
    plot_mode_3d,
    plot_static_moment_3d,
    plot_static_shear_3d,
    plot_static_axial_3d,
    plot_static_force_diagram,
    plot_force_diagram,
    plot_pushover_curve,
    plot_pushover_curve_enhanced,
    plot_capacity_spectrum,
    plot_npz_force_diagram,
    plot_npz_moment_3d,
    # Deformed shape (unified replacement for plot_deformed_3d / plot_rs_deformed_3d)
    plot_deformed_displacement_3d,
    # Unified functions (builder or NPZ data)
    plot_mesh,
    compare_meshes,
    plot_mode_animation,
    plot_force_diagram_3d,
    plot_building_views,
    plot_model_comparison,
)

from .diagnostics import (
    find_disconnected_nodes,
    print_disconnect_report,
    plot_disconnected_nodes,
)

from .interactive_viewer import plot_interactive_viewer

from .report import plot_storey_forces


__all__ = [
    # Viewer
    "ModelViewer",
    # Viz
    "plot_model_3d",
    "plot_deformed_3d",
    "plot_rs_deformed_3d",
    "plot_mode_3d",
    "plot_static_moment_3d",
    "plot_static_shear_3d",
    "plot_static_axial_3d",
    "plot_static_force_diagram",
    "plot_force_diagram",
    "plot_pushover_curve",
    "plot_pushover_curve_enhanced",
    "plot_capacity_spectrum",
    "plot_npz_force_diagram",
    "plot_npz_moment_3d",
    "plot_deformed_displacement_3d",
    "plot_mesh",
    "compare_meshes",
    "plot_mode_animation",
    "plot_force_diagram_3d",
    "plot_building_views",
    "plot_model_comparison",
    # Diagnostics
    "find_disconnected_nodes",
    "print_disconnect_report",
    "plot_disconnected_nodes",
    # Interactive
    "plot_interactive_viewer",
    # Reports
    "plot_storey_forces",
]
