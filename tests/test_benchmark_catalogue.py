"""Sync the benchmark catalogue against ``benchmark_register.md`` §3.

The register is the hand-authored documentation; ``benchmarks/catalogue.py``
is the machine-readable copy the GUI reads.  This test parses the register's
§3 "Implemented benchmarks" table and fails when the shared facts disagree, so
the two cannot drift (the project's §7.4 "one owner per fact" rule).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from fea_toolkit.benchmarks.catalogue import (
    BENCHMARK_ENTRIES,
    STATUS_BADGES,
    benchmark_recipe,
    builder_recipe,
    get_benchmark,
)

_REGISTER = Path(__file__).resolve().parent.parent / "docs" / "benchmark_register.md"


def _register_rows() -> list[list[str]]:
    """Return the §3 table cells (data rows only, no header/separator)."""
    lines = _REGISTER.read_text(encoding="utf-8").splitlines()
    start = lines.index("## 3. Implemented benchmarks")
    rows: list[list[str]] = []
    for line in lines[start + 1 :]:
        if not line.startswith("|"):
            if rows:
                break
            continue
        rows.append([cell.strip() for cell in line.strip("|").split("|")])
    return rows[2:]  # drop the header row and its |---| separator row


def _link_targets(cell: str) -> tuple[str, ...]:
    """Extract markdown link targets from a Doc cell (``[text](target)``)."""
    return tuple(re.findall(r"\]\(([^)]+)\)", cell))


def _test_files(cell: str) -> tuple[str, ...]:
    """Extract backtick filenames from a Test cell."""
    return tuple(re.findall(r"`([^`]+)`", cell))


def test_catalogue_matches_register_table() -> None:
    rows = _register_rows()
    register_titles = {cells[0] for cells in rows}
    catalogue_titles = {entry.title for entry in BENCHMARK_ENTRIES}
    assert register_titles == catalogue_titles

    by_title = {entry.title: entry for entry in BENCHMARK_ENTRIES}
    for cells in rows:
        title, type_cell, tier, badge, doc_cell, test_cell = cells
        entry = by_title[title]
        structural_type, load_type = (part.strip() for part in re.split(r"\s*·\s*", type_cell))
        assert entry.tier == tier, title
        assert STATUS_BADGES[entry.status] == badge, title
        assert entry.structural_type == structural_type, title
        assert entry.load_type == load_type, title
        assert entry.doc == _link_targets(doc_cell), title
        assert entry.test == _test_files(test_cell), title


def test_entry_ids_are_unique_and_resolvable() -> None:
    ids = [entry.id for entry in BENCHMARK_ENTRIES]
    assert len(ids) == len(set(ids))
    for entry in BENCHMARK_ENTRIES:
        assert get_benchmark(entry.id) is entry
        assert entry.status in STATUS_BADGES


def test_benchmark_recipe_presets_build_model_then_mesh() -> None:
    recipe = benchmark_recipe("ik2005_generic_frame")
    assert [step.verb for step in recipe.steps] == ["build_model", "mesh"]
    assert recipe.steps[0].params["builder"] == "generic_frame"


def test_builder_recipe_presets_build_model_then_mesh() -> None:
    recipe = builder_recipe("fema355c")
    assert [step.verb for step in recipe.steps] == ["build_model", "mesh"]
    assert recipe.steps[0].params == {"builder": "fema355c"}


def test_benchmark_recipe_carries_the_fixed_builder_params() -> None:
    recipe = benchmark_recipe("sac_imk_collapse_ida")
    assert recipe.steps[0].params == {"builder": "sac", "building": 3, "backbone": "imk"}


def test_benchmark_recipe_rejects_a_non_openable_benchmark() -> None:
    with pytest.raises(ValueError):
        benchmark_recipe("euler_cantilever")


def test_benchmark_recipe_runs_after_filling_the_code() -> None:
    from examples.sample_model import make_sample_model
    from fea_toolkit.workflow import run_recipe

    recipe = benchmark_recipe("ik2005_generic_frame")
    recipe.steps[0].params["code"] = "0909"
    run = run_recipe(recipe, make_sample_model())
    assert run.results[0].kind == "model"
    assert run.results[1].kind == "geometry"
    assert run.document.model_data.frame_elements
