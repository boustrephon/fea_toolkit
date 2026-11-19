"""The ``open_results`` verb — load a results archive's cases into the session.

Loads a results archive (``.npz`` / ``.h5`` / ``.hdf5``) and hands back its
cases as a :data:`~fea_toolkit.workflow.steps.CASES` result, so a recipe — or
the GUI's ``File ▸ Open results…`` — registers one view per case through the
same fan-out a static solve uses.  The archive carries its own display geometry,
so this verb needs no source model.

Beyond the ``CASES`` result, it rebuilds the per-case payloads a downstream
``report`` / ``save_results`` step reads (:attr:`StepContext.case_results`), a
display-only ``MeshModel`` (:attr:`StepContext.model`) and a minimal
``SAPModelData`` carrying the archive's unit labels
(:attr:`StepContext.model_data`), so an archive-only recipe can report and
re-save the archive without the ``.s2k`` it came from.
"""

from typing import Any

from ..steps import CASES, ParamSpec, Step, StepContext, StepResult, validate_params

__all__ = ["OPEN_RESULTS_PARAMS", "run_open_results"]

OPEN_RESULTS_PARAMS: dict[str, ParamSpec] = {
    "path": ParamSpec(default="", type=str, help="Path to the results archive (required)."),
}


def run_open_results(context: StepContext, step: Step) -> list[StepResult]:
    """Load a results archive, rebuild its cases and hand them back.

    Besides the returned :data:`~fea_toolkit.workflow.steps.CASES` result, the
    run's context is updated so a later ``report`` / ``save_results`` step can
    consume the archive without a source model:

    * ``case_results`` — the archive's static cases as ``{case: payload}`` in
      the same shape a static solve fills (``nodal_displacements`` keyed by node
      id, ``element_forces`` as the component-keyed ``fx_i`` … ``mz_j`` arrays);
    * ``model`` — a display-only ``MeshModel`` rebuilt from the archive geometry;
    * ``model_data`` — a minimal ``SAPModelData`` carrying the archive's unit
      labels, so consumers can read ``md.units``.

    Args:
        context: The run's working state — its ``case_results``, ``model`` and
            ``model_data`` are replaced with the archive-derived equivalents
            when the archive holds static cases.
        step: The step to run.  ``params`` carry ``path``.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.CASES` result whose payload is the
        archive's full ``{name: ndarray}`` bundle.

    Raises:
        ValueError: If ``path`` is empty.
    """
    import numpy as np

    from ...io.results_repository import NpzResultsRepository
    from ...io.results_schema import STATIC_FORCE_ARRAYS
    from ...model.sap_data import SAPModelData

    def _first(values: Any, default: str = "") -> str:
        if values is None:
            return default
        flat = np.asarray(values).ravel()
        return str(flat[0]) if flat.size else default

    params: dict[str, Any] = validate_params("open_results", OPEN_RESULTS_PARAMS, step.params)
    path = params["path"]
    if not path:
        raise ValueError("open_results: 'path' is required (the results archive to load)")

    repository = NpzResultsRepository(path)

    # Rebuild the per-case payloads a downstream report / save_results step
    # reads, in the shape a static solve fills, so an archive-only recipe can
    # be reported and re-saved without a source model.
    case_results: dict[str, dict[str, Any]] = {}
    for case in repository.cases():
        arrays = repository.arrays_for(case)
        payload: dict[str, Any] = {}
        displacements = repository.nodal_displacements(case)
        if displacements:
            payload["nodal_displacements"] = displacements
        forces = {name: arrays[name] for name in STATIC_FORCE_ARRAYS if name in arrays}
        if forces:
            payload["element_forces"] = forces
        if payload:
            case_results[case] = payload
    context.case_results.update(case_results)

    if case_results:
        context.model = repository.as_model()
        context.model_data = SAPModelData(
            nodes={},
            restraints={},
            materials={},
            sections={},
            frame_elements={},
            area_elements={},
            frame_assignments={},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
            units={
                "F": _first(repository.metadata("force_unit"), "kN"),
                "L": _first(repository.metadata("length_unit"), "m"),
                "T": "C",
            },
        )

    context.log(f"open_results: loaded {path} ({len(repository.cases())} case(s))")
    return [StepResult(kind=CASES, label="Opened results", payload=repository.to_arrays())]
