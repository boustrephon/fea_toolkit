"""Published benchmark models (SAC 3-story, …).

This subpackage imports the OpenSees package (via
:mod:`fea_toolkit.benchmarks.sac`), so it is deliberately **not** re-exported
from the top-level ``fea_toolkit`` package — import it explicitly when a
benchmark model is needed (tests, the figure generator, a viewer example).
"""

from .sac import (
    BEAM,
    COLUMN,
    COMPOSITE_I_FACTOR,
    SAC3_TCL,
    SAC3_UNITS,
    apply_composite_action,
    build_builder,
    builder_config,
    enrich_nonlinear,
    load_model,
    peak_x,
    preprocess_config,
)

__all__ = [
    "BEAM",
    "COLUMN",
    "COMPOSITE_I_FACTOR",
    "SAC3_TCL",
    "SAC3_UNITS",
    "apply_composite_action",
    "build_builder",
    "builder_config",
    "enrich_nonlinear",
    "load_model",
    "peak_x",
    "preprocess_config",
]
