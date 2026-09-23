---
title: "Modal Analysis Options"
description: "Modal analysis solver options, mode shape visualisation, and usage examples."
status: "complete"
tags: [analysis-type, modal, eigen, eigenvalue, solver]
category: [analysis-types]
related: [pushover_analysis.md, report_generation.md, storey_response.md]
---
# Modal Analysis Options

The method :meth:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder.run_modal_analysis`
supports the following ``eigen_solver`` modes:

| Value | Solver | Speed | Notes |
|---|---|---|---|
| ``"default"`` | ARPACK (implicitly restarted Lanczos) | Fast (~seconds) | Uses ARPACK's iterative Lanczos method. May fail with ``info=-9`` ("Starting vector is zero") when all DOFs are exactly zero. The builder automatically falls back to ``fullGenLapack``. |
| ``"genBandArpack"`` | ARPACK (generalised banded) | Fast (~seconds) | The **default** solver in OpenSees. More efficient than plain ARPACK for banded stiffness matrices. The builder applies a static gravity pre‑step that changes the current tangent stiffness before the eigen analysis. This is the recommended solver for most models. |
| ``"symmBandLapack"`` | Symmetric banded LAPACK | Fast | ❌ **Not suitable** — only solves standard eigenproblems (Aφ = λφ), not the generalised problem (Kφ = λMφ) needed for structural dynamics. Falls back to ARPACK → fullGenLapack. |
| ``"fullGenLapack"`` | LAPACK full eigenvalue solve | Very slow (~minutes–hours) | Computes **all** eigenvalues via LAPACK's dense solver. Robust but impractical for models with > 10 000 DOFs. Used as a fallback when ARPACK fails. |
| ``"ritz"`` | Gravity pre‑step + ARPACK | Fast (~seconds) | Runs a static gravity step under self‑weight **before** the eigen solve. This changes the current tangent stiffness (P‑delta effect from gravity) before the eigen analysis. Not true load‑dependent Ritz vectors — see note below. |

#### OpenSees Ritz vector support

OpenSees does **not** have a native ``ritz`` command in the standard distribution (it was never added to the Tcl interpreter). The ``"ritz"`` mode above applies a static gravity pre‑step that changes the current tangent stiffness (P‑delta effect from gravity) before the eigen solve via ARPACK. This is **not** a load‑dependent Ritz vector method — it simply runs the eigen analysis on the gravity‑loaded stiffness matrix rather than the unloaded one.

True Load-Dependent Ritz vectors (Krylov subspace: K⁻¹M applied repeatedly to a starting load pattern) can be generated manually by:
1. Applying a load pattern and solving static equilibrium
2. Forming the mass‑proportional load from the resulting displacements
3. Solving static equilibrium again for the next vector
4. Orthogonalising and iterating

This requires extracting system matrices (not directly available in OpenSeesPy) or running a sequence of static analyses with computed load vectors.

#### Built-in eigen solver options

OpenSees' ``eigen`` command accepts the following solver flags (per the official documentation at `<https://opensees.berkeley.edu/wiki/index.php?title=Eigen_Command>`_):

- ``-genBandArpack`` — generalised banded ARPACK (default, fast, recommended)
- ``-symmBandLapack`` — symmetric banded LAPACK (fast, but restricted to standard eigenproblems Aφ = λφ, not the generalised Kφ = λMφ needed for structural dynamics)
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

#### Over‑requesting modes

Asking for more modes than the model has free DOFs is safe.  ``ops.eigen(N)``
pads the result with an uninitialised sentinel (``DBL_MAX``, and OpenSees
itself warns ``mode <k> is out of range``); ``run_modal_analysis()`` filters
both non‑positive eigenvalues and those sentinels, so ``periods`` /
``eigenvalues`` contain only genuine modes and their length may be **less
than** the requested ``num_modes``.

Always derive downstream mode counts from the returned lists rather than the
requested value:

```python
modal = builder.run_modal_analysis(num_modes=12)   # may converge only 3
n_modes = len(modal["periods"])                     # ← use this
```

The sentinels matter beyond bookkeeping: their derived ω (≈1e154) overflows
the CQC correlation denominator, so passing them into a response‑spectrum
combination raises ``OverflowError: (34, 'Result too large')``.


### Mode shape visualisation

Mode shapes can be viewed interactively or saved as GIFs using the
``project_b_linear.py`` workflow script:

```bash
# Interactive PyVista window (runs modal analysis, fast if model cache exists)
python3 local/project_b_linear.py --cache --shapes --animate --mode-index 4

# Same, but cycle through all 32 modes (no --mode-index)
python3 local/project_b_linear.py --cache --shapes --animate

# Load from previously cached results (no analysis) — interactive window
python3 local/project_b_linear.py --from-cache --mode-index 4

# Save animated GIFs from cache (no analysis, no window)
python3 local/project_b_linear.py --from-cache --gif --mode-index 4
```

The mode index is **0‑based** (``--mode-index 4`` displays the 5th mode).

