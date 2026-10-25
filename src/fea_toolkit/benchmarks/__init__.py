"""Published benchmark models (SAC 3-story, FEMA 355C LA/Seattle/Boston, …).

This subpackage imports the OpenSees package (via
:mod:`fea_toolkit.benchmarks.sac`), so it is deliberately **not** re-exported
from the top-level ``fea_toolkit`` package — import it explicitly when a
benchmark model is needed (tests, the figure generator, a viewer example).

:mod:`fea_toolkit.benchmarks.fema355c` is itself OpenSees-free (pure
``SAPModelData`` construction + model-codec export — it imports no ``ops``).
"""

from .fema355c import (
    build_fema355c,
    write_snapshots,
)
from .fema355c import (
    schedules as fema355c_schedules,
)
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
from .sac_member_data import (
    MAT_TBL_3,
    MAT_TBL_9,
    MAT_TBL_20,
    MAT_TBLS,
    MemberBackbone,
)

__all__ = [
    "BEAM",
    "BEAM_FY",
    "COLUMN",
    "COLUMN_FY",
    "MAT_TBLS",
    "MAT_TBL_3",
    "MAT_TBL_9",
    "MAT_TBL_20",
    "SAC3_TCL",
    "SAC3_UNITS",
    "MemberBackbone",
    "build_builder",
    "build_fema355c",
    "builder_config",
    "enrich_nonlinear",
    "fema355c_schedules",
    "load_model",
    "peak_x",
    "preprocess_config",
    "write_snapshots",
]
