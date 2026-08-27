"""Analysis-builder mixin: element creation."""

import copy
import logging
import math
from typing import Optional

import numpy as np
import openseespy.opensees as ops

from ..model.geometry import get_SAP_vecxz
from ..model.sap_data import FrameElement, Node
from ..utils import (
    DEFAULT_RHO_MC_SI,
    g_from_units,
    length_scale_factor,
    mass_density_scale_factor,
)

logger = logging.getLogger(__name__)


class ElementMixin:
    """Frame/wall/shell element creation, brace selection, and lumped hinges."""

    def _create_wall_elements(self) -> None:
        """Create wall macro-elements from MeshModel.wall_elements.

        Dispatches on each :class:`~fea_toolkit.model.mesh_model.WallElement`
        ``element_type`` / ``material_type``:

        * ``SFI_MVLEM_3D`` / ``E_SFI_MVLEM_3D`` — per-fibre FSAM nD
          materials::

              element <TYPE> eleTag iNode jNode kNode lNode m \\
                  -thick *t -width *w -mat *matTags <-CoR c> ...

        * ``MVLEM_3D`` — per-fibre uniaxial concrete + steel + shear::

              element MVLEM_3D eleTag iNode jNode kNode lNode m \\
                  -thick *t -width *w -rho *rho \\
                  -matConcrete *concTags -matSteel *steelTags \\
                  -matShear shearTag <-CoR c> ...

        Node IDs are resolved to tags via ``mesh_model.nodes``.  FSAM
        names resolve via ``_nd_material_tags`` (populated by
        :meth:`_create_fsam_materials`); uniaxial names via
        ``self.material_tags``.  This method runs **after**
        :meth:`_create_fsam_materials` and **before**
        :meth:`_create_shell_elements`.
        """
        if not self.mesh_model.wall_elements:
            return

        _nd_tags = getattr(self, "_nd_material_tags", {})
        created = 0
        for wall in self.mesh_model.wall_elements.values():
            elem_type = wall.element_type or wall.material_type

            # Resolve node IDs → tags
            node_tags = []
            skip = False
            for nid in wall.node_ids:
                node = self.mesh_model.nodes.get(nid)
                if node is None:
                    if self.config.get("verbose", False):
                        print(
                            f"  ⚠ Wall element '{wall.elem_id}': node "
                            f"'{nid}' not found in mesh — skipping"
                        )
                    skip = True
                    break
                node_tags.append(node.node_tag)
            if skip:
                continue

            if wall.material_type == "uniaxial":
                created += self._create_mvlem3d_wall(wall, node_tags, elem_type)
            else:
                created += self._create_fsam_wall(wall, node_tags, elem_type, _nd_tags)

        if self.config.get("verbose", False):
            print(f"  Created {created} wall element(s)")

    def _create_fsam_wall(self, wall, node_tags: list, elem_type: str, _nd_tags: dict) -> int:
        """Emit an SFI_MVLEM_3D / E_SFI_MVLEM_3D element (per-fibre FSAM)."""
        mat_tags = []
        for name in wall.fsam_material_names:
            tag = _nd_tags.get(name)
            if tag is None:
                logger.warning(
                    "  ⚠ Wall element '%s': FSAM nD material '%s' not found in "
                    "_nd_material_tags — skipping element",
                    wall.elem_id,
                    name,
                )
                return 0
            mat_tags.append(tag)

        args: list = [
            wall.elem_tag,
            *node_tags,
            wall.m,
            "-thick",
            *wall.thick,
            "-width",
            *wall.width,
            "-mat",
            *mat_tags,
            "-CoR",
            wall.CoR,
        ]
        if wall.ThickMod is not None:
            args.extend(["-ThickMod", wall.ThickMod])
        if wall.Poisson is not None:
            args.extend(["-Poisson", wall.Poisson])
        if wall.Density is not None:
            args.extend(["-Density", wall.Density])

        ops.element(elem_type, *args)
        if self.config.get("verbose", False):
            print(f"  {elem_type} tag={wall.elem_tag} nodes={node_tags} m={wall.m}")
        return 1

    def _create_mvlem3d_wall(self, wall, node_tags: list, elem_type: str) -> int:
        """Emit an MVLEM_3D element (per-fibre uniaxial concrete/steel + shear).

        When ``wall.rho`` is absent, the per-fibre mass density defaults
        to the referenced concrete's ``unit_weight`` divided by
        :func:`~fea_toolkit.utils.g_from_units` (unit-consistent with the
        model), else ``DEFAULT_RHO_MC_SI`` scaled to model units — never
        a hardcoded SI literal.
        """

        def _resolve(names) -> Optional[list]:
            tags = []
            for name in names or []:
                tag = self.material_tags.get(name)
                if tag is None:
                    return None
                tags.append(tag)
            return tags

        conc_tags = _resolve(wall.concrete_names)
        steel_tags = _resolve(wall.steel_names)
        shear_tag = self.material_tags.get(wall.shear_name) if wall.shear_name else None
        if wall.rho:
            rho = wall.rho
        else:
            # Unit-aware fallback density: prefer the wall's concrete
            # material unit weight (mass density = unit_weight / g), else
            # the SI default scaled to model units — never a
            # unit-specific literal like 2400 kg/m³.
            _conc_name = (wall.concrete_names or [None])[0]
            _conc_mat = self.mesh_model.materials.get(_conc_name) if _conc_name else None
            _uw = float(getattr(_conc_mat, "unit_weight", 0.0) or 0.0)
            if _uw > 0.0:
                _rho_default = _uw / g_from_units(self.mesh_model.units)
            else:
                _rho_default = DEFAULT_RHO_MC_SI * mass_density_scale_factor(self.mesh_model.units)
            rho = [_rho_default] * wall.m
        if conc_tags is None or steel_tags is None or shear_tag is None:
            logger.warning(
                "  ⚠ Wall element '%s': missing uniaxial material "
                "tag (concrete/steel/shear) — skipping element",
                wall.elem_id,
            )
            return 0

        args: list = [
            wall.elem_tag,
            *node_tags,
            wall.m,
            "-thick",
            *wall.thick,
            "-width",
            *wall.width,
            "-rho",
            *rho,
            "-matConcrete",
            *conc_tags,
            "-matSteel",
            *steel_tags,
            "-matShear",
            shear_tag,
            "-CoR",
            wall.CoR,
        ]
        if wall.ThickMod is not None:
            args.extend(["-ThickMod", wall.ThickMod])
        if wall.Poisson is not None:
            args.extend(["-Poisson", wall.Poisson])
        if wall.Density is not None:
            args.extend(["-Density", wall.Density])

        ops.element(elem_type, *args)
        if self.config.get("verbose", False):
            print(f"  {elem_type} tag={wall.elem_tag} nodes={node_tags} m={wall.m}")
        return 1

    def _build_frame_tag_map(self) -> None:
        """Pre-compute frame element tags before creating elements.

        Ensures shell element tag assignment can avoid clashing with
        frame element tags.
        """
        elements = self.mesh_model.frame_elements
        next_tag = 1
        self.frame_tag_map = {}
        used_tags: set[int] = set()
        for eid, elem in elements.items():
            if getattr(elem, "inactive", False):
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

        if self.config["verbose"]:
            print("Creating frame elements...")

        elements = self.mesh_model.frame_elements
        assignments = self.mesh_model.frame_assignments
        dist_loads = self.mesh_model.frame_dist_loads
        rigid_links: list[tuple] = []

        # Save canonical state on first brace subdivision so
        # _restore_brace_canonical_state() can restore it on repeated builds.
        # Use deep copies to prevent shared mutable state with the MeshModel.
        if (
            self.config.get("subdivide_braces")
            and self._brace_selection
            and not hasattr(self, "_brace_canonical")
        ):
            self._brace_canonical = {
                "frame_elements": copy.deepcopy(self.mesh_model.frame_elements),
                "frame_assignments": copy.deepcopy(self.mesh_model.frame_assignments),
                "nodes": copy.deepcopy(self.mesh_model.nodes),
                "frame_dist_loads": copy.deepcopy(self.mesh_model.frame_dist_loads),
            }

        # Brace subdivision (Approach A) — before element creation loop so
        # child sub-elements are processed by _add_beam_column below.
        if self.config.get("subdivide_braces") and self._brace_selection:
            n_seg = self.config.get("brace_n_segments", 4)
            imperf = self.config.get("brace_imperfection_ratio", 1.0 / 500.0)
            end_off = self.config.get("brace_end_offset", 0.0)
            nodes = self.mesh_model.nodes
            max_elem_tag = max((e.elem_tag for e in elements.values()), default=0)
            max_node_tag = max((nd.node_tag for nd in nodes.values()), default=0)
            try:
                max_ops_tag = max(ops.getEleTags(), default=0)
            except Exception:
                max_ops_tag = 0
            max_rigid_tag = max((r[3] for r in self._offset_rigid_links), default=0)
            next_tag = max(max_elem_tag, max_node_tag, max_ops_tag, max_rigid_tag) + 1
            elements, assignments, nodes, next_tag, rigid_links = subdivide_elements(
                elements,
                assignments,
                nodes,
                n_segments=n_seg,
                imperfection_ratio=imperf,
                brace_ids=self._brace_selection,
                end_offset=end_off,
                next_tag=next_tag,
            )
            self.mesh_model.frame_elements = elements
            self.mesh_model.frame_assignments = assignments
            self.mesh_model.nodes = nodes
            # Rebuild frame tag map so children get OpenSees tags
            self._build_frame_tag_map()
            # Create OpenSees nodes for subdivision / offset nodes
            for nd in nodes.values():
                if nd.node_tag not in self._created_node_tags:
                    ops.node(nd.node_tag, nd.x, nd.y, nd.z)
                    self._created_node_tags.add(nd.node_tag)
            # Redistribute distributed loads from subdivided braces to children
            # Each child gets a proportional share of the parent's load range.
            from ..model.sap_data import FrameDistributedLoad as _FDL

            new_dist_loads: list = []
            for ld in dist_loads:
                if ld.frame_id not in self._brace_selection:
                    new_dist_loads.append(ld)
                    continue
                # Parent was subdivided — distribute to each child
                parent = self.mesh_model.frame_elements.get(ld.frame_id)
                if parent is None or not hasattr(parent, "child_ids"):
                    new_dist_loads.append(ld)
                    continue
                total_len = ld.dist_b - ld.dist_a if ld.dist_b > ld.dist_a else 0.0
                n_child = len(parent.child_ids)
                for ci, child_id in enumerate(parent.child_ids):
                    child_start = ld.dist_a + total_len * (ci / n_child)
                    child_end = ld.dist_a + total_len * ((ci + 1) / n_child)
                    # Compute child-specific rdist values proportional to the
                    # child's segment within the parent's parametric range.
                    parent_rdist_range = ld.rdist_b - ld.rdist_a
                    child_rdist_a = ld.rdist_a + parent_rdist_range * (ci / n_child)
                    child_rdist_b = ld.rdist_a + parent_rdist_range * ((ci + 1) / n_child)
                    new_dist_loads.append(
                        _FDL(
                            pattern=ld.pattern,
                            frame_id=child_id,
                            direction=ld.direction,
                            load_type=ld.load_type,
                            shape=ld.shape,
                            val_a=ld.val_a,
                            val_b=ld.val_b,
                            rdist_a=child_rdist_a,
                            rdist_b=child_rdist_b,
                            dist_a=child_start,
                            dist_b=child_end,
                        )
                    )
            self.mesh_model.frame_dist_loads = new_dist_loads

        for eid, elem in elements.items():
            if getattr(elem, "inactive", False):
                continue

            tag = self.frame_tag_map.get(eid)
            if tag is None:
                continue

            self._add_beam_column(elem, tag, elements, assignments)

        # Rigid link section — created once and reused for both brace
        # subdivision links and frame-end offset links.
        if rigid_links or self._offset_rigid_links:
            all_sec_tags = set(self.section_tags.values())
            all_sec_tags.update(self._shell_sec_tags.values())
            all_sec_tags.update(self._shell_sec_variants.values())
            rigid_section_tag = max(all_sec_tags, default=0) + 1
            rigid_E = 2.0e14
            rigid_A = 1.0
            rigid_I = 1.0
            # Arbitrary large shear modulus — the exact ratio is irrelevant
            # for a numerical rigid link (an artificial stiffener, not a
            # physical material).  Keep G large so shear/torsion never become
            # the soft DOFs of the link.
            rigid_G = rigid_E / 2.6
            ops.section(
                "Elastic",
                rigid_section_tag,
                rigid_E,
                rigid_A,
                rigid_I,
                rigid_I,
                rigid_G,
                rigid_I,
            )
            self._rigid_section_tag = rigid_section_tag

        # Rigid links from brace subdivision
        if rigid_links:
            for _link_id, _node_i_id, _node_j_id, link_tag in rigid_links:
                nd_i = self.mesh_model.nodes.get(_node_i_id)
                nd_j = self.mesh_model.nodes.get(_node_j_id)
                if nd_i is None or nd_j is None:
                    continue
                ni_tag = nd_i.node_tag
                nj_tag = nd_j.node_tag
                dx = float(nd_j.x - nd_i.x)
                dy = float(nd_j.y - nd_i.y)
                dz = float(nd_j.z - nd_i.z)
                vecxz = get_SAP_vecxz(np.array([dx, dy, dz]), 0.0)
                ops.geomTransf("Linear", link_tag, *vecxz)
                ops.element(
                    "elasticBeamColumn",
                    link_tag,
                    ni_tag,
                    nj_tag,
                    self._rigid_section_tag,
                    link_tag,
                    "-mass",
                    0.0,
                )
                self._rigid_link_elems[_link_id] = link_tag

        # Rigid links from frame end offsets
        # The Preprocessor returns (link_id, node_i, node_j, link_tag) tuples.
        # node_i and node_j are string node IDs — resolve to numeric tags.
        if self._offset_rigid_links:
            _mpc = self.config.get("rigid_link_mpc", False)
            for _link_id, _node_i_id, _node_j_id, link_tag in self._offset_rigid_links:
                nd_i = self.mesh_model.nodes.get(_node_i_id)
                nd_j = self.mesh_model.nodes.get(_node_j_id)
                if nd_i is None or nd_j is None:
                    continue
                ni_tag = nd_i.node_tag
                nj_tag = nd_j.node_tag
                if _mpc:
                    # MPC rigid link (ops.rigidLink "beam"): the original
                    # joint node is the master, the offset node the slave.
                    # Avoids the ill-conditioning of very stiff elastic
                    # links under PDelta (which fails at the gravity stage).
                    _off_i = _node_i_id.endswith(("_off_i", "_off_j"))
                    _off_j = _node_j_id.endswith(("_off_i", "_off_j"))
                    if _off_i and not _off_j:
                        _master_tag, _slave_tag = nj_tag, ni_tag
                    elif _off_j and not _off_i:
                        _master_tag, _slave_tag = ni_tag, nj_tag
                    else:  # defensive: neither id is an offset node
                        _master_tag, _slave_tag = ni_tag, nj_tag
                    ops.rigidLink("beam", _master_tag, _slave_tag)
                    self._rigid_link_elems[_link_id] = _slave_tag
                    continue
                # Compute vecxz for vertical/horizontal links (same convention as _add_beam_column)
                dx = float(nd_j.x - nd_i.x)
                dy = float(nd_j.y - nd_i.y)
                dz = float(nd_j.z - nd_i.z)
                vecxz = get_SAP_vecxz(np.array([dx, dy, dz]), 0.0)
                ops.geomTransf("Linear", link_tag, *vecxz)
                ops.element(
                    "elasticBeamColumn",
                    link_tag,
                    ni_tag,
                    nj_tag,
                    self._rigid_section_tag,
                    link_tag,
                    "-mass",
                    0.0,
                )
                self._rigid_link_elems[_link_id] = link_tag

        if self.config["verbose"]:
            n = len([e for e in elements.values() if not getattr(e, "inactive", False)])
            print(f"  Created {n} frame elements")

    def _add_beam_column(self, elem, tag, elements, assignments):
        """Add a single beam-column element to the OpenSees domain."""
        ni = self.mesh_model.nodes.get(elem.node_i)
        nj = self.mesh_model.nodes.get(elem.node_j)
        if ni is None or nj is None:
            return

        sec_name = assignments.get(elem.elem_id, "")

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

        # ── Brace truss elements ────────────────────────────────
        # When brace_truss is active, an element becomes a Truss with a
        # per-element Hysteretic material when (a) it is explicitly
        # selected via set_brace_selection / rebuild_with_fiber_sections
        # (authoritative — custom BRBs, diagonal I-sections), or (b) its
        # section is a recognised brace section / brace_sections name AND
        # its geometric role is 'brace' (so horizontal pipes and ordinary
        # beams stay flexural).
        _is_brace_truss = False
        if self.config.get("brace_truss") and hasattr(self, "_truss_mat_tags"):
            if self._brace_selection is not None:
                _is_brace_truss = (
                    elem.elem_id in self._brace_selection
                    or getattr(elem, "parent_id", None) in self._brace_selection
                )
            else:
                _explicit_sec = self.config.get("brace_sections") is not None
                _is_brace_truss = sec_name in self._truss_mat_tags and (
                    _explicit_sec or self._frame_element_types.get(elem.elem_id) == "brace"
                )
            # The truss branch indexes _truss_areas / _truss_Fy / _truss_E
            # by section name — only proceed when sec_name is registered
            # with valid truss properties.  An unregistered section (e.g.
            # skipped by the registration guard for a near-zero area) falls
            # through to the normal beam-column handling below, while
            # registered brace sections keep the truss + Hysteretic path.
            _is_brace_truss = _is_brace_truss and (
                sec_name in self._truss_areas
                and self._truss_areas.get(sec_name, 0.0) > 1e-12
                and sec_name in self._truss_Fy
                and sec_name in self._truss_E
            )
        if _is_brace_truss:
            A = self._truss_areas[sec_name]
            Fy = self._truss_Fy[sec_name]
            E_sec = self._truss_E[sec_name]
            # Per-element Hysteretic material using actual element length
            # for Euler buckling — each brace gets its own buckling load.
            _L_brace = math.sqrt((nj.x - ni.x) ** 2 + (nj.y - ni.y) ** 2 + (nj.z - ni.z) ** 2)
            eps_y = Fy / E_sec
            s1p, e1p = Fy, eps_y
            s2p, e2p = Fy * 1.01, eps_y + 0.01
            s3p, e3p = Fy * 1.02, eps_y + 0.05
            _sec = self.mesh_model.sections.get(sec_name)
            _I_min = getattr(_sec, "I22", 0.0) or getattr(_sec, "I33", 0.0) or 1e-6
            _P_cr = (math.pi**2 * E_sec * _I_min) / (_L_brace**2)
            sig_cr = _P_cr / A if A > 0 else Fy * 0.3
            eps_cr = sig_cr / E_sec
            s1n, e1n = -sig_cr, -eps_cr
            s2n, e2n = -sig_cr * 0.2, -eps_cr - 0.01
            s3n, e3n = -sig_cr * 0.1, -eps_cr - 0.05
            mat_tag = self._truss_mat_counter
            self._truss_mat_counter += 1
            ops.uniaxialMaterial(
                "Hysteretic",
                mat_tag,
                s1p,
                e1p,
                s2p,
                e2p,
                s3p,
                e3p,
                s1n,
                e1n,
                s2n,
                e2n,
                s3n,
                e3n,
                1.0,
                1.0,
                0.0,
                0.0,
                0.0,
            )
            self.material_tags[f"truss_{sec_name}_{tag}"] = mat_tag
            ops.element("Truss", tag, ni.node_tag, nj.node_tag, A, mat_tag)
            return

        # Geometric transformation
        angle = getattr(elem, "angle", 0.0)
        vecxz = get_SAP_vecxz(np.array([nj.x - ni.x, nj.y - ni.y, nj.z - ni.z]), angle)
        transf_type = self.config.get("geom_transf_type", "Linear")
        transf_tag = tag
        ops.geomTransf(transf_type, transf_tag, *vecxz)
        self._transf_tags[tag] = transf_tag

        # Element
        elem_type = self.config["element_type"]
        n_ip = self.config.get("num_int_pts", 3)
        if elem_type == "elasticBeamColumn":
            ops.element(elem_type, tag, *[ni.node_tag, nj.node_tag], sec_tag, transf_tag)
        else:
            int_tag = tag + 10000
            if self.config.get("beam_integration", "Lobatto") == "Lobatto":
                ops.beamIntegration("Lobatto", int_tag, sec_tag, n_ip)
            else:
                # HingeRadau with explicit hinge lengths
                _L_hinge = math.sqrt((nj.x - ni.x) ** 2 + (nj.y - ni.y) ** 2 + (nj.z - ni.z) ** 2)
                _sec = self.mesh_model.sections.get(sec_name)
                if _sec is not None:
                    from fea_toolkit.model.checks import compute_hinge_length

                    Lp = compute_hinge_length(_sec, _L_hinge)
                else:
                    Lp = 0.1 * _L_hinge
                ops.beamIntegration("HingeRadau", int_tag, sec_tag, Lp, sec_tag, Lp, sec_tag)
            ops.element(elem_type, tag, *[ni.node_tag, nj.node_tag], transf_tag, int_tag)

    def _restore_brace_canonical_state(self) -> None:
        """Restore canonical frame/assignment/node/dist-load state for brace
        subdivision, so repeated ``build_domain()`` or
        ``rebuild_with_fiber_sections()`` calls always subdivide the
        original (un‑subdivided) elements rather than already-subdivided
        ones.
        """
        if not hasattr(self, "_brace_canonical"):
            return
        snap = self._brace_canonical
        self.mesh_model.frame_elements = snap["frame_elements"]
        self.mesh_model.frame_assignments = snap["frame_assignments"]
        self.mesh_model.nodes = snap["nodes"]
        self.mesh_model.frame_dist_loads = snap["frame_dist_loads"]

    def _restore_hinge_canonical_state(self) -> None:
        """Restore canonical frame element endpoints and remove stale hinge nodes.

        Called at the start of :meth:`build_domain` (before
        :meth:`_create_nodes`) to prevent stale ``*_hinge_*`` nodes
        from a previous build cycle from being recreated.
        """
        if not hasattr(self, "_hinge_canonical_elements"):
            return
        # Remove any *_hinge_* nodes left from a previous build
        for nid in list(self.mesh_model.nodes.keys()):
            if nid.endswith(("_hinge_i", "_hinge_j")):
                del self.mesh_model.nodes[nid]
        # Restore canonical element endpoints and assignments
        for eid, elem in self.mesh_model.frame_elements.items():
            if eid in self._hinge_canonical_elements:
                ni, nj = self._hinge_canonical_elements[eid]
                elem.node_i = ni
                elem.node_j = nj
        self.mesh_model.frame_assignments = dict(self._hinge_canonical_assignments)

    def _restore_bond_canonical_state(self) -> None:
        """Restore canonical frame element endpoints and remove stale bond nodes.

        Called at the start of :meth:`build_domain` (before
        :meth:`_create_nodes`) to prevent stale ``*_bond_*`` nodes from a
        previous build cycle from being recreated, and to re-point elements
        to their original endpoints before ``_create_bond_slip_springs``
        re-instruments them.
        """
        if not hasattr(self, "_bond_canonical_elements"):
            return
        # Remove any *_bond_* nodes left from a previous build
        for nid in list(self.mesh_model.nodes.keys()):
            if nid.endswith(("_bond_i", "_bond_j")):
                del self.mesh_model.nodes[nid]
        # Restore canonical element endpoints
        for eid, elem in self.mesh_model.frame_elements.items():
            if eid in self._bond_canonical_elements:
                ni, nj = self._bond_canonical_elements[eid]
                elem.node_i = ni
                elem.node_j = nj

    def set_brace_selection(self, brace_ids: set, end_offset: float = 0.0) -> None:
        """Mark specific frame elements as braces for subdivision.

        Call **before** :meth:`build_domain` or
        :meth:`rebuild_with_fiber_sections`.  The elements identified by
        *brace_ids* will be subdivided into *brace_n_segments* segments
        with an initial imperfection (Approach A — subdivided element
        with initial geometric imperfection to capture buckling).

        Args:
            brace_ids: Set of frame element ID strings to treat as braces.
            end_offset: Distance from each working point to the gusset
                plate face (model length units).  Creates rigid link
                segments between the working point and the brace
                physical end.  Default 0.0 (no offset).
        """
        self._brace_selection = brace_ids
        self.config["subdivide_braces"] = True
        # Always clear first so a subsequent call with end_offset=0.0
        # does not retain a previous positive value.
        self.config.pop("brace_end_offset", None)
        if end_offset > 0:
            self.config["brace_end_offset"] = end_offset

    def check_brace_buckling(
        self,
        brace_ids: Optional[set] = None,
        K: float = 1.0,
        axial_demand: Optional[dict[str, float]] = None,
        print_results: bool = True,
    ) -> dict[str, dict[str, float]]:
        """Check selected braces against Euler buckling.

        Delegates to :func:`fea_toolkit.model.checks.check_brace_buckling`.

        Args:
            brace_ids: Set of element IDs to check.  Defaults to
                the stored ``_brace_selection``.
            K: Effective length factor (default 1.0).
            axial_demand: Optional ``{elem_id: axial_force_N}`` dict.
            print_results: If True, print a summary table.

        Returns:
            Dict of ``{elem_id: {P_cr, P_demand, ratio, slenderness, ...}}``.
        """
        from ..model.checks import check_brace_buckling as _check_buckling

        if brace_ids is None:
            brace_ids = self._brace_selection or set()
        return _check_buckling(self.mesh_model, brace_ids, K, axial_demand, print_results)

    def _create_lumped_hinges(self) -> None:
        """Replace frame elements with lumped plasticity hinges.

        Activated via ``config['hinge_model'] = 'lumped'``.

        Each frame element is split into::

            structural_node_i → hinge_i → elastic_mid → hinge_j → structural_node_j

        Coincident hinge nodes sit at the same coordinates.  Translation
        DOFs (1,2,3) are tied with ``equalDOF`` so only rotations (4,5,6)
        are released across the zero-length hinge elements.

        Hinge backbones use ``Hysteretic`` materials matched to ASCE 41
        rotation limits.
        """
        if self.config.get("hinge_model") != "lumped":
            return

        # ── Idempotency: preserve canonical state on first call ────────
        # Save canonical endpoints on first call; restoration is handled
        # by _restore_hinge_canonical_state() in build_domain().
        if not hasattr(self, "_hinge_canonical_elements"):
            self._hinge_canonical_elements = {
                eid: (elem.node_i, elem.node_j)
                for eid, elem in self.mesh_model.frame_elements.items()
                if not getattr(elem, "inactive", False)
            }
            self._hinge_canonical_assignments = dict(self.mesh_model.frame_assignments)

        elements = self.mesh_model.frame_elements
        assignments = self.mesh_model.frame_assignments

        next_node_tag = max((nd.node_tag for nd in self.mesh_model.nodes.values()), default=0) + 1
        # Consider existing OpenSees element tags (shells, rigid links already
        # created) and reserved offset-rigid-link tags to avoid collisions.
        try:
            max_ops_tag = max(ops.getEleTags(), default=0)
        except Exception:
            max_ops_tag = 0
        max_rigid_tag = max((r[3] for r in self._offset_rigid_links), default=0)
        next_tag = (
            max(
                max((e.elem_tag for e in elements.values() if not e.inactive), default=0),
                max_ops_tag,
                max_rigid_tag,
                max(self.frame_tag_map.values(), default=0),
            )
            + 1
        )
        # Separate counter for hinge section/material tags, seeded high
        # to avoid collision with existing tags.
        hinge_tag_base = (
            max((v for v in self.section_tags.values()), default=0) + len(self.section_tags) + 100
        )
        hinge_sec_tag = hinge_tag_base
        hinge_mat_tag = hinge_tag_base + len(self.section_tags) + 1

        new_elements: dict[str, FrameElement] = {}
        new_assignments: dict[str, str] = {}

        for eid, elem in list(elements.items()):
            if elem.inactive:
                new_elements[eid] = elem
                if eid in assignments:
                    new_assignments[eid] = assignments[eid]
                continue

            sec_name = assignments.get(eid) if assignments else None
            if not sec_name or sec_name not in self.section_tags:
                new_elements[eid] = elem
                if eid in assignments:
                    new_assignments[eid] = assignments[eid]
                continue

            ni = self.mesh_model.nodes.get(elem.node_i)
            nj = self.mesh_model.nodes.get(elem.node_j)
            if ni is None or nj is None:
                new_elements[eid] = elem
                if eid in assignments:
                    new_assignments[eid] = assignments[eid]
                continue

            L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
            if L < 1e-12:
                new_elements[eid] = elem
                if eid in assignments:
                    new_assignments[eid] = assignments[eid]
                continue

            sec = self.mesh_model.sections.get(sec_name)
            if sec is None:
                new_elements[eid] = elem
                if eid in assignments:
                    new_assignments[eid] = assignments[eid]
                continue

            # --- Create coincident hinge nodes ---
            hinge_i_id = f"{eid}_hinge_i"
            hinge_j_id = f"{eid}_hinge_j"
            hinge_i_tag = next_node_tag
            next_node_tag += 1
            hinge_j_tag = next_node_tag
            next_node_tag += 1

            self.mesh_model.nodes[hinge_i_id] = Node(
                node_id=hinge_i_id,
                node_tag=hinge_i_tag,
                x=ni.x,
                y=ni.y,
                z=ni.z,
            )
            self.mesh_model.nodes[hinge_j_id] = Node(
                node_id=hinge_j_id,
                node_tag=hinge_j_tag,
                x=nj.x,
                y=nj.y,
                z=nj.z,
            )

            # Create OpenSees nodes for coincident hinge nodes
            ops.node(hinge_i_tag, ni.x, ni.y, ni.z)
            ops.node(hinge_j_tag, nj.x, nj.y, nj.z)
            self._created_node_tags.update([hinge_i_tag, hinge_j_tag])

            # Tie translation DOFs between structural and hinge nodes
            ops.equalDOF(ni.node_tag, hinge_i_tag, 1, 2, 3)
            ops.equalDOF(nj.node_tag, hinge_j_tag, 1, 2, 3)

            # --- Create Hysteretic hinge section ---
            mat = self.mesh_model.materials.get(sec.material)

            # Defensive defaults for nullable section values — initialised
            # before the concrete guard so they are guaranteed bound for
            # the hinge backbone computation below.
            Z33 = getattr(sec, "Z33", None) or 0.0
            Z22 = getattr(sec, "Z22", None) or 0.0
            I33 = getattr(sec, "I33", None) or 0.0
            I22 = getattr(sec, "I22", None) or 0.0
            A_val = getattr(sec, "A", None) or 0.0
            J_val = getattr(sec, "J", None) or 0.0
            Fy = mat.Fy if mat and mat.Fy and mat.Fy > 0 else 2.5e8
            E = mat.E_mod if mat and mat.E_mod > 0 else 2.0e11
            G = mat.G_mod if mat and mat.G_mod and mat.G_mod > 0 else 0.4 * E

            # ── Concrete guard ──────────────────────────────────────
            # Concrete sections fire a warning (reinforcement data not
            # available) but still fall through to create elastic hinges
            # using the defaults initialised above.
            if mat and mat.type and "concrete" in mat.type.lower():
                import warnings

                warnings.warn(
                    f"Lumped hinges for concrete sections require reinforcement "
                    f"data not available in generic Section/Material model. "
                    f"Section '{sec_name}', material '{sec.material}' — "
                    f"using elastic moment defaults.",
                )

            # Compute yield moments from section geometry
            if Z33 > 0:
                My = Fy * Z33
            elif I33 > 0 and A_val > 0:
                d_eff = 2.0 * math.sqrt(I33 / A_val)  # 2× radius of gyration
                My = Fy * (I33 / max(d_eff * 0.5, 1e-6))
            else:
                My = Fy * 1e-4  # Minimal fallback
            if Z22 > 0:
                My_weak = Fy * Z22
            elif I22 > 0 and A_val > 0:
                d_eff = 2.0 * math.sqrt(I22 / A_val)
                My_weak = Fy * (I22 / max(d_eff * 0.5, 1e-6))
            else:
                My_weak = Fy * 1e-4

            # ASCE 41 plastic hinge length for yield rotation scaling
            from ..model.checks import compute_asce41_hinge_length

            Lp = compute_asce41_hinge_length(self.mesh_model, sec_name, L)
            theta_y = (
                (My * Lp) / (max(6.0 * E * max(I33, 1e-12), 1e-12))
                if E * max(I33, 1e-12) > 0
                else 0.005
            )
            theta_y_weak = (
                (My_weak * Lp) / (max(6.0 * E * max(I22, 1e-12), 1e-12))
                if E * max(I22, 1e-12) > 0
                else 0.005
            )
            theta_cap = theta_y * 6.0
            theta_cap_weak = theta_y_weak * 6.0

            # Axial material (elastic)
            ops.uniaxialMaterial("Elastic", hinge_mat_tag, max(A_val, 1e-6) * E / L)
            # Strong-axis moment (Hysteretic backbone)
            ops.uniaxialMaterial(
                "Hysteretic",
                hinge_mat_tag + 1,
                My,
                theta_y,
                My * 1.1,
                theta_cap,
                -My,
                -theta_y,
                -My * 1.1,
                -theta_cap,
                1.0,
                1.0,
                0.0,
                0.0,
                0.0,
            )
            # Weak-axis moment
            ops.uniaxialMaterial(
                "Hysteretic",
                hinge_mat_tag + 2,
                My_weak,
                theta_y_weak,
                My_weak * 1.1,
                theta_cap_weak,
                -My_weak,
                -theta_y_weak,
                -My_weak * 1.1,
                -theta_cap_weak,
                1.0,
                1.0,
                0.0,
                0.0,
                0.0,
            )
            # Torsion (elastic — no inelastic torsion expected)
            ops.uniaxialMaterial(
                "Elastic", hinge_mat_tag + 3, G * max(J_val, 1e-6) / L if J_val else G * 1e-6 / L
            )

            ops.section(
                "Aggregator",
                hinge_sec_tag,
                hinge_mat_tag,
                "P",
                hinge_mat_tag + 1,
                "Mz",
                hinge_mat_tag + 2,
                "My",
                hinge_mat_tag + 3,
                "T",
            )
            hinge_sec_tag += 1
            hinge_mat_tag += 4

            # Get local axes for element orientation
            try:
                vx, _vy, vz = self._get_local_axes(elem)
                orient = (vx[0], vx[1], vx[2], vz[0], vz[1], vz[2])
            except Exception:
                orient = None

            # --- Create zero-length hinge elements ---
            hinge_i_elem_tag = next_tag
            next_tag += 1
            if orient:
                ops.element(
                    "zeroLengthSection",
                    hinge_i_elem_tag,
                    ni.node_tag,
                    hinge_i_tag,
                    hinge_sec_tag - 1,
                    "-orient",
                    orient[0],
                    orient[1],
                    orient[2],
                    orient[3],
                    orient[4],
                    orient[5],
                )
            else:
                ops.element(
                    "zeroLengthSection",
                    hinge_i_elem_tag,
                    ni.node_tag,
                    hinge_i_tag,
                    hinge_sec_tag - 1,
                )

            hinge_j_elem_tag = next_tag
            next_tag += 1
            if orient:
                ops.element(
                    "zeroLengthSection",
                    hinge_j_elem_tag,
                    hinge_j_tag,
                    nj.node_tag,
                    hinge_sec_tag - 1,
                    "-orient",
                    orient[0],
                    orient[1],
                    orient[2],
                    orient[3],
                    orient[4],
                    orient[5],
                )
            else:
                ops.element(
                    "zeroLengthSection",
                    hinge_j_elem_tag,
                    hinge_j_tag,
                    nj.node_tag,
                    hinge_sec_tag - 1,
                )

            # --- Shorten original element to span between hinge nodes ---
            elem.node_i = hinge_i_id
            elem.node_j = hinge_j_id
            new_elements[eid] = elem
            new_assignments[eid] = sec_name

        # Update collections
        self.mesh_model.frame_elements = new_elements
        self.mesh_model.frame_assignments = new_assignments

    def _create_bond_slip_springs(self) -> None:
        """Insert zero-length ``Bond_SP01`` slip-rotation springs at member ends.

        Activated via ``config['bond_slip'] = True`` (off by default; only
        meaningful when ``create_fiber_sections`` is on, i.e. the pushover
        fiber rebuild).  Each fibre frame member is split into::

            structural_node_i → zeroLength(bond spring) → bond_i
                              … fibre element bond_i → bond_j …
              bond_j → zeroLength(bond spring) → structural_node_j

        The bond nodes are coincident with the member ends and their
        translation DOFs (1,2,3) are tied with ``equalDOF``, so only the
        end **rotations** are released across the zero-length springs.  Each
        spring is a ``section Aggregator`` with ``Bond_SP01`` (Zhao &
        Sritharan strain-penetration backbone, configured as a
        moment-rotation law) on the ``Mz``/``My`` DOFs plus elastic
        ``P``/``T`` terms — the bar-slip end rotation is added in series
        with the flexural fibre element, softening the post-peak response
        of flexure-critical frames.  The backbone is derived per member
        from the section rebar (:meth:`_derive_bond_slip_backbone`);
        ``config['bond_slip_backbone']`` may override it.

        .. note::
           OpenSeesPy 3.8.0.0 registers the material under its class name
           ``Bond_SP01`` (the Tcl command ``bond_sp01`` is not exported);
           the input values are used directly, so the moment/rotation
           backbone is fed in the model's own units.
        """
        if not self.config.get("bond_slip"):
            return
        if self.config.get("hinge_model") == "lumped":
            import warnings

            warnings.warn(
                "bond_slip and hinge_model='lumped' both re-point member ends — "
                "bond_slip skipped (lumped hinges win).",
                stacklevel=2,
            )
            return
        if not self.config.get("create_fiber_sections", False):
            # Only meaningful for fibre elements; elastic domains skip.
            return

        # ── Idempotency: preserve canonical state on first call ────────
        # Restoration is handled by _restore_bond_canonical_state() in
        # build_domain().
        if not hasattr(self, "_bond_canonical_elements"):
            self._bond_canonical_elements = {
                eid: (elem.node_i, elem.node_j)
                for eid, elem in self.mesh_model.frame_elements.items()
                if not getattr(elem, "inactive", False)
            }

        elements = self.mesh_model.frame_elements
        assignments = self.mesh_model.frame_assignments or {}

        next_node_tag = max((nd.node_tag for nd in self.mesh_model.nodes.values()), default=0) + 1
        try:
            max_ops_tag = max(ops.getEleTags(), default=0)
        except Exception:
            max_ops_tag = 0
        max_rigid_tag = max(
            (r[3] for r in getattr(self, "_offset_rigid_links", None) or []), default=0
        )
        next_tag = (
            max(
                max((e.elem_tag for e in elements.values() if not e.inactive), default=0),
                max_ops_tag,
                max_rigid_tag,
                max(self.frame_tag_map.values(), default=0),
            )
            + 1
        )
        # Separate counters for bond section/material tags, seeded high to
        # avoid collision with existing tags.
        bond_tag_base = (
            max((v for v in self.section_tags.values()), default=0) + len(self.section_tags) + 100
        )
        bond_mat_tag = bond_tag_base + len(self.section_tags) + 1

        units = self.mesh_model.units
        sy_m = float(self.config.get("bond_slip_sy_m", 0.000254)) * length_scale_factor(units)
        su_m = float(self.config.get("bond_slip_su_factor", 35.0)) * sy_m
        mu_factor = float(self.config.get("bond_slip_mu_factor", 1.4))
        b_ratio = float(self.config.get("bond_slip_b", 0.5))
        pinch = float(self.config.get("bond_slip_R", 0.7))
        override = self.config.get("bond_slip_backbone")

        n_spring = 0
        for eid, elem in list(elements.items()):
            if getattr(elem, "inactive", False):
                continue
            sec_name = assignments.get(eid)
            if not sec_name or sec_name not in self.section_tags:
                continue
            sec = self.mesh_model.sections.get(sec_name)
            if sec is None:
                continue
            ni = self.mesh_model.nodes.get(elem.node_i)
            nj = self.mesh_model.nodes.get(elem.node_j)
            if ni is None or nj is None:
                continue
            L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
            if L < 1e-12:
                continue

            bb = self._derive_bond_slip_backbone(
                sec, sy_m, su_m, mu_factor, b_ratio, pinch, override
            )
            if bb is None:
                continue

            # ── Coincident bond nodes ────────────────────────────────
            bond_i_id = f"{eid}_bond_i"
            bond_j_id = f"{eid}_bond_j"
            bond_i_tag = next_node_tag
            next_node_tag += 1
            bond_j_tag = next_node_tag
            next_node_tag += 1
            self.mesh_model.nodes[bond_i_id] = Node(
                node_id=bond_i_id, node_tag=bond_i_tag, x=ni.x, y=ni.y, z=ni.z
            )
            self.mesh_model.nodes[bond_j_id] = Node(
                node_id=bond_j_id, node_tag=bond_j_tag, x=nj.x, y=nj.y, z=nj.z
            )
            self._created_node_tags.update([bond_i_tag, bond_j_tag])
            ops.node(bond_i_tag, ni.x, ni.y, ni.z)
            ops.node(bond_j_tag, nj.x, nj.y, nj.z)

            # ── Spring materials ─────────────────────────────────────
            mat = self.mesh_model.materials.get(sec.material)
            E_mod = mat.E_mod if mat and mat.E_mod else 1.0
            A_val = getattr(sec, "A", None) or 0.0
            # Rigid terms for the non-slip DOFs (axial, shear, torsion) —
            # a unit-consistent 100× the member axial stiffness, so only
            # the end rotations are released across the spring.
            rigid_k = 100.0 * max(A_val, 1e-6) * E_mod / L

            rigid_tag = bond_mat_tag
            bond_mat_tag += 1
            mz_tag = bond_mat_tag
            bond_mat_tag += 1
            my_tag = bond_mat_tag
            bond_mat_tag += 1
            ops.uniaxialMaterial("Elastic", rigid_tag, rigid_k)
            ops.uniaxialMaterial(
                "Bond_SP01",
                mz_tag,
                bb["my"],
                bb["theta_y"],
                bb["mu"],
                bb["theta_u"],
                bb["b"],
                bb["R"],
            )
            ops.uniaxialMaterial(
                "Bond_SP01",
                my_tag,
                bb["my_w"],
                bb["theta_y_w"],
                bb["mu_w"],
                bb["theta_u_w"],
                bb["b"],
                bb["R"],
            )

            # ── Zero-length spring elements ──────────────────────────
            # Plain zeroLength with per-DOF materials (mirrors the Elwood
            # limit-state springs): dirs 1-4 rigid (axial/shear/torsion),
            # dir 5 = My (weak-axis slip), dir 6 = Mz (strong-axis slip).
            # No equalDOF — the element itself carries every DOF, keeping
            # the Transformation constraint handler compatible with the
            # rigidLink MPC joint offsets.
            try:
                vx, vy, _vz = self._get_local_axes(elem)
                orient = (vx[0], vx[1], vx[2], vy[0], vy[1], vy[2])
            except Exception:
                orient = None
            zl_i_tag = next_tag
            next_tag += 1
            zl_j_tag = next_tag
            next_tag += 1
            _mats = [rigid_tag, rigid_tag, rigid_tag, rigid_tag, my_tag, mz_tag]
            if orient:
                ops.element(
                    "zeroLength",
                    zl_i_tag,
                    ni.node_tag,
                    bond_i_tag,
                    "-mat",
                    *_mats,
                    "-dir",
                    1,
                    2,
                    3,
                    4,
                    5,
                    6,
                    "-orient",
                    *orient,
                )
                ops.element(
                    "zeroLength",
                    zl_j_tag,
                    bond_j_tag,
                    nj.node_tag,
                    "-mat",
                    *_mats,
                    "-dir",
                    1,
                    2,
                    3,
                    4,
                    5,
                    6,
                    "-orient",
                    *orient,
                )
            else:
                ops.element(
                    "zeroLength",
                    zl_i_tag,
                    ni.node_tag,
                    bond_i_tag,
                    "-mat",
                    *_mats,
                    "-dir",
                    1,
                    2,
                    3,
                    4,
                    5,
                    6,
                )
                ops.element(
                    "zeroLength",
                    zl_j_tag,
                    bond_j_tag,
                    nj.node_tag,
                    "-mat",
                    *_mats,
                    "-dir",
                    1,
                    2,
                    3,
                    4,
                    5,
                    6,
                )

            # ── Shorten the fibre element to span the bond nodes ─────
            elem.node_i = bond_i_id
            elem.node_j = bond_j_id
            n_spring += 1

        if n_spring and self.config.get("verbose", False):
            print(f"  Inserted bond-slip springs on {n_spring} member(s)")

    def _derive_bond_slip_backbone(
        self,
        sec,
        sy_m: float,
        su_m: float,
        mu_factor: float,
        b_ratio: float,
        pinch: float,
        override,
    ) -> Optional[dict]:
        """Derive (or read) the ``Bond_SP01`` moment-rotation backbone.

        ``config['bond_slip_backbone']`` may be an explicit dict (model
        units, keys ``my``, ``theta_y``, ``mu``, ``theta_u`` with optional
        weak-axis ``*_w`` variants and ``b``/``R``); otherwise the backbone
        is derived per section from the rebar:

        * ``My = A_s · f_y · jd`` (tension steel × lever arm)
        * ``θ_y = sy / jd`` (yield slip over the lever arm)
        * ``Mu = bond_slip_mu_factor · My``; ``θ_u = su / jd``

        Returns ``None`` when the section lacks rebar data (no spring is
        inserted for that member).
        """
        if isinstance(override, dict):
            return {
                "my": float(override["my"]),
                "theta_y": float(override["theta_y"]),
                "mu": float(override["mu"]),
                "theta_u": float(override["theta_u"]),
                "my_w": float(override.get("my_w", override["my"])),
                "theta_y_w": float(override.get("theta_y_w", override["theta_y"])),
                "mu_w": float(override.get("mu_w", override["mu"])),
                "theta_u_w": float(override.get("theta_u_w", override["theta_u"])),
                "b": float(override.get("b", b_ratio)),
                "R": float(override.get("R", pinch)),
            }

        depth = float(getattr(sec, "depth", None) or 0.0)
        bf = float(getattr(sec, "bf", None) or 0.0)
        cover = float(getattr(sec, "cover", None) or 0.0)
        top_bars = int(getattr(sec, "top_bars", None) or 0)
        dia = float(getattr(sec, "top_bar_dia", None) or 0.0)
        rebar_mat_name = getattr(sec, "rebar_material", None)
        rebar_mat = self.mesh_model.materials.get(rebar_mat_name) if rebar_mat_name else None
        fy = float(getattr(rebar_mat, "Fy", 0.0) or 0.0)
        if top_bars <= 0 or dia <= 0.0 or fy <= 0.0 or depth <= 0.0 or cover < 0.0:
            return None
        As = math.pi / 4.0 * dia * dia * float(top_bars)

        def _backbone(dim: float) -> tuple:
            jd = max(dim - 2.0 * cover, 1e-6)
            my = As * fy * jd
            return my, sy_m / jd, my * mu_factor, su_m / jd

        my, theta_y, mu, theta_u = _backbone(depth)
        my_w, theta_y_w, mu_w, theta_u_w = _backbone(bf if bf > 0.0 else depth)
        return {
            "my": my,
            "theta_y": theta_y,
            "mu": mu,
            "theta_u": theta_u,
            "my_w": my_w,
            "theta_y_w": theta_y_w,
            "mu_w": mu_w,
            "theta_u_w": theta_u_w,
            "b": b_ratio,
            "R": pinch,
        }
