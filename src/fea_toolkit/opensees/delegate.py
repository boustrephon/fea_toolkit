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

Besides :func:`run_static_out_of_process` (which returns the ``(raw, result)``
pair the static verb keeps), :func:`run_modal_out_of_process`,
:func:`run_rs_out_of_process` and :func:`run_pushover_out_of_process` delegate
the modal / response-spectrum / pushover analyses, which return an
:class:`~fea_toolkit.analysis.base.AnalysisResult` carried back through
``meta.json``.  A spectrum / pushover needs the modal result a preceding
``modal`` step produced; it is serialised with
:func:`~fea_toolkit.opensees._exchange.analysis_result_to_jsonable` and embedded
in the driver's ``--params-file``.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from .runtime import resolve_opensees_python, run_python

__all__ = [
    "run_modal_out_of_process",
    "run_pushover_out_of_process",
    "run_rs_out_of_process",
    "run_static_out_of_process",
]

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


def run_modal_out_of_process(context: Any, params: dict) -> Any:
    """Run a modal (eigenvalue) analysis out-of-process; return its result.

    Args:
        context: The recipe :class:`~fea_toolkit.workflow.steps.StepContext`.
        params: Keyword args for :func:`~fea_toolkit.analysis.modal.run_modal_analysis`
            — ``n_modes``, ``name``, ``config`` (all JSON-native).

    Returns:
        The :class:`~fea_toolkit.analysis.base.AnalysisResult` the modal
        analysis produced.
    """
    return _run_analysis_out_of_process(context, "modal", params)


def run_rs_out_of_process(context: Any, modal_result: Any, params: dict) -> Any:
    """Run a response-spectrum analysis out-of-process; return its result.

    Args:
        context: The recipe :class:`~fea_toolkit.workflow.steps.StepContext`.
        modal_result: The modal :class:`~fea_toolkit.analysis.base.AnalysisResult`
            a preceding modal step produced (embedded in the driver's params).
        params: Keyword args for
            :func:`~fea_toolkit.analysis.rs.run_response_spectrum_analysis` —
            ``direction``, ``T_spec``, ``Sa_spec``, ``damping``, ``n_modes``,
            ``name``, ``config``, ``record_frame_forces``, ``record_shell_forces``.

    Returns:
        The :class:`~fea_toolkit.analysis.base.AnalysisResult` the RS analysis
        produced.
    """
    from ._exchange import analysis_result_to_jsonable

    payload = dict(params)
    payload["modal"] = analysis_result_to_jsonable(modal_result)
    return _run_analysis_out_of_process(context, "rs", payload)


def run_pushover_out_of_process(context: Any, modal_result: Any, params: dict) -> Any:
    """Run a pushover analysis out-of-process; return its result.

    Args:
        context: The recipe :class:`~fea_toolkit.workflow.steps.StepContext`.
        modal_result: The modal :class:`~fea_toolkit.analysis.base.AnalysisResult`
            a preceding modal step produced (embedded in the driver's params).
        params: Keyword args for
            :func:`~fea_toolkit.analysis.pushover.run_pushover_analysis` —
            ``material_type``, ``gravity_patterns``, ``lateral_load_type``,
            ``max_disp_val``, ``num_steps``, ``brace_type``, ``brace_sections``,
            ``directions``, ``name``, ``config``.

    Returns:
        The :class:`~fea_toolkit.analysis.base.AnalysisResult` the pushover
        analysis produced.
    """
    from ._exchange import analysis_result_to_jsonable

    payload = dict(params)
    payload["modal"] = analysis_result_to_jsonable(modal_result)
    return _run_analysis_out_of_process(context, "pushover", payload)


def _run_analysis_out_of_process(context: Any, analysis: str, params: dict) -> Any:
    """Delegate one ``AnalysisResult``-returning analysis to the runtime interpreter.

    Writes the prepared model and a ``params.json`` keyword-argument file, runs
    :mod:`fea_toolkit.opensees.subprocess_driver`, and reads the
    :class:`~fea_toolkit.analysis.base.AnalysisResult` back out of ``meta.json``.

    Args:
        context: The recipe :class:`~fea_toolkit.workflow.steps.StepContext`.
        analysis: ``"modal"``, ``"rs"`` or ``"pushover"``.
        params: The JSON-native keyword-argument dict (``modal`` already
            serialised by the caller for ``rs`` / ``pushover``).

    Returns:
        The :class:`~fea_toolkit.analysis.base.AnalysisResult` the subprocess
        produced.

    Raises:
        ValueError: If no topology has been prepared.
        RuntimeError: If the subprocess exits non-zero (its trailing output is
            included in the message).
    """
    from ..io.stage_writer import write_model_file
    from ._exchange import analysis_result_from_jsonable, nested_to_jsonable

    mesh = context.model
    if mesh is None:
        raise ValueError(f"{analysis} needs a prepared topology — run a 'mesh' step first")

    python = resolve_opensees_python(context.opensees_python)
    context.log(f"delegating {analysis} analysis to {python} (no bundled OpenSees)")

    with tempfile.TemporaryDirectory(prefix=f"fea_toolkit_{analysis}_") as tmp:
        tmp_path = Path(tmp)
        model_file = tmp_path / "model.npz"
        params_file = tmp_path / "params.json"
        out_dir = tmp_path / "out"
        write_model_file(str(model_file), sap=context.model_data, mesh=mesh, fmt="npz")
        params_file.write_text(
            json.dumps(nested_to_jsonable(params), sort_keys=True), encoding="utf-8"
        )

        args = [
            "-m",
            "fea_toolkit.opensees.subprocess_driver",
            "--model",
            str(model_file),
            "--analysis",
            analysis,
            "--params-file",
            str(params_file),
            "--out-dir",
            str(out_dir),
        ]
        code, output = run_python(python, args, log=context.log)
        if code != 0:
            tail = "\n".join(output.splitlines()[-_TRACEBACK_TAIL:]) if output else ""
            raise RuntimeError(
                f"out-of-process {analysis} analysis failed (exit {code}) in {python!r}:\n{tail}"
            )

        meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
        return analysis_result_from_jsonable(meta["result"])
