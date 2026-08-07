"""Unit tests for storey-response helpers (``fea_toolkit.model.storey_response``).

Focus: :func:`group_shell_forces_by_section`, a pure-function grouping
helper that averages per-step shell membrane resultants over
(parent, row-band) sections.  Uses fabricated data only -- no OpenSees.
"""

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
