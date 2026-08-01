"""Tests for new visualization features: use_biaxial, physical shell damage, color legends."""

import math
import numpy as np
import pytest

try:
    import pyvista as pv
    _has_pyvista = True
    pv.OFF_SCREEN = True
except ImportError:
    _has_pyvista = False

# Module-level skip marker for pyvista-dependent tests
_needs_pyvista = pytest.mark.skipif(
    not _has_pyvista, reason="pyvista not installed"
)


def _make_pushover_data(frame_my_i, frame_mz_i, frame_my_j, frame_mz_j):
    """Build a minimal pushover NPZ-like data dict for viz smoke tests.

    Args:
        frame_my_i: (n_steps, n_frames) array of My at element i-end.
        frame_mz_i: (n_steps, n_frames) array of Mz at element i-end.
        frame_my_j: (n_steps, n_frames) array of My at element j-end.
        frame_mz_j: (n_steps, n_frames) array of Mz at element j-end.

    Returns:
        Dict[str, np.ndarray]: data dict accepted by the pushover
        visualization functions.
    """
    n_steps, n_frames = frame_my_i.shape
    zero_f = np.zeros((n_steps, n_frames))
    zero_n = np.zeros((n_steps, 2))
    return {
        "node_tag": np.array([1, 2]),
        "node_x": np.array([0.0, 4.0]),
        "node_y": np.array([0.0, 0.0]),
        "node_z": np.array([0.0, 3.0]),
        "frame_eid": np.array([0]),
        "frame_sap_id": np.array(["F1"]),
        "frame_node_i": np.array([1]),
        "frame_node_j": np.array([2]),
        "frame_sec_name": np.array(["COL"]),
        "frame_parent_sap_id": np.array([""]),
        "pushover/+X/step": np.array([0, 1]),
        "pushover/+X/frame_sap_id": np.array(["F1"]),
        "pushover/+X/frame_fx_i": zero_f,
        "pushover/+X/frame_fy_i": zero_f,
        "pushover/+X/frame_fz_i": zero_f,
        "pushover/+X/frame_mx_i": zero_f,
        "pushover/+X/frame_my_i": frame_my_i,
        "pushover/+X/frame_mz_i": frame_mz_i,
        "pushover/+X/frame_fx_j": zero_f,
        "pushover/+X/frame_fy_j": zero_f,
        "pushover/+X/frame_fz_j": zero_f,
        "pushover/+X/frame_mx_j": zero_f,
        "pushover/+X/frame_my_j": frame_my_j,
        "pushover/+X/frame_mz_j": frame_mz_j,
        "pushover/+X/node_tag": np.array([1, 2]),
        "pushover/+X/node_disp_x": zero_n,
        "pushover/+X/node_disp_y": zero_n,
        "pushover/+X/node_disp_z": zero_n,
    }


class TestHingeRatiosBiaxial:
    """Tests for _compute_hinge_ratios with use_biaxial parameter."""

    def test_uniaxial_default_mode(self):
        """Default mode (use_biaxial=False) uses Mz-only."""
        from fea_toolkit.plotting.viz import _compute_hinge_ratios
        forces = {
            "F1": {"mz_i": 10.0, "mz_j": 5.0, "my_i": 100.0, "my_j": 50.0},
            "F2": {"mz_i": 8.0, "mz_j": 12.0, "my_i": 80.0, "my_j": 120.0},
        }
        ratios = _compute_hinge_ratios(forces)
        assert "F1" in ratios
        assert abs(ratios["F1"][0] - 1.0) < 1e-9
        assert abs(ratios["F1"][1] - 0.5) < 1e-9

    def test_biaxial_srss_mode(self):
        """Biaxial mode combines My and Mz via SRSS."""
        from fea_toolkit.plotting.viz import _compute_hinge_ratios
        forces = {
            "F1": {"mz_i": 3.0, "mz_j": 0.0, "my_i": 4.0, "my_j": 0.0},
        }
        ratios = _compute_hinge_ratios(forces, use_biaxial=True)
        assert "F1" in ratios
        assert abs(ratios["F1"][0] - math.sqrt(2.0)) < 1e-9

    def test_biaxial_with_all_zero_forces(self):
        """Biaxial handles all-zero forces without division by zero."""
        from fea_toolkit.plotting.viz import _compute_hinge_ratios
        forces = {
            "F1": {"mz_i": 0.0, "mz_j": 0.0, "my_i": 0.0, "my_j": 0.0},
        }
        bi = _compute_hinge_ratios(forces, use_biaxial=True)
        assert "F1" in bi
        assert bi["F1"][0] == 0.0
        assert bi["F1"][1] == 0.0

    def test_biaxial_with_single_element(self):
        """Single-element biaxial works correctly."""
        from fea_toolkit.plotting.viz import _compute_hinge_ratios
        forces = {
            "F1": {"mz_i": 10.0, "mz_j": 20.0, "my_i": 5.0, "my_j": 10.0},
        }
        ratios = _compute_hinge_ratios(forces, use_biaxial=True)
        assert abs(ratios["F1"][0] - math.sqrt(0.5)) < 1e-9

    @_needs_pyvista
    def test_biaxial_kwarg_threaded_to_plot(self):
        """use_biaxial kwarg is accepted by plot_plastic_hinge_formation."""
        from fea_toolkit.plotting.viz import plot_plastic_hinge_formation
        data = _make_pushover_data(
            np.array([[3.0], [6.0]]),
            np.array([[4.0], [8.0]]),
            np.array([[3.0], [6.0]]),
            np.array([[4.0], [8.0]]),
        )
        pl = plot_plastic_hinge_formation(data, step=0, notebook=True,
                                          use_biaxial=True)
        assert pl is not None
        pl.close()

    @_needs_pyvista
    def test_animate_accepts_biaxial_kwarg(self):
        """animate_pushover_deformation accepts use_biaxial kwarg."""
        from fea_toolkit.plotting.viz import animate_pushover_deformation
        data = _make_pushover_data(
            np.array([[3.0], [6.0]]),
            np.array([[4.0], [8.0]]),
            np.array([[3.0], [6.0]]),
            np.array([[4.0], [8.0]]),
        )
        pl = animate_pushover_deformation(data, notebook=True, use_biaxial=True)
        assert pl is not None
        pl.close()


