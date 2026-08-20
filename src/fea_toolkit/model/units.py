"""Unit-system conversion for :class:`MeshModel` objects.

The OpenSees ``limitCurve`` commands (Elwood shear/axial limit states — see
``docs/shear_failure_modelling.md``, Phase 3) embed imperial units in their
empirical constants: forces in **kip**, lengths in **in**, ``f'c`` in **psi**.
The ``LimitState`` material built on top of them must therefore run inside a
**kip-in-ksi** OpenSees domain (forces kip, lengths in, stresses ksi, with the
shear curve's ``fc`` passed in psi at emission time).

:func:`convert_mesh_units` rescales a deep copy of a ``MeshModel`` from its
current ``units`` dict to a target (force, length) system — most commonly
``{"L": "in", "F": "kip"}`` for a limit-state analysis of an SI model.  All
length, area, section-property, stress, mass-density and load quantities are
rescaled via the unit factors in :mod:`fea_toolkit.utils`; dimensionless
quantities (strains, ratios, multipliers, parametric positions) are left
untouched.  The conversion is designed to be **round-trip exact** (converting
N-m -> kip-in -> N-m reproduces the original model), which
``tests/test_mesh_units.py`` verifies.

Coverage
--------
* Nodes and orphan nodes (coordinates).
* Sections — geometry (depth, width, cover, bar/tie diameters and spacing,
  plate/web/flange thicknesses) and derived properties (``A`` x L2,
  ``I33``/``I22``/``J`` x L4, ``Z33``/``Z22`` x L3, ``tie_fy`` x stress).
* Materials — stress-valued fields (``E_mod``, ``G_mod``, ``Fy``, ``Fu``,
  ``Fc``, ``eFc``, ``eff_Fy``, ``eff_Fu``), ``unit_weight`` (F/L3) and
  ``unit_mass`` (M/L3).  Stress-strain curve parameters are strains or
  dimensionless ratios and are left unchanged.
* Area elements (thickness), wall elements (fibre ``thick``/``width``,
  mass-density ``rho``/``Density``).
* Loads — frame distributed loads (intensity F/L and absolute distances L),
  area uniform loads (F/L2), joint loads (forces F, moments F.L).  Gravity
  load multipliers are dimensionless and unchanged.
* Mesh metadata — ``base_z``, ``diaphragm_levels``,
  ``diaphragm_z_tolerance``, diaphragm-component elevations, and
  ``hinge_params`` entries by dimension: hinge-length keys (``lpI``/``lpJ``)
  scale by L, hinge-moment keys (``My``/``Mc``/``Mp``, ``_pos``/``_neg``
  variants) by F·L, and rotation/ratio/other keys are unit-independent and
  preserved as-is.

Out of scope (left as-is, a ``UserWarning`` is emitted when present)
--------------------------------------------------------------------
* ``nd_materials`` (FSAM nD materials for layered shells) and
  ``layered_shell_sections`` — their stress fields need a dedicated scaling
  pass; wall FSAM fibre materials are named references and stay valid.
"""

from __future__ import annotations

import warnings
from copy import deepcopy
from dataclasses import fields as dc_fields
from typing import Any

from ..utils import (
    force_scale_factor,
    length_scale_factor,
    mass_density_scale_factor,
    stress_scale_factor,
)
from .mesh_model import MeshModel

KIP_IN_UNITS: dict[str, str] = {"L": "in", "F": "kip"}

# ── Section / geometry length-named fields (x L) ────────────────────
_LENGTH_FIELDS: frozenset[str] = frozenset(
    {
        "depth",
        "bf",
        "cover",
        "diameter",
        "thickness",
        "tf",
        "tw",
        "t",
        "b",
        "h",
        "d",
        "tie_diameter",
        "tie_spacing",
        "top_bar_dia",
        "bot_bar_dia",
        "bar_dia",
        "web_thickness",
        "flange_thickness",
        "clear_cover",
    }
)
# ── Stress-valued fields (x stress) ─────────────────────────────────
_STRESS_FIELDS: frozenset[str] = frozenset(
    {
        "E_mod",
        "G_mod",
        "Fy",
        "Fu",
        "Fc",
        "eFc",
        "eff_Fy",
        "eff_Fu",
        "tie_fy",
        "fyt",
        "fy",
    }
)
# ── Hinge length-named keys (x L) ──────────────────────────────────
# ``FrameElementProperties.hinge_params`` entries that carry a physical
# length (plastic hinge lengths at the I/J ends).
_HINGE_LENGTH_KEYS: frozenset[str] = frozenset(
    {
        "lpI",
        "lpJ",
    }
)
# ── Hinge moment-named keys (x F·L) ────────────────────────────────
# Yield/capping/plastic-moment backbone values, expressed in the model's
# force·length unit system (e.g. N·m for an N-m model).  Directional
# ModIMK-style variants (``_pos``/``_neg``) are included.
_HINGE_MOMENT_KEYS: frozenset[str] = frozenset(
    {
        "My",
        "Mc",
        "My_pos",
        "My_neg",
        "Mc_pos",
        "Mc_neg",
        "Mp",
    }
)


