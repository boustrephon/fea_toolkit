"""Tests for ``fea_toolkit.plotting.viz_pushover`` — pushover-result figures.

Covers the plastic-hinge heatmap / formation maps, shell damage map,
pushover envelope, deformation animation, and frame force evolution.
Uses synthetic NPZ-like data dicts — no OpenSees.
"""

import warnings

import matplotlib
import numpy as np
import pytest

try:
    import pyvista as pv

    _has_pyvista = True
    pv.OFF_SCREEN = True  # prevent interactive windows during tests
except ImportError:
    _has_pyvista = False

# Suppress PyVista's Jupyter backend warning — fires spuriously when
# pv.Plotter(notebook=True) is constructed in a non-Jupyter environment.
# This is a PyVista issue where it attempts to load its trame/Jupyter
# backend even in OFF_SCREEN mode.
warnings.filterwarnings("ignore", message="Failed to use notebook backend")


@pytest.fixture(autouse=True, scope="module")
def _configure_matplotlib():
    """Set the Agg backend once per module and close all figures on exit."""
    matplotlib.use("Agg")
    yield
    import matplotlib.pyplot as plt

    plt.close("all")


# ============================================================================
# Plastic-hinge heatmap
# ============================================================================


class TestPushoverHeatmap:
    """Tests for plot_plastic_hinge_heatmap (Phase 4a2)."""

    @pytest.fixture
    def raw_pushover_data(self):
        """Synthetic per-step pushover data for 3 elements × 5 steps.

        Element IDs: "1", "2", "3" with mid-heights 1.5, 4.5, 7.5 (m).
        Element 3 is NOT present in step 3 (missing data → gray).
        """
        return [
            {
                "frame_forces": {
                    "1": {"mz_i": 10.0, "mz_j": -8.0},
                    "2": {"mz_i": 20.0, "mz_j": -15.0},
                    "3": {"mz_i": 5.0, "mz_j": -6.0},
                },
                "node_coords": {"1": (0, 0, 0), "2": (4, 0, 3), "3": (4, 0, 6), "4": (4, 0, 9)},
            },
            {
                "frame_forces": {
                    "1": {"mz_i": 15.0, "mz_j": -12.0},
                    "2": {"mz_i": 28.0, "mz_j": -20.0},
                    "3": {"mz_i": 8.0, "mz_j": -9.0},
                },
                "node_coords": {"1": (0, 0, 0), "2": (4, 0, 3), "3": (4, 0, 6), "4": (4, 0, 9)},
            },
            {
                "frame_forces": {
                    "1": {"mz_i": 18.0, "mz_j": -14.0},
                    "2": {"mz_i": 25.0, "mz_j": -18.0},
                    "3": {"mz_i": 6.0, "mz_j": -7.0},
                },
                "node_coords": {"1": (0, 0, 0), "2": (4, 0, 3), "3": (4, 0, 6), "4": (4, 0, 9)},
            },
            {
                "frame_forces": {
                    "1": {"mz_i": 22.0, "mz_j": -16.0},
                    "2": {"mz_i": 20.0, "mz_j": -10.0},
                    # Element 3 MISSING — no data for this step
                },
                "node_coords": {"1": (0, 0, 0), "2": (4, 0, 3), "3": (4, 0, 6), "4": (4, 0, 9)},
            },
            {
                "frame_forces": {
                    "1": {"mz_i": 30.0, "mz_j": -25.0},
                    "2": {"mz_i": 18.0, "mz_j": -12.0},
                    "3": {"mz_i": 12.0, "mz_j": -10.0},
                },
                "node_coords": {"1": (0, 0, 0), "2": (4, 0, 3), "3": (4, 0, 6), "4": (4, 0, 9)},
            },
        ]

    def test_heatmap_basic(self, raw_pushover_data):
        """Heatmap returns a matplotlib Figure with expected shape."""
        import matplotlib.pyplot as plt

        from fea_toolkit.plotting.viz import plot_plastic_hinge_heatmap

        fig = plot_plastic_hinge_heatmap(
            raw_pushover_data,
            title="Test heatmap",
            figsize=(6, 4),
        )
        assert fig is not None, "Heatmap should return a Figure"
        assert fig.axes, "Figure should have at least one axis"

        # Verify title
        assert "Test heatmap" in fig.axes[0].get_title()

        # Verify axes labels
        assert fig.axes[0].get_xlabel() == "Push step"
        assert "elevation" in fig.axes[0].get_ylabel().lower()
        plt.close(fig)

    def test_heatmap_missing_data_gray(self, raw_pushover_data):
        """Element missing in a step should show as gray (no data)."""
        import matplotlib.pyplot as plt

        from fea_toolkit.plotting.viz import plot_plastic_hinge_heatmap

        fig = plot_plastic_hinge_heatmap(
            raw_pushover_data,
            figsize=(6, 4),
        )
        assert fig is not None

        # pcolormesh creates a QuadMesh — check it exists in collections
        assert len(fig.axes[0].collections) > 0, "QuadMesh should exist"

        # Check colorbar tick labels include "No data"
        # The colorbar is the last axis in the figure
        cbar_ax = fig.axes[-1]
        tick_labels = cbar_ax.get_yticklabels()
        tick_texts = [t.get_text() for t in tick_labels]
        assert any("No data" in txt for txt in tick_texts), (
            f"Expected 'No data' in colorbar ticks, got: {tick_texts}"
        )
        plt.close(fig)

    def test_heatmap_roof_drift_xaxis(self, raw_pushover_data):
        """Heatmap with drift-based X-axis."""
        import matplotlib.pyplot as plt

        from fea_toolkit.plotting.viz import plot_plastic_hinge_heatmap

        drifts = [0.1, 0.3, 0.6, 1.0, 1.8]  # 5 drift values
        fig = plot_plastic_hinge_heatmap(
            raw_pushover_data,
            xaxis="drift",
            drifts=drifts,
            figsize=(6, 4),
        )
        assert fig is not None
        assert "drift" in fig.axes[0].get_xlabel().lower()
        plt.close(fig)

    def test_heatmap_save_path(self, raw_pushover_data, tmp_path):
        """Heatmap saves to file when save_path is provided."""
        import os

        import matplotlib.pyplot as plt

        from fea_toolkit.plotting.viz import plot_plastic_hinge_heatmap

        save_path = str(tmp_path / "test_heatmap.png")
        fig = plot_plastic_hinge_heatmap(
            raw_pushover_data,
            save_path=save_path,
            figsize=(6, 4),
        )
        assert fig is not None
        # Check the file was created
        assert os.path.exists(save_path)
        plt.close(fig)

    def test_heatmap_no_data(self):
        """Heatmap with empty data returns None."""
        from fea_toolkit.plotting.viz import plot_plastic_hinge_heatmap

        fig = plot_plastic_hinge_heatmap([], figsize=(6, 4))
        assert fig is None


