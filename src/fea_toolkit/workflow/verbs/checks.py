"""The check verbs — pre-analysis checks, as ``table`` steps.

Three kinds of check live here, all demand-free (no solved results):

* **model checks** (``check_connectivity``, ``check_self_weight``,
  ``check_brace_buckling``) — wrap :mod:`fea_toolkit.model.checks`, which works
  on the parsed model alone;
* **capacity checks** (``member_shear_capacity``, ``wall_shear_check``,
  ``hinge_length``) — wrap :mod:`fea_toolkit.capacity`, the section/member
  capacity models, reporting *capacity* without comparing against a solved
  demand;
* **mesh quality** (``mesh_checks``) — wraps :mod:`fea_toolkit.mesh.checks` on
  the topology a ``mesh`` step produced.

**Why separate verbs rather than one `check` verb with a "which check" parameter.**
The checks take different arguments (a tolerance, a load-totals mapping, brace
ids and a K factor, a wall's membrane resultants, a concrete section).  One
verb with a `choices` selector would have to declare every check's parameters
and leave most of them inapplicable to most runs — a parameter surface that
*lies*.  Separate verbs keep each surface flat and true, and adding a check is
adding a verb.  The one exception is ``mesh_checks``, whose three metrics share
identical input and differ only in the metric — there a `metric` choice is
truthful.

Each verb keeps its heavy import inside the function, so importing the manifest
still does not load OpenSees.
"""

from typing import Any

from ..steps import (
    TABLE,
    ParamSpec,
    Step,
    StepContext,
    StepResult,
    Table,
    validate_params,
)

__all__ = [
    "BRACE_BUCKLING_PARAMS",
    "CONNECTIVITY_PARAMS",
    "HINGE_LENGTH_PARAMS",
    "MEMBER_SHEAR_CAPACITY_PARAMS",
    "MESH_CHECKS_PARAMS",
    "SELF_WEIGHT_PARAMS",
    "WALL_SHEAR_CHECK_PARAMS",
    "run_check_brace_buckling",
    "run_check_connectivity",
    "run_check_self_weight",
    "run_hinge_length",
    "run_member_shear_capacity",
    "run_mesh_checks",
    "run_wall_shear_check",
]

CONNECTIVITY_PARAMS: dict[str, ParamSpec] = {
    "tol": ParamSpec(
        default=1e-6,
        type=float,
        help="Coordinate tolerance for duplicate-node detection, in model length units.",
    ),
}

SELF_WEIGHT_PARAMS: dict[str, ParamSpec] = {
    "atol": ParamSpec(
        default=0.0,
        type=float,
        help="Absolute tolerance for the comparison; 0.0 lets the check use 1% of expected.",
    ),
    "verbose": ParamSpec(
        default=False,
        type=bool,
        help="Print the check's own summary to stdout as well as returning it.",
    ),
}

BRACE_BUCKLING_PARAMS: dict[str, ParamSpec] = {
    "brace_ids": ParamSpec(
        default=[],
        type=list,
        help="Element ids to check.  Empty uses the step's selection, or checks nothing.",
    ),
    "K": ParamSpec(
        default=1.0,
        type=float,
        help="Effective length factor (1.0 = pinned-pinned).",
    ),
    "print_results": ParamSpec(
        default=False,
        type=bool,
        help="Print the check's own table to stdout as well as returning it.",
    ),
}

MESH_CHECKS_PARAMS: dict[str, ParamSpec] = {
    "metric": ParamSpec(
        default="report",
        type=str,
        choices=("report", "aspect_ratios", "flatness", "skew"),
        help=(
            "Which metric to report — 'report' runs all three with their warning "
            "thresholds and a passed verdict; the others report one value per area."
        ),
    ),
    "aspect_warn": ParamSpec(
        default=4.0,
        type=float,
        help="Aspect-ratio warning threshold (default 4.0).",
    ),
    "flatness_warn": ParamSpec(
        default=0.02,
        type=float,
        help="Flatness-deviation warning threshold (default 0.02).",
    ),
    "skew_warn": ParamSpec(
        default=30.0,
        type=float,
        help="Skew warning threshold in degrees (default 30).",
    ),
}

#: ``hinge_length`` has no tunables — the plastic-hinge length formula is fixed
#: by the code (ASCE 41-17 §10.8).  The empty set still rejects an undeclared
#: parameter, so a recipe cannot reach an option the verb does not expose.
HINGE_LENGTH_PARAMS: dict[str, ParamSpec] = {}

MEMBER_SHEAR_CAPACITY_PARAMS: dict[str, ParamSpec] = {
    "aggregate_size_mm": ParamSpec(
        default=19.0,
        type=float,
        help="Coarse-aggregate size in mm (default 19), for the crack-spacing term.",
    ),
    "theta_deg": ParamSpec(
        default=35.0,
        type=float,
        help="Diagonal-compression field angle in degrees (default 35, CSA General Method).",
    ),
}

