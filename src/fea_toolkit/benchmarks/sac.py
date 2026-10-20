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

The benchmark's exact hinge / panel-zone parameters are not redistributable, so
the plastic moduli and yield strengths below are computed from AISC tables
(see ``docs/sac_nonlinear_benchmark.md``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from ..io.tcl_importer import parse_tcl_model
from ..model.sap_data import Material, SAPModelData
from ..opensees.analysis_builder import AnalysisBuilder
from ..opensees.preprocessor import preprocess_model

#: Bundled Tcl transcription of the SAC 3-story N-S perimeter frame.
SAC3_TCL = Path(__file__).with_name("sac_3story.tcl")

#: SI unit system of the bundled model (N, m, kg).
SAC3_UNITS: dict[str, str] = {"F": "N", "L": "m", "T": "C"}

# ── AISC member properties (SI) ──
# W24x68  (columns): Zx = 154 in^3 = 2.524e-3 m^3, d = 23.7 in = 0.602 m,
#                    tw = 0.415 in = 0.0105 m,  fy = 345 MPa.
# W30x116 (beams):   Zx = 378 in^3 = 6.194e-3 m^3, d = 30.0 in = 0.762 m,
#                    tw = 0.565 in = 0.0144 m,  fy = 248 MPa.
COLUMN = {"Z33": 2.524e-3, "depth": 0.602, "tw": 0.0105, "fy": 345e6}
BEAM = {"Z33": 6.194e-3, "depth": 0.762, "tw": 0.0144, "fy": 248e6}


def enrich_nonlinear(md: SAPModelData) -> SAPModelData:
    """Enrich the elastic SAC model with plastic moduli and yield strengths.

    Mutates and returns *md*: the two ``I/Wide Flange`` sections are matched by
    area (W24x68 column vs W30x116 beam) and given their AISC plastic modulus
    ``Z33``, depth, web thickness and a dedicated steel material whose ``Fy``
    sets the bilinear-hinge yield moment ``My = Zx·fy``.
    """
    md.materials["SteelCol"] = Material("SteelCol", "Steel", E_mod=2.0e11, Fy=COLUMN["fy"])
    md.materials["SteelBeam"] = Material("SteelBeam", "Steel", E_mod=2.0e11, Fy=BEAM["fy"])
    for sec in md.sections.values():
        if abs(sec.A - 1.290e-2) < 1e-6:  # W24x68 column
            sec.Z33 = COLUMN["Z33"]
            sec.depth = COLUMN["depth"]
            sec.tw = COLUMN["tw"]
            sec.material = "SteelCol"
        elif abs(sec.A - 2.206e-2) < 1e-6:  # W30x116 beam
            sec.Z33 = BEAM["Z33"]
            sec.depth = BEAM["depth"]
            sec.tw = BEAM["tw"]
            sec.material = "SteelBeam"
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


def build_builder(panel_zone: bool = False, *, nonlinear: bool = True) -> AnalysisBuilder:
    """Build the SAC model (elastic, or nonlinear M1/M2) into an ``AnalysisBuilder``.

    The returned builder has already had :meth:`AnalysisBuilder.build_domain`
    called, so it is ready to run an analysis.  The M2 variant preprocesses with
    ``panel_zone_model='krawinkler_gupta'`` (a separate ``MeshModel``), keeping
    the builder read-only.
    """
    md = load_model(nonlinear=nonlinear)
    mesh = preprocess_model(md, preprocess_config(panel_zone=panel_zone))
    cfg = builder_config() if nonlinear else {}
    b = AnalysisBuilder(mesh, cfg)
    b.build_domain()
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
    "COLUMN",
    "SAC3_TCL",
    "SAC3_UNITS",
    "build_builder",
    "builder_config",
    "enrich_nonlinear",
    "load_model",
    "peak_x",
    "preprocess_config",
]
