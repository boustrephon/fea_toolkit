"""File I/O: SAP2000 parsing, NPZ serialisation, and report utilities.

The canonical serialised exchange format is NPZ (see :mod:`fea_toolkit.io.npz_writer`
and :mod:`fea_toolkit.io.results_schema`).

Modules
-------
s2k_parser — Parse SAP2000 .S2K / .E2K text files into SAPModelData.
npz_writer — Serialise analysis results to NPZ archives.
npz_reader — Deserialise NPZ archives and convert to PyVista meshes.
results_schema — NPZ key layout and validation.
unified_writer — Combined NPZ/HDF5 writer with geometry + results.
report — pandas-based summary tables (modal, section, load, etc.).
ground_motion — PEER record reading, scaling, baseline correction.
helper — File-chooser dialogs (tkinter / macOS native).
"""

from .s2k_parser import SAP2000Parser

from .npz_writer import write_results_npz

from .npz_reader import (
    read_results_npz,
    read_results,
    npz_to_pyvista_frame_mesh,
    npz_to_pyvista_shell_mesh,
    npz_to_pyvista_modal_mesh,
    npz_to_rhino_colour_data,
    npz_build_id_tag_map,
    npz_build_child_map,
    npz_build_parent_map,
)

from .results_schema import validate_npz, make_static_key

from .unified_writer import (
    write_results,
    collect_geometry_arrays,
    collect_static_arrays,
    collect_modal_arrays,
    collect_rs_arrays,
)

from .ground_motion import (
    read_peer_record,
    read_time_history_csv,
    scale_to_pga,
    scale_to_target_sa,
    baseline_correct,
    record_summary,
)

from .report import (
    bounding_box,
    summarise_mass_sources,
    summarise_load_cases,
    summarise_load_patterns,
    load_pattern_totals,
    material_summary,
    section_summary,
    area_section_summary,
    modal_table,
    modal_table_enhanced,
    modal_participation_df,
    brace_buckling_check,
    format_linear_table,
    static_load_verification,
)

__all__ = [
    # Parser
    "SAP2000Parser",
    # NPZ I/O
    "write_results_npz",
    "read_results_npz",
    "read_results",
    "npz_to_pyvista_frame_mesh",
    "npz_to_pyvista_shell_mesh",
    "npz_to_pyvista_modal_mesh",
    "npz_to_rhino_colour_data",
    "npz_build_id_tag_map",
    "npz_build_child_map",
    "npz_build_parent_map",
    # Schema
    "validate_npz",
    "make_static_key",
    # Unified writer
    "write_results",
    "collect_geometry_arrays",
    "collect_static_arrays",
    "collect_modal_arrays",
    "collect_rs_arrays",
    # Ground motion
    "read_peer_record",
    "read_time_history_csv",
    "scale_to_pga",
    "scale_to_target_sa",
    "baseline_correct",
    "record_summary",
    # Reporting
    "bounding_box",
    "summarise_mass_sources",
    "summarise_load_cases",
    "summarise_load_patterns",
    "load_pattern_totals",
    "material_summary",
    "section_summary",
    "area_section_summary",
    "modal_table",
    "modal_table_enhanced",
    "modal_participation_df",
    "brace_buckling_check",
    "format_linear_table",
    "static_load_verification",
]