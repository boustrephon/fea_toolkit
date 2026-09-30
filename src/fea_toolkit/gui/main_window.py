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
from qtpy.QtGui import QAction, QDesktopServices, QKeySequence
from qtpy.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QLabel,
    QMainWindow,
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
from .controllers.interaction import load_policy
from .controllers.marquee import point_in_rect, polygon_hits_rect, segment_hits_rect
from .controllers.selection import CATEGORY_GROUPS, CATEGORY_NOUNS, SelectionIndex
from .controllers.view_registry import FIGURE, GEOMETRY, RESULTS, TABLE, View, ViewRegistry
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

#: Default size for a results overlay, as a **percentage of the model diagonal**.
#: Both the deformed shape and the force-diagram flags auto-scale to this fraction
#: of the model's size, so a millimetre model and a metre model read the same way
#: and neither a large displacement nor a large force overwhelms the scene (a raw
#: "amplification ×N" cannot do that — see ``ModelViewer.overlay_forces``).
_DEFAULT_OVERLAY_PCT = 10.0

#: Which end-force component a flag diagram opens on.  This is the **label**,
#: not the schema key: ``"M3"`` is SAP's local-DOF name for the moment about
#: local 3 (``"Mz"`` underneath — see
#: :data:`~fea_toolkit.model.sap_data.FORCE_QUANTITY_LABELS`), and bending is
#: what a flag diagram is usually read for.
_DEFAULT_FORCE_QUANTITY = "M3"

#: Default opacity for area elements.  Slabs are drawn translucent so the joints
#: and the members behind them stay readable — the reason a modeller reaches for
#: the opacity control at all.
_DEFAULT_SHELL_OPACITY = 0.7


