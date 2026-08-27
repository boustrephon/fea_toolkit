"""Utility functions for configuration merging and load-pattern inference.

This module is now a **re-export facade**: the implementation has been
split into single-concern modules (see the import list below) so no
single file dominates the utility layer, while ``from fea_toolkit.utils
import ...`` keeps working unchanged.

* :mod:`fea_toolkit._unit_scaling` — unit alias registry, display labels,
  SI-default material properties, and the ``*_scale_factor`` /
  ``*_to_si_factor`` converters plus :func:`scale_material_dict`.
* :mod:`fea_toolkit._loads_infer` — :func:`deep_merge`, load-pattern
  inference (:func:`infer_loads` / :func:`build_gravity_patterns` /
  :func:`pick_wind`).
* :mod:`fea_toolkit._flags` — flag-diagram geometry (pure NumPy,
  no renderer dependency): :func:`compute_flag_parts` and helpers.
* :mod:`fea_toolkit._cqc` — :func:`cqc_combine` (Der Kiureghian kernel)
  and :func:`sum_reactions_with_overturning`.
"""

from ._cqc import cqc_combine, sum_reactions_with_overturning  # noqa: F401
from ._flags import (
    _FLAG_COINCIDE_TOL,  # noqa: F401
    _FLAG_ZERO_SNAP_TOL,  # noqa: F401
    _dedupe_flag_vertices,  # noqa: F401
    _snap_flag_noise,  # noqa: F401
    compute_flag_parts,  # noqa: F401
)
from ._loads_infer import build_gravity_patterns, deep_merge, infer_loads, pick_wind  # noqa: F401
from ._unit_scaling import (
    _FORCE_LABELS,  # noqa: F401
    _G_SI,  # noqa: F401
    _LENGTH_LABELS,  # noqa: F401
    _UNIT_ALIASES,  # noqa: F401
    DEFAULT_E_C_PA,  # noqa: F401
    DEFAULT_E_S_PA,  # noqa: F401
    DEFAULT_EPS_C,  # noqa: F401
    DEFAULT_EPS_CC,  # noqa: F401
    DEFAULT_FC_PA,  # noqa: F401
    DEFAULT_FSAM_CONC_EPCC,  # noqa: F401
    DEFAULT_FSAM_CONC_ET,  # noqa: F401
    DEFAULT_FSAM_CONC_FPC_PA,  # noqa: F401
    DEFAULT_FSAM_CONC_FT_PA,  # noqa: F401
    DEFAULT_FSAM_CONC_RC,  # noqa: F401
    DEFAULT_FSAM_CONC_RT,  # noqa: F401
    DEFAULT_FSAM_CONC_XCRN,  # noqa: F401
    DEFAULT_FSAM_CONC_XCRP,  # noqa: F401
    DEFAULT_FSAM_STEEL_B,  # noqa: F401
    DEFAULT_FSAM_STEEL_CR1,  # noqa: F401
    DEFAULT_FSAM_STEEL_CR2,  # noqa: F401
    DEFAULT_FSAM_STEEL_R0,  # noqa: F401
    DEFAULT_FY_REBAR_PA,  # noqa: F401
    DEFAULT_FY_STEEL_PA,  # noqa: F401
    DEFAULT_G_C_PA,  # noqa: F401
    DEFAULT_G_MOD_FRAC,  # noqa: F401
    DEFAULT_G_S_PA,  # noqa: F401
    DEFAULT_GRAVITY_MS2,  # noqa: F401
    DEFAULT_NU_C,  # noqa: F401
    DEFAULT_NU_S,  # noqa: F401
    DEFAULT_RHO_MC_SI,  # noqa: F401
    DEFAULT_RHO_MS_SI,  # noqa: F401
    DEFAULT_RHO_WC_SI,  # noqa: F401
    DEFAULT_RHO_WS_SI,  # noqa: F401
    RC_NO_TIE_CONFINEMENT_FACTOR,  # noqa: F401
    RC_NO_TIE_EPSC_FACTOR,  # noqa: F401
    _normalise_unit,  # noqa: F401
    force_scale_factor,  # noqa: F401
    force_to_si_factor,  # noqa: F401
    force_unit_label,  # noqa: F401
    g_from_units,  # noqa: F401
    length_scale_factor,  # noqa: F401
    length_to_si_factor,  # noqa: F401
    length_unit_label,  # noqa: F401
    lineal_force_to_si_factor,  # noqa: F401
    mass_density_scale_factor,  # noqa: F401
    mass_density_to_si_factor,  # noqa: F401
    mass_scale_factor,  # noqa: F401
    mass_to_si_factor,  # noqa: F401
    scale_material_dict,  # noqa: F401
    stress_scale_factor,  # noqa: F401
    stress_to_si_factor,  # noqa: F401
    weight_density_scale_factor,  # noqa: F401
    weight_density_to_si_factor,  # noqa: F401
)
