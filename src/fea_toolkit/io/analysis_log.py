"""Structured analysis log — records warnings, errors, and skipped steps.

Usage::

    from fea_toolkit.io.analysis_log import AnalysisLog

    log = AnalysisLog()
    log.info("modal", f"{n_modes} modes, T1={periods[0]:.3f}s")
    log.warn("pushover", "+Y mode1: step 2/50 failed to converge")
    log.error("storey", f"Storey response failed: {e}")

    print(log.summary())     # plain-text summary
    log.to_json("path.json") # JSON export
"""

import json
import time
from typing import List, Dict, Optional


def _escape_md_cell(text: str) -> str:
    """Escape pipe characters and normalize newlines for markdown table cells."""
    return text.replace("|", "\\|").replace("\n", " ").replace("\r", "")


class AnalysisLog:
    """A simple structured log for analysis pipeline events."""

    def __init__(self):
        self._entries: List[Dict] = []
        self._start = time.monotonic()

    def info(self, step: str, msg: str) -> None:
        """Record an informational message."""
        self._entries.append({
            "level": "INFO",
            "step": step,
            "msg": msg,
            "time": time.monotonic(),
        })

    def warn(self, step: str, msg: str) -> None:
        """Record a warning."""
        self._entries.append({
            "level": "WARN",
            "step": step,
            "msg": msg,
            "time": time.monotonic(),
        })

    def error(self, step: str, msg: str) -> None:
        """Record an error (analysis step failed or was skipped)."""
        self._entries.append({
            "level": "ERROR",
            "step": step,
            "msg": msg,
            "time": time.monotonic(),
        })

    @property
    def entries(self) -> List[Dict]:
        return list(self._entries)

    def summary(self) -> str:
        """Return a plain-text summary of all entries (INFO excluded)."""
        lines = []
        for e in self._entries:
            if e["level"] == "INFO":
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
            json.dump({
                "generated": datetime.datetime.now().isoformat(),
                "n_entries": len(self._entries),
                "entries": [{
                    "level": e["level"],
                    "step": e["step"],
                    "msg": e["msg"],
                    "time_s": e["time"] - self._start,
                } for e in self._entries],
            }, f, indent=2)

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
            icon = {"INFO": "ℹ", "WARN": "⚠", "ERROR": "✗"}[e["level"]]
            t = e["time"] - self._start
            dur = 0.0 if idx == 0 else t - (prev_t - self._start)
            lines.append(
                f"| {icon} {e['level']} | {_escape_md_cell(e['step'])} | "
                f"{_escape_md_cell(e['msg'])} | {t:.1f} | {dur:.1f} |"
            )
            prev_t = e["time"]
        lines.append("</details>")
        return "\n".join(lines)
