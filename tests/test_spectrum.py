"""Tests for fea_toolkit.spectrum — response-spectrum carriers and code spectra.

Covers :class:`~fea_toolkit.spectrum.ResponseSpectrum`, the GB 50011 and
IEC 62271-207 spectrum builders, unit-aware gravity resolution, and the
seismic-spectrum plot hook.
"""

import math

import numpy as np
import pytest

from fea_toolkit.spectrum import (
    ResponseSpectrum,
    _build_spectrum,
    _gb50011_spectrum,
    _iec_spectrum,
    _interp_sa,
    _resolve_g,
)
from fea_toolkit.utils import DEFAULT_GRAVITY_MS2, g_from_units

# ── ResponseSpectrum tests ─────────────────────────────────────────────


class TestResponseSpectrum:
    """The canonical T/Sa demand-spectrum carrier for pushover CSM."""

    def test_from_arrays_roundtrip(self):
        s = ResponseSpectrum.from_arrays(
            T=[0.0, 0.5, 1.0],
            Sa=[2.0, 1.5, 0.8],
            code="ASCE7-16",
            description="site-specific",
        )
        assert s.code == "ASCE7-16"
        assert s.description == "site-specific"
        assert s.T == [0.0, 0.5, 1.0]
        assert s.Sa == [2.0, 1.5, 0.8]

    def test_interpolate(self):
        s = ResponseSpectrum.from_arrays(T=[0.0, 1.0, 2.0], Sa=[1.0, 2.0, 3.0])
        vals = s.interpolate([0.0, 0.5, 1.0, 2.0])
        assert np.allclose(vals, [1.0, 1.5, 2.0, 3.0])

    def test_rejects_nan_period(self):
        """NaN T ordinate is rejected (NaN comparisons otherwise pass the
        strictly-increasing check silently)."""
        with pytest.raises(ValueError):
            ResponseSpectrum.from_arrays(T=[0.0, float("nan")], Sa=[1.0, 2.0])

    def test_from_gb50011_ascending_branch(self):
        """T=0.05 uses the damping-corrected branch 0.45 + (η₂ − 0.45)·10·T."""
        s = ResponseSpectrum.from_gb50011(alpha_max=0.5, tg=0.35, zeta=0.05)
        assert s.code == "GB50011"
        assert len(s.T) == 200
        assert len(s.Sa) == 200
        # At 5% damping η₂ = 1.0; T=0.05 on the ascending branch:
        # (0.45 + (1.0 − 0.45)·10·0.05) · α_max · g = 0.725 · 0.5 · 9.80665
        sa_at_005 = s.interpolate([0.05])[0]
        expected = (0.45 + (1.0 - 0.45) * 10.0 * 0.05) * 0.5 * DEFAULT_GRAVITY_MS2
        np.testing.assert_allclose(sa_at_005, expected, rtol=1e-10)

    def test_from_gb50011_plateau(self):
        """At T=tg the spectrum returns η₂ × α_max × g."""
        s = ResponseSpectrum.from_gb50011(alpha_max=0.5, tg=0.35, zeta=0.05)
        assert s.interpolate([0.35])[0] > s.interpolate([0.05])[0]

    def test_validation_mismatched_lengths(self):
        with pytest.raises(ValueError):
            ResponseSpectrum(T=[0.0, 0.5], Sa=[1.0])

    def test_validation_empty(self):
        with pytest.raises(ValueError):
            ResponseSpectrum(T=[], Sa=[])


# ── Spectrum tests ─────────────────────────────────────────────────────


def test_gb50011_spectrum_zero_period():
    """At T=0, the spectrum should return 0.45 × α_max × g."""
    Sa = _gb50011_spectrum([0.0], alpha_max=0.5, tg=0.35)
    expected = 0.45 * 0.5 * DEFAULT_GRAVITY_MS2
    assert abs(Sa[0] - expected) < 1e-10, f"{Sa[0]} != {expected}"


