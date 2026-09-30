"""One module per verb — the implementation half of the workflow vocabulary.

Each module owns both its implementation (``run_*``) and its parameter set
(``*_PARAMS``), so the two cannot drift apart: a parameter the implementation
reads but does not declare does not exist as far as a recipe is concerned.
:mod:`fea_toolkit.workflow.registry` aggregates them into ``STEP_SPECS`` — the
single manifest a caller (the GUI's recipe panel, a validator, the verb
listing) needs to read.

Every module keeps its heavy imports inside the function body, so importing
this package — and therefore the manifest — does not load OpenSees.  That is
what lets the GUI render a verb's parameter form before a model is open.
"""

from .chart import CHART_PARAMS, run_chart
from .checks import (
    BRACE_BUCKLING_PARAMS,
    CONNECTIVITY_PARAMS,
    HINGE_LENGTH_PARAMS,
    MEMBER_SHEAR_CAPACITY_PARAMS,
    MESH_CHECKS_PARAMS,
    SELF_WEIGHT_PARAMS,
    WALL_SHEAR_CHECK_PARAMS,
    run_check_brace_buckling,
    run_check_connectivity,
    run_check_self_weight,
    run_hinge_length,
    run_member_shear_capacity,
    run_mesh_checks,
    run_wall_shear_check,
)
from .combine import COMBINE_PARAMS, run_combine
from .mesh import MESH_PARAMS, run_mesh
from .run_static import RUN_STATIC_PARAMS, run_static
from .scale_sections import SCALE_SECTIONS_PARAMS, run_scale_sections

__all__ = [
    "BRACE_BUCKLING_PARAMS",
    "CHART_PARAMS",
    "COMBINE_PARAMS",
    "CONNECTIVITY_PARAMS",
    "HINGE_LENGTH_PARAMS",
    "MEMBER_SHEAR_CAPACITY_PARAMS",
    "MESH_CHECKS_PARAMS",
    "MESH_PARAMS",
    "RUN_STATIC_PARAMS",
    "SCALE_SECTIONS_PARAMS",
    "SELF_WEIGHT_PARAMS",
    "WALL_SHEAR_CHECK_PARAMS",
    "run_chart",
    "run_check_brace_buckling",
    "run_check_connectivity",
    "run_check_self_weight",
    "run_combine",
    "run_hinge_length",
    "run_member_shear_capacity",
    "run_mesh",
    "run_mesh_checks",
    "run_scale_sections",
    "run_static",
    "run_wall_shear_check",
]
