"""Analysis-builder mixin: element creation."""

import copy
import logging
import math
import warnings
from typing import Optional

import numpy as np
import openseespy.opensees as ops

from ..model.geometry import frame_end_offsets_global, get_SAP_vecxz
from ..model.sap_data import FrameElement, Node
from ..utils import (
    DEFAULT_RHO_MC_SI,
    g_from_units,
    length_scale_factor,
    mass_density_scale_factor,
)
from .preprocessor import _resolve_rigid_offset_method
from .releases import plan_releases

logger = logging.getLogger(__name__)


def _trilinear_backbone(
    ei1: float, ei2: float, ei3: float, d1: float, d2: float
) -> tuple[float, float, float]:
    """Validate a trilinear M-phi backbone and derive its first two corners.

    The Ohtori ``mat_tbl`` backbone has three flexural slopes -- elastic ``EI1``
    (0 -> d1), post-yield ``EI2`` (d1 -> d2), hardening ``EI3`` (d2 -> inf) --
    and the two break curvatures ``d1``/``d2``.  This helper validates the
    backbone and derives the first two corner moments shared by every trilinear
    hinge formulation (``beamWithHinges``, the lumped ``Hysteretic`` hinge, and
    the two-``Steel02``-in-parallel decomposition).

    ``EI3`` may be zero (perfectly plastic beyond ``d2``) -- that is *not*
    invalid.  Callers that need a non-zero hardening slope (the ``Hysteretic``
    branches, which need a finite third corner) use the returned ``ei3_eff``
    fallback (``EI2 * 0.02``); :func:`_parallel_steel02_trilinear` consumes the
    *raw* ``ei3``.

    Args:
        ei1: Elastic flexural slope (moment per curvature), model units.
        ei2: Post-yield flexural slope.
        ei3: Hardening flexural slope (``0`` for perfectly plastic beyond d2).
        d1: First (yield) break curvature.
        d2: Second break curvature.

    Returns:
        ``(my_tri, m2, ei3_eff)`` -- the first yield moment ``EI1*d1``, the
        second corner moment ``My + EI2*(d2 - d1)``, and the effective hardening
        slope (``ei3`` when positive, else ``EI2 * 0.02``).

    Raises:
        ValueError: For a non-positive backbone (``ei1``, ``ei2`` or ``d1``) or
            non-monotonic break curvatures (``d2 <= d1``).
    """
    if ei1 <= 0.0 or ei2 <= 0.0 or d1 <= 0.0 or d2 <= d1:
        raise ValueError(
            f"trilinear backbone needs ei1>0, ei2>0, d1>0, d2>d1 "
            f"(got ei1={ei1}, ei2={ei2}, d1={d1}, d2={d2})"
        )
    my_tri = ei1 * d1
    m2 = my_tri + ei2 * (d2 - d1)
    ei3_eff = ei3 if ei3 > 0.0 else ei2 * 0.02
    return my_tri, m2, ei3_eff


def _parallel_steel02_trilinear(
    ei1: float, ei2: float, ei3: float, d1: float, d2: float
) -> tuple[dict[str, float], dict[str, float]]:
    """Decompose a trilinear M-φ backbone into two Steel02 springs in parallel.

    The Ohtori ``mat_tbl`` backbone has three flexural slopes — elastic ``EI1``
    (0 → d1), post-yield ``EI2`` (d1 → d2), hardening ``EI3`` (d2 → ∞).  Two
    bilinear springs summed at the shared curvature (``uniaxialMaterial
    Parallel``) reproduce it exactly:

    * spring A yields first (at ``d1``) and hardens at ``H_A = EI3``;
    * spring B yields second (at ``d2``) and is perfectly plastic (``H_B = 0``).

    The combined slopes are ``EI1 = E_A + E_B``, ``EI2 = H_A + E_B``,
    ``EI3 = H_A``, which solves to ``E_A = EI1 − EI2 + EI3``,
    ``E_B = EI2 − EI3``.  The two ``Steel02`` springs use the
    Giuffré–Menegotto–Pinto transition, which rounds the trilinear corners — the
    analogue of the benchmark ``Nonlin12`` S-function's yield-index α
    interpolation (absent from the sharp-break ``Hysteretic`` hinge).

    Args:
        ei1: Elastic flexural slope (moment per curvature), model units.
        ei2: Post-yield flexural slope.
        ei3: Hardening flexural slope (``0`` for perfectly-plastic beyond ``d2``).
        d1: First (yield) break curvature.
        d2: Second break curvature.

    Returns:
        ``(spring_a, spring_b)`` — ``Steel02`` parameter dicts with keys ``fy``
        (yield moment), ``E0`` (initial tangent) and ``b`` (hardening ratio).

    Raises:
        ValueError: For a non-positive backbone, or one whose slopes cannot be
            split into two positive springs (needs ``EI3 < EI2`` and
            ``EI1 + EI3 > EI2``, i.e. a monotonic softening trilinear).
    """
    _trilinear_backbone(ei1, ei2, ei3, d1, d2)  # shared backbone validation
    e_b = ei2 - ei3
    e_a = ei1 - ei2 + ei3
    if e_a <= 0.0 or e_b <= 0.0 or ei3 < 0.0:
        raise ValueError(
            f"trilinear backbone not decomposable into two positive springs "
            f"(need 0 <= EI3 < EI2 and EI1 + EI3 > EI2; "
            f"got EI1={ei1}, EI2={ei2}, EI3={ei3})"
        )
    return (
        {"fy": e_a * d1, "E0": e_a, "b": ei3 / e_a},
        {"fy": e_b * d2, "E0": e_b, "b": 0.0},
    )