# ============================================================================
# Shell damage map
# ============================================================================


class TestShellDamageMap:
    """Tests for plot_shell_damage_map (Phase 4b)."""

    @pytest.fixture(autouse=True)
    def _skip_if_no_pyvista(self):
        if not _has_pyvista:
            pytest.skip("pyvista not installed")

    @pytest.fixture
    def npz_with_shells(self):
        """NPZ dict with 2 frame elements + 2 shell elements, 3 push steps."""
        return {
            "node_tag": np.array([1, 2, 3, 4, 5]),
            "node_sap_id": np.array(["1", "2", "3", "4", "5"]),
            "node_x": np.array([0.0, 4.0, 4.0, 0.0, 4.0]),
            "node_y": np.array([0.0, 0.0, 0.0, 3.0, 3.0]),
            "node_z": np.array([0.0, 0.0, 3.0, 0.0, 3.0]),
            "frame_eid": np.array([0, 1]),
            "frame_sap_id": np.array(["F1", "F2"]),
            "frame_node_i": np.array([1, 2]),
            "frame_node_j": np.array([2, 3]),
            "frame_sec_name": np.array(["COL", "BEAM"]),
            "frame_parent_sap_id": np.array(["", ""]),
            "shell_eid": np.array([0, 1]),
            "shell_sap_id": np.array(["W1", "W2"]),
            "shell_sec_name": np.array(["WALL", "WALL"]),
            "shell_node_1": np.array([1, 2]),
            "shell_node_2": np.array([2, 5]),
            "shell_node_3": np.array([5, 3]),
            "shell_node_4": np.array([4, 4]),
            # Pushover data: 3 steps
            "pushover/+X/step": np.array([0, 1, 2]),
            "pushover/+X/shell_sap_id": np.array(["W1", "W2"]),
            "pushover/+X/shell_Nx": np.array(
                [
                    [100.0, 50.0],  # step 0
                    [200.0, 80.0],  # step 1
                    [300.0, 120.0],  # step 2
                ]
            ),
            "pushover/+X/shell_Ny": np.array(
                [
                    [50.0, 25.0],
                    [100.0, 40.0],
                    [150.0, 60.0],
                ]
            ),
            "pushover/+X/shell_Nxy": np.array(
                [
                    [10.0, 5.0],
                    [20.0, 8.0],
                    [30.0, 12.0],
                ]
            ),
            "pushover/+X/shell_Mx": np.array(
                [
                    [5.0, 3.0],
                    [10.0, 5.0],
                    [15.0, 8.0],
                ]
            ),
            "pushover/+X/shell_My": np.array(
                [
                    [3.0, 2.0],
                    [6.0, 3.0],
                    [9.0, 5.0],
                ]
            ),
            "pushover/+X/shell_Mxy": np.array(
                [
                    [1.0, 0.5],
                    [2.0, 1.0],
                    [3.0, 1.5],
                ]
            ),
            # Node displacements
            "pushover/+X/node_tag": np.array([1, 2, 3, 4, 5]),
            "pushover/+X/node_disp_x": np.array(
                [
                    [0.0, 0.01, 0.02, 0.0, 0.01],
                    [0.0, 0.02, 0.04, 0.0, 0.02],
                    [0.0, 0.03, 0.06, 0.0, 0.03],
                ]
            ),
            "pushover/+X/node_disp_y": np.zeros((3, 5)),
            "pushover/+X/node_disp_z": np.zeros((3, 5)),
            # Frame data for pushover (minimal — only needed for _resolve_pushover_data)
            "pushover/+X/frame_sap_id": np.array(["F1", "F2"]),
            "pushover/+X/frame_fx_i": np.zeros((3, 2)),
            "pushover/+X/frame_fy_i": np.zeros((3, 2)),
            "pushover/+X/frame_fz_i": np.zeros((3, 2)),
            "pushover/+X/frame_mx_i": np.zeros((3, 2)),
            "pushover/+X/frame_my_i": np.zeros((3, 2)),
            "pushover/+X/frame_mz_i": np.zeros((3, 2)),
            "pushover/+X/frame_fx_j": np.zeros((3, 2)),
            "pushover/+X/frame_fy_j": np.zeros((3, 2)),
            "pushover/+X/frame_fz_j": np.zeros((3, 2)),
            "pushover/+X/frame_mx_j": np.zeros((3, 2)),
            "pushover/+X/frame_my_j": np.zeros((3, 2)),
            "pushover/+X/frame_mz_j": np.zeros((3, 2)),
        }

    def test_shell_damage_computed_correctly(self, npz_with_shells):
        """_compute_shell_damage returns a dict with positive values."""
        from fea_toolkit.plotting.viz import _compute_shell_damage

        step0_shells = {
            "W1": {"Nx": 100.0, "Ny": 50.0, "Nxy": 10.0, "Mx": 5.0, "My": 3.0, "Mxy": 1.0}
        }
        indices = _compute_shell_damage(step0_shells)
        assert "W1" in indices
        D, m_mag = indices["W1"]
        assert D > 0
        assert m_mag >= 0

    def test_shell_damage_map_notebook(self, npz_with_shells):
        """plot_shell_damage_map with notebook=True returns plotter."""
        from fea_toolkit.plotting.viz import plot_shell_damage_map

        pl = plot_shell_damage_map(npz_with_shells, notebook=True)
        assert pl is not None
        pl.close()

    def test_shell_damage_map_no_shells(self):
        """With no shell data, function returns None."""
        from fea_toolkit.plotting.viz import plot_shell_damage_map

        # Provide 1 step with 0 shells, plus minimal geometry arrays
        empty_npz = {
            "pushover/+X/step": np.array([0]),
            "pushover/+X/shell_sap_id": np.array([]),
            "pushover/+X/shell_Nx": np.empty((1, 0)),
            "pushover/+X/shell_Ny": np.empty((1, 0)),
            "pushover/+X/shell_Nxy": np.empty((1, 0)),
            "pushover/+X/shell_Mx": np.empty((1, 0)),
            "pushover/+X/shell_My": np.empty((1, 0)),
            "pushover/+X/shell_Mxy": np.empty((1, 0)),
            # Node displacements (empty — no displacements recorded)
            "pushover/+X/node_tag": np.array([]),
            "pushover/+X/node_disp_x": np.empty((1, 0)),
            "pushover/+X/node_disp_y": np.empty((1, 0)),
            "pushover/+X/node_disp_z": np.empty((1, 0)),
            # Frame arrays needed to avoid early exit in _resolve_pushover_data
            "pushover/+X/frame_sap_id": np.array([]),
            # Also need geometry frame arrays for _resolve_pushover_data to not crash
            "frame_node_i": np.array([]),
            "frame_node_j": np.array([]),
            "frame_sap_id": np.array([]),
            "pushover/+X/frame_fx_i": np.empty((1, 0)),
            "pushover/+X/frame_fy_i": np.empty((1, 0)),
            "pushover/+X/frame_fz_i": np.empty((1, 0)),
            "pushover/+X/frame_mx_i": np.empty((1, 0)),
            "pushover/+X/frame_my_i": np.empty((1, 0)),
            "pushover/+X/frame_mz_i": np.empty((1, 0)),
            "pushover/+X/frame_fx_j": np.empty((1, 0)),
            "pushover/+X/frame_fy_j": np.empty((1, 0)),
            "pushover/+X/frame_fz_j": np.empty((1, 0)),
            "pushover/+X/frame_mx_j": np.empty((1, 0)),
            "pushover/+X/frame_my_j": np.empty((1, 0)),
            "pushover/+X/frame_mz_j": np.empty((1, 0)),
            "node_tag": np.array([]),
            "node_x": np.array([]),
            "node_y": np.array([]),
            "node_z": np.array([]),
        }
        pl = plot_shell_damage_map(empty_npz, notebook=True)
        assert pl is None

    def test_shell_damage_map_static_step(self, npz_with_shells):
        """Static step renders without slider."""
        from fea_toolkit.plotting.viz import plot_shell_damage_map

        pl = plot_shell_damage_map(npz_with_shells, step=1, notebook=True)
        assert pl is not None
        pl.close()


