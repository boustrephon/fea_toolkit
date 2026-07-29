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


# ═══════════════════════════════════════════════════════════════════════════
# Bilinearisation methods
# ═══════════════════════════════════════════════════════════════════════════


def bilinearize_stiffness_change(
    S_d_arr: np.ndarray,
    S_a_arr: np.ndarray,
    config: Optional[Dict[str, Any]] = None,
) -> Tuple[float, float, str]:
    """Bilinearise a capacity curve via secant stiffness-change detection.

    Yield is detected at the first point where the secant stiffness drops
    below a fraction of the initial elastic stiffness (**Criterion A**),
    *or* where a single step shows a large relative drop (**Criterion B**).

    The algorithm is:

    1. Determine the peak index (auto-detected or forced via ``peak_idx``).
    2. Compute initial elastic stiffness ``K_init`` from the first 20 % of
       points (at least 3 points).
    3. Compute secant stiffnesses :math:`K_{sec} = S_a / S_d`.
    4. **Criterion A**: find the first index where
       :math:`K_{sec} < threshold \\times K_{init}`.
    5. **Criterion B** (fallback): if criterion A finds nothing and there
       are enough points, find the largest single-step relative drop in
       secant stiffness.  If it exceeds ``min_relative_drop``, use that.
    6. If a change index is found *before* the peak index, yield is at
       that point.  Otherwise, yield at the peak (no clear yield
       detected).

    **When to use**: structures with a clear, sharp yield point where
    a single stiffness-change event can be identified — e.g. brace
    buckling in concentrically braced frames, well-defined section
    yielding in steel moment frames with compact sections.

    References:
        - ATC-40 (1996), *Seismic Evaluation and Retrofit of Concrete
          Buildings*, Applied Technology Council.
        - Faella, G., Giordano, A., & Mezzi, M. (2004). "Definition of
          Suitable Bilinear Pushover Curves in Nonlinear Static
          Analyses." *13th WCEE*, Paper 1626.

    Args:
        S_d_arr: Spectral displacements (m).  Should be monotonically
            increasing and non-negative.  Zero-values are accepted
            (clamped to 1e-12 for division).
        S_a_arr: Spectral accelerations (m/s²), corresponding to
            *S_d_arr*.  Should be non-negative.  Negative values are
            tolerated but may produce unexpected results — consider
            ``np.abs(S_a_arr)`` if numerical noise is present.
        config: Optional dict with keys:

            * ``threshold`` — fraction of initial secant stiffness
              below which yield is detected (default ``0.50``).
              Range ``(0, 1]``.  Higher values are more sensitive
              (detect yield earlier / at lower S_dy).
            * ``min_relative_drop`` — single-step relative drop
              threshold for criterion B (default ``-0.30``).  A drop
              more negative than this triggers yield at that step.
            * ``peak_idx`` — force the peak index to this value
              (0-based).  If ``None`` (default), auto-detected as
              ``argmax(S_a_arr)``.

    Returns:
        Tuple ``(S_dy, S_ay, method_name)`` where *method_name* is
        always ``'stiffness_change'``.

    Edge cases:
        - Fewer than 2 data points: returns
          ``(S_d_arr[0], S_a_arr[0], 'stiffness_change')``.
        - ``peak_idx < 1``: same early return.
        - No stiffness drop detected before peak: returns the peak
          coordinates (no yield — effectively elastic).
    """
    cfg = dict(config or {})
    threshold = cfg.get('threshold', 0.50)
    min_relative_drop = cfg.get('min_relative_drop', -0.30)
    peak_idx = cfg.get('peak_idx')

    if peak_idx is None:
        peak_idx = int(np.argmax(S_a_arr))

    if peak_idx < 1 or len(S_d_arr) < 2:
        return float(S_d_arr[0]), float(S_a_arr[0]), 'stiffness_change'

    # Initial elastic stiffness from first 20% of points
    n_el = max(3, len(S_d_arr) // 5)
    K_init = float(np.polyfit(S_d_arr[:n_el], S_a_arr[:n_el], 1)[0])

    secant_k = S_a_arr / np.maximum(S_d_arr, 1e-12)
    K_init_ref = secant_k[1] if len(secant_k) > 1 else K_init
    if K_init_ref < 1e-12:
        K_init_ref = K_init

    # Criterion A: absolute threshold
    abs_threshold = threshold * K_init_ref
    change_idx: Optional[int] = None
    for i in range(1, len(secant_k)):
        if secant_k[i] < abs_threshold:
            change_idx = i
            break

    # Criterion B: relative drop in one step
    if change_idx is None and len(secant_k) > 2:
        rel_drops = np.diff(secant_k[1:]) / np.maximum(secant_k[1:-1], 1e-12)
        worst = int(np.argmin(rel_drops))
        if rel_drops[worst] < min_relative_drop:
            change_idx = worst + 2  # +1 for step 0, +1 for diff index

    if change_idx is not None and change_idx < peak_idx:
        S_dy = float(S_d_arr[change_idx])
        S_ay = float(S_a_arr[change_idx])
    else:
        # No clear stiffness change — return peak (no yield detected)
        S_dy = float(S_d_arr[peak_idx])
        S_ay = float(S_a_arr[peak_idx])

    return S_dy, S_ay, 'stiffness_change'


def bilinearize_equal_energy(
    S_d_arr: np.ndarray,
    S_a_arr: np.ndarray,
    config: Optional[Dict[str, Any]] = None,
) -> Tuple[float, float, str]:
    """Bilinearise a capacity curve using the ATC-40 equal-energy method.

    Finds the yield point :math:`(S_{dy}, S_{ay})` that preserves the
    area under the capacity curve up to the peak, using an iterative
    Newton-style relaxation.  The bilinear curve is elastic-perfectly
    plastic with hardening defined by the elastic stiffness :math:`K`.

    The algorithm is:

    1. Determine the peak index and peak coordinates
       :math:`(S_{d,peak}, S_{a,peak})`.
    2. Compute initial elastic stiffness ``K_init`` from the first 20 %
       of data points.
    3. Compute the actual area under the capacity curve from the origin
       to the peak (trapezoidal integration).
    4. Iterate from an initial guess of ``initial_guess * S_d_peak``:
       - Compute :math:`S_{ay} = K_{init} \\times S_{dy}`
       - Compute the bilinear area as:
         :math:`A_{bilin} = \\frac{1}{2} \\times S_{ay} \\times S_{dy}
         + S_{ay} \\times (S_{d,peak} - S_{dy})
         + \\frac{1}{2} \\times (S_{a,peak} - S_{ay}) \\times (S_{d,peak} - S_{dy})`
       - Compute the relative error and update :math:`S_{dy}` using
         :math:`S_{dy} \\leftarrow S_{dy} \\times (1 - 0.5 \\times err)`
       - Clamp :math:`S_{dy}` to :math:`[S_{d,0}, S_{d,peak}]`.
    5. Clamp :math:`S_{ay}` to at least :math:`S_{a,1}` (avoid zero
       yield acceleration).
    6. If the converged yield is ≥ 90 % of the peak displacement, the
       curve is essentially linear — reset to the peak (ductility
       :math:`\\mu = 1`).

    **When to use**: structures with gradual yielding or no single
    stiffness-change event — e.g. ductile RC moment frames, steel
    moment frames with gradual plastification.

    References:
        - ATC-40 (1996), *Seismic Evaluation and Retrofit of Concrete
          Buildings*, Applied Technology Council (Procedure B).
        - Eurocode 8 (2004), EN 1998-1, Annex B.
        - Faella, G., Giordano, A., & Mezzi, M. (2004). "Definition of
          Suitable Bilinear Pushover Curves in Nonlinear Static
          Analyses." *13th WCEE*, Paper 1626.

    Args:
        S_d_arr: Spectral displacements (m).  Should be monotonically
            increasing and non-negative.
        S_a_arr: Spectral accelerations (m/s²), corresponding to
            *S_d_arr*.  Should be non-negative.
        config: Optional dict with keys:

            * ``initial_guess`` — fraction of ``S_d_peak`` to use as
              the starting point for iteration (default ``0.3``).
              Range ``(0, 1)``.  Lower values converge from below
              (conservative), higher values from above.
            * ``tolerance`` — relative area error tolerance for
              convergence (default ``0.001``).  Smaller values give
              more precise area matching but more iterations.
            * ``max_iter`` — maximum number of iterations
              (default ``100``).  Prevents infinite loops.
            * ``peak_idx`` — force the peak index to this value
              (0-based).  If ``None`` (default), auto-detected as
              ``argmax(S_a_arr)``.

    Returns:
        Tuple ``(S_dy, S_ay, method_name)`` where *method_name* is
        always ``'equal_energy'``.

    Edge cases:
        - Fewer than 2 data points: returns
          ``(S_d_arr[0], S_a_arr[0], 'equal_energy')``.
        - ``peak_idx < 1``: same early return.
        - Converged yield ≥ 90 % of peak: reset to peak (elastic
          response — :math:`\\mu = 1`).
    """
    cfg = dict(config or {})
    initial_guess = cfg.get('initial_guess', 0.3)
    tolerance = cfg.get('tolerance', 0.001)
    max_iter = cfg.get('max_iter', 100)
    peak_idx = cfg.get('peak_idx')

    if peak_idx is None:
        peak_idx = int(np.argmax(S_a_arr))

    if peak_idx < 1 or len(S_d_arr) < 2:
        return float(S_d_arr[0]), float(S_a_arr[0]), 'equal_energy'

    S_d_peak = float(S_d_arr[peak_idx])
    S_a_peak = float(S_a_arr[peak_idx])

    # Initial elastic stiffness from first 20% of points
    n_el = max(3, len(S_d_arr) // 5)
    K_init = float(np.polyfit(S_d_arr[:n_el], S_a_arr[:n_el], 1)[0])

    area_actual = float(np.trapezoid(S_a_arr[:peak_idx + 1], S_d_arr[:peak_idx + 1]))

    S_dy = S_d_peak * initial_guess
    for _ in range(max_iter):
        S_ay = K_init * S_dy
        A1 = 0.5 * S_ay * S_dy
        A2 = S_ay * (S_d_peak - S_dy)
        A3 = 0.5 * (S_a_peak - S_ay) * (S_d_peak - S_dy)
        area_bilin = A1 + A2 + A3
        err = (area_bilin - area_actual) / max(area_actual, 1e-12)
        if abs(err) < tolerance:
            break
        S_dy *= (1.0 - err * 0.5)
        S_dy = max(float(S_d_arr[0]), min(S_dy, S_d_peak))

    S_ay = max(K_init * S_dy,
               float(S_a_arr[1]) if len(S_a_arr) > 1 else float(S_a_arr[0]))

    # Sanity: if yield is >90% of peak, the curve is essentially
    # linear — reset to peak (mu = 1).
    if S_dy >= 0.90 * S_d_peak:
        S_dy = S_d_peak
        S_ay = S_a_peak

    return S_dy, S_ay, 'equal_energy'


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
    peak_idx = cfg.get('peak_idx')
    if peak_idx is None:
        peak_idx = int(np.argmax(S_a_arr))
    cfg['peak_idx'] = peak_idx

    # Try stiffness-change first
    S_dy, S_ay, _ = bilinearize_stiffness_change(S_d_arr, S_a_arr, cfg)

    S_d_peak = float(S_d_arr[peak_idx])
    S_a_peak = float(S_a_arr[peak_idx])

    if S_dy >= 0.90 * S_d_peak:
        # No clear yield — fall back to equal-energy
        S_dy, S_ay, _ = bilinearize_equal_energy(S_d_arr, S_a_arr, cfg)
        method = 'composite_equal_energy'
    else:
        method = 'composite_stiffness_change'

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
    direction: str = 'X',
    g: Optional[float] = None,
    damping_ratio: float = 0.05,
    max_iter: int = 50,
    tol: float = 0.01,
    bilinearize_method: str = 'composite',
    bilinearize_config: Optional[Dict[str, Any]] = None,
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
        * ``'S_dy'``, ``'S_ay'`` — bilinear yield point.
        * ``'bilinearize_method'`` — name of the method actually used.
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

    # 2. Bilinearise the capacity spectrum (find yield point)
    _bilin_map = {
        'composite': bilinearize_composite,
        'stiffness_change': bilinearize_stiffness_change,
        'equal_energy': bilinearize_equal_energy,
    }
    _bilin_fn = _bilin_map.get(bilinearize_method, bilinearize_composite)
    S_dy, S_ay, _bilin_name = _bilin_fn(
        S_d_arr, S_a_arr, config=bilinearize_config)

    # 3. Capacity spectrum demand method (secant iteration)
    T_spec = np.array(spectrum_periods)
    Sa_spec = np.array(spectrum_accels)

    # First-mode elastic period from modal analysis
    modal_periods = modal_results.get('periods', [])
    best_mode_period = modal_periods[best_mode] if best_mode < len(modal_periods) else 1.0

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
        'bilinearize_method': _bilin_name,
        'Gamma': Gamma,
        'M_eff': M_eff,
        'capacity_adrs': {'S_a': S_a_arr.tolist(), 'S_d': S_d_arr.tolist()},
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
