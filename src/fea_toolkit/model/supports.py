"""Support conditions: how a node's restraints and joint constraints read.

Qt-free and renderer-free on purpose.  The Inspector *lists* these facts and the
glyph renderer *draws* the same ones, so both the lookup and the naming live here
rather than in two call sites that could quietly drift apart.

A **restraint** is six flags — ``[U1, U2, U3, R1, R2, R3]``, ``1`` = fixed —
stored per node id on the model
(:class:`~fea_toolkit.model.sap_data.Restraint`).  A **joint constraint** is an
assignment rather than a flag: the model maps a node id to a constraint name
(``constraint_assignments``) and keeps the definition separately
(``constraints``), so describing one node needs both.
"""

from typing import Any

#: DOF labels, in the order the flags are stored.
DOF_LABELS = ("U1", "U2", "U3", "R1", "R2", "R3")

#: Fixed-DOF patterns that carry a conventional name.  Anything else is reported
#: by its DOF labels — naming an arbitrary set would be guessing.
_PATTERN_NAMES = {
    (1, 1, 1, 1, 1, 1): "Fixed",
    (1, 1, 1, 0, 0, 0): "Pinned",
}


def restraint_dofs(model: Any, node_id: str) -> tuple:
    """The six restraint flags at *node_id*.

    Args:
        model: A ``SAPModelData`` or ``MeshModel`` — anything with
            ``restraints``.  ``None`` (an archive, or no model) has none.
        node_id: Node label.

    Returns:
        ``(U1, U2, U3, R1, R2, R3)`` as ints, or ``()`` when the node carries no
        restraint definition at all.
    """
    restraints = getattr(model, "restraints", None) or {}
    entry = restraints.get(node_id)
    if entry is None:
        return ()
    return tuple(int(flag) for flag in getattr(entry, "dofs", ()))


def fixed_dofs(model: Any, node_id: str) -> tuple:
    """Labels of the restrained DOFs at *node_id*, e.g. ``("U1", "U2")``."""
    return tuple(label for label, flag in zip(DOF_LABELS, restraint_dofs(model, node_id)) if flag)


def restraint_label(model: Any, node_id: str) -> str:
    """A readable restraint line: ``"Fixed"``, ``"Pinned (U1 U2 U3)"``, or ``""``.

    A node with nothing restrained returns ``""`` rather than ``"Free"``, so the
    Inspector omits the row instead of reporting an absence on every node.
    """
    dofs = restraint_dofs(model, node_id)
    if not dofs or not any(dofs):
        return ""
    labels = " ".join(fixed_dofs(model, node_id))
    name = _PATTERN_NAMES.get(tuple(int(flag) for flag in dofs))
    return f"{name} ({labels})" if name else labels


def constraint_label(model: Any, node_id: str) -> str:
    """The joint constraint assigned to *node_id*, e.g. ``"D1 (DIAPHRAGM)"``.

    Returns ``""`` when the node has no assignment.  The supported DOFs of a
    *diaphragm* are a property of the component, not of the node, so only the
    assignment and its type are reported here.
    """
    assignments = getattr(model, "constraint_assignments", None) or {}
    name = assignments.get(node_id)
    if not name:
        return ""
    definitions = getattr(model, "constraints", None) or {}
    definition = definitions.get(name)
    kind = getattr(definition, "constraint_type", "") if definition is not None else ""
    return f"{name} ({kind})" if kind else str(name)


def support_rows(model: Any, node_id: str) -> list:
    """``[("Restraints", …), ("Constraint", …)]`` for *node_id*, blanks omitted.

    What the Inspector appends to a node's own fields.  Only rows with something
    to say are returned, so an unrestrained node gains no clutter.
    """
    rows = []
    restraint = restraint_label(model, node_id)
    if restraint:
        rows.append(("Restraints", restraint))
    constraint = constraint_label(model, node_id)
    if constraint:
        rows.append(("Constraint", constraint))
    return rows


def shell_only_base_node_ids(md: Any) -> set[str]:
    """The model's shell-only base node ids — read-only.

    A shell-only base node is a bottom node (``min z``) referenced by an area
    element but no frame element.  These are the nodes whose pinned
    ``[1,1,1,0,0,0]`` restraint is a rotational mechanism for shells (which
    carry no drilling stiffness), and so the set that both
    :func:`fix_shell_only_base_restraints` and the build-time
    ``fix_shell_only_base`` option upgrade to full fixity.

    Args:
        md: A ``SAPModelData`` or ``MeshModel`` — anything with ``nodes``,
            ``frame_elements`` and ``area_elements``.

    Returns:
        The node ids (a set), possibly empty.
    """
    nodes = getattr(md, "nodes", None) or {}
    if not nodes:
        return set()
    zs = [nd.z for nd in nodes.values()]
    min_z = min(zs)
    # Scale the base-node tolerance with the model's vertical extent, so a
    # millimetre model and a metre model agree on what "at the base" means (a
    # hardcoded 0.01 only suits metre models).
    z_tol = max((max(zs) - min_z) * 1e-3, 1e-9)
    base_ids = {nid for nid, nd in nodes.items() if abs(nd.z - min_z) <= z_tol}
    frame_conn: set = set()
    for element in (getattr(md, "frame_elements", None) or {}).values():
        if getattr(element, "node_i", None) in base_ids:
            frame_conn.add(element.node_i)
        if getattr(element, "node_j", None) in base_ids:
            frame_conn.add(element.node_j)
    area_nodes: set = set()
    for area in (getattr(md, "area_elements", None) or {}).values():
        area_nodes.update(area.node_ids)
    return {nid for nid in (base_ids - frame_conn) if nid in area_nodes}


def fix_shell_only_base_restraints(md: Any) -> tuple[int, list[str]]:
    """Apply full fixity to the model's shell-only base nodes, in place.

    A shell-only base node is a bottom node referenced by an area element but no
    frame element.  SAP2000 exports every base node as pinned ``[1,1,1,0,0,0]``,
    but a shell (e.g. ``ShellMITC4``) has no drilling stiffness, so a shell-only
    pinned base is a rotational mechanism that makes the stiffness matrix
    singular — the ``matrix singular U(i,i)=0`` modal failure.  This upgrades
    exactly those nodes (see :func:`shell_only_base_node_ids`) to
    ``[1,1,1,1,1,1]``, the set
    :func:`~fea_toolkit.model.checks.check_model_connectivity` reports under
    ``shell_only_base_nodes``.

    Args:
        md: A ``SAPModelData`` (or anything with ``nodes``, ``frame_elements``,
            ``area_elements`` and ``restraints``).

    Returns:
        ``(number of nodes fixed, their node ids)``.
    """
    from .sap_data import Restraint

    restraints = getattr(md, "restraints", None)
    if restraints is None:
        restraints = {}
        md.restraints = restraints
    fixed: list[str] = []
    for nid in sorted(shell_only_base_node_ids(md)):
        existing = restraints.get(nid)
        if existing is None:
            # No restraint at all — translations stay free, rotations fixed.
            dofs = [0, 0, 0, 1, 1, 1]
        else:
            # Keep the node's existing translational flags; fix only rotations.
            dofs = list(existing.dofs)
            dofs[3] = dofs[4] = dofs[5] = 1
        restraints[nid] = Restraint(dofs=dofs)
        fixed.append(nid)
    return len(fixed), fixed