WALL_SHEAR_CHECK_PARAMS: dict[str, ParamSpec] = {
    "Nxy": ParamSpec(
        default=0.0,
        type=float,
        help="In-plane membrane shear resultant, force per unit width (model units).",
    ),
    "Ny": ParamSpec(
        default=0.0,
        type=float,
        help="Vertical membrane resultant, force per unit width (model units).",
    ),
}


def _pairs(title: str, mapping: dict) -> Table:
    """A two-column ``check / value`` table from an ordered mapping.

    Args:
        title: The table's title.
        mapping: The rows, in order.

    Returns:
        The table, every value rendered as text.
    """
    return Table(
        title=title,
        columns=("check", "value"),
        rows=tuple((str(name), str(value)) for name, value in mapping.items()),
    )


def _num(value: Any) -> str:
    """Render a number as a short, noise-free string for a table cell.

    Floats use six significant digits (so ``0.05`` stays ``0.05`` and
    ``500000.0`` stays ``500000``); anything else is passed through :func:`str`.

    Args:
        value: The cell value.

    Returns:
        Its string form.
    """
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def run_check_connectivity(context: StepContext, step: Step) -> list[StepResult]:
    """Check the model for the faults that make a stiffness matrix singular.

    Wraps :func:`fea_toolkit.model.checks.check_model_connectivity`.

    Args:
        context: The run's working state.  ``model_data`` is the model checked —
            the copy an earlier step may have edited, not the caller's original.
        step: The step to run; ``params`` carries the tolerance.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.TABLE` result counting each fault
        the check looks for.

    Raises:
        ValueError: If a parameter is not declared by this verb.
    """
    from ...model.checks import check_model_connectivity

    params: dict[str, Any] = validate_params("check_connectivity", CONNECTIVITY_PARAMS, step.params)
    result = check_model_connectivity(context.model_data, tol=params["tol"])

    table = _pairs(
        "Connectivity",
        {
            "orphan nodes": len(result.get("orphan_nodes") or []),
            "shell-only base nodes": len(result.get("shell_only_base_nodes") or []),
            "duplicate coordinates": len(result.get("duplicate_coords") or []),
            "zero-area sections": len(result.get("zero_area_sections") or []),
            "summary": result.get("summary", ""),
        },
    )
    context.log(f"Connectivity: {table.rows[-1][1]}")
    return [StepResult(kind=TABLE, label="Connectivity", payload=table)]


def run_check_self_weight(context: StepContext, step: Step) -> list[StepResult]:
    """Compare the model's self-weight against the weight its geometry implies.

    Wraps :func:`fea_toolkit.model.checks.check_self_weight_consistency`.

    Args:
        context: The run's working state.  ``model_data`` supplies the geometry.
        step: The step to run; ``params`` carries the tolerance and verbosity.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.TABLE` result: the expected and
        applied totals, their discrepancy, the tolerance and the verdict.

    Raises:
        ValueError: If a parameter is not declared by this verb.
    """
    from ...model.checks import check_self_weight_consistency

    params: dict[str, Any] = validate_params("check_self_weight", SELF_WEIGHT_PARAMS, step.params)
    result = check_self_weight_consistency(
        context.model_data,
        atol=params["atol"] or None,
        verbose=params["verbose"],
    )

    table = _pairs(
        "Self-weight",
        {
            "expected": result.get("expected"),
            "applied": result.get("applied"),
            "discrepancy": result.get("discrepancy"),
            "tolerance": result.get("tolerance"),
            "passed": result.get("passed"),
        },
    )
    context.log(f"Self-weight: passed={result.get('passed')}")
    return [StepResult(kind=TABLE, label="Self-weight", payload=table)]


def run_check_brace_buckling(context: StepContext, step: Step) -> list[StepResult]:
    """Check the named braces against Euler buckling.

    Wraps :func:`fea_toolkit.model.checks.check_brace_buckling`.

    Args:
        context: The run's working state.  ``model_data`` supplies the sections.
        step: The step to run.  ``selection`` narrows the braces when
            ``brace_ids`` is empty, so a check can be scoped like any other step;
            ``params`` carries the K factor and the printing flag.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.TABLE` result — one row per brace,
        holding whatever quantities the check reported for it.

    Raises:
        ValueError: If a parameter is not declared by this verb.
    """
    from ...model.checks import check_brace_buckling

    params: dict[str, Any] = validate_params(
        "check_brace_buckling", BRACE_BUCKLING_PARAMS, step.params
    )
    brace_ids = set(params["brace_ids"])
    if not brace_ids and step.selection is not None:
        # The step's selection scopes the check exactly as it scopes any other
        # step, so a recipe does not have to name ids a second way.
        brace_ids = set(step.selection.get_frame_ids(context.model_data))

    result = check_brace_buckling(
        context.model_data,
        brace_ids=brace_ids,
        K=params["K"],
        print_results=params["print_results"],
    )
    first = next(iter(result.values())) if result else {}
    columns = ("element", *first.keys())
    rows = tuple(
        (str(elem_id), *(str(value) for value in values.values()))
        for elem_id, values in result.items()
    )
    table = Table(title="Brace buckling", columns=columns, rows=rows)
    context.log(f"Brace buckling: {len(rows)} brace(s) checked")
    return [StepResult(kind=TABLE, label="Brace buckling", payload=table)]


