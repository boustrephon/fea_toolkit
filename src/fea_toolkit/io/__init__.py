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

from ._serial import collect_geometry_arrays
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
from .results_schema import SCHEMA_VERSION, SCHEMA_VERSION_LEGACY, make_static_key, validate_npz
from .s2k_parser import SAP2000Parser
from .stage_reader import (
    flatten_stage,
    get_schema_version,
    read_dictionary_arrays,
    read_metadata,
    read_model_stages,
    read_stage_arrays,
)
from .stage_writer import write_model_stages
from .unified_writer import (
    collect_modal_arrays,
    collect_rs_arrays,
    collect_static_arrays,
    write_results,
)

# ── Lazy re-exports (PEP 562) ─────────────────────────────────────
# ``report`` imports pandas, which is NOT a required dependency (see
# ``pyproject.toml`` core deps) and is absent from Rhino 8's bundled
# CPython.  ``import fea_toolkit.io`` must work without pandas, so the
# report helper names are resolved lazily here instead of eagerly.
# ``from fea_toolkit.io import bounding_box`` and
# ``fea_toolkit.io.bounding_box`` work exactly as before.
_REPORT_NAMES = frozenset(
    {
        "area_section_summary",
        "bounding_box",
        "format_linear_table",
        "load_pattern_totals",
        "material_summary",
        "modal_participation_df",
        "modal_table",
        "modal_table_enhanced",
        "section_summary",
        "summarise_load_cases",
        "summarise_load_patterns",
        "summarise_mass_sources",
    }
)


def __getattr__(name: str):
    """PEP 562 lazy resolution for the pandas-dependent report API."""
    if name in _REPORT_NAMES:
        from . import report

        return getattr(report, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(_REPORT_NAMES))


__all__ = [
    "SCHEMA_VERSION",
    "SCHEMA_VERSION_LEGACY",
    "SAP2000Parser",
    "area_section_summary",
    "baseline_correct",
    "bounding_box",
    "collect_geometry_arrays",
    "collect_modal_arrays",
    "collect_rs_arrays",
    "collect_static_arrays",
    "flatten_stage",
    "format_linear_table",
    "get_schema_version",
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
    "read_dictionary_arrays",
    "read_metadata",
    "read_model_stages",
    "read_peer_record",
    "read_results",
    "read_results_npz",
    "read_stage_arrays",
    "read_time_history_csv",
    "record_summary",
    "scale_to_pga",
    "scale_to_target_sa",
    "section_summary",
    "summarise_load_cases",
    "summarise_load_patterns",
    "summarise_mass_sources",
    "validate_npz",
    "write_model_stages",
    "write_results",
    "write_results_npz",
]