# ============================================================================
# Pushover envelope
# ============================================================================


class TestPushoverEnvelope:
    """Tests for plot_pushover_envelope (Phase 4c)."""

    @pytest.fixture(autouse=True)
    def _skip_if_no_pyvista(self):
        if not _has_pyvista:
            pytest.skip("pyvista not installed")

    @pytest.fixture
    def npz_with_frames(self):
        """NPZ dict with 2 frame elements, 3 push steps, varying moments."""
        return {
            "node_tag": np.array([1, 2, 3]),
            "node_sap_id": np.array(["1", "2", "3"]),
            "node_x": np.array([0.0, 4.0, 8.0]),
            "node_y": np.array([0.0, 0.0, 0.0]),
            "node_z": np.array([0.0, 3.0, 6.0]),
            "frame_eid": np.array([0, 1]),
            "frame_sap_id": np.array(["F1", "F2"]),
            "frame_node_i": np.array([1, 2]),
            "frame_node_j": np.array([2, 3]),
            "frame_sec_name": np.array(["COL", "BEAM"]),
            "frame_parent_sap_id": np.array(["", ""]),
            "shell_eid": np.array([]),
            # Pushover data: 3 steps, 2 frames
            "pushover/+X/step": np.array([0, 1, 2]),
            "pushover/+X/frame_sap_id": np.array(["F1", "F2"]),
            "pushover/+X/frame_fx_i": np.zeros((3, 2)),
            "pushover/+X/frame_fy_i": np.zeros((3, 2)),
            "pushover/+X/frame_fz_i": np.zeros((3, 2)),
            "pushover/+X/frame_mx_i": np.zeros((3, 2)),
            "pushover/+X/frame_my_i": np.zeros((3, 2)),
            "pushover/+X/frame_mz_i": np.array(
                [
                    [10.0, 5.0],  # step 0: F1=10, F2=5
                    [25.0, 8.0],  # step 1: F1=25 (peak), F2=8
                    [15.0, 12.0],  # step 2: F1=15, F2=12 (peak)
                ]
            ),
            "pushover/+X/frame_fx_j": np.zeros((3, 2)),
            "pushover/+X/frame_fy_j": np.zeros((3, 2)),
            "pushover/+X/frame_fz_j": np.zeros((3, 2)),
            "pushover/+X/frame_mx_j": np.zeros((3, 2)),
            "pushover/+X/frame_my_j": np.zeros((3, 2)),
            "pushover/+X/frame_mz_j": np.array(
                [
                    [-8.0, -4.0],  # step 0
                    [-20.0, -6.0],  # step 1: F1 peak j
                    [-12.0, -10.0],  # step 2: F2 peak j
                ]
            ),
            # Node displacements (empty)
            "pushover/+X/node_tag": np.array([]),
            "pushover/+X/node_disp_x": np.empty((3, 0)),
            "pushover/+X/node_disp_y": np.empty((3, 0)),
            "pushover/+X/node_disp_z": np.empty((3, 0)),
        }

    def test_envelope_notebook(self, npz_with_frames):
        """Envelope with notebook=True returns a plotter."""
        from fea_toolkit.plotting.viz import plot_pushover_envelope

        pl = plot_pushover_envelope(
            npz_with_frames,
            quantity="Mz",
            notebook=True,
        )
        assert pl is not None
        pl.close()

    def test_envelope_tube_mode(self, npz_with_frames):
        """Envelope with mode='tube' works."""
        from fea_toolkit.plotting.viz import plot_pushover_envelope

        pl = plot_pushover_envelope(
            npz_with_frames,
            quantity="Mz",
            mode="tube",
            notebook=True,
        )
        assert pl is not None
        pl.close()

    def test_envelope_force_quantity(self, npz_with_frames):
        """Envelope with force quantity 'Fx' works."""
        from fea_toolkit.plotting.viz import plot_pushover_envelope

        pl = plot_pushover_envelope(
            npz_with_frames,
            quantity="Fx",
            notebook=True,
        )
        assert pl is not None
        pl.close()

    @pytest.fixture
    def npz_with_shells_and_frames(self):
        """NPZ dict with 2 frame + 2 shell elements, 3 push steps for envelope."""
        return {
            "node_tag": np.array([1, 2, 3, 4, 5]),
            "node_sap_id": np.array(["1", "2", "3", "4", "5"]),
            "node_x": np.array([0.0, 4.0, 4.0, 0.0, 4.0]),
            "node_y": np.array([0.0, 0.0, 0.0, 3.0, 3.0]),
            "node_z": np.array([0.0, 0.0, 3.0, 0.0, 3.0]),
            "frame_eid": np.array([0, 1]),
            "frame_sap_id": np.array(["F1", "F2"]),
            "frame_node_i": np.array([1, 2]),
            "frame_node_j": np.array([2, 3]),
            "frame_sec_name": np.array(["COL", "BEAM"]),
            "frame_parent_sap_id": np.array(["", ""]),
            "shell_eid": np.array([0, 1]),
            "shell_sap_id": np.array(["W1", "W2"]),
            "shell_sec_name": np.array(["WALL", "WALL"]),
            "shell_node_1": np.array([1, 2]),
            "shell_node_2": np.array([2, 5]),
            "shell_node_3": np.array([5, 3]),
            "shell_node_4": np.array([4, 4]),
            "pushover/+X/step": np.array([0, 1, 2]),
            "pushover/+X/frame_sap_id": np.array(["F1", "F2"]),
            "pushover/+X/frame_fx_i": np.zeros((3, 2)),
            "pushover/+X/frame_fy_i": np.zeros((3, 2)),
            "pushover/+X/frame_fz_i": np.zeros((3, 2)),
            "pushover/+X/frame_mx_i": np.zeros((3, 2)),
            "pushover/+X/frame_my_i": np.zeros((3, 2)),
            "pushover/+X/frame_mz_i": np.array(
                [
                    [10.0, 5.0],
                    [25.0, 8.0],
                    [15.0, 12.0],
                ]
            ),
            "pushover/+X/frame_fx_j": np.zeros((3, 2)),
            "pushover/+X/frame_fy_j": np.zeros((3, 2)),
            "pushover/+X/frame_fz_j": np.zeros((3, 2)),
            "pushover/+X/frame_mx_j": np.zeros((3, 2)),
            "pushover/+X/frame_my_j": np.zeros((3, 2)),
            "pushover/+X/frame_mz_j": np.array(
                [
                    [-8.0, -4.0],
                    [-20.0, -6.0],
                    [-12.0, -10.0],
                ]
            ),
            "pushover/+X/shell_sap_id": np.array(["W1", "W2"]),
            "pushover/+X/shell_Nx": np.array(
                [
                    [100.0, 50.0],
                    [200.0, 80.0],
                    [300.0, 120.0],
                ]
            ),
            "pushover/+X/shell_Ny": np.array(
                [
                    [50.0, 25.0],
                    [100.0, 40.0],
                    [150.0, 60.0],
                ]
            ),
            "pushover/+X/shell_Nxy": np.array(
                [
                    [10.0, 5.0],
                    [20.0, 8.0],
                    [30.0, 12.0],
                ]
            ),
            "pushover/+X/shell_Mx": np.array(
                [
                    [5.0, 3.0],
                    [10.0, 5.0],
                    [15.0, 8.0],
                ]
            ),
            "pushover/+X/shell_My": np.array(
                [
                    [3.0, 2.0],
                    [6.0, 3.0],
                    [9.0, 5.0],
                ]
            ),
            "pushover/+X/shell_Mxy": np.array(
                [
                    [1.0, 0.5],
                    [2.0, 1.0],
                    [3.0, 1.5],
                ]
            ),
            "pushover/+X/node_tag": np.array([1, 2, 3, 4, 5]),
            "pushover/+X/node_disp_x": np.array(
                [
                    [0.0, 0.01, 0.02, 0.0, 0.01],
                    [0.0, 0.02, 0.04, 0.0, 0.02],
                    [0.0, 0.03, 0.06, 0.0, 0.03],
                ]
            ),
            "pushover/+X/node_disp_y": np.zeros((3, 5)),
            "pushover/+X/node_disp_z": np.zeros((3, 5)),
        }

    def test_envelope_with_shells(self, npz_with_shells_and_frames):
        """Envelope renders shells and returns a plotter."""
        from fea_toolkit.plotting.viz import plot_pushover_envelope

        pl = plot_pushover_envelope(
            npz_with_shells_and_frames,
            quantity="Mz",
            notebook=True,
        )
        assert pl is not None
        pl.close()

    def test_envelope_no_data(self):
        """Envelope with no data returns None."""
        from fea_toolkit.plotting.viz import plot_pushover_envelope

        empty = {"pushover/+X/step": np.array([])}
        pl = plot_pushover_envelope(empty, notebook=True)
        assert pl is None


