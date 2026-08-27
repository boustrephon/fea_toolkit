import math


def cqc_combine(modal_values: list[float], omega: list[float], damp_ratios: list[float]) -> float:
    """Complete Quadratic Combination of modal results (Der Kiureghian 1980).

    Uses the standard CQC correlation coefficient formula:

    .. math::

        \\rho_{ij} = \\frac{8 \\sqrt{\\zeta_i \\zeta_j} (\\zeta_i + r \\zeta_j) r^{3/2}}
                           {(1 - r^2)^2 + 4 \\zeta_i \\zeta_j r (1 + r^2) + 4 (\\zeta_i^2 + \\zeta_j^2) r^2}

    where :math:`r = \\omega_i / \\omega_j` and :math:`\\zeta` is the damping ratio.

    Args:
        modal_values: Per-mode response quantities (shear, moment, etc.).
        omega: Circular frequencies of each mode (rad/s).
        damp_ratios: Damping ratio for each mode.

    Returns:
        CQC-combined scalar value.
    """
    n = len(modal_values)
    if n == 0:
        return 0.0
    if n == 1:
        return abs(modal_values[0])
    total = 0.0
    for i in range(n):
        for j in range(n):
            di = damp_ratios[i] if i < len(damp_ratios) else 0.05
            dj = damp_ratios[j] if j < len(damp_ratios) else 0.05
            om_i = omega[i] if i < len(omega) else 1.0
            om_j = omega[j] if j < len(omega) else 1.0
            bij = om_i / om_j if om_j > 0 else 1.0
            rho = (8.0 * math.sqrt(di * dj) * (di + bij * dj) * (bij**1.5)) / (
                (1.0 - bij**2.0) ** 2.0
                + 4.0 * di * dj * bij * (1.0 + bij**2.0)
                + 4.0 * (di**2.0 + dj**2.0) * bij**2.0
            )
            total += modal_values[i] * modal_values[j] * rho
    return math.sqrt(max(total, 0.0))


def sum_reactions_with_overturning(
    reactions: dict,
    nodes: dict,
) -> dict[str, float]:
    """Sum per‑node reaction forces and moments, adding overturning
    moments from force × lever‑arm about the plan centroid at base level.

    **What it does**
    ``ops.nodeReaction(tag, dof)`` returns the reaction at each restrained
    DOF.  For pinned-base columns the rotational DOFs (Mx, My) are free
    and ``nodeReaction`` returns **zero** for those components, even
    though the element carries bending moment.  This function reconstructs
    the full overturning moment by adding the force × lever‑arm
    contribution of each reaction component about a fixed reference point:

    ``mx += fz·dy − fy·dz``,  ``my += fx·dz − fz·dx``,  ``mz += fy·dx − fx·dy``

    The reference point is the **bounding‑box midpoint** ``(min+max)/2``
    of the **base (support) nodes only** — the centre of the base
    footprint.  This fixed reference is used consistently for all load
    cases (static and RS) so that moments share a common origin for
    comparison and combination.

    **Usage**
    - **Static lateral loads** (Wind, Quake): called from
      :func:`pumphouse_report_v2.run_linear_cases` and
      :meth:`AnalysisBuilder.run_static_analysis
      <fea_toolkit.opensees.analysis_builder.AnalysisBuilder.run_static_analysis>`
    - **Response‑spectrum analysis**: the same lever-arm logic is applied
      per-mode in
      :meth:`AnalysisBuilder.run_response_spectrum_analysis
      <fea_toolkit.opensees.analysis_builder.AnalysisBuilder.run_response_spectrum_analysis>`,
      but the source is ``ops.eleResponse(eid, 'forces')`` (global
      element‑end forces) rather than ``nodeReaction``.  The element‑end
      forces include the column bending moment directly, but the axial
      force lever‑arm (Fz from one column × distance to another) is a
      structural‑level effect that must still be added — this function's
      approach is replicated there.

    Args:
        reactions: ``{node_key: {fx, fy, fz, mx, my, mz}}``.
            Keys may be ``node_tag`` (int) or ``node_id`` (str);
            the function tries both lookups.
        nodes: Node lookup dict — each value must have ``.x``, ``.y``,
            ``.z`` attributes.

    Returns:
        ``{fx, fy, fz, mx, my, mz}`` summed vector with overturning
        moment included.
    """
    if not nodes:
        return {"fx": 0.0, "fy": 0.0, "fz": 0.0, "mx": 0.0, "my": 0.0, "mz": 0.0}

    # Build a one-time tag-to-node index for efficient lookups.
    # Reaction keys may be string IDs or integer node_tags; build both.
    tag_to_node: dict = {}
    for nd in nodes.values():
        t = getattr(nd, "node_tag", None)
        if t is not None:
            tag_to_node[t] = nd

    def _resolve_node(nid):
        """Look up a node by string key or integer tag."""
        if isinstance(nid, str):
            nd = nodes.get(nid)
            if nd is not None:
                return nd
        return tag_to_node.get(nid)

    # Identify the base (support) nodes — those that appear in reactions.
    # The centroid is computed from these nodes only, so that the
    # overturning moment reference is at the centre of the base footprint.
    _base_nds = []
    for nid in reactions:
        nd = _resolve_node(nid)
        if nd is not None:
            _base_nds.append(nd)

    if _base_nds:
        xs = [n.x for n in _base_nds]
        ys = [n.y for n in _base_nds]
        cx = (min(xs) + max(xs)) * 0.5  # bounding‑box midpoint (v1 match)
        cy = (min(ys) + max(ys)) * 0.5
        z_base = sum(n.z for n in _base_nds) / len(_base_nds)  # avg of support nodes
    else:
        # Fallback: all nodes
        xs = [n.x for n in nodes.values()]
        ys = [n.y for n in nodes.values()]
        cx = (min(xs) + max(xs)) * 0.5
        cy = (min(ys) + max(ys)) * 0.5
        z_base = min(n.z for n in nodes.values())

    summed = {"fx": 0.0, "fy": 0.0, "fz": 0.0, "mx": 0.0, "my": 0.0, "mz": 0.0}
    for nid, r in reactions.items():
        node = _resolve_node(nid)
        if node is None:
            continue
        fx = r.get("fx", 0.0)
        fy = r.get("fy", 0.0)
        fz = r.get("fz", 0.0)
        mx = r.get("mx", 0.0)
        my = r.get("my", 0.0)
        mz = r.get("mz", 0.0)
        dx = node.x - cx
        dy = node.y - cy
        dz = node.z - z_base
        summed["fx"] += fx
        summed["fy"] += fy
        summed["fz"] += fz
        summed["mx"] += mx + fz * dy - fy * dz
        summed["my"] += my + fx * dz - fz * dx
        summed["mz"] += mz + fy * dx - fx * dy
    return summed
