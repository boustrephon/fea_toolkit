"""The ``scale_sections`` verb — make a selection's sections less stiff.

This is **option 1** of the two ways to treat masonry (or any element) as
non-structural while keeping its weight:

1. **Reduce the stiffness in place** — this verb.  The elements are still built
   in OpenSees, so their loads and mass stay on them; only the stiffness drops.
2. **Leave the elements out** — :mod:`fea_toolkit.workflow.verbs.mesh` with a
   ``selection``, which holds those areas back as *loads-only*: no shell element
   is created and their loads become edge loads on the supporting frames.  The
   mass then has to be injected explicitly, because the element that carried it
   is gone.

The two are independent, and both are wanted: option 1 keeps a wall's weight and
a little stiffness, option 2 removes the wall from the stiffness system
altogether.  A recipe may use either, or neither.

The parsed model is **copied before it is edited**, so a recipe never mutates
the caller's model; the scaled copy becomes
:attr:`~fea_toolkit.workflow.steps.StepContext.model_data`, which the next step
(usually :mod:`fea_toolkit.workflow.verbs.mesh`) reads.

Stiffness lives in a different property per element kind — ``A``/``I33``/``I22``/
``J`` for a frame, ``thickness`` for a shell — so the default ``attributes``
(``"auto"``) scales whichever of those a matched section actually carries, and
the verb reports what it changed.  Note that a shell's ``thickness`` drives its
mass as well as its stiffness, so scaling it lightens the wall too; a wall whose
weight must be exactly preserved is better served by option 2, with the mass
supplied explicitly.
"""

import copy
from typing import Any

from ..steps import MODEL, ParamSpec, Step, StepContext, StepResult, validate_params

__all__ = ["SCALE_SECTIONS_PARAMS", "run_scale_sections"]

#: The properties ``attributes="auto"`` will scale: frame stiffness first, then
#: shell thickness.  Scaling is skipped for any a section does not have, and for
#: any that is zero, so one list serves both element kinds.
_AUTO_ATTRIBUTES = ("A", "I33", "I22", "J", "thickness")

SCALE_SECTIONS_PARAMS: dict[str, ParamSpec] = {
    "factor": ParamSpec(
        default=1.0,
        type=float,
        help=(
            "Multiplier applied to each named property.  The masonry convention is "
            "0.01; 1.0 (the default) changes nothing."
        ),
    ),
    "attributes": ParamSpec(
        default="auto",
        type=str,
        help=(
            "'auto' scales the stiffness property each matched section carries — "
            "A/I33/I22/J for a frame, thickness for a shell.  Or name them: "
            "'A,I33' / 'thickness'."
        ),
    ),
}


def _selected_sections(model: Any, selection: Any) -> list[str]:
    """The section names the selection's elements are assigned to.

    Args:
        model: The ``SAPModelData`` to resolve against.
        selection: The step's selection, or ``None`` for every referenced section.

    Returns:
        Sorted section names.  Sections no matching element uses are excluded —
        the verb acts on a selection, not on the section table.
    """
    if selection is None:
        names = set(model.frame_assignments.values()) | set(model.area_assignments.values())
        return sorted(n for n in names if n)

    names = set()
    for eid in selection.get_frame_ids(model):
        section = model.frame_assignments.get(eid)
        if section:
            names.add(section)
    for aid in selection.get_area_ids(model):
        section = model.area_assignments.get(aid)
        if section:
            names.add(section)
    return sorted(names)


def run_scale_sections(context: StepContext, step: Step) -> list[StepResult]:
    """Scale the stiffness properties of the sections a selection matches.

    The elements are **kept** — only their sections' properties change — so the
    loads and mass they carry stay on them.  See the module docstring for how
    this differs from the loads-only route.

    Args:
        context: The run's working state.  Its ``model_data`` is replaced with a
            scaled copy; the caller's own model is left untouched.
        step: The step to run.  ``selection`` picks the elements whose sections
            are scaled (``None`` = every referenced section); ``params`` are
            ``factor`` and ``attributes`` (``"auto"`` unless named).

    Returns:
        One :data:`~fea_toolkit.workflow.steps.MODEL` result whose payload is the
        scaled ``SAPModelData``.

    Raises:
        ValueError: If a parameter is not declared by this verb.
    """
    params: dict[str, Any] = validate_params("scale_sections", SCALE_SECTIONS_PARAMS, step.params)
    factor = params["factor"]
    named = params["attributes"].strip()
    if named.lower() == "auto":
        attributes = list(_AUTO_ATTRIBUTES)
    else:
        attributes = [name.strip() for name in named.split(",") if name.strip()]

    model = copy.deepcopy(context.model_data)
    changed: dict[str, list[str]] = {}
    inert: list[str] = []
    for name in _selected_sections(model, step.selection):
        section = model.sections.get(name)
        if section is None:
            context.log(f"scale_sections: section {name!r} is not defined — skipped")
            continue
        touched = []
        for attr in attributes:
            value = getattr(section, attr, None)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if value:
                setattr(section, attr, value * factor)
                touched.append(attr)
        if touched:
            changed[name] = touched
        else:
            inert.append(name)

    context.model_data = model

    if factor == 1.0:
        context.log("scale_sections: factor 1.0 — no property changed")
    else:
        context.log(
            f"scale_sections: factor {factor} on {attributes} — "
            f"{len(changed)} section(s) scaled"
            + (f", {len(inert)} unchanged (every named property is zero)" if inert else "")
        )
        for name, touched in sorted(changed.items()):
            context.log(f"    {name}: {', '.join(touched)}")
    return [StepResult(kind=MODEL, label="Sections scaled", payload=model)]
