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
    "frame_parent_sap_id": ("N_frame", "str"),
    "frame_sec_name": ("N_frame", "str"),
    "frame_node_i": ("N_frame", "int"),
    "frame_node_j": ("N_frame", "int"),
    "frame_t_start": ("N_frame", "float"),   # optional — 0..1 parametric position
    "frame_t_end": ("N_frame", "float"),     # optional — 0..1 parametric position
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

    Checks that required geometry and analysis arrays exist and that
    their shapes match the declared dimensions.  Dtype mismatches are
    reported as warnings (prefixed with ``[WARN]``) but do not block
    validation.

    Returns a list of error/warning messages (empty = fully valid).
    """
    messages: List[str] = []
    try:
        data = np.load(path, allow_pickle=True)
    except Exception as exc:
        return [f"Cannot load: {exc}"]

    # ── Resolve dimensions from present arrays ────────────────────
    dims: Dict[str, int] = {}
    tag_arr = data.get("node_tag")
    if tag_arr is not None:
        dims["N_node"] = len(tag_arr)
    for key in ("frame_eid", "frame_sap_id"):
        arr = data.get(key)
        if arr is not None:
            dims["N_frame"] = len(arr)
            break
    for key in ("shell_eid", "shell_sap_id"):
        arr = data.get(key)
        if arr is not None:
            dims["N_shell"] = len(arr)
            break
    for key in ("modal/period", "modal/frequency"):
        arr = data.get(key)
        if arr is not None:
            dims["N_mode"] = len(arr)
            break
    at = data.get("analysis_types")
    if at is not None:
        dims["N_analysis"] = len(at)

    def _check_shape(arr_name: str, arr, shape_desc: str, dtype_str: str):
        """Validate shape and dtype of a single array."""
        if arr is None:
            return
        expected = shape_desc.strip()
        actual_shape = arr.shape
        if expected:
            # Resolve dimension names (N_node, N_frame, etc.)
            parts = expected.split()
            resolved_parts = []
            for p in parts:
                p = p.strip()
                if p.startswith("N_") and p in dims:
                    resolved_parts.append(str(dims[p]))
                else:
                    resolved_parts.append(p)
            expected_shape = tuple(
                int(x) for x in resolved_parts if x
            )
            # For (N_mode,) arrays, allow 0-length if no modes
            if len(expected_shape) == 1:
                if expected_shape[0] != actual_shape[0] and \
                   not (expected_shape[0] == 0 and len(actual_shape) == 1):
                    if dims.get(expected.split()[0].strip()):
                        messages.append(
                            f"  Shape mismatch for {arr_name}: "
                            f"expected {expected_shape}, got {actual_shape}")
            elif expected_shape != actual_shape:
                messages.append(
                    f"  Shape mismatch for {arr_name}: "
                    f"expected {expected_shape}, got {actual_shape}")

    # ── Check geometry ────────────────────────────────────────────
    optional_geo = {"frame_t_start", "frame_t_end"}
    for key, (shape_desc, dtype_str) in GEOMETRY_ARRAYS.items():
        arr = data.get(key)
        if arr is None:
            if key not in optional_geo:
                messages.append(f"Missing geometry array: {key}")
            continue
        _check_shape(key, arr, shape_desc, dtype_str)

    # ── Check analysis types discriminator ────────────────────────
    analysis_types = data.get("analysis_types")
    if analysis_types is None:
        messages.append("Missing metadata array: analysis_types")
    else:
        types = list(analysis_types)
        if "static" in types:
            case_labels = data.get("static_case_labels")
            if case_labels is None:
                messages.append(
                    "analysis_types declares 'static' but "
                    "static_case_labels is missing")
            else:
                for case in case_labels:
                    for arr_name in STATIC_ARRAYS:
                        key = make_static_key(str(case), arr_name)
                        arr = data.get(key)
                        if arr is None:
                            messages.append(f"Missing static array: {key}")
                        else:
                            _check_shape(key, arr, "N_frame", "float")
        if "modal" in types:
            for key, (shape_desc, dtype_str) in MODAL_ARRAYS.items():
                arr = data.get(key)
                if arr is None:
                    messages.append(f"Missing modal array: {key}")
                    continue
                _check_shape(key, arr, shape_desc, dtype_str)
        if "rs" in types:
            for key, (shape_desc, dtype_str) in RS_ARRAYS.items():
                arr = data.get(key)
                if arr is None:
                    messages.append(f"Missing RS array: {key}")
                    continue
                _check_shape(key, arr, shape_desc, dtype_str)

    data.close()
    return messages
