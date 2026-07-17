### Modal analysis options

The method :meth:`~fea_toolkit.opensees.builder.OpenSeesBuilder.run_modal_analysis`
supports the following ``eigen_solver`` modes:

| Value | Solver | Speed | Notes |
|---|---|---|---|
| ``"default"`` | ARPACK (implicitly restarted Lanczos) | Fast (~seconds) | Uses ARPACK's iterative Lanczos method. May fail with ``info=-9`` ("Starting vector is zero") when all DOFs are exactly zero. The builder automatically falls back to ``fullGenLapack``. |
| ``"genBandArpack"`` | ARPACK (generalised banded) | Fast (~seconds) | The **default** solver in OpenSees. More efficient than plain ARPACK for banded stiffness matrices. **Requires a non‑zero starting vector** — the builder applies a Ritz gravity pre‑step (static gravity) before calling the solver. This is the recommended solver for most models. |
| ``"symmBandLapack"`` | Symmetric banded LAPACK | Fast | ❌ **Not suitable** — only solves standard eigenproblems (Aφ = λφ), not the generalised problem (Kφ = λMφ) needed for structural dynamics. Falls back to ARPACK → fullGenLapack. |
| ``"fullGenLapack"`` | LAPACK full eigenvalue solve | Very slow (~minutes–hours) | Computes **all** eigenvalues via LAPACK's dense solver. Robust but impractical for models with > 10 000 DOFs. Used as a fallback when ARPACK fails. |
| ``"ritz"`` | Gravity pre‑step + ARPACK | Fast (~seconds) | Runs a static gravity step under self‑weight **before** the eigen solve. The deformed shape seeds ARPACK's starting vector, giving vectors that better capture lateral‑load response. Same eigenvalue accuracy as ``"default"`` but with a Ritz‑type starting vector. |

#### OpenSees Ritz vector support

OpenSees does **not** have a native ``ritz`` command in the standard distribution (it was never added to the Tcl interpreter). The ``"ritz"`` mode above is the closest approximation — it applies the static gravity displacement as ARPACK's starting vector, which biases convergence toward modes that participate in the gravity response.

True Load-Dependent Ritz vectors (Krylov subspace: K⁻¹M applied repeatedly to a starting load pattern) can be generated manually by:
1. Applying a load pattern and solving static equilibrium
2. Forming the mass‑proportional load from the resulting displacements
3. Solving static equilibrium again for the next vector
4. Orthogonalising and iterating

This requires extracting system matrices (not directly available in OpenSeesPy) or running a sequence of static analyses with computed load vectors.

#### Built-in eigen solver options

OpenSees' ``eigen`` command accepts two solver flags (per the official documentation at `<https://opensees.berkeley.edu/wiki/index.php?title=Eigen_Command>`_):

- ``-genBandArpack`` — generalised banded ARPACK (default, fast, recommended)
- ``-fullGenLapack`` — dense LAPACK (robust, very slow for large models)

No ``-load`` or Ritz-specific option exists in the standard distribution.

#### Usage

```python
# Standard ARPACK (default)
modal = builder.run_modal_analysis(num_modes=6, eigen_solver="default")

# Generalised banded ARPACK (recommended, with Ritz pre-step)
modal = builder.run_modal_analysis(num_modes=6, eigen_solver="genBandArpack")

# Full LAPACK (robust, slow)
modal = builder.run_modal_analysis(num_modes=6, eigen_solver="fullGenLapack")

# Ritz vectors (gravity pre‑step)
modal = builder.run_modal_analysis(num_modes=6, eigen_solver="ritz")

# Symmetric banded LAPACK (standard eigenproblem only — not recommended)
modal = builder.run_modal_analysis(num_modes=6, eigen_solver="symmBandLapack")
```

#### Gravitational acceleration

The ``g`` parameter controls the value used for mass computation. Set it
explicitly, or leave as ``None`` to auto‑detect from the model's length unit:

```python
# Auto-detect from model units (SI: 9.80665 m/s²)
modal = builder.run_modal_analysis(num_modes=6, g=None)

# Explicit value (for mm units: 9806.65 mm/s²)
modal = builder.run_modal_analysis(num_modes=6, g=9806.65)
```

See :func:`~fea_toolkit.utils.g_from_units` for supported units.


### Mode shape visualisation

Mode shapes can be viewed interactively or saved as GIFs using the
``admin_linear.py`` workflow script:

```bash
# Interactive PyVista window (runs modal analysis, fast if model cache exists)
python3 local/admin_linear.py --cache --shapes --animate --mode-index 4

# Same, but cycle through all 32 modes (no --mode-index)
python3 local/admin_linear.py --cache --shapes --animate

# Load from previously cached results (no analysis) — interactive window
python3 local/admin_linear.py --from-cache --mode-index 4

# Save animated GIFs from cache (no analysis, no window)
python3 local/admin_linear.py --from-cache --gif --mode-index 4
```

The mode index is **0‑based** (``--mode-index 4`` displays the 5th mode).

Requirements:
- ``pyvista`` — ``pip install pyvista`` (for interactive viewing)
- ``imageio`` — ``pip install imageio`` (for GIF export)

#### Usage from code

```python
# After running modal analysis with extract_shapes=True
shapes = builder.extract_mode_shapes(num_modes)

from fea_toolkit.plotting import plot_mode_3d

# Animate mode 4 (0‑based) interactively
plot_mode_3d(
    builder, shapes, mode=4,
    scale=50.0, animate=True, periods=modal_result["periods"],
)

# Static (non‑animated) display with section‑coloured shells
plot_mode_3d(
    builder, shapes, mode=4,
    scale=50.0, animate=False, periods=modal_result["periods"],
)
```

The ``admin_linear.py`` pipeline also supports a ``--solver`` flag for
selecting the eigenvalue solver used during modal analysis:

```bash
python3 local/admin_linear.py --cache --shapes --animate --solver fullGenLapack
```
