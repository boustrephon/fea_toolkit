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
from fea_toolkit.capacity.gb50010 import (
    WallShearCheckResult,
    axial_capacity,
    moment_capacity,
    shear_capacity,
    wall_shear_check,
)

__all__ = [
    "CapacityResult",
    "WallShearCheckResult",
    "axial_capacity",
    "capacity_dcr",
    "force_length_unit_label",
    "force_unit_label",
    "hinge_length",
    "moment_capacity",
    "shear_capacity",
    "wall_shear_check",
]
