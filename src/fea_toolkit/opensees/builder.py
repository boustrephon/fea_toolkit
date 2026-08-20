# fea_toolkit/opensees/builder.py

"""Standalone Tcl export functions for SAPModelData.

These functions export an OpenSeesPy or SAP2000 model directly to
Xara-compatible Tcl scripts, bypassing the domain-construction pipeline.

Available functions:

* :func:`export_model_to_tcl` — Full model Tcl export (nodes, restraints,
  materials, sections, frame/shell elements, loads, mass).
* :func:`tcl_materials_and_sections` — Generate only the Tcl commands for
  materials and sections.
* :func:`pushover_tcl` — Generate Tcl commands for a displacement-controlled
  pushover analysis.

The :class:`OpenSeesBuilder` class previously defined here has been
removed.  Use the two-stage pipeline instead::

    from fea_toolkit.opensees.preprocessor import preprocess_model
    from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

    mm = preprocess_model(md)
    builder = AnalysisBuilder(mm, config)
    builder.build_domain()
    builder.create_loads({"DEAD": 1.0})
    results = builder.run_static_analysis()
"""

import math
import re
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Optional

import numpy as np

from ..model.sap_data import SAPModelData
from ..utils import (
    DEFAULT_E_S_PA,
    DEFAULT_EPS_C,
    DEFAULT_EPS_CC,
    DEFAULT_FC_PA,
    DEFAULT_FSAM_CONC_EPCC,
    DEFAULT_FSAM_CONC_ET,
    DEFAULT_FSAM_CONC_FPC_PA,
    DEFAULT_FSAM_CONC_FT_PA,
    DEFAULT_FSAM_CONC_RC,
    DEFAULT_FSAM_CONC_RT,
    DEFAULT_FSAM_CONC_XCRN,
    DEFAULT_FSAM_CONC_XCRP,
    DEFAULT_FSAM_STEEL_B,
    DEFAULT_FSAM_STEEL_CR1,
    DEFAULT_FSAM_STEEL_CR2,
    DEFAULT_FSAM_STEEL_R0,
    DEFAULT_FY_REBAR_PA,
    DEFAULT_FY_STEEL_PA,
    DEFAULT_G_MOD_FRAC,
    DEFAULT_RHO_MC_SI,
    DEFAULT_RHO_MS_SI,
    RC_NO_TIE_CONFINEMENT_FACTOR,
    RC_NO_TIE_EPSC_FACTOR,
    g_from_units,
    mass_density_scale_factor,
    stress_scale_factor,
)

if TYPE_CHECKING:
    from ..model.mesh_model import MeshModel


# ═══════════════════════════════════════════════════════════════════
# Standalone Tcl export functions
# These were extracted from the deprecated OpenSeesBuilder class and
# remain available for direct import.
# ═══════════════════════════════════════════════════════════════════