Requirements:
- ``pyvista`` — ``pip install pyvista`` (for interactive viewing)
- ``imageio`` — ``pip install imageio`` (for GIF export)

#### Usage from code

```python
# After running modal analysis with extract_shapes=True
shapes = builder.extract_mode_shapes(num_modes)

from fea_toolkit.plotting import mass_participation_ratios, plot_mode_animation

# Animate mode 4 (0‑based) interactively.  The period and the six-DOF mass
# participation are read from OpenSees's modalProperties() output.
plot_mode_animation(
    builder, shapes, mode=4,
    scale=5.0, animate=True,
    periods=modal_result["periods"],
    participation=mass_participation_ratios(modal_result["modal_props"]),
)

# Static (non‑animated) display with section‑coloured shells
plot_mode_animation(
    builder, shapes, mode=4,
    scale=5.0, animate=False, periods=modal_result["periods"],
)
```

From the command line (modes are **1‑based**, so ``--mode 1`` is the first mode):

```bash
python examples/view_model.py results.npz --result modal --mode 1
```

**Choosing the input: archive vs model file**

| Input | What happens | Participation rows shown |
|---|---|---|
| ``results.npz`` | Reads the archived ``modal/*`` block — no solver run | Whatever the archive holds.  Archives written before ``modal/{rx,ry,rz}_ratio`` existed show the **X/Y/Z row only** |
| ``model.s2k`` | Parses the SAP2000 model and runs the modal analysis **live**, so ``ops.modalProperties()`` is called in-process | **Always all six DOF** |

So to see the full six-DOF annotation for a model whose archive predates
the rotational keys, view the model file instead of the archive:

```bash
python examples/view_model.py model.s2k --result modal --mode 1
```

Add ``--num-modes N`` when the mode number you want exceeds the default of 12.
This costs one modal solve (seconds on a ~750-node model), whereas the archive
path is instant.

**On-plot annotation — period and six-DOF mass participation**

The title shows the mode number and its natural period, and a second text block
lists the six mass-participation ratios as percentages:

```
Mode 1    T = 0.3552 s                      Mass participation (%):
                                              X   0.03%    Y  25.30%    Z   0.00%
                                             RX   1.50%   RY   3.50%   RZ   5.50%
```

Both values come **straight from OpenSees**.  ``run_modal_analysis()`` stores the
``ops.modalProperties("-return", "-unorm")`` dict as ``modal_result["modal_props"]``,
and ``mass_participation_ratios()`` reads the six ``partiMassRatiosMX/MY/MZ`` and
``partiMassRatiosRMX/RMY/RMZ`` entries from it — the toolkit never recomputes
participation factors or effective modal masses.

For an NPZ source the same values are read from the archive, where
``npz_writer`` stored them verbatim as ``modal/{mx,my,mz,rx,ry,rz}_ratio`` (see
``docs/results_schema.md``).  Archives written before the rotational keys
existed omit ``modal/{rx,ry,rz}_ratio``; those DOFs are then shown as ``--``
rather than a misleading ``0.00%``.

**Amplitude and `scale`**

``scale`` is the mode-shape exaggeration expressed as a **percentage of the
model's largest bounding-box dimension** (default ``5`` = 5%).

The shape is normalised to unit peak before scaling, and that normalisation is
essential.  ``ops.nodeEigenvector`` returns *mass-normalised* eigenvectors
(:math:`\phi^T M \phi = 1`), so their components are **not displacements** —
the magnitude depends on the modal mass, and a local mode with a small modal
mass has far larger components than a global sway mode.  Scaling the raw
components by a fixed factor therefore looks reasonable for the global modes
and explodes for the local ones.  On the benchmark model, for example,
the peak component is 0.23 for modes 1–3 but 4.50 for mode 4 — a ~20× spread —
so a fixed factor of 30 displayed a **135 m** peak displacement on a 78 m
model.  After normalisation every mode shows the same peak (5% of span
= 3.9 m).

This is the same convention the Rhino deformed overlay uses — see
``fea_toolkit.rhino.results``, which auto-scales to 5% of the model span.

**Animation and rendering**

``_add_animation_timer`` registers a repeating timer and returns whether
PyVista's own timer took it.  PyVista's ``Timer`` calls ``Render()`` after
every tick, so on that path the callback must not render.  The low-level VTK
fallback observer never renders, so ``plot_mode_animation`` calls
``plotter.render()`` itself only on that path — otherwise the mesh geometry is
updated in memory but never repainted, and the animation appears frozen until
the user clicks or drags in the window.

The timer keyword is ``duration`` (not ``interval``), and the callback is
passed a single ``step`` argument — see the verified upstream contract and
the history of this bug in ``docs/dev_notes.md`` → *PyVista animation timer*.

The ``project_b_linear.py`` pipeline also supports a ``--solver`` flag for
selecting the eigenvalue solver used during modal analysis:

```bash
python3 local/project_b_linear.py --cache --shapes --animate --solver fullGenLapack
```
