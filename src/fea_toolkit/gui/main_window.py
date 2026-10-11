"""Main application window for the fea_toolkit desktop GUI.

Milestone 2 built the application *chrome* (menubar, toolbars, docks, message
log, status bar) around the central 3-D viewport that Milestone 1 embedded, and
Milestone 3 added the lazy Model Tree and the property inspector.  Most domain
actions are still present but disabled, each labelled with the milestone that
will wire it (``docs/gui_roadmap.md`` §9.6) -- a menu that appears later is
more jarring than a greyed-out one.

Live: opening a model (``Open`` / :meth:`MainWindow.open_path`), the camera
buttons, the message log, the units and cursor readouts, tree selection driving
the inspector and -- since Milestone 4 -- highlighting the selected entity in
the viewport, plus the node / shell display toggles.
"""

import contextlib
import gc
from typing import Any, Optional

from qtpy.QtCore import QItemSelection, QItemSelectionModel, QItemSelectionRange, Qt, QTimer, QUrl
from qtpy.QtGui import QAction, QDesktopServices, QGuiApplication, QKeySequence
from qtpy.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QStackedWidget,
    QTabWidget,
    QToolBar,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from .app import APP_NAME
from .controllers.interaction import default_config_path
from .controllers.marquee import (
    point_in_rect,
    polygon_hits_rect,
    polygon_inside_rect,
    segment_hits_rect,
    segment_inside_rect,
)
from .controllers.selection import CATEGORY_GROUPS, CATEGORY_NOUNS, SelectionIndex
from .controllers.settings import load_gui_settings, save_display_settings
from .controllers.view_registry import FIGURE, GEOMETRY, RESULTS, TABLE, View, ViewRegistry
from .controllers.view_state import clear_view_state, load_view_state, save_view_state
from .controllers.worker import TaskWorker
from .models.tree_model import ModelTreeModel
from .render_backend import QtRenderBackend, make_select_style
from .views.interactor import PickResult, ViewportInteraction
from .views.message_log import MessageLog
from .views.property_inspector import PropertyInspector
from .views.qt_mouse import install_mouse_filter

_PROJECT_URL = "https://github.com/boustrephon/fea_toolkit"
_CURSOR_POLL_MS = 60
_SELECT_COLOR = (1.0, 0.45, 0.0)  # selected frame / area element
_SELECT_NODE_COLOR = (0.15, 0.55, 1.0)  # selected node

#: Model-tree group key -> render category, the reverse of
#: ``controllers.selection.CATEGORY_GROUPS`` (which maps render category ->
#: group key).  Used to turn selected tree rows back into a highlight / a
#: ``Selection``.
_GROUP_CATEGORY = {group: category for category, group in CATEGORY_GROUPS.items()}

#: Once-only guard for :func:`_freeze_gc_once`.  A one-element list rather than a
#: bool, so marking it does not need a ``global`` rebind (ruff PLW0603).
_GC_FROZEN: list = []


def _freeze_gc_once() -> None:
    """Put this process's live objects beyond the collector's reach, once.

    The Preprocessor runs on a **worker thread**, and a collection triggered
    there walks the whole heap -- including the PySide6/VTK objects the main
    thread owns.  Shiboken's objects are not built to be traversed from another
    thread: on macOS that surfaced as an intermittent segfault, reproducible in
    ``tests/test_gui_views.py`` before this call existed, with the traceback
    landing inside ``copy.deepcopy`` -- at the time the preprocessor's model
    copy, and the largest allocation burst on that thread.

    That deepcopy is gone (the Preprocessor is copy-on-write now, see
    ``docs/dev_notes.md`` → *Copy-on-write replaces the model deepcopy*), and the
    worker now holds the collector off for its whole task
    (:meth:`~fea_toolkit.gui.controllers.worker.TaskWorker.run`), so no
    collection runs there at all.  This freeze is still worth its one line: it
    protects the Qt/VTK objects created *before* the first task — the window among
    them — which a per-task hold cannot reach back to.

    ``gc.freeze()`` moves everything alive *now* -- Qt, VTK, the window, the
    renderer -- into the permanent generation, so no later collection looks at
    it.  Objects created afterwards (the preprocessed topology and its
    ``MeshModel``) stay collectable, which is what stops models from leaking.
    """
    if _GC_FROZEN:
        return
    import gc

    _GC_FROZEN.append(True)
    gc.freeze()


def _entity_identity(entity: Any) -> tuple[Optional[str], Optional[str]]:
    """Locate *entity* in the viewport as ``(attribute_name, value)``.

    Frame elements, area elements and nodes each carry their SAP2000 label in
    a differently-named attribute; everything else in the tree (materials,
    sections, load definitions) has no geometry of its own, so it returns
    ``(None, None)`` and is not highlighted.
    """
    for attr in ("elem_id", "area_id", "node_id"):
        value = getattr(entity, attr, None)
        if value:
            return attr, str(value)
    return None, None


def _case_view_name(case: str, meta: dict) -> str:
    """Display name for a results view: the case, qualified by its combination.

    The unified schema records which combination a case was generated from; when
    the two names differ the label says so, because the variants of one
    combination would otherwise look identical in the tree.
    """
    group = meta.get("group") if meta else None
    if group and group != case:
        return f"{case} ({group})"
    return case


#: Prefix of a results view's key; the load-case label follows it.
_RESULTS_KEY_PREFIX = "results:"


def _table_key(label: str) -> str:
    """The table view's key for a step *label* — the inverse of its registration."""
    return f"table:{label.lower().replace(' ', '-')}"


def _case_of(view: Optional[View]) -> str:
    """The load case a results view stands for (``""`` for any other view).

    The case is carried in the view's key (``"results:DEAD"``), which is what
    :meth:`MainWindow.open_results_path` registers; this is its inverse.
    """
    if view is None or not view.key.startswith(_RESULTS_KEY_PREFIX):
        return ""
    return view.key[len(_RESULTS_KEY_PREFIX) :]


def _model_file_format(path: str, chosen_filter: str) -> tuple[str, str]:
    """Resolve a save-model format from *path* and the chosen dialog filter.

    An explicit extension in *path* wins; otherwise the selected filter decides
    and its canonical extension is appended.  ``"h5"`` covers both ``.h5`` and
    ``.hdf5`` (:func:`~fea_toolkit.io.stage_writer.write_model_stages` accepts
    ``fmt="h5"`` for either).
    """
    lower = path.lower()
    if lower.endswith(".json"):
        return "json", path
    if lower.endswith(".npz"):
        return "npz", path
    if lower.endswith((".h5", ".hdf5")):
        return "h5", path
    if "JSON" in chosen_filter:
        return "json", path + ".json"
    if "NPZ" in chosen_filter:
        return "npz", path + ".npz"
    return "h5", path + ".h5"


