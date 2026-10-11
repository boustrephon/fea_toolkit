"""Static linear analysis runners.

These functions execute the OpenSees solver (or combine its results) for the
static-linear verification workflow.  They live in ``analysis/`` (not ``io/``)
because they run analyses rather than only formatting data.

The pandas *summary* helpers they depend on (``bounding_box``,
``load_pattern_totals``) remain in :mod:`fea_toolkit.io.report`.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Callable, Optional

import numpy as np

if TYPE_CHECKING:
    from ..io.log import PipelineLog

try:
    import pandas as pd
except ImportError:  # pragma: no cover — pandas is optional (Rhino 8 CPython)

    class _MissingPandas:
        """Raise a clear error when a linear-analysis helper needs pandas.

        pandas is not a required dependency of the toolkit (see
        ``pyproject.toml`` core deps) and is absent from Rhino 8's
        bundled CPython.  The module must still import so that
        ``import fea_toolkit.analysis`` works without it; only actually
        calling a helper that needs a DataFrame raises.
        """

        def __getattr__(self, _name: str):
            raise RuntimeError(
                "linear analysis helpers require pandas, which is not "
                "installed in this Python environment (pip install pandas)."
            )

    pd = _MissingPandas()  # type: ignore[assignment]

from ..io.report import bounding_box
from ..model.sap_data import SAPModelData
from .case_listing import list_static_cases


def wind_sanity_data(
    md,
    df_linear,
    wind_case_x: str = "Wind+X",
    wind_case_y: str = "Wind+Y",
) -> dict:
    """Return structured wind-load sanity-check data.

    **Basis** — this is a plausibility test that the applied wind load
    scales with the building envelope, not a code/wind-tunnel pressure.
    Every number derives from the model itself:

    - ``x_face`` = bounding-box ``y_span`` × ``z_span`` — the face normal to
      global X (the windward face for the +X wind case);
    - ``y_face`` = bounding-box ``x_span`` × ``z_span`` — the face normal to
      global Y;
    - ``fx`` / ``fy`` = the absolute base reaction (``Fx`` from the
      ``Wind+X`` case, ``Fy`` from ``Wind+Y``) read from the linear
      analysis results;
    - ``p_x`` = ``fx / x_face`` and ``p_y`` = ``fy / y_face`` — the implied
      **mean pressure** on each face.

    ``within_10pct`` is ``True`` when ``|p_x - p_y| / max(p_x, p_y) < 0.1``
    — the two implied pressures agree, the signature of a consistent wind
    load set.  A large mismatch usually flags a missing/duplicated wind
    area or a load applied to the wrong face.  All quantities are in the
    model's own unit system.

    This is the structured form of :func:`wind_sanity_check`, so callers
    that build their own tables (e.g. the model review) can reuse the same
    numbers.

    Args:
        md: Model data (for node coordinates and units).
        df_linear: Linear analysis results; must contain ``Case``, ``Fx``,
            ``Fy`` columns.
        wind_case_x: Case name for the X wind load pattern.
        wind_case_y: Case name for the Y wind load pattern.

    Returns:
        Keys ``rows`` (unit-labelled table rows: ``Face``, ``Area``,
        ``Total``, ``Pressure``), ``within_10pct`` (bool), the raw values
        ``x_face`` / ``y_face`` / ``fx`` / ``fy`` / ``p_x`` / ``p_y``, the
        ``force_unit`` / ``length_unit`` labels and the ``bounding_box``.
    """
    bb = bounding_box(md)

    x_face = bb["y_span"] * bb["z_span"]
    y_face = bb["x_span"] * bb["z_span"]
    fu = md.units.get("F", "?")
    lu = md.units.get("L", "m")

    wind_x = df_linear[df_linear["Case"] == wind_case_x]
    wind_y = df_linear[df_linear["Case"] == wind_case_y]

    fx = abs(wind_x["Fx"].values[0]) if len(wind_x) else 0
    fy = abs(wind_y["Fy"].values[0]) if len(wind_y) else 0

    p_x = fx / x_face if x_face > 0 else 0
    p_y = fy / y_face if y_face > 0 else 0

    rows = [
        {
            "Face": "Wind +X (Y-Z face)",
            f"Area ({lu}\u00b2)": f"{x_face:.0f}",
            f"Total ({fu})": f"{fx:,.0f}",
            f"Pressure ({fu}/{lu}\u00b2)": f"{p_x:.2f}",
        },
        {
            "Face": "Wind +Y (X-Z face)",
            f"Area ({lu}\u00b2)": f"{y_face:.0f}",
            f"Total ({fu})": f"{fy:,.0f}",
            f"Pressure ({fu}/{lu}\u00b2)": f"{p_y:.2f}",
        },
    ]

    return {
        "rows": rows,
        "within_10pct": bool(max(p_x, p_y) > 0 and abs(p_x - p_y) / max(p_x, p_y) < 0.1),
        "x_face": x_face,
        "y_face": y_face,
        "fx": fx,
        "fy": fy,
        "p_x": p_x,
        "p_y": p_y,
        "force_unit": fu,
        "length_unit": lu,
        "bounding_box": bb,
    }


def wind_sanity_check(md, df_linear, wind_case_x: str = "Wind+X", wind_case_y: str = "Wind+Y"):
    """Return a markdown paragraph checking wind loads against face area.

    Args:
        md: Model data (for node coordinates and units).
        df_linear: Linear analysis results; must contain ``Case``, ``Fx``,
            ``Fy`` columns.
        wind_case_x: Case name for the X wind load pattern.
        wind_case_y: Case name for the Y wind load pattern.

    Returns:
        Markdown paragraph with bounding-box summary and wind-pressure
        table.
    """
    data = wind_sanity_data(md, df_linear, wind_case_x, wind_case_y)
    bb = data["bounding_box"]
    fu = data["force_unit"]
    lu = data["length_unit"]

    # Render the table body from the preformatted ``data["rows"]`` so the
    # markdown and the structured data used by the model review can never
    # drift apart.
    table_rows = [
        "| " + " | ".join(str(value) for value in row.values()) + " |" for row in data["rows"]
    ]

    lines = [
        f"**Bounding box:** "
        f"{bb['x_span']:.1f} {lu} (X) × "
        f"{bb['y_span']:.1f} {lu} (Y) × "
        f"{bb['z_span']:.1f} {lu} (Z) — "
        f"{bb['n_nodes']} nodes.",
        "",
        "**Wind load sanity check:**",
        "",
        f"| Face | Area ({lu}²) | Total {fu} | Pressure ({fu}/{lu}²) |",
        "|---|---|---|---|",
        *table_rows,
    ]

    if data["within_10pct"]:
        lines.append("")
        lines.append(
            "✅ Pressures are within 10 % — wind loads are consistent "
            "with the bounding box face areas."
        )

    return "\n".join(lines)


def static_load_verification(md, mesh_model, config: Optional[dict] = None):
    """Check equilibrium between applied loads and reactions.

    Combines the applied loads (per-pattern per-component totals
    accumulated by ``AnalysisBuilder.create_loads()``) with the
    reactions (from
    :meth:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder.check_load_equilibrium`)
    into a single table with Applied, Reaction, and Δ columns.

    Args:
        md: Parsed model data.
        mesh_model: Pre-processed mesh model.
        config: Optional builder configuration.

    Returns:
        DataFrame with one row per load pattern and Applied/Reaction/Δ
        columns.
    """
    from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

    if config is None:
        config = {"verbose": False}

    fu = md.units.get("F", "?")

    ab = AnalysisBuilder(mesh_model, config)
    ab.build_domain()
    # Apply ALL load patterns once so the per-pattern applied totals
    # (_sw/_gravity/_joint/_dist) are populated for every pattern.
    ab.create_loads()

    def _clean(v):
        """Coerce a merged-cell value to a finite float, else 0.0."""
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return 0.0
        return fv if math.isfinite(fv) else 0.0

    # ── Applied totals: per-pattern per-component ─────────────────
    applied: dict[str, dict[str, float]] = {}
    for _src in (
        getattr(ab, "_sw_load_totals", {}),
        getattr(ab, "_gravity_load_totals", {}),
        getattr(ab, "_joint_load_totals", {}),
        getattr(ab, "_dist_load_totals", {}),
    ):
        for pname, t in _src.items():
            d = applied.setdefault(pname, dict.fromkeys(("fx", "fy", "fz", "mx", "my", "mz"), 0.0))
            for _k in d:
                d[_k] += _clean(t.get(_k, 0.0))

    # ── Reactions: per-pattern static runs ────────────────────────
    df_rxn = ab.check_load_equilibrium()

    rows = []
    all_names = set(applied) | set(df_rxn["Load Pattern"])
    for pname in sorted(all_names, key=str.casefold):
        pat = md.load_patterns.get(pname)
        pat_type = pat.pattern_type if pat else "?"
        a = applied.get(pname, dict.fromkeys(("fx", "fy", "fz", "mx", "my", "mz"), 0.0))
        rr = df_rxn[df_rxn["Load Pattern"] == pname]
        r = rr.iloc[0] if len(rr) else {}
        ax = _clean(a["fx"])
        ay = _clean(a["fy"])
        az = _clean(a["fz"])
        rx = _clean(r.get(f"Reaction Fx ({fu})", 0.0))
        ry = _clean(r.get(f"Reaction Fy ({fu})", 0.0))
        rz = _clean(r.get(f"Reaction Fz ({fu})", 0.0))
        rows.append(
            {
                "Load Pattern": pname,
                "Type": pat_type,
                f"Applied Fx ({fu})": round(ax, 1),
                f"Applied Fy ({fu})": round(ay, 1),
                f"Applied Fz ({fu})": round(az, 1),
                f"Reaction Fx ({fu})": round(rx, 1),
                f"Reaction Fy ({fu})": round(ry, 1),
                f"Reaction Fz ({fu})": round(rz, 1),
                f"\u0394Fx ({fu})": round(ax + rx, 1),
                f"\u0394Fy ({fu})": round(ay + ry, 1),
                f"\u0394Fz ({fu})": round(az + rz, 1),
            }
        )

    return pd.DataFrame(rows)


def _rs_analysis_label(key) -> str:
    """Normalise an RS-result key to a ``RS-<DIR>`` analysis label.

    Accepts a bare direction letter (``"X"`` / ``"x"``), a prefixed form
    (``"rs_x"`` / ``"RS_X"`` / ``"RS-X"``), or an already-labelled key, and
    returns the canonical ``RS-X`` form so callers can key ``rs_results`` any
    convenient way without the summary picking up inconsistent labels.
    """
    k = str(key).strip().upper()
    if k.startswith(("RS-", "RS_")):
        k = k[3:]
    return f"RS-{k}"


def analysis_load_reaction_summary(
    md,
    mesh_model,
    *,
    config: Optional[dict] = None,
    rs_results: Optional[dict] = None,
    total_weight: Optional[float] = None,
    seismic_coefficient: Optional[float] = None,
) -> list[dict]:
    """Compare applied loads against base reactions for every analysis.

    A **pandas-free** companion to :func:`static_load_verification`: instead of
    returning a ``DataFrame``, it returns a plain ``list[dict]`` — one row per
    analysis — so it can be rendered as a table without the optional ``pandas``
    dependency (e.g. inside Rhino 8's embedded CPython, or by a
    :class:`~fea_toolkit.workflow.steps.Table`-rendering verb).

    The static section reproduces :func:`static_load_verification`'s
    applied-vs-reaction equilibrium check (one row per load pattern), and the
    response-spectrum section adds one row per direction carrying the CQC base
    shear and overturning moment.

    Every row carries the same unit-labelled keys (``None`` where a column does
    not apply to that analysis type):

    - ``Analysis`` — the load-pattern name (static) or ``RS-X`` / ``RS-Y``.
    - ``Type`` — ``"Static"`` or ``"Response Spectrum"``.
    - ``Applied Fx/Fy/Fz (<force unit>)`` — per-pattern applied totals
      (self-weight + gravity + joint + distributed); ``None`` for RS.
    - ``Reaction Fx/Fy/Fz (<force unit>)`` — summed base reactions.
    - ``Δ Fx/Fy/Fz (<force unit>)`` — signed applied + reaction, ~0 when
      equilibrated; ``None`` for RS (no applied load in the static sense).
    - ``Reaction Mx/My/Mz (<force·length unit>)`` — summed base moments with
      overturning (see
      :func:`~fea_toolkit.utils.sum_reactions_with_overturning`).
    - ``Base shear V (<force unit>)`` / ``Base moment M (<force·length unit>)``
      — the CQC-combined RS headline quantities; ``None`` for static rows.
    - ``Expected V (<force unit>)`` / ``V ratio`` — the RS sanity check
      ``Expected V = total_weight × seismic_coefficient`` and the ratio
      ``|V| / Expected V``; present only when *total_weight* and
      *seismic_coefficient* are both supplied (``None`` otherwise), and
      ``None`` for static rows.

    Args:
        md: Parsed :class:`~fea_toolkit.model.sap_data.SAPModelData` — supplies
            the load patterns and the unit labels (never a hardcoded force
            unit).
        mesh_model: Preprocessed :class:`~fea_toolkit.model.mesh_model.MeshModel`.
        config: Optional ``AnalysisBuilder`` config.  ``create_shells`` is
            derived from the mesh when not set (see :func:`_mesh_has_shells`),
            so a shell-stiffened model is built with its areas.
        rs_results: Optional ``{direction: rs_dict}`` mapping — each value is a
            per-direction result from
            :meth:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder.run_response_spectrum_analysis`,
            read via ``base_reactions_cqc``, ``base_shear_cqc`` and
            ``base_moment_cqc``.  ``None`` (or ``{}``) yields a static-only
            summary.
        total_weight: Optional total seismic weight (model force units), the
            product of the model's total seismic mass and
            :func:`~fea_toolkit.utils.g_from_units`.  Powers the RS
            ``Expected V`` sanity column.
        seismic_coefficient: Optional dimensionless base-shear coefficient
            ``α = Sa(T1) / g`` for the fundamental period, in the model's own
            units.  Multiplied by *total_weight* to give ``Expected V``.

    Returns:
        A ``list[dict]`` of rows — static patterns first (case-fold sorted),
        then RS directions in the order of *rs_results* — with the column
        layout above.
    """
    from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

    from ..utils import sum_reactions_with_overturning

    # Builder config: derive ``create_shells`` from the mesh so a shell model
    # is built with its areas rather than as a mechanism (mirrors run_static_cases).
    ab_config = {"element_type": "elasticBeamColumn", "verbose": False}
    if _mesh_has_shells(mesh_model):
        ab_config["create_shells"] = True
    if config:
        ab_config.update(config)

    fu = md.units.get("F", "?")
    lu = md.units.get("L", "?")
    mu = f"{fu}·{lu}"

    def _clean(v):
        """Coerce a cell value to a finite float, else 0.0."""
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return 0.0
        return fv if math.isfinite(fv) else 0.0

    rows: list[dict] = []

    # ── Static section: applied vs reaction per load pattern ──────
    ab = AnalysisBuilder(mesh_model, ab_config)
    ab.build_domain()
    # Apply ALL patterns once so the per-pattern applied totals
    # (_sw/_gravity/_joint/_dist) are populated for every pattern.
    ab.create_loads()

    applied: dict[str, dict[str, float]] = {}
    for _src in (
        getattr(ab, "_sw_load_totals", {}),
        getattr(ab, "_gravity_load_totals", {}),
        getattr(ab, "_joint_load_totals", {}),
        getattr(ab, "_dist_load_totals", {}),
    ):
        for pname, t in _src.items():
            d = applied.setdefault(pname, dict.fromkeys(("fx", "fy", "fz", "mx", "my", "mz"), 0.0))
            for _k in d:
                d[_k] += _clean(t.get(_k, 0.0))

    for pname in sorted(applied, key=str.casefold):
        a = applied[pname]
        result = ab.run_static_analysis(pattern_scales={pname: 1.0}, extract_reactions=True)
        summed = sum_reactions_with_overturning(result.get("reactions", {}), mesh_model.nodes)
        rows.append(
            {
                "Analysis": pname,
                "Type": "Static",
                f"Applied Fx ({fu})": round(_clean(a["fx"]), 1),
                f"Applied Fy ({fu})": round(_clean(a["fy"]), 1),
                f"Applied Fz ({fu})": round(_clean(a["fz"]), 1),
                f"Reaction Fx ({fu})": round(_clean(summed.get("fx", 0.0)), 1),
                f"Reaction Fy ({fu})": round(_clean(summed.get("fy", 0.0)), 1),
                f"Reaction Fz ({fu})": round(_clean(summed.get("fz", 0.0)), 1),
                f"Δ Fx ({fu})": round(_clean(a["fx"]) + _clean(summed.get("fx", 0.0)), 1),
                f"Δ Fy ({fu})": round(_clean(a["fy"]) + _clean(summed.get("fy", 0.0)), 1),
                f"Δ Fz ({fu})": round(_clean(a["fz"]) + _clean(summed.get("fz", 0.0)), 1),
                f"Reaction Mx ({mu})": round(_clean(summed.get("mx", 0.0)), 1),
                f"Reaction My ({mu})": round(_clean(summed.get("my", 0.0)), 1),
                f"Reaction Mz ({mu})": round(_clean(summed.get("mz", 0.0)), 1),
                f"Base shear V ({fu})": None,
                f"Base moment M ({mu})": None,
                f"Expected V ({fu})": None,
                "V ratio": None,
            }
        )

    # ── Response-spectrum section: base shear + overturning ──────
    for key, rs in (rs_results or {}).items():
        r = rs.get("base_reactions_cqc") or {}
        v_cqc = _clean(rs.get("base_shear_cqc", 0.0))
        m_cqc = _clean(rs.get("base_moment_cqc", 0.0))
        expected_v = None
        ratio = None
        if total_weight is not None and seismic_coefficient is not None:
            expected_v = _clean(total_weight) * _clean(seismic_coefficient)
            if math.isfinite(expected_v) and expected_v != 0:
                ratio = round(abs(v_cqc) / expected_v, 3)
        rows.append(
            {
                "Analysis": _rs_analysis_label(key),
                "Type": "Response Spectrum",
                f"Applied Fx ({fu})": None,
                f"Applied Fy ({fu})": None,
                f"Applied Fz ({fu})": None,
                f"Reaction Fx ({fu})": round(_clean(r.get("fx", 0.0)), 1),
                f"Reaction Fy ({fu})": round(_clean(r.get("fy", 0.0)), 1),
                f"Reaction Fz ({fu})": round(_clean(r.get("fz", 0.0)), 1),
                f"Δ Fx ({fu})": None,
                f"Δ Fy ({fu})": None,
                f"Δ Fz ({fu})": None,
                f"Reaction Mx ({mu})": round(_clean(r.get("mx", 0.0)), 1),
                f"Reaction My ({mu})": round(_clean(r.get("my", 0.0)), 1),
                f"Reaction Mz ({mu})": round(_clean(r.get("mz", 0.0)), 1),
                f"Base shear V ({fu})": round(v_cqc, 1),
                f"Base moment M ({mu})": round(m_cqc, 1),
                f"Expected V ({fu})": round(expected_v, 1) if expected_v is not None else None,
                "V ratio": ratio,
            }
        )

    return rows


def _mesh_has_shells(mesh_model) -> bool:
    """Whether a preprocessed model carries areas the builder must build as shells.

    The Preprocessor can leave areas in one of two states: **shells** (meshed,
    carrying their own pressure) or **loads-only** (demoted to frame edge loads).
    The reliable signal is ``MeshModel.area_element_types``: the Preprocessor
    fills it *only* when it created shells (``area_element_types`` is 0 entries
    for a split-only run, one entry per shell for a meshed one).

    ``loads_only_area_ids`` looks like the obvious discriminator but is **not
    usable**: the Preprocessor only populates it on the ``load_shell_selection``
    path, so a plain ``create_shells=False`` run leaves it *empty* while every
    area is in fact loads-only.  Reading it made a split-only mesh look meshed,
    which then forced ``create_shells`` on the builder — building shell elements
    the user had deliberately not asked for.

    Args:
        mesh_model: A ``MeshModel`` (or anything with the attribute).

    Returns:
        ``True`` when the mesh was built with shells.
    """
    return bool(getattr(mesh_model, "area_element_types", None))


#: Build-once thresholds for :func:`run_static_cases`: one OpenSees domain is
#: reused across the case set only when there are enough cases and/or enough
#: nodes for the per-case domain rebuild to matter.  Below both, the per-case
#: rebuild's simplicity and its failure isolation are preferred.
_BUILD_ONCE_MIN_CASES = 5
_BUILD_ONCE_MIN_NODES = 1000


def _should_build_once(ab_config: dict, n_cases: int, n_nodes: int) -> bool:
    """Whether ``run_static_cases`` may reuse one domain across linear cases.

    The build-once path assumes every case starts from a zero state, which only
    holds for a linear-elastic build; it is gated to ``elasticBeamColumn``
    without fiber sections.
    """
    if ab_config.get("element_type", "elasticBeamColumn") != "elasticBeamColumn":
        return False
    if ab_config.get("create_fiber_sections", False):
        return False
    return n_cases >= _BUILD_ONCE_MIN_CASES or n_nodes >= _BUILD_ONCE_MIN_NODES


def run_static_cases(
    mesh_model,
    cases: dict[str, dict[str, float]],
    *,
    config: Optional[dict] = None,
    raw_out: Optional[dict] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    on_progress: Optional[Callable[[int, str, int], None]] = None,
    attempted: Optional[list[str]] = None,
    log: Optional[PipelineLog] = None,
) -> list[dict]:
    """Run each ``{case: {pattern: factor}}`` entry as one static solve.

    This is the static half of :func:`run_linear_cases`, lifted out so a caller
    can run *only* static cases — no response-spectrum or modal pass — and can
    watch and stop it: *should_cancel* is polled **between** cases (a single
    ``run_static_analysis`` call is atomic and cannot be interrupted), and
    *on_progress* is told which case is starting.

    Each case is one solve of its factored patterns
    (``AnalysisBuilder.run_static_analysis(pattern_scales=...)``), so the factors
    are **load** multipliers, not a post-hoc scaling of results — scaling results
    is the load-combination layer's job
    (``docs/load_cases_and_combinations.md``).

    Args:
        mesh_model: Preprocessed :class:`~fea_toolkit.model.mesh_model.MeshModel`.
        cases: ``{case_name: {pattern: factor}}`` — the cases to solve, in run
            order.  :func:`~fea_toolkit.analysis.case_listing.list_static_cases`
            produces this for the model's own cases.
        config: Optional ``AnalysisBuilder`` config (solver settings).  When it
            does not set ``create_shells``, that is derived from the mesh — see
            :func:`_mesh_has_shells` — so a shell-stiffened model is built with
            its areas rather than as a mechanism.
        raw_out: Optional dict filled with each case's raw results —
            ``raw_out[case] = {"nodal_displacements": ..., "element_forces": {...},
            "shell_forces": {...}}`` (``shell_forces`` only when the model has
            shell areas) — the shape
            :func:`~fea_toolkit.io.npz_writer.results_arrays` accepts
            as ``static_results``.  A case that **fails** contributes no entry,
            so ``set(attempted) - set(raw_out)`` is the set that did not
            converge (see *attempted*).
        should_cancel: Optional zero-argument callable polled between cases; when
            it returns ``True`` the run stops and the rows collected so far are
            returned.
        on_progress: Optional ``(index, case_name, total)`` callback, called
            **before** each case is solved (``index`` is 1-based) — the hook a
            GUI worker turns into a progress signal.
        attempted: Optional list filled with the name of each case the loop
            reached, in run order.  A case skipped by cancellation never
            appears, so a caller can tell a case that **failed** from one that
            was never run.
        log: Optional :class:`~fea_toolkit.io.log.PipelineLog` (or a ``bool``)
            that receives the domain-reuse announcement and any per-case
            failure warning.  ``None``/``True`` print to stdout and retain
            nothing; ``False`` is silent.

    Returns:
        One summary row per solved case, in :func:`dict` form — the rows
        :func:`run_linear_cases` appends to its table, in the same column order.
    """
    from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
    from fea_toolkit.utils import sum_reactions_with_overturning

    from ..io.log import coerce_log

    log = coerce_log(log)

    ab_config = {"element_type": "elasticBeamColumn", "verbose": False}
    # Build what the mesh holds: a preprocessed model with shell areas needs
    # ``create_shells`` set, or those areas are never created and a
    # wall/slab-stiffened model is a mechanism.  A caller's explicit setting wins.
    if _mesh_has_shells(mesh_model):
        ab_config["create_shells"] = True
    if config:
        ab_config.update(config)

    rows: list[dict] = []
    total = len(cases)

    # Reuse one OpenSees domain across the case set when the gate says the
    # per-case rebuild matters.  Only valid for the linear path (see
    # ``_should_build_once``).
    build_once = _should_build_once(ab_config, len(cases), len(mesh_model.nodes))
    # Announce the domain-reuse decision so a monitored run shows whether the
    # domain is rebuilt per case or built once and reused across the case set.
    if build_once:
        log.info(
            "static",
            f"Build-once: reusing one OpenSees domain for {total} static "
            f"case(s) ({len(mesh_model.nodes)} node(s)) — per-case rebuild skipped",
        )
    else:
        log.info(
            "static",
            f"Rebuilding the OpenSees domain per case "
            f"({total} static case(s), {len(mesh_model.nodes)} node(s))",
        )
    ab = None
    if build_once:
        ab = AnalysisBuilder(mesh_model, ab_config, log=log)
        ab.build_domain()
        # A fresh builder has no saved edge constraints, so this is a no-op;
        # kept for symmetry with the per-case rebuild path in
        # ``_runner_static.py``.
        ab._reapply_edge_constraints()

    for index, (case_name, patterns) in enumerate(cases.items(), start=1):
        if should_cancel is not None and should_cancel():
            break
        if attempted is not None:
            attempted.append(case_name)
        if on_progress is not None:
            on_progress(index, case_name, total)

        try:
            if build_once:
                ab.remove_load_patterns()
                results = ab.run_static_analysis(
                    pattern_scales=patterns, extract_reactions=True, rebuild=False
                )
            else:
                ab = AnalysisBuilder(mesh_model, ab_config, log=log)
                results = ab.run_static_analysis(pattern_scales=patterns, extract_reactions=True)
            # Use centralized overturning-moment computation (v1 match)
            rxn = results.get("reactions", {})
            summed = sum_reactions_with_overturning(rxn, mesh_model.nodes)
            fx = summed["fx"]
            fy = summed["fy"]
            fz = summed["fz"]
            mx = summed["mx"]
            my = summed["my"]
            mz = summed["mz"]
            disp = results.get("nodal_displacements", {})
            max_roof_disp = 0.0
            if disp:
                roof_id = max(mesh_model.nodes.values(), key=lambda n: n.z).node_id
                if roof_id in disp:
                    d = disp[roof_id]
                    max_roof_disp = math.hypot(d[0], d[1], d[2])

            # ── Raw per-case results (optional export) ─────────────
            # Filled in the same order as the summary row so the caller
            # can build unified/stage files with static frame forces and
            # nodal displacements.
            if raw_out is not None:
                entry = {"nodal_displacements": results.get("nodal_displacements", {})}
                try:
                    # Component-keyed arrays ordered to match the exported
                    # geometry (see AnalysisBuilder.static_element_force_arrays).
                    elem_forces = ab.static_element_force_arrays()
                    if elem_forces:
                        entry["element_forces"] = elem_forces
                except Exception:
                    pass
                try:
                    # Per-shell stress/force resultants (membrane + bending),
                    # keyed by area SAP id — the shell counterpart of the frame
                    # end forces above.  Empty for a frame-only model.
                    shell_forces = ab.extract_static_shell_forces()
                    if shell_forces:
                        entry["shell_forces"] = shell_forces
                except Exception:
                    pass
                raw_out[case_name] = entry
        except Exception as e:
            fx = fy = fz = mx = my = mz = max_roof_disp = 0.0
            log.warning("static", f"{case_name} failed — {e}")
        finally:
            # A failed case must not leave its loads behind to poison the next.
            # A cleanup failure must not abort the remaining cases.
            if build_once:
                try:
                    ab.remove_load_patterns()
                except Exception as exc:
                    log.warning("static", f"cleanup after {case_name} failed — {exc}")

        rows.append(
            {
                "Case": case_name,
                "Type": "Static",
                "Fx": fx,
                "Fy": fy,
                "Fz": fz,
                "Mx": mx,
                "My": my,
                "Mz": mz,
                "Roof disp": max_roof_disp if "Wind" in case_name else None,
            }
        )

    return rows


def run_case_set(
    md: SAPModelData,
    mesh_model,
    cases: dict[str, dict[str, float]],
    *,
    combinations: Optional[dict[str, dict[str, float]]] = None,
    config: Optional[dict] = None,
    raw_out: Optional[dict] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    on_progress: Optional[Callable[[int, str, int], None]] = None,
    log: Optional[PipelineLog] = None,
) -> dict:
    """Solve cases, reduce any combinations, and assemble an in-memory archive.

    The whole of the GUI's ``Analysis ▸ Run`` in one call, kept Qt-free so it is
    tested without the ``[gui]`` extra: it solves each case with
    :func:`run_static_cases`, reduces the requested combinations with
    :func:`~fea_toolkit.analysis.combinations.build_combination_results`, and
    assembles the archive with
    :func:`~fea_toolkit.io.npz_writer.results_arrays`.  **Nothing is written to
    disk** — the returned ``"arrays"`` goes straight to
    :class:`~fea_toolkit.io.results_repository.NpzResultsRepository`, which is
    what lets a result be viewed without an archive (P27).

    A combination whose leaf cases did not all solve is **not** reduced: an
    envelope over a missing case would be quietly wrong, so it is reported in
    ``"unreduced"`` instead.

    Args:
        md: Parsed :class:`~fea_toolkit.model.sap_data.SAPModelData` — supplies
            the combination definitions and the archive's unit labels.
        mesh_model: The preprocessed model the cases are built from.
        cases: ``{case_name: {pattern: factor}}`` to solve, in run order.
        combinations: ``{combination_name: {leaf_case: factor}}`` to reduce from
            the case results.  The factors here are only used to decide whether
            a combination is reducible; the reduction reads its factors from the
            model's own combination definitions.
        raw_out: Optional dict filled with each case's raw results (see
            :func:`run_static_cases`).
        config: Optional ``AnalysisBuilder`` config.  ``create_shells`` is
            derived from the mesh when it is not given (see
            :func:`run_static_cases`).
        should_cancel: Polled between cases (see :func:`run_static_cases`).
        on_progress: ``(index, case_name, total)`` before each case (see
            :func:`run_static_cases`).
        log: Optional :class:`~fea_toolkit.io.log.PipelineLog` (or a ``bool``)
            forwarded to :func:`run_static_cases`.

    Returns:
        ``{"arrays": {name: ndarray}, "cases": [name, ...], "failed": [...],
        "unreduced": [...], "cancelled": bool}`` — ``"failed"`` are the
        *attempted* cases that did not solve, ``"unreduced"`` the combinations
        that could not be formed from what did, and a case skipped by
        cancellation is reported only through ``"cancelled"``.
    """
    from ..io.npz_writer import results_arrays
    from .combinations import build_combination_results

    raw: dict = {} if raw_out is None else raw_out
    attempted: list[str] = []
    run_static_cases(
        mesh_model,
        cases,
        config=config,
        raw_out=raw,
        should_cancel=should_cancel,
        on_progress=on_progress,
        attempted=attempted,
        log=log,
    )

    static_results: dict = dict(raw)
    requested = dict(combinations or {})
    unreduced = [name for name, leaves in requested.items() if not set(leaves) <= set(raw)]
    reducible = [name for name in requested if name not in unreduced]

    case_meta = None
    if reducible:
        combined, case_meta = build_combination_results(
            raw, model=md, combinations=reducible, return_meta=True
        )
        static_results.update(combined)

    arrays = results_arrays(
        md, static_results=static_results, mesh_model=mesh_model, case_meta=case_meta
    )
    # A cancelled case was never *attempted*, so it is neither a result nor a
    # failure — deriving cancellation from "not every case was attempted" keeps
    # the two apart (a case that ran and threw is still reported as failed).
    cancelled = len(attempted) < len(cases)
    return {
        "arrays": arrays,
        "cases": list(static_results),
        "failed": sorted(set(attempted) - set(raw)),
        "unreduced": unreduced,
        "cancelled": cancelled,
    }


def run_linear_cases(
    md: SAPModelData,
    mesh_model,
    spec_cfg: Optional[dict] = None,
    linear_cfg: Optional[dict] = None,
    raw_out: Optional[dict] = None,
    log: Optional[PipelineLog] = None,
) -> pd.DataFrame:
    """Run linear analysis cases and return a summary table.

    The Preprocessor work is done once (via *mesh_model*).  Each
    analysis case creates a lightweight ``AnalysisBuilder``.

    Auto-detects static load cases from the SAP2000 model, or uses
    the ``linear_cfg["cases"]`` list.

    Args:
        md: Parsed :class:`~fea_toolkit.model.sap_data.SAPModelData`.
        mesh_model: Pre-processed :class:`~fea_toolkit.model.mesh_model.MeshModel`.
        spec_cfg: Spectrum config passed through to the RS pass.
        linear_cfg: Linear config (``cases``, ``n_modes``).
        raw_out: Optional dict filled with the raw per-case results as
            ``raw_out[case_name] = {"nodal_displacements": ...,
            "element_forces": {...}}`` — the shape accepted by
            :func:`fea_toolkit.io.stage_writer.write_model_stages` /
            :func:`fea_toolkit.io.unified_writer.write_results` (static
            results).  Element forces are extracted in the element
            **local** coordinate system and stored under the plain
            ``fx_i`` … ``mz_j`` keys (aligned with the geometry arrays).
        log: Optional :class:`~fea_toolkit.io.log.PipelineLog` (or a ``bool``)
            forwarded to :func:`run_static_cases` for the static pass.

    Returns:
        Summary table (DataFrame) — one row per analysis case.
    """
    rows = []
    config = {"element_type": "elasticBeamColumn", "verbose": False}

    from ..io.log import coerce_log

    log = coerce_log(log)

    from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

    # ── Static cases: auto-detect LinStatic, merge overrides, drop empty ──
    # The listing lives in analysis/case_listing.py so the GUI's Analysis ▸ Run
    # dialog offers exactly the cases this runner would solve.
    static_cases = list_static_cases(md, mesh_model, config_cases=(linear_cfg or {}).get("cases"))
    rows.extend(run_static_cases(mesh_model, static_cases, config=config, raw_out=raw_out, log=log))

    # ── Response spectrum cases ─────────────────────────────────
    n_modes = 12
    if linear_cfg and "n_modes" in linear_cfg:
        n_modes = int(linear_cfg["n_modes"])
    elif spec_cfg and "n_modes" in spec_cfg:
        n_modes = int(spec_cfg["n_modes"])

    if spec_cfg:
        from fea_toolkit.spectrum import _build_spectrum

        from ..utils import g_from_units

        # g from the model's length unit so the spectral accelerations are
        # in the model's own unit system (matching the fallback branch).
        T_spec_built, Sa_spec_built, _, _, zeta_eff, _ = _build_spectrum(
            spec_cfg, g=g_from_units(md.units)
        )
        T_spec = T_spec_built
        Sa_spec = Sa_spec_built
    else:
        # Fallback: rare spectrum with 5% damping
        from fea_toolkit.spectrum import _gb50011_spectrum

        zeta_eff = 0.05
        gamma = 0.9 + (0.05 - zeta_eff) / (0.3 + 6.0 * zeta_eff)
        eta_1 = max(0.0, 0.02 + (0.05 - zeta_eff) / (4.0 + 32.0 * zeta_eff))
        eta_2 = max(0.55, 1.0 + (0.05 - zeta_eff) / (0.08 + 1.6 * zeta_eff))
        from ..utils import g_from_units

        gravity = g_from_units(md.units)
        tg = 0.25
        alpha_max = 0.50
        T_spec = np.linspace(0.0, 6.0, 300).tolist()
        Sa_spec = _gb50011_spectrum(
            T_spec, alpha_max, tg, gamma=gamma, eta1=eta_1, eta2=eta_2, g=gravity
        ).tolist()

    for rs_dir in ["X", "Y"]:
        ab = AnalysisBuilder(mesh_model, config)
        ab.build_domain()
        ab.compute_seismic_masses()
        try:
            modal = ab.run_modal_analysis(num_modes=n_modes, print_results=False)

            # Degenerate / single-element models return fewer converged modes
            # than requested (run_modal_analysis keeps only positive
            # eigenvalues).  Use the actual count so the RS pass and the
            # nodal-displacement superposition stay index-aligned.
            n_actual = len(modal["periods"])
            if n_actual == 0:
                raise ValueError("modal analysis returned no converged modes")

            def spectrum_func(T):
                return float(np.interp(T, T_spec, Sa_spec))

            rs = ab.run_response_spectrum_analysis(
                num_modes=n_actual,
                modal_periods=modal["periods"],
                spectrum_periods=T_spec,
                spectrum_accels=Sa_spec,
                direction=rs_dir,
                damping_ratio=zeta_eff,
                print_results=False,
            )
            v_srss = rs.get("base_shear_srss", 0.0)
            m_srss = rs.get("base_moment_srss", 0.0)
            # Full 6-DoF reactions with overturning (from new centralized
            # per-mode lever-arm computation in run_response_spectrum_analysis)
            r_cqc = rs.get("base_reactions_cqc", {})

            rs_disp_cqc, rs_disp_srss = ab.compute_rs_nodal_displacements(
                num_modes=n_actual,
                modal_periods=modal["periods"],
                eigenvalues=modal["eigenvalues"],
                spectrum_func=spectrum_func,
                direction=rs_dir,
                damping_ratio=zeta_eff,
                return_srss=True,
            )
            roof_tag = max(md.nodes.values(), key=lambda n: n.z).node_tag
            if roof_tag in rs_disp_cqc:
                d = rs_disp_cqc[roof_tag]
                roof_disp_rs = math.hypot(d[0], d[1], d[2])
                d_s = rs_disp_srss[roof_tag]
                roof_disp_srss = math.hypot(d_s[0], d_s[1], d_s[2])
            else:
                roof_disp_rs = roof_disp_srss = 0.0
        except Exception as e:
            v_srss = m_srss = roof_disp_rs = roof_disp_srss = 0.0
            r_cqc = {}
            log.warning("linear", f"RS-{rs_dir} failed — {e}")

        rows.append(
            {
                "Case": f"RS-{rs_dir}",
                "Type": "Response Spectrum",
                "Fx": r_cqc.get("fx", 0.0),
                "Fy": r_cqc.get("fy", 0.0),
                "Fz": r_cqc.get("fz", 0.0),
                "Mx": r_cqc.get("mx", 0.0),
                "My": r_cqc.get("my", 0.0),
                "Mz": r_cqc.get("mz", 0.0),
                "Roof disp": roof_disp_rs,
                "Roof disp SRSS": roof_disp_srss,
                "V_srss": v_srss,
                "M_srss": m_srss,
            }
        )

    return pd.DataFrame(rows)
