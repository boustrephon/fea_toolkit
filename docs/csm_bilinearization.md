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
| **fea_toolkit `rc`** | 10 % secant + equal-area (closed-form) | **Yes** |

**When to use**: Default for automated workflows, mixed structural
types, batch processing.

---

### 4. De Luca 10 %-Secant (RC / Curved Backbones)

**`bilinearize_rc()`** — registered under the method names **`'rc'`** and
**`'de_luca_10pct'`** in `compute_performance_point(..., bilinearize_method=...)`.

FEMA-273/356's 60 %-secant and EC8's equal-area fits are **biased for
curved reinforced-concrete backbones**: the fitted yield point snaps to
the cracking transition (a local stiffness change) rather than the
rebar-yield drift, which can overestimate displacement demand by ~25 %
(De Luca, Vamvatsikos & Iervolino 2013).

The method implements their near-optimal rule:

1. **Elastic secant** — the elastic branch is the secant from the origin
   to the point at `elastic_fraction` (default **10 %**) of the peak
   strength, instead of the FEMA 60 %-secant.
2. **Equal-area yield** — the yield point lies on that secant and the
   post-yield branch passes through the peak point; the yield
   displacement is the unique value that makes the area under the
   bilinear fit equal the capacity-curve area (minimising the absolute
   area discrepancy).

Because the yield point is constrained to the elastic secant line, the
equal-area equation is linear in `S_dy` and is solved **directly (no
iteration)**:

    S_dy = 2·(A_cap − ½·S_a_peak·S_d_peak) / (K_el·S_d_peak − S_a_peak)

| Config | Default | Effect |
|---|---|---|
| `elastic_fraction` | 0.10 | Fraction of peak strength for the elastic secant |
| `peak_idx` | auto | Force peak index |

**When to use**: RC frames/walls whose capacity curves soften gradually
(cracking → rebar yield → softening) without a sharp yield plateau.

**Real-benchmark validation (Gap 4 — 2026-08-24):** applied to the actual
Vecchio & Emara capacity curve (`tests/test_rc_benchmark.py::test_bilinearize_rc_real_curve`).
**Result: `S_dy ≈ 14 mm` (spectral displacement, model length units), `S_ay ≈ 2.01 m/s²`,
exact equal-area fit.**  The key claim holds — the yield does **not** snap to the
cracking transition (~2 mm spectral displacement).  The 0.5–1 % rebar-yield
threshold is a **roof-drift** band (roof displacement ÷ storey height), so it is
not compared directly against the spectral `S_dy`; `S_dy` is the equivalent-SDOF
spectral displacement produced by the ADRS modal transformation.  The curve keeps
hardening to the 155 mm end with no post-peak peak (see `docs/_pending_work.md` P5):
on a hardening-only backbone the equal-area constraint pushes the yield earlier, in
the conservative direction.  Re-validate the roof-drift band once the post-peak
descent gives the curve a real peak.

**References**: De Luca, F., Vamvatsikos, D., & Iervolino, I. (2013).
"Near-optimal piecewise linear fits of static pushover capacity curves."
*Earthquake Engineering & Structural Dynamics*, 42(4), 523–543.
doi:10.1002/eqe.2225

## Comparison of Methods

| Aspect | stiffness_change | equal_energy | composite | de_luca_10pct (`rc`) |
|---|---|---|---|---|
| **Iterative?** | No | Yes (≤ 100 iters) | Conditional | No (closed-form) |
| **Works on elastic curves?** | Returns peak | Returns 30 % guess or peak | Falls back to equal-energy | Returns peak (`de_luca_10pct_elastic`) |
| **Works on hardening curves?** | May find late yield | Preserves area | Uses stiffness-change | Preserves area (10 % secant) |
| **Works on softening curves?** | Detects drop if before peak | Preserves area up to peak | Falls back to equal-energy | Preserves area up to peak |
| **Curved RC backbones?** | Snaps to cracking | Snap-prone without 10 % secant | Fallback helps, still 60 %-biased | ✅ Designed for these |
| **Config keys** | threshold, min_relative_drop, peak_idx | initial_guess, tolerance, max_iter, peak_idx | All of the above | elastic_fraction, peak_idx |

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
   Structural Dynamics*, 42(4), 523–543. doi:10.1002/eqe.2225

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

## Mode Selection for the ADRS Transformation

