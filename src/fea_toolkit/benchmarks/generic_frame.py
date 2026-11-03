"""IK-2005 / Medina (2002) generic single-bay moment frames — generator.

Builds the 12 generic frames of Ibarra & Krawinkler (2005, PEER 2005/06),
whose structural-property tables (Medina & Krawinkler 2003, PEER 2003/15,
Appendix A, Tables A.7–A.18) are transcribed in
:mod:`~fea_toolkit.benchmarks.generic_frame_data`.

Model recipe (IK-2005 §5.3.1, Appendix B):

* single bay, N storeys, span = 2 × storey height (288 in / 144 in);
* the columns of a storey and the beam above them share one moment of inertia
  ``Ii = I1 · (Ii/I1)``;
* rotational springs at the **beam ends** (both ends) and at the **column
  bases**; the columns are otherwise elastic ("infinite strength" BH frames);
* the member-end spring backbone is the IK-2005 reference: peak-oriented,
  ``αs = 0.03``, ``αc = −0.10``, ``δc/δy = 4``, no cyclic deterioration
  (``γs,c,k,a = ∞``) and no residual strength (disregarded, §5.3.2).

**Units.**  The Medina tables are in US customary units (kips, inches,
kip-in, ksi).  This generator builds the frame in **SI (N, m, kg)** — the same
system as the SAC benchmark and the PEER records (m/s²) — so every table value
is converted exactly once from US to SI at the module boundary::

    1 in     = 0.0254 m                 (exact)
    1 kip    = 4448.2216152605 N        (exact; 1 kip = 1000 lbf)
    1 ksi    = 6.894757293168361e6 Pa   (exact)
    1 kip·in = 112.9848290276167 N·m    (kip·in = kip_to_N × in_to_m)
    1 in⁴    = (0.0254)⁴ m⁴             (exact)

Gravity is never hard-coded: the P-Δ load and the lumped masses use
:func:`~fea_toolkit._unit_scaling.g_from_units` on :data:`UNITS`.  No material
strength or modulus is hand-converted — :func:`steel_e_pa` scales the table's
``E = 29000 ksi`` through the exact factor above, and all backbone strengths
(``My``) and stiffnesses (``Kc`` / ``Kb``) scale through ``kip·in → N·m``.
"""

from __future__ import annotations

from typing import Any, Optional

from .._unit_scaling import g_from_units
from ..model.sap_data import (
    FrameElement,
    MassSource,
    Material,
    Node,
    Restraint,
    SAPModelData,
    Section,
)
from .generic_frame_data import (
    E_STEEL_KSI,
    FRAME_DATA,
    SPAN_IN,
    STOREY_HEIGHT_IN,
)

# ── Unit conversion (US customary → SI) ──
_IN_TO_M = 0.0254  # exact
_KIP_TO_N = 4448.2216152605  # exact (1 kip = 1000 lbf)
_KSI_TO_PA = 6.894757293168361e6  # exact
_KIP_IN_TO_NM = _KIP_TO_N * _IN_TO_M  # 112.9848… N·m per kip·in
_IN4_TO_M4 = _IN_TO_M**4  # 4.1623e-7 m⁴ per in⁴

#: SI unit system the frame is built in (N, m, kg; consistent with the records).
UNITS: dict[str, str] = {"F": "N", "L": "m", "T": "C"}

#: Cross-sectional area of the frame members (m²).  The Medina tables carry no
#: axial member data — the benchmark frames neglect axial flexibility — so A is
#: set large (axially rigid): a finite A is required by ``elasticBeamColumn``,
#: but any value this large contributes a lateral flexibility that is
#: < 0.1 % of the flexural flexibility (verified against the modal T1 check).
AXIAL_AREA_M2 = 100.0

# ── IK-2005 §5.3.2 reference backbone ──
ALPHA_S = 0.03  # strain-hardening stiffness ratio αs
ALPHA_C = -0.10  # post-capping stiffness ratio αc
DUCTILITY_RATIO = 4.0  # δc/δy — capping ductility
RESIDUAL_RATIO = 0.0  # residual strength disregarded (§5.3.2)


def storey_height_m() -> float:
    """Storey height in metres (12 ft = 144 in → 3.6576 m)."""
    return STOREY_HEIGHT_IN * _IN_TO_M


def span_m() -> float:
    """Bay span in metres (24 ft = 288 in → 7.3152 m)."""
    return SPAN_IN * _IN_TO_M


