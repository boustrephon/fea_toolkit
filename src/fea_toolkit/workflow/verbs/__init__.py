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

from .build_model import BUILD_MODEL_PARAMS, BUILDER_TITLES, run_build_model
from .chart import CHART_PARAMS, run_chart
from .check_hysteretic import HYSTERETIC_CHECK_PARAMS, run_check_hysteretic_backbone
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
from .close import CLOSE_PARAMS, run_close
from .combine import COMBINE_PARAMS, run_combine
from .fix_shell_only_base import FIX_SHELL_ONLY_BASE_PARAMS, run_fix_shell_only_base
from .ida import IDA_PARAMS, run_ida
from .isolate_base import ISOLATE_BASE_PARAMS, run_isolate_base
from .load_verification import LOAD_VERIFICATION_PARAMS, run_load_verification
from .mesh import MESH_PARAMS, run_mesh
from .modal import MODAL_PARAMS, run_modal
from .nonlinear_dynamic import NLD_PARAMS, run_nonlinear_dynamic
from .open import OPEN_PARAMS, run_open
from .open_results import OPEN_RESULTS_PARAMS, run_open_results
from .pushover import PUSHOVER_PARAMS, run_pushover
from .report import REPORT_PARAMS, run_report
from .response_spectrum import RS_PARAMS, run_response_spectrum
from .run_static import RUN_STATIC_PARAMS, run_static
from .save import SAVE_PARAMS, run_save
from .save_model import SAVE_MODEL_PARAMS, run_save_model
from .save_results import SAVE_RESULTS_PARAMS, run_save_results
from .scale_sections import SCALE_SECTIONS_PARAMS, run_scale_sections
from .select import SELECT_PARAMS, run_select

__all__ = [
    "BRACE_BUCKLING_PARAMS",
    "BUILDER_TITLES",
    "BUILD_MODEL_PARAMS",
    "CHART_PARAMS",
    "CLOSE_PARAMS",
    "COMBINE_PARAMS",
    "CONNECTIVITY_PARAMS",
    "FIX_SHELL_ONLY_BASE_PARAMS",
    "HINGE_LENGTH_PARAMS",
    "HYSTERETIC_CHECK_PARAMS",
    "IDA_PARAMS",
    "ISOLATE_BASE_PARAMS",
    "LOAD_VERIFICATION_PARAMS",
    "MEMBER_SHEAR_CAPACITY_PARAMS",
    "MESH_CHECKS_PARAMS",
    "MESH_PARAMS",
    "MODAL_PARAMS",
    "NLD_PARAMS",
    "OPEN_PARAMS",
    "OPEN_RESULTS_PARAMS",
    "PUSHOVER_PARAMS",
    "REPORT_PARAMS",
    "RS_PARAMS",
    "RUN_STATIC_PARAMS",
    "SAVE_MODEL_PARAMS",
    "SAVE_PARAMS",
    "SAVE_RESULTS_PARAMS",
    "SCALE_SECTIONS_PARAMS",
    "SELECT_PARAMS",
    "SELF_WEIGHT_PARAMS",
    "WALL_SHEAR_CHECK_PARAMS",
    "run_build_model",
    "run_chart",
    "run_check_brace_buckling",
    "run_check_connectivity",
    "run_check_hysteretic_backbone",
    "run_check_self_weight",
    "run_close",
    "run_combine",
    "run_fix_shell_only_base",
    "run_hinge_length",
    "run_ida",
    "run_isolate_base",
    "run_load_verification",
    "run_member_shear_capacity",
    "run_mesh",
    "run_mesh_checks",
    "run_modal",
    "run_nonlinear_dynamic",
    "run_open",
    "run_open_results",
    "run_pushover",
    "run_report",
    "run_response_spectrum",
    "run_save",
    "run_save_model",
    "run_save_results",
    "run_scale_sections",
    "run_select",
    "run_static",
    "run_wall_shear_check",
]