# ============================================================================
# Plastic hinge formation
# ============================================================================


class TestPlasticHingeFormation:
    """Tests for plot_plastic_hinge_formation (Phase 4a1)."""

    @pytest.fixture(autouse=True)
    def _skip_if_no_pyvista(self):
        if not _has_pyvista:
            pytest.skip("pyvista not installed")

    @pytest.fixture
    def npz_hinge_data(self):
        """NPZ dict with 2 frame elements + hinge forces, 3 push steps."""
        return {
            "node_tag": np.array([1, 2, 3]),
            "node_sap_id": np.array(["1", "2", "3"]),
            "node_x": np.array([0.0, 4.0, 4.0]),
            "node_y": np.array([0.0, 0.0, 0.0]),
            "node_z": np.array([0.0, 0.0, 3.0]),
            "frame_eid": np.array([0, 1]),
            "frame_sap_id": np.array(["F1", "F2"]),
            "frame_node_i": np.array([1, 2]),
            "frame_node_j": np.array([2, 3]),
            "frame_sec_name": np.array(["COL", "BEAM"]),
            "frame_parent_sap_id": np.array(["", ""]),
            "pushover/+X/step": np.array([0, 1, 2]),
            "pushover/+X/frame_sap_id": np.array(["F1", "F2"]),
            "pushover/+X/frame_fx_i": np.zeros((3, 2)),
            "pushover/+X/frame_fy_i": np.zeros((3, 2)),
            "pushover/+X/frame_fz_i": np.zeros((3, 2)),
            "pushover/+X/frame_mx_i": np.zeros((3, 2)),
            "pushover/+X/frame_my_i": np.zeros((3, 2)),
            "pushover/+X/frame_mz_i": np.array(
                [
                    [10.0, 5.0],
                    [25.0, 8.0],
                    [15.0, 12.0],
                ]
            ),
            "pushover/+X/frame_fx_j": np.zeros((3, 2)),
            "pushover/+X/frame_fy_j": np.zeros((3, 2)),
            "pushover/+X/frame_fz_j": np.zeros((3, 2)),
            "pushover/+X/frame_mx_j": np.zeros((3, 2)),
            "pushover/+X/frame_my_j": np.zeros((3, 2)),
            "pushover/+X/frame_mz_j": np.array(
                [
                    [-8.0, -4.0],
                    [-20.0, -6.0],
                    [-12.0, -10.0],
                ]
            ),
            "pushover/+X/node_tag": np.array([1, 2, 3]),
            "pushover/+X/node_disp_x": np.zeros((3, 3)),
            "pushover/+X/node_disp_y": np.zeros((3, 3)),
            "pushover/+X/node_disp_z": np.zeros((3, 3)),
        }

    def test_hinge_formation_notebook(self, npz_hinge_data):
        """Hinge formation with notebook=True returns plotter."""
        from fea_toolkit.plotting.viz import plot_plastic_hinge_formation

        pl = plot_plastic_hinge_formation(
            npz_hinge_data,
            step=0,
            notebook=True,
        )
        assert pl is not None
        pl.close()

    def test_hinge_formation_step_one(self, npz_hinge_data):
        """Specific step renders without slider."""
        from fea_toolkit.plotting.viz import plot_plastic_hinge_formation

        pl = plot_plastic_hinge_formation(
            npz_hinge_data,
            step=1,
            notebook=True,
        )
        assert pl is not None
        pl.close()

    def test_hinge_formation_no_data(self):
        """Empty data returns None."""
        from fea_toolkit.plotting.viz import plot_plastic_hinge_formation

        pl = plot_plastic_hinge_formation({}, notebook=True)
        assert pl is None

    def test_hinge_formation_raw_list(self):
        """Raw list without element-to-node mapping returns None gracefully.

        Raw list data has no mesh geometry for frame_eid_to_nodes.
        This is a known limitation — use NPZ dict or Builder instead.
        """
        from fea_toolkit.plotting.viz import plot_plastic_hinge_formation

        raw_data = [
            {
                "frame_forces": {
                    "F1": {"mz_i": 10.0, "mz_j": -8.0},
                    "F2": {"mz_i": 5.0, "mz_j": -4.0},
                },
                "node_coords": {"1": (0, 0, 0), "2": (4, 0, 0), "3": (4, 0, 3)},
            },
        ]
        # Known limitation — raw list has no frame_eid_to_nodes
        pl = plot_plastic_hinge_formation(raw_data, step=0, notebook=True)
        assert pl is None