def steel_e_pa() -> float:
    """Steel elastic modulus in Pa (29000 ksi → 1.99948e11 Pa)."""
    return E_STEEL_KSI * _KSI_TO_PA


def build_model(code: str) -> SAPModelData:
    """Build a generic single-bay frame as a :class:`SAPModelData` in SI units.

    Nodes are laid out on a 2-line × (N+1)-level grid: ``{level}-{line}`` with
    ``level = 0..N`` (0 = base) and ``line = 0..1`` (0 = left, 1 = right).
    Sections are one per storey (``sec{i}`` for storey ``i = 1..N``), shared by
    the two columns and the beam above.  Seismic masses are lumped at the floor
    levels (equal per floor, split across the two column lines).  The base nodes
    are **fully fixed** (all six DOFs); the column-base rotational spring is a
    ``zeroLength`` between each fixed base node and the ground-storey column
    (see :func:`base_spring_props`).

    Args:
        code: Four-digit frame code (e.g. ``"0909"``); must be in
            :data:`~fea_toolkit.benchmarks.generic_frame_data.FRAME_CODES`.

    Returns:
        The frame as ``SAPModelData`` (elastic members + masses; the member-end
        springs are stamped at build time from :func:`imk_params`).

    Raises:
        KeyError: For an unknown *code*.
    """
    data = FRAME_DATA[code]
    n = data["N"]
    norm = data["norm"]
    stories = data["stories"]

    e_mod = steel_e_pa()
    h = storey_height_m()
    span = span_m()
    g = g_from_units(UNITS)
    i1 = norm["I1"] * _IN4_TO_M4

    # ── Nodes ──
    nodes: dict[str, Node] = {}
    tag = 1
    for level in range(n + 1):
        for line in (0, 1):
            nid = f"{level}-{line}"
            nodes[nid] = Node(node_id=nid, node_tag=tag, x=line * span, y=0.0, z=level * h)
            tag += 1

    # ── Sections (one per storey; columns and beam share I) ──
    # The tables carry no axial member data (axial flexibility is neglected in
    # the benchmark), so A is axially rigid (see AXIAL_AREA_M2) and the
    # weak-axis/torsion inertias mirror I as out-of-plane placeholders.
    sections: dict[str, Section] = {}
    for i in range(1, n + 1):
        ii = i1 * stories[i]["Ii_I1"]
        sections[f"sec{i}"] = Section(
            name=f"sec{i}",
            shape="I/Wide Flange",
            material="Steel",
            A=AXIAL_AREA_M2,
            I33=ii,
            I22=ii,
            J=ii,
        )

    # ── Frame elements (columns then beams) ──
    frames: dict[str, FrameElement] = {}
    assigns: dict[str, str] = {}
    etag = 1
    for i in range(1, n + 1):
        for line in (0, 1):
            eid = f"C{i}-{line}"
            frames[eid] = FrameElement(
                elem_id=eid, elem_tag=etag, node_i=f"{i - 1}-{line}", node_j=f"{i}-{line}"
            )
            assigns[eid] = f"sec{i}"
            etag += 1
    for i in range(1, n + 1):
        eid = f"B{i}"
        frames[eid] = FrameElement(elem_id=eid, elem_tag=etag, node_i=f"{i}-0", node_j=f"{i}-1")
        assigns[eid] = f"sec{i}"
        etag += 1

    # ── Restraints (planar X-Z frame: restrain uy, rx, rz) ──
    restraints: dict[str, Restraint] = {}
    for level in range(n + 1):
        for line in (0, 1):
            nid = f"{level}-{line}"
            if level == 0:
                # Base: fixed (the column-base rotational spring — stamped as the
                # ground-storey column's ``imk_params_i`` with ``k0 = Kc`` — is a
                # zeroLength between this fixed node and the column interior).
                restraints[nid] = Restraint([1, 1, 1, 1, 1, 1])
            else:
                restraints[nid] = Restraint([0, 1, 0, 1, 0, 1])

    # ── Seismic masses (equal per floor, split across the two lines) ──
    total_mass = norm["W"] * _KIP_TO_N / g
    added_masses: dict[str, float] = {}
    for i in range(1, n + 1):
        m = total_mass * stories[i]["Wi_W"] / 2.0
        added_masses[f"{i}-0"] = m
        added_masses[f"{i}-1"] = m

    return SAPModelData(
        nodes=nodes,
        restraints=restraints,
        materials={"Steel": Material("Steel", "Steel", E_mod=e_mod, G_mod=e_mod / 2.6, nu=0.3)},
        sections=sections,
        frame_elements=frames,
        area_elements={},
        frame_assignments=assigns,
        area_assignments={},
        groups={},
        frame_auto_mesh={},
        frame_releases={},
        added_masses=added_masses,
        mass_sources={"MSSSRC1": MassSource(name="MSSSRC1", masses=True)},
        units=UNITS,
    )


