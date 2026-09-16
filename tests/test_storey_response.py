"""Unit tests for storey-response helpers (``fea_toolkit.model.storey_response``).

Covers :func:`group_shell_forces_by_section` (per-step shell membrane
resultants averaged over parent/row-band sections), :func:`rigid_body_fit`
(least-squares rigid-body motion with outlier rejection),
:func:`_cqc_coeff` (Der Kiureghian modal correlation), and
:func:`storey_drifts`.  Uses fabricated data only -- no OpenSees.
"""

import math

import numpy as np
import pytest

from fea_toolkit.model.storey_response import group_shell_forces_by_section


def _ids_and_parents():
    """Fabricated 2x2 quad mesh: 2 walls x 2 rows x 2 cols + a standalone shell."""
    shell_sap_ids = [
        "W1_sub_0_0",
        "W1_sub_0_1",
        "W1_sub_1_0",
        "W1_sub_1_1",
        "W2_sub_0_0",
        "W2_sub_0_1",
        "W2_sub_1_0",
        "W2_sub_1_1",
        "SHELL_STANDALONE",
    ]
    shell_parent_sap_id = ["W1", "W1", "W1", "W1", "W2", "W2", "W2", "W2", ""]
    return shell_sap_ids, shell_parent_sap_id


def test_banded_rows_2x2():
    shell_sap_ids, shell_parent_sap_id = _ids_and_parents()
    nxy = np.zeros((3, len(shell_sap_ids)))
    ny = np.zeros((3, len(shell_sap_ids)))
    # Step 2 values: W1 row0, W1 row1, W2 row0, W2 row1, standalone.
    nxy[2, :8] = [100.0, 120.0, 200.0, 220.0, 300.0, 320.0, 400.0, 420.0]
    ny[2, :8] = [10.0, 12.0, 20.0, 22.0, 30.0, 32.0, 40.0, 42.0]
    nxy[2, 8] = 999.0
    ny[2, 8] = 99.0

    df = group_shell_forces_by_section(shell_sap_ids, shell_parent_sap_id, nxy, ny, step_idx=2)

    assert list(df["section"]) == [
        "W1_section_0",
        "W1_section_1",
        "W2_section_0",
        "W2_section_1",
        "SHELL_STANDALONE",
    ]
    assert list(df["parent"]) == ["W1", "W1", "W2", "W2", ""]
    assert list(df["row"]) == ["0", "1", "0", "1", ""]
    assert list(df["n_subs"]) == [2, 2, 2, 2, 1]
    assert df.loc[0, "Nxy_avg"] == pytest.approx(110.0)
    assert df.loc[0, "Ny_avg"] == pytest.approx(11.0)
    assert df.loc[1, "Nxy_avg"] == pytest.approx(210.0)
    assert df.loc[1, "Ny_avg"] == pytest.approx(21.0)
    assert df.loc[2, "Nxy_avg"] == pytest.approx(310.0)
    assert df.loc[2, "Ny_avg"] == pytest.approx(31.0)
    assert df.loc[3, "Nxy_avg"] == pytest.approx(410.0)
    assert df.loc[3, "Ny_avg"] == pytest.approx(41.0)
    assert df.loc[4, "Nxy_avg"] == pytest.approx(999.0)
    assert df.loc[4, "Ny_avg"] == pytest.approx(99.0)


def test_step_index_selects_requested_step():
    shell_sap_ids, shell_parent_sap_id = _ids_and_parents()
    nxy = np.zeros((2, len(shell_sap_ids)))
    ny = np.zeros((2, len(shell_sap_ids)))
    nxy[0, :4] = [10.0, 12.0, 20.0, 22.0]
    nxy[1, :4] = [100.0, 120.0, 200.0, 220.0]

    df0 = group_shell_forces_by_section(shell_sap_ids, shell_parent_sap_id, nxy, ny, step_idx=0)
    df1 = group_shell_forces_by_section(shell_sap_ids, shell_parent_sap_id, nxy, ny, step_idx=1)

    assert df0.loc[0, "Nxy_avg"] == pytest.approx(11.0)
    assert df0.loc[1, "Nxy_avg"] == pytest.approx(21.0)
    assert df1.loc[0, "Nxy_avg"] == pytest.approx(110.0)
    assert df1.loc[1, "Nxy_avg"] == pytest.approx(210.0)