class TestShellDamagePhysical:
    """Tests for _compute_shell_damage with physical stress parameters."""

    def test_physical_mode_with_stress_and_thickness(self):
        """Physical mode uses von Mises + bending stress vs yield stress."""
        from fea_toolkit.plotting.viz import _compute_shell_damage
        shells = {"W1": {"Nx": 1e6, "Ny": 0.0, "Nxy": 0.0,
                         "Mx": 1000.0, "My": 0.0, "Mxy": 0.0}}
        indices = _compute_shell_damage(shells, yield_stress=40e6, thickness=0.15)
        assert "W1" in indices
        D, m_mag = indices["W1"]
        assert D > 0
        assert D < 1.0

    def test_physical_mode_higher_damage(self):
        """Higher forces produce higher damage index."""
        from fea_toolkit.plotting.viz import _compute_shell_damage
        shells = {"W1": {"Nx": 20e6, "Ny": 10e6, "Nxy": 5e6,
                         "Mx": 50000.0, "My": 30000.0, "Mxy": 10000.0}}
        indices = _compute_shell_damage(shells, yield_stress=40e6, thickness=0.15)
        D, m_mag = indices["W1"]
        assert D > 0.5

    def test_physical_mode_missing_params_falls_back(self):
        """Missing yield_stress or thickness falls back to range-based."""
        from fea_toolkit.plotting.viz import _compute_shell_damage
        shells = {"W1": {"Nx": 100.0, "Ny": 50.0, "Nxy": 10.0,
                         "Mx": 5.0, "My": 3.0, "Mxy": 1.0}}
        indices = _compute_shell_damage(shells)
        assert "W1" in indices
        D, _ = indices["W1"]
        assert D > 0

    def test_physical_mode_with_none_thickness(self):
        """None thickness should fall back to range-based."""
        from fea_toolkit.plotting.viz import _compute_shell_damage
        shells = {"W1": {"Nx": 100.0, "Ny": 50.0, "Nxy": 10.0,
                         "Mx": 5.0, "My": 3.0, "Mxy": 1.0}}
        indices = _compute_shell_damage(shells, yield_stress=40e6, thickness=None)
        assert "W1" in indices
        D, _ = indices["W1"]
        assert D > 0


class TestColorLegendHelpers:
    """Smoke tests for color legend helper functions."""

    def test_hinge_color_legend_importable(self):
        """_add_hinge_color_legend can be imported."""
        from fea_toolkit.plotting.viz import _add_hinge_color_legend
        assert callable(_add_hinge_color_legend)

    def test_shell_color_legend_importable(self):
        """_add_shell_color_legend can be imported."""
        from fea_toolkit.plotting.viz import _add_shell_color_legend
        assert callable(_add_shell_color_legend)

    @_needs_pyvista
    def test_legends_dont_raise_with_pyvista_plotter(self):
        """Adding both legends to a plotter does not raise."""
        from fea_toolkit.plotting.viz import (
            _add_hinge_color_legend, _add_shell_color_legend,
        )
        plotter = pv.Plotter(notebook=True)
        # PyVista 0.44+ requires a mapper (mesh with scalars) before
        # add_scalar_bar() can be called — add a dummy mesh so the mapper exists.
        dummy = pv.PolyData(np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]))
        dummy["scalars"] = np.array([0.0, 1.0])
        plotter.add_mesh(dummy)
        _add_hinge_color_legend(plotter)
        _add_shell_color_legend(plotter)
        plotter.close()


