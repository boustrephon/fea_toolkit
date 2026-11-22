"""Analysis-builder mixin: link (connection / isolator) elements.

Phase 1 emits the *linear* (elastic) representation of SAP2000/ETABS link
objects: a ``twoNodeLink`` element with one ``Elastic`` uniaxial material per
active DOF, using the effective stiffness written in
``LINK PROPERTY DEFINITIONS 02 - LINEAR``.  A grounded link (no end J) anchors
node i to a coincident fixed ground node via ``zeroLength``.

``link_model`` selects the representation:

* ``"elastic"`` (default) — effective-stiffness springs, valid for elastic
  static / modal / response-spectrum analyses of *all* link types.
* ``"nonlinear"`` — the per-DOF nonlinear types (Gap, Hook, Plastic (Wen),
  Damper) emit their type-specific uniaxial materials (Phase 2b(i)).  The
  bearing isolators emit dedicated bearing elements: Rubber Isolator →
  ``elastomericBearingPlasticity``/``elastomericBearingBoucWen``, Friction
  Isolator (and T/C Friction Isolator, compression-only) → ``singleFPBearing``,
  and Triple Friction Pendulum → ``TripleFrictionPendulum``.  A link type the
  toolkit does not cover is *warned* (``NotImplementedWarning``) and linearised
  on its effective stiffness; it raises ``NotImplementedError`` only when there
  is no effective stiffness to linearise onto.

``wen_model`` selects the Plastic (Wen) material: ``"steel02"`` (default,
smooth Giuffré–Menegotto-Pinto transition) or ``"hysteretic"`` (sharp bilinear
backbone).
"""

import logging
import math
import warnings
from typing import Optional

import numpy as np
import openseespy.opensees as ops

from .._warnings import REPORT_URL, NotImplementedWarning
from ..model.geometry import get_local_axes, rotate_about_axis
from ..model.sap_data import (
    DamperParams,
    GapParams,
    HookParams,
    RubberIsolatorParams,
    WenParams,
    link_type_status,
)

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


def _direction_vector(token: str) -> Optional[np.ndarray]:
    """Parse an axis-aligned or arbitrary direction token into a unit vector.

    ``LINK LOCAL AXES ASSIGNMENTS 2 - ADVANCED`` writes ``CoordDir1`` /
    ``CoordDir2`` as global-axis tokens (``+X``, ``-Y``, …) or, for a general
    direction, as a coordinate triple (``(0.707, 0, 0.707)`` — comma- or
    whitespace-separated, optionally parenthesised).  Returns ``None`` for an
    empty or unparseable token.
    """
    tok = token.strip()
    if not tok:
        return None
    v = _DIRECTION_VECTORS.get(tok.upper())
    if v is not None:
        return v.copy()
    s = tok.strip("()[]{}")
    parts = s.replace(",", " ").split()
    if len(parts) != 3:
        return None
    try:
        vec = np.array([float(x) for x in parts], dtype=float)
    except ValueError:
        return None
    norm = np.linalg.norm(vec)
    if norm < 1e-12:
        return None
    return vec / norm


# ═══════════════════════════════════════════════════════════════════
# Nonlinear link material backbones (pure — no ops, unit-testable)
# ═══════════════════════════════════════════════════════════════════

#: Nonlinear link emission policy — the single source of truth for which link
#: type gets which emitter.  ``"per-dof"`` types get one uniaxial spring per
#: direction; ``"bearing"`` types get a dedicated bearing element.  ``Linear``
#: resolves to ``"linear"``; anything else is classified through
#: :func:`fea_toolkit.model.sap_data.link_type_status` — a covered damper
#: subtype (``"Damper - …"``) resolves to ``"per-dof"``, an uncovered type to
#: ``"unsupported"`` (warned and linearised in ``_create_link_elements``).
_LINK_EMISSION_FAMILY: dict[str, str] = {
    "Gap": "per-dof",
    "Hook": "per-dof",
    "Plastic (Wen)": "per-dof",
    "Damper": "per-dof",
    "Rubber Isolator": "bearing",
    "Friction Isolator": "bearing",
    "T/C Friction Isolator": "bearing",
    "Triple Friction Pendulum": "bearing",
}