def test_gb50011_spectrum_plateau():
    """At T=tg, the spectrum should return η₂ × α_max × g."""
    Sa = _gb50011_spectrum([0.35], alpha_max=0.5, tg=0.35, eta2=1.0)
    expected = 1.0 * 0.5 * DEFAULT_GRAVITY_MS2
    assert abs(Sa[0] - expected) < 1e-10, f"{Sa[0]} != {expected}"


def test_gb50011_spectrum_descending():
    """At T=5*tg, the spectrum should be on the descending branch."""
    Sa = _gb50011_spectrum([1.75], alpha_max=0.5, tg=0.35)
    # Should be less than plateau value
    plateau = 1.0 * 0.5 * DEFAULT_GRAVITY_MS2
    assert Sa[0] < plateau, f"{Sa[0]} not less than plateau {plateau}"


def test_build_spectrum_defaults():
    """_build_spectrum with minimal config should return a reasonable spectrum."""
    cfg = {
        "intensity": 7,
        "acceleration": 0.10,
        "site_class": "I1",
        "level": "rare",
        "damping": 0.05,
    }
    T, Sa, amax, tg, zeta, label = _build_spectrum(cfg)
    assert len(T) == 300
    assert len(Sa) == 300
    assert amax == 0.50  # rare for VII
    assert tg == 0.25  # I1
    assert zeta == 0.05
    assert "Rare" in label


def test_build_spectrum_frequent():
    """Frequent level should use the frequent alpha_max."""
    cfg = {
        "intensity": 8,
        "acceleration": 0.20,
        "site_class": "II",
        "level": "frequent",
        "damping": 0.03,
    }
    _, _, amax, _, _, label = _build_spectrum(cfg)
    assert amax == 0.16  # frequent for VIII
    assert "Frequent" in label


def test_build_spectrum_g_is_unit_aware():
    """An explicit model-unit g scales Sa exactly with the length unit."""
    cfg = {"intensity": 7, "level": "rare", "site_class": "II", "damping": 0.05}
    _, sa_m, *_ = _build_spectrum(cfg, g=g_from_units({"F": "N", "L": "m", "T": "C"}))
    _, sa_mm, *_ = _build_spectrum(cfg, g=g_from_units({"F": "N", "L": "mm", "T": "C"}))
    # A millimetre model's g is 1000x the metre value, so Sa scales by 1000.
    np.testing.assert_allclose(np.asarray(sa_mm), np.asarray(sa_m) * 1000.0, rtol=1e-12)
    assert g_from_units({"L": "mm"}) == pytest.approx(9806.65)


def test_build_spectrum_default_g_is_si():
    """Omitting g falls back to the shared SI gravity constant, not a literal."""
    cfg = {"intensity": 7, "level": "rare", "site_class": "II", "damping": 0.05}
    _, sa_default, *_ = _build_spectrum(cfg)
    _, sa_explicit, *_ = _build_spectrum(cfg, g=DEFAULT_GRAVITY_MS2)
    np.testing.assert_allclose(np.asarray(sa_default), np.asarray(sa_explicit), rtol=1e-12)
    # An explicitly provided g is preserved verbatim (no re-derivation).
    _, sa_9_81, *_ = _build_spectrum(cfg, g=9.81)
    ratio = np.asarray(sa_9_81) / np.asarray(sa_default)
    np.testing.assert_allclose(ratio, 9.81 / DEFAULT_GRAVITY_MS2, rtol=1e-12)


def test_build_spectrum_units_argument_is_used():
    """units= scales Sa into the model's unit system (mm → ×1000)."""
    cfg = {"intensity": 7, "level": "rare", "site_class": "II", "damping": 0.05}
    _, sa_si, *_ = _build_spectrum(cfg)
    _, sa_mm, *_ = _build_spectrum(cfg, units={"F": "N", "L": "mm", "T": "C"})
    np.testing.assert_allclose(np.asarray(sa_mm), np.asarray(sa_si) * 1000.0, rtol=1e-12)


