"""Visualisation helpers for fea_toolkit models and results."""

from .diagnostics import (
    find_disconnected_nodes,
    plot_disconnected_nodes,
    print_disconnect_report,
)
from .interactive_viewer import plot_interactive_viewer
from .report import plot_storey_forces
from .viewer import ModelViewer
from .viz import (
    animate_pushover_deformation,
    compare_meshes,
    plot_building_views,
    plot_capacity_spectrum,
    plot_deformed_3d,
    # Deformed shape (unified replacement for plot_deformed_3d / plot_rs_deformed_3d)
    plot_deformed_displacement_3d,
    plot_force_diagram,
    plot_force_diagram_3d,
    plot_frame_force_evolution,
    # Unified functions (builder or NPZ data)
    plot_mesh,
    plot_mode_3d,
    plot_mode_animation,
    plot_model_3d,
    plot_model_comparison,
    plot_npz_force_diagram,
    plot_npz_moment_3d,
    # Pushover visualisation (Phases 4a–4e)
    plot_plastic_hinge_formation,
    plot_plastic_hinge_heatmap,
    plot_pushover_curve,
    plot_pushover_curve_enhanced,
    plot_pushover_envelope,
    plot_rs_deformed_3d,
    plot_shell_damage_map,
    plot_static_axial_3d,
    plot_static_force_diagram,
    plot_static_moment_3d,
    plot_static_shear_3d,
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
    "plot_deformed_3d",
    "plot_deformed_displacement_3d",
    "plot_disconnected_nodes",
    "plot_force_diagram",
    "plot_force_diagram_3d",
    "plot_frame_force_evolution",
    # Interactive
    "plot_interactive_viewer",
    "plot_mesh",
    "plot_mode_3d",
    "plot_mode_animation",
    # Viz
    "plot_model_3d",
    "plot_model_comparison",
    "plot_npz_force_diagram",
    "plot_npz_moment_3d",
    # Pushover visualisation (Phases 4a–4e)
    "plot_plastic_hinge_formation",
    "plot_plastic_hinge_heatmap",
    "plot_pushover_curve",
    "plot_pushover_curve_enhanced",
    "plot_pushover_envelope",
    "plot_rs_deformed_3d",
    "plot_shell_damage_map",
    "plot_static_axial_3d",
    "plot_static_force_diagram",
    "plot_static_moment_3d",
    "plot_static_shear_3d",
    # Reports
    "plot_storey_forces",
    "print_disconnect_report",
]
