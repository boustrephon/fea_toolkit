"""Analysis builder — create OpenSees domain from a prepared ``MeshModel``.

The :class:`AnalysisBuilder` takes a :class:`~fea_toolkit.model.mesh_model.MeshModel`
(fully prepared topology from the :class:`~fea_toolkit.opensees.preprocessor.Preprocessor`)
and creates the OpenSees domain objects.  It handles all analysis execution
and result extraction — no topology mutations occur here.
"""

from typing import Dict, Any, Optional, List, Tuple, Set, Union
import copy
import math
import numpy as np

import openseespy.opensees as ops

from ..model.mesh_model import MeshModel
from ..model.sap_data import (
    Node, FrameElement, AreaElement,
    ShellSection, Restraint,
)
from ..model.geometry import get_SAP_vecxz, get_local_axes
from ..model.geometry import polygon_area_3d
from ..model.tree_utils import collect_descendants
from ..model.selection import Selection
from ..utils import g_from_units, cqc_combine


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

    # ── Solver defaults for pushover / nonlinear analysis ────────────
    PUSHOVER_SOLVER_DEFAULTS: dict = {
        "solver_test_type": "NormDispIncr",
        "solver_test_tol": 1e-6,
        "solver_test_max_iter": 10,
        "solver_algorithm": "Newton",
        "solver_constraints": "Transformation",
        "solver_system": "BandGen",
        "gravity_num_substeps": 1,
    }

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
        # NOTE: mesh_model.edge_constraint_args is always [] today
        # (the Preprocessor stores detected pairs in detected_edge_pairs,
        # not constraint arguments).  The list is overwritten when
        # apply_edge_constraints() is first called at analysis time.
        self._saved_edge_constraints: List[tuple] = list(mesh_model.edge_constraint_args)
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
        self._sw_load_totals: Dict[str, Dict[str, float]] = {}
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
            'constraint_method': 'spring',
            'hinge_model': 'fiber',         # Distributed plasticity by default
        }
        # Merge solver defaults from the class constant
        defaults.update(self.PUSHOVER_SOLVER_DEFAULTS)
        for k, v in defaults.items():
            self.config.setdefault(k, v)

    # ═══════════════════════════════════════════════════════════════
    # Domain construction
    # ═══════════════════════════════════════════════════════════════

    def build_domain(self,
                     config_overrides: Optional[Dict[str, Any]] = None,
                     ) -> None:
        """Create the full OpenSees domain from the MeshModel.

        Creates nodes, restraints, materials, sections, frame elements,
        shell elements, lumped hinges, and rigid links.

        Args:
            config_overrides: Optional dict of config keys to temporarily
                override ``self.config`` for this build cycle.  Useful for
                pushover rebuilds that need fiber sections or different
                element types.  The overrides are reset after the build.
        """
        # Apply temporary config overrides
        _saved_overrides: Dict[str, Any] = {}
        if config_overrides:
            for k, v in config_overrides.items():
                _saved_overrides[k] = self.config.get(k)
                self.config[k] = v

        try:
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
        finally:
            # Restore any overridden config values
            for k, old_v in _saved_overrides.items():
                if old_v is None:
                    self.config.pop(k, None)
                else:
                    self.config[k] = old_v

    def rebuild_with_fiber_sections(
        self,
        brace_selection: Optional[set] = None,
        pushover_spring_scale: float = 1.0,
    ) -> None:
        """Rebuild the OpenSees domain with fiber sections for pushover.

        Calls :meth:`build_domain` with config overrides that enable fiber
        sections and dispBeamColumn elements.

        Args:
            brace_selection: Optional set of brace element IDs to
                subdivide with initial imperfection (Approach A).
                When provided, the builder stores the selection and
                enables ``subdivide_braces`` so that
                :meth:`_create_elements` will subdivide each brace
                into *brace_n_segments* segments with an initial
                sinusoidal imperfection.
            pushover_spring_scale: Scale factor for edge constraint
                spring stiffness on rebuild (default 1.0).

        Note:
            Braces are subdivided at domain creation time (in
            :meth:`_create_elements`), not deferred to analysis.
            The subdivided elements use ``PDelta`` geometric
            transformation by default, which is required for
            buckling to develop under compression.
        """
        overrides: Dict[str, Any] = {
            'element_type': 'dispBeamColumn',
            'create_fiber_sections': True,
            'use_elastic_sections': False,
        }
        if brace_selection:
            overrides['geom_transf_type'] = 'PDelta'
            overrides['subdivide_braces'] = True
            self._brace_selection = brace_selection

        self.build_domain(config_overrides=overrides)

        # Re-apply edge constraints if previously saved
        if self._saved_edge_constraints:
            self._reapply_edge_constraints(scale=pushover_spring_scale)

    def _reapply_edge_constraints(self, scale: float = 1.0) -> None:
        """Re-apply saved edge constraints after a domain rebuild.

        Iterates ``self._saved_edge_constraints`` (populated when
        :meth:`apply_edge_constraints` was first called) and re-applies
        each saved batch.  Used after ``build_domain()`` wipes the
        OpenSees domain (e.g. during pushover fiber-section rebuild).
        """
        if not self._saved_edge_constraints:
            return
        for args in self._saved_edge_constraints:
            coarse_edges, fine_nodes, coarse_elems, tolerance, k, verbose = args
            if scale != 1.0 and k is not None:
                k = k * scale
            self.apply_edge_constraints(
                coarse_edges=coarse_edges,
                fine_nodes=fine_nodes,
                coarse_elements=coarse_elems,
                tolerance=tolerance,
                penalty_stiffness=k,
                verbose=verbose or self.config.get('verbose', False),
            )

    # ═══════════════════════════════════════════════════════════════
    # Edge constraint helpers
    # ═══════════════════════════════════════════════════════════════

    def _node_tag_from_id(self, node_id: str) -> Optional[int]:
        """Return numeric tag for a node, or None if not found."""
        node = self.mesh_model.nodes.get(node_id)
        if node:
            return node.node_tag
        return None

    def _get_shell_area_ids(self) -> set:
        """Return the set of area element IDs that became actual shell elements.

        When ``create_shells`` is ``False`` (no shells built), all areas
        are still returned to support diagnostic detection of unconnected
        edges before deciding whether to create shells.
        """
        return {aid for aid in self.mesh_model.area_elements
                if not getattr(self.mesh_model.area_elements[aid], 'inactive', False)}

    # ═══════════════════════════════════════════════════════════════
    # Local axis utilities (used by visualisation)
    # ═══════════════════════════════════════════════════════════════

    def _get_local_axes(self, elem: FrameElement):
        """Return local (vx, vy, vz) unit vectors for a frame element.

        Parameters
        ----------
        elem : FrameElement
            Frame element from the MeshModel.

        Returns
        -------
        tuple of np.ndarray
            Local x, y, z unit vectors (3-element each).
        """
        node_i = self.mesh_model.nodes[elem.node_i]
        node_j = self.mesh_model.nodes[elem.node_j]
        coords_i = ops.nodeCoord(node_i.node_tag)
        coords_j = ops.nodeCoord(node_j.node_tag)
        vec_x = np.array(coords_j) - np.array(coords_i)
        return get_local_axes(vec_x, getattr(elem, 'angle', 0.0))

    def _global_to_local(self, elem: FrameElement, vec: np.ndarray) -> np.ndarray:
        """Transform a vector from global to local coordinates.

        Parameters
        ----------
        elem : FrameElement
            Frame element defining the local coordinate system.
        vec : np.ndarray
            3-element vector in global coordinates.

        Returns
        -------
        np.ndarray
            3-element vector in local coordinates.
        """
        vx, vy, vz = self._get_local_axes(elem)
        T = np.vstack([vx, vy, vz])
        return T @ vec

    # ═══════════════════════════════════════════════════════════════
    # Edge constraint dispatcher
    # ═══════════════════════════════════════════════════════════════

    def apply_edge_constraints(
        self,
        coarse_edges: Optional[List[Tuple[int, int]]] = None,
        fine_nodes: Optional[List[int]] = None,
        coarse_elements: Optional[List[int]] = None,
        tolerance: float = 1e-4,
        penalty_stiffness: Optional[float] = None,
        verbose: bool = True,
    ) -> int:
        """Apply edge constraints using the configured ``constraint_method``.

        Delegates to :meth:`apply_spring_edge_constraints` or
        :meth:`_apply_penalty_edge_constraints` based on
        ``self.config['constraint_method']``.

        * ``"spring"`` (default) — creates ``twoNodeLink`` spring elements.
        * ``"penalty"`` — uses ``equationConstraint`` MPCs + Penalty handler.

        See :meth:`apply_spring_edge_constraints` and
        :meth:`_apply_penalty_edge_constraints` for full parameter docs.
        """
        method = self.config.get('constraint_method', 'spring')
        if method == 'spring':
            return self.apply_spring_edge_constraints(
                coarse_edges=coarse_edges,
                fine_nodes=fine_nodes,
                coarse_elements=coarse_elements,
                tolerance=tolerance,
                penalty_stiffness=penalty_stiffness,
                verbose=verbose,
            )
        if method == 'penalty':
            return self._apply_penalty_edge_constraints(
                coarse_edges=coarse_edges,
                fine_nodes=fine_nodes,
                coarse_elements=coarse_elements,
                tolerance=tolerance,
                verbose=verbose,
            )
        raise ValueError(
            f"Unknown constraint_method '{method}'. "
            f"Choose 'spring' or 'penalty'.")

    # ═══════════════════════════════════════════════════════════════
    # Spring-based edge constraints (twoNodeLink)
    # ═══════════════════════════════════════════════════════════════

    def apply_spring_edge_constraints(
        self,
        coarse_edges: Optional[List[Tuple[int, int]]] = None,
        fine_nodes: Optional[List[int]] = None,
        coarse_elements: Optional[List[int]] = None,
        tolerance: float = 1e-4,
        penalty_stiffness: Optional[float] = None,
        verbose: bool = True,
    ) -> int:
        """Tie slave nodes to master edges using stiff zero-length spring
        elements (``twoNodeLink``) instead of ``equationConstraint`` MPCs.

        Spring elements create a **flexible** connection whose stiffness
        is controlled by *penalty_stiffness*.  The auto-computed default
        targets ~100× the shell in-plane stiffness (E·t).

        Each slave node is tied to both ends of its nearest master edge
        via two spring elements with stiffness weighted by interpolation
        factors N₁, N₂ (proximity along the edge).

        Args:
            coarse_edges: Explicit master edge node pairs ``(n1, n2)``.
            fine_nodes: Slave node candidates.  ``None`` = auto-detect.
            coarse_elements: Auto-extract master edges from element tags.
            tolerance: Max perpendicular distance for slave detection.
            penalty_stiffness: Spring stiffness per DOF.  ``None`` = auto.
            verbose: Print progress.

        Returns:
            Number of ``twoNodeLink`` elements created (2 per slave-edge pair).
        """
        # ── Resolve master edges ──────────────────────────────────
        edge_set: set = set()
        if coarse_elements is not None:
            for etag in coarse_elements:
                try:
                    nodes = ops.eleNodes(int(etag))
                except Exception:
                    continue
                for j in range(len(nodes)):
                    n1, n2 = nodes[j], nodes[(j + 1) % len(nodes)]
                    edge_set.add((min(n1, n2), max(n1, n2)))
        if coarse_edges is not None:
            for n1, n2 in coarse_edges:
                t1 = self._node_tag_from_id(str(n1)) if not isinstance(n1, int) else n1
                t2 = self._node_tag_from_id(str(n2)) if not isinstance(n2, int) else n2
                if t1 is None:
                    t1 = int(n1)
                if t2 is None:
                    t2 = int(n2)
                edge_set.add((min(t1, t2), max(t1, t2)))
        if not edge_set:
            if verbose:
                print("No master edges — nothing to constrain.")
            return 0

        # ── Resolve slave nodes ───────────────────────────────────
        if fine_nodes is not None:
            slave_candidates = []
            for n in fine_nodes:
                tag = self._node_tag_from_id(str(n)) if not isinstance(n, int) else n
                slave_candidates.append(tag if tag is not None else int(n))
        else:
            shell_ids = self._get_shell_area_ids()
            all_nodes: set = set()
            for eid in shell_ids:
                for n_id in self.mesh_model.area_elements[eid].node_ids:
                    tag = self._node_tag_from_id(n_id)
                    if tag is not None:
                        all_nodes.add(tag)
            slave_candidates = sorted(all_nodes)

        # ── Auto stiffness ────────────────────────────────────────
        if penalty_stiffness is None:
            avg_Et = 0.0
            _count = 0
            for _aid in self.mesh_model.area_elements:
                if getattr(self.mesh_model.area_elements[_aid], 'inactive', False):
                    continue
                _sec_name = self.mesh_model.area_assignments.get(_aid)
                if _sec_name:
                    _sec = self.mesh_model.sections.get(_sec_name)
                    if _sec and hasattr(_sec, 'thickness') and _sec.thickness > 0:
                        _mat = self.mesh_model.materials.get(_sec.material)
                        if _mat and _mat.E_mod > 0:
                            avg_Et += _mat.E_mod * _sec.thickness
                            _count += 1
            if _count > 0:
                avg_Et /= _count
                penalty_stiffness = 100.0 * avg_Et
                if verbose:
                    print(f"  Spring stiffness auto: E·t_avg = {avg_Et:.3e}, "
                          f"k = {penalty_stiffness:.3e}  "
                          f"(scanned {_count} shell element(s))")
            else:
                _E = 2e8   # KN/m² (200 GPa steel)
                _t = 0.15  # m (typical slab)
                penalty_stiffness = 100.0 * _E * _t
                if verbose:
                    print(f"  Spring stiffness auto: using fallback "
                          f"k = {penalty_stiffness:.3e}")

        # ── Find tags ─────────────────────────────────────────────
        max_elem = max(
            (e.elem_tag for e in self.mesh_model.frame_elements.values()
             if hasattr(e, 'elem_tag') and e.elem_tag is not None),
            default=0,
        )
        try:
            active = ops.getEleTags()
            if active:
                max_elem = max(max_elem, max(active))
        except Exception:
            pass
        ele_tag = max_elem + 100_000
        mat_tag = ele_tag + 50_000

        # ── Apply springs ─────────────────────────────────────────
        count = 0
        for m1_id, m2_id in edge_set:
            try:
                c1 = np.array(ops.nodeCoord(m1_id))
                c2 = np.array(ops.nodeCoord(m2_id))
            except Exception:
                continue
            edge_vec = c2 - c1
            edge_len = float(np.linalg.norm(edge_vec))
            if edge_len < 1e-12:
                continue

            for s_id in slave_candidates:
                if s_id in (m1_id, m2_id):
                    continue
                try:
                    cs = np.array(ops.nodeCoord(s_id))
                except Exception:
                    continue
                cross = float(np.linalg.norm(np.cross(cs - c1, cs - c2)))
                if cross / max(edge_len, 1e-12) > tolerance:
                    continue
                proj = float(np.dot(cs - c1, edge_vec)) / edge_len
                if proj <= 0.0 or proj >= edge_len:
                    continue
                N2 = proj / edge_len
                N1 = 1.0 - N2

                for master, weight in ((m1_id, N1), (m2_id, N2)):
                    if weight < 1e-12:
                        continue
                    k = penalty_stiffness * weight
                    ops.uniaxialMaterial('Elastic', mat_tag, k)
                    ops.element('twoNodeLink', ele_tag, int(s_id), int(master),
                                '-mat', mat_tag, mat_tag, mat_tag,
                                mat_tag, mat_tag, mat_tag,
                                '-dir', 1, 2, 3, 4, 5, 6)
                    if verbose:
                        print(
                            f"  Spring constraint: node {s_id} → "
                            f"master {master}  (k={k:.2e}, w={weight:.3f})"
                        )
                    ele_tag += 1
                    mat_tag += 1
                    count += 1

        if count:
            self._edge_constraint_method = 'spring'
            if coarse_edges is not None or coarse_elements is not None:
                # Save arguments so they can be re-applied after a
                # domain rebuild (pushover switches to fiber sections).
                # Single-entry list: edge constraints are applied as one
                # batch per analysis cycle.
                self._saved_edge_constraints = [(
                    coarse_edges, fine_nodes, coarse_elements,
                    tolerance, penalty_stiffness, verbose,
                )]
            if verbose:
                print(f"Applied {count} spring element(s).")

        return count

    # ═══════════════════════════════════════════════════════════════
    # Penalty-based edge constraints (equationConstraint MPCs)
    # ═══════════════════════════════════════════════════════════════

    def _apply_penalty_edge_constraints(
        self,
        coarse_edges: Optional[List[Tuple[int, int]]] = None,
        fine_nodes: Optional[List[int]] = None,
        coarse_elements: Optional[List[int]] = None,
        tolerance: float = 1e-4,
        verbose: bool = True,
    ) -> int:
        """Apply edge constraints using ``equationConstraint`` MPCs with
        the Penalty constraint handler.

        Unaligned slave nodes that lie on coarse-mesh edges are tied via
        ``ops.equationConstraint()`` with interpolation weights based on
        their position along the edge.  All six DOFs are constrained.

        The Penalty handler is required — ``Transformation`` cannot
        process ``equationConstraint`` MPCs.

        Args:
            coarse_edges: Explicit master edge node pairs.
            fine_nodes: Slave node IDs.  ``None`` = all shell nodes.
            coarse_elements: Auto-extract master edges from element tags.
            tolerance: Max perpendicular distance to consider a slave
                node "on the edge".
            verbose: Print progress messages.

        Returns:
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
            slave_candidates = []
            for n in fine_nodes:
                tag = self._node_tag_from_id(str(n)) if not isinstance(n, int) else n
                if tag is None:
                    tag = int(n)
                slave_candidates.append(tag)
        else:
            shell_area_ids = self._get_shell_area_ids()
            all_nodes: set = set()
            for eid in shell_area_ids:
                elem = self.mesh_model.area_elements[eid]
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
            self._edge_constraint_method = 'penalty'
            if coarse_edges is not None or coarse_elements is not None:
                self._saved_edge_constraints = [(
                    coarse_edges, fine_nodes, coarse_elements,
                    tolerance, None, verbose,
                )]
            if verbose:
                print(f"Applied {count} edge constraint(s). "
                      f"Solver will use Penalty handler.")

        return count

    # ═══════════════════════════════════════════════════════════════
    # Diagnostic: detect unconnected edges
    # ═══════════════════════════════════════════════════════════════

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

        Args:
            tolerance: Maximum perpendicular distance from a node to a
                line segment for it to be considered "on the edge".
            include_frame_connections: Also check whether frame element
                nodes align with shell edges.

        Returns:
            List of dicts with keys ``slave_node``, ``master_node_i``,
            ``master_node_j``, ``coords``, ``N1``, ``N2``, ``edge_length``,
            ``distance``.
        """
        reports: List[Dict[str, Any]] = []

        shell_area_ids = self._get_shell_area_ids()
        if not shell_area_ids:
            return reports

        edge_set: set = set()
        for eid in shell_area_ids:
            elem = self.mesh_model.area_elements[eid]
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

        shell_node_set: set = set()
        for eid in shell_area_ids:
            elem = self.mesh_model.area_elements[eid]
            for n_id in elem.node_ids:
                tag = self._node_tag_from_id(n_id)
                if tag is not None:
                    shell_node_set.add(tag)

        if include_frame_connections:
            for eid, elem in self.mesh_model.frame_elements.items():
                for n_id in (elem.node_i, elem.node_j):
                    tag = self._node_tag_from_id(n_id)
                    if tag is not None:
                        shell_node_set.add(tag)

        all_slave_nodes = sorted(shell_node_set)

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
        """Apply boundary conditions from MeshModel restraints.

        Also propagates restraints from area corner nodes to intermediate
        mesh-created nodes along each edge of subdivided shell areas.
        Without this, ``ShellMITC4`` elements at restrained edges would
        have unrestrained intermediate nodes, creating a rotational
        mechanism (singular stiffness matrix).
        """
        import numpy as np

        for node_id, restraint in self.mesh_model.restraints.items():
            nd = self.mesh_model.nodes.get(node_id)
            if nd is None:
                continue
            ops.fix(nd.node_tag, *restraint.dofs[:6])

        # ── Propagate edge restraints to mesh nodes ──────────────
        # For each subdivided area parent, check if both corner nodes
        # along an edge have restraints.  If so, intermediate mesh
        # nodes inherit the AND (more-restrictive) combination.
        for aid, elem in self.mesh_model.area_elements.items():
            if not getattr(elem, 'inactive', False):
                continue  # only look at subdivided parents
            if len(elem.node_ids) != 4:
                continue
            corners = list(elem.node_ids)  # 4 corner SAP node IDs
            edges = [(0, 1), (1, 2), (3, 2), (0, 3)]
            for ci, cj in edges:
                nid_i = corners[ci]
                nid_j = corners[cj]
                ri = self.mesh_model.restraints.get(nid_i)
                rj = self.mesh_model.restraints.get(nid_j)
                if ri is None or rj is None:
                    continue
                # AND of restraint DOFs (more restricted wins)
                combined = [
                    min(ri.dofs[d], rj.dofs[d]) for d in range(6)
                ]
                if sum(combined) == 0:
                    continue
                nd_i = self.mesh_model.nodes.get(nid_i)
                nd_j = self.mesh_model.nodes.get(nid_j)
                if nd_i is None or nd_j is None:
                    continue
                p_i = np.array([nd_i.x, nd_i.y, nd_i.z])
                p_j = np.array([nd_j.x, nd_j.y, nd_j.z])
                edge_vec = p_j - p_i
                edge_len_sq = np.dot(edge_vec, edge_vec)
                if edge_len_sq < 1e-12:
                    continue

                mesh_prefix = f"{aid}_mesh_"
                for nd in list(self.mesh_model.nodes.values()):
                    if mesh_prefix not in nd.node_id:
                        continue
                    p = np.array([nd.x, nd.y, nd.z])
                    t = np.dot(p - p_i, edge_vec) / edge_len_sq
                    if t < 1e-6 or t > 1 - 1e-6:
                        continue
                    proj = p_i + t * edge_vec
                    if np.linalg.norm(p - proj) > 0.01:
                        continue
                    ops.fix(nd.node_tag, *combined)

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
            self._truss_Fy: Dict[str, float] = {}
            self._truss_E: Dict[str, float] = {}
            self._truss_mat_counter: int = 100000
            # Use tags beyond both material AND section tags to avoid clashes
            # with fiber-section materials created in _create_single_section
            # (which use mat_tag = section_tag).
            _existing = max(
                max(self.material_tags.values(), default=0),
                max(self.section_tags.values(), default=0),
            )
            truss_tag = _existing + 1

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
                # Hysteretic material creation deferred to _add_beam_column
                # where the actual element length is known for buckling calc.
                self._truss_Fy[sec_name] = Fy
                self._truss_E[sec_name] = E_sec
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
        # ── Fiber section path (frame sections only) ────────────
        if self.config.get('create_fiber_sections'):
            from ..model.sap_data import ShellSection as _ShellSec
            if not isinstance(sec, _ShellSec):
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

                # Create fiber section (after to_fiber_patches succeeds)
                try:
                    entries = sec.to_fiber_patches(mat_tag=mat_tag, nfy=8, nfz=4)
                except NotImplementedError:
                    # Fall back to elastic — no Fiber section was created,
                    # so no tag collision with the Elastic replacement.
                    if self.config.get('verbose', False):
                        print(f"  Section {tag} ({sec.name}): fiber not supported, "
                              f"falling back to elastic")
                    ops.section('Elastic', tag, E_mod, _A, _I33, _I22, G_mod, _J)
                    return

                ops.section('Fiber', tag, '-GJ', _J)
                for entry in entries:
                    if entry[0] in ('rect', 'circ', 'quad'):
                        ops.patch(*entry)
                    elif entry[0] == 'straight':
                        ops.layer('straight', *entry[1:])
                    elif entry[0] == 'circ_layer':
                        ops.layer('circ', *entry[1:])

                if self.config.get('verbose', False):
                    print(f"  Section {tag}: {sec.name} (Fiber, {len(entries)} patches)")
                return  # fiber path done

        # ── Elastic section path (including ShellSections) ──────
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

        # Brace subdivision (Approach A: subdivided elements + imperfection)
        if self.config.get('subdivide_braces') and self._brace_selection:
            n_seg = self.config.get('brace_n_segments', 4)
            imperf = self.config.get('brace_imperfection_ratio', 1.0 / 500.0)
            end_off = self.config.get('brace_end_offset', 0.0)
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
                elements, assignments, nodes,
                n_segments=n_seg,
                imperfection_ratio=imperf,
                brace_ids=self._brace_selection,
                end_offset=end_off,
                next_tag=next_tag,
            )
            # Update mesh model references with subdivided topology
            self.mesh_model.frame_elements = elements
            self.mesh_model.frame_assignments = assignments
            self.mesh_model.nodes = nodes
            # Create OpenSees nodes for subdivision / offset nodes
            for nd in nodes.values():
                if nd.node_tag not in self._created_node_tags:
                    ops.node(nd.node_tag, nd.x, nd.y, nd.z)
                    self._created_node_tags.add(nd.node_tag)
            # Create rigid link elements (same pattern as offset rigid links)
            if rigid_links:
                all_sec_tags = set(self.section_tags.values())
                all_sec_tags.update(self._shell_sec_tags.values())
                all_sec_tags.update(self._shell_sec_variants.values())
                rigid_section_tag = max(all_sec_tags, default=0) + 1
                rigid_E = 2.0e14
                rigid_A = 1.0
                rigid_I = 1.0
                ops.section('Elastic', rigid_section_tag, rigid_E, rigid_A,
                            rigid_I, rigid_I, rigid_E / 2.6, rigid_I)
                for _link_id, _node_i_id, _node_j_id, link_tag in rigid_links:
                    nd_i = nodes.get(_node_i_id)
                    nd_j = nodes.get(_node_j_id)
                    if nd_i is None or nd_j is None:
                        continue
                    ni_tag = nd_i.node_tag
                    nj_tag = nd_j.node_tag
                    dx = float(nd_j.x - nd_i.x)
                    dy = float(nd_j.y - nd_i.y)
                    dz = float(nd_j.z - nd_i.z)
                    vecxz = get_SAP_vecxz(np.array([dx, dy, dz]), 0.0)
                    ops.geomTransf('Linear', link_tag, *vecxz)
                    ops.element('elasticBeamColumn', link_tag, ni_tag, nj_tag,
                                rigid_section_tag, link_tag, '-mass', 0.0)
                    self._rigid_link_elems[_link_id] = link_tag

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

        # ── Brace truss elements ────────────────────────────────
        # When brace_truss is active, sections matching _truss_mat_tags
        # become Truss elements with Hysteretic material instead of
        # beam-column elements (matching the legacy Builder behaviour).
        if (self.config.get('brace_truss')
                and hasattr(self, '_truss_mat_tags')
                and sec_name in self._truss_mat_tags):
            A = self._truss_areas[sec_name]
            Fy = self._truss_Fy[sec_name]
            E_sec = self._truss_E[sec_name]
            # Per-element Hysteretic material using actual element length
            # for Euler buckling — each brace gets its own buckling load.
            _L_brace = math.sqrt(
                (nj.x - ni.x)**2 + (nj.y - ni.y)**2 + (nj.z - ni.z)**2)
            eps_y = Fy / E_sec
            s1p, e1p = Fy, eps_y
            s2p, e2p = Fy * 1.01, eps_y + 0.01
            s3p, e3p = Fy * 1.02, eps_y + 0.05
            _sec = self.mesh_model.sections.get(sec_name)
            _I_min = (getattr(_sec, 'I22', 0.0) or
                      getattr(_sec, 'I33', 0.0) or 1e-6)
            _P_cr = (math.pi ** 2 * E_sec * _I_min) / (_L_brace ** 2)
            sig_cr = _P_cr / A if A > 0 else Fy * 0.3
            eps_cr = sig_cr / E_sec
            s1n, e1n = -sig_cr, -eps_cr
            s2n, e2n = -sig_cr * 0.2, -eps_cr - 0.01
            s3n, e3n = -sig_cr * 0.1, -eps_cr - 0.05
            mat_tag = self._truss_mat_counter
            self._truss_mat_counter += 1
            ops.uniaxialMaterial('Hysteretic', mat_tag,
                                 s1p, e1p, s2p, e2p, s3p, e3p,
                                 s1n, e1n, s2n, e2n, s3n, e3n,
                                 1.0, 1.0, 0.0, 0.0, 0.0)
            self.material_tags[f"truss_{sec_name}_{tag}"] = mat_tag
            ops.element('Truss', tag, ni.node_tag, nj.node_tag, A, mat_tag)
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
                # HingeRadau with explicit hinge lengths
                _L_hinge = math.sqrt(
                    (nj.x - ni.x)**2 + (nj.y - ni.y)**2 + (nj.z - ni.z)**2)
                _sec = self.mesh_model.sections.get(sec_name)
                if _sec is not None:
                    from fea_toolkit.model.checks import compute_hinge_length
                    Lp = compute_hinge_length(_sec, _L_hinge)
                else:
                    Lp = 0.1 * _L_hinge
                ops.beamIntegration('HingeRadau', int_tag, sec_tag, Lp, sec_tag, Lp, sec_tag)
            ops.element(elem_type, tag, *[ni.node_tag, nj.node_tag], transf_tag, int_tag)

    # ── Brace selection (Approach A) ─────────────────────────────

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

    # ── Lumped hinges ────────────────────────────────────────────

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
        if self.config.get('hinge_model') != 'lumped':
            return

        elements = self.mesh_model.frame_elements
        assignments = self.mesh_model.frame_assignments

        next_node_tag = max((nd.node_tag for nd in self.mesh_model.nodes.values()),
                            default=0) + 1
        next_tag = max((e.elem_tag for e in elements.values() if not e.inactive),
                       default=0) + 1
        # Separate counter for hinge section/material tags, seeded high
        # to avoid collision with existing tags.
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

            ni = self.mesh_model.nodes.get(elem.node_i)
            nj = self.mesh_model.nodes.get(elem.node_j)
            if ni is None or nj is None:
                new_elements[eid] = elem
                continue

            L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
            if L < 1e-12:
                new_elements[eid] = elem
                continue

            # Type-specific section tag lookup
            etype = self._frame_element_types.get(eid)
            type_key = f"{sec_name}__{etype}" if etype else None
            if type_key and type_key in self.section_tags:
                sec_tag = self.section_tags[type_key]
            else:
                sec_tag = self.section_tags[sec_name]
            sec = self.mesh_model.sections.get(sec_name)
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

            self.mesh_model.nodes[hinge_i_id] = Node(
                node_id=hinge_i_id, node_tag=hinge_i_tag,
                x=ni.x, y=ni.y, z=ni.z,
            )
            self.mesh_model.nodes[hinge_j_id] = Node(
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
            mat = self.mesh_model.materials.get(sec.material)

            if mat and mat.type and 'concrete' in mat.type.lower():
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
            from ..model.checks import compute_asce41_hinge_length
            Lp = compute_asce41_hinge_length(self.mesh_model, sec_name, L)
            theta_y = (My * Lp) / (max(6.0 * E * sec.I33, 1e-12)) if E * sec.I33 > 0 else 0.005
            theta_y_weak = (My_weak * Lp) / (max(6.0 * E * sec.I22, 1e-12)) if E * sec.I22 > 0 else 0.005
            theta_cap = theta_y * 6.0
            theta_cap_weak = theta_y_weak * 6.0

            # Axial material (elastic)
            ops.uniaxialMaterial('Elastic', hinge_mat_tag, sec.A * E / L)
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
        self.mesh_model.frame_elements = new_elements
        self.mesh_model.frame_assignments = new_assignments

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

        # ── Pre-compute frame + area self-weight per-node ────────
        # Stored as a list of (node_tag, fz) tuples; applied per-pattern
        # during the pattern loop below if the pattern's swf > 0.
        _sw_node_loads: List[Tuple[int, float]] = []
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
            total_w = _A * mat.unit_weight * L
            nd_i = self.mesh_model.nodes.get(elem.node_i)
            nd_j = self.mesh_model.nodes.get(elem.node_j)
            if nd_i is not None:
                _sw_node_loads.append((nd_i.node_tag, -total_w * 0.5))
            if nd_j is not None:
                _sw_node_loads.append((nd_j.node_tag, -total_w * 0.5))

        # ── Area element self-weight ─────────────────────────────
        from ..model.sap_data import ShellSection as _ShellSec
        for aid, area in self.mesh_model.area_elements.items():
            if getattr(area, 'inactive', False):
                continue
            sec_name = self.mesh_model.area_assignments.get(aid, '')
            sec = self.mesh_model.sections.get(sec_name)
            if sec is None or not isinstance(sec, _ShellSec):
                continue
            mat = self.mesh_model.materials.get(sec.material)
            if mat is None or mat.unit_weight == 0:
                continue
            t = getattr(sec, 'thickness', 0.0)
            if t <= 0:
                continue
            poly = [self.mesh_model.nodes.get(nid) for nid in area.node_ids]
            poly = [nd for nd in poly if nd is not None]
            if len(poly) < 3:
                continue
            area_3d = 0.0
            v0 = np.array([poly[0].x, poly[0].y, poly[0].z])
            for k in range(1, len(poly) - 1):
                v1 = np.array([poly[k].x, poly[k].y, poly[k].z])
                v2 = np.array([poly[k + 1].x, poly[k + 1].y, poly[k + 1].z])
                area_3d += 0.5 * np.linalg.norm(np.cross(v1 - v0, v2 - v0))
            total_w = area_3d * t * mat.unit_weight
            n_corners = len(poly)
            for nd in poly:
                _sw_node_loads.append((nd.node_tag, -total_w / n_corners))

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
        # Include patterns with self_weight_factor > 0 so their self-weight
        # can be activated even when they have no explicit load entries.
        for pn, lp in self.mesh_model.load_patterns.items():
            if abs(getattr(lp, 'self_weight_factor', 0.0)) > 1e-12:
                all_patterns.add(pn)
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
                T = np.column_stack([vx, vy, vz])
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
                # Look up the unsplit (original) frame element to get
                # its local axes for projecting the global direction.
                elem = self.mesh_model.frame_elements.get(ld.frame_id)
                if elem is None or getattr(elem, 'inactive', False):
                    continue
                try:
                    vx, vy, vz = self.get_local_axes(elem)
                except Exception:
                    continue
                # Determine the global direction vector
                if ld.direction == 'Gravity':
                    gdir = np.array([0.0, 0.0, -1.0])
                elif ld.direction == 'X':
                    gdir = np.array([1.0, 0.0, 0.0])
                elif ld.direction == 'Y':
                    gdir = np.array([0.0, 1.0, 0.0])
                elif ld.direction == 'Z':
                    gdir = np.array([0.0, 0.0, 1.0])
                elif ld.direction == 'LocalX':
                    gdir = vx
                elif ld.direction == 'LocalY':
                    gdir = vy
                elif ld.direction == 'LocalZ':
                    gdir = vz
                else:
                    gdir = np.array([0.0, 0.0, -1.0])
                wa = ld.val_a * scale
                wb = ld.val_b * scale
                a_overL = max(0.0, min(1.0, ld.rdist_a))
                b_overL = max(0.0, min(1.0, ld.rdist_b))
                # Project global direction onto local axes
                wx_a = wa * np.dot(gdir, vx)
                wy_a = wa * np.dot(gdir, vy)
                wz_a = wa * np.dot(gdir, vz)
                wx_b = wb * np.dot(gdir, vx)
                wy_b = wb * np.dot(gdir, vy)
                wz_b = wb * np.dot(gdir, vz)
                # Apply using the same approach as the legacy Builder
                is_uniform = abs(wa - wb) < 1e-6
                is_full_span = abs(a_overL) < 1e-12 and abs(b_overL - 1.0) < 1e-12
                if is_uniform and is_full_span:
                    ops.eleLoad('-ele', tag, '-type', '-beamUniform',
                                wy_a, wz_a, wx_a)
                elif is_uniform:
                    ops.eleLoad('-ele', tag, '-type', '-beamUniform',
                                wy_a, wz_a, wx_a, a_overL, b_overL)
                else:
                    # Non-uniform → decompose into partial-span segments
                    N = 4
                    span_frac = b_overL - a_overL
                    for i in range(N):
                        seg_a = a_overL + i * span_frac / N
                        seg_b = a_overL + (i + 1) * span_frac / N
                        xi = (i + 0.5) / N
                        wy_mid = wy_a + (wy_b - wy_a) * xi
                        wz_mid = wz_a + (wz_b - wz_a) * xi
                        wx_mid = wx_a + (wx_b - wx_a) * xi
                        ops.eleLoad('-ele', tag, '-type', '-beamUniform',
                                    wy_mid, wz_mid, wx_mid, seg_a, seg_b)
                load_total += abs(wa) * abs(b_overL - a_overL)

            # ── Self-weight for this pattern ────────────────────────
            # Apply if the pattern has self_weight_factor > 0 (e.g. DEAD swf=1).
            # Look up the pattern's swf from MeshModel load_patterns (passed
            # through from SAP2000 by the Preprocessor).
            _lp = self.mesh_model.load_patterns.get(pname)
            swf = getattr(_lp, 'self_weight_factor', 0.0) if _lp else 0.0
            if abs(swf) > 1e-12:
                sw_scale = swf * scale
                sw_total = 0.0
                _sw_fz_total = 0.0
                for node_tag, fz in _sw_node_loads:
                    ops.load(node_tag, 0.0, 0.0, fz * sw_scale, 0.0, 0.0, 0.0)
                    sw_total += abs(fz * sw_scale)
                    _sw_fz_total += fz * sw_scale
                # Store per-pattern for check_self_weight_consistency
                if pname not in self._sw_load_totals:
                    self._sw_load_totals[pname] = {k: 0.0 for k in
                                             ('fx','fy','fz','mx','my','mz')}
                self._sw_load_totals[pname]['fz'] += _sw_fz_total
                load_total += sw_total

            self.load_totals[pname] = load_total

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

        When *pattern_scales* is provided, the domain is rebuilt with
        only those load patterns active (matching the facade's behaviour).
        When *pattern_scales* is ``None`` (default), the existing domain
        is analysed as-is.

        Returns a dict with nodal_displacements, reactions, element_forces,
        and load_totals.
        """
        # Rebuild domain with new pattern scales if requested
        if pattern_scales is not None:
            self.build_domain()
            self._reapply_edge_constraints()
            self.create_loads(pattern_scales=pattern_scales)

        sol_cfg = self.config
        sd = self.PUSHOVER_SOLVER_DEFAULTS
        test_type = sol_cfg.get('solver_test_type', sd['solver_test_type'])
        test_tol = sol_cfg.get('solver_test_tol', sd['solver_test_tol'])
        test_iter = sol_cfg.get('solver_test_max_iter', sd['solver_test_max_iter'])
        algo = sol_cfg.get('solver_algorithm', sd['solver_algorithm'])
        n_sub = sol_cfg.get('gravity_num_substeps', sd['gravity_num_substeps'])

        cs = sol_cfg.get('solver_constraints', sd['solver_constraints'])
        if self._edge_constraint_method == 'penalty':
            cs = 'Penalty'
            ops.constraints('Penalty', 1.0e12, 1.0e12)
        else:
            ops.constraints(cs)
        ops.numberer('RCM')
        ops.system(sol_cfg.get('solver_system', sd['solver_system']))
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
                print(f"  ⚠ {eigen_solver} solver failed — falling back to ARPACK")
                try:
                    eigenvals_all = ops.eigen(num_modes)
                except Exception:
                    eigenvals_all = []
                if not eigenvals_all:
                    print("  ⚠ ARPACK also failed — falling back to fullGenLapack")
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
                print("  ⚠ ARPACK solver failed — falling back to fullGenLapack")
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

        **How base reactions are computed**

        ``ops.responseSpectrumAnalysis(... '-mode', mode)`` sets the
        modal displacement field for a single mode on the domain (via
        ``node->setTrialDisp()``).  After ``commitDomain()``, element
        internal forces are consistent with those displacements.  We then
        query ``ops.eleResponse(eid, 'forces')`` which returns **global
        element‑end forces** ``[Fx, Fy, Fz, Mx, My, Mz]`` at the I-end
        then J-end.

        For each base-connected element we extract the base-end forces
        and accumulate into a per-mode 6-DoF reaction vector.  The
        element-end moments include column bending directly, but the
        **axial force lever‑arm** (Fz from one column × distance to the
        centroid of another) is a structural‑level effect not present in
        individual element stiffness outputs.  We add it here:

        ``mx += Mx_direct + Fz·dy − Fy·dz``
        ``my += My_direct + Fx·dz − Fz·dx``

        using the **fixed geometric centroid** (bounding-box midpoint
        ``(min+max)/2`` of all nodes).  This fixed reference is the same
        as :func:`~fea_toolkit.utils.sum_reactions_with_overturning`
        uses for static lateral loads — ensuring consistent moment
        origins across all analysis types.

        .. note::
           A fixed reference point is **required** for CQC combination:
           if each mode used its own Fz-weighted centroid, the per-mode
           moments would reference different points and CQC would be
           physically invalid.

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
            Dictionary with:
            - ``modal_base_shear`` / ``modal_base_moment`` (scalar, backward compat)
            - ``base_shear_cqc`` / ``base_shear_srss`` / ``base_moment_cqc`` / ``base_moment_srss``
            - ``modal_periods``
            - ``modal_base_reactions`` (list of 6-DoF dicts per mode)
            - ``base_reactions_cqc`` / ``base_reactions_srss`` (6-DoF combined)
              where Mx/My include overturning from Fz × lever-arm about
              the fixed geometric centroid (bounding-box midpoint).
              This fixed reference ensures CQC validity across modes.
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
            # Use the actual OpenSees tag from the frame_tag_map so that
            # split-frame children are addressed by their correct tag.
            ops_tag = self.frame_tag_map.get(eid, elem.elem_tag)
            if elem.node_i in base_nodes and elem.node_j not in base_nodes:
                base_elements.append((ops_tag, 'i'))
            elif elem.node_j in base_nodes and elem.node_i not in base_nodes:
                base_elements.append((ops_tag, 'j'))

        # ── Pre-compute fixed reference point for overturning moment ──
        # Compute from base (support) nodes only — the centre of the base
        # footprint. This ensures a consistent reference across all modes
        # for valid CQC combination.  Same approach as
        # sum_reactions_with_overturning in utils.py.
        _base_nds = [self.mesh_model.nodes[nid] for nid in base_nodes
                     if nid in self.mesh_model.nodes]
        if _base_nds:
            _xs = [n.x for n in _base_nds]
            _ys = [n.y for n in _base_nds]
            _cx = (min(_xs) + max(_xs)) * 0.5
            _cy = (min(_ys) + max(_ys)) * 0.5
            _z_base = sum(n.z for n in _base_nds) / len(_base_nds)
        else:
            _cx = _cy = _z_base = 0.0

        # Pre-compute base-element node coordinates for lever-arm
        # Build a one-time tag-to-element index (ops tag → element)
        _elem_by_tag: Dict = {}
        for _e in elements.values():
            _elem_by_tag[_e.elem_tag] = _e

        _base_elem_coords = []
        for eid, end in base_elements:
            elem = elements.get(str(eid)) or _elem_by_tag.get(eid)
            if elem is None:
                continue
            nid = elem.node_i if end == 'i' else elem.node_j
            nd = self.mesh_model.nodes.get(nid)
            if nd is None:
                continue
            _base_elem_coords.append((eid, end, nd.x, nd.y, nd.z))

        modal_base_reactions = []
        for mode in range(1, num_modes + 1):
            ops.responseSpectrumAnalysis(SPECTRUM_TS_TAG, dof, '-mode', mode)

            rxn = {'fx': 0.0, 'fy': 0.0, 'fz': 0.0,
                   'mx': 0.0, 'my': 0.0, 'mz': 0.0}
            for eid, end, nx, ny, nz in _base_elem_coords:
                try:
                    forces = ops.eleResponse(eid, 'forces')
                except Exception:
                    continue
                if end == 'i':
                    fx, fy, fz, mx, my, mz = forces[0], forces[1], forces[2], forces[3], forces[4], forces[5]
                else:
                    fx, fy, fz, mx, my, mz = forces[6], forces[7], forces[8], forces[9], forces[10], forces[11]

                rxn['fx'] += fx
                rxn['fy'] += fy
                rxn['fz'] += fz
                # Overturning: direct moment + force × lever-arm about fixed reference
                dx = nx - _cx
                dy = ny - _cy
                dz = nz - _z_base
                rxn['mx'] += mx + fz * dy - fy * dz
                rxn['my'] += my + fx * dz - fz * dx
                rxn['mz'] += mz + fy * dx - fx * dy

            modal_base_reactions.append(rxn)

        # ── CQC / SRSS per component ───────────────────────────
        dof_map = {'X': (0, 4), 'Y': (1, 3), 'Z': (2, 4)}
        #   X: shear=fx(idx 0), overturning=my(idx 4)
        #   Y: shear=fy(idx 1), overturning=mx(idx 3)  ← was mz before fix
        #   Z: shear=fz(idx 2), overturning=my(idx 4)
        f_idx, m_idx = dof_map[direction]
        comp_order = ['fx', 'fy', 'fz', 'mx', 'my', 'mz']

        # Keep scalar arrays for backward compat
        modal_base_shear = [r[comp_order[f_idx]] for r in modal_base_reactions]
        modal_base_moment = [r[comp_order[m_idx]] for r in modal_base_reactions]

        base_reactions_cqc = {}
        base_reactions_srss = {}
        for comp in comp_order:
            vals = [r[comp] for r in modal_base_reactions]
            base_reactions_cqc[comp] = cqc_combine(vals, omega, damp_ratios)
            base_reactions_srss[comp] = math.sqrt(sum(v * v for v in vals))

        base_shear_cqc = base_reactions_cqc[comp_order[f_idx]]
        base_shear_srss = base_reactions_srss[comp_order[f_idx]]
        base_moment_cqc = base_reactions_cqc[comp_order[m_idx]]
        base_moment_srss = base_reactions_srss[comp_order[m_idx]]

        result = {
            'modal_base_shear': modal_base_shear,
            'modal_base_moment': modal_base_moment,
            'base_shear_cqc': base_shear_cqc,
            'base_shear_srss': base_shear_srss,
            'base_moment_cqc': base_moment_cqc,
            'base_moment_srss': base_moment_srss,
            'modal_periods': modal_periods,
            # New: full 6-DoF base reactions per-mode and combined
            'modal_base_reactions': modal_base_reactions,
            'base_reactions_cqc': base_reactions_cqc,
            'base_reactions_srss': base_reactions_srss,
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

    # =========================================================================
    # RS element forces (after run_response_spectrum_analysis)
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
        if self.config.get('verbose'):
            print("Extracting element RS forces...")

        omega = [2.0 * math.pi / T if T > 0 else 0.0 for T in modal_periods]
        damp_ratios = [damping_ratio] * num_modes

        dof = {'X': 1, 'Y': 2, 'Z': 3}[direction]

        SPECTRUM_TS_TAG = 9999

        elements = self.mesh_model.frame_elements

        # Pre-compute element info + storage
        elem_data = {}
        for eid, elem in elements.items():
            if getattr(elem, 'inactive', False):
                continue
            ni = self.mesh_model.nodes.get(elem.node_i)
            nj = self.mesh_model.nodes.get(elem.node_j)
            if ni is None or nj is None:
                continue
            z_i, z_j = ni.z, nj.z
            if z_i > z_j:
                z_i, z_j = z_j, z_i
            ops_tag = self.frame_tag_map.get(eid, elem.elem_tag)
            elem_data[eid] = {
                'tag': ops_tag,
                'elem_id': eid,
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
            n_use = min(ne, num_modes)
            o_use = omega[:n_use]
            d_use = damp_ratios[:n_use]

            My_i = cqc_combine(ed['My_i'][:n_use], o_use, d_use)
            My_j = cqc_combine(ed['My_j'][:n_use], o_use, d_use)
            Mz_i = cqc_combine(ed['Mz_i'][:n_use], o_use, d_use)
            Mz_j = cqc_combine(ed['Mz_j'][:n_use], o_use, d_use)

            # Element length
            elem = elements.get(eid)
            if elem:
                ni = self.mesh_model.nodes.get(elem.node_i)
                nj = self.mesh_model.nodes.get(elem.node_j)
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
                'elem_id': ed['elem_id'],
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
                      f"{'Vy (kN)':>12} {'Vz (kN)':>12} {'My (kN-m)':>12} {'Mz (kN-m)':>12}")
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
        return_srss: bool = False,
    ) -> Union[Dict[int, Tuple[float, float, float]],
               Tuple[Dict[int, Tuple[float, float, float]],
                     Dict[int, Tuple[float, float, float]]]]:
        """Compute CQC‑ (and optionally SRSS‑) combined peak nodal
        displacements from RS analysis.

        Uses mode‑shape superposition rather than re‑running the RS analysis:

            u_m = Γ_m · φ_m · Sa_m / ω²_m

        then combined with CQC (and optionally SRSS).

        Args:
            num_modes: Number of modes.
            modal_periods: Natural periods of each mode (s).
            eigenvalues: Eigenvalues (ω²) from :meth:`run_modal_analysis`.
            spectrum_func: Callable ``f(T) → Sa`` in **m/s²**.
            direction: Excitation direction ``'X'``, ``'Y'``, or ``'Z'``.
            damping_ratio: Damping ratio for CQC correlation.
            return_srss: If True, return ``(cqc_result, srss_result)``
                as a tuple of two dicts.  If False (default), return
                only ``cqc_result`` for backward compatibility.

        Returns:
            Dict mapping ``node_tag`` → ``(dx, dy, dz)`` in model length
            units.  When ``return_srss=True``, returns a tuple of two
            such dicts: ``(cqc, srss)``.
        """
        dof = {'X': 1, 'Y': 2, 'Z': 3}[direction]
        dof_idx = dof - 1

        # Get participation factors from modalProperties
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

        node_tags = list(ops.getNodeTags())

        per_mode = {tag: {d: [] for d in range(3)} for tag in node_tags}

        for m in range(num_modes):
            if eigenvalues[m] <= 1e-12 or omega[m] <= 1e-12:
                for tag in node_tags:
                    for d in range(3):
                        per_mode[tag][d].append(0.0)
                continue

            T = modal_periods[m]
            Sa = spectrum_func(T)
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
                for d in range(3):
                    if d != dof_idx:
                        per_mode[tag][d].append(0.0)

        cqc_result = {}
        srss_result = {}
        for tag in node_tags:
            cqc_vals = tuple(
                cqc_combine(per_mode[tag][d], omega, damp)
                for d in range(3)
            )
            cqc_result[tag] = cqc_vals
            srss_vals = tuple(
                math.sqrt(sum(v * v for v in per_mode[tag][d]))
                for d in range(3)
            )
            srss_result[tag] = srss_vals

        if return_srss:
            return cqc_result, srss_result
        return cqc_result

    def extract_mode_shapes(
        self, num_modes: int
    ) -> Dict[int, Dict[int, Tuple[float, float, float]]]:
        """Extract mode shape displacements for each node and each mode.

        Must be called **after** :meth:`run_modal_analysis`.

        Args:
            num_modes: Number of modes to extract.

        Returns:
            ``{mode_index: {node_tag: (dx, dy, dz)}}`` where *mode_index*
            is 0‑based and displacements are raw eigenvector components.
        """
        node_tags = list(ops.getNodeTags())
        dof_map = {0: 1, 1: 2, 2: 3}
        shapes: Dict[int, Dict[int, Tuple]] = {}
        for m in range(num_modes):
            mode_num = m + 1
            per_node: Dict[int, Tuple] = {}
            for tag in node_tags:
                dx = ops.nodeEigenvector(tag, mode_num, dof_map[0])
                dy = ops.nodeEigenvector(tag, mode_num, dof_map[1])
                dz = ops.nodeEigenvector(tag, mode_num, dof_map[2])
                per_node[tag] = (dx, dy, dz)
            shapes[m] = per_node
        return shapes

    def extract_static_element_forces(self) -> Dict[int, Dict[str, float]]:
        """Extract element end forces in the **global** coordinate system.

        Must be called **after** :meth:`run_static_analysis`.

        Returns:
            Dict mapping ``elem_tag`` → dict with keys ``'Fx'``, ``'Fy'``,
            ``'Fz'``, ``'Mx'``, ``'My'``, ``'Mz'`` (global forces at the
            I‑end of the element) and ``'Fx_j'``, ``'Fy_j'``, ``'Fz_j'``,
            ``'Mx_j'``, ``'My_j'``, ``'Mz_j'`` (J‑end).
        """
        elements = self.mesh_model.frame_elements
        results = {}
        for eid, elem in elements.items():
            if getattr(elem, 'inactive', False):
                continue
            # Resolve the OpenSees element tag — may differ from elem.elem_tag
            # when the Preprocessor creates frame elements with deterministic
            # tags stored in frame_tag_map.
            tag = self.frame_tag_map.get(eid, elem.elem_tag)
            try:
                f = ops.eleResponse(tag, 'forces')
            except Exception:
                continue
            f_i_global = np.array([f[0], f[1], f[2]])
            m_i_global = np.array([f[3], f[4], f[5]])
            f_j_global = np.array([f[6], f[7], f[8]])
            m_j_global = np.array([f[9], f[10], f[11]])

            results[tag] = {
                'Fx': f_i_global[0], 'Fy': f_i_global[1], 'Fz': f_i_global[2],
                'Mx': m_i_global[0], 'My': m_i_global[1], 'Mz': m_i_global[2],
                'Fx_j': f_j_global[0], 'Fy_j': f_j_global[1], 'Fz_j': f_j_global[2],
                'Mx_j': m_j_global[0], 'My_j': m_j_global[1], 'Mz_j': m_j_global[2],
            }
        return results

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
        from ..model.geometry import get_local_axes
        ni = self.mesh_model.nodes.get(elem.node_i)
        nj = self.mesh_model.nodes.get(elem.node_j)
        if ni is None or nj is None:
            raise ValueError(f"Cannot resolve nodes for {elem.elem_id}")
        vx = np.array([nj.x - ni.x, nj.y - ni.y, nj.z - ni.z])
        return get_local_axes(vx, getattr(elem, 'angle', 0.0))

    # ═══════════════════════════════════════════════════════════════
    # Load equilibrium check
    # ═══════════════════════════════════════════════════════════════

    def check_load_equilibrium(self) -> "pd.DataFrame":
        """Check equilibrium between applied loads and reactions.

        For each load pattern in the model, runs a static analysis
        with that pattern alone and compares the applied load totals
        (from :attr:`load_totals`) against the summed reactions.

        Reaction moments include the force × lever‑arm overturning
        contribution via
        :func:`~fea_toolkit.utils.sum_reactions_with_overturning`
        (same fixed centroid approach used for RS analysis in
        :meth:`run_response_spectrum_analysis`).

        Returns:
            A ``pandas.DataFrame`` with one row per pattern and
            columns for applied force, reaction force, and
            the equilibrium imbalance ``Δ = applied + reaction``
            (should be near zero for a correctly built model).
        """
        import pandas as pd

        rows: list = []
        fu = self.mesh_model.units.get('F', '?')
        # Collect pattern names from the MeshModel's load_patterns dict
        # (matching the legacy Builder which iterates self.model.load_patterns).
        # Skip patterns with zero applied loads (e.g. SLX, SLY).
        _patterns = set()
        for pname, lp in self.mesh_model.load_patterns.items():
            # Check for any loads associated with this pattern
            has_loads = (
                lp.self_weight_factor > 0
                or any(ld.pattern == pname for ld in self.mesh_model.frame_dist_loads)
                or any(ld.pattern == pname for ld in self.mesh_model.joint_loads)
                or any(ld.pattern == pname for ld in self.mesh_model.area_gravity_loads)
                or any(ld.pattern == pname for ld in self.mesh_model.edge_loads_from_areas)
            )
            if has_loads:
                _patterns.add(pname)
        for pname in sorted(_patterns, key=str.casefold):
            result = self.run_static_analysis(
                extract_reactions=True,
                pattern_scales={pname: 1.0},
            )
            rxn = result.get('reactions', {})
            rx = sum(v['fx'] for v in rxn.values())
            ry = sum(v['fy'] for v in rxn.values())
            rz = sum(v['fz'] for v in rxn.values())

            rows.append({
                'Load Pattern': pname,
                f'Reaction Fx ({fu})': round(rx, 1),
                f'Reaction Fy ({fu})': round(ry, 1),
                f'Reaction Fz ({fu})': round(rz, 1),
            })

        return pd.DataFrame(rows)

    # ═══════════════════════════════════════════════════════════════
    # Export
    # ═══════════════════════════════════════════════════════════════

    def export_results(self,
                      filepath: str,
                      static_results: Optional[Dict[str, Any]] = None,
                      modal_result: Optional[Dict[str, Any]] = None,
                      mode_shapes: Optional[Dict] = None,
                      rs_results: Optional[Dict[str, Dict]] = None,
                      rs_element_forces: Optional[Dict[str, Any]] = None,
                      rs_nodal_displacements: Optional[Dict[int, tuple]] = None,
                      fmt: str = "npz",
                      ) -> str:
        """Export model geometry and analysis results to a unified file.

        Delegates to :func:`~fea_toolkit.io.unified_writer.write_results`
        using the builder's ``mesh_model`` and the provided results.

        Args:
            filepath: Output file path (``.npz`` or ``.h5``).
            static_results: Dict from :meth:`run_static_analysis`.
            modal_result: Dict from
                :meth:`~fea_toolkit.opensees.builder.OpenSeesBuilder.run_modal_analysis`.
            mode_shapes: Mode shape eigenvectors ``{mode_idx: {tag: (dx,dy,dz)}}``.
            rs_results: Response-spectrum results dict.
            rs_element_forces: Dict from :meth:`extract_element_rs_forces`.
            rs_nodal_displacements: Dict from
                :meth:`compute_rs_nodal_displacements`.
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
            rs_element_forces=rs_element_forces,
            rs_nodal_displacements=rs_nodal_displacements,
            fmt=fmt,
            config=self.config,
        )

    # ═══════════════════════════════════════════════════════════════
    # Pushover analysis
    # ═══════════════════════════════════════════════════════════════

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
        """Run a displacement‑controlled pushover analysis.

        **Two‑stage process:**

        1. **Gravity** — apply the specified gravity patterns via
           :meth:`run_static_analysis` with ``extract_reactions=True``.
        2. **Lateral push** — lock gravity, apply lateral loads, then
           push a control node in increments using
           ``DisplacementControl`` integration.

        Four lateral load types are supported:

        * ``'uniform'`` — mass‑proportional acceleration (uniform
          acceleration of the structure).
        * ``'triangular'`` — load proportional to :math:`m_i h_i^k`
          per ASCE 7 equivalent lateral force.
        * ``'mode1'`` — load proportional to the fundamental
          eigenvector :math:`\\mathbf{M} \\boldsymbol{\\phi}_1`
          (modal pushover).
        * ``'pattern'`` — read an existing SAP2000 load pattern
          (frame distributed loads) from the model data.

        Args:
            gravity_patterns: Dict mapping load pattern name → scale
                factor for gravity loads, e.g. ``{"DEAD": 1.0}``.
            lateral_load_type: ``'uniform'``, ``'triangular'``,
                ``'mode1'``, or ``'pattern'``.
            lateral_pattern_name: SAP2000 load pattern name (required
                when *lateral_load_type* is ``'pattern'``).
            lateral_direction: Push direction — ``'X'``, ``'Y'``, or
                ``'Z'``.
            control_node_tag: OpenSees node tag for displacement
                control.  ``None`` = auto‑select (highest unrestrained
                node in the push direction).
            max_disp: Target displacement at the control node (m).
            num_steps: Number of push steps.
            fundamental_period: Fundamental period (s) for
                ``'triangular'`` load exponent ``k``.  ``None`` uses
                the period of the first mode from the model.
            mode_shapes: Dict ``{mode_idx: {node_tag: (dx, dy, dz)}}``
                from :meth:`extract_mode_shapes`; required for
                ``'mode1'``.
            mode_index: Mode index (0‑based) for ``'mode1'``.
            print_progress: Print a progress line per step.

        Returns:
            Dict with keys ``step``, ``control_disp``, ``base_shear``,
            ``status``, ``gravity_displacements``, ``control_node``,
            ``dof``, ``lateral_load_type``.
        """
        valid_types = {'uniform', 'triangular', 'mode1', 'pattern'}
        if lateral_load_type not in valid_types:
            raise ValueError(
                f"Unknown lateral_load_type '{lateral_load_type}'. "
                f"Choose from {valid_types}."
            )
        if lateral_load_type == 'pattern' and not lateral_pattern_name:
            raise ValueError(
                "lateral_pattern_name is required when "
                "lateral_load_type='pattern'"
            )

        if self.config.get('verbose') or print_progress:
            print(f"Running pushover: {lateral_load_type} in "
                  f"{lateral_direction}, {num_steps} steps, "
                  f"max disp = {max_disp:.3f} m")

        dof = {'X': 1, 'Y': 2, 'Z': 3}[lateral_direction]

        # ── Rebuild with fiber sections ──────────────────────────
        # Pushover always attempts fiber sections (nonlinear).  Check
        # whether any section overrides the base to_fiber_patches —
        # if none do, fall back to elastic sections.
        # Note: brace_truss is orthogonal — braces use Hysteretic truss
        # elements while beams/columns can still use fiber sections.
        _use_fiber = True
        from ..model.sap_data import Section as _BaseSection, ShellSection
        for sec in self.mesh_model.sections.values():
            if isinstance(sec, ShellSection):
                continue
            try:
                sec.to_fiber_patches(mat_tag=1)
            except NotImplementedError:
                _use_fiber = False
                break

        if not _use_fiber:
            overrides: Dict[str, Any] = {
                'element_type': 'elasticBeamColumn',
                'create_fiber_sections': False,
                'use_elastic_sections': True,
            }
            self.build_domain(config_overrides=overrides)
        else:
            self.rebuild_with_fiber_sections(
                brace_selection=self._brace_selection,
            )

        # ── Re-apply edge constraints ────────────────────────────
        _spring_scale = float(self.config.get('pushover_spring_scale', 1.0))
        if self._saved_edge_constraints and _spring_scale > 0:
            for args in self._saved_edge_constraints:
                coarse_edges, fine_nodes, coarse_elems, tolerance, k, verbose = args
                if _spring_scale != 1.0 and k is not None:
                    k = k * _spring_scale
                self.apply_edge_constraints(
                    coarse_edges=coarse_edges,
                    fine_nodes=fine_nodes,
                    coarse_elements=coarse_elems,
                    tolerance=tolerance,
                    penalty_stiffness=k,
                    verbose=verbose or self.config.get('verbose', False),
                )
            if self.config.get('verbose', False) or print_progress:
                n = len(self._saved_edge_constraints)
                print(f"  Re-applied edge constraints from {n} tear(s)")

        # ── Seismic masses (for lateral load shape) ──────────────
        try:
            self.compute_seismic_masses()
        except Exception:
            if self.config.get('verbose'):
                print("  compute_seismic_masses failed, using fallback masses")
            self._compute_fallback_masses()

        # ── Gravity analysis ─────────────────────────────────────
        # Create loads directly (domain was just rebuilt by
        # rebuild_with_fiber_sections above).  Avoid passing
        # pattern_scales to run_static_analysis, which would trigger
        # a second build_domain() without fiber overrides, replacing
        # dispBeamColumn elements with elasticBeamColumn.
        self.create_loads(pattern_scales=gravity_patterns)
        grav_results = self.run_static_analysis(
            extract_reactions=True,
        )
        grav_disp = (
            grav_results.get('nodal_displacements', {})
            if grav_results else {}
        )

        # ── Control node auto‑select ─────────────────────────────
        if control_node_tag is None:
            candidate = None
            max_z = -1e12
            for nid, nd in self.mesh_model.nodes.items():
                restraint = self.mesh_model.restraints.get(nid)
                if restraint and len(restraint.dofs) > dof - 1:
                    if restraint.dofs[dof - 1] == 1:
                        continue  # restrained in push direction
                try:
                    z = ops.nodeCoord(nd.node_tag)[2]
                except Exception:
                    continue
                if z > max_z:
                    max_z = z
                    candidate = nd.node_tag
            if candidate is not None:
                control_node_tag = candidate
            else:
                raise RuntimeError(
                    "Could not auto-select control node — "
                    "no unrestrained nodes found"
                )

        if print_progress:
            print(f"  Control node = {control_node_tag}")

        # ── Record gravity control displacement ──────────────────
        try:
            grav_ctrl_disp = ops.nodeDisp(int(control_node_tag))[dof - 1]
        except Exception:
            grav_ctrl_disp = 0.0

        # ── Lock gravity ─────────────────────────────────────────
        ops.loadConst('-time', 0.0)

        # Find a free pattern tag
        _pat_tag = 9001
        try:
            existing = ops.getLoadPatternTags()
            if existing:
                _pat_tag = max(max(existing), 9000) + 1
        except Exception:
            pass

        # ── Apply lateral loads ──────────────────────────────────
        if lateral_load_type == 'pattern':
            # Use existing SAP2000 frame distributed loads projected
            # onto the push direction.
            dir_map = {'Gravity': (0,0,-1), 'X': (1,0,0),
                       'Y': (0,1,0), 'Z': (0,0,1)}

            for ld in self.mesh_model.frame_dist_loads:
                if ld.pattern != lateral_pattern_name:
                    continue

                gx, gy, gz = dir_map.get(ld.direction, (0, 0, 0))
                elem = self.mesh_model.frame_elements.get(ld.frame_id)
                if elem is None or getattr(elem, 'inactive', False):
                    continue
                ops_tag = self.frame_tag_map.get(ld.frame_id, elem.elem_tag)

                wa, wb = float(ld.val_a), float(ld.val_b)
                aL, bL = ld.rdist_a, ld.rdist_b

                nd_i = self.mesh_model.nodes.get(elem.node_i)
                nd_j = self.mesh_model.nodes.get(elem.node_j)
                if nd_i is None or nd_j is None:
                    continue
                axis = np.array([nd_j.x - nd_i.x, nd_j.y - nd_i.y, nd_j.z - nd_i.z])
                try:
                    vx, vy, vz = get_local_axes(axis, getattr(elem, 'angle', 0.0))
                except Exception:
                    continue

                T = np.column_stack([vx, vy, vz])
                g_local = np.linalg.solve(T, np.array([gx, gy, gz]))
                wy_a = g_local[1] * wa
                wz_a = g_local[2] * wa
                wx_a = g_local[0] * wa
                wy_b = g_local[1] * wb
                wz_b = g_local[2] * wb
                wx_b = g_local[0] * wb

                if abs(wa) < 1e-12 and abs(wb) < 1e-12:
                    continue

                is_uniform = abs(wa - wb) < 1e-12
                if is_uniform and abs(aL) < 1e-12 and abs(bL - 1.0) < 1e-12:
                    ops.eleLoad('-ele', ops_tag, '-type', '-beamUniform',
                                wy_a, wz_a, wx_a)
                elif is_uniform:
                    ops.eleLoad('-ele', ops_tag, '-type', '-beamUniform',
                                wy_a, wz_a, wx_a, aL, bL)
                else:
                    for i in range(4):
                        span = bL - aL
                        seg_a = aL + i * span / 4
                        seg_b = aL + (i + 1) * span / 4
                        xi = (i + 0.5) / 4
                        ops.eleLoad('-ele', ops_tag, '-type', '-beamUniform',
                                    wy_a + (wy_b - wy_a) * xi,
                                    wz_a + (wz_b - wz_a) * xi,
                                    wx_a + (wx_b - wx_a) * xi,
                                    seg_a, seg_b)

            if print_progress:
                n = sum(1 for ld in self.mesh_model.frame_dist_loads
                        if ld.pattern == lateral_pattern_name)
                print(f"  Applied lateral loads from pattern "
                      f"'{lateral_pattern_name}' ({n} load(s))")
        else:
            ops.timeSeries('Linear', _pat_tag)
            ops.pattern('Plain', _pat_tag, _pat_tag)

            if lateral_load_type == 'uniform':
                node_loads = self._compute_uniform_lateral_loads(
                    direction=lateral_direction,
                    node_masses=self.node_masses,
                )
            elif lateral_load_type == 'triangular':
                node_loads = self._compute_triangular_lateral_loads(
                    direction=lateral_direction,
                    node_masses=self.node_masses,
                    fundamental_period=fundamental_period,
                )
            elif lateral_load_type == 'mode1':
                if mode_shapes is None:
                    raise ValueError(
                        "mode_shapes is required when "
                        "lateral_load_type='mode1'"
                    )
                node_loads = self._compute_mode_shape_lateral_loads(
                    direction=lateral_direction,
                    node_masses=self.node_masses,
                    mode_shapes=mode_shapes,
                    mode_index=mode_index,
                )
            else:
                node_loads = {}

            for tag, (fx, fy, fz) in node_loads.items():
                ops.load(int(tag), fx, fy, fz, 0.0, 0.0, 0.0)

            n_loaded = len(node_loads)
            if print_progress:
                print(f"  Applied lateral loads ({lateral_load_type}) "
                      f"to {n_loaded} node(s)")

        # ── Displacement‑controlled push analysis setup ──────────
        disp_inc = max_disp / max(num_steps, 1)

        # Use looser tolerances matching v1 (builder.py) pushover —
        # NormDispIncr with 1e-4 tolerance, 20 iterations, energy
        # norm.  Tight tolerances (1e-6/10 iter) prevent convergence
        # for mode-shape-based pushover patterns.
        _algo = self.config.get('solver_algorithm', 'Newton')
        _test_tol = self.config.get('solver_test_tol', 1e-4)
        _test_iter = self.config.get('solver_test_max_iter', 20)
        _system = self.config.get('solver_system', 'BandGen')

        ops.wipeAnalysis()
        _cs = self.config.get('solver_constraints', 'Transformation')
        if self._edge_constraint_method == 'penalty':
            _cs = 'Penalty'
            ops.constraints('Penalty', 1.0e12, 1.0e12)
        else:
            ops.constraints(_cs)
        ops.numberer('RCM')
        ops.system(_system)
        ops.test('NormDispIncr', _test_tol, _test_iter, 0, 2)

        ops.integrator('DisplacementControl',
                       int(control_node_tag), dof, disp_inc)
        ops.analysis('Static')

        # ── Gravity state (step 0) ───────────────────────────────
        steps: List[int] = [0]
        ctrl_disps: List[float] = [0.0]
        base_shears: List[float] = [0.0]
        statuses: List[int] = [0]

        try:
            ops.reactions()
            bs0 = 0.0
            for nid, nd in self.mesh_model.nodes.items():
                r = self.mesh_model.restraints.get(nid)
                if r and len(r.dofs) > dof - 1 and r.dofs[dof - 1] == 1:
                    try:
                        rxn = ops.nodeReaction(nd.node_tag, dof)
                        if isinstance(rxn, (list, tuple)):
                            rxn = rxn[0] if rxn else 0.0
                        bs0 += float(rxn)
                    except Exception:
                        pass
            base_shears[0] = bs0
        except Exception:
            pass

        # ── Push loop with algorithm fallback chain ──────────────
        for step in range(1, num_steps + 1):
            _algo_chain: List = [_algo]
            if _algo != 'NewtonLineSearch':
                _algo_chain.append('NewtonLineSearch')
            if _algo != 'ModifiedNewton':
                _algo_chain.append(('ModifiedNewton', '-initial'))
            _algo_chain.append('KrylovNewton')

            ok = -1
            for attempt in _algo_chain:
                if isinstance(attempt, tuple):
                    ops.algorithm(attempt[0], attempt[1])
                else:
                    ops.algorithm(attempt)
                ok = ops.analyze(1)
                if ok == 0:
                    break

            statuses.append(ok)

            # Record control node displacement (relative to gravity)
            try:
                cd_total = ops.nodeDisp(int(control_node_tag))[dof - 1]
                cd = cd_total - grav_ctrl_disp
            except Exception:
                cd = 0.0
            ctrl_disps.append(cd)

            # Calculate base shear
            try:
                ops.reactions()
                bs = 0.0
                for nid, nd in self.mesh_model.nodes.items():
                    r = self.mesh_model.restraints.get(nid)
                    if r and len(r.dofs) > dof - 1 and r.dofs[dof - 1] == 1:
                        try:
                            rxn = ops.nodeReaction(nd.node_tag, dof)
                            if isinstance(rxn, (list, tuple)):
                                rxn = rxn[0] if rxn else 0.0
                            bs += float(rxn)
                        except Exception:
                            pass
            except Exception:
                bs = 0.0
            base_shears.append(bs)
            steps.append(step)

            if print_progress:
                s = '✓' if ok == 0 else '✗'
                print(f"    Step {step:4d}/{num_steps}: "
                      f"u={cd:.6f} m  V={bs:.2f} kN  {s}")

            if ok != 0:
                if print_progress:
                    print("    Push stopped — non-converged step "
                          f"(last algorithm: {_algo_chain[-1]})")
                break

        return {
            'step': steps,
            'control_disp': ctrl_disps,
            'base_shear': base_shears,
            'status': statuses,
            'gravity_displacements': grav_disp,
            'control_node': control_node_tag,
            'dof': dof,
            'lateral_load_type': lateral_load_type,
        }

    # ═══════════════════════════════════════════════════════════════
    # Pushover helpers
    # ═══════════════════════════════════════════════════════════════

    def _compute_fallback_masses(self) -> Dict[str, float]:
        """Compute nodal masses from element self‑weight when no MASS SOURCE.

        Used as a fallback when the model has no mass source definitions.
        Masses are used to define the shape of uniform/triangular pushover
        load patterns.
        """
        from ..utils import g_from_units
        g = g_from_units(self.mesh_model.units)
        node_mass: Dict[str, float] = {}

        for eid, elem in self.mesh_model.frame_elements.items():
            if getattr(elem, 'inactive', False):
                continue
            sec_name = self.mesh_model.frame_assignments.get(eid)
            if not sec_name or sec_name not in self.mesh_model.sections:
                continue
            sec = self.mesh_model.sections[sec_name]
            mat = self.mesh_model.materials.get(sec.material)
            if mat is None or mat.unit_weight == 0:
                continue
            ni = self.mesh_model.nodes.get(elem.node_i)
            nj = self.mesh_model.nodes.get(elem.node_j)
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

        Per ASCE 41 / ATC‑40 \"Uniform\" pattern — each node with mass
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
            node = self.mesh_model.nodes.get(nid)
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
        z_vals = [node.z for node in self.mesh_model.nodes.values()]
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
            node = self.mesh_model.nodes.get(nid)
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
        """Compute mode‑shape‑proportional lateral loads $F_i = m_i \\cdot |\\phi_i|$.

        Each node receives a load proportional to its mass times the
        **absolute value** of the eigenvector component in the push
        direction.  Using absolute values ensures all loads act in
        the same direction — without this, nodes with opposite mode-
        shape signs would oppose the push, creating a near-self-
        equilibrating pattern that prevents convergence.

        The sign of the control-node mode shape is used to set the
        global direction (positive or negative push).

        Returns:
            ``{node_tag: (fx, fy, fz)}`` in global coordinates.
        """
        if mode_index not in mode_shapes:
            raise ValueError(f"Mode index {mode_index} not found in mode_shapes")

        mode = mode_shapes[mode_index]
        dof_idx = {'X': 0, 'Y': 1, 'Z': 2}.get(direction.upper(), 0)

        nodal_loads: Dict[int, Tuple[float, float, float]] = {}
        for nid, mass in node_masses.items():
            if mass <= 0:
                continue
            node = self.mesh_model.nodes.get(nid)
            if node is None:
                continue
            phi = mode.get(node.node_tag, (0.0, 0.0, 0.0))
            f_mag = mass * abs(phi[dof_idx])
            if abs(f_mag) < 1e-12:
                continue
            f = [0.0, 0.0, 0.0]
            f[dof_idx] = f_mag
            nodal_loads[node.node_tag] = (f[0], f[1], f[2])
        return nodal_loads

    # ═══════════════════════════════════════════════════════════════
    # Capacity Spectrum Method (CSM)
    # ═══════════════════════════════════════════════════════════════

    def pushover_to_adrs(
        self,
        pushover_results: Dict[str, Any],
        modal_results: Dict[str, Any],
        mode_shapes: Dict[int, Dict[int, Tuple[float, float, float]]],
        direction: str = 'X',
        g: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Convert a pushover capacity curve to ADRS coordinates.

        Delegates to :func:`~fea_toolkit.model.csm.pushover_to_adrs`.

        Args:
            pushover_results: Output from :meth:`run_pushover_analysis`.
            modal_results: Output from :meth:`run_modal_analysis`.
            mode_shapes: Output from :meth:`extract_mode_shapes`.
            direction: Push direction (``'X'``, ``'Y'``, or ``'Z'``).
            g: Gravitational acceleration (m/s²).

        Returns:
            Dict with ``'S_a'``, ``'S_d'``, ``'Gamma'``, ``'M_eff'``,
            ``'phi_control'`` — see :func:`~fea_toolkit.model.csm.pushover_to_adrs`.
        """
        from ..model.csm import pushover_to_adrs as _csm_pushover_to_adrs
        return _csm_pushover_to_adrs(
            pushover_results=pushover_results,
            modal_results=modal_results,
            mode_shapes=mode_shapes,
            direction=direction,
            g=g,
        )

    def compute_performance_point(
        self,
        pushover_results: Dict[str, Any],
        modal_results: Dict[str, Any],
        mode_shapes: Dict[int, Dict[int, Tuple[float, float, float]]],
        spectrum_periods: List[float],
        spectrum_accels: List[float],
        direction: str = 'X',
        g: Optional[float] = None,
        damping_ratio: float = 0.05,
        max_iter: int = 50,
        tol: float = 0.01,
    ) -> Dict[str, Any]:
        """Find the performance point using the Capacity Spectrum Method.

        Delegates to :func:`~fea_toolkit.model.csm.compute_performance_point`.

        Args:
            pushover_results: Output from :meth:`run_pushover_analysis`.
            modal_results: Output from :meth:`run_modal_analysis`.
            mode_shapes: Output from :meth:`extract_mode_shapes`.
            spectrum_periods: Periods (s) defining the elastic demand spectrum.
            spectrum_accels: Spectral accelerations (m/s²).
            direction: Push direction.
            g: Gravitational acceleration.
            damping_ratio: Elastic damping ratio (default 0.05).
            max_iter: Maximum iterations (default 50).
            tol: Convergence tolerance on S_d (default 0.01).

        Returns:
            Dict with ``'S_dp'``, ``'S_ap'``, ``'V_base'``, ``'D_roof'``,
            ``'T_eq'``, ``'mu'``, ``'converged'`` — see
            :func:`~fea_toolkit.model.csm.compute_performance_point`.
        """
        from ..model.csm import compute_performance_point as _csm_compute
        return _csm_compute(
            pushover_results=pushover_results,
            modal_results=modal_results,
            mode_shapes=mode_shapes,
            spectrum_periods=spectrum_periods,
            spectrum_accels=spectrum_accels,
            direction=direction,
            g=g,
            damping_ratio=damping_ratio,
            max_iter=max_iter,
            tol=tol,
        )


def run_modal(mesh_model, n_modes: int = 12,
              config: dict = None):
    """Run modal analysis through the two-stage path.

    Returns the same dict as :meth:`AnalysisBuilder.run_modal_analysis`.
    """
    from .analysis_builder import AnalysisBuilder
    if config is None:
        config = {"verbose": False}
    ab = AnalysisBuilder(mesh_model, config)
    ab.build_domain()
    ab.compute_seismic_masses()
    modal = ab.run_modal_analysis(num_modes=n_modes, print_results=False)
    shapes = ab.extract_mode_shapes(n_modes)
    return {"modal": modal, "shapes": shapes}
