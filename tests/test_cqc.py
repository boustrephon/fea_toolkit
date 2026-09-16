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


@pytest.mark.filterwarnings("ignore:invalid value encountered in divide")
def test_rho_matrix_diagonal_survives_non_finite_omega():
    """A non-finite ω must not zero that mode's self-correlation.

    ``ω = inf`` (a sentinel for a mode whose frequency is unusable) makes the
    diagonal ratio ``inf / inf`` NaN.  The NaN sanitising step used to zero
    that entry along with the genuinely uncorrelated cross terms, so ``ρ_ii``
    became 0 and the mode dropped silently out of the CQC sum.  The diagonal
    is restored to 1 after sanitising, while the non-finite cross terms stay
    at 0 rather than NaN.
    """
    rho = cqc_rho_matrix([float("inf"), 2.0], [0.05, 0.05])
    assert np.all(np.isfinite(rho))
    assert rho[0, 0] == pytest.approx(1.0)
    assert rho[1, 1] == pytest.approx(1.0)
    assert rho[0, 1] == 0.0
    assert rho[1, 0] == 0.0


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
