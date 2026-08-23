"""Code-specific member capacity modules.

One module per statutory building code.  Functions inside each code module are
named by structural action (``moment_capacity``, ``shear_capacity``, ...) and
scoped to the code by the module name, so multiple codes can coexist without
name clashes::

    from fea_toolkit.capacity.gb50010 import moment_capacity
    from fea_toolkit.capacity.asce41 import moment_capacity

Available codes
---------------
``gb50010``
    GB 50010-2010 reinforced-concrete member capacity functions
    (flexure, axial, shear, wall checks).

``asce41``
    ASCE 41-17 member capacity functions (plastic hinge length, §10.8).

Unit convention
---------------
Material strengths are authored in SI (Pa) and converted to the model's unit
system with :func:`fea_toolkit.utils.stress_scale_factor`; section dimensions
and demands are in model units.  Capacities are returned in the model's force
(or force × length) units.
"""

from fea_toolkit.capacity._common import (
    CapacityResult,
    capacity_dcr,
    force_length_unit_label,
    force_unit_label,
)
from fea_toolkit.capacity.asce41 import hinge_length
from fea_toolkit.capacity.elwood_limit_state import (
    ElwoodColumnGeometry,
    ElwoodColumnParameters,
    axial_capacity_surface,
    elwood_axial_deg_slope,
    elwood_axial_drift_at_failure,
    elwood_column_geometry,
    elwood_column_parameters,
    elwood_limit_state_envelope,
    elwood_shear_drift_at_failure,
    elwood_shear_limit_force,
    elwood_spring_slopes,
    three_point_axial_surface,
)
from fea_toolkit.capacity.gb50010 import (
    WallShearCheckResult,
    axial_capacity,
    moment_capacity,
    shear_capacity,
    wall_shear_check,
)
from fea_toolkit.capacity.shear_capacity import (
    ShearCapacityResult,
    ShearFailureEntry,
    ShearFailureReport,
    member_shear_capacity,
    report_shear_failure,
    shear_backbone,
)

__all__ = [
    "CapacityResult",
    "ElwoodColumnGeometry",
    "ElwoodColumnParameters",
    "ShearCapacityResult",
    "ShearFailureEntry",
    "ShearFailureReport",
    "WallShearCheckResult",
    "axial_capacity",
    "axial_capacity_surface",
    "capacity_dcr",
    "elwood_axial_deg_slope",
    "elwood_axial_drift_at_failure",
    "elwood_column_geometry",
    "elwood_column_parameters",
    "elwood_limit_state_envelope",
    "elwood_shear_drift_at_failure",
    "elwood_shear_limit_force",
    "elwood_spring_slopes",
    "force_length_unit_label",
    "force_unit_label",
    "hinge_length",
    "member_shear_capacity",
    "moment_capacity",
    "report_shear_failure",
    "shear_backbone",
    "shear_capacity",
    "three_point_axial_surface",
    "wall_shear_check",
]