def imk_params(
    ke: float,
    my: float,
    theta_y: Optional[float] = None,
    *,
    alpha_s: float = ALPHA_S,
    alpha_c: float = ALPHA_C,
    ductility: float = DUCTILITY_RATIO,
    residual: float = RESIDUAL_RATIO,
) -> dict[str, float]:
    """Map the IK-2005 §5.3.2 backbone to an ``imk_params`` dict for a spring.

    The spring backbone is expressed at the member end (Appendix B: the
    "spring" parameters refer to the member-end moment–rotation relationship).
    Given the member-end elastic stiffness ``ke`` and yield moment ``my``, the
    elastic yield rotation is ``theta_y = my / ke`` (the table's ``θy`` is the
    *rounded* version of this).  The IK-2005 reference (peak-oriented, ``αs``,
    ``αc``, ``δc/δy``, no CD, no residual) maps to the plastic-rotation
    backbone the lumped IMK hinge consumes:

    * ``theta_p = (δc/δy − 1) · θy`` (pre-capping plastic rotation);
    * ``mc_my = 1 + αs · (δc/δy − 1)`` (capping ratio Mc/My);
    * ``theta_pc = (mc_my − residual) · My / (|αc| · ke)`` (post-capping plastic
      rotation, i.e. the drop Mc → residual at slope ``αc·ke``);
    * ``res = 0`` and ``Λs = Λc = Λa = Λk = 0`` (no residual, no CD).

    Args:
        ke: Member-end elastic rotational stiffness (model units).
        my: Yield moment (positive; model units).
        theta_y: Elastic yield rotation (rad); ``None`` uses ``my / ke``.
        alpha_s: Strain-hardening ratio (default :data:`ALPHA_S`).
        alpha_c: Post-capping ratio (default :data:`ALPHA_C`).
        ductility: Capping ductility ``δc/δy`` (default :data:`DUCTILITY_RATIO`).
        residual: Residual-strength ratio ``Mr/My`` (default
            :data:`RESIDUAL_RATIO`).

    Returns:
        An ``imk_params`` dict ready for the ``hinge_backbone='imk'`` /
        ``'imk_peak'`` lumped-hinge path.
    """
    if theta_y is None:
        theta_y = my / ke
    mc_my = 1.0 + alpha_s * (ductility - 1.0)
    theta_p = (ductility - 1.0) * theta_y
    theta_pc = (mc_my - residual) * my / (abs(alpha_c) * ke)
    return {
        "My_pos": my,
        "My_neg": -my,
        "theta_p_pos": theta_p,
        "theta_p_neg": theta_p,
        "theta_pc_pos": theta_pc,
        "theta_pc_neg": theta_pc,
        "mc_my": mc_my,
        "res_pos": residual,
        "res_neg": residual,
        "lamda_S": 0.0,
        "lamda_C": 0.0,
        "lamda_A": 0.0,
        "lamda_K": 0.0,
        "c_S": 1.0,
        "c_C": 1.0,
        "c_A": 1.0,
        "c_K": 1.0,
        "D_pos": 1.0,
        "D_neg": 1.0,
    }


def base_spring_props(code: str) -> dict[str, float]:
    """Column-base spring (``Kc``, ``Myc``, ``theta_yc``) for *code*, in SI.

    The base row of the table gives the spring stiffness ``Kc`` (N·m/rad),
    yield moment ``Myc`` (N·m) and yield rotation ``theta_yc`` (rad) directly.
    """
    base = FRAME_DATA[code]["stories"][0]
    return {
        "Kc": base["Kc"] * _KIP_IN_TO_NM,
        "Myc": base["Myc"] * _KIP_IN_TO_NM,
        "theta_yc": base["theta_yc"],
    }