def _case_of(view: Optional[View]) -> str:
    """The load case a results view stands for (``""`` for any other view).

    The case is carried in the view's key (``"results:DEAD"``), which is what
    :meth:`MainWindow.open_results_path` registers; this is its inverse.
    """
    if view is None or not view.key.startswith(_RESULTS_KEY_PREFIX):
        return ""
    return view.key[len(_RESULTS_KEY_PREFIX) :]


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
        self._default_style: Any = None  # interactor style to restore after Select mode
        self._worker: Any = None
        self._policy, self._policy_notes = load_policy()
        self._cursor_timer: Optional[QTimer] = None
        self._interaction_enabled = False
        self._actions: dict = {}
        self._stack: Any = None
        self._table_page: Any = None
        self._figure_page: Any = None

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
        self._store = None
        self._views = ViewRegistry()
        if isinstance(model, SAPModelData):
            self._store = InMemoryModelStore(model, source=source)
            self._views = ViewRegistry(self._store)
            self._views.add_geometry("unprocessed", "Unprocessed", model, source=source)
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
        self._mouse_filter = install_mouse_filter(self._interactor, self._interaction, self)

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
        """Rubber-band select everything whose projection the rectangle touches.

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
        frames, areas, nodes = self._marquee_entities(x0, y0, x1, y1)
        self._select_many(frames, areas, nodes)
        total = len(frames) + len(areas) + len(nodes)
        self.log(f"Marquee selected {total} entit{'y' if total == 1 else 'ies'}.")

    def _marquee_entities(self, x0: float, y0: float, x1: float, y1: float) -> tuple:
        """``(frame_ids, area_ids, node_ids)`` sets whose projection is in the box."""
        import vtk

        renderer = self._interactor.renderer

        def project(world: Any) -> tuple:
            coord = vtk.vtkCoordinate()
            coord.SetCoordinateSystemToWorld()
            coord.SetValue(*[float(v) for v in world])
            disp = coord.GetComputedDisplayValue(renderer)
            return float(disp[0]), float(disp[1])

        index = self._selection_index
        frames = {
            f.elem_id
            for f in index.frames
            if segment_hits_rect(*project(f.start), *project(f.end), x0, y0, x1, y1)
        }
        areas = {
            s.area_id
            for s in index.shells
            if polygon_hits_rect([project(v) for v in s.vertices], x0, y0, x1, y1)
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
        model.select(selection, QItemSelectionModel.SelectionFlag.Select)
        if last_index is not None:
            model.setCurrentIndex(last_index, QItemSelectionModel.SelectionFlag.NoUpdate)

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
        a["file.save_results"] = self._real_action(
            "Save results\u2026",
            self._on_save_results,
            tip="Write the displayed result to an NPZ archive",
        )
        a["file.save_results"].setEnabled(False)
        a["file.export_tcl"] = self._placeholder("Export Tcl", "Milestone 7")
        a["file.export_image"] = self._placeholder("Export screenshot", "Milestone 7")
        a["edit.copy"] = self._placeholder("Copy", "a future release")
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
        a["view.show_restraints"] = self._toggle_action(
            "Show restraints",
            self._on_show_restraints,
            tip="Show or hide support symbols at restrained nodes",
        )
        a["view.show_labels"] = self._placeholder("Show element labels", "P23")
        a["view.show_loads"] = self._placeholder("Show loads", "Milestone 6")
        a["view.show_forces"] = self._placeholder("Show force diagrams", "Milestone 7")
        a["view.clear_highlights"] = self._real_action(
            "Clear highlights", self._on_clear_highlights, tip="Drop the selection highlight"
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
        a["analysis.modal"] = self._placeholder("Modal analysis", "Milestone 5")
        a["analysis.spectrum"] = self._placeholder("Response spectrum", "Milestone 5")
        a["analysis.pushover"] = self._placeholder("Pushover", "Milestone 5")
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
        m.addSeparator()
        for key in ("file.save_results", "file.export_tcl", "file.export_image"):
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
        m.addSeparator()
        camera = m.addMenu("Camera")
        for key in ("view.iso", "view.xy", "view.xz", "view.yz"):
            camera.addAction(a[key])
        display = m.addMenu("Display")
        for key in (
            "view.show_nodes",
            "view.show_shells",
            "view.show_frames",
            "view.show_restraints",
            "view.show_labels",
            "view.show_loads",
            "view.show_forces",
        ):
            display.addAction(a[key])
        display.addSeparator()
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
        self._deformed_scale.setValue(_DEFAULT_OVERLAY_PCT)
        self._deformed_scale.setSuffix("%")
        self._deformed_scale.setToolTip("Deformed-shape size (% of model diagonal)")
        self._deformed_scale.setStatusTip("Deformed-shape size (% of model diagonal)")
        self._deformed_scale.valueChanged.connect(self._on_deformed_scale_changed)

        self._force_scale = QDoubleSpinBox(self)
        self._force_scale.setObjectName("force_scale")
        self._force_scale.setRange(0.1, 1000.0)
        self._force_scale.setDecimals(1)
        self._force_scale.setSingleStep(5.0)
        self._force_scale.setValue(_DEFAULT_OVERLAY_PCT)
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
        self._force_quantity.setCurrentText(_DEFAULT_FORCE_QUANTITY)
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
        self._shell_opacity.setValue(_DEFAULT_SHELL_OPACITY)
        self._shell_opacity.setToolTip("Opacity of area elements (shells)")
        self._shell_opacity.setStatusTip("Opacity of area elements (shells)")
        self._shell_opacity.valueChanged.connect(self._on_shell_opacity_changed)

        self._shrink = QDoubleSpinBox(self)
        self._shrink.setObjectName("shrink")
        self._shrink.setRange(0.5, 1.0)
        self._shrink.setDecimals(2)
        self._shrink.setSingleStep(0.05)
        self._shrink.setValue(1.0)
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

        camera = None
        if not reset_view:
            with contextlib.suppress(Exception):
                camera = self._interactor.camera_position

        # A new model replaces the previous scene outright -- the backend
        # *appends* actors, so without this the old geometry would linger
        # behind the new one.  A results overlay belongs to *one* view's
        # geometry, so the toggle is reset along with it.
        self._reset_results_overlay()
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
            show_nodes=True,
            color_by_section=color_by_section,
            shell_opacity=float(self._shell_opacity.value()),
            shrink=float(self._shrink.value()),
            show_restraints=self._actions["view.show_restraints"].isChecked(),
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
            self._tree_model.set_model(model, self._views.views())
        self._reset_display_toggles()
        if camera is None:
            self._interactor.reset_camera()
        else:
            self._interactor.camera_position = camera
        self._update_units_label()
        self._set_model_actions_enabled(self._store is not None)
        self._set_analysis_actions_enabled()
        self._set_view_actions_enabled()
        self.log("Displayed model geometry.")

    def _reset_display_toggles(self) -> None:
        """Re-check the display toggles to match freshly rendered geometry.

        Every overlay is drawn again by :meth:`show_model`, so a toggle left
        unchecked by the previous model would otherwise contradict what is on
        screen.  Signals are blocked: there is nothing to re-render yet.
        """
        for key in (
            "view.show_nodes",
            "view.show_frames",
            "view.show_shells",
            "view.show_restraints",
        ):
            action = self._actions[key]
            action.blockSignals(True)
            action.setChecked(True)
            action.blockSignals(False)

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
            self._show_table_view(view)
        elif view.kind == FIGURE:
            self._show_figure_view(view)
        else:
            return
        self._inspector.show_object(self._views.get(view.key))
        self.log(f"Showing view: {view.name}.")

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

        The page is created once and re-used; the canvas is rebuilt per view
        because a figure belongs to one result, not to the window.
        """
        figure = self._views.figure(view.key)
        if figure is None:
            return
        if self._figure_page is None:
            self._figure_page = QWidget(self)
            self._figure_layout = QVBoxLayout(self._figure_page)
            self._figure_layout.setContentsMargins(0, 0, 0, 0)
            self._stack.addWidget(self._figure_page)

        while self._figure_layout.count():
            item = self._figure_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

        canvas = FigureCanvasQTAgg(figure)
        self._figure_layout.addWidget(canvas)
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
            active is not None and active.parent is not None
        )
        # A deformed shape needs a results view that carries displacement:
        # a pressed button that could only report "nothing to draw" is worse
        # than a disabled one that is greyed until it can work.
        repository = self._views.results(active.key) if active is not None else None
        case = _case_of(active)
        self._actions["results.deformed"].setEnabled(
            repository is not None and bool(case) and repository.has_displacements(case)
        )
        # The same rule for the flag diagram, on end forces — and the quantity
        # selector is greyed with it, so it cannot look like it is choosing
        # something the archive has none of.
        has_forces = repository is not None and bool(case) and repository.has_forces(case)
        self._actions["results.forces"].setEnabled(has_forces)
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
        self._clear_deformed()
        self._set_toggle_checked("results.deformed", False)
        self._clear_forces()
        self._set_toggle_checked("results.forces", False)
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

    def _on_forces_toggled(self, checked: bool) -> None:
        """**Results ▸ Force diagrams**: flag the active case's end forces.

        The chosen quantity is a *label*: the selector shows SAP's local-DOF
        names and this reads the schema key underneath, so ``"M3"`` becomes
        ``"Mz"`` (see
        :data:`~fea_toolkit.model.sap_data.FORCE_QUANTITY_LABELS`).  As with the
        deformed shape the amplification is a *display* choice — the archive's
        forces are read in model units and never modified.

        Args:
            checked: Whether the diagram should now be drawn.
        """
        if not checked:
            self._clear_forces()
            return

        forces = self._active_element_forces()
        if not forces:
            self.log("No element forces in this view — nothing to draw.", "error")
            self._set_toggle_checked("results.forces", False)
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

    def _reset_results_overlay(self) -> None:
        """Forget the results overlay whose scene :meth:`show_model` replaced.

        The backend's ``clear()`` has already removed the actor, so this only
        has to put the toggle back — hence ``blockSignals`` in
        :meth:`_set_toggle_checked`: re-entering the handler would try to
        clear an actor that is gone.
        """
        self._set_toggle_checked("results.deformed", False)
        self._set_toggle_checked("results.forces", False)

    def _on_show_nodes(self, checked: bool) -> None:
        """Show or hide the node-marker overlay."""
        self._backend.set_category_visible("nodes", checked)

    def _on_show_shells(self, checked: bool) -> None:
        """Show or hide the area-element overlay."""
        self._backend.set_category_visible("shells", checked)

    def _on_show_frames(self, checked: bool) -> None:
        """Show or hide the frame-element overlay."""
        self._backend.set_category_visible("frames", checked)

    def _on_show_restraints(self, checked: bool) -> None:
        """Show or hide the support symbols."""
        self._backend.set_category_visible("restraints", checked)

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
        self._mesh_preset_step(edited, label)

    def _mesh_preset_step(self, step: Any, label: str) -> None:
        """Append *step* to the recipe and run it — the Model-menu preset pattern.

        P32's analysis presets (Modal / Response spectrum / Pushover) will use
        the same pattern: compose a step in a dialog, append it, and run the
        recipe, so an analysis is a visible, editable step like any other.

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

        self._mesh_preset_step(Step(verb="mesh", params=config), label)

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
        label = getattr(self, "_pending_label", None) or f"Running recipe ({len(recipe)} steps)"
        self._pending_label = None

        self._set_model_actions_enabled(False)
        self._progress.setRange(0, 0)  # busy: a recipe reports no fine-grained progress
        self._progress.setVisible(True)
        self.log(f"{label} \u2026")

        def task(should_cancel):
            run = run_recipe(
                recipe,
                model_data,
                model=starting_mesh,
                cancel=should_cancel,
                log=messages.append,
            )
            return run, messages

        _freeze_gc_once()
        worker = TaskWorker(task, parent=self)
        worker.succeeded.connect(self._recipe_finished)
        worker.failed.connect(self._preprocess_failed)
        worker.finished.connect(self._preprocess_ended)
        self._worker = worker
        worker.start()

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

        run, messages = payload
        for message in messages:
            self.log(message)
        for result in run.results:
            if result.kind == "geometry":
                self._show_geometry_result(result.label, result.payload)
            elif result.kind == CASES:
                added = self._show_results_result(result.label, result.payload)
                if not added and not run.cancelled:
                    self.log(f"{result.label}: the run produced no results.", "warn")
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
        key = f"table:{label.lower().replace(' ', '-')}"
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

    def _show_results_result(self, label: str, arrays: Any) -> list:
        """Register each case in a recipe's results as a view, and show the first.

        Both ``cases`` verbs — a solve and a combination reduction — hand back
        an in-memory results archive, so this is the same path the **Analysis ▸
        Run** results take: a
        :class:`~fea_toolkit.io.results_repository.NpzResultsRepository` over
        the array dict, one view per case, nothing written to disk.  Cases
        replace by key, so re-running a recipe refreshes its views instead of
        piling them up.

        Args:
            label: The step's result label, e.g. ``"Static cases"`` — recorded
                as the view's provenance.
            arrays: The archive arrays the step produced.

        Returns:
            The case labels registered, in archive order — empty when the
            archive holds no case.
        """
        from ..io.results_repository import NpzResultsRepository

        repository = NpzResultsRepository(arrays or {})
        cases = list(repository.cases())
        if not cases:
            return []

        # The mesh the run was prepared from is what the results are keyed to,
        # so prefer it; an archive also carries its own display geometry, which
        # is what makes the view drawable with nothing else loaded.
        mesh = self._store.preprocessed() if self._store is not None else None
        model = mesh if mesh is not None else repository.as_model()
        for case in cases:
            self._views.add_results(
                f"{_RESULTS_KEY_PREFIX}{case}",
                _case_view_name(case, repository.case_meta(case)),
                repository,
                source=f"Recipe: {label} \u00b7 {case}",
                model=model,
                activate=False,
            )
        self.log(f"{label}: {len(cases)} case(s) added as views.")

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
        self.show_model(mesh_model, reset_view=False)
        self.log(f"Added view: {name}.")

    def _preprocess_failed(self, message: str) -> None:
        """Report a preprocessing failure, leaving the display alone."""
        self.log(f"Preprocessing failed: {message}", "error")

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
        action = self._actions["analysis.run"]
        action.setEnabled(mesh is not None and not running)
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
        self._remember_source(model, source=path)
        self.show_model(model)
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
