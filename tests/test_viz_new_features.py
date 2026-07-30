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

    def test_biaxial_kwarg_threaded_to_plot(self):
        """use_biaxial kwarg is accepted by plot_plastic_hinge_formation."""
        if not _has_pyvista:
            pytest.skip("pyvista not installed")
        from fea_toolkit.plotting.viz import plot_plastic_hinge_formation
        data = {
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
            "pushover/+X/frame_fx_i": np.zeros((2, 1)),
            "pushover/+X/frame_fy_i": np.zeros((2, 1)),
            "pushover/+X/frame_fz_i": np.zeros((2, 1)),
            "pushover/+X/frame_mx_i": np.zeros((2, 1)),
            "pushover/+X/frame_my_i": np.array([[3.0], [6.0]]),
            "pushover/+X/frame_mz_i": np.array([[4.0], [8.0]]),
            "pushover/+X/frame_fx_j": np.zeros((2, 1)),
            "pushover/+X/frame_fy_j": np.zeros((2, 1)),
            "pushover/+X/frame_fz_j": np.zeros((2, 1)),
            "pushover/+X/frame_mx_j": np.zeros((2, 1)),
            "pushover/+X/frame_my_j": np.array([[3.0], [6.0]]),
            "pushover/+X/frame_mz_j": np.array([[4.0], [8.0]]),
            "pushover/+X/node_tag": np.array([1, 2]),
            "pushover/+X/node_disp_x": np.zeros((2, 2)),
            "pushover/+X/node_disp_y": np.zeros((2, 2)),
            "pushover/+X/node_disp_z": np.zeros((2, 2)),
        }
        pl = plot_plastic_hinge_formation(data, step=0, notebook=True,
                                          use_biaxial=True)
        assert pl is not None
        pl.close()

    def test_animate_accepts_biaxial_kwarg(self):
        """animate_pushover_deformation accepts use_biaxial kwarg."""
        if not _has_pyvista:
            pytest.skip("pyvista not installed")
        from fea_toolkit.plotting.viz import animate_pushover_deformation
        data = {
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
            "pushover/+X/frame_fx_i": np.zeros((2, 1)),
            "pushover/+X/frame_fy_i": np.zeros((2, 1)),
            "pushover/+X/frame_fz_i": np.zeros((2, 1)),
            "pushover/+X/frame_mx_i": np.zeros((2, 1)),
            "pushover/+X/frame_my_i": np.array([[3.0], [6.0]]),
            "pushover/+X/frame_mz_i": np.array([[4.0], [8.0]]),
            "pushover/+X/frame_fx_j": np.zeros((2, 1)),
            "pushover/+X/frame_fy_j": np.zeros((2, 1)),
            "pushover/+X/frame_fz_j": np.zeros((2, 1)),
            "pushover/+X/frame_mx_j": np.zeros((2, 1)),
            "pushover/+X/frame_my_j": np.array([[3.0], [6.0]]),
            "pushover/+X/frame_mz_j": np.array([[4.0], [8.0]]),
            "pushover/+X/node_tag": np.array([1, 2]),
            "pushover/+X/node_disp_x": np.zeros((2, 2)),
            "pushover/+X/node_disp_y": np.zeros((2, 2)),
            "pushover/+X/node_disp_z": np.zeros((2, 2)),
        }
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

    def test_legends_dont_raise_with_pyvista_plotter(self):
        """Adding both legends to a plotter does not raise."""
        if not _has_pyvista:
            pytest.skip("pyvista not installed")
        from fea_toolkit.plotting.viz import (
            _add_hinge_color_legend, _add_shell_color_legend,
        )
        plotter = pv.Plotter(notebook=True)
        _add_hinge_color_legend(plotter)
        _add_shell_color_legend(plotter)
        plotter.close()


class TestAnimationInterval:
    """Tests for animation_interval_ms parameter."""

    def test_animate_accepts_interval_param(self):
        """animate_pushover_deformation accepts animation_interval_ms."""
        if not _has_pyvista:
            pytest.skip("pyvista not installed")
        from fea_toolkit.plotting.viz import animate_pushover_deformation
        data = {
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
            "pushover/+X/frame_fx_i": np.zeros((2, 1)),
            "pushover/+X/frame_fy_i": np.zeros((2, 1)),
            "pushover/+X/frame_fz_i": np.zeros((2, 1)),
            "pushover/+X/frame_mx_i": np.zeros((2, 1)),
            "pushover/+X/frame_my_i": np.zeros((2, 1)),
            "pushover/+X/frame_mz_i": np.array([[5.0], [10.0]]),
            "pushover/+X/frame_fx_j": np.zeros((2, 1)),
            "pushover/+X/frame_fy_j": np.zeros((2, 1)),
            "pushover/+X/frame_fz_j": np.zeros((2, 1)),
            "pushover/+X/frame_mx_j": np.zeros((2, 1)),
            "pushover/+X/frame_my_j": np.zeros((2, 1)),
            "pushover/+X/frame_mz_j": np.array([[5.0], [10.0]]),
            "pushover/+X/node_tag": np.array([1, 2]),
            "pushover/+X/node_disp_x": np.zeros((2, 2)),
            "pushover/+X/node_disp_y": np.zeros((2, 2)),
            "pushover/+X/node_disp_z": np.zeros((2, 2)),
        }
        pl = animate_pushover_deformation(data, notebook=True,
                                          animation_interval_ms=100)
        assert pl is not None
        pl.close()