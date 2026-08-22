"""Simplified MCFT shear capacity, shear backbones, and the mode-of-failure reporter.

This module is the shared physics behind the toolkit's two shear-modelling
layers (see ``docs/shear_failure_modelling.md``):

1. **Capacity model** (:func:`member_shear_capacity`) — the simplified
   Modified Compression Field Theory (Bentz, Vecchio & Collins 2006; the
   CSA A23.3-04 General Method, Clause 11, with a fixed :math:`\\theta`).
   The concrete contribution :math:`V_c = \\beta\\sqrt{f'_c} b_w d_v` uses a
   strain-dependent :math:`\\beta`, so axial compression raises the capacity
   and tension lowers it.  All math is in **model units** (stress × length²
   → force), so it is unit-agnostic like the rest of the toolkit.

2. **Nonlinear shear backbone** (:func:`shear_backbone`) — a trilinear
   force–shear-strain relationship (cracking → peak → degrading → residual)
   used to replace the plain elastic ``GA_v`` in the ``SectionAggregator``
   when ``aggregate_shear = "nonlinear"``.

3. **Mode-of-failure reporter** (:func:`report_shear_failure`) — consumes a
   pushover result that includes per-step element forces and returns the
   shear demand-to-capacity ratio per member and the **failure sequence**,
   mirroring the sub-element capacity philosophy of Kotsovos & Zygouris
   (2019).

References
----------
* Vecchio, F.J. & Emara, M.B. (1992). *ACI Structural Journal* 89(1) 46–56.
* Bentz, E.C., Vecchio, F.J. & Collins, M.P. (2006). *ACI Structural
  Journal* 103(1) 50–59 (simplified MCFT).
* CSA A23.3-04, Clause 11 — General Method for shear design.
* Kotsovos, G.M. & Zygouris, N.S. (2019). *Magazine of Concrete Research*
  71(3) 109–125.
* Guner, S. (2008). PhD thesis, University of Toronto (§4.8 — Duong frame
  validation; beam shear failures at 48 mm and 68 mm).
"""

from dataclasses import dataclass
from typing import Any, Optional

import numpy as np

from ..model.sap_data import Section
from ..utils import force_scale_factor, length_scale_factor, stress_scale_factor

# ═══════════════════════════════════════════════════════════════
# Defaults (mirrored in analysis_builder.py config defaults)
# ═══════════════════════════════════════════════════════════════

DEFAULT_AGGREGATE_SIZE_MM = 19.0
DEFAULT_SHEAR_THETA_DEG = 35.0
DEFAULT_TIE_LEG_COUNT = 2
DEFAULT_SHEAR_AREA_FACTOR = 5.0 / 6.0
DEFAULT_POST_CRACK_STIFFNESS_RATIO = 0.03  # K_after_crack = ratio · GA_v
DEFAULT_POST_PEAK_STIFFNESS_RATIO = -0.05  # K_degrade = ratio · GA_v (< 0)
DEFAULT_SHEAR_RESIDUAL_RATIO = 0.20  # V_r = ratio · V_n


# ── Dataclasses ────────────────────────────────────────────────


@dataclass
class ShearGeometry:
    """Section shear geometry in model units (L / L² / force)."""

    bw: float = 0.0  # web width (flexural plane dimension)
    h: float = 0.0  # gross depth
    d: float = 0.0  # effective depth to longitudinal steel
    dv: float = 0.0  # effective shear depth (0.9·d, min 0.72·h)
    av: float = 0.0  # transverse-reinforcement area crossing the shear plane
    s: float = 0.0  # stirrup spacing
    asl: float = 0.0  # longitudinal steel area on the flexural tension side


@dataclass
class ShearCapacityResult:
    """Nominal shear capacity (model force units)."""

    vc: float  # concrete contribution
    vs: float  # transverse-reinforcement contribution
    vn: float  # nominal capacity = min(vc + vs, vn_upper)
    vn_upper: float  # 0.25·f'c·b_w·d_v cap
    vcr: float  # diagonal-cracking shear 0.33·sqrt(f'c)·b_w·d_v
    beta: float
    epsilon_x: float
    dv: float
    d: float
    bw: float
    s_ze_mm: float  # equivalent crack-spacing parameter (mm)


