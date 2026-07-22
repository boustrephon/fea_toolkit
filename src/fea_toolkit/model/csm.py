"""Capacity Spectrum Method (CSM) utilities.

Standalone functions for converting pushover results to ADRS format and
computing the performance point per ATC-40 / GB 50011.

These functions are **pure data-flow** — they take analysis results dicts
as input and return results dicts.  They have no dependency on any builder
or model object, making them easy to test and reuse.
"""

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


def pushover_to_adrs(
    pushover_results: Dict[str, Any],
    modal_results: Dict[str, Any],
    mode_shapes: Dict[int, Dict[int, Tuple[float, float, float]]],
    direction: str = 'X',
    g: Optional[float] = None,
) -> Dict[str, Any]:
    """Convert a pushover capacity curve to ADRS (Acceleration-Displacement
    Response Spectrum) coordinates.

    The conversion uses the fundamental mode:

    .. math::

        S_d = \\frac{\\Delta_{control}}{\\Gamma_1 \\phi_{1,control}}

        S_a = \\frac{V_{base}}{M_1^*}

    where :math:`\\Gamma_1` is the modal participation factor,
    :math:`\\phi_{1,control}` is the mode shape value at the control
    node, and :math:`M_1^*` is the effective modal mass.

    Args:
        pushover_results: Output from :meth:`run_pushover_analysis`.
        modal_results: Output from :meth:`run_modal_analysis` (must
            contain ``'modal_props'``).
        mode_shapes: Output from :meth:`extract_mode_shapes`.
        direction: Push direction (``'X'``, ``'Y'``, or ``'Z'``).
        g: Gravitational acceleration (m/s²).  Not used directly in the
            current implementation — provided for API compatibility with
            callers that compute it.

    Returns:
        Dict with keys:

        * ``'S_a'`` — list of spectral accelerations (m/s²).
        * ``'S_d'`` — list of spectral displacements (m).
        * ``'Gamma'`` — modal participation factor.
        * ``'M_eff'`` — effective modal mass (kg).
        * ``'phi_control'`` — mode shape at control node.
        * ``'S_dy'``, ``'S_ay'`` — bilinear yield point (m, m/s²)
          or ``None`` if not computed.
    """
    direction_map = {'X': 0, 'Y': 1, 'Z': 2}
    dof_idx = direction_map.get(direction.upper(), 0)

    control_node_tag = pushover_results.get('control_node')
    if control_node_tag is None:
        raise ValueError("pushover_results must contain 'control_node'")

    # Modal participation factor from the dominant mode in the push direction
    modal_props = modal_results.get('modal_props', {})
    mass_key = (f'partiMassMX' if direction.upper() == 'X'
                else f'partiMassMY' if direction.upper() == 'Y'
                else f'partiMassMZ')
    ratio_key = (f'partiMassRatiosMX' if direction.upper() == 'X'
                 else f'partiMassRatiosMY' if direction.upper() == 'Y'
                 else f'partiMassRatiosMZ')

    mass_list = modal_props.get(mass_key, [0.0])
    ratio_list = modal_props.get(ratio_key, [0.0])

    # Find the mode with the highest mass participation in push direction
    best_mode = 0
    best_ratio = 0.0
    for i, r in enumerate(ratio_list):
        if abs(r) > best_ratio:
            best_ratio = abs(r)
            best_mode = i

    M_eff = mass_list[best_mode] if mass_list else 1.0
    if abs(M_eff) < 1e-12:
        total_mass_key = 'totalFreeMass'
        free_mass = modal_props.get(total_mass_key, [0])
        free_val = free_mass[0] if free_mass else 0.0
        M_eff = free_val if abs(free_val) > 1e-12 else 1.0

    # Participation factor for mass-normalised eigenvectors.
    # nodeEigenvector returns mass-normalised eigenvectors (φᵀMφ = 1),
    # so the participation factor Γ = √M_eff.
    Gamma = math.sqrt(abs(M_eff))

    # Mode shape value at the control node (best mode)
    phi_control = 1.0
    if mode_shapes and best_mode in mode_shapes:
        node_shape = mode_shapes[best_mode].get(control_node_tag)
        if node_shape is not None:
            phi_control = node_shape[dof_idx]
    if abs(phi_control) < 1e-12:
        phi_control = 1.0

    # Convert
    control_disp = pushover_results.get('control_disp', [0.0])
    base_shear = pushover_results.get('base_shear', [0.0])

    S_d = [abs(d) / (abs(Gamma) * abs(phi_control)) for d in control_disp]
    S_a = [abs(v) / abs(M_eff) for v in base_shear]

    return {
        'S_a': S_a,
        'S_d': S_d,
        'Gamma': Gamma,
        'M_eff': M_eff,
        'phi_control': phi_control,
        'best_mode': best_mode,
        'S_dy': None,
        'S_ay': None,
    }


