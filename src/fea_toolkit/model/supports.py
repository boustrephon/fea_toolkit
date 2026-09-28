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
