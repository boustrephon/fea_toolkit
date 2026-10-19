"""Shell element-family selection (thin / thick / nonlinear).

The toolkit builds 3-D shell elements in two matched families so a triangle
(3-node) and a quad (4-node) element always share one formulation — never
mixing a thick quad with a thin triangle at the same interface:

- **Thin** (Kirchhoff / DKT): ``ShellDKGQ`` / ``ShellDKGT`` (linear) and
  ``ShellNLDKGQ`` / ``ShellNLDKGT`` (nonlinear).
- **Thick** (Mindlin / MITC): ``ASDShellQ4`` / ``ASDShellT3``.

``ShellMITC4`` is the legacy quad-only spelling (there is no ``ShellMITC3``);
it is mapped into the thick family with ``ASDShellT3`` as its triangle sibling.

This module is pure data — it imports no ``ops`` — so the Preprocessor can
read it without loading OpenSees.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..model.sap_data import ShellSection

#: Sentinel ``AreaElementProperties.element_type`` value meaning "no explicit
#: override — resolve the family from the section's ``shell_type`` (and the
#: nonlinear family for layered sections) at build time".
SHELL_FAMILY_AUTO = "auto"

#: Quad shell element → its 3-node sibling and formulation family.
SHELL_FAMILIES: dict[str, dict[str, Optional[str]]] = {
    "ShellDKGQ": {"quad": "ShellDKGQ", "tri": "ShellDKGT", "family": "thin"},
    "ShellNLDKGQ": {"quad": "ShellNLDKGQ", "tri": "ShellNLDKGT", "family": "thin"},
    "ASDShellQ4": {"quad": "ASDShellQ4", "tri": "ASDShellT3", "family": "thick"},
    # Legacy quad-only; no ``ShellMITC3`` exists, so triangles use the thick sibling.
    "ShellMITC4": {"quad": "ShellMITC4", "tri": "ASDShellT3", "family": "thick"},
}


def default_shell_element(sec: ShellSection) -> str:
    """Return the default linear quad shell element for a section.

    The family is chosen from the section's CSI ``shell_type``: any type
    containing "Thick" (``"Shell-Thick"``, ``"Plate-Thick"``) selects the thick
    family ``ASDShellQ4``; everything else (``"Shell-Thin"``, ``"Membrane"``,
    ``"Plate-Thin"``, unspecified) selects the thin family ``ShellDKGQ``.

    Args:
        sec: A :class:`~fea_toolkit.model.sap_data.ShellSection` (or any object
            exposing ``shell_type``).

    Returns:
        The quad element command name.
    """
    st = (getattr(sec, "shell_type", None) or "").lower()
    if "thick" in st:
        return "ASDShellQ4"
    return "ShellDKGQ"


def quad_to_tri(quad: str) -> Optional[str]:
    """Return the 3-node sibling of a quad shell element, or ``None``."""
    entry = SHELL_FAMILIES.get(quad)
    return entry["tri"] if entry is not None else None