# ============================================================================
# Pushover deformation animation
# ============================================================================


class TestAnimatePushoverDeformation:
    """Tests for animate_pushover_deformation (Phase 4d)."""

    @pytest.fixture(autouse=True)
    def _skip_if_no_pyvista(self):
        if not _has_pyvista:
            pytest.skip("pyvista not installed")

    @pytest.fixture
    def npz_anim_data(self):
        """NPZ dict with 2 frame elements, 2 shell elements, 3 push steps."""
        return {
            "node_tag": np.array([1, 2, 3, 4, 5]),
            "node_sap_id": np.array(["1", "2", "3", "4", "5"]),
            "node_x": np.array([0.0, 4.0, 4.0, 0.0, 4.0]),
            "node_y": np.array([0.0, 0.0, 0.0, 3.0, 3.0]),
            "node_z": np.array([0.0, 0.0, 3.0, 0.0, 3.0]),
            "frame_eid": np.array([0, 1]),
            "frame_sap_id": np.array(["F1", "F2"]),
            "frame_node_i": np.array([1, 2]),
            "frame_node_j": np.array([2, 3]),
            "frame_sec_name": np.array(["COL", "BEAM"]),
            "frame_parent_sap_id": np.array(["", ""]),
            "shell_eid": np.array([0, 1]),
            "shell_sap_id": np.array(["W1", "W2"]),
            "shell_sec_name": np.array(["WALL", "WALL"]),
            "shell_node_1": np.array([1, 2]),
            "shell_node_2": np.array([2, 5]),
            "shell_node_3": np.array([5, 3]),
            "shell_node_4": np.array([4, 4]),
            "pushover/+X/step": np.array([0, 1, 2]),
            "pushover/+X/frame_sap_id": np.array(["F1", "F2"]),
            "pushover/+X/frame_fx_i": np.zeros((3, 2)),
            "pushover/+X/frame_fy_i": np.zeros((3, 2)),
            "pushover/+X/frame_fz_i": np.zeros((3, 2)),
            "pushover/+X/frame_mx_i": np.zeros((3, 2)),
            "pushover/+X/frame_my_i": np.zeros((3, 2)),
            "pushover/+X/frame_mz_i": np.array(
                [
                    [10.0, 5.0],
                    [25.0, 8.0],
                    [15.0, 12.0],
                ]
            ),
            "pushover/+X/frame_fx_j": np.zeros((3, 2)),
            "pushover/+X/frame_fy_j": np.zeros((3, 2)),
            "pushover/+X/frame_fz_j": np.zeros((3, 2)),
            "pushover/+X/frame_mx_j": np.zeros((3, 2)),
            "pushover/+X/frame_my_j": np.zeros((3, 2)),
            "pushover/+X/frame_mz_j": np.array(
                [
                    [-8.0, -4.0],
                    [-20.0, -6.0],
                    [-12.0, -10.0],
                ]
            ),
            "pushover/+X/shell_sap_id": np.array(["W1", "W2"]),
            "pushover/+X/shell_Nx": np.array(
                [
                    [100.0, 50.0],
                    [200.0, 80.0],
                    [300.0, 120.0],
                ]
            ),
            "pushover/+X/shell_Ny": np.array(
                [
                    [50.0, 25.0],
                    [100.0, 40.0],
                    [150.0, 60.0],
                ]
            ),
            "pushover/+X/shell_Nxy": np.array(
                [
                    [10.0, 5.0],
                    [20.0, 8.0],
                    [30.0, 12.0],
                ]
            ),
            "pushover/+X/shell_Mx": np.array(
                [
                    [5.0, 3.0],
                    [10.0, 5.0],
                    [15.0, 8.0],
                ]
            ),
            "pushover/+X/shell_My": np.array(
                [
                    [3.0, 2.0],
                    [6.0, 3.0],
                    [9.0, 5.0],
                ]
            ),
            "pushover/+X/shell_Mxy": np.array(
                [
                    [1.0, 0.5],
                    [2.0, 1.0],
                    [3.0, 1.5],
                ]
            ),
            "pushover/+X/node_tag": np.array([1, 2, 3, 4, 5]),
            "pushover/+X/node_disp_x": np.array(
                [
                    [0.0, 0.01, 0.02, 0.0, 0.01],
                    [0.0, 0.02, 0.04, 0.0, 0.02],
                    [0.0, 0.03, 0.06, 0.0, 0.03],
                ]
            ),
            "pushover/+X/node_disp_y": np.zeros((3, 5)),
            "pushover/+X/node_disp_z": np.zeros((3, 5)),
        }

    def test_anim_notebook(self, npz_anim_data):
        """Animation with notebook=True returns plotter."""
        from fea_toolkit.plotting.viz import animate_pushover_deformation

        pl = animate_pushover_deformation(npz_anim_data, notebook=True)
        assert pl is not None
        pl.close()

    def test_anim_no_data(self):
        """Animation with no data returns None."""
        from fea_toolkit.plotting.viz import animate_pushover_deformation

        pl = animate_pushover_deformation({}, notebook=True)
        assert pl is None

    def test_anim_frames_only(self, npz_anim_data):
        """Animation with show_shells=False works."""
        from fea_toolkit.plotting.viz import animate_pushover_deformation

        pl = animate_pushover_deformation(
            npz_anim_data,
            show_shells=False,
            notebook=True,
        )
        assert pl is not None
        pl.close()

    def test_anim_save_html_call(self, npz_anim_data, tmp_path):
        """Animation with save_html should not crash (file may not be written in off-screen mode)."""
        from fea_toolkit.plotting.viz import animate_pushover_deformation

        html_path = str(tmp_path / "test_anim.html")
        pl = animate_pushover_deformation(
            npz_anim_data,
            save_html=html_path,
            notebook=True,
        )
        assert pl is not None
        # HTML export may fail silently in off-screen mode — that's OK
        pl.close()

    def test_anim_raw_list(self):
        """Animation with raw list data returns None (no geometry data)."""
        from fea_toolkit.plotting.viz import animate_pushover_deformation

        raw_data = [
            {"frame_forces": {"1": {"mz_i": 10.0, "mz_j": -8.0}}, "shell_forces": {}},
            {"frame_forces": {"1": {"mz_i": 20.0, "mz_j": -15.0}}, "shell_forces": {}},
        ]
        pl = animate_pushover_deformation(raw_data, notebook=True)
        assert pl is None  # raw list has no node_coords or mesh data


