---
title: "CSM Bilinearization"
description: "Bilinearisation methods for capacity curves (stiffness-change, energy-based, R-μ-T) for the Capacity Spectrum Method per ATC-40 and Eurocode 8."
status: "complete"
tags: [csm, bilinearization, capacity-spectrum, pushover, yield-point]
category: [analysis-types]
related: [pushover_analysis.md, modal_analysis.md, workflow.md]
---

# Bilinearisation of Capacity Curves

## Overview

Bilinearisation converts a nonlinear pushover capacity curve into an
idealised elastic-plastic (or elastic-hardening) bilinear curve.  This
idealisation is a prerequisite for the Capacity Spectrum Method (CSM)
per ATC-40 and Eurocode 8, as well as for the N2 method and other
displacement-based assessment procedures.

The bilinear curve is defined by:

- **Yield point** `(S_dy, S_ay)` — the onset of yielding.
- **Initial elastic stiffness** `K_init` — slope of the elastic
  branch.
- **Post-elastic stiffness** (implicit in the yield-to-peak geometry).

The choice of bilinearisation method significantly affects the computed
yield point, which cascades into the ductility demand and ultimately
the performance point.

## Methods

### 1. Stiffness-Change Detection

**`bilinearize_stiffness_change()`**

Yield is detected where the secant stiffness drops below a fraction of
the initial elastic stiffness (Criterion A), or where a single step
shows a large relative drop (Criterion B).

| Config | Default | Effect |
|---|---|---|
| `threshold` | 0.50 | Fraction of K_init for Criterion A |
| `min_relative_drop` | -0.30 | Single-step drop threshold for Criterion B |
| `peak_idx` | auto | Force peak index |

**When to use**: Braced frames, buckling-critical members, structures
with a clear stiffness discontinuity.

**Limitations**: On purely elastic or gradually hardening curves, no
stiffness drop is detected, and the method falls back to the peak
(i.e., no yield — ductility = 1).

---

### 2. Equal-Energy (ATC-40 / EC8)

**`bilinearize_equal_energy()`**

Finds the yield point that preserves the area under the capacity curve
up to the peak.  Iterative Newton-style relaxation from an initial
guess.

| Config | Default | Effect |
|---|---|---|
| `initial_guess` | 0.3 | Fraction of S_d_peak for start |
| `tolerance` | 0.001 | Relative area error tolerance |
| `max_iter` | 100 | Maximum iterations |
| `peak_idx` | auto | Force peak index |

**Algorithm** (per iteration):
1. Compute `S_ay = K_init * S_dy`
2. Compute bilinear area:
   `A_bilin = 0.5 * S_ay * S_dy + S_ay * (S_d_peak - S_dy) + 0.5 * (S_a_peak - S_ay) * (S_d_peak - S_dy)`
3. Relative error `err = (A_bilin - A_actual) / A_actual`
4. Update: `S_dy *= (1.0 - 0.5 * err)`, clamp to `[S_d[0], S_d_peak]`
5. If converged yield ≥ 90 % of peak → reset to peak (elastic case).

**When to use**: Ductile moment frames, RC structures with gradual
plastification, structures with no single stiffness-change event.

**References**: ATC-40 Procedure B, Eurocode 8 Annex B, N2 method.

---

### 3. Composite (Default)

**`bilinearize_composite()`**

A hybrid method that tries stiffness-change first and falls back to
equal-energy when no clear stiffness change is detected.  A final
sanity clamp ensures `S_dy ≥ 10 % of S_d_peak`.

| Config | Effect |
|---|---|
| Any key from sub-methods | Passed through to stiffness-change and/or equal-energy |

**Design rationale**:

1. **Stiffness-change preferred**: deterministic, non-iterative, works
   well for structures with clear yield.
2. **Equal-energy fallback**: handles curved backbones (RC, masonry)
   reliably.
3. **10 % clamp**: prevents pathologically low yield points.  This
   aligns with research by Vamvatsikos et al. (2013) showing that
   capturing initial stiffness via a low-percentage secant (10 %)
   reduces bias in ductility estimates.

| Source | Method | 10 % Rule |
|---|---|---|
| ATC-40 / EC8 | Equal-energy area balance | No |
| FEMA 356 | 60 % secant + area balance | No |
| Vamvatsikos (2013) | 10 % secant + area minimisation | **Yes** |
| **fea_toolkit composite** | Stiffness-change + equal-energy + clamp | **Yes** |