def beam_spring_props(code: str, storey: int) -> dict[str, float]:
    """Beam-end spring (``Kb``, ``Myb``, ``theta_yb``) for *storey*, in SI.

    Args:
        code: Frame code.
        storey: Storey index (1-based), matching the table rows ``1/2`` … ``N/N+1``.
    """
    data = FRAME_DATA[code]
    norm = data["norm"]
    row = data["stories"][storey]
    return {
        "Kb": norm["Kb2"] * row["Kbi_Kb2"] * _KIP_IN_TO_NM,
        "Myb": norm["Myb2"] * row["Mybi_Myb2"] * _KIP_IN_TO_NM,
        "theta_yb": norm["theta_yb2"] * row["theta_ybi_theta_yb2"],
    }


def enrich_imk(md: SAPModelData, code: str) -> SAPModelData:
    """Stamp the IK-2005 §5.3.2 ``IMKPeakOriented`` springs onto the frame.

    * **beam ends** — near-rigid ``imk_peak`` springs (the elastic beam provides
      the member's ``6EI/L``);
    * **ground-storey column bases** — a total-M-θ spring (``k0 = Kc``), stamped
      as ``imk_params_i`` so only the base end carries it and the column top
      stays a rigid near-rigid elastic spring;
    * **upper columns** — elastic (no ``imk_params``).

    Mutates and returns *md*.
    """
    base = base_spring_props(code)
    base_params = imk_params(ke=base["Kc"], my=base["Myc"])
    base_params["k0"] = base["Kc"]
    for eid, elem in md.frame_elements.items():
        if eid.startswith("B"):
            storey = int(eid[1:])
            b = beam_spring_props(code, storey)
            elem.imk_params = imk_params(ke=b["Kb"], my=b["Myb"])
        elif eid.startswith("C1-"):
            elem.imk_params_i = dict(base_params)
    return md


def add_pdelta_gravity(md: SAPModelData, factor: float = 1.4) -> SAPModelData:
    """Add held gravity joint loads ``P = factor × seismic mass × g`` for P-Δ.

    IK-2005 models global P-Δ as ``1.4 × the seismically effective mass`` held
    throughout the history (dead + 40 % live).  One downward global-Z joint load
    per mass node, pattern ``"GRAV"`` (idempotent).  Mirrors
    :func:`fea_toolkit.benchmarks.sac.add_pdelta_gravity`.
    """
    from ..model.sap_data import JointLoad

    g = g_from_units(md.units)
    md.joint_loads = [jl for jl in md.joint_loads if jl.pattern != "GRAV"]
    for nid, mass in md.added_masses.items():
        if mass <= 0.0:
            continue
        md.joint_loads.append(JointLoad(pattern="GRAV", node_id=nid, fz=-factor * mass * g))
    return md


def builder_config(*, pdelta: bool = False) -> dict[str, Any]:
    """The ``AnalysisBuilder`` config for the IK-2005 generic frame.

    Args:
        pdelta: When True, emit the ``PDelta`` geometric transformation (global
            P-Δ) instead of ``Linear``.
    """
    return {
        "element_type": "elasticBeamColumn",
        "hinge_model": "lumped",
        "hinge_backbone": "imk_peak",
        # The benchmark's member-end stiffness is the *exact* 6EI/L (the
        # spring/beam-column split is internal — IK-2005 Appendix B) — a large
        # factor keeps the near-rigid spring ~rigid so the elastic member carries
        # the full Ke (residual softening < 1 %).
        "hinge_stiffness_factor": 100.0,
        "geom_transf_type": "PDelta" if pdelta else "Linear",
    }


def preprocess_config() -> dict[str, Any]:
    """The ``Preprocessor`` config for the generic frame."""
    return {"element_type": "elasticBeamColumn"}


def build_builder(
    code: str,
    *,
    pdelta: bool = False,
    pdelta_gravity: bool = False,
):
    """Build the generic frame into an ``AnalysisBuilder`` (domain already built).

    Args:
        code: Frame code (e.g. ``"0909"``).
        pdelta: When True, use the ``PDelta`` geometric transformation.
        pdelta_gravity: When True, add the held ``1.4 × seismic mass`` gravity
            loads (pair with ``pdelta=True`` for a P-Δ collapse comparison).

    Returns:
        An ``AnalysisBuilder`` with ``build_domain()`` already called.
    """
    from ..opensees.analysis_builder import AnalysisBuilder
    from ..opensees.preprocessor import preprocess_model

    md = build_model(code)
    enrich_imk(md, code)
    if pdelta_gravity:
        add_pdelta_gravity(md)
    mesh = preprocess_model(md, preprocess_config())
    builder = AnalysisBuilder(mesh, builder_config(pdelta=pdelta))
    builder.build_domain()
    return builder