#: Link types with a nonlinear emitter (derived — never hand-edited).
_SUPPORTED_NONLINEAR_LINK_TYPES = frozenset(_LINK_EMISSION_FAMILY)

#: Steel02 (Giuffré–Menegotto-Pinto) transition-curvature defaults.
_STEEL02_CR1 = 0.925
_STEEL02_CR2 = 0.15

#: Tag block for friction models (distinct from the link material block).
_LINK_FRICTION_BASE = 710000


def _link_emission_family(prop) -> str:
    """Emission family: ``"linear"`` / ``"per-dof"`` / ``"bearing"`` / ``"unsupported"``.

    Uncovered link types (see :func:`fea_toolkit.model.sap_data.link_type_status`)
    resolve to ``"unsupported"``; covered types resolve through the family table.
    """
    if link_type_status(prop.link_type) is not None:
        return "unsupported"
    if prop.link_type == "Linear":
        return "linear"
    family = _LINK_EMISSION_FAMILY.get(prop.link_type)
    if family is not None:
        return family
    if prop.link_type.startswith("Damper"):
        return "per-dof"
    return "unsupported"


def _is_per_dof_nonlinear(prop) -> bool:
    """True if the link type is emitted per-DOF in the nonlinear representation."""
    return _link_emission_family(prop) == "per-dof"


def _wen_r0(n: float) -> float:
    """Map the Wen yield exponent ``n`` (sharpness) to a Steel02 ``R0``.

    Both control the roundness of the yield transition in the same direction
    (larger → sharper), but the two functional forms differ, so this is a
    documented *qualitative* correspondence, not a closed form.  Clamped to the
    recommended Steel02 band (10–30).
    """
    return min(max(10.0, 5.0 + 5.0 * n), 30.0)


def _hysteretic_bilinear_args(ke, fy, ratio, extent=None):
    """``Hysteretic`` backbone args for a symmetric bilinear (``ke, fy, ratio``).

    Returns the 17 positional args *after* the material tag.  The first point
    is the yield point ``(fy, fy/ke)`` — never the origin, which ``Hysteretic``
    rejects (it divides ``mom1p/rot1p``).  The second and third points extend
    the post-yield branch (slope ``ratio * ke``), which ``Hysteretic`` keeps
    beyond the last point.
    """
    ey = fy / ke if ke > 0.0 else 0.0
    if extent is None or extent <= ey:
        extent = 10.0 * ey if ey > 0.0 else 1.0
    s2 = fy + ratio * ke * (extent - ey)
    s3 = fy + ratio * ke * (2.0 * extent - ey)
    return [
        fy,
        ey,  # s1p, e1p (yield)
        s2,
        extent,  # s2p, e2p
        s3,
        2.0 * extent,  # s3p, e3p
        -fy,
        -ey,  # s1n, e1n
        -s2,
        -extent,  # s2n, e2n
        -s3,
        -2.0 * extent,  # s3n, e3n
        1.0,
        1.0,
        0.0,
        0.0,
        0.0,  # neutral pinching / damage
    ]


