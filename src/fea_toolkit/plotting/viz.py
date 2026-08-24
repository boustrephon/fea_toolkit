"""Visualisation helpers for fea_toolkit models and results.

Two backends are supported:

* **PyVista** — interactive 3D model view and deformed shape.
* **Matplotlib** — 2D force / moment diagrams along element height.

All functions gracefully fall back to a warning if the required package
is not installed.

The implementation lives in :mod:`fea_toolkit.plotting.viz_common`,
:mod:`fea_toolkit.plotting.viz_model`,
:mod:`fea_toolkit.plotting.viz_pushover` and
:mod:`fea_toolkit.plotting.viz_forces`; this module re-exports every name
so ``from fea_toolkit.plotting.viz import ...`` keeps working.
"""

from .viz_common import (
    _DEFAULT_HINGE_CMAP,
    _NPZ_TYPES,
    _add_animation_timer,
    _add_hinge_color_legend,
    _add_shell_color_legend,
    _ratio_to_color,
    _rgb_to_hex,
    _sample_cmap,
    _set_isometric_view,
)
from .viz_forces import (
    _add_coloured_poly,
    _add_coloured_tube,
    _compute_flag_direction,
    _compute_local_forces,
    _extract_npz_frame_forces,
    _get_element_axis,
    _load_npz_for_plotting,
    _render_frame_force_diagram,
    _resolve_npz_static_case,
)
from .viz_model import (
    _add_meshed_geometry,
    _add_original_geometry,
    _build_deformed_mesh,
    _collapse_to_parents,
    _render_scene,
    _resolve_frame_node,
    _resolve_mesh_data,
    _resolve_shell_node,
    _run_interactive_viewer,
    _save_comparison_images,
    _sort_children_by_location,
    compare_meshes,
    plot_building_views,
    plot_deformed_displacement_3d,
    plot_mesh,
    plot_mode_animation,
    plot_model_comparison,
)
from .viz_pushover import (
    _compute_hinge_ratios,
    _compute_hinge_ratios_all_steps,
    _compute_shell_damage,
    _ratio_to_shell_color,
    _resolve_pushover_data,
    _resolve_shell_data,
    animate_pushover_deformation,
    plot_capacity_spectrum,
    plot_frame_force_evolution,
    plot_plastic_hinge_formation,
    plot_plastic_hinge_heatmap,
    plot_pushover_curve,
    plot_pushover_curve_enhanced,
    plot_pushover_envelope,
    plot_shell_damage_map,
)

__all__ = [
    "_DEFAULT_HINGE_CMAP",
    "_NPZ_TYPES",
    "_add_animation_timer",
    "_add_coloured_poly",
    "_add_coloured_tube",
    "_add_hinge_color_legend",
    "_add_meshed_geometry",
    "_add_original_geometry",
    "_add_shell_color_legend",
    "_build_deformed_mesh",
    "_collapse_to_parents",
    "_compute_flag_direction",
    "_compute_hinge_ratios",
    "_compute_hinge_ratios_all_steps",
    "_compute_local_forces",
    "_compute_shell_damage",
    "_extract_npz_frame_forces",
    "_get_element_axis",
    "_load_npz_for_plotting",
    "_ratio_to_color",
    "_ratio_to_shell_color",
    "_render_frame_force_diagram",
    "_render_scene",
    "_resolve_frame_node",
    "_resolve_mesh_data",
    "_resolve_npz_static_case",
    "_resolve_pushover_data",
    "_resolve_shell_data",
    "_resolve_shell_node",
    "_rgb_to_hex",
    "_run_interactive_viewer",
    "_sample_cmap",
    "_save_comparison_images",
    "_set_isometric_view",
    "_sort_children_by_location",
    "animate_pushover_deformation",
    "compare_meshes",
    "plot_building_views",
    "plot_capacity_spectrum",
    "plot_deformed_displacement_3d",
    "plot_frame_force_evolution",
    "plot_mesh",
    "plot_mode_animation",
    "plot_model_comparison",
    "plot_plastic_hinge_formation",
    "plot_plastic_hinge_heatmap",
    "plot_pushover_curve",
    "plot_pushover_curve_enhanced",
    "plot_pushover_envelope",
    "plot_shell_damage_map",
]
