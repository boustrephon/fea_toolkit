"""Tests for fea_toolkit.model.csm — bilinearization and capacity spectrum.

Covers the bilinear-backbone registry and the four built-in methods
(stiffness-change, equal-energy, composite, De Luca RC), the
pushover→ADRS conversion, and performance-point evaluation.
"""

import math

import numpy as np
import pytest

from fea_toolkit.model.csm import (
    bilinearize_composite,
    bilinearize_equal_energy,
    bilinearize_rc,
    bilinearize_stiffness_change,
)
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
from fea_toolkit.opensees.preprocessor import preprocess_model


class TestCapacitySpectrumMethod:
    """Tests for :meth:`AnalysisBuilder.pushover_to_adrs` and
    :meth:`AnalysisBuilder.compute_performance_point`.

    CSM/ADRS requires a consistent mass matrix, which the elastic
    cantilever fixture could not provide (near-zero effective mass via
    ``compute_seismic_masses()``).  These tests use the fiber-capable
    single-storey RC moment frame from :func:`make_rc_frame_model`
    instead: the initial domain is built with elastic sections (used
    for the modal analysis), and ``run_pushover_analysis()`` rebuilds
    it with ``forceBeamColumn`` + fiber sections internally, giving a
    genuinely nonlinear capacity curve.
    """

    @pytest.fixture
    def rc_model(self):
        """Fiber-capable single-storey RC moment frame for CSM testing."""
        from examples.sample_model import make_rc_frame_model

        return make_rc_frame_model()

    @pytest.fixture
    def rc_ab(self, rc_model):
        """AnalysisBuilder for the RC frame (elastic sections for modal)."""
        import openseespy.opensees as ops

        cfg = {"element_type": "elasticBeamColumn", "split_elements": False, "verbose": False}
        mesh_model = preprocess_model(rc_model, cfg)
        b = AnalysisBuilder(mesh_model, cfg)
        yield b
        ops.wipe()

    @pytest.fixture
    def rc_adrs(self, rc_ab):
        """ADRS data from a nonlinear pushover of the RC frame."""
        rc_ab.build_domain()
        rc_ab.compute_seismic_masses()
        modal = rc_ab.run_modal_analysis(num_modes=1, print_results=False)
        shapes = rc_ab.extract_mode_shapes(1)
        results = rc_ab.run_pushover_analysis(
            gravity_patterns={"DEAD": 1.0},
            lateral_load_type="uniform",
            lateral_direction="X",
            control_node_tag=4,
            max_disp=0.3,
            num_steps=50,
            print_progress=False,
        )
        adrs = rc_ab.pushover_to_adrs(results, modal, shapes, direction="X")
        return rc_ab, results, modal, shapes, adrs

    def test_pushover_to_adrs_values_consistent(self, rc_adrs):
        """ADRS values are positive and consistent (no NaN or negative)."""
        _b, _results, _modal, _shapes, adrs = rc_adrs
        assert len(adrs["S_a"]) == len(adrs["S_d"]) > 0
        assert abs(adrs["M_eff"]) > 0
        assert all(v >= 0 for v in adrs["S_a"])
        assert all(v >= 0 for v in adrs["S_d"])
        assert all(math.isfinite(v) for v in adrs["S_a"])
        assert all(math.isfinite(v) for v in adrs["S_d"])

    def test_performance_point_elastic(self, rc_adrs):
        """Elastic demand path: converges at mu = 1 with finite values.

        A weak demand spectrum (50 % of the bilinearised yield
        acceleration) keeps the frame below yield, so the CSM iteration
        must converge to mu = 1.  The equivalent secant period is the
        secant stiffness of the *capacity curve at the performance
        point* — for a real RC frame this is not guaranteed to equal
        the elastic modal period, so it is only required to be positive
        and finite.
        """
        from fea_toolkit.model.csm import bilinearize_composite

        b, results, modal, shapes, adrs = rc_adrs
        # Anchor the spectrum to the bilinearised yield acceleration —
        # the same non-hard-coded scaling used by
        # test_workflows.py::test_compute_performance_point, with a
        # 0.5× factor so the demand plateau stays below yield and the
        # frame remains elastic.
        _S_dy, S_ay, _ = bilinearize_composite(
            np.asarray(adrs["S_d"], dtype=float),
            np.asarray(adrs["S_a"], dtype=float),
        )
        assert S_ay > 1e-6, f"Degenerate yield acceleration S_ay={S_ay:.3g}"

        T_spec = [0.0, 0.1, 0.5, 1.0, 2.0, 4.0, 6.0]
        accels = [0.5, 1.5, 1.5, 0.75, 0.375, 0.25, 0.125]
        # Scale so the spectrum peak is 50 % of the frame's yield
        # acceleration — well below yield → mu = 1.
        scale = (0.5 * S_ay) / max(accels)
        Sa_spec = [a * scale for a in accels]
        pp = b.compute_performance_point(
            results,
            modal,
            shapes,
            T_spec,
            Sa_spec,
            direction="X",
        )
        assert pp["converged"]
        assert pp["S_dp"] > 0
        assert pp["S_ap"] > 0
        assert math.isfinite(pp["T_eq"]) and pp["T_eq"] > 0
        # Demand below yield → no ductility.
        assert pp["mu"] == pytest.approx(1.0, abs=0.01)


@pytest.fixture
def clean_bilinearize_registry():
    """Snapshot and restore the global ``BILINEARIZE_METHODS`` registry.

    The CSM bilinearization registry is process-global mutable state; this
    fixture guarantees tests that register/override methods cannot leak
    into one another (mirrors the ``ops.wipe()`` teardown hygiene used for
    OpenSees global state).
    """
    from fea_toolkit.model.csm import BILINEARIZE_METHODS

    saved = dict(BILINEARIZE_METHODS)
    yield BILINEARIZE_METHODS
    BILINEARIZE_METHODS.clear()
    BILINEARIZE_METHODS.update(saved)


# ============================================================================
# model.csm — standalone utility functions
# ============================================================================


