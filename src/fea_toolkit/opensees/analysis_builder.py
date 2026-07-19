"""Analysis builder — create OpenSees domain from a prepared ``MeshModel``.

The :class:`AnalysisBuilder` takes a :class:`~fea_toolkit.model.mesh_model.MeshModel`
(fully prepared topology from the :class:`~fea_toolkit.opensees.preprocessor.Preprocessor`)
and creates the OpenSees domain objects.  It handles all analysis execution
and result extraction — no topology mutations occur here.
"""

from typing import Dict, Any, Optional, List, Tuple, Set
import copy
import math
import numpy as np

import openseespy.opensees as ops

from ..model.mesh_model import MeshModel
from ..model.sap_data import (
    Node, FrameElement, AreaElement,
    ShellSection, Restraint,
)
from ..model.geometry import get_SAP_vecxz
from ..model.geometry import convert_area_loads_to_edge_loads, polygon_area_3d
from ..model.tree_utils import collect_descendants
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
        self.material_tags: Dict[str, int] = dict(mesh_model.material_tags)
        self.section_tags: Dict[str, int] = dict(mesh_model.section_tags)
        self._shell_sec_tags: Dict[str, int] = dict(mesh_model.shell_sec_tags)
        self._shell_sec_variants: Dict[str, int] = dict(mesh_model.shell_sec_variants)
        self._frame_element_types: Dict[str, str] = dict(mesh_model.frame_element_types)
        self._area_element_types: Dict[str, str] = dict(mesh_model.area_element_types)
        self._offset_rigid_links: List[tuple] = list(mesh_model.offset_rigid_links)
        self._edge_constraint_method: Optional[str] = None
        self._saved_edge_constraints: List[tuple] = list(mesh_model.saved_edge_constraints)
        self._rigid_link_elems: Dict[str, int] = {}
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
        self._rigid_link_elems = {}
        ops.model('basic', '-ndm', 3, '-ndf', 6)

        # Pre-compute frame tag map so shell elements can avoid clashing
        self._build_frame_tag_map()

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
        """Create OpenSees materials.

        Assigns material tags sequentially if not already populated in
        ``self.material_tags``.  Creates elastic materials for all
        referenced materials (needed for section creation), plus
        nonlinear materials for fiber sections and brace trusses.
        """
        # Auto-assign material tags
        next_tag = max(self.material_tags.values(), default=0) + 1 if self.material_tags else 1
        for mat_name, mat in self.mesh_model.materials.items():
            if mat_name not in self.material_tags:
                self.material_tags[mat_name] = next_tag
                next_tag += 1

        # Create elastic materials for all materials
        # Determine which material names are used by brace-truss sections so
        # we can skip Elastic creation for them (the Hysteretic material
        # replaces the Elastic at a distinct tag, but creating both is wasteful).
        _brace_mat_names: set = set()
        if self.config.get('brace_truss'):
            from ..model.sap_data import (
                PipeSection, AngleSection, DoubleAngleSection,
                TeeSection, ChannelSection,
            )
            brace_sec_types = (
                PipeSection, AngleSection, DoubleAngleSection,
                TeeSection, ChannelSection,
            )
            explicit = self.config.get('brace_sections')
            for sec_name, sec in self.mesh_model.sections.items():
                if explicit is not None:
                    if sec_name not in explicit:
                        continue
                elif not isinstance(sec, brace_sec_types):
                    continue
                _brace_mat_names.add(sec.material)

        for mat_name, mat in self.mesh_model.materials.items():
            tag = self.material_tags.get(mat_name)
            if tag is None:
                continue
            E_mod = mat.E_mod or 200e9
            if mat_name in _brace_mat_names:
                continue  # will be created as Hysteretic in brace-truss section
            try:
                ops.uniaxialMaterial('Elastic', tag, E_mod)
            except Exception:
                pass  # may already exist

        # Fiber section materials
        if self.config.get('create_fiber_sections'):
            from ..model.sap_data import (
                PipeSection, AngleSection, DoubleAngleSection,
                TeeSection, ChannelSection,
            )
            for sec_name, sec in self.mesh_model.sections.items():
                mat_name = sec.material
                mat_tag = self.material_tags.get(mat_name)
                if mat_tag is None:
                    continue
                # Section-specific nonlinear materials created by
                # sec.to_fiber_patches(mat_tag, ...) in _create_single_section

        # Brace truss materials
        if self.config.get('brace_truss'):
            from ..model.sap_data import (
                PipeSection, AngleSection, DoubleAngleSection,
                TeeSection, ChannelSection,
            )
            brace_types = (
                PipeSection, AngleSection, DoubleAngleSection,
                TeeSection, ChannelSection,
            )
            self._truss_mat_tags: Dict[str, int] = {}
            self._truss_areas: Dict[str, float] = {}
            # Use tags beyond material count
            n_mat = max(self.material_tags.values(), default=0) + 1 if self.material_tags else 1
            truss_tag = n_mat

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

                self._truss_mat_tags[sec_name] = truss_tag
                self._truss_areas[sec_name] = area
                ops.uniaxialMaterial('Hysteretic', truss_tag,
                                     1.1 * Fy, 0.007 * E_sec / Fy,
                                     0.3 * Fy, 0.004 * E_sec / Fy,
                                     1.1 * Fy, 0.007 * E_sec / Fy,
                                     1.0, 0.0, 0.0, 0.0)
                self.material_tags[f"{sec_name}__truss"] = truss_tag
                truss_tag += 1

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
        if self.config.get('create_fiber_sections'):
            # ── Fiber section path ───────────────────────────────
            mat = self.mesh_model.materials.get(sec.material)
            if mat is None:
                E_mod = 200e9
                G_mod = 80e9
            else:
                E_mod = mat.E_mod or 200e9
                G_mod = mat.G_mod or (E_mod / 2.6)

            _A = getattr(sec, 'A', 0.0) or 0.0
            _I33 = getattr(sec, 'I33', 0.0) or 0.0
            _I22 = getattr(sec, 'I22', 0.0) or 0.0
            _J = getattr(sec, 'J', 0.0) or 0.0

            # Create material appropriate for the section type
            mat_tag = tag  # material tag = section tag
            if mat is not None and mat.type.lower() == 'concrete':
                Fc = getattr(mat, 'Fc', 0.0) or 3.0e7
                epsc = getattr(mat, 'eFc', 0.0) or 0.002
                ops.uniaxialMaterial('Concrete01', mat_tag,
                                     -Fc, -abs(epsc), -0.2 * Fc, -0.006)
            else:
                Fy = getattr(mat, 'Fy', 0.0) or 2.5e8
                ops.uniaxialMaterial('Steel01', mat_tag, Fy, E_mod, 0.01)

            # Create fiber section
            ops.section('Fiber', tag, '-GJ', _J)
            try:
                entries = sec.to_fiber_patches(mat_tag=mat_tag, nfy=8, nfz=4)
            except NotImplementedError:
                # Fall back to elastic
                if self.config.get('verbose', False):
                    print(f"  Section {tag} ({sec.name}): fiber not supported, "
                          f"falling back to elastic")
                ops.section('Elastic', tag, E_mod, _A, _I33, _I22, G_mod, _J)
                return

            for entry in entries:
                if entry[0] in ('rect', 'circ', 'quad'):
                    ops.patch(*entry)
                elif entry[0] == 'straight':
                    ops.layer('straight', *entry[1:])
                elif entry[0] == 'circ_layer':
                    ops.layer('circ', *entry[1:])

            if self.config.get('verbose', False):
                print(f"  Section {tag}: {sec.name} (Fiber, {len(entries)} patches)")

        else:
            # ── Elastic section path ──────────────────────────────
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
        loads_only = self.mesh_model.loads_only_area_ids
        for aid, area in self.mesh_model.area_elements.items():
            # Skip loads-only areas — they contribute mass but not stiffness
            if aid in loads_only:
                continue
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

            # Determine element tag — avoid clashing with frame elements
            max_frame_tag = max(self.frame_tag_map.values(), default=0)
            max_rigid_tag = max(
                (r[3] for r in self._offset_rigid_links),
                default=0,
            ) if self._offset_rigid_links else 0
            next_shell_tag = max(max_frame_tag, max_rigid_tag) + 1 + shell_count
            elem_tag = next_shell_tag

            if len(node_tags) == 3:
                # Repeat last node tag for the 4th corner (Collapsed quad)
                ops.element('ShellMITC4', elem_tag,
                            node_tags[0], node_tags[1], node_tags[2],
                            node_tags[2], sec_tag)
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

    def _build_frame_tag_map(self) -> None:
        """Pre-compute frame element tags before creating elements.

        Ensures shell element tag assignment can avoid clashing with
        frame element tags.
        """
        elements = self.mesh_model.frame_elements
        next_tag = 1
        self.frame_tag_map = {}
        used_tags: Set[int] = set()
        for eid, elem in elements.items():
            if getattr(elem, 'inactive', False):
                continue
            if elem.elem_tag in used_tags:
                tag = next_tag
                next_tag += 1
            else:
                tag = elem.elem_tag if elem.elem_tag > 0 else next_tag
                next_tag = max(next_tag, tag + 1)
            used_tags.add(tag)
            self.frame_tag_map[eid] = tag

    def _create_elements(self) -> None:
        """Create OpenSees frame elements from MeshModel."""
        from ..model.geometry import subdivide_elements

        if self.config['verbose']:
            print("Creating frame elements...")

        elements = self.mesh_model.frame_elements
        assignments = self.mesh_model.frame_assignments
        dist_loads = self.mesh_model.frame_dist_loads

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
        # The Preprocessor returns (link_id, node_i, node_j, link_tag) tuples.
        # node_i and node_j are string node IDs — resolve to numeric tags.
        if self._offset_rigid_links:
            # Pick a tag beyond ALL section tags (frame + shell variant)
            all_sec_tags = set(self.section_tags.values())
            all_sec_tags.update(self._shell_sec_tags.values())
            all_sec_tags.update(self._shell_sec_variants.values())
            rigid_section_tag = max(all_sec_tags, default=0) + 1
            rigid_E = 2.0e14
            rigid_A = 1.0
            rigid_I = 1.0
            ops.section('Elastic', rigid_section_tag, rigid_E, rigid_A,
                        rigid_I, rigid_I, rigid_E / 2.6, rigid_I)
            for _link_id, _node_i_id, _node_j_id, link_tag in self._offset_rigid_links:
                nd_i = self.mesh_model.nodes.get(_node_i_id)
                nd_j = self.mesh_model.nodes.get(_node_j_id)
                if nd_i is None or nd_j is None:
                    continue
                ni_tag = nd_i.node_tag
                nj_tag = nd_j.node_tag
                # Compute vecxz for vertical/horizontal links (same convention as _add_beam_column)
                dx = float(nd_j.x - nd_i.x)
                dy = float(nd_j.y - nd_i.y)
                dz = float(nd_j.z - nd_i.z)
                from ..model.geometry import get_SAP_vecxz
                vecxz = get_SAP_vecxz(np.array([dx, dy, dz]), 0.0)
                ops.geomTransf('Linear', link_tag, *vecxz)
                ops.element('elasticBeamColumn', link_tag, ni_tag, nj_tag,
                            rigid_section_tag, link_tag, '-mass', 0.0)
                self._rigid_link_elems[_link_id] = link_tag

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
            ops.element(elem_type, tag, *[ni.node_tag, nj.node_tag], transf_tag, int_tag)

    # ── Lumped hinges ────────────────────────────────────────────

    def _create_lumped_hinges(self) -> None:
        """Create zero-length hinge elements (stub — extended by subclasses)."""
        pass

    # ── Loads ────────────────────────────────────────────────────

    def _create_loads(self,
                      pattern_scales: Optional[Dict[str, float]] = None,
                      ) -> None:
        """Create OpenSees load patterns from MeshModel data."""

        elements = self.mesh_model.frame_elements
        assignments = self.mesh_model.frame_assignments
        dist_loads = self.mesh_model.frame_dist_loads
        edge_loads = self.edge_loads_from_areas

        patterns_created: set = set()
        self.load_totals = {}
        self._sw_load_totals = {}
        self._gravity_load_totals = {}

        # Self-weight computation (applied downstream when pattern activated)
        _sw_frame_loads: Dict[str, list] = {}  # pname → [(node_tag, fz), ...]
        sw_factor = self.config.get('self_weight_factor', 1.0)
        if sw_factor > 0:
            for eid, elem in elements.items():
                if getattr(elem, 'inactive', False):
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
                total_w = w * L
                self._sw_load_totals[eid] = total_w
                # Half-weight to each end node (use node tags, not element tags)
                nd_i = self.mesh_model.nodes.get(elem.node_i)
                nd_j = self.mesh_model.nodes.get(elem.node_j)
                if nd_i is not None:
                    _sw_frame_loads.setdefault('Self weight', []).append((nd_i.node_tag, -total_w * 0.5))
                if nd_j is not None:
                    _sw_frame_loads.setdefault('Self weight', []).append((nd_j.node_tag, -total_w * 0.5))

        # Pattern loop — deterministic tag generation
        all_patterns = set()
        for ld in dist_loads:
            all_patterns.add(ld.pattern)
        for ld in edge_loads:
            all_patterns.add(ld.pattern)
        for jl in getattr(self.mesh_model, 'joint_loads', []):
            all_patterns.add(jl.pattern)
        for gl in getattr(self.mesh_model, 'frame_gravity_loads', []):
            all_patterns.add(gl.pattern)
        for agl in getattr(self.mesh_model, 'area_gravity_loads', []):
            all_patterns.add(agl.pattern)
        all_patterns.add('Self weight')
        # Assign deterministic tags based on sorted pattern names
        _pat_tags = {
            pname: (1000 + i, 100 + i)
            for i, pname in enumerate(sorted(all_patterns))
        }

        for pname in sorted(all_patterns):
            if pattern_scales is not None and pname not in pattern_scales:
                continue
            scale = pattern_scales.get(pname, 1.0) if pattern_scales else 1.0

            ts_tag, ptag = _pat_tags.get(pname, (1000, 100))
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

                load_total += abs(wa + wb) * 0.5 * abs(bL - aL)

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
                ts_tag, ptag = _pat_tags.get(pname, (1000, 100))
                ops.timeSeries('Linear', ts_tag)
                ops.pattern('Plain', ptag, ts_tag)
                patterns_created.add(pname)
            nd = self.mesh_model.nodes.get(jl.node_id)
            if nd is None:
                continue
            ops.load(nd.node_tag, jl.fx, jl.fy, jl.fz, jl.mx, jl.my, jl.mz)

        # ── Self-weight (auto-included when any pattern is active) ──
        if _sw_frame_loads:
            sw_pname = 'Self weight'
            if pattern_scales is not None:
                if sw_pname in pattern_scales:
                    scale = pattern_scales[sw_pname]
                else:
                    scale = 1.0  # auto-include with default factor
            else:
                # pattern_scales=None means apply all patterns with factor 1.0
                scale = 1.0
            sw_scale = abs(scale)
            if sw_scale > 1e-12:
                # Create pattern if not yet created
                if sw_pname not in patterns_created:
                    ts_tag, ptag = _pat_tags.get(sw_pname, (1000, 100))
                    ops.timeSeries('Linear', ts_tag)
                    ops.pattern('Plain', ptag, ts_tag)
                    patterns_created.add(sw_pname)
                sw_total = 0.0
                for node_tag, fz in _sw_frame_loads.get(sw_pname, []):
                    ops.load(node_tag, 0.0, 0.0, fz * scale, 0.0, 0.0, 0.0)
                    sw_total += abs(fz * scale)
                self.load_totals[sw_pname] = sw_total

        # ── Frame gravity loads (explicit multipliers on self-weight) ──
        for gl in getattr(self.mesh_model, 'frame_gravity_loads', []):
            pname = gl.pattern
            if pattern_scales is not None and pname not in pattern_scales:
                continue
            scale = pattern_scales.get(pname, 1.0) if pattern_scales else 1.0
            if abs(scale) < 1e-12:
                continue
            # Create pattern if needed
            if pname not in patterns_created:
                ts_tag, ptag = _pat_tags.get(pname, (1000, 100))
                ops.timeSeries('Linear', ts_tag)
                ops.pattern('Plain', ptag, ts_tag)
                patterns_created.add(pname)
            elem = elements.get(gl.frame_id)
            if elem is None or getattr(elem, 'inactive', False):
                continue
            sec_name = assignments.get(gl.frame_id, '')
            if not sec_name:
                continue
            sec = self.mesh_model.sections.get(sec_name)
            if sec is None:
                continue
            mat = self.mesh_model.materials.get(sec.material)
            if mat is None or abs(mat.unit_weight) < 1e-12:
                continue
            ni = self.mesh_model.nodes.get(elem.node_i)
            nj = self.mesh_model.nodes.get(elem.node_j)
            if ni is None or nj is None:
                continue
            L = math.sqrt((nj.x - ni.x)**2 + (nj.y - ni.y)**2 + (nj.z - ni.z)**2)
            if L < 1e-12:
                continue
            sw_per_len = getattr(sec, 'A', 0.0) * mat.unit_weight
            fx = sw_per_len * L * gl.multiplier_x * scale * 0.5
            fy = sw_per_len * L * gl.multiplier_y * scale * 0.5
            fz = sw_per_len * L * gl.multiplier_z * scale * 0.5
            ops.load(ni.node_tag, fx, fy, fz, 0.0, 0.0, 0.0)
            ops.load(nj.node_tag, fx, fy, fz, 0.0, 0.0, 0.0)
            if pname not in self._gravity_load_totals:
                self._gravity_load_totals[pname] = {'fx': 0.0, 'fy': 0.0, 'fz': 0.0,
                                                     'mx': 0.0, 'my': 0.0, 'mz': 0.0}
            self._gravity_load_totals[pname]['fx'] += fx * 2
            self._gravity_load_totals[pname]['fy'] += fy * 2
            self._gravity_load_totals[pname]['fz'] += fz * 2

        # ── Area gravity loads (explicit multipliers) ────────────
        for agl in getattr(self.mesh_model, 'area_gravity_loads', []):
            pname = agl.pattern
            if pattern_scales is not None and pname not in pattern_scales:
                continue
            scale = pattern_scales.get(pname, 1.0) if pattern_scales else 1.0
            if abs(scale) < 1e-12:
                continue
            if pname not in patterns_created:
                ts_tag, ptag = _pat_tags.get(pname, (1000, 100))
                ops.timeSeries('Linear', ts_tag)
                ops.pattern('Plain', ptag, ts_tag)
                patterns_created.add(pname)
            area_elem = self.mesh_model.area_elements.get(agl.area_id)
            if area_elem is None:
                continue
            if getattr(area_elem, 'inactive', False):
                # Parent was split/meshed — apply to all leaf descendants
                sub_ids = collect_descendants(
                    agl.area_id, self.mesh_model.area_elements)
                if not sub_ids:
                    continue
                for sub_id in sub_ids:
                    sub_elem = self.mesh_model.area_elements[sub_id]
                    sec_name = self.mesh_model.area_assignments.get(sub_id, '')
                    if not sec_name:
                        continue
                    sec = self.mesh_model.sections.get(sec_name)
                    if sec is None:
                        continue
                    mat = self.mesh_model.materials.get(sec.material)
                    if mat is None or abs(mat.unit_weight) < 1e-12:
                        continue
                    thickness = getattr(sub_elem, 'thickness', 0.0) or 0.0
                    if thickness < 1e-12:
                        continue
                    corner_pts = []
                    for nid in sub_elem.node_ids:
                        nd = self.mesh_model.nodes.get(nid)
                        if nd is None:
                            break
                        corner_pts.append((nd.x, nd.y, nd.z))
                    if len(corner_pts) < 3:
                        continue
                    area_mag = polygon_area_3d(corner_pts)
                    if area_mag < 1e-12:
                        continue
                    sw_per_area = thickness * mat.unit_weight
                    tfx = sw_per_area * area_mag * agl.multiplier_x * scale
                    tfy = sw_per_area * area_mag * agl.multiplier_y * scale
                    tfz = sw_per_area * area_mag * agl.multiplier_z * scale
                    n_c = len(sub_elem.node_ids)
                    for nid in sub_elem.node_ids:
                        nd = self.mesh_model.nodes.get(nid)
                        if nd is not None:
                            ops.load(nd.node_tag, tfx / n_c, tfy / n_c, tfz / n_c,
                                     0.0, 0.0, 0.0)
                continue
            # Active (unmeshed) area element
            sec_name = self.mesh_model.area_assignments.get(agl.area_id, '')
            if not sec_name:
                continue
            sec = self.mesh_model.sections.get(sec_name)
            if sec is None:
                continue
            mat = self.mesh_model.materials.get(sec.material)
            if mat is None or abs(mat.unit_weight) < 1e-12:
                continue
            thickness = getattr(area_elem, 'thickness', 0.0) or 0.0
            if thickness < 1e-12:
                continue
            corner_pts = []
            for nid in area_elem.node_ids:
                nd = self.mesh_model.nodes.get(nid)
                if nd is None:
                    break
                corner_pts.append((nd.x, nd.y, nd.z))
            if len(corner_pts) < 3:
                continue
            area_mag = polygon_area_3d(corner_pts)
            if area_mag < 1e-12:
                continue
            sw_per_area = thickness * mat.unit_weight
            tfx = sw_per_area * area_mag * agl.multiplier_x * scale
            tfy = sw_per_area * area_mag * agl.multiplier_y * scale
            tfz = sw_per_area * area_mag * agl.multiplier_z * scale
            n_c = len(area_elem.node_ids)
            for nid in area_elem.node_ids:
                nd = self.mesh_model.nodes.get(nid)
                if nd is not None:
                    ops.load(nd.node_tag, tfx / n_c, tfy / n_c, tfz / n_c,
                             0.0, 0.0, 0.0)

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
            ops.reactions()
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
    # Mass
    # ═══════════════════════════════════════════════════════════════

    def compute_seismic_masses(self, g: Optional[float] = None) -> Dict[str, float]:
        """Compute lumped nodal masses from the model's MASS SOURCE entries.

        All mass contributions are lumped to nodes and assigned via
        ``ops.mass(node, m, m, m, 0, 0, 0)``.

        Args:
            g: Gravitational acceleration.  ``None`` = auto-detect from
                model units (SI default 9.80665 m/s²).

        Returns:
            Dictionary mapping node ID → total lumped mass (tonnes).
        """
        from ..utils import g_from_units
        if g is None:
            g = g_from_units(self.mesh_model.units)

        mm = self.mesh_model
        elements = mm.frame_elements
        assignments = mm.frame_assignments
        dist_loads = mm.frame_dist_loads

        node_mass: Dict[str, float] = {}

        mass_sources = getattr(mm, 'mass_sources', {})
        if not mass_sources:
            # No MASS SOURCE definitions — fallback: element self-weight + DEAD
            self._mass_from_elements(mm, elements, assignments, node_mass, g)
            self._mass_from_dist_loads(mm, elements, dist_loads, node_mass, g,
                                       ["DEAD"])
        else:
            for ms in mass_sources.values():
                if ms.elements:
                    self._mass_from_elements(mm, elements, assignments, node_mass, g)

                if ms.loads and ms.load_pattern:
                    for lp_name, mult in ms.load_pattern.items():
                        if abs(mult) < 1e-12:
                            continue
                        self._mass_from_dist_loads(mm, elements, dist_loads,
                                                   node_mass, g, [lp_name], mult)
                        self._mass_from_joint_loads(mm, node_mass, g, lp_name, mult)
                        self._mass_from_area_gravity(mm, node_mass, g, lp_name, mult)
                        self._mass_from_area_uniform(mm, node_mass, g, lp_name, mult)

        # Assign masses to OpenSees nodes
        for nid, m in node_mass.items():
            nd = mm.nodes.get(nid)
            if nd is None:
                continue
            tag = nd.node_tag
            if m > 0:
                ops.mass(tag, m, m, m, 0, 0, 0)
            else:
                ops.mass(tag, 1e-6, 1e-6, 1e-6, 0, 0, 0)

        self.node_masses = node_mass
        self._mass_g = g

        if self.config.get('verbose'):
            total = sum(node_mass.values())
            print(f"  Total seismic mass: {total:.2f} tonnes")
            print(f"  Total seismic weight: {total * g / 1000:.2f} MN")

        return node_mass

    def _mass_from_elements(self, mm, elements, assignments,
                             node_mass, g):
        """Add mass from element self-weight."""
        for eid, elem in elements.items():
            if getattr(elem, 'inactive', False):
                continue
            sec_name = assignments.get(eid, '')
            if not sec_name:
                continue
            sec = mm.sections.get(sec_name)
            if sec is None:
                continue
            mat = mm.materials.get(sec.material)
            if mat is None or abs(mat.unit_weight) < 1e-12:
                continue
            ni = mm.nodes.get(elem.node_i)
            nj = mm.nodes.get(elem.node_j)
            if ni is None or nj is None:
                continue
            L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
            if L < 1e-12:
                continue
            weight = getattr(sec, 'A', 0.0) * mat.unit_weight * L
            mass = weight / g
            node_mass[elem.node_i] = node_mass.get(elem.node_i, 0.0) + mass * 0.5
            node_mass[elem.node_j] = node_mass.get(elem.node_j, 0.0) + mass * 0.5

        # Area elements
        for aid, ae in mm.area_elements.items():
            if getattr(ae, 'inactive', False):
                continue
            sec_name = mm.area_assignments.get(aid, '')
            if not sec_name:
                continue
            sec = mm.sections.get(sec_name)
            if sec is None:
                continue
            mat = mm.materials.get(sec.material)
            if mat is None or abs(mat.unit_weight) < 1e-12:
                continue
            thickness = getattr(ae, 'thickness', 0.0) or 0.0
            if thickness < 1e-12:
                continue
            corner_pts = []
            for nid in ae.node_ids:
                nd = mm.nodes.get(nid)
                if nd is None:
                    break
                corner_pts.append((nd.x, nd.y, nd.z))
            if len(corner_pts) < 3:
                continue
            area_mag = polygon_area_3d(corner_pts)
            if area_mag < 1e-12:
                continue
            weight = area_mag * thickness * mat.unit_weight
            mass = weight / g
            n_c = len(ae.node_ids)
            for nid in ae.node_ids:
                node_mass[nid] = node_mass.get(nid, 0.0) + mass / n_c

    def _mass_from_dist_loads(self, mm, elements, dist_loads,
                               node_mass, g, pattern_names, mult=1.0):
        """Add mass from frame distributed loads in given patterns."""
        for ld in dist_loads or []:
            if ld.pattern not in pattern_names:
                continue
            elem = elements.get(ld.frame_id)
            if elem is None or getattr(elem, 'inactive', False):
                continue
            ni = mm.nodes.get(elem.node_i)
            nj = mm.nodes.get(elem.node_j)
            if ni is None or nj is None:
                continue
            L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
            if L < 1e-12:
                continue
            load_len = ld.dist_b - ld.dist_a
            avg = (ld.val_a + ld.val_b) * 0.5
            total_force = avg * load_len * mult
            mass = total_force / g
            node_mass[elem.node_i] = node_mass.get(elem.node_i, 0.0) + mass * 0.5
            node_mass[elem.node_j] = node_mass.get(elem.node_j, 0.0) + mass * 0.5

    def _mass_from_joint_loads(self, mm, node_mass, g, lp_name, mult):
        """Add mass from joint loads in the given pattern."""
        for jl in getattr(mm, 'joint_loads', []):
            if jl.pattern != lp_name:
                continue
            total_force = abs(jl.fz) * mult
            mass = total_force / g
            node_mass[jl.node_id] = node_mass.get(jl.node_id, 0.0) + mass

    def _mass_from_area_gravity(self, mm, node_mass, g, lp_name, mult):
        """Add mass from area gravity loads in the given pattern."""
        from ..model.tree_utils import collect_descendants
        for agl in getattr(mm, 'area_gravity_loads', []):
            if agl.pattern != lp_name:
                continue
            ae = mm.area_elements.get(agl.area_id)
            if ae is None:
                continue
            if getattr(ae, 'inactive', False):
                sub_ids = collect_descendants(agl.area_id, mm.area_elements)
                if not sub_ids:
                    continue
                for sub_id in sub_ids:
                    sub_elem = mm.area_elements.get(sub_id)
                    if sub_elem is None:
                        continue
                    sec_name = mm.area_assignments.get(sub_id, '')
                    if not sec_name:
                        continue
                    sec = mm.sections.get(sec_name)
                    if sec is None:
                        continue
                    mat = mm.materials.get(sec.material)
                    if mat is None or abs(mat.unit_weight) < 1e-12:
                        continue
                    thickness = getattr(sub_elem, 'thickness', 0.0) or 0.0
                    if thickness < 1e-12:
                        continue
                    corner_pts = []
                    for nid in sub_elem.node_ids:
                        nd = mm.nodes.get(nid)
                        if nd is None:
                            break
                        corner_pts.append((nd.x, nd.y, nd.z))
                    if len(corner_pts) < 3:
                        continue
                    area_mag = polygon_area_3d(corner_pts)
                    if area_mag < 1e-12:
                        continue
                    sw_per_area = thickness * mat.unit_weight
                    total_fz = sw_per_area * area_mag * abs(agl.multiplier_z) * mult
                    mass = total_fz / g
                    n_c = len(sub_elem.node_ids)
                    for nid in sub_elem.node_ids:
                        node_mass[nid] = node_mass.get(nid, 0.0) + mass / n_c
                continue
            sec_name = mm.area_assignments.get(agl.area_id, '')
            if not sec_name:
                continue
            sec = mm.sections.get(sec_name)
            if sec is None:
                continue
            mat = mm.materials.get(sec.material)
            if mat is None or abs(mat.unit_weight) < 1e-12:
                continue
            thickness = getattr(ae, 'thickness', 0.0) or 0.0
            if thickness < 1e-12:
                continue
            corner_pts = []
            for nid in ae.node_ids:
                nd = mm.nodes.get(nid)
                if nd is None:
                    break
                corner_pts.append((nd.x, nd.y, nd.z))
            if len(corner_pts) < 3:
                continue
            area_mag = polygon_area_3d(corner_pts)
            if area_mag < 1e-12:
                continue
            sw_per_area = thickness * mat.unit_weight
            total_fz = sw_per_area * area_mag * abs(agl.multiplier_z) * mult
            mass = total_fz / g
            n_c = len(ae.node_ids)
            for nid in ae.node_ids:
                node_mass[nid] = node_mass.get(nid, 0.0) + mass / n_c

    def _mass_from_area_uniform(self, mm, node_mass, g, lp_name, mult):
        """Add mass from area uniform loads in the given pattern."""
        from ..model.tree_utils import collect_descendants
        for aul in getattr(mm, 'area_uniform_loads', []):
            if aul.pattern != lp_name:
                continue
            ae = mm.area_elements.get(aul.area_id)
            if ae is None:
                continue
            if getattr(ae, 'inactive', False):
                sub_ids = collect_descendants(aul.area_id, mm.area_elements)
                if not sub_ids:
                    continue
                for sub_id in sub_ids:
                    sub_elem = mm.area_elements.get(sub_id)
                    if sub_elem is None:
                        continue
                    corner_pts = []
                    for nid in sub_elem.node_ids:
                        nd = mm.nodes.get(nid)
                        if nd is None:
                            break
                        corner_pts.append((nd.x, nd.y, nd.z))
                    if len(corner_pts) < 3:
                        continue
                    area_mag = polygon_area_3d(corner_pts)
                    if area_mag < 1e-12:
                        continue
                    pressure = abs(aul.value)
                    total_force = pressure * area_mag * mult
                    mass = total_force / g
                    n_c = len(sub_elem.node_ids)
                    for nid in sub_elem.node_ids:
                        node_mass[nid] = node_mass.get(nid, 0.0) + mass / n_c
                continue
            corner_pts = []
            for nid in ae.node_ids:
                nd = mm.nodes.get(nid)
                if nd is None:
                    break
                corner_pts.append((nd.x, nd.y, nd.z))
            if len(corner_pts) < 3:
                continue
            area_mag = polygon_area_3d(corner_pts)
            if area_mag < 1e-12:
                continue
            pressure = abs(aul.value)
            total_force = pressure * area_mag * mult
            mass = total_force / g
            n_c = len(ae.node_ids)
            for nid in ae.node_ids:
                node_mass[nid] = node_mass.get(nid, 0.0) + mass / n_c

    # ═══════════════════════════════════════════════════════════════
    # Modal and response-spectrum analysis
    # ═══════════════════════════════════════════════════════════════

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
                r = damp_ratios[i] if i < len(damp_ratios) else 0.05
                s = damp_ratios[j] if j < len(damp_ratios) else 0.05
                rho = (8.0 * math.sqrt(r * s) * (r + s) * math.pow(omega[i] * omega[j], 1.5)) / \
                      ((1.0 - (omega[i] / omega[j]) ** 2) ** 2 +
                       4.0 * r * s * omega[i] * omega[j] / (omega[i] + omega[j]) ** 2 *
                       (1.0 + (omega[i] / omega[j]) ** 2))
                total += modal_values[i] * modal_values[j] * rho
        return math.sqrt(max(total, 0.0))

    def run_modal_analysis(self, num_modes: int = 30,
                           print_results: bool = True,
                           eigen_solver: str = "default",
                           g: Optional[float] = None) -> Dict[str, Any]:
        """Run eigenvalue / modal analysis and return results.

        Requires that seismic masses have been assigned (call
        :meth:`compute_seismic_masses` first) and the domain has been
        built via :meth:`build_domain`.

        Args:
            num_modes: Number of eigenvalues to solve for.
            print_results: If True, print a modal properties table.
            eigen_solver: Solver strategy.

                ``"default"``
                    ARPACK (fast), fallback to fullGenLapack.
                ``"fullGenLapack"``
                    Robust but slow for large models.
                ``"genBandArpack"``
                    Generalized banded ARPACK — requires a Ritz pre-step.
                ``"symmBandLapack"``
                    Symmetric banded Lapack solver.
                ``"ritz"``
                    Gravity pre-step then ARPACK.
            g: Gravitational acceleration.  ``None`` = auto-detect.

        Returns:
            Dictionary with keys:

            * ``'eigenvalues'`` — list of eigenvalues (omega^2).
            * ``'periods'`` — list of natural periods (s).
            * ``'frequencies'`` — list of natural frequencies (Hz).
            * ``'modal_props'`` — the full ``ops.modalProperties()`` dict.
            * ``'num_modes'`` — number of converged modes.
        """
        if self.config.get('verbose'):
            print(f"Running modal analysis for {num_modes} modes...")

        from ..utils import g_from_units
        if g is None:
            g = g_from_units(self.mesh_model.units)

        # ── Ensure seismic masses are present ────────────────────
        _has_mass = False
        for t in ops.getNodeTags():
            try:
                m = ops.nodeMass(t)
                if sum(abs(x) for x in m) > 1e-12:
                    _has_mass = True
                    break
            except Exception:
                pass
        if not _has_mass:
            _stored_g = getattr(self, '_mass_g', None)
            _active_g = g if g is not None else _stored_g
            self.compute_seismic_masses(g=_active_g)

        # ── Ritz / pre-load nudge ────────────────────────────────
        _needs_nudge = eigen_solver in ("genBandArpack", "ritz")
        if _needs_nudge:
            if self.config.get('verbose'):
                print("  Ritz pre-step (static gravity)...")
            # Run a self-weight gravity load step
            self.create_loads(pattern_scales={"Self weight": 1.0})
            try:
                if self._edge_constraint_method == 'penalty':
                    ops.constraints('Penalty', 1.0e12, 1.0e12)
                else:
                    ops.constraints('Transformation')
                ops.numberer('RCM')
                ops.system(self.config.get('solver_system', 'BandGen'))
                ops.test('NormDispIncr', 1e-3, 5, 0)
                _algorithms = ['Newton', 'NewtonLineSearch',
                               'ModifiedNewton', 'KrylovNewton']
                _ok = -1
                for _alg in _algorithms:
                    try:
                        ops.algorithm(_alg)
                    except Exception:
                        continue
                    ops.integrator('LoadControl', 1.0)
                    ops.analysis('Static')
                    _ok = ops.analyze(1)
                    if _ok == 0:
                        break
                if _ok != 0 and self.config.get('verbose'):
                    print("  ⚠ Ritz pre-step did not converge — "
                          "continuing with zero initial state")
            except Exception:
                if self.config.get('verbose'):
                    print("  ⚠ Ritz pre-step failed — continuing")

        # ── Set constraint handler for eigen analysis ────────────
        try:
            if self._edge_constraint_method == 'penalty':
                ops.constraints('Penalty', 1.0e12, 1.0e12)
            else:
                ops.constraints(self.config.get('solver_constraints',
                                                'Transformation'))
            ops.numberer('RCM')
            ops.system(self.config.get('solver_system', 'BandGen'))
        except Exception:
            pass

        # ── Eigenvalue solver ────────────────────────────────────
        eigenvals_all = []
        _solver_map = {
            "genBandArpack": "-genBandArpack",
            "symmBandLapack": "-symmBandLapack",
            "fullGenLapack": "-fullGenLapack",
            "default": None,
        }
        solver_flag = _solver_map.get(eigen_solver)
        if solver_flag is not None:
            try:
                eigenvals_all = ops.eigen(solver_flag, num_modes)
            except Exception:
                eigenvals_all = []
            if not eigenvals_all:
                try:
                    eigenvals_all = ops.eigen(num_modes)
                except Exception:
                    eigenvals_all = []
                if not eigenvals_all:
                    try:
                        eigenvals_all = ops.eigen('-fullGenLapack', num_modes)
                    except Exception:
                        eigenvals_all = []
        else:
            try:
                eigenvals_all = ops.eigen(num_modes)
            except Exception:
                eigenvals_all = []
            if not eigenvals_all:
                try:
                    eigenvals_all = ops.eigen('-fullGenLapack', num_modes)
                except Exception:
                    eigenvals_all = []

        eigenvals = [ev for ev in eigenvals_all if ev > 1e-12]
        n_modes = len(eigenvals)
        if n_modes < num_modes and self.config.get('verbose'):
            print(f"  Warning: only {n_modes} positive eigenvalues out of "
                  f"{num_modes}.  Proceeding with {n_modes} modes.")

        periods = [2.0 * math.pi / math.sqrt(ev) for ev in eigenvals]
        frequencies = [math.sqrt(ev) / (2.0 * math.pi) for ev in eigenvals]

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
            if modal_props:
                try:
                    total_mass = modal_props.get('totalFreeMass', [0])[0]
                    print(f"Total translational mass (free DOFs): "
                          f"{total_mass:.2f} tonnes\n")
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
                        print(f"{i+1:5d} {frequencies[i]:10.4f} "
                              f"{periods[i]:10.4f} {mx:12.2f} {my:12.2f} "
                              f"{mz:12.2f} {rx:6.2f}% {ry:6.2f}% {rz:6.2f}%")
                except Exception:
                    pass
            else:
                print(f"{'Mode':>5} {'Period(s)':>10} {'Freq(Hz)':>10}")
                print("-" * 30)
                for i in range(n_modes):
                    print(f"{i+1:5d} {periods[i]:10.4f} {frequencies[i]:10.4f}")

        return results

    def run_response_spectrum_analysis(
        self,
        num_modes: int,
        modal_periods: List[float],
        spectrum_periods: List[float],
        spectrum_accels: List[float],
        direction: str = 'X',
        damping_ratio: float = 0.05,
        T_rigid: Optional[float] = None,
        print_results: bool = True,
    ) -> Dict[str, Any]:
        """Run a response‑spectrum analysis using CQC modal combination.

        Performs mode‑by‑mode RS analysis using OpenSees'
        ``responseSpectrumAnalysis``, then combines with CQC.

        Args:
            num_modes: Number of modes to include.
            modal_periods: Natural periods of each mode (s).
            spectrum_periods: Period axis of the response spectrum (s).
            spectrum_accels: Spectral acceleration values (m/s^2).
            direction: Excitation direction — ``'X'``, ``'Y'``, or ``'Z'``.
            damping_ratio: Damping ratio for CQC correlation.
            T_rigid: Rigid cut-off period (s). ``None`` = no cut-off.
            print_results: If True, print a summary table.

        Returns:
            Dictionary with ``modal_base_shear``, ``modal_base_moment``,
            ``base_shear_cqc``, ``base_shear_srss``, ``base_moment_cqc``,
            ``base_moment_srss``, ``modal_periods``.
        """
        if self.config.get('verbose'):
            print(f"Running response spectrum analysis (dir={direction})...")

        num_modes = min(num_modes, len(modal_periods))
        if num_modes == 0:
            raise ValueError("No modal periods available for RS analysis")

        omega = [2.0 * math.pi / T if T > 0 else 0.0 for T in modal_periods]
        damp_ratios = [damping_ratio] * num_modes

        SPECTRUM_TS_TAG = 9999
        try:
            ops.remove('timeSeries', SPECTRUM_TS_TAG)
        except Exception:
            pass
        ops.timeSeries('Path', SPECTRUM_TS_TAG,
                       '-time', *spectrum_periods,
                       '-values', *spectrum_accels)

        modal_base_shear = []
        modal_base_moment = []
        dof = {'X': 1, 'Y': 2, 'Z': 3}[direction]

        dof_idx = {'X': 0, 'Y': 1, 'Z': 2}[direction]
        base_nodes = {
            nid for nid, r in self.mesh_model.restraints.items()
            if len(r.dofs) > dof_idx and r.dofs[dof_idx] == 1
        }

        elements = self.mesh_model.frame_elements
        base_elements = []
        for eid, elem in elements.items():
            if getattr(elem, 'inactive', False):
                continue
            nd_i = self.mesh_model.nodes.get(elem.node_i)
            nd_j = self.mesh_model.nodes.get(elem.node_j)
            if nd_i is None or nd_j is None:
                continue
            if elem.node_i in base_nodes and elem.node_j not in base_nodes:
                base_elements.append((elem.elem_tag, 'i'))
            elif elem.node_j in base_nodes and elem.node_i not in base_nodes:
                base_elements.append((elem.elem_tag, 'j'))

        for mode in range(1, num_modes + 1):
            ops.responseSpectrumAnalysis(SPECTRUM_TS_TAG, dof, '-mode', mode)

            v_base = 0.0
            m_base = 0.0
            dof_map = {'X': (0, 4), 'Y': (1, 5), 'Z': (2, 3)}
            f_idx, m_idx = dof_map[direction]

            for eid, end in base_elements:
                try:
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
            print(f"{'Mode':>5} {'Period(s)':>10} {'Shear (kN)':>14} "
                  f"{'Moment (kN-m)':>16}")
            print("-" * 48)
            for i, (T, v, m) in enumerate(zip(modal_periods[:num_modes],
                                                modal_base_shear,
                                                modal_base_moment)):
                print(f"{i+1:5d} {T:10.4f} {v:14.2f} {m:16.2f}")
            print("-" * 48)
            print(f"{'CQC':>5} {'':>10} {base_shear_cqc:14.2f} "
                  f"{base_moment_cqc:16.2f}")
            print(f"{'SRSS':>5} {'':>10} {base_shear_srss:14.2f} "
                  f"{base_moment_srss:16.2f}")
            print()

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

    # ═══════════════════════════════════════════════════════════════
    # Export
    # ═══════════════════════════════════════════════════════════════

    def export_results(self,
                      filepath: str,
                      static_results: Optional[Dict[str, Any]] = None,
                      modal_result: Optional[Dict[str, Any]] = None,
                      mode_shapes: Optional[Dict] = None,
                      rs_results: Optional[Dict[str, Dict]] = None,
                      fmt: str = "npz",
                      ) -> str:
        """Export model geometry and analysis results to a unified file.

        Delegates to :func:`~fea_toolkit.io.unified_writer.write_results`
        using the builder's ``mesh_model`` and the provided results.

        Args:
            filepath: Output file path (``.npz`` or ``.h5``).
            static_results: Dict from :meth:`run_static_analysis`.
            modal_result: Dict from :meth:`~fea_toolkit.opensees.builder.OpenSeesBuilder.run_modal_analysis`.
            mode_shapes: Mode shape eigenvectors ``{mode_idx: {tag: (dx,dy,dz)}}``.
            rs_results: Response-spectrum results dict.
            fmt: ``"npz"`` (default) or ``"h5"``.

        Returns:
            Absolute path to the written file.
        """
        from ..io.unified_writer import write_results

        return write_results(
            path=filepath,
            mesh_model=self.mesh_model,
            static_results=static_results,
            modal_result=modal_result,
            mode_shapes=mode_shapes,
            rs_results=rs_results,
            fmt=fmt,
            config=self.config,
        )
