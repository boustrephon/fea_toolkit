"""Tests for the CQC / SRSS combination kernels and their numpy vectorisation.

The vectorised helpers (:func:`cqc_rho_matrix`, :func:`cqc_combine_matrix`,
:func:`srss_combine_matrix`) exist purely as a performance path — they must
return the *same* numbers as the scalar :func:`cqc_combine` so that speeding
up the response-spectrum pass cannot silently change results.
"""

import numpy as np
import pytest

from fea_toolkit._cqc import (
    cqc_combine,
    cqc_combine_matrix,
    cqc_rho_matrix,
    srss_combine_matrix,
)

#: ``(omega, damping)`` pairs covering the interesting regimes.
MODAL_CASES = (
    ([1.0, 2.0, 3.0], [0.05, 0.05, 0.05]),  # well separated
    ([0.5, 0.51, 4.0], [0.05, 0.05, 0.05]),  # closely spaced modes (ρ → 1)
    ([1.0, 2.0, 3.0], [0.02, 0.05, 0.10]),  # mixed damping
    ([0.1, 1.0, 10.0, 100.0], [0.05] * 4),  # wide spread
    ([2.0], [0.05]),  # single mode
    ([], []),  # no modes
)


@pytest.mark.parametrize("omega,damp", MODAL_CASES)
def test_rho_matrix_diagonal_is_unity(omega, damp):
    """A mode is perfectly correlated with itself (ρ_ii = 1)."""
    rho = cqc_rho_matrix(omega, damp)
    assert rho.shape == (len(omega), len(omega))
    for i in range(len(omega)):
        assert rho[i, i] == pytest.approx(1.0)


def test_rho_matrix_is_symmetric_and_bounded():
    """ρ must be symmetric with entries in [0, 1]."""
    rho = cqc_rho_matrix([1.0, 1.5, 2.0, 5.0], [0.05] * 4)
    assert np.allclose(rho, rho.T), rho
    assert np.all(rho >= 0.0)
    assert np.all(rho <= 1.0 + 1e-12)


def test_rho_matrix_guards_extreme_ratio():
    """Sentinel / extreme frequencies must not produce NaN or overflow.

    A frequency ratio beyond ``_MAX_FREQ_RATIO`` is treated as uncorrelated
    (ρ = 0), mirroring the scalar short-circuit that protects the
    ``(1 - r²)²`` denominator.
    """
    rho = cqc_rho_matrix([1.0, 1.0e200], [0.05, 0.05])
    assert np.all(np.isfinite(rho))
    assert rho[0, 1] == 0.0
    assert rho[1, 0] == 0.0
    assert rho[0, 0] == pytest.approx(1.0)


def test_rho_matrix_non_finite_diagonal_matches_scalar():
    """A non-finite ω makes its diagonal ratio ``inf / inf`` → NaN.

    The scalar path skips a non-finite self-ratio (``continue``), so the mode
    contributes nothing to its own CQC self-term.  The matrix path must drop
    that diagonal to 0 as well — force-preserving it at 1 would inflate the
    combined result relative to the scalar path it is meant to reproduce.  The
    non-finite cross terms also stay at 0 rather than NaN.
    """
    rho = cqc_rho_matrix([float("inf"), 2.0], [0.05, 0.05])
    assert np.all(np.isfinite(rho))
    assert rho[0, 0] == 0.0
    assert rho[1, 1] == pytest.approx(1.0)
    assert rho[0, 1] == 0.0
    assert rho[1, 0] == 0.0


def test_cqc_matrix_scalar_parity_with_inf_frequency():
    """Scalar and matrix paths agree when one mode has ``ω = inf``.

    With ``values = [10, 20]`` and ``omega = [inf, 2]`` the scalar path skips
    the whole mode-0 row (its ratios are non-finite), leaving only the finite
    mode's self-term ``20²`` → ``sqrt(400) = 20``.  The matrix path must return
    the same value, which requires dropping the ``inf / inf`` diagonal to 0.
    """
    values = [10.0, 20.0]
    omega = [float("inf"), 2.0]
    damp = [0.05, 0.05]

    scalar = cqc_combine(values, omega, damp)
    matrix = cqc_combine_matrix(np.array(values), cqc_rho_matrix(omega, damp))

    assert scalar == pytest.approx(20.0)
    assert matrix == pytest.approx(20.0)
    assert float(matrix) == pytest.approx(scalar)


