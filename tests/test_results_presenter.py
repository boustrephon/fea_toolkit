"""Unit tests for :class:`ResultsPresenter` — the Qt-free results fan-out.

The presenter writes to a :class:`ViewRegistry` and reports side effects (log
lines, view switches, animation state) through injected callbacks, so it is
testable without a running GUI — or the ``[gui]`` extra, which the module never
imports.
"""

from types import SimpleNamespace

from fea_toolkit.gui.controllers.results_presenter import ResultsPresenter
from fea_toolkit.gui.controllers.view_registry import (
    TABLE,
    View,
    ViewRegistry,
    _case_of,
    _case_view_name,
    _table_key,
)


class _FakeTable:
    def __init__(self, title: str):
        self.title = title


class _Log:
    def __init__(self):
        self.lines = []

    def __call__(self, message: str, level: str = "info") -> None:
        self.lines.append((level, message))


def _presenter(session, log, shown):
    return ResultsPresenter(
        session=session,
        log=log,
        show_view=shown.append,
        show_model=lambda model, **kw: None,
        nld_step_frames=lambda payload: ({}, []),
        frames={},
        times={},
        on_animatability_changed=lambda: None,
    )


# ── View-key helpers ────────────────────────────────────────────


def test_table_key():
    assert _table_key("Modal") == "table:modal"
    assert _table_key("Response Spectrum") == "table:response-spectrum"


def test_case_view_name_qualifies_combination():
    assert _case_view_name("DEAD", {"group": "DEAD"}) == "DEAD"
    assert _case_view_name("DEAD", {"group": "Comb1"}) == "DEAD (Comb1)"
    assert _case_view_name("DEAD", None) == "DEAD"


def test_case_of_reads_the_results_key_prefix():
    results = View(key="results:DEAD", name="DEAD", kind="results", source="")
    table = View(key="table:modal", name="Modal", kind="table", source="")
    assert _case_of(results) == "DEAD"
    assert _case_of(table) == ""
    assert _case_of(None) == ""


# ── Tables and figures ──────────────────────────────────────────


def test_show_table_result_registers_and_shows():
    session = SimpleNamespace(views=ViewRegistry(), store=None)
    log = _Log()
    shown = []
    presenter = _presenter(session, log, shown)

    presenter.show_table_result("Checks", _FakeTable("Check findings"))

    view = session.views.get(_table_key("Checks"))
    assert view is not None
    assert view.kind == TABLE
    assert view.name == "Check findings"
    assert shown == [view]
    assert ("info", "Added table view: Check findings.") in log.lines


def test_show_table_result_none_is_a_warning():
    session = SimpleNamespace(views=ViewRegistry(), store=None)
    log = _Log()
    shown = []
    presenter = _presenter(session, log, shown)

    presenter.show_table_result("Checks", None)

    assert shown == []
    assert ("warn", "Checks: nothing to show.") in log.lines


def test_show_figure_result_registers_and_shows():
    session = SimpleNamespace(views=ViewRegistry(), store=None)
    log = _Log()
    shown = []
    presenter = _presenter(session, log, shown)
    figure = object()

    presenter.show_figure_result("IDA curve", figure)

    view = session.views.get("figure:ida-curve")
    assert view is not None
    assert shown == [view]
    assert ("info", "Added figure view: IDA curve.") in log.lines


def test_show_results_archive_empty_returns_no_cases():
    session = SimpleNamespace(views=ViewRegistry(), store=None)
    log = _Log()
    shown = []
    presenter = _presenter(session, log, shown)

    assert presenter.show_results_archive("Static", {}) == []
    assert shown == []