def _hysteretic_one_sided_args(ke, open, side, extent=None):
    """``Hysteretic`` backbone args for a one-sided Gap/Hook link.

    ``side`` is ``"gap"`` (compression-only) or ``"hook"`` (tension-only);
    ``open`` is the initial gap.  The active branch has slope ``ke`` from the
    gap-closure point; the released branch carries a tiny residual stiffness
    (``ke * 1e-6``) so the material never has a truly zero tangent.

    When ``open == 0`` the active branch skips the (zero, zero) gap-closure
    point — ``Hysteretic`` rejects a backbone whose first branch point sits at
    the origin (it divides ``mom1n/rot1n``).
    """
    if extent is None or extent <= 0.0:
        extent = 10.0 * open if open > 0.0 else 1.0
    tiny = ke * 1.0e-6 if ke > 0.0 else 1.0e-9

    # Released direction: flat at a tiny stress over [extent, 3*extent], so its
    # first point is never at the origin (slope = tiny from the origin).
    released_pos = (tiny * extent, extent, tiny * extent, 2.0 * extent, tiny * extent, 3.0 * extent)
    released_neg = (
        -tiny * extent,
        -extent,
        -tiny * extent,
        -2.0 * extent,
        -tiny * extent,
        -3.0 * extent,
    )

    if side == "gap":  # compression-only: negative branch active
        if open > 0.0:
            active = (
                0.0,
                -open,
                -ke * extent,
                -open - extent,
                -2.0 * ke * extent,
                -open - 2.0 * extent,
            )
        else:
            active = (
                -ke * extent,
                -extent,
                -2.0 * ke * extent,
                -2.0 * extent,
                -3.0 * ke * extent,
                -3.0 * extent,
            )
        pos, neg = released_pos, active
    else:  # hook: tension-only, positive branch active
        if open > 0.0:
            active = (0.0, open, ke * extent, open + extent, 2.0 * ke * extent, open + 2.0 * extent)
        else:
            active = (
                ke * extent,
                extent,
                2.0 * ke * extent,
                2.0 * extent,
                3.0 * ke * extent,
                3.0 * extent,
            )
        pos, neg = active, released_neg

    return [*pos, *neg, 1.0, 1.0, 0.0, 0.0, 0.0]


def _rubber_bearing_params(prop):
    """Return ``(kInit, qd, alpha1)`` for a Rubber Isolator's shear DOFs.

    ``kInit`` / ``qd`` act on the coupled U2/U3 shear, so the U2 value is used
    (U3 for an axisymmetric bearing is equal).  ``alpha1`` is the post-yield
    stiffness ratio.

    SAP2000's ``Fy`` is the *yield force*, but the Schellenberg bearing's
    ``qd`` is the *post-yield intercept* (its monotonic post-yield force is
    ``F = alpha1*kInit*u + qd``), so ``qd = Fy * (1 - ratio)`` reproduces the
    SAP2000 bilinear exactly.
    """
    p = prop.nonlinear_params
    k = p.k if isinstance(p, RubberIsolatorParams) else prop.ke
    fy = p.fy if isinstance(p, RubberIsolatorParams) else [0.0] * 6
    ratio = p.ratio if isinstance(p, RubberIsolatorParams) else 0.0
    k_init = k[1] if k[1] > 0.0 else k[2]
    fy = fy[1] if fy[1] > 0.0 else fy[2]
    return k_init, fy * (1.0 - ratio), ratio


def _emit_friction_model(frn_tag, p):
    """Create the friction model for a Friction Isolator (Coulomb/VelDependent)."""
    if p.mu_fast > 0.0 and p.rate > 0.0:
        ops.frictionModel("VelDependent", frn_tag, p.mu, p.mu_fast, p.rate)
    else:
        ops.frictionModel("Coulomb", frn_tag, p.mu)


