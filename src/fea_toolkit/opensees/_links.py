"""Analysis-builder mixin: link (connection / isolator) elements.

Phase 1 emits the *linear* (elastic) representation of SAP2000/ETABS link
objects: a ``twoNodeLink`` element with one ``Elastic`` uniaxial material per
active DOF, using the effective stiffness written in
``LINK PROPERTY DEFINITIONS 02 - LINEAR``.  A grounded link (no end J) anchors
node i to a coincident fixed ground node via ``zeroLength``.

``link_model`` selects the representation:

* ``"elastic"`` (default) — effective-stiffness springs, valid for elastic
  static / modal / response-spectrum analyses of *all* link types (linear,
  rubber/friction isolator, gap, …).
* ``"nonlinear"`` — Phase 2; only ``Linear`` links are emitted, any other type
  raises :class:`NotImplementedError`.
"""

import logging

import numpy as np
import openseespy.opensees as ops

from ..model.geometry import get_local_axes

logger = logging.getLogger(__name__)

#: Material tag block for link springs, distinct from the hinge+release
#: (500000) and soil-spring (600000) blocks.
_LINK_MAT_BASE = 700000

#: Global-axis direction tokens accepted by ``LINK LOCAL AXES ASSIGNMENTS
#: 2 - ADVANCED`` (``CoordDir1`` / ``CoordDir2``), mapped to unit vectors.
_DIRECTION_VECTORS: dict[str, np.ndarray] = {
    "+X": np.array([1.0, 0.0, 0.0]),
    "-X": np.array([-1.0, 0.0, 0.0]),
    "+Y": np.array([0.0, 1.0, 0.0]),
    "-Y": np.array([0.0, -1.0, 0.0]),
    "+Z": np.array([0.0, 0.0, 1.0]),
    "-Z": np.array([0.0, 0.0, -1.0]),
}


