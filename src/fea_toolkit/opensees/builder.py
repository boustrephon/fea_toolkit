# fea_toolkit/opensees/builder.py

"""Build an OpenSees model from SAPModelData.

    Examples of OpenSeesPy usage:
    https://github.com/AmirHosseinNamadchi/OpenSeesPy-Examples
"""

from typing import Dict, Any, Optional, Tuple, List
from pathlib import Path
import copy
import json
import math
import numpy as np

import openseespy.opensees as ops
# Module-level reference — can be swapped with a RecordingOpenSees proxy
# for Tcl export or Xara backend execution without modifying any code
# below this line.  See OpenSeesBuilder.xara_build() for the swap pattern.
try:
    import opstool as opst
    OPSTOOL_AVAILABLE = True
except ImportError:
    OPSTOOL_AVAILABLE = False

from ..model.sap_data import SAPModelData
from ..model.geometry import get_SAP_vecxz, global_to_local_distributed_load, rotate_about_axis
from ..model.sap_data import Section, FrameElement, FrameDistributedLoad, Node
from ..model.sap_data import GravityLoad, AreaGravityLoad, ShellSection
from ..model.geometry import convert_area_loads_to_edge_loads
from ..model.selection import Selection


class OpenSeesBuilder:
    """Construct an OpenSees model from a SAPModelData instance.

    Usage:
        config = {
            'element_type': 'forceBeamColumn',
            'num_int_pts': 3,
            'use_elastic_sections': True,
            'create_fiber_sections': False,
            'split_elements': True,
            'geom_transf_type': 'Linear',   # 'Linear', 'PDelta', or 'Corotational'
            'verbose': False,
        }
        builder = OpenSeesBuilder(model_data, config)
        builder.build()
        # builder.write_script("output.tcl")
        results = builder.run_analysis()
    """

    def __init__(self, model_data: SAPModelData, config: Optional[Dict[str, Any]] = None):
        """Initialise the builder.

        Args:
            model_data: SAPModelData instance (from parser.get_model_data()).
            config: Dictionary with keys:
                - element_type (str): 'elasticBeamColumn', 'forceBeamColumn', 'dispBeamColumn', 'nonlinearBeamColumn'
                - num_int_pts (int): Number of integration points (default 3)
                - use_elastic_sections (bool): If True, create elastic sections (default True)
                - create_fiber_sections (bool): If True, create fiber sections (default False)
                - split_elements (bool): If True, split elements at intermediate nodes (default True)
                - geom_transf_type (str): Geometric transformation — 'Linear', 'PDelta', or 'Corotational'
                  (default 'Linear').  Note: 'Corotational' does NOT support eleLoad in 3D;
                  use beam_load_to_nodal_loads() in those cases (see fea_toolkit.model.geometry).
                - verbose (bool): Print progress (default False)
        """
        self.model = model_data
        self.units = model_data.units
        self.config = config or {}
        self._set_defaults()
        self.split_elements: Optional[Dict[str, FrameElement]] = None
        self.split_assignments: Optional[Dict[str, str]] = None
        self.split_dist_loads: Optional[List[FrameDistributedLoad]] = None
        self._has_edge_constraints: bool = False
        self._offset_rigid_links: List[tuple] = []
        # Snapshot pristine frame data so rebuilds always start from the
        # original geometry (before any offset/split/mesh mutations).
        self._original_frame_elements = copy.deepcopy(model_data.frame_elements)
        self._original_frame_assignments = copy.deepcopy(model_data.frame_assignments)
        self._original_nodes = copy.deepcopy(model_data.nodes)
        self._original_area_elements = copy.deepcopy(model_data.area_elements)
        self._original_area_assignments = copy.deepcopy(model_data.area_assignments)
        # self._transf_tags: Dict[int, int] = {}   # elem_id -> transf_tag

    def _set_defaults(self) -> None:
        """Set default configuration values."""
        defaults = {
            'element_type': 'elasticBeamColumn',
            'num_int_pts': 3,
            'use_elastic_sections': True,
            'create_fiber_sections': False,
            'split_elements': True,
            'verbose': False,
            'geom_transf_type': 'Linear',
            'beam_integration': 'Lobatto',  # 'Lobatto' or 'HingeRadau'
            'simplify_distributed_loads': False,
            'create_shells': False,          # Create shell elements for area elements
                                             # Areas in the loads selection are still
                                             # loads-only; all other areas become shells.
            'subdivide_braces': False,
            'brace_n_segments': 4,
            'brace_imperfection_ratio': 1.0/500.0,
            'brace_end_offset': 0.0,
            'brace_truss': False,          # Replace braces with truss elements (Approach B)
            'brace_sections': None,        # Optional list of section names to treat as braces
                                           # (None = auto-detect by shape type)
            'brace_fatigue': False,         # Wrap Hysteretic with Fatigue material for cyclic degradation
            'brace_fatigue_E0': 0.095,      # Fatigue Coffin-Manson: strain amplitude at fracture
            'brace_fatigue_m': -0.3,        # Fatigue Coffin-Manson: exponent
            'hinge_model': 'fiber',         # Distributed plasticity by default
            # Solver settings (can be overridden per-call)
            'solver_test_tol': 1e-6,
            'solver_test_max_iter': 10,
            'solver_algorithm': 'Newton',
            'gravity_num_substeps': 1,
            'solver_constraints': 'Transformation',
            'solver_system': 'BandGen',
        }
        for key, default in defaults.items():
            if key not in self.config:
                self.config[key] = default

    # -------------------------------------------------------------------------
    # Xara (OpenSeesRT) Tcl backend
    # -------------------------------------------------------------------------

    @classmethod
    def xara_build(
        cls,
        model_data: "SAPModelData",
        config: Optional[Dict[str, Any]] = None,
        *,
        tcl_path: str = "model.tcl",
        lib_path: str = "",
        tclsh_path: str = "",
        ndm: int = 3,
        ndf: int = 6,
        timeout: float = 300.0,
    ) -> tuple["OpenSeesBuilder", int, str]:
        """Build and run a model via Xara's standalone ``tclsh8.6``.

        This is a convenience pipeline that:

        1. Records all ``ops.*`` calls via :class:`RecordingOpenSees`
        2. Saves them as a Xara-compatible Tcl script
        3. Runs the script via the standalone ``tclsh8.6`` interpreter
           (bypassing the Tcl 8.6 / Tcl 9 version conflict)

        Usage::

            from fea_toolkit.opensees.builder import OpenSeesBuilder
            from examples.sample_model import make_sample_model

            md = make_sample_model()
            builder, ret, stdout = OpenSeesBuilder.xara_build(
                md, {"verbose": False},
                tcl_path="/tmp/my_model.tcl",
            )
            print(stdout)

        Args:
            model_data: SAP model data.
            config: Builder configuration dict.
            tcl_path: Where to write the generated Tcl script.
            lib_path: Path to ``libOpenSeesRT.dylib``. Auto-detected
                if empty.
            tclsh_path: Path to ``tclsh8.6``.  Auto-detected if empty.
            ndm: Spatial dimensions (default 3).
            ndf: DOFs per node (default 6).
            timeout: Maximum execution time in seconds.

        Returns:
            ``(builder, exit_code, stdout)``.
        """
        from fea_toolkit.opensees.recorder import (
            RecordingOpenSees, XaraTclRunner,
        )
        import fea_toolkit.opensees.builder as _builder_mod

        # Swap module-level ops with recorder proxy
        _real_ops = _builder_mod.ops
        recorder = RecordingOpenSees(_real_ops)
        _builder_mod.ops = recorder

        try:
            builder = cls(model_data, config)
            if config is None:
                config = {}
            create_shells = config.get("create_shells", False)
            builder.build(
                pattern_scales=config.get("pattern_scales"),
                selection=config.get("selection") if create_shells else None,
            )

            # Export commands to Tcl
            recorder.save_as_xara_tcl(
                tcl_path, lib_path=lib_path, ndm=ndm, ndf=ndf,
            )

            # Run via standalone tclsh8.6
            runner = XaraTclRunner(
                tclsh_path or XaraTclRunner.which_tclsh(),
            )
            ret, stdout = runner.run(tcl_path, timeout=timeout)
        finally:
            # Restore the real ops module
            _builder_mod.ops = _real_ops

        return builder, ret, stdout

    @staticmethod
    def export_model_to_tcl(
        model_data: "SAPModelData",
        path: str,
        lib_path: str = "",
        ndm: int = 3,
        ndf: int = 6,
        tcl_prefix: str = "",
        tcl_suffix: str = "",
    ) -> None:
        """Export a SAP model directly to a Xara-compatible Tcl script.

        This is an alternative to the recording-based approach that
        translates the structured ``SAPModelData`` directly into Tcl
        commands, avoiding the scoping issues that arise when replaying
        flat ``ops.*`` call sequences.

        The generated Tcl file can be run via :class:`XaraTclRunner`::

            from fea_toolkit.opensees.recorder import XaraTclRunner

            OpenSeesBuilder.export_model_to_tcl(md, "model.tcl")
            runner = XaraTclRunner()
            ret, stdout = runner.run("model.tcl")

        To add nonlinear materials, layered shell sections, and
        analysis commands, use *tcl_prefix* (inserted after the
        ``model Basic`` preamble) and/or *tcl_suffix* (appended
        before ``wipe``)::

            tcl = OpenSeesBuilder.pushover_tcl(
                control_node=8, dof=2, max_disp=0.1,
                lateral_loads={5: (0,10000,0), 6: (0,10000,0),
                               7: (0,10000,0), 8: (0,10000,0)},
            )
            OpenSeesBuilder.export_model_to_tcl(md, "wall.tcl",
                tcl_suffix=tcl,
            )

        Args:
            model_data: SAP model data to export.
            path: Output ``.tcl`` file path.
            lib_path: Path to ``libOpenSeesRT.dylib``.
            ndm: Spatial dimensions (default 3).
            ndf: DOFs per node (default 6).
            tcl_prefix: Tcl commands inserted after the model preamble
                (e.g. for nDMaterial definitions before sections).
            tcl_suffix: Tcl commands appended before ``wipe``
                (e.g. for analysis, recorders, results output).
        """
        if not lib_path:
            try:
                import opensees as _xara_ops
                lib_dir = os.path.dirname(_xara_ops.__file__)
                for ext in (".dylib", ".so"):
                    cand = os.path.join(lib_dir, f"libOpenSeesRT{ext}")
                    if os.path.exists(cand):
                        lib_path = cand
                        break
            except ImportError:
                lib_path = "libOpenSeesRT.dylib"

        lines = [
            "# Xara/OpenSeesRT Tcl script -- exported by OpenSeesBuilder",
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
        tag_offset = max(len(model_data.materials), 1) + 1
        for i, sn in enumerate(model_data.sections, start=tag_offset):
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

        # nD materials (for nonlinear shell analysis)
        _nd_mat_tag: Dict[str, int] = {}
        if model_data.nd_materials:
            lines.append("")
            lines.append("# ── nD materials (nonlinear shells) ──")
            _nd_base = max(_mat_tag.values()) + 1 if _mat_tag else 1
            for i, (nd_name, nd_mat) in enumerate(
                    model_data.nd_materials.items(), start=_nd_base):
                _nd_mat_tag[nd_name] = i
                lines.append(nd_mat.to_tcl(i))
            # Wrap each nD material as PlateFiber for layered shell use
            for nd_name, nd_mat in model_data.nd_materials.items():
                tag = _nd_mat_tag[nd_name]
                if nd_mat.material_type != "ElasticIsotropic":
                    pf_tag = tag + len(model_data.nd_materials)
                    lines.append(
                        f"nDMaterial PlateFromPlaneStress {pf_tag} {tag} 0.0"
                    )

        # Sections
        if model_data.sections:
            lines.append("")
            lines.append("# ── Frame sections ──")
            for sec_name, sec in model_data.sections.items():
                tag = _sec_tag[sec_name]
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
            transf_added = False
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
                if not transf_added:
                    lines.append(
                        f"geomTransf Linear {eid} {vecxz}"
                    )
                    transf_added = True
                else:
                    lines.append(
                        f"geomTransf Linear {eid} {vecxz}"
                    )
                lines.append(
                    f"element elasticBeamColumn {elem.elem_tag} "
                    f"{ni.node_tag} {nj.node_tag} {_sec_tag.get(sec_name, sec_name)} {eid}"
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
                + len(model_data.nd_materials) + 1
                if (_mat_tag or _sec_tag or _nd_mat_tag) else 1000
            )

            # Emit layered shell sections from model data
            for ls_name, ls_sec in (
                    model_data.layered_shell_sections or {}).items():
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

        # Insert tcl_prefix after model preamble, before sections
        if tcl_prefix:
            # Find the # ── Nodes ── marker and insert prefix after it
            # Actually, insert before the first element-related section
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

        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")

    @staticmethod
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
        *tcl_suffix* to :meth:`export_model_to_tcl`.

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
                "set dU_base " + dU,
                "set dU $dU_base",
                f"integrator DisplacementControl {control_node} {dof} $dU",
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
                f"        integrator DisplacementControl {control_node} {dof} $dU",
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
                f"        integrator DisplacementControl {control_node} {dof} $dU",
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
    # -------------------------------------------------------------------------
    def build(self,
              pattern_scales: Optional[Dict[str, float]] = None,
              selection: Optional[Selection] = None,
              ) -> None:
        """Build the complete OpenSees model in memory.

        Args:
            pattern_scales: Optional dict mapping pattern name → scale factor.
                If provided, only these patterns are created with the given
                scale.  If ``None`` (default), all patterns are applied with
                factor 1.0.
            selection: Optional :class:`Selection` to control which area loads
                are converted to equivalent frame edge loads.  ``None`` means
                all area loads are converted (unless ``create_shells=True``,
                in which case ``None`` means all areas become shells).

            When ``config['create_shells']`` is ``True``, areas that **do not**
            match this *selection* are turned into ``ShellMITC4`` elements.
            Areas that **do** match remain loads‑only (their loads are converted
            to frame edge loads, as before).
        """
        # Persist selection so re-builds (e.g. from run_static_analysis)
        # don't lose it.
        if selection is not None:
            self._area_selection = selection

        # Reset per-build state so stale values from a previous build()
        # don't carry over (e.g. split_elements from a prior True→False
        # config change or _offset_rigid_links after frame_end_offsets
        # was cleared).
        self.split_elements = None
        self.split_assignments = None
        self.split_dist_loads = None
        self._offset_rigid_links = []

        # Restore pristine geometry so rebuilds always start from the
        # original data (before any offset/split/mesh mutations).
        self.model.frame_elements = copy.deepcopy(self._original_frame_elements)
        self.model.frame_assignments = copy.deepcopy(self._original_frame_assignments)
        self.model.nodes = copy.deepcopy(self._original_nodes)
        self.model.area_elements = copy.deepcopy(self._original_area_elements)
        self.model.area_assignments = copy.deepcopy(self._original_area_assignments)

        create_shells = self.config.get('create_shells', False)
        if self.config['verbose']:
            print("Building OpenSees model...")
            print(f"  Element type: {self.config['element_type']}")
            # print(f"  Integration points: {self.config['num_int_pts']}")
            print(f"  Split elements: {self.config['split_elements']}")
            if create_shells:
                print(f"  Create shells: yes")
                if selection is not None:
                    n_sel = len(selection.get_area_ids(self.model))
                    n_all = len(self.model.area_elements)
                    print(f"  Area elements: {n_all} total, "
                          f"{n_sel} loads‑only, {n_all - n_sel} shells")
                else:
                    print(f"  Area elements: all {len(self.model.area_elements)} → shells")

        ops.wipe()
        # wipe() clears all MPCs — caller must re-apply edge constraints
        self._has_edge_constraints = False
        ops.model('basic', '-ndm', 3, '-ndf', 6)

        self._create_nodes()
        self._apply_restraints()
        self._create_materials()
        self._create_sections()

        # Element splitting (if enabled)
        if self.config['split_elements']:
            self._split_elements()
        
        # Apply frame end offsets (rigid zones at joints)
        if self.model.frame_end_offsets:
            self._apply_frame_end_offsets()

        # Convert area uniform loads to equivalent frame edge loads
        self._convert_area_loads(selection=selection)

        # Mesh area elements and create shell elements
        if create_shells:
            self._mesh_areas(selection=selection)
            self._create_shell_elements(loads_only_selection=selection)

        self._create_lumped_hinges()
        self._create_elements()
        self._create_loads(pattern_scales=pattern_scales)
        self._setup_recorders()  # optional

        if self.config['verbose']:
            print("Model building complete.")

    # -------------------------------------------------------------------------
    # Node creation
    # -------------------------------------------------------------------------
    # Shared helpers
    # -------------------------------------------------------------------------

    def _polygon_area(self, node_ids, nodes=None):
        """Compute the 3D area of a polygon via Newell's method.

        Args:
            node_ids: Iterable of node ID strings.
            nodes: Optional node dict (defaults to ``self.model.nodes``).

        Returns:
            ``(area_mag, pts)`` tuple where *area_mag* is the polygon area
            (always ≥ 0) and *pts* is the list of ``(x, y, z)`` tuples.
            Returns ``(0.0, [])`` if fewer than 3 nodes are resolved.
        """
        if nodes is None:
            nodes = self.model.nodes
        pts = []
        for nid in node_ids:
            nd = nodes.get(nid)
            if nd is None:
                return 0.0, []
            pts.append((nd.x, nd.y, nd.z))
        if len(pts) < 3:
            return 0.0, []
        nx = ny = nz = 0.0
        for i in range(len(pts)):
            x1, y1, z1 = pts[i]
            x2, y2, z2 = pts[(i + 1) % len(pts)]
            nx += (y1 - y2) * (z1 + z2)
            ny += (z1 - z2) * (x1 + x2)
            nz += (x1 - x2) * (y1 + y2)
        area_mag = 0.5 * np.sqrt(nx * nx + ny * ny + nz * nz)
        return area_mag, pts

    # -------------------------------------------------------------------------
    def _create_nodes(self) -> None:
        """Create OpenSees nodes from model_data.nodes.
        Also records the set of created node tags for subdivision tracking.
        """
        if self.config['verbose']:
            print("Creating nodes...")
        self._created_node_tags: set = set()
        for node in self.model.nodes.values():
            ops.node(node.node_tag, node.x, node.y, node.z)
            self._created_node_tags.add(node.node_tag)

    # =========================================================================
    # Boundary conditions
    # =========================================================================
    def _apply_restraints(self) -> None:
        if self.config['verbose']:
            print("Applying restraints...")
        for node_id, restraint in self.model.restraints.items():
            tag = self.model.nodes[node_id].node_tag
            ops.fix(tag, *restraint.dofs[:6])

    # -------------------------------------------------------------------------
    # Materials (placeholder)
    # -------------------------------------------------------------------------
    def _create_materials(self) -> None:
        """Create OpenSees uniaxial materials for fiber sections.

        For each frame section that will use fiber patches, a ``Steel01``
        (or ``Concrete01``) material is created with the same tag as the
        section, so that :meth:`Section.to_fiber_patches` can reference it.

        When ``brace_truss=True``, also creates ``Hysteretic`` materials
        for brace sections with asymmetric tension/compression to capture
        buckling (Approach B).
        """
        if not self.config['create_fiber_sections'] and not self.config.get('brace_truss'):
            return
        if self.config['verbose']:
            print("Creating materials for fiber sections...")

        # ── Truss brace materials (Hysteretic with compression degradation) ──
        if self.config.get('brace_truss'):
            from ..model.sap_data import (
                PipeSection, AngleSection, DoubleAngleSection,
                TeeSection, ChannelSection,
            )
            brace_types = (
                PipeSection, AngleSection, DoubleAngleSection,
                TeeSection, ChannelSection,
            )
            self._truss_mat_tags: Dict[str, int] = {}  # section_name -> mat_tag
            self._truss_areas: Dict[str, float] = {}   # section_name -> area
            # Start material tags after all section tags (which will be created
            # later in _create_sections).  Use a large offset to avoid collision.
            n_sec = len(self.model.sections)
            mat_tag = n_sec + 1

            # Determine which sections to treat as braces:
            # brace_sections list overrides auto-detection by shape type.
            explicit = self.config.get('brace_sections')
            for sec_name, sec in self.model.sections.items():
                if explicit is not None:
                    if sec_name not in explicit:
                        continue
                elif not isinstance(sec, brace_types):
                    continue
                mat_obj = self.model.materials.get(sec.material)
                if mat_obj is None:
                    continue
                A = sec.A if sec.A and sec.A > 0 else 1e-4
                E = mat_obj.E_mod if mat_obj.E_mod and mat_obj.E_mod > 0 else 2.0e8
                Fy = mat_obj.Fy if mat_obj.Fy and mat_obj.Fy > 0 else 2.75e5

                # Euler buckling stress for the longest brace of this section
                # (conservative — use the longest brace as reference)
                max_L = 0.0
                for eid, elem in self.model.frame_elements.items():
                    if self.model.frame_assignments.get(eid) != sec_name:
                        continue
                    ni = self.model.nodes.get(elem.node_i)
                    nj = self.model.nodes.get(elem.node_j)
                    if ni is None or nj is None:
                        continue
                    L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
                    max_L = max(max_L, L)

                I22 = sec.I22 if sec.I22 and sec.I22 > 0 else sec.I33
                if max_L > 0 and I22 > 0:
                    P_cr = (math.pi ** 2 * E * I22) / (max_L ** 2)
                    sig_cr = P_cr / A  # buckling stress
                else:
                    sig_cr = Fy * 0.3  # fallback: 30% of yield

                eps_y = Fy / E
                eps_cr = sig_cr / E if E > 0 else 0.001

                # Hysteretic material: 3 points in tension, 3 in compression
                # Tension: (0,0) -> yield -> slight hardening
                s1p, e1p = Fy, eps_y
                s2p, e2p = Fy * 1.01, eps_y + 0.01
                s3p, e3p = Fy * 1.02, eps_y + 0.05
                # Compression: (0,0) -> buckling -> post-buckling residual
                s1n, e1n = -sig_cr, -eps_cr
                s2n, e2n = -sig_cr * 0.2, -eps_cr - 0.01
                s3n, e3n = -sig_cr * 0.1, -eps_cr - 0.05

                ops.uniaxialMaterial('Hysteretic', mat_tag,
                                     s1p, e1p, s2p, e2p, s3p, e3p,
                                     s1n, e1n, s2n, e2n, s3n, e3n,
                                     1.0, 1.0, 0.0, 0.0, 0.0)

                # Optionally wrap with Fatigue for low-cycle degradation
                if self.config.get('brace_fatigue'):
                    fatigue_tag = mat_tag + 1
                    E0 = self.config.get('brace_fatigue_E0', 0.095)
                    m = self.config.get('brace_fatigue_m', -0.3)
                    ops.uniaxialMaterial('Fatigue', fatigue_tag, mat_tag,
                                         '-E0', E0, '-m', m)
                    use_tag = fatigue_tag
                    fatigue_note = f" + Fatigue({fatigue_tag})"
                    mat_tag += 1  # consume an extra tag for Fatigue
                else:
                    use_tag = mat_tag
                    fatigue_note = ""

                self._truss_mat_tags[sec_name] = use_tag
                self._truss_areas[sec_name] = A
                if self.config['verbose']:
                    hysteretic_tag = mat_tag if not self.config.get('brace_fatigue') else mat_tag
                    print(f"  Truss brace Hysteretic({hysteretic_tag}){fatigue_note}: {sec_name} "
                          f"(A={A:.6f}, Fy={Fy:.0f}, sig_cr={sig_cr:.0f})")
                mat_tag += 1

    # -------------------------------------------------------------------------
    # Sections
    # -------------------------------------------------------------------------
    def _create_sections(self) -> None:
        """Create OpenSees sections (elastic or fiber)."""
        if self.config['verbose']:
            print("Creating sections...")
        # Map section name -> tag
        self.section_tags: Dict[str, int] = {}
        tag = 1
        for sec_name, sec in self.model.sections.items():
            self.section_tags[sec_name] = tag
            self._create_single_section(sec, tag)
            tag += 1

    def _create_single_section(self, sec: Section, tag: int) -> None:
        """Create one OpenSees section (elastic or fiber)."""
        # Get material properties
        mat = self.model.materials.get(sec.material)
        if mat is None:
            E_mod = 2.1e11   # default steel in Pa
            G_mod = 8.077e10
        else:
            E_mod = mat.E_mod
            G_mod = mat.G_mod
            if G_mod == 0 and E_mod > 0:
                nu = mat.nu if mat.nu > 0 else 0.3
                G_mod = E_mod / (2 * (1 + nu))

        if self.config['use_elastic_sections']:
            ops.section('Elastic', tag, E_mod, sec.A, sec.I33, sec.I22, G_mod, sec.J)
            if self.config['verbose']:
                print(f"  Section {tag}: {sec.name} (Elastic)")

        elif self.config['create_fiber_sections']:
            # Shell sections can't use fiber patches; create elastic instead
            if isinstance(sec, ShellSection):
                ops.section('Elastic', tag, E_mod, sec.A, sec.I33, sec.I22, G_mod, sec.J)
                if self.config['verbose']:
                    print(f"  Section {tag}: {sec.name} (Elastic — shell)")
                return

            # ── Create uniaxial material for fibers ──
            mat_tag = tag  # same tag as the section for simplicity
            from ..model.sap_data import (
                ConcreteRectangularSection, ConcreteCircularSection,
            )
            is_concrete = isinstance(sec, (ConcreteRectangularSection, ConcreteCircularSection))

            if is_concrete:
                # Concrete section: need 3 materials (unconfined, confined, steel)
                # Use a dedicated running counter so no two sections overlap.
                if not hasattr(self, '_next_concrete_mat_tag'):
                    self._next_concrete_mat_tag = tag + len(self.model.sections) + 1
                concrete_mat_tag = self._next_concrete_mat_tag
                self._next_concrete_mat_tag += 3
                if mat is not None and mat.type.lower() == 'concrete':
                    Fc = mat.Fc if mat.Fc and mat.Fc > 0 else 3.0e7
                    Ec = mat.E_mod if mat.E_mod > 0 else 2.5e10
                    epsc = mat.eFc if mat.eFc and mat.eFc > 0 else 0.002
                    ops.uniaxialMaterial('Concrete01', concrete_mat_tag,
                                         -Fc, -abs(epsc), -0.2 * Fc, -0.006)
                    fcc = Fc * 1.3
                    epscc = 0.005
                    ops.uniaxialMaterial('Concrete01', concrete_mat_tag + 1,
                                         -fcc, -abs(epscc), -0.2 * fcc, -0.02)
                    Fy = mat.Fy if mat.Fy and mat.Fy > 0 else 4.0e8
                    ops.uniaxialMaterial('Steel02', concrete_mat_tag + 2,
                                         Fy, 2.0e11, 0.01, 18.5, 0.925, 0.15)
                else:
                    ops.uniaxialMaterial('Concrete01', concrete_mat_tag, -3.0e7, -0.002,
                                         -6.0e6, -0.006)
                    ops.uniaxialMaterial('Concrete01', concrete_mat_tag + 1,
                                         -3.9e7, -0.005, -7.8e6, -0.02)
                    ops.uniaxialMaterial('Steel02', concrete_mat_tag + 2,
                                         4.0e8, 2.0e11, 0.01, 18.5, 0.925, 0.15)
                fiber_mat_tag = concrete_mat_tag
            elif mat is not None and mat.type.lower() == 'steel':
                Fy = mat.Fy if mat.Fy and mat.Fy > 0 else 2.5e8
                E = mat.E_mod if mat.E_mod > 0 else 2.0e11
                ops.uniaxialMaterial('Steel01', mat_tag, Fy, E, 0.01)
                fiber_mat_tag = mat_tag
            elif mat is not None and mat.type.lower() == 'concrete':
                Fc = mat.Fc if mat.Fc and mat.Fc > 0 else 3.0e7
                epsc = mat.eFc if mat.eFc and mat.eFc > 0 else 0.002
                ops.uniaxialMaterial('Concrete01', mat_tag, -Fc, -abs(epsc), -0.2 * Fc, -0.006)
                fiber_mat_tag = mat_tag
            else:
                ops.uniaxialMaterial('Steel01', mat_tag, 2.5e8, 2.0e11, 0.01)
                fiber_mat_tag = mat_tag

            # ── Create fiber section ──
            ops.section('Fiber', tag, '-GJ', sec.J)
            try:
                entries = sec.to_fiber_patches(mat_tag=fiber_mat_tag)
                for entry in entries:
                    if entry[0] in ('rect', 'circ', 'quad'):
                        ops.patch(*entry)
                    elif entry[0] == 'straight':
                        ops.layer('straight', *entry[1:])
                    elif entry[0] == 'circ_layer':
                        ops.layer('circ', *entry[1:])
                if self.config['verbose']:
                    print(f"  Section {tag}: {sec.name} (Fiber, {len(entries)} entries)")
            except NotImplementedError as exc:
                if self.config['verbose']:
                    print(f"  Section {tag}: {sec.name} — {exc}, falling back to elastic")
                ops.section('Elastic', tag, E_mod, sec.A, sec.I33, sec.I22, G_mod, sec.J)

        else:
            # Fallback to elastic
            ops.section('Elastic', tag, E_mod, sec.A, sec.I33, sec.I22, G_mod, sec.J)
            if self.config['verbose']:
                print(f"  Section {tag}: {sec.name} (Elastic fallback)")

    # -------------------------------------------------------------------------
    # Element splitting
    # -------------------------------------------------------------------------
    def _split_elements(self) -> None:
        """Perform element splitting using geometry.split_elements."""
        from ..model.geometry import split_elements

        # Call split_elements with the model data
        new_elements, new_assignments, new_dist_loads = split_elements(
            self.model.nodes,
            self.model.frame_elements,
            self.model.frame_assignments,
            getattr(self.model, 'frame_dist_loads', []),   # pass the list of distributed loads
            getattr(self.model, 'frame_auto_mesh', {}),
            tol=1E-6,
            verbose=self.config['verbose']
        )
        self.split_elements = new_elements
        self.split_assignments = new_assignments
        self.split_dist_loads = new_dist_loads   # store for later use in _create_loads

    # -------------------------------------------------------------------------
    # Frame end offsets (rigid zones at joints)
    # -------------------------------------------------------------------------

    def _apply_frame_end_offsets(self) -> None:
        """Apply frame end offsets from parsed SAP2000 data.

        Creates offset nodes and records rigid link entries that are
        later created as stiff beam elements in ``_create_elements``.
        """
        from ..model.geometry import apply_frame_end_offsets
        from ..model.sap_data import FrameEndOffset

        # Operate on the same element collection that _create_elements will
        # consume — split elements if available, otherwise originals.
        if self.split_elements is not None:
            elements = self.split_elements
            assignments = self.split_assignments
        else:
            elements = self.model.frame_elements
            assignments = self.model.frame_assignments

        # Resolve offsets keyed by original element IDs onto the active
        # element collection.  When the original element has been split,
        # its offset applies only to the first child (I-end) and last
        # child (J-end); middle children get no offset.
        resolved_offsets: Dict[str, FrameEndOffset] = {}
        for eid, off in self.model.frame_end_offsets.items():
            elem = elements.get(eid)
            if elem is None:
                continue
            if getattr(elem, 'inactive', False) and hasattr(elem, 'child_ids') and elem.child_ids:
                # Parent was split — apply offsets to end children.
                child_ids = elem.child_ids
                # I-end offset → first child
                first_child = elements.get(child_ids[0])
                if first_child is not None and off.end_i > 0:
                    resolved_offsets[child_ids[0]] = FrameEndOffset(
                        end_i=off.end_i, end_j=0.0,
                    )
                # J-end offset → last child
                last_child = elements.get(child_ids[-1])
                if last_child is not None and off.end_j > 0:
                    resolved_offsets[child_ids[-1]] = FrameEndOffset(
                        end_i=0.0, end_j=off.end_j,
                    )
            else:
                resolved_offsets[eid] = off

        max_elem_tag = max(
            (e.elem_tag for e in elements.values()), default=0
        )
        max_node_tag = max(
            (nd.node_tag for nd in self.model.nodes.values()), default=0
        )
        next_tag = max(max_elem_tag, max_node_tag) + 1

        nodes = self.model.nodes

        elements, assignments, nodes, next_tag, rigid_links = apply_frame_end_offsets(
            elements, assignments, nodes,
            resolved_offsets,
            next_tag=next_tag,
        )
        self._offset_rigid_links = rigid_links

        # Write back so _create_elements picks up the offset‑adjusted data.
        if self.split_elements is not None:
            self.split_elements = elements
            self.split_assignments = assignments
        else:
            self.model.frame_elements = elements
            self.model.frame_assignments = assignments
        self.model.nodes = nodes

        # Create OpenSees nodes for the offset nodes
        for nd in self.model.nodes.values():
            if nd.node_tag not in self._created_node_tags:
                ops.node(nd.node_tag, nd.x, nd.y, nd.z)
                self._created_node_tags.add(nd.node_tag)

        if self.config['verbose'] and rigid_links:
            print(f"  Created {len(rigid_links)} rigid link(s) "
                  f"for frame end offsets")

    # -------------------------------------------------------------------------
    # Area meshing
    # -------------------------------------------------------------------------

    def _mesh_areas(self,
                    selection: Optional[Selection] = None,
                    ) -> None:
        """Subdivide area elements per parsed AREA MESH ASSIGNMENTS.

        Must be called before ``_create_shell_elements`` so that meshed
        sub-areas become ``ShellMITC4`` elements instead of the original
        coarse area.

        Args:
            selection: Optional :class:`Selection`.  Areas matching this
                selection are loads‑only and will **not** be meshed.
        """
        if not self.model.area_mesh:
            return

        # Exclude loads-only areas from meshing
        if selection is not None:
            loads_only = set(selection.get_area_ids(self.model))
            mesh_filtered = {
                aid: m for aid, m in self.model.area_mesh.items()
                if aid not in loads_only
            }
        else:
            mesh_filtered = self.model.area_mesh

        if not mesh_filtered:
            return

        from ..model.geometry import mesh_area_elements

        max_elem_tag = max(
            (ae.area_tag for ae in self.model.area_elements.values()), default=0
        )
        max_node_tag = max(
            (nd.node_tag for nd in self.model.nodes.values()), default=0
        )
        next_tag = max(max_elem_tag, max_node_tag) + 1

        areas, assignments, nodes, next_tag = mesh_area_elements(
            self.model.area_elements,
            self.model.area_assignments,
            self.model.nodes,
            mesh_filtered,
            next_tag=next_tag,
        )
        # Update model with meshed data
        self.model.area_elements = areas
        self.model.area_assignments = assignments
        self.model.nodes = nodes

        # Create OpenSees nodes for mesh nodes
        for nd in self.model.nodes.values():
            if nd.node_tag not in self._created_node_tags:
                ops.node(nd.node_tag, nd.x, nd.y, nd.z)
                self._created_node_tags.add(nd.node_tag)

        if self.config['verbose']:
            sub_count = sum(1 for aid in areas if "_sub_" in aid)
            if sub_count:
                print(f"  Area meshing: {sub_count} sub-elements created")

    # -------------------------------------------------------------------------
    # Elements
    # -------------------------------------------------------------------------

    def _get_local_axes(self, elem: FrameElement) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return local y and z unit vectors for a frame element."""
        #  Could use lookup table - maybe faster
        if self._node_tag_from_id:
            coords_i = ops.nodeCoord(self._node_tag_from_id(elem.node_i))
            coords_j = ops.nodeCoord(self._node_tag_from_id(elem.node_j))
        else:
            coords_i = ops.nodeCoord(self.model.nodes[elem.node_i].node_tag)
            coords_j = ops.nodeCoord(self.model.nodes[elem.node_j].node_tag)
        vec_x = np.array(coords_j) - np.array(coords_i)
        length = np.linalg.norm(vec_x)
        if length < 1e-12:
            raise ValueError(f"Zero length element {elem.elem_id} (tag {elem.elem_tag}) between nodes {elem.node_i} and {elem.node_j}")
        vec_x_norm = vec_x / length
        vecxz = get_SAP_vecxz(vec_x_norm, elem.angle)
        # local z is vecxz
        vec_z = vecxz / np.linalg.norm(vecxz)
        # local y = cross(vec_z, vec_x)  (right‑handed)
        vec_y = np.cross(vec_z, vec_x_norm)
        vec_y = vec_y / np.linalg.norm(vec_y)
        return vec_x_norm, vec_y, vec_z

    def _global_to_local(self, elem: FrameElement, vec: np.ndarray) -> np.ndarray:
        """Transform a vector from global to local coordinates."""
        vec_x, vec_y, vec_z = self._get_local_axes(elem)
        
        # Create the transformation matrix (3x3)
        T = np.vstack([vec_x, vec_y, vec_z])
        
        # Use matrix multiplication (@ operator) to transform the vector
        return T @ vec

    def _create_elements(self) -> None:
        """Create OpenSees frame elements, using split elements if available."""
        if self.config['verbose']:
            print("Creating elements...")

        # Choose source — apply brace subdivision if configured
        if self.split_elements:
            elements = self.split_elements
            assignments = self.split_assignments
        else:
            elements = self.model.frame_elements
            assignments = self.model.frame_assignments

        # Subdivide braces (Approach A: subdivided elements + imperfection)
        self._rigid_link_elems: List[tuple] = []
        if self.config.get('subdivide_braces') and hasattr(self, '_brace_selection'):
            nodes = self.model.nodes
            max_elem_tag = max((e.elem_tag for e in elements.values()), default=0)
            max_node_tag = max((nd.node_tag for nd in self.model.nodes.values()), default=0)
            # Account for shell element tags already created in OpenSees so
            # brace subdivision / rigid link tags don't collide with shells.
            try:
                max_ops_tag = max(ops.getEleTags(), default=0)
            except Exception:
                max_ops_tag = 0
            max_rigid_tag = max(
                (r[3] for r in self._offset_rigid_links),
                default=0,
            )
            next_tag = max(max_elem_tag, max_node_tag, max_ops_tag, max_rigid_tag) + 1
            from ..model.geometry import subdivide_elements
            elements, assignments, nodes, next_tag, rigid_links = subdivide_elements(
                elements, assignments, nodes,
                n_segments=self.config.get('brace_n_segments', 4),
                imperfection_ratio=self.config.get('brace_imperfection_ratio', 1.0/500.0),
                brace_ids=self._brace_selection,
                end_offset=self.config.get('brace_end_offset', 0.0),
                next_tag=next_tag,
            )
            self._rigid_link_elems = rigid_links
            self.model.nodes = nodes
            # Create OpenSees nodes for subdivision/offset nodes.
            # Only create nodes that weren't already created by _create_nodes().
            for nd in self.model.nodes.values():
                if nd.node_tag not in self._created_node_tags:
                    ops.node(nd.node_tag, nd.x, nd.y, nd.z)
                    self._created_node_tags.add(nd.node_tag)

        # Build frame_tag_map for loads (and for element creation if needed)
        self.frame_tag_map = {
            eid: elem.elem_tag
            for eid, elem in elements.items()
            if not elem.inactive
        }

        # Create rigid link elements (stiff elastic segments)
        all_rigid_links = list(self._rigid_link_elems) + list(self._offset_rigid_links)
        if all_rigid_links:
            if self.config['verbose']:
                print(f"  Creating {len(all_rigid_links)} rigid links "
                      f"(brace offsets + frame end offsets)...")
            # Dedicated super‑stiff section for rigid links
            rigid_sec_tag = max(self.section_tags.values()) + 1 if self.section_tags else 10000
            # A × E large enough to be effectively rigid over a short offset
            rigid_E = 2e14  # 1000× steel E
            rigid_A = 1.0   # 1 m²
            rigid_I = 1.0   # 1 m⁴
            # section('Elastic', tag, E, A, Iz, Iy, G, J)
            ops.section('Elastic', rigid_sec_tag, rigid_E, rigid_A, rigid_I, rigid_I, rigid_E / 2.6, rigid_I)

            for link_id, nid_i, nid_j, link_tag in all_rigid_links:
                node_i_tag = self._node_tag_from_id(nid_i)
                node_j_tag = self._node_tag_from_id(nid_j)
                if node_i_tag is None or node_j_tag is None:
                    continue
                # Compute a reasonable vecxz from the link direction
                try:
                    xi, yi, zi = ops.nodeCoord(node_i_tag)
                    xj, yj, zj = ops.nodeCoord(node_j_tag)
                except Exception:
                    xi = yi = zi = xj = yj = zj = 0.0
                dx, dy, dz = xj - xi, yj - yi, zj - zi
                length = math.hypot(dx, dy, dz)
                if length < 1e-12:
                    continue
                # Choose a reference vector that's not parallel to the link
                ref_x, ref_y, ref_z = (0.0, 0.0, 1.0)
                if abs(dz / length) > 0.99:
                    ref_x, ref_y, ref_z = (1.0, 0.0, 0.0)
                transf_type = self.config.get('geom_transf_type', 'Linear')
                transf_tag = link_tag * 10  # keep transf tags distinct
                ops.geomTransf(transf_type, transf_tag, ref_x, ref_y, ref_z)
                ops.element('elasticBeamColumn', link_tag,
                            node_i_tag, node_j_tag,
                            rigid_sec_tag, transf_tag)

        for elem_id, elem in elements.items():
            if elem.inactive:
                continue
            
            # Get section name from assignments
            sec_name = '' if (assignments is None) else assignments.get(elem_id)
            if (not sec_name) or (sec_name not in self.section_tags):
                if self.config['verbose']:
                    print(f"  Skipping element {elem_id}: no valid section")
                continue
            if self._node_tag_from_id: 
                node_i_tag = self._node_tag_from_id(elem.node_i)
                node_j_tag = self._node_tag_from_id(elem.node_j)
            else:
                node_i_tag = self.model.nodes[elem.node_i].node_tag
                node_j_tag = self.model.nodes[elem.node_j].node_tag
            angle = elem.angle
            sec_tag = self.section_tags[sec_name]
            # Use element tag from elem['id']
            elem_tag = elem.elem_tag

            if (node_i_tag is not None) and (node_j_tag is not None):
                # Truss brace elements (Approach B — Hysteretic material)
                if (self.config.get('brace_truss')
                        and hasattr(self, '_truss_mat_tags')
                        and sec_name in self._truss_mat_tags):
                    mat_tag = self._truss_mat_tags[sec_name]
                    A = self._truss_areas[sec_name]
                    ops.element('Truss', elem_tag, node_i_tag, node_j_tag, A, mat_tag)
                    if self.config['verbose']:
                        print(f"  Truss {elem_tag}: {elem_id} ({sec_name}) "
                              f"A={A:.6f}")
                else:
                    self._add_beam_column(node_i_tag, node_j_tag, sec_tag, elem_tag, angle)

    def _add_beam_column(self, node_i: int, node_j: int, sec_tag: int,
                         elem_tag: int, angle_deg: float) -> None:
        """Create a beam‑column element with geometric transformation."""

        coords_i = ops.nodeCoord(node_i)
        coords_j = ops.nodeCoord(node_j)
        vec_x = np.array(coords_j) - np.array(coords_i)
        length = np.linalg.norm(vec_x)
        if length < 1e-12:
            raise ValueError(f"Zero length element {elem_tag} between nodes {node_i} and {node_j}")

        # Determine orientation vector vecxz
        vec_x_norm = vec_x / length
        vecxz = get_SAP_vecxz(vec_x_norm, angle_deg)

        # Create geometric transformation
        transf_type = self.config.get('geom_transf_type', 'Linear')
        if transf_type == 'Corotational' and self.config['verbose']:
            print("  Note: Corotational geomTransf + eleLoad is not supported "
                  "in 3D OpenSees. Pushover lateral loads are applied as nodal "
                  "loads so this is safe for pushover. For static analysis with "
                  "distributed loads, use 'Linear' or 'PDelta'.")
        transf_tag = elem_tag
        ops.geomTransf(transf_type, transf_tag, *vecxz)

        # Create element based on type
        elem_type = self.config['element_type'].lower()
        if elem_type == 'elasticbeamcolumn':
            ops.element('elasticBeamColumn', elem_tag, node_i, node_j,
                        sec_tag, transf_tag)
        elif elem_type == 'forcebeamcolumn':
            int_tag = elem_tag
            beam_int = self.config.get('beam_integration', 'Lobatto')
            if beam_int == 'HingeRadau':
                # Plastic hinge length from section geometry.
                # OpenSeesPy signature:
                #   beamIntegration('HingeRadau', tag, secI, lpI, secJ, lpJ, secE)
                # secI / secJ  — section at element I / J ends (hinge zones)
                # lpI / lpJ    — plastic hinge length at each end
                # secE         — interior (elastic) section
                Lp = self._compute_hinge_length(sec_tag, length)
                ops.beamIntegration('HingeRadau', int_tag,
                                    sec_tag, Lp, sec_tag, Lp, sec_tag)
            else:
                npts = self.config['num_int_pts']
                ops.beamIntegration('Lobatto', int_tag, sec_tag, npts)
            ops.element('forceBeamColumn', elem_tag, node_i, node_j,
                        transf_tag, int_tag)
        elif elem_type == 'dispbeamcolumn':
            int_tag = elem_tag
            npts = self.config['num_int_pts']
            ops.beamIntegration('Lobatto', int_tag, sec_tag, npts)
            ops.element('dispBeamColumn', elem_tag, node_i, node_j,
                        transf_tag, int_tag)
        elif elem_type == 'nonlinearbeamcolumn':
            npts = self.config['num_int_pts']
            ops.element('nonlinearBeamColumn', elem_tag, node_i, node_j,
                        npts, sec_tag, transf_tag)
        else:
            raise ValueError(f"Unsupported element_type: {elem_type}")

        if self.config['verbose']:
            print(f"  Element {elem_tag}: {node_i} -> {node_j}")

    # -------------------------------------------------------------------------
    # Shell edge constraints (unconnected mesh edges)
    # -------------------------------------------------------------------------

    def _get_shell_area_ids(self) -> set:
        """Return the set of area element IDs that became actual shell elements.

        Uses the same filtering logic as :meth:`_create_shell_elements`:
        areas in the loads-only selection are excluded.

        When ``create_shells`` is ``False`` (no shells built), all areas
        are still returned to support diagnostic detection of unconnected
        edges before deciding whether to create shells.
        """
        sel = getattr(self, '_area_selection', None)
        if self.config.get('create_shells', False) and sel is not None:
            loads_only = set(sel.get_area_ids(self.model))
            return {aid for aid in self.model.area_elements
                    if aid not in loads_only
                    and not getattr(self.model.area_elements[aid], 'inactive', False)}
        return {aid for aid in self.model.area_elements
                if not getattr(self.model.area_elements[aid], 'inactive', False)}

    def detect_unconnected_edges(
        self,
        tolerance: float = 1e-4,
        include_frame_connections: bool = False,
    ) -> List[Dict[str, Any]]:
        """Scan shell elements and report fine-mesh nodes that sit on
        coarse-mesh edges without being directly connected.

        This is a **diagnostic** tool — it identifies locations where
        SAP2000 would apply Auto Edge Constraints.  Use its output to
        build the mapping for :meth:`apply_edge_constraints`.

        Parameters
        ----------
        tolerance : float
            Maximum perpendicular distance from a node to a line segment
            for it to be considered "on the edge".
        include_frame_connections : bool
            Also check whether frame element nodes align with shell edges.
            Slower but useful when shell elements connect to frame elements
            at non-nodal points.

        Returns
        -------
        List[Dict[str, Any]]
            Each entry::
                {
                    "slave_node": int,
                    "master_node_i": int,
                    "master_node_j": int,
                    "coords": (x, y, z),
                    "N1": float,   # weight for master_node_i
                    "N2": float,   # weight for master_node_j
                    "edge_length": float,
                    "distance": float,  # perpendicular distance to edge
                }
        """
        reports: List[Dict[str, Any]] = []

        # Determine which areas actually became shell elements
        # (loads-only areas are not created as shells)
        shell_area_ids = self._get_shell_area_ids()
        if not shell_area_ids:
            return reports

        # Collect all shell element edges (deduplicated) from shell areas only.
        # Map model node IDs → OpenSees node tags for ops calls.
        edge_set: set = set()
        for eid in shell_area_ids:
            elem = self.model.area_elements[eid]
            nodes = elem.node_ids
            for j in range(len(nodes)):
                t1 = self._node_tag_from_id(nodes[j])
                t2 = self._node_tag_from_id(nodes[(j + 1) % len(nodes)])
                if t1 is None or t2 is None:
                    continue
                edge_set.add((min(t1, t2), max(t1, t2)))
        all_edges = list(edge_set)

        if not all_edges:
            return reports

        # Collect all shell node tags (for slave detection) from shell areas only
        shell_node_set: set = set()
        for eid in shell_area_ids:
            elem = self.model.area_elements[eid]
            for n_id in elem.node_ids:
                tag = self._node_tag_from_id(n_id)
                if tag is not None:
                    shell_node_set.add(tag)

        if include_frame_connections:
            for eid, elem in self.model.frame_elements.items():
                for n_id in (elem.node_i, elem.node_j):
                    tag = self._node_tag_from_id(n_id)
                    if tag is not None:
                        shell_node_set.add(tag)

        all_slave_nodes = sorted(shell_node_set)

        # For each edge, check each slave node
        for m1_tag, m2_tag in all_edges:
            try:
                c1 = np.array(ops.nodeCoord(m1_tag))
                c2 = np.array(ops.nodeCoord(m2_tag))
            except Exception:
                continue

            edge_vec = c2 - c1
            edge_len = np.linalg.norm(edge_vec)
            if edge_len < 1e-12:
                continue

            for s_tag in all_slave_nodes:
                if s_tag == m1_tag or s_tag == m2_tag:
                    continue
                try:
                    cs = np.array(ops.nodeCoord(s_tag))
                except Exception:
                    continue

                cross_prod = np.cross(cs - c1, cs - c2)
                distance = np.linalg.norm(cross_prod) / edge_len

                if distance > tolerance:
                    continue

                proj = np.dot(cs - c1, edge_vec) / edge_len
                if 0.0 < proj < edge_len:
                    N2 = proj / edge_len
                    N1 = 1.0 - N2
                    reports.append({
                        "slave_node": s_tag,
                        "master_node_i": m1_tag,
                        "master_node_j": m2_tag,
                        "coords": tuple(cs),
                        "master_coords_i": tuple(c1),
                        "master_coords_j": tuple(c2),
                        "N1": round(N1, 6),
                        "N2": round(N2, 6),
                        "edge_length": round(edge_len, 6),
                        "distance": round(distance, 8),
                    })

        return reports

    def apply_edge_constraints(
        self,
        coarse_edges: Optional[List[Tuple[int, int]]] = None,
        fine_nodes: Optional[List[int]] = None,
        coarse_elements: Optional[List[int]] = None,
        tolerance: float = 1e-4,
        verbose: bool = True,
    ) -> int:
        """Apply ETABS-style linear edge constraints between coarse and
        fine shell meshes.

        Unaligned slave nodes that lie on coarse-mesh edges are tied via
        ``ops.mpc()`` with interpolation weights based on their position
        along the edge.  All six DOFs are constrained.

        .. note::
            After calling this method the solver constraint handler is
            automatically set to **Penalty** (``1e12, 1e12``) in
            subsequent analysis runs.  Do **not** set
            ``solver_constraints`` to ``"Transformation"`` in the config
            when edge constraints are present.

        Parameters
        ----------
        coarse_edges : list of (int, int) or None
            Explicit master edge node pairs, e.g. ``[(10, 11), (11, 12)]``.
        fine_nodes : list of int or None
            Slave node IDs to check.  If ``None``, all shell nodes are
            candidates.
        coarse_elements : list of int or None
            Instead of *coarse_edges*, provide shell element tags to
            auto-extract their boundary edges.  E.g. ``[1001, 1002]``.
        tolerance : float
            Max perpendicular distance to consider a slave node "on the edge".
        verbose : bool
            Print progress messages.

        Returns
        -------
        int
            Number of multi-point constraints applied.
        """
        # ── Resolve master edges ────────────────────────────────────
        edge_set: set = set()
        if coarse_elements is not None:
            for etag in coarse_elements:
                try:
                    nodes = ops.eleNodes(int(etag))
                except Exception:
                    continue
                for j in range(len(nodes)):
                    n1 = nodes[j]
                    n2 = nodes[(j + 1) % len(nodes)]
                    edge_set.add((min(n1, n2), max(n1, n2)))
        if coarse_edges is not None:
            for n1, n2 in coarse_edges:
                # Accept both model node IDs (str) and direct OpenSees tags (int)
                t1 = self._node_tag_from_id(str(n1)) if not isinstance(n1, int) else n1
                t2 = self._node_tag_from_id(str(n2)) if not isinstance(n2, int) else n2
                if t1 is None:
                    t1 = int(n1)
                if t2 is None:
                    t2 = int(n2)
                edge_set.add((min(t1, t2), max(t1, t2)))
        if not edge_set:
            print("No master edges provided — nothing to constrain.")
            return 0

        # ── Resolve slave nodes ─────────────────────────────────────
        if fine_nodes is not None:
            # Accept both model node IDs (str) and direct OpenSees tags (int)
            slave_candidates = []
            for n in fine_nodes:
                tag = self._node_tag_from_id(str(n)) if not isinstance(n, int) else n
                if tag is None:
                    tag = int(n)
                slave_candidates.append(tag)
        else:
            # Default: nodes from areas that actually became shells
            shell_area_ids = self._get_shell_area_ids()
            all_nodes: set = set()
            for eid in shell_area_ids:
                elem = self.model.area_elements[eid]
                for n_id in elem.node_ids:
                    tag = self._node_tag_from_id(n_id)
                    if tag is not None:
                        all_nodes.add(tag)
            slave_candidates = sorted(all_nodes)

        # ── Apply constraints ───────────────────────────────────────
        count = 0
        for m1_id, m2_id in edge_set:
            try:
                c1 = np.array(ops.nodeCoord(m1_id))
                c2 = np.array(ops.nodeCoord(m2_id))
            except Exception:
                continue
            edge_vec = c2 - c1
            edge_len = np.linalg.norm(edge_vec)
            if edge_len < 1e-12:
                continue

            for s_id in slave_candidates:
                if s_id == m1_id or s_id == m2_id:
                    continue
                try:
                    cs = np.array(ops.nodeCoord(s_id))
                except Exception:
                    continue

                cross_prod = np.cross(cs - c1, cs - c2)
                distance = np.linalg.norm(cross_prod) / edge_len
                if distance > tolerance:
                    continue

                proj = np.dot(cs - c1, edge_vec) / edge_len
                if 0.0 < proj < edge_len:
                    N2 = proj / edge_len
                    N1 = 1.0 - N2
                    for dof in range(1, 7):
                        # equationConstraint: 1.0*U_slave - N1*U_m1 - N2*U_m2 = 0
                        # Requires Penalty constraint handler.
                        ops.equationConstraint(
                            int(s_id), dof, 1.0,
                            int(m1_id), dof, -N1,
                            int(m2_id), dof, -N2,
                        )
                    count += 1
                    if verbose:
                        print(
                            f"  Edge constraint: node {s_id} → "
                            f"edge ({m1_id}–{m2_id})  "
                            f"(N1={N1:.3f}, N2={N2:.3f})"
                        )

        if count:
            self._has_edge_constraints = True
            if verbose:
                print(f"Applied {count} edge constraint(s). "
                      f"Solver will use Penalty handler.")

        return count

    # -------------------------------------------------------------------------
    # Brace subdivision
    # -------------------------------------------------------------------------
    def set_brace_selection(self, brace_ids: set, end_offset: float = 0.0) -> None:
        """Mark specific frame elements as braces for subdivision.

        Call **before** :meth:`build`.  The elements identified by *brace_ids*
        will be subdivided into *brace_n_segments* segments with an initial
        imperfection (Approach A — subdivided element with Corotational geom
        to capture buckling).

        Args:
            brace_ids: Set of frame element ID strings to treat as braces.
            end_offset: Distance from each working point to the gusset plate
                face (model length units).  Creates rigid link segments
                between the working point and the brace physical end.
                Default 0.0 (no offset).  Typical value for steel gusset
                plates: 0.1–0.3 m.
        """
        self._brace_selection = brace_ids
        self.config['subdivide_braces'] = True
        if end_offset > 0:
            self.config['brace_end_offset'] = end_offset

    def check_brace_buckling(
        self,
        brace_ids: Optional[set] = None,
        K: float = 1.0,
        axial_demand: Optional[Dict[str, float]] = None,
        print_results: bool = True,
    ) -> Dict[str, Dict[str, float]]:
        """Check selected braces against Euler buckling.

        Computes :math:`P_{cr} = \\frac{\\pi^2 E I_{22}}{(K L)^2}` for each
        brace and optionally compares against provided axial demand.

        Args:
            brace_ids: Set of element IDs to check.  Defaults to
                ``self._brace_selection`` (set via :meth:`set_brace_selection`).
            K: Effective length factor (default 1.0 — pinned-pinned).
            axial_demand: Optional ``{elem_id: axial_force_N}`` dict with
                estimated compressive demand (e.g. from a prior linear static
                analysis).  If provided, the demand/capacity ratio is reported.
            print_results: If True, print a summary table.

        Returns:
            ``{elem_id: {'P_cr': ..., 'P_demand': ..., 'ratio': ...,
                         'slenderness': ..., 'length': ..., 'section': ...}}``
        """
        if brace_ids is None:
            brace_ids = getattr(self, '_brace_selection', set())
        if not brace_ids:
            print("No brace IDs provided.")
            return {}

        # Use the ORIGINAL (pre‑subdivision) model data — after subdivision
        # the original brace element is marked inactive and its sub‑elements
        # use different IDs.  The buckling check is an analytical computation
        # on the original element geometry, so we always read from the source.
        elements = self.model.frame_elements
        assignments = self.model.frame_assignments

        results: Dict[str, Dict[str, float]] = {}
        for eid in brace_ids:
            elem = elements.get(eid)
            if elem is None:
                continue
            sec_name = assignments.get(eid) if assignments else None
            if not sec_name or sec_name not in self.model.sections:
                continue
            sec = self.model.sections[sec_name]
            mat = self.model.materials.get(sec.material)
            if mat is None:
                continue

            ni = self.model.nodes.get(elem.node_i)
            nj = self.model.nodes.get(elem.node_j)
            if ni is None or nj is None:
                continue
            L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
            if L < 1e-12:
                continue

            E = mat.E_mod if mat.E_mod > 0 else 2.0e11
            I22 = sec.I22 if sec.I22 and sec.I22 > 0 else sec.I33
            A = sec.A if sec.A > 0 else 1e-4

            P_cr = (math.pi ** 2 * E * I22) / ((K * L) ** 2)
            r = math.sqrt(I22 / A)
            slenderness = (K * L) / r if r > 0 else float('inf')

            demand = axial_demand.get(eid, 0.0) if axial_demand else 0.0
            ratio = demand / P_cr if P_cr > 0 else float('inf')

            results[eid] = {
                'P_cr': P_cr,
                'P_demand': demand,
                'ratio': ratio,
                'slenderness': slenderness,
                'length': L,
                'section': sec_name,
            }

        if print_results and results:
            # Use the model's native force unit for display
            force_unit = self.model.units.get('F', 'N')

            print(f"\n── Euler buckling check (K={K}) ──")
            header = (f"  {'ID':>12} {'Section':>20} {'L (m)':>8} "
                      f"{'λ':>8} {'P_cr (' + force_unit + ')':>14}")
            if axial_demand:
                header += f" {'P_dem (' + force_unit + ')':>14} {'Ratio':>8}"
            print(header)
            print("  " + "-" * len(header))
            for eid, r in sorted(results.items()):
                line = (f"  {eid:>12} {r['section']:>20} {r['length']:8.3f} "
                        f"{r['slenderness']:8.1f} {r['P_cr']:10.1f}")
                if axial_demand:
                    line += f" {r['P_demand']:10.1f} {r['ratio']:8.3f}"
                print(line)

            if axial_demand:
                n_critical = sum(1 for r in results.values() if r['ratio'] > 0.5)
                if n_critical:
                    print(f"\n  ⚠ {n_critical} brace(s) with demand > 50% of P_cr")
                else:
                    print(f"\n  ✅ All braces with demand < 50% of P_cr")

        return results

    def _compute_asce41_hinge_length(
        self, sec_tag: int, elem_length: float, sec_name: str = ""
    ) -> float:
        """Plastic hinge length *Lp* per ASCE 41-17 §10.8.

        Steel moment frames:  Lp = 0.08L + 0.022 · db · fy    (Eq 10-1)
        Steel braces:         Lp = 0.08L + 0.015 · db · fy    (Eq 10-2)
        RC beams / columns:   Lp = 0.05L + 0.1 · db · fy / √fc

        Falls back to a geometric estimate when section data is unavailable.

        Args:
            sec_tag: Section tag number.
            elem_length: Element length (model units).
            sec_name: Section name (for material lookup).

        Returns:
            Plastic hinge length in model length units.
        """
        if not sec_name or sec_name not in self.model.sections:
            return max(0.05, elem_length * 0.1)

        sec = self.model.sections[sec_name]
        mat = self.model.materials.get(sec.material)
        if mat is None:
            return max(0.05, elem_length * 0.1)

        fy = mat.Fy if mat.Fy and mat.Fy > 0 else 2.5e8
        # Determine length-unit conversion: model units → mm.
        # Default (N, m) → multiply by 1000.
        lu = self.model.units.get("L", "m")
        if lu in ("mm", "millimeter", "millimeters"):
            _to_mm = 1.0
        elif lu in ("cm", "centimeter", "centimeters"):
            _to_mm = 10.0
        elif lu in ("m", "meter", "meters"):
            _to_mm = 1000.0
        elif lu in ("in", "inch", "inches"):
            _to_mm = 25.4
        elif lu in ("ft", "foot", "feet"):
            _to_mm = 304.8
        else:
            _to_mm = 1000.0  # fallback

        fy_mpa = fy / 1e6
        fc_mpa = (mat.Fc / 1e6) if mat.Fc and mat.Fc > 0 else 25.0
        db = 0.0

        if hasattr(sec, 'top_bar_dia') and getattr(sec, 'top_bar_dia', 0) > 0:
            db = sec.top_bar_dia * _to_mm  # model units → mm
        elif hasattr(sec, 'bar_dia') and getattr(sec, 'bar_dia', 0) > 0:
            db = sec.bar_dia * _to_mm
        elif hasattr(sec, 'tf') and sec.tf > 0:
            db = sec.tf * _to_mm
        elif hasattr(sec, 't') and sec.t > 0:
            db = sec.t * _to_mm
        else:
            db = 20.0  # default bar diameter (mm)

        is_concrete = hasattr(sec, 'cover')
        is_brace = hasattr(sec, 'od') or hasattr(sec, 't')

        if is_concrete:
            Lp = (0.05 * elem_length
                  + 0.1 * db * fy_mpa / (fc_mpa ** 0.5) / 1000.0)
        elif is_brace:
            Lp = (0.08 * elem_length
                  + 0.015 * db * fy_mpa / 1000.0)
        else:
            Lp = (0.08 * elem_length
                  + 0.022 * db * fy_mpa / 1000.0)

        return min(Lp, 0.33 * elem_length)  # ASCE 41 cap

    def _compute_hinge_length(self, sec_tag: int, elem_length: float) -> float:
        """Estimate plastic hinge length *Lp* for ``HingeRadau`` integration.

        Uses the section depth or a fraction of element length:

        * **Steel I‑sections**: :math:`L_p = 0.5 \\cdot d` (depth)
        * **Pipe sections**: :math:`L_p = 0.5 \\cdot OD`
        * **Other sections**: :math:`L_p = 0.1 \\cdot L` (10\\% of span)

        Returns:
            Plastic hinge length in model length units.
        """
        # Find the section by tag
        sec_name = None
        for name, tag in getattr(self, 'section_tags', {}).items():
            if tag == sec_tag:
                sec_name = name
                break
        if sec_name and sec_name in self.model.sections:
            sec = self.model.sections[sec_name]
            try:
                from ..model.sap_data import ISection, PipeSection, BoxSection
                if isinstance(sec, ISection):
                    return max(0.05, sec.depth * 0.5)
                elif isinstance(sec, PipeSection):
                    return max(0.05, sec.od * 0.5)
                elif isinstance(sec, BoxSection):
                    return max(0.05, sec.depth * 0.5)
            except Exception:
                pass
        return max(0.05, elem_length * 0.1)

    # -------------------------------------------------------------------------
    # Loads
    # -------------------------------------------------------------------------
    def _node_tag_from_id(self, node_id: str) -> Optional[int]:
        """Return numeric tag for a node, or None if not found."""
        node = self.model.nodes.get(node_id)
        if node:
            return node.node_tag
        return None

    def _create_loads(self, pattern_scales: Optional[Dict[str, float]] = None) -> None:
        """Create load patterns and apply loads (joint and distributed).

        Args:
            pattern_scales: If provided, only create patterns listed in this
                dict, applying the given scale factor to every load in that
                pattern.  If ``None`` (default), all patterns are created with
                factor 1.0.
        """
        if self.config['verbose']:
            print("Creating loads...")

        # Resolve which patterns to activate
        all_patterns = self.model.load_patterns
        if pattern_scales is not None:
            active = {name: pattern_scales.get(name, 0.0)
                      for name in all_patterns if name in pattern_scales}
        else:
            active = {name: 1.0 for name in all_patterns}

        # Accumulators keyed by pattern *name*
        joint_load_totals: Dict[str, Dict[str, float]] = {}
        frame_load_totals: Dict[str, Dict[str, float]] = {}
        sw_load_totals: Dict[str, Dict[str, float]] = {}

        # Determine which distributed loads to use (split or original)
        dist_loads = (self.split_dist_loads if self.split_dist_loads is not None
                    else self.model.frame_dist_loads)

        # Merge in edge loads converted from area uniform loads
        edge_loads = getattr(self, 'edge_loads_from_areas', [])
        if edge_loads:
            dist_loads = list(dist_loads) + list(edge_loads)

        # Build pattern tags (one per unique load pattern name)
        pattern_tags = {}
        for i, (pattern_name, scale) in enumerate(active.items(), start=1):
            if abs(scale) < 1e-12:
                continue
            ops.timeSeries('Linear', i)
            ops.pattern('Plain', i, i)
            pattern_tags[pattern_name] = i
            if self.config['verbose']:
                print(f"  Pattern '{pattern_name}' (tag={i}, scale={scale})")

        # ------------------------------------------------------------------
        # Joint loads (scaled)
        # ------------------------------------------------------------------
        for jl in self.model.joint_loads:
            scale = active.get(jl.pattern, 0.0)
            if abs(scale) < 1e-12:
                continue
            pat_tag = pattern_tags.get(jl.pattern)
            if pat_tag is None:
                continue
            node = self._node_tag_from_id(jl.node_id)
            if node is None:
                continue
            ops.load(node, jl.fx * scale, jl.fy * scale, jl.fz * scale,
                     jl.mx * scale, jl.my * scale, jl.mz * scale)

            pname = jl.pattern
            if pname not in joint_load_totals:
                joint_load_totals[pname] = {k: 0.0 for k in
                                            ('fx','fy','fz','mx','my','mz')}
            for key in ('fx', 'fy', 'fz', 'mx', 'my', 'mz'):
                joint_load_totals[pname][key] += getattr(jl, key) * scale

            if self.config['verbose']:
                print(f"    Joint load ({pat_tag}): node {node}, scale={scale}: "
                      f"{jl.fx*scale:,.1f} | {jl.fy*scale:,.1f} | {jl.fz*scale:,.1f} | "
                      f"{jl.mx*scale:,.1f} | {jl.my*scale:,.1f} | {jl.mz*scale:,.1f}")

        # ------------------------------------------------------------------
        # Frame distributed loads (scaled)
        # ------------------------------------------------------------------
        if not hasattr(self, 'frame_tag_map'):
            elements = self.split_elements if self.split_elements else self.model.frame_elements
            self.frame_tag_map = {
                eid: elem.elem_tag
                for eid, elem in elements.items()
                if not elem.inactive
            }

        def get_elem_tag(frame_id: str) -> Optional[int]:
            return self.frame_tag_map.get(frame_id)

        for ld in dist_loads:
            scale = active.get(ld.pattern, 0.0)
            if abs(scale) < 1e-12:
                continue
            pat_tag = pattern_tags.get(ld.pattern)
            if pat_tag is None:
                continue
            elem_tag = get_elem_tag(ld.frame_id)
            if elem_tag is None:
                if self.config['verbose']:
                    print(f"  Warning: element '{ld.frame_id}' not found or inactive")
                continue

            if self.split_elements:
                elem = self.split_elements.get(ld.frame_id)
            else:
                elem = self.model.frame_elements.get(ld.frame_id)
            if elem is None:
                continue

            try:
                vec_x, vec_y, vec_z = self._get_local_axes(elem)
            except Exception as e:
                if self.config['verbose']:
                    print(f"  Warning: could not compute local axes for element {ld.frame_id}: {e}")
                continue

            if ld.direction == 'Gravity':
                global_dir = np.array([0.0, 0.0, -1.0])
            elif ld.direction == 'X':
                global_dir = np.array([1.0, 0.0, 0.0])
            elif ld.direction == 'Y':
                global_dir = np.array([0.0, 1.0, 0.0])
            elif ld.direction == 'Z':
                global_dir = np.array([0.0, 0.0, 1.0])
            elif ld.direction == 'LocalX':
                global_dir = vec_x
            elif ld.direction == 'LocalY':
                global_dir = vec_y
            elif ld.direction == 'LocalZ':
                global_dir = vec_z
            else:
                global_dir = np.array([0.0, 0.0, -1.0])

            wx_a = ld.val_a * scale * np.dot(global_dir, vec_x)
            wy_a = ld.val_a * scale * np.dot(global_dir, vec_y)
            wz_a = ld.val_a * scale * np.dot(global_dir, vec_z)
            wx_b = ld.val_b * scale * np.dot(global_dir, vec_x)
            wy_b = ld.val_b * scale * np.dot(global_dir, vec_y)
            wz_b = ld.val_b * scale * np.dot(global_dir, vec_z)

            if self._node_tag_from_id:
                coords_i = ops.nodeCoord(self._node_tag_from_id(elem.node_i))
                coords_j = ops.nodeCoord(self._node_tag_from_id(elem.node_j))
            else:
                coords_i = ops.nodeCoord(self.model.nodes[elem.node_i].node_tag)
                coords_j = ops.nodeCoord(self.model.nodes[elem.node_j].node_tag)
            length = np.linalg.norm(np.array(coords_j) - np.array(coords_i))
            if length < 1e-12:
                continue

            aOverL = max(0.0, min(1.0, ld.rdist_a))
            bOverL = max(0.0, min(1.0, ld.rdist_b))
            load_l = ld.dist_b - ld.dist_a

            # Accumulate totals in GLOBAL coordinates
            pname = ld.pattern
            if pname not in frame_load_totals:
                frame_load_totals[pname] = {k: 0.0 for k in
                                            ('fx','fy','fz','mx','my','mz')}
            # Build local-to-global transformation matrix
            T = np.column_stack([vec_x, vec_y, vec_z])
            # Total force in local coordinates
            f_local = np.array([
                0.5 * (wx_a + wx_b) * load_l,
                0.5 * (wy_a + wy_b) * load_l,
                0.5 * (wz_a + wz_b) * load_l,
            ])
            f_global = T @ f_local
            f_loc = {
                'fx': f_global[0], 'fy': f_global[1], 'fz': f_global[2],
            }
            # Approximate fixed-end moments in local coordinates
            span = bOverL - aOverL
            if span > 1e-12 and abs(load_l) > 1e-12:
                m_local = np.array([
                    0.0,
                    (wy_a + wy_b) * 0.5 * span * load_l * load_l / 12.0,
                    (wz_a + wz_b) * 0.5 * span * load_l * load_l / 12.0,
                ])
            else:
                m_local = np.zeros(3)
            m_global = T @ m_local
            f_loc.update({'mx': m_global[0], 'my': m_global[1], 'mz': m_global[2]})

            for key, val in f_loc.items():
                frame_load_totals[pname][key] += val

            if self.config['verbose']:
                print(f"    Frame load ({pat_tag}): element {elem_tag}, "
                      f"fx={f_loc['fx']:,.1f}, fy={f_loc['fy']:,.1f}, "
                      f"fz={f_loc['fz']:,.1f} | {ld.frame_id}")

            # Apply eleLoad
            # NOTE: The 8‑argument form (wy1, wz1, wx1, aL, bL, wy2, wz2, wx2)
            # is broken in OpenSeesPy 3.8.0.0 — the end values (wy2 etc.) are
            # silently ignored.  We therefore decompose non‑uniform loads into
            # N partial‑span uniform segments using the working 5‑argument form
            # (wy, wz, wx, aL, bL), which preserves both total force and moment
            # distribution.
            if ld.load_type == 'Force':
                is_uniform = abs(ld.val_a - ld.val_b) < 1e-6
                is_full_span = abs(aOverL) < 1e-12 and abs(bOverL - 1.0) < 1e-12

                if is_uniform and is_full_span:
                    ops.eleLoad('-ele', elem_tag, '-type', '-beamUniform',
                                wy_a, wz_a, wx_a)
                elif is_uniform:
                    # Uniform on a partial span → 5‑argument form
                    ops.eleLoad('-ele', elem_tag, '-type', '-beamUniform',
                                wy_a, wz_a, wx_a, aOverL, bOverL)
                else:
                    # Trapezoidal/linear → decompose into N partial‑span
                    # uniform segments using the working 5‑argument form.
                    # This preserves both the total force AND the moment
                    # distribution, unlike a single uniform average.
                    N = 4  # segments — more = better moment accuracy
                    span_frac = bOverL - aOverL
                    for i in range(N):
                        seg_a = aOverL + i * span_frac / N
                        seg_b = aOverL + (i + 1) * span_frac / N
                        # Mid‑point intensity of this segment
                        xi = (i + 0.5) / N
                        wy_mid = wy_a + (wy_b - wy_a) * xi
                        wz_mid = wz_a + (wz_b - wz_a) * xi
                        wx_mid = wx_a + (wx_b - wx_a) * xi
                        ops.eleLoad('-ele', elem_tag, '-type', '-beamUniform',
                                    wy_mid, wz_mid, wx_mid, seg_a, seg_b)

            elif ld.load_type == 'Moment':
                if self.config['verbose']:
                    print("  Warning: moment distributed loads not yet supported")

        # ------------------------------------------------------------------
        # Self-weight for patterns with SelfWtMult != 0
        # ------------------------------------------------------------------
        def _add_sw(pname: str, node_tag: int, fz_val: float) -> None:
            """Apply a nodal load for self-weight and track it."""
            ops.load(node_tag, 0.0, 0.0, fz_val, 0.0, 0.0, 0.0)
            if pname not in sw_load_totals:
                sw_load_totals[pname] = {k: 0.0 for k in
                                         ('fx','fy','fz','mx','my','mz')}
            sw_load_totals[pname]['fz'] += fz_val

        for pname, scale in active.items():
            if abs(scale) < 1e-12:
                continue
            pat = all_patterns.get(pname)
            if pat is None or abs(pat.self_weight_factor) < 1e-12:
                continue
            sw_factor = pat.self_weight_factor * scale

            if self.config['verbose']:
                print(f"  Self-weight for '{pname}' (factor={sw_factor:.4f})")

            elements = (self.split_elements if self.split_elements
                        else self.model.frame_elements)
            for eid, elem in elements.items():
                if elem.inactive:
                    continue
                sec_name = self.model.frame_assignments.get(eid)
                if not sec_name:
                    continue
                sec = self.model.sections.get(sec_name)
                if sec is None:
                    continue
                mat = self.model.materials.get(sec.material)
                if mat is None or abs(mat.unit_weight) < 1e-12:
                    continue

                # Element length
                ni = self.model.nodes.get(elem.node_i)
                nj = self.model.nodes.get(elem.node_j)
                if ni is None or nj is None:
                    continue
                L = np.linalg.norm([
                    nj.x - ni.x, nj.y - ni.y, nj.z - ni.z
                ])
                if L < 1e-12:
                    continue

                # Weight = volume × unit_weight × self_weight_factor
                weight = sec.A * mat.unit_weight * L * sw_factor

                # Half to each end node (gravity downward = negative Z)
                tag_i = self._node_tag_from_id(elem.node_i)
                tag_j = self._node_tag_from_id(elem.node_j)
                if tag_i is not None:
                    _add_sw(pname, tag_i, -weight * 0.5)
                if tag_j is not None:
                    _add_sw(pname, tag_j, -weight * 0.5)

            # ── Area element self-weight ──
            for aid, area_elem in self.model.area_elements.items():
                if getattr(area_elem, 'inactive', False):
                    continue
                sec_name = self.model.area_assignments.get(aid)
                if not sec_name:
                    continue
                sec = self.model.sections.get(sec_name)
                if sec is None:
                    continue
                mat = self.model.materials.get(sec.material)
                if mat is None or abs(mat.unit_weight) < 1e-12:
                    continue
                thickness = area_elem.thickness
                if thickness < 1e-12:
                    continue

                # Polygon area via Newell's method
                area_mag, _ = self._polygon_area(area_elem.node_ids)
                if area_mag < 1e-12:
                    continue

                # Self-weight (always downward = negative Z)
                fz_total = thickness * mat.unit_weight * area_mag * sw_factor
                n_corners = len(area_elem.node_ids)
                for nid in area_elem.node_ids:
                    tag = self._node_tag_from_id(nid)
                    if tag is not None:
                        _add_sw(pname, tag, -fz_total / n_corners)

        # ------------------------------------------------------------------
        # FRAME LOADS - GRAVITY (explicit multipliers on self-weight)
        # ------------------------------------------------------------------
        def _add_gravity(pname: str, node_tag: int, fx: float, fy: float, fz: float) -> None:
            """Apply a nodal force from a gravity load and track it."""
            ops.load(node_tag, fx, fy, fz, 0.0, 0.0, 0.0)
            if pname not in sw_load_totals:
                sw_load_totals[pname] = {k: 0.0 for k in
                                         ('fx','fy','fz','mx','my','mz')}
            sw_load_totals[pname]['fx'] += fx
            sw_load_totals[pname]['fy'] += fy
            sw_load_totals[pname]['fz'] += fz

        for pname, scale in active.items():
            if abs(scale) < 1e-12:
                continue
            if pname not in all_patterns:
                continue
            pat_tag = pattern_tags.get(pname)
            if pat_tag is None:
                continue

            # ── Frame gravity loads ──
            elements = (self.split_elements if self.split_elements
                        else self.model.frame_elements)
            for gl in self.model.frame_gravity_loads:
                if gl.pattern != pname:
                    continue
                elem = elements.get(gl.frame_id)
                if elem is None or elem.inactive:
                    continue
                sec_name = self.model.frame_assignments.get(gl.frame_id)
                if not sec_name:
                    continue
                sec = self.model.sections.get(sec_name)
                if sec is None:
                    continue
                mat = self.model.materials.get(sec.material)
                if mat is None or abs(mat.unit_weight) < 1e-12:
                    continue

                ni = self.model.nodes.get(elem.node_i)
                nj = self.model.nodes.get(elem.node_j)
                if ni is None or nj is None:
                    continue
                L = np.linalg.norm([
                    nj.x - ni.x, nj.y - ni.y, nj.z - ni.z
                ])
                if L < 1e-12:
                    continue

                # Force = volume × unit_weight × multiplier × scale
                sw_per_len = sec.A * mat.unit_weight
                fx = sw_per_len * L * gl.multiplier_x * scale * 0.5
                fy = sw_per_len * L * gl.multiplier_y * scale * 0.5
                fz = sw_per_len * L * gl.multiplier_z * scale * 0.5

                tag_i = self._node_tag_from_id(elem.node_i)
                tag_j = self._node_tag_from_id(elem.node_j)
                if tag_i is not None:
                    _add_gravity(pname, tag_i, fx, fy, fz)
                if tag_j is not None:
                    _add_gravity(pname, tag_j, fx, fy, fz)

            # ── Area gravity loads ──
            for agl in self.model.area_gravity_loads:
                if agl.pattern != pname:
                    continue
                area_elem = self.model.area_elements.get(agl.area_id)
                if area_elem is None:
                    continue
                if getattr(area_elem, 'inactive', False):
                    # Parent was meshed — apply gravity load to each
                    # sub-element instead so the load is not lost.
                    sub_ids = sorted(
                        aid for aid in self.model.area_elements
                        if aid.startswith(f"{agl.area_id}_sub_")
                    )
                    if not sub_ids:
                        continue
                    for sub_id in sub_ids:
                        sub_elem = self.model.area_elements[sub_id]
                        sec_name = self.model.area_assignments.get(sub_id)
                        if not sec_name:
                            continue
                        sec = self.model.sections.get(sec_name)
                        if sec is None:
                            continue
                        mat = self.model.materials.get(sec.material)
                        if mat is None or abs(mat.unit_weight) < 1e-12:
                            continue
                        thickness = sub_elem.thickness
                        if thickness < 1e-12:
                            continue
                        area_mag, _ = self._polygon_area(sub_elem.node_ids)
                        if area_mag < 1e-12:
                            continue
                        sw_per_area = thickness * mat.unit_weight
                        total_fx = sw_per_area * area_mag * agl.multiplier_x * scale
                        total_fy = sw_per_area * area_mag * agl.multiplier_y * scale
                        total_fz = sw_per_area * area_mag * agl.multiplier_z * scale
                        n_corners = len(sub_elem.node_ids)
                        for nid in sub_elem.node_ids:
                            tag = self._node_tag_from_id(nid)
                            if tag is not None:
                                _add_gravity(pname, tag,
                                             total_fx / n_corners,
                                             total_fy / n_corners,
                                             total_fz / n_corners)
                    continue

                # Get section + material for density
                sec_name = self.model.area_assignments.get(agl.area_id)
                if not sec_name:
                    continue
                sec = self.model.sections.get(sec_name)
                if sec is None:
                    continue
                mat = self.model.materials.get(sec.material)
                if mat is None or abs(mat.unit_weight) < 1e-12:
                    continue
                thickness = area_elem.thickness
                if thickness < 1e-12:
                    continue

                # 3D polygon area via Newell's method
                area_mag, _ = self._polygon_area(area_elem.node_ids)
                if area_mag < 1e-12:
                    continue

                # Force = area × thickness × unit_weight × multiplier × scale
                sw_per_area = thickness * mat.unit_weight
                total_fx = sw_per_area * area_mag * agl.multiplier_x * scale
                total_fy = sw_per_area * area_mag * agl.multiplier_y * scale
                total_fz = sw_per_area * area_mag * agl.multiplier_z * scale

                n_corners = len(area_elem.node_ids)
                for nid in area_elem.node_ids:
                    tag = self._node_tag_from_id(nid)
                    if tag is not None:
                        _add_gravity(pname, tag,
                                     total_fx / n_corners,
                                     total_fy / n_corners,
                                     total_fz / n_corners)

        # ------------------------------------------------------------------
        # Merge all totals into public attribute
        # ------------------------------------------------------------------
        all_ptns = set(joint_load_totals) | set(frame_load_totals) | set(sw_load_totals)
        self.load_totals: Dict[str, Dict[str, float]] = {}
        for pname in all_ptns:
            self.load_totals[pname] = {k: 0.0 for k in
                                       ('fx','fy','fz','mx','my','mz')}
            for key in self.load_totals[pname]:
                self.load_totals[pname][key] += joint_load_totals.get(pname, {}).get(key, 0.0)
                self.load_totals[pname][key] += frame_load_totals.get(pname, {}).get(key, 0.0)
                self.load_totals[pname][key] += sw_load_totals.get(pname, {}).get(key, 0.0)

        if self.config['verbose']:
            print("\n  --- Load totals per pattern ---")
            for pname, totals in self.load_totals.items():
                parts = [f"{k} = {v:,.1f}" for k, v in totals.items()]
                print(f"  {pname}: {' | '.join(parts)}")
            print()


    # =========================================================================
    # Script writing
    # =========================================================================
    def write_script(self, file_path: str) -> None:
        """Write the OpenSees model as a Tcl/Python script.

        Note: This is a placeholder – you can implement by serialising
        the operations or using opstool's export functionality.
        """
        if self.config['verbose']:
            print(f"Writing script to {file_path}... (placeholder)")

    # -------------------------------------------------------------------------
    # Recorders
    # -------------------------------------------------------------------------
    def _setup_recorders(self) -> None:
        """Set up recorders for output (optional)."""
        if self.config['verbose']:
            print("Setting up recorders... (optional)")
        # ops.recorder('Node', '-file', 'displacements.out', '-node', 1, '-dof', 1, 2, 3, 'disp')

    # -------------------------------------------------------------------------
    # Area load → edge load conversion
    # -------------------------------------------------------------------------
    def _convert_area_loads(self,
                            selection: Optional[Selection] = None,
                            ) -> None:
        """Convert area uniform loads to equivalent frame edge loads.

        Args:
            selection: Optional :class:`Selection` to restrict which areas
                are converted.  Only area uniform loads on areas matching
                the selection will be converted.  ``None`` means all
                (unless ``create_shells=True``, in which case ``None``
                means no areas are loads‑only — all become shells, so
                no conversion occurs).
        """
        # When shell mode is active, None selection → no loads converted
        if selection is None:
            if self.config.get('create_shells', False):
                self.edge_loads_from_areas = []
                return
            selection = getattr(self, '_area_selection', None)

        if not self.model.area_uniform_loads:
            self.edge_loads_from_areas = []
            return

        # Filter by selection if provided
        area_loads: List = self.model.area_uniform_loads
        area_elements = self.model.area_elements
        if selection is not None:
            sel_area_ids = set(selection.get_area_ids(self.model))
            area_loads = [ld for ld in area_loads if ld.area_id in sel_area_ids]
            area_elements = {
                aid: ae for aid, ae in area_elements.items()
                if aid in sel_area_ids
            }
            if not area_loads:
                if self.config['verbose']:
                    print("  No area uniform loads match the selection")
                self.edge_loads_from_areas = []
                return

        # Use split elements if available, else originals
        elements = (self.split_elements if self.split_elements
                    else self.model.frame_elements)

        edge_loads = convert_area_loads_to_edge_loads(
            self.model.nodes,
            area_elements,
            elements,
            area_loads,
        )
        self.edge_loads_from_areas = edge_loads
        if self.config['verbose']:
            print(f"  Converted {len(area_loads)} area loads "
                  f"into {len(edge_loads)} frame edge loads")

    # -------------------------------------------------------------------------
    # -------------------------------------------------------------------------
    # Shell element creation
    # -------------------------------------------------------------------------
    def _create_shell_elements(self,
                               loads_only_selection: Optional[Selection] = None,
                               ) -> None:
        """Create ShellMITC4 elements for areas not in the loads-only selection.

        Args:
            loads_only_selection: Optional :class:`Selection` designating
                which areas are loads‑only (their loads get converted to
                frame edge loads).  Areas **not** matching this selection
                are created as ``ShellMITC4`` elements.
                ``None`` means **all** areas become shells.
        """
        if loads_only_selection is not None:
            sel_area_ids = set(loads_only_selection.get_area_ids(self.model))
        else:
            sel_area_ids = set()

        if self.config['verbose']:
            print("Creating shell elements...")

        # Track shell section tags (one per unique section name)
        self._shell_sec_tags: Dict[str, int] = {}
        next_sec_tag = (max(self.section_tags.values(), default=0) + 1
                        if self.section_tags else 1)

        shell_count = 0
        for aid, area in self.model.area_elements.items():
            # Skip areas in the loads-only selection
            if aid in sel_area_ids:
                continue
            # Skip inactive (meshed) originals — their sub-elements are
            # already in area_elements and will be processed in the same loop.
            if getattr(area, 'inactive', False):
                continue

            nids = area.node_ids
            if len(nids) < 3:
                continue

            # Gather node tags
            node_tags = []
            skip = False
            for nid in nids:
                node = self.model.nodes.get(nid)
                if node is None:
                    skip = True
                    break
                node_tags.append(node.node_tag)
            if skip:
                continue

            # Determine section and material
            sec_name = self.model.area_assignments.get(aid, '')
            if not sec_name or sec_name not in self.model.sections:
                continue

            sec = self.model.sections[sec_name]
            mat = self.model.materials.get(sec.material)
            if mat is None:
                continue

            # Create shell section on first use
            if sec_name not in self._shell_sec_tags:
                tag = next_sec_tag
                next_sec_tag += 1
                self._shell_sec_tags[sec_name] = tag
                self._create_single_shell_section(sec, mat, tag)

            sec_tag = self._shell_sec_tags[sec_name]

            # Determine a unique element tag — base on the same active
            # frame collection that _create_elements will use (split
            # children if available, otherwise originals), and also
            # account for reserved rigid-link tags from frame end
            # offsets (already populated in _offset_rigid_links).
            active_frames = (
                self.split_elements
                if self.split_elements is not None
                else self.model.frame_elements
            )
            max_frame_tag = max(
                (e.elem_tag for e in active_frames.values()
                 if not getattr(e, 'inactive', False)),
                default=0,
            )
            max_rigid_tag = max(
                (r[3] for r in self._offset_rigid_links),
                default=0,
            )
            elem_tag = max(max_frame_tag, max_rigid_tag) + shell_count + 1

            # Create ShellMITC4 (quad) or ShellDKGT (tri) element
            if len(nids) == 4:
                ops.element('ShellMITC4', elem_tag, *node_tags, sec_tag)
            elif len(nids) == 3:
                # Degenerate quad by repeating the last node
                ops.element('ShellMITC4', elem_tag,
                            node_tags[0], node_tags[1], node_tags[2],
                            node_tags[2], sec_tag)
            else:
                if self.config['verbose']:
                    print(f"  Warning: area {aid} has {len(nids)} nodes, "
                          f"skipping shell creation")
                continue

            shell_count += 1
            if self.config['verbose'] and shell_count % 50 == 0:
                print(f"  ... created {shell_count} shell elements")

        if self.config['verbose']:
            print(f"  Created {shell_count} shell elements "
                  f"({len(self._shell_sec_tags)} shell sections)")

    @staticmethod
    def _create_single_shell_section(sec: Section, mat, tag: int) -> None:
        """Create an elastic membrane plate section for a shell element.

        Uses ``ops.section('ElasticPlateSection', ...)`` with the
        material's Young's modulus, Poisson's ratio, density, and the
        section's thickness.
        """
        E_mod = mat.E_mod if mat.E_mod and mat.E_mod > 0 else 3.0e10
        nu = mat.nu if mat.nu is not None and mat.nu > 0 else 0.2
        thickness = getattr(sec, 'thickness', 0.0)
        rho = mat.unit_mass if mat.unit_mass and mat.unit_mass > 0 else 0.0

        if thickness <= 0.0:
            thickness = 1.0  # fallback

        ops.section('ElasticPlateSection', tag, E_mod, nu, thickness, rho)

    # -------------------------------------------------------------------------
    # Lumped plasticity (zeroLengthSection hinges)
    # -------------------------------------------------------------------------

    def _create_lumped_hinges(self) -> None:
        """Replace frame elements with lumped plasticity hinges.

        Each frame element is split into::

            structural_node_i → hinge_i → elastic_mid → hinge_j → structural_node_j

        Coincident hinge nodes sit at the same coordinates.  Translation
        DOFs (1,2,3) are tied with ``equalDOF`` so only rotations (4,5,6)
        are released across the zero-length hinge elements.

        Hinge backbones use ``Hysteretic`` materials matched to ASCE 41
        rotation limits.  Activated via ``config['hinge_model'] = 'lumped'``.
        """
        if self.config.get('hinge_model') != 'lumped':
            return

        elements = (self.split_elements if self.split_elements
                    else self.model.frame_elements)
        assignments = (self.split_assignments if self.split_assignments
                       else self.model.frame_assignments)

        next_node_tag = max((nd.node_tag for nd in self.model.nodes.values()),
                            default=0) + 1
        next_tag = max((e.elem_tag for e in elements.values() if not e.inactive),
                       default=0) + 1
        # Separate counter for hinge section/material tags, seeded high
        # to avoid collision with existing section/element/material tags.
        hinge_tag_base = (max((v for v in self.section_tags.values()), default=0)
                          + len(self.section_tags) + 100)
        hinge_sec_tag = hinge_tag_base
        hinge_mat_tag = hinge_tag_base + len(self.section_tags) + 1

        new_elements: Dict[str, FrameElement] = {}
        new_assignments: Dict[str, str] = {}

        for eid, elem in list(elements.items()):
            if elem.inactive:
                new_elements[eid] = elem
                continue

            sec_name = assignments.get(eid) if assignments else None
            if not sec_name or sec_name not in self.section_tags:
                new_elements[eid] = elem
                continue

            ni = self.model.nodes.get(elem.node_i)
            nj = self.model.nodes.get(elem.node_j)
            if ni is None or nj is None:
                new_elements[eid] = elem
                continue

            L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
            if L < 1e-12:
                new_elements[eid] = elem
                continue

            sec_tag = self.section_tags[sec_name]
            sec = self.model.sections.get(sec_name)
            if sec is None:
                new_elements[eid] = elem
                continue

            # --- Create coincident hinge nodes ---
            hinge_i_id = f"{eid}_hinge_i"
            hinge_j_id = f"{eid}_hinge_j"
            hinge_i_tag = next_node_tag
            next_node_tag += 1
            hinge_j_tag = next_node_tag
            next_node_tag += 1

            self.model.nodes[hinge_i_id] = Node(
                node_id=hinge_i_id, node_tag=hinge_i_tag,
                x=ni.x, y=ni.y, z=ni.z,
            )
            self.model.nodes[hinge_j_id] = Node(
                node_id=hinge_j_id, node_tag=hinge_j_tag,
                x=nj.x, y=nj.y, z=nj.z,
            )

            # Create OpenSees nodes for coincident hinge nodes
            ops.node(hinge_i_tag, ni.x, ni.y, ni.z)
            ops.node(hinge_j_tag, nj.x, nj.y, nj.z)
            self._created_node_tags.update([hinge_i_tag, hinge_j_tag])

            # Tie translation DOFs between structural and hinge nodes
            ops.equalDOF(ni.node_tag, hinge_i_tag, 1, 2, 3)
            ops.equalDOF(nj.node_tag, hinge_j_tag, 1, 2, 3)

            # --- Create Hysteretic hinge section ---
            mat = self.model.materials.get(sec.material)

            if mat and mat.type and 'concrete' in mat.type.lower():
                # Concrete: softer hinges
                Fy = mat.Fy if mat.Fy and mat.Fy > 0 else 4.0e8
                E = mat.E_mod if mat.E_mod > 0 else 2.5e10
                My = Fy * (sec.Z33 if sec.Z33 else sec.I33 / (L * 0.5))
                My_weak = Fy * (sec.Z22 if sec.Z22 else sec.I22 / (L * 0.5))
            else:
                # Steel: use section yield moment
                Fy = mat.Fy if mat.Fy and mat.Fy > 0 else 2.5e8
                E = mat.E_mod if mat.E_mod > 0 else 2.0e11
                My = Fy * (sec.Z33 if sec.Z33 else sec.I33 / (L * 0.5))
                My_weak = Fy * (sec.Z22 if sec.Z22 else sec.I22 / (L * 0.5))

            # ASCE 41 plastic hinge length for yield rotation scaling
            Lp = self._compute_asce41_hinge_length(0, L, sec_name)
            # Yield rotation: My * Lp / (6 * E * I) approximates the
            # rotation over the plastic hinge region.
            theta_y = (My * Lp) / (max(6.0 * E * sec.I33, 1e-12)) if E * sec.I33 > 0 else 0.005
            theta_y_weak = (My_weak * Lp) / (max(6.0 * E * sec.I22, 1e-12)) if E * sec.I22 > 0 else 0.005
            theta_cap = theta_y * 6.0
            theta_cap_weak = theta_y_weak * 6.0

            # Axial material (elastic)
            ops.uniaxialMaterial('Elastic', hinge_mat_tag,
                                 sec.A * E / L)
            # Strong-axis moment (Hysteretic backbone)
            ops.uniaxialMaterial('Hysteretic', hinge_mat_tag + 1,
                                 My, theta_y, My * 1.1, theta_cap,
                                 -My, -theta_y, -My * 1.1, -theta_cap,
                                 1.0, 1.0, 0.0, 0.0, 0.0)
            # Weak-axis moment
            ops.uniaxialMaterial('Hysteretic', hinge_mat_tag + 2,
                                 My_weak, theta_y_weak, My_weak * 1.1, theta_cap_weak,
                                 -My_weak, -theta_y_weak, -My_weak * 1.1, -theta_cap_weak,
                                 1.0, 1.0, 0.0, 0.0, 0.0)
            # Torsion (elastic — no inelastic torsion expected)
            G = mat.G_mod if mat and mat.G_mod and mat.G_mod > 0 else 0.4 * E
            ops.uniaxialMaterial('Elastic', hinge_mat_tag + 3,
                                 G * sec.J / L if sec.J else 1e6)

            ops.section('Aggregator', hinge_sec_tag,
                        hinge_mat_tag, 'P',
                        hinge_mat_tag + 1, 'Mz',
                        hinge_mat_tag + 2, 'My',
                        hinge_mat_tag + 3, 'T')
            hinge_sec_tag += 1
            hinge_mat_tag += 4

            # Get local axes for element orientation
            try:
                vx, vy, vz = self._get_local_axes(elem)
                orient = (vx[0], vx[1], vx[2], vz[0], vz[1], vz[2])
            except Exception:
                orient = None

            # --- Create zero-length hinge elements ---
            hinge_i_elem_tag = next_tag
            next_tag += 1
            if orient:
                ops.element('zeroLengthSection', hinge_i_elem_tag,
                            ni.node_tag, hinge_i_tag, hinge_sec_tag - 1,
                            '-orient', orient[0], orient[1], orient[2],
                            orient[3], orient[4], orient[5])
            else:
                ops.element('zeroLengthSection', hinge_i_elem_tag,
                            ni.node_tag, hinge_i_tag, hinge_sec_tag - 1)

            hinge_j_elem_tag = next_tag
            next_tag += 1
            if orient:
                ops.element('zeroLengthSection', hinge_j_elem_tag,
                            hinge_j_tag, nj.node_tag, hinge_sec_tag - 1,
                            '-orient', orient[0], orient[1], orient[2],
                            orient[3], orient[4], orient[5])
            else:
                ops.element('zeroLengthSection', hinge_j_elem_tag,
                            hinge_j_tag, nj.node_tag, hinge_sec_tag - 1)

            # --- Shorten original element to span between hinge nodes ---
            elem.node_i = hinge_i_id
            elem.node_j = hinge_j_id
            new_elements[eid] = elem
            new_assignments[eid] = sec_name

        # Update collections
        if self.split_elements is not None:
            self.split_elements = new_elements
            self.split_assignments = new_assignments
        else:
            self.model.frame_elements = new_elements
            self.model.frame_assignments = new_assignments

    # Analysis
    # -------------------------------------------------------------------------
    def run_static_analysis(self, 
                            odb_tag:int = 0,
                            extract_reactions: bool = True,
                            pattern_scales: Optional[Dict[str, float]] = None,
                            ) -> Dict[str, Any]:
        """Run a linear static analysis and return results.

        Args:
            odb_tag: If > 0 and opstool is installed, also save results via
                     opstool for richer post‑processing.
            extract_reactions: If True, compute nodal reactions at restrained
                               nodes and include them in the returned dict.
            pattern_scales: Optional dict of ``{pattern_name: scale_factor}``.
                If provided, the model is rebuilt with only those patterns
                active at the given scales.  If ``None`` (default), the
                existing model (as built) is analysed.

        Returns:
            Dictionary with keys:

            - ``'nodal_displacements'`` — dict of ``{node_tag: (dx, dy, dz)}``
            - ``'nodal_reactions'`` (if ``extract_reactions``) — dict of
              ``{node_tag: (fx, fy, fz, mx, my, mz)}``.
            - ``'summed_reactions'`` — single summed force/moment vector
            - ``'load_totals'`` — the applied load totals per pattern
        """
        # Rebuild with different patterns if requested
        if pattern_scales is not None:
            # Re-use any persisted selection from the original build
            sel = getattr(self, '_area_selection', None)
            self.build(pattern_scales=pattern_scales, selection=sel)

        unit_L = self.units['L']
        unit_F = self.units.get('F', 'N')
        if self.config['verbose']:
            print("Running analysis...")

        # Define analysis parameters (from config, with fallback defaults)
        test_type = self.config.get('solver_test_type', 'NormDispIncr')
        test_tol = self.config.get('solver_test_tol', 1e-6)
        test_iter = self.config.get('solver_test_max_iter', 10)
        algo = self.config.get('solver_algorithm', 'Newton')
        n_sub = self.config.get('gravity_num_substeps', 1)

        cs = self.config.get('solver_constraints', 'Transformation')
        if self._has_edge_constraints:
            cs = 'Penalty'
            ops.constraints('Penalty', 1.0e12, 1.0e12)
        else:
            ops.constraints(cs)
        ops.numberer('RCM')
        ops.system(self.config.get('solver_system', 'BandGen'))
        ops.test(test_type, test_tol, test_iter)
        # Use ModifiedNewton('-initial') for robustness with imperfect braces
        if algo == 'ModifiedNewton':
            ops.algorithm('ModifiedNewton', '-initial')
        else:
            ops.algorithm(algo)
        ops.integrator('LoadControl', 1.0 / n_sub)
        ops.analysis('Static')

        # Perform analysis — apply gravity in sub-steps if configured
        ok = 0
        for _ in range(n_sub):
            ok = ops.analyze(1)
            if ok != 0:
                break
        if ok != 0:
            print("Analysis failed!")
            return {}

        # Extract results
        results: Dict[str, Any] = {}

        # --- Nodal displacements ---
        if OPSTOOL_AVAILABLE and odb_tag > 0:
            opst.post.CreateODB(odb_tag=1)
            opst.post.save_model_data(odb_tag=1)
            nodes_df = opst.post.get_model_data(data_type='Nodal', odb_tag=1)
            if nodes_df is not None:
                results['nodal_displacements'] = nodes_df.to_dict()
        else:
            displacements = {}
            for node_id, node in self.model.nodes.items():
                tag = node.node_tag
                try:
                    disp = ops.nodeDisp(tag)
                    if isinstance(disp, np.ndarray):
                        disp_list = disp.tolist()
                    elif isinstance(disp, (list, tuple)):
                        disp_list = list(disp)
                    elif isinstance(disp, (int, float)):
                        disp_list = [float(disp)]
                    else:
                        continue
                    # Pad to at least 3 components
                    while len(disp_list) < 3:
                        disp_list.append(0.0)
                    displacements[tag] = (disp_list[0], disp_list[1], disp_list[2])
                except Exception:
                    pass
            if displacements:
                results['nodal_displacements'] = displacements

        # --- Nodal reactions ---
        if extract_reactions:
            try:
                ops.reactions()
                reactions = {}
                for node_id, restraint in self.model.restraints.items():
                    node = self.model.nodes.get(node_id)
                    if node is None:
                        continue
                    tag = node.node_tag
                    rx = ops.nodeReaction(tag, 1) if restraint.dofs[0] else 0.0
                    ry = ops.nodeReaction(tag, 2) if restraint.dofs[1] else 0.0
                    rz = ops.nodeReaction(tag, 3) if restraint.dofs[2] else 0.0
                    rmx = ops.nodeReaction(tag, 4) if restraint.dofs[3] else 0.0
                    rmy = ops.nodeReaction(tag, 5) if restraint.dofs[4] else 0.0
                    rmz = ops.nodeReaction(tag, 6) if restraint.dofs[5] else 0.0
                    reactions[tag] = (rx, ry, rz, rmx, rmy, rmz)

                if reactions:
                    results['nodal_reactions'] = reactions
                    # Also compute summed reactions for equilibrium check
                    summed = {'fx': 0.0, 'fy': 0.0, 'fz': 0.0,
                              'mx': 0.0, 'my': 0.0, 'mz': 0.0}
                    for r in reactions.values():
                        summed['fx'] += r[0]
                        summed['fy'] += r[1]
                        summed['fz'] += r[2]
                        summed['mx'] += r[3]
                        summed['my'] += r[4]
                        summed['mz'] += r[5]
                    results['summed_reactions'] = summed
                    if self.config['verbose']:
                        print(f"\n  Summed reactions ({unit_F}, {unit_F}·{unit_L}):")
                        print(f"    Fx = {summed['fx']:+.3f}  Fy = {summed['fy']:+.3f}  Fz = {summed['fz']:+.3f}")
                        print(f"    Mx = {summed['mx']:+.3f}  My = {summed['my']:+.3f}  Mz = {summed['mz']:+.3f}")
            except Exception as e:
                if self.config['verbose']:
                    print(f"  Warning: could not extract reactions: {e}")

        # --- Applied load totals (for comparison) ---
        if hasattr(self, 'load_totals'):
            results['load_totals'] = self.load_totals
            if self.config['verbose']:
                unit_F = self.units.get('F', 'N')
                print(f"\n  Applied load totals per pattern ({unit_F}):")
                for pname, totals in self.load_totals.items():
                    print(f"    {pname}: Fx={totals['fx']:+.3f}  Fy={totals['fy']:+.3f}  Fz={totals['fz']:+.3f}")

        if self.config['verbose']:
            print("Analysis complete.")
        return results

    def export_split_model(self, output_path: Path) -> None:
        """Export split elements and assignments to a JSON file."""
        if not self.split_elements:
            print("No split elements available. Run build() with split_elements=True first.")
            return
        data = {
            "nodes": {nid: {"tag": node.node_tag, "x": node.x, "y": node.y, "z": node.z} for nid, node in self.model.nodes.items()},
            "split_elements": self.split_elements,
            "split_assignments": self.split_assignments,
            "original_assignments": self.model.frame_assignments,
            "split_loads": self.split_dist_loads,
        }
        with open(output_path, "w") as f:
            json.dump(data, f, indent=2, default=str)

    # =========================================================================
    # Static element forces (after run_static_analysis)
    # =========================================================================
    def extract_static_element_forces(self) -> Dict[int, Dict[str, float]]:
        """Extract element end forces in the **global** coordinate system.

        Must be called **after** :meth:`run_static_analysis`.

        Returns:
            Dict mapping ``elem_tag`` → dict with keys ``'Fx'``, ``'Fy'``,
            ``'Fz'``, ``'Mx'``, ``'My'``, ``'Mz'`` (global forces at the
            I‑end of the element) and ``'Fx_j'``, ``'Fy_j'``, ``'Fz_j'``,
            ``'Mx_j'``, ``'My_j'``, ``'Mz_j'`` (J‑end).
        """
        elements = (self.split_elements if self.split_elements
                    else self.model.frame_elements)
        results = {}
        for eid, elem in elements.items():
            if getattr(elem, 'inactive', False):
                continue
            tag = elem.elem_tag
            try:
                f = ops.eleResponse(tag, 'forces')
            except Exception:
                continue
            results[tag] = {
                'Fx': f[0], 'Fy': f[1], 'Fz': f[2],
                'Mx': f[3], 'My': f[4], 'Mz': f[5],
                'Fx_j': f[6], 'Fy_j': f[7], 'Fz_j': f[8],
                'Mx_j': f[9], 'My_j': f[10], 'Mz_j': f[11],
            }
        return results

    # =========================================================================
    # Results export
    # =========================================================================
    def export_results_to_npz(self,
                              filepath: str,
                              results: Dict[str, Any],
                              section_responses: Optional[Dict[str, bool]] = None,
                              other_combos: Optional[Dict[str, Dict[str, Any]]] = None,
                              ) -> None:
        """Export analysis results to a compressed NumPy .npz file.

        The file contains flat arrays that can be loaded by Rhino's CPython
        (``np.load``) and matched to Rhino geometry via SAP UserStrings.

        **Single‑combo export** (default — arrays have no prefix):

        =================  ================================================
        Array              Description
        =================  ================================================
        ``sub_elem_tags``  OpenSees ``elem_tag`` (int)
        ``sub_sap_ids``    Original SAP FrameID (str), e.g. ``"1"``
        ``sub_t_start``    Parametric position of I‑end along parent [0,1]
        ``sub_t_end``      Parametric position of J‑end along parent [0,1]
        ``sub_node_i_tag`` OpenSees node tag of the I‑end (int)
        ``sub_node_j_tag`` OpenSees node tag of the J‑end (int)
        ``fx_i`` … ``mz_i``  I‑end forces in **global** coordinates
        ``fx_j`` … ``mz_j``  J‑end forces in **global** coordinates
        ``fx_i_local`` … ``mz_i_local``  I‑end forces in **local** coordinates
        ``fx_j_local`` … ``mz_j_local``  J‑end forces in **local** coordinates
        =================  ================================================

        **Nodal data** (one row per model node):

        ================  ================================================
        Array             Description
        ================  ================================================
        ``node_tags``     OpenSees ``node_tag`` (int)
        ``node_sap_ids``  SAP node ID (str), e.g. ``"1"``
        ``node_x``        Original X coordinate
        ``node_y``        Original Y coordinate
        ``node_z``        Original Z coordinate
        ``node_dx``       Displacement in X
        ``node_dy``       Displacement in Y
        ``node_dz``       Displacement in Z
        ================  ================================================

        **Section response data** (one row per integration point, only
        saved when ``section_responses`` is provided):

        =================  ================================================
        Array              Description
        =================  ================================================
        ``sec_ip``         Integration point index (1‑based)
        ``sec_sub_idx``    Index into the sub‑element arrays above
        ``sec_N``          Axial force at this IP
        ``sec_Mz``         Major‑axis moment
        ``sec_My``         Minor‑axis moment
        ``sec_Vz``         Shear in local z
        ``sec_Vy``         Shear in local y
        ``sec_T``          Torsion
        ``sec_eps_max``    Maximum fiber strain (tension) at this IP
        ``sec_sig_max``    Maximum fiber stress (tension) at this IP
        ``sec_eps_min``    Minimum fiber strain (compression) at this IP
        ``sec_sig_min``    Minimum fiber stress (compression) at this IP
        =================  ================================================

        **Metadata**:

        ================  ================================================
        Array             Description
        ================  ================================================
        ``force_unit``    Force unit string (e.g. ``"N"``, ``"kN"``)
        ``length_unit``   Length unit string (e.g. ``"m"``, ``"mm"``)
        ``metadata_json`` JSON string with creation timestamp, model
                          stats, config, and available data flags
        ================  ================================================

        **Multi‑combo export** — pass *other_combos* to include additional
        load combinations in the same NPZ file.  Each combo's arrays are
        prefixed with ``{combo_key}_``, e.g. ``"1.2D+1.5L_sub_fx_i"``.
        The metadata field ``"combos"`` lists all available combo keys.
        Shared data (node coordinates, element connectivity, etc.) is
        stored once without prefix.

        Args:
            filepath: Path for the output ``.npz`` file.
            results: Dict returned by :meth:`run_static_analysis`.
            section_responses: Optional dict of what extra data to save.
                Keys: ``'section_forces'``, ``'section_defo'``,
                ``'fiber_stress'``, ``'fiber_strain'``.  Set to ``True``
                to include that data.  Only has effect when fiber or
                nonlinear sections are in use.
            other_combos: Optional dict mapping combo keys to their
                ``elem_forces`` (from
                :meth:`extract_static_element_forces`).  Example::

                    other_combos={
                        "1.2D+1.5L": b.extract_static_element_forces(),
                        "1.2D+1.5W": b.extract_static_element_forces(),
                    }
        """
        import numpy as np

        def _build_combo_arrays(key: str, combo_forces: Dict, sub_list: List,
                                n_sub: int, namespace: dict) -> None:
            """Build prefixed local‑force arrays for a load combination."""
            for q in ("fx", "fy", "fz", "mx", "my", "mz"):
                namespace[f"{key}_sub_{q}_i"] = np.full(n_sub, np.nan)
                namespace[f"{key}_sub_{q}_j"] = np.full(n_sub, np.nan)
                namespace[f"{key}_sub_{q}_i_local"] = np.full(n_sub, np.nan)
                namespace[f"{key}_sub_{q}_j_local"] = np.full(n_sub, np.nan)
            for i, sd in enumerate(sub_list):
                f = combo_forces.get(sd["elem_tag"])
                if f is None:
                    continue
                tag = sd["elem_tag"]
                for q, glb, loc_comp in [("fx", "Fx", 0), ("fy", "Fy", 1),
                                          ("fz", "Fz", 2), ("mx", "Mx", 0),
                                          ("my", "My", 1), ("mz", "Mz", 2)]:
                    namespace[f"{key}_sub_{q}_i"][i] = f.get(glb, np.nan)
                    namespace[f"{key}_sub_{q}_j"][i] = f.get(f"{glb}_j", np.nan)
                # Local transform
                elem = sd["elem"]
                try:
                    vx, vy, vz = self._get_local_axes(elem)
                    T = np.vstack([vx, vy, vz])
                    fi = T @ np.array([f.get("Fx",0), f.get("Fy",0), f.get("Fz",0)])
                    mi = T @ np.array([f.get("Mx",0), f.get("My",0), f.get("Mz",0)])
                    fj = T @ np.array([f.get("Fx_j",0), f.get("Fy_j",0), f.get("Fz_j",0)])
                    mj = T @ np.array([f.get("Mx_j",0), f.get("My_j",0), f.get("Mz_j",0)])
                    namespace[f"{key}_sub_fx_i_local"][i] = fi[0]
                    namespace[f"{key}_sub_fy_i_local"][i] = fi[1]
                    namespace[f"{key}_sub_fz_i_local"][i] = fi[2]
                    namespace[f"{key}_sub_mx_i_local"][i] = mi[0]
                    namespace[f"{key}_sub_my_i_local"][i] = mi[1]
                    namespace[f"{key}_sub_mz_i_local"][i] = mi[2]
                    namespace[f"{key}_sub_fx_j_local"][i] = fj[0]
                    namespace[f"{key}_sub_fy_j_local"][i] = fj[1]
                    namespace[f"{key}_sub_fz_j_local"][i] = fj[2]
                    namespace[f"{key}_sub_mx_j_local"][i] = mj[0]
                    namespace[f"{key}_sub_my_j_local"][i] = mj[1]
                    namespace[f"{key}_sub_mz_j_local"][i] = mj[2]
                except Exception:
                    pass

        elements = (self.split_elements if self.split_elements
                    else self.model.frame_elements)

        # ── 1. Build child‑to‑parent t‑position map ────────────────────
        parent_breakpoints: Dict[str, List[float]] = {}
        for eid, elem in elements.items():
            if elem.inactive and elem.t_locations:
                pts = [0.0] + sorted(elem.t_locations) + [1.0]
                parent_breakpoints[eid] = pts

        # ── 2. Gather sub‑element data ─────────────────────────────────
        sub_list: List[dict] = []
        for eid, elem in elements.items():
            if elem.inactive:
                continue
            parent_id = elem.parent_id or eid
            if elem.parent_id and parent_id in parent_breakpoints:
                pts = parent_breakpoints[parent_id]
                try:
                    idx = int(eid.split("-")[-1])
                except (ValueError, IndexError):
                    idx = 0
                t_start = pts[idx] if idx < len(pts) - 1 else 0.0
                t_end = pts[idx + 1] if idx + 1 < len(pts) else 1.0
            else:
                t_start = 0.0
                t_end = 1.0
            sub_list.append({
                "sap_id": parent_id,
                "elem_tag": elem.elem_tag,
                "elem": elem,  # keep reference for local axes transform
                "t_start": t_start,
                "t_end": t_end,
            })

        # ── 3. Extract element forces ──────────────────────────────────
        elem_forces = self.extract_static_element_forces()

        n_sub = len(sub_list)
        sub_elem_tags = np.empty(n_sub, dtype=np.int32)
        sub_sap_ids: List[str] = []
        sub_t_start = np.empty(n_sub, dtype=np.float64)
        sub_t_end = np.empty(n_sub, dtype=np.float64)
        sub_node_i_tag = np.empty(n_sub, dtype=np.int32)
        sub_node_j_tag = np.empty(n_sub, dtype=np.int32)
        sub_fx_i = np.full(n_sub, np.nan)
        sub_fy_i = np.full(n_sub, np.nan)
        sub_fz_i = np.full(n_sub, np.nan)
        sub_mx_i = np.full(n_sub, np.nan)
        sub_my_i = np.full(n_sub, np.nan)
        sub_mz_i = np.full(n_sub, np.nan)
        sub_fx_j = np.full(n_sub, np.nan)
        sub_fy_j = np.full(n_sub, np.nan)
        sub_fz_j = np.full(n_sub, np.nan)
        sub_mx_j = np.full(n_sub, np.nan)
        sub_my_j = np.full(n_sub, np.nan)
        sub_mz_j = np.full(n_sub, np.nan)
        # Local-coordinate forces (computed from global via rotation matrix)
        sub_fx_i_local     = np.full(n_sub, np.nan)
        sub_fy_i_local     = np.full(n_sub, np.nan)
        sub_fz_i_local     = np.full(n_sub, np.nan)
        sub_mx_i_local     = np.full(n_sub, np.nan)
        sub_my_i_local     = np.full(n_sub, np.nan)
        sub_mz_i_local     = np.full(n_sub, np.nan)
        sub_fx_j_local     = np.full(n_sub, np.nan)
        sub_fy_j_local     = np.full(n_sub, np.nan)
        sub_fz_j_local     = np.full(n_sub, np.nan)
        sub_mx_j_local     = np.full(n_sub, np.nan)
        sub_my_j_local     = np.full(n_sub, np.nan)
        sub_mz_j_local     = np.full(n_sub, np.nan)

        for i, sd in enumerate(sub_list):
            sub_elem_tags[i] = sd["elem_tag"]
            sub_sap_ids.append(sd["sap_id"])
            sub_t_start[i] = sd["t_start"]
            sub_t_end[i] = sd["t_end"]
            # Element connectivity for standalone plotting
            elem = sd["elem"]
            sub_node_i_tag[i] = self.model.nodes[elem.node_i].node_tag
            sub_node_j_tag[i] = self.model.nodes[elem.node_j].node_tag
            f = elem_forces.get(sd["elem_tag"])
            if f is not None:
                sub_fx_i[i] = f.get("Fx", np.nan)
                sub_fy_i[i] = f.get("Fy", np.nan)
                sub_fz_i[i] = f.get("Fz", np.nan)
                sub_mx_i[i] = f.get("Mx", np.nan)
                sub_my_i[i] = f.get("My", np.nan)
                sub_mz_i[i] = f.get("Mz", np.nan)
                sub_fx_j[i] = f.get("Fx_j", np.nan)
                sub_fy_j[i] = f.get("Fy_j", np.nan)
                sub_fz_j[i] = f.get("Fz_j", np.nan)
                sub_mx_j[i] = f.get("Mx_j", np.nan)
                sub_my_j[i] = f.get("My_j", np.nan)
                sub_mz_j[i] = f.get("Mz_j", np.nan)

                # Local-coordinate transform
                elem = sd["elem"]
                try:
                    vec_x, vec_y, vec_z = self._get_local_axes(elem)
                    T = np.vstack([vec_x, vec_y, vec_z])  # global→local rotation
                    fi = T @ np.array([f.get("Fx", 0.0), f.get("Fy", 0.0), f.get("Fz", 0.0)])
                    mi = T @ np.array([f.get("Mx", 0.0), f.get("My", 0.0), f.get("Mz", 0.0)])
                    fj = T @ np.array([f.get("Fx_j", 0.0), f.get("Fy_j", 0.0), f.get("Fz_j", 0.0)])
                    mj = T @ np.array([f.get("Mx_j", 0.0), f.get("My_j", 0.0), f.get("Mz_j", 0.0)])
                    sub_fx_i_local[i], sub_fy_i_local[i], sub_fz_i_local[i] = fi
                    sub_mx_i_local[i], sub_my_i_local[i], sub_mz_i_local[i] = mi
                    sub_fx_j_local[i], sub_fy_j_local[i], sub_fz_j_local[i] = fj
                    sub_mx_j_local[i], sub_my_j_local[i], sub_mz_j_local[i] = mj

                except Exception:
                    pass

        # ── 3c. Additional load combinations ──────────────────────────
        # For each combo we build prefixed force arrays using the same
        # sub‑element list and local‑axis transform.
        combo_names: List[str] = []
        if other_combos:
            for combo_key, combo_forces in other_combos.items():
                combo_names.append(combo_key)
                _build_combo_arrays(combo_key, combo_forces, sub_list,
                                    n_sub, locals())

        # ── 3b. Section responses (integration-point-level data) ───────
        save_sec_forces = section_responses and section_responses.get("section_forces")
        save_sec_defo   = section_responses and section_responses.get("section_defo")
        save_fiber_stress = section_responses and section_responses.get("fiber_stress")
        save_fiber_strain = section_responses and section_responses.get("fiber_strain")

        sec_ip_list: List[int] = []
        sec_sub_idx_list: List[int] = []
        sec_N_list: List[float] = []
        sec_Mz_list: List[float] = []
        sec_My_list: List[float] = []
        sec_Vz_list: List[float] = []
        sec_Vy_list: List[float] = []
        sec_T_list: List[float] = []
        sec_eps_max_list: List[float] = []
        sec_sig_max_list: List[float] = []
        sec_eps_min_list: List[float] = []
        sec_sig_min_list: List[float] = []

        if save_sec_forces or save_sec_defo or save_fiber_stress or save_fiber_strain:
            # Determine number of integration points from config
            beam_int = self.config.get("beam_integration", "Lobatto")
            if beam_int == "HingeRadau":
                n_ip = 3  # I‑end hinge, elastic interior, J‑end hinge
            else:
                n_ip = self.config.get("num_int_pts", 3)

            for i, sd in enumerate(sub_list):
                tag = sd["elem_tag"]
                for ip in range(1, n_ip + 1):
                    sec_ip_list.append(ip)
                    sec_sub_idx_list.append(i)

                    # Section forces (OpenSees order: N, Mz, My, Vz, Vy, T)
                    if save_sec_forces:
                        try:
                            sf = ops.eleResponse(tag, "section", ip, "force")
                            sec_N_list.append(float(sf[0]))
                            sec_Mz_list.append(float(sf[1]))
                            sec_My_list.append(float(sf[2]))
                            sec_Vz_list.append(float(sf[3]))
                            sec_Vy_list.append(float(sf[4]))
                            sec_T_list.append(float(sf[5]))
                        except Exception:
                            sec_N_list.append(np.nan)
                            sec_Mz_list.append(np.nan)
                            sec_My_list.append(np.nan)
                            sec_Vz_list.append(np.nan)
                            sec_Vy_list.append(np.nan)
                            sec_T_list.append(np.nan)
                    else:
                        sec_N_list.append(np.nan)
                        sec_Mz_list.append(np.nan)
                        sec_My_list.append(np.nan)
                        sec_Vz_list.append(np.nan)
                        sec_Vy_list.append(np.nan)
                        sec_T_list.append(np.nan)

                    # Fiber extremes at this IP
                    has_fiber = save_fiber_stress or save_fiber_strain
                    if has_fiber:
                        try:
                            fd = ops.eleResponse(tag, "section", ip, "fiberData")
                            # fiberData returns flat [σ1, ε1, σ2, ε2, ...]
                            stresses = fd[0::2]
                            strains  = fd[1::2]
                            if save_fiber_stress:
                                sec_sig_max_list.append(float(max(stresses)))
                                sec_sig_min_list.append(float(min(stresses)))
                            if save_fiber_strain:
                                sec_eps_max_list.append(float(max(strains)))
                                sec_eps_min_list.append(float(min(strains)))
                        except Exception:
                            if save_fiber_stress:
                                sec_sig_max_list.append(np.nan)
                                sec_sig_min_list.append(np.nan)
                            if save_fiber_strain:
                                sec_eps_max_list.append(np.nan)
                                sec_eps_min_list.append(np.nan)
                    else:
                        if save_fiber_stress:
                            sec_sig_max_list.append(np.nan)
                            sec_sig_min_list.append(np.nan)
                        if save_fiber_strain:
                            sec_eps_max_list.append(np.nan)
                            sec_eps_min_list.append(np.nan)

        # ── 4. Nodal displacements ─────────────────────────────────────
        nodal_disp = results.get("nodal_displacements", {})
        n_nodes = len(self.model.nodes)
        node_tags = np.empty(n_nodes, dtype=np.int32)
        node_sap_ids: List[str] = []
        node_x = np.empty(n_nodes, dtype=np.float64)
        node_y = np.empty(n_nodes, dtype=np.float64)
        node_z = np.empty(n_nodes, dtype=np.float64)
        node_dx = np.full(n_nodes, np.nan)
        node_dy = np.full(n_nodes, np.nan)
        node_dz = np.full(n_nodes, np.nan)

        for i, (nid, node) in enumerate(self.model.nodes.items()):
            node_tags[i] = node.node_tag
            node_sap_ids.append(nid)
            node_x[i] = node.x
            node_y[i] = node.y
            node_z[i] = node.z
            disp = nodal_disp.get(node.node_tag)
            if disp is not None:
                node_dx[i] = disp[0]
                node_dy[i] = disp[1]
                node_dz[i] = disp[2]

        # ── 5. Units & metadata ────────────────────────────────────────
        force_unit = self.units.get("F", "N")
        length_unit = self.units.get("L", "m")

        # Build JSON metadata string for self‑describing NPZ
        import datetime
        import json
        elem_type = self.config.get("element_type", "elasticBeamColumn")
        metadata = {
            "created": datetime.datetime.now().isoformat(),
            "toolkit_version": getattr(__import__("fea_toolkit", fromlist=["__version__"]), "__version__", "unknown"),
            "model_nodes": len(self.model.nodes),
            "model_frame_elements": len(self.model.frame_elements),
            "model_area_elements": len(self.model.area_elements),
            "model_sections": len(self.model.sections),
            "model_groups": len(self.model.groups),
            "split_elements": self.config.get("split_elements", False),
            "element_type": elem_type,
            "force_unit": force_unit,
            "length_unit": length_unit,
            "has_section_responses": bool(sec_ip_list),
            "has_local_forces": True,
            "combos": combo_names if combo_names else None,
        }

        # ── 6. Build the save dict ─────────────────────────────────────
        # Include combo-prefixed arrays if any
        combo_arrays = {}
        if combo_names:
            for key in combo_names:
                for q in ("fx", "fy", "fz", "mx", "my", "mz"):
                    for end in ("i", "j"):
                        for loc in ("", "_local"):
                            aname = f"{key}_sub_{q}_{end}{loc}"
                            val = locals().get(aname)
                            if val is not None:
                                combo_arrays[aname] = val

        save_dict = {
            # Elements
            "sub_elem_tags": sub_elem_tags,
            "sub_sap_ids": np.array(sub_sap_ids, dtype=object),
            "sub_t_start": sub_t_start,
            "sub_t_end": sub_t_end,
            "sub_node_i_tag": sub_node_i_tag,
            "sub_node_j_tag": sub_node_j_tag,
            "sub_fx_i": sub_fx_i, "sub_fy_i": sub_fy_i, "sub_fz_i": sub_fz_i,
            "sub_mx_i": sub_mx_i, "sub_my_i": sub_my_i, "sub_mz_i": sub_mz_i,
            "sub_fx_j": sub_fx_j, "sub_fy_j": sub_fy_j, "sub_fz_j": sub_fz_j,
            "sub_mx_j": sub_mx_j, "sub_my_j": sub_my_j, "sub_mz_j": sub_mz_j,
            # Local-coordinate forces
            "sub_fx_i_local": sub_fx_i_local, "sub_fy_i_local": sub_fy_i_local, "sub_fz_i_local": sub_fz_i_local,
            "sub_mx_i_local": sub_mx_i_local, "sub_my_i_local": sub_my_i_local, "sub_mz_i_local": sub_mz_i_local,
            "sub_fx_j_local": sub_fx_j_local, "sub_fy_j_local": sub_fy_j_local, "sub_fz_j_local": sub_fz_j_local,
            "sub_mx_j_local": sub_mx_j_local, "sub_my_j_local": sub_my_j_local, "sub_mz_j_local": sub_mz_j_local,

            # Nodes
            "node_tags": node_tags,
            "node_sap_ids": np.array(node_sap_ids, dtype=object),
            "node_x": node_x, "node_y": node_y, "node_z": node_z,
            "node_dx": node_dx, "node_dy": node_dy, "node_dz": node_dz,
            # Metadata
            "force_unit": force_unit,
            "length_unit": length_unit,
            "metadata_json": np.array(json.dumps(metadata), dtype=object),
            # Multi‑combo prefixed arrays
            **combo_arrays,
        }

        # Section responses (only add if we collected data)
        if sec_ip_list:
            save_dict["sec_ip"] = np.array(sec_ip_list, dtype=np.int32)
            save_dict["sec_sub_idx"] = np.array(sec_sub_idx_list, dtype=np.int32)
            if save_sec_forces:
                save_dict["sec_N"] = np.array(sec_N_list)
                save_dict["sec_Mz"] = np.array(sec_Mz_list)
                save_dict["sec_My"] = np.array(sec_My_list)
                save_dict["sec_Vz"] = np.array(sec_Vz_list)
                save_dict["sec_Vy"] = np.array(sec_Vy_list)
                save_dict["sec_T"] = np.array(sec_T_list)
            if save_fiber_stress:
                save_dict["sec_sig_max"] = np.array(sec_sig_max_list)
                save_dict["sec_sig_min"] = np.array(sec_sig_min_list)
            if save_fiber_strain:
                save_dict["sec_eps_max"] = np.array(sec_eps_max_list)
                save_dict["sec_eps_min"] = np.array(sec_eps_min_list)

        # ── 6. Write ───────────────────────────────────────────────────
        np.savez_compressed(filepath, **save_dict)

        if self.config.get("verbose"):
            print(f"  Exported results to {filepath}")
            print(f"    {n_sub} sub‑elements, {n_nodes} nodes")
            if sec_ip_list:
                n_sec_pts = len(sec_ip_list)
                print(f"    {n_sec_pts} section integration points")

    # =========================================================================
    # Pushover analysis
    #
    # References:
    #   - ASCE 41-17 §7.2: Seismic Evaluation and Retrofit of Existing Buildings
    #   - ATC-40 §2.2: Seismic Evaluation and Retrofit of Concrete Buildings
    #   - OpenSees RCFramePushOver_v2.py (PEER example):
    #     docs/references/RCFramePushOver_v2.py
    #
    # The two-stage approach (gravity → loadConst → displacement-controlled
    # lateral push) follows the standard OpenSees pushover workflow from the
    # PEER example above.
    # =========================================================================
    def run_pushover_analysis(
        self,
        gravity_patterns: Dict[str, float],
        lateral_load_type: str = 'uniform',
        lateral_pattern_name: Optional[str] = None,
        lateral_direction: str = 'X',
        control_node_tag: Optional[int] = None,
        max_disp: float = 0.5,
        num_steps: int = 100,
        fundamental_period: Optional[float] = None,
        mode_shapes: Optional[Dict] = None,
        mode_index: int = 0,
        print_progress: bool = True,
    ) -> Dict[str, Any]:
        """Run a non‑linear static pushover analysis.

        The model is rebuilt with ``forceBeamColumn`` elements and **fiber
        sections** (overriding the builder's current ``element_type`` and
        section config).  The analysis proceeds in two stages:

        1. **Gravity** — load‑controlled with the specified
           ``gravity_patterns``.
        2. **Lateral push** — displacement‑controlled on the control node,
           using the chosen lateral load pattern as the reference
           distribution shape.

        The lateral load shape is determined by *lateral_load_type*:

        * ``'uniform'`` — mass‑proportional (uniform acceleration).
          $F_i \\propto m_i$.  Per ASCE 41 / ATC-40 "Uniform" pattern.
          Requires masses (call ``compute_seismic_masses()`` first or
          ensure mass data is available).
        * ``'triangular'`` — $F_i \\propto m_i h_i^k$, where $k$ depends
          on the fundamental period (ASCE 7 ELF procedure).  Requires
          masses and optionally *fundamental_period*.
        * ``'mode1'`` — $F_i \\propto m_i \\phi_{i1}$, proportional to
          mass × first‑mode eigenvector.  Requires *mode_shapes* from
          :meth:`extract_mode_shapes`.
        * ``'pattern'`` — uses the existing SAP2000 load pattern named
          by *lateral_pattern_name* (distributed loads on frame elements).

        Args:
            gravity_patterns: Dict mapping load pattern name → scale factor
                (e.g. ``{'DEAD': 1.0, 'SDL': 0.5}``).
            lateral_load_type: One of ``'uniform'``, ``'triangular'``,
                ``'mode1'``, or ``'pattern'``.
            lateral_pattern_name: Name of the SAP2000 load pattern to use
                as the lateral load (only used when
                ``lateral_load_type='pattern'``).
            lateral_direction: ``'X'``, ``'Y'``, or ``'Z'`` — the DOF at
                the control node that is displaced.
            control_node_tag: Node tag for displacement control.  If
                ``None``, the node with the highest Z coordinate that has
                a reaction restraint in the push direction is chosen
                automatically.
            max_disp: Maximum displacement of the control node (model
                length units, typically m).
            num_steps: Number of push increments.
            fundamental_period: Natural period of the fundamental mode (s).
                Used to compute the $k$ exponent for ``'triangular'``
                pattern.  If ``None``, $k = 1.0$ is used.
            mode_shapes: Output of :meth:`extract_mode_shapes`, required
                when ``lateral_load_type='mode1'``.
            mode_index: 0‑based mode index to use for ``'mode1'`` load
                shape (default 0 = fundamental mode).
            print_progress: If True, print step summaries.

        Returns:
            Dictionary with keys:

            * ``'step'`` — list of step numbers (0‑based, 0 = after gravity).
            * ``'control_disp'`` — list of control node displacements.
            * ``'base_shear'`` — list of total base shear in the push
              direction at each step.
            * ``'status'`` — list of analysis return codes (0 = success).
            * ``'gravity_displacements'`` — nodal displacements after
              gravity (before push).
            * ``'control_node'`` — the control node tag used.
            * ``'dof'`` — the push DOF (1, 2, or 3).
            * ``'lateral_load_type'`` — the load type used.
        """
        valid_types = {'uniform', 'triangular', 'mode1', 'pattern'}
        if lateral_load_type not in valid_types:
            raise ValueError(
                f"lateral_load_type='{lateral_load_type}' not recognised.  "
                f"Choose from {sorted(valid_types)}."
            )
        if lateral_load_type == 'pattern' and not lateral_pattern_name:
            raise ValueError(
                "lateral_pattern_name is required when lateral_load_type='pattern'."
            )

        if self.config['verbose']:
            print("\n===== PUSHOVER ANALYSIS =====")
            print(f"  Gravity: {gravity_patterns}")
            print(f"  Lateral type: {lateral_load_type}  dir={lateral_direction}")
            print(f"  Max disp: {max_disp}  Steps: {num_steps}")

        dof_map = {'X': 1, 'Y': 2, 'Z': 3}
        dof = dof_map.get(lateral_direction.upper(), 1)

        # ── 1. Rebuild with fiber sections + forceBeamColumn ──
        push_config = dict(self.config)
        # Override element type: forceBeamColumn and dispBeamColumn both
        # support fiber sections; elasticBeamColumn does NOT — it ignores
        # fiber definitions and always behaves elastically.
        push_config['element_type'] = 'dispBeamColumn'
        push_config['create_fiber_sections'] = True
        push_config['use_elastic_sections'] = False
        # Only apply Corotational + robust solver when braces are subdivided
        has_subdivided_braces = (
            push_config.get('subdivide_braces')
            and hasattr(self, '_brace_selection')
            and self._brace_selection
        )
        if has_subdivided_braces:
            # NOTE: Corotational geometry with imperfect subdivided braces
            # does NOT converge under gravity loads (the large-displacement
            # formulation is ill-conditioned for the small-displacement
            # gravity stage).  To make Approach A work, a two-stage rebuild
            # is needed: Linear geometry → gravity → Corotational → push.
            # See docs/pushover_analysis.md for details.
            push_config['geom_transf_type'] = 'PDelta'
            push_config['solver_test_tol'] = 1e-5
            push_config['solver_test_max_iter'] = 50
            push_config['solver_algorithm'] = 'ModifiedNewton'
            push_config['gravity_num_substeps'] = 5

        # Check if all frame sections support fiber patches; fall back to elastic
        all_support_fiber = True
        for sec in self.model.sections.values():
            if isinstance(sec, ShellSection):
                continue  # shell sections don't need fiber patches
            try:
                sec.to_fiber_patches(mat_tag=1)
            except NotImplementedError:
                all_support_fiber = False
                break
        if not all_support_fiber:
            push_config['create_fiber_sections'] = False
            push_config['use_elastic_sections'] = True
            push_config['element_type'] = 'elasticBeamColumn'
            if self.config['verbose'] or print_progress:
                print("  Some sections do not support fiber patches — "
                      "using elastic sections (linear pushover)")

        self.config = push_config

        ops.wipe()
        # wipe() clears all MPCs — caller must re-apply edge constraints
        self._has_edge_constraints = False
        ops.model('basic', '-ndm', 3, '-ndf', 6)
        self._create_nodes()
        self._apply_restraints()
        self._create_materials()
        self._create_sections()
        if self.config.get('split_elements', False):
            self._split_elements()
        self._create_elements()

        # ── 2. Compute seismic masses (needed for uniform/triangular/mode1) ──
        node_masses: Dict[str, float] = {}
        if lateral_load_type in ('uniform', 'triangular', 'mode1'):
            if self.model.mass_sources:
                node_masses = self.compute_seismic_masses()
            else:
                # Fallback: lump element self-weight to nodes
                node_masses = self._compute_fallback_masses()
            if print_progress:
                total_mass = sum(node_masses.values())
                print(f"  Seismic mass: {total_mass:.2f} tonnes")

        # ── 3. Apply gravity ──
        grav_results = self.run_static_analysis(
            extract_reactions=True,
            pattern_scales=gravity_patterns,
        )
        grav_disp = grav_results.get('nodal_displacements', {}) if grav_results else {}

        # Record base shear from gravity (before lateral loads)
        base_shear_grav = grav_results.get('summed_reactions', {}).get(
            'fx' if lateral_direction.upper() == 'X' else
            'fy' if lateral_direction.upper() == 'Y' else 'fz',
            0.0
        ) if grav_results else 0.0

        if print_progress and grav_results:
            sr = grav_results.get('summed_reactions', {})
            print(f"  Gravity reactions: Fx={sr.get('fx',0):+.3f}  "
                  f"Fz={sr.get('fz',0):+.3f}  ok=0")

        # ── 4. Choose control node ──
        if control_node_tag is None:
            candidates = []
            for nid, node in self.model.nodes.items():
                restr = self.model.restraints.get(nid)
                if restr is None or not restr.dofs[dof - 1]:
                    candidates.append((node.z, node.node_tag))
            if not candidates:
                for nid, node in self.model.nodes.items():
                    candidates.append((node.z, node.node_tag))
            candidates.sort(key=lambda x: -x[0])
            control_node_tag = candidates[0][1]

        if print_progress:
            print(f"  Control node: {control_node_tag}")
            print(f"  Push DOF: {dof} ({lateral_direction})")

        # ── 5. Lock gravity loads and create lateral reference pattern ──
        # Following the standard OpenSees two-stage approach
        # (see docs/references/RCFramePushOver_v2.py):
        #   loadConst('-time', 0.0)  → lock gravity, reset domain time
        #   pattern('Plain', <new tag>, <new time series>) → lateral shape
        ops.loadConst('-time', 0.0)

        # Count only *active* gravity patterns (non-zero scale) to determine
        # the next available tag — _create_loads assigns tags 1..N sequentially
        # for active patterns, so N+1 is guaranteed free.
        num_active = sum(1 for s in gravity_patterns.values() if abs(s) >= 1e-12)
        lat_tag = num_active + 1
        ops.timeSeries('Linear', lat_tag)
        ops.pattern('Plain', lat_tag, lat_tag)

        if lateral_load_type == 'pattern':
            # Use existing SAP2000 frame distributed loads
            for ld in self.model.frame_dist_loads:
                if ld.pattern != lateral_pattern_name:
                    continue

                dir_map = {'Gravity': (0,0,-1), 'X': (1,0,0), 'Y': (0,1,0), 'Z': (0,0,1)}
                gx, gy, gz = dir_map.get(ld.direction, (0, 0, 0))

                elem = (self.split_elements if self.split_elements
                        else self.model.frame_elements).get(ld.frame_id)
                if elem is None or getattr(elem, 'inactive', False):
                    continue
                elem_tag = self.frame_tag_map.get(ld.frame_id)
                if elem_tag is None:
                    continue

                wa, wb = float(ld.val_a), float(ld.val_b)
                aL, bL = ld.rdist_a, ld.rdist_b
                try:
                    vx, vy, vz = self._get_local_axes(elem)
                except Exception:
                    continue
                T = np.column_stack([vx, vy, vz])
                g_local = np.linalg.solve(T, np.array([gx, gy, gz]))
                wy_a = g_local[1] * wa; wz_a = g_local[2] * wa; wx_a = g_local[0] * wa
                wy_b = g_local[1] * wb; wz_b = g_local[2] * wb; wx_b = g_local[0] * wb

                if abs(wa) < 1e-12 and abs(wb) < 1e-12:
                    continue

                is_uniform = abs(wa - wb) < 1e-12
                if is_uniform and abs(aL) < 1e-12 and abs(bL - 1.0) < 1e-12:
                    ops.eleLoad('-ele', elem_tag, '-type', '-beamUniform',
                                wy_a, wz_a, wx_a)
                elif is_uniform:
                    ops.eleLoad('-ele', elem_tag, '-type', '-beamUniform',
                                wy_a, wz_a, wx_a, aL, bL)
                else:
                    for i in range(4):
                        span = bL - aL
                        seg_a = aL + i * span / 4
                        seg_b = aL + (i + 1) * span / 4
                        xi = (i + 0.5) / 4
                        ops.eleLoad('-ele', elem_tag, '-type', '-beamUniform',
                                    wy_a + (wy_b - wy_a) * xi,
                                    wz_a + (wz_b - wz_a) * xi,
                                    wx_a + (wx_b - wx_a) * xi,
                                    seg_a, seg_b)
        else:
            # Generate nodal loads from mass distribution
            if lateral_load_type == 'uniform':
                nodal_loads = self._compute_uniform_lateral_loads(
                    lateral_direction, node_masses,
                )
            elif lateral_load_type == 'triangular':
                nodal_loads = self._compute_triangular_lateral_loads(
                    lateral_direction, node_masses, fundamental_period,
                )
            elif lateral_load_type == 'mode1':
                if mode_shapes is None:
                    raise ValueError(
                        "mode_shapes is required when lateral_load_type='mode1'.  "
                        "Call extract_mode_shapes() first."
                    )
                nodal_loads = self._compute_mode_shape_lateral_loads(
                    lateral_direction, node_masses, mode_shapes, mode_index,
                )
            else:
                nodal_loads = {}

            for tag, (fx, fy, fz) in nodal_loads.items():
                ops.load(tag, fx, fy, fz, 0.0, 0.0, 0.0)

        # ── 6. Lateral push ──
        ops.wipeAnalysis()
        disp_inc = max_disp / num_steps
        if self._has_edge_constraints:
            ops.constraints('Penalty', 1.0e12, 1.0e12)
        else:
            ops.constraints(self.config.get('solver_constraints', 'Transformation'))
        ops.numberer('RCM')
        ops.system(self.config.get('solver_system', 'BandGen'))
        ops.test('NormDispIncr', self.config.get('solver_test_tol', 1e-4),
                 self.config.get('solver_test_max_iter', 20), 0, 2)
        algo = self.config.get('solver_algorithm', 'Newton')
        if algo == 'ModifiedNewton':
            ops.algorithm('ModifiedNewton', '-initial')
        elif algo == 'NewtonLineSearch':
            ops.algorithm('NewtonLineSearch')
        elif algo == 'KrylovNewton':
            ops.algorithm('KrylovNewton')
        else:
            ops.algorithm(algo)
        ops.integrator('DisplacementControl', control_node_tag, dof, disp_inc)
        ops.analysis('Static')

        grav_ctrl_disp = 0.0
        try:
            grav_ctrl_disp = ops.nodeDisp(control_node_tag)[dof - 1]
        except Exception:
            pass

        steps = [0]
        ctrl_disps = [0.0]
        base_shears = [base_shear_grav]
        statuses = [0]

        try:
            ops.reactions()
            bs = 0.0
            for nid, rst in self.model.restraints.items():
                node = self.model.nodes.get(nid)
                if node is None: continue
                if rst.dofs[dof - 1]:
                    bs += ops.nodeReaction(node.node_tag, dof)
            base_shears[0] = bs
        except Exception:
            pass

        for step in range(1, num_steps + 1):
            ok = ops.analyze(1)
            statuses.append(ok)

            try:
                cd_total = ops.nodeDisp(control_node_tag)[dof - 1]
                cd = cd_total - grav_ctrl_disp   # relative to gravity
            except Exception:
                cd = 0.0
            ctrl_disps.append(cd)

            try:
                ops.reactions()
                bs = 0.0
                for nid, rst in self.model.restraints.items():
                    node = self.model.nodes.get(nid)
                    if node is None:
                        continue
                    if rst.dofs[dof - 1]:
                        bs += ops.nodeReaction(node.node_tag, dof)
            except Exception:
                bs = 0.0
            base_shears.append(bs)
            steps.append(step)

            if print_progress and (step % max(1, num_steps // 10) == 0 or ok != 0):
                print(f"  Step {step:4d}/{num_steps}: disp={cd:+.6f}  "
                      f"base_shear={bs:+.3f}  ok={ok}")

            if ok != 0:
                if print_progress:
                    print(f"  Pushover stopped at step {step} (non-converged)")
                break

        results = {
            'step': steps,
            'control_disp': ctrl_disps,
            'base_shear': base_shears,
            'status': statuses,
            'gravity_displacements': grav_disp,
            'control_node': control_node_tag,
            'dof': dof,
            'lateral_load_type': lateral_load_type,
        }
        if print_progress:
            fd = ctrl_disps[-1] if ctrl_disps else 0.0
            fb = base_shears[-1] if base_shears else 0.0
            print(f"\n  Pushover complete: {len(steps)-1} steps, "
                  f"max disp = {fd:.4f}, max base shear = {fb:.1f}")
        return results

    # =========================================================================
    # Lateral load pattern generators (for pushover)
    # =========================================================================

    def _compute_fallback_masses(self) -> Dict[str, float]:
        """Compute nodal masses from element self‑weight when no MASS SOURCE.

        Used as a fallback when the model has no mass source definitions.
        Masses are used to define the shape of uniform/triangular pushover
        load patterns.
        """
        node_mass: Dict[str, float] = {}
        g = 9.81
        elements = (self.split_elements if self.split_elements
                    else self.model.frame_elements)
        assignments = (self.split_assignments if self.split_elements
                       else self.model.frame_assignments)

        for eid, elem in elements.items():
            if getattr(elem, 'inactive', False):
                continue
            sec_name = assignments.get(eid) if assignments else None
            if not sec_name or sec_name not in self.model.sections:
                continue
            sec = self.model.sections[sec_name]
            mat = self.model.materials.get(sec.material)
            if mat is None or mat.unit_weight == 0:
                continue
            ni = self.model.nodes.get(elem.node_i)
            nj = self.model.nodes.get(elem.node_j)
            if ni is None or nj is None:
                continue
            L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
            if L < 1e-12:
                continue
            weight = sec.A * mat.unit_weight * L
            mass = weight / g
            node_mass[elem.node_i] = node_mass.get(elem.node_i, 0.0) + mass * 0.5
            node_mass[elem.node_j] = node_mass.get(elem.node_j, 0.0) + mass * 0.5

        return node_mass

    def _compute_uniform_lateral_loads(
        self,
        direction: str,
        node_masses: Dict[str, float],
    ) -> Dict[int, Tuple[float, float, float]]:
        """Compute mass‑proportional lateral loads (uniform acceleration).

        Per ASCE 41 / ATC‑40 "Uniform" pattern — each node with mass
        receives a load proportional to its mass in the push direction.
        The absolute magnitude is irrelevant because ``DisplacementControl``
        scales the entire pattern to achieve the target displacement.

        Returns:
            ``{node_tag: (fx, fy, fz)}`` in global coordinates.
        """
        dof_idx = {'X': 0, 'Y': 1, 'Z': 2}.get(direction.upper(), 0)

        nodal_loads: Dict[int, Tuple[float, float, float]] = {}
        for nid, mass in node_masses.items():
            if mass <= 0:
                continue
            node = self.model.nodes.get(nid)
            if node is None:
                continue
            f = [0.0, 0.0, 0.0]
            f[dof_idx] = mass
            nodal_loads[node.node_tag] = (f[0], f[1], f[2])
        return nodal_loads

    def _compute_triangular_lateral_loads(
        self,
        direction: str,
        node_masses: Dict[str, float],
        fundamental_period: Optional[float] = None,
    ) -> Dict[int, Tuple[float, float, float]]:
        """Compute triangular (ELF) lateral loads proportional to $m_i h_i^k$.

        Per ASCE 7 / ASCE 41:
        * $k = 1.0$ for $T \\le 0.5$ s
        * $k = 2.0$ for $T \\ge 2.5$ s
        * Linear interpolation for $0.5 < T < 2.5$ s

        Height $h_i$ is measured relative to the lowest node in the model.

        Returns:
            ``{node_tag: (fx, fy, fz)}`` in global coordinates.
        """
        dof_idx = {'X': 0, 'Y': 1, 'Z': 2}.get(direction.upper(), 0)

        # Find base elevation
        z_vals = [node.z for node in self.model.nodes.values()]
        z_min = min(z_vals) if z_vals else 0.0

        # Compute k exponent per ASCE 7
        if fundamental_period is None:
            k = 1.0
        elif fundamental_period <= 0.5:
            k = 1.0
        elif fundamental_period >= 2.5:
            k = 2.0
        else:
            k = 1.0 + (fundamental_period - 0.5) / 2.0

        nodal_loads: Dict[int, Tuple[float, float, float]] = {}
        for nid, mass in node_masses.items():
            if mass <= 0:
                continue
            node = self.model.nodes.get(nid)
            if node is None:
                continue
            h = max(node.z - z_min, 0.0)
            f_mag = mass * (h ** k)
            if abs(f_mag) < 1e-12:
                continue
            f = [0.0, 0.0, 0.0]
            f[dof_idx] = f_mag
            nodal_loads[node.node_tag] = (f[0], f[1], f[2])
        return nodal_loads

    def _compute_mode_shape_lateral_loads(
        self,
        direction: str,
        node_masses: Dict[str, float],
        mode_shapes: Dict[int, Dict[int, Tuple[float, float, float]]],
        mode_index: int = 0,
    ) -> Dict[int, Tuple[float, float, float]]:
        """Compute mode‑shape‑proportional lateral loads $F_i = m_i \\phi_i$.

        Each node receives a load proportional to its mass times the
        eigenvector component in the push direction.

        Returns:
            ``{node_tag: (fx, fy, fz)}`` in global coordinates.
        """
        if mode_index not in mode_shapes:
            raise ValueError(f"Mode index {mode_index} not found in mode_shapes")

        mode = mode_shapes[mode_index]  # {node_tag: (dx, dy, dz)}
        dof_idx = {'X': 0, 'Y': 1, 'Z': 2}.get(direction.upper(), 0)

        nodal_loads: Dict[int, Tuple[float, float, float]] = {}
        for nid, mass in node_masses.items():
            if mass <= 0:
                continue
            node = self.model.nodes.get(nid)
            if node is None:
                continue
            phi = mode.get(node.node_tag, (0.0, 0.0, 0.0))
            f_mag = mass * phi[dof_idx]
            if abs(f_mag) < 1e-12:
                continue
            f = [0.0, 0.0, 0.0]
            f[dof_idx] = f_mag
            nodal_loads[node.node_tag] = (f[0], f[1], f[2])
        return nodal_loads

    # =========================================================================
    # Seismic masses (based on MASS SOURCE definition)
    # =========================================================================
    def compute_seismic_masses(self, g: float = 9.81) -> Dict[str, float]:
        """Compute lumped nodal masses from the model's MASS SOURCE entries.

        The MASS SOURCE table in SAP2000 controls how masses are derived:

        * ``Elements=True``  — element self‑weight is converted to mass.
        * ``Masses=True``    — any explicit lumped masses are included
          (not yet supported — no SAP2000 example available).
        * ``Loads=True``     — loads from the specified load pattern(s) are
          converted to mass (total force / g).

        All mass contributions are lumped to nodes and assigned via
        ``ops.mass(node, m, m, m, 0, 0, 0)``.

        Args:
            g: Gravitational acceleration (m/s²).  Default 9.81.

        Returns:
            Dictionary mapping node ID → total lumped mass (tonnes).
        """
        if self.config['verbose']:
            print("Computing seismic masses from MASS SOURCE...")

        node_mass: Dict[str, float] = {}
        all_patterns = self.model.load_patterns
        all_materials = self.model.materials

        # Determine which elements / assignments to use (split or original)
        if self.split_elements:
            elements = self.split_elements
            assignments = self.split_assignments
            dist_loads = self.split_dist_loads
        else:
            elements = self.model.frame_elements
            assignments = self.model.frame_assignments
            dist_loads = self.model.frame_dist_loads

        for ms in self.model.mass_sources.values():
            # --- Element self‑weight mass ---
            if ms.elements:
                # ── Frame elements ──
                for eid, elem in elements.items():
                    if getattr(elem, 'inactive', False):
                        continue
                    sec_name = assignments.get(eid) if assignments else None
                    if not sec_name or sec_name not in self.model.sections:
                        continue
                    sec = self.model.sections[sec_name]
                    mat = all_materials.get(sec.material)
                    if mat is None or mat.unit_weight == 0:
                        continue
                    ni = self.model.nodes.get(elem.node_i)
                    nj = self.model.nodes.get(elem.node_j)
                    if ni is None or nj is None:
                        continue
                    L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
                    if L < 1e-12:
                        continue
                    # Weight = volume × unit_weight
                    weight = sec.A * mat.unit_weight * L
                    mass = weight / g
                    node_mass[elem.node_i] = node_mass.get(elem.node_i, 0.0) + mass * 0.5
                    node_mass[elem.node_j] = node_mass.get(elem.node_j, 0.0) + mass * 0.5

                # ── Area elements ──
                for aid, area_elem in self.model.area_elements.items():
                    if getattr(area_elem, 'inactive', False):
                        continue
                    sec_name = self.model.area_assignments.get(aid)
                    if not sec_name:
                        continue
                    sec = self.model.sections.get(sec_name)
                    if sec is None:
                        continue
                    mat = all_materials.get(sec.material)
                    if mat is None or mat.unit_weight == 0:
                        continue
                    thickness = area_elem.thickness
                    if thickness < 1e-12:
                        continue
                    # Polygon area via Newell's method
                    area_mag, _ = self._polygon_area(area_elem.node_ids)
                    if area_mag < 1e-12:
                        continue
                    # Weight = area × thickness × unit_weight
                    weight = area_mag * thickness * mat.unit_weight
                    mass = weight / g
                    n_corners = len(area_elem.node_ids)
                    for nid in area_elem.node_ids:
                        node_mass[nid] = node_mass.get(nid, 0.0) + mass / n_corners

            # --- Load‑based mass ---
            if ms.loads and ms.load_pattern:
                for lp_name, mult in ms.load_pattern.items():
                    if abs(mult) < 1e-12:
                        continue
                    # ── Frame distributed loads → mass ──
                    for ld in dist_loads or []:
                        if ld.pattern != lp_name:
                            continue
                        elem = elements.get(ld.frame_id)
                        if elem is None or getattr(elem, 'inactive', False):
                            continue
                        ni = self.model.nodes.get(elem.node_i)
                        nj = self.model.nodes.get(elem.node_j)
                        if ni is None or nj is None:
                            continue
                        L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
                        if L < 1e-12:
                            continue
                        # Total load = average intensity × loaded length
                        load_len = ld.dist_b - ld.dist_a
                        avg = (ld.val_a + ld.val_b) * 0.5
                        total_force = avg * load_len * mult
                        mass = total_force / g
                        node_mass[elem.node_i] = node_mass.get(elem.node_i, 0.0) + mass * 0.5
                        node_mass[elem.node_j] = node_mass.get(elem.node_j, 0.0) + mass * 0.5

                    # ── Joint loads → mass ──
                    for jl in self.model.joint_loads or []:
                        if jl.pattern != lp_name:
                            continue
                        # Use vertical component (F3) as the load magnitude
                        total_force = abs(jl.fz) * mult
                        mass = total_force / g
                        node_mass[jl.node_id] = node_mass.get(jl.node_id, 0.0) + mass

                    # ── Area gravity loads → mass ──
                    for agl in self.model.area_gravity_loads or []:
                        if agl.pattern != lp_name:
                            continue
                        area_elem = self.model.area_elements.get(agl.area_id)
                        if area_elem is None:
                            continue
                        if getattr(area_elem, 'inactive', False):
                            # Parent was meshed — redistribute to sub-elements.
                            sub_ids = sorted(
                                aid for aid in self.model.area_elements
                                if aid.startswith(f"{agl.area_id}_sub_")
                            )
                            if not sub_ids:
                                continue
                            for sub_id in sub_ids:
                                sub_elem = self.model.area_elements[sub_id]
                                sec_name = self.model.area_assignments.get(sub_id)
                                if not sec_name:
                                    continue
                                sec = self.model.sections.get(sec_name)
                                if sec is None:
                                    continue
                                mat = all_materials.get(sec.material)
                                if mat is None or mat.unit_weight == 0:
                                    continue
                                thickness = sub_elem.thickness
                                if thickness < 1e-12:
                                    continue
                                area_mag, _ = self._polygon_area(sub_elem.node_ids)
                                if area_mag < 1e-12:
                                    continue
                                sw_per_area = thickness * mat.unit_weight
                                total_fz = sw_per_area * area_mag * abs(agl.multiplier_z) * mult
                                mass = total_fz / g
                                n_corners = len(sub_elem.node_ids)
                                for nid in sub_elem.node_ids:
                                    node_mass[nid] = node_mass.get(nid, 0.0) + mass / n_corners
                            continue
                        sec_name = self.model.area_assignments.get(agl.area_id)
                        if not sec_name:
                            continue
                        sec = self.model.sections.get(sec_name)
                        if sec is None:
                            continue
                        mat = all_materials.get(sec.material)
                        if mat is None or mat.unit_weight == 0:
                            continue
                        thickness = area_elem.thickness
                        if thickness < 1e-12:
                            continue
                        # Polygon area
                        area_mag, _ = self._polygon_area(area_elem.node_ids)
                        if area_mag < 1e-12:
                            continue
                        # Force = area × thickness × unit_weight × multiplier × mult
                        # abs(multiplier) on the load direction; sign from mult
                        # (MASS SOURCE multiplier) decides inclusion/exclusion.
                        sw_per_area = thickness * mat.unit_weight
                        total_fz = sw_per_area * area_mag * abs(agl.multiplier_z) * mult
                        mass = total_fz / g
                        n_corners = len(area_elem.node_ids)
                        for nid in area_elem.node_ids:
                            node_mass[nid] = node_mass.get(nid, 0.0) + mass / n_corners

                    # ── Area uniform loads → mass ──
                    for aul in self.model.area_uniform_loads or []:
                        if aul.pattern != lp_name:
                            continue
                        area_elem = self.model.area_elements.get(aul.area_id)
                        if area_elem is None:
                            continue
                        if getattr(area_elem, 'inactive', False):
                            # Parent was meshed — redistribute to sub-elements.
                            sub_ids = sorted(
                                aid for aid in self.model.area_elements
                                if aid.startswith(f"{aul.area_id}_sub_")
                            )
                            if not sub_ids:
                                continue
                            for sub_id in sub_ids:
                                sub_elem = self.model.area_elements[sub_id]
                                area_mag, _ = self._polygon_area(sub_elem.node_ids)
                                if area_mag < 1e-12:
                                    continue
                                pressure = abs(aul.value)
                                total_force = pressure * area_mag * mult
                                mass = total_force / g
                                n_corners = len(sub_elem.node_ids)
                                for nid in sub_elem.node_ids:
                                    node_mass[nid] = node_mass.get(nid, 0.0) + mass / n_corners
                            continue
                        # Polygon area
                        area_mag, _ = self._polygon_area(area_elem.node_ids)
                        if area_mag < 1e-12:
                            continue
                        # Use pressure magnitude in gravity direction
                        pressure = abs(aul.value)
                        total_force = pressure * area_mag * mult
                        mass = total_force / g
                        n_corners = len(area_elem.node_ids)
                        for nid in area_elem.node_ids:
                            node_mass[nid] = node_mass.get(nid, 0.0) + mass / n_corners

        # Assign masses to OpenSees nodes
        for nid, m in node_mass.items():
            tag = self._node_tag_from_id(nid)
            if tag is not None:
                if m > 0:
                    ops.mass(tag, m, m, m, 0, 0, 0)
                else:
                    ops.mass(tag, 1e-6, 1e-6, 1e-6, 0, 0, 0)

        self.node_masses = node_mass

        if self.config['verbose']:
            total = sum(node_mass.values())
            print(f"  Total seismic mass: {total:.2f} tonnes")
            print(f"  Total seismic weight: {total * g / 1000:.2f} MN")

        return node_mass

    # =========================================================================
    # Modal analysis
    # =========================================================================
    def run_modal_analysis(self, num_modes: int = 30,
                           print_results: bool = True) -> Dict[str, Any]:
        """Run eigenvalue / modal analysis and return results.

        Requires that seismic masses have been assigned (call
        :meth:`compute_seismic_masses` first) and the stiffness model has been
        built (call :meth:`build` first).

        Args:
            num_modes: Number of eigenvalues to solve for.
            print_results: If True, print a modal properties table.

        Returns:
            Dictionary with keys:

            * ``'eigenvalues'`` — list of eigenvalues (ω²).
            * ``'periods'`` — list of natural periods (s).
            * ``'frequencies'`` — list of natural frequencies (Hz).
            * ``'modal_props'`` — the full ``ops.modalProperties()`` dict.
            * ``'num_modes'`` — number of converged modes.
        """
        if self.config['verbose']:
            print(f"Running modal analysis for {num_modes} modes...")

        # Run eigenvalue analysis
        eigenvals_all = ops.eigen('-fullGenLapack', num_modes)
        eigenvals = [ev for ev in eigenvals_all if ev > 1e-12]
        n_modes = len(eigenvals)
        if n_modes < num_modes:
            if self.config['verbose']:
                print(f"  Warning: only {n_modes} positive eigenvalues out of "
                      f"{num_modes}.  Proceeding with {n_modes} modes.")

        periods = [2.0 * math.pi / math.sqrt(ev) for ev in eigenvals]
        frequencies = [math.sqrt(ev) / (2.0 * math.pi) for ev in eigenvals]

        # Get modal properties via built-in command
        try:
            modal_props = ops.modalProperties('-return', '-unorm')
        except Exception:
            modal_props = {}

        results = {
            'eigenvalues': eigenvals,
            'periods': periods,
            'frequencies': frequencies,
            'modal_props': modal_props,
            'num_modes': n_modes,
        }

        if print_results:
            print("\n===== MODAL ANALYSIS =====")
            # Try to get participation info from modalProperties
            if modal_props:
                try:
                    total_mass = modal_props.get('totalFreeMass', [0])[0]
                    print(f"Total translational mass (free DOFs): {total_mass:.2f} tonnes")
                    print()
                    header = (f"{'Mode':>5} {'Freq(Hz)':>10} {'Period(s)':>10} "
                              f"{'Mx(t)':>12} {'My(t)':>12} {'Mz(t)':>12} "
                              f"{'%X':>7} {'%Y':>7} {'%Z':>7}")
                    print(header)
                    print("-" * len(header))
                    for i in range(n_modes):
                        mx = modal_props.get('partiMassMX', [0]*n_modes)[i]
                        my = modal_props.get('partiMassMY', [0]*n_modes)[i]
                        mz = modal_props.get('partiMassMZ', [0]*n_modes)[i]
                        rx = modal_props.get('partiMassRatiosMX', [0]*n_modes)[i]
                        ry = modal_props.get('partiMassRatiosMY', [0]*n_modes)[i]
                        rz = modal_props.get('partiMassRatiosMZ', [0]*n_modes)[i]
                        print(f"{i+1:5d} {frequencies[i]:10.4f} {periods[i]:10.4f} "
                              f"{mx:12.2f} {my:12.2f} {mz:12.2f} "
                              f"{rx:6.2f}% {ry:6.2f}% {rz:6.2f}%")
                except Exception:
                    # Fallback if modalProperties keys not available
                    pass
            else:
                # Simple fallback
                print(f"{'Mode':>5} {'Period(s)':>10} {'Freq(Hz)':>10}")
                print("-" * 30)
                for i in range(n_modes):
                    print(f"{i+1:5d} {periods[i]:10.4f} {frequencies[i]:10.4f}")

            if periods:
                print(f"\nFirst 5 periods (s):")
                for i, T in enumerate(periods[:5]):
                    print(f"  Mode {i+1}: T = {T:.4f} s  f = {frequencies[i]:.4f} Hz")

        return results

    # =========================================================================
    # Mode shape extraction
    # =========================================================================
    def extract_mode_shapes(
        self, num_modes: int
    ) -> Dict[int, Dict[int, Tuple[float, float, float]]]:
        """Extract mode shape displacements for each node and each mode.

        Must be called **after** :meth:`run_modal_analysis` (the model must
        be built with masses assigned).

        Args:
            num_modes: Number of modes to extract (should match the value
                       used in :meth:`run_modal_analysis`).

        Returns:
            ``{mode_index: {node_tag: (dx, dy, dz)}}`` where *mode_index* is
            0‑based and displacements are the **eigenvector** components
            (not normalised to unit mass — these are the raw values from
            ``ops.nodeEigenvector``).
        """
        node_tags = list(ops.getNodeTags())
        # dof index 0→1 (X), 1→2 (Y), 2→3 (Z)
        dof_map = {0: 1, 1: 2, 2: 3}

        shapes: Dict[int, Dict[int, Tuple]] = {}
        for m in range(num_modes):
            mode_num = m + 1  # OpenSees is 1‑based
            per_node: Dict[int, Tuple] = {}
            for tag in node_tags:
                dx = ops.nodeEigenvector(tag, mode_num, dof_map[0])
                dy = ops.nodeEigenvector(tag, mode_num, dof_map[1])
                dz = ops.nodeEigenvector(tag, mode_num, dof_map[2])
                per_node[tag] = (dx, dy, dz)
            shapes[m] = per_node

        return shapes

    # =========================================================================
    # Response spectrum analysis
    # =========================================================================
    def run_response_spectrum_analysis(
        self,
        num_modes: int,
        modal_periods: List[float],
        spectrum_periods: List[float],
        spectrum_accels: List[float],
        direction: str = 'X',
        damping_ratio: float = 0.05,
        print_results: bool = True,
    ) -> Dict[str, Any]:
        """Run a response‑spectrum analysis using CQC modal combination.

        This performs a mode‑by‑mode response spectrum analysis using
        OpenSees' ``responseSpectrumAnalysis`` command, then combines
        results with the Complete Quadratic Combination (CQC) rule.

        Args:
            num_modes: Number of modes to include.
            modal_periods: Natural periods of each mode (s) — from
                           :meth:`run_modal_analysis`.
            spectrum_periods: Period axis of the response spectrum (s).
            spectrum_accels: Spectral acceleration values (in **m/s²**)
                             corresponding to ``spectrum_periods``.
            direction: Excitation direction — ``'X'``, ``'Y'``, or ``'Z'``.
            damping_ratio: Damping ratio for CQC correlation (default 0.05).
            print_results: If True, print a summary table.

        Returns:
            Dictionary with keys:

            * ``'modal_base_shear'`` — list of base shear per mode (kN).
            * ``'base_shear_cqc'`` — CQC‑combined base shear (kN).
            * ``'base_shear_srss'`` — SRSS‑combined base shear (kN).
            * ``'modal_periods'`` — the input modal periods.
        """
        if self.config['verbose']:
            print(f"Running response spectrum analysis (dir={direction})...")

        omega = [2.0 * math.pi / T if T > 0 else 0.0 for T in modal_periods]
        damp_ratios = [damping_ratio] * num_modes

        # Define the spectrum as a time series (use a high tag to avoid
        # conflicts with load‑pattern time series)
        SPECTRUM_TS_TAG = 9999
        ops.timeSeries('Path', SPECTRUM_TS_TAG,
                       '-time', *spectrum_periods,
                       '-values', *spectrum_accels)

        # Mode-by-mode analysis — extract base shear from element forces
        modal_base_shear = []
        modal_base_moment = []
        dof = {'X': 1, 'Y': 2, 'Z': 3}[direction]

        # Find base elements — those attached to nodes that are restrained
        # in the analysis direction (pinned supports have UX/UY/UZ fixed,
        # but rotations free, so checking all‑6 is too restrictive).
        dof_idx = {'X': 0, 'Y': 1, 'Z': 2}[direction]
        base_nodes = {
            nid for nid, r in self.model.restraints.items()
            if len(r.dofs) > dof_idx and r.dofs[dof_idx] == 1
        }

        # Determine which elements to use (split or original)
        if self.split_elements:
            elements = self.split_elements
        else:
            elements = self.model.frame_elements

        base_elements = []
        for eid, elem in elements.items():
            if getattr(elem, 'inactive', False):
                continue
            if elem.node_i in base_nodes and elem.node_j not in base_nodes:
                base_elements.append((elem.elem_tag, 'i', elem.node_j))
            elif elem.node_j in base_nodes and elem.node_i not in base_nodes:
                base_elements.append((elem.elem_tag, 'j', elem.node_i))

        for mode in range(1, num_modes + 1):
            ops.responseSpectrumAnalysis(SPECTRUM_TS_TAG, dof, '-mode', mode)

            # Sum global forces at base element ends
            v_base = 0.0
            m_base = 0.0
            dof_map = {'X': (0, 4), 'Y': (1, 5), 'Z': (2, 3)}
            f_idx, m_idx = dof_map[direction]

            for eid, end, _ in base_elements:
                try:
                    # 'forces' returns global element forces
                    # [Fx,Fy,Fz,Mx,My,Mz] at I-end then J-end
                    forces = ops.eleResponse(eid, 'forces')
                except Exception:
                    continue
                if end == 'i':
                    v_base += forces[f_idx]
                    m_base += forces[m_idx]
                else:
                    v_base += forces[f_idx + 6]
                    m_base += forces[m_idx + 6]

            modal_base_shear.append(v_base)
            modal_base_moment.append(m_base)

        # CQC combination
        base_shear_cqc = self._cqc_combine(modal_base_shear, omega, damp_ratios)
        base_shear_srss = math.sqrt(sum(v * v for v in modal_base_shear))
        base_moment_cqc = self._cqc_combine(modal_base_moment, omega, damp_ratios)
        base_moment_srss = math.sqrt(sum(m * m for m in modal_base_moment))

        result = {
            'modal_base_shear': modal_base_shear,
            'modal_base_moment': modal_base_moment,
            'base_shear_cqc': base_shear_cqc,
            'base_shear_srss': base_shear_srss,
            'base_moment_cqc': base_moment_cqc,
            'base_moment_srss': base_moment_srss,
            'modal_periods': modal_periods,
        }

        if print_results:
            print(f"\n===== RESPONSE SPECTRUM ({direction}) =====")
            print(f"{'Mode':>5} {'Period(s)':>10} {'Shear (kN)':>14} {'Moment (kN·m)':>16}")
            print("-" * 48)
            for i, (T, v, m) in enumerate(zip(modal_periods[:num_modes],
                                                modal_base_shear,
                                                modal_base_moment)):
                print(f"{i+1:5d} {T:10.4f} {v:14.2f} {m:16.2f}")
            print("-" * 48)
            print(f"{'CQC':>5} {'':>10} {base_shear_cqc:14.2f} {base_moment_cqc:16.2f}")
            print(f"{'SRSS':>5} {'':>10} {base_shear_srss:14.2f} {base_moment_srss:16.2f}")
            print()

        return result

    # -------------------------------------------------------------------------
    # CQC combination helper
    # -------------------------------------------------------------------------
    @staticmethod
    def _cqc_combine(modal_values: List[float],
                     omega: List[float],
                     damp_ratios: List[float]) -> float:
        """Complete Quadratic Combination of modal results."""
        n = len(modal_values)
        if n == 0:
            return 0.0
        if n == 1:
            return abs(modal_values[0])
        total = 0.0
        for i in range(n):
            for j in range(n):
                di = damp_ratios[i]
                dj = damp_ratios[j]
                bij = omega[i] / omega[j] if omega[j] > 0 else 1.0
                rho = (
                    8.0 * math.sqrt(di * dj) * (di + bij * dj) * (bij ** 1.5)
                ) / (
                    (1.0 - bij ** 2.0) ** 2.0
                    + 4.0 * di * dj * bij * (1.0 + bij ** 2.0)
                    + 4.0 * (di ** 2.0 + dj ** 2.0) * bij ** 2.0
                )
                total += modal_values[i] * modal_values[j] * rho
        return math.sqrt(total)

    # =========================================================================
    # Element-level RS forces (per element, CQC-combined)
    # =========================================================================
    def extract_element_rs_forces(
        self,
        num_modes: int,
        modal_periods: List[float],
        spectrum_periods: List[float],
        spectrum_accels: List[float],
        direction: str = 'X',
        damping_ratio: float = 0.05,
        print_results: bool = True,
    ) -> Dict[str, Any]:
        """Run RS analysis and return CQC‑combined element forces sorted by height.

        For each element this returns the CQC‑combined moments (My_i, My_j,
        Mz_i, Mz_j) and the corresponding shears derived from the moment
        gradient (Vy = dMz/dx, Vz = dMy/dx).

        Args:
            Same as :meth:`run_response_spectrum_analysis`.

        Returns:
            Dictionary with keys:

            * ``'element_results'`` — list of dicts sorted by elevation, each
              containing ``elem_id``, ``z_bot``, ``z_mid``, ``Vy_i``, ``Vy_j``,
              ``Vz_i``, ``Vz_j``, ``My_i``, ``My_j``, ``Mz_i``, ``Mz_j``.
            * ``'modal_periods'``, ``'omega'`` — for diagnostics.
        """
        if self.config['verbose']:
            print("Extracting element RS forces...")

        omega = [2.0 * math.pi / T if T > 0 else 0.0 for T in modal_periods]
        damp_ratios = [damping_ratio] * num_modes

        dof = {'X': 1, 'Y': 2, 'Z': 3}[direction]

        # The spectrum time series must already exist (created by a prior
        # call to :meth:`run_response_spectrum_analysis`).
        SPECTRUM_TS_TAG = 9999

        # Determine elements (skip inactive)
        if self.split_elements:
            elements_dict = self.split_elements
        else:
            elements_dict = self.model.frame_elements

        # Pre-compute element info + storage
        elem_data = {}  # eid -> {z_bot, z_mid, My_i[], My_j[], Mz_i[], Mz_j[]}
        for eid, elem in elements_dict.items():
            if getattr(elem, 'inactive', False):
                continue
            ni = self.model.nodes.get(elem.node_i)
            nj = self.model.nodes.get(elem.node_j)
            if ni is None or nj is None:
                continue
            z_i, z_j = ni.z, nj.z
            if z_i > z_j:
                z_i, z_j = z_j, z_i
            elem_data[eid] = {
                'tag': elem.elem_tag,
                'z_bot': z_i,
                'z_mid': (z_i + z_j) * 0.5,
                'My_i': [], 'My_j': [], 'Mz_i': [], 'Mz_j': [],
            }

        # Mode-by-mode extraction
        for mode in range(1, num_modes + 1):
            ops.responseSpectrumAnalysis(SPECTRUM_TS_TAG, dof, '-mode', mode)
            for eid, ed in elem_data.items():
                try:
                    forces = ops.eleResponse(ed['tag'], 'forces')
                except Exception:
                    forces = [0.0] * 12
                ed['My_i'].append(forces[4])
                ed['My_j'].append(forces[10])
                ed['Mz_i'].append(forces[5])
                ed['Mz_j'].append(forces[11])

        # CQC combine per element and compute shears
        element_results = []
        for eid, ed in elem_data.items():
            ne = len(ed['My_i'])
            # Only combine as many modes as we have data for
            n_use = min(ne, num_modes)
            o_use = omega[:n_use]
            d_use = damp_ratios[:n_use]

            My_i = self._cqc_combine(ed['My_i'][:n_use], o_use, d_use)
            My_j = self._cqc_combine(ed['My_j'][:n_use], o_use, d_use)
            Mz_i = self._cqc_combine(ed['Mz_i'][:n_use], o_use, d_use)
            Mz_j = self._cqc_combine(ed['Mz_j'][:n_use], o_use, d_use)

            # Element length
            elem = elements_dict.get(eid)
            if elem:
                ni = self.model.nodes.get(elem.node_i)
                nj = self.model.nodes.get(elem.node_j)
                if ni and nj:
                    L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
                else:
                    L = 1.0
            else:
                L = 1.0

            # Shear from moment gradient
            Vy_i = (Mz_i - Mz_j) / L if L > 1e-12 else 0.0
            Vy_j = Vy_i
            Vz_i = (My_i - My_j) / L if L > 1e-12 else 0.0
            Vz_j = Vz_i

            element_results.append({
                'elem_id': eid,
                'z_bot': ed['z_bot'],
                'z_mid': ed['z_mid'],
                'Vy_i': Vy_i, 'Vy_j': Vy_j,
                'Vz_i': Vz_i, 'Vz_j': Vz_j,
                'My_i': My_i, 'My_j': My_j,
                'Mz_i': Mz_i, 'Mz_j': Mz_j,
            })

        # Sort by height
        element_results.sort(key=lambda r: r['z_mid'])

        if print_results:
            print(f"\n===== RESPONSE SPECTRUM RESULTS ({direction} only, CQC) FOR ALL ELEMENTS =====")
            header = (f"{'Elem':>30} {'Z_bot(m)':>10} {'Z_mid(m)':>10} {'End':>5} "
                      f"{'Vy (kN)':>12} {'Vz (kN)':>12} {'My (kN·m)':>12} {'Mz (kN·m)':>12}")
            print(header)
            print("-" * len(header))
            for r in element_results:
                eid_str = f"{r['elem_id']:30s}"
                print(f"{eid_str} {r['z_bot']:10.2f} {r['z_mid']:10.2f} {'I':>5} "
                      f"{r['Vy_i']:12.2f} {r['Vz_i']:12.2f} {r['My_i']:12.2f} {r['Mz_i']:12.2f}")
                print(f"{eid_str} {r['z_bot']:10.2f} {r['z_mid']:10.2f} {'J':>5} "
                      f"{r['Vy_j']:12.2f} {r['Vz_j']:12.2f} {r['My_j']:12.2f} {r['Mz_j']:12.2f}")

        return {
            'element_results': element_results,
            'modal_periods': modal_periods,
            'omega': omega,
        }

    # =========================================================================
    # RS nodal displacements (from mode‑shape combination)
    # =========================================================================
    def compute_rs_nodal_displacements(
        self,
        num_modes: int,
        modal_periods: List[float],
        eigenvalues: List[float],
        spectrum_func,
        direction: str = 'X',
        damping_ratio: float = 0.05,
    ) -> Dict[int, Tuple[float, float, float]]:
        """Compute CQC‑combined peak nodal displacements from RS analysis.

        Uses mode‑shape superposition rather than re‑running the RS analysis:

            u_m = Γ_m · φ_m · Sa_m / ω²_m

        then CQC across modes.

        Args:
            num_modes: Number of modes.
            modal_periods: Natural periods of each mode (s).
            eigenvalues: Eigenvalues (ω²) from :meth:`run_modal_analysis`.
            spectrum_func: Callable ``f(T) → Sa`` in **m/s²**.
            direction: Excitation direction ``'X'``, ``'Y'``, or ``'Z'``.
            damping_ratio: Damping ratio for CQC correlation.

        Returns:
            Dict mapping ``node_tag`` → ``(dx, dy, dz)`` in model length units.
        """
        dof = {'X': 1, 'Y': 2, 'Z': 3}[direction]
        dof_idx = dof - 1  # 0‑based for the result tuple

        # Get participation factors from modalProperties.
        # nodeEigenvector returns mass-normalised eigenvectors (φᵀMφ = 1),
        # so the correct participation factor is Γ = √M_eff.
        # partiFactorMX from '-unorm' uses unit-max normalisation and is
        # incompatible with mass-normalised eigenvectors.
        try:
            mp = ops.modalProperties('-return', '-unorm')
        except Exception:
            mp = {}
        mass_key = ('partiMassMX' if direction == 'X'
                    else 'partiMassMY' if direction == 'Y'
                    else 'partiMassMZ')
        eff_masses = mp.get(mass_key, [0.0] * num_modes)

        omega = [2.0 * math.pi / T if T > 0 else 0.0 for T in modal_periods]
        damp = [damping_ratio] * num_modes

        # Collect node tags
        node_tags = list(ops.getNodeTags())

        # Store per-mode displacement contributions: per_mode[tag][d] = [val_mode0, ...]
        per_mode = {tag: {d: [] for d in range(3)} for tag in node_tags}

        for m in range(num_modes):
            if eigenvalues[m] <= 1e-12 or omega[m] <= 1e-12:
                for tag in node_tags:
                    for d in range(3):
                        per_mode[tag][d].append(0.0)
                continue

            T = modal_periods[m]
            Sa = spectrum_func(T)           # m/s²
            Gamma = math.sqrt(abs(eff_masses[m])) if eff_masses[m] != 0 else 0.0
            factor = Gamma * Sa / (omega[m] ** 2)

            if abs(factor) < 1e-15:
                for tag in node_tags:
                    for d in range(3):
                        per_mode[tag][d].append(0.0)
                continue

            for tag in node_tags:
                phi = ops.nodeEigenvector(tag, m + 1, dof)
                per_mode[tag][dof_idx].append(phi * factor)
                # Off‑direction DOFs get zero (unidirectional excitation)
                for d in range(3):
                    if d != dof_idx:
                        per_mode[tag][d].append(0.0)

        # CQC combine per node
        cqc_result = {}
        for tag in node_tags:
            vals = tuple(
                self._cqc_combine(per_mode[tag][d], omega, damp)
                for d in range(3)
            )
            cqc_result[tag] = vals

        return cqc_result
    def add_missing_mass_correction(
        self,
        rs_results: Dict[str, Any],
        modal_results: Dict[str, Any],
        spectrum_func,
        g: float = 9.81,
        T_short: float = 0.01,
    ) -> Dict[str, Any]:
        """Compute missing mass (rigid) contribution to base shear and moment.

        The rigid response captures the portion of the total mass that is not
        activated by the computed modes (residual mass = total − ΣM_eff).

        Args:
            rs_results: Output from :meth:`run_response_spectrum_analysis`.
            modal_results: Output from :meth:`run_modal_analysis`.
            spectrum_func: Callable ``f(T) → Sa`` in **m/s²** (not g) for
                           a given period ``T`` (s).
            g: Gravitational acceleration (m/s²).
            T_short: Period at which to evaluate the rigid response (s).

        Returns:
            Dictionary with keys:

            * ``'V_missing_X'`` — missing base shear in X (kN).
            * ``'V_missing_Y'`` — missing base shear in Y (kN).
            * ``'M_missing_YY'`` — missing base moment about Y (kN·m).
            * ``'M_missing_XX'`` — missing base moment about X (kN·m).
            * ``'residual_mass_X'`` — residual mass in X (t).
            * ``'residual_mass_Y'`` — residual mass in Y (t).
            * ``'h_cm'`` — centre of mass height (m).
            * ``'Sa_short'`` — spectral acceleration at T_short (m/s²).
        """
        mp = modal_results.get('modal_props', {})
        total_free_mass = mp.get('totalFreeMass', [0.0])[0]

        # Sum effective modal masses from modal_props
        num_modes = modal_results['num_modes']
        sum_meff_X = sum(mp.get('partiMassMX', [0.0] * num_modes)[:num_modes])
        sum_meff_Y = sum(mp.get('partiMassMY', [0.0] * num_modes)[:num_modes])

        residual_mass_X = max(0.0, total_free_mass - sum_meff_X)
        residual_mass_Y = max(0.0, total_free_mass - sum_meff_Y)

        # Spectral acceleration at short period
        Sa_short = spectrum_func(T_short)

        # Missing base shear
        V_missing_X = residual_mass_X * Sa_short
        V_missing_Y = residual_mass_Y * Sa_short

        # Centre of mass height
        total_mass = 0.0
        total_mass_z = 0.0
        try:
            for node_tag in ops.getNodeTags():
                mass_x = ops.nodeMass(node_tag)[0]
                if mass_x > 0:
                    z = ops.nodeCoord(node_tag)[2]
                    total_mass += mass_x
                    total_mass_z += mass_x * z
        except Exception:
            pass
        h_cm = total_mass_z / total_mass if total_mass > 0 else 0.0

        # Missing base moment
        M_missing_YY = residual_mass_X * Sa_short * h_cm
        M_missing_XX = residual_mass_Y * Sa_short * h_cm

        return {
            'V_missing_X': V_missing_X,
            'V_missing_Y': V_missing_Y,
            'M_missing_YY': M_missing_YY,
            'M_missing_XX': M_missing_XX,
            'residual_mass_X': residual_mass_X,
            'residual_mass_Y': residual_mass_Y,
            'h_cm': h_cm,
            'T_short': T_short,
            'Sa_short': Sa_short,
        }

    # =========================================================================
    # Capacity Spectrum Method (ADRS) — performance point
    # =========================================================================

    def pushover_to_adrs(
        self,
        pushover_results: Dict[str, Any],
        modal_results: Dict[str, Any],
        mode_shapes: Dict[int, Dict[int, Tuple[float, float, float]]],
        direction: str = 'X',
        g: float = 9.81,
    ) -> Dict[str, Any]:
        """Convert a pushover capacity curve to ADRS (Acceleration-Displacement
        Response Spectrum) coordinates.

        The conversion uses the fundamental mode:

        .. math::

            S_d = \\frac{\\Delta_{control}}{\\Gamma_1 \\phi_{1,control}}

            S_a = \\frac{V_{base}}{M_1^*}

        where :math:`\\Gamma_1` is the modal participation factor,
        :math:`\\phi_{1,control}` is the mode shape value at the control
        node, and :math:`M_1^*` is the effective modal mass.

        Args:
            pushover_results: Output from :meth:`run_pushover_analysis`.
            modal_results: Output from :meth:`run_modal_analysis` (must
                contain ``'modal_props'``).
            mode_shapes: Output from :meth:`extract_mode_shapes`.
            direction: Push direction (``'X'``, ``'Y'``, or ``'Z'``).
            g: Gravitational acceleration (m/s²).

        Returns:
            Dict with keys:

            * ``'S_a'`` — list of spectral accelerations (m/s²).
            * ``'S_d'`` — list of spectral displacements (m).
            * ``'Gamma'`` — modal participation factor.
            * ``'M_eff'`` — effective modal mass (kg).
            * ``'phi_control'`` — mode shape at control node.
            * ``'S_dy'``, ``'S_ay'`` — bilinear yield point (m, m/s²)
              or ``None`` if not computed.
        """
        direction_map = {'X': 0, 'Y': 1, 'Z': 2}
        dof_idx = direction_map.get(direction.upper(), 0)

        control_node_tag = pushover_results.get('control_node')
        if control_node_tag is None:
            raise ValueError("pushover_results must contain 'control_node'")

        # Modal participation factor from the dominant mode in the push direction
        modal_props = modal_results.get('modal_props', {})
        gamma_key = (f'partiFactorMX' if direction.upper() == 'X'
                     else f'partiFactorMY' if direction.upper() == 'Y'
                     else f'partiFactorMZ')
        mass_key = (f'partiMassMX' if direction.upper() == 'X'
                    else f'partiMassMY' if direction.upper() == 'Y'
                    else f'partiMassMZ')
        ratio_key = (f'partiMassRatiosMX' if direction.upper() == 'X'
                     else f'partiMassRatiosMY' if direction.upper() == 'Y'
                     else f'partiMassRatiosMZ')

        mass_list = modal_props.get(mass_key, [0.0])
        ratio_list = modal_props.get(ratio_key, [0.0])
        gamma_list = modal_props.get(gamma_key, [])  # partiFactor* — from -unorm;
                                                       # not directly usable with
                                                       # mass-normalised eigenvectors

        # Find the mode with the highest mass participation in push direction
        best_mode = 0
        best_ratio = 0.0
        for i, r in enumerate(ratio_list):
            if abs(r) > best_ratio:
                best_ratio = abs(r)
                best_mode = i

        M_eff = mass_list[best_mode] if mass_list else 1.0
        if abs(M_eff) < 1e-12:
            total_mass_key = 'totalFreeMass'
            free_mass = modal_props.get(total_mass_key, [0])
            M_eff = free_mass[0] if free_mass else 1.0

        # Participation factor for mass-normalised eigenvectors.
        # nodeEigenvector returns mass-normalised eigenvectors (φᵀMφ = 1),
        # so the participation factor Γ = √M_eff.
        # partiFactorMX from modalProperties('-return', '-unorm') uses
        # unit-max normalisation and cannot be used directly here.
        Gamma = math.sqrt(abs(M_eff))

        # Mode shape value at the control node (best mode)
        phi_control = 1.0
        if mode_shapes and best_mode in mode_shapes:
            node_shape = mode_shapes[best_mode].get(control_node_tag)
            if node_shape is not None:
                phi_control = node_shape[dof_idx]
        if abs(phi_control) < 1e-12:
            phi_control = 1.0

        # Convert
        control_disp = pushover_results.get('control_disp', [0.0])
        base_shear = pushover_results.get('base_shear', [0.0])

        S_d = [abs(d) / (abs(Gamma) * abs(phi_control)) for d in control_disp]
        S_a = [abs(v) / abs(M_eff) for v in base_shear]

        return {
            'S_a': S_a,
            'S_d': S_d,
            'Gamma': Gamma,
            'M_eff': M_eff,
            'phi_control': phi_control,
            'best_mode': best_mode,
            'S_dy': None,
            'S_ay': None,
        }

    def compute_performance_point(
        self,
        pushover_results: Dict[str, Any],
        modal_results: Dict[str, Any],
        mode_shapes: Dict[int, Dict[int, Tuple[float, float, float]]],
        spectrum_periods: List[float],
        spectrum_accels: List[float],
        direction: str = 'X',
        g: float = 9.81,
        damping_ratio: float = 0.05,
        max_iter: int = 50,
        tol: float = 0.01,
    ) -> Dict[str, Any]:
        """Find the performance point using the Capacity Spectrum Method (CSM).

        The capacity spectrum is bilinearised and intersected with the
        demand response spectrum (in ADRS format).  Equivalent viscous
        damping from hysteresis is used to reduce the elastic demand
        (per ATC-40 / GB 50011 CSM procedure).

        Args:
            pushover_results: Output from :meth:`run_pushover_analysis`.
            modal_results: Output from :meth:`run_modal_analysis`.
            mode_shapes: Output from :meth:`extract_mode_shapes`.
            spectrum_periods: Periods (s) defining the elastic demand
                spectrum.
            spectrum_accels: Spectral accelerations (m/s²) corresponding
                to *spectrum_periods*.
            direction: Push direction.
            g: Gravitational acceleration.
            damping_ratio: Elastic damping ratio (default 0.05).
            max_iter: Maximum iterations for secant convergence.
            tol: Convergence tolerance on S_d (relative).

        Returns:
            Dict with keys:

            * ``'S_dp'`` — performance point spectral displacement (m).
            * ``'S_ap'`` — performance point spectral acceleration (m/s²).
            * ``'V_base'`` — corresponding base shear (N).
            * ``'D_roof'`` — corresponding roof displacement (m).
            * ``'T_eq'`` — equivalent period at performance point (s).
            * ``'mu'`` — ductility demand.
            * ``'converged'`` — whether the iteration converged.
            * ``'S_dy'``, ``'S_ay'`` — bilinear yield point.
            * ``'capacity_adrs'`` — the full ADRS curve (dict with ``'S_a'``,
              ``'S_d'``).
        """
        # 1. Convert pushover to ADRS
        adrs = self.pushover_to_adrs(
            pushover_results, modal_results, mode_shapes,
            direction=direction, g=g,
        )
        S_a_arr = np.array(adrs['S_a'])
        S_d_arr = np.array(adrs['S_d'])

        # Filter out negative / zero values
        mask = (S_d_arr > 1e-12) & (S_a_arr > 1e-12)
        S_d_arr = S_d_arr[mask]
        S_a_arr = S_a_arr[mask]
        if len(S_d_arr) < 3:
            raise ValueError(
                "Too few valid data points in capacity spectrum"
            )

        Gamma = adrs['Gamma']
        M_eff = adrs['M_eff']
        phi_control = adrs['phi_control']
        best_mode = adrs.get('best_mode', 0)
        control_disp = np.array(pushover_results.get('control_disp', [0]))[mask]
        base_shear = np.array(pushover_results.get('base_shear', [0]))[mask]
        total_mass = M_eff  # effective modal mass for first mode

        # 2. Bilinearise the capacity spectrum (find yield point)
        # Use the equal-energy method: find (S_dy, S_ay) such that
        # area under bilinear curve = area under actual curve up to peak
        peak_idx = np.argmax(S_a_arr)
        S_d_peak = S_d_arr[peak_idx]
        S_a_peak = S_a_arr[peak_idx]

        # Initial elastic stiffness from first 20% of points
        n_el = max(3, len(S_d_arr) // 5)
        K_init = np.polyfit(S_d_arr[:n_el], S_a_arr[:n_el], 1)[0]

        # Bilinear fit using equal energy
        # S_ay = K_init * S_dy  (elastic)
        # Area under bilinear = 0.5 * S_ay * S_dy + S_ay * (S_d_peak - S_dy)
        #                        + 0.5 * (S_a_peak - S_ay) * (S_d_peak - S_dy)
        # Area under actual = trapezoidal integral
        # Integrate only up to the peak (not including any descending branch)
        area_actual = np.trapezoid(S_a_arr[:peak_idx + 1], S_d_arr[:peak_idx + 1])

        # Solve for S_dy using the equal-energy principle
        # This is a quadratic: 0.5*K_init*S_dy² - S_a_peak*S_d_peak + ...
        # Actually, use a simpler iterative search
        S_dy = S_d_peak * 0.3  # initial guess
        for _ in range(100):
            S_ay = K_init * S_dy
            # Area under bilinear up to peak
            A1 = 0.5 * S_ay * S_dy
            A2 = S_ay * (S_d_peak - S_dy)
            A3 = 0.5 * (S_a_peak - S_ay) * (S_d_peak - S_dy)
            area_bilin = A1 + A2 + A3
            err = (area_bilin - area_actual) / area_actual
            if abs(err) < 0.001:
                break
            # Adjust S_dy
            S_dy *= (1.0 - err * 0.5)

        S_ay = max(K_init * S_dy, S_a_arr[1] if len(S_a_arr) > 1 else S_a_arr[0])

        # 3. Capacity spectrum demand method (secant iteration)
        T_spec = np.array(spectrum_periods)
        Sa_spec = np.array(spectrum_accels)

        # First-mode elastic period from modal analysis
        modal_periods = modal_results.get('periods', [])
        best_mode_period = modal_periods[best_mode] if best_mode < len(modal_periods) else 1.0

        S_d_trial = S_d_peak * 0.2  # start at 20% of peak
        converged = False
        prev_S_d = S_d_trial
        stall_count = 0
        history = []

        for iteration in range(max_iter):
            # Spectral acceleration at trial point (interpolate capacity)
            if S_d_trial <= S_d_arr[0]:
                S_a_trial = S_a_arr[0]
            elif S_d_trial >= S_d_arr[-1]:
                S_a_trial = S_a_arr[-1]
            else:
                S_a_trial = float(np.interp(S_d_trial, S_d_arr, S_a_arr))

            # Equivalent period at trial point
            T_eq = 2.0 * math.pi * math.sqrt(S_d_trial / max(S_a_trial, 1e-12))

            # Ductility
            mu = max(S_d_trial / max(S_dy, 1e-12), 1.0)

            # Equivalent viscous damping from hysteresis (ATC-40 Eqn 5-19)
            if mu > 1.0:
                beta_eq = damping_ratio + 0.637 * (mu - 1.0) / (mu * math.pi)
            else:
                beta_eq = damping_ratio

            # Damping reduction factor (ATC-40 / GB 50011 compatible)
            B = 1.0
            if beta_eq > damping_ratio:
                B = math.sqrt((1.0 + 10.0 * (beta_eq - damping_ratio)) /
                              (1.0 + 5.0 * (beta_eq - damping_ratio)))
            B = max(0.5, min(2.0, B))

            # Demand spectral acceleration at T_eq
            Sa_demand = float(np.interp(T_eq, T_spec, Sa_spec)) / B

            # Demand spectral displacement
            S_d_demand = Sa_demand * (T_eq / (2.0 * math.pi)) ** 2

            history.append((S_d_trial, S_d_demand))

            # Convergence checks
            delta = abs(S_d_demand - S_d_trial)
            if delta / max(S_d_trial, 1e-12) < tol:
                converged = True
                S_dp = S_d_demand
                break

            # Also converge if S_d_trial stops changing (stalled)
            change = abs(S_d_trial - prev_S_d) / max(S_d_trial, 1e-12)
            if change < tol * 0.1 and iteration > 3:
                stall_count += 1
                if stall_count >= 3:
                    converged = True
                    S_dp = S_d_trial
                    break
            else:
                stall_count = 0

            prev_S_d = S_d_trial

            # Update trial: move towards demand
            S_d_trial = S_d_trial * 0.5 + S_d_demand * 0.5

            # Clamp: if S_d_trial drops below first data point and
            # S_d_demand also below it, we are in the elastic range.
            # Use the elastic spectral response as the performance point.
            if S_d_trial < S_d_arr[0] and S_d_demand < S_d_arr[0]:
                # Compute elastic spectral displacement from modal period
                Sa_el = float(np.interp(best_mode_period, T_spec, Sa_spec))
                S_d_el = Sa_el * (best_mode_period / (2.0 * math.pi)) ** 2
                S_dp = S_d_el
                converged = True
                break

        if not converged:
            # Use last trial point
            S_dp = S_d_trial

        # Compute final values at performance point
        if S_dp <= S_d_arr[0]:
            # Elastic range: use demand spectrum to compute consistent values
            S_ap = float(np.interp(best_mode_period, T_spec, Sa_spec))
            # Account for damping reduction at the performance point
            mu_p = max(S_dp / max(S_dy, 1e-12), 1.0)
            if mu_p > 1.0:
                beta_p = damping_ratio + 0.637 * (mu_p - 1.0) / (mu_p * math.pi)
                B_p = 1.0
                if beta_p > damping_ratio:
                    B_p = math.sqrt((1.0 + 10.0 * (beta_p - damping_ratio)) /
                                    (1.0 + 5.0 * (beta_p - damping_ratio)))
                B_p = max(0.5, min(2.0, B_p))
                S_ap /= B_p
            V_p = S_ap * abs(M_eff)
            D_p = S_dp * abs(Gamma) * abs(phi_control)
        elif S_dp >= S_d_arr[-1]:
            S_ap = float(np.interp(S_dp, S_d_arr, S_a_arr))
            V_p = float(np.interp(S_dp, S_d_arr, base_shear))
            D_p = float(np.interp(S_dp, S_d_arr, control_disp))
        else:
            S_ap = float(np.interp(S_dp, S_d_arr, S_a_arr))
            V_p = float(np.interp(S_dp, S_d_arr, base_shear))
            D_p = float(np.interp(S_dp, S_d_arr, control_disp))

        T_eq_final = 2.0 * math.pi * math.sqrt(S_dp / max(S_ap, 1e-12))
        mu_final = max(S_dp / max(S_dy, 1e-12), 1.0)

        return {
            'S_dp': S_dp,
            'S_ap': S_ap,
            'V_base': V_p,
            'D_roof': D_p,
            'T_eq': T_eq_final,
            'mu': mu_final,
            'converged': converged,
            'iterations': len(history),
            'S_dy': S_dy,
            'S_ay': S_ay,
            'Gamma': Gamma,
            'M_eff': M_eff,
            'capacity_adrs': {'S_a': S_a_arr.tolist(), 'S_d': S_d_arr.tolist()},
        }