def _validate_hinge_pairing(hinge_model: Optional[str], hinge_backbone: Optional[str]) -> None:
    """Validate a ``hinge_model``/``hinge_backbone`` pairing before path selection.

    Raises:
        ValueError: When ``beam_with_hinges`` is paired with a backbone other
            than the three it supports, or when ``lumped`` is paired with a
            ``beamWithHinges``-only backbone (``trilinear_steel02`` /
            ``trilinear_clough``) that would otherwise silently fall through to
            the ASCE 41 lumped hinge.
    """
    if hinge_model == "beam_with_hinges":
        allowed = ("trilinear", "trilinear_steel02", "trilinear_clough")
        if hinge_backbone not in allowed:
            raise ValueError(
                f"hinge_model='beam_with_hinges' requires hinge_backbone in "
                f"{allowed}; got {hinge_backbone!r}"
            )
    elif hinge_model == "lumped" and hinge_backbone in ("trilinear_steel02", "trilinear_clough"):
        raise ValueError(
            f"hinge_backbone={hinge_backbone!r} is a beamWithHinges-only "
            "formulation; use hinge_model='beam_with_hinges' (not 'lumped')"
        )


def _imk_bilin_args(
    params: dict[str, float], k0: float, my_pos: float, my_neg: float
) -> tuple[float, ...]:
    """Map a stamped ``imk_params`` dict to the ``IMKBilin`` material's positional args.

    The ``IMKBilin`` (modified Ibarra–Krawinkler, bilinear hysteretic — **steel**)
    signature is ``IMKBilin matTag Ke dp_pos dpc_pos du_pos Fy_pos FmaxFy_pos
    FresFy_pos dp_neg dpc_neg du_neg Fy_neg FmaxFy_neg FresFy_neg Lamda_S
    Lamda_C Lamda_K c_S c_C c_K D_pos D_neg``.  ``FmaxFy`` = capping ratio
    (``mc_my``), ``FresFy`` = residual ratio (``res``); all strengths and
    deformations are positive (``Fy_neg`` = ``-my_neg``).  A missing ``theta_u``
    falls back to ``theta_p + theta_pc`` — the *no-plateau* ``θu``, deliberately
    the **conservative** choice: a smaller ``du`` drops the hinge to zero
    strength earlier (predicts collapse sooner) than the calibrated
    ``"plateau"`` convention (``θp + θpc + 0.02``), so an uncalibrated
    hand-built dict never overstates ductility.
    """
    theta_p_pos = float(params.get("theta_p_pos", 0.0))
    theta_p_neg = float(params.get("theta_p_neg", 0.0))
    theta_pc_pos = float(params.get("theta_pc_pos", 0.0))
    theta_pc_neg = float(params.get("theta_pc_neg", 0.0))
    theta_u_pos = float(params.get("theta_u_pos", 0.0))
    theta_u_neg = float(params.get("theta_u_neg", 0.0))
    # Missing ``du`` → the conservative no-plateau fallback (``θp + θpc``).  A
    # smaller ``du`` collapses the hinge earlier than the calibrated
    # ``"plateau"`` default (``θp + θpc + 0.02``), so a hand-built dict never
    # overstates its ductility.  The calibration path resolves ``θu``
    # explicitly (see ``_resolve_theta_u``), so this only fires for
    # uncalibrated dicts.
    if theta_u_pos <= 0.0:
        theta_u_pos = theta_p_pos + theta_pc_pos
    if theta_u_neg <= 0.0:
        theta_u_neg = theta_p_neg + theta_pc_neg
    mc_my = float(params.get("mc_my", 1.0))
    res_pos = float(params.get("res_pos", 0.0))
    res_neg = float(params.get("res_neg", 0.0))
    # IMKBilin's post-capping stiffness is K_pc = -Fcap/dpc; the calibrated θpc is
    # the rotation over which M drops Mc -> Mr (K_pc = (Fres-Fcap)/θpc).  Map it.
    denom_pos = mc_my - res_pos
    dpc_pos = (mc_my * theta_pc_pos / denom_pos) if denom_pos > 0 else theta_pc_pos
    denom_neg = mc_my - res_neg
    dpc_neg = (mc_my * theta_pc_neg / denom_neg) if denom_neg > 0 else theta_pc_neg
    return (
        k0,
        theta_p_pos,
        dpc_pos,
        theta_u_pos,
        my_pos,
        mc_my,
        res_pos,
        theta_p_neg,
        dpc_neg,
        theta_u_neg,
        -my_neg,  # Fy_neg (positive magnitude)
        mc_my,
        res_neg,
        float(params.get("lamda_S", 0.0)),
        float(params.get("lamda_C", 0.0)),
        float(params.get("lamda_K", 0.0)),
        float(params.get("c_S", 1.0)),
        float(params.get("c_C", 1.0)),
        float(params.get("c_K", 1.0)),
        float(params.get("D_pos", 1.0)),
        float(params.get("D_neg", 1.0)),
    )


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
                    self._log.warning(
                        "elements",
                        f"Wall element '{wall.elem_id}': node '{nid}' not found in mesh — skipping",
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

        self._log.info("elements", f"Created {created} wall element(s)")

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
        self._log.debug("elements", f"{elem_type} tag={wall.elem_tag} nodes={node_tags} m={wall.m}")
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
        self._log.debug("elements", f"{elem_type} tag={wall.elem_tag} nodes={node_tags} m={wall.m}")
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

        self._log.info("elements", "Creating frame elements...")

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
            rigid_E, rigid_A, rigid_I, rigid_G, rigid_J = self._rigid_section_properties()
            ops.section(
                "Elastic",
                rigid_section_tag,
                rigid_E,
                rigid_A,
                rigid_I,
                rigid_I,
                rigid_G,
                rigid_J,
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
            # Resolve the MPC flag through the single spelling so the new
            # ``rigid_offset_method="mpc"`` key actually emits MPCs (the
            # legacy ``rigid_link_mpc`` boolean maps onto it in
            # ``_rigid_offset_method``) — reading the boolean directly here
            # silently degrades ``"mpc"`` to stiff elastic links.
            _mpc = self._rigid_offset_method() == "mpc"
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

        n = len([e for e in elements.values() if not getattr(e, "inactive", False)])
        self._log.info("elements", f"Created {n} frame elements")

    def _rigid_offset_method(self) -> str:
        """Resolve how frame end offsets are carried onto the OpenSees model.

        Delegates to the shared :func:`_resolve_rigid_offset_method` in
        ``preprocessor.py`` so the Preprocessor and the builder use one
        spelling.
        """
        return _resolve_rigid_offset_method(self.config)

    def _rigid_section_properties(self) -> tuple[float, float, float, float, float]:
        """``(E, A, I, G, J)`` for the stiff-beam rigid-link fallback section.

        The rigid link is an artificial stiffener, so its exact modulus does not
        matter — only that it is much stiffer than the members it bridges.  The
        section is therefore scaled to ≈1000× the stiffest member's axial
        (``E·A``) and flexural (``E·I``) stiffness, derived from the model's own
        materials/sections in model units (§4.6 — no hardcoded physical
        constant).  Falls back to a dimensionless ``1.0`` placeholder when the
        model carries no usable section/material stiffness.

        Returns:
            ``(E, A, I, G, J)`` for ``ops.section("Elastic", ...)``.
        """
        factor = 1000.0
        max_EA = 0.0
        max_EI = 0.0
        max_E = 0.0
        sections = getattr(self.mesh_model, "sections", {}) or {}
        materials = getattr(self.mesh_model, "materials", {}) or {}
        for sec in sections.values():
            A = float(getattr(sec, "A", 0.0) or 0.0)
            sec_I = max(
                float(getattr(sec, "I33", 0.0) or 0.0),
                float(getattr(sec, "I22", 0.0) or 0.0),
            )
            mat = materials.get(getattr(sec, "material", ""))
            E = float(getattr(mat, "E_mod", 0.0) or 0.0) if mat is not None else 0.0
            if E > 0.0:
                max_EA = max(max_EA, E * A)
                max_EI = max(max_EI, E * sec_I)
                max_E = max(max_E, E)

        if max_E <= 0.0:
            # No usable section/material stiffness in the model — a
            # dimensionless placeholder (never a hardcoded physical constant).
            return 1.0, 1.0, 1.0, 1.0 / 2.6, 1.0

        rigid_E = max_E
        rigid_A = factor * max_EA / rigid_E if max_EA > 0.0 else 1.0
        rigid_I = factor * max_EI / rigid_E if max_EI > 0.0 else rigid_A
        # Keep G large (≈E/2.6) so shear/torsion never become the soft DOFs of
        # the link; the exact ratio is irrelevant for an artificial stiffener.
        rigid_G = rigid_E / 2.6
        rigid_J = rigid_I
        return rigid_E, rigid_A, rigid_I, rigid_G, rigid_J

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
        axis = np.array([nj.x - ni.x, nj.y - ni.y, nj.z - ni.z])
        vecxz = get_SAP_vecxz(axis, angle)
        transf_type = self.config.get("geom_transf_type", "Linear")
        transf_tag = tag
        offset = (getattr(self.mesh_model, "frame_end_offsets", None) or {}).get(elem.elem_id)
        if (
            offset is not None
            and self._rigid_offset_method() == "jntOffset"
            and any(
                abs(v) > 1e-12
                for v in (
                    offset.end_i,
                    offset.end_j,
                    offset.off_y_i,
                    offset.off_z_i,
                    offset.off_y_j,
                    offset.off_z_j,
                )
            )
        ):
            # Native rigid offset: the flexible span runs centroid-to-centroid
            # while the joints stay on the reference line.  Offsets are GLOBAL,
            # so the local (1, 2, 3) values are rotated via the shared helper.
            off_i, off_j = frame_end_offsets_global(
                axis,
                angle,
                offset.end_i,
                offset.end_j,
                offset.off_y_i,
                offset.off_z_i,
                offset.off_y_j,
                offset.off_z_j,
            )
            ops.geomTransf(transf_type, transf_tag, *vecxz, "-jntOffset", *off_i, *off_j)
        else:
            ops.geomTransf(transf_type, transf_tag, *vecxz)
        self._transf_tags[tag] = transf_tag

        # ── beamWithHinges (concentrated plasticity) ────────────────
        # The benchmark's own formulation: an elastic interior (E, A, Iz, Iy,
        # G, J) with a trilinear moment-*curvature* hinge section (Uniaxial,
        # strong-axis Mz) at each end.  No "nearly rigid" lumped spring, so the
        # elastic stiffness is the exact 6EI1/L.  The plastic hinge length
        # converts curvature to member-end rotation; the benchmark's
        # member-end rotation is θ = φ·L/6, so Lp = L/6 (hinge_length_fraction).
        if self.config.get("hinge_model") == "beam_with_hinges":
            _validate_hinge_pairing("beam_with_hinges", self.config.get("hinge_backbone"))
        if self.config.get("hinge_model") == "beam_with_hinges" and self.config.get(
            "hinge_backbone"
        ) in ("trilinear", "trilinear_steel02", "trilinear_clough"):
            _sec = self.mesh_model.sections.get(sec_name)
            _mat = self.mesh_model.materials.get(_sec.material) if _sec else None
            ei1 = getattr(_sec, "ei1", None) or 0.0
            ei2 = getattr(_sec, "ei2", None) or 0.0
            ei3 = getattr(_sec, "ei3", None) or 0.0
            d1 = getattr(_sec, "d1", None) or 0.0
            d2 = getattr(_sec, "d2", None) or 0.0
            my_tri, m2, ei3_eff = _trilinear_backbone(ei1, ei2, ei3, d1, d2)
            d3 = 2.0 * d2
            m3 = m2 + ei3_eff * (d3 - d2)
            E = getattr(_mat, "E_mod", None) if _mat is not None else None
            if not E or E <= 0.0:
                _mat_name = getattr(_mat, "name", None) if _mat is not None else None
                raise ValueError(
                    f"hinge_model='beam_with_hinges' needs a positive E_mod on "
                    f"section '{sec_name}' (material {_mat_name!r}); got E={E!r}"
                )
            G = _mat.G_mod if _mat and _mat.G_mod and _mat.G_mod > 0 else 0.4 * E
            A = getattr(_sec, "A", None) or 0.0
            Iz = getattr(_sec, "I33", None) or 0.0
            Iy = getattr(_sec, "I22", None) or 0.0
            J = getattr(_sec, "J", None) or 0.0
            if not hasattr(self, "_bwh_tag_counter"):
                self._bwh_tag_counter = (
                    max((v for v in self.section_tags.values()), default=0) + 500
                )
            if self.config.get("hinge_backbone") == "trilinear_steel02":
                # Two Steel02 springs in parallel reproduce the trilinear M-φ
                # backbone with a smooth Giuffré–Menegotto–Pinto transition —
                # the analogue of the benchmark Nonlin12 S-function's yield-index
                # α interpolation, which the sharp-break Hysteretic lacks.
                _spring_a, _spring_b = _parallel_steel02_trilinear(ei1, ei2, ei3, d1, d2)
                _mat_a = self._bwh_tag_counter
                _mat_b = self._bwh_tag_counter + 1
                _mat_par = self._bwh_tag_counter + 2
                _sec_tag = self._bwh_tag_counter + 3
                self._bwh_tag_counter += 4
                ops.uniaxialMaterial(
                    "Steel02",
                    _mat_a,
                    _spring_a["fy"],
                    _spring_a["E0"],
                    _spring_a["b"],
                )
                ops.uniaxialMaterial(
                    "Steel02",
                    _mat_b,
                    _spring_b["fy"],
                    _spring_b["E0"],
                    _spring_b["b"],
                )
                ops.uniaxialMaterial("Parallel", _mat_par, _mat_a, _mat_b)
                _hinge_mat = _mat_par
            else:
                # Hysteretic trilinear: sharp corners, symmetric reloading.
                # The "clough" spelling adds unloading-stiffness degradation
                # (beta) — the Clough/Takeda signature — which softens the
                # cyclic force response at the same backbone and strength.
                _mat_tag = self._bwh_tag_counter
                _sec_tag = self._bwh_tag_counter + 1
                self._bwh_tag_counter += 2
                _beta = 0.5 if self.config.get("hinge_backbone") == "trilinear_clough" else 0.0
                ops.uniaxialMaterial(
                    "Hysteretic",
                    _mat_tag,
                    my_tri,
                    d1,
                    m2,
                    d2,
                    m3,
                    d3,
                    -my_tri,
                    -d1,
                    -m2,
                    -d2,
                    -m3,
                    -d3,
                    1.0,
                    1.0,
                    0.0,
                    0.0,
                    _beta,
                )
                _hinge_mat = _mat_tag
            ops.section("Uniaxial", _sec_tag, _hinge_mat, "Mz")
            _secE_tag = self._bwh_tag_counter
            self._bwh_tag_counter += 1
            _int_tag = tag + 10000
            _L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
            _lp = float(self.config.get("hinge_length_fraction", 1.0 / 6.0)) * _L
            # Elastic interior (E, A, Iz, Iy, G, J) carried explicitly, as the
            # legacy ``beamWithHinges`` command did.
            ops.section(
                "Elastic",
                _secE_tag,
                E,
                max(A, 1e-6),
                max(Iz, 1e-12),
                max(Iy, 1e-12),
                G,
                max(J, 1e-12),
            )
            # ``HingeRadauTwo`` keeps every integration weight positive up to
            # Lp = L/2, whereas the ``beamWithHinges`` command's ``HingeRadau``
            # goes negative once Lp > L/8.  With the benchmark's Lp = L/6 those
            # negative interior weights make the 6×6 flexibility matrix
            # ill-conditioned ("could not invert flexibility" on Linux), so the
            # equivalent ``forceBeamColumn`` + ``HingeRadauTwo`` is used here.
            ops.beamIntegration("HingeRadauTwo", _int_tag, _sec_tag, _lp, _sec_tag, _lp, _secE_tag)
            ops.element("forceBeamColumn", tag, ni.node_tag, nj.node_tag, transf_tag, _int_tag)
            # Damage recorders — the member's two end sections (1 = i-end,
            # 6 = j-end under the 6-point plastic-hinge integration).
            self._hinge_tags.extend([tag, tag])
            self._hinge_sections.extend([1, 6])
            self._hinge_yield_rotations.extend([d1, d1])
            self._hinge_yield_moments.extend([my_tri, my_tri])
            self._hinge_moment_signs.extend([1.0, 1.0])
            return

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

    def _reset_hinge_recorders(self) -> None:
        """Reset the transient runner's hinge-damage recorder lists.

        The transient runner reads these to report φmax / Emax / Nd.  Reset on
        every :meth:`build_domain` so a rebuild (e.g. before each ground-motion
        record) does not append to the previous build's lists.  ``_hinge_tags``
        holds one element tag per hinge location; ``_hinge_sections`` holds the
        ``beamWithHinges`` section number at that location (``None`` for a
        lumped zero-length hinge); ``_hinge_yield_rotations`` /
        ``_hinge_yield_moments`` hold the yield threshold (rotation for lumped,
        curvature for ``beamWithHinges``) and yield moment; ``_hinge_moment_signs``
        holds the ±1 sign that aligns each lumped hinge's nodal-moment response
        with its dir-5 deformation (see ``_runner_dynamic``).
        """
        self._hinge_tags: list[int] = []
        self._hinge_sections: list = []
        self._hinge_yield_rotations: list[float] = []
        self._hinge_yield_moments: list[float] = []
        self._hinge_moment_signs: list[float] = []

    def _create_lumped_hinges(self) -> None:
        """Replace frame elements with lumped plasticity hinges.

        Activated via ``config['hinge_model'] = 'lumped'``.

        Each frame element is split into::

            structural_node_i → hinge_i → elastic_mid → hinge_j → structural_node_j

        Coincident hinge nodes sit at the same coordinates.  Each hinge is a
        ``zeroLength`` element carrying all six DOFs via ``-mat``/``-dir``
        (no ``equalDOF``): rigid ``Elastic`` materials retain P/Vy/Vz/T,
        while the hinge materials (``Hysteretic`` or ``Steel01``) provide the
        two rotational DOFs.

        Hinge backbones use ``Hysteretic`` materials matched to ASCE 41
        rotation limits.
        """
        if self.config.get("hinge_model") != "lumped":
            return
        _validate_hinge_pairing("lumped", self.config.get("hinge_backbone"))

        # ── Idempotency: preserve canonical state on first call ────────
        # Save canonical endpoints on first call; restoration is handled
        # by _restore_hinge_canonical_state() in build_domain().
        if not hasattr(self, "_hinge_canonical_elements"):
            # Capture the frozen beam endpoints directly from the mesh — panel
            # zones are now resolved by the Preprocessor, so a beam already
            # points at its *_pz node here, and that is the correct
            # pre-instrumentation state to restore on a rebuild.
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
        hinge_mat_tag = hinge_tag_base + len(self.section_tags) + 1

        new_elements: dict[str, FrameElement] = {}
        new_assignments: dict[str, str] = {}
        # Hinge instrumentation for the transient runner's damage recorders
        # (φmax / Emax / Nd), rebuilt on each build_domain.
        self._reset_hinge_recorders()

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

            # Retained DOFs are rigid (η × member stiffness) — the hinge is a
            # pure rotational spring; the elastic member carries P/V/T.  The
            # rigid translations live inside a zeroLength element (the release
            # convention) so no equalDOF tie is needed.
            eta = float(self.config.get("release_rigidity_factor", 100.0))
            ops.uniaxialMaterial(
                "Elastic", hinge_mat_tag, eta * max(A_val, 1e-6) * E / L
            )  # P (dir 1)
            ops.uniaxialMaterial(
                "Elastic", hinge_mat_tag + 4, eta * G * max(A_val, 1e-6) / L
            )  # Vy (dir 2)
            ops.uniaxialMaterial(
                "Elastic", hinge_mat_tag + 5, eta * G * max(A_val, 1e-6) / L
            )  # Vz (dir 3)
            if self.config.get("hinge_backbone") == "bilinear":
                # Bilinear steel hinge (Steel01) — concentrated plasticity for
                # steel moment frames (e.g. the SAC benchmark).  The elastic
                # rotational stiffness is a configurable multiple of the
                # member's own flexural stiffness (6EI/L), so the hinge is
                # nearly rigid in the elastic range and the interior member
                # carries the elastic deformation; past My it hardens at
                # hardening_ratio.
                b = float(self.config.get("hardening_ratio", 0.02))
                stiff_factor = float(self.config.get("hinge_stiffness_factor", 10.0))
                if stiff_factor <= 0.0 or not math.isfinite(stiff_factor):
                    raise ValueError(
                        f"hinge_stiffness_factor must be positive and finite, got {stiff_factor}"
                    )
                e0_strong = stiff_factor * 6.0 * E * max(I33, 1e-12) / max(L, 1e-12)
                e0_weak = stiff_factor * 6.0 * E * max(I22, 1e-12) / max(L, 1e-12)
                ops.uniaxialMaterial("Steel01", hinge_mat_tag + 1, My, e0_strong, b)
                ops.uniaxialMaterial("Steel01", hinge_mat_tag + 2, My_weak, e0_weak, b)
                hinge_theta_y = My / max(e0_strong, 1e-12)
                hinge_my = My
            elif self.config.get("hinge_backbone") == "trilinear":
                # Trilinear hinge (Hysteretic) from the transcribed Ohtori
                # ``mat_tbl`` backbone — elastic EI1, post-yield EI2, hardening
                # EI3, break curvatures d1/d2 (My = EI1*d1).  The member-level
                # curvature→rotation conversion uses the member end stiffness
                # 6EI/L, so the hinge's rotational slopes are 6*EI2/L and
                # 6*EI3/L, with a "nearly rigid" elastic stiffness
                # (hinge_stiffness_factor × 6*EI1/L) so the elastic interior
                # member carries the elastic deformation.
                ei1 = getattr(sec, "ei1", None) or 0.0
                ei2 = getattr(sec, "ei2", None) or 0.0
                ei3 = getattr(sec, "ei3", None) or 0.0
                d1 = getattr(sec, "d1", None) or 0.0
                d2 = getattr(sec, "d2", None) or 0.0
                my_tri, m2, ei3_eff = _trilinear_backbone(ei1, ei2, ei3, d1, d2)
                stiff_factor = float(self.config.get("hinge_stiffness_factor", 10.0))
                if stiff_factor <= 0.0 or not math.isfinite(stiff_factor):
                    raise ValueError(
                        f"hinge_stiffness_factor must be positive and finite, got {stiff_factor}"
                    )
                k0 = stiff_factor * 6.0 * ei1 / max(L, 1e-12)
                k1 = 6.0 * ei2 / max(L, 1e-12)
                k2 = 6.0 * ei3_eff / max(L, 1e-12)

                theta_y = my_tri / k0
                theta_2 = theta_y + (m2 - my_tri) / k1
                theta_cap = theta_2 * 2.0
                m_cap = m2 + k2 * (theta_cap - theta_2)

                for mat_tag in (hinge_mat_tag + 1, hinge_mat_tag + 2):
                    ops.uniaxialMaterial(
                        "Hysteretic",
                        mat_tag,
                        my_tri,
                        theta_y,
                        m2,
                        theta_2,
                        m_cap,
                        theta_cap,
                        -my_tri,
                        -theta_y,
                        -m2,
                        -theta_2,
                        -m_cap,
                        -theta_cap,
                        1.0,
                        1.0,
                        0.0,
                        0.0,
                        0.0,
                    )
                hinge_theta_y = theta_y
                hinge_my = my_tri
            elif self.config.get("hinge_backbone") == "imk":
                # IMK steel hinge (Lignos-Krawinkler, collapse-level) — a
                # ``IMKBilin`` uniaxial material on the strong axis with an
                # energy-based cyclic-deterioration rule.  Parameters come from
                # the section's stamped ``imk_params`` dict (see
                # ``benchmarks/imk_calibration.py``).  K0 is the near-rigid
                # ``stiff_factor`` x 6EI/L so the elastic member carries the
                # elastic deformation, matching the bilinear/trilinear
                # lumped-hinge convention.
                params = getattr(sec, "imk_params", None)
                stiff_factor = float(self.config.get("hinge_stiffness_factor", 10.0))
                k0 = stiff_factor * 6.0 * E * max(I33, 1e-12) / max(L, 1e-12)
                if params:
                    my_imk = float(params.get("My_pos", My))
                    my_imk_neg = float(params.get("My_neg", -my_imk))
                    ops.uniaxialMaterial(
                        "IMKBilin",
                        hinge_mat_tag + 1,
                        *_imk_bilin_args(params, k0, my_imk, my_imk_neg),
                    )
                    hinge_theta_y = my_imk / max(k0, 1e-12)
                    hinge_my = my_imk
                else:
                    # Section carries no IMK calibration (e.g. the elastic
                    # leaning column) — keep the strong axis elastic at k0 and
                    # report no yield for it.
                    ops.uniaxialMaterial("Elastic", hinge_mat_tag + 1, k0)
                    hinge_theta_y = float("inf")
                    hinge_my = 0.0
                # Weak-axis moment stays elastic (out-of-plane; the 2D frame
                # carries all in-plane action on the strong axis).
                ops.uniaxialMaterial(
                    "Elastic",
                    hinge_mat_tag + 2,
                    stiff_factor * 6.0 * E * max(I22, 1e-12) / max(L, 1e-12),
                )
            else:
                # Strong-axis moment (ASCE 41 Hysteretic backbone)
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
                hinge_theta_y = theta_y
                hinge_my = My
            # Torsion (rigid — no inelastic torsion expected)
            ops.uniaxialMaterial(
                "Elastic",
                hinge_mat_tag + 3,
                eta * (G * max(J_val, 1e-6) / L if J_val else G * 1e-6 / L),
            )

            hinge_mat_tag += 6

            # Get local axes for element orientation and the strong-axis moment
            # sign (the zeroLength nodal-moment sign flips with the -orient
            # handedness between columns and beams — see _runner_dynamic).
            try:
                vx, _vy, vz = self._get_local_axes(elem)
                orient = (vx[0], vx[1], vx[2], vz[0], vz[1], vz[2])
                sign = -float(vz[1])
            except Exception:
                orient = None
                sign = 1.0

            # --- Create zero-length hinge elements (zeroLength + -mat/-dir) ---
            # Retained DOFs (P, Vy, Vz, T) are rigid; the flexural hinges are
            # the two rotational DOFs.  `-orient (vx, vz)` (above) points the
            # hinge's local y-axis along the member's local z — the strong
            # axis — so dir 5 is the strong-axis (in-plane) hinge and dir 6
            # the weak-axis hinge.
            m = hinge_mat_tag - 6  # base material tag
            mats = [m, m + 4, m + 5, m + 3, m + 1, m + 2]  # P, Vy, Vz, T, strong, weak
            dirs = [1, 2, 3, 4, 5, 6]

            hinge_i_elem_tag = next_tag
            next_tag += 1
            if orient:
                ops.element(
                    "zeroLength",
                    hinge_i_elem_tag,
                    ni.node_tag,
                    hinge_i_tag,
                    "-mat",
                    *mats,
                    "-dir",
                    *dirs,
                    "-orient",
                    *orient,
                )
            else:
                ops.element(
                    "zeroLength",
                    hinge_i_elem_tag,
                    ni.node_tag,
                    hinge_i_tag,
                    "-mat",
                    *mats,
                    "-dir",
                    *dirs,
                )

            hinge_j_elem_tag = next_tag
            next_tag += 1
            if orient:
                ops.element(
                    "zeroLength",
                    hinge_j_elem_tag,
                    hinge_j_tag,
                    nj.node_tag,
                    "-mat",
                    *mats,
                    "-dir",
                    *dirs,
                    "-orient",
                    *orient,
                )
            else:
                ops.element(
                    "zeroLength",
                    hinge_j_elem_tag,
                    hinge_j_tag,
                    nj.node_tag,
                    "-mat",
                    *mats,
                    "-dir",
                    *dirs,
                )

            self._hinge_tags.extend([hinge_i_elem_tag, hinge_j_elem_tag])
            self._hinge_sections.extend([None, None])
            self._hinge_yield_rotations.extend([hinge_theta_y, hinge_theta_y])
            self._hinge_yield_moments.extend([hinge_my, hinge_my])
            self._hinge_moment_signs.extend([sign, sign])

            # --- Shorten original element to span between hinge nodes ---
            elem.node_i = hinge_i_id
            elem.node_j = hinge_j_id
            new_elements[eid] = elem
            new_assignments[eid] = sec_name

        # Update collections
        self.mesh_model.frame_elements = new_elements
        self.mesh_model.frame_assignments = new_assignments

    # ── Member end releases / partial fixity ─────────────────────────
    # The retained-DOF rigidity factor (η) and the fully-released softness
    # factor (μ) are the canonical module-level constants in
    # :mod:`fea_toolkit.opensees.releases` — imported here (and by
    # ``AnalysisBuilder._set_defaults()``) so a single definition drives the
    # OpenSeesPy domain, the Tcl export and the config defaults.

    def _restore_release_canonical_state(self) -> None:
        """Restore canonical frame endpoints and drop stale release nodes.

        Called at the start of :meth:`build_domain` (before
        :meth:`_create_nodes`) so repeated builds re-instrument the original
        elements rather than already-instrumented ones.
        """
        if not hasattr(self, "_release_canonical_elements"):
            return
        for nid in list(self.mesh_model.nodes.keys()):
            if nid.endswith(("_rel_i", "_rel_j")):
                del self.mesh_model.nodes[nid]
        for eid, elem in self.mesh_model.frame_elements.items():
            if eid in self._release_canonical_elements:
                ni, nj = self._release_canonical_elements[eid]
                elem.node_i = ni
                elem.node_j = nj

    def _create_member_releases(self) -> None:
        """Insert zero-length release elements at released frame ends.

        Activated by ``config['apply_releases']`` (default ``True``).
        SAP2000 member end releases (``FRAME RELEASE ASSIGNMENTS 1 -
        GENERAL``) free selected local DOFs at a member end; partial fixity
        (``... 2 - PARTIAL FIXITY``) replaces a freed DOF with a semi-rigid
        spring.

        The release topology and the per-DOF stiffnesses are produced by the
        shared, OpenSees-free
        :func:`~fea_toolkit.opensees.releases.plan_releases` — the **same**
        planner the Tcl exporters use — so this method only translates the
        plan into ``ops`` calls and the OpenSeesPy domain is identical to the
        Tcl domain by construction.

        Each released end is modelled with the OpenSees "extra node +
        ``zeroLength``" pattern (M. Scott, OpenSeesDigital 2022)::

            structural_node_i → zeroLength(release) → {eid}_rel_i … member

        The ``zeroLength`` is given the **member's local axes** (``-orient``)
        so the release acts on the element's *local* DOFs (not global).
        Retained DOFs get a rigid ``Elastic`` material (η × the member's own
        section/material stiffness); fully released DOFs get a soft spring
        (μ × member stiffness), or are omitted from ``-dir`` when
        ``release_softness_factor`` is ``0``; partial-fixity DOFs use the
        SAP2000 spring stiffness directly.

        Using ``-mat``/``-dir`` rather than ``equalDOF`` keeps the
        ``Transformation`` constraint handler compatible with ``rigidLink``
        MPC end offsets — the same rationale as the bond-slip springs.
        """
        if not self.config.get("apply_releases", True):
            return
        releases = getattr(self.mesh_model, "frame_releases", None) or {}
        if not releases:
            return
        if self.config.get("hinge_model") == "lumped":
            warnings.warn(
                "apply_releases and hinge_model='lumped' both re-point member "
                "ends — releases skipped (lumped hinges win).",
                stacklevel=2,
            )
            return

        # ── Idempotency: preserve canonical endpoints on first call ──
        # Restoration is handled by _restore_release_canonical_state().
        # When bond-slip springs have already run (they precede this method
        # in build_domain), the frame elements are re-pointed to their
        # *_bond_i / *_bond_j nodes, so capture the true pre-instrumentation
        # endpoints from the bond canonical map instead of the current
        # (bond) node ids.
        if not hasattr(self, "_release_canonical_elements"):
            if hasattr(self, "_bond_canonical_elements"):
                self._release_canonical_elements = dict(self._bond_canonical_elements)
            else:
                self._release_canonical_elements = {
                    eid: (elem.node_i, elem.node_j)
                    for eid, elem in self.mesh_model.frame_elements.items()
                    if not getattr(elem, "inactive", False)
                }

        # Shared planner (also used by the Tcl export paths).  It owns the
        # η/μ scaling, the partial-fixity springs, the zero-length guard and
        # the warning messages — nothing release-specific is recomputed here.
        plan = plan_releases(self.mesh_model, self.config)
        if not plan["ends"]:
            return

        # ── Coincident release nodes ─────────────────────────────────
        release_node_tags: dict[str, int] = {}
        for rn in plan["release_nodes"]:
            self.mesh_model.nodes[rn["node_id"]] = Node(
                node_id=rn["node_id"],
                node_tag=rn["node_tag"],
                x=rn["x"],
                y=rn["y"],
                z=rn["z"],
            )
            ops.node(rn["node_tag"], rn["x"], rn["y"], rn["z"])
            self._created_node_tags.add(rn["node_tag"])
            release_node_tags[rn["node_id"]] = rn["node_tag"]

        # ── Element / material tag bases ─────────────────────────────
        # Element tags must clear every tag already in play: frame elements
        # (including any hinge/bond re-pointing), rigid end-offset links, and
        # anything already registered with OpenSees.
        try:
            max_ops_tag = max(ops.getEleTags(), default=0)
        except Exception:
            max_ops_tag = 0
        max_rigid_tag = max((r[3] for r in self._offset_rigid_links), default=0)
        next_tag = (
            max(
                max(
                    (e.elem_tag for e in self.mesh_model.frame_elements.values() if not e.inactive),
                    default=0,
                ),
                max_ops_tag,
                max_rigid_tag,
                max(self.frame_tag_map.values(), default=0),
            )
            + 1
        )
        next_mat_tag = max(self.material_tags.values(), default=0) + 1000
        mat_cache: dict[float, int] = {}

        for end in plan["ends"]:
            dirs: list[int] = []
            mats: list[int] = []
            for dof, stiffness in end["dofs"]:
                cache_key = round(stiffness, 6)
                mat_tag = mat_cache.get(cache_key)
                if mat_tag is None:
                    mat_tag = next_mat_tag
                    next_mat_tag += 1
                    ops.uniaxialMaterial("Elastic", mat_tag, stiffness)
                    mat_cache[cache_key] = mat_tag
                dirs.append(dof)
                mats.append(mat_tag)

            args: list = [
                "zeroLength",
                next_tag,
                end["struct_node_tag"],
                release_node_tags[end["node_id"]],
                "-mat",
                *mats,
                "-dir",
                *dirs,
            ]
            if end["orient"]:
                args += ["-orient", *end["orient"]]
            ops.element(*args)
            next_tag += 1

        # ── Re-point member ends to their release nodes ──────────────
        for eid, (node_i, node_j) in plan["endpoints"].items():
            elem = self.mesh_model.frame_elements.get(eid)
            if elem is not None:
                elem.node_i = node_i
                elem.node_j = node_j

    def _create_panel_zones(self) -> None:
        """Emit the panel-zone zero-length springs from ``MeshModel.panel_zones``.

        The Preprocessor has already split each beam-column joint (a column
        node plus a coincident ``*_pz`` beam node, with the beams re-pointed)
        and recorded the Krawinkler & Gupta spring parameters on the mesh.
        This method is read-only with respect to the ``MeshModel``: it only
        writes OpenSees objects — the in-plane translation tie, the out-of-plane
        beam-node fix, the bilinear ``Steel01`` material, and the zero-length
        rotational spring (DOF 5 = RY in a planar X-Z frame).
        """
        panel_zones = self.mesh_model.panel_zones
        if not panel_zones:
            return

        elements = self.mesh_model.frame_elements
        # Compute the next free element tag, avoiding every tag already claimed
        # by the mesh (active frame tags, the frame-tag map) and the live
        # OpenSees domain (shells, walls, rigid links created earlier in
        # build_domain).
        try:
            max_ops_tag = max(ops.getEleTags(), default=0)
        except Exception:
            max_ops_tag = 0
        max_rigid_tag = max(
            (r[3] for r in getattr(self, "_offset_rigid_links", None) or []), default=0
        )
        next_tag = (
            max(
                max(
                    (e.elem_tag for e in elements.values() if not getattr(e, "inactive", False)),
                    default=0,
                ),
                max_ops_tag,
                max_rigid_tag,
                max(self.frame_tag_map.values(), default=0),
            )
            + 1
        )
        mat_tag = 900001  # high, distinct base — avoids load/hinge tag collisions

        for column_node_id, spec in panel_zones.items():
            col_tag = self.mesh_model.nodes[column_node_id].node_tag
            beam_tag = self.mesh_model.nodes[spec.beam_node_id].node_tag

            # ── Panel-zone spring (in-plane rotation DOF 5 = RY) ──
            # Tie the in-plane translations across the panel zone and fix the
            # beam node's out-of-plane DOFs (UY / RX / RZ) exactly; the only
            # relative DOF is the in-plane rotation (RY), carried by the K&G
            # bilinear spring.
            ops.equalDOF(col_tag, beam_tag, 1, 3)
            ops.fix(beam_tag, 0, 1, 0, 1, 0, 1)
            ops.uniaxialMaterial("Steel01", mat_tag, spec.my, spec.k_theta, spec.b)
            ops.element(
                "zeroLength",
                next_tag,
                col_tag,
                beam_tag,
                "-mat",
                mat_tag,
                "-dir",
                5,
            )
            next_tag += 1
            mat_tag += 1

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
            # Capture the frozen beam endpoints directly from the mesh — panel
            # zones are resolved by the Preprocessor, so a beam already points
            # at its *_pz node here (the correct pre-instrumentation state).
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

        if n_spring:
            self._log.info("elements", f"Inserted bond-slip springs on {n_spring} member(s)")

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