The ADRS (Acceleration-Displacement Response Spectrum) transformation in
:func:`~fea_toolkit.model.csm.pushover_to_adrs` converts the physical
pushover curve (base shear V vs roof displacement δ) into spectral
coordinates using a **single equivalent-SDOF mode**:

```text
S_a = |V| / M_eff        # m/s²
S_d = |δ| / |Γ · φ_control|
```

where:

- `M*` (or `M_star`) = generalised modal mass = `Σ mᵢ φᵢ²` (summed over the **full 3D mode shape**)
- `L` = generalised participation factor = `Σ mᵢ φᵢ` (direction-specific projection)
- `M_eff` = **effective modal mass** = `L² / M*`
- `Γ` = participation factor = `L / M*`
- `φ_control` = mode-shape ordinate at the control/roof node

The denominator `|Γ · φ_control|` uses the absolute value of the
product so `S_d` is non-negative even when the participation factor
and the control-node mode-shape ordinate have opposite signs.

**Sign convention**: `pushover_to_adrs()` converts the signed base-shear
and roof-displacement arrays to **magnitudes** (`abs(V)`, `abs(δ)`) before
forming the nonnegative ADRS coordinates `S_a`, `S_d`.  The physical
push direction (`+X`, `-X`, `+Y`, `-Y`) is **not** embedded in these
scalars — it is retained in the caller's directional stack entry (e.g.
the ``"-X"`` key of a per-direction results dict) and in the signed
``control_disp`` / ``base_shear`` arrays returned alongside the ADRS
coordinates.  Directional stack entries therefore carry the sign;
ADRS coordinates are always nonnegative magnitudes.

### Which mode is selected?

For a push in direction *d*, the function picks the mode with the
**highest modal participation ratio in that direction** (`L² / M*`).
This is the standard ASCE 41 approach: the pushover lateral-load
pattern and the equivalent-SDOF scaling use the mode that most strongly
participates in the push direction.

### The ill-conditioned-mode trap (torsional fundamental modes)

A naive ``max(L² / M*)`` selection can fail on 3D buildings whose
fundamental mode is **torsional** (or otherwise barely participates in
the push direction).  The participation ratio `L² / M*` is
**scale-invariant** but numerically ill-conditioned when `M* ≈ 0`:
eigenvector-normalisation noise in a mode that barely displaces in the
push direction (e.g. a 0.3 %-participation torsional mode) makes
`L² / M*` spuriously large, so the naive max picks the wrong mode and
corrupts the S_a / S_d scaling of the entire CSM curve.

To prevent this, :func:`~fea_toolkit.model.csm.pushover_to_adrs`
performs a **two-pass selection**:

1. **Pass 1 — compute every mode's generalised modal mass.**  For each
   mode shape, compute `(L, M*)` for the push direction and record the
   maximum generalised modal mass `M*_max` across all modes.
2. **Pass 2 — reject low-participation modes.**  Any mode whose
   push-direction generalised modal mass `M*` is below
   `1 % of M*_max` is excluded *before* evaluating the participation
   ratio.  Then the mode with the highest `L² / M*` among the
   survivors is selected.