def run_mesh_checks(context: StepContext, step: Step) -> list[StepResult]:
    """Report the quality of the meshed areas — aspect ratio, flatness and skew.

    Wraps :mod:`fea_toolkit.mesh.checks`, which reads the ``MeshModel`` a
    ``mesh`` step produced — the checks are about the *meshed* topology, so this
    verb is the one check that needs a prepared model.

    Args:
        context: The run's working state.  ``model`` must hold the ``MeshModel``
            a previous ``mesh`` step produced; ``model_data`` is not consulted.
        step: The step to run; ``params`` carry the metric and its thresholds.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.TABLE` result: a summary verdict
        for ``metric=\"report\"``, or one value per area for a single metric.

    Raises:
        ValueError: If a parameter is not declared by this verb.
    """
    from ...mesh.checks import aspect_ratios, flatness, report, skew

    params: dict[str, Any] = validate_params("mesh_checks", MESH_CHECKS_PARAMS, step.params)

    areas = context.model.area_elements
    nodes = context.model.nodes
    metric = params["metric"]
    if metric == "report":
        result = report(
            areas,
            nodes,
            aspect_warn=params["aspect_warn"],
            flatness_warn=params["flatness_warn"],
            skew_warn=params["skew_warn"],
        )
        rows = [
            ("elements", str(result["n_elements"])),
            ("passed", str(result["passed"])),
        ]
        rows += [("warning", warning) for warning in result["warnings"]]
        title = "Mesh quality"
        table = Table(title=title, columns=("check", "value"), rows=tuple(rows))
    else:
        fn = {"aspect_ratios": aspect_ratios, "flatness": flatness, "skew": skew}[metric]
        title = {"aspect_ratios": "Aspect ratios", "flatness": "Flatness", "skew": "Skew"}[metric]
        values = fn(areas, nodes)
        rows = tuple((aid, _num(value)) for aid, value in sorted(values.items()))
        table = Table(title=title, columns=("area", "value"), rows=rows)

    context.log(f"Mesh checks: {metric} over {len(areas)} area(s)")
    return [StepResult(kind=TABLE, label=title, payload=table)]


def run_hinge_length(context: StepContext, step: Step) -> list[StepResult]:
    """Plastic hinge length ``L_p`` (ASCE 41-17 §10.8) for the selected members.

    Wraps :func:`fea_toolkit.capacity.asce41.hinge_length`, which reads the
    member's section and material and returns ``L_p`` in model length units.

    Args:
        context: The run's working state.  ``model_data`` supplies the members,
            sections, materials and units (``None`` selection = every member).
        step: The step to run; ``selection`` picks the members.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.TABLE` result — one row per member.

    Raises:
        ValueError: If a parameter is not declared by this verb.
    """
    import math

    from ...capacity.asce41 import hinge_length

    validate_params("hinge_length", HINGE_LENGTH_PARAMS, step.params)
    md = context.model_data
    if step.selection is None:
        frame_ids = set(md.frame_elements)
    else:
        frame_ids = set(step.selection.get_frame_ids(md))

    rows = []
    for eid in sorted(frame_ids):
        elem = md.frame_elements.get(eid)
        if elem is None:
            continue
        sec_name = md.frame_assignments.get(eid, "")
        ni = md.nodes.get(elem.node_i)
        nj = md.nodes.get(elem.node_j)
        if ni is None or nj is None:
            continue
        length = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
        if length < 1e-12:
            continue
        rows.append((eid, sec_name, _num(length), _num(hinge_length(md, sec_name, length))))

    table = Table(
        title="Plastic hinge length",
        columns=("element", "section", "length", "Lp"),
        rows=tuple(rows),
    )
    context.log(f"Hinge length: {len(rows)} member(s) checked")
    return [StepResult(kind=TABLE, label="Hinge length", payload=table)]