def test_rho_matrix_handles_zero_frequency():
    """ω = 0 falls back to a 1.0 ratio, exactly as the scalar path does."""
    rho = cqc_rho_matrix([0.0, 0.0], [0.05, 0.05])
    assert np.all(np.isfinite(rho))
    assert rho[0, 1] == pytest.approx(1.0)


@pytest.mark.parametrize("omega,damp", MODAL_CASES)
def test_cqc_matrix_matches_scalar(omega, damp):
    """The vectorised CQC path reproduces ``cqc_combine`` element-for-element."""
    rng = np.random.default_rng(20240914)
    values = rng.normal(scale=100.0, size=(4, 3, len(omega)))
    rho = cqc_rho_matrix(omega, damp)
    got = cqc_combine_matrix(values, rho)

    assert got.shape == (4, 3)
    for e in range(values.shape[0]):
        for c in range(values.shape[1]):
            want = cqc_combine(list(values[e, c]), omega, damp)
            assert got[e, c] == pytest.approx(want, rel=1e-9, abs=1e-9)


@pytest.mark.parametrize("omega,damp", MODAL_CASES)
def test_srss_matrix_matches_scalar(omega, damp):
    """The vectorised SRSS path reproduces ``sqrt(sum(v²))``."""
    rng = np.random.default_rng(7)
    values = rng.normal(scale=100.0, size=(4, 3, len(omega)))
    got = srss_combine_matrix(values)

    assert got.shape == (4, 3)
    for e in range(values.shape[0]):
        for c in range(values.shape[1]):
            want = float(np.sqrt(np.sum(values[e, c] ** 2)))
            assert got[e, c] == pytest.approx(want, rel=1e-12, abs=1e-12)


def test_combine_matrix_shapes():
    """A 2-D input yields a 1-D result; a 3-D input yields a 2-D result."""
    omega = [1.0, 2.0, 3.0]
    rho = cqc_rho_matrix(omega, [0.05] * 3)
    assert cqc_combine_matrix(np.ones((5, 3)), rho).shape == (5,)
    assert cqc_combine_matrix(np.ones((5, 2, 3)), rho).shape == (5, 2)
    assert srss_combine_matrix(np.ones((5, 2, 3))).shape == (5, 2)


def test_combine_matrix_clips_negative_quadratic():
    """A negative quadratic sum (possible with anti-correlated modes) clips to 0."""
    omega = [1.0, 1.0001]
    rho = cqc_rho_matrix(omega, [0.05, 0.05])
    assert cqc_combine_matrix(np.array([[1.0, -1.0]]), rho)[0] >= 0.0


# ═══════════════════════════════════════════════════════════════════
# Scalar kernel edge cases + ``fea_toolkit.utils`` facade re-export
# ═══════════════════════════════════════════════════════════════════


def test_scalar_combine_guards_sentinel_frequency():
    """A sentinel-scale ω must not overflow the scalar ρ denominator.

    ``ops.eigen(N)`` on a model with fewer free DOFs than requested pads the
    eigenvalues with DBL_MAX, whose derived ω (~1e154) used to raise
    ``OverflowError`` in the ``(1 - bij**2)**2`` term.  Such pairs are
    uncorrelated (ρ ~ bij**-5) and are skipped, leaving the SRSS of the two
    diagonal contributions.
    """
    result = cqc_combine([100.0, 50.0], [1.3e154, 30.0], [0.05, 0.05])
    assert np.isfinite(result)
    assert result == pytest.approx(np.sqrt(100.0**2 + 50.0**2), abs=0.1)


def test_scalar_combine_zero_omega_is_finite():
    """A zero ω (skipped mode) contributes nothing and must not divide by 0."""
    result = cqc_combine([0.0, 50.0], [0.0, 30.0], [0.05, 0.05])
    assert np.isfinite(result)
    assert result == pytest.approx(50.0)


def test_utils_facade_reexports_scalar_kernel():
    """``fea_toolkit.utils.cqc_combine`` is the same object as the kernel."""
    from fea_toolkit.utils import cqc_combine as facade

    assert facade is cqc_combine