# ═════════════════════════════════════════════════════════════════════
# Scaling primitives
# ═════════════════════════════════════════════════════════════════════


def unit_multipliers(
    from_units: dict[str, str],
    to_units: dict[str, str],
) -> dict[str, float]:
    """Target/source multipliers for the common quantity kinds.

    Each entry converts a value *from* ``from_units`` *to* ``to_units`` by
    multiplication (``value_target = value_source * factor``).

    Returns:
        ``{"length", "force", "stress", "force_per_length",
        "force_times_length", "force_per_area", "mass_density",
        "force_per_length_cubed"}`` factors.
    """
    lsf = length_scale_factor(to_units) / length_scale_factor(from_units)
    fsf = force_scale_factor(to_units) / force_scale_factor(from_units)
    ssf = stress_scale_factor(to_units) / stress_scale_factor(from_units)
    mdsf = mass_density_scale_factor(to_units) / mass_density_scale_factor(from_units)
    return {
        "length": lsf,
        "force": fsf,
        "stress": ssf,
        "force_per_length": fsf / lsf,
        "force_times_length": fsf * lsf,
        "force_per_area": fsf / lsf**2,
        "force_per_length_cubed": fsf / lsf**3,
        "mass_density": mdsf,
    }


def _scale_named_fields(obj: Any, fields_to_factors: dict[str, float]) -> None:
    """Scale numeric dataclass fields by a name -> factor map (in place)."""
    names = set(fields_to_factors)
    for f in dc_fields(obj):
        if f.name not in names:
            continue
        value = getattr(obj, f.name)
        if isinstance(value, (int, float)):
            setattr(obj, f.name, value * fields_to_factors[f.name])


def _hinge_param_multiplier(key: str, m: dict[str, float]) -> float:
    """Unit multiplier for a single ``hinge_params`` entry (1.0 = unchanged).

    Length keys (``lpI``/``lpJ``) rescale by ``m["length"]``; moment keys
    (``My``/``Mc``/``Mp`` and ``_pos``/``_neg`` variants) rescale by
    ``m["force_times_length"]``; every other key (plastic rotations,
    ratios, …) is unit-independent and returns ``1.0``.
    """
    if key in _HINGE_LENGTH_KEYS:
        return m["length"]
    if key in _HINGE_MOMENT_KEYS:
        return m["force_times_length"]
    return 1.0


def _scale_section(sec: Any, m: dict[str, float]) -> None:
    """Rescale a Section subclass in place."""
    _scale_named_fields(sec, dict.fromkeys(_LENGTH_FIELDS, m["length"]))
    _scale_named_fields(sec, dict.fromkeys(_STRESS_FIELDS, m["stress"]))
    # Derived section properties (length^n), present on the Section base.
    for name, factor in (
        ("A", m["length"] ** 2),
        ("I33", m["length"] ** 4),
        ("I22", m["length"] ** 4),
        ("J", m["length"] ** 4),
        ("Z33", m["length"] ** 3),
        ("Z22", m["length"] ** 3),
    ):
        value = getattr(sec, name, None)
        if isinstance(value, (int, float)):
            setattr(sec, name, value * factor)


def _scale_material(mat: Any, m: dict[str, float]) -> None:
    """Rescale a Material in place (stress fields, weights, mass)."""
    _scale_named_fields(mat, dict.fromkeys(_STRESS_FIELDS, m["stress"]))
    if isinstance(mat.unit_weight, (int, float)) and mat.unit_weight:
        mat.unit_weight *= m["force_per_length_cubed"]
    if isinstance(mat.unit_mass, (int, float)) and mat.unit_mass:
        mat.unit_mass *= m["mass_density"]
    # ss_curve parameters are strains / dimensionless ratios — untouched.


def _scale_loads(mesh: MeshModel, m: dict[str, float]) -> None:
    """Rescale the load lists in place."""
    for ld in mesh.frame_dist_loads:
        # 'Moment' distributed loads are moment-per-length (a force), so
        # they rescale by the force factor; force loads by force/length.
        _val_scale = (
            m["force"]
            if str(getattr(ld, "load_type", "")).lower() == "moment"
            else m["force_per_length"]
        )
        ld.val_a *= _val_scale
        ld.val_b *= _val_scale
        ld.dist_a *= m["length"]
        ld.dist_b *= m["length"]
    for ld in mesh.edge_loads_from_areas:  # list[FrameDistributedLoad]
        _val_scale = (
            m["force"]
            if str(getattr(ld, "load_type", "")).lower() == "moment"
            else m["force_per_length"]
        )
        ld.val_a *= _val_scale
        ld.val_b *= _val_scale
        ld.dist_a *= m["length"]
        ld.dist_b *= m["length"]
    for ld in mesh.joint_loads:
        ld.fx *= m["force"]
        ld.fy *= m["force"]
        ld.fz *= m["force"]
        ld.mx *= m["force_times_length"]
        ld.my *= m["force_times_length"]
        ld.mz *= m["force_times_length"]
    for ld in mesh.area_uniform_loads:
        ld.value *= m["force_per_area"]
    # Gravity load / area-gravity multipliers are dimensionless — unchanged.