@dataclass
class ShearFailureEntry:
    """A shear-capacity exceedance at one member/step."""

    elem_id: str
    step: int
    control_disp: Optional[float]
    demand: float
    capacity: float
    dcr: float
    axial: float  # compression positive


@dataclass
class ShearFailureReport:
    """Result of :func:`report_shear_failure`."""

    entries: list[ShearFailureEntry]  # exceedances in chronological order
    max_dcr: dict[str, float]  # elem_id → peak demand/capacity ratio
    members_checked: list[str]
    governing_elem: Optional[str] = None
    governing_step: Optional[int] = None
    governing_dcr: float = 0.0


# ═══════════════════════════════════════════════════════════════
# Capacity model
# ═══════════════════════════════════════════════════════════════


def section_shear_geometry(
    section: Section,
    tie_legs: int = DEFAULT_TIE_LEG_COUNT,
) -> ShearGeometry:
    """Derive the shear geometry of a frame section in model units.

    Uses ``bf`` for the web width, ``depth``/``cover`` for the effective
    depth, and the ``tie_*`` / ``top_bars``-type fields for the transverse
    and longitudinal reinforcement.  Missing transverse data yields
    ``av = s = 0`` (no stirrup contribution); missing bars yield
    ``asl = 0`` (strain term disabled).
    """
    bw = float(getattr(section, "bf", 0.0) or 0.0) or float(getattr(section, "b", 0.0) or 0.0)
    h = float(getattr(section, "depth", 0.0) or 0.0) or float(getattr(section, "h", 0.0) or 0.0)
    cover = float(getattr(section, "cover", 0.0) or 0.0)
    d = max(h - cover, 0.0)
    dv = max(0.9 * d, 0.72 * h)

    tie_dia = float(getattr(section, "tie_diameter", 0.0) or 0.0)
    s = float(getattr(section, "tie_spacing", 0.0) or 0.0)
    av = 0.0
    if tie_dia > 0.0:
        av = tie_legs * (np.pi / 4.0) * tie_dia**2

    asl = 0.0
    bar_dia = float(getattr(section, "top_bar_dia", 0.0) or 0.0) or float(
        getattr(section, "bot_bar_dia", 0.0) or 0.0
    )
    n_bars = int(getattr(section, "top_bars", 0) or 0) + int(getattr(section, "bot_bars", 0) or 0)
    if bar_dia > 0.0 and n_bars > 0:
        asl = 0.5 * n_bars * (np.pi / 4.0) * bar_dia**2

    return ShearGeometry(bw=bw, h=h, d=d, dv=dv, av=av, s=s, asl=asl)


