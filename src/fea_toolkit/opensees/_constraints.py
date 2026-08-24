"""Analysis-builder mixin: edge constraints, nodes, and restraints."""

import logging
from typing import Any, Optional

import numpy as np
import openseespy.opensees as ops

from ..model.geometry import get_local_axes
from ..model.sap_data import FrameElement

logger = logging.getLogger(__name__)


class ConstraintMixin:
    """Edge-constraint application, node/restraint creation, and connectivity diagnostics."""

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
                verbose=verbose or self.config.get("verbose", False),
            )

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
        return {
            aid
            for aid in self.mesh_model.area_elements
            if not getattr(self.mesh_model.area_elements[aid], "inactive", False)
        }

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
        return get_local_axes(vec_x, getattr(elem, "angle", 0.0))

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

    def apply_edge_constraints(
        self,
        coarse_edges: Optional[list[tuple[int, int]]] = None,
        fine_nodes: Optional[list[int]] = None,
        coarse_elements: Optional[list[int]] = None,
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
        method = self.config.get("constraint_method", "spring")
        if method == "spring":
            return self.apply_spring_edge_constraints(
                coarse_edges=coarse_edges,
                fine_nodes=fine_nodes,
                coarse_elements=coarse_elements,
                tolerance=tolerance,
                penalty_stiffness=penalty_stiffness,
                verbose=verbose,
            )
        if method == "penalty":
            return self._apply_penalty_edge_constraints(
                coarse_edges=coarse_edges,
                fine_nodes=fine_nodes,
                coarse_elements=coarse_elements,
                tolerance=tolerance,
                verbose=verbose,
            )
        raise ValueError(f"Unknown constraint_method '{method}'. Choose 'spring' or 'penalty'.")

    def apply_spring_edge_constraints(
        self,
        coarse_edges: Optional[list[tuple[int, int]]] = None,
        fine_nodes: Optional[list[int]] = None,
        coarse_elements: Optional[list[int]] = None,
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
                if getattr(self.mesh_model.area_elements[_aid], "inactive", False):
                    continue
                _sec_name = self.mesh_model.area_assignments.get(_aid)
                if _sec_name:
                    _sec = self.mesh_model.sections.get(_sec_name)
                    if _sec and hasattr(_sec, "thickness") and _sec.thickness > 0:
                        _mat = self.mesh_model.materials.get(_sec.material)
                        if _mat and _mat.E_mod > 0:
                            avg_Et += _mat.E_mod * _sec.thickness
                            _count += 1
            if _count > 0:
                avg_Et /= _count
                penalty_stiffness = 100.0 * avg_Et
                if verbose:
                    print(
                        f"  Spring stiffness auto: E·t_avg = {avg_Et:.3e}, "
                        f"k = {penalty_stiffness:.3e}  "
                        f"(scanned {_count} shell element(s))"
                    )
            else:
                _E = 2e8  # KN/m² (200 GPa steel)
                _t = 0.15  # m (typical slab)
                penalty_stiffness = 100.0 * _E * _t
                if verbose:
                    print(f"  Spring stiffness auto: using fallback k = {penalty_stiffness:.3e}")

        # ── Find tags ─────────────────────────────────────────────
        max_elem = max(
            (
                e.elem_tag
                for e in self.mesh_model.frame_elements.values()
                if hasattr(e, "elem_tag") and e.elem_tag is not None
            ),
            default=0,
        )
        try:
            active = ops.getEleTags()
            if active:
                max_elem = max(max_elem, *active)
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
                    ops.uniaxialMaterial("Elastic", mat_tag, k)
                    ops.element(
                        "twoNodeLink",
                        ele_tag,
                        int(s_id),
                        int(master),
                        "-mat",
                        mat_tag,
                        mat_tag,
                        mat_tag,
                        mat_tag,
                        mat_tag,
                        mat_tag,
                        "-dir",
                        1,
                        2,
                        3,
                        4,
                        5,
                        6,
                    )
                    if verbose:
                        print(
                            f"  Spring constraint: node {s_id} → "
                            f"master {master}  (k={k:.2e}, w={weight:.3f})"
                        )
                    ele_tag += 1
                    mat_tag += 1
                    count += 1

        if count:
            self._edge_constraint_method = "spring"
            if coarse_edges is not None or coarse_elements is not None:
                # Save arguments so they can be re-applied after a
                # domain rebuild (pushover switches to fiber sections).
                # Single-entry list: edge constraints are applied as one
                # batch per analysis cycle.
                self._saved_edge_constraints = [
                    (
                        coarse_edges,
                        fine_nodes,
                        coarse_elements,
                        tolerance,
                        penalty_stiffness,
                        verbose,
                    )
                ]
            if verbose:
                print(f"Applied {count} spring element(s).")

        return count

    def _apply_penalty_edge_constraints(
        self,
        coarse_edges: Optional[list[tuple[int, int]]] = None,
        fine_nodes: Optional[list[int]] = None,
        coarse_elements: Optional[list[int]] = None,
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
                if s_id in (m1_id, m2_id):
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
                            int(s_id),
                            dof,
                            1.0,
                            int(m1_id),
                            dof,
                            -N1,
                            int(m2_id),
                            dof,
                            -N2,
                        )
                    count += 1
                    if verbose:
                        print(
                            f"  Edge constraint: node {s_id} → "
                            f"edge ({m1_id}–{m2_id})  "
                            f"(N1={N1:.3f}, N2={N2:.3f})"
                        )

        if count:
            self._edge_constraint_method = "penalty"
            if coarse_edges is not None or coarse_elements is not None:
                self._saved_edge_constraints = [
                    (
                        coarse_edges,
                        fine_nodes,
                        coarse_elements,
                        tolerance,
                        None,
                        verbose,
                    )
                ]
            if verbose:
                print(f"Applied {count} edge constraint(s). Solver will use Penalty handler.")

        return count

    def detect_unconnected_edges(
        self,
        tolerance: float = 1e-4,
        include_frame_connections: bool = False,
    ) -> list[dict[str, Any]]:
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
        reports: list[dict[str, Any]] = []

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
                if s_tag in (m1_tag, m2_tag):
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
                    reports.append(
                        {
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
                        }
                    )

        return reports

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
            if not getattr(elem, "inactive", False):
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
                combined = [min(ri.dofs[d], rj.dofs[d]) for d in range(6)]
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
                    if nd.node_id in self.mesh_model.restraints:
                        # Already fixed by the explicit-restraint loop above.
                        # The Preprocessor propagates edge restraints into
                        # ``mesh_model.restraints`` (geometry's
                        # ``_propagate_edge_restraints``), so re-applying the
                        # AND combination here would double-constrain the DOF
                        # and make OpenSees reject the duplicate SP_Constraint.
                        continue
                    p = np.array([nd.x, nd.y, nd.z])
                    t = np.dot(p - p_i, edge_vec) / edge_len_sq
                    if t < 1e-6 or t > 1 - 1e-6:
                        continue
                    proj = p_i + t * edge_vec
                    if np.linalg.norm(p - proj) > 0.01:
                        continue
                    ops.fix(nd.node_tag, *combined)