**When to use**: Default for automated workflows, mixed structural
types, batch processing.

## Comparison of Methods

| Aspect | stiffness_change | equal_energy | composite |
|---|---|---|---|
| **Iterative?** | No | Yes (≤ 100 iters) | Conditional |
| **Works on elastic curves?** | Returns peak | Returns 30 % guess or peak | Falls back to equal-energy |
| **Works on hardening curves?** | May find late yield | Preserves area | Uses stiffness-change |
| **Works on softening curves?** | Detects drop if before peak | Preserves area up to peak | Falls back to equal-energy |
| **Config keys** | threshold, min_relative_drop, peak_idx | initial_guess, tolerance, max_iter, peak_idx | All of the above |

## Edge Cases

| Case | Behaviour |
|---|---|
| **Empty arrays** | All methods return (0.0, 0.0, method_name) |
| **2 data points** | Peak at index 1 → secant stiffness computed, falls through to peak |
| **Negative S_a (numerical noise)** | Peak search via argmax still works; negative values skipped |
| **Yield ≥ 90% of peak** | Equal-energy resets to peak (mu=1); composite falls back |
| **Yield < 10% of peak** | Composite clamps to 10 % |

## References

1. **ATC-40** (1996). *Seismic Evaluation and Retrofit of Concrete
   Buildings*. Applied Technology Council, Redwood City, CA.

2. **Eurocode 8** (2004). EN 1998-1: *Design of Structures for
   Earthquake Resistance — Part 1: General Rules, Seismic Actions and
   Rules for Buildings*. CEN, Brussels.

3. **FEMA 356** (2000). *Prestandard and Commentary for the Seismic
   Rehabilitation of Buildings*. Federal Emergency Management Agency,
   Washington, DC.

4. **FEMA 440** (2005). *Improvement of Nonlinear Static Seismic
   Analysis Procedures*. Federal Emergency Management Agency,
   Washington, DC.

5. **Vamvatsikos, D., De Luca, F., & Iervolino, I.** (2013).
   "Near-optimal piecewise linear fits of static pushover capacity
   curves for equivalent SDOF analysis." *Earthquake Engineering &
   Structural Dynamics*, 42(4), 589–600. doi:10.1002/eqe.2225

6. **Faella, G., Giordano, A., & Mezzi, M.** (2004). "Definition of
   Suitable Bilinear Pushover Curves in Nonlinear Static Analyses."
   *13th World Conference on Earthquake Engineering*, Paper 1626.

## Performance Point (CSM)

The `compute_performance_point()` function in `csm.py` implements the
full ATC‑40 Capacity Spectrum Method with secant iteration:

1. **ADRS conversion** — pushover curve (V‑δ) → spectral acceleration
   vs. spectral displacement using the first-mode participation factor
   and effective modal mass.
2. **Bilinearization** — one of the three methods above (composite
   by default) determines the yield point (S_dy, S_ay).
3. **Secant iteration** — starting from 20 % of peak S_d, repeatedly:
   - Compute equivalent secant period T_eq from the trial point
   - Compute ductility μ and equivalent viscous damping β_eq
     (ATC‑40 Eqn 5‑19)
   - Compute damping reduction factor B (ATC‑40 / GB 50011 compatible)
   - Interpolate elastic demand spectrum at T_eq and reduce by B
     to obtain inelastic demand displacement
   - Check convergence (relative tolerance or 3‑iteration stall)
   - Weighted update of trial point (50 % demand, 50 % capacity)
4. **Elastic convergence** — if both trial and demand drop below the
   first capacity data point, the structure is elastic.  Performance
   point computed directly from best‑mode period and elastic spectrum.
5. **Returns** — S_dp, S_ap, V_base, D_roof, T_eq, μ, converged flag,
   iteration count, bilinear yield point (S_dy, S_ay).

### API

```python
from fea_toolkit.model.csm import compute_performance_point

pp = compute_performance_point(
    pushover_results, modal_results, mode_shapes,
    spectrum_periods, spectrum_accels,
    direction="X",
    damping_ratio=0.05,
    max_iter=50,
    tol=0.01,
    bilinearize_method="composite",
)
# S_dp is in model length units (e.g. m for a kN-m model, mm for a kN-mm model)
print(f"S_dp = {pp['S_dp']:.3f} (model length units), converged = {pp['converged']}")
```

