"""Published benchmark models (SAC 3-story, FEMA 355C Boston 3-/9-story, …).

This subpackage imports the OpenSees package (via
:mod:`fea_toolkit.benchmarks.sac`), so it is deliberately **not** re-exported
from the top-level ``fea_toolkit`` package — import it explicitly when a
benchmark model is needed (tests, the figure generator, a viewer example).

:mod:`fea_toolkit.benchmarks.sac_boston` is itself OpenSees-free (pure
``SAPModelData`` construction + model-codec export — it imports no ``ops``).
"""

from .sac import (
    BEAM,
    BEAM_FY,
    COLUMN,
    COLUMN_FY,
    SAC3_TCL,
    SAC3_UNITS,
    build_builder,
    builder_config,
    enrich_nonlinear,
    load_model,
    peak_x,
    preprocess_config,
)
from .sac_boston import (
    boston3_2d,
    boston3_3d,
    boston9_2d,
    boston9_3d,
    build_2d,
    build_3d,
    write_snapshots,
)

__all__ = [
    "BEAM",
    "BEAM_FY",
    "COLUMN",
    "COLUMN_FY",
    "SAC3_TCL",
    "SAC3_UNITS",
    "boston3_2d",
    "boston3_3d",
    "boston9_2d",
    "boston9_3d",
    "build_2d",
    "build_3d",
    "build_builder",
    "builder_config",
    "enrich_nonlinear",
    "load_model",
    "peak_x",
    "preprocess_config",
    "write_snapshots",
]
