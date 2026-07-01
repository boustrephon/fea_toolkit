### Modal analysis options

The method :meth:`~fea_toolkit.opensees.builder.OpenSeesBuilder.run_modal_analysis`
supports three ``eigen_solver`` modes:

| Value | Solver | Speed | Notes |
|---|---|---|---|
| ``"default"`` | ARPACK (implicitly restarted Lanczos) | Fast (~seconds) | Uses ARPACK's iterative Lanczos method. May fail with ``info=-9`` ("Starting vector is zero") when all DOFs are exactly zero. The builder automatically falls back to ``fullGenLapack``. |
| ``"fullGenLapack"`` | LAPACK full eigenvalue solve | Very slow (~minutes–hours) | Computes **all** eigenvalues of the system via LAPACK's dense solver. Robust but impractical for models with > 10 000 DOFs. Used as a fallback when ARPACK fails. |
| ``"ritz"`` | Load‑Dependent Ritz vectors + ARPACK | Fast (~seconds) | Runs a static gravity step under self‑weight **before** the eigen solve. The deformed shape seeds ARPACK's starting vector, giving vectors that better capture the dynamic response to lateral loads. Same eigenvalue accuracy as ``"default"`` but with a Ritz‑type starting vector. |

#### Usage

```python
# Standard ARPACK (default)
modal = builder.run_modal_analysis(num_modes=6, eigen_solver="default")

# Full LAPACK (robust, slow)
modal = builder.run_modal_analysis(num_modes=6, eigen_solver="fullGenLapack")

# Ritz vectors (gravity pre‑step)
modal = builder.run_modal_analysis(num_modes=6, eigen_solver="ritz")
```

#### Gravitational acceleration

The ``g`` parameter controls the value used for mass computation. Set it
explicitly, or leave as ``None`` to auto‑detect from the model's length unit:

```python
# Auto-detect from model units (SI: 9.80665 m/s²)
modal = builder.run_modal_analysis(g=None)

# Explicit value (for mm units: 9806.65 mm/s²)
modal = builder.run_modal_analysis(g=9806.65)
```

See :func:`~fea_toolkit.utils.g_from_units` for supported units.