class LinkMixin:
    """Emit link (connection / isolator / damper) elements."""

    def _create_link_elements(self) -> None:
        links = getattr(self.mesh_model, "links", None) or []
        if not links:
            return
        props = getattr(self.mesh_model, "link_properties", None) or {}
        assigns = getattr(self.mesh_model, "link_assignments", None) or {}
        model = self.config.get("link_model", "elastic")

        try:
            max_ops_tag = max(ops.getEleTags(), default=0)
        except Exception:
            max_ops_tag = 0
        max_frame = max(
            (e.elem_tag for e in self.mesh_model.frame_elements.values() if not e.inactive),
            default=0,
        )
        next_tag = max(max_frame, max_ops_tag, max(self.frame_tag_map.values(), default=0)) + 1
        mat_cache: dict[float, int] = {}

        for link in links:
            prop = props.get(assigns.get(link.link_id, ""))
            if prop is None:
                logger.warning(
                    "link %s has no recognised property assignment — skipped", link.link_id
                )
                continue
            if model == "nonlinear" and prop.link_type != "Linear":
                raise NotImplementedError(
                    f"link property {prop.name!r} is type {prop.link_type!r}; "
                    'nonlinear link modelling is Phase 2 (use link_model="elastic" '
                    "for the effective-stiffness representation)"
                )

            ni = self._node_tag_from_id(link.node_i)
            if ni is None:
                logger.warning("link %s references a missing node — skipped", link.link_id)
                continue

            grounded = not link.node_j
            if grounded:
                nd = self.mesh_model.nodes.get(link.node_i)
                if nd is None:
                    continue
                j_tag = max(self._created_node_tags, default=0) + 1
                ops.node(j_tag, nd.x, nd.y, nd.z)
                ops.fix(j_tag, 1, 1, 1, 1, 1, 1)
                self._created_node_tags.add(j_tag)
            else:
                j_tag = self._node_tag_from_id(link.node_j)
                if j_tag is None:
                    logger.warning("link %s references a missing node — skipped", link.link_id)
                    continue

            mats, dirs = self._link_spring_mats(prop, mat_cache)
            if not dirs:
                logger.warning("link %s has no active DOFs — skipped", link.link_id)
                continue

            orient = self._link_orient(link)
            if orient is None:
                # Zero-length link with no reference-vector axes: fall back to
                # global axes (SAP's local axes are unspecified).
                logger.debug("link %s has no local axes; using global axes", link.link_id)
                if grounded:
                    ops.element("zeroLength", next_tag, j_tag, ni, "-mat", *mats, "-dir", *dirs)
                else:
                    ops.element("twoNodeLink", next_tag, ni, j_tag, "-mat", *mats, "-dir", *dirs)
            else:
                vx, vy = orient
                if grounded:
                    ops.element(
                        "zeroLength",
                        next_tag,
                        j_tag,
                        ni,
                        "-mat",
                        *mats,
                        "-dir",
                        *dirs,
                        "-orient",
                        *vx,
                        *vy,
                    )
                else:
                    ops.element(
                        "twoNodeLink",
                        next_tag,
                        ni,
                        j_tag,
                        "-mat",
                        *mats,
                        "-dir",
                        *dirs,
                        "-orient",
                        *vx,
                        *vy,
                    )
            next_tag += 1

    def _link_spring_mats(self, prop, mat_cache: dict[float, int]):
        """Return ``(mat_tags, dirs)`` for a link property, creating materials."""
        mats: list[int] = []
        dirs: list[int] = []
        fixed_k = self._link_fixed_stiffness(prop)
        for dof in range(6):
            k = fixed_k if prop.fixed[dof] else prop.ke[dof]
            if k <= 0.0:
                continue
            mat_tag = mat_cache.get(k)
            if mat_tag is None:
                mat_tag = _LINK_MAT_BASE + len(mat_cache)
                mat_cache[k] = mat_tag
                ops.uniaxialMaterial("Elastic", mat_tag, k)
            mats.append(mat_tag)
            dirs.append(dof + 1)
        return mats, dirs

    @staticmethod
    def _link_fixed_stiffness(prop) -> float:
        """Stiffness for a ``Fixed`` link DOF — rigid, scaled off the link.

        A fixed DOF is modelled as a very stiff ``Elastic`` spring.  Scaling it
        off the link's own largest effective stiffness keeps it unit-consistent
        without an absolute magic number (which would be ill-conditioned in the
        wrong unit system).
        """
        scale = max((k for k in prop.ke if k > 0.0), default=1.0)
        return scale * 1.0e6

    def _link_orient(self, link):
        """Return ``(vx, vy)`` orientation vectors, or ``None`` for zero length.

        Reference-vector axes (``coord_dir1`` / ``coord_dir2`` from
        ``LINK LOCAL AXES ASSIGNMENTS 2 - ADVANCED``) take precedence — they
        are the only way to orient a zero-length link (e.g. a vertical base
        isolator whose U1 must be vertical).  Otherwise the I→J geometry and
        the ``angle`` rotation (``1 - TYPICAL``) define the axes.
        """
        vx = _DIRECTION_VECTORS.get(link.coord_dir1.strip().upper())
        if vx is not None:
            vy = _DIRECTION_VECTORS.get(link.coord_dir2.strip().upper())
            if vy is not None and abs(float(np.dot(vx, vy))) < 1e-9:
                # coord_dir2 is an explicit, perpendicular local-2 direction.
                return vx.copy(), vy.copy()
            vx_o, vy_o, _vz = get_local_axes(vx, link.angle)
            return vx_o, vy_o
        ni = self.mesh_model.nodes.get(link.node_i)
        nj = self.mesh_model.nodes.get(link.node_j)
        if ni is None or nj is None:
            return None
        axis = np.array([nj.x - ni.x, nj.y - ni.y, nj.z - ni.z])
        if np.linalg.norm(axis) < 1e-12:
            return None
        vx_o, vy_o, _vz = get_local_axes(axis, link.angle)
        return vx_o, vy_o
