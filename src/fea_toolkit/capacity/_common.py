"""Shared plumbing for code-specific capacity modules.

Each statutory building code gets one module in this package
(``gb50010.py``, ``asce41.py``, ...).  Functions inside a code module are
named by structural action (``moment_capacity``, ``shear_capacity``, ...) —
the module name scopes them to the code, so multiple codes can coexist
without name clashes::

    from fea_toolkit.capacity.gb50010 import moment_capacity
    from fea_toolkit.capacity.asce41 import moment_capacity

This module holds the shared result dataclass, the demand/capacity (DCR)
helper, safe number coercion and unit-label helpers reused by every code
module.

Unit convention
---------------
Material strengths are authored in SI (Pa) and converted to the model's unit
system with :func:`fea_toolkit.utils.stress_scale_factor`; section dimensions
and demands are in model units.  Capacities are therefore returned in the
model's force (or force × length) units.
"""

from dataclasses import dataclass, field
from typing import Any, Optional

from ..utils import (
    force_unit_label,  # re-exported canonical label (normalises "KN"→"kN")
    length_unit_label,
    stress_scale_factor,
)


@dataclass
class CapacityResult:
    """Result of a single code-specified capacity check.

    Attributes:
        value: Capacity in model force or force × length units.
        units: Short label for ``value`` (e.g. ``"kN"``, ``"kN·m"``).
        governing_clause: Code clause that governed (e.g. ``"GB 50010 §6.2.10"``).
        demand: Optional demand in the same units as ``value``.
        dcr: Demand/capacity ratio (``demand / value``) when *demand* is set.
        extra: Additional derived quantities (``h0``, ``x``, ``a_s``, ...).
    """

    value: float
    units: str
    governing_clause: str = ""
    demand: Optional[float] = None
    dcr: Optional[float] = None
    extra: dict[str, Any] = field(default_factory=dict)


def capacity_dcr(demand: float, capacity: float) -> float:
    """Return the demand/capacity (utilisation) ratio ``demand / capacity``.

    Returns ``float("inf")`` when a positive *demand* acts on a non-positive
    *capacity*, and ``0.0`` when both are non-positive (no meaningful ratio).

    Args:
        demand: Demand force / moment (model units).
        capacity: Capacity force / moment (model units).

    Returns:
        The DCR ratio.
    """
    if capacity <= 0.0:
        return float("inf") if demand > 0.0 else 0.0
    return demand / capacity


def safe_float(value: Any) -> float:
    """Coerce a section attribute or S2K value to float (None/empty → 0.0)."""
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def force_length_unit_label(units: Optional[dict]) -> str:
    """Short label for the model's force × length unit (e.g. ``"kN·m"``).

    The force part goes through :func:`fea_toolkit.utils.force_unit_label`
    so SAP2000 short forms (``"KN"``) and full names (``"kilonewton"``) are
    normalised to the canonical display label (``"kN"``).
    """
    return f"{force_unit_label(units)}·{length_unit_label(units)}"


__all__ = [
    "CapacityResult",
    "capacity_dcr",
    "force_length_unit_label",
    "force_unit_label",
    "safe_float",
    "stress_scale_factor",
]
