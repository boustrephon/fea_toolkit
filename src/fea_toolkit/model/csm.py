"""
Capacity Spectrum Method (CSM) — bilinearization and performance‑point detection.

Provides three bilinearization methods for pushover capacity curves:

1. **Stiffness-change detection** — yield point where secant stiffness
   drops below a threshold of the initial elastic stiffness.
2. **Equal-energy (ATC‑40 / EC8)** — yield point preserving the area
   under the capacity curve.
3. **Composite** — stiffness‑change primary, equal‑energy fallback,
   with a 10 % of peak displacement clamp (Vamvatsikos 10 % rule).

The `compute_performance_point()` function implements the full ATC‑40
Capacity Spectrum Method (CSM) with secant iteration: ADRS conversion,
bilinearization, equivalent viscous damping, damping reduction factor,
and convergence detection via relative tolerance and stall detection.

**Unit convention (important)**

The pushover results carry a ``units`` dict (e.g. ``{"F": "KN",
"L": "m", "T": "C"}``).  All spectral quantities returned by this module
are expressed in those model units:

- ``S_a``, ``S_ay``, ``S_ap`` — spectral acceleration in **m/s²**
  (force/mass acceleration, dimensionally consistent regardless of the
  force/length unit chosen).
- ``S_d``, ``S_dy``, ``S_dp`` — spectral displacement in model length
  units (m for a kN-m model).
- ``V_base`` — base shear in model force units (kN for a kN-m model).
- ``D_roof`` — roof displacement in model length units (m).

The ADRS conversion computes ``S_a = V / M_eff`` (Newton's second law
with no explicit gravitational constant), which is exact in any
consistent unit system: kN / t = m/s², N / kg = m/s².  It does **not**
divide by ``g`` — dividing by ``g`` would produce g-units and silently
corrupt every downstream quantity (T_eq, ductility, intersection).
"""

from __future__ import annotations

import math
import warnings
from typing import Any, Optional

import numpy as np

# ═══════════════════════════════════════════════════════════════════════════
# Bilinearization — Stiffness-change detection
# ═══════════════════════════════════════════════════════════════════════════


def bilinearize_stiffness_change(
    S_d_arr: np.ndarray,
    S_a_arr: np.ndarray,
    config: Optional[dict[str, Any]] = None,
) -> tuple[float, float, str]:
    """Bilinearise by detecting where secant stiffness drops significantly.

    Yield is detected where the secant stiffness falls below a fraction
    of the initial elastic stiffness (Criterion A), or where a single
    step shows a large relative drop (Criterion B).

    Args:
        S_d_arr: Spectral displacements array (sorted ascending, >=0).
        S_a_arr: Spectral accelerations (same length as S_d_arr).
        config: Optional dict with keys:

            - ``threshold`` (float, default 0.50): Fraction of K_init
              below which yield is declared (Criterion A).
            - ``min_relative_drop`` (float, default -0.30): Single-step
              secant-stiffness relative drop threshold (Criterion B).
            - ``peak_idx`` (int, optional): Index of the peak / target
              displacement. Auto-detected from argmax if not provided.

    Returns:
        Tuple (S_dy, S_ay, method) where method is ``'stiffness_change'``.

    Edge cases:
        - No clear yield detected → yield at peak (mu = 1).
        - Empty arrays → returns (0.0, 0.0, 'stiffness_change').
    """
    config = config or {}
    S_d_arr = np.asarray(S_d_arr, dtype=float)
    S_a_arr = np.asarray(S_a_arr, dtype=float)

    # Let argmax raise ValueError on empty arrays — the caller's
    # responsibility to provide valid capacity curve data.
    if len(S_d_arr) == 0 or len(S_a_arr) == 0:
        return 0.0, 0.0, "stiffness_change"

    peak_idx = config.get("peak_idx")
    if peak_idx is None:
        peak_idx = int(np.argmax(S_a_arr))
    peak_idx = min(peak_idx, len(S_d_arr) - 1)

    S_d_peak = float(S_d_arr[peak_idx])
    S_a_peak = float(S_a_arr[peak_idx])

    # initial elastic stiffness: first point with positive S_d and S_a
    K_init = 0.0
    for i in range(len(S_d_arr)):
        if S_d_arr[i] > 1e-12 and S_a_arr[i] > 1e-12:
            K_init = S_a_arr[i] / S_d_arr[i]
            break
    if K_init < 1e-12:
        K_init = S_a_peak / max(S_d_peak, 1e-12)

    threshold = config.get("threshold", 0.50)
    min_drop = config.get("min_relative_drop", -0.30)

    S_dy, S_ay = S_d_peak, S_a_peak

    for i in range(1, peak_idx):
        if S_d_arr[i] < 1e-12:
            continue
        K_sec = S_a_arr[i] / S_d_arr[i]
        # Criterion A: secant stiffness below threshold * K_init
        if K_sec < threshold * K_init:
            S_dy, S_ay = S_d_arr[i], S_a_arr[i]
            break
        # Criterion B: large relative drop in secant stiffness
        if i > 1:
            K_prev = S_a_arr[i - 1] / max(S_d_arr[i - 1], 1e-12)
            relative_drop = (K_sec - K_prev) / max(K_prev, 1e-12)
            if relative_drop < min_drop:
                S_dy, S_ay = S_d_arr[i], S_a_arr[i]
                break

    return float(S_dy), float(S_ay), "stiffness_change"


# ═══════════════════════════════════════════════════════════════════════════
# Bilinearization — Equal-energy (ATC‑40 / EC8)
# ═══════════════════════════════════════════════════════════════════════════