class TestHingeColormap:
    """Smoke tests for hinge-ratio colour mapping helpers."""

    def _assert_close(self, a, b, tol=1e-6):
        assert abs(a[0] - b[0]) < tol
        assert abs(a[1] - b[1]) < tol
        assert abs(a[2] - b[2]) < tol

    def test_ratio_to_color_boundaries_match_sampled_cmap(self):
        """Ratios 0.0/0.5/1.0 map exactly to cmap samples at 0/0.5/1.0."""
        from fea_toolkit.plotting.viz import (
            _ratio_to_color, _sample_cmap,
        )
        samples = _sample_cmap([0.0, 0.5, 1.0], "plasma")
        self._assert_close(_ratio_to_color(0.0, 1.0), samples[0])
        # 0.5 boundary → exactly the mid (yielding) colour.
        self._assert_close(_ratio_to_color(0.5, 1.0), samples[1])
        self._assert_close(_ratio_to_color(1.0, 1.0), samples[2])

    def test_ratio_to_color_interpolates_between_samples(self):
        """Mid-segment ratios interpolate linearly between samples."""
        from fea_toolkit.plotting.viz import (
            _ratio_to_color, _sample_cmap,
        )
        c0, c1, c2 = _sample_cmap([0.0, 0.5, 1.0], "plasma")
        # norm = 0.25 → halfway between c0 and c1.
        mid_low = tuple(0.5 * (c0[i] + c1[i]) for i in range(3))
        self._assert_close(_ratio_to_color(0.25, 1.0), mid_low)
        # norm = 0.75 → halfway between c1 and c2.
        mid_high = tuple(0.5 * (c1[i] + c2[i]) for i in range(3))
        self._assert_close(_ratio_to_color(0.75, 1.0), mid_high)

    def test_ratio_to_color_clamps_above_max(self):
        """Ratios above max_r clamp to the fully-yielded colour."""
        from fea_toolkit.plotting.viz import (
            _ratio_to_color, _sample_cmap,
        )
        c2 = _sample_cmap([1.0], "plasma")[0]
        self._assert_close(_ratio_to_color(2.0, 1.0), c2)
        self._assert_close(_ratio_to_color(5.0, 2.0), c2)

    def test_ratio_to_color_zero_max_r(self):
        """max_r ≈ 0 maps to the elastic (low) colour without error."""
        from fea_toolkit.plotting.viz import (
            _ratio_to_color, _sample_cmap,
        )
        c0 = _sample_cmap([0.0], "plasma")[0]
        self._assert_close(_ratio_to_color(0.0, 0.0), c0)
        self._assert_close(_ratio_to_color(1.0, 1e-15), c0)

    def test_custom_colormap(self):
        """Custom colormap names are honoured."""
        from fea_toolkit.plotting.viz import (
            _ratio_to_color, _sample_cmap,
        )
        samples = _sample_cmap([0.0, 0.5, 1.0], "viridis")
        self._assert_close(_ratio_to_color(0.0, 1.0, cmap_name="viridis"),
                           samples[0])
        self._assert_close(_ratio_to_color(0.5, 1.0, cmap_name="viridis"),
                           samples[1])
        self._assert_close(_ratio_to_color(1.0, 1.0, cmap_name="viridis"),
                           samples[2])

    def test_invalid_colormap_falls_back(self):
        """Unknown colormap names fall back to the fixed palette."""
        from fea_toolkit.plotting.viz import (
            _ratio_to_color, _sample_cmap,
        )
        fallback = [(0.3, 0.45, 0.69), (0.9, 0.8, 0.2), (0.9, 0.25, 0.2)]
        assert _sample_cmap([0.0, 0.5, 1.0], "no-such-cmap-xyz") == fallback
        self._assert_close(_ratio_to_color(0.0, 1.0,
                                           cmap_name="no-such-cmap-xyz"),
                           fallback[0])

    def test_rgb_to_hex(self):
        """_rgb_to_hex renders #RRGGBB and clamps out-of-range values."""
        from fea_toolkit.plotting.viz import _rgb_to_hex
        assert _rgb_to_hex((0.0, 0.0, 0.0)) == "#000000"
        assert _rgb_to_hex((1.0, 1.0, 1.0)) == "#ffffff"
        assert _rgb_to_hex((1.0, 0.5, 0.0)) == "#ff8000"
        assert _rgb_to_hex((1.5, -0.2, 0.5)) == "#ff0080"


class TestAnimationInterval:
    """Tests for animation_interval_ms parameter."""

    @_needs_pyvista
    def test_animate_accepts_interval_param(self):
        """animate_pushover_deformation accepts animation_interval_ms."""
        from fea_toolkit.plotting.viz import animate_pushover_deformation
        data = _make_pushover_data(
            np.zeros((2, 1)),
            np.array([[5.0], [10.0]]),
            np.zeros((2, 1)),
            np.array([[5.0], [10.0]]),
        )
        pl = animate_pushover_deformation(data, notebook=True,
                                          animation_interval_ms=100)
        assert pl is not None
        pl.close()