def compute_performance_point(
    pushover_results: Dict[str, Any],
    modal_results: Dict[str, Any],
    mode_shapes: Dict[int, Dict[int, Tuple[float, float, float]]],
    spectrum_periods: List[float],
    spectrum_accels: List[float],
    direction: str = 'X',
    g: Optional[float] = None,
    damping_ratio: float = 0.05,
    max_iter: int = 50,
    tol: float = 0.01,
) -> Dict[str, Any]:
    """Find the performance point using the Capacity Spectrum Method (CSM).

    The capacity spectrum is bilinearised and intersected with the
    demand response spectrum (in ADRS format).  Equivalent viscous
    damping from hysteresis is used to reduce the elastic demand
    (per ATC-40 / GB 50011 CSM procedure).

    Args:
        pushover_results: Output from :meth:`run_pushover_analysis`.
        modal_results: Output from :meth:`run_modal_analysis`.
        mode_shapes: Output from :meth:`extract_mode_shapes`.
        spectrum_periods: Periods (s) defining the elastic demand
            spectrum.
        spectrum_accels: Spectral accelerations (m/s²) corresponding
            to *spectrum_periods*.
        direction: Push direction.
        g: Gravitational acceleration.
        damping_ratio: Elastic damping ratio (default 0.05).
        max_iter: Maximum iterations for secant convergence.
        tol: Convergence tolerance on S_d (relative).

    Returns:
        Dict with keys:

        * ``'S_dp'`` — performance point spectral displacement (m).
        * ``'S_ap'`` — performance point spectral acceleration (m/s²).
        * ``'V_base'`` — corresponding base shear (N).
        * ``'D_roof'`` — corresponding roof displacement (m).
        * ``'T_eq'`` — equivalent period at performance point (s).
        * ``'mu'`` — ductility demand.
        * ``'converged'`` — whether the iteration converged.
        * ``'S_dy'``, ``'S_ay'`` — bilinear yield point.
        * ``'capacity_adrs'`` — the full ADRS curve (dict with ``'S_a'``,
          ``'S_d'``).
    """
    # 1. Convert pushover to ADRS
    adrs = pushover_to_adrs(
        pushover_results, modal_results, mode_shapes,
        direction=direction, g=g,
    )
    S_a_arr = np.array(adrs['S_a'])
    S_d_arr = np.array(adrs['S_d'])

    # Filter out negative / zero values
    mask = (S_d_arr > 1e-12) & (S_a_arr > 1e-12)
    S_d_arr = S_d_arr[mask]
    S_a_arr = S_a_arr[mask]
    if len(S_d_arr) < 3:
        raise ValueError(
            "Too few valid data points in capacity spectrum"
        )

    Gamma = adrs['Gamma']
    M_eff = adrs['M_eff']
    phi_control = adrs['phi_control']
    best_mode = adrs.get('best_mode', 0)
    control_disp = np.array(pushover_results.get('control_disp', [0]))[mask]
    base_shear = np.array(pushover_results.get('base_shear', [0]))[mask]
    total_mass = M_eff  # effective modal mass for first mode

    # 2. Bilinearise the capacity spectrum (find yield point)
    peak_idx = np.argmax(S_a_arr)
    S_d_peak = S_d_arr[peak_idx]
    S_a_peak = S_a_arr[peak_idx]

    # Initial elastic stiffness from first 20% of points
    n_el = max(3, len(S_d_arr) // 5)
    K_init = np.polyfit(S_d_arr[:n_el], S_a_arr[:n_el], 1)[0]

    # Bilinear fit using equal energy
    area_actual = np.trapezoid(S_a_arr[:peak_idx + 1], S_d_arr[:peak_idx + 1])

    # Solve for S_dy using equal-energy principle (iterative search)
    S_dy = S_d_peak * 0.3  # initial guess
    for _ in range(100):
        S_ay = K_init * S_dy
        # Area under bilinear up to peak
        A1 = 0.5 * S_ay * S_dy
        A2 = S_ay * (S_d_peak - S_dy)
        A3 = 0.5 * (S_a_peak - S_ay) * (S_d_peak - S_dy)
        area_bilin = A1 + A2 + A3
        err = (area_bilin - area_actual) / area_actual
        if abs(err) < 0.001:
            break
        S_dy *= (1.0 - err * 0.5)
        # Clamp to [S_d_arr[0], S_d_peak] to prevent runaway correction
        S_dy = max(S_d_arr[0], min(S_dy, S_d_peak))

    S_ay = max(K_init * S_dy, S_a_arr[1] if len(S_a_arr) > 1 else S_a_arr[0])

    # 3. Capacity spectrum demand method (secant iteration)
    T_spec = np.array(spectrum_periods)
    Sa_spec = np.array(spectrum_accels)

    # First-mode elastic period from modal analysis
    modal_periods = modal_results.get('periods', [])
    best_mode_period = modal_periods[best_mode] if best_mode < len(modal_periods) else 1.0

    S_d_trial = S_d_peak * 0.2  # start at 20% of peak
    converged = False
    prev_S_d = S_d_trial
    stall_count = 0
    history = []

    for iteration in range(max_iter):
        # Spectral acceleration at trial point (interpolate capacity)
        if S_d_trial <= S_d_arr[0]:
            S_a_trial = S_a_arr[0]
        elif S_d_trial >= S_d_arr[-1]:
            S_a_trial = S_a_arr[-1]
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
        'S_dp': S_dp,
        'S_ap': S_ap,
        'V_base': V_p,
        'D_roof': D_p,
        'T_eq': T_eq_final,
        'mu': mu_final,
        'converged': converged,
        'iterations': len(history),
        'S_dy': S_dy,
        'S_ay': S_ay,
        'Gamma': Gamma,
        'M_eff': M_eff,
        'capacity_adrs': {'S_a': S_a_arr.tolist(), 'S_d': S_d_arr.tolist()},
    }