def export_model_to_tcl(
    model_data: "SAPModelData",
    # Also accepts MeshModel (nd_materials/layered_shell_sections are optional).
    path: str,
    lib_path: str = "",
    ndm: int = 3,
    ndf: int = 6,
    tcl_prefix: str = "",
    tcl_suffix: str = "",
    config: Optional[dict[str, Any]] = None,
) -> None:
    """Export a SAP model directly to a Xara-compatible Tcl script.

    This is a direct translation of the structured ``SAPModelData`` into
    Tcl commands, avoiding the scoping issues that arise when replaying
    flat ``ops.*`` call sequences.

    When *config* is provided with ``create_fiber_sections=True``,
    nonlinear materials and fiber sections are automatically
    generated as part of the Tcl output (placed at the end of the
    preamble, before any user-supplied *tcl_prefix*).

    The generated Tcl file can be run via :class:`XaraTclRunner`::

        from fea_toolkit.opensees.recorder import XaraTclRunner
        from fea_toolkit.opensees.builder import export_model_to_tcl

        export_model_to_tcl(md, "model.tcl")
        runner = XaraTclRunner()
        ret, stdout = runner.run("model.tcl")

    To add analysis commands, use *tcl_suffix* (appended before
    ``wipe``)::

        from fea_toolkit.opensees.builder import pushover_tcl

        tcl = pushover_tcl(
            control_node=8, dof=2, max_disp=0.1,
            lateral_loads={5: (0,10000,0), 6: (0,10000,0),
                           7: (0,10000,0), 8: (0,10000,0)},
            base_node_tags=[1],
        )
        export_model_to_tcl(md, "wall.tcl", tcl_suffix=tcl)

    Args:
        model_data: :class:`~fea_toolkit.model.sap_data.SAPModelData` or
            :class:`~fea_toolkit.model.mesh_model.MeshModel` to export.
            When a ``MeshModel`` is passed, the preprocessed topology
            (split frames, meshed areas, subdivided braces) is written
            to Tcl rather than the raw SAP2000 data.
        path: Output ``.tcl`` file path.
        lib_path: Path to ``libOpenSeesRT.dylib``.
        ndm: Spatial dimensions (default 3).
        ndf: DOFs per node (default 6).
        tcl_prefix: Tcl commands inserted after the model preamble
            (e.g. for nDMaterial definitions before sections).
        tcl_suffix: Tcl commands appended before ``wipe``
            (e.g. for analysis, recorders, results output).
        config: Builder config dict.  When provided with
            ``create_fiber_sections=True``, nonlinear materials
            and fiber sections are auto-generated.  When
            ``geom_transf_type`` is ``"PDelta"`` or
            ``"Corotational"``, the corresponding geometric
            transformation is used for frame elements.
    """
    if not lib_path:
        try:
            import os as _os

            import opensees as _xara_ops

            lib_dir = _os.path.dirname(_xara_ops.__file__)
            for ext in (".dylib", ".so"):
                cand = _os.path.join(lib_dir, f"libOpenSeesRT{ext}")
                if _os.path.exists(cand):
                    lib_path = cand
                    break
        except ImportError:
            lib_path = "libOpenSeesRT.dylib"

    lines = [
        "# Xara/OpenSeesRT Tcl script -- exported by fea_toolkit",
        f"load {{{lib_path}}}",
        f"model Basic -ndm {ndm} -ndf {ndf}",
        "",
        "# ── Nodes ──",
    ]

    # Map SAP string IDs to integer tags for Tcl compatibility
    _mat_tag: dict[str, int] = {}
    _sec_tag: dict[str, int] = {}
    for i, mn in enumerate(model_data.materials, start=1):
        _mat_tag[mn] = i
    mat_count = max(len(model_data.materials), 1)
    sec_tag_offset = mat_count + 1
    for i, sn in enumerate(model_data.sections, start=sec_tag_offset):
        _sec_tag[sn] = i

    # Nodes
    for nid, nd in model_data.nodes.items():
        lines.append(f"node {nd.node_tag} {nd.x:g} {nd.y:g} {nd.z:g}")

    # Restraints
    restraints_added = False
    for nid, r in model_data.restraints.items():
        if not restraints_added:
            lines.append("")
            lines.append("# ── Restraints ──")
            restraints_added = True
        nd = model_data.nodes.get(nid)
        if nd is None:
            continue
        tags = " ".join(str(int(x)) for x in r.dofs)
        lines.append(f"fix {nd.node_tag} {tags}")

    # Materials (values guaranteed non-None by SAPModelData.apply_material_defaults)
    #
    # FSAM-referenced materials are emitted as ConcreteCM / Steel02 so the
    # fixed-strut-angle wall model can resolve getCrackingStrain() at
    # runtime (Concrete01/Steel01 cannot).  All other materials keep the
    # legacy Concrete01/Steel01 output.  Config keys follow the same
    # flat SI-scaled convention as AnalysisBuilder.
    #
    # The MVLEM_3D wall's shear-spring ("shear") and interior-dummy
    # ("dummy") materials deliberately keep those legacy laws here rather
    # than the ElasticPP / tiny-E Elastic used by the OpenSeesPy
    # AnalysisBuilder path: this Tcl script targets Xara's libOpenSeesRT
    # runtime, whose verified material set is Concrete01/Steel02/
    # ConcreteCM/SteelMPF (see examples/verify_openseespy.py) — it does
    # not ship ElasticPP.
    _fsam_refs: set = set()
    for _nd in getattr(model_data, "nd_materials", {}).values():
        if _nd.material_type == "FSAM":
            _fsam_refs.update(_nd.fsam_referenced_material_names())
    _sf_tcl = stress_scale_factor(model_data.units)

    if model_data.materials:
        lines.append("")
        lines.append("# ── Materials ──")
        for mat_name, mat in model_data.materials.items():
            tag = _mat_tag[mat_name]
            if mat_name in _fsam_refs:
                # FSAM concrete law (ConcreteCM) — getCrackingStrain() required.
                if mat.type and "concrete" in mat.type.lower():
                    _fpc_raw = (
                        config.get("fsam_conc_fpc_override")
                        if config and config.get("fsam_conc_fpc_override") is not None
                        else None
                    )
                    _fpc = mat.Fc if mat.Fc and mat.Fc > 0 else DEFAULT_FSAM_CONC_FPC_PA * _sf_tcl
                    fpc = float(float(_fpc_raw) * _sf_tcl) if _fpc_raw is not None else float(_fpc)
                    _ft_raw = (
                        config.get("fsam_conc_ft_override")
                        if config and config.get("fsam_conc_ft_override") is not None
                        else None
                    )
                    ft = (
                        float(float(_ft_raw) * _sf_tcl)
                        if _ft_raw is not None
                        else float(DEFAULT_FSAM_CONC_FT_PA * _sf_tcl)
                    )
                    # Resolve FSAM concrete strain/softening defaults.
                    # ``config.get`` returns ``Any``; wrap in float() to
                    # narrow for the static checker (values are always numeric).
                    _cfg = config or {}
                    _epcc = float(_cfg.get("fsam_conc_epcc", DEFAULT_FSAM_CONC_EPCC))
                    _rc = float(_cfg.get("fsam_conc_rc", DEFAULT_FSAM_CONC_RC))
                    _xcrn = float(_cfg.get("fsam_conc_xcrn", DEFAULT_FSAM_CONC_XCRN))
                    _et = float(_cfg.get("fsam_conc_et", DEFAULT_FSAM_CONC_ET))
                    _rt = float(_cfg.get("fsam_conc_rt", DEFAULT_FSAM_CONC_RT))
                    _xcrp = float(_cfg.get("fsam_conc_xcrp", DEFAULT_FSAM_CONC_XCRP))
                    # ConcreteCM uses the negative-compression convention
                    # (matching the verified SFI_MVLEM_3D probe): fpc, epcc,
                    # and xcrn are NEGATIVE.  Positive magnitudes break the
                    # FSAM damage-coefficient initialisation when an
                    # SFI_MVLEM_3D element consumes the material.
                    lines.append(
                        f"uniaxialMaterial ConcreteCM {tag} {-abs(fpc):g} "
                        f"{-abs(_epcc):g} {mat.E_mod:g} {_rc:g} "
                        f"{-abs(_xcrn):g} {ft:g} {_et:g} {_rt:g} {_xcrp:g}"
                    )
                    continue
                # FSAM steel law (Steel02).
                fy = (
                    config.get("fsam_steel_Fy_override")
                    if config and config.get("fsam_steel_Fy_override") is not None
                    else None
                )
                fy = fy * _sf_tcl if fy is not None else (mat.Fy or DEFAULT_FY_REBAR_PA * _sf_tcl)
                es = (
                    config.get("fsam_steel_Es_override")
                    if config and config.get("fsam_steel_Es_override") is not None
                    else None
                )
                es = es * _sf_tcl if es is not None else mat.E_mod
                _b = (
                    config.get("fsam_steel_b")
                    if config and config.get("fsam_steel_b") is not None
                    else DEFAULT_FSAM_STEEL_B
                )
                _R0 = (
                    config.get("fsam_steel_R0")
                    if config and config.get("fsam_steel_R0") is not None
                    else DEFAULT_FSAM_STEEL_R0
                )
                _cR1 = (
                    config.get("fsam_steel_cR1")
                    if config and config.get("fsam_steel_cR1") is not None
                    else DEFAULT_FSAM_STEEL_CR1
                )
                _cR2 = (
                    config.get("fsam_steel_cR2")
                    if config and config.get("fsam_steel_cR2") is not None
                    else DEFAULT_FSAM_STEEL_CR2
                )
                lines.append(
                    f"uniaxialMaterial Steel02 {tag} {fy:g} {es:g} {_b:g} {_R0:g} {_cR1:g} {_cR2:g}"
                )
                continue
            if mat.type and "concrete" in mat.type.lower():
                Fc = mat.Fc
                epsc = mat.eFc if mat.eFc and mat.eFc > 0 else DEFAULT_EPS_C
                Fu = 0.2 * Fc
                epsu = 0.006
                lines.append(
                    f"uniaxialMaterial Concrete01 {tag} {-Fc:g} {-epsc:g} {-Fu:g} {-epsu:g}"
                )
            else:
                E_mod = mat.E_mod
                Fy = mat.Fy
                lines.append(f"uniaxialMaterial Steel01 {tag} {Fy:g} {E_mod:g} 0.01")

    # nD materials (for nonlinear shell analysis, MeshModel may not have them)
    _nd_mat_tag: dict[str, int] = {}
    _nd_materials = getattr(model_data, "nd_materials", {})
    if _nd_materials:
        lines.append("")
        lines.append("# ── nD materials (nonlinear shells) ──")
        _nd_base = max(_mat_tag.values()) + 1 if _mat_tag else 1
        for i, (nd_name, nd_mat) in enumerate(_nd_materials.items(), start=_nd_base):
            _nd_mat_tag[nd_name] = i
            lines.append(nd_mat.to_tcl(i, mat_tags=_mat_tag))
        # Wrap each nD material as PlateFiber for layered shell use.
        # FSAM is excluded — it is an nD material used directly by
        # SFI_MVLEM_3D / LayeredShell sections and cannot be wrapped as
        # PlateFromPlaneStress.
        for nd_name, nd_mat in _nd_materials.items():
            tag = _nd_mat_tag[nd_name]
            if nd_mat.material_type not in ("ElasticIsotropic", "FSAM"):
                pf_tag = tag + len(_nd_materials)
                # Out-of-plane shear modulus: honor Eout, else derive G from
                # E and nu exactly as NDMaterial.to_tcl does.
                eout = (
                    nd_mat.Eout
                    if nd_mat.Eout is not None
                    else (
                        nd_mat.E / (2.0 * (1.0 + nd_mat.nu))
                        if nd_mat.nu is not None
                        else nd_mat.E / 2.6
                    )
                )
                lines.append(f"nDMaterial PlateFromPlaneStress {pf_tag} {tag} {eout:g}")

    # Sections
    if model_data.sections:
        lines.append("")
        lines.append("# ── Frame sections ──")

        fiber_sec_names: set = set()
        if config and config.get("create_fiber_sections", False):
            # Collect names of sections that have fiber patches
            # by attempting to_fiber_patches() — catches any section
            # type that supports fiber conversion, not just a
            # hardcoded isinstance list.
            for sec_name, sec in model_data.sections.items():
                try:
                    sec.to_fiber_patches(mat_tag=1)
                    fiber_sec_names.add(sec_name)
                except NotImplementedError:
                    pass
        for sec_name, sec in model_data.sections.items():
            tag = _sec_tag[sec_name]
            # Skip Elastic if this section will be emitted as fiber
            if sec_name in fiber_sec_names:
                continue
            E_mod = 2.0e11
            mat = model_data.materials.get(sec.material)
            if mat and mat.E_mod and mat.E_mod > 0:
                E_mod = mat.E_mod
            G = mat.G_mod if mat and mat.G_mod and mat.G_mod > 0 else 0.4 * E_mod
            lines.append(
                f"section Elastic {tag} {E_mod:g} {sec.A:g} {sec.I33:g} {sec.I22:g} {G:g} {sec.J:g}"
            )

    # Frame elements
    if model_data.frame_elements:
        lines.append("")
        lines.append("# ── Frame elements ──")
        for eid, elem in model_data.frame_elements.items():
            if getattr(elem, "inactive", False):
                continue
            sec_name = model_data.frame_assignments.get(eid, "")
            if not sec_name:
                continue
            ni = model_data.nodes.get(elem.node_i)
            nj = model_data.nodes.get(elem.node_j)
            if ni is None or nj is None:
                continue
            # Geometric transformation
            dx = nj.x - ni.x
            dy = nj.y - ni.y
            vecxz = "1 0 0" if abs(dx) < 1e-12 and abs(dy) < 1e-12 else "0 0 1"
            # Use config-driven geometric transformation
            # Use a deterministic integer tag for the transformation
            transf_tag = 20000 + elem.elem_tag
            transf_type = "Linear"
            if config:
                transf_type = config.get("geom_transf_type", "Linear")
            lines.append(f"geomTransf {transf_type} {transf_tag} {vecxz}")
            sec_tag = _sec_tag.get(sec_name, sec_name)
            if (
                config
                and config.get("create_fiber_sections", False)
                and sec_name in fiber_sec_names
            ):
                # Nonlinear beam-column with fibre section
                int_tag = 10000 + elem.elem_tag
                n_int_pts = config.get("num_int_pts", 5)
                lines.append(f"beamIntegration Lobatto {int_tag} {sec_tag} {n_int_pts}")
                lines.append(
                    f"element forceBeamColumn {elem.elem_tag} "
                    f"{ni.node_tag} {nj.node_tag} {transf_tag} {int_tag}"
                )
            else:
                lines.append(
                    f"element elasticBeamColumn {elem.elem_tag} "
                    f"{ni.node_tag} {nj.node_tag} {sec_tag} {transf_tag}"
                )

    # Area elements (shells) — unique shell sections only
    if model_data.area_elements:
        lines.append("")
        lines.append("# ── Shell sections & area elements ──")

        # Map area section names to a _shell_sec_tag dict; prefer
        # LayeredShellSection if available, else ElasticMembranePlate.
        _shell_sec_tag: dict[str, int] = {}
        _all_tag_vals = (
            list(_mat_tag.values()) + list(_sec_tag.values()) + list(_nd_mat_tag.values())
        )
        _next_shell_tag = max(_all_tag_vals) + len(_nd_materials) + 1 if _all_tag_vals else 1000

        # Emit layered shell sections from model data
        for ls_name, ls_sec in (getattr(model_data, "layered_shell_sections", {})).items():
            stag = _next_shell_tag
            _next_shell_tag += 1
            _shell_sec_tag[ls_name] = stag
            lines.append(ls_sec.to_tcl(stag, _nd_mat_tag))

        # Emit ElasticMembranePlate sections for remaining area
        # sections that don't have a layered definition.
        for aid, elem in model_data.area_elements.items():
            if getattr(elem, "inactive", False):
                continue
            sec_name = model_data.area_assignments.get(aid, "")
            if not sec_name or sec_name in _shell_sec_tag:
                continue
            stag = _next_shell_tag
            _next_shell_tag += 1
            _shell_sec_tag[sec_name] = stag

        # Shell elements
        for aid, elem in model_data.area_elements.items():
            if getattr(elem, "inactive", False):
                continue
            nids = [
                str(nd.node_tag)
                for nd_id in elem.node_ids
                for nd in [model_data.nodes.get(nd_id)]
                if nd is not None
            ]
            if len(nids) < 3:
                continue
            stag = _shell_sec_tag.get(model_data.area_assignments.get(aid, ""), 1)
            nn = len(nids)
            if nn == 4:
                lines.append(f"element ShellDKGQ {elem.area_tag} " + " ".join(nids) + f" {stag}")
            elif nn == 3:
                lines.append(f"element ShellDKGT {elem.area_tag} " + " ".join(nids) + f" {stag}")

        # Wall elements — emitted after shells so inactive wall-source areas
        # below are skipped while the wall element replaces them.  Two
        # families are supported:
        #   * MVLEM_3D (material_type == "uniaxial") — per-fibre uniaxial
        #     concrete/steel names plus a single shear-spring name resolve
        #     via `_mat_tag`.
        #   * SFI_MVLEM_3D / E_SFI_MVLEM_3D — per-fibre FSAM nD material
        #     names resolve via `_nd_mat_tag` (assigned above).
        for _wid, _wall in (getattr(model_data, "wall_elements", {})).items():
            _w_nids = [
                str(model_data.nodes[_nid].node_tag)
                for _nid in _wall.node_ids
                if _nid in model_data.nodes
            ]
            if len(_w_nids) != 4:
                continue
            if getattr(_wall, "material_type", "FSAM") == "uniaxial":
                _w_conc = [
                    str(_mat_tag[_n]) for _n in (_wall.concrete_names or []) if _n in _mat_tag
                ]
                _w_steel = [str(_mat_tag[_n]) for _n in (_wall.steel_names or []) if _n in _mat_tag]
                _w_shear = _mat_tag.get(_wall.shear_name) if _wall.shear_name else None
                _w_rho = _wall.rho or [2400.0] * _wall.m
                if len(_w_conc) != _wall.m or len(_w_steel) != _wall.m or _w_shear is None:
                    print(
                        f"  ⚠ [export_model_to_tcl] wall '{_wall.elem_id}': "
                        f"uniaxial MVLEM_3D material resolution incomplete "
                        f"(concrete {len(_w_conc)}/{_wall.m}, steel "
                        f"{len(_w_steel)}/{_wall.m}, shear "
                        f"{'ok' if _w_shear is not None else 'missing'}) — "
                        f"element skipped"
                    )
                    continue
                _w_elem = getattr(_wall, "element_type", None) or "MVLEM_3D"
                _w_parts = [
                    f"element {_w_elem} {_wall.elem_tag}",
                    *_w_nids,
                    str(_wall.m),
                ]
                _w_parts.append("-thick")
                _w_parts.extend(str(v) for v in _wall.thick)
                _w_parts.append("-width")
                _w_parts.extend(str(v) for v in _wall.width)
                _w_parts.append("-rho")
                _w_parts.extend(str(v) for v in _w_rho)
                _w_parts.append("-matConcrete")
                _w_parts.extend(_w_conc)
                _w_parts.append("-matSteel")
                _w_parts.extend(_w_steel)
                _w_parts.append("-matShear")
                _w_parts.append(str(_w_shear))
                _w_parts.append("-CoR")
                _w_parts.append(str(_wall.CoR))
                if _wall.ThickMod is not None:
                    _w_parts.extend(["-ThickMod", str(_wall.ThickMod)])
                if _wall.Poisson is not None:
                    _w_parts.extend(["-Poisson", str(_wall.Poisson)])
                if _wall.Density is not None:
                    _w_parts.extend(["-Density", str(_wall.Density)])
                lines.append(" ".join(_w_parts))
                continue
            _w_mat_tags = [
                str(_nd_mat_tag[_name])
                for _name in _wall.fsam_material_names
                if _name in _nd_mat_tag
            ]
            if len(_w_mat_tags) != _wall.m:
                print(
                    f"  ⚠ [export_model_to_tcl] wall '{_wall.elem_id}': FSAM "
                    f"material resolution incomplete "
                    f"({len(_w_mat_tags)}/{_wall.m} tags) — element skipped"
                )
                continue
            _w_elem = getattr(_wall, "element_type", None) or "SFI_MVLEM_3D"
            _w_parts = [
                f"element {_w_elem} {_wall.elem_tag}",
                *_w_nids,
                str(_wall.m),
            ]
            _w_parts.append("-thick")
            _w_parts.extend(str(v) for v in _wall.thick)
            _w_parts.append("-width")
            _w_parts.extend(str(v) for v in _wall.width)
            _w_parts.append("-mat")
            _w_parts.extend(_w_mat_tags)
            _w_parts.append("-CoR")
            _w_parts.append(str(_wall.CoR))
            if _wall.ThickMod is not None:
                _w_parts.extend(["-ThickMod", str(_wall.ThickMod)])
            if _wall.Poisson is not None:
                _w_parts.extend(["-Poisson", str(_wall.Poisson)])
            if _wall.Density is not None:
                _w_parts.extend(["-Density", str(_wall.Density)])
            lines.append(" ".join(_w_parts))

    # Auto-generate nonlinear materials and fiber sections from config
    nonlinear_tcl = tcl_materials_and_sections(model_data, config)

    # Insert auto-generated nonlinear Tcl before the sections block
    first_section_idx = None
    for i, line in enumerate(lines):
        if line.startswith("# ── Frame sections"):
            first_section_idx = i
            break
    if first_section_idx is not None and nonlinear_tcl:
        lines.insert(first_section_idx, "")
        lines.insert(first_section_idx, nonlinear_tcl)

    # Insert user-provided tcl_prefix (same location)
    if tcl_prefix:
        first_section_idx = None
        for i, line in enumerate(lines):
            if line.startswith(("# ── Materials", "# ── Frame sections")):
                first_section_idx = i
                break
        if first_section_idx is not None:
            lines.insert(first_section_idx, "")
            lines.insert(first_section_idx, "# ── User-defined prefix (nD materials, etc.) ──")
            lines.insert(first_section_idx + 2, tcl_prefix)
            lines.insert(first_section_idx + 3, "")

    # Append tcl_suffix before final wipe
    if tcl_suffix:
        lines.append("")
        lines.append("# ── User-defined suffix (analysis, recorders) ──")
        lines.append(tcl_suffix)

    lines.append("")
    lines.append('puts "Model exported successfully."')
    lines.append("wipe")
    lines.append("exit")

    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


