"""Structured catalogue of the implemented benchmarks (the GUI library panel).

A thin, OpenSees-free index over ``docs/benchmark_register.md`` §3.  The
register stays the authoritative, hand-authored documentation; this module is
the machine-readable copy the GUI reads, plus the code fact (``id``) that a
Markdown table cannot hold.  A sync test (``tests/test_benchmark_catalogue.py``)
parses the register §3 and fails CI when the shared facts — title, type, tier,
status, doc, test — disagree with this catalogue (the project's §7.4
"one owner per fact" rule).

The per-benchmark builder/recipe reference is deliberately absent here: it
arrives with the ``build_model`` verb (P46, the next increment), because only
some benchmarks expose a GUI-openable builder today and a ``Recipe`` needs the
verb layer.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "BENCHMARK_ENTRIES",
    "STATUS_BADGES",
    "BenchmarkEntry",
    "get_benchmark",
]

#: Validation-status vocabulary -> display badge (the project's §4.7 notation).
#: ``validated`` matches its closed-form/paper reference within the tier band;
#: ``partial`` runs but its reference comparison is not fully closed;
#: ``in_progress`` is actively under investigation with a known gap.  The
#: authoritative detail lives in each benchmark's own doc, not here.
STATUS_BADGES = {
    "validated": "✅",
    "partial": "⚠️",
    "in_progress": "🚧",
}


@dataclass(frozen=True)
class BenchmarkEntry:
    """One implemented benchmark, mirroring a ``benchmark_register.md`` §3 row.

    ``title`` is the exact ``Benchmark`` cell text — it is the sync key against
    the register.  ``tier`` is ``"T1"``–``"T4"`` or ``"—"`` for a synthetic
    exercise.  ``doc`` / ``test`` carry the filenames exactly as the register
    links them (``doc`` includes ``.md``, ``test`` includes ``.py``).
    """

    id: str
    title: str
    structural_type: str
    load_type: str
    tier: str
    status: str
    doc: tuple[str, ...]
    test: tuple[str, ...]


BENCHMARK_ENTRIES: tuple[BenchmarkEntry, ...] = (
    BenchmarkEntry(
        id="euler_cantilever",
        title="Euler cantilever `PL³/(3EI)`",
        structural_type="vertical cantilever",
        load_type="static",
        tier="T1",
        status="validated",
        doc=("cantilever_benchmark.md",),
        test=("test_benchmark_cantilever.py",),
    ),
    BenchmarkEntry(
        id="przemieniecki_paz_shear_building",
        title="Przemieniecki/Paz shear building + CQC",
        structural_type="shear building",
        load_type="modal/RS",
        tier="T1",
        status="validated",
        doc=("shear_building_benchmark.md",),
        test=("test_benchmark_shear_building.py",),
    ),
    BenchmarkEntry(
        id="sac_modal",
        title="SAC 3/9/20-story frame — modal",
        structural_type="steel moment frame",
        load_type="modal",
        tier="T2",
        status="validated",
        doc=("sac_benchmark.md",),
        test=("test_benchmark_sac3.py", "test_benchmark_sac920.py"),
    ),
    BenchmarkEntry(
        id="sac_nltha",
        title="SAC 3/9/20-story frame — NLTHA",
        structural_type="steel moment frame",
        load_type="NLTHA",
        tier="T4",
        status="validated",
        doc=("sac_nonlinear_benchmark.md",),
        test=("test_benchmark_sac3_nltha.py", "test_benchmark_sac920.py"),
    ),
    BenchmarkEntry(
        id="sac_imk_collapse_ida",
        title="SAC 3-story frame — IMK collapse (IDA)",
        structural_type="steel moment frame",
        load_type="IDA",
        tier="T4",
        status="partial",
        doc=("sac_imk_collapse_benchmark.md",),
        test=("test_benchmark_sac_ida.py",),
    ),
    BenchmarkEntry(
        id="ik2005_generic_frame",
        title="IK-2005 generic-frame collapse (IDA)",
        structural_type="steel moment frame",
        load_type="IDA",
        tier="T4",
        status="in_progress",
        doc=("ik2005_generic_frame_benchmark.md",),
        test=("test_generic_frame*.py",),
    ),
    BenchmarkEntry(
        id="vecchio_emara_frame",
        title="Vecchio & Emara (1992) frame",
        structural_type="RC moment frame",
        load_type="pushover",
        tier="T3",
        status="validated",
        doc=("vecchio_emara_benchmark.md",),
        test=("test_rc_benchmark.py",),
    ),
    BenchmarkEntry(
        id="duong_shear_critical_frame",
        title="Duong et al. (2007) shear-critical frame",
        structural_type="RC moment frame",
        load_type="pushover",
        tier="T3",
        status="validated",
        doc=("duong_benchmark.md",),
        test=("test_duong_benchmark.py",),
    ),
    BenchmarkEntry(
        id="rc_wall_pushover",
        title="RC wall pushover (LayeredShell + MVLEM)",
        structural_type="shear wall",
        load_type="pushover",
        tier="—",
        status="validated",
        doc=("mvlem_wall_analysis.md", "shell_support.md"),
        test=("test_wall_pushover.py",),
    ),
)

_BY_ID: dict[str, BenchmarkEntry] = {entry.id: entry for entry in BENCHMARK_ENTRIES}


def get_benchmark(benchmark_id: str) -> BenchmarkEntry:
    """Return the catalogue entry for *benchmark_id* (``KeyError`` if unknown)."""
    return _BY_ID[benchmark_id]
