#!/usr/bin/env python
"""IK-2005 generic-frame strength-coefficient check (γ = Vy/W vs Vmax/W).

The IK-2005 collapse intensity ``(Sa,c/g)/γ`` divides the collapse spectral
acceleration by the strength coefficient ``γ = Vy/W``.  ``generic_frame_run``
hard-codes ``γ = 1.0`` (the Appendix-B *first-yield* design value), so the
reported median is currently just ``Sa,c/g``.  This script is the record-free
check on that divisor: it reads the analytic first-yield ``Vy/W`` straight off
the Medina tables and compares it with the *peak* pushover base-shear
coefficient ``Vmax/W`` (the actual OpenSees pushover, ``αs = 0.03`` hardening
included), both with and without P-Δ.

If the paper normalised by *peak* strength instead of *first-yield* strength,
the reported ``(Sa,c/g)/γ`` would shift by the ratio ``Vmax/W ÷ Vy/W``.

Usage::

    python scripts/ik2005_strength_check.py [--code 0909] [--all]
"""

from __future__ import annotations

import argparse
from typing import Any, Optional

import numpy as np

from fea_toolkit._unit_scaling import g_from_units
from fea_toolkit.benchmarks import generic_frame as gf
from fea_toolkit.benchmarks.generic_frame_data import FRAME_CODES, FRAME_DATA, STOREY_HEIGHT_IN

#: Roof-drift ratios (in %) at which to sample the V/W curve.
_DRIFT_SAMPLES_PCT = (0.25, 0.5, 1.0, 2.0, 3.0)


def analytic_first_yield_gamma(code: str) -> float:
    """First-yield strength coefficient ``Vy/W`` from the Medina tables.

    First-yield base shear (US kip/in units): the first-storey beam sway yields
    at ``V = 2·Myb1/h`` (portal, ``M_beam = V·h/2``) and the column base at
    ``V = 4·Myc/h`` (``M_base = V·h/4``); the lower one is ``Vy``.  The design
    (IK-2005 Appendix B) sets ``Vy/W = 1.0``.

    Args:
        code: Four-digit frame code.

    Returns:
        ``Vy/W`` (dimensionless) — 1.0 by design.
    """
    d = FRAME_DATA[code]
    w = float(d["norm"]["W"])
    h = STOREY_HEIGHT_IN
    myb1 = d["norm"]["Myb2"] * d["stories"][1]["Mybi_Myb2"]
    myc = d["stories"][0]["Myc"]
    return min(2.0 * myb1 / h, 4.0 * myc / h) / w


def _pushover(code: str, *, pdelta: bool, pdelta_gravity: bool) -> dict[str, Any]:
    """Run one triangular pushover to ~3 % roof drift and return its pieces.

    Args:
        code: Four-digit frame code.
        pdelta: When True, emit the ``PDelta`` geometric transformation.
        pdelta_gravity: When True, hold the ``1.4 × seismic mass`` gravity load
            (pair with ``pdelta=True`` for the collapse-run configuration).

    Returns:
        ``{"result", "weight", "roof_h"}`` — the raw pushover result dict, the
        seismic weight ``W = Σ m·g`` (N), and the roof height (m).
    """
    import openseespy.opensees as ops

    roof_h = float(FRAME_DATA[code]["N"]) * gf.storey_height_m()
    ops.wipe()
    try:
        builder = gf.build_builder(code, pdelta=pdelta, pdelta_gravity=pdelta_gravity)
        masses = builder.compute_seismic_masses()
        weight = float(sum(masses.values())) * g_from_units(builder.units)
        res = builder.run_pushover_analysis(
            gravity_patterns={"GRAV": 1.0} if pdelta_gravity else {},
            lateral_load_type="triangular",
            lateral_direction="X",
            max_disp=0.03 * roof_h,
            num_steps=200,
            print_progress=False,
        )
    finally:
        ops.wipe()
    return {"result": res, "weight": weight, "roof_h": roof_h}


def _converged_curve(res: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """``(disp, shear)`` arrays restricted to converged, non-initial steps."""
    disp = np.asarray(res["control_disp"], dtype=float)
    shear = np.asarray(res["base_shear"], dtype=float)
    status = np.asarray(res["status"], dtype=int)
    ok = status == 0
    ok[0] = False  # step 0 is the zero-lateral (gravity) state
    return disp[ok], shear[ok]


def peak_gamma(res: dict[str, Any], weight: float) -> float:
    """Peak pushover base-shear coefficient ``Vmax/W``."""
    _disp, shear = _converged_curve(res)
    return float(shear.max()) / weight


def gamma_at_drift(res: dict[str, Any], weight: float, roof_h: float, drift_pct: float) -> float:
    """Base-shear coefficient ``V/W`` at a roof-drift ratio (%)."""
    disp, shear = _converged_curve(res)
    if disp.size < 2:
        return float("nan")
    target = (drift_pct / 100.0) * roof_h
    return float(np.interp(target, disp, shear)) / weight


def _report(code: str) -> None:
    d = FRAME_DATA[code]
    n, t1 = d["N"], d["T1"]
    gamma_y = analytic_first_yield_gamma(code)

    design = _pushover(code, pdelta=False, pdelta_gravity=False)
    collapse = _pushover(code, pdelta=True, pdelta_gravity=True)

    g_peak = peak_gamma(design["result"], design["weight"])
    g_peak_pd = peak_gamma(collapse["result"], collapse["weight"])

    print(f"\n{code}  N={n}  T1={t1:.1f} s")
    print(f"  Vy/W   (first-yield, analytic table)  = {gamma_y:.4f}")
    print(f"  Vmax/W (peak pushover, no P-Δ)        = {g_peak:.4f}")
    print(f"  Vmax/W (peak pushover, P-Δ + 1.4·g)   = {g_peak_pd:.4f}")
    print(f"  peak ÷ first-yield (no P-Δ)            = {g_peak / gamma_y:.4f}")
    samples = [
        f"{p:.2f}%→{gamma_at_drift(design['result'], design['weight'], design['roof_h'], p):.4f}"
        for p in _DRIFT_SAMPLES_PCT
    ]
    print("  V/W @ roof drift (no P-Δ)             : " + "  ".join(samples))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code", default="0909", help="four-digit frame code (default 0909)")
    parser.add_argument("--all", action="store_true", help="report all 12 frames")
    args = parser.parse_args(argv)

    codes = list(FRAME_CODES) if args.all else [args.code]
    for code in codes:
        _report(code)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