# ── Extracted module-level functions for Xara/OpenSeesRT Tcl workflow ──
# These were formerly static methods on OpenSeesBuilder.  They are
# independent of the deprecated class and work directly with SAPModelData.


def tcl_materials_and_sections(
    model_data: "SAPModelData",
    config: Optional[dict[str, Any]] = None,
) -> str:
    """Generate Tcl code for nonlinear materials and fiber sections.

    Produces the ``uniaxialMaterial`` and ``section Fiber`` commands
    needed for pushover or dynamic analysis.  Returns an empty string if
    ``create_fiber_sections`` is not enabled in *config*.

    Args:
        model_data: SAP model data with sections and materials.
        config: Builder config dict (or ``None`` to skip).

    Returns:
        Tcl commands as a string, ready to use as ``tcl_prefix``.
    """
    if config is None:
        return ""
    if not config.get("create_fiber_sections", False):
        return ""

    lines: list[str] = [
        "",
        "# ── Nonlinear materials and fiber sections ──",
        "# (generated by fea_toolkit.opensees.builder)",
    ]

    # Tag numbering scheme:
    #   materials: 1..M  (same as export_model_to_tcl)
    #   sections:  M+1 .. M+S  (same)
    #   concrete mat tags for RC: start after all section tags
    mat_count = max(len(model_data.materials), 1)
    sec_count = len(model_data.sections)
    sec_tag_offset = mat_count + 1
    next_concrete_tag = sec_tag_offset + sec_count

    # Map section name → tag
    _sec_tag: dict[str, int] = {}
    for i, sn in enumerate(model_data.sections, start=sec_tag_offset):
        _sec_tag[sn] = i

    # Compute stress unit factor from model units
    _sf = stress_scale_factor(model_data.units)

    for sec_name, sec in model_data.sections.items():
        sec_tag = _sec_tag[sec_name]
        mat = model_data.materials.get(sec.material)

        from ..model.sap_data import (
            ConcreteCircularSection,
            ConcreteRectangularSection,
            RectangularSection,
            ShellSection,
        )

        # Shell sections → elastic only (no fiber)
        if isinstance(sec, ShellSection):
            continue

        is_rc = isinstance(
            sec,
            (
                ConcreteRectangularSection,
                ConcreteCircularSection,
                RectangularSection,
            ),
        )

        if is_rc:
            # ── RC fiber section: unconfined, confined, rebar ──
            concrete_mat_tag = next_concrete_tag
            next_concrete_tag += 3

            if mat is not None:
                Fc = mat.Fc if mat.Fc and mat.Fc > 0 else DEFAULT_FC_PA * _sf
                epsc = (
                    float(mat.ss_curve.s_fc)
                    if mat.ss_curve is not None and mat.ss_curve.s_fc is not None
                    else DEFAULT_EPS_C
                )
                if epsc > 0.01:
                    epsc = DEFAULT_EPS_C

                # Confined strength: Mander confinement (when tie data is
                # present on the section) → else eFc from SAP2000 (if any)
                # → else the shared RC_NO_TIE_CONFINEMENT_FACTOR /
                # RC_NO_TIE_EPSC_FACTOR heuristic (1.25 × f'c, 2.0 × εc —
                # consistent with the OpenSees Berkeley comparison manual
                # default and the Mander-model approximation).
                fcc = Fc * RC_NO_TIE_CONFINEMENT_FACTOR
                epscc = epsc * RC_NO_TIE_EPSC_FACTOR
                ecu_cc = 0.02
                fc_method = getattr(sec, "fiber_confinement", None)
                _conf_dict = None
                if callable(fc_method):
                    tie_fy = getattr(sec, "tie_fy", None) or 0.0
                    if tie_fy <= 0:
                        # Resolve tie_fy from the transverse rebar material
                        # (RebarMatT) first, then the longitudinal rebar
                        # material (RebarMatL) as a fallback.
                        _tie_mat_name = getattr(sec, "tie_rebar_mat", None) or getattr(
                            sec, "rebar_material", None
                        )
                        _tie_mat = (
                            model_data.materials.get(_tie_mat_name) if _tie_mat_name else None
                        )
                        if _tie_mat is not None:
                            tie_fy = getattr(_tie_mat, "Fy", 0.0) or 0.0
                    try:
                        _conf_val = fc_method(Fc, tie_fy)
                        _conf_dict = _conf_val if isinstance(_conf_val, dict) else None
                    except Exception:
                        _conf_dict = None
                    if _conf_dict is not None:
                        fcc = _conf_dict.get("fcc", fcc)
                        epscc = _conf_dict.get("ecc", epscc)
                        ecu_cc = _conf_dict.get("ecu", ecu_cc)
                # A None result from fiber_confinement (or a non-callable
                # method) means Mander confinement data is unavailable —
                # fall back to the SAP2000 eFc / ss_curve.s_cap values before
                # retaining the shared confinement heuristic defaults
                # (RC_NO_TIE_CONFINEMENT_FACTOR / RC_NO_TIE_EPSC_FACTOR).
                if _conf_dict is None:
                    if mat.eFc and mat.eFc > 0:
                        fcc = mat.eFc
                    _scc = (
                        float(mat.ss_curve.s_cap)
                        if mat.ss_curve is not None and mat.ss_curve.s_cap is not None
                        else None
                    )
                    if _scc is not None and _scc <= 0.1:
                        epscc = _scc
                if epscc > 0.1:
                    epscc = DEFAULT_EPS_CC
                # Cap the confined spalling strain (configurable).
                _ecu_max = float(config.get("confined_ecu_max", 0.025))
                ecu_cc = min(ecu_cc, _ecu_max)

                lines.append(
                    f"uniaxialMaterial Concrete01 {concrete_mat_tag} "
                    f"{-Fc:g} {-abs(epsc):g} {-0.2 * Fc:g} {-0.006:g}"
                )
                lines.append(
                    f"uniaxialMaterial Concrete01 {concrete_mat_tag + 1} "
                    f"{-fcc:g} {-abs(epscc):g} {-0.2 * fcc:g} {-ecu_cc:g}"
                )
                # ── Steel rebar ──
                # Resolve Fy/Es in priority order:
                #   1) section's SAP2000 rebar_material (RebarMatL) lookup
                #   2) framework rebar defaults (DEFAULT_FY_REBAR_PA / E_S)
                #      scaled to model units
                _rebar_mat = None
                _rebar_mat_name = getattr(sec, "rebar_material", None)
                if _rebar_mat_name:
                    _rebar_mat = model_data.materials.get(_rebar_mat_name)
                if _rebar_mat is not None:
                    Fy = (
                        _rebar_mat.Fy
                        if _rebar_mat.Fy and _rebar_mat.Fy > 0
                        else DEFAULT_FY_REBAR_PA * _sf
                    )
                    E_mod = (
                        _rebar_mat.E_mod
                        if _rebar_mat.E_mod and _rebar_mat.E_mod > 0
                        else DEFAULT_E_S_PA * _sf
                    )
                else:
                    Fy = DEFAULT_FY_REBAR_PA * _sf
                    E_mod = DEFAULT_E_S_PA * _sf
                lines.append(
                    f"uniaxialMaterial Steel02 {concrete_mat_tag + 2} "
                    f"{Fy:g} {E_mod:g} {0.01:g} {18.5:g} {0.925:g} {0.15:g}"
                )
            else:
                _fc = DEFAULT_FC_PA * _sf
                _fcc = _fc * RC_NO_TIE_CONFINEMENT_FACTOR
                _fy = DEFAULT_FY_REBAR_PA * _sf
                _e = DEFAULT_E_S_PA * _sf
                lines.append(
                    f"uniaxialMaterial Concrete01 {concrete_mat_tag} "
                    f"{-_fc:g} {-DEFAULT_EPS_C:g} {-0.2 * _fc:g} {-0.006:g}"
                )
                lines.append(
                    f"uniaxialMaterial Concrete01 {concrete_mat_tag + 1} "
                    f"{-_fcc:g} {-DEFAULT_EPS_CC:g} {-0.2 * _fcc:g} {-0.02:g}"
                )
                lines.append(
                    f"uniaxialMaterial Steel02 {concrete_mat_tag + 2} "
                    f"{_fy:g} {_e:g} {0.01:g} {18.5:g} {0.925:g} {0.15:g}"
                )

            fiber_mat_tag = concrete_mat_tag

        else:
            # ── Steel fiber section: Steel01 ──
            if mat is not None and mat.type.lower() == "steel":
                Fy = mat.Fy if mat.Fy and mat.Fy > 0 else DEFAULT_FY_STEEL_PA * _sf
                E_mod = mat.E_mod if mat.E_mod > 0 else DEFAULT_E_S_PA * _sf
            else:
                Fy = DEFAULT_FY_STEEL_PA * _sf
                E_mod = DEFAULT_E_S_PA * _sf
            fiber_mat_tag = sec_tag
            lines.append(f"uniaxialMaterial Steel01 {fiber_mat_tag} {Fy:g} {E_mod:g} {0.01:g}")

        # Compute shear modulus for GJ torsional rigidity
        _E = (
            (mat.E_mod if mat and mat.E_mod and mat.E_mod > 0 else DEFAULT_E_S_PA * _sf)
            if mat
            else DEFAULT_E_S_PA * _sf
        )
        _G = (
            (mat.G_mod if mat and mat.G_mod and mat.G_mod > 0 else DEFAULT_G_MOD_FRAC * _E)
            if mat
            else DEFAULT_G_MOD_FRAC * _E
        )

        # ── Fiber section ──
        gj = _G * sec.J
        lines.append(f"section Fiber {sec_tag} -GJ {gj:g} {{")
        try:
            entries = sec.to_fiber_patches(mat_tag=fiber_mat_tag)
        except NotImplementedError:
            # No fiber patches — skip this section, remove the header
            lines.pop()
            continue

        for entry in entries:
            if entry[0] == "rect":
                # patch rect $matTag $numSubdivY $numSubdivZ $yI $zI $yJ $zJ
                lines.append(
                    f"  patch rect {entry[1]} {entry[2]} {entry[3]} "
                    f"{entry[4]:g} {entry[5]:g} {entry[6]:g} {entry[7]:g}"
                )
            elif entry[0] == "circ":
                lines.append(
                    f"  patch circ {entry[1]} {entry[2]} {entry[3]} "
                    f"{entry[4]:g} {entry[5]:g} {entry[6]:g} {entry[7]:g}"
                )
            elif entry[0] == "quad":
                lines.append(
                    f"  patch quad {entry[1]} {entry[2]} {entry[3]} "
                    f"{entry[4]:g} {entry[5]:g} {entry[6]:g} {entry[7]:g} "
                    f"{entry[8]:g} {entry[9]:g} {entry[10]:g} {entry[11]:g}"
                )
            elif entry[0] == "straight":
                # layer straight $matTag $numBars $area $yStart $zStart $yEnd $zEnd
                parts = [str(x) for x in entry[1:]]
                lines.append(f"  layer straight {' '.join(parts)}")
            elif entry[0] == "circ_layer":
                parts = [str(x) for x in entry[1:]]
                lines.append(f"  layer circ {' '.join(parts)}")
        lines.append("}")

    if len(lines) > 3:
        return "\n".join(lines) + "\n"
    return ""


