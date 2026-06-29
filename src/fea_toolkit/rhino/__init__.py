"""Rhino 3-D visualisation module for ``fea_toolkit``."""

# RhinoImporterV2 requires Rhino at import time.  Try v2 first; fall back to v1.
try:
    from .importer_v2 import RhinoImporterV2 as RhinoImporter
except ImportError:
    from .importer import RhinoImporter

from .colour_from_npz import (colour_from_npz, colour_frame_by_npz_ratio,
                               create_result_flags, create_all_result_flags)

__all__ = ["RhinoImporter", "colour_from_npz", "colour_frame_by_npz_ratio",
           "create_result_flags", "create_all_result_flags"]