# ============================================================================
# Frame force evolution
# ============================================================================


class TestFrameForceEvolution:
    """Tests for plot_frame_force_evolution (Phase 4e)."""

    @pytest.fixture
    def force_evolution_data(self):
        """2-frame, 3-step dataset for force evolution."""
        return [
            {
                "frame_forces": {
                    "F1": {
                        "mz_i": 10.0,
                        "mz_j": -8.0,
                        "fx_i": 5.0,
                        "fx_j": -5.0,
                        "fy_i": 2.0,
                        "fy_j": -2.0,
                        "fz_i": 3.0,
                        "fz_j": -3.0,
                    },
                    "F2": {
                        "mz_i": 5.0,
                        "mz_j": -4.0,
                        "fx_i": 2.0,
                        "fx_j": -2.0,
                        "fy_i": 1.0,
                        "fy_j": -1.0,
                        "fz_i": 1.5,
                        "fz_j": -1.5,
                    },
                },
            },
            {
                "frame_forces": {
                    "F1": {
                        "mz_i": 25.0,
                        "mz_j": -20.0,
                        "fx_i": 8.0,
                        "fx_j": -8.0,
                        "fy_i": 5.0,
                        "fy_j": -5.0,
                        "fz_i": 6.0,
                        "fz_j": -6.0,
                    },
                    "F2": {
                        "mz_i": 8.0,
                        "mz_j": -6.0,
                        "fx_i": 3.0,
                        "fx_j": -3.0,
                        "fy_i": 2.0,
                        "fy_j": -2.0,
                        "fz_i": 2.5,
                        "fz_j": -2.5,
                    },
                },
            },
            {
                "frame_forces": {
                    "F1": {
                        "mz_i": 15.0,
                        "mz_j": -12.0,
                        "fx_i": 6.0,
                        "fx_j": -6.0,
                        "fy_i": 3.0,
                        "fy_j": -3.0,
                        "fz_i": 4.0,
                        "fz_j": -4.0,
                    },
                    "F2": {
                        "mz_i": 12.0,
                        "mz_j": -10.0,
                        "fx_i": 4.0,
                        "fx_j": -4.0,
                        "fy_i": 3.0,
                        "fy_j": -3.0,
                        "fz_i": 3.5,
                        "fz_j": -3.5,
                    },
                },
            },
        ]

    def test_force_evolution_basic(self, force_evolution_data):
        """Basic force evolution returns Figure with correct subplots."""
        import matplotlib.pyplot as plt

        from fea_toolkit.plotting.viz import plot_frame_force_evolution

        fig = plot_frame_force_evolution(
            force_evolution_data,
            quantity="Mz",
            figsize=(6, 4),
        )
        assert fig is not None
        # 2 elements should produce 2 subplots (1 row × 2 cols)
        assert len(fig.axes) == 2
        plt.close(fig)

    def test_force_evolution_with_yield(self, force_evolution_data):
        """Yield moment is drawn as a dashed line."""
        import matplotlib.pyplot as plt

        from fea_toolkit.plotting.viz import plot_frame_force_evolution

        fig = plot_frame_force_evolution(
            force_evolution_data,
            quantity="Mz",
            yield_moment={"F1": 20.0, "F2": 10.0},
            figsize=(6, 4),
        )
        assert fig is not None
        plt.close(fig)

    def test_force_evolution_no_data(self):
        """Empty data returns None."""
        from fea_toolkit.plotting.viz import plot_frame_force_evolution

        fig = plot_frame_force_evolution([], figsize=(6, 4))
        assert fig is None

    def test_force_evolution_quantity_v(self, force_evolution_data):
        """Force quantity 'V' (shear) works."""
        import matplotlib.pyplot as plt

        from fea_toolkit.plotting.viz import plot_frame_force_evolution

        fig = plot_frame_force_evolution(
            force_evolution_data,
            quantity="V",
            figsize=(6, 4),
        )
        assert fig is not None
        plt.close(fig)

    def test_force_evolution_quantity_n(self, force_evolution_data):
        """Force quantity 'N' (axial) works."""
        import matplotlib.pyplot as plt

        from fea_toolkit.plotting.viz import plot_frame_force_evolution

        fig = plot_frame_force_evolution(
            force_evolution_data,
            quantity="N",
            figsize=(6, 4),
        )
        assert fig is not None
        plt.close(fig)