def pushover_tcl(
    *,
    control_node: int,
    dof: int = 1,
    max_disp: float = 0.1,
    num_steps: int = 100,
    lateral_loads: Optional[dict[int, tuple]] = None,
    gravity_loads: Optional[dict[int, tuple]] = None,
    gravity_pattern: str = "",
    adaptive: bool = False,
    base_node_tags: Optional[list[int]] = None,
    output_prefix: str = "wall",
) -> str:
    """Generate a pushover analysis block for Xara/OpenSeesRT Tcl.

    Returns a Tcl code string suitable for passing as
    *tcl_suffix* to :func:`export_model_to_tcl`.

    Args:
        control_node: Node tag for displacement control.
        dof: Degree of freedom for control (1=X, 2=Y, 3=Z).
        max_disp: Target displacement at control node.
        num_steps: Number of analysis steps.
        lateral_loads: Dict mapping node_tag -> (fx, fy, fz)
            for the lateral load pattern.
        gravity_loads: Dict mapping node_tag -> (fx, fy, fz)
            for the gravity load pattern (applied first).
        gravity_pattern: Name for the gravity load pattern
            (e.g. ``"Gravity"``).  If empty and *gravity_loads*
            is provided, a plain pattern is used.
        adaptive: If True, emit an adaptive algorithm fallback
            chain (Newton → KrylovNewton → ModifiedNewton with
            automatic step-size reduction) suitable for highly
            nonlinear pushover analyses.
        base_node_tags: List of node tags for the base reactions
            to record.  A ``recorder Node`` is emitted for each tag.
            If ``None`` (deprecated), a single reaction recorder is
            emitted for node 1.
        output_prefix: Prefix for the recorder output file names
            (default ``"wall"`` for backward compatibility).  The
            control-displacement recorder is written to
            ``{output_prefix}_disp.out``, the summed base reactions to
            ``{output_prefix}_bs.out`` (single line ``rx ry rz``), and
            the per-node reaction recorders to
            ``{output_prefix}_reaction.out`` (single base node) or
            ``{output_prefix}_reaction_{tag}.out`` (multiple).

    Returns:
        Tcl commands as a string.
    """
    lines: list[str] = []

    # ── Step A: Gravity ──
    if gravity_loads:
        lines.append("")
        lines.append("# ── Step A: Gravity analysis ──")
        lines.append('pattern Plain 1 "Linear" {')
        for nid, (fx, fy, fz) in gravity_loads.items():
            lines.append(f"    load {nid} {fx:g} {fy:g} {fz:g} 0 0 0")
        lines.append("}")
        lines.extend(
            [
                "constraints Transformation",
                "numberer RCM",
                "system BandGeneral",
                "test NormDispIncr 1.0e-3 20 0",
                "algorithm Newton",
                "integrator LoadControl 0.05",
                "analysis Static",
                "analyze 20",
                "loadConst -time 0.0",
                'puts "-> Gravity loads locked."',
            ]
        )

    # ── Step B: Lateral pushover ──
    if lateral_loads:
        lines.append("")
        lines.append('puts "-> Gravity complete, starting pushover analysis..."')
        lines.append("flush stdout")
        lines.append("")
        lines.append("# ── Step B: Lateral pushover ──")
        lines.append('pattern Plain 2 "Linear" {')
        for nid, (fx, fy, fz) in lateral_loads.items():
            lines.append(f"    load {nid} {fx:g} {fy:g} {fz:g} 0 0 0")
        lines.append("}")

    lines.extend(
        [
            "",
            "system BandGeneral",
            "numberer RCM",
            "constraints Transformation",
        ]
    )

    # ── Recorders (BEFORE analysis, NOT after) ──
    # Emit one reaction recorder per base node.  A single base node uses
    # ``{output_prefix}_reaction.out``; multiple base nodes get per-node
    # files ``{output_prefix}_reaction_<tag>.out``.
    rec_nodes = [1] if base_node_tags is None else list(base_node_tags)
    recorder_lines: list[str] = [
        "",
        f"recorder Node -file {output_prefix}_disp.out -time -node {control_node} -dof {dof} disp",
    ]
    if len(rec_nodes) == 1:
        recorder_lines.append(
            f"recorder Node -file {output_prefix}_reaction.out "
            f"-time -node {rec_nodes[0]} -dof {dof} reaction"
        )
    else:
        for tag in rec_nodes:
            recorder_lines.append(
                f"recorder Node -file {output_prefix}_reaction_{tag}.out "
                f"-time -node {tag} -dof {dof} reaction"
            )
    lines.extend(recorder_lines)
    lines.extend(
        [
            'puts "-> Recorders set up, analysis begins..."',
            "flush stdout",
        ]
    )

    if adaptive:
        # Adaptive pushover with algorithm fallback chain
        dU_base_val = max_disp / num_steps
        lines.extend(
            [
                f"set control_node {control_node}",
                f"set dof {dof}",
                f"set dU_base {dU_base_val:.8g}",
                "# ── Solver settings for shell+fiber models ──",
                "# Sparse solver is essential for models with >1000 shell elements",
                "system UmfPack",
                "# Penalty handles shell-edge MPCs correctly (Transformation silently ignores them)",
                "constraints Penalty 1.0e12 1.0e12",
                "",
                "# ── Gentle ramp-up for initial pushover step ──",
                "# Use 1/10 of base step for first step to stabilize fiber section convergence",
                "set dU [expr $dU_base / 10.0]",
                f"set targetDisp {max_disp:.6g}",
                "set currentDisp 0.0",
                "set stepCount 0",
                "",
                "# Relaxed norm (1e-3) matches gravity convergence — fiber sections need this",
                "test NormDispIncr 1.0e-3 200 0",
                "integrator DisplacementControl $control_node $dof $dU",
                "analysis Static",
                "",
                "# ── Base-shear history file (one line per step) ──",
                f"set bs_file [open {output_prefix}_bs.out w]",
                f"set base_tags [list {(' '.join(str(t) for t in rec_nodes))}]",
                "",
                "while {$currentDisp < $targetDisp} {",
                "",
                "    algorithm Newton",
                "    set ok [analyze 1]",
                "",
                "    # Fallback 1: Krylov-Newton",
                "    if {$ok != 0} {",
                '        puts "   Krylov-Newton fallback..."',
                "        flush stdout",
                "        test NormDispIncr 1.0e-2 500 0",
                "        algorithm KrylovNewton",
                "        set ok [analyze 1]",
                "    }",
                "",
                "    # Fallback 2: ModifiedNewton (initial stiffness)",
                "    if {$ok != 0} {",
                '        puts "   ModifiedNewton fallback..."',
                "        flush stdout",
                "        algorithm ModifiedNewton -initial",
                "        set ok [analyze 1]",
                "    }",
                "",
                "    # Fallback 3: cut step size by 90%",
                "    if {$ok != 0} {",
                '        puts "   Step cut from $dU to [expr $dU * 0.1]"',
                "        flush stdout",
                "        set dU [expr $dU * 0.1]",
                "        integrator DisplacementControl $control_node $dof $dU",
                "        algorithm Newton",
                "        test NormDispIncr 1.0e-2 500 0",
                "        set ok [analyze 1]",
                "    }",
                "",
                "    # Fallback 4: cycle back to tight norm with KrylovNewton at minimal step",
                "    if {$ok != 0} {",
                '        puts "   Final fallback: KrylovNewton + 1.0e-1 norm..."',
                "        flush stdout",
                "        set dU [expr $dU_base / 100.0]",
                "        integrator DisplacementControl $control_node $dof $dU",
                "        test NormDispIncr 1.0e-1 1000 0",
                "        algorithm KrylovNewton",
                "        set ok [analyze 1]",
                "    }",
                "",
                "    if {$ok != 0} {",
                "        puts {\\n[CRITICAL] Model collapse reached.}",
                "        flush stdout",
                "        break",
                "    }",
                "",
                "    # ── Record base shear for this step ──",
                "    reactions",
                "    set rx 0; set ry 0; set rz 0",
                "    foreach n $base_tags {",
                "        set rx [expr $rx + [nodeReaction $n 1]]",
                "        set ry [expr $ry + [nodeReaction $n 2]]",
                "        set rz [expr $rz + [nodeReaction $n 3]]",
                "    }",
                '    puts $bs_file "$rx $ry $rz"',
                "",
                "    # Restore step size and norm tolerance when possible",
                "    if {$dU < $dU_base} {",
                "        set dU $dU_base",
                "        test NormDispIncr 1.0e-3 200 0",
                "        integrator DisplacementControl $control_node $dof $dU",
                "    }",
                "",
                "    set currentDisp [nodeDisp $control_node $dof]",
                "    incr stepCount",
                "    if {[expr $stepCount % 20] == 0} {",
                '         puts [format "   Drift = %.2f mm (step %d)" $currentDisp $stepCount]',
                "         flush stdout",
                "    }",
                "}",
                "",
                "close $bs_file",
            ]
        )
    else:
        # Simple fixed-step pushover — per-step base-shear history
        lines.extend(
            [
                "test NormDispIncr 1.0e-6 100",
                "algorithm Newton",
                f"integrator DisplacementControl {control_node} {dof} "
                f"[expr {max_disp:.6g} / {num_steps}]",
                "analysis Static",
                "",
                f"set base_tags [list {(' '.join(str(t) for t in rec_nodes))}]",
                f"set bs_file [open {output_prefix}_bs.out w]",
                "",
                "for {set i 1} {$i <= " + str(num_steps) + "} {incr i} {",
                "    set ok [analyze 1]",
                "    if {$ok != 0} {",
                '        puts "Pushover: analyze failed at step $i"',
                "        break",
                "    }",
                "    # ── Record base shear for this step ──",
                "    reactions",
                "    set rx 0; set ry 0; set rz 0",
                "    foreach n $base_tags {",
                "        set rx [expr $rx + [nodeReaction $n 1]]",
                "        set ry [expr $ry + [nodeReaction $n 2]]",
                "        set rz [expr $rz + [nodeReaction $n 3]]",
                "    }",
                '    puts $bs_file "$rx $ry $rz"',
                "}",
                "close $bs_file",
                'puts "Pushover: completed [expr {$i - 1}] steps"',
            ]
        )

    # ── Results ──
    lines.extend(
        [
            "",
            f'puts "Control node {control_node} dof {dof}: [nodeDisp {control_node} {dof}]"',
            "",
            "reactions",
            "# Sum base reactions",
            "set rx 0; set ry 0; set rz 0",
            "foreach n [getNodeTags] {",
            "    set rx [expr $rx + [nodeReaction $n 1]]",
            "    set ry [expr $ry + [nodeReaction $n 2]]",
            "    set rz [expr $rz + [nodeReaction $n 3]]",
            "}",
            'puts "Base reactions: Rx = $rx  Ry = $ry  Rz = $rz"',
        ]
    )

    return "\n".join(lines)


