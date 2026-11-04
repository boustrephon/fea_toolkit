"""Out-of-process analysis driver for the "separate" build.

Run inside a Python environment that has both ``openseespy`` and ``fea_toolkit``
installed — the runtime environment the solve verbs delegate to::

    python -m fea_toolkit.opensees.subprocess_driver \
        --model model.npz --analysis static --cases '{"DEAD": {"DEAD": 1.0}}' \
        --config '{}' --out-dir /tmp/run

It loads the prepared ``SAPModelData`` + ``MeshModel`` from a stage file
(:func:`~fea_toolkit.io.stage_writer.write_model_file`) and runs the requested
analysis with the same analysis-layer functions the in-process verbs use:

* ``static`` — :func:`~fea_toolkit.analysis.linear.run_case_set` (the only one
  that produces archive arrays), writing ``results.npz`` + ``meta.json``
  (cases / failed / unreduced / cancelled / summaries / raw);
* ``modal`` / ``rs`` / ``pushover`` — :func:`~fea_toolkit.analysis.modal.run_modal_analysis`,
  :func:`~fea_toolkit.analysis.rs.run_response_spectrum_analysis` and
  :func:`~fea_toolkit.analysis.pushover.run_pushover_analysis`, whose
  :class:`~fea_toolkit.analysis.base.AnalysisResult` is written to ``meta.json``
  via :mod:`fea_toolkit.opensees._exchange`.

The modal / rs / pushover keyword arguments travel in a ``--params-file`` JSON
file (not the command line — a mode-shape payload can exceed ``ARG_MAX``), and
the modal result a spectrum / pushover combines is embedded in that file under
the ``modal`` key as the JSON form of an ``AnalysisResult``.

Everything OpenSees-bound is imported inside the functions, so the module can
be listed without a solver present.  ``main()`` catches and prints any failure
with a traceback and exits non-zero, so the caller's merged log shows why the
subprocess stopped.
"""

from __future__ import annotations

import argparse
import json
import traceback
from pathlib import Path
from typing import Any, Optional


def _load_model(model_path: str) -> tuple[Any, Any]:
    """Load ``(sap, mesh)`` from a stage file written by ``write_model_file``."""
    from ..io.stage_reader import read_model_stages

    sap = read_model_stages(model_path, "sap")
    mesh = read_model_stages(model_path, "mesh")
    return sap, mesh


def _run_static(sap: Any, mesh: Any, cases: dict, config: Optional[dict]) -> dict:
    """Solve a static case set, returning ``run_case_set``'s result plus ``raw``."""
    from ..analysis.linear import run_case_set

    raw: dict = {}
    result = run_case_set(sap, mesh, cases, config=config or None, raw_out=raw)
    result["raw"] = raw
    return result


def _run_analysis(analysis: str, mesh: Any, params: dict) -> Any:
    """Run one of the ``AnalysisResult``-returning analyses.

    Args:
        analysis: ``"modal"``, ``"rs"`` or ``"pushover"``.
        mesh: The prepared :class:`~fea_toolkit.model.mesh_model.MeshModel`.
        params: Keyword arguments for the analysis function.  For ``rs`` /
            ``pushover``, ``params["modal"]`` holds the JSON form of the
            :class:`~fea_toolkit.analysis.base.AnalysisResult` to combine/push.

    Returns:
        The :class:`~fea_toolkit.analysis.base.AnalysisResult` the analysis
        function produced.
    """
    from ._exchange import analysis_result_from_jsonable, nested_from_jsonable

    params = dict(params)
    modal = None
    if analysis in ("rs", "pushover"):
        modal = analysis_result_from_jsonable(params.pop("modal"))
    # The remaining params (T_spec / Sa_spec, config, …) may carry encoded
    # ndarray markers written by :func:`nested_to_jsonable`.
    params = nested_from_jsonable(params)

    if analysis == "modal":
        from ..analysis.modal import run_modal_analysis

        return run_modal_analysis(mesh, **params)
    if analysis == "rs":
        from ..analysis.rs import run_response_spectrum_analysis

        return run_response_spectrum_analysis(mesh, modal, **params)
    if analysis == "pushover":
        from ..analysis.pushover import run_pushover_analysis

        # A live AnalysisBuilder cannot cross the process boundary; the caller's
        # ``return_builders`` flag is deliberately dropped here.
        params["return_builders"] = False
        return run_pushover_analysis(mesh, modal, **params)
    raise ValueError(f"unknown analysis type {analysis!r}")


def _write_case_set(out_dir: str, result: dict) -> None:
    """Persist a static case set's ``results.npz`` and ``meta.json``."""
    from ..io.npz_writer import save_results_arrays
    from ._exchange import nested_to_jsonable

    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    save_results_arrays(str(target / "results.npz"), result["arrays"])
    meta = {
        "kind": "case_set",
        "cases": result["cases"],
        "failed": result["failed"],
        "unreduced": result.get("unreduced", []),
        "cancelled": bool(result.get("cancelled", False)),
        "summaries": nested_to_jsonable(result.get("summaries", [])),
        "raw": nested_to_jsonable(result.get("raw", {})),
    }
    (target / "meta.json").write_text(json.dumps(meta, sort_keys=True), encoding="utf-8")


def _write_analysis_result(out_dir: str, result: Any) -> None:
    """Persist an :class:`AnalysisResult` (modal / rs / pushover) as ``meta.json``."""
    from ._exchange import analysis_result_to_jsonable

    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    meta = {"kind": "analysis_result", "result": analysis_result_to_jsonable(result)}
    (target / "meta.json").write_text(json.dumps(meta, sort_keys=True), encoding="utf-8")


def main(argv: Optional[list] = None) -> int:
    """Parse the CLI, run one analysis, and write the results.

    Returns:
        ``0`` on success, ``1`` on any error (printed to stderr/stdout).
    """
    parser = argparse.ArgumentParser(
        prog="fea_toolkit.opensees.subprocess_driver",
        description="Run an OpenSees analysis out-of-process and write NPZ results.",
    )
    parser.add_argument("--model", required=True, help="Stage file (sap + mesh).")
    parser.add_argument(
        "--analysis",
        required=True,
        choices=("static", "modal", "rs", "pushover"),
        help="Analysis type.",
    )
    parser.add_argument("--cases", default="{}", help="JSON {case: {pattern: factor}} (static).")
    parser.add_argument("--config", default="{}", help="JSON AnalysisBuilder config (static).")
    parser.add_argument(
        "--params-file",
        default="",
        help="JSON file of keyword args (modal / rs / pushover).",
    )
    parser.add_argument("--out-dir", required=True, help="Directory for results.")
    args = parser.parse_args(argv)

    try:
        sap, mesh = _load_model(args.model)
        if args.analysis == "static":
            cases = json.loads(args.cases)
            config = json.loads(args.config)
            result = _run_static(sap, mesh, cases, config)
            _write_case_set(args.out_dir, result)
            solved = len(result["cases"])
            failed = ", ".join(result["failed"]) if result["failed"] else "none"
            print(f"subprocess_driver: solved {solved} case(s); failed: {failed}")
        else:
            params = json.loads(Path(args.params_file).read_text(encoding="utf-8"))
            result = _run_analysis(args.analysis, mesh, params)
            _write_analysis_result(args.out_dir, result)
            print(f"subprocess_driver: {args.analysis} complete ({result.name})")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
