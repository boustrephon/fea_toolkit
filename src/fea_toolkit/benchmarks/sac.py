"""SAC 3-story benchmark — loadable model and nonlinear evaluation-model builder.

The three-story SAC steel moment frame (LA3) is the published-code benchmark
anchor for both the elastic modal path (T2) and the nonlinear NLTHA path (T4),
from Ohtori et al. (2004), DOI 10.1061/(ASCE)0733-9399(2004)130:4(366).

This module is the *single source of truth* for the SAC model in the package:
it bundles the Tcl transcription (``sac_3story.tcl``), enriches the elastic
model with AISC plastic section moduli / yield strengths, and builds the two
nonlinear evaluation-model variants:

* **M1** — centreline joints + concentrated-plasticity bilinear steel hinges
  (``hinge_model='lumped'``, ``hinge_backbone='bilinear'``).
* **M2** — M1 plus an explicit Krawinkler & Gupta panel zone at each
  beam-column joint (``panel_zone_model='krawinkler_gupta'``).

Both variants default to the bilinear hinge; ``backbone="trilinear"`` switches
them onto the transcribed ``mat_tbl`` backbone (``hinge_backbone="trilinear"``).

Both variants use the member schedule from the official benchmark data
(``struct3.m``): W14x257 / W14x311 columns, per-level beams (W33x118 / W30x116 /
W24x68) with a W21x44 fourth bay, and a leaning column.  The lateral model uses
bare-steel beam inertia (the composite slab is a gravity-only effect).

The benchmark's exact hinge / panel-zone parameters are not redistributable, so
the plastic moduli and yield strengths below are computed from AISC tables
(see ``docs/sac_nonlinear_benchmark.md``).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Optional

import numpy as np

from .._unit_scaling import g_from_units
from ..io.tcl_importer import parse_tcl_model
from ..model.sap_data import (
    BoxSection,
    FrameElement,
    FrameRelease,
    ISection,
    MassSource,
    Material,
    Node,
    Restraint,
    SAPModelData,
    Section,
)
from ..opensees.analysis_builder import AnalysisBuilder
from ..opensees.preprocessor import preprocess_model
from .sac_member_data import (
    BEAM_MATERIALS,
    COLUMN_MATERIALS,
    MAT_TBLS,
    MAT_TO_SECTION,
)

#: Bundled Tcl transcription of the SAC 3-story N-S perimeter frame.
SAC3_TCL = Path(__file__).with_name("sac_3story.tcl")

#: SI unit system of the bundled model (N, m, kg).
SAC3_UNITS: dict[str, str] = {"F": "N", "L": "m", "T": "C"}

#: Integration time step of the benchmark's NLTHA (``struct#.m`` ``dt_cal``).
#: The archive records are stored at their native sampling (0.02 s for
#: El Centro / Northridge / Kobe, 0.01 s for Hachinohe); the benchmark
#: sub-steps them to 0.005 s (Newmark β=1/4, γ=1/2) for the integration.
SAC_DT_CAL: float = 0.005

# ── SAC member schedule (SI) ──
# From the official benchmark model data (Ohtori et al. 2004, NEES archive
# resource 257, `struct3.m`).  Columns are 345 MPa (50 ksi); beams 248 MPa
# (36 ksi).  The lateral model uses bare-steel beam inertia and a rigid axial
# (EA = 1e10) for the floor diaphragm — the composite slab is gravity-only.
COLUMN_FY = 345e6
BEAM_FY = 248e6

#: AISC expected-yield ratio ``Ry`` for the column axial-load ratio ``Pg/Pye``
#: (1.1 for A992/A572-50 — matches ``lignos_2019_wide_flange_column``'s default
#: ``ry_factor``).
_COLUMN_RY = 1.1

#: Moment-frame columns keyed by area (m²).  The far-line *leaning* column
#: (models the interior gravity framing) is absent — it stays elastic.
#:
#: ``tw`` is the panel-zone web thickness.  The transcribed values are ~1/2 in
#: (≈0.013 m) larger than the bare column web (W14x257 web = 0.030 m), which is
#: only consistent with a non-documented doubler plate (1/4 in per face).  The
#: Ohtori paper does not record doubler plates, so the values are kept as-is
#: rather than "corrected" to the bare web.
_COLUMNS_BY_AREA = {
    0.048759: {"Z33": 7.98e-3, "depth": 0.417, "tw": 0.0435},  # W14x257 (Zx 487 in³)
    0.05895: {"Z33": 9.88e-3, "depth": 0.437, "tw": 0.0488},  # W14x311 (Zx 603 in³)
}

#: Beams keyed by strong-axis inertia (m⁴) — rigid axial A = 0.05 m².
_BEAMS_BY_I = {
    2.455e-3: {"Z33": 6.80e-3, "depth": 0.836, "tw": 0.0140},  # W33x118
    2.0514e-3: {"Z33": 6.194e-3, "depth": 0.762, "tw": 0.0144},  # W30x116
    7.6145e-4: {"Z33": 2.90e-3, "depth": 0.602, "tw": 0.0105},  # W24x68 (Zx 177 in³)
    3.50775e-4: {"Z33": 1.563e-3, "depth": 0.526, "tw": 0.0089},  # W21x44
}

#: ``mat_tbl`` ``Mat_no`` per member (struct3.m ``element_tbl``), keyed by the
#: same area/inertia as ``_COLUMNS_BY_AREA`` / ``_BEAMS_BY_I``.  The leaning
#: column (material 6) and the rigid-axial link bay (material 7) are included.
_COLUMN_MAT_NO = {0.048759: 1, 0.05895: 2}  # W14x257 → 1, W14x311 → 2
_BEAM_MAT_NO = {
    2.455e-3: 3,  # W33x118 → 3
    2.0514e-3: 4,  # W30x116 → 4
    7.6145e-4: 5,  # W24x68 → 5
    3.50775e-4: 7,  # W21x44 → 7 (rigid-axial link bay)
}
_LEANING_MAT_NO = 6

#: Representative members for the figure generator / docs.
COLUMN = {"Z33": 7.98e-3, "depth": 0.417, "tw": 0.0435, "fy": COLUMN_FY}  # W14x257
BEAM = {"Z33": 6.194e-3, "depth": 0.762, "tw": 0.0144, "fy": BEAM_FY}  # W30x116

#: The three explicit rigid floor diaphragms from ``struct3.m``'s master-slave
#: table (``slv_tbl``): each floor ties its five nodes' horizontal DOF.
_FAITHFUL_DIAPHRAGMS = [
    {"name": "floor2", "nodes": ["6", "7", "8", "9", "10"]},
    {"name": "floor3", "nodes": ["11", "12", "13", "14", "15"]},
    {"name": "roof", "nodes": ["16", "17", "18", "19", "20"]},
]

# NOTE: ``struct3.m`` models the fourth bay as an axial-only brace (element type
# 3).  The faithful variant keeps it as a flexural beam instead — the far end of
# the bay connects to the leaning column (flexural inertia ~28× smaller than a
# moment column), so the bay's bending contribution is immaterial; the rigid
# floor diaphragm is the material difference (see ``preprocess_config``).


def _match_props(catalog: dict, value: float, tol: float) -> Optional[dict]:
    """Return the catalog entry whose key is within *tol* of *value*."""
    for key, props in catalog.items():
        if abs(key - value) < tol:
            return props
    return None


def _frame_element_roles(md: SAPModelData) -> dict[str, str]:
    """Map frame element id → ``'col'`` | ``'beam'`` by orientation."""
    roles: dict[str, str] = {}
    for eid, elem in md.frame_elements.items():
        ni = md.nodes.get(elem.node_i)
        nj = md.nodes.get(elem.node_j)
        if ni is None or nj is None:
            continue
        roles[eid] = "col" if abs(nj.z - ni.z) > abs(nj.x - ni.x) else "beam"
    return roles


def enrich_nonlinear(md: SAPModelData) -> SAPModelData:
    """Enrich the elastic SAC model with plastic moduli and yield strengths.

    Mutates and returns *md*: the moment-frame columns (W14x257 / W14x311) and
    the beams (W33x118 / W30x116 / W24x68 / W21x44) are given their AISC plastic
    modulus ``Z33``, depth, web thickness and a dedicated steel material whose
    ``Fy`` sets the bilinear-hinge yield moment ``My = Zx·fy``.  The leaning
    column stays elastic (no ``Z33``, elastic material).
    """
    md.materials["SteelCol"] = Material("SteelCol", "Steel", E_mod=2.0e11, Fy=COLUMN_FY)
    md.materials["SteelBeam"] = Material("SteelBeam", "Steel", E_mod=2.0e11, Fy=BEAM_FY)
    roles = _frame_element_roles(md)
    for eid, sname in md.frame_assignments.items():
        sec = md.sections.get(sname)
        if sec is None:
            continue
        if roles.get(eid) == "col":
            props = _match_props(_COLUMNS_BY_AREA, sec.A, 1e-6)
            if props is None:
                continue  # leaning column — stays elastic
            sec.material = "SteelCol"
        else:
            props = _match_props(_BEAMS_BY_I, sec.I33, 1e-8)
            if props is None:
                continue
            sec.material = "SteelBeam"
        sec.Z33 = props["Z33"]
        sec.depth = props["depth"]
        sec.tw = props["tw"]
    return md


def _parse_mat_no(sname: str) -> Optional[int]:
    """Parse the ``Mat_no`` from a section named ``mat{n}`` (9/20-story models)."""
    if sname.startswith("mat") and sname[3:].isdigit():
        return int(sname[3:])
    return None


def _mat_no_by_section(md: SAPModelData, building: int) -> dict[str, int]:
    """Map section name → ``element_tbl`` ``Mat_no`` for *building*.

    The 9/20-story builders name their sections ``mat{n}`` (one per backbone),
    so the mapping is read straight off the name.  The 3-story Tcl transcription
    carries no ``Mat_no`` labels, so it falls back to matching by area (columns)
    / inertia (beams) — the same keys as :func:`enrich_nonlinear`.
    """
    if building != 3:
        out: dict[str, int] = {}
        for sname in md.sections:
            n = _parse_mat_no(sname)
            if n is not None:
                out[sname] = n
        return out
    roles = _frame_element_roles(md)
    out = {}
    for eid, sname in md.frame_assignments.items():
        if sname in out:
            continue
        sec = md.sections.get(sname)
        if sec is None:
            continue
        if roles.get(eid) == "col":
            n = _match_props(_COLUMN_MAT_NO, sec.A, 1e-6)
            if n is None:
                n = _LEANING_MAT_NO
        else:
            n = _match_props(_BEAM_MAT_NO, sec.I33, 1e-8)
            if n is None:
                continue
        out[sname] = n
    return out


def enrich_trilinear(md: SAPModelData, building: int = 3) -> SAPModelData:
    """Stamp the Ohtori trilinear backbones (``mat_tbl``) onto the SAC sections.

    Maps each section to its ``Mat_no`` (3-story by area/inertia, 9/20-story by
    the ``mat{n}`` section name) and writes the trilinear backbone
    ``(EI1, EI2, EI3, d1, d2)`` so the builder's ``hinge_backbone='trilinear'``
    path can read it.  The 3-story leaning column (material 6) and rigid-axial
    link bay (material 7) are included.

    Args:
        md: The enriched SAC model; sections are stamped in place.
        building: 3, 9 or 20 — selects the ``mat_tbl`` table and member mapping.
    """
    mat_tbl = MAT_TBLS[building]
    for sname, mat_no in _mat_no_by_section(md, building).items():
        sec = md.sections.get(sname)
        if sec is None:
            continue
        row = mat_tbl[mat_no]
        sec.ei1, sec.ei2, sec.ei3 = row.ei1, row.ei2, row.ei3
        sec.d1, sec.d2 = row.d1, row.d2
    return md


def enrich_display_sections(md: SAPModelData, building: int = 3) -> SAPModelData:
    """Rebuild the SAC sections with their physical W/box display shapes.

    Each backbone-defined section is rebuilt as an :class:`ISection` (or
    :class:`BoxSection` for the 20-story box corners) carrying the physical
    section's flange dimensions (``bf``, ``tf``) so the visualiser can draw the
    cross-section.  The analysis properties (``A``, ``I33``, ``I22``, ``J``,
    ``Z33``, ``Z22`` and the ``ei1/ei2/ei3/d1/d2`` backbone), the panel-zone
    ``depth``/``tw``, and the base ``Section`` metadata (``manufacturer``,
    ``modifiers``, ``cg_offset_*``, ``ecc_v*``, ``imk_params``) are preserved
    unchanged; the physical display depth and web thickness go to the separate
    ``depth_disp``/``tw_disp`` fields so they never feed the panel-zone
    computation.

    Args:
        md: The enriched SAC model; sections are rebuilt in place.
        building: 3, 9 or 20 — selects the ``Mat_no → section`` map.  The
            3-story Tcl transcription matches by area/inertia; the 9/20-story
            builders name their sections ``mat{n}`` (see :func:`_mat_no_by_section`).

    Returns:
        *md*, mutated in place.

    Raises:
        KeyError: If a section's ``Mat_no`` maps to a shape with no geometry in
            the steel-section catalogue (``BOX_SECTIONS`` / ``W_SECTIONS``), i.e.
            ``MAT_TO_SECTION`` resolves to a shape name absent from both tables.
    """
    from ..model.steel_sections import BOX_SECTIONS, W_SECTIONS

    name_map = MAT_TO_SECTION[building]

    for sname, mat_no in _mat_no_by_section(md, building).items():
        sec = md.sections[sname]
        shape_name = name_map[mat_no]
        if shape_name in BOX_SECTIONS:
            cls, shape = BoxSection, "Box/Tube"
        else:
            cls, shape = ISection, "I/Wide Flange"
        row = BOX_SECTIONS.get(shape_name) or W_SECTIONS.get(shape_name)
        if row is None:
            raise KeyError(
                f"no section geometry for shape {shape_name!r} "
                f"(building {building}); check MAT_TO_SECTION_{building}"
            )
        _, _, _, _, d, bf, tf, tw, _, _ = row
        md.sections[sname] = cls(
            name=sname,
            shape=shape,
            material=sec.material,
            A=sec.A,
            I33=sec.I33,
            I22=sec.I22,
            J=sec.J,
            Z33=sec.Z33,
            Z22=sec.Z22,
            manufacturer=sec.manufacturer,
            modifiers=sec.modifiers,
            cg_offset_2=sec.cg_offset_2,
            cg_offset_3=sec.cg_offset_3,
            ecc_v2=sec.ecc_v2,
            ecc_v3=sec.ecc_v3,
            depth=getattr(sec, "depth", None) or 0.0,
            bf=bf,
            tf=tf,
            tw=getattr(sec, "tw", None) or 0.0,
            depth_disp=d,
            tw_disp=tw,
            ei1=sec.ei1,
            ei2=sec.ei2,
            ei3=sec.ei3,
            d1=sec.d1,
            d2=sec.d2,
            imk_params=getattr(sec, "imk_params", None),
        )
    return md


def derive_column_axial_ratios(md: SAPModelData, building: int = 3) -> dict[str, float]:
    """Per-section column gravity axial-load ratio ``Pg/Pye`` (tributary).

    The SAC benchmark's ``struct#.m`` transient is base-excited and carries **no
    gravity-load patterns**, so the operating axial load is derived from the
    *seismic weight* — the FEMA 355C seismically-effective dead weight (dead +
    reduced live + steel self-weight), which the model carries as ``added_masses``
    lumped at the floor nodes by tributary bay width.  This matches the
    benchmark's collapse-study convention that "seismic masses and gravity loads
    have the same origin" (Lignos 2008, Stanford TR177 §5); the FEMA 355C dead /
    reduced-live pressures are the same values transcribed in ``fema355c.py``.

    ``Pg`` for a column line is ``g · Σ (tributary mass above the base)`` and
    ``Pye = A · Ry · Fy`` (``Ry = 1.1``), so the ratio is the one the Lignos et
    al. (2019) column backbone consumes as ``axial_ratio``.

    Args:
        md: The SAC model after :func:`enrich_nonlinear` (moment-frame columns
            carry the ``SteelCol`` material; the leaning column stays elastic).
        building: 3, 9 or 20.  Only 3 is wired today (9/20 need per-member
            column splices).

    Returns:
        ``{section_name: Pg/Pye}`` for the moment-frame column sections, taking
        the *maximum* ratio across the column lines that share a section (the
        base column of each line is the critical member).

    See :func:`gravity_axial_ratios` for the independent full-solve cross-check.
    """
    if building != 3:
        raise NotImplementedError(
            f"column axial ratios for the {building}-story model need per-member "
            "column splices (not yet wired); use building=3"
        )
    g = g_from_units(md.units)
    roles = _frame_element_roles(md)
    nodes = md.nodes

    # Column lines: key each vertical member by the (x, y) of its lower node,
    # recording the section of the *lowest* segment (the base column — the one
    # that carries the full accumulated axial load).
    line_base: dict[tuple[float, float], tuple[float, str]] = {}
    for eid, elem in md.frame_elements.items():
        if roles.get(eid) != "col":
            continue
        ni = nodes[elem.node_i]
        nj = nodes[elem.node_j]
        bottom = ni if ni.z < nj.z else nj
        key = (bottom.x, bottom.y)
        if key not in line_base or bottom.z < line_base[key][0]:
            line_base[key] = (bottom.z, md.frame_assignments.get(eid, ""))

    # Accumulate the tributary mass above each column line and derive the ratio.
    ratios: dict[str, float] = {}
    for (x, y), (_, sname) in line_base.items():
        sec = md.sections.get(sname)
        if sec is None or getattr(sec, "material", None) != "SteelCol":
            continue  # leaning column — no hinge
        total_mass = 0.0
        for nid, mass in (md.added_masses or {}).items():
            nd = nodes.get(nid)
            if nd is None:
                continue
            if abs(nd.x - x) < 1e-9 and abs(nd.y - y) < 1e-9:
                total_mass += float(mass)
        pye = sec.A * _COLUMN_RY * COLUMN_FY
        ratios[sname] = max(ratios.get(sname, 0.0), g * total_mass / pye)
    return ratios


def gravity_axial_ratios(md: SAPModelData, building: int = 3) -> dict[str, float]:
    """Per-section column ``Pg/Pye`` from a full elastic gravity solve (cross-check).

    Independent of :func:`derive_column_axial_ratios`: applies the seismic weight
    as nodal gravity loads (``mass × g`` downward) and runs a linear static
    analysis, then reads each base column's axial force from its support
    reaction.  The two must agree — the benchmark collapses "seismic mass" and
    "gravity load" into one quantity, so the only difference is any moment-frame
    gravity redistribution, which is zero for the symmetric planar frames.

    Args:
        md: The SAC model before preprocessing; ``added_masses`` are the FEMA
            355C seismic weight lumped at the floor nodes.
        building: 3, 9 or 20.  Only 3 is exercised today.

    Returns:
        ``{section_name: Pg/Pye}`` for the moment-frame column sections, taking
        the maximum ratio across the base columns that share a section.
    """
    import openseespy.opensees as ops

    g = g_from_units(md.units)
    roles = _frame_element_roles(md)
    nodes = md.nodes

    mesh = preprocess_model(md, preprocess_config())
    builder = AnalysisBuilder(mesh, {})
    builder.build_domain()

    # Apply the seismic weight as nodal gravity loads (downward −Z).
    ops.timeSeries("Constant", 910001)
    ops.pattern("Plain", 910002, 910001)
    for nid, mass in (md.added_masses or {}).items():
        nd = mesh.nodes.get(nid)
        if nd is not None and mass:
            ops.load(nd.node_tag, 0.0, 0.0, -float(mass) * g, 0.0, 0.0, 0.0)

    res = builder.run_static_analysis(extract_reactions=True)
    reactions: dict[str, dict[str, float]] = res.get("reactions", {})

    ratios: dict[str, float] = {}
    for eid, elem in md.frame_elements.items():
        if roles.get(eid) != "col":
            continue
        ni = nodes[elem.node_i]
        nj = nodes[elem.node_j]
        bottom = ni if ni.z < nj.z else nj
        if bottom.node_id not in (md.restraints or {}):
            continue  # not a base (fixed) column
        sname = md.frame_assignments.get(eid, "")
        sec = md.sections.get(sname)
        if sec is None or getattr(sec, "material", None) != "SteelCol":
            continue  # leaning column
        rxn = reactions.get(bottom.node_id)
        if rxn is None:
            continue
        pye = sec.A * _COLUMN_RY * COLUMN_FY
        ratios[sname] = max(ratios.get(sname, 0.0), abs(float(rxn.get("fz", 0.0))) / pye)
    return ratios


def enrich_imk(
    md: SAPModelData,
    building: int = 3,
    column_axial_ratio: float = 0.0,
    column_axial_ratios: Optional[dict[str, float]] = None,
    beam_unbraced_factor: float = 1.0,
    theta_u: Optional[float] = None,
    theta_u_model: str = "plateau",
) -> SAPModelData:
    """Stamp Lignos–Krawinkler ``IMKBilin`` hinge parameters onto the SAC sections.

    Computes one :class:`~fea_toolkit.benchmarks.imk_calibration.SteelIMKParams`
    per moment-frame section from the member's *physical* AISC geometry and
    stamps the flattened ``imk_params`` dict onto the section, which the builder's
    ``hinge_backbone="imk"`` path reads (see :func:`builder_config`).  Columns use
    the Lignos et al. (2019) wide-flange equations with ``column_axial_ratio``
    (``Pg/Pye``); beams use Lignos & Krawinkler (2011).  The leaning column stays
    elastic (no ``imk_params``).

    Geometry (``D``/``BF``/``TF``/``TW``/``Z33``/``I22``/``A``) is read from the
    AISC table via :func:`fea_toolkit.model.steel_sections.get_section_properties`,
    with ``ry = sqrt(I22/A)``.  The representative member length ``L`` is the
    longest frame element assigned to the section (exact for the 3-story model,
    whose members of one section share a length); the beam unbraced length is
    ``beam_unbraced_factor * L`` (default 1.0 — no intermediate lateral bracing, a
    conservative first cut).

    Args:
        md: SAC model, after :func:`enrich_display_sections` (the moment-frame
            sections carry the ``SteelCol`` / ``SteelBeam`` material).
        building: 3, 9 or 20.  Only 3 is wired today.
        column_axial_ratio: Gravity axial-load ratio ``Pg/Pye`` for columns
            (``0`` = pure flexure, the no-axial-load envelope).  Superseded
            per-section by *column_axial_ratios* when that is given.
        column_axial_ratios: Optional ``{section_name: Pg/Pye}`` override, applied
            per section (wins over the scalar *column_axial_ratio* for any
            section it names).  Typically :func:`derive_column_axial_ratios`
            (the tributary FEMA 355C seismic-weight estimate) — the benchmark
            matching convention — with :func:`gravity_axial_ratios` as the
            independent full-solve cross-check.
        beam_unbraced_factor: ``Lb / L`` for beams (default 1.0).
        theta_u: Explicit ``IMKBilin`` ultimate rotation (rad) override; ``None``
            defers to *theta_u_model* (benchmark calibration).  When given, the
            effective selection is recorded on ``md.imk_theta_u_model`` as
            ``"explicit:<value>"``.
        theta_u_model: Ultimate-rotation convention, one of
            :data:`~fea_toolkit.benchmarks.imk_calibration.THETA_U_MODELS`.
            Default ``"plateau"``.  Recorded on ``md.imk_theta_u_model`` as
            run-level provenance when *theta_u* is ``None`` (an explicit
            *theta_u* takes precedence and is recorded instead).

    Raises:
        NotImplementedError: For ``building`` != 3 (per-member unbraced lengths /
            column axial loads not yet wired).

    Returns:
        *md*, mutated in place.
    """
    if building != 3:
        raise NotImplementedError(
            f"IMK hinges for the {building}-story model need per-member unbraced "
            "lengths and column axial loads (not yet wired); use building=3"
        )
    from ..model.steel_sections import get_section_properties
    from .imk_calibration import lignos_krawinkler_2011

    # Representative (longest) member length per assigned section.
    lengths: dict[str, float] = {}
    for eid, sname in md.frame_assignments.items():
        elem = md.frame_elements.get(eid)
        if elem is None:
            continue
        ni = md.nodes.get(elem.node_i)
        nj = md.nodes.get(elem.node_j)
        if ni is None or nj is None:
            continue
        length = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
        if length > lengths.get(sname, 0.0):
            lengths[sname] = length

    name_map = MAT_TO_SECTION[building]
    for sname, mat_no in _mat_no_by_section(md, building).items():
        sec = md.sections.get(sname)
        if sec is None or sname not in lengths:
            continue
        is_col = sec.material == "SteelCol"
        if not is_col and sec.material != "SteelBeam":
            continue  # leaning column (elastic material) — no hinge
        props = get_section_properties(name_map[mat_no])
        if props is None:
            continue
        length = lengths[sname]
        axial = column_axial_ratio
        if column_axial_ratios is not None:
            axial = float(column_axial_ratios.get(sname, column_axial_ratio))
        params = lignos_krawinkler_2011(
            section_depth_m=props["D"],
            web_thickness_m=props["TW"],
            flange_width_m=props["BF"],
            flange_thickness_m=props["TF"],
            radius_of_gyration_m=float(np.sqrt(props["I22"] / props["A"])),
            unbraced_length_m=length if is_col else beam_unbraced_factor * length,
            member_length_m=length,
            fy_pa=COLUMN_FY if is_col else BEAM_FY,
            plastic_modulus_m3=props["Z33"],
            axial_ratio=axial if is_col else 0.0,
            column=is_col,
            theta_u=theta_u,
            theta_u_model=theta_u_model,
        )
        sec.imk_params = params.to_dict()
    # Run-level provenance: the *effective* ultimate-rotation selection.  An
    # explicit ``theta_u`` overrides the convention, so record it (with its
    # value) rather than leaving the unused ``theta_u_model`` label behind.
    if theta_u is None:
        md.imk_theta_u_model = theta_u_model
    else:
        md.imk_theta_u_model = f"explicit:{theta_u:g}"
    # Run-level provenance for the column axial-load source: per-section ratios
    # (from :func:`derive_column_axial_ratios` / a caller-supplied dict), a
    # single explicit scalar, or none (the pure-flexure envelope).
    if column_axial_ratios is not None:
        md.imk_column_axial_source = "per-section"
    elif column_axial_ratio != 0.0:
        md.imk_column_axial_source = f"explicit:{column_axial_ratio:g}"
    else:
        md.imk_column_axial_source = "none"
    return md


# ═══════════════════════════════════════════════════════════════
# 9/20-story benchmark models (struct9.m / struct20.m)
# ═══════════════════════════════════════════════════════════════
# Geometry, seismic mass and base fixity transcribed from the benchmark
# structural-data files ``local/257/_extract/struct9.m`` and ``struct20.m``
# (gitignored sources — see ``sac_member_data.py`` for the matching ``mat_tbl``
# / ``element_tbl`` transcription).  Units N, m, kg; one N-S perimeter frame.
_SAC_E = 2.0e11  # Pa — elastic modulus common to all SAC members.

_SAC_BUILDINGS: dict[int, dict[str, Any]] = {
    9: {
        # 1 basement (3.6576 m) + 5.4864 m ground + 8 × 3.9624 m.
        "heights": [3.6576, 5.4864] + [3.9624] * 8,
        "bay": 9.1440,
        # Seismic mass per beam level, bottom-up (one frame = half the building).
        "seismic_mass": [4.8159e5, 5.0349e5] + [4.9473e5] * 7 + [5.3414e5],
        "n_basements": 1,
        # 5th (gravity) bay beams are pin-ended at their far end (element_tbl
        # type 5 = fixed-pin): release M3 at J for bay index 4.
        "release_bay": 4,
    },
    20: {
        # 2 basements (3.6576 m each) + 5.4864 m ground + 19 × 3.9624 m.
        "heights": [3.6576, 3.6576, 5.4864] + [3.9624] * 19,
        "bay": 6.0960,
        "seismic_mass": [2.6561e5, 2.6561e5, 2.8166e5] + [2.7582e5] * 18 + [2.9188e5],
        "n_basements": 2,
        # Basement beams are braces (element_tbl type 3); kept flexural here — a
        # basement-only approximation that does not move the lateral modes.
        "release_bay": None,
    },
}


def _build_benchmark_model(building: int) -> SAPModelData:
    """Build the SAC 9/20-story benchmark frame from the transcribed data.

    Geometry, member schedule (``element_tbl``), trilinear backbones
    (``mat_tbl``) and seismic masses come from ``struct9.m`` / ``struct20.m``.
    The base is pinned at the bottom basement level with a lateral tie at the
    ground level (the ``Fix_node`` prescription); masses are lumped at the floor
    nodes by tributary bay width.  The rigid floor diaphragms (``slv_tbl``
    master-slave) are applied only under ``fidelity="faithful"`` (see
    :func:`build_builder`): the beam axial stiffness already ties each floor
    near-rigidly, so the explicit tie moves the base shear by ~0.004 %.
    """
    spec = _SAC_BUILDINGS[building]
    heights: list[float] = spec["heights"]
    bay: float = spec["bay"]
    n_bays = 5
    n_lines = n_bays + 1
    n_levels = len(heights)
    col_mats = COLUMN_MATERIALS[building]
    beam_mats = BEAM_MATERIALS[building]
    mat_tbl = MAT_TBLS[building]

    levels = [0.0]
    z = 0.0
    for h in heights:
        z += h
        levels.append(z)

    nodes: dict[str, Node] = {}
    tag = 1
    for j in range(n_levels + 1):
        for i in range(n_lines):
            nid = f"{j}-{i}"
            nodes[nid] = Node(node_id=nid, node_tag=tag, x=i * bay, y=0.0, z=levels[j])
            tag += 1

    # One elastic section per backbone: A = EA/E, I33 = EI1/E (strong axis),
    # I22 = EI2/E (weak axis).  The backbones carry no torsion constant, and the
    # out-of-plane DOFs (uy, rx, rz) are restrained anyway, so J is left equal to
    # I33 as an out-of-plane placeholder (same convention as the 3-story Tcl).
    sections: dict[str, Section] = {}
    for mat_no, row in mat_tbl.items():
        inertia = row.ei1 / _SAC_E
        sections[f"mat{mat_no}"] = Section(
            name=f"mat{mat_no}",
            shape="I/Wide Flange",
            material="Steel",
            A=row.ea / _SAC_E,
            I33=inertia,
            I22=row.ei2 / _SAC_E,
            J=inertia,
        )

    frames: dict[str, FrameElement] = {}
    assigns: dict[str, str] = {}
    releases: dict[str, FrameRelease] = {}
    etag = 1
    for k in range(n_levels):  # columns: storey k, line i
        for i in range(n_lines):
            eid = f"C{k}-{i}"
            frames[eid] = FrameElement(
                elem_id=eid, elem_tag=etag, node_i=f"{k}-{i}", node_j=f"{k + 1}-{i}"
            )
            assigns[eid] = f"mat{col_mats[k][i]}"
            etag += 1
    release_bay = spec.get("release_bay")
    for lvl in range(1, n_levels + 1):  # beams: level lvl, bay i
        for i in range(n_bays):
            eid = f"B{lvl}-{i}"
            frames[eid] = FrameElement(
                elem_id=eid, elem_tag=etag, node_i=f"{lvl}-{i}", node_j=f"{lvl}-{i + 1}"
            )
            assigns[eid] = f"mat{beam_mats[lvl - 1][i]}"
            if release_bay is not None and i == release_bay:
                releases[eid] = FrameRelease(eid, end_i=[0] * 6, end_j=[0, 0, 0, 0, 0, 1])
            etag += 1

    n_base = spec["n_basements"]
    restraints: dict[str, Restraint] = {}
    for j in range(n_levels + 1):
        for i in range(n_lines):
            nid = f"{j}-{i}"
            if j == 0:
                restraints[nid] = Restraint([1, 1, 1, 1, 0, 1])  # pinned base
            elif j == n_base:
                restraints[nid] = Restraint([1, 1, 0, 1, 0, 1])  # ground lateral tie
            else:
                restraints[nid] = Restraint([0, 1, 0, 1, 0, 1])  # free in-plane

    added_masses: dict[str, float] = {}
    weights = [0.5] + [1.0] * (n_lines - 2) + [0.5]
    for lvl, m in enumerate(spec["seismic_mass"], start=1):
        for i, w in enumerate(weights):
            added_masses[f"{lvl}-{i}"] = m * w / float(n_bays)

    return SAPModelData(
        nodes=nodes,
        restraints=restraints,
        materials={"Steel": Material("Steel", "Steel", E_mod=_SAC_E, G_mod=_SAC_E / 2.6, nu=0.3)},
        sections=sections,
        frame_elements=frames,
        area_elements={},
        frame_assignments=assigns,
        area_assignments={},
        groups={},
        frame_auto_mesh={},
        frame_releases=releases,
        added_masses=added_masses,
        mass_sources={"MSSSRC1": MassSource(name="MSSSRC1", masses=True)} if added_masses else {},
        units=SAC3_UNITS,
    )


def load_model(
    nonlinear: bool = True, *, building: int = 3, backbone: str = "bilinear"
) -> SAPModelData:
    """Load a SAC model (3/9/20-story); enrich for nonlinear use.

    The 3-story model is parsed from the bundled Tcl transcription
    (``sac_3story.tcl``); the 9/20-story models are built programmatically from
    the transcribed ``struct9.m`` / ``struct20.m`` data (geometry, ``mat_tbl``
    backbones, ``element_tbl`` member schedule, seismic masses).

    ``building`` and ``backbone`` are keyword-only, so the first positional
    argument selects ``nonlinear`` (``load_model(True)`` / ``load_model(False)``).

    Args:
        building: 3, 9 or 20.
        nonlinear: When True, enrich for nonlinear use (plastic moduli for the
            bilinear hinge, or the trilinear backbone).  When False, return the
            bare elastic model (the T2/B1 modal anchor's input).
        backbone: ``"bilinear"`` (default), ``"trilinear"``,
            ``"trilinear_steel02"``, ``"trilinear_clough"`` or ``"imk"``.  The
            three trilinear spellings all call :func:`enrich_trilinear` so the
            sections carry the transcribed ``mat_tbl`` backbone; they differ
            only in the hinge material the builder emits (``Hysteretic``,
            two-``Steel02``-in-parallel, or ``Hysteretic`` with
            unloading-stiffness degradation).  ``"imk"`` calls :func:`enrich_imk`
            to stamp the Lignos-Krawinkler ``IMKBilin`` ``imk_params`` onto the
            sections.

    Raises:
        ValueError: For an unknown *building* or *backbone*.
        NotImplementedError: For the bilinear 9/20-story hinge (needs per-member
            AISC ``Z33``/``depth``/``tw``, not yet transcribed).
    """
    if building == 3:
        md = parse_tcl_model(SAC3_TCL.read_text(encoding="utf-8"), SAC3_UNITS)
    elif building in (9, 20):
        md = _build_benchmark_model(building)
    else:
        raise ValueError(f"unknown SAC building {building!r} (expected 3, 9 or 20)")

    if nonlinear:
        if backbone not in (
            "bilinear",
            "trilinear",
            "trilinear_steel02",
            "trilinear_clough",
            "imk",
        ):
            raise ValueError(
                f"unknown SAC backbone {backbone!r} (expected 'bilinear', "
                "'trilinear', 'trilinear_steel02', 'trilinear_clough' or 'imk')"
            )
        if building == 3:
            enrich_nonlinear(md)
        if backbone == "imk":
            if building != 3:
                raise NotImplementedError(
                    f"IMK hinges for the {building}-story model need per-member "
                    "unbraced lengths and column axial loads (not yet wired); "
                    "use building=3"
                )
        elif backbone in ("trilinear", "trilinear_steel02", "trilinear_clough"):
            enrich_trilinear(md, building=building)
        elif building != 3:
            raise NotImplementedError(
                f"bilinear hinges for the {building}-story model need per-member "
                "AISC Z33/depth/tw (not yet transcribed); use backbone='trilinear'"
            )
        enrich_display_sections(md, building=building)
        if backbone == "imk":
            enrich_imk(md, building=building)
    return md


def builder_config(backbone: str = "bilinear", hinge_model: str = "lumped") -> dict[str, Any]:
    """The ``AnalysisBuilder`` config for the nonlinear evaluation model.

    Purely build-level choices (element type, hinge model).  The panel-zone
    choice is a *preprocessing* choice and lives in
    :func:`preprocess_config` — the builder keys off the frozen
    ``mesh_model.panel_zones`` spec instead.

    Args:
        backbone: ``"bilinear"`` (default, the M1/M2 Steel01 hinge),
            ``"trilinear"`` (the transcribed ``mat_tbl`` backbone as a
            ``Hysteretic`` hinge), ``"trilinear_steel02"`` (the same backbone
            as two ``Steel02`` springs in parallel), ``"trilinear_clough"``
            (the same ``Hysteretic`` hinge with Clough/Takeda unloading-
            stiffness degradation) or ``"imk"`` (the Lignos-Krawinkler
            ``IMKBilin`` hinge; ``imk_params`` must be stamped on the sections —
            see ``benchmarks/imk_calibration.py``).
        hinge_model: ``"lumped"`` (default, zero-length rotational springs) or
            ``"beam_with_hinges"`` (the benchmark's own concentrated-plasticity
            ``beamWithHinges`` element — ``trilinear`` / ``trilinear_steel02`` /
            ``trilinear_clough`` backbones).

    Raises:
        ValueError: For an unsupported ``hinge_model``/``backbone`` pairing
            (only ``lumped`` + ``bilinear``/``trilinear``/``imk``, and
            ``beam_with_hinges`` + ``trilinear``/``trilinear_steel02``/
            ``trilinear_clough`` are valid).
    """
    _valid_backbones = {
        "lumped": ("bilinear", "trilinear", "imk"),
        "beam_with_hinges": ("trilinear", "trilinear_steel02", "trilinear_clough"),
    }
    allowed = _valid_backbones.get(hinge_model)
    if allowed is None or backbone not in allowed:
        raise ValueError(
            f"unsupported hinge_model/backbone combination: "
            f"hinge_model={hinge_model!r} with backbone={backbone!r}; "
            f"valid pairings are lumped+{{bilinear,trilinear,imk}} and "
            f"beam_with_hinges+{{trilinear,trilinear_steel02,trilinear_clough}}"
        )
    return {
        "element_type": "elasticBeamColumn",
        "hinge_model": hinge_model,
        "hinge_backbone": backbone,
        # The trilinear Hysteretic hinge has two sharp yield corners where plain
        # Newton stalls; the SAC transient runs opt in to the line search (the
        # default elsewhere is plain Newton).
        "transient_algorithm": "NewtonLineSearch",
        # The benchmark base shear sums only the above-ground storeys, so opt in
        # to the runner's below-ground exclusion (drops free basements below the
        # ground tie).
        "transient_exclude_below_ground": True,
    }


def preprocess_config(panel_zone: bool = False) -> dict[str, Any]:
    """The ``Preprocessor`` config for the nonlinear evaluation model.

    Args:
        panel_zone: ``True`` for the M2 model (adds the Krawinkler & Gupta panel
            zone); ``False`` for M1 (centreline joint, bilinear hinges only).
    """
    cfg: dict[str, Any] = {"element_type": "elasticBeamColumn"}
    if panel_zone:
        cfg["panel_zone_model"] = "krawinkler_gupta"
    return cfg


def _floor_ux_tie_groups(building: int) -> list[list[str]]:
    """The ``slv_tbl`` horizontal floor ties for a SAC frame, as node-ID groups.

    ``struct#.m``'s ``slv_tbl`` ties every node at a floor level to that
    level's master node in the horizontal (X) DOF only.  The 9/20-story models
    lay their nodes out as ``{level}-{line}`` (``level`` 0 = base .. ``n_levels``
    = roof, ``line`` 0..5), with the middle line (2) as the master.  The base
    level (0) and the ground lateral-tie level (``n_basements``) are restrained
    and therefore excluded — matching the benchmark's table (the 9-story ties
    levels 2..10; the 20-story ties level 1 and levels 3..22).

    Returns:
        One group per tied level, ``[master_id, slave_id, ...]``.
    """
    spec = _SAC_BUILDINGS[building]
    n_levels = len(spec["heights"])
    n_base = spec["n_basements"]
    groups: list[list[str]] = []
    for j in range(1, n_levels + 1):
        if j == n_base:  # ground lateral-tie level (ux already restrained)
            continue
        master = f"{j}-2"
        slaves = [f"{j}-{i}" for i in range(6) if i != 2]
        groups.append([master, *slaves])
    return groups


def build_builder(
    panel_zone: bool = False,
    nonlinear: bool = True,
    *,
    building: int = 3,
    backbone: str = "bilinear",
    hinge_model: str = "lumped",
    fidelity: str = "simplified",
) -> AnalysisBuilder:
    """Build a SAC model (3/9/20-story, elastic or nonlinear) into an
    ``AnalysisBuilder``.

    The returned builder has already had :meth:`AnalysisBuilder.build_domain`
    called, so it is ready to run an analysis.  The M2 variant preprocesses with
    ``panel_zone_model='krawinkler_gupta'`` (a separate ``MeshModel``), keeping
    the builder read-only.

    ``building`` (and the remaining arguments) are keyword-only, so the first
    positional argument selects ``panel_zone`` and the second ``nonlinear``
    (``build_builder(True)`` / ``build_builder(False, True)``).

    Args:
        building: 3, 9 or 20 (see :func:`load_model`).
        panel_zone: ``True`` for the M2 model (adds the Krawinkler & Gupta panel
            zone); ``False`` for M1.
        nonlinear: ``True`` for the nonlinear evaluation models (bilinear
            hinges); ``False`` for the elastic anchor.
        backbone: ``"bilinear"`` (default, the M1/M2 Steel01 hinge),
            ``"trilinear"`` (the transcribed ``mat_tbl`` backbone as a
            ``Hysteretic`` hinge), ``"trilinear_steel02"`` (the same backbone
            as two ``Steel02`` springs in parallel), ``"trilinear_clough"``
            (the same ``Hysteretic`` hinge with Clough/Takeda unloading-
            stiffness degradation) or ``"imk"`` (the Lignos-Krawinkler
            ``IMKBilin`` hinge; ``imk_params`` must be stamped on the sections —
            see ``benchmarks/imk_calibration.py``).
        hinge_model: ``"lumped"`` (default; ``backbone`` may be ``bilinear``,
            ``trilinear`` or ``imk``) or ``"beam_with_hinges"`` (the benchmark's
            own concentrated-plasticity element; ``backbone`` may be
            ``trilinear``, ``trilinear_steel02`` or ``trilinear_clough``).
        fidelity: ``"simplified"`` (default) or ``"faithful"`` — the explicit
            ``struct#.m`` master-slave floor ties (``slv_tbl``).  Off by
            default: the ties move the base shear by ~0.004 % (the beam axial
            stiffness already ties each floor near-rigidly) while making the
            transient ~35× slower under the ``Transformation`` handler.

    Raises:
        ValueError: For an unknown *fidelity*, or for ``panel_zone=True`` with
            ``fidelity='faithful'`` (the faithful ``slv_tbl`` floor ties
            conflict with panel-zone preprocessing).
    """
    if fidelity not in ("simplified", "faithful"):
        raise ValueError(f"unknown fidelity {fidelity!r} (expected 'simplified' or 'faithful')")
    if panel_zone and fidelity == "faithful":
        raise ValueError(
            "panel_zone=True is incompatible with fidelity='faithful': the faithful "
            "slv_tbl floor ties reference the column (joint) node IDs, which "
            "panel-zone preprocessing re-points the beams away from (onto *_pz "
            "nodes); the resulting chained equalDOF constraints yield an incorrect "
            "fundamental frequency (verified on the 3-story frame: "
            "f1 ≈ 7.2 Hz instead of ≈ 0.94 Hz)."
        )
    md = load_model(building=building, nonlinear=nonlinear, backbone=backbone)
    mesh = preprocess_model(md, preprocess_config(panel_zone=panel_zone))
    cfg = builder_config(backbone, hinge_model) if nonlinear else {}
    # The benchmark's ``slv_tbl`` horizontal floor ties, opt-in under
    # ``fidelity="faithful"``.  Applied inside ``build_domain`` via the
    # ``floor_ux_ties`` config, so they survive the transient runner's
    # per-record domain rebuilds.  They are off by default: the beam axial
    # stiffness already ties each floor near-rigidly, so the explicit tie moves
    # the base shear by ~0.004% while the ``equalDOF`` constraints make the
    # transient ~35× slower (the ``Transformation`` handler condenses each
    # master-slave tie).
    ties: list[list[str]] = []
    if fidelity == "faithful":
        if building == 3:
            ties = [list(g["nodes"]) for g in _FAITHFUL_DIAPHRAGMS]
        elif building in (9, 20):
            ties = _floor_ux_tie_groups(building)
    if ties:
        cfg["floor_ux_ties"] = ties
    b = AnalysisBuilder(mesh, cfg)
    b.build_domain()
    return b


def load_benchmark_record(path: str, dt: float = SAC_DT_CAL) -> tuple[np.ndarray, float]:
    """Read a benchmark ground-motion record, resampled to the integration step.

    The four benchmark signals are archived at their native sampling; the
    benchmark integrates them at ``dt_cal = 0.005`` s (``struct#.m``, Newmark
    β=1/4 γ=1/2).  This helper reads a record and linearly resamples it to
    *dt* so the transient analysis matches the benchmark's integration step.

    Args:
        path: Path to the CSV record (``time,accel`` columns, m/s²).
        dt: Target integration step (s); defaults to :data:`SAC_DT_CAL`.

    Returns:
        ``(accel, dt)`` — the resampled acceleration samples (m/s²) and the
        integration step (s).
    """
    from ..io.ground_motion import read_time_history_csv

    times, accel = read_time_history_csv(path)
    dt0 = float(times[1] - times[0])
    if abs(dt0 - dt) > 1e-9:
        t_new = np.arange(0.0, times[-1] + dt, dt)
        accel = np.interp(t_new, times, accel)
    return accel, float(dt)


def peak_x(res: dict[str, Any]) -> float:
    """Peak |X| displacement over all nodes (≈ the roof peak for a shear frame)."""
    peak = 0.0
    for arr in res["displacements"].values():
        if arr.size:
            peak = max(peak, float(np.max(np.abs(arr[:, 0]))))
    return peak


__all__ = [
    "BEAM",
    "BEAM_FY",
    "COLUMN",
    "COLUMN_FY",
    "SAC3_TCL",
    "SAC3_UNITS",
    "SAC_DT_CAL",
    "build_builder",
    "builder_config",
    "derive_column_axial_ratios",
    "enrich_display_sections",
    "enrich_imk",
    "enrich_nonlinear",
    "enrich_trilinear",
    "gravity_axial_ratios",
    "load_benchmark_record",
    "load_model",
    "peak_x",
    "preprocess_config",
]