def dynamic_time_history_tcl(
    *,
    ground_motion_file: str,
    output_prefix: str,
    dt: float = 0.005,
    num_steps: int = 1000,
    damping: float = 0.05,
    period_1: float = 0.2,
    period_2: float = 2.0,
    direction: str = "X",
    gravity_loads: Optional[dict[int, tuple]] = None,
) -> str:
    """Generate a nonlinear time-history analysis block for OpenSees Tcl.

    Returns a Tcl code string suitable for passing as *tcl_suffix* to
    :func:`export_model_to_tcl`.

    Args:
        ground_motion_file: Path to a text file with one acceleration
            value per line (consistent units with the model).
        output_prefix: Prefix for recorder output files (e.g.
            ``"output/dyn"`` → ``dyn_disp.out``, ``dyn_env_disp.out``).
            The directory must already exist.
        dt: Time step of the ground motion record (model time units).
        num_steps: Number of analysis steps.
        damping: Rayleigh damping ratio for the two retained modes.
        period_1: First Rayleigh period (seconds).
        period_2: Second Rayleigh period (seconds).
        direction: Excitation direction — ``"X"`` (dof 1), ``"Y"``
            (dof 2) or ``"Z"`` (dof 3).
        gravity_loads: Dict mapping node_tag -> (fx, fy, fz) for the
            gravity load pattern, applied and locked before the
            transient phase.

    Returns:
        Tcl commands as a string.
    """
    dof_map = {"X": 1, "Y": 2, "Z": 3}
    dof = dof_map.get(direction.upper(), 1)

    # Rayleigh coefficients from the two periods.
    # C = a0 * M + a1 * K  with  a0 = 4π ζ / (T1 + T2),
    # a1 = ζ * T1 * T2 / (π * (T1 + T2)) — exact two-period formula.
    a0 = 4.0 * math.pi * damping / (period_1 + period_2)
    a1 = damping * period_1 * period_2 / (math.pi * (period_1 + period_2))

    lines: list[str] = []

    # ── Step A: Gravity ──
    if gravity_loads:
        lines.append("")
        lines.append("# ── Step A: Gravity analysis ──")
        lines.append('pattern Plain 1 "Linear" {')
        for nid, (fx, fy, fz) in gravity_loads.items():
            lines.append(f"    load {nid} {fx:g} {fy:g} {fz:g} 0 0 0")
        lines.append("}")
        lines.extend(
            [
                "constraints Transformation",
                "numberer RCM",
                "system BandGeneral",
                "test NormDispIncr 1.0e-3 20 0",
                "algorithm Newton",
                "integrator LoadControl 0.05",
                "analysis Static",
                "analyze 20",
                "loadConst -time 0.0",
                'puts "-> Gravity loads locked."',
                "flush stdout",
            ]
        )

    # ── Step B: Transient analysis ──
    lines.extend(
        [
            "",
            'puts "-> Gravity complete, starting time-history analysis..."',
            "flush stdout",
            "",
            "# ── Step B: Transient dynamic analysis ──",
            "constraints Transformation",
            "numberer RCM",
            "system BandGeneral",
            f"rayleigh {a0:.8g} 0.0 {a1:.8g} 0.0",
            "",
            f"timeSeries Path 2 -dt {dt:g} -filePath {ground_motion_file} -factor 1.0",
            f"pattern UniformExcitation 2 {dof} -accel 2",
            "",
        ]
    )

    # ── Recorders (BEFORE analysis) ──
    lines.extend(
        [
            "set nodeTags [getNodeTags]",
            f"recorder Node -file {output_prefix}_disp.out -time -node $nodeTags -dof 1 2 3 disp",
            f"recorder EnvelopeNode -file {output_prefix}_env_disp.out -time "
            f"-node $nodeTags -dof 1 2 3 disp",
            'puts "-> Recorders set up, analysis begins..."',
            "flush stdout",
            "",
            "test EnergyIncr 1.0e-6 200 0",
            "algorithm Newton",
            "integrator Newmark 0.5 0.25",
            "analysis Transient",
            f"analyze {num_steps} {dt:g}",
            'puts "Time-history analysis complete."',
            "flush stdout",
        ]
    )

    return "\n".join(lines)


