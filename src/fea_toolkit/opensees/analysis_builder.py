"""Analysis builder — create OpenSees domain from a prepared ``MeshModel``.

The :class:`AnalysisBuilder` takes a :class:`~fea_toolkit.model.mesh_model.MeshModel`
(fully prepared topology from the :class:`~fea_toolkit.opensees.preprocessor.Preprocessor`)
and creates the OpenSees domain objects.  It handles all analysis execution
and result extraction — no topology mutations occur here.
"""

from typing import Dict, Any, Optional, List, Tuple
import copy
import math
import numpy as np

import openseespy.opensees as ops

from ..model.mesh_model import MeshModel
from ..model.sap_data import (
    Node, FrameElement, AreaElement,
    ShellSection, Restraint,
)
from ..model.geometry import get_SAP_vecxz, global_to_local_distributed_load
from ..model.geometry import convert_area_loads_to_edge_loads
from ..model.selection import Selection


class AnalysisBuilder:
    """Create and analyse an OpenSees model from a prepared MeshModel.

    Usage::

        builder = AnalysisBuilder(mesh_model, config)
        builder.build_domain()
        builder.create_loads({"DEAD": 1.0})
        results = builder.run_static_analysis()

    Args:
        mesh_model: Prepared topology from the Preprocessor.
        config: Configuration dict (same keys as
            :class:`~fea_toolkit.opensees.builder.OpenSeesBuilder`).
    """

    def __init__(self,
                 mesh_model: MeshModel,
                 config: Optional[Dict[str, Any]] = None):
        self.mesh_model = mesh_model
        self.units = mesh_model.units
        self.config = config or {}
        self._set_defaults()

        # Domain state (built during build_domain)
        self.frame_tag_map: Dict[str, int] = {}
        self.section_tags: Dict[str, int] = dict(mesh_model.section_tags)
        self._shell_sec_tags: Dict[str, int] = dict(mesh_model.shell_sec_tags)
        self._shell_sec_variants: Dict[str, int] = dict(mesh_model.shell_sec_variants)
        self._frame_element_types: Dict[str, str] = dict(mesh_model.frame_element_types)
        self._area_element_types: Dict[str, str] = dict(mesh_model.area_element_types)
        self._offset_rigid_links: List[tuple] = list(mesh_model.offset_rigid_links)
        self._edge_constraint_method: Optional[str] = None
        self._saved_edge_constraints: List[tuple] = list(mesh_model.saved_edge_constraints)
        self.edge_loads_from_areas: list = list(mesh_model.edge_loads_from_areas)
        self._base_z = mesh_model.base_z

        # Per-build tracking
        self._created_node_tags: set = set()
        self._next_variant_tag: int = (max(self.section_tags.values(), default=0) + 1
                                       if self.section_tags else 1)
        self._rigid_link_elems: Dict[str, int] = {}

        # Brace state
        self._brace_selection: Optional[set] = None

        # Mass tracking
        self.node_masses: Dict[str, float] = {}
        self._mass_g: float = 9.81

        # Load totals
        self.load_totals: Dict[str, float] = {}
        self._sw_load_totals: Dict[str, float] = {}
        self._gravity_load_totals: Dict[str, float] = {}

        # Model log
        self._model_log: Optional[Any] = None
        self._model_diagnostics: Dict[str, Any] = {}

        # Transf tags
        self._transf_tags: Dict[int, int] = {}

    def _set_defaults(self) -> None:
        """Set default configuration values."""
        defaults = {
            'element_type': 'elasticBeamColumn',
            'num_int_pts': 3,
            'use_elastic_sections': True,
            'create_fiber_sections': False,
            'verbose': False,
            'geom_transf_type': 'Linear',
            'beam_integration': 'Lobatto',
            'simplify_distributed_loads': False,
            'solver_test_tol': 1e-6,
            'solver_test_max_iter': 10,
            'solver_algorithm': 'Newton',
            'gravity_num_substeps': 1,
            'solver_constraints': 'Transformation',
            'solver_system': 'BandGen',
            'constraint_method': 'spring',
        }
        for k, v in defaults.items():
            self.config.setdefault(k, v)

    # ═══════════════════════════════════════════════════════════════
    # Domain construction
    # ═══════════════════════════════════════════════════════════════

    def build_domain(self) -> None:
        """Create the full OpenSees domain from the MeshModel.

        Creates nodes, restraints, materials, sections, frame elements,
        shell elements, lumped hinges, and rigid links.
        """
        ops.wipe()
        self._edge_constraint_method = None
        ops.model('basic', '-ndm', 3, '-ndf', 6)

        self._create_nodes()
        self._apply_restraints()
        self._create_materials()
        self._create_sections()
        self._create_shell_elements()
        self._create_lumped_hinges()
        self._create_elements()

    def create_loads(self,
                     pattern_scales: Optional[Dict[str, float]] = None,
                     ) -> None:
        """Create load patterns on the OpenSees domain.

        Args:
            pattern_scales: Dict mapping pattern name → scale factor.
                If provided, only these patterns are created.  If None,
                all patterns from the mesh model are applied.
        """
        self._create_loads(pattern_scales=pattern_scales)
        self._apply_rigid_diaphragms()

    # ── Node creation ────────────────────────────────────────────

    def _create_nodes(self) -> None:
        """Create OpenSees nodes from MeshModel nodes."""
        self._created_node_tags = set()
        for node in self.mesh_model.nodes.values():
            tag = node.node_tag
            ops.node(tag, node.x, node.y, node.z)
            self._created_node_tags.add(tag)

    def _apply_restraints(self) -> None:
        """Apply boundary conditions from MeshModel restraints."""
        for node_id, restraint in self.mesh_model.restraints.items():
            nd = self.mesh_model.nodes.get(node_id)
            if nd is None:
                continue
            ops.fix(nd.node_tag, *restraint.dofs[:6])

    # ── Materials ────────────────────────────────────────────────

    def _create_materials(self) -> None:
        """Create OpenSees materials (fiber sections, brace trusses)."""
        if not self.config['create_fiber_sections'] and not self.config.get('brace_truss'):
            return

        from ..model.sap_data import (
            PipeSection, AngleSection, DoubleAngleSection,
            TeeSection, ChannelSection,
        )
        brace_types = (
            PipeSection, AngleSection, DoubleAngleSection,
            TeeSection, ChannelSection,
        )

        if self.config.get('brace_truss'):
            self._truss_mat_tags: Dict[str, int] = {}
            self._truss_areas: Dict[str, float] = {}
            n_sec = len(self.mesh_model.sections)
            mat_tag = n_sec + 1

            explicit = self.config.get('brace_sections')
            for sec_name, sec in self.mesh_model.sections.items():
                if explicit is not None:
                    if sec_name not in explicit:
                        continue
                elif not isinstance(sec, brace_types):
                    continue

                area = getattr(sec, 'A', 0.0) or 0.0
                if area < 1e-12:
                    continue

                mat = self.mesh_model.materials.get(sec.material)
                E_sec = mat.E_mod if mat else 200e9
                Fy = getattr(sec, 'Fy', None) or getattr(mat, 'Fy', 250e6) if mat else 250e6

                self._truss_mat_tags[sec_name] = mat_tag
                self._truss_areas[sec_name] = area
                ops.uniaxialMaterial('Hysteretic', mat_tag,
                                     1.1 * Fy, 0.007 * E_sec / Fy,  # tension envelope
                                     0.3 * Fy, 0.004 * E_sec / Fy,  # compression (buckling)
                                     1.1 * Fy, 0.007 * E_sec / Fy,  # reloading
                                     1.0, 0.0, 0.0, 0.0)
                mat_tag += 1

    # ── Sections ─────────────────────────────────────────────────

    def _create_sections(self) -> None:
        """Create OpenSees sections from MeshModel sections.

        Assigns section tags sequentially if they are not already
        populated in ``self.section_tags`` (from MeshModel).
        """
        if self.config['verbose']:
            print("Creating sections...")

        next_tag = max(self.section_tags.values(), default=0) + 1 if self.section_tags else 1
        for sec_name, sec in self.mesh_model.sections.items():
            if sec_name not in self.section_tags:
                self.section_tags[sec_name] = next_tag
                next_tag += 1
            tag = self.section_tags[sec_name]
            self._create_single_section(sec, tag)

    def _create_single_section(self, sec, tag: int) -> None:
        """Create a single OpenSees section."""
        mods = getattr(sec, 'modifiers', {}) or {}
        if self.config['create_fiber_sections']:
            # Fiber section path
            mat_name = sec.material
            mat_tag = tag  # material has the same tag as section
            if not self.config.get('brace_truss') or sec_name not in (
                    self._truss_mat_tags if hasattr(self, '_truss_mat_tags') else {}):
                E = self.mesh_model.materials.get(mat_name).E_mod if mat_name in self.mesh_model.materials else 200e9
                ops.uniaxialMaterial('Elastic', tag, E)

            from ..model.sap_data import Concrete01Material, Steel01Material
            cover = 0.04
            sec.to_fiber_patches(mat_tag, cover=cover, nfy=8, nfz=4)
        else:
            # Elastic section path
            mat = self.mesh_model.materials.get(sec.material)
            if mat is None:
                if self.config.get('verbose', False):
                    print(f"  ⚠ Section {sec.name}: material '{sec.material}' not found, using defaults")
                E_mod = 200e9
                G_mod = 80e9
                nu_val = 0.3
            else:
                E_mod = mat.E_mod
                if mat.G_mod and mat.G_mod > 0:
                    G_mod = mat.G_mod
                else:
                    G_mod = E_mod / (2 * (1 + mat.nu)) if mat.nu else E_mod / 2.6
                nu_val = mat.nu

            _A = getattr(sec, 'A', 0.0) or 0.0
            _I33 = getattr(sec, 'I33', 0.0) or 0.0
            _I22 = getattr(sec, 'I22', 0.0) or 0.0
            _J = getattr(sec, 'J', 0.0) or 0.0

            # Stiffness modifiers
            amod = mods.get('AMod', 1.0)
            i33mod = mods.get('I3Mod', 1.0)
            i22mod = mods.get('I2Mod', 1.0)
            jmod = mods.get('JMod', 1.0)

            if self.config.get('use_elastic_sections', True):
                ops.section('Elastic', tag, E_mod, _A * amod,
                            _I33 * i33mod, _I22 * i22mod, G_mod, _J * jmod)

    # ── Shell elements ───────────────────────────────────────────

    def _create_shell_elements(self) -> None:
        """Create ShellMITC4 elements from MeshModel area elements."""
        if not self.config.get('create_shells', False):
            return

        if self.config['verbose']:
            print("Creating shell elements...")

        self._shell_sec_tags = dict(self.mesh_model.shell_sec_tags) if self.mesh_model.shell_sec_tags else {}
        self._shell_sec_variants = dict(self.mesh_model.shell_sec_variants) if self.mesh_model.shell_sec_variants else {}
        next_sec_tag = (max(self.section_tags.values(), default=0) + 1
                        if self.section_tags else 1)

        shell_count = 0
        for aid, area in self.mesh_model.area_elements.items():
            if getattr(area, 'inactive', False):
                continue

            nids = area.node_ids
            if len(nids) < 3:
                continue

            # Gather node tags
            node_tags = []
            skip = False
            for nid in nids:
                node = self.mesh_model.nodes.get(nid)
                if node is None:
                    skip = True
                    break
                node_tags.append(node.node_tag)
            if skip:
                continue

            sec_name = self.mesh_model.area_assignments.get(aid, '')
            if not sec_name or sec_name not in self.mesh_model.sections:
                continue

            sec = self.mesh_model.sections[sec_name]
            mat = self.mesh_model.materials.get(sec.material)
            if mat is None:
                continue

            # Determine section tag
            area_etype = self._area_element_types.get(aid)
            if area_etype and self._get_type_factor(area_etype) != 1.0:
                variant_key = f"{sec_name}__{area_etype}"
                if variant_key not in self._shell_sec_variants:
                    tag = next_sec_tag
                    next_sec_tag += 1
                    self._shell_sec_variants[variant_key] = tag
                    self._create_single_shell_section(sec, mat, tag, etype=area_etype)
                sec_tag = self._shell_sec_variants[variant_key]
            else:
                if sec_name not in self._shell_sec_tags:
                    tag = next_sec_tag
                    next_sec_tag += 1
                    self._shell_sec_tags[sec_name] = tag
                    self._create_single_shell_section(sec, mat, tag)
                sec_tag = self._shell_sec_tags[sec_name]

            # Determine element tag
            max_frame_tag = max(
                (e.elem_tag for e in self.mesh_model.frame_elements.values()
                 if not getattr(e, 'inactive', False)),
                default=0,
            )
            max_rigid_tag = max(
                (r[3] for r in self._offset_rigid_links),
                default=0,
            )
            elem_tag = max(max_frame_tag, max_rigid_tag, shell_count) + 1

            if len(node_tags) == 3:
                ops.element('ShellMITC4', elem_tag, *node_tags, sec_tag)
            else:
                ops.element('ShellMITC4', elem_tag, *node_tags[:4], sec_tag)
            shell_count += 1

        if self.config['verbose']:
            print(f"  Created {shell_count} shell elements")

    def _create_single_shell_section(self, sec, mat, tag, etype=None):
        """Create a single ElasticMembranePlateSection in OpenSees."""
        E_mod = mat.E_mod or 200e9
        nu_val = mat.nu or 0.2
        factor = self._get_type_factor(etype) if etype else 1.0
        thickness = getattr(sec, 'thickness', 0.0) or 1.0
        if factor != 1.0:
            E_mod *= factor
        ops.section('ElasticMembranePlateSection', tag, E_mod, nu_val, thickness)

    def _get_type_factor(self, etype: str) -> float:
        """Return stiffness reduction factor for a structural type."""
        factors = self.config.get('stiffness_factors', {})
        return factors.get(etype, 1.0)

    # ── Frame elements ───────────────────────────────────────────

    def _create_elements(self) -> None:
        """Create OpenSees frame elements from MeshModel."""
        from ..model.geometry import subdivide_elements

        if self.config['verbose']:
            print("Creating frame elements...")

        elements = self.mesh_model.frame_elements
        assignments = self.mesh_model.frame_assignments
        dist_loads = self.mesh_model.frame_dist_loads

        # Build the element tag map from MeshModel
        next_tag = 1
        self.frame_tag_map = {}
        for eid, elem in elements.items():
            if getattr(elem, 'inactive', False):
                continue
            if elem.elem_tag in self.frame_tag_map.values():
                tag = next_tag
                next_tag += 1
            else:
                tag = elem.elem_tag if elem.elem_tag > 0 else next_tag
                next_tag = max(next_tag, tag + 1)
            self.frame_tag_map[eid] = tag

        for eid, elem in elements.items():
            if getattr(elem, 'inactive', False):
                continue

            tag = self.frame_tag_map.get(eid)
            if tag is None:
                continue

            self._add_beam_column(elem, tag, elements, assignments)

        # Brace subdivision
        if self.config.get('subdivide_braces', False):
            n_seg = self.config.get('brace_n_segments', 4)
            imperf = self.config.get('brace_imperfection_ratio', 1.0 / 500.0)
            subdivide_elements(self, elements, assignments,
                               n_segments=n_seg, imperfection_ratio=imperf)

        # Rigid links from frame end offsets
        for ni_tag, nj_tag, sec_name, elem_tag in self._offset_rigid_links:
            sec = self.mesh_model.sections.get(sec_name)
            if sec is None:
                continue
            if sec_name not in self.section_tags:
                mat = self.mesh_model.materials.get(sec.material)
                mat_tag = 9999
                ops.uniaxialMaterial('Elastic', mat_tag, 200e9)
                from ..model.sap_data import Section as SecCls
                ops.section('Elastic', elem_tag, 200e9, getattr(sec, 'A', 1e9),
                            getattr(sec, 'I33', 1e9), getattr(sec, 'I22', 1e9),
                            80e9, getattr(sec, 'J', 1e9))
                self.section_tags[sec_name] = elem_tag
            ops.geomTransf('Linear', elem_tag)
            ops.element('elasticBeamColumn', elem_tag, ni_tag, nj_tag, elem_tag,
                        elem_tag, '-mass', 0.0)
            self._rigid_link_elems[sec_name] = elem_tag

        if self.config['verbose']:
            n = len([e for e in elements.values() if not getattr(e, 'inactive', False)])
            print(f"  Created {n} frame elements")

    def _add_beam_column(self, elem, tag, elements, assignments):
        """Add a single beam-column element to the OpenSees domain."""
        ni = self.mesh_model.nodes.get(elem.node_i)
        nj = self.mesh_model.nodes.get(elem.node_j)
        if ni is None or nj is None:
            return

        sec_name = assignments.get(elem.elem_id, '')
        if not sec_name:
            sec_name = assignments.get(elem.elem_id, '')

        # Determine section tag (check type-specific variant first)
        etype = self._frame_element_types.get(elem.elem_id)
        if etype:
            variant_key = f"{sec_name}__{etype}"
            if variant_key in self.section_tags:
                sec_tag = self.section_tags[variant_key]
            else:
                sec_tag = self.section_tags.get(sec_name, -1)
        else:
            sec_tag = self.section_tags.get(sec_name, -1)

        if sec_tag < 0:
            return

        # Geometric transformation
        angle = getattr(elem, 'angle', 0.0)
        vecxz = get_SAP_vecxz(np.array([nj.x - ni.x, nj.y - ni.y, nj.z - ni.z]), angle)
        transf_type = self.config.get('geom_transf_type', 'Linear')
        transf_tag = tag
        ops.geomTransf(transf_type, transf_tag, *vecxz)
        self._transf_tags[tag] = transf_tag

        # Element
        elem_type = self.config['element_type']
        n_ip = self.config.get('num_int_pts', 3)
        if elem_type == 'elasticBeamColumn':
            ops.element(elem_type, tag, *[ni.node_tag, nj.node_tag], sec_tag, transf_tag)
        else:
            int_tag = tag + 10000
            if self.config.get('beam_integration', 'Lobatto') == 'Lobatto':
                ops.beamIntegration('Lobatto', int_tag, sec_tag, n_ip)
            else:
                ops.beamIntegration('HingeRadau', int_tag, sec_tag, n_ip)
            ops.element(elem_type, tag, *[ni.node_tag, nj.node_tag], n_ip, int_tag, transf_tag)

    # ── Lumped hinges ────────────────────────────────────────────

    def _create_lumped_hinges(self) -> None:
        """Create zero-length hinge elements (stub — extended by subclasses)."""
        pass

    # ── Loads ────────────────────────────────────────────────────

    def _create_loads(self,
                      pattern_scales: Optional[Dict[str, float]] = None,
                      ) -> None:
        """Create OpenSees load patterns from MeshModel data."""
        from ..model.geometry import global_to_local_distributed_load

        elements = self.mesh_model.frame_elements
        assignments = self.mesh_model.frame_assignments
        dist_loads = self.mesh_model.frame_dist_loads
        edge_loads = self.edge_loads_from_areas

        patterns_created: set = set()
        self.load_totals = {}
        self._sw_load_totals = {}
        self._gravity_load_totals = {}

        # Self-weight
        sw_factor = self.config.get('self_weight_factor', 1.0)
        if sw_factor > 0:
            for eid, elem in elements.items():
                if getattr(elem, 'inactive', False):
                    continue
                tag = self.frame_tag_map.get(eid)
                if tag is None:
                    continue
                sec_name = assignments.get(eid, '')
                sec = self.mesh_model.sections.get(sec_name)
                if sec is None:
                    continue
                mat = self.mesh_model.materials.get(sec.material)
                if mat is None or mat.unit_weight == 0:
                    continue
                _A = getattr(sec, 'A', 0.0)
                if _A <= 0:
                    continue
                ni = self.mesh_model.nodes.get(elem.node_i)
                nj = self.mesh_model.nodes.get(elem.node_j)
                if ni is None or nj is None:
                    continue
                L = math.sqrt((nj.x - ni.x)**2 + (nj.y - ni.y)**2 + (nj.z - ni.z)**2)
                w = _A * mat.unit_weight * sw_factor
                self._sw_load_totals[eid] = w * L

        # Pattern loop
        all_patterns = set()
        for ld in dist_loads:
            all_patterns.add(ld.pattern)
        for ld in edge_loads:
            all_patterns.add(ld.pattern)

        for pname in sorted(all_patterns):
            if pattern_scales is not None and pname not in pattern_scales:
                continue
            scale = pattern_scales.get(pname, 1.0) if pattern_scales else 1.0

            ts_tag = hash(pname) % 9000 + 1000
            ptag = hash(pname) % 9000 + 100
            ops.timeSeries('Linear', ts_tag)
            ops.pattern('Plain', ptag, ts_tag)
            patterns_created.add(pname)

            load_total = 0.0

            # Frame distributed loads
            for ld in dist_loads:
                if ld.pattern != pname:
                    continue
                tag = self.frame_tag_map.get(ld.frame_id)
                if tag is None:
                    continue
                elem = elements.get(ld.frame_id)
                if elem is None or getattr(elem, 'inactive', False):
                    continue
                ni = self.mesh_model.nodes.get(elem.node_i)
                nj = self.mesh_model.nodes.get(elem.node_j)
                if ni is None or nj is None:
                    continue

                wa = ld.val_a * scale
                wb = ld.val_b * scale
                aL = ld.rdist_a
                bL = ld.rdist_b

                vx, vy, vz = self.get_local_axes(elem)
                T = np.vstack([vx, vy, vz])
                dir_map = {'Gravity': (0, 0, -1), 'X': (1, 0, 0), 'Y': (0, 1, 0), 'Z': (0, 0, 1)}
                gx, gy, gz = dir_map.get(ld.direction, (0, 0, 0))
                g_local = np.linalg.solve(T, np.array([gx, gy, gz]))
                wy_a = g_local[1] * wa
                wz_a = g_local[2] * wa
                wx_a = g_local[0] * wa
                wy_b = g_local[1] * wb
                wz_b = g_local[2] * wb
                wx_b = g_local[0] * wb

                is_uniform = abs(wa - wb) < 1e-12
                if is_uniform and abs(aL) < 1e-12 and abs(bL - 1.0) < 1e-12:
                    ops.eleLoad('-ele', tag, '-type', '-beamUniform', wy_a, wz_a, wx_a)
                elif is_uniform:
                    ops.eleLoad('-ele', tag, '-type', '-beamUniform', wy_a, wz_a, wx_a, aL, bL)
                else:
                    L_seg = bL - aL
                    for i in range(4):
                        seg_a = aL + i * L_seg / 4
                        seg_b = aL + (i + 1) * L_seg / 4
                        xi = (i + 0.5) / 4
                        ops.eleLoad('-ele', tag, '-type', '-beamUniform',
                                    wy_a + (wy_b - wy_a) * xi,
                                    wz_a + (wz_b - wz_a) * xi,
                                    wx_a + (wx_b - wx_a) * xi,
                                    seg_a, seg_b)

                load_total += abs(wa) * abs(bL - aL)

            # Edge loads (from area-to-frame conversion)
            for ld in edge_loads:
                if ld.pattern != pname:
                    continue
                tag = self.frame_tag_map.get(ld.frame_id)
                if tag is None:
                    continue
                wa = ld.val_a * scale
                wb = ld.val_b * scale
                ops.eleLoad('-ele', tag, '-type', '-beamUniform', 0.0, -wa, 0.0, ld.rdist_a, ld.rdist_b)
                load_total += abs(wa) * abs(ld.rdist_b - ld.rdist_a)

            self.load_totals[pname] = load_total

        # Joint loads
        for jl in getattr(self.mesh_model, 'joint_loads', []):
            pname = jl.pattern
            if pattern_scales is not None and pname not in pattern_scales:
                continue
            if pname not in patterns_created:
                scale = pattern_scales.get(pname, 1.0) if pattern_scales else 1.0
                ts_tag = hash(pname) % 9000 + 1000
                ptag = hash(pname) % 9000 + 100
                ops.timeSeries('Linear', ts_tag)
                ops.pattern('Plain', ptag, ts_tag)
                patterns_created.add(pname)
            nd = self.mesh_model.nodes.get(jl.node_id)
            if nd is None:
                continue
            ops.load(nd.node_tag, jl.fx, jl.fy, jl.fz, jl.mx, jl.my, jl.mz)

    # ── Rigid diaphragms ─────────────────────────────────────────

    def _apply_rigid_diaphragms(self) -> int:
        """Apply rigid diaphragm constraints at detected storey levels."""
        levels = self.mesh_model.diaphragm_levels
        config_val = self.config.get('rigid_diaphragms', False)
        if not config_val or not levels:
            return 0

        if isinstance(config_val, list):
            levels = sorted(float(z) for z in config_val)

        applied = 0
        for z in levels:
            tags_at_z = []
            for nid, nd in self.mesh_model.nodes.items():
                if abs(nd.z - float(z)) > 0.01:
                    continue
                try:
                    ops.nodeCoord(nd.node_tag)
                    tags_at_z.append(nd.node_tag)
                except Exception:
                    continue
            if len(tags_at_z) < 2:
                continue

            xs = [float(ops.nodeCoord(t)[0]) for t in tags_at_z]
            ys = [float(ops.nodeCoord(t)[1]) for t in tags_at_z]
            cx = sum(xs) / len(xs)
            cy = sum(ys) / len(ys)
            master = min(tags_at_z,
                         key=lambda t: (float(ops.nodeCoord(t)[0]) - cx)**2
                                      + (float(ops.nodeCoord(t)[1]) - cy)**2)
            slaves = [t for t in tags_at_z if t != master]
            try:
                ops.rigidDiaphragm(3, master, *slaves)
                applied += 1
            except Exception:
                continue
        return applied

    # ═══════════════════════════════════════════════════════════════
    # Analysis methods
    # ═══════════════════════════════════════════════════════════════

    def run_static_analysis(self,
                            extract_reactions: bool = True,
                            pattern_scales: Optional[Dict[str, float]] = None,
                            ) -> Dict[str, Any]:
        """Run static analysis on the current OpenSees domain.

        Returns a dict with nodal_displacements, reactions, element_forces,
        and load_totals.
        """
        test_type = self.config.get('solver_test_type', 'NormDispIncr')
        test_tol = self.config.get('solver_test_tol', 1e-6)
        test_iter = self.config.get('solver_test_max_iter', 10)
        algo = self.config.get('solver_algorithm', 'Newton')
        n_sub = self.config.get('gravity_num_substeps', 1)

        cs = self.config.get('solver_constraints', 'Transformation')
        if self._edge_constraint_method == 'penalty':
            cs = 'Penalty'
            ops.constraints('Penalty', 1.0e12, 1.0e12)
        else:
            ops.constraints(cs)
        ops.numberer('RCM')
        ops.system(self.config.get('solver_system', 'BandGen'))
        ops.test(test_type, test_tol, test_iter)

        _algo_chain = [algo]
        if algo != 'NewtonLineSearch':
            _algo_chain.append('NewtonLineSearch')
        if algo != 'ModifiedNewton':
            _algo_chain.append(('ModifiedNewton', '-initial'))
        if algo != 'KrylovNewton':
            _algo_chain.append('KrylovNewton')

        ops.integrator('LoadControl', 1.0 / n_sub)
        ops.analysis('Static')

        converged = 0
        ok = -1
        for attempt in _algo_chain:
            if isinstance(attempt, tuple):
                ops.algorithm(*attempt)
            else:
                if attempt == 'ModifiedNewton':
                    ops.algorithm('ModifiedNewton', '-initial')
                else:
                    ops.algorithm(attempt)
            ok = 0
            for s in range(converged, n_sub):
                ok = ops.analyze(1)
                if ok != 0:
                    break
                converged = s + 1
            if ok == 0:
                break

        if ok != 0:
            return {}

        # Extract results
        result: Dict[str, Any] = {}

        # Nodal displacements
        result['nodal_displacements'] = {}
        for nd in self.mesh_model.nodes.values():
            try:
                disp = ops.nodeDisp(nd.node_tag)
                result['nodal_displacements'][nd.node_id] = list(disp)
            except Exception:
                continue

        # Reactions
        if extract_reactions:
            result['reactions'] = {}
            for nid, restraint in self.mesh_model.restraints.items():
                nd = self.mesh_model.nodes.get(nid)
                if nd is None:
                    continue
                try:
                    rxn = ops.nodeReaction(nd.node_tag)
                    result['reactions'][nid] = {
                        'fx': rxn[0], 'fy': rxn[1], 'fz': rxn[2],
                        'mx': rxn[3], 'my': rxn[4], 'mz': rxn[5],
                    }
                except Exception:
                    continue

        return result

    # ═══════════════════════════════════════════════════════════════
    # Utilities
    # ═══════════════════════════════════════════════════════════════

    def get_local_axes(self, elem: FrameElement) -> Tuple[np.ndarray, ...]:
        """Compute local x, y, z unit vectors for a frame element.

        Uses ``get_SAP_vecxz`` from the geometry module (which handles
        the SAP2000 vecxz convention) combined with the element's
        section rotation angle.

        Args:
            elem: Frame element with ``node_i``, ``node_j``, and
                ``angle`` attributes.

        Returns:
            ``(vx, vy, vz)`` tuple of three unit vectors forming a
            right‑handed local coordinate system.

        Raises:
            ValueError: If either node cannot be resolved, or the
                element has zero length.
        """
        ni = self.mesh_model.nodes.get(elem.node_i)
        nj = self.mesh_model.nodes.get(elem.node_j)
        if ni is None or nj is None:
            raise ValueError(f"Cannot resolve nodes for {elem.elem_id}")
        vx = np.array([nj.x - ni.x, nj.y - ni.y, nj.z - ni.z])
        length = np.linalg.norm(vx)
        if length < 1e-12:
            raise ValueError(f"Zero-length element {elem.elem_id}")
        vx_norm = vx / length
        # Use get_SAP_vecxz for the reference vector
        vecxz = get_SAP_vecxz(vx_norm, getattr(elem, 'angle', 0.0))
        vz = vecxz / np.linalg.norm(vecxz)
        vy = np.cross(vz, vx_norm)
        vy_norm = vy / np.linalg.norm(vy)
        return vx_norm, vy_norm, vz