def bilinearize_equal_energy(
    S_d_arr: np.ndarray,
    S_a_arr: np.ndarray,
    config: Optional[dict[str, Any]] = None,
) -> tuple[float, float, str]:
    """Bilinearise by preserving the area under the capacity curve.

    Iterative Newton-style relaxation to find the yield point (S_dy, S_ay)
    such that the area under the bilinear curve equals the area under the
    capacity curve up to the peak displacement.

    Args:
        S_d_arr: Spectral displacements array (sorted ascending, >=0).
        S_a_arr: Spectral accelerations (same length as S_d_arr).
        config: Optional dict with keys:

            - ``initial_guess`` (float, default 0.30): Fraction of
              S_d_peak to use as the initial yield-displacement guess.
            - ``tolerance`` (float, default 1e-3): Relative area-error
              convergence tolerance.
            - ``max_iter`` (int, default 100): Maximum iterations.
            - ``peak_idx`` (int, optional): Auto-detected from argmax.

    Returns:
        Tuple ``(S_dy, S_ay, method)`` where *method* is one of:

        - ``'equal_energy'`` — converged to tolerance.
        - ``'equal_energy_degenerate'`` — yield acceleration collapsed
          to zero (degenerate capacity curve).
        - ``'equal_energy_not_converged'`` — ``max_iter`` exhausted
          without reaching tolerance (a warning is emitted).

    Edge cases:
        - No clear yield (S_dy >= 90 % of peak) → yield at peak (mu = 1).
        - Empty arrays → returns (0.0, 0.0, 'equal_energy').
    """
    config = config or {}
    S_d_arr = np.asarray(S_d_arr, dtype=float)
    S_a_arr = np.asarray(S_a_arr, dtype=float)

    if len(S_d_arr) == 0 or len(S_a_arr) == 0:
        return 0.0, 0.0, "equal_energy"

    peak_idx = config.get("peak_idx")
    if peak_idx is None:
        peak_idx = int(np.argmax(S_a_arr))
    peak_idx = min(peak_idx, len(S_d_arr) - 1)

    S_d_peak = float(S_d_arr[peak_idx])
    S_a_peak = float(S_a_arr[peak_idx])

    if peak_idx < 2:
        # Not enough data — yield at peak
        S_ay = S_a_peak
        return S_d_peak, S_ay, "equal_energy"

    # Initial elastic stiffness (first point with positive S_d and S_a)
    K_init = 0.0
    for i in range(peak_idx):
        if S_d_arr[i] > 1e-12 and S_a_arr[i] > 1e-12:
            K_init = S_a_arr[i] / S_d_arr[i]
            break
    if K_init < 1e-12:
        K_init = S_a_peak / max(S_d_peak, 1e-12)

    # Cumulative integral (trapezoidal rule)
    integral = np.zeros_like(S_d_arr)
    for i in range(1, peak_idx + 1):
        integral[i] = integral[i - 1] + 0.5 * (S_d_arr[i] - S_d_arr[i - 1]) * (
            S_a_arr[i] + S_a_arr[i - 1]
        )
    A_cap = integral[peak_idx]

    # Local variable for S_a at yield (used in loop and after)

    # Initial guess
    initial_guess = config.get("initial_guess", 0.30)
    tol = config.get("tolerance", 1e-3)
    max_iter = config.get("max_iter", 100)

    S_dy = S_d_peak * initial_guess
    S_ay = 0.0  # ensure bound before loop
    if S_dy <= S_d_arr[0]:
        S_dy = float(S_d_arr[1] if len(S_d_arr) > 1 else S_d_arr[0])

    converged = False
    err = 0.0

    for _iteration in range(max_iter):
        if S_dy <= S_d_arr[0]:
            S_dy = float(S_d_arr[0])
        if S_dy >= S_d_peak:
            S_dy = S_d_peak
            S_ay = S_a_peak
            # Reaching the peak boundary is not inherently degenerate;
            # the 90 % sanity check after the loop handles this case
            # and sets converged = True.  Return the same label as
            # the old code so callers see "equal_energy".
            return S_dy, S_ay, "equal_energy"

        # S_a at S_dy (interpolate capacity curve)
        S_ay_elastic = K_init * S_dy
        S_a_at_dy = float(np.interp(S_dy, S_d_arr, S_a_arr))
        S_ay = min(S_ay_elastic, S_a_at_dy)

        if S_ay <= 0:
            # Degenerate curve — yield acceleration collapsed to zero.
            S_dy = S_d_peak
            S_ay = S_a_peak
            return S_dy, S_ay, "equal_energy_degenerate"

        # Area under bilinear curve up to peak
        A_bilin = 0.5 * S_ay * S_dy + S_ay * (S_d_peak - S_dy)
        A_bilin += 0.5 * (S_a_peak - S_ay) * (S_d_peak - S_dy)

        err = (A_bilin - A_cap) / max(A_cap, 1e-12)

        if abs(err) < tol:
            converged = True
            break

        # Update: increase S_dy if area too small, decrease if too large
        S_dy *= 1.0 - 0.5 * err
        S_dy = max(float(S_d_arr[0] if len(S_d_arr) > 0 else 0.0), min(S_dy, S_d_peak))

    # Sanity: if yield is >90% of peak and the iteration did NOT
    # converge below tolerance, the curve is essentially linear —
    # reset to peak (mu = 1).  If it converged, accept the result
    # even if it happens to be near the peak (e.g. hardening curves
    # with yield at the end point).
    if S_dy >= 0.90 * S_d_peak and not converged:
        S_dy = S_d_peak
        S_ay = S_a_peak
        converged = True

    if not converged:
        warnings.warn(
            f"bilinearize_equal_energy did not converge after "
            f"{max_iter} iterations (err={err:.2e}); using "
            f"last-iteration values.",
            RuntimeWarning,
        )
        return S_dy, S_ay, "equal_energy_not_converged"

    return S_dy, S_ay, "equal_energy"


