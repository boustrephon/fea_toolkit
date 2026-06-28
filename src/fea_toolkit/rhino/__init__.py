"""Rhino 3-D visualisation module for ``fea_toolkit``."""

from .importer import RhinoImporter

# RhinoImporterV2 requires Rhino at import time, so it's lazy-loaded.
# Import explicitly:
#   from fea_toolkit.rhino import RhinoImporterV2

__all__ = ["RhinoImporter"]

from .importer import RhinoImporter

__all__ = ["RhinoImporter"]