def member_shear_capacity(
    section: Section,
    concrete: Any,
    rebar: Optional[Any] = None,
    tie: Optional[Any] = None,
    units: Optional[dict[str, str]] = None,
    axial: float = 0.0,
    shear: float = 0.0,
    moment: float = 0.0,
    aggregate_size_mm: float = DEFAULT_AGGREGATE_SIZE_MM,
    theta_deg: float = DEFAULT_SHEAR_THETA_DEG,
    epsilon_x: Optional[float] = None,
) -> ShearCapacityResult:
    """Nominal shear capacity of an RC section by the simplified MCFT.

    Args:
        section: Frame section (e.g. ``ConcreteRectangularSection``).
        concrete: Concrete ``Material`` (``Fc``, ``E_mod``, ``G_mod``).
        rebar: Longitudinal rebar ``Material`` (``E_mod``) — optional; the
            strain term is disabled when ``None`` (β is then maximised).
        tie: Transverse rebar ``Material`` (``Fy``) — optional; ``tie_fy``
            on the section wins when both are present.
        units: Model units dict — required.  The empirical
            ``β·√f'c·b_w·d_v`` term and the crack-spacing parameter are
            anchored to the MPa/mm convention, so model stresses/lengths
            are rescaled internally via the unit factors.
        axial: Axial force at the section, **compression positive**
            (model force).
        shear: |shear force| at the section (model force).
        moment: |bending moment| at the section (model force × length).
        aggregate_size_mm: Coarse-aggregate size in mm (default 19).
        theta_deg: Angle of the diagonal compression field in degrees
            (default 35 — CSA General Method).  Used in the mid-depth
            longitudinal strain term; the transverse-steel contribution
            ``V_s = A_v·f_yv·d_v/s`` deliberately keeps the 45°-truss
            (ACI-318) form, as documented in
            ``docs/shear_failure_modelling.md``.
        epsilon_x: Explicit mid-depth longitudinal strain to use instead of
            the force-derived value (overrides the ``axial``/``shear``/
            ``moment`` strain term).

    Returns:
        :class:`ShearCapacityResult` with all forces in model units.
    """
    g = section_shear_geometry(section)
    fc = float(getattr(concrete, "Fc", 0.0) or 0.0)
    if fc <= 0.0 or g.bw <= 0.0 or g.dv <= 0.0:
        raise ValueError(
            "member_shear_capacity requires a concrete section with f'c, "
            f"b_w and d_v — got fc={fc}, bw={g.bw}, dv={g.dv}"
        )

    # ── Longitudinal strain at mid-depth (CSA General Method) ──
    es = float(getattr(rebar, "E_mod", 0.0) or 0.0)
    if es <= 0.0:
        es = None  # strain term disabled
    asl = g.asl
    cot = 1.0 / np.tan(np.deg2rad(theta_deg))
    if epsilon_x is not None:
        eps_x = max(float(epsilon_x), 0.0)
    else:
        eps_x = 0.0
        if es is not None and asl > 0.0:
            num = abs(moment) / g.dv + 0.5 * (-axial) + 0.5 * abs(shear) * cot
            eps_x = max(num, 0.0) / (2.0 * es * asl)

    # ── Equivalent crack-spacing parameter s_ze (mm) ───────────
    if units is None:
        raise ValueError(
            "member_shear_capacity requires the model 'units' dict — the "
            "empirical √f'c·b·d term and the crack-spacing parameter are "
            "anchored to the MPa/mm convention and must be rescaled."
        )
    lsf = length_scale_factor(units)
    fsf = force_scale_factor(units)
    ssf = stress_scale_factor(units)

    s_mm = g.s / lsf * 1000.0
    s_ze_mm = s_mm * 35.0 / (15.0 + max(aggregate_size_mm, 5.0))

    beta = (0.4 / (1.0 + 1500.0 * eps_x)) * (1300.0 / (1000.0 + s_ze_mm))

    # Concrete contribution — CSA A23.3-04, Clause 11.  The empirical
    # β·√f'c·b_w·d_v term uses f'c in MPa and b,d in mm (giving N), then
    # rescales to model force units.
    fc_mpa = fc / (ssf * 1.0e6)
    bw_mm = g.bw / lsf * 1000.0
    dv_mm = g.dv / lsf * 1000.0

    vc = beta * np.sqrt(fc_mpa) * bw_mm * dv_mm * fsf
    vcr = 0.33 * np.sqrt(fc_mpa) * bw_mm * dv_mm * fsf
    vn_upper = 0.25 * fc_mpa * bw_mm * dv_mm * fsf

    vs = 0.0
    if g.av > 0.0 and g.s > 0.0:
        fyv = float(getattr(section, "tie_fy", 0.0) or 0.0) or float(getattr(tie, "Fy", 0.0) or 0.0)
        if fyv > 0.0:
            # V_s uses the 45°-truss (ACI-318) form A_v·f_yv·d_v/s —
            # cot θ = 1 — matching the formula documented in
            # docs/shear_failure_modelling.md.  θ only enters the
            # mid-depth longitudinal strain term above.
            vs = g.av * fyv * g.dv / g.s

    vn = min(vc + vs, vn_upper)
    return ShearCapacityResult(
        vc=float(vc),
        vs=float(vs),
        vn=float(vn),
        vn_upper=float(vn_upper),
        vcr=float(vcr),
        beta=float(beta),
        epsilon_x=float(eps_x),
        dv=g.dv,
        d=g.d,
        bw=g.bw,
        s_ze_mm=float(s_ze_mm),
    )


# ═══════════════════════════════════════════════════════════════
# Nonlinear shear backbone
# ═══════════════════════════════════════════════════════════════