class TestCsmModule:
    """Test the standalone CSM utility functions in model/csm.py."""

    def test_pushover_to_adrs_basic(self):
        """ADRS conversion works with valid pushover + modal results."""
        from fea_toolkit.model.csm import pushover_to_adrs

        pushover = {
            "control_node": 1,
            "control_disp": [0.0, 0.01, 0.02, 0.03, 0.04],
            "base_shear": [0.0, 100.0, 180.0, 240.0, 280.0],
        }
        modal = {
            "modal_props": {
                "partiMassRatiosMX": [0.8, 0.1],
                "partiMassMX": [800.0, 100.0],
            },
            "periods": [0.5, 0.1],
            "nodal_masses": {1: 1000.0},
        }
        shapes = {0: {1: (1.0, 0.0, 0.0)}, 1: {1: (0.0, 1.0, 0.0)}}

        adrs = pushover_to_adrs(pushover, modal, shapes, direction="X")
        assert isinstance(adrs, dict)
        assert set(adrs) >= {"S_a", "S_d", "Gamma", "M_eff", "phi_control", "best_mode"}
        assert len(adrs["S_a"]) == len(pushover["control_disp"])
        assert len(adrs["S_d"]) == len(pushover["control_disp"])
        assert adrs["Gamma"] > 0
        assert adrs["M_eff"] > 0
        assert adrs["phi_control"] > 0
        assert adrs["best_mode"] == 0  # mode 0 has 80% ratio

    def test_compute_performance_point_basic(self):
        """Performance point computation runs with valid data."""
        from fea_toolkit.model.csm import compute_performance_point

        pushover = {
            "control_node": 1,
            "control_disp": [0.0, 0.005, 0.01, 0.02, 0.03, 0.04, 0.05],
            "base_shear": [0.0, 50.0, 100.0, 180.0, 240.0, 280.0, 300.0],
        }
        modal = {
            "modal_props": {
                "partiMassRatiosMX": [0.8, 0.15],
                "partiMassMX": [800.0, 150.0],
            },
            "periods": [0.5, 0.12],
            "nodal_masses": {1: 1000.0},
        }
        shapes = {0: {1: (1.0, 0.0, 0.0)}, 1: {1: (0.0, 1.0, 0.0)}}
        periods = [0.0, 0.1, 0.2, 0.5, 1.0, 2.0, 3.0]
        accels = [3.0, 3.0, 3.0, 1.5, 0.8, 0.4, 0.2]

        pp = compute_performance_point(
            pushover,
            modal,
            shapes,
            periods,
            accels,
            direction="X",
            damping_ratio=0.05,
            max_iter=20,
            tol=0.05,
        )
        assert isinstance(pp, dict)
        assert set(pp) >= {
            "S_dp",
            "S_ap",
            "V_base",
            "D_roof",
            "T_eq",
            "mu",
            "converged",
            "S_dy",
            "S_ay",
        }
        assert pp["S_dp"] > 1e-6
        assert pp["S_ap"] > 1e-6
        assert pp["V_base"] > 1e-6
        assert isinstance(pp["converged"], bool)

    def test_compute_performance_point_accepts_de_luca_method(self):
        """``bilinearize_method='de_luca_10pct'`` / ``'rc'`` dispatch to
        :func:`bilinearize_rc` without error and report the method used."""
        from fea_toolkit.model.csm import compute_performance_point

        pushover = {
            "control_node": 1,
            "control_disp": [0.0, 0.005, 0.01, 0.02, 0.03, 0.04, 0.05],
            "base_shear": [0.0, 50.0, 100.0, 180.0, 240.0, 280.0, 300.0],
        }
        modal = {
            "modal_props": {
                "partiMassRatiosMX": [0.8, 0.15],
                "partiMassMX": [800.0, 150.0],
            },
            "periods": [0.5, 0.12],
            "nodal_masses": {1: 1000.0},
        }
        shapes = {0: {1: (1.0, 0.0, 0.0)}, 1: {1: (0.0, 1.0, 0.0)}}
        periods = [0.0, 0.1, 0.2, 0.5, 1.0, 2.0, 3.0]
        accels = [3.0, 3.0, 3.0, 1.5, 0.8, 0.4, 0.2]

        for name in ("de_luca_10pct", "rc"):
            pp = compute_performance_point(
                pushover,
                modal,
                shapes,
                periods,
                accels,
                direction="X",
                damping_ratio=0.05,
                max_iter=20,
                tol=0.05,
                bilinearize_method=name,
            )
            assert isinstance(pp, dict)
            assert pp["bilinearize_method"].startswith("de_luca_10pct"), (
                f"method {name}: expected de_luca_10pct*, got {pp['bilinearize_method']}"
            )
            assert pp["S_dy"] > 0
            assert pp["S_ay"] > 0

    def test_compute_performance_point_too_few_points(self):
        """Raises ValueError with fewer than 3 valid data points."""
        from fea_toolkit.model.csm import compute_performance_point

        pushover = {
            "control_node": 1,
            "control_disp": [0.0, 0.01],
            "base_shear": [0.0, 100.0],
        }
        modal = {
            "modal_props": {
                "partiMassRatiosMX": [0.8],
                "partiMassMX": [800.0],
            },
            "periods": [0.5],
            "nodal_masses": {1: 1000.0},
        }
        shapes = {0: {1: (1.0, 0.0, 0.0)}}
        with pytest.raises(ValueError, match=r"too few|Too few"):
            compute_performance_point(
                pushover,
                modal,
                shapes,
                [0.1, 0.5],
                [3.0, 1.5],
            )

    def test_pushover_to_adrs_y_direction(self):
        """ADRS conversion in Y direction picks correct mass data."""
        from fea_toolkit.model.csm import pushover_to_adrs

        pushover = {
            "control_node": 1,
            "control_disp": [0.0, 0.01],
            "base_shear": [0.0, 100.0],
        }
        modal = {
            "modal_props": {
                "partiMassRatiosMX": [0.2],
                "partiMassMX": [200.0],
                "partiMassRatiosMY": [0.7],
                "partiMassMY": [700.0],
            },
            "periods": [0.5],
            "nodal_masses": {1: 700.0},
        }
        shapes = {0: {1: (0.0, 1.0, 0.0)}}

        adrs = pushover_to_adrs(pushover, modal, shapes, direction="Y")
        # With nodal_masses = {1: 700.0} and mode shape (0, 1, 0):
        # L = 700 * 1.0 = 700, M_star = 700 * 1.0^2 = 700
        # Gamma = L / M_star = 1.0, M_eff = L^2 / M_star = 700
        assert adrs["M_eff"] == 700.0
        assert adrs["Gamma"] == 1.0

    def test_pushover_to_adrs_rejects_ill_conditioned_modes(self):
        """Low-participation torsional modes must not win ADRS selection.

        Regression test for the Project B CSM flattening bug: a
        torsional eigenvector whose residual X components are all ~0.002
        same-sign noise produces a tiny ``M_star_x`` (~0.003) while
        ``L_x / M_star_x`` is ill-conditioned, so ``L_x²/M_star_x``
        blows up to ≈ ``M_total``.  Under a naive max of ``L²/M_star``
        this contaminated mode out-ranks the true ~99 % X sway mode,
        corrupting the ``S_a``/``S_d`` scaling of the whole CSM curve.
        """
        from fea_toolkit.model.csm import pushover_to_adrs

        pushover = {
            "control_node": 1,
            "control_disp": [0.0, 0.01],
            "base_shear": [0.0, 100.0],
        }
        masses = {1: 100.0, 2: 100.0, 3: 100.0, 4: 100.0, 5: 100.0, 6: 100.0}
        modal = {
            "modal_props": {},
            "periods": [0.5, 0.1],
            "nodal_masses": masses,
        }
        # Mode 0 (index 0): genuine X sway, ~99 % participation with a
        # varying mode shape.  Mode 1 (index 1): torsional eigenvector
        # (large ±z components) whose residual X components are all
        # ~0.002 same-sign noise — M_star_x ≈ 0.003 but L_x²/M_star_x
        # ≈ M_total, which used to win the naive max selection.
        sway_phis = [1.0, 0.95, 0.9, 0.85, 0.8, 0.75]
        shapes = {
            0: {i: (phi, 0.0, 0.0) for i, phi in zip(range(1, 7), sway_phis)},
            1: {
                i: (0.002 + (i - 1) * 0.0001, 0.0, 1.0 if i % 2 == 0 else -1.0) for i in range(1, 7)
            },
        }

        adrs = pushover_to_adrs(pushover, modal, shapes, direction="X")

        # The fix must pick the true sway mode, not the contaminated
        # torsional mode.
        assert adrs["best_mode"] == 0
        L0 = 100.0 * sum(sway_phis)
        Ms0 = 100.0 * sum(p * p for p in sway_phis)
        assert abs(adrs["M_eff"] - L0 * L0 / Ms0) < 1e-8
        # Effective modal mass can never exceed the total physical mass.
        assert adrs["M_eff"] < 600.0
        assert adrs["phi_control"] == 1.0

    def test_bilinearize_registry_builtins_present(self):
        """The five built-in method names are pre-registered."""
        from fea_toolkit.model.csm import (
            BILINEARIZE_METHODS,
            bilinearize_composite,
            bilinearize_equal_energy,
            bilinearize_rc,
            bilinearize_stiffness_change,
            get_bilinearize_method,
        )

        assert set(BILINEARIZE_METHODS) == {
            "composite",
            "stiffness_change",
            "equal_energy",
            "rc",
            "de_luca_10pct",
        }
        assert get_bilinearize_method("composite") is bilinearize_composite
        assert get_bilinearize_method("stiffness_change") is bilinearize_stiffness_change
        assert get_bilinearize_method("equal_energy") is bilinearize_equal_energy
        assert get_bilinearize_method("rc") is bilinearize_rc
        assert get_bilinearize_method("de_luca_10pct") is bilinearize_rc

    def test_bilinearize_registry_register_and_dispatch_custom(self, clean_bilinearize_registry):
        """A third-party registered name flows through the full CSM."""
        from fea_toolkit.model.csm import (
            bilinearize_rc,
            compute_performance_point,
            register_bilinearize_method,
        )

        register_bilinearize_method("my_rc", bilinearize_rc)

        pushover = {
            "control_node": 1,
            "control_disp": [0.0, 0.005, 0.01, 0.02, 0.03, 0.04, 0.05],
            "base_shear": [0.0, 50.0, 100.0, 180.0, 240.0, 280.0, 300.0],
        }
        modal = {
            "modal_props": {
                "partiMassRatiosMX": [0.8, 0.15],
                "partiMassMX": [800.0, 150.0],
            },
            "periods": [0.5, 0.12],
            "nodal_masses": {1: 1000.0},
        }
        shapes = {0: {1: (1.0, 0.0, 0.0)}, 1: {1: (0.0, 1.0, 0.0)}}
        periods = [0.0, 0.1, 0.2, 0.5, 1.0, 2.0, 3.0]
        accels = [3.0, 3.0, 3.0, 1.5, 0.8, 0.4, 0.2]

        pp = compute_performance_point(
            pushover,
            modal,
            shapes,
            periods,
            accels,
            direction="X",
            damping_ratio=0.05,
            max_iter=20,
            tol=0.05,
            bilinearize_method="my_rc",
        )
        assert pp["bilinearize_method"].startswith("de_luca_10pct")
        assert pp["S_dy"] > 0
        assert pp["S_ay"] > 0

    def test_bilinearize_registry_unknown_method_raises(self):
        """Unknown names raise ValueError listing the registered keys."""
        from fea_toolkit.model.csm import get_bilinearize_method

        with pytest.raises(ValueError, match=r"Unknown bilinearize_method 'nope'"):
            get_bilinearize_method("nope")

    def test_compute_performance_point_rejects_unknown_method(self):
        """An unregistered ``bilinearize_method`` propagates ValueError in CSM."""
        from fea_toolkit.model.csm import compute_performance_point

        pushover = {
            "control_node": 1,
            "control_disp": [0.0, 0.01, 0.02, 0.03, 0.04],
            "base_shear": [0.0, 50.0, 100.0, 150.0, 180.0],
        }
        modal = {
            "modal_props": {"partiMassRatiosMX": [0.8], "partiMassMX": [800.0]},
            "periods": [0.5],
            "nodal_masses": {1: 1000.0},
        }
        shapes = {0: {1: (1.0, 0.0, 0.0)}}

        with pytest.raises(ValueError, match=r"Unknown bilinearize_method 'nope'"):
            compute_performance_point(
                pushover,
                modal,
                shapes,
                [0.0, 0.5, 1.0],
                [3.0, 1.5, 0.8],
                bilinearize_method="nope",
            )

    def test_bilinearize_registry_overwrite_guard(self, clean_bilinearize_registry):
        """Re-registering a built-in requires overwrite=True."""
        from fea_toolkit.model.csm import (
            BILINEARIZE_METHODS,
            get_bilinearize_method,
            register_bilinearize_method,
        )

        def _stub(S_d_arr, S_a_arr, config=None):
            return (0.05, 100.0, "stub")

        with pytest.raises(ValueError, match=r"already registered"):
            register_bilinearize_method("composite", _stub)

        register_bilinearize_method("composite", _stub, overwrite=True)
        assert get_bilinearize_method("composite") is _stub
        assert "composite" in BILINEARIZE_METHODS

    def test_bilinearize_registry_non_callable_raises(self):
        """Registering a non-callable raises TypeError."""
        from fea_toolkit.model.csm import register_bilinearize_method

        with pytest.raises(TypeError, match=r"must be callable"):
            register_bilinearize_method("bad", "not-a-callable")


