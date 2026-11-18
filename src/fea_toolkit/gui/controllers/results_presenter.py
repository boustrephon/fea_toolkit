"""Fan-out of a recipe run's results into table / figure / results views.

The window's ``_recipe_finished`` dispatches each recipe result to a
``show_<kind>_result`` method here.  Each method distils the verb's payload (a
:class:`~fea_toolkit.workflow.steps.Table`, a Matplotlib figure, or a results
archive) and registers it through the session's
:class:`~fea_toolkit.gui.controllers.view_registry.ViewRegistry`, then asks the
window to display it.  The presenter is Qt-free: it writes to the session and
reports side effects (log lines, view switches, animation state) through the
callbacks the window injects, so it is unit-testable without a running GUI.
"""

from typing import Any, Callable, Optional

from .view_registry import _RESULTS_KEY_PREFIX, View, _case_view_name, _table_key

__all__ = ["ResultsPresenter"]


class ResultsPresenter:
    """Distil recipe results into registered views and drive their display.

    Args:
        session: The window's :class:`~fea_toolkit.gui.session.Session`; the
            presenter registers views and reads the preprocessed mesh from it.
        log: The window's ``log(message, level="info")`` callback.
        show_view: Display a registered view (``rebuild_tree=True`` is baked in
            by the window's wrapper).
        show_model: The window's ``show_model`` method, for the geometry result.
        nld_step_frames: The window's transient-frame extractor, keyed to the
            store mesh.
        frames: The window's ``_nld_frames`` dict (transient history by key).
        times: The window's ``_nld_times`` dict (time labels by key).
        on_animatability_changed: The window's view-action refresher, run after
            the nonlinear-dynamic history is stored or cleared.
    """

    def __init__(
        self,
        session: Any,
        log: Callable[[str, str], None],
        show_view: Callable[[View], None],
        show_model: Callable[..., None],
        nld_step_frames: Callable[[Any], tuple],
        frames: dict,
        times: dict,
        on_animatability_changed: Callable[[], None],
    ) -> None:
        self._session = session
        self._log = log
        self._show_view = show_view
        self._show_model = show_model
        self._nld_step_frames = nld_step_frames
        self._frames = frames
        self._times = times
        self._on_animatability_changed = on_animatability_changed

    def set_session(self, session: Any) -> None:
        """Re-point the presenter at *session* (e.g. after a project load).

        The window replaces its whole ``Session`` object when it opens a
        project, so the presenter must be re-bound before a recipe run registers
        results — otherwise they would land in the discarded session.
        """
        self._session = session

    # ── Tables and figures ────────────────────────────────────────

    def show_table_result(self, label: str, table: Any) -> None:
        """Register a check's ``table`` result as a view and show it.

        The view key is derived from the step's label, so re-running the same
        check replaces its view rather than piling one up per run.
        """
        if table is None:
            self._log(f"{label}: nothing to show.", "warn")
            return
        key = _table_key(label)
        name = getattr(table, "title", None) or label
        view = self._session.views.add_table(key, name, table, source=f"Recipe: {label}")
        self._show_view(view)
        self._log(f"Added table view: {name}.")

    def show_figure_result(self, label: str, figure: Any) -> None:
        """Register a chart's ``figure`` result as a view and show it."""
        if figure is None:
            self._log(f"{label}: the chart produced no figure.", "warn")
            return
        key = f"figure:{label.lower().replace(' ', '-')}"
        view = self._session.views.add_figure(key, label, figure, source=f"Recipe: {label}")
        self._show_view(view)
        self._log(f"Added figure view: {label}.")

    # ── Analysis summaries ────────────────────────────────────────

    def show_nonlinear_dynamic_result(self, label: str, payload: Any) -> None:
        """Summarise a nonlinear-dynamic (time-history) result as a table.

        The ``nonlinear_dynamic`` verb's payload is an
        :class:`~fea_toolkit.analysis.base.AnalysisResult` whose ``data`` holds
        the full transient (``times`` and the per-node displacement / velocity /
        acceleration arrays) plus the peak reductions.  The peaks and the run's
        convergence are distilled into a one-row table; the per-step
        displacement history is stored for ``_on_animate_toggled``.
        """
        from ...workflow.steps import Table

        data = getattr(payload, "data", None) or {}
        if not isinstance(data, dict):
            self._log(f"{label}: the nonlinear-dynamic result has no data.", "warn")
            return

        def _num(key: str) -> str:
            value = data.get(key)
            try:
                return f"{float(value):.4g}"
            except (TypeError, ValueError):
                return "\u2014"

        def _int(key: str) -> str:
            value = data.get(key)
            try:
                return str(int(value))
            except (TypeError, ValueError):
                return "\u2014"

        rows = (
            ("Peak displacement", _num("peak_displacement")),
            ("Peak base shear", _num("peak_base_shear")),
            ("Peak plastic rotation", _num("peak_plastic_rotation")),
            ("Yielded hinges", _int("num_yielded_hinges")),
            ("Dissipated energy", _num("max_dissipated_energy")),
            ("Converged steps", _int("converged_steps")),
            ("T1 / T2 (s)", f"{_num('period_1')} / {_num('period_2')}"),
            ("Direction", str(data.get("direction", ""))),
            ("Record", str(data.get("gm_file", ""))),
        )
        self.show_table_result(
            label,
            Table(title=label, columns=("Quantity", "Value"), rows=rows),
        )
        frames, times = self._nld_step_frames(payload)
        if frames:
            self._frames[_table_key(label)] = frames
            self._times[_table_key(label)] = times
            self._log(f"{label}: transient history stored for animation ({len(frames)} steps).")
            # The table view is now animatable, so refresh the Results menu
            # (it was greyed for a view with no displacement field).
            self._on_animatability_changed()
        else:
            key = _table_key(label)
            self._frames.pop(key, None)
            self._times.pop(key, None)
            self._log(f"{label}: no transient displacement history to animate.", "warn")
            # A previous run of this label may have stored frames; refresh the
            # Results menu so the now-unanimatable table is greyed correctly.
            self._on_animatability_changed()

    def show_ida_result(self, label: str, payload: Any) -> None:
        """Summarise an IDA run as a collapse table and an IDA-curve figure.

        The ``ida`` verb's payload is the
        :func:`~fea_toolkit.analysis.ida.run_ida` result dict — the intensity
        ladder, per-rung drift ratios and convergence flags, and the collapse
        intensity / criterion / bisection bracket.  The curve is plotted by
        :func:`~fea_toolkit.plotting.viz_ida.plot_ida_curve` and the collapse
        quantities distilled into a one-row table.
        """
        from ...workflow.steps import Table

        data = payload if isinstance(payload, dict) else {}
        if not data:
            self._log(f"{label}: the IDA run produced no result.", "warn")
            return

        def _num(value: Any) -> str:
            try:
                return f"{float(value):.4g}"
            except (TypeError, ValueError):
                return "\u2014"

        def _count(value: Any) -> str:
            if value is None:
                return "\u2014"
            try:
                return str(len(value))
            except TypeError:
                return "\u2014"

        rows = (
            ("Collapse intensity", _num(data.get("collapse_intensity"))),
            ("Collapse criterion", str(data.get("collapse_criterion") or "\u2014")),
            ("Rungs", _count(data.get("intensities"))),
            ("Structure height", _num(data.get("structure_height"))),
        )
        self.show_table_result(
            label,
            Table(title=label, columns=("Quantity", "Value"), rows=rows),
        )

        from ...plotting.viz_ida import plot_ida_curve

        figure = plot_ida_curve(data, title=label)
        if figure is not None:
            self.show_figure_result(f"{label} curve", figure)
        else:
            self._log(f"{label}: matplotlib unavailable \u2014 no IDA curve plotted.", "warn")

    def show_modal_result(self, label: str, payload: Any) -> None:
        """Summarise a modal result as a table and one results view per mode.

        The ``modal`` verb's payload is an
        :class:`~fea_toolkit.analysis.base.AnalysisResult` whose ``data`` holds
        ``{"modal": {…}, "shapes": {…}}``.  The periods / participation are
        distilled into a table, and each mode shape becomes a ``results`` view
        (deformed geometry) — one per mode, like a static case.
        """
        from ...workflow.steps import Table

        data = getattr(payload, "data", None) or {}
        modal = data.get("modal", data) if isinstance(data, dict) else {}
        periods = list(modal.get("periods", []) or [])
        if not periods:
            self._log(f"{label}: the modal result has no converged modes.", "warn")
            return
        frequencies = list(modal.get("frequencies", []) or [])
        props = modal.get("modal_props", {}) or {}
        keys = ("partiMassRatiosMX", "partiMassRatiosMY", "partiMassRatiosMZ")
        rows = []
        for i, period in enumerate(periods):
            freq = frequencies[i] if i < len(frequencies) else 0.0
            ratios = []
            for key in keys:
                series = props.get(key) or []
                ratios.append(float(series[i]) if i < len(series) else 0.0)
            rows.append((str(i + 1), f"{period:.4f}", f"{freq:.4f}", *[f"{r:.2f}" for r in ratios]))
        self.show_table_result(
            label,
            Table(
                title=label,
                columns=("Mode", "Period (s)", "Freq (Hz)", "Mx (%)", "My (%)", "Mz (%)"),
                rows=tuple(rows),
            ),
        )

        shapes = data.get("shapes")
        if isinstance(shapes, dict) and shapes:
            self.show_modal_shapes_views(label, shapes, parent=_table_key(label))

    def show_modal_shapes_views(
        self, label: str, shapes: dict, parent: Optional[str] = None
    ) -> None:
        """Register one ``results`` view per mode shape, like a static case.

        Each mode's eigenvector field (``{mode_index: {node_tag: (dx, dy, dz)}}``)
        becomes a ``static/Mode-<i>/node_*`` case via
        :func:`~fea_toolkit.gui.project.mode_shape_arrays`, then registers through
        the same path a static case takes.  The mode views nest under *parent*
        (the step's summary table) so they bundle rather than flood the tree.

        Args:
            label: The step's result label.
            shapes: The per-mode eigenvector fields.
            parent: Key of the view the mode views hang off.
        """
        from ..project import mode_shape_arrays

        mesh = self._session.store.preprocessed() if self._session.store is not None else None
        if mesh is None:
            self._log(f"{label}: no prepared mesh to draw the mode shapes against.", "warn")
            return
        arrays = mode_shape_arrays(mesh, shapes)
        self.show_results_archive(label, arrays, parent=parent)

    def show_rs_result(self, label: str, payload: Any) -> None:
        """Summarise a response-spectrum result as a table and a results view.

        The ``response_spectrum`` verb's payload is an
        :class:`~fea_toolkit.analysis.base.AnalysisResult` whose ``data`` holds
        ``{"rs_result": {…}, "direction": …, "node_displacements": {…}}``.  The
        combined base shear / moment scalars are distilled into a one-row table,
        and the CQC combined displacement field becomes a ``results`` view — one
        per direction, exactly like a static case — so the deformed shape is
        inspectable rather than only logged.
        """
        from ...workflow.steps import Table

        data = getattr(payload, "data", None)
        if not isinstance(data, dict):
            self._log(f"{label}: no response-spectrum data.", "warn")
            return
        rs = data.get("rs_result") or {}
        direction = data.get("direction", "")
        table = Table(
            title=label,
            columns=("Direction", "V CQC", "V SRSS", "V total", "M CQC", "M SRSS"),
            rows=(
                (
                    str(direction),
                    f"{float(rs.get('base_shear_cqc', 0.0)):.2f}",
                    f"{float(rs.get('base_shear_srss', 0.0)):.2f}",
                    f"{float(rs.get('base_shear_total', 0.0)):.2f}",
                    f"{float(rs.get('base_moment_cqc', 0.0)):.2f}",
                    f"{float(rs.get('base_moment_srss', 0.0)):.2f}",
                ),
            ),
        )
        self.show_table_result(label, table)

        node_displacements = data.get("node_displacements")
        if isinstance(node_displacements, dict) and node_displacements:
            self.show_rs_results_view(
                label, str(direction), node_displacements, parent=_table_key(label)
            )

    def show_rs_results_view(
        self,
        label: str,
        direction: str,
        node_displacements: dict,
        parent: Optional[str] = None,
    ) -> None:
        """Register the combined RS displacement field as a ``results`` view.

        The field is keyed by OpenSees node tag, but the existing
        :class:`~fea_toolkit.io.results_repository.NpzResultsRepository` reads
        the static-shaped ``static/<case>/node_*`` layout, so the field is
        marshalled into that shape over the mesh's own geometry (via
        :func:`~fea_toolkit.gui.project.rs_displacement_arrays`) and registered
        through the same path a static case takes.  The view nests under
        *parent* (the step's summary table).

        Args:
            label: The step's result label.
            direction: The spectrum direction this field combines.
            node_displacements: ``{node_tag: (dx, dy, dz)}`` combined field.
            parent: Key of the view the results view hangs off.
        """
        from ..project import rs_displacement_arrays

        mesh = self._session.store.preprocessed() if self._session.store is not None else None
        if mesh is None:
            self._log(
                f"{label}: no prepared mesh to draw the {direction} displaced shape against.",
                "warn",
            )
            return
        arrays = rs_displacement_arrays(mesh, {direction: node_displacements})
        self.show_results_archive(label, arrays, parent=parent)

    def show_pushover_result(self, label: str, payload: Any) -> None:
        """Summarise a pushover run as a table, ADRS figures and deformed shapes.

        The ``pushover`` verb's payload is an
        :class:`~fea_toolkit.analysis.base.AnalysisResult` whose ``data`` maps each
        push direction to its result dict, which carries the ADRS capacity curve
        (``adrs``), the performance point (``pp``), the demand spectrum
        (``spectrum``) and the per-step ``results`` (including ``lateral_load_type``).
        A summary table lists each direction × load type; each also becomes an ADRS
        figure, and (when steps were recorded) a ``results`` view of its final
        converged deformed shape — nested under the table, direction-first.
        """
        from ...workflow.steps import Table

        data = getattr(payload, "data", None) or {}
        directions = sorted(data) if isinstance(data, dict) else []
        if not directions:
            self._log(f"{label}: the run produced no pushover directions.", "warn")
            return
        self._log(f"{label}: complete ({', '.join(directions)}).")

        # ── Summary table: one row per direction × load type ────────
        rows = []
        for direction in directions:
            entry = data.get(direction) or {}
            results = entry.get("results") or {}
            pp = entry.get("pp") or {}
            load_type = (
                results.get("lateral_load_type", "uniform")
                if isinstance(results, dict)
                else "uniform"
            )
            control = results.get("control_disp") or [] if isinstance(results, dict) else []
            shear = results.get("base_shear") or [] if isinstance(results, dict) else []
            max_disp = max((abs(v) for v in control), default=0.0)
            max_shear = max((abs(v) for v in shear), default=0.0)
            rows.append(
                (
                    str(direction),
                    str(load_type),
                    f"{max_disp:.4f}",
                    f"{max_shear:.2f}",
                    f"{float(pp.get('D_roof', 0.0)):.4f}",
                    f"{float(pp.get('V_base', 0.0)):.2f}",
                    f"{float(pp.get('mu', 0.0)):.2f}",
                    str(bool(pp.get("converged", False))),
                )
            )
        self.show_table_result(
            label,
            Table(
                title=label,
                columns=(
                    "Direction",
                    "Load type",
                    "Max roof disp",
                    "Max base shear",
                    "PP D_roof",
                    "PP V_base",
                    "Ductility",
                    "Converged",
                ),
                rows=tuple(rows),
            ),
        )
        table_key = _table_key(label)

        from ...plotting.viz_pushover import plot_capacity_spectrum
        from ..project import figure_identity

        views = []
        for direction in directions:
            entry = data.get(direction) or {}
            adrs = entry.get("adrs")
            spectrum = entry.get("spectrum") or {}
            periods = spectrum.get("T") or []
            accels = spectrum.get("Sa") or []
            if not adrs or not periods or len(periods) != len(accels):
                continue
            figure = plot_capacity_spectrum(
                adrs,
                periods,
                accels,
                performance_point=entry.get("pp"),
                title=f"{label} — {direction}",
            )
            if figure is None:
                continue
            results = entry.get("results") or {}
            load_type = (
                results.get("lateral_load_type", "uniform")
                if isinstance(results, dict)
                else "uniform"
            )
            key, name = figure_identity(label, direction, load_type)
            view = self._session.views.add_figure(
                key,
                name,
                figure,
                source=f"Recipe: {label}",
                parent=table_key,
                activate=False,
            )
            views.append(view)

        # Per-(direction, load type) deformed-shape views (final recorded step),
        # nested under the summary table — only when steps were recorded.  The full
        # per-step history is threaded into the same archive so the "animate" toggle
        # can step through it.
        mesh = self._session.store.preprocessed() if self._session.store is not None else None
        if mesh is not None:
            from ...io.npz_writer import collect_pushover_arrays
            from ..project import pushover_displacement_arrays

            final_fields = {}
            step_by_pair = {}
            results_by_pair = {}
            for direction in directions:
                entry = data.get(direction) or {}
                results = entry.get("results") or {}
                if not isinstance(results, dict):
                    continue
                load_type = results.get("lateral_load_type", "uniform")
                step_results = results.get("step_results") or []
                if step_results and step_results[-1].get("node_displacements"):
                    pair = (direction, load_type)
                    final_fields[pair] = step_results[-1]["node_displacements"]
                    step_by_pair[pair] = step_results
                    results_by_pair[pair] = results
            if final_fields:
                arrays = pushover_displacement_arrays(mesh, final_fields)
                for (direction, load_type), per_step in step_by_pair.items():
                    arrays.update(
                        collect_pushover_arrays(
                            mesh,
                            per_step,
                            direction=direction,
                            load_type=load_type,
                            pushover_results=results_by_pair.get((direction, load_type)),
                        )
                    )
                self.show_results_archive(label, arrays, parent=table_key, show=False)

        if views:
            self._show_view(views[0])
        self._log(f"{label}: added {len(views)} ADRS figure(s).")

    def show_results_result(self, label: str, arrays: Any) -> list:
        """Register each case in a recipe's results archive as a view.

        Both ``cases`` verbs — a solve and a combination reduction — hand back
        an in-memory results archive, so this is the same path the **Analysis ▸
        Run** results take.  See :meth:`show_results_archive`.
        """
        return self.show_results_archive(label, arrays or {})

    @staticmethod
    def _archive_matches_mesh(repository: Any, mesh: Any) -> bool:
        """Whether *repository*'s embedded geometry is the same model as *mesh*.

        A solve / combination / modal / RS / pushover result is keyed to the
        session's own mesh, so its archive's node tags coincide with the mesh's;
        an ``open_results`` step loads a *different* archive whose node tags do
        not.  When they differ the archive's own geometry must be drawn, not the
        session mesh's — without replacing the working model.
        """
        import numpy as np

        archive_tags = repository.display_geometry().get("node_tag")
        if archive_tags is None:
            return True  # no embedded geometry: the session mesh is the only source
        mesh_tags = [node.node_tag for node in mesh.nodes.values()]
        return np.array_equal(np.asarray(archive_tags, dtype=int), np.asarray(mesh_tags, dtype=int))

    def show_results_archive(
        self, label: str, arrays: dict, parent: Optional[str] = None, *, show: bool = True
    ) -> list:
        """Register one ``results`` view per case in *arrays* and show the first.

        A :class:`~fea_toolkit.io.results_repository.NpzResultsRepository` over
        the array dict supplies the cases (``static_case_labels``); the mesh the
        run was prepared from is what the results are keyed to, so it is used
        whenever the archive's node tags match it, and an archive also carries
        its own display geometry — which is what makes the view drawable with
        nothing else loaded, and what a different-model archive (``open_results``)
        is drawn against rather than the session mesh.  Cases replace by key, so
        re-running a recipe refreshes its views instead of piling them up.

        Args:
            label: The step's result label, recorded as the view's provenance.
            arrays: The archive arrays — ``static_case_labels`` plus the
                ``static/<case>/…`` blocks and the geometry arrays.
            parent: Key of the step's summary view the case views nest under
                (``None`` leaves them top-level).
            show: Whether to display the first case after registering (a fan-out
                step that keeps another view active, e.g. pushover's ADRS figure,
                passes ``False``).

        Returns:
            The case labels registered, in archive order — empty when the
            archive holds no case.
        """
        from ...io.results_repository import NpzResultsRepository

        repository = NpzResultsRepository(arrays or {})
        cases = list(repository.cases())
        if not cases:
            return []

        mesh = self._session.store.preprocessed() if self._session.store is not None else None
        model = (
            mesh
            if mesh is not None and self._archive_matches_mesh(repository, mesh)
            else repository.as_model()
        )
        for case in cases:
            self._session.views.add_results(
                f"{_RESULTS_KEY_PREFIX}{case}",
                _case_view_name(case, repository.case_meta(case)),
                repository,
                source=f"Recipe: {label} \u00b7 {case}",
                model=model,
                parent=parent,
                case=case,
                activate=False,
            )
        self._log(f"{label}: {len(cases)} case(s) added as views.")

        if show:
            first = self._session.views.get(f"{_RESULTS_KEY_PREFIX}{cases[0]}")
            if first is not None:
                self._show_view(first)
        return cases

    def open_results(self, label: str, arrays: dict) -> list:
        """Register each case in a *file* archive as a view, keyed to the archive.

        Unlike :meth:`show_results_archive` — which prefers the mesh a recipe
        run was prepared from — a file archive's cases are keyed to the
        archive's own geometry (``repository.as_model()``), and each view's key
        is namespaced by *label* so two archives that share a case name do not
        overwrite one another (re-opening the *same* archive still replaces, by
        key).

        Args:
            label: The archive's provenance label (e.g. its file name), used both
                in each view's source and as the key namespace.
            arrays: The archive arrays — ``static_case_labels`` plus the
                ``static/<case>/…`` blocks and the geometry arrays.

        Returns:
            The case labels registered, in archive order — empty when the
            archive holds no case.
        """
        from ...io.results_repository import NpzResultsRepository

        repository = NpzResultsRepository(arrays or {})
        cases = list(repository.cases())
        if not cases:
            return []
        model = repository.as_model()
        for case in cases:
            self._session.views.add_results(
                f"{_RESULTS_KEY_PREFIX}{label}:{case}",
                _case_view_name(case, repository.case_meta(case)),
                repository,
                source=f"{label} \u00b7 {case}",
                model=model,
                case=case,
                activate=False,
            )
        self._log(f"{label}: {len(cases)} case(s) added as views.")
        first = self._session.views.get(f"{_RESULTS_KEY_PREFIX}{label}:{cases[0]}")
        if first is not None:
            # The archive's geometry replaces the model's, so the model's hidden
            # set no longer applies.
            self._session.hidden_elem_ids.clear()
            self._show_view(first)
        return cases

    def show_geometry_result(self, label: str, mesh_model: Any) -> None:
        """Report a geometry result, register it as a view and display it.

        Splitting is *opt-in per element* in the model itself (SAP2000's
        auto-mesh flags: ``AtJoints`` / ``AtFrames``), so a model that asks for
        nothing is legitimately unchanged — say so rather than implying a
        failure.

        Args:
            label: The result's display name, e.g. ``"Meshed"``.
            mesh_model: The ``MeshModel`` the step produced.
        """
        elements = mesh_model.frame_elements.values()
        children = sum(1 for elem in elements if getattr(elem, "parent_id", None))
        parents = sum(1 for elem in elements if getattr(elem, "inactive", False))
        if children or parents:
            detail = f"{children} split sub-elements, {parents} superseded parents"
        else:
            detail = "no element requested splitting"
        self._log(
            f"Preprocessed: {len(mesh_model.frame_elements)} frame elements ({detail}), "
            f"{len(mesh_model.area_elements)} area elements."
        )
        # Record the preprocessed model on the store, so Analysis ▸ Run uses the
        # topology just displayed rather than preprocessing again (P27,
        # refinement 1: preprocessing is a user action, never silent).
        if self._session.store is not None:
            self._session.store.set_preprocessed(mesh_model)
        # The view *names* are the GUI's own convention: a split-only run is
        # still "Processed" and a meshing run "Meshed", whatever the verb
        # labelled its result.
        if label == "Meshed":
            key, name = "meshed", "Meshed"
        else:
            key, name = "processed", "Processed"
        self._session.views.add_geometry(key, name, mesh_model, source=f"Recipe: {label}")
        if self._session.hidden_elem_ids:
            self._session.hidden_elem_ids.clear()
            self._log("Preprocessing rebuilt the model; hidden elements were restored.")
        self._show_model(mesh_model, reset_view=False)
        self._log(f"Added view: {name}.")