def mesh_model_to_gravity_loads(
    mesh_model: "MeshModel",
    pattern_combination: Optional[dict[str, float]] = None,
    g_acc: float = 0.0,
) -> dict[int, tuple]:
    """Convert MeshModel loads to ``{node_tag: (fx, fy, fz)}`` dict
    for gravity load application in pushover analysis.

    Computes gravity loads from two sources per load pattern:

    1. **Explicit gravity loads** (used when pattern's
       ``self_weight_factor`` is 0) — from ``frame_gravity_loads``,
       ``area_gravity_loads``, ``joint_loads``, and ``area_uniform_loads``
       (pressure loads distributed to nodes).

    2. **Material self-weight** (used when pattern's
       ``self_weight_factor`` ≠ 0, e.g. ``"Self weight"`` with sw=1) —
       element self-weight computed **directly** from section geometry
       and material density.  SAP2000 does **not** emit GravityLoad table
       entries for self-weight; it computes them internally.

    The load pattern dictionary controls which patterns contribute and
    at what scale factor, e.g. ``{"DEAD": 1.0, "Self weight": 1.0, "LL": 0.25}``.

    If *pattern_combination* is not provided, defaults to all patterns
    with ``DesignType=Dead`` (per ASCE/GB 50011 convention).

    Args:
        mesh_model: The preprocessed ``MeshModel``.
        pattern_combination: Dict mapping pattern name → factor.
            E.g. ``{"DEAD": 1.0, "Self weight": 1.0}``.
        g_acc: Gravitational acceleration (m/s²). Derived from units
            when not specified.

    Returns:
        Dict mapping node_tag -> (fx, fy, fz) with gravity forces
        (typically (0, 0, -force_z)).
    """
    units = mesh_model.units
    if g_acc == 0.0:
        g_acc = g_from_units(units)
    rho_mc = DEFAULT_RHO_MC_SI * mass_density_scale_factor(units)
    rho_ms = DEFAULT_RHO_MS_SI * mass_density_scale_factor(units)

    def _mass_density(mat) -> float:
        """Return mass density from material or fallback default."""
        if mat is not None and mat.unit_weight > 0:
            return mat.unit_weight / g_acc
        is_steel = mat is not None and "steel" in (mat.type or "").lower()
        return rho_ms if is_steel else rho_mc

    if pattern_combination is None:
        # Default: all DesignType=Dead patterns
        pattern_combination = {
            pn: 1.0
            for pn, lp in mesh_model.load_patterns.items()
            if (lp.pattern_type or "").upper() == "DEAD"
        }

    # Helper: compute polygon area in 3D
    def _poly_area(node_ids: list[str]) -> float:
        pts = []
        for nid in node_ids:
            nd = mesh_model.nodes.get(nid)
            if nd is None:
                return 0.0
            pts.append((nd.x, nd.y, nd.z))
        if len(pts) < 3:
            return 0.0
        nx = ny = nz = 0.0
        for i in range(len(pts)):
            x1, y1, z1 = pts[i]
            x2, y2, z2 = pts[(i + 1) % len(pts)]
            nx += (y1 - y2) * (z1 + z2)
            ny += (z1 - z2) * (x1 + x2)
            nz += (x1 - x2) * (y1 + y2)
        return 0.5 * math.hypot(nz, math.hypot(nx, ny))

    node_mass: dict[int, float] = defaultdict(float)

    for pattern_name, factor in pattern_combination.items():
        lp = mesh_model.load_patterns.get(pattern_name)
        has_self_weight = (
            lp is not None and abs(lp.self_weight_factor) > 1e-12
        )  # tolerance needs generalised

        # ── Source A: Explicit loads (used when sw=0) ──
        if not has_self_weight:
            # A1. Frame gravity loads (explicit self-weight multipliers)
            for gl in mesh_model.frame_gravity_loads:
                if gl.pattern != pattern_name:
                    continue
                fe = mesh_model.frame_elements.get(gl.frame_id)
                if fe is None or getattr(fe, "inactive", False):
                    continue
                ni = mesh_model.nodes.get(fe.node_i)
                nj = mesh_model.nodes.get(fe.node_j)
                if ni is None or nj is None:
                    continue
                sec_name = mesh_model.frame_assignments.get(gl.frame_id, "")
                sec = mesh_model.sections.get(sec_name) if sec_name else None
                if sec is not None and sec.A > 0:
                    dx = nj.x - ni.x
                    dy = nj.y - ni.y
                    dz = nj.z - ni.z
                    length = math.sqrt(dx * dx + dy * dy + dz * dz)
                    mat = mesh_model.materials.get(sec.material)
                    mass_density = _mass_density(mat)
                    elem_mass = sec.A * length * mass_density * abs(gl.multiplier_z)
                    half = elem_mass * factor * 0.5
                    node_mass[ni.node_tag] += half
                    node_mass[nj.node_tag] += half

            # A2. Area gravity loads (explicit self-weight multipliers)
            for al in mesh_model.area_gravity_loads:
                if al.pattern != pattern_name:
                    continue
                ae = mesh_model.area_elements.get(al.area_id)
                if ae is None or getattr(ae, "inactive", False):
                    continue
                area = _poly_area(ae.node_ids)
                if area <= 0:
                    continue
                t = ae.thickness if ae.thickness > 0 else 0.15
                sec_name = mesh_model.area_assignments.get(al.area_id)
                sec = mesh_model.sections.get(sec_name) if sec_name else None
                mat = mesh_model.materials.get(sec.material) if sec else None
                mass_density = _mass_density(mat)
                area_mass = area * t * mass_density * abs(al.multiplier_z)
                n_nodes = len(ae.node_ids)
                if n_nodes > 0:
                    node_share = area_mass * factor / n_nodes
                    for nid in ae.node_ids:
                        nd = mesh_model.nodes.get(nid)
                        if nd is not None:
                            node_mass[nd.node_tag] += node_share

            # A3. Joint loads (concentrated forces)
            # It is assumed that loads are in the model force units
            for jl in mesh_model.joint_loads:
                if jl.pattern != pattern_name:
                    continue
                nd = mesh_model.nodes.get(jl.node_id)
                if nd is None:
                    continue
                node_mass[nd.node_tag] += jl.fz * factor / g_acc if g_acc > 0 else jl.fz * factor

            # A4. Area uniform loads (pressure → nodal forces)
            for au in mesh_model.area_uniform_loads:
                if au.pattern != pattern_name:
                    continue
                if au.direction.upper() not in ("GRAVITY", "Z"):
                    continue
                ae = mesh_model.area_elements.get(au.area_id)
                if ae is None or getattr(ae, "inactive", False):
                    continue
                area = _poly_area(ae.node_ids)
                if area <= 0:
                    continue
                total_fz = au.value * area
                n_nodes = len(ae.node_ids)
                if n_nodes > 0:
                    node_share = total_fz * factor / (n_nodes * g_acc) if g_acc > 0 else 0.0
                    for nid in ae.node_ids:
                        nd = mesh_model.nodes.get(nid)
                        if nd is not None:
                            node_mass[nd.node_tag] += node_share

        # ── Source B: Material self-weight (used when sw≠0) ──
        else:
            # B1. Frame element self-mass from section × mass_density × length
            for eid, fe in mesh_model.frame_elements.items():
                if getattr(fe, "inactive", False):
                    continue
                ni = mesh_model.nodes.get(fe.node_i)
                nj = mesh_model.nodes.get(fe.node_j)
                if ni is None or nj is None:
                    continue
                sec_name = mesh_model.frame_assignments.get(eid, "")
                sec = mesh_model.sections.get(sec_name) if sec_name else None
                if sec is None or sec.A <= 0:
                    continue
                dx = nj.x - ni.x
                dy = nj.y - ni.y
                dz = nj.z - ni.z
                length = math.sqrt(dx * dx + dy * dy + dz * dz)
                mat = mesh_model.materials.get(sec.material)
                mass_density = _mass_density(mat)
                elem_mass = sec.A * length * mass_density * factor
                half = elem_mass * 0.5
                node_mass[ni.node_tag] += half
                node_mass[nj.node_tag] += half

            # B2. Area element self-mass from area × thickness × mass_density
            for aid, ae in mesh_model.area_elements.items():
                if getattr(ae, "inactive", False):
                    continue
                area = _poly_area(ae.node_ids)
                if area <= 0:
                    continue
                t = ae.thickness if ae.thickness > 0 else 0.15
                sec_name = mesh_model.area_assignments.get(aid, "")
                sec = mesh_model.sections.get(sec_name) if sec_name else None
                mat = mesh_model.materials.get(sec.material) if sec else None
                mass_density = _mass_density(mat)
                area_mass = area * t * mass_density * factor
                n_nodes = len(ae.node_ids)
                if n_nodes > 0:
                    node_share = area_mass / n_nodes
                    for nid in ae.node_ids:
                        nd = mesh_model.nodes.get(nid)
                        if nd is not None:
                            node_mass[nd.node_tag] += node_share

    # Convert mass to loads
    gravity_loads: dict[int, tuple] = {}
    for tag, mass in node_mass.items():
        gravity_loads[tag] = (0.0, 0.0, -mass * g_acc)

    return gravity_loads


