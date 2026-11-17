"""The GUI session — the document state the main window owns and displays.

The main window was holding its document state (the model store, the registered
views, the recipe, the named selections, the hidden-element set and the last
run) as bare attributes on the window itself.  Consolidating them on a single
:class:`Session` gives that state one owner, lets a whole run be saved and
reopened in one call (the planned ``to_bundle`` / ``from_bundle``) and — because
this module imports no Qt — lets that round-trip be unit-tested without the
``[gui]`` extra.

Like :mod:`fea_toolkit.gui.models.model_index` and
:mod:`fea_toolkit.gui.controllers.view_registry`, the session is Qt-free: it
holds the *data* the window renders, never a widget.
"""

import contextlib
from dataclasses import dataclass, field
from typing import Any, Optional

from ..workflow.recipe import Recipe
from .controllers.view_registry import FIGURE, GEOMETRY, RESULTS, TABLE, ViewRegistry

__all__ = ["Session"]


@dataclass
class Session:
    """The document state the main window owns and displays.

    Attributes:
        store: The :class:`~fea_toolkit.io.model_store.ModelStore` (``None``
            until a model is opened).
        views: The registered views (geometry / results / table / figure).
        recipe: The workflow recipe being authored.
        named_selections: Named selections, keyed by name.
        hidden_elem_ids: Element ids hidden from the viewport.
        last_run: The last completed
            :class:`~fea_toolkit.workflow.recipe.RecipeRun`, retained so
            ``File ▸ Save project`` can serialise its results.
    """

    store: Any = None
    views: ViewRegistry = field(default_factory=ViewRegistry)
    recipe: Recipe = field(default_factory=Recipe)
    named_selections: dict[str, Any] = field(default_factory=dict)
    hidden_elem_ids: set[str] = field(default_factory=set)
    last_run: Any = None

    def reset(self) -> None:
        """Clear the document state, leaving the workflow recipe untouched.

        The store, views, named selections, hidden set and last run all belong
        to the document currently open; :meth:`reset` empties them for a fresh
        document.  The recipe is deliberately left in place: a recipe run that
        switches documents calls ``reset()`` and then applies the run's
        outcomes onto the recipe.  A user-initiated model open clears the
        recipe separately (through the recipe panel).
        """
        self.store = None
        self.views = ViewRegistry()
        self.named_selections.clear()
        self.hidden_elem_ids.clear()
        self.last_run = None

    def to_bundle(
        self, *, name: str, source_label: str, camera: Any = None, display: Optional[dict] = None
    ) -> dict:
        """Collect the session's state into an in-memory project bundle.

        Everything needed to reconstruct the session is gathered here, Qt-free;
        the window supplies the two pieces of state the session does not own —
        the viewport camera and the display preferences — then hands the
        returned bundle straight to
        :func:`~fea_toolkit.gui.project.write_project_zip`.

        Args:
            name: Project display name.
            source_label: Original model path (informational only).
            camera: The viewport camera position, or ``None``.
            display: The display preferences dict, or ``None``.

        Returns:
            A dict with ``manifest`` plus the ``sap`` / ``mesh`` /
            ``results_arrays`` / ``figures`` / ``tables`` payloads.
        """
        from . import project as project_mod
        from .project import collect_project_results

        store = self.store
        collected = collect_project_results(
            self.last_run.results if self.last_run is not None else []
        )
        if not collected["static_arrays"]:
            self._merge_results_view_arrays(collected["static_arrays"])

        figures = project_mod.figures_payload(collected["pushover"])
        if collected.get("ida"):
            figures.update(
                project_mod.ida_figure_payload(collected["ida"], label=self._ida_result_label())
            )
        tables = project_mod.tables_payload(self._project_tables())

        active = self.views.active
        manifest = project_mod.build_manifest(
            name=name,
            source_label=source_label,
            model_file="model.h5",
            units=dict(getattr(store.raw(), "units", None) or {}),
            recipe=self.recipe,
            views=self.views.views(),
            active_view=active.key if active is not None else None,
            camera=camera,
            hidden_elem_ids=sorted(self.hidden_elem_ids),
            display=display or {},
        )
        return {
            "manifest": manifest,
            "sap": store.raw(),
            "mesh": store.preprocessed(),
            "results_arrays": self._project_results_arrays(collected),
            "figures": figures,
            "tables": tables,
        }

    @classmethod
    def from_bundle(cls, bundle: dict) -> "Session":
        """Rebuild a session from a project bundle, Qt-free.

        Reconstructs the store, the registered views, the recipe and the hidden
        set from the bundle :func:`~fea_toolkit.gui.project.read_project`
        returned — nothing is re-run.  The named selections are not part of the
        project bundle, so a reopened project starts with none (as before).

        Args:
            bundle: The ``read_project`` result (manifest + sap + mesh +
                results + tables + figures).

        Returns:
            A new :class:`Session` ready for the window to re-attach its
            viewport, tree and inspector.
        """
        from ..io.model_store import InMemoryModelStore
        from ..workflow import Recipe
        from .project import regenerate_figure

        manifest = bundle["manifest"] or {}
        sap = bundle["sap"]
        mesh = bundle["mesh"]
        repository = bundle["results"]
        tables = bundle["tables"] or {}
        figures = bundle["figures"] or {}

        session = cls()
        project = manifest.get("project") or {}
        source_label = project.get("source_label", "")
        if sap is not None:
            session.store = InMemoryModelStore(sap, source=source_label)
            session.views = ViewRegistry(session.store)
            # A project always carries at least the raw "unprocessed" geometry
            # view; add it up front, and the manifest's views register over it
            # (replacing it by key when the project saved one).
            session.views.add_geometry("unprocessed", "Unprocessed", sap, source=source_label)
            if mesh is not None:
                session.store.backfill_mesh_load_metadata(mesh)
                session.store.set_preprocessed(mesh)

        geometry_sources = {"unprocessed": sap, "processed": mesh, "meshed": mesh}
        # Results views render the archive's own geometry, which carries section
        # *names* but not definitions, and no supports.  Attach the source
        # model's sections and restraints so a results view colours and supports
        # its elements exactly as the geometry views do.
        display_model = (
            repository.as_model(sections=sap.sections, restraints=sap.restraints)
            if repository is not None and sap is not None
            else None
        )
        for entry in manifest.get("views") or []:
            kind = entry.get("kind")
            key = entry.get("key")
            name = entry.get("name")
            source = entry.get("source", "")
            parent = entry.get("parent")
            if kind == GEOMETRY:
                model = geometry_sources.get(key)
                if model is not None:
                    session.views.add_geometry(key, name, model, source=source, activate=False)
            elif kind == RESULTS:
                case = entry.get("case")
                if repository is not None and case in repository.cases():
                    session.views.add_results(
                        f"results:{case}",
                        name,
                        repository,
                        source=source,
                        model=display_model,
                        parent=parent,
                        activate=False,
                    )
            elif kind == TABLE:
                table = tables.get(entry.get("table_key", key))
                if table is not None:
                    session.views.add_table(
                        key, name, table, source=source, parent=parent, activate=False
                    )
            elif kind == FIGURE:
                figure_data = figures.get(entry.get("figure_key", key))
                if figure_data:
                    figure = regenerate_figure(figure_data)
                    if figure is not None:
                        session.views.add_figure(
                            key, name, figure, source=source, parent=parent, activate=False
                        )

        recipe_dict = project.get("recipe")
        if recipe_dict is not None:
            with contextlib.suppress(Exception):
                session.recipe = Recipe.from_dict(recipe_dict)

        active = session._preferred_open_view(manifest.get("active_view"))
        viewport = manifest.get("viewport") or {}
        hidden_model = (
            mesh
            if mesh is not None and active is not None and active.key in ("processed", "meshed")
            else sap
        )
        hidden = viewport.get("hidden_elem_ids")
        if not isinstance(hidden, list):
            session.hidden_elem_ids = set()
        else:
            ids = {str(ref) for ref in hidden}
            drawable = session._drawable_element_ids(hidden_model)
            session.hidden_elem_ids = ids if (not ids or ids <= drawable) else set()

        return session

    def _preferred_open_view(self, saved_key: Optional[str]) -> Optional[Any]:
        """The view a reopened project shows first: geometry, not a results view.

        A saved ``active_view`` is often a results view (the last mode the user
        animated); reopening straight into it hides the model's supports, groups
        and loads.  Prefer the geometry views — meshed/processed first, then
        unprocessed — and fall back to the saved view (or ``None``).
        """
        for key in ("meshed", "processed", "unprocessed"):
            view = self.views.get(key)
            if view is not None:
                return view
        return self.views.get(saved_key) if saved_key else None

    def _drawable_element_ids(self, model: Any) -> set[str]:
        """Type-qualified ids of the elements *model* can draw.

        Inactive (split/meshed) parents are skipped, so an id that is never
        drawn cannot leak into the hidden set.
        """
        ids: set[str] = set()
        for etype, attr in (("Frame", "frame_elements"), ("Area", "area_elements")):
            for key, elem in (getattr(model, attr, {}) or {}).items():
                if not getattr(elem, "inactive", False):
                    ids.add(f"{etype}:{key}")
        return ids

    def _project_results_arrays(self, collected: dict) -> dict:
        """Merge the collected static archive arrays with modal + RS + pushover
        + IDA.

        RS displacement fields, modal mode shapes and pushover directions are
        persisted as static-shaped cases (``static/RS-<direction>/node_*``,
        ``static/Mode-<i>/node_*``, ``static/Pushover-<direction>/node_*``) so
        each deformed-shape view round-trips like a static load case (Option C).
        The pushover per-step history is persisted as ``pushover/<direction>/…``.
        A single-record IDA contributes its scalar ladder / collapse provenance
        (``ida/<record>/…``) plus the retained collapse-rung transient block
        (``transient/ida_<record>/…``), so the IDA view and its history rebuild
        on load without re-running the analysis.
        """
        from ..io.npz_writer import collect_pushover_arrays
        from ..io.unified_writer import (
            collect_modal_arrays,
            collect_rs_arrays,
            collect_rs_element_force_arrays,
            collect_rs_shell_force_arrays,
        )
        from .project import (
            mode_shape_arrays,
            pushover_displacement_arrays,
            rs_displacement_arrays,
        )

        arrays = dict(collected.get("static_arrays") or {})
        modal = collected.get("modal")
        if modal:
            arrays.update(collect_modal_arrays(modal, collected.get("mode_shapes")))
        rs = collected.get("rs") or {}
        if rs:
            arrays.update(collect_rs_arrays(rs.get("rs_x"), rs.get("rs_y")))

        # Element/shell RS forces are single-direction blocks in the schema
        # (no direction axis), so one direction must serve both.  Pick the
        # first direction present in either mapping — mirroring the review
        # path's first-direction convention — and collect each block only when
        # it recorded that direction, so the two blocks can never disagree.
        rs_element_forces = collected.get("rs_element_forces") or {}
        rs_shell_forces = collected.get("rs_shell_forces") or {}
        directions = list(dict.fromkeys([*rs_element_forces, *rs_shell_forces]))
        rs_force_direction = directions[0] if directions else None
        if rs and rs_force_direction is not None and rs_force_direction in rs_element_forces:
            arrays.update(collect_rs_element_force_arrays(rs_element_forces[rs_force_direction]))
        if rs and rs_force_direction is not None and rs_force_direction in rs_shell_forces:
            arrays.update(collect_rs_shell_force_arrays(rs_shell_forces[rs_force_direction]))

        mesh = self.store.preprocessed() if self.store is not None else None
        if mesh is not None:
            rs_disp = collected.get("rs_nodal_displacements") or {}
            if rs_disp:
                rs_arrays = rs_displacement_arrays(mesh, rs_disp)
                self._append_static_case_labels(arrays, rs_arrays.pop("static_case_labels"))
                arrays.update(rs_arrays)

            shapes = collected.get("mode_shapes") or {}
            if shapes:
                mode_arrays = mode_shape_arrays(mesh, shapes)
                self._append_static_case_labels(arrays, mode_arrays.pop("static_case_labels"))
                arrays.update(mode_arrays)

            step_results = collected.get("pushover_step_results") or {}
            pushover_results = collected.get("pushover_results") or {}
            if step_results:
                for (direction, load_type), per_step in step_results.items():
                    arrays.update(
                        collect_pushover_arrays(
                            mesh,
                            per_step,
                            direction=direction,
                            load_type=load_type,
                            pushover_results=pushover_results.get((direction, load_type)),
                        )
                    )
                # Per-(direction, load type) deformed-shape view: the final
                # recorded step.
                final_fields = {
                    (direction, load_type): per_step[-1].get("node_displacements")
                    for (direction, load_type), per_step in step_results.items()
                    if per_step and per_step[-1].get("node_displacements")
                }
                if final_fields:
                    po_arrays = pushover_displacement_arrays(mesh, final_fields)
                    self._append_static_case_labels(arrays, po_arrays.pop("static_case_labels"))
                    arrays.update(po_arrays)
                self._append_analysis_type(arrays, "pushover")

        # ── IDA (single-record run) + its retained transient history ──
        # The numerical ladder / collapse scalars and the collapse-rung transient
        # block are persisted alongside the regenerable curve, so a reload can
        # rebuild the IDA view and its history without re-running the analysis.
        ida = collected.get("ida")
        if ida:
            from ..io.npz_writer import (
                _merge_ida_transient,
                collect_ida_transient_arrays,
                collect_single_ida_arrays,
            )

            arrays.update(collect_single_ida_arrays(ida))
            self._append_analysis_type(arrays, "ida")
            ida_transient = collect_ida_transient_arrays(ida)
            if ida_transient:
                _merge_ida_transient(arrays, ida_transient)
                self._append_analysis_type(arrays, "transient")
        return arrays

    @staticmethod
    def _append_analysis_type(arrays: dict, name: str) -> None:
        """Append *name* to ``analysis_types``, creating it when absent."""
        import numpy as np

        existing = arrays.get("analysis_types")
        if existing is not None and len(existing):
            arrays["analysis_types"] = np.concatenate(
                [np.asarray(existing), np.array([name], dtype=str)]
            )
        else:
            arrays["analysis_types"] = np.array([name], dtype=str)

    @staticmethod
    def _append_static_case_labels(arrays: dict, labels: Any) -> None:
        """Append *labels* to ``static_case_labels``, creating it when absent."""
        import numpy as np

        existing = arrays.get("static_case_labels")
        if existing is not None and len(existing):
            arrays["static_case_labels"] = np.concatenate(
                [np.asarray(existing), np.asarray(labels)]
            )
        else:
            arrays["static_case_labels"] = labels

    def _ida_result_label(self) -> str:
        """The label the registered IDA figure used — the run's IDA result label.

        The figure view and the saved ``figures.json`` payload must derive the
        same key, so the label is read back from the completed run's IDA step
        (``name`` parameter) rather than re-defaulting to ``"IDA"``.
        """
        if self.last_run is not None:
            for result in self.last_run.results:
                if getattr(result, "kind", None) == "ida":
                    return getattr(result, "label", "IDA") or "IDA"
        return "IDA"

    def _project_tables(self) -> dict:
        """Collect ``{key: Table}`` from every table view in the registry."""
        tables = {}
        for view in self.views.views():
            if view.kind == TABLE:
                table = self.views.table(view.key)
                if table is not None:
                    tables[view.key] = table
        return tables

    def _merge_results_view_arrays(self, arrays: dict) -> None:
        """Merge static arrays from results views (``Analysis ▸ Run``, no recipe).

        Falls back to the registered results repositories when the recipe run
        produced no ``CASES`` payload, so a project can be saved from either path.
        """
        seen = set()
        for view in self.views.views():
            if view.kind == RESULTS:
                repository = self.views.results(view.key)
                if repository is not None and id(repository) not in seen:
                    seen.add(id(repository))
                    arrays.update(repository.to_arrays())