def test_parented_shell_without_matching_suffix_collapses_to_whole_parent():
    shell_sap_ids = ["W1_EXTRA", "W1_sub_0_0", "W1_sub_0_1"]
    shell_parent_sap_id = ["W1", "W1", "W1"]
    nxy = np.array([[1.0, 10.0, 20.0]])
    ny = np.array([[0.1, 1.0, 2.0]])

    df = group_shell_forces_by_section(shell_sap_ids, shell_parent_sap_id, nxy, ny, step_idx=0)

    # "W1_EXTRA" has a parent but no matching ``_sub_{row}_{col}`` suffix,
    # so it collapses into the bare whole-parent section.
    assert list(df["section"]) == ["W1", "W1_section_0"]
    assert list(df["row"]) == ["", "0"]
    assert list(df["n_subs"]) == [1, 2]
    assert df.loc[0, "Nxy_avg"] == pytest.approx(1.0)
    assert df.loc[1, "Nxy_avg"] == pytest.approx(15.0)


def test_nan_handling_in_section_mean():
    shell_sap_ids = ["W1_sub_0_0", "W1_sub_0_1"]
    shell_parent_sap_id = ["W1", "W1"]
    nxy = np.array([[np.nan, 120.0]])
    ny = np.array([[np.nan, 12.0]])

    df = group_shell_forces_by_section(shell_sap_ids, shell_parent_sap_id, nxy, ny, step_idx=0)

    assert df.loc[0, "n_subs"] == 2  # NaN shells still counted in the section
    assert df.loc[0, "Nxy_avg"] == pytest.approx(120.0)
    assert df.loc[0, "Ny_avg"] == pytest.approx(12.0)


def test_malformed_child_suffix_missing_col_raises():
    # ``{parent}_sub_{row}`` is incomplete -- the mesher always emits
    # ``{aid}_sub_{j}_{i}``, so a single trailing segment signals a
    # malformed child ID.
    with pytest.raises(ValueError, match="malformed child suffix"):
        group_shell_forces_by_section(
            ["W1_sub_0"], ["W1"], np.zeros((1, 1)), np.zeros((1, 1)), step_idx=0
        )


def test_malformed_child_suffix_extra_segment_raises():
    # ``{parent}_sub_{row}_{col}_extra`` has too many segments.
    with pytest.raises(ValueError, match="malformed child suffix"):
        group_shell_forces_by_section(
            ["W1_sub_0_1_9"], ["W1"], np.zeros((1, 1)), np.zeros((1, 1)), step_idx=0
        )


def test_wall_slab_wi_suffix_collapses_to_whole_parent():
    # The wall-slab intersection mesher emits ``{sid}_wi_sub_{j}_{i}``.
    # The ``_wi_`` marker means the ``{parent}_sub_`` prefix does not
    # match, so these children legitimately collapse to the whole-parent
    # section instead of raising.
    shell_sap_ids = ["W1_wi_sub_0_1"]
    shell_parent_sap_id = ["W1"]
    nxy = np.array([[42.0]])
    ny = np.array([[4.2]])

    df = group_shell_forces_by_section(shell_sap_ids, shell_parent_sap_id, nxy, ny, step_idx=0)

    assert list(df["section"]) == ["W1"]
    assert list(df["row"]) == [""]
    assert list(df["n_subs"]) == [1]
    assert df.loc[0, "Nxy_avg"] == pytest.approx(42.0)
    assert df.loc[0, "Ny_avg"] == pytest.approx(4.2)


def test_length_mismatch_raises():
    with pytest.raises(ValueError, match="equal length"):
        group_shell_forces_by_section(
            ["W1_sub_0_0"], [], np.zeros((1, 1)), np.zeros((1, 1)), step_idx=0
        )


def test_empty_input_returns_empty_dataframe():
    df = group_shell_forces_by_section([], [], np.zeros((1, 0)), np.zeros((1, 0)), step_idx=0)

    assert df.empty
    assert list(df.columns) == ["section", "parent", "row", "n_subs", "Nxy_avg", "Ny_avg"]


