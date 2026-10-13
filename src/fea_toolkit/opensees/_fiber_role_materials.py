"""Shared fiber-role material resolution.

One source of truth for the OpenSees uniaxial material law and its
arguments for each fiber role (``"steel"``, ``"rebar"``, ``"concrete"``,
``"core"``).  The OpenSeesPy domain builder
(:meth:`~fea_toolkit.opensees._sections.SectionMixin._create_fiber_role_material`)
and both Tcl exporters
(:func:`~fea_toolkit.opensees.builder._emit_fiber_role_material_tcl` and
:func:`~fea_toolkit.opensees.recorder._emit_fiber_role_material_tcl`) call
:func:`resolve_fiber_role_material`, so each role resolves to the same law —
including confinement and configured overrides — in every path.
"""

from __future__ import annotations

from ..utils import (
    DEFAULT_E_S_PA,
    DEFAULT_EPS_C,
    DEFAULT_FC_PA,
    DEFAULT_FSAM_CONC_FT_PA,
    DEFAULT_FY_REBAR_PA,
    DEFAULT_FY_STEEL_PA,
    RC_NO_TIE_CONFINEMENT_FACTOR,
    RC_NO_TIE_EPSC_FACTOR,
)


def resolve_concrete_epsc(mat) -> float:
    """Return the concrete peak strain (positive magnitude) for a material.

    ``Material.eFc`` is a *confined compressive strength* (Pa), not a strain,
    so it is never used as the peak strain.  The stress-strain curve's ``s_fc``
    (strain at peak ``fc'``) is used when present and within a plausible range;
    otherwise :data:`DEFAULT_EPS_C` (0.002) applies.
    """
    if mat is not None and mat.ss_curve is not None and mat.ss_curve.s_fc is not None:
        try:
            s_fc = float(mat.ss_curve.s_fc)
        except (TypeError, ValueError):
            s_fc = 0.0
        if 0.0 < s_fc <= 0.01:
            return s_fc
    return DEFAULT_EPS_C


def _concrete_law(
    config: dict, ssf: float, fc: float, epsc0: float, fpcu: float, epsu: float
) -> tuple[str, tuple]:
    """Return the concrete law (``Concrete01`` default, ``Concrete02`` opt-in).

    Both laws share the Kent–Scott–Park compression backbone: peak ``fc`` at
    ``epsc0``, descending to ``fpcu`` at ``epsu``.  ``Concrete02`` adds a
    genuinely descending post-crushing branch plus linear tension softening
    (``ft`` / ``ets``).
    """
    law = config.get("concrete_material", "Concrete01")
    if law == "Concrete02":
        lam = float(config.get("concrete02_lambda", 0.1))
        ft = config.get("concrete02_ft_override")
        ft = DEFAULT_FSAM_CONC_FT_PA * ssf if ft is None else float(ft) * ssf
        ets = config.get("concrete02_Ets_override")
        ets = ft / 0.001 if ets is None else float(ets) * ssf
        return "Concrete02", (-fc, -abs(epsc0), -fpcu, -abs(epsu), lam, ft, ets)
    return "Concrete01", (-fc, -abs(epsc0), -fpcu, -abs(epsu))