def _find_dominant_mode(
    modal_data: dict,
    direction: str = "X",
) -> int:
    """Find the mode index with the highest mass participation in *direction*."""
    ratio_key = {
        "X": "partiMassRatiosMX",
        "Y": "partiMassRatiosMY",
        "Z": "partiMassRatiosMZ",
    }.get(direction.upper(), "partiMassRatiosMX")
    modal_props = modal_data.get("modal_props", {})
    ratios = modal_props.get(ratio_key, [])
    if not ratios:
        return 0
    return int(np.argmax(np.abs(ratios)))


def compute_lateral_loads(
    mesh_model: "MeshModel",
    pattern_type: str = "triangular",
    direction: str = "X",
    nodal_masses: Optional[dict[int, float]] = None,
    modal_data: Optional[dict] = None,
    k: float = 1.0,
) -> dict[int, tuple]:
    """Generate a unit-reference lateral load pattern for pushover analysis.

    Three pattern types are supported (FEMA 356):
    * **uniform** — ``F_i ∝ m_i`` (mass-proportional).
    * **triangular** — ``F_i ∝ m_i × z_i^k`` (inverted triangle, k=1 default).
    * **modal** — ``F_i ∝ m_i × φ_{i,mode}`` (dominant mode in push direction).

    All patterns are normalised so that sum(|F_i|) = 1.0 (unit reference load).
    """
    dof_idx = {"X": 0, "Y": 1, "Z": 2}.get(direction.upper(), 0)
    if nodal_masses is None:
        nodal_masses = {nd.node_tag: 1.0 for nd in mesh_model.nodes.values()}
    total_mass = sum(nodal_masses.values())

    loads: dict[int, tuple] = {}
    total_w = 0.0

    if pattern_type == "uniform":
        for nd in mesh_model.nodes.values():
            mi = nodal_masses.get(nd.node_tag, total_mass / max(len(mesh_model.nodes), 1))
            w = mi / total_mass if total_mass > 0 else 1.0
            f = [0.0, 0.0, 0.0]
            f[dof_idx] = w
            loads[nd.node_tag] = tuple(f)
            total_w += w

    elif pattern_type == "triangular":
        if mesh_model.nodes:
            z_min = min(nd.z for nd in mesh_model.nodes.values())
            z_max = max(nd.z for nd in mesh_model.nodes.values())
            z_range = max(z_max - z_min, 1e-12)
            for nd in mesh_model.nodes.values():
                mi = nodal_masses.get(nd.node_tag, 1.0)
                z_norm = (nd.z - z_min) / z_range
                w = mi * (z_norm**k)
                f = [0.0, 0.0, 0.0]
                f[dof_idx] = w
                loads[nd.node_tag] = tuple(f)
                total_w += w

    elif pattern_type == "modal":
        if modal_data is None:
            raise ValueError("modal_data is required for pattern_type='modal'")
        shapes = modal_data.get("shapes", modal_data.get("mode_shapes", {}))
        if not shapes:
            return compute_lateral_loads(mesh_model, "uniform", direction, nodal_masses)
        mode_idx = _find_dominant_mode(modal_data, direction)
        mode_shape = shapes.get(mode_idx, shapes.get(0, {}))
        for nd in mesh_model.nodes.values():
            phi = abs(mode_shape.get(nd.node_tag, (1.0, 0.0, 0.0))[dof_idx])
            mi = nodal_masses.get(nd.node_tag, 1.0)
            w = mi * phi
            f = [0.0, 0.0, 0.0]
            f[dof_idx] = w
            loads[nd.node_tag] = tuple(f)
            total_w += w
    else:
        raise ValueError(f"Unknown pattern_type='{pattern_type}'")

    if total_w > 1e-12:
        for tag, f_val in loads.items():
            f_list = list(f_val)
            f_list = [v / total_w for v in f_list]
            loads[tag] = tuple(f_list)
    return loads