class MainWindow(QMainWindow):
    """Top-level window hosting the 3-D viewport and the application chrome.

    Args:
        model: Optional model to render immediately -- a ``SAPModelData``, a
            ``MeshModel`` or an ``AnalysisBuilder``.
        parent: Optional Qt parent widget.
    """

    def __init__(self, model: Optional[Any] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(APP_NAME)

        self._model: Any = None
        self._store: Any = None
        self._views = ViewRegistry()
        self._source_label = ""
        self._viewer: Any = None
        self._interactor: Any = None
        self._backend: Any = None
        self._selection_index: Any = None
        self._interaction: Any = None
        self._mouse_filter: Any = None
        self._hidden_elem_ids: set[str] = set()
        #: The camera and hidden set right after ``View ▸ Reset view``, so
        #: ``closeEvent`` can tell an unchanged view from one the user has moved.
        self._reset_view_baseline: Optional[tuple] = None
        self._default_style: Any = None  # interactor style to restore after Select mode
        self._worker: Any = None
        #: The last completed :class:`~fea_toolkit.workflow.recipe.RecipeRun`,
        #: retained so ``File ▸ Save project`` can serialise its results.
        self._last_run: Any = None
        self._policy, self._display, self._policy_notes = load_gui_settings()
        self._cursor_timer: Optional[QTimer] = None
        self._interaction_enabled = False
        self._actions: dict = {}
        self._stack: Any = None
        self._table_page: Any = None
        self._figure_page: Any = None
        self._figure_canvases: dict = {}
        self._anim_timer: Optional[QTimer] = None
        self._anim_displacements: dict = {}
        self._anim_base_scale: float = 1.0
        self._anim_phase: float = 0.0
        self._anim_frames: list = []
        self._anim_frame_index: int = 0
        self._anim_step_mode: bool = False

        self._create_viewport()
        self._create_actions()
        self._build_menus()
        self._build_toolbars()
        self._build_docks()
        self._build_status_bar()
        self._set_model_actions_enabled(False)
        self._set_analysis_actions_enabled()
        self._set_view_actions_enabled()
        self._decorate_view()
        for note in self._policy_notes:
            self.log(note, "warn")
        button = "Left-click" if self._policy.pick_button == "left" else "Right-click"
        self.log(f"Ready.  {button} an element to select it; drag to orbit.")

        if model is not None:
            self._remember_source(model)
            self.show_model(model)

    def _remember_source(self, model: Any, source: str = "") -> None:
        """Wrap the loaded model in a store and start a fresh list of views.

        The Preprocessor consumes a ``SAPModelData`` and returns a ``MeshModel``,
        and it preprocesses a *copy* — so re-running it from the parsed source is
        always safe, while re-running on a ``MeshModel`` would not be.  Handed a
        ``MeshModel`` or ``AnalysisBuilder`` directly (tests, scripts, a stage
        file) there is nothing to preprocess and the Model-menu actions stay
        disabled; the model still gets a view so the tree and Inspector work.

        Args:
            model: The model about to be displayed.
            source: Human-readable provenance, e.g. the file name.
        """
        from ..io.model_store import InMemoryModelStore
        from ..model.mesh_model import MeshModel
        from ..model.sap_data import SAPModelData

        self._source_label = source
        self._reset_view_baseline = None
        self._store = None
        self._views = ViewRegistry()
        self._reset_session_display()
        if isinstance(model, SAPModelData):
            self._store = InMemoryModelStore(model, source=source)
            self._views = ViewRegistry(self._store)
            self._views.add_geometry("unprocessed", "Unprocessed", model, source=source)
            # Let the recipe step dialog seed the ``run_static`` cases editor
            # from this model's own static cases.
            panel = getattr(self, "_recipe_panel", None)
            if panel is not None:
                panel.set_model_data(model)
        elif isinstance(model, MeshModel):
            self._views.add_geometry("processed", "Processed", model, source=source)

    # ── Viewport ────────────────────────────────────────────────────

    def _create_viewport(self) -> None:
        """Create the embedded PyVistaQt interactor and its render backend.

        The central area is a stacked widget: page 0 is the 3-D viewport, and
        pages 1 and 2 are created lazily for a table view and a figure view
        (P30 phase C) — a model view draws geometry, but a ``check`` step
        produces a table and a ``chart`` step a figure, neither of which belongs
        in the viewport.
        """
        from .render_backend import MainThreadQtInteractor

        # Quad-view-ready container: holds a single viewport today, a grid of
        # them later (roadmap design rule 10).
        self._viewport_container = QWidget(self)
        self._viewport_layout = QVBoxLayout(self._viewport_container)
        self._viewport_layout.setContentsMargins(0, 0, 0, 0)

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._viewport_container)  # page 0: the viewport
        self.setCentralWidget(self._stack)

        self._interactor = MainThreadQtInteractor(self._viewport_container)
        self._viewport_layout.addWidget(self._interactor)
        self._backend = QtRenderBackend(self._interactor)
        self._create_interaction()

    # ── Picking (viewport -> tree) ───────────────────────────────────

    def _create_interaction(self) -> None:
        """Wire viewport gestures to selection, per the interaction policy.

        The gesture comes from a **Qt event filter** (`views/qt_mouse.py`), not
        from VTK observers: measured on macOS, the widget forwards a press to
        the interactor but never the matching release, so a release-driven
        gesture as a VTK observer can never complete (`docs/dev_notes.md`).
        """
        self._interaction = ViewportInteraction(
            self._interactor,
            policy=self._policy,
            on_pick=self._on_viewport_pick,
            node_actors=lambda: self._backend.actors("nodes"),
            on_marquee=self._on_marquee,
        )
        self._mouse_filter = install_mouse_filter(
            self._interactor,
            self._interaction,
            self,
            on_context_menu=self._on_viewport_context_menu,
        )

    def _on_viewport_pick(self, result: PickResult) -> None:
        """Apply a click to the selection: replace, add, toggle, or clear.

        Args:
            result: The pick from the interaction adapter (``hit`` is ``False``
                when the click met no geometry).  ``result.modifiers`` decides
                the action -- empty for replace, the policy's add / toggle
                modifiers otherwise.
        """
        if self._viewer is None or self._selection_index is None:
            return
        category = self._backend.category_of_actor(result.actor) if result.hit else None
        label = self._selection_index.label(category, result.index) if category else None
        if label is None:
            # A click on empty space clears -- unless a modifier is holding the
            # current selection open.
            if not result.modifiers:
                self._clear_selection()
            return
        group_key = self._selection_index.group_key(category)
        if self._select_entity_in_tree(group_key, label, modifiers=result.modifiers):
            noun = CATEGORY_NOUNS.get(category, "entity")
            self.log(f"Selected {noun} {label} in the tree from the viewport.")

    def _clear_selection(self) -> None:
        """Empty the inspector, the tree selection and the viewport highlight."""
        self._inspector.show_object(None)
        if self._viewer is not None:
            self._viewer.clear_highlights()
        selection = self._tree_view.selectionModel()
        if selection is not None:
            selection.clear()

    def _select_entity_in_tree(self, group_key: str, label: str, modifiers: tuple = ()) -> bool:
        """Select the row for *label* with the flag *modifiers* imply.

        Expand *group_key*, apply a replace / add / toggle to the tree selection
        and scroll the row into view.  Selecting the row drives the inspector and
        the viewport highlight through the ordinary tree wiring.

        Args:
            group_key: Group key, e.g. ``"frame_elements"``.
            label: The entity's SAP label.
            modifiers: Canonical modifier names held at the pick; the policy's
                add / toggle modifier decide the selection flag, empty means
                replace.

        Returns:
            ``True`` when the row existed and was selected.
        """
        index = self._tree_model.index_for(group_key, label)
        if index is None:
            return False
        self._tree_view.expand(index.parent())
        model = self._tree_view.selectionModel()
        if self._policy.toggle_modifier in modifiers:
            flag = QItemSelectionModel.SelectionFlag.Toggle
        elif self._policy.add_modifier in modifiers:
            flag = QItemSelectionModel.SelectionFlag.Select
        else:
            flag = QItemSelectionModel.SelectionFlag.ClearAndSelect
        model.setCurrentIndex(index, QItemSelectionModel.SelectionFlag.NoUpdate)
        model.select(index, flag)
        self._tree_view.scrollTo(index)
        return True

    # ── Multi-selection (viewport -> tree -> Selection) ───────────────

    def _on_marquee(self, start: tuple, end: tuple) -> None:
        """Rubber-band select what the drag rectangle encloses or touches.

        A left-to-right drag is a *window* marquee (only fully-enclosed entities);
        a right-to-left drag is a *crossing* marquee (whatever the box touches).

        Args:
            start, end: Rectangle corners in VTK *device* pixels (bottom-left
                origin), the drag's anchor and release.
        """
        if self._viewer is None or self._selection_index is None:
            return
        x0, y0 = start
        x1, y1 = end
        if abs(x1 - x0) < 3 or abs(y1 - y0) < 3:
            return  # a click-sized box is a pick, not a marquee
        window = x1 >= x0  # left-to-right -> window, right-to-left -> crossing
        frames, areas, nodes = self._marquee_entities(x0, y0, x1, y1, window)
        self._select_many(frames, areas, nodes)
        total = len(frames) + len(areas) + len(nodes)
        kind = "window" if window else "crossing"
        self.log(f"Marquee ({kind}) selected {total} entit{'y' if total == 1 else 'ies'}.")

    def _marquee_entities(self, x0: float, y0: float, x1: float, y1: float, window: bool) -> tuple:
        """``(frame_ids, area_ids, node_ids)`` sets inside the drag rectangle.

        A *window* marquee keeps only what is fully enclosed; a *crossing* marquee
        keeps whatever the rectangle touches.
        """
        import vtk

        renderer = self._interactor.renderer

        def project(world: Any) -> tuple:
            coord = vtk.vtkCoordinate()
            coord.SetCoordinateSystemToWorld()
            coord.SetValue(*[float(v) for v in world])
            disp = coord.GetComputedDisplayValue(renderer)
            return float(disp[0]), float(disp[1])

        index = self._selection_index
        frame_test = segment_inside_rect if window else segment_hits_rect
        area_test = polygon_inside_rect if window else polygon_hits_rect
        frames = {
            f.elem_id
            for f in index.frames
            if frame_test(*project(f.start), *project(f.end), x0, y0, x1, y1)
        }
        areas = {
            s.area_id
            for s in index.shells
            if area_test([project(v) for v in s.vertices], x0, y0, x1, y1)
        }
        nodes = {
            n.node_id for n in index.nodes if point_in_rect(*project(n.position), x0, y0, x1, y1)
        }
        return frames, areas, nodes

    def _select_many(self, frames: set, areas: set, nodes: set) -> None:
        """Replace the tree selection with *frames* / *areas* / *nodes*."""
        model = self._tree_view.selectionModel()
        if model is None:
            return
        model.clearSelection()
        entities = (
            [("frame_elements", label) for label in sorted(frames)]
            + [("area_elements", label) for label in sorted(areas)]
            + [("nodes", label) for label in sorted(nodes)]
        )
        selection = QItemSelection()
        last_index = None
        for group_key, label in entities:
            index = self._tree_model.index_for(group_key, label)
            if index is None:
                continue
            self._tree_view.expand(index.parent())
            selection.append(QItemSelectionRange(index))
            last_index = index
        if last_index is not None:
            model.setCurrentIndex(last_index, QItemSelectionModel.SelectionFlag.NoUpdate)
        model.select(selection, QItemSelectionModel.SelectionFlag.Select)

    def _selected_entities(self) -> tuple:
        """``(frame_ids, area_ids, node_ids)`` sets for the current selection."""
        frames: set = set()
        areas: set = set()
        nodes: set = set()
        model = self._tree_view.selectionModel()
        if model is None:
            return frames, areas, nodes
        for index in model.selectedIndexes():
            if index.column() != 0:
                continue
            group = self._tree_model._group_of(index)
            if group is None:
                continue
            category = _GROUP_CATEGORY.get(group.key)
            if category is None:
                continue
            entity = index.data(Qt.ItemDataRole.UserRole)
            label = (
                getattr(entity, "elem_id", None)
                or getattr(entity, "area_id", None)
                or getattr(entity, "node_id", None)
            )
            if label is None:
                continue
            ({"frames": frames, "shells": areas, "nodes": nodes})[category].add(str(label))
        return frames, areas, nodes

    def _refresh_selection_highlight(self) -> None:
        """Re-highlight the viewport to match the full tree selection."""
        if self._viewer is None:
            return
        frames, areas, nodes = self._selected_entities()
        self._viewer.clear_highlights()
        if frames or areas:
            self._viewer.highlight_elements(
                frame_ids=sorted(frames), area_ids=sorted(areas), color=_SELECT_COLOR
            )
        if nodes:
            self._viewer.highlight_nodes(sorted(nodes), color=_SELECT_NODE_COLOR)

    def _on_selection_changed(self, *_args) -> None:
        """Keep the viewport highlight in step with a multi-row selection."""
        self._refresh_selection_highlight()

    def current_selection(self) -> Optional[Any]:
        """The current visual selection as a ``Selection``, or ``None``.

        Collapses the selected tree rows into a scoping ``Selection`` a workflow
        step (or a duplicate view) can take.  Best-effort: ``element_ids`` is
        shared between frames and areas, so a frame id that collides with an
        area id cannot be told apart once collapsed -- the same ambiguity the
        expression grammar has.
        """
        frames, areas, nodes = self._selected_entities()
        if not (frames or areas or nodes):
            return None
        from ..model.selection import Selection

        types = []
        if frames:
            types.append("Frame")
        if areas:
            types.append("Area")
        if nodes:
            types.append("Node")
        return Selection(
            element_types=types,
            element_ids=sorted(frames | areas) or None,
            node_ids=sorted(nodes) or None,
        )

    # ── Selection read-outs and the context menu ──────────────────────

    def _on_copy_selection(self) -> None:
        """Copy the current selection to the clipboard as a selection expression."""
        selection = self.current_selection()
        if selection is None:
            self.log("Nothing selected to copy.", "warn")
            return
        expr = selection.to_string()
        QGuiApplication.clipboard().setText(expr)
        self.log(f"Copied selection: {expr}")

    def _on_selection_weight(self) -> None:
        """Report the self-weight of the current selection in a dialog."""
        selection = self.current_selection()
        if selection is None or self._model is None:
            self.log("Nothing selected to weigh.", "warn")
            return
        from ..model.self_weight import selection_weight

        weight = selection_weight(self._model, selection)
        if weight.count == 0:
            self.log(
                "The selection has no weightable elements (nodes only, or no "
                "section/material with a unit weight).",
                "warn",
            )
            return
        message = (
            f"{weight.frame_count} frame(s), {weight.area_count} area(s)\n\n"
            f"Frame weight:  {weight.frame_weight:.4g} {weight.unit}\n"
            f"Area weight:   {weight.area_weight:.4g} {weight.unit}\n"
            f"Total weight:  {weight.total:.4g} {weight.unit}"
        )
        QMessageBox.information(self, "Selection weight", message)

    def _on_viewport_context_menu(self, global_pos: Any) -> None:
        """Open a context menu for the current selection (right-click, when free).

        The right button reaches here only when it is not the policy's pick
        button (see ``views/qt_mouse.py``), so a ``right_click`` preset keeps its
        button for selection and never opens the menu.
        """
        menu = QMenu(self)
        has_selection = self.current_selection() is not None

        copy_action = menu.addAction("Copy selection")
        copy_action.setEnabled(has_selection)
        copy_action.triggered.connect(self._on_copy_selection)

        weight_action = menu.addAction("Mass / weight of selection…")
        weight_action.setEnabled(has_selection)
        weight_action.triggered.connect(self._on_selection_weight)

        menu.addSeparator()
        menu.addAction(self._actions["view.hide_selected"])
        menu.addAction(self._actions["view.isolate_selected"])
        menu.addAction(self._actions["view.show_all"])

        menu.exec(global_pos)

    # ── Select / Orbit mode (the SAP2000 arrangement) ─────────────────

    def _on_select_mode_toggled(self, checked: bool) -> None:
        """**View ▸ Select mode**: swap between Select and Orbit interaction.

        In Select mode a drag marquee-selects and orbiting is disabled; in Orbit
        mode a drag orbits and a clean click still selects.
        """
        from dataclasses import replace

        self._policy = replace(self._policy, select_mode=checked)
        self._interaction.set_policy(self._policy)
        self._set_select_style(checked)
        self.log("Select mode on." if checked else "Orbit mode on.")

    def _set_select_style(self, select: bool) -> None:
        """Disable the select button's orbit while in Select mode."""
        iren = getattr(self._interactor, "iren", None)
        if iren is None:
            return
        vtk_iren = getattr(iren, "interactor", None) or iren
        if select:
            if self._default_style is None:
                self._default_style = vtk_iren.GetInteractorStyle()
            vtk_iren.SetInteractorStyle(make_select_style(self._policy.pick_button))
        elif self._default_style is not None:
            vtk_iren.SetInteractorStyle(self._default_style)
            self._default_style = None

    # ── Actions ─────────────────────────────────────────────────────

    def _real_action(
        self,
        text: str,
        slot: Any,
        *,
        shortcut: Optional[str] = None,
        tip: Optional[str] = None,
    ) -> QAction:
        """Build a connected, enabled action (shared by menus and toolbars)."""
        act = QAction(text, self)
        if shortcut:
            act.setShortcut(QKeySequence(shortcut))
        act.setStatusTip(tip or text)
        if tip:
            act.setToolTip(tip)
        act.triggered.connect(slot)
        return act

    def _placeholder(self, text: str, milestone: str) -> QAction:
        """Build a disabled placeholder tagged with the milestone that wires it."""
        act = QAction(text, self)
        tip = f"Available in {milestone}"
        act.setStatusTip(tip)
        act.setToolTip(tip)
        act.setEnabled(False)
        return act

    def _toggle_action(
        self,
        text: str,
        slot: Any,
        *,
        tip: Optional[str] = None,
        checked: bool = True,
        enabled: bool = True,
    ) -> QAction:
        """Build a checkable action (a display toggle).

        The geometry toggles start **checked** — the model is drawn unless asked
        otherwise — while a *results* overlay starts unchecked and disabled,
        because it needs a view that can supply the data.
        """
        act = QAction(text, self)
        act.setCheckable(True)
        act.setChecked(checked)
        act.setStatusTip(tip or text)
        if tip:
            act.setToolTip(tip)
        act.setEnabled(enabled)
        act.toggled.connect(slot)
        return act

    def _create_actions(self) -> None:
        """Create every action once so menus and toolbars can share them."""
        a = self._actions

        # ── Live in M2 ──
        a["file.open"] = self._real_action(
            "Open", self._on_open, shortcut="Ctrl+O", tip="Open a SAP2000 .s2k or JSON model"
        )
        a["file.open_results"] = self._real_action(
            "Open results\u2026",
            self._on_open_results,
            tip="Open an NPZ/HDF5 results archive and add a view per load case",
        )
        a["file.quit"] = self._real_action("Quit", self.close, shortcut="Ctrl+Q")
        a["view.fit"] = self._real_action("Zoom to fit", self._on_zoom_fit)
        a["view.reset_view"] = self._real_action(
            "Reset view",
            self._on_reset_view,
            tip="Reset the camera, show all hidden elements, and forget this model's saved view state",
        )
        a["view.iso"] = self._real_action("Isometric", self._view_iso, tip="Isometric view")
        a["view.xy"] = self._real_action("Top (XY)", self._view_xy, tip="Look down the Z axis")
        a["view.xz"] = self._real_action("Front (XZ)", self._view_xz, tip="Look along the Y axis")
        a["view.yz"] = self._real_action("Side (YZ)", self._view_yz, tip="Look along the X axis")
        a["help.docs"] = self._real_action("Documentation", self._on_docs)
        a["help.about"] = self._real_action(f"About {APP_NAME}", self._on_about)

        # ── macOS application-menu roles ──
        # On macOS Qt places a role-carrying action in the Application menu and
        # labels the item from the action text; elsewhere the role is inert.
        # Qt's own items would read "About Python" / "Quit Python" -- the
        # process bundle names them (docs/dev_notes.md).
        a["file.quit"].setMenuRole(QAction.MenuRole.QuitRole)
        a["help.about"].setMenuRole(QAction.MenuRole.AboutRole)

        # ── Greyed placeholders (each names its milestone) ──
        a["file.save_model"] = self._real_action(
            "Save model\u2026",
            self._on_save_model,
            tip="Write the meshed model to a JSON, NPZ or HDF5 file",
        )
        a["file.save_model"].setEnabled(False)
        a["file.save_results"] = self._real_action(
            "Save results\u2026",
            self._on_save_results,
            tip="Write the displayed result to an NPZ archive",
        )
        a["file.save_results"].setEnabled(False)
        a["file.save_project"] = self._real_action(
            "Save project\u2026",
            self._on_save_project,
            tip="Write the model, recipe, results and figures to a project directory",
        )
        a["file.save_project"].setEnabled(False)
        a["file.open_project"] = self._real_action(
            "Open project\u2026",
            self._on_open_project,
            tip="Reload a saved project (.zip archive) without re-running analyses",
        )
        a["file.export_tcl"] = self._placeholder("Export Tcl", "Milestone 7")
        a["file.export_image"] = self._placeholder("Export screenshot", "Milestone 7")
        a["edit.copy"] = self._real_action(
            "Copy selection",
            self._on_copy_selection,
            tip="Copy the current selection to the clipboard as a selection expression",
        )
        a["edit.duplicate_view"] = self._real_action(
            "Duplicate view",
            self._on_duplicate_view,
            tip="Copy the current view and narrow it with a selection expression",
        )
        a["edit.edit_selection"] = self._real_action(
            "Edit view selection\u2026",
            self._on_edit_view_selection,
            tip="Change the selection expression of a derived view",
        )
        a["edit.preferences"] = self._placeholder("Preferences", "Milestone 8")
        a["edit.preferences"].setMenuRole(QAction.MenuRole.PreferencesRole)
        a["view.show_nodes"] = self._toggle_action(
            "Show nodes", self._on_show_nodes, tip="Show or hide node markers"
        )
        a["view.show_shells"] = self._toggle_action(
            "Show shells", self._on_show_shells, tip="Show or hide area elements"
        )
        a["view.show_frames"] = self._toggle_action(
            "Show beams", self._on_show_frames, tip="Show or hide frame elements"
        )
        a["view.show_extruded"] = self._toggle_action(
            "Extruded shapes",
            self._on_show_extruded,
            tip=(
                "Draw each frame as an extruded solid (its cross-section swept "
                "along the member) instead of a thin line"
            ),
            checked=self._display.show_extruded,
        )
        a["view.show_restraints"] = self._toggle_action(
            "Show restraints",
            self._on_show_restraints,
            tip="Show or hide support symbols at restrained nodes",
        )
        a["view.show_labels"] = self._placeholder("Show element labels", "P23")
        a["view.show_loads"] = self._placeholder("Show loads", "Milestone 6")
        a["view.show_forces"] = self._toggle_action(
            "Show force diagrams",
            self._on_forces_toggled,
            tip="Toggle the active load case's member end forces",
            checked=False,
            enabled=False,
        )
        a["view.clear_highlights"] = self._real_action(
            "Clear highlights", self._on_clear_highlights, tip="Drop the selection highlight"
        )
        a["view.hide_selected"] = self._real_action(
            "Hide selected", self._on_hide_selected, tip="Hide the selected elements"
        )
        a["view.isolate_selected"] = self._real_action(
            "Isolate selected",
            self._on_isolate_selected,
            tip="Hide everything but the selected elements",
        )
        a["view.show_all"] = self._real_action(
            "Show all", self._on_show_all, tip="Restore every hidden element"
        )
        a["view.select_mode"] = self._toggle_action(
            "Select mode",
            self._on_select_mode_toggled,
            tip="Toggle Select mode: a drag marquee-selects instead of orbiting",
            checked=False,
        )
        a["view.reset_layout"] = self._placeholder("Reset layout", "Milestone 8")
        a["model.mesh"] = self._real_action(
            "Mesh…",
            self._on_mesh_dialog,
            tip="Preprocess: split frames at joints and mesh areas (runs on a worker)",
        )
        a["model.selections"] = self._placeholder("Selections", "a future release")
        a["model.units"] = self._placeholder("Units", "a future release")
        # ── Recipe: the workflow as an ordered list of steps ──
        a["recipe.run"] = self._real_action(
            "Run recipe",
            self._on_recipe_run,
            shortcut="Ctrl+Shift+R",
            tip="Run the recipe's steps in order, on a worker",
        )
        a["recipe.clear"] = self._real_action(
            "Clear recipe", self._on_recipe_clear, tip="Empty the Recipe panel"
        )
        a["recipe.open"] = self._real_action(
            "Open recipe\u2026",
            self._on_recipe_open,
            tip="Read a recipe from a JSON file and show it in the panel",
        )
        a["recipe.save"] = self._real_action(
            "Save recipe\u2026",
            self._on_recipe_save,
            tip="Write the Recipe panel's steps to a JSON file",
        )
        a["recipe.export"] = self._real_action(
            "Export as Python\u2026",
            self._on_recipe_export,
            tip="Write the recipe as a runnable Python script",
        )
        for key in ("recipe.run", "recipe.clear", "recipe.save", "recipe.export"):
            a[key].setEnabled(False)
        a["analysis.run"] = self._real_action(
            "Run\u2026",
            self._on_analysis_run,
            shortcut="Ctrl+R",
            tip="Choose static cases or combinations to solve (needs a preprocessed model)",
        )
        a["analysis.modal"] = self._real_action(
            "Modal analysis",
            self._on_modal_preset,
            tip="Solve the eigenvalue (modal) problem against the preprocessed model",
        )
        a["analysis.spectrum"] = self._real_action(
            "Response spectrum",
            self._on_spectrum_preset,
            tip="CQC response-spectrum combination for one direction",
        )
        a["analysis.pushover"] = self._real_action(
            "Pushover",
            self._on_pushover_preset,
            tip="Nonlinear static (pushover) analysis with CSM evaluation",
        )
        for key in ("analysis.modal", "analysis.spectrum", "analysis.pushover"):
            a[key].setEnabled(False)
        a["analysis.stop"] = self._real_action(
            "Stop",
            self._on_analysis_stop,
            tip="Stop the running analysis at its next case boundary",
        )
        a["analysis.stop"].setEnabled(False)
        a["results.deformed"] = self._toggle_action(
            "Deformed shape",
            self._on_deformed_toggled,
            tip="Draw the active load case's deformed shape, amplified by the scale",
            checked=False,
            enabled=False,
        )
        a["results.forces"] = self._toggle_action(
            "Force diagrams",
            self._on_forces_toggled,
            tip="Draw the active load case's member end forces, in the chosen component",
            checked=False,
            enabled=False,
        )
        a["results.animate"] = self._toggle_action(
            "Animate deformed shape",
            self._on_animate_toggled,
            tip="Oscillate the active view's deformed shape (a mode shape, a static or RS field)",
            checked=False,
            enabled=False,
        )
        a["results.storey"] = self._placeholder("Storey response", "Milestone 7")
        a["results.pushover_curve"] = self._placeholder("Pushover curve", "Milestone 7")
        a["results.clear"] = self._real_action(
            "Clear results",
            self._on_clear_results,
            tip="Remove the deformed shape and force diagrams from the viewport",
        )

    # ── Menus + toolbars ────────────────────────────────────────────

    def _build_menus(self) -> None:
        a = self._actions
        bar = self.menuBar()

        m = bar.addMenu("&File")
        m.addAction(a["file.open"])
        m.addAction(a["file.open_results"])
        m.addAction(a["file.open_project"])
        m.addSeparator()
        for key in (
            "file.save_model",
            "file.save_results",
            "file.save_project",
            "file.export_tcl",
            "file.export_image",
        ):
            m.addAction(a[key])
        m.addSeparator()
        m.addAction(a["file.quit"])

        m = bar.addMenu("&Edit")
        m.addAction(a["edit.copy"])
        m.addAction(a["edit.duplicate_view"])
        m.addAction(a["edit.edit_selection"])
        m.addSeparator()
        m.addAction(a["edit.preferences"])

        m = bar.addMenu("&View")
        m.addAction(a["view.fit"])
        m.addAction(a["view.reset_view"])
        m.addSeparator()
        camera = m.addMenu("Camera")
        for key in ("view.iso", "view.xy", "view.xz", "view.yz"):
            camera.addAction(a[key])
        display = m.addMenu("Display")
        for key in (
            "view.show_nodes",
            "view.show_shells",
            "view.show_frames",
            "view.show_extruded",
            "view.show_restraints",
            "view.show_labels",
            "view.show_loads",
            "view.show_forces",
        ):
            display.addAction(a[key])
        display.addSeparator()
        display.addAction(a["view.hide_selected"])
        display.addAction(a["view.isolate_selected"])
        display.addAction(a["view.show_all"])
        display.addAction(a["view.clear_highlights"])
        m.addSeparator()
        m.addAction(a["view.select_mode"])
        m.addAction(a["view.reset_layout"])

        m = bar.addMenu("&Model")
        for key in ("model.mesh", "model.selections", "model.units"):
            m.addAction(a[key])

        m = bar.addMenu("&Analysis")
        m.addAction(a["analysis.run"])
        m.addSeparator()
        for key in ("analysis.modal", "analysis.spectrum", "analysis.pushover"):
            m.addAction(a[key])
        m.addSeparator()
        m.addAction(a["analysis.stop"])

        m = bar.addMenu("&Recipe")
        m.addAction(a["recipe.run"])
        m.addSeparator()
        m.addAction(a["recipe.open"])
        m.addAction(a["recipe.save"])
        m.addAction(a["recipe.export"])
        m.addSeparator()
        m.addAction(a["recipe.clear"])

        m = bar.addMenu("&Results")
        for key in (
            "results.deformed",
            "results.forces",
            "results.animate",
            "results.storey",
            "results.pushover_curve",
        ):
            m.addAction(a[key])
        m.addSeparator()
        m.addAction(a["results.clear"])

        m = bar.addMenu("&Help")
        m.addAction(a["help.docs"])
        m.addSeparator()
        m.addAction(a["help.about"])

    def _build_toolbars(self) -> None:
        a = self._actions

        # The overlay scales sit on the toolbar rather than in a dialog: they are
        # adjusted *while* looking at the shape/diagram, and re-drawing on every
        # change is what makes finding a readable size quick.  Each is a
        # **percentage of the model diagonal** — the auto-scale's own target —
        # so the knob is unit-agnostic and never exposes a raw length-per-force
        # factor, which is what made a whole-building flag diagram enormous.
        self._deformed_scale = QDoubleSpinBox(self)
        self._deformed_scale.setObjectName("deformed_scale")
        self._deformed_scale.setRange(0.1, 1000.0)
        self._deformed_scale.setDecimals(1)
        self._deformed_scale.setSingleStep(5.0)
        self._deformed_scale.setValue(self._display.deformed_scale)
        self._deformed_scale.setSuffix("%")
        self._deformed_scale.setToolTip("Deformed-shape size (% of model diagonal)")
        self._deformed_scale.setStatusTip("Deformed-shape size (% of model diagonal)")
        self._deformed_scale.valueChanged.connect(self._on_deformed_scale_changed)

        self._force_scale = QDoubleSpinBox(self)
        self._force_scale.setObjectName("force_scale")
        self._force_scale.setRange(0.1, 1000.0)
        self._force_scale.setDecimals(1)
        self._force_scale.setSingleStep(5.0)
        self._force_scale.setValue(self._display.force_scale)
        self._force_scale.setSuffix("%")
        self._force_scale.setToolTip("Force-diagram size (% of model diagonal)")
        self._force_scale.setStatusTip("Force-diagram size (% of model diagonal)")
        self._force_scale.valueChanged.connect(self._on_force_scale_changed)

        # Which end-force component a flag diagram shows.  The SAP local-DOF
        # names read better than the schema's x/y/z keys and are the same six
        # local DOFs (see FORCE_QUANTITY_LABELS); the combo therefore holds the
        # *label* and the handler passes the key underneath it.
        from ..model.sap_data import FORCE_QUANTITY_LABELS

        self._force_quantity = QComboBox(self)
        self._force_quantity.setObjectName("force_quantity")
        for label in FORCE_QUANTITY_LABELS:
            self._force_quantity.addItem(label)
        self._force_quantity.setCurrentText(self._display.force_quantity)
        self._force_quantity.setToolTip("End-force component drawn as a flag diagram")
        self._force_quantity.setStatusTip("End-force component drawn as a flag diagram")
        self._force_quantity.currentIndexChanged.connect(self._on_force_quantity_changed)
        self._force_quantity.setEnabled(False)  # until a view carries end forces

        # Display quality knobs.  Opacity is an actor property, so it updates in
        # place; shrink is geometry and needs the model redrawn — the two are
        # wired differently for that reason.
        self._shell_opacity = QDoubleSpinBox(self)
        self._shell_opacity.setObjectName("shell_opacity")
        self._shell_opacity.setRange(0.05, 1.0)
        self._shell_opacity.setDecimals(2)
        self._shell_opacity.setSingleStep(0.05)
        self._shell_opacity.setValue(self._display.shell_opacity)
        self._shell_opacity.setToolTip("Opacity of area elements (shells)")
        self._shell_opacity.setStatusTip("Opacity of area elements (shells)")
        self._shell_opacity.valueChanged.connect(self._on_shell_opacity_changed)

        self._shrink = QDoubleSpinBox(self)
        self._shrink.setObjectName("shrink")
        self._shrink.setRange(0.5, 1.0)
        self._shrink.setDecimals(2)
        self._shrink.setSingleStep(0.05)
        self._shrink.setValue(self._display.shrink)
        self._shrink.setToolTip("Draw elements shrunken, opening up the joints")
        self._shrink.setStatusTip("Draw elements shrunken, opening up the joints")
        self._shrink.valueChanged.connect(self._on_shrink_changed)

        def _put(bar: QToolBar, key: Any) -> None:
            """Add one entry: ``None`` is a separator, ``"@name"`` a widget."""
            widgets = {
                "@deformed_scale": ("Deform %", self._deformed_scale),
                "@force_scale": ("Flags %", self._force_scale),
                "@force_quantity": ("Force", self._force_quantity),
                "@shell_opacity": ("Shells", self._shell_opacity),
                "@shrink": ("Shrink", self._shrink),
            }
            if key is None:
                bar.addSeparator()
            elif key in widgets:
                text, widget = widgets[key]
                bar.addWidget(QLabel(text))
                bar.addWidget(widget)
            else:
                bar.addAction(a[key])

        main = QToolBar("Main", self)
        main.setObjectName("toolbar_main")
        main.setMovable(False)
        main.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        for key in (
            "file.open",
            "file.save_results",
            None,
            "analysis.run",
            "analysis.stop",
            None,
            "model.mesh",
        ):
            _put(main, key)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, main)
        self._toolbar_main = main

        view = QToolBar("View", self)
        view.setObjectName("toolbar_view")
        view.setMovable(False)
        view.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        for key in (
            "view.fit",
            None,
            "view.iso",
            "view.xy",
            "view.xz",
            "view.yz",
            "view.select_mode",
            None,
            "view.show_nodes",
            "view.show_frames",
            "view.show_shells",
            "view.show_restraints",
            "view.show_labels",
            "view.show_loads",
            "view.show_forces",
            None,
            "results.deformed",
            "@deformed_scale",
            "results.forces",
            "@force_scale",
            "@force_quantity",
            None,
            "@shell_opacity",
            "@shrink",
        ):
            _put(view, key)
        self.addToolBar(Qt.ToolBarArea.RightToolBarArea, view)
        self._toolbar_view = view

    # ── Docks ───────────────────────────────────────────────────────

    @staticmethod
    def _placeholder_panel(text: str, milestone: str) -> QLabel:
        """An empty dock panel that names the milestone which fills it."""
        label = QLabel(f"{text} \u2014 {milestone}")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setEnabled(False)
        label.setWordWrap(True)
        return label

    def _build_docks(self) -> None:
        """Left: the Model Tree over the Inspector; bottom: the Message Log."""
        self._tree_model = ModelTreeModel(parent=self)
        self._tree_view = QTreeView(self)
        self._tree_view.setObjectName("tree_model")
        self._tree_view.setModel(self._tree_model)
        # Rows are materialised only when a group is expanded (see
        # ModelTreeModel), so a large model stays responsive.
        self._tree_view.setUniformRowHeights(True)
        self._tree_view.setAlternatingRowColors(True)
        self._tree_view.setColumnWidth(0, 200)
        # Multi-select: Shift/Ctrl-click add/toggle, and a marquee selects many.
        self._tree_view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._tree_view.selectionModel().currentChanged.connect(self._on_tree_selection)
        self._tree_view.selectionModel().selectionChanged.connect(self._on_selection_changed)

        trees = QTabWidget(self)
        trees.setObjectName("tabs_trees")
        trees.addTab(self._tree_view, "Model Tree")
        trees.addTab(self._placeholder_panel("Property tree", "a future release"), "Property Tree")
        self._tree_dock = QDockWidget("Model", self)
        self._tree_dock.setObjectName("dock_trees")
        self._tree_dock.setWidget(trees)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._tree_dock)

        self._inspector = PropertyInspector(self)
        self._inspector_dock = QDockWidget("Inspector", self)
        self._inspector_dock.setObjectName("dock_inspector")
        self._inspector_dock.setWidget(self._inspector)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._inspector_dock)
        self.splitDockWidget(self._tree_dock, self._inspector_dock, Qt.Orientation.Vertical)

        self._message_log = MessageLog(self)
        self._message_log.setObjectName("log_messages")
        self._log_dock = QDockWidget("Messages", self)
        self._log_dock.setObjectName("dock_messages")
        self._log_dock.setWidget(self._message_log)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._log_dock)
        self._build_recipe_dock()

    def _build_recipe_dock(self) -> None:
        """Build the Recipe dock — the workflow as an ordered list of steps.

        It sits in the bottom area beside the Message Log, because running a
        recipe is precisely what the log narrates.  ``RecipePanel`` needs Qt, so
        it is imported here rather than at module scope (lazy-import policy).
        """
        from .views.recipe_panel import RecipePanel

        self._recipe_panel = RecipePanel(self)
        self._recipe_panel.changed.connect(self._set_recipe_actions_enabled)
        self._recipe_dock = QDockWidget("Recipe", self)
        self._recipe_dock.setObjectName("dock_recipe")
        self._recipe_dock.setWidget(self._recipe_panel)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._recipe_dock)

    def _size_bottom_docks(self) -> None:
        """Keep the Messages and Recipe docks to a fifth of the window height.

        Both sit in the bottom dock area beside each other, so they share one
        strip; sizing them together keeps the viewport dominant.
        """
        height = max(60, int(self.height() * 0.2))
        docks = [self._log_dock, self._recipe_dock]
        self.resizeDocks(docks, [height] * len(docks), Qt.Orientation.Vertical)

    # ── Status bar ──────────────────────────────────────────────────

    def _build_status_bar(self) -> None:
        bar = self.statusBar()
        bar.showMessage("Ready")

        self._units_label = QLabel("units \u2014")
        self._elem_label = QLabel("elem \u2014")
        self._coord_label = QLabel("x \u2014  y \u2014  z \u2014")
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setFixedWidth(160)
        self._progress.setVisible(False)

        for widget in (self._units_label, self._elem_label, self._coord_label, self._progress):
            bar.addPermanentWidget(widget)

    # ── View decoration (view cube + cursor readout) ────────────────

    def _decorate_view(self) -> None:
        """Add the orientation (view) cube and prepare the cursor readout.

        PyVista's ``add_camera_orientation_widget`` provides the interactive
        view cube.  The *interaction* extras -- ``enable_terrain_style`` (which
        keeps the model's Z axis vertical while orbiting) and
        ``track_mouse_position`` (which feeds the status-bar coordinates) --
        both need a live interactor, so they are installed from
        :meth:`showEvent`: pyvista raises ``RuntimeError`` for an off-screen or
        never-shown plotter.
        """
        self._interactor.add_camera_orientation_widget()
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setInterval(_CURSOR_POLL_MS)
        self._cursor_timer.timeout.connect(self._update_cursor_position)

    def showEvent(self, event):
        """Install the interactive extras once the viewport is live.

        ``enable_terrain_style`` keeps the viewport's Z axis vertical (terrain
        interaction); ``track_mouse_position`` feeds the status-bar cursor
        coordinates.  Both require an interactive viewport, so they are
        best-effort: an off-screen or never-shown plotter logs a warning and
        the window still opens (off-screen rendering is a legitimate use).
        """
        super().showEvent(event)
        self._size_bottom_docks()
        if self._interaction_enabled:
            return
        self._interaction_enabled = True
        try:
            self._interactor.enable_terrain_style()
            self._interactor.track_mouse_position()
        except RuntimeError:
            self.log(
                "Interactive view controls unavailable (non-interactive viewport).",
                "warn",
            )
            return
        self._cursor_timer.start()

    def _update_cursor_position(self) -> None:
        """Refresh the status-bar coordinate readout from the last mouse event.

        Picking legitimately misses when the cursor is outside the render
        window or over no geometry, in which case the readout simply keeps its
        previous value -- so a failed pick is a no-op, not an error.
        """
        try:
            point = self._interactor.pick_mouse_position()
        except Exception:
            return
        if point is None:
            return
        self._coord_label.setText(
            f"x {float(point[0]):.4g}  y {float(point[1]):.4g}  z {float(point[2]):.4g}"
        )

    # ── Model display ───────────────────────────────────────────────

    def _merge_hidden(self, view_selection: Any) -> Any:
        """Layer the hidden-element set over *view_selection* as exclusions.

        ``None`` when there is nothing to hide and no view lens.  Otherwise a
        ``Selection`` whose ``exclude_element_ids`` carries the hidden ids over
        any lens already on *view_selection*.  A node-scoped lens is returned
        unchanged: its joint expansion does not consult ``exclude_element_ids``
        (a known limitation), so folding the exclusions in would be ignored.
        "Node-scoped" means any lens that expands from joints — ``element_types``
        naming ``Node``, a ``node_ids`` / ``exclude_node_ids`` / ``constraints``
        criterion, or a type exclusion that leaves nodes in — not only one whose
        ``element_types`` names ``Node``.
        """
        if not self._hidden_elem_ids and view_selection is None:
            return None
        from dataclasses import replace

        from ..model.selection import Selection

        base = view_selection if view_selection is not None else Selection()
        if base._selects_nodes_explicitly():
            return view_selection
        excluded = set(base.exclude_element_ids or ())
        excluded.update(self._hidden_elem_ids)
        return replace(base, exclude_element_ids=sorted(excluded))

    def _warn_if_view_node_scoped(self) -> None:
        """Warn when hide / isolate cannot take visible effect on the active view.

        A node-scoped lens is the one case :meth:`_merge_hidden` returns
        unchanged — its joint expansion does not consult ``exclude_element_ids``
        — so the freshly hidden elements remain visible until the user switches
        to a view whose selection is not node-scoped.  "Node-scoped" means any
        lens that expands from joints — ``element_types`` naming ``Node``, a
        ``node_ids`` / ``exclude_node_ids`` / ``constraints`` criterion, or a
        type exclusion that leaves nodes in — not only one whose
        ``element_types`` names ``Node``.
        """
        view = self._views.active
        selection = view.selection if view is not None else None
        if selection is not None and selection._selects_nodes_explicitly():
            self.log(
                "This view's selection is node-scoped, so hidden elements remain visible here.",
                "warn",
            )

    def show_model(
        self,
        model: Any,
        color_by_section: bool = True,
        *,
        collapse_to_parents: bool = False,
        selection: Any = None,
        reset_view: bool = True,
        rebuild_tree: bool = True,
    ) -> None:
        """Render *model* into the embedded viewport.

        Args:
            model: A ``SAPModelData``, a ``MeshModel`` or an ``AnalysisBuilder``.
            color_by_section: Colour elements by section name.
            collapse_to_parents: Draw the unsplit members rather than their
                split sub-elements.  The GUI reaches the drawn members through
                the **Unprocessed** view instead, so this is ``False`` here and
                remains for callers that want the collapsed render directly.
            selection: Optional ``Selection`` narrowing what is drawn — a view's
                lens, applied without copying the model.
            reset_view: Reset the camera first.  ``False`` keeps the current
                view, which is what switching views wants.
            rebuild_tree: Rebuild the Model Tree for *model*.  ``False`` for a
                pure display change — the same model, so rebuilding would only
                throw away the user's selection and expansion.
        """
        from ..model.mesh_model import MeshModel
        from ..model.sap_data import SAPModelData
        from ..plotting.viewer import ModelViewer

        selection = self._merge_hidden(selection)

        camera = None
        if not reset_view:
            with contextlib.suppress(Exception):
                camera = self._interactor.camera_position

        # A new model replaces the previous scene outright -- the backend
        # *appends* actors, so without this the old geometry would linger
        # behind the new one.  A results overlay belongs to *one* view's
        # geometry, so a *view switch* remembers it (redrawn for the new case
        # below) while a fresh model load resets it upstream in
        # :meth:`_remember_source`.
        results_intent = {
            "deformed": self._actions["results.deformed"].isChecked(),
            "animate": self._actions["results.animate"].isChecked(),
            "forces": self._actions["results.forces"].isChecked(),
        }
        self._stop_animation()
        self._backend.clear()

        if isinstance(model, MeshModel):
            viewer = ModelViewer(
                mesh_model=model,
                backend=self._backend,
                collapse_to_parents=collapse_to_parents,
                selection=selection,
            )
        elif isinstance(model, SAPModelData):
            viewer = ModelViewer(
                model_data=model,
                backend=self._backend,
                collapse_to_parents=collapse_to_parents,
                selection=selection,
            )
        else:
            viewer = ModelViewer(
                builder=model,
                backend=self._backend,
                collapse_to_parents=collapse_to_parents,
                selection=selection,
            )

        viewer.show_model(
            show_nodes=self._actions["view.show_nodes"].isChecked(),
            show_frames=self._actions["view.show_frames"].isChecked(),
            show_shells=self._actions["view.show_shells"].isChecked(),
            color_by_section=color_by_section,
            shell_opacity=float(self._shell_opacity.value()),
            shrink=float(self._shrink.value()),
            show_restraints=self._actions["view.show_restraints"].isChecked(),
            restraint_style=self._display.restraint_style,
            show_extruded=self._actions["view.show_extruded"].isChecked(),
            extruded_detail=self._display.extruded_detail,
        )
        self._viewer = viewer
        self._model = model
        self._selection_index = SelectionIndex.from_viewer(viewer)
        # The Inspector describes one object, but support conditions live on the
        # model (keyed by node id), so it is given the parsed source — which keeps
        # its restraints and constraint assignments across every view — rather
        # than the model currently drawn.  With no store (an archive) it falls
        # back to the displayed model, which simply has neither.
        self._inspector.set_source_model(self._store.raw() if self._store is not None else model)
        if rebuild_tree:
            self._tree_model.set_model(self._tree_model_source(model), self._views.views())
        if camera is None:
            self._interactor.reset_camera()
        else:
            self._interactor.camera_position = camera
        self._update_units_label()
        self._set_model_actions_enabled(self._store is not None)
        self._set_analysis_actions_enabled()
        self._set_view_actions_enabled()
        self._reapply_results_overlay(results_intent)
        self.log("Displayed model geometry.")

    def _tree_model_source(self, fallback: Any) -> Any:
        """The model the Model Tree presents — the mesh, then raw, then *fallback*.

        A results view's source is a display model rebuilt from the archive: it
        carries geometry and section names but no supports, materials, groups or
        loads.  The tree should present the *real* model, so it uses the store's
        preprocessed (mesh) model when one exists, the parsed (raw) model
        otherwise, and the view's source only with no store at all (a standalone
        results archive).
        """
        if self._store is not None:
            preprocessed = self._store.preprocessed()
            if preprocessed is not None:
                return preprocessed
            raw = self._store.raw()
            if raw is not None:
                return raw
        return fallback

    def _reset_session_display(self) -> None:
        """Reset the display and results toggles for a freshly opened model.

        Called when a new model replaces the previous one — never when switching
        views of the same model, where the user's visibility and overlay choices
        must survive (see :meth:`_reapply_results_overlay`).
        """
        for key in (
            "view.show_nodes",
            "view.show_frames",
            "view.show_shells",
            "view.show_restraints",
        ):
            self._set_toggle_checked(key, True)
        # Extruded shapes is a user preference, not a per-model default: it
        # keeps the state the settings file loaded (off unless the user asked).
        self._set_toggle_checked("view.show_extruded", self._display.show_extruded)
        self._stop_animation()
        self._set_toggle_checked("results.animate", False)
        self._set_toggle_checked("results.deformed", False)
        self._sync_force_toggles(False)

    def _reapply_results_overlay(self, intent: dict) -> None:
        """Redraw the results overlay the user had on, for the newly shown view.

        :meth:`show_model` clears the scene, so an overlay that was on must be
        re-read from the new view's case (and the animation restarted) rather
        than silently dropped.  A view that cannot supply the data is skipped —
        its action was just disabled by :meth:`_set_view_actions_enabled`.
        """
        deformed = self._actions["results.deformed"]
        if intent.get("deformed") and deformed.isEnabled():
            self._on_deformed_toggled(True)
        animate = self._actions["results.animate"]
        if intent.get("animate") and animate.isEnabled():
            self._on_animate_toggled(True)
        if intent.get("forces") and self._actions["results.forces"].isEnabled():
            self._on_forces_toggled(True)

    def _preferred_open_view(self, saved_key: Optional[str]) -> Optional[Any]:
        """The view a reopened project shows first: geometry, not a results view.

        A saved ``active_view`` is often a results view (the last mode the user
        animated); reopening straight into it hides the model's supports, groups
        and loads.  Prefer the geometry views — meshed/processed first, then
        unprocessed — and fall back to the saved view (or ``None``).
        """
        for key in ("meshed", "processed", "unprocessed"):
            view = self._views.get(key)
            if view is not None:
                return view
        return self._views.get(saved_key) if saved_key else None

    @staticmethod
    def _backfill_mesh_load_metadata(mesh: Any, sap: Any) -> None:
        """Copy SAP load definitions onto a disk-loaded mesh that lacks them.

        A project saved before ``MeshModel`` carried ``load_cases`` /
        ``load_combinations`` / ``response_spectrum_functions`` holds a mesh
        with empty dicts.  Preprocessing never changes those definitions, so
        backfill them from the raw model at open time — the Model Tree then
        shows Load Cases, Load Combinations and Response Spectra without a
        re-save.
        """
        for attr in ("load_cases", "load_combinations", "response_spectrum_functions"):
            source = getattr(sap, attr, None)
            if source and not getattr(mesh, attr, None):
                setattr(mesh, attr, source)

    def _update_units_label(self) -> None:
        """Show the model's unit system in the status bar."""
        from ..io.model_store import model_header

        if self._model is None:
            self._units_label.setText("units \u2014")
            return
        self._units_label.setText(model_header(self._model).units_label())

    # ── Logging ─────────────────────────────────────────────────────

    def log(self, message: str, level: str = "info") -> None:
        """Append a line to the message log (``info`` | ``warn`` | ``error``)."""
        log_widget = getattr(self, "_message_log", None)
        if log_widget is not None:
            log_widget.log(message, level)

    def _on_tree_selection(self, current, _previous=None) -> None:
        """Show the selected entity in the inspector and highlight it below.

        Two kinds of row reach here: a **view** (switch the scene and report the
        view's counts) and a **model entity** (inspect it and highlight it).  The
        viewport half rides on ``ModelViewer.highlight_elements`` /
        ``highlight_nodes``, which resolve SAP labels back to geometry from the
        *same* extracted geometry the display was built from -- so the reverse
        (tree -> viewport) direction needs no cell-id map, only the pick
        direction does (``docs/gui_roadmap.md`` design rule 7).
        """
        entity = current.data(Qt.ItemDataRole.UserRole) if current.isValid() else None
        if isinstance(entity, View):
            self._activate_view(entity)
            return
        self._inspector.show_object(entity)
        self._highlight_entity(entity)

    def _activate_view(self, view: View) -> None:
        """Display the scene *view* names, without disturbing the tree.

        The geometry lives in the registry, so a view carries only its counts and
        provenance; the camera is kept, because switching between views of the
        same model should not move the user's viewpoint.
        """
        self._show_view(view)

    def _show_view(self, view: View, *, rebuild_tree: bool = False) -> None:
        """Render *view* — its source, through its selection — and report it.

        A geometry or results view draws the model into the viewport; a table or
        figure view raises its own central page instead, because those payloads
        are not model geometry.

        Args:
            view: The view to display.
            rebuild_tree: Rebuild the Model Tree.  Needed when the view's *name*
                changed (the row's label is the name); a rebuild invalidates the
                current index, so it is skipped for a plain switch — otherwise Qt
                reports an empty selection and the Inspector is cleared again.
        """
        self._views.set_active(view.key)
        if view.kind in (GEOMETRY, RESULTS):
            # A node-scoped lens ignores the hidden-element exclusions, so the
            # hidden elements stay visible until a non-node-scoped view is shown.
            if self._hidden_elem_ids:
                self._warn_if_view_node_scoped()
            source = self._views.source(view.key)
            if source is None:
                return
            self._stack.setCurrentWidget(self._viewport_container)
            self.show_model(
                source,
                reset_view=False,
                rebuild_tree=rebuild_tree,
                selection=view.selection,
            )
        elif view.kind == TABLE:
            if rebuild_tree:
                self._rebuild_tree()
            self._show_table_view(view)
        elif view.kind == FIGURE:
            if rebuild_tree:
                self._rebuild_tree()
            self._show_figure_view(view)
        else:
            return
        self._inspector.show_object(self._views.get(view.key))
        self.log(f"Showing view: {view.name}.")

    def _rebuild_tree(self) -> None:
        """Refresh the Model Tree for the current model and registered views.

        ``show_model`` repopulates the tree as a side effect of drawing geometry,
        so a geometry or results view keeps it current.  A table or figure view
        raises its own central page and never calls ``show_model``, which left
        every table/figure registered by a recipe run invisible in the **Views**
        group.  This repopulates the tree without touching the viewport.
        """
        self._tree_model.set_model(self._tree_model_source(self._model), self._views.views())

    def _show_table_view(self, view: View) -> None:
        """Render a ``table`` view — a check's findings as a read-only grid.

        The cells are pre-formatted by the verb that produced the
        :class:`~fea_toolkit.workflow.steps.Table`, so this is a renderer: it
        lays the payload out verbatim and never reformats a value.
        """
        from qtpy.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem

        table = self._views.table(view.key)
        if table is None:
            return
        if self._table_page is None:
            self._table_page = QWidget(self)
            layout = QVBoxLayout(self._table_page)
            layout.setContentsMargins(6, 6, 6, 6)
            self._table_widget = QTableWidget(self._table_page)
            self._table_widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self._table_widget.horizontalHeader().setSectionResizeMode(
                QHeaderView.ResizeMode.Stretch
            )
            layout.addWidget(self._table_widget)
            self._stack.addWidget(self._table_page)

        self._table_widget.clear()
        self._table_widget.setColumnCount(len(table.columns))
        self._table_widget.setRowCount(len(table.rows))
        self._table_widget.setHorizontalHeaderLabels([str(c) for c in table.columns])
        self._table_widget.setVerticalHeaderLabels([str(i + 1) for i in range(len(table.rows))])
        for row, cells in enumerate(table.rows):
            for column, cell in enumerate(cells):
                self._table_widget.setItem(row, column, QTableWidgetItem(str(cell)))
        self._stack.setCurrentWidget(self._table_page)

    def _show_figure_view(self, view: View) -> None:
        """Render a ``figure`` view — a chart's Matplotlib figure on a canvas.

        Each figure is embedded in **exactly one** canvas, created lazily and
        cached by figure key.  A Matplotlib figure must not be re-parented across
        multiple canvases: wrapping the same figure in a new
        :class:`~matplotlib.backends.backend_qtagg.FigureCanvasQTAgg` on every
        switch re-runs its layout each time, so the legend and markers visibly
        inflate.  The page is shared; only the canvas widget it shows changes.
        """
        figure = self._views.figure(view.key)
        if figure is None:
            return
        if self._figure_page is None:
            self._figure_page = QWidget(self)
            self._figure_layout = QVBoxLayout(self._figure_page)
            self._figure_layout.setContentsMargins(0, 0, 0, 0)
            self._stack.addWidget(self._figure_page)

        # Detach the canvas currently on the page without deleting it — a cached
        # canvas stays alive and is re-attached when its figure is shown again.
        while self._figure_layout.count():
            item = self._figure_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()

        canvas = self._figure_canvases.get(view.key)
        if canvas is None:
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

            canvas = FigureCanvasQTAgg(figure)
            self._figure_canvases[view.key] = canvas
        self._figure_layout.addWidget(canvas)
        canvas.show()
        canvas.draw()
        self._stack.setCurrentWidget(self._figure_page)

    # ── Derived views (Edit menu) ────────────────────────────────────

    def _on_duplicate_view(self) -> None:
        """**Edit ▸ Duplicate view**: copy the active view and narrow it.

        The duplicate is a *lens on a lens* — it shares its parent's geometry and
        adds a ``Selection`` — so the dialog asks for that selection straight
        away: an unfiltered duplicate would just be the parent again.
        """
        parent = self._views.active
        if parent is None:
            return
        from .views.selection_dialog import SelectionDialog

        selection = SelectionDialog.edit(parent.selection, self)
        if selection is None:  # cancelled
            return
        key = self._next_derived_key(parent.key)
        name = f"{parent.name} \u00b7 {selection.to_string() or 'unfiltered'}"
        view = self._views.add_derived(key, name, parent.key, selection)
        if view is None:
            return
        self._show_view(view, rebuild_tree=True)
        self.log(f"Added view: {name}.")

    def _on_edit_view_selection(self) -> None:
        """**Edit ▸ Edit view selection…**: re-filter the active derived view."""
        view = self._views.active
        if view is None or view.parent is None:
            return
        parent = self._views.get(view.parent)
        from .views.selection_dialog import SelectionDialog

        selection = SelectionDialog.edit(view.selection, self)
        if selection is None:  # cancelled
            return
        stem = parent.name if parent is not None else view.name
        name = f"{stem} \u00b7 {selection.to_string() or 'unfiltered'}"
        updated = self._views.add_derived(view.key, name, view.parent, selection)
        if updated is None:
            return
        self._show_view(updated, rebuild_tree=True)
        self.log(f"Updated view: {name}.")

    def _next_derived_key(self, parent_key: str) -> str:
        """A derived-view key no registered view is using."""
        index = 1
        while self._views.get(f"{parent_key}:{index}") is not None:
            index += 1
        return f"{parent_key}:{index}"

    def _set_view_actions_enabled(self) -> None:
        """Enable duplication / selection editing to match the active view."""
        active = self._views.active
        self._actions["edit.duplicate_view"].setEnabled(active is not None)
        self._actions["edit.edit_selection"].setEnabled(
            active is not None and active.kind == GEOMETRY and active.parent is not None
        )
        # A deformed shape needs a results view that carries displacement:
        # a pressed button that could only report "nothing to draw" is worse
        # than a disabled one that is greyed until it can work.
        repository = self._views.results(active.key) if active is not None else None
        case = _case_of(active)
        self._actions["results.deformed"].setEnabled(
            repository is not None and bool(case) and repository.has_displacements(case)
        )
        # Animation animates the deformed shape, so it is enabled exactly when
        # the deformed overlay is (a displacement field is its only input).
        self._actions["results.animate"].setEnabled(
            repository is not None and bool(case) and repository.has_displacements(case)
        )
        # The same rule for the flag diagram, on end forces — and the quantity
        # selector is greyed with it, so it cannot look like it is choosing
        # something the archive has none of.
        has_forces = repository is not None and bool(case) and repository.has_forces(case)
        self._actions["results.forces"].setEnabled(has_forces)
        self._actions["view.show_forces"].setEnabled(has_forces)
        self._force_quantity.setEnabled(has_forces)
        # Saving needs a result to write — the active view decides.
        self._actions["file.save_results"].setEnabled(repository is not None)

    def _highlight_entity(self, entity: Any) -> None:
        """Highlight *entity* in the viewport, replacing the previous highlight.

        Entities with no geometry of their own (materials, sections, load
        definitions) simply clear the highlight.
        """
        if self._viewer is None:
            return
        self._viewer.clear_highlights()
        attr, value = _entity_identity(entity)
        if attr is None:
            return
        if attr == "node_id":
            self._viewer.highlight_nodes([value], color=_SELECT_NODE_COLOR)
        elif attr == "area_id":
            self._viewer.highlight_elements(area_ids=[value], color=_SELECT_COLOR)
        else:
            self._viewer.highlight_elements(frame_ids=[value], color=_SELECT_COLOR)

    def _on_clear_highlights(self) -> None:
        """Drop the current selection highlight from the viewport."""
        if self._viewer is not None:
            self._viewer.clear_highlights()

    # ── Results overlays (Results menu) ─────────────────────────────

    def _on_deformed_toggled(self, checked: bool) -> None:
        """**Results ▸ Deformed shape**: overlay the active case's deformation.

        The amplification is a *display* choice: the archive's displacements are
        read in model units and never modified, only drawn at the toolbar scale.

        Args:
            checked: Whether the overlay should now be drawn.
        """
        if not checked:
            self._clear_deformed()
            return

        displacements = self._active_displacements()
        if not displacements:
            self.log("No displacement data in this view — nothing to deform.", "error")
            self._set_toggle_checked("results.deformed", False)
            return

        self._viewer.overlay_deformed(
            displacements,
            scale=None,
            auto_fraction=self._deformed_scale.value() / 100.0,
        )
        self._viewer.show()
        self.log(f"Deformed shape: {self._deformed_scale.value():g}% of model.")

    def _on_deformed_scale_changed(self, _value: float) -> None:
        """Re-draw the deformed overlay after its size changed.

        Only when it is already on — changing the knob must not *start*
        drawing, which would make the spin box a second way to trigger the
        action.  The deformed shape and the flag diagram now have **separate**
        knobs, because an amplification and a length-per-force factor are not
        the same number; both are expressed as a percentage of the model
        diagonal so neither needs to know the model's units.
        """
        if self._actions["results.deformed"].isChecked():
            self._clear_deformed()
            self._on_deformed_toggled(True)

    def _on_force_scale_changed(self, _value: float) -> None:
        """Re-draw the flag diagram after its size changed (same rule: no start)."""
        if self._actions["results.forces"].isChecked():
            self._clear_forces()
            self._on_forces_toggled(True)

    def _on_clear_results(self) -> None:
        """**Results ▸ Clear results**: remove every results overlay."""
        self._stop_animation()
        self._set_toggle_checked("results.animate", False)
        self._clear_deformed()
        self._set_toggle_checked("results.deformed", False)
        self._clear_forces()
        self._sync_force_toggles(False)
        self.log("Cleared the results overlay.")

    def _active_results_source(self) -> tuple[Any, str]:
        """``(repository, case)`` for the active view — ``(None, "")`` without one.

        Every results accessor needs the same pair, and the force overlay needs
        it twice: once for the entries and once for how the archive records
        them.
        """
        view = self._views.active
        repository = self._views.results(view.key) if view is not None else None
        case = _case_of(view) or ""
        return repository, case

    def _active_displacements(self) -> dict:
        """``{node_id: (dx, dy, dz)}`` for the active results view, else ``{}``."""
        repository, case = self._active_results_source()
        if repository is None or not case:
            return {}
        return repository.nodal_displacements(case)

    def _clear_deformed(self) -> None:
        """Remove the deformed overlay, leaving the model itself drawn."""
        if self._viewer is not None:
            self._viewer.clear_deformed()

    def _pushover_step_frames(self, repository: Any, case: str) -> list:
        """Per-step displacement fields for a pushover case, or ``[]`` otherwise.

        A pushover results view carries its full
        ``pushover/<direction>/<load_type>/node_disp_*`` history (threaded through
        :meth:`_show_pushover_result`); this turns it into ``[{node_id: (dx, dy,
        dz)}]`` per recorded step so the animation can step through it.
        Non-pushover cases return ``[]``.
        """
        import numpy as np

        if not case.startswith("Pushover"):
            return []
        raw = repository.raw()
        rest = case[len("Pushover") :]
        if len(rest) >= 4 and rest[2] == "-":
            direction, load_type = rest[:2], rest[3:]
        else:
            # Legacy case label without a load type.
            direction, load_type = rest, "uniform"
        dx = raw.get(f"pushover/{direction}/{load_type}/node_disp_x")
        tags = raw.get(f"pushover/{direction}/{load_type}/node_tag")
        if dx is None or tags is None:
            return []
        dy = raw.get(f"pushover/{direction}/{load_type}/node_disp_y")
        dz = raw.get(f"pushover/{direction}/{load_type}/node_disp_z")
        # Map the sorted pushover node tags back to node ids via the geometry.
        geo_tags = raw.get("node_tag")
        geo_ids = raw.get("node_sap_id")
        tag_to_id: dict = {}
        if geo_tags is not None:
            for i, tag in enumerate(np.asarray(geo_tags)):
                node_id = str(np.asarray(geo_ids)[i]) if geo_ids is not None else str(tag)
                tag_to_id[int(tag)] = node_id
        n_step = dx.shape[0]
        frames = []
        for k in range(n_step):
            field: dict = {}
            for j, tag in enumerate(np.asarray(tags)):
                node_id = tag_to_id.get(int(tag), str(tag))
                field[node_id] = np.array(
                    [
                        float(dx[k, j]),
                        float(dy[k, j]) if dy is not None else 0.0,
                        float(dz[k, j]) if dz is not None else 0.0,
                    ],
                    dtype=float,
                )
            frames.append(field)
        return frames

    def _on_animate_toggled(self, checked: bool) -> None:
        """**Results ▸ Animate deformed shape**: animate the active shape.

        A single displacement field (a mode shape, a static/RS field) oscillates at
        a sinusoidally varying scale; a pushover view steps through its recorded
        push history instead.

        Args:
            checked: Whether the animation should run.
        """
        if not checked:
            self._stop_animation()
            # Leave the shape at rest — the final step for a pushover history, the
            # un-animated scale for a single field — not frozen mid-animation, but
            # only while the deformed-shape toggle is on; otherwise clear it.
            if not self._actions["results.deformed"].isChecked():
                self._clear_deformed()
                return
            fraction = self._deformed_scale.value() / 100.0
            if self._anim_step_mode and self._anim_frames:
                self._viewer.overlay_deformed(
                    self._anim_frames[-1], scale=None, auto_fraction=fraction
                )
                self._viewer.show()
            elif self._anim_displacements:
                self._viewer.overlay_deformed(
                    self._anim_displacements, scale=None, auto_fraction=fraction
                )
                self._viewer.show()
            return

        displacements = self._active_displacements()
        if not displacements:
            self.log("No displacement data in this view — nothing to animate.", "error")
            self._set_toggle_checked("results.animate", False)
            return

        repository, case = self._active_results_source()
        frames = (
            self._pushover_step_frames(repository, case) if repository is not None and case else []
        )
        fraction = self._deformed_scale.value() / 100.0
        if frames:
            self._anim_step_mode = True
            self._anim_frames = frames
            self._anim_frame_index = 0
            self._anim_base_scale = self._viewer.deformed_amplification(frames[-1], fraction)
        else:
            self._anim_step_mode = False
            self._anim_frames = []
            self._viewer.overlay_deformed(displacements, scale=None, auto_fraction=fraction)
            self._viewer.show()
            self._anim_displacements = displacements
            self._anim_base_scale = self._viewer.deformed_amplification(displacements, fraction)
        self._anim_phase = 0.0
        if self._anim_timer is None:
            self._anim_timer = QTimer(self)
            self._anim_timer.setInterval(40)
            self._anim_timer.timeout.connect(self._anim_tick)
        self._anim_timer.start()

    def _anim_tick(self) -> None:
        """Redraw the animated shape at the next phase (oscillate) or step."""
        if self._anim_step_mode and self._anim_frames:
            frame = self._anim_frames[self._anim_frame_index]
            self._anim_frame_index = (self._anim_frame_index + 1) % len(self._anim_frames)
            self._viewer.overlay_deformed(frame, scale=self._anim_base_scale)
            self._viewer.show()
            return
        import math

        self._anim_phase += 0.25  # ≈ 6.25 rad/s at 40 ms → ~1 Hz
        scale = self._anim_base_scale * math.sin(self._anim_phase)
        self._viewer.overlay_deformed(self._anim_displacements, scale=scale)
        self._viewer.show()

    def _stop_animation(self) -> None:
        """Stop the deformed-shape animation timer without re-drawing."""
        if self._anim_timer is not None:
            self._anim_timer.stop()

    def _sync_force_toggles(self, checked: bool) -> None:
        """Keep the two force-diagram toggles in lockstep.

        ``Results ▸ Force diagrams`` and ``View ▸ Display ▸ Show force
        diagrams`` are one operation with two menu spellings, so neither can
        drift out of step with the other (see :meth:`_set_toggle_checked`,
        which sets a toggle without re-entering its handler).

        Args:
            checked: The state both toggles should hold.
        """
        self._set_toggle_checked("results.forces", checked)
        self._set_toggle_checked("view.show_forces", checked)

    def _on_forces_toggled(self, checked: bool) -> None:
        """**Results ▸ Force diagrams**: flag the active case's end forces.

        Also the handler for **View ▸ Display ▸ Show force diagrams**, so the
        two spellings of the one toggle always agree.

        The chosen quantity is a *label*: the selector shows SAP's local-DOF
        names and this reads the schema key underneath, so ``"M3"`` becomes
        ``"Mz"`` (see
        :data:`~fea_toolkit.model.sap_data.FORCE_QUANTITY_LABELS`).  As with the
        deformed shape the amplification is a *display* choice — the archive's
        forces are read in model units and never modified.

        Args:
            checked: Whether the diagram should now be drawn.
        """
        self._sync_force_toggles(checked)
        if not checked:
            self._clear_forces()
            return

        forces = self._active_element_forces()
        if not forces:
            self.log("No element forces in this view — nothing to draw.", "error")
            self._sync_force_toggles(False)
            return

        from ..model.sap_data import FORCE_QUANTITY_LABELS

        label = self._force_quantity.currentText()
        self._viewer.overlay_forces(
            forces,
            quantity=FORCE_QUANTITY_LABELS.get(label, label),
            use_local=self._active_forces_are_local(),
            scale_factor=None,
            auto_fraction=self._force_scale.value() / 100.0,
        )
        self._viewer.show()
        self.log(f"Force diagram: {label} at {self._force_scale.value():g}% of model.")

    def _on_force_quantity_changed(self, _index: int) -> None:
        """Re-draw the flag diagram after the quantity changed.

        Only when it is already on — choosing a quantity must not *start*
        drawing, the same rule the scale box follows.
        """
        if self._actions["results.forces"].isChecked():
            self._clear_forces()
            self._on_forces_toggled(True)

    def _active_element_forces(self) -> dict:
        """``{elem_id: {key: value}}`` for the active results view, else ``{}``."""
        repository, case = self._active_results_source()
        if repository is None or not case:
            return {}
        return repository.element_forces(case)

    def _active_forces_are_local(self) -> bool:
        """Whether the active view's archive records its end forces element-local.

        The flag diagram draws *local* component values, so it has to know
        before it reads them — see :meth:`ResultsRepository.forces_are_local`.
        """
        repository, _case = self._active_results_source()
        return repository.forces_are_local() if repository is not None else False

    def _clear_forces(self) -> None:
        """Remove the force-flag overlay, leaving the model itself drawn."""
        if self._viewer is not None:
            self._viewer.clear_forces()

    def _set_toggle_checked(self, key: str, checked: bool) -> None:
        """Set a results toggle without re-entering its handler.

        Args:
            key: Action key, e.g. ``"results.deformed"``.
            checked: The state to set.
        """
        action = self._actions.get(key)
        if action is None or action.isChecked() == checked:
            return
        action.blockSignals(True)
        action.setChecked(checked)
        action.blockSignals(False)

    def _on_show_nodes(self, checked: bool) -> None:
        """Show or hide the node-marker overlay."""
        self._backend.set_category_visible("nodes", checked)

    def _on_show_shells(self, checked: bool) -> None:
        """Show or hide the area-element overlay."""
        self._backend.set_category_visible("shells", checked)

    def _on_show_frames(self, checked: bool) -> None:
        """Show or hide the frame-element overlay."""
        self._backend.set_category_visible("frames", checked)

    def _on_show_extruded(self, checked: bool) -> None:
        """Toggle extruded-solid frames; a geometry change, so re-render."""
        from dataclasses import replace

        self._display = replace(self._display, show_extruded=checked)
        self._refresh_display()

    def _on_show_restraints(self, checked: bool) -> None:
        """Show or hide the support symbols."""
        self._backend.set_category_visible("restraints", checked)

    def _on_hide_selected(self) -> None:
        """**View ▸ Display ▸ Hide selected**: hide the selected elements.

        Folds the selected frame/area ids into the persistent hidden set, then
        re-renders in place.  A node-only selection is a no-op — a lone joint has
        no surface to hide, and ``Selection.resolve_connected`` drops its nodes
        automatically once their elements are hidden.
        """
        refs = self._selected_element_refs()
        if not refs:
            self.log("Nothing selected to hide.", "warn")
            return
        self._hidden_elem_ids.update(refs)
        self._refresh_display()
        self.log(f"Hid {len(refs)} element(s).")
        self._warn_if_view_node_scoped()

    def _on_show_all(self) -> None:
        """**View ▸ Display ▸ Show all**: restore every hidden element."""
        if not self._hidden_elem_ids:
            self.log("Nothing is hidden.", "warn")
            return
        self._hidden_elem_ids.clear()
        self._refresh_display()
        self.log("Restored all hidden elements.")

    def _on_reset_view(self) -> None:
        """**View ▸ Reset view**: return to a clean view and forget the saved state.

        Resets the camera, restores every hidden element and clears the saved
        per-model state, so a strange saved view cannot follow the model to its
        next open.  Display *preferences* (opacity, shrink, …) are untouched —
        those are user settings, not this model's state.
        """
        if self._interactor is not None:
            with contextlib.suppress(Exception):
                self._interactor.reset_camera()
        if self._hidden_elem_ids:
            self._hidden_elem_ids.clear()
            self._refresh_display()
        if self._source_label:
            for note in clear_view_state(self._source_label):
                self.log(note, "warn")
        self._reset_view_baseline = (self._current_camera(), frozenset(self._hidden_elem_ids))
        self.log("Reset the view to defaults.")

    def _all_element_ids(self) -> set[str]:
        """Every ``Frame:``/``Area:`` id the displayed model can draw.

        Ids are type-qualified so a frame and an area sharing a SAP label stay
        distinct — ``Selection.exclude_element_ids`` is a flat list that cannot
        tell them apart on its own.  Inactive (split/meshed) parents are
        skipped, so an id that is never drawn cannot leak into the hidden set.
        """
        model = self._model
        ids: set[str] = set()
        for etype, attr in (("Frame", "frame_elements"), ("Area", "area_elements")):
            for key, elem in (getattr(model, attr, {}) or {}).items():
                if not getattr(elem, "inactive", False):
                    ids.add(f"{etype}:{key}")
        return ids

    def _selected_element_refs(self) -> set[str]:
        """Type-qualified element ids for the current tree selection.

        Unlike :meth:`current_selection` — which collapses frames and areas into
        one ``element_ids`` list — this keeps the type, so a frame and an area
        that share a SAP label are hidden/isolated independently.
        """
        frames, areas, _nodes = self._selected_entities()
        return {f"Frame:{fid}" for fid in frames} | {f"Area:{aid}" for aid in areas}

    def _on_isolate_selected(self) -> None:
        """**View ▸ Display ▸ Isolate selected**: hide everything but the selection.

        The inverse of ``Hide selected``: the hidden set is *replaced* by every
        drawable element id minus the selected ones, so only the selection (and
        its shared joints) remains.  ``Show all`` restores the full model.
        """
        keep = self._selected_element_refs()
        if not keep:
            self.log("Nothing selected to isolate.", "warn")
            return
        self._hidden_elem_ids = self._all_element_ids() - keep
        self._refresh_display()
        self.log(f"Isolated {len(keep)} element(s).")
        self._warn_if_view_node_scoped()

    def _on_shell_opacity_changed(self, value: float) -> None:
        """Apply the new shell opacity to the drawn actors, in place.

        No re-render: opacity is a render property, so this stays responsive.
        A view rendered *later* takes the value from the spin box, so the setting
        survives a view switch.
        """
        if self._viewer is not None:
            self._viewer.set_shell_opacity(float(value))

    def _on_shrink_changed(self, _value: float) -> None:
        """Re-draw with the new shrink factor.

        Unlike opacity this changes the *geometry*, so it needs a re-render —
        done in place by :meth:`_refresh_display`, which keeps the camera and the
        tree selection.
        """
        self._refresh_display()

    def _refresh_display(self) -> None:
        """Re-render the active view in place after a display-only change.

        The source, the camera and the Model Tree are all kept: this is the same
        model drawn differently, so resetting any of them would lose the user's
        place.  A no-op when no view is active — the controls are usable before
        anything is open.
        """
        active = self._views.active
        source = self._views.source(active.key) if active is not None else None
        if source is None:
            return
        self.show_model(
            source,
            reset_view=False,
            rebuild_tree=False,
            selection=active.selection,
        )

    # ── Preprocessing (Model menu) ───────────────────────────────────

    def _on_mesh_dialog(self) -> None:
        """**Model ▸ Mesh…**: compose the ``mesh`` step in a dialog, then run it.

        The Model-menu entry is a preset over the recipe: the dialog composes the
        meshing step the way the Recipe panel's Add does, and accepting appends
        the step and runs the recipe — so one click still preprocesses, while
        the step stays visible, editable and saveable in the Recipe panel instead
        of hidden in a handler.
        """
        from ..workflow import Step
        from .views.step_dialog import StepDialog

        if self._store is None:
            self.log("Open a SAP2000 model before preprocessing.", "warn")
            return
        edited = StepDialog.edit(Step(verb="mesh"), self)
        if edited is None:
            return
        if edited.params.get("create_shells", True):
            label = "Splitting elements at joints and meshing areas"
        else:
            label = "Splitting elements at joints"
        self._preset_step(edited, label)

    def _preset_step(self, step: Any, label: str) -> None:
        """Append *step* to the recipe and run it — the menu-preset pattern.

        The Model-menu Mesh entry and the Analysis-menu analysis entries are all
        presets over the recipe: compose a step in a dialog, append it, and run
        the recipe, so an operation is a visible, editable step like any other.

        Args:
            step: The step to append and run.
            label: What the Message Log should say is happening.
        """
        self._recipe_panel.add_step(step.verb, step.selection, step.params, optional=step.optional)
        self._pending_label = label
        self._on_recipe_run()

    def _mesh_preset(self, config: dict, label: str) -> None:
        """Append a ``mesh`` step for *config* and run it — the non-interactive form.

        Tests and programmatic callers use this instead of the dialog; it is the
        same preset the Model menu stands for, without the prompt.

        Args:
            config: The Preprocessor configuration the preset stands for.
            label: What the Message Log should say is happening.
        """
        if self._store is None:
            self.log("Open a SAP2000 model before preprocessing.", "warn")
            return
        from ..workflow import Step

        self._preset_step(Step(verb="mesh", params=config), label)

    # ── Analysis presets (Analysis menu) ─────────────────────────────

    def _on_modal_preset(self) -> None:
        """**Analysis ▸ Modal analysis**: compose a ``modal`` step and run it."""
        self._analysis_preset("modal", "Modal analysis")

    def _on_spectrum_preset(self) -> None:
        """**Analysis ▸ Response spectrum**: compose an RS step and run it."""
        self._analysis_preset("response_spectrum", "Response spectrum")

    def _on_pushover_preset(self) -> None:
        """**Analysis ▸ Pushover**: compose a pushover step and run it."""
        self._analysis_preset("pushover", "Pushover")

    def _analysis_preset(self, verb: str, label: str) -> None:
        """Compose an analysis step in a dialog, append it, and run the recipe.

        The Analysis-menu entries are presets over the recipe, exactly as
        **Model ▸ Mesh** is: the dialog composes the step, and accepting appends
        it and runs the recipe — so an analysis is a visible, editable,
        exportable step rather than a hidden handler.

        Args:
            verb: The analysis verb — ``"modal"``, ``"response_spectrum"`` or
                ``"pushover"``.
            label: What the Message Log should say is happening.
        """
        store = self._store
        mesh = store.preprocessed() if store is not None else None
        if store is None or mesh is None:
            self.log(
                "This analysis needs a preprocessed model \u2014 use Model \u25b8 "
                "Split elements or Mesh areas first.",
                "warn",
            )
            return
        if self._worker is not None and self._worker.isRunning():
            self.log("An analysis is already running.", "warn")
            return

        from ..workflow import Step
        from .views.step_dialog import StepDialog

        edited = StepDialog.edit(Step(verb=verb), self, store.raw())
        if edited is None:
            return
        if verb in ("response_spectrum", "pushover"):
            self._ensure_modal_precursor()
        self._preset_step(edited, label)

    def _ensure_modal_precursor(self) -> None:
        """Append a default ``modal`` step when the recipe has none.

        ``response_spectrum`` and ``pushover`` declare
        ``needs=("geometry", "modal")``, so a preset for either must run after
        a modal step has produced the modal result.  Prepending a default modal
        step keeps the preset self-contained; the user can edit it afterwards
        like any other step.
        """
        if not any(s.verb == "modal" for s in self._recipe_panel.recipe.steps):
            self._recipe_panel.add_step("modal")

    def _on_recipe_run(self) -> None:
        """**Recipe ▸ Run recipe**: run the panel's steps on a worker.

        The recipe is snapshotted before the worker starts (through its own
        serialisation), so editing the panel mid-run cannot change what is being
        run.  The steps' log lines are collected and flushed on the GUI thread
        once the run ends — a worker must not touch widgets.
        """
        from ..workflow import Recipe, run_recipe

        store = self._store
        if store is None:
            self.log("Open a SAP2000 model before running a recipe.", "warn")
            return
        if not len(self._recipe_panel.recipe):
            self.log(
                "The recipe has no steps \u2014 use Model \u25b8 Split elements / "
                "Mesh areas, or add one from the Recipe menu.",
                "warn",
            )
            return
        if self._worker is not None and self._worker.isRunning():
            self.log("A run is already in progress.", "warn")
            return

        recipe = Recipe.from_dict(self._recipe_panel.recipe.to_dict())
        model_data = store.raw()
        starting_mesh = store.preprocessed()
        messages: list = []
        self._recipe_messages = messages
        label = getattr(self, "_pending_label", None) or f"Running recipe ({len(recipe)} steps)"
        self._pending_label = None

        # One PipelineLog for the run: recipe progress (``context.log``) and the
        # structured analysis events share it, and its sink collects them into
        # ``messages`` for the GUI-thread flush in ``_recipe_finished``.
        from ..io.log import PipelineLog, render_entry

        pipeline_log = PipelineLog(verbose=True, collect=False, _stdout=False)

        def _collect(entry: dict) -> None:
            if entry["level"] != "DEBUG":
                messages.append(render_entry(entry))

        pipeline_log.add_sink(_collect)

        self._set_model_actions_enabled(False)
        self._progress.setRange(0, 0)  # busy: a recipe reports no fine-grained progress
        self._progress.setVisible(True)
        self._actions["analysis.stop"].setEnabled(True)
        self.log(f"{label} \u2026")

        # Per-step status flows back to the panel through the worker's existing
        # ``progress`` signal (current=step index, label="<verb>:<status>"), so
        # the Status column updates live without the worker touching widgets.
        self._recipe_panel.reset_statuses()
        reporter: list = [None]

        def task(should_cancel):
            def on_step(index, verb, status):
                if reporter[0] is not None:
                    reporter[0](index, len(recipe), f"{verb}:{status}")

            run = run_recipe(
                recipe,
                model_data,
                model=starting_mesh,
                cancel=should_cancel,
                pipeline_log=pipeline_log,
                on_step=on_step,
            )
            return run, messages

        _freeze_gc_once()
        worker = TaskWorker(task, parent=self)
        reporter[0] = worker.report_progress
        worker.progress.connect(self._on_recipe_step_progress)
        worker.succeeded.connect(self._recipe_finished)
        worker.failed.connect(self._preprocess_failed)
        worker.finished.connect(self._preprocess_ended)
        self._worker = worker
        worker.start()

    def _on_recipe_step_progress(self, index: int, _total: int, label: str) -> None:
        """Update the Recipe panel's Status column for the step reporting in.

        Args:
            index: Zero-based step position in the recipe.
            _total: The number of steps (unused — the panel already knows it).
            label: ``"<verb>:<status>"``, as encoded by the run's ``on_step``.
        """
        _verb, _sep, status = label.partition(":")
        self._recipe_panel.set_step_status(index, status or "running")

    def _recipe_finished(self, payload: Any) -> None:
        """Report what the recipe produced and register each result as a view.

        A ``cases`` result carries the same in-memory archive the **Analysis ▸
        Run** path serves, so it becomes one view per case, exactly as there —
        a recipe's results are no longer merely a line in the log.  Cancellation
        and failure stay distinct: a *cancelled* run legitimately holds only the
        cases it solved before stopping, so no result is reported as failed, and
        an empty one is left to the cancellation line rather than called out as
        missing data.

        Args:
            payload: ``(RecipeRun, [log lines])`` from the worker.
        """
        from ..workflow import CASES, FIGURE, TABLE
        from ..workflow.steps import MODAL, PUSHOVER, RS

        run, messages = payload
        self._last_run = run
        for message in messages:
            self.log(message)
        for result in run.results:
            if result.kind == "geometry":
                self._show_geometry_result(result.label, result.payload)
            elif result.kind == CASES:
                added = self._show_results_result(result.label, result.payload)
                if not added and not run.cancelled:
                    self.log(f"{result.label}: the run produced no results.", "warn")
            elif result.kind == MODAL:
                self._show_modal_result(result.label, result.payload)
            elif result.kind == PUSHOVER:
                self._show_pushover_result(result.label, result.payload)
            elif result.kind == RS:
                self._show_rs_result(result.label, result.payload)
            elif result.kind == TABLE:
                self._show_table_result(result.label, result.payload)
            elif result.kind == FIGURE:
                self._show_figure_result(result.label, result.payload)
        for index, verb, message in run.failures:
            self.log(f"Step {index} ({verb}) failed and is optional \u2014 {message}", "warn")
        if run.cancelled:
            self.log("The recipe was cancelled.", "warn")
        self._set_analysis_actions_enabled()

    def _show_table_result(self, label: str, table: Any) -> None:
        """Register a check's ``table`` result as a view and show it.

        The view key is derived from the step's label, so re-running the same
        check replaces its view rather than piling one up per run.
        """
        if table is None:
            self.log(f"{label}: nothing to show.", "warn")
            return
        key = _table_key(label)
        name = getattr(table, "title", None) or label
        view = self._views.add_table(key, name, table, source=f"Recipe: {label}")
        self._show_view(view, rebuild_tree=True)
        self.log(f"Added table view: {name}.")

    def _show_figure_result(self, label: str, figure: Any) -> None:
        """Register a chart's ``figure`` result as a view and show it."""
        if figure is None:
            self.log(f"{label}: the chart produced no figure.", "warn")
            return
        key = f"figure:{label.lower().replace(' ', '-')}"
        view = self._views.add_figure(key, label, figure, source=f"Recipe: {label}")
        self._show_view(view, rebuild_tree=True)
        self.log(f"Added figure view: {label}.")

    def _show_modal_result(self, label: str, payload: Any) -> None:
        """Summarise a modal result as a table and one results view per mode.

        The ``modal`` verb's payload is an
        :class:`~fea_toolkit.analysis.base.AnalysisResult` whose ``data`` holds
        ``{"modal": {…}, "shapes": {…}}``.  The periods / participation are
        distilled into a table, and each mode shape becomes a ``results`` view
        (deformed geometry) — one per mode, like a static case.
        """
        from ..workflow.steps import Table

        data = getattr(payload, "data", None) or {}
        modal = data.get("modal", data) if isinstance(data, dict) else {}
        periods = list(modal.get("periods", []) or [])
        if not periods:
            self.log(f"{label}: the modal result has no converged modes.", "warn")
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
        self._show_table_result(
            label,
            Table(
                title=label,
                columns=("Mode", "Period (s)", "Freq (Hz)", "Mx (%)", "My (%)", "Mz (%)"),
                rows=tuple(rows),
            ),
        )

        shapes = data.get("shapes")
        if isinstance(shapes, dict) and shapes:
            self._show_modal_shapes_views(label, shapes, parent=_table_key(label))

    def _show_modal_shapes_views(
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
        from .project import mode_shape_arrays

        mesh = self._store.preprocessed() if self._store is not None else None
        if mesh is None:
            self.log(f"{label}: no prepared mesh to draw the mode shapes against.", "warn")
            return
        arrays = mode_shape_arrays(mesh, shapes)
        self._show_results_archive(label, arrays, parent=parent)

    def _show_rs_result(self, label: str, payload: Any) -> None:
        """Summarise a response-spectrum result as a table and a results view.

        The ``response_spectrum`` verb's payload is an
        :class:`~fea_toolkit.analysis.base.AnalysisResult` whose ``data`` holds
        ``{"rs_result": {…}, "direction": …, "node_displacements": {…}}``.  The
        combined base shear / moment scalars are distilled into a one-row table,
        and the CQC combined displacement field becomes a ``results`` view — one
        per direction, exactly like a static case — so the deformed shape is
        inspectable rather than only logged.
        """
        from ..workflow.steps import Table

        data = getattr(payload, "data", None)
        if not isinstance(data, dict):
            self.log(f"{label}: no response-spectrum data.", "warn")
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
        self._show_table_result(label, table)

        node_displacements = data.get("node_displacements")
        if isinstance(node_displacements, dict) and node_displacements:
            self._show_rs_results_view(
                label, str(direction), node_displacements, parent=_table_key(label)
            )

    def _show_rs_results_view(
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
        from .project import rs_displacement_arrays

        mesh = self._store.preprocessed() if self._store is not None else None
        if mesh is None:
            self.log(
                f"{label}: no prepared mesh to draw the {direction} displaced shape against.",
                "warn",
            )
            return
        arrays = rs_displacement_arrays(mesh, {direction: node_displacements})
        self._show_results_archive(label, arrays, parent=parent)

    def _show_pushover_result(self, label: str, payload: Any) -> None:
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
        from ..workflow.steps import Table

        data = getattr(payload, "data", None) or {}
        directions = sorted(data) if isinstance(data, dict) else []
        if not directions:
            self.log(f"{label}: the run produced no pushover directions.", "warn")
            return
        self.log(f"{label}: complete ({', '.join(directions)}).")

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
        self._show_table_result(
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

        from ..plotting.viz_pushover import plot_capacity_spectrum
        from .project import figure_identity

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
            view = self._views.add_figure(
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
        mesh = self._store.preprocessed() if self._store is not None else None
        if mesh is not None:
            from ..io.npz_writer import collect_pushover_arrays
            from .project import pushover_displacement_arrays

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
                self._show_results_archive(label, arrays, parent=table_key, show=False)

        if views:
            self._show_view(views[0], rebuild_tree=True)
        self.log(f"{label}: added {len(views)} ADRS figure(s).")

    def _show_results_result(self, label: str, arrays: Any) -> list:
        """Register each case in a recipe's results archive as a view.

        Both ``cases`` verbs — a solve and a combination reduction — hand back
        an in-memory results archive, so this is the same path the **Analysis ▸
        Run** results take.  See :meth:`_show_results_archive`.
        """
        return self._show_results_archive(label, arrays or {})

    def _show_results_archive(
        self, label: str, arrays: dict, parent: Optional[str] = None, *, show: bool = True
    ) -> list:
        """Register one ``results`` view per case in *arrays* and show the first.

        A :class:`~fea_toolkit.io.results_repository.NpzResultsRepository` over
        the array dict supplies the cases (``static_case_labels``); the mesh the
        run was prepared from is what the results are keyed to, so it is
        preferred, and an archive also carries its own display geometry, which is
        what makes the view drawable with nothing else loaded.  Cases replace by
        key, so re-running a recipe refreshes its views instead of piling them up.

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
        from ..io.results_repository import NpzResultsRepository

        repository = NpzResultsRepository(arrays or {})
        cases = list(repository.cases())
        if not cases:
            return []

        mesh = self._store.preprocessed() if self._store is not None else None
        model = mesh if mesh is not None else repository.as_model()
        for case in cases:
            self._views.add_results(
                f"{_RESULTS_KEY_PREFIX}{case}",
                _case_view_name(case, repository.case_meta(case)),
                repository,
                source=f"Recipe: {label} \u00b7 {case}",
                model=model,
                parent=parent,
                activate=False,
            )
        self.log(f"{label}: {len(cases)} case(s) added as views.")

        if show:
            first = self._views.get(f"{_RESULTS_KEY_PREFIX}{cases[0]}")
            if first is not None:
                self._show_view(first, rebuild_tree=True)
        return cases

    def _show_geometry_result(self, label: str, mesh_model: Any) -> None:
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
        self.log(
            f"Preprocessed: {len(mesh_model.frame_elements)} frame elements ({detail}), "
            f"{len(mesh_model.area_elements)} area elements."
        )
        # Record the preprocessed model on the store, so Analysis ▸ Run uses the
        # topology just displayed rather than preprocessing again (P27,
        # refinement 1: preprocessing is a user action, never silent).
        if self._store is not None:
            self._store.set_preprocessed(mesh_model)
        # The view *names* are the GUI's own convention: a split-only run is
        # still "Processed" and a meshing run "Meshed", whatever the verb
        # labelled its result.
        if label == "Meshed":
            key, name = "meshed", "Meshed"
        else:
            key, name = "processed", "Processed"
        self._views.add_geometry(key, name, mesh_model, source=f"Recipe: {label}")
        if self._hidden_elem_ids:
            self._hidden_elem_ids.clear()
            self.log("Preprocessing rebuilt the model; hidden elements were restored.")
        self.show_model(mesh_model, reset_view=False)
        self.log(f"Added view: {name}.")

    def _preprocess_failed(self, message: str) -> None:
        """Report a recipe failure, flushing buffered diagnostics first.

        The pipeline messages collected by ``_collect`` on the worker thread are
        flushed here (on the GUI thread) before the error, so the diagnostics that
        led up to the failure appear before it — matching the success path's flush
        in :meth:`_recipe_finished`.
        """
        for pending in getattr(self, "_recipe_messages", None) or []:
            self.log(pending)
        self._recipe_messages = None
        self.log(f"Recipe failed: {message}", "error")

    def _preprocess_ended(self) -> None:
        """Restore the UI once the worker has stopped, successfully or not.

        The collection here is the other half of the worker's ``gc.disable()``
        (:mod:`fea_toolkit.gui.controllers.worker`): cycles created while the
        task ran are reclaimed on the **GUI thread**, where traversing the
        PySide6/VTK objects the window owns is safe.
        """
        self._worker = None
        self._progress.setVisible(False)
        self._progress.setRange(0, 100)
        self._actions["analysis.stop"].setEnabled(False)
        self._set_model_actions_enabled(self._store is not None)
        self._set_analysis_actions_enabled()
        gc.collect()

    def _set_model_actions_enabled(self, enabled: bool) -> None:
        """Enable the preprocessing actions — they need a parsed source model.

        Args:
            enabled: Whether a ``SAPModelData`` is loaded and can be preprocessed.
        """
        for key in ("model.mesh",):
            self._actions[key].setEnabled(enabled)

    def _set_analysis_actions_enabled(self) -> None:
        """Enable **Analysis ▸ Run** only when a preprocessed model exists.

        ``run_static_cases`` needs a ``MeshModel`` beside the parsed model, and
        Run deliberately does **not** preprocess on its own: a topology change
        the user should see reported comes from ``Model ▸ Split`` / ``Mesh
        areas`` (P27, refinement 1).  Until one has been run, the action stays
        greyed and its tooltip names the step it needs.  A run already in flight
        also keeps it disabled, so a second dialog cannot be opened over it.
        """
        store = self._store
        mesh = store.preprocessed() if store is not None else None
        running = self._worker is not None and self._worker.isRunning()
        enabled = mesh is not None and not running
        for key in ("analysis.run", "analysis.modal", "analysis.spectrum", "analysis.pushover"):
            self._actions[key].setEnabled(enabled)
        # Saving the meshed model needs the same preprocessed ``MeshModel``, but
        # is safe while an analysis runs (the topology is frozen), so it is not
        # gated on ``running``.
        self._actions["file.save_model"].setEnabled(mesh is not None)
        # A project can be saved with just a model (results are optional), so it
        # is gated on a loaded model rather than on a preprocessed mesh.
        self._actions["file.save_project"].setEnabled(self._store is not None)
        action = self._actions["analysis.run"]
        if mesh is None:
            action.setToolTip(
                "Run \u2014 needs a preprocessed model: Model \u25b8 Split elements or Mesh areas"
            )
        else:
            action.setToolTip("Choose static cases or combinations to solve")

    def _set_recipe_actions_enabled(self) -> None:
        """Enable the recipe actions that the panel's contents allow.

        Everything except *Run* is meaningful only with a step to act on, so an
        empty recipe leaves them greyed rather than letting a click do nothing.
        """
        if not hasattr(self, "_recipe_panel"):
            return
        has_steps = len(self._recipe_panel.recipe) > 0
        for key in ("recipe.run", "recipe.clear", "recipe.save", "recipe.export"):
            self._actions[key].setEnabled(has_steps)

    # ── Recipe files ────────────────────────────────────────────────

    def _on_recipe_clear(self) -> None:
        """**Recipe ▸ Clear recipe**: empty the panel."""
        self._recipe_panel.clear()
        self.log("Recipe cleared.")

    def _on_recipe_save(self) -> None:
        """**Recipe ▸ Save recipe…**: write the panel's steps to a JSON file."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Save recipe", "recipe.json", "Recipe JSON (*.json)"
        )
        if not path:
            return
        try:
            self._recipe_panel.recipe.to_json(path)
        except OSError as exc:
            self.log(f"Could not write {path}: {exc}", "error")
            return
        self.log(f"Saved recipe: {path}")

    def _on_recipe_open(self) -> None:
        """**Recipe ▸ Open recipe…**: read a recipe and show it in the panel."""
        from ..workflow import Recipe

        path, _ = QFileDialog.getOpenFileName(self, "Open recipe", "", "Recipe JSON (*.json)")
        if not path:
            return
        try:
            recipe = Recipe.from_json(path)
        except (OSError, ValueError) as exc:
            self.log(f"Could not read {path}: {exc}", "error")
            return
        self._recipe_panel.set_recipe(recipe)
        self.log(f"Opened recipe: {path} ({len(recipe)} steps)")

    def _on_recipe_export(self) -> None:
        """**Recipe ▸ Export as Python…**: write the recipe as a runnable script."""
        from pathlib import Path

        path, _ = QFileDialog.getSaveFileName(
            self, "Export recipe as Python", "recipe.py", "Python (*.py)"
        )
        if not path:
            return
        try:
            Path(path).write_text(self._recipe_panel.recipe.to_python(), encoding="utf-8")
        except OSError as exc:
            self.log(f"Could not write {path}: {exc}", "error")
            return
        self.log(f"Exported recipe: {path}")

    # ── Handlers ────────────────────────────────────────────────────

    def _on_open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open model",
            "",
            "SAP2000 model (*.s2k *.json);;All files (*)",
        )
        if path and not self.open_path(path):
            QMessageBox.critical(
                self, "Open failed", f"Could not open:\n{path}\n\nSee the message log."
            )

    def _on_open_results(self) -> None:
        """**File ▸ Open results…**: choose an archive and register its cases."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open results",
            "",
            "Results archive (*.npz *.h5 *.hdf5);;All files (*)",
        )
        if path and not self.open_results_path(path):
            QMessageBox.critical(
                self,
                "Open failed",
                f"Could not open results:\n{path}\n\nSee the message log.",
            )

    def open_results_path(self, path: str) -> bool:
        """Open a results archive and register one view per load case.

        Dialog-free so tests can drive it directly.  An archive carries its own
        display geometry, so this works with **no model open** — the views draw
        the geometry the archive was written from — and the display model is
        built once and shared by every case view, so a dozen cases cost one
        model between them.

        Args:
            path: Archive path (NPZ or HDF5).

        Returns:
            ``False`` when the file is missing, unreadable or not a results
            archive; ``True`` otherwise.
        """
        from pathlib import Path

        from ..io.npz_reader import is_results_archive
        from ..io.results_repository import NpzResultsRepository

        self.log(f"Opening results {path} \u2026")
        try:
            if not is_results_archive(path):
                self.log(f"{path} is not a results archive.", "error")
                return False
            repository = NpzResultsRepository(path)
            cases = repository.cases()
            model = repository.as_model()
        except Exception as exc:
            self.log(f"Failed to open results {path}: {exc}", "error")
            return False

        name = Path(path).name
        keys = cases or [name]
        for case in keys:
            meta = repository.case_meta(case) if cases else {}
            self._views.add_results(
                f"results:{case}",
                _case_view_name(case, meta),
                repository,
                source=f"{name} \u00b7 {case}",
                model=model,
                activate=False,
            )
        self.log(f"Opened {len(keys)} results view(s) from {name}.")

        first = self._views.get(f"results:{keys[0]}")
        if first is not None:
            self._hidden_elem_ids.clear()
            self._show_view(first, rebuild_tree=True)
        return True

    def _on_save_results(self) -> None:
        """**File ▸ Save results…**: write the displayed result to a chosen NPZ.

        The path is always prompted for — with a suggested name — rather than a
        fixed output, so variants differing by a small change can be kept side
        by side (P27, refinement 2).  What is saved is the archive the active
        view is reading, so the file round-trips to exactly what the view shows,
        and the run itself stays viewable without it.
        """
        from pathlib import Path

        view = self._views.active
        repository = self._views.results(view.key) if view is not None else None
        if repository is None:
            self.log("Nothing to save \u2014 show a results case first.", "warn")
            return

        case = _case_of(view) or "results"
        stem = Path(self._source_label).stem or "results"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save results", f"{stem}_{case}.npz", "Results archive (*.npz)"
        )
        if not path:
            return
        if not path.lower().endswith(".npz"):
            path += ".npz"

        from ..io.npz_writer import save_results_arrays

        try:
            saved = save_results_arrays(path, repository.raw())
        except Exception as exc:  # pragma: no cover - a filesystem failure
            self.log(f"Could not save results: {exc}", "error")
            return
        self.log(f"Saved results to {saved}.")

    def _on_save_model(self) -> None:
        """**File ▸ Save model…**: write the meshed model to a chosen file.

        The preprocessed ``MeshModel`` is what is saved; a parsed model that has
        not been meshed yet has nothing to write, so the action stays greyed
        until ``Model ▸ Split elements`` / ``Mesh areas`` has produced one.  The
        format follows the chosen extension: ``.json`` writes the lossless
        codec payload (a ``MeshModel`` round-trip through
        :func:`~fea_toolkit.io.model_codec.json_to_model`), while ``.npz`` /
        ``.h5`` write the self-describing stage file (both ``sap`` and ``mesh``
        stages, so the file can be re-analysed or imported).  The write itself
        is :func:`~fea_toolkit.io.stage_writer.write_model_file`, shared with the
        ``save_model`` recipe verb, so the menu and a recipe save identically.
        """
        from pathlib import Path

        store = self._store
        mesh = store.preprocessed() if store is not None else None
        if mesh is None:
            self.log(
                "Save model needs a preprocessed model \u2014 use Model \u25b8 Split "
                "elements or Mesh areas first.",
                "warn",
            )
            return

        stem = Path(self._source_label).stem or "model"
        path, chosen = QFileDialog.getSaveFileName(
            self,
            "Save model",
            f"{stem}_mesh.h5",
            "Model file (*.h5 *.npz *.json);;HDF5 stage file (*.h5 *.hdf5);;"
            "NPZ stage file (*.npz);;JSON codec (*.json)",
        )
        if not path:
            return

        requested = path
        fmt, path = _model_file_format(path, chosen)
        if path != requested and Path(path).exists():
            answer = QMessageBox.question(
                self,
                "Confirm overwrite",
                f"{path} already exists.\nOverwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            from ..io.stage_writer import write_model_file

            written = write_model_file(path, sap=store.raw(), mesh=mesh, fmt=fmt)
        except Exception as exc:
            self.log(f"Could not save model: {exc}", "error")
            return
        self.log(f"Saved model to {written}.")

    # ── Project bundle (File ▸ Save/Open project) ────────────────────

    def _project_results_arrays(self, collected: dict) -> dict:
        """Merge the collected static archive arrays with modal + RS + pushover.

        RS displacement fields, modal mode shapes and pushover directions are
        persisted as static-shaped cases (``static/RS-<direction>/node_*``,
        ``static/Mode-<i>/node_*``, ``static/Pushover-<direction>/node_*``) so
        each deformed-shape view round-trips like a static load case (Option C).
        The pushover per-step history is persisted as ``pushover/<direction>/…``.
        """
        from ..io.npz_writer import collect_pushover_arrays
        from ..io.unified_writer import collect_modal_arrays, collect_rs_arrays
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

        mesh = self._store.preprocessed() if self._store is not None else None
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
        return arrays

    def _append_analysis_type(self, arrays: dict, name: str) -> None:
        """Append *name* to ``analysis_types``, creating it when absent."""
        import numpy as np

        existing = arrays.get("analysis_types")
        if existing is not None and len(existing):
            arrays["analysis_types"] = np.concatenate(
                [np.asarray(existing), np.array([name], dtype=str)]
            )
        else:
            arrays["analysis_types"] = np.array([name], dtype=str)

    def _append_static_case_labels(self, arrays: dict, labels: Any) -> None:
        """Append *labels* to ``static_case_labels``, creating it when absent."""
        import numpy as np

        existing = arrays.get("static_case_labels")
        if existing is not None and len(existing):
            arrays["static_case_labels"] = np.concatenate(
                [np.asarray(existing), np.asarray(labels)]
            )
        else:
            arrays["static_case_labels"] = labels

    def _project_tables(self) -> dict:
        """Collect ``{key: Table}`` from every table view in the registry."""
        tables = {}
        for view in self._views.views():
            if view.kind == TABLE:
                table = self._views.table(view.key)
                if table is not None:
                    tables[view.key] = table
        return tables

    def _merge_results_view_arrays(self, arrays: dict) -> None:
        """Merge static arrays from results views (``Analysis ▸ Run``, no recipe).

        Falls back to the registered results repositories when the recipe run
        produced no ``CASES`` payload, so a project can be saved from either path.
        """
        seen = set()
        for view in self._views.views():
            if view.kind == RESULTS:
                repository = self._views.results(view.key)
                if repository is not None and id(repository) not in seen:
                    seen.add(id(repository))
                    arrays.update(repository.raw())

    def _on_save_project(self) -> None:
        """**File ▸ Save project…**: write the full session to a single ``.zip`` archive.

        A project is a zip archive (``<name>.zip``) holding the model stage, the
        results archive, the recipe, the ordered view list and the figure / table
        payloads — everything needed to reconstruct the session without re-running
        an analysis.
        """
        from pathlib import Path

        if self._store is None:
            self.log("Open a model before saving a project.", "warn")
            return

        from . import project as project_mod

        stem = Path(self._source_label).stem or "project"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save project", f"{stem}.zip", "FEA project (*.zip)"
        )
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() != ".zip":
            target = Path(f"{target}.zip")

        collected = project_mod.collect_project_results(
            self._last_run.results if self._last_run is not None else []
        )
        if not collected["static_arrays"]:
            self._merge_results_view_arrays(collected["static_arrays"])

        figures = project_mod.figures_payload(collected["pushover"])
        tables = project_mod.tables_payload(self._project_tables())
        active = self._views.active
        active_key = active.key if active is not None else None
        recipe = getattr(getattr(self, "_recipe_panel", None), "recipe", None)
        manifest = project_mod.build_manifest(
            name=stem,
            source_label=self._source_label,
            model_file="model.h5",
            units=dict(getattr(self._store.raw(), "units", None) or {}),
            recipe=recipe,
            views=self._views.views(),
            active_view=active_key,
            camera=self._current_camera(),
            hidden_elem_ids=sorted(self._hidden_elem_ids),
            display=self._collect_display_values(),
        )
        notes = project_mod.write_project_zip(
            target,
            manifest=manifest,
            sap=self._store.raw(),
            mesh=self._store.preprocessed(),
            results_arrays=self._project_results_arrays(collected),
            figures=figures,
            tables=tables,
        )
        for note in notes:
            self.log(note, "warn")
        self.log(f"Saved project to {target}.")

    def _on_open_project(self) -> None:
        # A project is a single ``<name>.zip`` file (a zip), so the native file
        # picker is the right tool — it lists and selects it without the macOS
        # directory picker's quirk of hiding folders whose names carry a file
        # extension.  ``open_project_path`` also still accepts a legacy ``.fea``
        # directory when handed a path directly.
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open project",
            "",
            "FEA project (*.zip);;All files (*)",
        )
        if path and not self.open_project_path(path):
            QMessageBox.critical(
                self,
                "Open failed",
                f"Could not open project:\n{path}\n\nSee the message log.",
            )

    def open_project_path(self, path: str) -> bool:
        """Load a project (directory or ``.zip`` archive) and rebuild the session.

        Dialog-free so tests can drive it directly.  The model, results, tables
        and figures are read back from the bundle and re-registered as views — so
        nothing is re-run — and the active view, camera and hidden set are
        restored from the manifest.
        """
        from ..workflow import Recipe
        from .project import is_project, read_project, regenerate_figure

        self.log(f"Opening project {path} …")
        try:
            if not is_project(path):
                self.log(f"{path} is not a project (directory or .zip archive).", "error")
                return False
            bundle = read_project(path)
        except Exception as exc:
            self.log(f"Failed to open project {path}: {exc}", "error")
            return False

        manifest = bundle["manifest"] or {}
        sap = bundle["sap"]
        mesh = bundle["mesh"]
        repository = bundle["results"]
        tables = bundle["tables"] or {}
        figures = bundle["figures"] or {}

        if sap is None:
            self.log("Project has no model stage; nothing to display.", "error")
            return False

        project = manifest.get("project") or {}
        source_label = project.get("source_label", "")
        self._save_view_state()
        self._remember_source(sap, source=source_label)
        if mesh is not None:
            self._backfill_mesh_load_metadata(mesh, sap)
            self._store.set_preprocessed(mesh)

        geometry_sources = {"unprocessed": sap, "processed": mesh, "meshed": mesh}
        # Results views render the archive's own geometry, which carries section
        # *names* but not definitions, and no supports.  Attach the source
        # model's sections and restraints so a results view colours and supports
        # its elements exactly as the geometry views do.
        display_model = (
            repository.as_model(sections=sap.sections, restraints=sap.restraints)
            if repository is not None
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
                    self._views.add_geometry(key, name, model, source=source, activate=False)
            elif kind == RESULTS:
                case = entry.get("case")
                if repository is not None and case in repository.cases():
                    self._views.add_results(
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
                    self._views.add_table(
                        key, name, table, source=source, parent=parent, activate=False
                    )
            elif kind == FIGURE:
                figure_data = figures.get(entry.get("figure_key", key))
                if figure_data:
                    figure = regenerate_figure(figure_data)
                    if figure is not None:
                        self._views.add_figure(
                            key, name, figure, source=source, parent=parent, activate=False
                        )

        recipe_dict = project.get("recipe")
        if recipe_dict is not None:
            panel = getattr(self, "_recipe_panel", None)
            if panel is not None:
                with contextlib.suppress(Exception):
                    panel.set_recipe(Recipe.from_dict(recipe_dict))

        viewport = manifest.get("viewport") or {}
        hidden = viewport.get("hidden_elem_ids")
        self._hidden_elem_ids = {str(ref) for ref in hidden} if isinstance(hidden, list) else set()

        self._apply_display_values(manifest.get("display") or {})

        active = self._preferred_open_view(manifest.get("active_view"))
        if active is None:
            views = self._views.views()
            active = views[0] if views else None
        if active is not None:
            self._show_view(active, rebuild_tree=True)
            self._restore_camera(viewport.get("camera_position"))

        self.log(f"Opened project: {len(self._views)} view(s).")
        return True

    def open_path(self, path: str) -> bool:
        """Parse *path* and display it; return False on failure (no dialog).

        Dialog-free so tests can drive it directly.
        """
        from ..io.s2k_parser import SAP2000Parser

        self.log(f"Opening {path} \u2026")
        try:
            model = SAP2000Parser(path).parse().get_model_data()
        except Exception as exc:
            self.log(f"Failed to open {path}: {exc}", "error")
            return False
        self._save_view_state()  # preserve the outgoing model's view before switching
        self._remember_source(model, source=path)
        state, notes = load_view_state(path)
        for note in notes:
            # A stale state (the model was re-edited) is an ordinary outcome, so
            # it is logged at info; anything else is a warning.
            level = "info" if "is stale" in note else "warn"
            self.log(note, level)
        hidden = state.get("hidden_elem_ids")
        self._hidden_elem_ids = {str(ref) for ref in hidden} if isinstance(hidden, list) else set()
        self.show_model(model)
        self._restore_camera(state.get("camera_position"))
        return True

    def _on_zoom_fit(self) -> None:
        self._interactor.reset_camera()

    def _view_iso(self) -> None:
        self._interactor.view_isometric()

    def _view_xy(self) -> None:
        self._interactor.view_xy()

    def _view_xz(self) -> None:
        self._interactor.view_xz()

    def _view_yz(self) -> None:
        self._interactor.view_yz()

    def _on_docs(self) -> None:
        QDesktopServices.openUrl(QUrl(_PROJECT_URL))

    def _on_about(self) -> None:
        from .. import __version__ as version

        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> {version}<br><br>"
            "A FEA to OpenSees/Rhino conversion toolkit.<br>"
            "GUI: Milestone 4 (selection sync).",
        )

    # ── Analysis (Analysis menu) ────────────────────────────────────

    def _on_analysis_run(self) -> None:
        """**Analysis ▸ Run…**: pick cases, solve on a worker, register the views.

        The listing is built Qt-free (``analysis.case_listing``) and handed to
        the dialog, which adds the per-case load multipliers; what comes back is
        a set of static solves.  Nothing is written to disk — the in-memory
        archive is registered as one view per case, so a result can be looked at
        without an NPZ.  ``File ▸ Save results`` persists one when wanted.
        """
        store = self._store
        mesh = store.preprocessed() if store is not None else None
        if store is None or mesh is None:
            self.log(
                "Run needs a preprocessed model \u2014 use Model \u25b8 Split elements "
                "or Mesh areas first.",
                "warn",
            )
            return
        if self._worker is not None and self._worker.isRunning():
            self.log("An analysis is already running.", "warn")
            return

        from ..analysis.case_listing import (
            list_combinations,
            list_patterns,
            list_static_cases,
        )
        from .views.analysis_dialog import AnalysisDialog

        md = store.raw()
        specs = {spec.name: spec for spec in list_combinations(md)}
        request = AnalysisDialog.get_request(
            list_static_cases(md, mesh),
            list(specs.values()),
            list_patterns(md),
            self,
        )
        if request is None:  # cancelled
            return

        cases = request.cases
        combos = {name: dict(specs[name].leaves) for name in request.combinations if name in specs}
        self.run_analysis(cases, combos, config=request.config)

    def run_analysis(
        self, cases: dict, combinations: Optional[dict] = None, config: Optional[dict] = None
    ) -> bool:
        """Solve *cases* (and reduce *combinations*) on a worker — dialog-free.

        The entry point the dialog defers to, and that tests drive directly,
        exactly as :meth:`open_results_path` is for ``File ▸ Open results``.

        Args:
            cases: ``{case: {pattern: factor}}`` to solve.
            combinations: ``{combination: {leaf_case: factor}}`` to reduce.
            config: Optional ``AnalysisBuilder`` config overrides.

        Returns:
            ``True`` when the run started; ``False`` when it cannot yet (no
            preprocessed model) or one is already running.
        """
        store = self._store
        mesh = store.preprocessed() if store is not None else None
        if store is None or mesh is None:
            self.log(
                "Run needs a preprocessed model \u2014 use Model \u25b8 Split elements "
                "or Mesh areas first.",
                "warn",
            )
            return False
        if self._worker is not None and self._worker.isRunning():
            self.log("An analysis is already running.", "warn")
            return False
        self._start_analysis(
            store.raw(), mesh, dict(cases), dict(combinations or {}), dict(config or {})
        )
        return True

    def _on_analysis_stop(self) -> None:
        """**Analysis ▸ Stop**: ask the worker to stop at its next case boundary.

        Cancellation is cooperative, exactly as the threading model requires: a
        single ``run_static_analysis`` call is atomic, so the run ends *between*
        cases rather than mid-solve.
        """
        if self._worker is None or not self._worker.isRunning():
            return
        self._worker.cancel()
        self._actions["analysis.stop"].setEnabled(False)
        self.log("Stop requested \u2014 the run ends at the next case boundary.", "warn")

    def _start_analysis(self, md: Any, mesh: Any, cases: dict, combos: dict, config: dict) -> None:
        """Solve *cases* (and reduce *combos*) on a worker.

        Args:
            md: The parsed model — needed to reduce combinations.
            mesh: The preprocessed model the cases are built from.
            cases: ``{case: {pattern: factor}}`` to solve, in run order.
            combos: ``{combination: {leaf_case: factor}}`` to reduce afterwards.
            config: Optional ``AnalysisBuilder`` config overrides.
        """
        self.log(f"Running {len(cases)} case(s) and {len(combos)} combination(s) \u2026")
        self._progress.setRange(0, 0)  # busy until the first case reports
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._actions["analysis.stop"].setEnabled(True)
        self._actions["analysis.run"].setEnabled(False)

        # The task reports progress through the worker's signal; the reporter is
        # published in a one-element list because the worker does not exist yet
        # where the task is defined.
        reporter: list = []

        def task(should_cancel: Any) -> dict:
            from ..analysis.linear import run_case_set

            return run_case_set(
                md,
                mesh,
                cases,
                combinations=combos,
                config=config or None,
                should_cancel=should_cancel,
                on_progress=lambda index, name, total: reporter[0](index, total, name),
            )

        _freeze_gc_once()
        worker = TaskWorker(task, parent=self)
        reporter.append(worker.report_progress)
        worker.progress.connect(self._analysis_progress)
        worker.succeeded.connect(self._analysis_finished)
        worker.failed.connect(self._analysis_failed)
        worker.finished.connect(self._analysis_ended)
        self._worker = worker
        worker.start()

    def _analysis_progress(self, current: int, total: int, label: str = "") -> None:
        """Show determinate progress, and log which case is being solved."""
        self._progress.setRange(0, max(int(total), 1))
        self._progress.setValue(int(current))
        if label:
            self.log(f"Solving {label} ({current}/{total}) \u2026")

    def _analysis_finished(self, result: dict) -> None:
        """Register the in-memory archive as one view per case and show the first.

        The result is served through a ``NpzResultsRepository`` backed by the
        array dict — never a file path — so the run's results can be viewed with
        nothing written.  Re-running a case replaces its view (the same key),
        while a different case adds one, so repeated runs do not pile up
        (P27, refinement 2).
        """
        from ..io.results_repository import NpzResultsRepository

        if result.get("cancelled"):
            self.log("Analysis stopped before finishing.", "warn")
        for name in result.get("failed") or []:
            self.log(f"Case did not solve: {name}", "warn")
        for name in result.get("unreduced") or []:
            self.log(f"Combination not reduced (a required case is missing): {name}", "warn")

        repository = NpzResultsRepository(result.get("arrays") or {})
        cases = list(result.get("cases") or repository.cases())
        if not cases:
            self.log("The run produced no results.", "warn")
            return

        mesh = self._store.preprocessed() if self._store is not None else None
        model = mesh if mesh is not None else repository.as_model()
        for case in cases:
            self._views.add_results(
                f"{_RESULTS_KEY_PREFIX}{case}",
                _case_view_name(case, repository.case_meta(case)),
                repository,
                source=f"Analysis run \u00b7 {case}",
                model=model,
                activate=False,
            )
        self.log(f"Run complete: {len(cases)} case(s) available as views.")

        first = self._views.get(f"{_RESULTS_KEY_PREFIX}{cases[0]}")
        if first is not None:
            self._show_view(first, rebuild_tree=True)

    def _analysis_failed(self, message: str) -> None:
        """Report a run failure, leaving the display alone."""
        self.log(f"Analysis failed: {message}", "error")

    def _analysis_ended(self) -> None:
        """Restore the UI once the analysis worker has stopped.

        The collection is the other half of the worker's ``gc.disable()``: cycles
        created while the task ran are reclaimed on the GUI thread, where
        traversing the PySide6/VTK objects the window owns is safe.
        """
        self._worker = None
        self._progress.setVisible(False)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._actions["analysis.stop"].setEnabled(False)
        self._set_analysis_actions_enabled()
        gc.collect()

    # ── Teardown ────────────────────────────────────────────────────

    def closeEvent(self, event):
        """Stop the cursor timer, detach the mouse filter and release the render window."""
        with contextlib.suppress(Exception):
            self._save_display_settings()
            self._save_view_state()
        if self._cursor_timer is not None:
            self._cursor_timer.stop()
        if self._mouse_filter is not None:
            self._interactor.removeEventFilter(self._mouse_filter)
            self._mouse_filter = None
        if self._worker is not None and self._worker.isRunning():
            # Preprocessing is atomic, so this only gives it a grace period
            # rather than interrupting it mid-call.
            self._worker.cancel()
            self._worker.wait(3000)
        if self._interactor is not None:
            with contextlib.suppress(Exception):
                self._interactor.close()
        super().closeEvent(event)

    def _collect_display_values(self) -> dict:
        """The display knobs, shared by the settings save and the project save."""
        return {
            "shell_opacity": float(self._shell_opacity.value()),
            "shrink": float(self._shrink.value()),
            "deformed_scale": float(self._deformed_scale.value()),
            "force_scale": float(self._force_scale.value()),
            "force_quantity": self._force_quantity.currentText(),
            "restraint_style": self._display.restraint_style,
            "show_extruded": self._display.show_extruded,
            "extruded_detail": self._display.extruded_detail,
            "show_nodes": self._actions["view.show_nodes"].isChecked(),
            "show_frames": self._actions["view.show_frames"].isChecked(),
            "show_shells": self._actions["view.show_shells"].isChecked(),
            "show_restraints": self._actions["view.show_restraints"].isChecked(),
        }

    def _apply_display_values(self, display: dict) -> None:
        """Restore a saved ``display`` section onto the widgets and render state.

        The numeric knobs and the force-quantity selector are set on their
        widgets (with signals blocked, so no per-knob re-render fires), and the
        frozen :class:`~fea_toolkit.gui.controllers.settings.DisplaySettings`
        fields that carry the restraint style, extruded-frame toggle and detail
        are replaced.  A later view render reads all of these, so the project
        reopens with the look it was saved with.

        Values are resolved through :func:`~fea_toolkit.gui.controllers.settings
        .resolve_display`, so an out-of-range or unknown key in a stale manifest
        falls back to the default rather than corrupting a widget.
        """
        if not display:
            return
        from dataclasses import replace

        from .controllers.settings import resolve_display

        resolved, notes = resolve_display(display)
        for note in notes:
            self.log(note, "warn")

        for widget, name in (
            (self._shell_opacity, "shell_opacity"),
            (self._shrink, "shrink"),
            (self._deformed_scale, "deformed_scale"),
            (self._force_scale, "force_scale"),
        ):
            widget.blockSignals(True)
            widget.setValue(float(getattr(resolved, name)))
            widget.blockSignals(False)

        self._force_quantity.setCurrentText(resolved.force_quantity)

        self._display = replace(
            self._display,
            restraint_style=resolved.restraint_style,
            show_extruded=resolved.show_extruded,
            extruded_detail=resolved.extruded_detail,
        )
        self._set_toggle_checked("view.show_extruded", resolved.show_extruded)
        self._set_toggle_checked("view.show_nodes", resolved.show_nodes)
        self._set_toggle_checked("view.show_frames", resolved.show_frames)
        self._set_toggle_checked("view.show_shells", resolved.show_shells)
        self._set_toggle_checked("view.show_restraints", resolved.show_restraints)

    def _save_display_settings(self) -> None:
        """Write the display knobs to the settings file, best effort.

        A failure to save is reported in the log rather than raised, so closing
        the window never blocks on a read-only config directory.
        """
        for note in save_display_settings(default_config_path(), self._collect_display_values()):
            self.log(note, "warn")

    def _current_camera(self) -> Optional[list]:
        """The current camera position, best effort (``None`` when unavailable)."""
        camera = None
        with contextlib.suppress(Exception):
            raw = self._interactor.camera_position
            if raw:
                camera = [list(map(float, row)) for row in raw]
        return camera

    def _save_view_state(self) -> None:
        """Write the camera and hidden set for the open model, best effort.

        Skipped when no model is open (``_source_label`` is empty), and when the
        displayed model is not the one ``_source_label`` names — after
        ``File ▸ Open results`` the archive draws its own geometry, so its camera
        and visibility must not overwrite the model's saved state.  Also skipped
        while the view is unchanged since a ``View ▸ Reset view``, so the reset
        does not recreate the state it just cleared.  A failure is logged rather
        than raised, exactly like :meth:`_save_display_settings`.
        """
        if not self._source_label:
            return
        if self._store is not None:
            displayed = self._model
            if displayed is not self._store.raw() and displayed is not self._store.preprocessed():
                return
        camera = self._current_camera()
        if self._reset_view_baseline is not None:
            baseline_camera, baseline_hidden = self._reset_view_baseline
            if camera == baseline_camera and frozenset(self._hidden_elem_ids) == baseline_hidden:
                return
        state = {"hidden_elem_ids": sorted(self._hidden_elem_ids)}
        if camera:
            state["camera_position"] = camera
        for note in save_view_state(self._source_label, state):
            self.log(note, "warn")

    def _restore_camera(self, camera: Any) -> None:
        """Restore a saved camera position, best effort (bad data is ignored)."""
        if not camera:
            return
        with contextlib.suppress(Exception):
            self._interactor.camera_position = camera