class LinkMixin:
    """Emit link (connection / isolator / damper) elements."""

    def _create_link_elements(self) -> None:
        links = getattr(self.mesh_model, "links", None) or []
        if not links:
            return
        props = getattr(self.mesh_model, "link_properties", None) or {}
        assigns = getattr(self.mesh_model, "link_assignments", None) or {}
        model = self.config.get("link_model", "elastic")
        wen_model = self.config.get("wen_model", "steel02")
        rubber_model = self.config.get("rubber_model", "plasticity")

        try:
            max_ops_tag = max(ops.getEleTags(), default=0)
        except Exception:
            max_ops_tag = 0
        max_frame = max(
            (e.elem_tag for e in self.mesh_model.frame_elements.values() if not e.inactive),
            default=0,
        )
        next_tag = max(max_frame, max_ops_tag, max(self.frame_tag_map.values(), default=0)) + 1
        mat_cache: dict[tuple, int] = {}
        friction_count = [0]
        warned_linearised: set[str] = set()
        warned_unsupported: set[str] = set()

        for link in links:
            prop = props.get(assigns.get(link.link_id, ""))
            if prop is None:
                logger.warning(
                    "link %s has no recognised property assignment — skipped", link.link_id
                )
                continue
            family = _link_emission_family(prop)
            if family == "unsupported":
                # Uncovered link type — warn (once per property) and linearise on
                # the effective stiffness.  Error only when there is nothing to
                # linearise onto (a zero-stiffness link would silently drop the
                # member from the model).
                if not any(prop.ke) and not any(prop.fixed):
                    raise NotImplementedError(
                        f"link property {prop.name!r} has type {prop.link_type!r}, "
                        f"which has no nonlinear emitter and no effective stiffness "
                        f"to linearise onto — cannot build it. Please report this "
                        f"at {REPORT_URL}"
                    )
                if prop.name not in warned_unsupported:
                    warned_unsupported.add(prop.name)
                    warnings.warn(
                        f"link property {prop.name!r} has type {prop.link_type!r}, "
                        f"which has no nonlinear emitter; it is represented by "
                        f"effective stiffness. Please report this at {REPORT_URL}",
                        NotImplementedWarning,
                        stacklevel=2,
                    )
            elif model == "elastic" and family != "linear" and prop.name not in warned_linearised:
                warned_linearised.add(prop.name)
                logger.warning(
                    "link property %r (%s) represented by effective stiffness; "
                    "set link_model='nonlinear' for nonlinear response",
                    prop.name,
                    prop.link_type,
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

            if model == "nonlinear" and family == "bearing":
                self._create_bearing_element(
                    link,
                    prop,
                    ni,
                    j_tag,
                    grounded,
                    next_tag,
                    mat_cache,
                    rubber_model,
                    friction_count,
                )
                next_tag += 1
                continue

            mats, dirs = self._link_spring_mats(prop, mat_cache, model, wen_model)
            if not dirs:
                logger.warning("link %s has no active DOFs — skipped", link.link_id)
                continue

            orient = self._link_orient(link)
            if orient is None:
                # Zero-length link with no reference-vector axes: fall back to
                # the global axes (SAP2000's default local axes for a
                # zero-length link).  A coincident ``twoNodeLink`` has no I→J
                # vector for OpenSees to derive local axes from, so an explicit
                # ``-orient`` is required — without it the element's local frame
                # is degenerate and the model solves as singular (NaN), which is
                # exactly the failure a coincident link otherwise produces.
                logger.debug("link %s has no local axes; using global axes", link.link_id)
                global_orient = ("-orient", 1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
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
                        *global_orient,
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
                        *global_orient,
                    )
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

    def _link_spring_mats(self, prop, mat_cache, model="elastic", wen_model="steel02"):
        """Return ``(mat_tags, dirs)`` for a link property, creating materials."""
        mats: list[int] = []
        dirs: list[int] = []
        fixed_k = self._link_fixed_stiffness(prop)
        nonlinear = model == "nonlinear" and _is_per_dof_nonlinear(prop)
        for dof in range(6):
            if prop.fixed[dof]:
                mat_tag = self._cached_mat(mat_cache, ("elastic", fixed_k), "Elastic", fixed_k)
            elif nonlinear:
                mat_tag = self._nonlinear_dof_mat(prop, dof, wen_model, mat_cache)
                if mat_tag is None:
                    continue
            else:
                k = prop.ke[dof]
                if k <= 0.0:
                    continue
                mat_tag = self._cached_mat(mat_cache, ("elastic", k), "Elastic", k)
            mats.append(mat_tag)
            dirs.append(dof + 1)
        return mats, dirs

    @staticmethod
    def _cached_mat(mat_cache: dict[tuple, int], key: tuple, mat_type: str, *params) -> int:
        """Create (or reuse) a uniaxial material, returning its integer tag."""
        tag = mat_cache.get(key)
        if tag is None:
            tag = _LINK_MAT_BASE + len(mat_cache)
            mat_cache[key] = tag
            ops.uniaxialMaterial(mat_type, tag, *params)
        return tag

    def _nonlinear_dof_mat(self, prop, dof, wen_model, mat_cache):
        """Material tag for one DOF of a nonlinear per-DOF link (or ``None``)."""
        params = prop.nonlinear_params
        if prop.link_type == "Gap" and isinstance(params, GapParams):
            # The nonlinear stiffness ``K`` (not the effective ``Ke``) is the
            # backbone slope; fall back to ``Ke`` when no nonlinear K is stored
            # (e.g. the S2K Gap table carries only the effective Ke).
            ke = params.k[dof] if params.k[dof] > 0.0 else prop.ke[dof]
            if ke <= 0.0:
                return None
            args = _hysteretic_one_sided_args(ke, params.open[dof], "gap")
            return self._cached_mat(
                mat_cache, ("hyst", "gap", ke, params.open[dof]), "Hysteretic", *args
            )
        if prop.link_type == "Hook" and isinstance(params, HookParams):
            ke = params.k[dof] if params.k[dof] > 0.0 else prop.ke[dof]
            if ke <= 0.0:
                return None
            args = _hysteretic_one_sided_args(ke, params.open[dof], "hook")
            return self._cached_mat(
                mat_cache, ("hyst", "hook", ke, params.open[dof]), "Hysteretic", *args
            )
        if prop.link_type.startswith("Damper"):
            return self._damper_dof_mat(prop, dof, mat_cache)
        if prop.link_type == "Plastic (Wen)" and isinstance(params, WenParams):
            return self._wen_dof_mat(prop, dof, wen_model, mat_cache)
        return None

    def _wen_dof_mat(self, prop, dof, wen_model, mat_cache):
        """Material for one Wen DOF — Steel02 (smooth) or Hysteretic (bilinear)."""
        params = prop.nonlinear_params
        ke = params.k[dof] if params.k[dof] > 0.0 else prop.ke[dof]
        fy = params.fy[dof]
        if ke <= 0.0:
            return None
        if fy <= 0.0:
            # No yield force in this DOF → effectively linear.
            return self._cached_mat(mat_cache, ("elastic", ke), "Elastic", ke)
        if wen_model == "steel02":
            return self._cached_mat(
                mat_cache,
                ("steel02", fy, ke, params.ratio, params.n),
                "Steel02",
                fy,
                ke,
                params.ratio,
                _wen_r0(params.n),
                _STEEL02_CR1,
                _STEEL02_CR2,
                0.0,  # a1 — isotropic hardening ratio (0 = none)
                1.0,  # a2 — hardening denominator (nonzero)
                0.0,  # a3 — isotropic hardening ratio (0 = none)
                1.0,  # a4 — hardening denominator (nonzero)
                0.0,  # sigInit
            )
        args = _hysteretic_bilinear_args(ke, fy, params.ratio)
        return self._cached_mat(
            mat_cache, ("hyst", "bilin", ke, fy, params.ratio), "Hysteretic", *args
        )

    def _damper_dof_mat(self, prop, dof, mat_cache):
        """Material for one damper DOF, driven by the ``c``/``ce``/``ke``/``fy`` present.

        ``c`` (the nonlinear dashpot coefficient, falling back to the effective
        ``ce`` when unset) > 0 → a (viscoelastic when ``ke`` is also present)
        dashpot;
        otherwise a bilinear/friction ``Hysteretic`` backbone when both the
        initial stiffness and a yield force are present.  A DOF with only an
        effective stiffness (no damping, no yield) falls back to an ``Elastic``
        spring so a specified stiffness is never silently dropped, even when
        other DOFs of the same link are nonlinear.

        Uncovered damper subtypes (friction directional / orthotropic / spring
        based) never reach here — ``_link_emission_family`` routes them to
        ``"unsupported"``, which warns and linearises in ``_create_link_elements``.
        """
        params = prop.nonlinear_params
        cexp = params.cexp if isinstance(params, DamperParams) else 1.0
        # The nonlinear dashpot coefficient ``c`` (F·(s/L)^cexp) is distinct
        # from the effective damping ``Ce`` (a linearised coefficient).  For a
        # linear damper (cexp == 1) the two coincide, so fall back to ``ce``
        # when ``c`` was not populated (e.g. a table carrying only Ce).
        c_nl = params.c[dof] if isinstance(params, DamperParams) else 0.0
        dash_c = c_nl if c_nl > 0.0 else prop.ce[dof]
        ke = prop.ke[dof]
        if dash_c > 0.0:
            if ke > 0.0:
                # Viscoelastic: elastic stiffness in parallel with the damper.
                e_tag = self._cached_mat(mat_cache, ("elastic", ke), "Elastic", ke)
                v_tag = self._cached_mat(
                    mat_cache, ("viscous", dash_c, cexp), "Viscous", dash_c, cexp
                )
                return self._cached_mat(
                    mat_cache, ("parallel", e_tag, v_tag), "Parallel", e_tag, v_tag
                )
            return self._cached_mat(mat_cache, ("viscous", dash_c, cexp), "Viscous", dash_c, cexp)
        fy = params.fy[dof] if isinstance(params, DamperParams) else 0.0
        ratio = params.ratio if isinstance(params, DamperParams) else 0.0
        k_nl = params.k[dof] if isinstance(params, DamperParams) else 0.0
        k_init = k_nl if k_nl > 0.0 else ke
        if k_init > 0.0 and fy > 0.0:
            # Bilinear (ratio > 0) or friction (ratio = 0 → elastic-perfectly
            # plastic) damper — a symmetric Hysteretic backbone.  The *nonlinear*
            # stiffness ``K`` is the initial slope (the effective ``Ke`` is the
            # linearised secant, wrong for the backbone) — same rule as the
            # Rubber Isolator's ``_rubber_bearing_params``.
            args = _hysteretic_bilinear_args(k_init, fy, ratio)
            return self._cached_mat(
                mat_cache, ("hyst", "bilin", k_init, fy, ratio), "Hysteretic", *args
            )
        if ke > 0.0:
            # Linear-only DOF: no damping and no yield — use the effective
            # elastic stiffness even though other DOFs of this link are
            # nonlinear (so a specified stiffness is never silently dropped).
            return self._cached_mat(mat_cache, ("elastic", ke), "Elastic", ke)
        return None

    def _create_bearing_element(
        self, link, prop, ni, j_tag, grounded, next_tag, mat_cache, rubber_model, friction_count
    ):
        """Emit a bearing element for a Rubber/Friction Isolator or TPB link.

        The rubber and friction-pendulum bearings model the coupled U2/U3 shear
        internally and take ``Elastic`` materials for the axial (``-P``),
        torsional (``-T``) and bending (``-My``/``-Mz``) DOFs.  ``iNode`` is
        the bottom (concave sliding surface for an FP bearing), ``jNode`` the
        top (slider).  The triple friction pendulum (``TripleFrictionPendulum``)
        takes the same four materials *positionally* plus three friction models,
        and ignores ``-orient`` (its axial axis is I→J).
        """
        if grounded:
            i_node, j_node = j_tag, ni  # j_tag is the coincident ground node
        else:
            i_node, j_node = ni, j_tag

        aux = {
            "P": self._bearing_aux_mat(prop, 0, mat_cache),
            "T": self._bearing_aux_mat(prop, 3, mat_cache),
            "My": self._bearing_aux_mat(prop, 4, mat_cache),
            "Mz": self._bearing_aux_mat(prop, 5, mat_cache),
        }

        if prop.link_type == "Rubber Isolator":
            k_init, qd, alpha1 = _rubber_bearing_params(prop)
            if rubber_model == "boucwen":
                args = [
                    "elastomericBearingBoucWen",
                    next_tag,
                    i_node,
                    j_node,
                    k_init,
                    qd,
                    alpha1,
                    0.0,
                    0.0,
                    1.0,
                    0.5,
                    0.5,
                ]
            else:
                # ``elastomericBearingPlasticity`` args after the shear DOFs:
                # ``alpha2`` (post-yield ratio of the *nonlinear* hardening
                # component, 0 for the bilinear LRB) and ``mu`` (the nonlinear-
                # hardening exponent).  ``mu`` must be positive: mu=0 makes the
                # hardening term degenerate (|u|^0) and the element NaNs under
                # static gravity.  The repo's E-Defense LRB backbone benchmark
                # (tests/test_link_benchmarks.py) calibrates ``mu = 1.0``.
                args = [
                    "elastomericBearingPlasticity",
                    next_tag,
                    i_node,
                    j_node,
                    k_init,
                    qd,
                    alpha1,
                    0.0,
                    1.0,
                ]
        elif prop.link_type == "Triple Friction Pendulum":
            p = prop.nonlinear_params
            frn_tags = []
            for mu in p.mu:
                frn_tag = _LINK_FRICTION_BASE + friction_count[0]
                friction_count[0] += 1
                ops.frictionModel("Coulomb", frn_tag, mu)
                frn_tags.append(frn_tag)
            # The TPB element takes its four auxiliary materials positionally
            # (matP/T/My/Mz) and ignores ``-orient`` — it orients its axial
            # (local-x) axis along I→J, which is vertical for a base isolator.
            args = [
                "TripleFrictionPendulum",
                next_tag,
                i_node,
                j_node,
                *frn_tags,
                aux["P"],
                aux["T"],
                aux["My"],
                aux["Mz"],
                *p.radius,
                *p.ubar,
                p.W,
                p.Uy,
                p.Kvt,
                p.minFv,
                p.tol,
            ]
        else:  # Friction Isolator / T/C Friction Isolator
            p = prop.nonlinear_params
            if prop.link_type == "T/C Friction Isolator":
                logger.warning(
                    "link property %r (T/C Friction Isolator) is represented by "
                    "singleFPBearing with its *compression* friction; the tension "
                    "friction coefficients (SlowT/FastT/RateT) are not representable "
                    "and are ignored",
                    prop.name,
                )
            frn_tag = _LINK_FRICTION_BASE + friction_count[0]
            friction_count[0] += 1
            _emit_friction_model(frn_tag, p)
            k_init = p.k[1] if p.k[1] > 0.0 else p.k[2]
            args = [
                "singleFPBearing",
                next_tag,
                i_node,
                j_node,
                frn_tag,
                p.radius,
                k_init,
            ]

        if prop.link_type != "Triple Friction Pendulum":
            args += ["-P", aux["P"], "-T", aux["T"], "-My", aux["My"], "-Mz", aux["Mz"]]
            orient = self._link_orient(link)
            if orient is not None:
                vx, vy = orient
                args += ["-orient", *vx, *vy]
        ops.element(*args)

    def _bearing_aux_mat(self, prop, dof, mat_cache):
        """Elastic material for a bearing's axial/torsional/bending DOF."""
        if prop.fixed[dof]:
            k = self._link_fixed_stiffness(prop)
            return self._cached_mat(mat_cache, ("elastic", k), "Elastic", k)
        k = prop.ke[dof]
        if k > 0.0:
            return self._cached_mat(mat_cache, ("elastic", k), "Elastic", k)
        # Released DOF: a tiny (but non-zero) stiffness, so the bearing
        # element's internal 1/k never divides by zero — physically ~free.
        tiny = max((v for v in prop.ke if v > 0.0), default=1.0) * 1.0e-6
        return self._cached_mat(mat_cache, ("elastic", tiny), "Elastic", tiny)

    def _link_fixed_stiffness(self, prop) -> float:
        """Stiffness for a ``Fixed`` link DOF — rigid, scaled off the model.

        A fixed DOF is modelled as a very stiff ``Elastic`` spring.  Scaling it
        off the link's own largest effective stiffness keeps it unit-consistent
        without an absolute magic number (which would be ill-conditioned in the
        wrong unit system).  A fully-rigid link (all-zero effective stiffness)
        scales the penalty off the stiffest effective stiffness across *all*
        link properties; only when the model has none does it fall back to a
        dimensionless ``1.0`` with a warning (the fixed-DOF stiffness is then
        only approximate in the model's units).
        """
        scale = max((k for k in prop.ke if k > 0.0), default=0.0)
        if scale <= 0.0:
            scale = self._max_link_stiffness()
            if scale <= 0.0:
                logger.warning(
                    "link property %r has fixed DOFs but no effective stiffness "
                    "anywhere in the model to scale the penalty off; using a "
                    "dimensionless 1.0 (the fixed-DOF stiffness is then only "
                    "approximate in the model's units)",
                    prop.name,
                )
                scale = 1.0
        return scale * 1.0e6

    def _max_link_stiffness(self) -> float:
        """Largest effective stiffness across all link properties (0 if none)."""
        props = getattr(self.mesh_model, "link_properties", None) or {}
        return max((k for p in props.values() for k in p.ke if k > 0.0), default=0.0)

    def _link_orient(self, link):
        """Return ``(vx, vy)`` orientation vectors, or ``None`` for zero length.

        ``vx`` is the local-1 axis and ``vy`` the local-2 axis.  Reference-vector
        axes (``coord_dir1`` / ``coord_dir2`` from ``LINK LOCAL AXES ASSIGNMENTS
        2 - ADVANCED``) take precedence — they are the only way to orient a
        zero-length link (e.g. a vertical base isolator whose U1 must be
        vertical).  A ``coord_dir2`` that is not exactly perpendicular to
        ``coord_dir1`` is projected onto the plane normal to it before the
        ``angle`` rotation is applied.  Otherwise the I→J geometry and the
        ``angle`` rotation (``1 - TYPICAL``, degrees about local 1) define the
        axes.

        Returns:
            ``(vx, vy)`` as unit vectors, or ``None`` when the orientation could
            not be resolved (a zero-length/coincident link with no reference
            vectors) — distinct from a resolved default orientation.
        """
        vx = _direction_vector(link.coord_dir1)
        if vx is not None:
            vy = _direction_vector(link.coord_dir2)
            if vy is not None:
                # coord_dir2 may not be exactly perpendicular to vx: project it
                # onto the plane normal to vx, then apply the TYPICAL ``angle``
                # about vx.  For an already-orthogonal reference with angle 0
                # this reproduces the previous result exactly.
                proj = vy - np.dot(vy, vx) * vx
                norm = float(np.linalg.norm(proj))
                if norm < 1e-12:
                    # coord_dir2 is (anti)parallel to vx — no usable local-2
                    # reference; fall back to the CSI default axes.
                    vx_o, vy_o, _vz = get_local_axes(vx, link.angle)
                    return vx_o, vy_o
                vy2 = proj / norm
                if link.angle != 0.0:
                    vy2 = rotate_about_axis(vy2, vx, math.radians(link.angle))
                return vx, vy2
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
