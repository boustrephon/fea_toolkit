"""Frame-section elastic properties derived from dimensions — pure geometry.

ETABS parametric ``FRAMESECTION`` records carry only the shape *dimensions*
(``D``/``B``/``TF``/``TW``/``T``/``DIS``), not the derived section properties
that SAP2000's ``FRAME SECTION PROPERTIES`` tables give explicitly.  This module
computes those derived properties — area, second moments of area, the
St. Venant torsional constant and the elastic section moduli — from the
dimensions, so an ETABS model can be built without a manufacturer catalogue.

Conventions (matching CSI / SAP2000):

* ``depth`` (``D``) is the strong-axis dimension along the local 2-axis and
  ``bf`` (``B``) the weak-axis dimension along the local 3-axis.
* ``I33`` is the moment of inertia for bending about the local 3-axis (uses
  ``depth``); ``I22`` is for bending about the local 2-axis (uses ``bf``).
* ``J`` is the St. Venant torsional constant: the thin-wall summation
  ``Σ b·t³/3`` over the plates of an open section, Bredt's formula
  ``4·Am² / ∮ ds/t`` for the closed box, and the rectangular-bar series for
  the solid rectangle.
* ``Z33`` / ``Z22`` are the *elastic* section moduli (``2·I33/D`` /
  ``2·I22/B``), matching the shipped ``rect_props``.  For an asymmetric shape
  whose extreme fibre is not at ``D/2`` / ``B/2`` from the centroid (channel
  weak axis, angle, double angle, tee) the affected modulus is returned as
  ``0.0``.

Centroid offsets (``cg_offset_2`` / ``cg_offset_3``) are **not** populated here:
their sign depends on the section reference-point convention, which must be
pinned down against a real ETABS export before being emitted (see
``docs/e2k_import_plan.md``, "M2 hardening").  The stiffness quantities above
are centroidal and complete regardless.

The formulas are ported from ``E2K_utilities.section_utilities``
(``I_props``/``CH_props``/``RH_props``/``CHS_props``/``A_props``/``T_props``/
``R_props``/``C_props``), with ``J`` and the elastic moduli added where the
reference omits them.
"""

from __future__ import annotations

import math

__all__ = [
    "angle_props",
    "box_props",
    "channel_props",
    "circle_props",
    "double_angle_props",
    "i_props",
    "pipe_props",
    "rect_props",
    "tee_props",
]


def _zeros() -> dict[str, float]:
    return {"A": 0.0, "I33": 0.0, "I22": 0.0, "J": 0.0, "Z33": 0.0, "Z22": 0.0}


def rect_props(depth: float, width: float) -> dict[str, float]:
    """Elastic properties of a solid rectangle from ``D × B``.

    ``I33 = B·D³/12`` (strong axis), ``I22 = D·B³/12`` (weak axis) and the
    St. Venant torsional constant from the closed-form rectangular-bar series
    (first term plus the ``0.21`` correction).
    """
    d, b = depth, width
    if d <= 0.0 or b <= 0.0:
        return _zeros()
    long_side, short_side = (d, b) if d >= b else (b, d)
    ratio = short_side / long_side
    j = long_side * short_side**3 * (1.0 / 3.0 - 0.21 * ratio * (1.0 - ratio**4 / 12.0))
    return {
        "A": d * b,
        "I33": b * d**3 / 12.0,
        "I22": d * b**3 / 12.0,
        "J": j,
        "Z33": b * d**2 / 6.0,
        "Z22": d * b**2 / 6.0,
    }


def circle_props(diameter: float) -> dict[str, float]:
    """Elastic properties of a solid circular section / rod from ``D``."""
    d = diameter
    if d <= 0.0:
        return _zeros()
    i = math.pi * d**4 / 64.0
    return {
        "A": math.pi * d**2 / 4.0,
        "I33": i,
        "I22": i,
        "J": math.pi * d**4 / 32.0,
        "Z33": math.pi * d**3 / 32.0,
        "Z22": math.pi * d**3 / 32.0,
    }