def bilinearize_composite(
    S_d_arr: np.ndarray,
    S_a_arr: np.ndarray,
    config: Optional[dict[str, Any]] = None,
) -> tuple[float, float, str]:
    """Composite bilinearisation: stiffness-change primary, equal-energy fallback.

    Designed as a **sensible default** for automated workflows where
    the structural behaviour is not known in advance.

    The algorithm is:

    1. Extract or auto-detect the peak index.
    2. Run :func:`bilinearize_stiffness_change` (with the same config).
    3. If the stiffness-change result yields at ≥ 90 % of the peak
       displacement (no clear stiffness change detected), fall back to
       :func:`bilinearize_equal_energy` (with the same config).
    4. Apply a **sanity clamp**: if the yield displacement is below
       10 % of the peak displacement, raise it to exactly 10 % and
       interpolate the corresponding acceleration.  This prevents
       pathologically low yield points that would produce unrealistically
       high ductility demands.

    **When to use**: as the default method when the structure type is
    unknown or mixed.  Also suitable for "black-box" batch processing
    where individual curve shapes cannot be inspected.

    Design rationale:
        - Stiffness-change is preferred when it works because it is
          deterministic and non-iterative.
        - Equal-energy fallback handles curved backbones (RC, masonry)
          reliably.
        - The 10 % clamp (Vamvatsikos 10 % rule) captures the initial
          stiffness more accurately than area-balancing alone, reducing
          bias in the computed ductility.

    References:
        - ATC-40 (1996), *Seismic Evaluation and Retrofit of Concrete
          Buildings*, Applied Technology Council.
        - Vamvatsikos, D., De Luca, F., & Iervolino, I. (2013).
          "Near-optimal piecewise linear fits of static pushover
          capacity curves." *Earthquake Engineering & Structural
          Dynamics*, 42(4), 589–600.  doi:10.1002/eqe.2225
        - Faella, G., Giordano, A., & Mezzi, M. (2004). "Definition of
          Suitable Bilinear Pushover Curves in Nonlinear Static
          Analyses." *13th WCEE*, Paper 1626.

    Args:
        S_d_arr: Spectral displacements (m).  Should be monotonically
            increasing and non-negative.
        S_a_arr: Spectral accelerations (m/s²), corresponding to
            *S_d_arr*.  Should be non-negative.

    Returns:
        Tuple ``(S_dy, S_ay, method_name)`` where *method_name* is
        ``'composite_stiffness_change'`` or ``'composite_equal_energy'``
        indicating which sub-method produced the result (before the
        10 % clamp).

    Edge cases:
        - Empty arrays: delegates to stiffness-change, which
          returns ``(0.0, 0.0, 'stiffness_change')``, then the 90 %
          check fires (0.0 ≥ 0.9 * 0.0 is True), so equal-energy is
          called, returning ``(0.0, 0.0, 'equal_energy')``.
          Final method is ``'composite_equal_energy'``.
        - True elastic curve: stiffness-change returns peak →
          fallback to equal-energy → the 10 % clamp may or may not
          engage depending on the equal-energy result.
    """
    cfg = dict(config or {})
    S_d_arr = np.asarray(S_d_arr, dtype=float)
    S_a_arr = np.asarray(S_a_arr, dtype=float)

    if len(S_d_arr) == 0 or len(S_a_arr) == 0:
        return 0.0, 0.0, "composite_equal_energy"
    peak_idx = cfg.get("peak_idx")
    if peak_idx is None:
        peak_idx = int(np.argmax(S_a_arr))
    # Clamp a caller-supplied peak_idx to the final valid index so it can
    # never be used to index out of bounds (matching the boundary behaviour
    # of bilinearize_stiffness_change / bilinearize_equal_energy).
    peak_idx = max(0, min(int(peak_idx), len(S_d_arr) - 1))
    cfg["peak_idx"] = peak_idx

    # Try stiffness-change first
    S_dy, S_ay, _ = bilinearize_stiffness_change(S_d_arr, S_a_arr, cfg)

    S_d_peak = float(S_d_arr[peak_idx])
    float(S_a_arr[peak_idx])

    if S_dy >= 0.90 * S_d_peak:
        # No clear yield — fall back to equal-energy
        S_dy, S_ay, _ = bilinearize_equal_energy(S_d_arr, S_a_arr, cfg)
        method = "composite_equal_energy"
    else:
        method = "composite_stiffness_change"

    # Sanity clamp: yield must be at least 10% of peak displacement
    min_S_dy = 0.10 * S_d_peak if peak_idx > 1 else float(S_d_arr[0])
    if S_dy < min_S_dy:
        # Interpolate S_a at the minimum yield displacement
        if min_S_dy <= S_d_arr[0]:
            S_dy = min_S_dy
            S_ay = float(S_a_arr[0])
        else:
            S_ay = float(np.interp(min_S_dy, S_d_arr, S_a_arr))
            S_dy = min_S_dy

    return S_dy, S_ay, method


# ═══════════════════════════════════════════════════════════════════════════
# Equivalent viscous damping — ATC‑40
# ═══════════════════════════════════════════════════════════════════════════


def compute_equivalent_damping(
    mu: float,
    alpha: float = 0.0,
    beta_0: float = 0.05,
    kappa: float = 0.33,
) -> float:
    """Compute equivalent viscous damping per ATC‑40 Eqn 5‑19.

    .. math::

        \\beta_{\\text{eq}} = \\beta_0 + \\kappa \\cdot
        \\frac{2}{\\pi} \\cdot \\frac{(1 - \\alpha)(\\mu - 1)}
        {\\mu (1 + \\alpha \\mu - \\alpha)}

    where:
    - β₀ = inherent (elastic) damping ratio
    - κ = damping modification factor
    - α = post‑yield stiffness ratio
    - μ = ductility = Sd_max / S_dy

    Args:
        mu: Ductility demand (≥1).
        alpha: Post‑yield stiffness ratio (0..1, default 0.0).
        beta_0: Inherent elastic damping ratio (default 0.05).
        kappa: Damping modification factor (default 0.33, ATC‑40 Type C).

    Returns:
        Equivalent viscous damping ratio (beta_0 + hysteretic contribution).
    """
    if mu <= 1.0:
        return beta_0

    # Hysteretic damping per ATC-40 Eqn 5-19
    beta_h = (2.0 / math.pi) * (1.0 - alpha) * (mu - 1.0) / (mu * (1.0 + alpha * mu - alpha))
    beta_eff = beta_0 + kappa * beta_h

    return float(beta_eff)


