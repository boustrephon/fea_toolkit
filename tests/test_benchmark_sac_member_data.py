"""Sanity checks for the transcribed SAC member backbone tables."""

import re
from pathlib import Path

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


# ═══════════════════════════════════════════════════════════════
# Gated source-diff check — local/257/_extract/struct*.m
# ═══════════════════════════════════════════════════════════════
# Parses the benchmark's own ``mat_tbl`` blocks and asserts the module
# reproduces them value-for-value.  Skips when the (gitignored) sources are
# absent, e.g. on a clean checkout.
_SRC_DIR = Path(__file__).resolve().parents[1] / "local" / "257" / "_extract"

_NUM_ROW = re.compile(
    r"^\s*(\d+)\s+"
    r"([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+"
    r"([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)"
)
_SPLICE_ROW = re.compile(
    r"^\s*(\d+)\s+"
    r"\((\d+)/(\d+)\)\*mat_tbl\((\d+),2:8\)"
    r"\+\((\d+)/(\d+)\)\*mat_tbl\((\d+),2:8\)"
)


def _block(text: str, marker: str) -> str:
    """Slice from ``marker`` through its closing ``];``."""
    start = text.index(marker)
    end = text.index("];", start) + 2
    return text[start:end]


def _parse_mat_tbl(path: Path):
    """Parse a struct*.m file's ``mat_tbl`` into (base, splices).

    Returns ``base``: ``{Mat_no: [EI1, EI2, EI3, EA, GA, d1, d2]}`` and
    ``splices``: ``{Mat_no: ((wa, row_a), (wb, row_b))}`` mirroring the source
    expression ``(wa)*mat_tbl(row_a) + (wb)*mat_tbl(row_b)``.
    """
    text = path.read_text(encoding="utf-8")
    base: dict[int, list[float]] = {}
    for line in _block(text, "mat_tbl = [").splitlines():
        m = _NUM_ROW.match(line)
        if m:
            base[int(m.group(1))] = [float(x) for x in m.groups()[1:]]

    splices: dict[int, tuple[tuple[float, int], tuple[float, int]]] = {}
    if "mat_tbl = [mat_tbl" in text:
        for line in _block(text, "mat_tbl = [mat_tbl").splitlines():
            m = _SPLICE_ROW.match(line)
            if m:
                g = m.groups()
                mat_no = int(g[0])
                wa = int(g[1]) / int(g[2])
                row_a = int(g[3])
                wb = int(g[4]) / int(g[5])
                row_b = int(g[6])
                splices[mat_no] = ((wa, row_a), (wb, row_b))
    return base, splices


@pytest.mark.parametrize(
    ("fname", "table"),
    [
        ("struct3.m", MAT_TBL_3),
        ("struct9.m", MAT_TBL_9),
        ("struct20.m", MAT_TBL_20),
    ],
)
def test_module_matches_source_verbatim(fname, table):
    """The committed tables reproduce ``local/257/_extract/struct*.m`` exactly."""
    path = _SRC_DIR / fname
    if not path.exists():
        pytest.skip(f"benchmark source not present: {path}")

    base, splices = _parse_mat_tbl(path)

    # Base rows reproduce the source numbers value-for-value.
    for mat_no, vals in base.items():
        assert tuple(table[mat_no]) == tuple(vals), (
            f"{fname} material {mat_no}: {tuple(table[mat_no])} != {tuple(vals)}"
        )

    # Splice rows equal (wa)*base[row_a] + (wb)*base[row_b] from the source.
    for mat_no, ((wa, row_a), (wb, row_b)) in splices.items():
        expected = tuple(wa * x + wb * y for x, y in zip(base[row_a], base[row_b]))
        assert tuple(table[mat_no]) == expected, (
            f"{fname} splice {mat_no}: {tuple(table[mat_no])} != {expected}"
        )

    # The module has exactly the source's rows — none missing, none extra.
    assert set(table) == set(base) | set(splices), (
        f"{fname}: module rows {sorted(table)} != source rows {sorted(set(base) | set(splices))}"
    )
