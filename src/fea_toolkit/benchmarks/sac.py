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

Both variants use the member schedule from the official benchmark data
(``struct3.m``): W14x257 / W14x311 columns, per-level beams (W33x118 / W30x116 /
W24x68) with a W21x44 fourth bay, and a leaning column.  The lateral model uses
bare-steel beam inertia (the composite slab is a gravity-only effect).

The benchmark's exact hinge / panel-zone parameters are not redistributable, so
the plastic moduli and yield strengths below are computed from AISC tables
(see ``docs/sac_nonlinear_benchmark.md``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..io.tcl_importer import parse_tcl_model
from ..model.sap_data import Material, SAPModelData
from ..opensees.analysis_builder import AnalysisBuilder
from ..opensees.preprocessor import preprocess_model
from .sac_member_data import MAT_TBL_3

#: Bundled Tcl transcription of the SAC 3-story N-S perimeter frame.
SAC3_TCL = Path(__file__).with_name("sac_3story.tcl")

#: SI unit system of the bundled model (N, m, kg).
SAC3_UNITS: dict[str, str] = {"F": "N", "L": "m", "T": "C"}

# ── SAC member schedule (SI) ──
# From the official benchmark model data (Ohtori et al. 2004, NEES archive
# resource 257, `struct3.m`).  Columns are 345 MPa (50 ksi); beams 248 MPa
# (36 ksi).  The lateral model uses bare-steel beam inertia and a rigid axial
# (EA = 1e10) for the floor diaphragm — the composite slab is gravity-only.
COLUMN_FY = 345e6
BEAM_FY = 248e6

#: Moment-frame columns keyed by area (m²).  The far-line *leaning* column
#: (models the interior gravity framing) is absent — it stays elastic.
_COLUMNS_BY_AREA = {
    0.048759: {"Z33": 7.34e-3, "depth": 0.417, "tw": 0.0435},  # W14x257
    0.05895: {"Z33": 9.88e-3, "depth": 0.437, "tw": 0.0488},  # W14x311
}

#: Beams keyed by strong-axis inertia (m⁴) — rigid axial A = 0.05 m².
_BEAMS_BY_I = {
    2.455e-3: {"Z33": 6.80e-3, "depth": 0.836, "tw": 0.0140},  # W33x118
    2.0514e-3: {"Z33": 6.194e-3, "depth": 0.762, "tw": 0.0144},  # W30x116
    7.6145e-4: {"Z33": 2.524e-3, "depth": 0.602, "tw": 0.0105},  # W24x68
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
COLUMN = {"Z33": 7.34e-3, "depth": 0.417, "tw": 0.0435, "fy": COLUMN_FY}  # W14x257
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


def enrich_trilinear(md: SAPModelData) -> SAPModelData:
    """Stamp the Ohtori trilinear backbones (``mat_tbl``) onto the SAC sections.

    Maps each moment-frame section to its ``Mat_no`` (columns by area, beams by
    inertia — the same keys as :func:`enrich_nonlinear`) and writes the
    trilinear backbone ``(EI1, EI2, EI3, d1, d2)`` onto the section so the
    builder's ``hinge_backbone='trilinear'`` path can read it.  The leaning
    column (material 6) and the rigid-axial link bay (material 7) are included.
    """
    roles = _frame_element_roles(md)
    for eid, sname in md.frame_assignments.items():
        sec = md.sections.get(sname)
        if sec is None:
            continue
        if roles.get(eid) == "col":
            mat_no = _match_props(_COLUMN_MAT_NO, sec.A, 1e-6)
            if mat_no is None:
                mat_no = _LEANING_MAT_NO  # leaning (gravity) column
        else:
            mat_no = _match_props(_BEAM_MAT_NO, sec.I33, 1e-8)
            if mat_no is None:
                continue
        row = MAT_TBL_3[mat_no]
        sec.ei1, sec.ei2, sec.ei3 = row.ei1, row.ei2, row.ei3
        sec.d1, sec.d2 = row.d1, row.d2
    return md


def load_model(nonlinear: bool = True) -> SAPModelData:
    """Load the SAC model from the bundled Tcl; enrich for nonlinear use.

    Args:
        nonlinear: When True, call :func:`enrich_nonlinear` so the model carries
            plastic moduli and yield strengths.  When False, return the bare
            elastic model (the T2 modal anchor's input).
    """
    md = parse_tcl_model(SAC3_TCL.read_text(encoding="utf-8"), SAC3_UNITS)
    if nonlinear:
        enrich_nonlinear(md)
    return md


def builder_config() -> dict[str, Any]:
    """The ``AnalysisBuilder`` config for the nonlinear evaluation model.

    Purely build-level choices (element type, hinge model).  The panel-zone
    choice is a *preprocessing* choice and lives in
    :func:`preprocess_config` — the builder keys off the frozen
    ``mesh_model.panel_zones`` spec instead.
    """
    return {
        "element_type": "elasticBeamColumn",
        "hinge_model": "lumped",
        "hinge_backbone": "bilinear",
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


def _add_faithful_diaphragms(mesh: Any) -> None:
    """Emit the ``struct3.m`` master-slave rigid floor diaphragms.

    ``struct3.m``'s ``slv_tbl`` ties each floor's five nodes' horizontal (X) DOF
    to the line-1 node (an ``equalDOF`` master-slave, not a full rigid
    diaphragm).  Called after :meth:`AnalysisBuilder.build_domain`, before any
    analysis runs.

    Elastic-only: in the nonlinear (hinged) model the diaphragm's ``equalDOF``
    chains with each lumped hinge's translation tie (``hinge.X → floor.X →
    master.X``), which the ``Transformation`` handler mis-resolves — the same
    failure class as the M2 panel-zone degeneracy.  The effect is small anyway:
    the rigid-axial beams already approximate the diaphragm (faithful elastic T1
    ≈ simplified T1 ≈ 0.99 s).
    """
    import openseespy.opensees as ops

    for group in _FAITHFUL_DIAPHRAGMS:
        node_ids = group["nodes"]
        master = mesh.nodes.get(node_ids[0])
        if master is None:
            continue
        for nid in node_ids[1:]:
            nd = mesh.nodes.get(nid)
            if nd is None:
                continue
            ops.equalDOF(master.node_tag, nd.node_tag, 1)


def build_builder(
    panel_zone: bool = False, *, nonlinear: bool = True, fidelity: str = "simplified"
) -> AnalysisBuilder:
    """Build the SAC model (elastic, or nonlinear M1/M2) into an ``AnalysisBuilder``.

    The returned builder has already had :meth:`AnalysisBuilder.build_domain`
    called, so it is ready to run an analysis.  The M2 variant preprocesses with
    ``panel_zone_model='krawinkler_gupta'`` (a separate ``MeshModel``), keeping
    the builder read-only.

    Args:
        panel_zone: ``True`` for the M2 model (adds the Krawinkler & Gupta panel
            zone); ``False`` for M1.
        nonlinear: ``True`` for the nonlinear evaluation models (bilinear
            hinges); ``False`` for the elastic (T2) anchor.
        fidelity: ``"simplified"`` (default) — the floor diaphragm is implicit
            (rigid-axial beams); or ``"faithful"`` — the rigid floor diaphragms
            are explicit, matching ``struct3.m``'s master-slave table.
    """
    md = load_model(nonlinear=nonlinear)
    mesh = preprocess_model(md, preprocess_config(panel_zone=panel_zone))
    cfg = builder_config() if nonlinear else {}
    b = AnalysisBuilder(mesh, cfg)
    b.build_domain()
    if fidelity == "faithful":
        _add_faithful_diaphragms(mesh)
    return b


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
    "build_builder",
    "builder_config",
    "enrich_nonlinear",
    "enrich_trilinear",
    "load_model",
    "peak_x",
    "preprocess_config",
]