def damping_reduction_factor(
    beta_eq: float,
    beta_0: float = 0.05,
    lo: float = 0.5,
    hi: float = 2.0,
) -> float:
    """Damping reduction factor B used to scale the elastic demand spectrum.

    Implements the ATC‑40 / GB 50011 compatible expression:

    .. math::

        B = \\sqrt{\\frac{1 + 10(\\beta_{\\text{eq}} - \\beta_0)}
                       {1 + 5(\\beta_{\\text{eq}} - \\beta_0)}}

    clamped to ``[lo, hi]`` (ATC‑40 allows B in [0.5, 1.0] plus a small
    extension for very high damping; the toolkit widens the upper bound
    to 2.0 so lightly-damped systems are not amplified unrealistically).

    The inelastic demand acceleration at an equivalent period is then
    ``Sa_demand = Sa_elastic / B``.

    Args:
        beta_eq: Equivalent viscous damping ratio (≥ beta_0).
        beta_0: Inherent elastic damping ratio (default 0.05).
        lo: Lower clamp (default 0.5).
        hi: Upper clamp (default 2.0).

    Returns:
        Damping reduction factor B (≥ 1 when beta_eq > beta_0).
    """
    if beta_eq <= beta_0:
        return 1.0
    B = math.sqrt((1.0 + 10.0 * (beta_eq - beta_0)) / (1.0 + 5.0 * (beta_eq - beta_0)))
    return float(max(lo, min(hi, B)))


# ═══════════════════════════════════════════════════════════════════════════
# Performance point calculation
# ═══════════════════════════════════════════════════════════════════════════


