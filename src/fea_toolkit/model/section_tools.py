"""Section tools — St. Venant torsion and fibre meshing for arbitrary sections.

Placeholder home for the planned pure-geometry "section tools" toolkit (see
``docs/sd_sections.md`` §3).  The elastic helpers in
:mod:`fea_toolkit.model.section_properties` today return ``J`` as the *polar*
second moment ``I33 + I22`` for arbitrary polygons — an upper bound to the
St. Venant torsional constant (exact only for a circle).  This module will grow
the correct computations:

* ``st_venant_j`` — the St. Venant torsional constant of an arbitrary polygon
  (thin-wall ``Σ b·t³/3`` / Bredt for thin-walled shapes, a Prandtl
  stress-function / finite-difference / boundary-element solve for solid
  sections), generalising the rectangular-bar series in
  :func:`~fea_toolkit.model.section_properties.rect_props`.
* section moduli ``Z33`` / ``Z22`` from extreme fibres (once the SD local-axis
  orientation is pinned down).
* polygon fibre meshing for :class:`~fea_toolkit.model.sap_data.SDSection`
  (``to_fiber_patches``).

Nothing here is wired into the build path yet — the stubs raise
``NotImplementedError`` so a caller cannot silently get a wrong result.
"""

from __future__ import annotations

from typing import Optional

__all__ = ["st_venant_j"]


def st_venant_j(
    vertices: list[tuple[float, float]],
    *,
    thickness: Optional[float] = None,
) -> float:
    """St. Venant torsional constant of an arbitrary solid or thin-walled polygon.

    **Placeholder — not implemented.**  :func:`polygon_props` currently returns
    the polar second moment ``I33 + I22`` as an upper-bound approximation; this
    function will replace that with the true St. Venant constant.

    Planned dispatch (``docs/sd_sections.md`` §3):

    * ``thickness`` given (thin-walled) → ``Σ b·t³/3`` for open sections or
      Bredt ``4·Am² / ∮ ds/t`` for closed ones;
    * ``thickness`` omitted (solid) → a Prandtl stress-function /
      finite-difference / boundary-element solve, generalising
      :func:`rect_props`'s rectangular-bar series.

    Args:
        vertices: The closed ``(y, z)`` corner loop in the section local 2-3
            plane (the closing edge, last → first, is implied).
        thickness: Optional uniform wall thickness; when given the thin-wall
            formulas are used instead of the solid-section solve.

    Returns:
        The St. Venant torsional constant ``J``.

    Raises:
        NotImplementedError: Always, until the computation is implemented.
    """
    raise NotImplementedError(
        "st_venant_j is a placeholder — see docs/sd_sections.md §3 (section "
        "tools).  polygon_props() still returns the polar second moment "
        "I33 + I22 as an upper-bound approximation."
    )
