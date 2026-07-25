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
from typing import TYPE_CHECKING, Dict, Any, Optional, List, Tuple
from ..model.sap_data import SAPModelData

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
    config: Optional[Dict[str, Any]] = None,
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
            import opensees as _xara_ops
            import os as _os
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
    _mat_tag: Dict[str, int] = {}
    _sec_tag: Dict[str, int] = {}
    for i, mn in enumerate(model_data.materials, start=1):
        _mat_tag[mn] = i
    mat_count = max(len(model_data.materials), 1)
    sec_tag_offset = mat_count + 1
    for i, sn in enumerate(model_data.sections, start=sec_tag_offset):
        _sec_tag[sn] = i

    # Nodes
    for nid, nd in model_data.nodes.items():
        lines.append(
            f"node {nd.node_tag} {nd.x:g} {nd.y:g} {nd.z:g}"
        )

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

    # Materials
    if model_data.materials:
        lines.append("")
        lines.append("# ── Materials ──")
        for mat_name, mat in model_data.materials.items():
            tag = _mat_tag[mat_name]
            if mat.type and "concrete" in mat.type.lower():
                Fc = (mat.Fc if mat.Fc and mat.Fc > 0 else 3.0e7) / 1.0
                epsc = (mat.eFc if mat.eFc and mat.eFc > 0 else 0.002)
                Fu = 0.2 * Fc
                epsu = 0.006
                lines.append(
                    f"uniaxialMaterial Concrete01 {tag} "
                    f"{-Fc:g} {-epsc:g} {-Fu:g} {-epsu:g}"
                )
            else:
                E_mod = (mat.E_mod if mat.E_mod and mat.E_mod > 0
                         else 2.0e11)
                Fy = (mat.Fy if mat.Fy and mat.Fy > 0 else 2.5e8)
                lines.append(
                    f"uniaxialMaterial Steel01 {tag} "
                    f"{Fy:g} {E_mod:g} 0.01"
                )

    # nD materials (for nonlinear shell analysis, MeshModel may not have them)
    _nd_mat_tag: Dict[str, int] = {}
    _nd_materials = getattr(model_data, 'nd_materials', {})
    if _nd_materials:
        lines.append("")
        lines.append("# ── nD materials (nonlinear shells) ──")
        _nd_base = max(_mat_tag.values()) + 1 if _mat_tag else 1
        for i, (nd_name, nd_mat) in enumerate(
                _nd_materials.items(), start=_nd_base):
            _nd_mat_tag[nd_name] = i
            lines.append(nd_mat.to_tcl(i))
        # Wrap each nD material as PlateFiber for layered shell use
        for nd_name, nd_mat in _nd_materials.items():
            tag = _nd_mat_tag[nd_name]
            if nd_mat.material_type != "ElasticIsotropic":
                pf_tag = tag + len(_nd_materials)
                lines.append(
                    f"nDMaterial PlateFromPlaneStress {pf_tag} {tag} 0.0"
                )

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
            G = (mat.G_mod if mat and mat.G_mod and mat.G_mod > 0
                 else 0.4 * E_mod)
            lines.append(
                f"section Elastic {tag} "
                f"{E_mod:g} {sec.A:g} {sec.I33:g} {sec.I22:g} "
                f"{G:g} {sec.J:g}"
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
            dz = nj.z - ni.z
            if abs(dx) < 1e-12 and abs(dy) < 1e-12:
                vecxz = "1 0 0"
            else:
                vecxz = "0 0 1"
            # Use config-driven geometric transformation
            # Use a deterministic integer tag for the transformation
            transf_tag = 20000 + elem.elem_tag
            transf_type = "Linear"
            if config:
                transf_type = config.get("geom_transf_type", "Linear")
            lines.append(
                f"geomTransf {transf_type} {transf_tag} {vecxz}"
            )
            sec_tag = _sec_tag.get(sec_name, sec_name)
            if config and config.get("create_fiber_sections", False) and sec_name in fiber_sec_names:
                # Nonlinear beam-column with fibre section
                int_tag = 10000 + elem.elem_tag
                n_int_pts = config.get("num_int_pts", 5)
                lines.append(
                    "beamIntegration Lobatto {} {} {}".format(
                        int_tag, sec_tag, n_int_pts)
                )
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
        _shell_sec_tag: Dict[str, int] = {}
        _next_shell_tag = (
            max(dict(**_mat_tag, **_sec_tag, **_nd_mat_tag).values())
            + len(_nd_materials) + 1
            if (_mat_tag or _sec_tag or _nd_mat_tag) else 1000
        )

        # Emit layered shell sections from model data
        for ls_name, ls_sec in (
                getattr(model_data, 'layered_shell_sections', {})).items():
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
            nids = [str(nd.node_tag) for nd_id in elem.node_ids
                    for nd in [model_data.nodes.get(nd_id)]
                    if nd is not None]
            if len(nids) < 3:
                continue
            stag = _shell_sec_tag.get(
                model_data.area_assignments.get(aid, ""), 1
            )
            nn = len(nids)
            if nn == 4:
                lines.append(
                    f"element ShellMITC4 {elem.area_tag} "
                    + " ".join(nids) + f" {stag}"
                )
            elif nn == 3:
                lines.append(
                    f"element ShellDKGT {elem.area_tag} "
                    + " ".join(nids) + f" {stag}"
                )

    # Auto-generate nonlinear materials and fiber sections from config
    nonlinear_tcl = tcl_materials_and_sections(
        model_data, config)

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
            if line.startswith("# ── Materials") or line.startswith("# ── Frame sections"):
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
    lines.append("puts \"Model exported successfully.\"")
    lines.append("wipe")
    lines.append("exit")

    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


# ── Extracted module-level functions for Xara/OpenSeesRT Tcl workflow ──
# These were formerly static methods on OpenSeesBuilder.  They are
# independent of the deprecated class and work directly with SAPModelData.


def tcl_materials_and_sections(
    model_data: "SAPModelData",
    config: Optional[Dict[str, Any]] = None,
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

    lines: List[str] = [
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
    _sec_tag: Dict[str, int] = {}
    for i, sn in enumerate(model_data.sections, start=sec_tag_offset):
        _sec_tag[sn] = i

    for sec_name, sec in model_data.sections.items():
        sec_tag = _sec_tag[sec_name]
        mat = model_data.materials.get(sec.material)

        from ..model.sap_data import (
            ConcreteRectangularSection,
            ConcreteCircularSection,
            RectangularSection,
            ShellSection,
            Section as BaseSection,
        )

        # Shell sections → elastic only (no fiber)
        if isinstance(sec, ShellSection):
            continue

        is_rc = isinstance(sec, (
            ConcreteRectangularSection,
            ConcreteCircularSection,
            RectangularSection,
        ))

        if is_rc:
            # ── RC fiber section: unconfined, confined, rebar ──
            concrete_mat_tag = next_concrete_tag
            next_concrete_tag += 3

            if mat is not None:
                Fc = mat.Fc if mat.Fc and mat.Fc > 0 else 3.0e7
                epsc = (float(mat.extra.get("SFc", 0.002))
                        if hasattr(mat, "extra") else 0.002)
                if epsc > 0.01:
                    epsc = 0.002

                # Confined strength: use eFc from SAP2000, else 1.3×Fc
                fcc = mat.eFc if mat.eFc and mat.eFc > 0 else Fc * 1.3
                epscc = (float(mat.extra.get("SCap", 0.005))
                         if hasattr(mat, "extra") else 0.005)
                if epscc > 0.1:
                    epscc = 0.005

                lines.append(
                    f"uniaxialMaterial Concrete01 {concrete_mat_tag} "
                    f"{-Fc:g} {-abs(epsc):g} {-0.2*Fc:g} {-0.006:g}"
                )
                lines.append(
                    f"uniaxialMaterial Concrete01 {concrete_mat_tag + 1} "
                    f"{-fcc:g} {-abs(epscc):g} {-0.2*fcc:g} {-0.02:g}"
                )
                Fy = mat.Fy if mat.Fy and mat.Fy > 0 else 4.0e8
                lines.append(
                    f"uniaxialMaterial Steel02 {concrete_mat_tag + 2} "
                    f"{Fy:g} {2.0e11:g} {0.01:g} {18.5:g} {0.925:g} {0.15:g}"
                )
            else:
                lines.append(
                    f"uniaxialMaterial Concrete01 {concrete_mat_tag} "
                    f"{-3.0e7:g} {-0.002:g} {-6.0e6:g} {-0.006:g}"
                )
                lines.append(
                    f"uniaxialMaterial Concrete01 {concrete_mat_tag + 1} "
                    f"{-3.9e7:g} {-0.005:g} {-7.8e6:g} {-0.02:g}"
                )
                lines.append(
                    f"uniaxialMaterial Steel02 {concrete_mat_tag + 2} "
                    f"{4.0e8:g} {2.0e11:g} {0.01:g} {18.5:g} {0.925:g} {0.15:g}"
                )

            fiber_mat_tag = concrete_mat_tag

        else:
            # ── Steel fiber section: Steel01 ──
            if mat is not None and mat.type.lower() == "steel":
                Fy = mat.Fy if mat.Fy and mat.Fy > 0 else 2.5e8
                E_mod = mat.E_mod if mat.E_mod > 0 else 2.0e11
            else:
                Fy = 2.5e8
                E_mod = 2.0e11
            fiber_mat_tag = sec_tag
            lines.append(
                f"uniaxialMaterial Steel01 {fiber_mat_tag} "
                f"{Fy:g} {E_mod:g} {0.01:g}"
            )

        # Compute shear modulus for GJ torsional rigidity
        _E = (mat.E_mod if mat and mat.E_mod and mat.E_mod > 0
              else 2.0e11) if mat else 2.0e11
        _G = (mat.G_mod if mat and mat.G_mod and mat.G_mod > 0
              else 0.4 * _E) if mat else 0.4 * _E

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
    lateral_loads: Optional[Dict[int, tuple]] = None,
    gravity_loads: Optional[Dict[int, tuple]] = None,
    gravity_pattern: str = "",
    adaptive: bool = False,
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

    Returns:
        Tcl commands as a string.
    """
    lines: List[str] = []

    # ── Step A: Gravity ──
    if gravity_loads:
        lines.append("")
        lines.append("# ── Step A: Gravity analysis ──")
        lines.append(f"pattern Plain 1 \"Linear\" {{")
        for nid, (fx, fy, fz) in gravity_loads.items():
            lines.append(f"    load {nid} {fx:g} {fy:g} {fz:g} 0 0 0")
        lines.append("}")
        lines.extend([
            "constraints Transformation",
            "numberer RCM",
            "system BandGeneral",
            "test NormDispIncr 1.0e-6 10 0",
            "algorithm Newton",
            "integrator LoadControl 0.1",
            "analysis Static",
            "analyze 10",
            'loadConst -time 0.0',
            'puts "-> Gravity loads locked."',
        ])

    # ── Step B: Lateral pushover ──
    if lateral_loads:
        lines.append("")
        lines.append("# ── Step B: Lateral pushover ──")
        lines.append("pattern Plain 2 \"Linear\" {")
        for nid, (fx, fy, fz) in lateral_loads.items():
            lines.append(f"    load {nid} {fx:g} {fy:g} {fz:g} 0 0 0")
        lines.append("}")

    lines.extend([
        "",
        "system BandGeneral",
        "numberer RCM",
        "constraints Transformation",
    ])

    if adaptive:
        # Adaptive pushover with algorithm fallback chain
        dU = f"[expr {max_disp:.6g} / {num_steps}]"
        lines.extend([
            f"set control_node {control_node}",
            f"set dof {dof}",
            "set dU_base " + dU,
            "set dU $dU_base",
            "integrator DisplacementControl $control_node $dof $dU",
            "analysis Static",
            "",
            f"set targetDisp {max_disp:.6g}",
            "set currentDisp 0.0",
            "set stepCount 0",
            "",
            "while {$currentDisp < $targetDisp} {",
            "",
            "    test NormDispIncr 1.0e-5 200 0",
            "    algorithm Newton",
            "    set ok [analyze 1]",
            "",
            "    # Fallback 1: Krylov-Newton",
            '    if {$ok != 0} {',
            "        puts \"   Krylov-Newton fallback...\"",
            "        test NormDispIncr 1.0e-5 500 0",
            "        algorithm KrylovNewton",
            "        set ok [analyze 1]",
            "    }",
            "",
            "    # Fallback 2: ModifiedNewton (initial stiffness)",
            '    if {$ok != 0} {',
            "        puts \"   ModifiedNewton fallback...\"",
            "        algorithm ModifiedNewton -initial",
            "        set ok [analyze 1]",
            "    }",
            "",
            "    # Fallback 3: cut step size",
            '    if {$ok != 0} {',
            "        puts \"   Step cut from $dU to [expr $dU * 0.1]\"",
            "        set dU [expr $dU * 0.1]",
            "        integrator DisplacementControl $control_node $dof $dU",
            "        algorithm Newton",
            "        set ok [analyze 1]",
            "    }",
            "",
            '    if {$ok != 0} {',
            '        puts "\\n[CRITICAL] Model collapse reached."',
            "        break",
            "    }",
            "",
            "    # Restore step size when possible",
            "    if {$dU < $dU_base} {",
            "        set dU $dU_base",
            "        integrator DisplacementControl $control_node $dof $dU",
            "    }",
            "",
            "    set currentDisp [nodeDisp $control_node $dof]",
            "    incr stepCount",
            '    if {[expr $stepCount % 20] == 0} {',
            "         puts [format \"   Drift = %.2f mm (step %d)\" $currentDisp $stepCount]",
            "    }",
            "}",
        ])
    else:
        # Simple fixed-step pushover
        lines.extend([
            "test NormDispIncr 1.0e-6 100",
            "algorithm Newton",
            f"integrator DisplacementControl {control_node} {dof} "
            f"[expr {max_disp:.6g} / {num_steps}]",
            "analysis Static",
            "",
            f"set ok [analyze {num_steps}]",
            'puts "Pushover: $ok steps"',
        ])

    # ── Results ──
    lines.extend([
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
    ])

    # Recorders
    lines.extend([
        "",
        f"recorder Node -file wall_disp.out -time -node {control_node} -dof {dof} disp",
        "recorder Node -file wall_reaction.out -time -node 1 -dof 1 reaction",
        "recorder Element -file wall_forces.out -ele 1 force",
    ])

    return "\n".join(lines)


def mesh_model_to_gravity_loads(
    mesh_model: "MeshModel",
    pattern_names: Optional[list[str]] = None,
    g: float = 9.81,
) -> Dict[int, tuple]:
    """Convert MeshModel load patterns to ``{node_tag: (fx, fy, fz)}``
    dict suitable for passing as *gravity_loads* to :func:`pushover_tcl`.

    Args:
        mesh_model: The preprocessed ``MeshModel``.
        pattern_names: List of load pattern names to include.
            If ``None``, all patterns with type ``"Dead"`` or
            containing ``"DEAD"`` are used.
        g: Gravitational acceleration.

    Returns:
        Dict mapping node_tag -> (0, 0, -force_z) for gravity direction.
    """
    from collections import defaultdict

    if pattern_names is None:
        pattern_names = [
            pn for pn, lp in mesh_model.load_patterns.items()
            if "DEAD" in pn.upper() or lp.pattern_type == "Dead"
        ]

    pattern_set = set(pattern_names)
    node_mass: Dict[int, float] = defaultdict(float)
    for gl in mesh_model.frame_gravity_loads:
        if gl.pattern not in pattern_set:
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
            dx = nj.x - ni.x; dy = nj.y - ni.y; dz = nj.z - ni.z
            length = math.sqrt(dx*dx + dy*dy + dz*dz)
            mat = mesh_model.materials.get(sec.material)
            density = mat.unit_weight / g if mat and mat.unit_weight > 0 else 2400.0
            elem_mass = sec.A * length * density
            half = elem_mass * abs(gl.multiplier_z) * 0.5
            node_mass[ni.node_tag] += half
            node_mass[nj.node_tag] += half

    for jl in mesh_model.joint_loads:
        if jl.pattern not in pattern_set:
            continue
        nd = mesh_model.nodes.get(jl.node_id)
        if nd is None:
            continue
        node_mass[nd.node_tag] += jl.fz / g if g > 0 else jl.fz

    gravity_loads: Dict[int, tuple] = {}
    for tag, mass in node_mass.items():
        gravity_loads[tag] = (0.0, 0.0, -mass * g)

    return gravity_loads


def modal_to_lateral_loads(
    mesh_model: "MeshModel",
    modal_data: dict,
    direction: str = "X",
) -> Dict[int, tuple]:
    """Generate mode-1 proportional lateral loads from modal results.

    Args:
        mesh_model: Preprocessed model data.
        modal_data: Dict from ``run_modal_analysis()``.
        direction: ``"X"`` (dof 1), ``"Y"`` (dof 2), or ``"Z"`` (dof 3).

    Returns:
        ``{node_tag: (fx, fy, fz)}`` with mode1-proportional loads
        normalised so that ``sum(|fx|) = 1.0`` (unit reference load).
    """
    dof_idx = {"X": 0, "Y": 1, "Z": 2}.get(direction.upper(), 0)
    shapes = modal_data.get("shapes", modal_data.get("mode_shapes", {}))
    if not shapes:
        loads: Dict[int, tuple] = {}
        for nd in mesh_model.nodes.values():
            fx = 1.0 if direction.upper() == "X" else 0.0
            fy = 1.0 if direction.upper() == "Y" else 0.0
            fz = 1.0 if direction.upper() == "Z" else 0.0
            loads[nd.node_tag] = (fx, fy, fz)
        return loads

    mode1 = shapes.get(0, shapes.get(1, {}))
    loads = {}
    total = 0.0
    for nd in mesh_model.nodes.values():
        phi = abs(mode1.get(nd.node_tag, (1.0, 0.0, 0.0))[dof_idx])
        mass = getattr(nd, "mass", 1.0) or 1.0
        w = mass * phi
        f = [0.0, 0.0, 0.0]
        f[dof_idx] = w
        loads[nd.node_tag] = tuple(f)
        total += w

    if total > 1e-12:
        for tag in loads:
            f = list(loads[tag])
            f = [v / total for v in f]
            loads[tag] = tuple(f)

    return loads
