"""Structured logging for model diagnostics and analysis-pipeline events.

:class:`ModelLog` writes model-build diagnostics to structured JSON log
files (with optional standalone PyVista visualisation scripts);
:class:`AnalysisLog` records analysis-pipeline warnings / errors / skipped
steps for the report workflow (``generate_report()``).

Each model build populates a :class:`ModelLog` with diagnostic findings
(wall nodes inside slab areas, disconnected nodes, tear detection gaps,
etc.) and saves it as ``{model_name}.log.json`` alongside the NPZ output.

Usage::

    from fea_toolkit.io.log import ModelLog

    log = ModelLog("Admin_0.7E_short term", output_dir="output")
    log.add_diagnostic(
        type="wall_slab_intersection",
        severity="warning",
        message="Wall 60 (Shear Wall) inside slab 335",
        details={
            "slab_id": "335",
            "wall_id": "60",
            "nodes": [
                {"node_id": "582", "node_tag": 422, "x": 55.0, "y": 22.0, "z": 13.28},
                {"node_id": "583", "node_tag": 423, "x": 55.0, "y": 17.4, "z": 13.28},
            ],
            "slab_X": [54.0, 60.0],
            "slab_Y": [16.0, 24.0],
            "slab_Z": 13.28,
        },
        fix_applied=False,
    )
    log.save()                          # → output/Admin_0.7E_short term.log.json
    log.to_script()                     # → output/Admin_0.7E_short term.log.py

Viewing diagnostics from a saved log::

    from fea_toolkit.io.log import load_log
    log = load_log("output/Admin_0.7E_short term.log.json")
    log.print_summary()

The ``.log.json`` file is machine-readable.  The ``.log.py`` file is a
self-contained Python script that runs PyVista to visualise all
diagnostics — run it standalone::

    python "output/Admin_0.7E_short term.log.py"
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional


class ModelLog:
    """Structured log of model diagnostics.

    Args:
        model_name: Human-readable model name (used for the log filename).
        output_dir: Directory for the log file (default ``"output"``).
        build_config: Optional dict of the builder config used for this
            build (included in the log for provenance).
    """

    def __init__(
        self,
        model_name: str,
        output_dir: str = "output",
        build_config: Optional[dict[str, Any]] = None,
    ) -> None:
        self.model_name = model_name
        self.output_dir = output_dir
        self.created = datetime.now(timezone.utc).isoformat()
        self.build_config = build_config or {}
        self.diagnostics: list[dict[str, Any]] = []
        self._next_id = 1

    # ── Public API ──────────────────────────────────────────────

    def add_diagnostic(
        self,
        type: str,
        severity: str,
        message: str,
        details: Optional[dict[str, Any]] = None,
        fix_applied: bool = False,
        visualisation_hint: str = "",
    ) -> int:
        """Record a diagnostic finding.

        Args:
            type: Diagnostic category, e.g. ``"wall_slab_intersection"``,
                ``"disconnected_node"``, ``"tear_detection_gap"``.
            severity: ``"info"``, ``"warning"``, or ``"error"``.
            message: Human-readable summary (one line).
            details: Structured data with enough context for a visualisation
                script to reconstruct the finding (node IDs, coordinates,
                element tags, etc.).
            fix_applied: Whether the builder automatically corrected this.
            visualisation_hint: Suggested plot type, e.g. ``"pyvista_3d"``,
                ``"matplotlib_2d"``.  Empty = use default.

        Returns:
            The 1‑based diagnostic ID.
        """
        entry: dict[str, Any] = {
            "id": self._next_id,
            "type": type,
            "severity": severity,
            "message": message,
            "details": details or {},
            "fix_applied": fix_applied,
            "visualisation_hint": visualisation_hint,
        }
        self.diagnostics.append(entry)
        self._next_id += 1
        return entry["id"]

    def save(self, filepath: Optional[str] = None) -> str:
        """Write the log to a ``.log.json`` file.

        Args:
            filepath: Full path for the output file.  If ``None``,
                generates ``{output_dir}/{model_name}.log.json``
                with spaces replaced by underscores.

        Returns:
            The path the file was written to.
        """
        if filepath is None:
            safe_name = re.sub(r"[^a-zA-Z0-9_\-.]", "_", self.model_name)
            filepath = os.path.join(self.output_dir, f"{safe_name}.log.json")
        # Prevent path traversal
        resolved = os.path.abspath(os.path.join(self.output_dir, os.path.basename(filepath)))
        if not resolved.startswith(os.path.abspath(self.output_dir)):
            raise ValueError(f"File path escapes output directory: {filepath}")
        filepath = resolved
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        with open(filepath, "w") as f:
            json.dump(self.to_dict(), f, indent=2, default=str)
        return filepath

    def to_script(self, filepath: Optional[str] = None) -> str:
        """Generate a self-contained ``.log.py`` visualisation script.

        The generated script:
        * Embeds all diagnostic data as inline JSON.
        * Imports PyVista and renders each diagnostic in a 3D scene.
        * Can be run standalone: ``python {filepath}``.

        Args:
            filepath: Full path for the output file.  If ``None``,
                generates ``{output_dir}/{model_name}.log.py``
                with spaces replaced by underscores.

        Returns:
            The path the file was written to.
        """
        if filepath is None:
            safe_name = re.sub(r"[^a-zA-Z0-9_\-.]", "_", self.model_name)
            filepath = os.path.join(self.output_dir, f"{safe_name}.log.py")
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)

        data_dict = self.to_dict()
        # Use Python repr for safe literal embedding
        data_repr = repr(data_dict)

        script = f'''#! /usr/bin/env python3
"""Diagnostic visualisation — generated by fea_toolkit.io.log.ModelLog."""
import json
import sys

import numpy as np
try:
    import pyvista as pv
except ImportError:
    print("pyvista is required for visualisation.  pip install pyvista")
    sys.exit(1)

_DATA = {data_repr}


def _render_wall_slab_intersection(diag: dict) -> pv.Plotter:
    \"\"\"Render a wall-inside-slab diagnostic.\"\"\"
    d = diag["details"]
    plotter = pv.Plotter()
    # Slab bounding box
    sx = d["slab_X"]
    sy = d["slab_Y"]
    sz = d["slab_Z"]
    corners = np.array([
        [sx[0], sy[0], sz],
        [sx[1], sy[0], sz],
        [sx[1], sy[1], sz],
        [sx[0], sy[1], sz],
    ])
    face = np.array([[4, 0, 1, 2, 3]])
    slab = pv.PolyData(corners, faces=face)
    plotter.add_mesh(slab, color="lightblue", opacity=0.3, show_edges=True,
                     edge_color="blue", line_width=1)

    # Wall nodes inside
    for wn in d.get("nodes", []):
        sphere = pv.Sphere(radius=0.3, center=(wn["x"], wn["y"], wn["z"]))
        plotter.add_mesh(sphere, color="red")
        plotter.add_point_labels(
            np.array([[wn["x"], wn["y"], wn["z"]]]),
            [f"W{{wn['node_id']}}"],
            font_size=10, point_size=0,
        )

    # Wall edge line
    if len(d.get("nodes", [])) >= 2:
        xs = [n["x"] for n in d["nodes"]]
        ys = [n["y"] for n in d["nodes"]]
        zs = [n["z"] for n in d["nodes"]]
        pts = np.column_stack([xs, ys, zs])
        plotter.add_lines(pts, color="red", width=3)

    plotter.show_grid()
    plotter.enable_terrain_style()
    bounds = plotter.bounds
    cx = (bounds[0] + bounds[1]) * 0.5
    cy = (bounds[2] + bounds[3]) * 0.5
    cz = (bounds[4] + bounds[5]) * 0.5
    dmax = max(bounds[1] - bounds[0], bounds[3] - bounds[2],
               bounds[5] - bounds[4], 1.0) * 1.5
    plotter.camera.position = (cx + dmax, cy + dmax, cz + dmax * 0.3)
    plotter.camera.focal_point = (cx, cy, cz)
    plotter.camera.up = (0, 0, 1)
    return plotter


def _render_all(entries: list) -> pv.Plotter:
    \"\"\"Combine all diagnostics into a single 3D scene.\"\"\"
    plotter = pv.Plotter()
    for diag in entries:
        if diag["type"] == "wall_slab_intersection":
            d = diag["details"]
            sx = d["slab_X"]
            sy = d["slab_Y"]
            sz = d["slab_Z"]
            corners = np.array([
                [sx[0], sy[0], sz], [sx[1], sy[0], sz],
                [sx[1], sy[1], sz], [sx[0], sy[1], sz],
            ])
            face = np.array([[4, 0, 1, 2, 3]])
            slab = pv.PolyData(corners, faces=face)
            color = "green" if diag.get("fix_applied") else "red"
            plotter.add_mesh(slab, color=color, opacity=0.2, show_edges=True,
                             edge_color=color, line_width=1)
            for wn in d.get("nodes", []):
                sphere = pv.Sphere(radius=0.2, center=(wn["x"], wn["y"], wn["z"]))
                plotter.add_mesh(sphere, color=color)
    plotter.show_grid()
    plotter.enable_terrain_style()
    return plotter


def print_summary(entries: list) -> None:
    \"\"\"Print a human-readable summary of all diagnostics.\"\"\"
    if not entries:
        print("  No diagnostics recorded.")
        return
    for diag in entries:
        fix = "✓ Fixed" if diag["fix_applied"] else "✗ Unfixed"
        print(f"  [#{{diag['id']}}] {{diag['severity'].upper()}} "
              f"{{diag['type']}} — {{diag['message']}} [{{fix}}]")
    n_unfixed = sum(1 for d in entries if not d["fix_applied"])
    if n_unfixed:
        print(f"\\n  ⚠ {{n_unfixed}} unfixed diagnostic(s) remaining.")
    else:
        print(f"\\n  All {{len(entries)}} diagnostics resolved.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Visualise diagnostics from a fea_toolkit model build.")
    parser.add_argument("--summary", action="store_true",
                        help="Print summary only (no plot)")
    parser.add_argument("--slab", type=str, default="",
                        help="Focus on a specific slab ID (e.g. '335')")
    args = parser.parse_args()

    entries = _DATA["diagnostics"]
    ws_entries = [d for d in entries if d["type"] == "wall_slab_intersection"]
    if args.slab:
        ws_entries = [d for d in ws_entries if d["details"].get("slab_id") == args.slab]

    print(f"Model: {{_DATA['model_name']}}")
    print(f"Created: {{_DATA['created']}}")
    print(f"Diagnostics: {{len(entries)}} ({{len(ws_entries)}} wall-slab)")
    print()

    if not entries:
        print("  No diagnostics recorded.")
        sys.exit(0)

    print_summary(entries)

    if args.summary:
        sys.exit(0)

    if ws_entries:
        label = f" for slab {{args.slab}}" if args.slab else ""
        print(f"\\nOpening visualisation{{label}}...")
        print("  Colour key:")
        print("    Blue quad  = target slab (labelled corner coordinates)")
        print("    Red edges  = walls with nodes inside this slab")
        print("    Orange     = adjacent walls sharing an edge (above/below)")
        print("    Red sphere = wall node location (ID + tag)")
        print()
        plotter = _render_wall_slab_intersection(ws_entries[0])
        if plotter:
            plotter.show()
        else:
            plotter = _render_all(entries)
            plotter.show()
    else:
        plotter = _render_all(entries)
        plotter.show()
'''
        with open(filepath, "w") as f:
            f.write(script.lstrip("\n"))
        os.chmod(filepath, 0o755)
        return filepath

    def print_summary(self) -> None:
        """Print a human-readable summary to stdout."""
        n = len(self.diagnostics)
        if n == 0:
            print(f"  [{self.model_name}] No diagnostics recorded.")
            return
        n_unfixed = sum(1 for d in self.diagnostics if not d["fix_applied"])
        print(
            f"  [{self.model_name}] {n} diagnostic(s): "
            f"{n_unfixed} unfixed, {n - n_unfixed} resolved."
        )
        for diag in self.diagnostics:
            fix = "✓" if diag["fix_applied"] else "✗"
            print(
                f"    [#{diag['id']}] {diag['severity'].upper():>7} "
                f"{diag['type']:<30} {diag['message']}  [{fix}]"
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "model_name": self.model_name,
            "output_dir": self.output_dir,
            "created": self.created,
            "build_config": self.build_config,
            "diagnostics": self.diagnostics,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelLog:
        """Deserialise from a dict (e.g. loaded from JSON)."""
        log = cls(
            model_name=data.get("model_name", "unknown"),
            output_dir=data.get("output_dir", "output"),
            build_config=data.get("build_config"),
        )
        log.created = data.get("created", log.created)
        log.diagnostics = data.get("diagnostics", [])
        if log.diagnostics:
            log._next_id = max(d["id"] for d in log.diagnostics) + 1
        return log

    def merge(self, other: ModelLog) -> None:
        """Merge diagnostics from another log into this one."""
        import copy

        for diag in other.diagnostics:
            merged = copy.deepcopy(diag)
            merged["id"] = self._next_id
            self.diagnostics.append(merged)
            self._next_id += 1


def load_log(filepath: str) -> ModelLog:
    """Load a :class:`ModelLog` from a ``.log.json`` file.

    Args:
        filepath: Path to the ``.log.json`` file.

    Returns:
        Deserialised :class:`ModelLog`.
    """
    with open(filepath) as f:
        data = json.load(f)
    return ModelLog.from_dict(data)


def print_log_summary(filepath: str) -> None:
    """Quick summary of a saved log file."""
    log = load_log(filepath)
    log.print_summary()


# ═══════════════════════════════════════════════════════════════════
# AnalysisLog — structured analysis-pipeline event log
# ═══════════════════════════════════════════════════════════════════


def _escape_md_cell(text: str) -> str:
    """Escape pipe characters and normalize newlines for markdown table cells."""
    return text.replace("|", "\\|").replace("\n", " ").replace("\r", "")


class AnalysisLog:
    """A simple structured log for analysis pipeline events."""

    def __init__(self):
        self._entries: list[dict] = []
        self._start = time.monotonic()

    def info(self, step: str, msg: str) -> None:
        """Record an informational message."""
        self._entries.append(
            {
                "level": "INFO",
                "step": step,
                "msg": msg,
                "time": time.monotonic(),
            }
        )

    def warn(self, step: str, msg: str) -> None:
        """Record a warning."""
        self._entries.append(
            {
                "level": "WARN",
                "step": step,
                "msg": msg,
                "time": time.monotonic(),
            }
        )

    def warning(self, step: str, msg: str) -> None:
        """Record a warning (alias of :meth:`warn` for ``generate_report()``)."""
        self.warn(step, msg)

    def error(self, step: str, msg: str) -> None:
        """Record an error (analysis step failed or was skipped)."""
        self._entries.append(
            {
                "level": "ERROR",
                "step": step,
                "msg": msg,
                "time": time.monotonic(),
            }
        )

    @property
    def entries(self) -> list[dict]:
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
