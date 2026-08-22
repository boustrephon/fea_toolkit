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

from .ground_motion import (
    baseline_correct,
    read_peer_record,
    read_time_history_csv,
    record_summary,
    scale_to_pga,
    scale_to_target_sa,
)
from .npz_reader import (
    npz_build_child_map,
    npz_build_id_tag_map,
    npz_build_parent_map,
    npz_to_pyvista_frame_mesh,
    npz_to_pyvista_modal_mesh,
    npz_to_pyvista_shell_mesh,
    npz_to_rhino_colour_data,
    read_results,
    read_results_npz,
)
from .npz_writer import write_results_npz
from .report import (
    area_section_summary,
    bounding_box,
    format_linear_table,
    load_pattern_totals,
    material_summary,
    modal_participation_df,
    modal_table,
    modal_table_enhanced,
    section_summary,
    summarise_load_cases,
    summarise_load_patterns,
    summarise_mass_sources,
)
from .results_schema import make_static_key, validate_npz
from .s2k_parser import SAP2000Parser
from .unified_writer import (
    collect_geometry_arrays,
    collect_modal_arrays,
    collect_rs_arrays,
    collect_static_arrays,
    write_results,
)

__all__ = [
    # Parser
    "SAP2000Parser",
    "area_section_summary",
    "baseline_correct",
    # Reporting
    "bounding_box",
    "collect_geometry_arrays",
    "collect_modal_arrays",
    "collect_rs_arrays",
    "collect_static_arrays",
    "format_linear_table",
    "load_pattern_totals",
    "make_static_key",
    "material_summary",
    "modal_participation_df",
    "modal_table",
    "modal_table_enhanced",
    "npz_build_child_map",
    "npz_build_id_tag_map",
    "npz_build_parent_map",
    "npz_to_pyvista_frame_mesh",
    "npz_to_pyvista_modal_mesh",
    "npz_to_pyvista_shell_mesh",
    "npz_to_rhino_colour_data",
    # Ground motion
    "read_peer_record",
    "read_results",
    "read_results_npz",
    "read_time_history_csv",
    "record_summary",
    "scale_to_pga",
    "scale_to_target_sa",
    "section_summary",
    "summarise_load_cases",
    "summarise_load_patterns",
    "summarise_mass_sources",
    # Schema
    "validate_npz",
    # Unified writer
    "write_results",
    # NPZ I/O
    "write_results_npz",
]