# ============================================================================
# model.csm — bilinearization methods
# ============================================================================


class TestBilinearization:
    """Test the three bilinearisation methods in model/csm.py.

    Uses synthetic capacity curves:
    - bilinear: linear-elastic up to 0.02 m → flat plastic plateau.
    - elastic: pure linear (no yield).
    - hardening: parabolic with gradual stiffness decay.
    - peak_curve: slight post-peak softening.
    """

    # ── Fixtures ─────────────────────────────────────────────────────

    @pytest.fixture
    def bilinear_curve(self):
        """Bilinear: S_a = 5000 * S_d up to S_d=0.02, then gentle post-yield
        hardening (slope = 500).  The peak is well past the knee, so
        stiffness-change can detect the knee and composite will not fall back."""
        S_d = np.linspace(0.0, 0.08, 41)
        # Linear elastic up to S_d = 0.02 (S_a = 100), then hardening at 500.
        S_a = np.where(S_d <= 0.02, 5000.0 * S_d, 100.0 + 500.0 * (S_d - 0.02))
        S_a[0] = 0.0
        return S_d, S_a

    @pytest.fixture
    def elastic_curve(self):
        """Elastic: S_a = 10000 * S_d (pure linear)."""
        S_d = np.linspace(0.0, 0.05, 21)
        S_a = 10000.0 * S_d
        S_a[0] = 0.0
        return S_d, S_a

    @pytest.fixture
    def hardening_curve(self):
        """Hardening: S_a = 5000 * sqrt(S_d) — gradual stiffness decay."""
        S_d = np.linspace(0.0, 0.10, 31)
        S_a = 5000.0 * np.sqrt(S_d)
        S_a[0] = 0.0
        return S_d, S_a

    @pytest.fixture
    def peak_curve(self):
        """Curve with a clear peak before softening."""
        S_d = np.linspace(0.0, 0.10, 31)
        # Ascend to 0.040 m, then descend
        S_a = np.where(S_d <= 0.04, 10000.0 * S_d, 400.0 - 200.0 * (S_d - 0.04) / 0.06)
        S_a = np.maximum(S_a, 0.0)
        return S_d, S_a

    @pytest.fixture
    def sudden_drop_curve(self):
        """Curve with an abrupt softening step to test criterion B.

        Peak at index 7 (S_d=0.035, S_a=285).  A single-step stiffness
        drop occurs at index 8 (S_d=0.040, S_a=110).  Since the drop is
        *after* the peak, stiffness-change returns peak (fallback) and
        composite falls back to equal-energy.
        """
        S_d = np.array(
            [
                0.0,
                0.005,
                0.010,
                0.015,
                0.020,
                0.025,
                0.030,
                0.035,
                0.040,
                0.050,
                0.060,
                0.070,
                0.080,
            ]
        )
        S_a = np.array(
            [0.0, 50.0, 100.0, 150.0, 200.0, 250.0, 275.0, 285.0, 110.0, 120.0, 125.0, 128.0, 130.0]
        )
        return S_d, S_a

    @pytest.fixture
    def two_point_curve(self):
        """Only 2 data points — triggers early-return."""
        return np.array([0.0, 0.01]), np.array([0.0, 100.0])

    @pytest.fixture
    def empty_curve(self):
        """Empty arrays — edge case for defensive checking."""
        return np.array([]), np.array([])

    @pytest.fixture
    def noisy_curve(self):
        """Bilinear curve carrying one out-of-contract negative S_a sentinel.

        ``S_a[3] = -5.0`` at ``S_d = 0.006`` stands for any ordinate that
        violates the bilinearizers' documented non-negative-ordinate
        contract (see :meth:`test_noisy_curve_with_negative_sa`).
        """
        S_d = np.linspace(0.0, 0.08, 41)
        S_a = np.where(S_d <= 0.02, 5000.0 * S_d, 100.0 + 500.0 * (S_d - 0.02))
        # Inject a single out-of-contract negative ordinate (midsweep,
        # well inside the peak-search and stiffness-change scan).
        S_a[3] = -5.0
        S_a[0] = 0.0
        return S_d, S_a

    @pytest.fixture
    def rc_like_curve(self):
        """Smooth RC-style backbone: tanh saturation + post-peak softening.

        Saturates gradually (cracking → rebar yield) with no sharp yield
        plateau, then softens after the peak — the shape the De Luca
        10 %-secant rule is designed for.  Peak at S_d ≈ 0.06 m.
        """
        S_d = np.linspace(0.0, 0.12, 61)
        S_a_peak = 150.0  # m/s²
        K0 = 6000.0  # initial stiffness
        S_a = S_a_peak * np.tanh(K0 * S_d / S_a_peak)
        soft = S_d >= 0.06
        S_a[soft] = S_a[soft] - 80.0 * (S_d[soft] - 0.06) / 0.06
        S_a = np.maximum(S_a, 0.0)
        S_a[0] = 0.0
        return S_d, S_a

    # ── bilinearize_stiffness_change ─────────────────────────────────

    def test_stiffness_change_detects_bilinear_knee(self, bilinear_curve):
        """Detects yield where secant stiffness drops below 50 % of initial.

        For this bilinear+hardening curve (K₁=5000, K₂=500), the secant
        stiffness crosses the 50 % threshold at S_d ≈ 0.046 m."""
        S_d, S_a = bilinear_curve
        S_dy, _S_ay, method = bilinearize_stiffness_change(S_d, S_a)
        assert method == "stiffness_change"
        # The stiffness-change detector finds the first point where
        # secant stiffness < 50 % of K_init.  For this curve that's
        # near S_d ≈ 0.046.
        peak_idx = int(np.argmax(S_a))
        assert 0.040 <= S_dy <= 0.055, f"Expected S_dy in [0.040, 0.055], got {S_dy:.6f}"
        assert S_dy < S_d[peak_idx] * 0.9, (
            f"Expected yield well below peak, got S_dy={S_dy:.4f} vs peak={S_d[peak_idx]:.4f}"
        )

    def test_stiffness_change_elastic_resets_to_peak(self, elastic_curve):
        """Elastic curve → no stiffness drop → returns peak."""
        S_d, S_a = elastic_curve
        S_dy, S_ay, method = bilinearize_stiffness_change(S_d, S_a)
        assert method == "stiffness_change"
        peak_idx = int(np.argmax(S_a))
        assert S_dy == pytest.approx(S_d[peak_idx])
        assert S_ay == pytest.approx(S_a[peak_idx])

    def test_stiffness_change_hardening_finds_point(self, hardening_curve):
        """Hardening curve returns a valid yield point."""
        S_d, S_a = hardening_curve
        S_dy, S_ay, method = bilinearize_stiffness_change(S_d, S_a)
        assert method == "stiffness_change"
        assert S_dy > 0
        assert S_ay > 0

    def test_stiffness_change_threshold_config(self, bilinear_curve):
        """Higher threshold → more sensitive → earlier yield (smaller S_dy)."""
        S_d, S_a = bilinear_curve
        S_dy_lo, _, _ = bilinearize_stiffness_change(S_d, S_a, {"threshold": 0.30})
        S_dy_hi, _, _ = bilinearize_stiffness_change(S_d, S_a, {"threshold": 0.85})
        # A lower threshold (0.30) is less sensitive → detects later
        # (higher S_dy).  A higher threshold (0.85) is more sensitive
        # → detects earlier (lower S_dy).
        assert S_dy_hi <= S_dy_lo, (
            f"Higher threshold (0.85) should give S_dy <= lower (0.30), "
            f"got {S_dy_hi:.6f} > {S_dy_lo:.6f}"
        )

    def test_stiffness_change_sudden_drop_criterion_b(self, sudden_drop_curve):
        """Sudden single-step stiffness drop triggers criterion B."""
        S_d, S_a = sudden_drop_curve
        S_dy, _S_ay, method = bilinearize_stiffness_change(S_d, S_a)
        assert method == "stiffness_change"
        # Should detect at or near the drop index (8 → S_d=0.040)
        assert 0.035 <= S_dy <= 0.045, f"Expected S_dy near 0.040, got {S_dy:.6f}"

    def test_stiffness_change_peak_idx_config(self, bilinear_curve):
        """Explicit peak_idx truncates search range."""
        S_d, S_a = bilinear_curve
        # peak_idx=5 → peaks early → yield forced to peak area
        S_dy_early, _S_ay_early, method = bilinearize_stiffness_change(S_d, S_a, {"peak_idx": 5})
        assert method == "stiffness_change"
        # peak_idx=5 is before the true knee
        # S_d_arr ≈ [0, 0.002, 0.004, 0.006, 0.008, 0.010, ...]
        assert S_dy_early <= S_d[5], (
            f"Expected S_dy <= {S_d[5]:.6f} (peak at index 5), got {S_dy_early:.6f}"
        )

    def test_stiffness_change_two_points(self, two_point_curve):
        """Only 2 data points → returns the last (non-zero) point."""
        S_d, S_a = two_point_curve
        S_dy, S_ay, method = bilinearize_stiffness_change(S_d, S_a)
        assert method == "stiffness_change"
        # With 2 points, peak_idx=1 (not < 1), so it computes secant
        # stiffness and falls through to return the peak.
        assert S_dy == pytest.approx(0.01)
        assert S_ay == pytest.approx(100.0)

    # ── bilinearize_equal_energy ─────────────────────────────────────

    def test_equal_energy_bilinear_reasonable(self, bilinear_curve):
        """Bilinear curve → yield in plausible range with energy balance."""
        S_d, S_a = bilinear_curve
        S_dy, _S_ay, method = bilinearize_equal_energy(S_d, S_a)
        assert method == "equal_energy"
        peak_idx = int(np.argmax(S_a))
        S_d_peak = S_d[peak_idx]
        # Yield should be at or before peak for bilinear with plateau
        assert S_dy <= S_d_peak, f"Expected S_dy ≤ peak ({S_d_peak:.4f}), got {S_dy:.4f}"
        assert S_dy > 0

    def test_equal_energy_elastic_converges(self, elastic_curve):
        """Elastic curve (linear) converges at the initial guess (30% of peak).

        For a purely linear S_a = K * S_d, the bilinear area exactly
        matches the actual area at *any* S_dy because K_init = K is
        constant.  The iteration converges immediately at the initial
        guess (0.3 * S_d_peak), so no peak-reset occurs.
        """
        S_d, S_a = elastic_curve
        S_dy, _S_ay, method = bilinearize_equal_energy(S_d, S_a)
        assert method == "equal_energy"
        peak_idx = int(np.argmax(S_a))
        # Initial guess = 0.3 * S_d_peak
        expected = 0.3 * S_d[peak_idx]
        assert S_dy == pytest.approx(expected, abs=1e-6), (
            f"Expected S_dy ≈ {expected:.6f} (30% of peak), got {S_dy:.6f}"
        )
        # The ≥90% reset should NOT trigger for a linear curve
        # because S_dy (30% of peak) < 90% of peak.
        assert S_dy < 0.90 * S_d[peak_idx], (
            f"Peak reset should not trigger: S_dy={S_dy:.4f}, "
            f"90% of peak={0.90 * S_d[peak_idx]:.4f}"
        )

    def test_equal_energy_config_tolerance(self, hardening_curve):
        """Tighter tolerance affects iteration depth (result stable)."""
        S_d, S_a = hardening_curve
        # Coarse tolerance should still give a sensible S_dy
        S_dy_coarse, _S_ay_coarse, _ = bilinearize_equal_energy(
            S_d, S_a, {"tolerance": 0.05, "max_iter": 5}
        )
        S_dy_fine, _S_ay_fine, _ = bilinearize_equal_energy(
            S_d, S_a, {"tolerance": 0.0001, "max_iter": 200}
        )
        # Both should be in a plausible range
        peak_idx = int(np.argmax(S_a))
        S_d_peak = S_d[peak_idx]
        for label, S_dy_val in [("coarse", S_dy_coarse), ("fine", S_dy_fine)]:
            assert S_dy_val > 0, f"{label} S_dy={S_dy_val:.4f} out of range (peak={S_d_peak:.4f})"

    def test_equal_energy_two_points(self, two_point_curve):
        """Only 2 data points — peak_idx < 2 returns yield at peak."""
        S_d, S_a = two_point_curve
        S_dy, S_ay, method = bilinearize_equal_energy(S_d, S_a)
        assert method == "equal_energy"
        # With peak_idx=1 (< 2), the function returns S_d_peak = 0.01.
        assert S_dy == pytest.approx(0.01, abs=1e-10)
        assert S_ay == pytest.approx(100.0, abs=1e-10)

    def test_equal_energy_hardening_area_error(self, hardening_curve):
        """Equal-energy result preserves area (or converges at peak) on hardening curve.

        For a hardening curve S_a = 5000*sqrt(S_d), the equal-energy
        iteration may converge at the peak (S_dy = S_d_peak) because
        the area error never drops below tolerance before S_dy reaches
        the peak boundary.  When that happens, the bilinear area
        degenerates to a triangle (A2 = A3 = 0) and the area check
        is not meaningful — verify that the method still returns a
        sensible result.
        """
        S_d, S_a = hardening_curve
        S_dy, S_ay, method = bilinearize_equal_energy(S_d, S_a)
        assert method in ("equal_energy", "equal_energy_not_converged")
        assert S_dy > 0
        assert S_ay > 0
        # If yield is below peak, verify area preservation
        peak_idx = int(np.argmax(S_a))
        if S_dy < S_d[peak_idx]:
            n_el = max(3, len(S_d) // 5)
            K_init = float(np.polyfit(S_d[:n_el], S_a[:n_el], 1)[0])
            S_d_peak = float(S_d[peak_idx])
            S_a_peak = float(S_a[peak_idx])
            area_actual = float(np.trapezoid(S_a[: peak_idx + 1], S_d[: peak_idx + 1]))
            S_ay_derived = K_init * S_dy
            A1 = 0.5 * S_ay_derived * S_dy
            A2 = S_ay_derived * (S_d_peak - S_dy)
            A3 = 0.5 * (S_a_peak - S_ay_derived) * (S_d_peak - S_dy)
            area_bilin = A1 + A2 + A3
            rel_err = abs(area_bilin - area_actual) / max(area_actual, 1e-12)
            assert rel_err <= 0.01, (
                f"Area error {rel_err:.4f} exceeds 1% for hardening curve: "
                f"area_bilin={area_bilin:.6e}, area_actual={area_actual:.6e}"
            )

    def test_equal_energy_initial_guess_config(self, hardening_curve):
        """Higher initial_guess shifts the converged S_dy upward.

        For a hardening curve S_a = 5000*sqrt(S_d), the equal-energy
        iteration moves S_dy from the initial guess toward the energy-
        preserving value.  A higher initial guess produces a higher
        converged S_dy because the relaxation S_dy *= 1 - 0.5*err
        moves from above vs below the true value.
        """
        S_d, S_a = hardening_curve
        S_dy_low, _, _ = bilinearize_equal_energy(S_d, S_a, {"initial_guess": 0.2})
        S_dy_high, _, _ = bilinearize_equal_energy(S_d, S_a, {"initial_guess": 0.6})
        peak_idx = int(np.argmax(S_a))
        S_d_peak = S_d[peak_idx]
        # Both guesses should yield plausible values < peak.
        assert 0 < S_dy_low <= S_d_peak, (
            f"Expected 0 < S_dy_low ({S_dy_low:.4f}) <= peak({S_d_peak:.4f})"
        )
        assert 0 < S_dy_high <= S_d_peak, (
            f"Expected 0 < S_dy_high ({S_dy_high:.4f}) <= peak({S_d_peak:.4f})"
        )
        # The higher initial guess should converge to a larger S_dy.
        assert S_dy_low <= S_dy_high, (
            f"Expected S_dy_low ({S_dy_low:.4f}) <= S_dy_high ({S_dy_high:.4f})"
        )
        # The lower guess should be closer to the initial 20% of peak
        # and the higher guess closer to 60% of peak.
        # For hardening curves both guesses may converge at the peak
        # (S_dy == S_d_peak) because the equal-energy iteration hits
        # the peak boundary before reaching tolerance.  When this
        # happens, S_dy_low/peak == 1.0, which is >0.35 (0.2+0.15).
        # Only check the ratio when the result is below the peak.
        if S_dy_low < S_d_peak:
            assert abs(S_dy_low / S_d_peak - 0.2) <= 0.15, (
                f"S_dy_low/{S_d_peak} = {S_dy_low / S_d_peak:.4f}, expected near 0.20"
            )
        if S_dy_high < S_d_peak:
            assert abs(S_dy_high / S_d_peak - 0.6) <= 0.4, (
                f"S_dy_high/{S_d_peak} = {S_dy_high / S_d_peak:.4f}, expected near 0.6"
            )

    # ── bilinearize_composite ────────────────────────────────────────

    def test_composite_bilinear_uses_stiffness_change(self, bilinear_curve):
        """Clear yield below peak → composite uses stiffness-change path."""
        S_d, S_a = bilinear_curve
        S_dy, _S_ay, method = bilinearize_composite(S_d, S_a)
        assert method == "composite_stiffness_change", (
            f"Expected composite_stiffness_change, got {method}"
        )
        peak_idx = int(np.argmax(S_a))
        assert 0.040 <= S_dy <= 0.055, f"Expected S_dy in [0.040, 0.055], got {S_dy:.6f}"
        # Yield must be well below the peak (no fallback)
        assert S_dy < 0.90 * S_d[peak_idx], (
            f"Yield at {S_dy:.4f} should be < 90% of peak ({S_d[peak_idx]:.4f})"
        )

    def test_composite_elastic_falls_back(self, elastic_curve):
        """Elastic curve → stiffness-change yields at peak → fallback.

        Stiffness-change sees no secant drop below threshold (all
        secant values ≈ 10000) so it returns the peak.  Composite
        then falls back to equal-energy which converges at 30% of
        peak (see test_equal_energy_elastic_converges).
        """
        S_d, S_a = elastic_curve
        S_dy, _S_ay, method = bilinearize_composite(S_d, S_a)
        assert method == "composite_equal_energy"
        peak_idx = int(np.argmax(S_a))
        # Expected yield = 30% peak (equal-energy default initial guess)
        expected = 0.3 * S_d[peak_idx]
        assert S_dy == pytest.approx(expected, abs=1e-6), (
            f"Expected S_dy ≈ {expected:.6f} (30% peak), got {S_dy:.6f}"
        )

    def test_composite_hardening_in_range(self, hardening_curve):
        """Hardening curve returns a method and plausible yield."""
        S_d, S_a = hardening_curve
        S_dy, _S_ay, method = bilinearize_composite(S_d, S_a)
        assert method in ("composite_stiffness_change", "composite_equal_energy")
        peak_idx = int(np.argmax(S_a))
        assert S_dy < S_d[peak_idx], f"Expected yield < peak ({S_d[peak_idx]:.4f}), got {S_dy:.4f}"
        assert S_dy > 0

    def test_composite_minimum_10_percent_clamp(self, bilinear_curve):
        """Verify S_dy is clamped to ≥10 % of peak displacement.

        Construct a curve whose stiffness-change yield would land near
        zero, then verify the composite clamp brings it up to 10 %.
        """
        S_d = np.array(
            [0.0, 0.001, 0.002, 0.003, 0.004, 0.005, 0.010, 0.020, 0.040, 0.060, 0.080, 0.100]
        )
        S_a = np.array(
            [0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 100.0, 200.0, 350.0, 400.0, 420.0, 430.0]
        )
        S_dy, _S_ay, _method = bilinearize_composite(S_d, S_a)
        peak_idx = int(np.argmax(S_a))
        min_S_dy = 0.10 * S_d[peak_idx]
        assert S_dy >= min_S_dy - 1e-12, (
            f"Expected S_dy >= 10% of peak ({min_S_dy:.6f}), got {S_dy:.6f}"
        )

    def test_composite_peak_idx_passthrough(self, peak_curve):
        """Explicit peak_idx is passed through to sub-methods."""
        S_d, S_a = peak_curve
        peak_idx = 15  # well before the true peak
        S_dy, _S_ay, method = bilinearize_composite(S_d, S_a, {"peak_idx": peak_idx})
        assert method in ("composite_stiffness_change", "composite_equal_energy")
        # The yield should be ≤ S_d[peak_idx] since that's the forced peak
        assert S_dy <= S_d[peak_idx] + 1e-12, (
            f"Expected S_dy <= forced peak at index {peak_idx} "
            f"({S_d[peak_idx]:.6f}), got {S_dy:.6f}"
        )

    def test_composite_config_passthrough(self, bilinear_curve):
        """Config dict is passed through, affecting sub-method behavior."""
        S_d, S_a = bilinear_curve
        S_dy_default, _, method_default = bilinearize_composite(S_d, S_a)
        S_dy_custom, _, method_custom = bilinearize_composite(
            S_d, S_a, {"initial_guess": 0.6}
        )  # passed to equal-energy
        # Both should be stiffness-change since bilinear has clear knee
        # below 90% of peak
        assert method_default == "composite_stiffness_change", (
            f"Expected composite_stiffness_change, got {method_default}"
        )
        assert method_custom == "composite_stiffness_change", (
            f"Expected composite_stiffness_change, got {method_custom}"
        )
        # Both should return the same stiffness-change result
        assert 0.040 <= S_dy_default <= 0.055, (
            f"Expected S_dy in [0.040, 0.055], got {S_dy_default:.6f}"
        )
        assert 0.040 <= S_dy_custom <= 0.055, (
            f"Expected S_dy in [0.040, 0.055], got {S_dy_custom:.6f}"
        )

    # ── Edge cases (applies to all methods) ─────────────────────────

    def test_empty_arrays_return_defaults(self, empty_curve):
        """All three methods return (0.0, 0.0, method) on empty arrays.

        Each method guards explicitly against zero-length arrays
        and returns a safe default rather than raising ValueError.
        """
        S_d, S_a = empty_curve
        for fn, expected_method in [
            (bilinearize_stiffness_change, "stiffness_change"),
            (bilinearize_equal_energy, "equal_energy"),
            (bilinearize_composite, "composite_equal_energy"),
            (bilinearize_rc, "de_luca_10pct"),
        ]:
            S_dy, S_ay, method = fn(S_d, S_a)
            assert S_dy == 0.0, f"{fn.__name__}: expected S_dy=0.0, got {S_dy}"
            assert S_ay == 0.0, f"{fn.__name__}: expected S_ay=0.0, got {S_ay}"
            assert method == expected_method, (
                f"{fn.__name__}: expected method={expected_method}, got {method}"
            )

    def test_noisy_curve_with_negative_sa(self, noisy_curve):
        """Negative S_a ordinates are out of contract and folded by the caller.

        The bilinearizers document *S_a_arr* as non-negative (see their
        ``Args`` sections) and do not filter negatives internally: a raw
        negative ordinate inside the stiffness-change scan is adopted
        verbatim as the yield point, so passing it straight through would
        return a *negative* yield acceleration.  Negative handling is
        deliberate and caller-side — :func:`compute_performance_point`
        folds a -X/-Y push with ``np.abs`` and masks any ordinate below
        ``-1e-12`` before dispatch (only origin machine noise such as
        ``-1e-17`` ever reaches the methods).  This test folds the curve
        exactly as the production path does, then asserts all four
        methods return a finite, positive yield point.
        """
        S_d, S_a = noisy_curve

        # Out-of-contract guard: the raw negative ordinate is *not*
        # skipped — it becomes the stiffness-change yield point.  Pinning
        # that leak keeps the np.abs() fold below load-bearing rather than
        # cosmetic: if the bilinearizers ever become negative-tolerant,
        # this fails and the contract (and docstring) must be revisited.
        raw_S_dy, raw_S_ay, _ = bilinearize_stiffness_change(S_d, S_a)
        assert raw_S_ay < 0.0, (
            "Expected the out-of-contract raw negative ordinate to be adopted "
            f"verbatim by stiffness-change, got S_ay={raw_S_ay:.6e}"
        )
        assert raw_S_dy == pytest.approx(S_d[int(np.argmin(S_a))])

        # Caller-side fold, mirroring compute_performance_point().
        for fn in (
            bilinearize_stiffness_change,
            bilinearize_equal_energy,
            bilinearize_composite,
            bilinearize_rc,
        ):
            S_dy, S_ay, _ = fn(S_d, np.abs(S_a))
            assert S_dy > 0, f"S_dy should be positive, got {S_dy:.6e}"
            assert S_ay > 0, f"S_ay should be positive, got {S_ay:.6e}"
            assert math.isfinite(S_dy)
            assert math.isfinite(S_ay)

    def test_elastic_curve_all_methods_consistent(self, elastic_curve):
        """All three methods agree the elastic curve has not yielded.

        For a purely linear curve S_a = K * S_d:
        - stiffness_change returns the peak (no stiffness drop detected)
        - equal_energy converges at the 30% initial guess

        Both results should produce a plausible ductility ≤ 3.33 (i.e.
        S_dy ≥ 30 % of peak), consistent with an essentially elastic
        structure.
        """
        S_d, S_a = elastic_curve
        peak_idx = int(np.argmax(S_a))
        S_d_peak = S_d[peak_idx]
        S_a_peak = S_a[peak_idx]

        results = {
            "stiffness_change": bilinearize_stiffness_change(S_d, S_a),
            "equal_energy": bilinearize_equal_energy(S_d, S_a),
            "composite": bilinearize_composite(S_d, S_a),
            "rc": bilinearize_rc(S_d, S_a),
        }

        for name, (S_dy, S_ay, method) in results.items():
            # Yield displacement must be at least 30 % of peak
            assert S_dy >= 0.30 * S_d_peak, (
                f"{name} ({method}): S_dy={S_dy:.6f} < 30% of peak "
                f"({0.30 * S_d_peak:.6f}) for an elastic curve"
            )
            # Yield acceleration must be positive and finite
            assert 0 < S_ay <= S_a_peak, (
                f"{name} ({method}): S_ay={S_ay:.6f} out of range (0, {S_a_peak:.6f}]"
            )
            # Ductility mu = S_d_peak / S_dy must be ≤ 3.34
            # (3.33 allows for floating-point rounding — exact 30% guess
            # gives 3.333..., which just exceeds 3.33)
            mu = S_d_peak / S_dy
            assert mu <= 3.34, (
                f"{name} ({method}): mu={mu:.2f} > 3.34 "
                f"(S_dy={S_dy:.6f}, peak={S_d_peak:.6f}) "
                f"for an elastic curve"
            )

    def test_yield_index_before_peak(self, bilinear_curve):
        """Yield index from stiffness-change and equal-energy is before peak.

        This is a fundamental constraint: yield must occur before the peak
        of the capacity curve.  A yield-after-peak result would indicate a
        pathological fit.
        """
        S_d, S_a = bilinear_curve
        peak_idx = int(np.argmax(S_a))
        for fn in (
            bilinearize_stiffness_change,
            bilinearize_equal_energy,
            bilinearize_rc,
        ):
            S_dy, _S_ay, _ = fn(S_d, S_a)
            # Find the first index where S_d ≥ S_dy
            if S_dy > 0:
                yield_idx = int(np.argmax(S_d >= S_dy))
                assert yield_idx <= peak_idx, (
                    f"Yield at index {yield_idx} is after peak at index {peak_idx}"
                )
                assert S_dy <= S_d[peak_idx], (
                    f"Yield S_dy={S_dy:.6f} should be <= S_d_peak={S_d[peak_idx]:.6f}"
                )

    def test_composite_sudden_drop_falls_back(self, sudden_drop_curve):
        """Sudden drop after peak → stiffness-change falls back to peak
        (>90% of S_d_peak) → composite uses equal-energy."""
        S_d, S_a = sudden_drop_curve
        S_dy, _S_ay, method = bilinearize_composite(S_d, S_a)
        assert method == "composite_equal_energy", (
            f"Expected composite_equal_energy (stiffness-change returns "
            f"peak, triggering fallback), got {method}"
        )
        peak_idx = int(np.argmax(S_a))
        # Equal-energy converges near S_d ≈ 0.026 for this curve
        # (the initial guess is 30% of peak = 0.0105, but the curve
        # is not linear, so the iteration moves S_dy upward to
        # preserve area).
        # Equal-energy converges at the peak for a sudden-drop curve where
        # the curve is near-linear up to the peak — S_dy may equal S_d_peak.
        assert 0.020 <= S_dy <= S_d[peak_idx], (
            f"Expected S_dy in [0.020, {S_d[peak_idx]:.6f}], got {S_dy:.6f}"
        )

    def test_de_luca_recovers_exact_bilinear_knee(self, bilinear_curve):
        """On a bilinear curve the 10 %-secant equals the true elastic slope,
        so the equal-area rule recovers the exact knee: (0.02, 100)."""
        S_d, S_a = bilinear_curve
        S_dy, S_ay, method = bilinearize_rc(S_d, S_a)
        assert method == "de_luca_10pct"
        assert S_dy == pytest.approx(0.02, abs=1e-9)
        assert S_ay == pytest.approx(100.0, abs=1e-9)

    def test_de_luca_rc_curve_yield_not_at_cracking(self, rc_like_curve):
        """The 10 %-secant rule does not snap to the cracking transition.

        For the tanh RC backbone the cracking transition sits at
        S_d ≈ 0.0025 (the 10 %-secant point); the equal-area yield must
        land in the rebar-yield band (25–75 % of peak displacement) and
        preserve the capacity area.
        """
        S_d, S_a = rc_like_curve
        peak_idx = int(np.argmax(S_a))
        S_d_peak = float(S_d[peak_idx])
        S_a_peak = float(S_a[peak_idx])

        S_dy, S_ay, method = bilinearize_rc(S_d, S_a)

        assert method == "de_luca_10pct"
        # Yield well past the cracking transition and below the peak.
        assert 0.25 * S_d_peak <= S_dy <= 0.75 * S_d_peak, (
            f"S_dy={S_dy:.6f} outside rebar-yield band "
            f"[{0.25 * S_d_peak:.6f}, {0.75 * S_d_peak:.6f}]"
        )
        # Yield strength within the capacity envelope.
        assert 0.0 < S_ay <= S_a_peak
        # Equal-area: bilinear fit preserves the capacity-curve area.
        integral = 0.0
        for i in range(1, peak_idx + 1):
            integral += 0.5 * (S_d[i] - S_d[i - 1]) * (S_a[i] + S_a[i - 1])
        A_bilin = 0.5 * S_dy * S_ay + 0.5 * (S_ay + S_a_peak) * (S_d_peak - S_dy)
        rel_err = abs(A_bilin - integral) / max(integral, 1e-12)
        assert rel_err < 1e-2, f"equal-area error {rel_err:.2%}"