# ============================================================================
# Rigid-body fit, modal CQC coefficient, and storey drifts
# ============================================================================


class TestRigidBodyFit:
    """Tests for model.storey_response.rigid_body_fit."""

    def test_perfect_rigid_body_translation(self):
        """Pure translation (Ux=0.01, Uy=-0.005, Rz=0) recovers exactly."""
        from fea_toolkit.model.storey_response import rigid_body_fit

        np = __import__("numpy")
        x = np.array([0.0, 5.0, 5.0, 0.0])
        y = np.array([0.0, 0.0, 4.0, 4.0])
        x_cm, y_cm = 2.5, 2.0
        Ux_true, Uy_true, Rz_true = 0.01, -0.005, 0.0
        ux = Ux_true - Rz_true * (y - y_cm)
        uy = Uy_true + Rz_true * (x - x_cm)

        Ux, Uy, Rz, rms, _n_used, n_out, _ = rigid_body_fit(ux, uy, x, y, x_cm, y_cm)
        assert abs(Ux - Ux_true) < 1e-12
        assert abs(Uy - Uy_true) < 1e-12
        assert abs(Rz - Rz_true) < 1e-12
        assert rms < 1e-12
        assert n_out == 0

    def test_rigid_body_translation_and_rotation(self):
        """Combined translation + rotation recovers exactly."""
        from fea_toolkit.model.storey_response import rigid_body_fit

        np = __import__("numpy")
        x = np.array([0.0, 6.0, 6.0, 0.0])
        y = np.array([0.0, 0.0, 5.0, 5.0])
        x_cm, y_cm = 3.0, 2.5
        Ux_true, Uy_true, Rz_true = 0.02, 0.01, 0.005
        ux = Ux_true - Rz_true * (y - y_cm)
        uy = Uy_true + Rz_true * (x - x_cm)

        Ux, Uy, Rz, rms, _n_used, n_out, _ = rigid_body_fit(ux, uy, x, y, x_cm, y_cm)
        assert abs(Ux - Ux_true) < 1e-12
        assert abs(Uy - Uy_true) < 1e-12
        assert abs(Rz - Rz_true) < 1e-12
        assert rms < 1e-12
        assert n_out == 0

    def test_outlier_rejected(self):
        """One synthetic outlier is rejected; fit matches remaining nodes."""
        from fea_toolkit.model.storey_response import rigid_body_fit

        np = __import__("numpy")
        # 5 nodes in a cross pattern — all follow the same rigid-body field
        x = np.array([0.0, 6.0, 3.0, 3.0, 3.0])
        y = np.array([0.0, 0.0, -3.0, 3.0, 0.0])
        x_cm, y_cm = 3.0, 0.0
        Ux_true, Uy_true, Rz_true = 0.01, -0.005, 0.003

        # Clean displacements
        ux = Ux_true - Rz_true * (y - y_cm)
        uy = Uy_true + Rz_true * (x - x_cm)

        # Corrupt the last node (at CM) with a large offset
        ux[-1] += 0.10
        uy[-1] += -0.08

        Ux, Uy, Rz, _rms, n_used, n_out, mask = rigid_body_fit(
            ux, uy, x, y, x_cm, y_cm, outlier_threshold=3.0
        )

        # The outlier should be rejected
        assert n_out == 1, f"Expected 1 outlier, got {n_out}"
        assert n_used == 4
        assert not mask[-1], "Corrupted node should be masked as outlier"

        # Fit should be close to the true value (not biased by outlier)
        assert abs(Ux - Ux_true) < 1e-6
        assert abs(Uy - Uy_true) < 1e-6
        assert abs(Rz - Rz_true) < 1e-8


