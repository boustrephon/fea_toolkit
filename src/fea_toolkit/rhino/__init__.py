"""Rhino 3-D visualisation module for ``fea_toolkit``."""

from .colour_from_npz import (
    colour_frame_by_npz_ratio,
    colour_from_npz,
    create_all_result_flags,
    create_result_flags,
    mark_unconnected_edges,
)
from .importer import RhinoImporter
from .results import (
    apply_results,
    colour_frames_from_results,
    colour_shells_from_results,
    create_deformed_geometry,
)

__all__ = [
    "RhinoImporter",
    "apply_results",
    "colour_frame_by_npz_ratio",
    "colour_frames_from_results",
    "colour_from_npz",
    "colour_shells_from_results",
    "create_all_result_flags",
    "create_deformed_geometry",
    "create_result_flags",
    "mark_unconnected_edges",
]
