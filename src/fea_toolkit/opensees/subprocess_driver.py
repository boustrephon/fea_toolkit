"""Out-of-process analysis driver for the "separate" build.

Run inside a Python environment that has both ``openseespy`` and ``fea_toolkit``
installed — the runtime environment the solve verbs delegate to::

    python -m fea_toolkit.opensees.subprocess_driver \\
        --model model.npz --analysis static --cases '{"DEAD": {"DEAD": 1.0}}' \\
        --config '{}' --out-dir /tmp/run

It loads the prepared ``SAPModelData`` + ``MeshModel`` from a stage file
(:func:`~fea_toolkit.io.stage_writer.write_model_file`), runs the requested
case set with the same :func:`~fea_toolkit.analysis.linear.run_case_set` the
in-process verb uses, and writes:

* ``results.npz`` — the assembled archive arrays
  (:func:`~fea_toolkit.io.npz_writer.save_results_arrays`),
* ``meta.json`` — cases / failed / unreduced / cancelled, the summaries, and
  the per-case raw results, encoded by
  :mod:`fea_toolkit.opensees._exchange`.

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


def _write(out_dir: str, result: dict) -> None:
    """Persist ``results.npz`` and ``meta.json`` into *out_dir*."""
    from ..io.npz_writer import save_results_arrays
    from ._exchange import nested_to_jsonable

    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    save_results_arrays(str(target / "results.npz"), result["arrays"])
    meta = {
        "cases": result["cases"],
        "failed": result["failed"],
        "unreduced": result.get("unreduced", []),
        "cancelled": bool(result.get("cancelled", False)),
        "summaries": nested_to_jsonable(result.get("summaries", [])),
        "raw": nested_to_jsonable(result.get("raw", {})),
    }
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
    parser.add_argument("--analysis", required=True, choices=("static",), help="Analysis type.")
    parser.add_argument("--cases", default="{}", help="JSON {case: {pattern: factor}}.")
    parser.add_argument("--config", default="{}", help="JSON AnalysisBuilder config.")
    parser.add_argument("--out-dir", required=True, help="Directory for results.")
    args = parser.parse_args(argv)

    try:
        cases = json.loads(args.cases)
        config = json.loads(args.config)
        sap, mesh = _load_model(args.model)
        if args.analysis == "static":
            result = _run_static(sap, mesh, cases, config)
        else:  # pragma: no cover — argparse constrains the choice
            raise ValueError(f"unknown analysis type {args.analysis!r}")
        _write(args.out_dir, result)
        solved = len(result["cases"])
        failed = ", ".join(result["failed"]) if result["failed"] else "none"
        print(f"subprocess_driver: solved {solved} case(s); failed: {failed}")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