def pipe_props(od: float, t: float) -> dict[str, float]:
    """Elastic properties of a circular hollow section (pipe) from ``od``/``t``."""
    if od <= 0.0 or t <= 0.0 or 2.0 * t >= od:
        return _zeros()
    di = od - 2.0 * t
    i = math.pi * (od**4 - di**4) / 64.0
    return {
        "A": math.pi * (od**2 - di**2) / 4.0,
        "I33": i,
        "I22": i,
        "J": math.pi * (od**4 - di**4) / 32.0,
        "Z33": 2.0 * i / od,
        "Z22": 2.0 * i / od,
    }


def i_props(depth: float, bf: float, tf: float, tw: float) -> dict[str, float]:
    """Elastic properties of an I / wide-flange section from ``D B TF TW``."""
    d, b = depth, bf
    if d <= 0.0 or b <= 0.0 or tf <= 0.0 or tw <= 0.0:
        return _zeros()
    web = d - 2.0 * tf
    if web <= 0.0:
        return _zeros()
    i33 = b * d**3 / 12.0 - (b - tw) * web**3 / 12.0
    i22 = tf * b**3 / 6.0 + web * tw**3 / 12.0
    return {
        "A": b * d - (b - tw) * web,
        "I33": i33,
        "I22": i22,
        "J": (2.0 * b * tf**3 + web * tw**3) / 3.0,
        "Z33": 2.0 * i33 / d,
        "Z22": 2.0 * i22 / b,
    }


def channel_props(depth: float, bf: float, tf: float, tw: float) -> dict[str, float]:
    """Elastic properties of a channel from ``D B TF TW``.

    ``I22`` is centroidal (the weak-axis centroid offset is removed); the weak
    elastic modulus ``Z22`` is returned as ``0.0`` because the extreme fibre is
    not at ``B/2`` from the centroid.
    """
    d, b = depth, bf
    if d <= 0.0 or b <= 0.0 or tf <= 0.0 or tw <= 0.0:
        return _zeros()
    web = d - 2.0 * tf
    if web <= 0.0:
        return _zeros()
    a = b * d - (b - tw) * web
    cy = (tf * b**2 + 0.5 * web * tw**2) / a
    i33 = b * d**3 / 12.0 - (b - tw) * web**3 / 12.0
    i22 = (2.0 * tf * b**3 + web * tw**3) / 3.0 - a * cy**2
    return {
        "A": a,
        "I33": i33,
        "I22": i22,
        "J": (2.0 * b * tf**3 + web * tw**3) / 3.0,
        "Z33": 2.0 * i33 / d,
        "Z22": 0.0,
    }


def box_props(depth: float, bf: float, tf: float, tw: float) -> dict[str, float]:
    """Elastic properties of a rectangular hollow section (box/tube).

    ``J`` uses Bredt's thin-wall formula ``4·Am² / ∮ ds/t`` with the median-line
    dimensions ``(B−tw)`` and ``(D−tf)``.
    """
    d, b = depth, bf
    if d <= 0.0 or b <= 0.0 or tf <= 0.0 or tw <= 0.0:
        return _zeros()
    inner_d = d - 2.0 * tf
    inner_b = b - 2.0 * tw
    if inner_d <= 0.0 or inner_b <= 0.0:
        return _zeros()
    i33 = b * d**3 / 12.0 - inner_b * inner_d**3 / 12.0
    i22 = d * b**3 / 12.0 - inner_d * inner_b**3 / 12.0
    # Bredt: J = 4·Am² / ∮ ds/t, median line u=B−tw (×tf), v=D−tf (×tw).
    u = b - tw
    v = d - tf
    j = 2.0 * u**2 * v**2 * tf * tw / (u * tw + v * tf)
    return {
        "A": b * d - inner_b * inner_d,
        "I33": i33,
        "I22": i22,
        "J": j,
        "Z33": 2.0 * i33 / d,
        "Z22": 2.0 * i22 / b,
    }