def resolve_fiber_role_material(
    role: str,
    sec,
    mat,
    materials: dict,
    config: dict,
    ssf: float,
) -> tuple[str, tuple]:
    """Resolve the ``(law, args)`` pair for one fiber role.

    The role fixes the OpenSees law; :meth:`Section.fiber_role_material`
    (already applied by the caller to produce *mat*) supplies the backing
    SAP2000 material.  Strengths/moduli authored in SI (Pa) fall through the
    shared ``*_PA`` defaults scaled to model units by *ssf*.

    Args:
        role: One of ``"steel"``, ``"rebar"``, ``"concrete"``, ``"core"``.
        sec: The :class:`~fea_toolkit.model.sap_data.Section` (used for
            confinement and tie-data resolution on the ``"core"`` role).
        mat: The resolved backing material (may be ``None``).
        materials: The model's ``{name: Material}`` map, used to resolve the
            tie rebar material for Mander confinement.
        config: Builder config dict (rebar / concrete overrides).
        ssf: Stress scale factor (Pa → model stress units).

    Returns:
        ``(law_name, args)`` where *args* are the positional arguments for the
        ``uniaxialMaterial`` command after the tag.

    Raises:
        ValueError: If *role* is not a recognised fiber role.
    """
    if role == "steel":
        Fy = getattr(mat, "Fy", 0.0) if mat is not None else 0.0
        Fy = Fy or DEFAULT_FY_STEEL_PA * ssf
        E = getattr(mat, "E_mod", 0.0) if mat is not None else 0.0
        E = E or DEFAULT_E_S_PA * ssf
        return "Steel01", (Fy, E, 0.01)

    if role == "rebar":
        # Priority: config override (SI Pa, scaled) → SAP2000 material →
        # framework defaults.
        Fy = config.get("rebar_Fy_override")
        Es = config.get("rebar_Es_override")
        if Fy is not None:
            Fy = Fy * ssf
        if Es is not None:
            Es = Es * ssf
        if (Fy is None or Es is None) and mat is not None:
            rm_Fy = getattr(mat, "Fy", 0.0) or 0.0
            rm_Es = getattr(mat, "E_mod", 0.0) or 0.0
            if Fy is None and rm_Fy > 0:
                Fy = rm_Fy
            if Es is None and rm_Es > 0:
                Es = rm_Es
        if not Fy:
            Fy = DEFAULT_FY_REBAR_PA * ssf
        if not Es:
            Es = DEFAULT_E_S_PA * ssf
        return "Steel02", (
            Fy,
            Es,
            float(config.get("rebar_b", 0.01)),
            float(config.get("rebar_R0", 18.0)),
            float(config.get("rebar_cR1", 0.925)),
            float(config.get("rebar_cR2", 0.15)),
        )

    if role == "concrete":
        Fc = getattr(mat, "Fc", 0.0) if mat is not None else 0.0
        Fc = Fc or DEFAULT_FC_PA * ssf
        epsc = resolve_concrete_epsc(mat)
        fpcu = float(config.get("core_residual_factor", 0.2)) * Fc
        return _concrete_law(config, ssf, Fc, epsc, fpcu, 0.006)

    if role == "core":
        Fc = getattr(mat, "Fc", 0.0) if mat is not None else 0.0
        Fc = Fc or DEFAULT_FC_PA * ssf
        epsc = resolve_concrete_epsc(mat)
        Fc_core = Fc * RC_NO_TIE_CONFINEMENT_FACTOR
        epsc_core = abs(epsc) * RC_NO_TIE_EPSC_FACTOR
        ecu_core = 0.02

        tie_fy = sec.confinement_steel_fy(materials)

        confinement = None
        fc_method = getattr(sec, "fiber_confinement", None)
        if callable(fc_method):
            try:
                confinement = fc_method(Fc, tie_fy)
            except Exception:
                confinement = None
        if confinement is not None:
            Fc_core = confinement.get("fcc", Fc_core)
            epsc_core = confinement.get("ecc", epsc_core)
            ecu_core = confinement.get("ecu", 0.02)

        ecu_max = float(config.get("confined_ecu_max", 0.025))
        ecu_core = max(min(ecu_core, ecu_max), epsc_core)
        fpcu = float(config.get("core_residual_factor", 0.2)) * Fc_core
        return _concrete_law(config, ssf, Fc_core, epsc_core, fpcu, ecu_core)

    raise ValueError(
        f"Unknown fiber material role {role!r} for section {getattr(sec, 'name', '?')!r}"
    )


def fiber_role_material_tcl(
    role: str,
    sec,
    mat,
    materials: dict,
    config: dict,
    ssf: float,
    tag: int,
) -> str:
    """Return the ``uniaxialMaterial`` Tcl line for one fiber role.

    Formats :func:`resolve_fiber_role_material`'s ``(law, args)`` result as
    ``uniaxialMaterial <law> <tag> <args…>``.
    """
    law, args = resolve_fiber_role_material(role, sec, mat, materials, config, ssf)
    return f"uniaxialMaterial {law} {tag} " + " ".join(f"{a:g}" for a in args)
