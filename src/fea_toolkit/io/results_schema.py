"""
Unified NPZ results schema — defines the array layout for all analysis results.

Aligns with opstool's ODB (NetCDF) dimension naming so NPZ ↔ ODB conversion
is a direct rename.  See ``docs/results_schema.md`` for the full specification.
"""

from typing import Dict, List, Optional, Set, Tuple
import numpy as np


# ── Required array names per result type ──────────────────────────────────

GEOMETRY_ARRAYS: Dict[str, Tuple] = {
    "node_tag": ("N_node", "int"),
    "node_sap_id": ("N_node", "str"),
    "node_x": ("N_node", "float"),
    "node_y": ("N_node", "float"),
    "node_z": ("N_node", "float"),
    "frame_eid": ("N_frame", "int"),
    "frame_sap_id": ("N_frame", "str"),
    "frame_sec_name": ("N_frame", "str"),
    "frame_node_i": ("N_frame", "int"),
    "frame_node_j": ("N_frame", "int"),
    "shell_eid": ("N_shell", "int"),
    "shell_sap_id": ("N_shell", "str"),
    "shell_sec_name": ("N_shell", "str"),
    "shell_node_1": ("N_shell", "int"),
    "shell_node_2": ("N_shell", "int"),
    "shell_node_3": ("N_shell", "int"),
    "shell_node_4": ("N_shell", "int"),
}

STATIC_ARRAYS = [
    "node_dx", "node_dy", "node_dz",
    "fx_i", "fy_i", "fz_i", "mx_i", "my_i", "mz_i",
    "fx_j", "fy_j", "fz_j", "mx_j", "my_j", "mz_j",
    "fx_i_local", "fy_i_local", "fz_i_local",
    "mx_i_local", "my_i_local", "mz_i_local",
    "fx_j_local", "fy_j_local", "fz_j_local",
    "mx_j_local", "my_j_local", "mz_j_local",
]

MODAL_ARRAYS: Dict[str, Tuple] = {
    "modal/period": ("N_mode", "float"),
    "modal/frequency": ("N_mode", "float"),
    "modal/omega": ("N_mode", "float"),
    "modal/mx_ratio": ("N_mode", "float"),
    "modal/my_ratio": ("N_mode", "float"),
    "modal/mz_ratio": ("N_mode", "float"),
    "modal/mx_eff": ("N_mode", "float"),
    "modal/my_eff": ("N_mode", "float"),
    "modal/mz_eff": ("N_mode", "float"),
    "modal/mode_dx": ("N_node N_mode", "float"),
    "modal/mode_dy": ("N_node N_mode", "float"),
    "modal/mode_dz": ("N_node N_mode", "float"),
}

RS_ARRAYS: Dict[str, Tuple] = {
    "rs/period": ("N_mode", "float"),
    "rs/sa_x": ("N_mode", "float"),
    "rs/sa_y": ("N_mode", "float"),
    "rs/eff_mass_x": ("N_mode", "float"),
    "rs/eff_mass_y": ("N_mode", "float"),
    "rs/v_base_x": ("N_mode", "float"),
    "rs/v_base_y": ("N_mode", "float"),
    "rs/v_cqc_x": ("", "float"),
    "rs/v_cqc_y": ("", "float"),
    "rs/v_total_x": ("", "float"),
    "rs/v_total_y": ("", "float"),
}

META_ARRAYS: Dict[str, Tuple] = {
    "force_unit": ("", "str"),
    "length_unit": ("", "str"),
    "created": ("", "str"),
    "analysis_types": ("N_analysis", "str"),
}


def make_static_key(case_name: str, array_name: str) -> str:
    """Build the NPZ key for a static result array, e.g. ``static/DEAD/fx_i``."""
    return f"static/{case_name}/{array_name}"


def validate_npz(path: str) -> List[str]:
    """Validate an NPZ file against the schema.

    Returns a list of missing or malformed array names (empty = valid).
    """
    errors: List[str] = []
    try:
        data = np.load(path, allow_pickle=True)
    except Exception as exc:
        return [f"Cannot load: {exc}"]

    # Check geometry
    for key, (shape_desc, _dtype) in GEOMETRY_ARRAYS.items():
        if key not in data:
            errors.append(f"Missing geometry array: {key}")

    # Check analysis types
    analysis_types = data.get("analysis_types")
    if analysis_types is not None:
        types = list(analysis_types)
        if "static" in types:
            case_labels = data.get("static_case_labels")
            if case_labels is not None:
                for case in case_labels:
                    for arr in STATIC_ARRAYS:
                        key = make_static_key(str(case), arr)
                        if key not in data:
                            errors.append(f"Missing static array: {key}")
        if "modal" in types:
            for key in MODAL_ARRAYS:
                if key not in data:
                    errors.append(f"Missing modal array: {key}")
        if "rs" in types:
            for key in RS_ARRAYS:
                if key not in data:
                    errors.append(f"Missing RS array: {key}")

    data.close()
    return errors
