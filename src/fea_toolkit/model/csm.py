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
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


# ═══════════════════════════════════════════════════════════════════════════
# Bilinearization — Stiffness-change detection
# ═══════════════════════════════════════════════════════════════════════════


def bilinearize_stiffness_change(
    S_d_arr: np.ndarray,
    S_a_arr: np.ndarray,
    config: Optional[Dict[str, Any]] = None,
) -> Tuple[float, float, str]:
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

    if len(S_d_arr) < 2 or len(S_a_arr) < 2:
        return 0.0, 0.0, "stiffness_change"

    peak_idx = config.get("peak_idx")
    if peak_idx is None:
        peak_idx = int(np.argmax(S_a_arr))
    peak_idx = min(peak_idx, len(S_d_arr) - 1)

    S_d_peak = float(S_d_arr[peak_idx])
    S_a_peak = float(S_a_arr[peak_idx])

    # initial elastic stiffness: first non-zero secant
    K_init = float(S_a_arr[1] / max(S_d_arr[1], 1e-12)) if S_d_arr[0] < 1e-12 else 0.0
    if K_init < 1e-12:
        # fallback: use first two points
        for i in range(1, len(S_d_arr)):
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
    config: Optional[Dict[str, Any]] = None,
) -> Tuple[float, float, str]:
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
        Tuple (S_dy, S_ay, method) where method is ``'equal_energy'``.

    Edge cases:
        - No clear yield (S_dy >= 90 % of peak) → yield at peak (mu = 1).
        - Empty arrays → returns (0.0, 0.0, 'equal_energy').
    """
    config = config or {}
    S_d_arr = np.asarray(S_d_arr, dtype=float)
    S_a_arr = np.asarray(S_a_arr, dtype=float)

    if len(S_d_arr) < 2 or len(S_a_arr) < 2:
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

    # Initial elastic stiffness (first non-zero secant)
    K_init = float(S_a_arr[1] / max(S_d_arr[1], 1e-12)) if S_d_arr[0] < 1e-12 else 0.0
    if K_init < 1e-12:
        for i in range(1, peak_idx):
            if S_d_arr[i] > 1e-12 and S_a_arr[i] > 1e-12:
                K_init = S_a_arr[i] / S_d_arr[i]
                break
    if K_init < 1e-12:
        K_init = S_a_peak / max(S_d_peak, 1e-12)

    # Cumulative integral (trapezoidal rule)
    I = np.zeros_like(S_d_arr)
    for i in range(1, peak_idx + 1):
        I[i] = I[i - 1] + 0.5 * (S_d_arr[i] - S_d_arr[i - 1]) * (
            S_a_arr[i] + S_a_arr[i - 1]
        )
    A_cap = I[peak_idx]

    # Local variable for S_a at yield (used in loop and after)
    S_ay_local = 0.0

    # Initial guess
    initial_guess = config.get("initial_guess", 0.30)
    tol = config.get("tolerance", 1e-3)
    max_iter = config.get("max_iter", 100)

    S_dy = S_d_peak * initial_guess
    S_ay = 0.0  # ensure bound before loop
    if S_dy <= S_d_arr[0]:
        S_dy = float(S_d_arr[1] if len(S_d_arr) > 1 else S_d_arr[0])

    for _iteration in range(max_iter):
        if S_dy <= S_d_arr[0]:
            S_dy = float(S_d_arr[0])
        if S_dy >= S_d_peak:
            S_dy = S_d_peak
            S_ay = S_a_peak
            return S_dy, S_ay, "equal_energy"

        # S_a at S_dy (interpolate capacity curve)
        S_ay_elastic = K_init * S_dy
        S_a_at_dy = float(np.interp(S_dy, S_d_arr, S_a_arr))
        S_ay = min(S_ay_elastic, S_a_at_dy)

        if S_ay <= 0:
            S_dy = S_d_peak * 1.5  # move away from zero
            continue

        # Area under bilinear curve up to peak
        A_bilin = 0.5 * S_ay * S_dy + S_ay * (S_d_peak - S_dy)
        A_bilin += 0.5 * (S_a_peak - S_ay) * (S_d_peak - S_dy)

        err = (A_bilin - A_cap) / max(A_cap, 1e-12)

        if abs(err) < tol:
            break

        # Update: increase S_dy if area too small, decrease if too large
        S_dy *= 1.0 - 0.5 * err
        S_dy = max(float(S_d_arr[0] if len(S_d_arr) > 0 else 0.0),
                   min(S_dy, S_d_peak))

    # Sanity: if yield is >90% of peak, the curve is essentially
    # linear — reset to peak (mu = 1).
    if S_dy >= 0.90 * S_d_peak:
        S_dy = S_d_peak
        S_ay = S_a_peak

    return S_dy, S_ay, "equal_energy"


def bilinearize_composite(
    S_d_arr: np.ndarray,
    S_a_arr: np.ndarray,
    config: Optional[Dict[str, Any]] = None,
) -> Tuple[float, float, str]:
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
        config: Optional dict passed through to each sub-method.
            See :func:`bilinearize_stiffness_change` and
            :func:`bilinearize_equal_energy` for supported keys.

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
    peak_idx = cfg.get("peak_idx")
    if peak_idx is None:
        peak_idx = int(np.argmax(S_a_arr))
    cfg["peak_idx"] = peak_idx

    # Try stiffness-change first
    S_dy, S_ay, _ = bilinearize_stiffness_change(S_d_arr, S_a_arr, cfg)

    S_d_peak = float(S_d_arr[peak_idx])
    S_a_peak = float(S_a_arr[peak_idx])

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
# Performance point calculation
# ═══════════════════════════════════════════════════════════════════════════


def compute_performance_point(
    pushover_results: Dict[str, Any],
    modal_results: Dict[str, Any],
    mode_shapes: Dict[int, Dict[int, Tuple[float, float, float]]],
    spectrum_periods: List[float],
    spectrum_accels: List[float],
    direction: str = "X",
    g: Optional[float] = None,
    damping_ratio: float = 0.05,
    max_iter: int = 50,
    tol: float = 0.01,
    bilinearize_method: str = "composite",
    bilinearize_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Find the performance point using the Capacity Spectrum Method (CSM).

    Fully implements the ATC-40 Capacity Spectrum Method with secant
    iteration.  The capacity curve is converted to ADRS format,
    bilinearised, and intersected with the design response spectrum
    reduced by equivalent viscous damping.

    Procedure:
        1. **ADRS conversion** — pushover curve (V‑δ) → spectral
           acceleration vs. spectral displacement using the first-mode
           participation factor and effective modal mass.
        2. **Bilinearization** — one of ``'composite'`` (default),
           ``'stiffness_change'``, or ``'equal_energy'`` methods
           determines the yield point (S_dy, S_ay).
        3. **Secant iteration** — starting from 20 % of the peak
           spectral displacement, repeatedly:
           a. Compute equivalent secant period T_eq from the trial
              point on the capacity curve.
           b. Compute ductility μ and equivalent viscous damping
              β_eq (ATC-40 Eqn 5-19).
           c. Compute damping reduction factor B (ATC‑40 / GB 50011
              compatible).
           d. Interpolate the elastic demand spectrum at T_eq and
              reduce it by B to obtain the inelastic demand
              displacement S_d_demand.
           e. Check convergence: relative change < tol, or stall
              detection (S_d_trial stops changing over 3 iterations).
           f. Update trial point (50 % weighting with demand).
        4. **Elastic convergence** — if both trial and demand drop
           below the first capacity data point, the structure is in
           the elastic range.  The performance point is computed
           directly from the best-mode period and elastic spectrum.
        5. **Return values** — performance point (S_dp, S_ap),
           base shear V_base, roof displacement D_roof, equivalent
           period T_eq, ductility μ, and convergence status.

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
        spectrum_accels: Spectral accelerations (m/s²) corresponding
            to *spectrum_periods*.
        direction: Push direction (``'X'`` or ``'Y'``), used for
            extracting the appropriate mode shape component.
        g: Gravitational acceleration.  If ``None``, taken from
            ``pushover_results`` or default 9.81.
        damping_ratio: Elastic damping ratio (default 0.05).
        max_iter: Maximum iterations for secant convergence (default 50).
        tol: Convergence tolerance on S_d (relative, default 1 %).
        bilinearize_method: One of ``'composite'`` (default),
            ``'stiffness_change'``, or ``'equal_energy'``.
        bilinearize_config: Optional dict passed to the bilinearisation
            function.

    Returns:
        Dict with keys:

        * ``'S_dp'`` — performance point spectral displacement (m).
        * ``'S_ap'`` — performance point spectral acceleration (m/s²).
        * ``'V_base'`` — corresponding base shear (N).
        * ``'D_roof'`` — corresponding roof displacement (m).
        * ``'T_eq'`` — equivalent period at performance point (s).
        * ``'mu'`` — ductility demand.
        * ``'converged'`` — whether the iteration converged.
        * ``'iterations'`` — number of iterations used.
        * ``'S_dy'``, ``'S_ay'`` — bilinear yield point.
        * ``'bilinearize_method'`` — name of the method actually used.
        * ``'Gamma'`` — participation factor.
        * ``'M_eff'`` — effective modal mass.
        * ``'capacity_adrs'`` — dict with ``'S_a'`` and ``'S_d'`` lists.

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
        pushover_results, modal_results, mode_shapes,
        direction=direction, g=g,
    )
    S_a_arr = np.array(adrs["S_a"])
    S_d_arr = np.array(adrs["S_d"])

    # Filter out negative / zero values
    mask = (S_d_arr > 1e-12) & (S_a_arr > 1e-12)
    S_d_arr = S_d_arr[mask]
    S_a_arr = S_a_arr[mask]
    if len(S_d_arr) < 3:
        raise ValueError(
            "Too few valid data points in capacity spectrum"
        )

    Gamma = adrs["Gamma"]
    M_eff = adrs["M_eff"]
    phi_control = adrs["phi_control"]
    best_mode = adrs.get("best_mode", 0)
    control_disp = np.array(pushover_results.get("control_disp", [0]))[mask]
    base_shear = np.array(pushover_results.get("base_shear", [0]))[mask]

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
    S_dy, S_ay, _bilin_name = _bilin_fn(
        S_d_arr, S_a_arr, config=bilinearize_config)

    # 3. Capacity spectrum demand method (secant iteration)
    T_spec = np.array(spectrum_periods)
    Sa_spec = np.array(spectrum_accels)

    # First-mode elastic period from modal analysis
    modal_periods = modal_results.get("periods", [])
    best_mode_period = float(modal_periods[best_mode]) if best_mode < len(modal_periods) else 1.0

    peak_idx = int(np.argmax(S_a_arr))
    S_d_peak = float(S_d_arr[peak_idx])

    S_d_trial = S_d_peak * 0.2  # start at 20% of peak
    S_dp = S_d_trial  # default in case loop completes without convergence
    converged = False
    prev_S_d = S_d_trial
    stall_count = 0
    history = []

    for iteration in range(max_iter):
        # Spectral acceleration at trial point (interpolate capacity)
        if S_d_trial <= S_d_arr[0]:
            S_a_trial = float(S_a_arr[0])
        elif S_d_trial >= S_d_arr[-1]:
            S_a_trial = float(S_a_arr[-1])
        else:
            S_a_trial = float(np.interp(S_d_trial, S_d_arr, S_a_arr))

        # Equivalent period at trial point
        T_eq = 2.0 * math.pi * math.sqrt(S_d_trial / max(S_a_trial, 1e-12))

        # Ductility
        mu = max(S_d_trial / max(S_dy, 1e-12), 1.0)

        # Equivalent viscous damping from hysteresis (ATC-40 Eqn 5-19)
        if mu > 1.0:
            beta_eq = damping_ratio + 0.637 * (mu - 1.0) / (mu * math.pi)
        else:
            beta_eq = damping_ratio

        # Damping reduction factor (ATC-40 / GB 50011 compatible)
        B = 1.0
        if beta_eq > damping_ratio:
            B = math.sqrt((1.0 + 10.0 * (beta_eq - damping_ratio)) /
                          (1.0 + 5.0 * (beta_eq - damping_ratio)))
        B = max(0.5, min(2.0, B))

        # Demand spectral acceleration at T_eq
        Sa_demand = float(np.interp(T_eq, T_spec, Sa_spec)) / B

        # Demand spectral displacement
        S_d_demand = Sa_demand * (T_eq / (2.0 * math.pi)) ** 2

        history.append((S_d_trial, S_d_demand))

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
            beta_p = damping_ratio + 0.637 * (mu_p - 1.0) / (mu_p * math.pi)
            B_p = 1.0
            if beta_p > damping_ratio:
                B_p = math.sqrt((1.0 + 10.0 * (beta_p - damping_ratio)) /
                                (1.0 + 5.0 * (beta_p - damping_ratio)))
            B_p = max(0.5, min(2.0, B_p))
            S_ap /= B_p
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

    T_eq_final = 2.0 * math.pi * math.sqrt(S_dp / max(S_ap, 1e-12))
    mu_final = max(S_dp / max(S_dy, 1e-12), 1.0)

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
        "capacity_adrs": {"S_a": S_a_arr.tolist(), "S_d": S_d_arr.tolist()},
    }


def check_modal_pushover_mode(
    direction: str,
    mass_ratios: List[float],
    rs_modal_base_shear: Optional[List[float]] = None,
) -> Tuple[int, Optional[int], Optional[str]]:
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


def pushover_to_adrs(
    pushover_results: Dict[str, Any],
    modal_results: Dict[str, Any],
    mode_shapes: Dict[int, Dict[int, Tuple[float, float, float]]],
    direction: str = "X",
    g: Optional[float] = None,
) -> Dict[str, Any]:
    """Convert pushover capacity curve (V‑δ) → ADRS (S_a‑S_d) format.

    Uses the first-mode (or best-mode) participation factor and
    effective modal mass to convert the physical pushover curve to
    spectral coordinates.

    .. math::

        S_a = \\frac{V}{M^* \\cdot g} \\cdot \\frac{1}{\\phi_{\\text{control}}}
        S_d = \\frac{\\delta}{\\Gamma \\cdot \\phi_{\\text{control}}}

    Where:
    - M^* = effective modal mass = L² / M* where M* = Σ m_i φ_i²
    - Γ = participation factor = L / M*
    - φ_control = mode shape ordinate at the control/roof node

    Args:
        pushover_results: Dict with ``'control_disp'``, ``'base_shear'``,
            and ``'roof_disp'`` (or ``'monitor_disp'``) arrays.
        modal_results: Dict with ``'periods'``, and ``'nodal_masses'``
            (optional).
        mode_shapes: Dict mapping mode index → {node_tag: (ux, uy, uz)}.
        direction: Push direction (``'X'``, ``'Y'``, or ``'Z'``).
        g: Gravitational acceleration.  Uses ``pushover_results`` value
            or defaults to 9.81.

    Returns:
        Dict with keys:

        * ``'S_a'`` — spectral acceleration array (g units).
        * ``'S_d'`` — spectral displacement array (model length units).
        * ``'Gamma'`` — participation factor.
        * ``'M_eff'`` — effective modal mass.
        * ``'phi_control'`` — control-node mode shape ordinate.
        * ``'best_mode'`` — selected mode index.
    """
    if g is None:
        g = pushover_results.get("g", 9.81)

    # Use best mode (highest mass participation in push direction)
    masses = modal_results.get("nodal_masses", {})
    periods = modal_results.get("periods", [])

    dir_idx = {"X": 0, "Y": 1, "Z": 2}.get(direction, 0)

    # Find the mode with highest participation in the push direction
    best_mode = 0
    best_participation = 0.0
    for mode_idx, shape in mode_shapes.items():
        # Compute participation factor L/M*
        M_star = 0.0
        L = 0.0
        for node_tag, (ux, uy, uz) in shape.items():
            m = masses.get(node_tag, 0.0)
            phi = [ux, uy, uz][dir_idx]
            M_star += m * phi**2
            L += m * phi
        if M_star > 0:
            participation = L**2 / M_star
            if participation > best_participation:
                best_participation = participation
                best_mode = mode_idx

    # Extract the best mode shape
    best_shape = mode_shapes.get(best_mode, {})
    M_star = 0.0
    L = 0.0
    for node_tag, (ux, uy, uz) in best_shape.items():
        m = masses.get(node_tag, 0.0)
        phi = [ux, uy, uz][dir_idx]
        M_star += m * phi**2
        L += m * phi

    if M_star <= 0:
        # Fallback: no masses or mode shapes — use unit values
        Gamma = 1.0
        M_eff = 1.0
        phi_control = 1.0
    else:
        Gamma = L / M_star
        M_eff = L**2 / M_star
        # Control node = roof node (highest Z)
        roof_tag = max(
            (tag for tag in pushover_results.get("node_coords", {}).keys()
             if tag in best_shape),
            key=lambda t: pushover_results.get("node_coords", {}).get(t, (0, 0, 0))[2],
            default=None,
        )
        if roof_tag is not None:
            phi_control = [best_shape[roof_tag][0],
                           best_shape[roof_tag][1],
                           best_shape[roof_tag][2]][dir_idx]
        else:
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

    # S_a in g, S_d in model length units
    pc = 1.0 if phi_control is None else abs(phi_control)
    S_a = base_shear / (M_eff * g * pc)
    S_d = control_disp / (Gamma * pc)

    return {
        "S_a": S_a.tolist(),
        "S_d": S_d.tolist(),
        "Gamma": Gamma,
        "M_eff": M_eff,
        "phi_control": phi_control,
        "best_mode": best_mode,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Equivalent damping — ATC‑40
# ═══════════════════════════════════════════════════════════════════════════


def compute_equivalent_damping(
    alpha: float,
    Ke: float,
    S_dy: float,
    S_ay: float,
    mu: float,
) -> float:
    """Compute equivalent viscous damping per ATC‑40.

    .. math::

        \\beta_{\\text{eff}} = \\beta_0 + \\kappa \\cdot
        \\frac{2}{\\pi} \\cdot \\frac{(1 - \\alpha)(\\mu - 1)}
        {\\mu (1 + \\alpha \\mu - \\alpha)}

    where:
    - β₀ = 0.05 (inherent damping)
    - κ = 0.33 (damping modification factor for typical existing buildings)
    - α = post‑yield stiffness ratio
    - μ = ductility = Sd_max / S_dy

    Args:
        alpha: Post‑yield stiffness ratio (0..1).
        Ke: Elastic stiffness.
        S_dy: Yield displacement.
        S_ay: Yield spectral acceleration.
        mu: Ductility demand (≥1).

    Returns:
        Equivalent viscous damping ratio (0.05 + hysteretic contribution).
    """
    if mu <= 1.0:
        return 0.05

    beta_0 = 0.05
    kappa = 0.33  # Type C (poor hysteretic behaviour) — conservative

    # Hysteretic damping per ATC-40 Eqn 5-19
    beta_h = (2.0 / math.pi) * (1.0 - alpha) * (mu - 1.0) / (
        mu * (1.0 + alpha * mu - alpha)
    )
    beta_eff = beta_0 + kappa * beta_h

    return float(beta_eff)


# ═══════════════════════════════════════════════════════════════════════════
# Accessory
# ═══════════════════════════════════════════════════════════════════════════


def _seismic_weight_from_masses(masses: List[float],
                                 g: float = 9.81) -> float:
    """Convert nodal masses to seismic weight (W = Σ m_i · g)."""
    return sum(masses) * g


def _control_node_mass(masses: List[float],
                       mode_shape: List[float]) -> float:
    """Compute effective modal mass for the control node.

    .. math::

        M^* = Σ m_i · φ_i²
        L = Σ m_i · φ_i
        M_eff = L² / M^*
    """
    M_star = sum(m * p**2 for m, p in zip(masses, mode_shape))
    L = sum(m * p for m, p in zip(masses, mode_shape))
    return L**2 / M_star if M_star > 0 else 0.0


def first_mode_period(modal_results: Dict[str, Any]) -> float:
    """Extract first-mode period from modal results.

    Returns 0.0 if modal results are empty or periods unavailable.
    """
    periods = modal_results.get("periods", [])
    if periods:
        periods = [float(p) for p in periods]
        return periods[0]
    return 0.0


def _period_from_stiffness(k_eff: float, m_eff: float) -> float:
    """Effective period from secant stiffness and effective mass."""
    if k_eff <= 0 or m_eff <= 0:
        return 0.0
    return 2.0 * math.pi * math.sqrt(m_eff / k_eff)


def _kappa_damping_factor(beta_eq: float) -> float:
    """Reduce theoretical hysteresis damping per ATC-40 κ-factor.

    Type-A buildings (good hysteretic behaviour): κ = 1.0
    Type-B buildings (average): κ = 2/3
    Type-C buildings (poor): κ = 1/3

    Defaults to Type B (κ = 0.67).
    """
    kappa = 2.0 / 3.0
    return 0.05 + kappa * (beta_eq - 0.05)


def _adrs_to_physical(s_d: float, s_a: float,
                       Gamma: float, M_eff: float,
                       g: float = 9.81) -> Tuple[float, float]:
    """Convert ADRS coordinates back to physical displacement and base shear.

    D_roof = S_d · Γ · φ_control
    V_base = S_a · M_eff · g / φ_control
    """
    D_roof = s_d * Gamma * 1.0  # φ_control = 1.0 (normalised)
    V_base = s_a * M_eff * g / 1.0
    return D_roof, V_base