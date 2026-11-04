"""Out-of-process solve delegation for the "separate" build.

When the bundled executable was compiled without ``openseespy`` (the "separate"
build), the solve verbs cannot run analysis in-process.  They delegate to a
Python interpreter that has OpenSeesPy installed — the runtime environment
resolved by :mod:`fea_toolkit.opensees.runtime` — by:

1. writing the prepared ``SAPModelData`` + ``MeshModel`` to a stage file
   (:func:`fea_toolkit.io.stage_writer.write_model_file`),
2. running :mod:`fea_toolkit.opensees.subprocess_driver` in that interpreter,
3. reading the driver's ``results.npz`` + ``meta.json`` back into the same
   in-memory shapes the in-process path returns.

The module imports nothing from OpenSees at module level, so the "separate"
build can import it (and every solve verb can call it) without triggering the
``openseespy`` import stub.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from .runtime import resolve_opensees_python, run_python

__all__ = ["run_static_out_of_process"]

#: Trailing lines of subprocess output shown when a delegated solve fails.
_TRACEBACK_TAIL = 30


def run_static_out_of_process(context: Any, cases: dict, config: dict) -> tuple[dict, dict]:
    """Run a static case set out-of-process; return ``(raw, result)``.

    Args:
        context: The recipe :class:`~fea_toolkit.workflow.steps.StepContext` —
            supplies ``model_data``, ``model``, ``opensees_python`` and ``log``.
        cases: ``{case_name: {pattern: factor}}``, in run order.
        config: ``AnalysisBuilder`` config dict (may be empty).

    Returns:
        ``(raw, result)`` where ``raw`` is the per-case raw results dict and
        ``result`` carries ``arrays`` / ``cases`` / ``failed`` / ``unreduced`` /
        ``cancelled`` / ``summaries`` — the same shape
        :func:`~fea_toolkit.analysis.linear.run_case_set` returns.

    Raises:
        ValueError: If no topology has been prepared.
        RuntimeError: If the subprocess exits non-zero (its trailing output is
            included in the message).
    """
    from ..io.npz_reader import read_results_npz
    from ..io.stage_writer import write_model_file
    from ._exchange import nested_from_jsonable

    mesh = context.model
    if mesh is None:
        raise ValueError("run_static needs a prepared topology — run a 'mesh' step first")

    python = resolve_opensees_python(context.opensees_python)
    context.log(f"delegating static analysis to {python} (no bundled OpenSees)")

    with tempfile.TemporaryDirectory(prefix="fea_toolkit_static_") as tmp:
        tmp_path = Path(tmp)
        model_file = tmp_path / "model.npz"
        out_dir = tmp_path / "out"
        write_model_file(str(model_file), sap=context.model_data, mesh=mesh, fmt="npz")

        args = [
            "-m",
            "fea_toolkit.opensees.subprocess_driver",
            "--model",
            str(model_file),
            "--analysis",
            "static",
            "--cases",
            json.dumps(cases, sort_keys=True),
            "--config",
            json.dumps(config or {}, sort_keys=True),
            "--out-dir",
            str(out_dir),
        ]
        code, output = run_python(python, args, log=context.log)
        if code != 0:
            tail = "\n".join(output.splitlines()[-_TRACEBACK_TAIL:]) if output else ""
            raise RuntimeError(
                f"out-of-process static analysis failed (exit {code}) in {python!r}:\n{tail}"
            )

        arrays = read_results_npz(str(out_dir / "results.npz"))
        meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
        raw = nested_from_jsonable(meta["raw"])
        result = {
            "arrays": arrays,
            "cases": meta["cases"],
            "failed": meta["failed"],
            "unreduced": meta.get("unreduced", []),
            "cancelled": meta["cancelled"],
            "summaries": nested_from_jsonable(meta["summaries"]),
        }

    return raw, result