def shear_backbone(
    section: Section,
    concrete: Any,
    rebar: Optional[Any] = None,
    tie: Optional[Any] = None,
    units: Optional[dict[str, str]] = None,
    axial: float = 0.0,
    shear: float = 0.0,
    moment: float = 0.0,
    aggregate_size_mm: float = DEFAULT_AGGREGATE_SIZE_MM,
    theta_deg: float = DEFAULT_SHEAR_THETA_DEG,
    shear_area_factor: float = DEFAULT_SHEAR_AREA_FACTOR,
    post_crack_ratio: float = DEFAULT_POST_CRACK_STIFFNESS_RATIO,
    post_peak_ratio: float = DEFAULT_POST_PEAK_STIFFNESS_RATIO,
    residual_ratio: float = DEFAULT_SHEAR_RESIDUAL_RATIO,
    epsilon_x: Optional[float] = None,
) -> Optional[dict[str, float]]:
    """Build a trilinear force–shear-strain backbone for the section.

    Returns a dict with the six anchor points of the backbone:

        ``v_cr`` / ``g_cr`` — diagonal-cracking force/strain (elastic end)
        ``v_n``  / ``g_n``  — nominal shear capacity / strain at peak
        ``v_r``  / ``g_r``  — residual force / strain at the end of the
                              degrading branch

    The elastic segment uses ``GA_v = G·(f·A)`` with ``f =
    shear_area_factor``.  Post-cracking and post-peak branches use
    ``post_crack_ratio·GA_v`` and ``post_peak_ratio·GA_v`` (negative),
    respectively; the residual force is ``residual_ratio·V_n``.

    Returns:
        The backbone dict, or ``None`` when the section has no shear
        stiffness (missing concrete modulus / area).
    """
    # Default strain state: when no demand forces are supplied, the
    # capacity is evaluated at the longitudinal steel's first-yield strain
    # (flexure-shear interaction) rather than at zero strain, which would
    # overstate the peak (no cracking/softening of the flexure).
    if epsilon_x is None and abs(shear) < 1e-9 and abs(moment) < 1e-9:
        fy = float(getattr(rebar, "Fy", 0.0) or 0.0)
        es = float(getattr(rebar, "E_mod", 0.0) or 0.0)
        if fy > 0.0 and es > 0.0:
            epsilon_x = fy / es
    cap = member_shear_capacity(
        section,
        concrete,
        rebar=rebar,
        tie=tie,
        units=units,
        axial=axial,
        shear=shear,
        moment=moment,
        aggregate_size_mm=aggregate_size_mm,
        theta_deg=theta_deg,
        epsilon_x=epsilon_x,
    )

    g_mod = (
        concrete.shear_modulus()
        if hasattr(concrete, "shear_modulus")
        else float(getattr(concrete, "G_mod", 0.0) or 0.0)
    )
    a_gross = float(getattr(section, "A", 0.0) or 0.0)
    if not a_gross:
        a_gross = cap.bw * cap.d
    gav = g_mod * shear_area_factor * a_gross
    if gav <= 0.0:
        return None

    v_cr = min(cap.vcr, cap.vn)
    g_cr = v_cr / gav
    v_n = cap.vn
    g_n = g_cr + (v_n - v_cr) / max(post_crack_ratio * gav, 1e-12 * gav)
    v_r = residual_ratio * v_n
    g_r = g_n + (v_n - v_r) / max((-post_peak_ratio) * gav, 1e-12 * gav)

    return {
        "v_cr": float(v_cr),
        "g_cr": float(g_cr),
        "v_n": float(v_n),
        "g_n": float(g_n),
        "v_r": float(v_r),
        "g_r": float(g_r),
        "gav": float(gav),
    }


# ═══════════════════════════════════════════════════════════════
# Mode-of-failure reporter
# ═══════════════════════════════════════════════════════════════