def test_build_spectrum_units_matches_g_from_units():
    """units= is equivalent to g=g_from_units(units)."""
    cfg = {"intensity": 7, "level": "rare", "site_class": "II", "damping": 0.05}
    units = {"F": "N", "L": "mm", "T": "C"}
    _, sa_units, *_ = _build_spectrum(cfg, units=units)
    _, sa_g, *_ = _build_spectrum(cfg, g=g_from_units(units))
    np.testing.assert_allclose(np.asarray(sa_units), np.asarray(sa_g), rtol=1e-15)


def test_build_spectrum_explicit_g_beats_units():
    """An explicit g wins over units (silent precedence)."""
    cfg = {"intensity": 7, "level": "rare", "site_class": "II", "damping": 0.05}
    _, sa_both, *_ = _build_spectrum(cfg, g=9.81, units={"F": "N", "L": "mm", "T": "C"})
    _, sa_g_only, *_ = _build_spectrum(cfg, g=9.81)
    np.testing.assert_allclose(np.asarray(sa_both), np.asarray(sa_g_only), rtol=1e-15)


def test_resolve_g_precedence():
    """_resolve_g: explicit g → units dict → shared SI constant."""
    assert _resolve_g(None, None) == DEFAULT_GRAVITY_MS2
    assert _resolve_g(None, {"L": "mm"}) == pytest.approx(9806.65)
    assert _resolve_g(9.81, {"L": "mm"}) == 9.81


def test_from_gb50011_units_argument():
    """from_gb50011(units=...) yields model-unit ordinates."""
    units = {"F": "N", "L": "mm", "T": "C"}
    spec_mm = ResponseSpectrum.from_gb50011(alpha_max=0.5, tg=0.35, units=units)
    spec_g = ResponseSpectrum.from_gb50011(alpha_max=0.5, tg=0.35, g=g_from_units(units))
    np.testing.assert_allclose(np.asarray(spec_mm.Sa), np.asarray(spec_g.Sa), rtol=1e-15)
    spec_si = ResponseSpectrum.from_gb50011(alpha_max=0.5, tg=0.35)
    np.testing.assert_allclose(np.asarray(spec_mm.Sa), np.asarray(spec_si.Sa) * 1000.0, rtol=1e-12)


def test_plot_seismic_spectrum_threads_units():
    """plot_seismic_spectrum accepts g/units and labels the resolved unit."""
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from fea_toolkit.spectrum import plot_seismic_spectrum

    cfg = {"intensity": 7, "acceleration": 0.10, "site_class": "II", "damping": 0.05}
    fig = plot_seismic_spectrum(cfg, units={"F": "N", "L": "mm", "T": "C"})
    assert fig is not None
    ylabel = fig.axes[0].get_ylabel()
    plt.close(fig)
    assert "mm/s" in ylabel


def test_interp_sa():
    """Interpolation should return known values at input points."""
    T = [0.0, 0.5, 1.0]
    Sa = [0.0, 1.0, 2.0]
    result = _interp_sa([0.25, 0.75], T, Sa)
    expected = np.interp([0.25, 0.75], T, Sa)
    np.testing.assert_array_almost_equal(result, expected)


# ── IEC 62271-207 spectrum tests ───────────────────────────────────────