def run_member_shear_capacity(context: StepContext, step: Step) -> list[StepResult]:
    """Nominal RC shear capacity (simplified MCFT) for the selected frame sections.

    Wraps :func:`fea_toolkit.capacity.shear_capacity.member_shear_capacity`, run
    with zero demand forces so the table reports *capacity* (``Vc``/``Vs``/``Vn``)
    rather than a demand/capacity ratio.  Sections without a concrete strength
    are skipped and logged, not failed.

    Args:
        context: The run's working state.  ``model_data`` supplies the sections,
            materials and units (``None`` selection = every frame section).
        step: The step to run; ``selection`` picks the members whose sections are
            checked; ``params`` carry the aggregate size and strut angle.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.TABLE` result — one row per section.

    Raises:
        ValueError: If a parameter is not declared by this verb.
    """
    from ...capacity.shear_capacity import member_shear_capacity

    params: dict[str, Any] = validate_params(
        "member_shear_capacity", MEMBER_SHEAR_CAPACITY_PARAMS, step.params
    )
    md = context.model_data
    if step.selection is None:
        sec_names = sorted({name for name in md.frame_assignments.values() if name})
    else:
        sec_names = sorted(
            {
                md.frame_assignments[fid]
                for fid in step.selection.get_frame_ids(md)
                if fid in md.frame_assignments
            }
        )

    rows = []
    for sec_name in sec_names:
        section = md.sections.get(sec_name)
        if section is None:
            continue
        concrete = md.materials.get(section.material)
        if concrete is None or (concrete.Fc or 0) <= 0:
            context.log(
                f"member_shear_capacity: section {sec_name!r} has no concrete strength — skipped"
            )
            continue
        try:
            result = member_shear_capacity(
                section,
                concrete,
                units=md.units,
                aggregate_size_mm=params["aggregate_size_mm"],
                theta_deg=params["theta_deg"],
            )
        except ValueError as exc:
            context.log(f"member_shear_capacity: section {sec_name!r} skipped: {exc}")
            continue
        rows.append(
            (
                sec_name,
                _num(result.vc),
                _num(result.vs),
                _num(result.vn),
                _num(result.vn_upper),
                _num(result.d),
                _num(result.dv),
                _num(result.bw),
            )
        )

    table = Table(
        title="Member shear capacity",
        columns=("section", "Vc", "Vs", "Vn", "Vn_upper", "d", "dv", "bw"),
        rows=tuple(rows),
    )
    context.log(f"Member shear capacity: {len(rows)} section(s) checked")
    return [StepResult(kind=TABLE, label="Member shear capacity", payload=table)]


def run_wall_shear_check(context: StepContext, step: Step) -> list[StepResult]:
    """In-plane shear / normal stress check (GB 50010) for the selected walls.

    Wraps :func:`fea_toolkit.capacity.gb50010.wall_shear_check`.  The demands are
    the step's ``Nxy`` / ``Ny`` membrane resultants — the check reports the
    stresses against the code limits rather than reading a solved case, so it
    stays a pre-analysis "what-if" check.  Areas without a shell section (or a
    concrete strength) are skipped and logged.

    Args:
        context: The run's working state.  ``model_data`` supplies the walls,
            their shell sections and materials (``None`` selection = every area).
        step: The step to run; ``selection`` picks the walls; ``params`` carry the
            ``Nxy`` / ``Ny`` resultants.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.TABLE` result — one row per wall.

    Raises:
        ValueError: If a parameter is not declared by this verb.
    """
    from ...capacity.gb50010 import wall_shear_check

    params: dict[str, Any] = validate_params(
        "wall_shear_check", WALL_SHEAR_CHECK_PARAMS, step.params
    )
    md = context.model_data
    if step.selection is None:
        area_ids = set(md.area_elements)
    else:
        area_ids = set(step.selection.get_area_ids(md))

    rows = []
    for aid in sorted(area_ids):
        sec_name = md.area_assignments.get(aid, "")
        section = md.sections.get(sec_name)
        thickness = getattr(section, "thickness", None) if section is not None else None
        if thickness is None or float(thickness) <= 0:
            context.log(f"wall_shear_check: area {aid!r} has no shell thickness — skipped")
            continue
        concrete = md.materials.get(section.material)
        if concrete is None or (concrete.Fc or 0) <= 0:
            context.log(
                f"wall_shear_check: area {aid!r} material has no concrete strength — skipped"
            )
            continue
        result = wall_shear_check(params["Nxy"], params["Ny"], float(thickness), concrete, md.units)
        rows.append(
            (
                aid,
                _num(result.tau),
                _num(result.tau_limit),
                _num(result.sigma),
                _num(result.sigma_limit),
                str(result.ok_shear),
                str(result.ok_normal),
            )
        )

    table = Table(
        title="Wall shear check",
        columns=("area", "tau", "tau_limit", "sigma", "sigma_limit", "ok_shear", "ok_normal"),
        rows=tuple(rows),
    )
    context.log(f"Wall shear check: {len(rows)} wall(s) checked")
    return [StepResult(kind=TABLE, label="Wall shear check", payload=table)]