# ═════════════════════════════════════════════════════════════════════
# Public API
# ═════════════════════════════════════════════════════════════════════


def convert_mesh_units(
    mesh: MeshModel,
    target_units: dict[str, str],
) -> MeshModel:
    """Return a deep copy of *mesh* rescaled to *target_units*.

    The source unit system is read from ``mesh.units`` (force/length basis
    — time is always seconds, so only ``L`` and ``F`` drive the scaling).
    The returned model has ``units`` equal to ``target_units`` merged over
    the source units (source-only keys such as ``"T"`` are preserved) and
    every supported quantity rescaled accordingly; the input model is
    untouched.

    Args:
        mesh: Model to convert (not mutated).
        target_units: Target system, e.g. ``{"L": "in", "F": "kip"}``
            (see :data:`KIP_IN_UNITS`) or ``{"L": "m", "F": "N"}``.

    Returns:
        A rescaled deep copy.

    Raises:
        ValueError: If ``target_units`` lacks a length or force unit.

    Warns:
        UserWarning: If unsupported (non-converted) data is present —
            ``nd_materials`` (FSAM nD materials) and ``layered_shell_sections``
            / ``area_element_properties.layer_stack`` layers are not fully
            rescaled and may be wrong when the target units differ from the
            source.
    """
    if not target_units.get("L") or not target_units.get("F"):
        raise ValueError(
            f"convert_mesh_units requires target_units with 'L' and 'F' keys, got {target_units!r}"
        )
    out = deepcopy(mesh)
    m = unit_multipliers(mesh.units, target_units)

    # ── Nodes ────────────────────────────────────────────────────
    for node in out.nodes.values():
        node.x *= m["length"]
        node.y *= m["length"]
        node.z *= m["length"]
    for node in out.orphan_nodes.values():
        node.x *= m["length"]
        node.y *= m["length"]
        node.z *= m["length"]

    # ── Sections / materials ─────────────────────────────────────
    for sec in out.sections.values():
        _scale_section(sec, m)
    for mat in out.materials.values():
        _scale_material(mat, m)

    # ── Area / wall elements ─────────────────────────────────────
    for ae in out.area_elements.values():
        ae.thickness *= m["length"]
    for wall in out.wall_elements.values():
        wall.thick = [t * m["length"] for t in wall.thick]
        wall.width = [t * m["length"] for t in wall.width]
        if wall.rho:
            wall.rho = [r * m["mass_density"] for r in wall.rho]
        if isinstance(wall.Density, (int, float)) and wall.Density:
            wall.Density *= m["mass_density"]

    # ── Loads ────────────────────────────────────────────────────
    _scale_loads(out, m)

    # ── Mesh metadata (length-scaled) ────────────────────────────
    if out.base_z is not None:
        out.base_z *= m["length"]
    out.diaphragm_levels = [z * m["length"] for z in out.diaphragm_levels]
    out.diaphragm_z_tolerance *= m["length"]
    out.diaphragm_components = [(z * m["length"], ids) for z, ids in out.diaphragm_components]
    for props in out.frame_element_properties.values():
        if props.hinge_params:
            props.hinge_params = {
                k: v * _hinge_param_multiplier(k, m) if isinstance(v, (int, float)) else v
                for k, v in props.hinge_params.items()
            }

    # ── Area element properties (shell section overrides) ────────
    for props in out.area_element_properties.values():
        _thick = getattr(props, "thickness", None)
        if isinstance(_thick, (int, float)):
            props.thickness = _thick * m["length"]
        for _layer in getattr(props, "layer_stack", None) or []:
            _lt = getattr(_layer, "thickness", None)
            if isinstance(_lt, (int, float)):
                _layer.thickness = _lt * m["length"]

    # ── Unsupported surfaces ─────────────────────────────────────
    if out.nd_materials:
        warnings.warn(
            "convert_mesh_units: 'nd_materials' (FSAM nD materials) are not "
            "rescaled — a dedicated pass is required; results may be wrong "
            "if the target units differ from the source.",
            UserWarning,
            stacklevel=2,
        )
    if out.layered_shell_sections or any(
        getattr(p, "layer_stack", None) for p in out.area_element_properties.values()
    ):
        warnings.warn(
            "convert_mesh_units: 'layered_shell_sections' / "
            "'area_element_properties.layer_stack' layers are not fully "
            "rescaled — their layer material strengths need a dedicated "
            "pass (layer thicknesses are length-scaled).",
            UserWarning,
            stacklevel=2,
        )

    out.units = {**out.units, **target_units}
    return out
