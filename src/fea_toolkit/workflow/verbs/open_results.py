"""The ``open_results`` verb — load a results archive's cases into the session.

Loads a results archive (``.npz`` / ``.h5`` / ``.hdf5``) and hands back its
cases as a :data:`~fea_toolkit.workflow.steps.CASES` result, so a recipe — or
the GUI's ``File ▸ Open results…`` — registers one view per case through the
same fan-out a static solve uses.  The archive carries its own display geometry,
so this verb needs no source model.

Beyond the ``CASES`` result, it **replaces** the per-case payloads a downstream
``report`` / ``save_results`` step reads (:attr:`StepContext.case_results` — the
archive's cases, shell resultants included, never merged with a previously
opened archive's), rebuilds a display-only ``MeshModel``
(:attr:`StepContext.model`) and a minimal ``SAPModelData`` carrying the
archive's unit labels (:attr:`StepContext.model_data`), clears the prior solve's
non-static ``results`` and retains the archive's ``forces_coordinate_system`` —
so an archive-only recipe can report and re-save the archive without the
``.s2k`` it came from.
"""

from typing import Any

from ..steps import CASES, ParamSpec, Step, StepContext, StepResult, validate_params

__all__ = ["OPEN_RESULTS_PARAMS", "run_open_results"]

#: Membrane + bending shell resultants, spelled out (not derived from the schema
#: names) — the same rule as
#: :data:`fea_toolkit.io.results_repository._SHELL_COMPONENTS`, so the two lists
#: fail loudly side by side if the schema renames one.
_SHELL_COMPONENTS = ("fx", "fy", "fxy", "mx", "my", "mxy")

OPEN_RESULTS_PARAMS: dict[str, ParamSpec] = {
    "path": ParamSpec(default="", type=str, help="Path to the results archive (required)."),
}


def run_open_results(context: StepContext, step: Step) -> list[StepResult]:
    """Load a results archive, rebuild its cases and hand them back.

    Besides the returned :data:`~fea_toolkit.workflow.steps.CASES` result, the
    run's context is updated so a later ``report`` / ``save_results`` step can
    consume the archive without a source model:

    * ``case_results`` — **replaced** with the archive's static cases as
      ``{case: payload}`` in the same shape a static solve fills
      (``nodal_displacements`` keyed by node id, ``element_forces`` as the
      component-keyed ``fx_i`` … ``mz_j`` arrays, and ``shell_forces`` as
      ``{area_id: {fx … mxy, elem_tag, sec_name}}`` when the archive carries
      shell resultants) — never merged with a previously opened archive's cases;
    * ``model`` — a display-only ``MeshModel`` rebuilt from the archive geometry;
    * ``model_data`` — a minimal ``SAPModelData`` carrying the archive's unit
      labels, so consumers can read ``md.units``;
    * ``results`` — cleared of a prior solve's entries (``static_summaries``,
      ``modal``, ``rs``, …), then carrying the archive's
      ``forces_coordinate_system`` so a later ``save_results`` step writes the
      same convention back.

    Args:
        context: The run's working state — its ``case_results``, ``model`` and
            ``model_data`` are replaced with the archive-derived equivalents
            when the archive holds static cases, and its ``results`` are cleared
            (retaining only the archive's ``forces_coordinate_system``).
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

    # The archive's end-force coordinate convention, retained so a later
    # save_results step re-writes the same convention instead of defaulting to
    # "local" and mismatching unchanged component arrays (e.g. global forces on
    # inclined members).  ``results_arrays`` only writes "local" archives, so a
    # non-local archive is refused at save time rather than silently mislabelled.
    force_convention = "local" if repository.forces_are_local() else "global"

    def _shell_resultants(arrays: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Rebuild ``{area_id: {component, elem_tag, sec_name}}`` from a case's arrays.

        The archive stores the per-case shell block as flat, self-indexed arrays
        (``shell_sap_id`` / ``shell_*`` / ``shell_elem_tag`` / ``shell_sec_name``);
        ``save_results`` re-serialises them through
        ``_collect_case_shell_forces``, which reads the dict-of-dicts shape
        ``extract_static_shell_forces`` produces.  ``shell_sap_id`` is **unique
        within a case** — one row per analysed area, because the producer keys
        its result dict by ``area_sap_id`` — so keying the rebuilt dict by
        ``area_id`` cannot overwrite a duplicate.  Rebuilding that shape here —
        rather than via
        :meth:`~fea_toolkit.io.results_repository.ResultsRepository.shell_forces`,
        which drops ``elem_tag`` / ``sec_name`` — keeps the round-trip lossless.
        """
        ids = arrays.get("shell_sap_id")
        if ids is None:
            return {}
        resultants: dict[str, dict[str, Any]] = {}
        for index, area_id in enumerate(np.asarray(ids).ravel()):
            entry: dict[str, Any] = {}
            for component in _SHELL_COMPONENTS:
                values = arrays.get(f"shell_{component}")
                if values is not None and index < len(values):
                    entry[component] = float(values[index])
            tags = arrays.get("shell_elem_tag")
            if tags is not None and index < len(tags):
                entry["elem_tag"] = int(tags[index])
            names = arrays.get("shell_sec_name")
            if names is not None and index < len(names):
                entry["sec_name"] = str(names[index])
            if entry:
                resultants[str(area_id)] = entry
        return resultants

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
        shell_forces = _shell_resultants(arrays)
        if shell_forces:
            payload["shell_forces"] = shell_forces
        if payload:
            case_results[case] = payload

    # Rebuild the display geometry and the minimal model *before* touching the
    # session's state.  ``as_model`` reconstructs the mesh from the archive's
    # geometry arrays and can fail on a malformed archive; a failed
    # reconstruction must leave the prior context intact rather than
    # half-replaced (cases/results already swapped but model/model_data not).
    model = None
    model_data = None
    if case_results:
        model = repository.as_model()
        model_data = SAPModelData(
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

    # Opening an archive replaces the session's results wholesale: the cases are
    # the newly opened archive's (never merged with a previous archive's), and a
    # prior solve's non-static results (``static_summaries``, modal, RS, …) are
    # stale.  Only the archive's force convention is retained for the re-save.
    context.case_results = case_results
    context.results.clear()
    context.results["forces_coordinate_system"] = force_convention
    if model is not None:
        context.model = model
        context.model_data = model_data

    context.log(f"open_results: loaded {path} ({len(repository.cases())} case(s))")
    return [StepResult(kind=CASES, label="Opened results", payload=repository.to_arrays())]