def compute_performance_point(
    pushover_results: dict[str, Any],
    modal_results: dict[str, Any],
    mode_shapes: dict[int, dict[int, tuple[float, float, float]]],
    spectrum_periods: list[float],
    spectrum_accels: list[float],
    direction: str = "X",
    damping_ratio: float = 0.05,
    max_iter: int = 50,
    tol: float = 0.01,
    bilinearize_method: str = "composite",
    bilinearize_config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Find the performance point using the Capacity Spectrum Method (CSM).

    Fully implements the ATC-40 Capacity Spectrum Method with secant
    iteration.  The capacity curve is converted to ADRS format,
    bilinearised, and intersected with the design response spectrum
    reduced by equivalent viscous damping.

    Procedure:
        1. **ADRS conversion** — pushover curve (V‑δ) → spectral
           acceleration vs. spectral displacement using the first-mode
           participation factor and effective modal mass.  ``S_a`` is
           computed as ``V / M_eff`` (**m/s²**) — see the module
           docstring for the unit convention.
        2. **Bilinearization** — one of ``'composite'`` (default),
           ``'stiffness_change'``, or ``'equal_energy'`` methods
           determines the yield point (S_dy, S_ay).
        3. **Secant iteration** — starting from 20 % of the peak
           spectral displacement, repeatedly:
           a. Compute equivalent secant period T_eq from the trial
              point on the capacity curve.
           b. Compute ductility μ and equivalent viscous damping
              β_eq (ATC-40 Eqn 5-19).
           c. Compute damping reduction factor B (
              :func:`damping_reduction_factor`).
           d. Interpolate the elastic demand spectrum at T_eq and
              reduce it by B to obtain the inelastic demand
              displacement S_d_demand.
           e. Check convergence: relative change < tol, or stall
              detection (S_d_trial stops changing over 3 iterations).
           f. Update trial point (50 % weighting with demand).
        4. **Elastic convergence** — if both trial and demand drop
           below the first capacity data point, the structure is in
           the elastic range.  The performance point is computed
           directly from the best-mode period and elastic spectrum.
        5. **Return values** — performance point (S_dp, S_ap),
           base shear V_base, roof displacement D_roof, equivalent
           period T_eq, ductility μ, convergence status, and the
           damping ratio β_eq / reduction factor B used at the
           performance point.

    Args:
        pushover_results: Output from :meth:`run_pushover_analysis`.
            Expected keys: ``'control_disp'``, ``'base_shear'``,
            ``'roof_disp'``, ``'monitor_disp'`` (roof displacement
            history).
        modal_results: Output from :meth:`run_modal_analysis`.
            Expected key: ``'periods'`` (list of periods in s).
        mode_shapes: Output from :meth:`extract_mode_shapes`.
            Dict mapping mode index → {node_tag: (ux, uy, uz)}.
        spectrum_periods: Periods (s) defining the elastic demand
            response spectrum.
        spectrum_accels: Spectral accelerations (**m/s²**) corresponding
            to *spectrum_periods*.
        direction: Push direction (``'X'`` or ``'Y'``), used for
            extracting the appropriate mode shape component.
        damping_ratio: Elastic damping ratio (default 0.05).
        max_iter: Maximum iterations for secant convergence (default 50).
        tol: Convergence tolerance on S_d (relative, default 1 %).
        bilinearize_method: One of ``'composite'`` (default),
            ``'stiffness_change'``, or ``'equal_energy'``.
        bilinearize_config: Optional dict passed to the bilinearisation
            function.

    Returns:
        Dict with keys:

        * ``'S_dp'`` — performance point spectral displacement (m).
        * ``'S_ap'`` — performance point spectral acceleration (m/s²).
        * ``'V_base'`` — corresponding base shear (model force units).
        * ``'D_roof'`` — corresponding roof displacement (m).
        * ``'T_eq'`` — equivalent period at performance point (s).
        * ``'mu'`` — ductility demand.
        * ``'converged'`` — whether the iteration converged.
        * ``'iterations'`` — number of iterations used.
        * ``'S_dy'``, ``'S_ay'`` — bilinear yield point.
        * ``'bilinearize_method'`` — name of the method actually used.
        * ``'Gamma'`` — participation factor.
        * ``'M_eff'`` — effective modal mass.
        * ``'beta_eq'`` — equivalent viscous damping ratio at the
          performance point (β₀ + hysteretic contribution).
        * ``'B'`` — damping reduction factor applied to the demand
          spectrum at the performance point.
        * ``'capacity_adrs'`` — dict with ``'S_a'`` (m/s²) and
          ``'S_d'`` lists.

    Raises:
        ValueError: If the capacity spectrum has fewer than 3 valid
            data points after filtering.

    Example:
        >>> pp = compute_performance_point(
        ...     pushover_results, modal_results, mode_shapes,
        ...     [0.0, 0.1, 0.5, 1.0, 2.0],
        ...     [2.5, 2.5, 1.0, 0.5, 0.2],
        ...     direction='X',
        ... )
        >>> print(f"S_dp = {pp['S_dp']:.3f} m, converged = {pp['converged']}")
    """
    # 1. Convert pushover to ADRS
    adrs = pushover_to_adrs(
        pushover_results,
        modal_results,
        mode_shapes,
        direction=direction,
    )
    # Fold the capacity curve into the positive (S_d, S_a) quadrant.
    # A -X / -Y push produces negative physical displacements and base
    # shear; without this, the non-negativity mask below filters out the
    # entire curve and raises "Too few valid data points in capacity
    # spectrum".  The push direction is carried by the caller's label
    # (e.g. "-X") — the CSM iteration operates on magnitudes.  The
    # physical sign is re-applied to V_base / D_roof at the end.
    S_a_arr = np.abs(np.array(adrs["S_a"], dtype=float))
    S_d_arr = np.abs(np.array(adrs["S_d"], dtype=float))

    # Validate array lengths before masking
    ctrl_raw = pushover_results.get("control_disp", [0])
    shear_raw = pushover_results.get("base_shear", [0])
    n_ctrl = len(ctrl_raw)
    n_adrs = len(S_d_arr)
    if n_ctrl != n_adrs:
        raise ValueError(
            f"control_disp length ({n_ctrl}) does not match ADRS capacity "
            f"length ({n_adrs}). The control_disp and base_shear arrays "
            f"must have the same length as the pushover data before ADRS "
            f"conversion."
        )

    # Filter out negative / zero values — but keep the origin so the
    # capacity curve starts at (0,0).  The first push step can have a
    # tiny non-zero displacement; prepending an explicit origin keeps
    # the curve anchored for the bilinearization and for plotting.
    mask = (S_d_arr >= -1e-12) & (S_a_arr >= -1e-12)
    S_d_arr = S_d_arr[mask]
    S_a_arr = S_a_arr[mask]
    if len(S_d_arr) < 3:
        raise ValueError("Too few valid data points in capacity spectrum")

    Gamma = adrs["Gamma"]
    M_eff = adrs["M_eff"]
    phi_control = adrs["phi_control"]
    best_mode = adrs.get("best_mode", 0)
    _ctrl_arr = np.array(ctrl_raw, dtype=float)
    _shear_arr = np.array(shear_raw, dtype=float)
    # Push-direction sign, recovered from the final control displacement,
    # is re-applied to the physical (non-spectral) outputs below.
    dir_sign = 1.0
    if _ctrl_arr.size > 0 and _ctrl_arr[-1] < 0:
        dir_sign = -1.0
    control_disp_orig = np.abs(_ctrl_arr)[mask]
    base_shear_orig = np.abs(_shear_arr)[mask]

    # Anchor at origin (0,0) so the capacity curve starts at the origin
    # and the secant stiffness K_init is well-defined from the first
    # non-zero step.  The physical arrays below are anchored in lockstep
    # with the spectral arrays so ``np.interp`` always receives equal
    # length ``xp``/``fp`` (previously the origin was only prepended to
    # the physical arrays when the *first masked* step had non-trivial
    # displacement/shear, which desynced lengths when it did not).
    if S_d_arr[0] > 1e-12 or S_a_arr[0] > 1e-12:
        S_d_arr = np.concatenate([[0.0], S_d_arr])
        S_a_arr = np.concatenate([[0.0], S_a_arr])
        control_disp_orig = np.concatenate([[0.0], control_disp_orig])
        base_shear_orig = np.concatenate([[0.0], base_shear_orig])
    # The physical arrays are anchored in lockstep with the spectral
    # arrays (above); they are used directly for V_base/D_roof
    # interpolation.
    control_disp = control_disp_orig
    base_shear = base_shear_orig

    # 2. Bilinearise the capacity spectrum (find yield point)
    _bilin_map = {
        "composite": bilinearize_composite,
        "stiffness_change": bilinearize_stiffness_change,
        "equal_energy": bilinearize_equal_energy,
    }
    if bilinearize_method not in _bilin_map:
        raise ValueError(
            f"Unknown bilinearize_method '{bilinearize_method}'. "
            f"Expected one of {list(_bilin_map.keys())}."
        )
    _bilin_fn = _bilin_map[bilinearize_method]
    S_dy, S_ay, _bilin_name = _bilin_fn(S_d_arr, S_a_arr, config=bilinearize_config)

    # 3. Capacity spectrum demand method (secant iteration)
    T_spec = np.array(spectrum_periods)
    Sa_spec = np.array(spectrum_accels)

    # First-mode elastic period from modal analysis
    modal_periods = modal_results.get("periods", [])
    # Normalise best_mode to 0-based index for modal_periods list access.
    # If mode_shapes keys are 1-based (e.g. {1: ..., 2: ...}), convert.
    if mode_shapes and min(mode_shapes.keys()) == 1:
        best_mode_idx = max(best_mode - 1, 0)
    else:
        best_mode_idx = best_mode
    best_mode_period = (
        float(modal_periods[best_mode_idx]) if best_mode_idx < len(modal_periods) else 1.0
    )

    peak_idx = int(np.argmax(S_a_arr))
    S_d_peak = float(S_d_arr[peak_idx])

    S_d_trial = S_d_peak * 0.2  # start at 20% of peak
    S_dp = S_d_trial  # default in case loop completes without convergence
    converged = False
    prev_S_d = S_d_trial
    stall_count = 0
    history = []
    beta_eq_final = damping_ratio
    B_final = 1.0
    # Initialised before the loop so the fallback below never references
    # an undefined loop variable when max_iter is zero.
    T_eq = best_mode_period

    for iteration in range(max_iter):
        # Spectral acceleration at trial point (interpolate capacity)
        if S_d_trial <= S_d_arr[0]:
            S_a_trial = float(S_a_arr[0])
        elif S_d_trial >= S_d_arr[-1]:
            S_a_trial = float(S_a_arr[-1])
        else:
            S_a_trial = float(np.interp(S_d_trial, S_d_arr, S_a_arr))

        # Equivalent period at trial point
        #   T_eq = 2π √(S_d / S_a)  — S_a in m/s² (force/mass), so this
        #   is the exact secant period in seconds regardless of the
        #   model's force/length units (kN/t = N/kg = m/s²).
        T_eq = 2.0 * math.pi * math.sqrt(S_d_trial / max(S_a_trial, 1e-12))

        # Ductility
        mu = max(S_d_trial / max(S_dy, 1e-12), 1.0)

        # Equivalent viscous damping from hysteresis (ATC-40 Eqn 5-19)
        beta_eq = compute_equivalent_damping(mu, beta_0=damping_ratio)

        # Damping reduction factor (ATC-40 / GB 50011 compatible)
        B = damping_reduction_factor(beta_eq, beta_0=damping_ratio)

        # Demand spectral acceleration at T_eq
        Sa_demand = float(np.interp(T_eq, T_spec, Sa_spec)) / B

        # Demand spectral displacement
        S_d_demand = Sa_demand * (T_eq / (2.0 * math.pi)) ** 2

        history.append((S_d_trial, S_d_demand))
        beta_eq_final = beta_eq
        B_final = B

        # Convergence checks
        delta = abs(S_d_demand - S_d_trial)
        if delta / max(S_d_trial, 1e-12) < tol:
            converged = True
            S_dp = S_d_demand
            break

        # Also converge if S_d_trial stops changing (stalled)
        change = abs(S_d_trial - prev_S_d) / max(S_d_trial, 1e-12)
        if change < tol * 0.1 and iteration > 3:
            stall_count += 1
            if stall_count >= 3:
                converged = True
                S_dp = S_d_trial
                break
        else:
            stall_count = 0

        prev_S_d = S_d_trial

        # Update trial: move towards demand
        S_d_trial = S_d_trial * 0.5 + S_d_demand * 0.5

        # Clamp: if S_d_trial drops below first data point and
        # S_d_demand also below it, we are in the elastic range.
        if S_d_trial < S_d_arr[0] and S_d_demand < S_d_arr[0]:
            Sa_el = float(np.interp(best_mode_period, T_spec, Sa_spec))
            S_d_el = Sa_el * (best_mode_period / (2.0 * math.pi)) ** 2
            S_dp = S_d_el
            converged = True
            break

    if not converged:
        # Use last trial point
        S_dp = S_d_trial

    # Compute final values at performance point
    if S_dp <= S_d_arr[0]:
        # Elastic range
        S_ap = float(np.interp(best_mode_period, T_spec, Sa_spec))
        mu_p = max(S_dp / max(S_dy, 1e-12), 1.0)
        if mu_p > 1.0:
            beta_p = compute_equivalent_damping(mu_p, beta_0=damping_ratio)
            B_p = damping_reduction_factor(beta_p, beta_0=damping_ratio)
            S_ap /= B_p
            beta_eq_final = beta_p
            B_final = B_p
        V_p = S_ap * abs(M_eff)
        D_p = S_dp * abs(Gamma) * abs(phi_control)
    elif S_dp >= S_d_arr[-1]:
        S_ap = float(np.interp(S_dp, S_d_arr, S_a_arr))
        V_p = float(np.interp(S_dp, S_d_arr, base_shear))
        D_p = float(np.interp(S_dp, S_d_arr, control_disp))
    else:
        S_ap = float(np.interp(S_dp, S_d_arr, S_a_arr))
        V_p = float(np.interp(S_dp, S_d_arr, base_shear))
        D_p = float(np.interp(S_dp, S_d_arr, control_disp))

    # Re-apply the push-direction sign to the physical outputs so that a
    # -X / -Y performance point reports a negative V_base and D_roof.
    V_p *= dir_sign
    D_p *= dir_sign

    T_eq_final = 2.0 * math.pi * math.sqrt(S_dp / max(S_ap, 1e-12))
    mu_final = max(S_dp / max(S_dy, 1e-12), 1.0)
    # Fall back only when the computed value is non-finite or
    # non-positive — a valid finite T_eq_final is kept even when the
    # capacity-demand iteration did not formally converge.
    if not math.isfinite(T_eq_final) or T_eq_final <= 0:
        # Fall back to a valid previously recorded period rather than
        # recomputing the same S_dp/S_ap expression (which produced the
        # invalid T_eq_final above).  Prefer the best-mode elastic period.
        _valid_T = best_mode_period
        if not math.isfinite(_valid_T) or _valid_T <= 0:
            _valid_T = T_eq
        if math.isfinite(_valid_T) and _valid_T > 0:
            T_eq_final = _valid_T

    return {
        "S_dp": S_dp,
        "S_ap": S_ap,
        "V_base": V_p,
        "D_roof": D_p,
        "T_eq": T_eq_final,
        "mu": mu_final,
        "converged": converged,
        "iterations": len(history),
        "S_dy": S_dy,
        "S_ay": S_ay,
        "bilinearize_method": _bilin_name,
        "Gamma": Gamma,
        "M_eff": M_eff,
        "beta_eq": beta_eq_final,
        "B": B_final,
        "capacity_adrs": {"S_a": S_a_arr.tolist(), "S_d": S_d_arr.tolist()},
    }


def check_modal_pushover_mode(
    direction: str,
    mass_ratios: list[float],
    rs_modal_base_shear: Optional[list[float]] = None,
) -> tuple[int, Optional[int], Optional[str]]:
    """Verify the mode selected for modal pushover.

    The primary mode for pushover is the one with the **highest mass
    participation ratio** in the push direction (ASCE 41 standard).
    When RS per-mode base shear data is available, also checks whether
    that mode is the dominant RS contributor.  A warning is returned
    if they differ.

    Args:
        direction: Push direction (``'X'`` or ``'Y'``), for diagnostics.
        mass_ratios: List of mass participation ratios from modal
            analysis.
        rs_modal_base_shear: Optional list of per-mode base shear
            magnitudes from RS analysis (same ordering as modes).

    Returns:
        Tuple ``(best_mode_idx, rs_dominant_mode, warning)`` where:

        * ``best_mode_idx`` — 0‑based index of the mode with highest
          mass participation.
        * ``rs_dominant_mode`` — RS-dominant mode index, or ``None``.
        * ``warning`` — warning string if modes differ, else ``None``.
    """
    best_idx = int(np.argmax(np.abs(mass_ratios)))
    warning: Optional[str] = None
    rs_dominant: Optional[int] = None

    if rs_modal_base_shear is not None and len(rs_modal_base_shear) > 0:
        rs_arr = np.abs(rs_modal_base_shear)
        rs_dominant = int(np.argmax(rs_arr))
        if rs_dominant != best_idx:
            warning = (
                f"Modal pushover in {direction}: mass-based best mode "
                f"(index {best_idx}) differs from RS-based dominant mode "
                f"(index {rs_dominant}).  Consider reviewing the selected "
                f"mode — the mode with highest RS base shear contribution "
                f"may be more appropriate."
            )

    return best_idx, rs_dominant, warning


# ═══════════════════════════════════════════════════════════════════════════
# ADRS conversion
# ═══════════════════════════════════════════════════════════════════════════


def _modal_participation(
    shape: dict[int, tuple[float, float, float]],
    masses: dict[int, float],
    dir_idx: int,
) -> tuple[float, float]:
    """Compute the modal participation terms (L, M_star) for one mode.

    Args:
        shape: Mode shape dict mapping node tag → (ux, uy, uz).
        masses: Nodal mass dict mapping node tag → mass.
        dir_idx: Direction index, 0 (X), 1 (Y), or 2 (Z).

    Returns:
        Tuple ``(L, M_star)`` where ``L = Σ m_i φ_i`` and
        ``M_star = Σ m_i φ_i²`` for the given direction.
    """
    L = 0.0
    M_star = 0.0
    for node_tag, (ux, uy, uz) in shape.items():
        m = masses.get(node_tag, 0.0)
        phi = [ux, uy, uz][dir_idx]
        M_star += m * phi**2
        L += m * phi
    return L, M_star


def pushover_to_adrs(
    pushover_results: dict[str, Any],
    modal_results: dict[str, Any],
    mode_shapes: dict[int, dict[int, tuple[float, float, float]]],
    direction: str = "X",
) -> dict[str, Any]:
    """Convert pushover capacity curve (V‑δ) → ADRS (S_a‑S_d) format.

    Uses the first-mode (or best-mode) participation factor and
    effective modal mass to convert the physical pushover curve to
    spectral coordinates.

    .. math::

        S_a = \\frac{V}{M^*}          \\quad (\\text{m/s²})
        S_d = \\frac{\\delta}{\\Gamma \\cdot \\phi_{\\text{control}}}

    Where:
    - M^* = effective modal mass = L² / M* where M* = Σ m_i φ_i²
    - Γ = participation factor = L / M*
    - φ_control = mode shape ordinate at the control/roof node

    **Units**: ``S_a`` is returned in **m/s²**, computed directly from
    Newton's second law ``F = M·a`` with no gravitational constant — the
    model's force and mass units cancel (kN/t = N/kg = m/s²).  This is
    the same unit system as the demand spectrum accelerations passed to
    :func:`compute_performance_point`.  ``S_d`` is in model length units
    (m for a kN-m model).

    Args:
        pushover_results: Dict with ``'control_disp'``, ``'base_shear'``,
            and ``'roof_disp'`` (or ``'monitor_disp'``) arrays.
        modal_results: Dict with ``'periods'`` and **required**
            ``'nodal_masses'`` — a ``{node_tag: mass}`` map (same keys as
            ``mode_shapes``).  Missing or empty mass data raises
            ``ValueError`` because it would degenerate ``Gamma`` /
            ``M_eff`` to zero and falsify the ADRS conversion.
        mode_shapes: Dict mapping mode index → {node_tag: (ux, uy, uz)}.
        direction: Push direction (``'X'``, ``'Y'``, or ``'Z'``).

    Returns:
        Dict with keys:

        * ``'S_a'`` — spectral acceleration array (**m/s²**).
        * ``'S_d'`` — spectral displacement array (model length units).
        * ``'Gamma'`` — participation factor.
        * ``'M_eff'`` — effective modal mass.
        * ``'phi_control'`` — control-node mode shape ordinate.
        * ``'best_mode'`` — index of the mode with the highest
          push-direction participation, measured by ``L² / M_star``
          (equivalently ``M_eff``).  For mass-normalized eigenvectors —
          the standard output of
          :meth:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder.extract_mode_shapes` —
          ``M_star = 1`` for every mode, so this reduces to the mode with
          the largest ``L = Σ mᵢ φᵢ`` in the push direction.  The index
          follows the ``mode_shapes`` key space (0‑based for
          ``extract_mode_shapes()`` output, where OpenSees mode number =
          ``best_mode + 1``).
    """
    # Use best mode (highest mass participation in push direction).
    # ``nodal_masses`` is part of the required modal-result contract
    # (``run_modal_analysis`` always includes it).  A missing or empty
    # mass map degenerates Gamma / M_eff to zero — with no participation
    # terms every mode is "negligible", yet the empty-map accept-state
    # check can still pass and silently emit S_a = V / 0.  Fail loudly.
    masses = modal_results.get("nodal_masses", {})
    if not masses:
        raise ValueError(
            "pushover_to_adrs: modal_results['nodal_masses'] is missing or "
            "empty — ADRS conversion requires the nodal mass map (produced "
            "by run_modal_analysis). Without it Gamma and M_eff degenerate "
            "and S_a/S_d are invalid."
        )

    # Strip a leading sign ("-Y" → "Y") so negative-Y pushes select the
    # Y component.  Unsupported labels are explicitly rejected rather
    # than silently falling back to axis 0 (X).
    _base_dir = direction.lstrip("+-")
    if _base_dir not in ("X", "Y", "Z"):
        raise ValueError(
            f"Unsupported push direction: {direction!r}. "
            f"Expected one of 'X', 'Y', 'Z', '+X', '-X', '+Y', '-Y', '+Z', '-Z'."
        )
    dir_idx = {"X": 0, "Y": 1, "Z": 2}[_base_dir]

    # Find the mode with highest participation in the push direction.
    #
    # Two-pass selection: first compute (L, M_star) for every mode and
    # record the maximum modal effective mass M_star_max.  Modes whose
    # push-direction effective mass is negligible relative to the best
    # mode are excluded *before* evaluating the participation ratio
    # L²/M_star.  This matters because the ratio is scale-invariant but
    # numerically ill-conditioned when M_star ≈ 0: eigenvector-
    # normalization noise in a mode that barely displaces in the push
    # direction (e.g. a 0.3 %-participation torsional mode out-ranking
    # the true 62 % sway mode of a 3D building) makes L²/M_star
    # spuriously large, so a naive max selection picks the wrong mode
    # and corrupts the S_a/S_d scaling of the whole CSM curve.  The
    # threshold is measured against M_star_max (same eigenvector
    # normalization) rather than the absolute total mass — this is
    # only meaningful when all mode shapes share a common
    # normalization.
    best_mode = 0
    best_participation = 0.0
    mode_accepted = False

    _stats: list[tuple[int, float, float]] = []
    _m_star_max = 0.0
    for mode_idx, shape in mode_shapes.items():
        L, M_star = _modal_participation(shape, masses, dir_idx)
        _stats.append((mode_idx, L, M_star))
        _m_star_max = max(_m_star_max, M_star)

    # Reject modes carrying < 1 % of the best mode's push-direction
    # effective modal mass (M_star = Σ mᵢ φᵢ²).  1 % is far below the
    # 30–60 % of a genuine sway mode but safely above the ~1e-3
    # relative level of the numerically contaminated modes observed
    # with real 3D building models.
    _min_m_star = _m_star_max * 1e-2

    for mode_idx, L, M_star in _stats:
        if M_star > 0 and M_star >= _min_m_star:
            mode_accepted = True
            participation = L**2 / M_star
            if participation > best_participation:
                best_participation = participation
                best_mode = mode_idx

    if not mode_accepted:
        # Every mode was rejected as degenerate (no mode carries
        # meaningful effective modal mass in the push direction — e.g.
        # when ``num_modes`` is too small to reach the true
        # push-direction mode).  A unit-value fallback (Gamma = 1.0,
        # M_eff = 1.0) is NOT used: M_eff would be dimensionless rather
        # than a mass, and the resulting S_a / S_d / T_eq would not be
        # valid CSM quantities.  Fail loudly instead.
        raise ValueError(
            "pushover_to_adrs: no mode has meaningful effective modal "
            f"mass in direction {direction!r} — increase num_modes or "
            "restrain out-of-plane degrees of freedom. S_a, S_d, and "
            "T_eq cannot be computed without a physical equivalent mode."
        )
    else:
        # Extract the best mode shape
        best_shape = mode_shapes.get(best_mode, {})
        L, M_star = _modal_participation(best_shape, masses, dir_idx)

        if M_star <= 0:
            # No masses or mode shapes — the unit-value identity
            # (Gamma = 1.0, M_eff = 1.0) is dimensionally invalid
            # (M_eff must be a mass) and would corrupt S_a / S_d /
            # T_eq.  Fail loudly instead.
            raise ValueError(
                "pushover_to_adrs: selected mode has non-positive "
                f"effective modal mass in direction {direction!r} "
                "— missing nodal masses or degenerate mode shape."
            )
        else:
            Gamma = L / M_star
            M_eff = L**2 / M_star
            # Use control/monitor node from pushover_results if available,
            # otherwise fall back to the roof node (highest Z).
            node_coords = pushover_results.get("node_coords", {})
            cntl_tag = pushover_results.get(
                "control_node", pushover_results.get("control_node_tag")
            )
            if cntl_tag is not None and cntl_tag in best_shape:
                phi_control = [
                    best_shape[cntl_tag][0],
                    best_shape[cntl_tag][1],
                    best_shape[cntl_tag][2],
                ][dir_idx]
            else:
                roof_tag = max(
                    (tag for tag in node_coords if tag in best_shape),
                    key=lambda t: node_coords.get(t, (0, 0, 0))[2],
                    default=None,
                )
                if roof_tag is not None:
                    phi_control = [
                        best_shape[roof_tag][0],
                        best_shape[roof_tag][1],
                        best_shape[roof_tag][2],
                    ][dir_idx]
                else:
                    warnings.warn(
                        "pushover_to_adrs: control_node is missing and no roof "
                        "node could be resolved — phi_control falls back to 1.0.",
                        UserWarning,
                        stacklevel=2,
                    )
                    phi_control = 1.0

    control_disp = np.array(pushover_results.get("control_disp", []), dtype=float)
    base_shear = np.array(pushover_results.get("base_shear", []), dtype=float)

    if len(control_disp) < 2 or len(base_shear) < 2:
        return {
            "S_a": [],
            "S_d": [],
            "Gamma": Gamma,
            "M_eff": M_eff,
            "phi_control": 1.0 if phi_control is None else phi_control,
            "best_mode": best_mode,
        }

    # S_a in m/s² via F = M·a (no g): kN/t = N/kg = m/s², so the
    # conversion is exact in any consistent force/mass unit system.
    # S_d in model length units.  Use |Gamma| so S_d is positive for a
    # push in the positive direction — the mode-shape sign is arbitrary
    # (eigenvector normalization) and must not flip the spectral
    # displacement ordering of the capacity curve.
    pc = 1.0 if phi_control is None else abs(phi_control)
    S_a = base_shear / M_eff  # m/s²
    S_d = control_disp / (abs(Gamma) * pc)  # model length units

    # The capacity curve is anchored at the origin (S_a[0] = 0, S_d[0] = 0).
    # At zero displacement the first push step produces V ≈ 0, so S_a[0]
    # can carry a tiny negative machine-noise value (e.g. -1e-17) that
    # must not fail downstream non-negativity assertions.
    if len(S_a) > 0 and S_a[0] < 0:
        S_a[0] = 0.0

    return {
        "S_a": S_a.tolist(),
        "S_d": S_d.tolist(),
        "Gamma": Gamma,
        "M_eff": M_eff,
        "phi_control": phi_control,
        "best_mode": best_mode,
    }