# ── Convenience wrappers ──


def modal_to_lateral_loads(
    mesh_model: "MeshModel",
    modal_data: dict,
    direction: str = "X",
    nodal_masses: Optional[dict[int, float]] = None,
) -> dict[int, tuple]:
    """Legacy wrapper — delegates to :func:`compute_lateral_loads`."""
    return compute_lateral_loads(
        mesh_model,
        pattern_type="modal",
        direction=direction,
        nodal_masses=nodal_masses,
        modal_data=modal_data,
    )


def uniform_lateral_loads(
    mesh_model: "MeshModel",
    direction: str = "X",
    nodal_masses: Optional[dict[int, float]] = None,
) -> dict[int, tuple]:
    """Generate uniform (mass-proportional) lateral loads."""
    return compute_lateral_loads(
        mesh_model,
        pattern_type="uniform",
        direction=direction,
        nodal_masses=nodal_masses,
    )


def triangular_lateral_loads(
    mesh_model: "MeshModel",
    direction: str = "X",
    nodal_masses: Optional[dict[int, float]] = None,
    k: float = 1.0,
) -> dict[int, tuple]:
    """Generate triangular (height-proportional) lateral loads."""
    return compute_lateral_loads(
        mesh_model,
        pattern_type="triangular",
        direction=direction,
        nodal_masses=nodal_masses,
        k=k,
    )


def validate_control_node(
    control_node_tag: int,
    mesh_model: "MeshModel",
) -> bool:
    """Verify a pushover control node is connected to frame elements."""
    control_node_id: Optional[str] = None
    for nd in mesh_model.nodes.values():
        if nd.node_tag == control_node_tag:
            control_node_id = nd.node_id
            break
    if control_node_id is None:
        print(f"  ⚠ Control node tag {control_node_tag} not found!")
        return False
    connected = 0
    for fe in mesh_model.frame_elements.values():
        if getattr(fe, "inactive", False):
            continue
        if control_node_id in (fe.node_i, fe.node_j):
            connected += 1
    if connected == 0:
        print(f"  ⚠ Control node {control_node_tag} NOT connected to any frame element!")
        return False
    print(f"  Control node {control_node_tag} connected to {connected} frame element(s) — OK")
    return True


def verify_tcl_gravity_loads(
    tcl_path: str,
    expected_total_z: Optional[float] = None,
) -> dict:
    """Parse a Tcl file and verify gravity loading.

    Reads the generated Tcl file, finds ``pattern Plain 1`` blocks
    (the gravity load pattern), sums all Z-direction loads, and
    optionally compares against an expected total.
    """
    result: dict = {"present": False, "total_z": 0.0, "z_loads": 0, "match": None, "error": None}
    try:
        with open(tcl_path) as f:
            content = f.read()
    except Exception as e:
        result["error"] = str(e)
        return result
    in_gravity = False
    total_z = 0.0
    load_count = 0
    for line in content.split("\n"):
        stripped = line.strip()
        if re.match(r"pattern\s+Plain\s+1", stripped):
            in_gravity = True
            continue
        if in_gravity:
            if stripped == "}":
                break
            m = re.match(r"load\s+\d+\s+[-\d.e+]+\s+[-\d.e+]+\s+([-\d.e+]+)", stripped)
            if m:
                fz = float(m.group(1))
                total_z += fz
                load_count += 1
    result["present"] = load_count > 0
    result["total_z"] = total_z
    result["z_loads"] = load_count
    if expected_total_z is not None and abs(expected_total_z) > 1e-12:
        ratio = abs(total_z) / abs(expected_total_z)
        result["match"] = abs(ratio - 1.0) < 0.01
    return result
