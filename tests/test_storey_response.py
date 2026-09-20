"""Unit tests for storey-response helpers (``fea_toolkit.model.storey_response``).

Covers :func:`group_shell_forces_by_section` (per-step shell membrane
resultants averaged over parent/row-band sections), :func:`rigid_body_fit`
(least-squares rigid-body motion with outlier rejection),
:func:`_cqc_coeff` (Der Kiureghian modal correlation), and
:func:`storey_drifts`.  Uses fabricated data only -- no OpenSees.
"""

import math

import numpy as np
import pandas as pd  # optional [report] extra — the suite runs with it; see docs/dev_notes.md
import pytest

from fea_toolkit.model.storey_response import (
    group_shell_forces_by_section,
    storey_levels_from_z,
    sum_storey_forces,
)


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


class TestSumStoreyForces:
    """Per-level global force summation with lever-arm moments."""

    @staticmethod
    def _frame():
        """Four columns on a 4x4 plan between z=0 and z=3 (model units)."""
        return {
            1: (0.0, 0.0, 0.0),
            2: (4.0, 0.0, 0.0),
            3: (0.0, 4.0, 0.0),
            4: (4.0, 4.0, 0.0),
            5: (0.0, 0.0, 3.0),
            6: (4.0, 0.0, 3.0),
            7: (0.0, 4.0, 3.0),
            8: (4.0, 4.0, 3.0),
        }

    @staticmethod
    def _column(ni, nj, shear):
        """A vertical member carrying +shear at the top, -shear at the base."""
        return {
            "node_i": ni,
            "node_j": nj,
            "f_i": [-shear, 0.0, 0.0, 0.0, 0.0, 0.0],
            "f_j": [shear, 0.0, 0.0, 0.0, 0.0, 0.0],
        }

    def test_levels_are_clustered_and_ordered(self):
        assert [z for z, _ in storey_levels_from_z([0.0, 0.0, 3.0, 3.0], 0.5)] == [0.0, 3.0]
        # 0.3 sits inside the 0.5 band of 0.0, so only two levels remain.
        assert len(storey_levels_from_z([0.0, 0.3, 6.0], 0.5)) == 2

    def test_shear_sums_by_level(self):
        """``mode="end"``: member-end forces per level (the load path)."""
        nodes = self._frame()
        members = [self._column(ni, nj, 10.0) for ni, nj in ((1, 5), (2, 6), (3, 7), (4, 8))]
        res = sum_storey_forces(nodes, members, mode="end")
        assert [r["elevation"] for r in res] == [0.0, 3.0]
        assert res[0]["Fx"] == pytest.approx(-40.0)
        assert res[1]["Fx"] == pytest.approx(40.0)
        assert res[0]["n_ends"] == 4
        assert res[1]["n_ends"] == 4
        # Perfectly symmetric loading carries no torsion.
        assert res[0]["Mz"] == pytest.approx(0.0)
        assert res[1]["Mz"] == pytest.approx(0.0)

    def test_cut_mode_takes_the_lower_side_only(self):
        """``mode="cut"``: the force transmitted across the plane above a level.

        Each column spans 0 -> 3, so it contributes its lower-end force at the
        base and nothing at the roof — no storey sits above the roof.  A member
        is therefore credited once per level, never twice: crediting the upper
        end as well would add a delivered end force *and* a transmitted
        internal force for the same member, and the two are opposite in sign.
        """
        nodes = self._frame()
        members = [self._column(ni, nj, 10.0) for ni, nj in ((1, 5), (2, 6), (3, 7), (4, 8))]
        res = sum_storey_forces(nodes, members)  # cut is the default
        assert [r["elevation"] for r in res] == [0.0, 3.0]
        assert res[0]["Fx"] == pytest.approx(-40.0)  # four -10 kN lower ends
        assert res[0]["n_ends"] == 4
        assert res[1]["Fx"] == pytest.approx(0.0)  # roof: no storey above it
        assert res[1]["n_ends"] == 0

    def test_cut_mode_counts_a_member_crossing_a_level_without_a_node(self):
        """A member spanning a level with no node there still contributes.

        Node 3 sits at z=1.5 with no member framing into it, while the column
        runs z=0 -> 3 straight through.  ``mode="end"`` credits that column only
        at 0 and 3, so the intermediate level loses it entirely; ``mode="cut"``
        credits it at 0 and 1.5, where the level is crossed and the force is
        still transmitted.
        """
        nodes = {1: (0.0, 0.0, 0.0), 2: (0.0, 0.0, 3.0), 3: (0.0, 0.0, 1.5)}
        members = [self._column(1, 2, 10.0)]
        end = sum_storey_forces(nodes, members, mode="end")
        assert [r["elevation"] for r in end] == [0.0, 1.5, 3.0]
        assert [r["n_ends"] for r in end] == [1, 0, 1]
        cut = sum_storey_forces(nodes, members)
        assert [r["n_ends"] for r in cut] == [1, 1, 0]
        assert cut[1]["n_crossing"] == 1  # the interior cut
        assert cut[1]["Fx"] == pytest.approx(-10.0)  # force is unchanged
        # Moment is carried too: r x F = (0, 1.5, 0) x (-10, 0, 0) = (0, -15, 0).
        assert cut[1]["My"] == pytest.approx(-15.0)
        assert cut[2]["Fx"] == pytest.approx(0.0)  # roof

    def test_cut_mode_credits_a_node_clustered_into_the_level(self):
        """A lower end inside the level band counts as that level's end.

        The column starts at z=0.3 (node 2), which ``z_tolerance=0.5`` clusters
        into the z=0.0 level seeded by the unattached node 1.  The level then
        sits *below* the member's lower end, so a bare numerical-coincidence
        test would skip the member there and the base would read 0; the
        clustering band credits it with its lower-end force — the load-path
        value — as an end, not as an interior cut.
        """
        nodes = {1: (0.0, 0.0, 0.0), 2: (0.0, 0.0, 0.3), 3: (0.0, 0.0, 3.0)}
        members = [self._column(2, 3, 10.0)]
        res = sum_storey_forces(nodes, members, z_tolerance=0.5)
        # The z=0.0 and z=0.3 nodes form one level at their mean elevation.
        assert [r["elevation"] for r in res] == pytest.approx([0.15, 3.0])
        assert res[0]["Fx"] == pytest.approx(-10.0)
        assert res[0]["n_ends"] == 1
        assert res[0]["n_crossing"] == 0  # the lower end, not an interior cut
        assert res[1]["Fx"] == pytest.approx(0.0)  # roof: no storey above

    def test_end_mode_base_reads_the_reaction(self):
        """The base shows the full reaction — the load *leaving*, not arriving.

        ``"end"`` is nodal equilibrium, so a restrained level reads the support
        reaction rather than the (near-zero) load arriving there: four columns
        delivering 100 kN each to the base give 400 kN *of reaction* at the
        base, not ~0, while the roof receives nothing.
        """
        nodes = self._frame()
        members = [
            {
                "node_i": ni,
                "node_j": nj,
                "f_i": [0.0, 0.0, -100.0, 0.0, 0.0, 0.0],
                "f_j": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            }
            for ni, nj in ((1, 5), (2, 6), (3, 7), (4, 8))
        ]
        base, top = sum_storey_forces(nodes, members, mode="end")
        assert base["Fz"] == pytest.approx(-400.0)
        assert top["Fz"] == pytest.approx(0.0)

    def test_end_mode_totals_to_zero_and_cut_mode_totals_to_the_base(self):
        """The two modes are a load path and an accumulation.

        ``"end"`` credits **both** ends of every member, so summing its levels
        gives ``sum(f_i + f_j) == 0`` for span-load-free members.  ``"cut"``
        credits only the lower end, so its levels sum to the base value — the
        reaction.
        """
        nodes = self._frame()
        members = [self._column(ni, nj, 10.0) for ni, nj in ((1, 5), (2, 6), (3, 7), (4, 8))]
        end = sum_storey_forces(nodes, members, mode="end")
        assert sum(r["Fx"] for r in end) == pytest.approx(0.0)
        cut = sum_storey_forces(nodes, members)
        assert sum(r["Fx"] for r in cut) == pytest.approx(-40.0)

    def test_torsion_from_shear_offsets(self):
        """A one-sided +X shear pair produces Mz = force x lever arm."""
        nodes = self._frame()
        members = [self._column(1, 5, 10.0), self._column(2, 6, 10.0)]
        top = sum_storey_forces(nodes, members, mode="end")[1]
        assert top["Fx"] == pytest.approx(20.0)
        # Two 10 kN forces, each 2 m off cy=2 -> 40 kN-m of torsion.
        assert top["Mz"] == pytest.approx(40.0)

    def test_overturning_from_axial_offsets(self):
        """An off-centre axial force drives Mx / My by the lever arm."""
        nodes = self._frame()
        members = [
            {
                "node_i": 1,
                "node_j": 5,
                "f_i": [0.0, 0.0, -100.0, 0.0, 0.0, 0.0],
                "f_j": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            }
        ]
        base = sum_storey_forces(nodes, members, mode="end")[0]
        # dx = -2, dy = -2  ->  Mx = fz*dy = +200,  My = -fz*dx = -200
        assert base["Mx"] == pytest.approx(200.0)
        assert base["My"] == pytest.approx(-200.0)
        assert base["Mz"] == pytest.approx(0.0)

    def test_horizontal_members_are_excluded(self):
        """Both ends share one elevation, so a beam contributes nothing."""
        nodes = self._frame()
        beam = {
            "node_i": 5,
            "node_j": 6,
            "f_i": [5.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "f_j": [-5.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        }
        res = sum_storey_forces(nodes, [beam])
        assert all(r["n_ends"] == 0 for r in res)
        assert all(r["Fx"] == pytest.approx(0.0) for r in res)

    def test_cm_method_bbox_is_default(self):
        res = sum_storey_forces(self._frame(), [])
        assert res[0]["cx"] == pytest.approx(2.0)
        assert res[0]["cy"] == pytest.approx(2.0)

    def test_cm_method_mass(self):
        nodes = self._frame()
        masses = {1: 3.0, 2: 1.0, 3: 1.0, 4: 1.0, 5: 3.0, 6: 1.0, 7: 1.0, 8: 1.0}
        res = sum_storey_forces(nodes, [], cm_method="mass", node_masses=masses)
        # (3*0 + 1*4 + 1*0 + 1*4) / 6 = 8/6
        assert res[0]["cx"] == pytest.approx(8.0 / 6.0)

    def test_cm_method_mass_requires_masses(self):
        with pytest.raises(ValueError, match="node_masses"):
            sum_storey_forces(self._frame(), [], cm_method="mass")

    def test_unknown_cm_method_raises(self):
        with pytest.raises(ValueError, match="cm_method"):
            sum_storey_forces(self._frame(), [], cm_method="nope")

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError, match="mode"):
            sum_storey_forces(self._frame(), [], mode="nope")

    def test_explicit_levels_override_clustering(self):
        res = sum_storey_forces(self._frame(), [], levels=[0.0, 1.5, 3.0])
        assert [r["elevation"] for r in res] == [0.0, 1.5, 3.0]

    def test_empty_nodes_returns_empty(self):
        assert sum_storey_forces({}, []) == []
