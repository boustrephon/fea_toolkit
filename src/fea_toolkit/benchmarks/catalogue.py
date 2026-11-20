"""Structured catalogue of the implemented benchmarks (the GUI library panel).

A thin, OpenSees-free index over ``docs/benchmark_register.md`` §3.  The
register stays the authoritative, hand-authored documentation; this module is
the machine-readable copy the GUI reads, plus the code fact (``id``) that a
Markdown table cannot hold.  A sync test (``tests/test_benchmark_catalogue.py``)
parses the register §3 and fails CI when the shared facts — title, type, tier,
status, doc, test — disagree with this catalogue (the project's §7.4
"one owner per fact" rule).

A benchmark that exposes a GUI-openable builder carries its ``builder`` name
and the fixed ``build_model`` defaults in ``builder_params``; the
``benchmark_recipe`` helper turns that into the recipe a caller presets when a
user opens the benchmark.  Only some benchmarks expose a builder today — the
rest are validated in their test scripts and carry no builder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = [
    "BENCHMARK_ENTRIES",
    "STATUS_BADGES",
    "BenchmarkEntry",
    "benchmark_recipe",
    "builder_recipe",
    "get_benchmark",
]

#: Validation-status vocabulary -> display badge (the project's §4.7 notation).
#: ``validated`` matches its closed-form/paper reference within the tier band
#: (for a tier ``—`` synthetic exercise it means *implemented and tested* — the
#: feature pages are the basis, not a paper); ``partial`` runs but its reference
#: comparison is not fully closed; ``in_progress`` is actively under
#: investigation with a known gap.  The authoritative detail lives in each
#: benchmark's own doc, not here.
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
    #: The GUI-openable builder (``"sac"`` / ``"generic_frame"``), or ``None``
    #: when the benchmark is validated only in its test (no interactive builder).
    builder: Optional[str] = None
    #: Fixed ``build_model`` defaults for the builder; the user-choice parameters
    #: (``building`` / ``code`` / ``region`` / ``stories`` / ``design``) take
    #: their ``build_model`` defaults and are edited in the step dialog.
    builder_params: dict[str, Any] = field(default_factory=dict, hash=False)


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
        builder="sac",
        builder_params={"nonlinear": False},
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
        builder="sac",
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
        builder="sac",
        builder_params={"building": 3, "backbone": "imk"},
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
        builder="generic_frame",
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
    BenchmarkEntry(
        id="e_defense_lrb",
        title="E-Defense 5-storey base-isolated frame (LRB-CLB)",
        structural_type="base-isolated steel moment frame",
        load_type="modal/NLTHA",
        tier="T4",
        status="validated",
        doc=("e_defense_lrb_benchmark.md",),
        test=("test_e_defense_lrb.py",),
        builder="e_defense_lrb",
    ),
    BenchmarkEntry(
        id="e_defense_tpb",
        title="E-Defense 5-storey base-isolated frame (TPB)",
        structural_type="base-isolated steel moment frame",
        load_type="modal/NLTHA",
        tier="T4",
        status="validated",
        doc=("e_defense_tpb_benchmark.md",),
        test=("test_e_defense_tpb.py",),
        builder="e_defense_tpb",
    ),
)

_BY_ID: dict[str, BenchmarkEntry] = {entry.id: entry for entry in BENCHMARK_ENTRIES}


def get_benchmark(benchmark_id: str) -> BenchmarkEntry:
    """Return the catalogue entry for *benchmark_id* (``KeyError`` if unknown)."""
    return _BY_ID[benchmark_id]


def builder_recipe(
    builder: str, params: Optional[dict] = None, *, name: Optional[str] = None
) -> Any:
    """Return a ``Recipe`` that builds *builder* and then meshes it.

    The first step is a ``build_model`` step preset with *builder* and any fixed
    *params* (the user-choice parameters take their ``build_model`` defaults and
    are edited in the step dialog); the second prepares the topology.  This is
    the one spelling of "build an example model": the GUI library panel and the
    ``File ▸ Open example…`` shortcut both preset through it, and
    :func:`benchmark_recipe` delegates to it (anti-pattern 17).

    Args:
        builder: A ``build_model`` builder name (``"fema355c"``, ``"sac"`` or
            ``"generic_frame"``).
        params: Fixed ``build_model`` defaults to bake into the step.
        name: Recipe name; defaults to *builder*.

    Returns:
        A ``Recipe`` whose steps are ``build_model`` then ``mesh``.
    """
    from ..workflow import Recipe, Step

    step_params = dict(params or {})
    step_params["builder"] = builder
    return Recipe(
        name=name or builder,
        steps=[
            Step("build_model", params=step_params),
            Step("mesh"),
        ],
    )


def benchmark_recipe(benchmark_id: str) -> Any:
    """Return a ``Recipe`` that builds and meshes the benchmark *benchmark_id*.

    Resolves the catalogue entry and delegates to :func:`builder_recipe` with
    the entry's ``builder`` and fixed ``builder_params`` (the user-choice
    parameters — ``building`` / ``code`` / ``region`` / ``stories`` / ``design``
    — take their ``build_model`` defaults and are edited in the step dialog).

    Args:
        benchmark_id: A catalogue :attr:`BenchmarkEntry.id`.

    Returns:
        A ``Recipe`` whose steps are ``build_model`` then ``mesh``.

    Raises:
        KeyError: If *benchmark_id* is unknown.
        ValueError: If the benchmark has no GUI-openable builder.
    """
    entry = get_benchmark(benchmark_id)
    if entry.builder is None:
        raise ValueError(
            f"benchmark {benchmark_id!r} has no GUI-openable builder — it is "
            f"validated in {', '.join(entry.test)}, not built interactively"
        )
    return builder_recipe(entry.builder, entry.builder_params, name=entry.id)