def angle_props(depth: float, bf: float, tf: float, tw: float) -> dict[str, float]:
    """Elastic properties of a single angle from ``D B TF TW``.

    ``I33`` / ``I22`` are about the geometric (local 2/3) axes, centroidal.  The
    elastic moduli are returned as ``0.0`` (principal-axis moduli would need the
    product of inertia and axis rotation).
    """
    d, b = depth, bf
    if d <= 0.0 or b <= 0.0 or tf <= 0.0 or tw <= 0.0:
        return _zeros()
    a = b * d - (d - tf) * (b - tw)
    if a <= 0.0:
        return _zeros()
    cz = 0.5 * (d**2 * tw + tf**2 * (b - tw)) / a
    cy = 0.5 * (b**2 * tf + tw**2 * (d - tf)) / a
    i33 = (tw * d**3 + b * tf**3 - tw * tf**3) / 3.0 - a * cz**2
    i22 = (tf * b**3 + d * tw**3 - tf * tw**3) / 3.0 - a * cy**2
    return {
        "A": a,
        "I33": i33,
        "I22": i22,
        "J": (b * tf**3 + d * tw**3) / 3.0,
        "Z33": 0.0,
        "Z22": 0.0,
    }


def double_angle_props(
    depth: float, bf: float, tf: float, tw: float, dis: float = 0.0
) -> dict[str, float]:
    """Elastic properties of a back-to-back double angle from ``D B TF TW DIS``.

    ``B`` is the overall width (two legs plus the gap ``DIS``); ``I22`` uses the
    parallel-axis theorem to translate each leg to the symmetric centroid.
    """
    d, b = depth, bf
    if d <= 0.0 or b <= 0.0 or tf <= 0.0 or tw <= 0.0:
        return _zeros()
    b0 = 0.5 * (b - dis)
    if b0 <= 0.0:
        return _zeros()
    a_leg = b0 * d - (d - tf) * (b0 - tw)
    if a_leg <= 0.0:
        return _zeros()
    cz = 0.5 * (d**2 * tw + tf**2 * (b0 - tw)) / a_leg
    cy = 0.5 * (b0**2 * tf + tw**2 * (d - tf)) / a_leg
    i33 = 2.0 * ((tw * d**3 + b0 * tf**3 - tw * tf**3) / 3.0 - a_leg * cz**2)
    i22_leg = (tf * b0**3 + d * tw**3 - tf * tw**3) / 3.0 - a_leg * cy**2
    i22 = 2.0 * (i22_leg + a_leg * (0.5 * dis + cy) ** 2)
    return {
        "A": 2.0 * a_leg,
        "I33": i33,
        "I22": i22,
        "J": 2.0 * (b0 * tf**3 + d * tw**3) / 3.0,
        "Z33": 0.0,
        "Z22": 0.0,
    }


def tee_props(depth: float, bf: float, tf: float, tw: float) -> dict[str, float]:
    """Elastic properties of a tee (flange + stem) from ``D B TF TW``."""
    d, b = depth, bf
    if d <= 0.0 or b <= 0.0 or tf <= 0.0 or tw <= 0.0:
        return _zeros()
    a = (b - tw) * tf + d * tw
    cz = 0.5 * ((b - tw) * tf**2 + d**2 * tw) / a
    i33 = ((b - tw) * tf**3 + d**3 * tw) / 3.0 - a * cz**2
    i22 = (b**3 * tf + (d - tf) * tw**3) / 12.0
    return {
        "A": a,
        "I33": i33,
        "I22": i22,
        "J": (b * tf**3 + (d - tf) * tw**3) / 3.0,
        "Z33": 0.0,
        "Z22": 0.0,
    }