**When is this filter active?**  The threshold compares `M*` against
`M*_max` **within the same eigenvector normalization**, so it is only
meaningful when all mode shapes share a common normalization.  With
**mass-normalised eigenvectors** — the standard output of
:meth:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder.extract_mode_shapes` —
 ``M* = 1`` for every mode and the filter passes all modes trivially.
 Selection then reduces to the largest ``L² / M* = L²`` — i.e. the
 largest ``abs(L) = |Σ mᵢ φᵢ|`` in the push direction, since the
 squared ratio is sign-invariant.  This favours a translational sway
 mode over a torsional mode (whose directional components largely
 cancel in the sum).  The filter is therefore a **defensive measure for
non-mass-normalised mode shapes** (e.g. hand-scaled shapes from an
external source) where a mode with `M* ≈ 0` could otherwise produce a
spuriously large `L² / M*` and out-rank the true sway mode.

### Lateral-torsional coupling: known limitations

CSM assumes the response is controlled by a **single translational
mode in the push direction** (the fundamental mode).  For
unsymmetric-plan buildings this assumption degrades or fails depending
on the degree of lateral-torsional coupling.  Following the
classification of Chopra & Goel (2004), *"A modal pushover analysis
procedure to estimate seismic demands for unsymmetric-plan buildings"*,
*Earthquake Engineering & Structural Dynamics* **33**, 903–927:

| System | Mode 1 | Mode 2 | CSM suitability |
|---|---|---|---|
| Torsionally-stiff (TS) | Lateral-dominant | Torsional-dominant | ✓ Adequate |
| Torsionally-similarly-stiff (TSS) | Strongly coupled | Strongly coupled | ✗ Degrades |
| Torsionally-flexible (TF) | Torsional-dominant | Lateral-dominant | ⚠ First mode unusable |

 - **Torsionally-flexible (TF)** — the first mode is torsional, so a
   naive "first-mode" ADRS conversion would be corrupt.  The two-pass
   filter above may select a lateral mode instead, but this is only a
   **heuristic** based on the highest surviving ``L² / M*``: the 1 %
   ``M*`` threshold does not itself identify lateral versus torsional
   modes, so the non-fundamental-mode choice should be validated before
   being relied upon.  This is the situation in the Admin Building
   test model.
- **Torsionally-similarly-stiff (TSS)** — the first two (or more)
  modes have **closely-spaced periods and strongly coupled
  lateral-torsional motion**: both modes carry comparable lateral *and*
  torsional components centred on a combined period.  No single
  translational mode exists, so the single-mode CSM assumption breaks
  down — results "deteriorate for a torsionally-similarly-stiff
  unsymmetric-plan system" (Chopra & Goel 2004).  A later study of 96
  steel moment-resisting-frame buildings reached the same conclusion:
  CSM is unreliable for buildings with dominant lateral-torsional
  modes of vibration.  For such buildings use Modal Pushover Analysis
  (MPA, combining per-mode pushovers by the CQC rule) or nonlinear
  response history analysis instead.

**Practical detection**: the ``partiMassRatiosMX`` / ``partiMassRatiosMY``
arrays returned by ``run_modal_analysis()`` give the directional
mass-participation of each mode.  If the push-direction participation
is low across the first few modes (< ~10 %), the structure may be
torsionally dominated — reconsider whether CSM is appropriate or
whether more modes / a multi-mode pushover are needed.  If two
adjacent modes have both similar periods and similar directional
participation, they are likely coupled lateral-torsional modes (TSS)
and CSM should not be relied upon.

The function raises a `ValueError` when a valid mode cannot be found.
This can happen in two ways:

1. *Every* mode is rejected by the participation filter (no mode
   carries meaningful generalised modal mass in the push direction —
   e.g. too few modes requested), or
2. the selected best mode has a non-positive `M*` (zero or NaN) in the
   push direction, indicating missing nodal masses or a degenerate mode
   shape.

On failure, the function returns **no** `S_a`, `S_d`, or `T_eq` —
these quantities are meaningless without a physical equivalent mode.
A unit-value fallback (`Γ = 1.0`, `M_eff = 1.0`) is **not** used, since
`M_eff` would be dimensionless (1.0) rather than a mass and the
resulting ADRS coordinates would be invalid CSM quantities.

### Why this matters for the CSM results

The yield point (`S_ay`, `S_dy`) and performance point (`S_ap`, `S_dp`)
computed by :func:`~fea_toolkit.model.csm.compute_performance_point`
are entirely downstream of the ADRS coordinate system.  If
`pushover_to_adrs` selects the wrong (torsional, ill-conditioned) mode,
the ductility, equivalent damping, and the intersection with the
demand spectrum are all computed in corrupted coordinates.  The
two-pass filter is intended to ensure a building with a torsional
fundamental mode (or with slight plan irregularity) still converts
its X/Y pushover curves using the genuine X/Y sway modes.  In
practice the filter may still accept a weakly participating mode
when the requested direction has no strongly participating sway
mode (e.g. too few modes); downstream validation of the resulting
capacity curve remains the caller's responsibility.

**References:**

- ASCE 41-17 (2017). *Seismic Evaluation and Retrofit of Existing
  Buildings*. American Society of Civil Engineers, Reston, VA.
- Applied Technology Council (1996). *ATC-40 — Seismic Evaluation and
  Retrofit of Concrete Buildings*. Redwood City, CA.
- Fajfar, P. (2000). "A Nonlinear Analysis Method for Performance-Based
  Seismic Design." *Earthquake Spectra*, 16(3), 573–592.
  doi:10.1193/1.1586128

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