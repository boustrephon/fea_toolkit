"""Structured pipeline event log.

:class:`PipelineLog` is the toolkit's single structured log: sources *emit*
``(level, step, msg)`` events once, and *sinks* render them (stdout, JSON, a
Qt signal, …).  :func:`fea_toolkit.report.generate_report` collects one for the
report's "Analysis Log" table, while a bare analysis call falls back to
:func:`console_log` — a stdout-only log that retains nothing.

Usage::

    from fea_toolkit.io.log import PipelineLog

    log = PipelineLog()
    log.info("static", "build-once: reusing one OpenSees domain")
    log.warn("modal", "fewer than 3 modes converged")
    print(log.summary())
    print(log.markdown())
    log.to_json("analysis.log.json")

Levels are ``DEBUG`` < ``INFO`` < ``WARN`` < ``ERROR``; ``step`` is the
scoping key (the subpackage/phase that emitted the event).  ``to_json``
persists the event list as a machine-readable ``.log.json`` file.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Callable, ClassVar, Union

# ═══════════════════════════════════════════════════════════════════
# PipelineLog — structured pipeline event log (one event, many sinks)
# ═══════════════════════════════════════════════════════════════════


def _escape_md_cell(text: str) -> str:
    """Escape pipe characters and normalize newlines for markdown table cells."""
    return text.replace("|", "\\|").replace("\n", " ").replace("\r", "")


def render_entry(entry: dict) -> str:
    """Render one entry as the one-line string a text sink shows.

    ``INFO`` → the bare message; ``DEBUG`` → ``[step] msg``; ``WARN`` →
    ``⚠ [step] msg``; ``ERROR`` → ``✗ [step] msg``.  This is the shared format
    for a text sink (console, GUI message log) so the same event renders the
    same way everywhere.
    """
    level = entry["level"]
    if level == "WARN":
        return f"⚠ [{entry['step']}] {entry['msg']}"
    if level == "ERROR":
        return f"✗ [{entry['step']}] {entry['msg']}"
    if level == "DEBUG":
        return f"[{entry['step']}] {entry['msg']}"
    return entry["msg"]


class PipelineLog:
    """A structured pipeline event log: emit once, render via sinks.

    Every ``info``/``warn``/``error``/``debug`` call appends one entry (when
    ``collect``) and passes it to every registered sink.  The default stdout
    sink prints ``INFO``/``DEBUG`` only when ``verbose``, and always prints
    ``WARN``/``ERROR`` so a warning is never silently swallowed.

    Args:
        verbose: Gate the default stdout sink's ``INFO``/``DEBUG`` lines.
        collect: Retain entries (for ``summary``/``markdown``/``to_json``).
        _stdout: Install the default stdout sink (internal; ``False`` makes a
            fully silent log — see :func:`coerce_log`).
    """

    def __init__(
        self,
        *,
        verbose: bool = True,
        collect: bool = True,
        _stdout: bool = True,
    ) -> None:
        self._entries: list[dict] = []
        self._sinks: list[Callable[[dict], None]] = []
        self._start = time.monotonic()
        self._verbose = verbose
        self._collect = collect
        if _stdout:
            self.add_sink(self._stdout_sink)

    # ── Emission (single entry point) ──────────────────────────────

    def _emit(self, level: str, step: str, msg: str) -> None:
        entry = {
            "level": level,
            "step": step,
            "msg": msg,
            "time": time.monotonic(),
        }
        if self._collect:
            self._entries.append(entry)
        for sink in self._sinks:
            sink(entry)

    def info(self, step: str, msg: str) -> None:
        """Record an informational message."""
        self._emit("INFO", step, msg)

    def debug(self, step: str, msg: str) -> None:
        """Record a diagnostic message (printed only when verbose)."""
        self._emit("DEBUG", step, msg)

    def warn(self, step: str, msg: str) -> None:
        """Record a warning."""
        self._emit("WARN", step, msg)

    def warning(self, step: str, msg: str) -> None:
        """Record a warning (alias of :meth:`warn`)."""
        self.warn(step, msg)

    def error(self, step: str, msg: str) -> None:
        """Record an error (analysis step failed or was skipped)."""
        self._emit("ERROR", step, msg)

    # ── Sinks ──────────────────────────────────────────────────────

    def add_sink(self, fn: Callable[[dict], None]) -> None:
        """Register a renderer called with each emitted entry dict."""
        self._sinks.append(fn)

    def _stdout_sink(self, entry: dict) -> None:
        level = entry["level"]
        if level in ("INFO", "DEBUG") and not self._verbose:
            return
        print(render_entry(entry))

    @property
    def entries(self) -> list[dict]:
        return list(self._entries)

    def summary(self) -> str:
        """Return a plain-text summary of warnings and errors (INFO/DEBUG excluded)."""
        lines = []
        for e in self._entries:
            if e["level"] in ("INFO", "DEBUG"):
                continue
            icon = {"WARN": "⚠", "ERROR": "✗"}.get(e["level"], "·")
            lines.append(f"  {icon} [{e['step']}] {e['msg']}")
        if not lines:
            lines.append("  (no warnings or errors)")
        n_err = sum(1 for e in self._entries if e["level"] == "ERROR")
        n_warn = sum(1 for e in self._entries if e["level"] == "WARN")
        lines.insert(0, f"Analysis log — {n_err} error(s), {n_warn} warning(s)")
        return "\n".join(lines)

    def to_json(self, path: str) -> None:
        """Write log entries to a JSON file with elapsed-time keys."""
        import datetime

        with open(path, "w") as f:
            json.dump(
                {
                    "generated": datetime.datetime.now().isoformat(),
                    "n_entries": len(self._entries),
                    "entries": [
                        {
                            "level": e["level"],
                            "step": e["step"],
                            "msg": e["msg"],
                            "time_s": e["time"] - self._start,
                        }
                        for e in self._entries
                    ],
                },
                f,
                indent=2,
            )

    def markdown(self) -> str:
        """Return a collapsible markdown section for QMD display."""
        n_err = sum(1 for e in self._entries if e["level"] == "ERROR")
        n_warn = sum(1 for e in self._entries if e["level"] == "WARN")
        n_info = sum(1 for e in self._entries if e["level"] == "INFO")
        lines = [
            "<details open>",
            f"<summary>Analysis Log — {n_err} ✗, {n_warn} ⚠, {n_info} ℹ</summary>",
            "",
            "| Level | Step | Message | Time (s) | Dur. (s) |",
            "|-------|------|---------|----------|----------|",
        ]
        prev_t = self._start
        for idx, e in enumerate(self._entries):
            icon = {"INFO": "ℹ", "WARN": "⚠", "ERROR": "✗", "DEBUG": "🐞"}[e["level"]]
            t = e["time"] - self._start
            dur = 0.0 if idx == 0 else t - (prev_t - self._start)
            lines.append(
                f"| {icon} {e['level']} | {_escape_md_cell(e['step'])} | "
                f"{_escape_md_cell(e['msg'])} | {t:.1f} | {dur:.1f} |"
            )
            prev_t = e["time"]
        lines.append("</details>")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# Module helpers — the default logs and the ``log`` coercion
# ═══════════════════════════════════════════════════════════════════

#: Shared stdout-only log for callers that pass no ``log``: prints progress to
#: stdout and retains nothing, so a bare ``run_static_cases(...)`` still reports
#: without accumulating entries nobody reads.
console_log = PipelineLog(verbose=True, collect=False)

#: Shared fully-silent log for ``log=False`` (the old ``verbose=False``).
_silent_log = PipelineLog(verbose=False, collect=False, _stdout=False)


def coerce_log(log: Union[PipelineLog, bool, None]) -> PipelineLog:
    """Normalise the public ``log`` parameter to a :class:`PipelineLog`.

    ``None`` / ``True`` → :data:`console_log` (stdout, no retention);
    ``False`` → a fully silent log (no output, no retention); a
    :class:`PipelineLog` is returned unchanged.
    """
    if isinstance(log, PipelineLog):
        return log
    if log is False:
        return _silent_log
    return console_log


# ═══════════════════════════════════════════════════════════════════
# PipelineHandler — bridge stdlib ``logging`` records into a PipelineLog
# ═══════════════════════════════════════════════════════════════════


def _step_from_logger(name: str) -> str:
    """Map a logger name to a :class:`PipelineLog` step tag.

    ``fea_toolkit.opensees._constraints`` → ``constraints``: the leaf module
    name with a leading ``_`` / ``_runner_`` stripped.  A name with no dotted
    leaf is returned unchanged.
    """
    leaf = name.rsplit(".", 1)[-1]
    leaf = leaf.removeprefix("_runner_").removeprefix("_")
    return leaf or name


class PipelineHandler(logging.Handler):
    """Bridge stdlib :mod:`logging` records into a :class:`PipelineLog`.

    The toolkit's module-level ``logger = logging.getLogger(__name__)`` calls
    (builder Tcl-export wall warnings, the constraint narrative, limit-state
    skips, …) are the last ``logging``-based output.  Attach one of these to
    the ``fea_toolkit`` logger during a run (see
    :func:`fea_toolkit.report.generate_report`) so those warnings/infos become
    ``PipelineLog`` events instead of going to stderr.

    Level mapping: ``DEBUG→debug``, ``INFO→info``, ``WARNING→warn``,
    ``ERROR/CRITICAL→error``.  The ``step`` tag is derived from the record's
    logger name via :func:`_step_from_logger`.
    """

    _LEVEL_METHODS: ClassVar[dict[int, str]] = {
        logging.DEBUG: "debug",
        logging.INFO: "info",
        logging.WARNING: "warn",
        logging.ERROR: "error",
        logging.CRITICAL: "error",
    }

    def __init__(self, log: PipelineLog, level: int = logging.INFO) -> None:
        super().__init__(level=level)
        self._log = log

    def emit(self, record: logging.LogRecord) -> None:
        """Forward one record to the wrapped :class:`PipelineLog`."""
        method = self._LEVEL_METHODS.get(record.levelno)
        if method is None:
            return
        try:
            getattr(self._log, method)(_step_from_logger(record.name), record.getMessage())
        except Exception:  # a sink must never break the emit path
            self.handleError(record)