class TestCQC:
    """Tests for CQC correlation coefficient and combined drift."""

    def test_cqc_coeff_identical_modes(self):
        """Identical frequencies → ρ = 1.0 (fully correlated)."""
        from fea_toolkit.model.storey_response import _cqc_coeff

        rho = _cqc_coeff(2.0, 2.0, zeta=0.05)
        assert abs(rho - 1.0) < 1e-12, f"ρ(identical) = {rho}, expected 1.0"

    def test_cqc_coeff_well_separated(self):
        """Well-separated frequencies → ρ ≈ 0 (uncorrelated)."""
        from fea_toolkit.model.storey_response import _cqc_coeff

        rho = _cqc_coeff(10.0, 0.5, zeta=0.05)
        # r = 20, denominator ≈ (1-400)^2 = 159201, numerator ≈ 8*.05^2*21*20^1.5
        # Very small ≈ 0.0003
        assert abs(rho) < 0.001, f"ρ(well-separated) = {rho}, expected near 0"

    def test_cqc_coeff_known_pair(self):
        """Known pair (r=0.8, ζ=0.05) gives ρ ≈ 0.166 per Der Kiureghian."""
        from fea_toolkit.model.storey_response import _cqc_coeff

        # r = f_i/f_j = 4.0/5.0 = 0.8
        rho = _cqc_coeff(4.0, 5.0, zeta=0.05)
        expected = 0.166  # Der Kiureghian (1981) Table 1, ζ=0.05, r=0.8
        assert abs(rho - expected) < 0.005, f"ρ(0.8, 0.05) = {rho:.4f}, expected {expected:.3f}"

    def test_cqc_combined_drift_two_modes(self):
        """Two-mode CQC drift verifies the einsum path.

        ρ = [[1.0, ρ₁₂], [ρ₁₂, 1.0]]
        drifts = [0.010, 0.005]
        combined = sqrt(ρ₁₁·d₁² + 2·ρ₁₂·d₁·d₂ + ρ₂₂·d₂²)
        """
        from fea_toolkit.model.storey_response import _cqc_coeff

        np = __import__("numpy")

        rho_12 = _cqc_coeff(3.0, 5.0, zeta=0.05)
        rho = np.array([[1.0, rho_12], [rho_12, 1.0]])
        di = np.array([[0.010, 0.005]])  # shape (1 gap, 2 modes)

        combined = float(np.sqrt(np.abs(np.einsum("sm, mn, sn -> s", di, rho, di))[0]))
        expected = math.sqrt(1.0 * 0.010**2 + 2 * rho_12 * 0.010 * 0.005 + 1.0 * 0.005**2)
        assert abs(combined - expected) < 1e-12, (
            f"CQC combined = {combined:.8f}, expected {expected:.8f}"
        )


class TestStoreyDrifts:
    """Tests for storey_drifts()."""

    def test_basic_two_storey_drift(self):
        """Two storeys with known Ux difference gives expected drift."""
        from fea_toolkit.model.storey_response import storey_drifts
        from fea_toolkit.model.stories import StoryLevel

        __import__("numpy")
        pd = __import__("pandas")

        stories = [
            StoryLevel("Base", 0.0),
            StoryLevel("Storey 1", 3.0),
        ]
        df_disp = pd.DataFrame(
            [
                {"Storey": "Base", "Elevation": 0.0, "Ux": 0.0, "Uy": 0.0, "Rz": 0.0, "R_max": 5.0},
                {
                    "Storey": "Storey 1",
                    "Elevation": 3.0,
                    "Ux": 0.015,
                    "Uy": 0.0,
                    "Rz": 0.001,
                    "R_max": 5.0,
                },
            ]
        )
        df = storey_drifts(df_disp, stories)
        assert len(df) == 1
        row = df.iloc[0]
        # Drift_X = 0.015 / 3.0 = 0.005
        assert abs(row["Drift_X"] - 0.005) < 1e-8
        # Drift_Rz = 0.001 / 3.0 ≈ 0.000333
        assert abs(row["Drift_Rz"] - 0.001 / 3.0) < 1e-8
        # Peak drift = sqrt(0.005² + 0²) + |0.000333| * 5.0
        expected_peak = 0.005 + (0.001 / 3.0) * 5.0  # ≈ 0.006667
        assert abs(row["Drift_peak"] - expected_peak) < 5e-5
        assert abs(row["h (m)"] - 3.0) < 1e-8