def report_shear_failure(
    builder: Any,
    results: dict[str, Any],
    aggregate_size_mm: float = DEFAULT_AGGREGATE_SIZE_MM,
    theta_deg: float = DEFAULT_SHEAR_THETA_DEG,
) -> ShearFailureReport:
    """Report the shear demand-to-capacity history of every concrete member.

    The pushover that produced *results* must have been run with
    ``record_element_forces=True`` (so ``results["element_forces_history"]``
    holds per-step local element forces).  For each concrete frame member
    and each pushover step the local end forces are reduced to
    ``V = max transverse shear``, ``M = max flexural moment`` and
    ``N = -Fx`` (compression positive) and compared against the simplified
    MCFT capacity.

    Returns:
        :class:`ShearFailureReport` with the chronological exceedance list,
        the peak DCR per member, and the governing member/step.
    """
    history = results.get("element_forces_history")
    if not history:
        raise ValueError(
            "report_shear_failure requires a pushover result recorded with "
            "record_element_forces=True (results['element_forces_history'] "
            "is empty)."
        )
    control_disps = results.get("control_disp") or []

    mesh = builder.mesh_model
    assignments = mesh.frame_assignments or {}
    report = ShearFailureReport(
        entries=[],
        max_dcr={},
        members_checked=[],
    )

    for eid, elem in mesh.frame_elements.items():
        if getattr(elem, "inactive", False):
            continue
        sec_name = assignments.get(eid)
        if not sec_name:
            continue
        sec = mesh.sections.get(sec_name)
        if sec is None:
            continue
        concrete = mesh.materials.get(getattr(sec, "material", ""))
        if concrete is None or str(getattr(concrete, "type", "")).lower() != "concrete":
            continue  # steel members are not shear-checked
        # Skip sections with degenerate concrete shear geometry (fc, bw or
        # dv non-positive — e.g. a section missing depth/bf), so
        # member_shear_capacity cannot raise and abort the whole report.
        _g = section_shear_geometry(sec)
        _fc = float(getattr(concrete, "Fc", 0.0) or 0.0)
        if _fc <= 0.0 or _g.bw <= 0.0 or _g.dv <= 0.0:
            continue
        rebar = mesh.materials.get(getattr(sec, "rebar_material", "") or "")
        tie = mesh.materials.get(getattr(sec, "tie_rebar_mat", "") or "")
        tag = builder.frame_tag_map.get(eid, getattr(elem, "elem_tag", eid))

        report.members_checked.append(eid)
        peak_dcr = 0.0
        for step, forces in enumerate(history):
            f = forces.get(tag)
            if not f:
                continue
            v_dem = max(
                np.hypot(f.get("Fy", 0.0), f.get("Fz", 0.0)),
                np.hypot(f.get("Fy_j", 0.0), f.get("Fz_j", 0.0)),
            )
            m_dem = max(
                np.hypot(f.get("My", 0.0), f.get("Mz", 0.0)),
                np.hypot(f.get("My_j", 0.0), f.get("Mz_j", 0.0)),
            )
            n_comp = -float(f.get("Fx", 0.0))
            cap = member_shear_capacity(
                sec,
                concrete,
                rebar=rebar,
                tie=tie,
                units=builder.units,
                axial=n_comp,
                shear=v_dem,
                moment=m_dem,
                aggregate_size_mm=aggregate_size_mm,
                theta_deg=theta_deg,
            )
            if cap.vn <= 0.0:
                continue
            dcr = v_dem / cap.vn
            peak_dcr = max(peak_dcr, float(dcr))
            if dcr >= 1.0:
                report.entries.append(
                    ShearFailureEntry(
                        elem_id=eid,
                        step=step,
                        control_disp=(
                            float(control_disps[step]) if step < len(control_disps) else None
                        ),
                        demand=float(v_dem),
                        capacity=float(cap.vn),
                        dcr=float(dcr),
                        axial=float(n_comp),
                    )
                )
        report.max_dcr[eid] = float(peak_dcr)

    if report.entries:
        # Entries are appended in element loop order; sort by step so the
        # governing (earliest) failure is deterministic.
        report.entries.sort(key=lambda e: (e.step, e.elem_id))
        first = report.entries[0]
        report.governing_elem = first.elem_id
        report.governing_step = first.step
        report.governing_dcr = first.dcr
    # No exceedance — the governing member is the highest-DCR member.
    elif report.max_dcr:
        report.governing_elem = max(report.max_dcr, key=report.max_dcr.get)
        report.governing_dcr = report.max_dcr[report.governing_elem]
    return report