class TestIec62271:
    """IEC 62271-207 seismic response spectrum."""

    def test_zero_period_returns_pga(self):
        """At T=0 the spectrum returns the peak ground acceleration."""
        assert _iec_spectrum(0.0, pga=0.4) == pytest.approx(0.4)

    def test_plateau_branch(self):
        """On the plateau (1.0 ≤ f ≤ 8.0 Hz) Sa = pga · 2.5 · β."""
        zeta = 0.05
        beta = (3.21 - 0.68 * math.log(100.0 * zeta)) / 2.1156
        # T = 0.5 s → f = 2.0 Hz, deep in the plateau band.
        assert _iec_spectrum(0.5, pga=0.4, zeta=zeta) == pytest.approx(0.4 * 2.5 * beta)

    def test_rising_branch(self):
        """On the rising branch (f < 1.0 Hz) Sa = pga/0.25 · 0.572 · β · f."""
        zeta = 0.05
        beta = (3.21 - 0.68 * math.log(100.0 * zeta)) / 2.1156
        # T = 2.0 s → f = 0.5 Hz.
        assert _iec_spectrum(2.0, pga=0.4, zeta=zeta) == pytest.approx(
            0.4 / 0.25 * 0.572 * beta * 0.5
        )

    def test_falling_branch(self):
        """On the falling branch (8 < f ≤ 33 Hz) Sa is below the plateau."""
        zeta = 0.05
        beta = (3.21 - 0.68 * math.log(100.0 * zeta)) / 2.1156
        # T = 0.1 s → f = 10.0 Hz.
        sa = _iec_spectrum(0.1, pga=0.4, zeta=zeta)
        expected = 0.4 / 0.25 * ((6.6 * beta - 2.64) / 10.0 - 0.2 * beta + 0.33)
        assert sa == pytest.approx(expected)
        assert sa < 0.4 * 2.5 * beta

    def test_high_frequency_returns_pga(self):
        """Above 33 Hz the response is constant at pga."""
        # T = 0.01 s → f = 100 Hz.
        assert _iec_spectrum(0.01, pga=0.4) == pytest.approx(0.4)

    def test_vectorized(self):
        """Array input returns a per-point array of spectral accelerations."""
        T = np.array([0.0, 0.5, 2.0])
        Sa = _iec_spectrum(T, pga=0.4)
        assert isinstance(Sa, np.ndarray)
        assert Sa.shape == (3,)
        assert Sa[0] == pytest.approx(0.4)

    def test_invalid_zeta_rejected(self):
        """Non-positive damping is rejected (log(100·ζ) is undefined)."""
        with pytest.raises(ValueError):
            _iec_spectrum(0.5, pga=0.4, zeta=0.0)

    def test_from_factory(self):
        """from_iec62271 builds a canonical ResponseSpectrum."""
        s = ResponseSpectrum.from_iec62271(pga=0.4, zeta=0.05)
        assert s.code == "IEC62271-207"
        # 200 uniform grid points + 3 IEC branch-corner periods (1/33,
        # 1/8, 1/1.1 s) → 203 unique ordinates.
        assert len(s.T) == 203
        assert len(s.Sa) == 203
        assert s.Sa[0] == pytest.approx(0.4)
        # Plateau point must exceed the zero-period ordinate.
        assert s.interpolate([0.5])[0] > 0.4

    def test_factory_sampled_at_branch_periods(self):
        """from_iec62271 samples the exact IEC branch corners.

        The piecewise spectrum changes slope at 33, 8 and 1.1 Hz
        (T = 1/33, 1/8, 1/1.1 s).  These must appear as explicit
        ordinates so interpolation reproduces the corner values, not
        a linear blend across a branch transition.
        """
        s = ResponseSpectrum.from_iec62271(pga=0.4, zeta=0.05)
        for branch_T in (1.0 / 33.0, 1.0 / 8.0, 1.0 / 1.1):
            assert branch_T in s.T, f"Branch period T={branch_T} missing from IEC ordinates"

        # The corner value must equal _iec_spectrum evaluated exactly
        # at that period (not interpolated across it).
        zeta = 0.05
        beta = (3.21 - 0.68 * np.log(100.0 * zeta)) / 2.1156
        assert s.interpolate([1.0 / 1.1])[0] == pytest.approx(0.4 / 0.25 * 0.572 * beta * 1.1)
        assert s.interpolate([1.0 / 8.0])[0] == pytest.approx(0.4 * 2.5 * beta)
        # Falling branch at f = 33 Hz, where it transitions to the
        # constant-pga high-frequency region.
        assert s.interpolate([1.0 / 33.0])[0] == pytest.approx(
            0.4 / 0.25 * ((6.6 * beta - 2.64) / 33.0 - 0.2 * beta + 0.33)
        )

    def test_negative_pga_rejected(self):
        """_iec_spectrum rejects negative peak ground acceleration."""
        with pytest.raises(ValueError):
            _iec_spectrum(0.5, pga=-0.1)
        with pytest.raises(ValueError):
            ResponseSpectrum.from_iec62271(pga=-0.4)