**Status:** ✅ Fully implemented and wired into `report.py` via
`_run_pushover_with_csm()`.

---

## ADRS Unit Convention and Damping Reduction

This section documents the exact ADRS (Acceleration-Displacement Response
Spectrum) unit convention used by :func:`~fea_toolkit.model.csm.pushover_to_adrs`
and :func:`~fea_toolkit.model.csm.compute_performance_point`, together
with the equivalent-damping and damping-reduction machinery introduced
for the CSM workflow.

### 1. Spectral acceleration — Newton's second law, no `g`

The capacity curve is converted from base-shear vs. roof-displacement
(`V`–`δ`) to ADRS coordinates via:

```text
S_a = V / M_eff          # m/s²  (NOT V / (M_eff · g))
S_d = δ / (|Γ| · |φ_control|)
```

- `M_eff` is the first-mode effective modal mass (model mass units —
  tonnes for a kN-m model) and `V` is the base shear (kN).  Because
  `kN / t = m/s²` exactly, `S_a = V / M_eff` is **Newton's second law**
  and holds for **any** consistent F/L/T unit system — no gravitational
  constant `g` appears in the conversion.
- Earlier versions incorrectly used `S_a = V / (M_eff · g)`, which returns
  spectral acceleration in **g-units** while `compute_performance_point`
  treated it as `m/s²`.  That single mismatch inflated
  `T_eq = 2π√(S_d/S_a)` by `√g ≈ 3.13`, so the demand spectrum was
  interpolated at the wrong (longer) period and the secant iteration
  stalled on a phantom high-ductility point far from the true elastic
  intersection.  This is now fixed.

### 2. Origin anchoring

The capacity curve is anchored at the origin `(S_d = 0, S_a = 0)`.
Earlier code dropped the first data point with a `S_d > 1e-12` mask,
so the capacity curve did not start at `(0, 0)` and the initial-elastic
stiffness `K_init` was underestimated.

### 3. Equivalent viscous damping (ATC-40 Eqn 5-19)

```text
β_eq = β_0 + κ · (2/π) · (μ − 1) / (μ · (1 + αμ − α))
```

Implemented in :func:`~fea_toolkit.model.csm.compute_equivalent_damping`:

- `β_0` — elastic damping ratio (default 0.05).
- `κ` — damping adjustment factor (default 0.33).
- `μ` — ductility `S_dp / S_dy`.
- `α` — post-yield stiffness ratio `(S_a_peak − S_ay) / (S_d_peak − S_dy)`
  normalized by initial stiffness.

### 4. Damping reduction factor `B` (ATC-40 / GB 50011 compatible)

```text
B = √[(1 + 10·Δβ) / (1 + 5·Δβ)]        with  Δβ = β_eq − β_0
```

Implemented in :func:`~fea_toolkit.model.csm.damping_reduction_factor`
and clamped to `[0.5, 2.0]`.

The inelastic demand spectrum is obtained by dividing the elastic
spectrum by `B`:

```text
Sa_demand(T_eq) = Sa_elastic(T_eq) / B
Sd_demand       = Sa_demand(T_eq) · (T_eq / 2π)²
```

### 5. New result keys

:func:`~fea_toolkit.model.csm.compute_performance_point` now returns two
additional keys alongside the existing ``S_dp``, ``S_ap``, ``V_base``,
``D_roof``, ``T_eq``, ``mu``, ``converged``:

- ``beta_eq`` — equivalent viscous damping ratio at the performance point.
- ``B`` — spectral damping reduction factor.

For the bundled RC frame example at a demand scale of 13.0× the elastic
spectrum, the converged point is `μ = 3.00`, `T_eq = 0.574 s`,
`β_eq = 0.189`, `B = 1.188` (33 iterations).

**Status:** ✅ Fully implemented.  See `tests/test_workflows.py::TestCSMWorkflow`.

---

## See Also

- [Pushover (Non-linear Static) Analysis](pushover_analysis.md)
- [Builder Reference — Two-stage Pipeline](builder_reference.md)
- [Modal Analysis Options](modal_analysis.md)
- Source: `src/fea_toolkit/model/csm.py`
- Tests: `tests/test_model.py::TestBilinearization` (24 tests)