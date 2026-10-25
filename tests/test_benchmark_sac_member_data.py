"""Sanity checks for the transcribed SAC member backbone tables."""

import pytest

from fea_toolkit.benchmarks.sac_member_data import (
    MAT_TBL_3,
    MAT_TBL_9,
    MAT_TBL_20,
    MAT_TBLS,
    _splice,
)


def test_tables_cover_all_three_buildings():
    assert set(MAT_TBLS) == {3, 9, 20}


@pytest.mark.parametrize(
    ("table", "n_rows"),
    [(MAT_TBL_3, 7), (MAT_TBL_9, 16), (MAT_TBL_20, 25)],
)
def test_row_counts_match_source(table, n_rows):
    assert len(table) == n_rows


@pytest.mark.parametrize("table", [MAT_TBL_3, MAT_TBL_9, MAT_TBL_20])
def test_trilinear_backbone_is_monotonic(table):
    for mat, row in table.items():
        assert row.ei1 > row.ei2 > row.ei3 > 0.0, f"mat {mat}"
        assert 0.0 < row.d1 < row.d2, f"mat {mat}"
        assert row.ea > 0.0 and row.ga > 0.0, f"mat {mat}"


def test_splice_rows_match_weighted_average_definition():
    assert MAT_TBL_9[12] == _splice(MAT_TBL_9[2], MAT_TBL_9[3])
    assert MAT_TBL_9[16] == _splice(MAT_TBL_9[5], MAT_TBL_9[6])
    assert MAT_TBL_20[17] == _splice(MAT_TBL_20[1], MAT_TBL_20[3])
    assert MAT_TBL_20[25] == _splice(MAT_TBL_20[9], MAT_TBL_20[11])


def test_struct3_leaning_column_and_rigid_link_bay():
    # Material 6 is the leaning (gravity) column — ~28x softer than the
    # moment-frame columns; material 7 is the rigid-axial link bay (EA = 1e10).
    assert MAT_TBL_3[6].ei1 == pytest.approx(1.0070e7)
    assert MAT_TBL_3[7].ea == pytest.approx(1.0e10)
    assert MAT_TBL_3[6].ei1 < MAT_TBL_3[1].ei1 / 10.0
