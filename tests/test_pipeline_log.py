"""Tests for :class:`fea_toolkit.io.log.PipelineLog` and its helpers.

The log is the toolkit's single structured event source: emit once, render via
sinks.  These tests cover the default stdout sink, ``verbose``/``collect``
semantics, the ``coerce_log`` helper, and JSON persistence.
"""

import json
import logging

from fea_toolkit.io.log import (
    PipelineHandler,
    PipelineLog,
    _step_from_logger,
    coerce_log,
    console_log,
    render_entry,
)


def test_info_prints_plain_and_warn_error_are_prefixed(capsys):
    log = PipelineLog()
    log.info("static", "hello")
    log.warning("modal", "few modes")
    log.error("rs", "boom")
    out = capsys.readouterr().out
    assert "hello\n" in out
    assert "⚠ [modal] few modes\n" in out
    assert "✗ [rs] boom\n" in out


def test_verbose_false_suppresses_info_but_not_warnings(capsys):
    log = PipelineLog(verbose=False)
    log.info("static", "hidden")
    log.warning("modal", "shown")
    out = capsys.readouterr().out
    assert "hidden" not in out
    assert "⚠ [modal] shown" in out


def test_collect_false_prints_but_retains_nothing(capsys):
    log = PipelineLog(collect=False)
    log.info("static", "hello")
    assert capsys.readouterr().out == "hello\n"
    assert log.entries == []


def test_summary_excludes_info_and_debug_but_markdown_includes_all():
    log = PipelineLog(verbose=False)
    log.info("static", "INFO_MSG")
    log.debug("geo", "DEBUG_MSG")
    log.warning("modal", "WARN_MSG")
    log.error("rs", "ERROR_MSG")
    summary = log.summary()
    assert "INFO_MSG" not in summary
    assert "DEBUG_MSG" not in summary
    assert "WARN_MSG" in summary and "ERROR_MSG" in summary
    md = log.markdown()
    assert "🐞" in md and "⚠" in md and "✗" in md and "ℹ" in md


def test_entries_are_recorded_in_emission_order():
    log = PipelineLog(verbose=False)
    log.info("a", "1")
    log.warning("b", "2")
    assert [e["step"] for e in log.entries] == ["a", "b"]
    assert log.entries[0]["level"] == "INFO"


def test_coerce_log():
    log = PipelineLog()
    assert coerce_log(None) is console_log
    assert coerce_log(True) is console_log
    assert coerce_log(log) is log
    silent = coerce_log(False)
    assert silent is coerce_log(False)  # shared singleton
    assert silent._collect is False
    assert silent._sinks == []


def test_to_json_round_trip(tmp_path):
    log = PipelineLog(verbose=False)
    log.info("static", "hello")
    path = tmp_path / "log.json"
    log.to_json(str(path))
    data = json.loads(path.read_text())
    assert data["n_entries"] == 1
    assert data["entries"][0]["step"] == "static"
    assert data["entries"][0]["msg"] == "hello"


def test_step_from_logger_strips_runner_and_underscore():
    assert _step_from_logger("fea_toolkit.opensees._constraints") == "constraints"
    assert _step_from_logger("fea_toolkit.opensees._runner_static") == "static"
    assert _step_from_logger("fea_toolkit.opensees.pushover") == "pushover"
    assert _step_from_logger("fea_toolkit.analysis.combinations") == "combinations"


def test_pipeline_handler_forwards_logging_records():
    log = PipelineLog(verbose=False)
    handler = PipelineHandler(log)
    logger = logging.getLogger("fea_toolkit.test_pipeline_handler")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    try:
        logger.warning("edge %s ignored", "A")
        logger.info("control node OK")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(logging.NOTSET)
        logger.propagate = True

    assert [e["level"] for e in log.entries] == ["WARN", "INFO"]
    assert [e["step"] for e in log.entries] == ["test_pipeline_handler", "test_pipeline_handler"]
    assert log.entries[0]["msg"] == "edge A ignored"
    assert log.entries[1]["msg"] == "control node OK"


def test_render_entry_formats_each_level():
    assert render_entry({"level": "INFO", "step": "static", "msg": "hello"}) == "hello"
    assert render_entry({"level": "DEBUG", "step": "geo", "msg": "d"}) == "[geo] d"
    assert render_entry({"level": "WARN", "step": "modal", "msg": "w"}) == "⚠ [modal] w"
    assert render_entry({"level": "ERROR", "step": "rs", "msg": "e"}) == "✗ [rs] e"
