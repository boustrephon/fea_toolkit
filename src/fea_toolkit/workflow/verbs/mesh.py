"""The ``mesh`` verb — prepare the model's topology.

Wraps :class:`~fea_toolkit.opensees.preprocessor.Preprocessor`: split frames at
their joints, mesh the areas into shells, and optionally subdivide the slabs at
the wall lines the detector finds.  Areas matching the step's ``selection`` are
held back as **loads-only** — no shell elements are created for them and their
loads become edge loads on the supporting frames.

The Preprocessor import is deliberately inside the function: importing this
module must not load OpenSees, so the verb manifest stays readable without it.
"""

from typing import Any

from ..steps import GEOMETRY, ParamSpec, Step, StepContext, StepResult, validate_params

__all__ = ["MESH_PARAMS", "run_mesh"]

#: The Preprocessor options the ``mesh`` verb exposes.  A Preprocessor option
#: that is absent here is unavailable to a recipe by design — exposing it is a
#: deliberate act, so the recipe surface cannot drift from the documented one.
MESH_PARAMS: dict[str, ParamSpec] = {
    "split_elements": ParamSpec(
        default=True,
        type=bool,
        help="Split frames at their joints before analysis.",
    ),
    "create_shells": ParamSpec(
        default=True,
        type=bool,
        help="Mesh area elements into shell elements (off = split frames only).",
    ),
    "split_areas_at_frame_edges": ParamSpec(
        default=True,
        type=bool,
        help="Cut slabs along the frame edges that meet them, so the meshes share nodes.",
    ),
    "detect_wall_slab_intersections": ParamSpec(
        default=True,
        type=bool,
        help="Report wall edges that pass through a slab without sharing a node.",
    ),
    "split_slabs_at_walls": ParamSpec(
        default=False,
        type=bool,
        help=(
            "Also cut the slabs along those wall lines.  Without it the wall and "
            "slab meshes are independent, so the two are not tied at the interface."
        ),
    ),
}


def run_mesh(context: StepContext, step: Step) -> list[StepResult]:
    """Preprocess the parsed model into a ``MeshModel``.

    Args:
        context: The run's working state.  Its ``model`` is replaced with the
            prepared topology, so a later analysis step consumes this mesh.
        step: The step to run.  ``selection`` designates the loads-only areas;
            ``params`` are Preprocessor configuration.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.GEOMETRY` result whose payload is
        the prepared ``MeshModel``.  ``context.model`` is set to the same object.

    Raises:
        ValueError: If a parameter is not declared by this verb.
    """
    from ...opensees.preprocessor import Preprocessor

    params: dict[str, Any] = validate_params("mesh", MESH_PARAMS, step.params)
    mesh = Preprocessor(dict(params)).run(context.model_data, load_shell_selection=step.selection)
    context.model = mesh

    label = "Meshed" if params["create_shells"] else "Split"
    context.log(
        f"{label}: {len(mesh.frame_elements)} frame elements, "
        f"{len(mesh.area_elements)} area elements"
        + (f", loads-only: {step.selection}" if step.selection is not None else "")
    )
    return [StepResult(kind=GEOMETRY, label=label, payload=mesh)]
