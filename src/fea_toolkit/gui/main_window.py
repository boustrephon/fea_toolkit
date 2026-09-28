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

from qtpy.QtCore import QItemSelectionModel, Qt, QTimer, QUrl
from qtpy.QtGui import QAction, QDesktopServices, QKeySequence
from qtpy.QtWidgets import (
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QTabWidget,
    QToolBar,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from .app import APP_NAME
from .controllers.interaction import load_policy
from .controllers.selection import SelectionIndex
from .controllers.view_registry import View, ViewRegistry
from .controllers.worker import TaskWorker
from .models.tree_model import ModelTreeModel
from .render_backend import QtRenderBackend
from .views.interactor import PickResult, ViewportInteraction
from .views.message_log import MessageLog
from .views.property_inspector import PropertyInspector
from .views.qt_mouse import install_mouse_filter

_PROJECT_URL = "https://github.com/boustrephon/fea_toolkit"
_CURSOR_POLL_MS = 60
_SELECT_COLOR = (1.0, 0.45, 0.0)  # selected frame / area element
_SELECT_NODE_COLOR = (0.15, 0.55, 1.0)  # selected node

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

#: Default amplification for the deformed-shape overlay.  A real transverse
#: displacement is a small fraction of the model's size, so the shape is
#: invisible 1:1 — the same reason SAP2000 offers a scale box.
_DEFAULT_DEFORMED_SCALE = 50.0


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
        self._worker: Any = None
        self._policy, self._policy_notes = load_policy()
        self._cursor_timer: Optional[QTimer] = None
        self._interaction_enabled = False
        self._actions: dict = {}

        self._create_viewport()
        self._create_actions()
        self._build_menus()
        self._build_toolbars()
        self._build_docks()
        self._build_status_bar()
        self._set_model_actions_enabled(False)
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
        """Create the embedded PyVistaQt interactor and its render backend."""
        from .render_backend import MainThreadQtInteractor

        # Quad-view-ready container: holds a single viewport today, a grid of
        # them later (roadmap design rule 10).
        self._viewport_container = QWidget(self)
        self._viewport_layout = QVBoxLayout(self._viewport_container)
        self._viewport_layout.setContentsMargins(0, 0, 0, 0)
        self.setCentralWidget(self._viewport_container)

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
        )
        self._mouse_filter = install_mouse_filter(self._interactor, self._interaction, self)

    def _on_viewport_pick(self, result: PickResult) -> None:
        """Select whatever a click found; a click on nothing clears the selection.

        Args:
            result: The pick from the interaction adapter (``hit`` is ``False``
                when the click met no geometry).
        """
        if self._viewer is None or self._selection_index is None:
            return
        category = self._backend.category_of_actor(result.actor) if result.hit else None
        label = self._selection_index.label(category, result.index) if category else None
        if label is None:
            self._clear_selection()
            return
        group_key = self._selection_index.group_key(category)
        if self._select_entity_in_tree(group_key, label):
            self.log(f"Selected {label} in the tree from the viewport.")

    def _clear_selection(self) -> None:
        """Empty the inspector, the tree selection and the viewport highlight."""
        self._inspector.show_object(None)
        if self._viewer is not None:
            self._viewer.clear_highlights()
        selection = self._tree_view.selectionModel()
        if selection is not None:
            selection.clear()

    def _select_entity_in_tree(self, group_key: str, label: str) -> bool:
        """Expand *group_key*, select the row for *label* and scroll to it.

        Selecting the row drives the inspector and the viewport highlight
        through the ordinary tree wiring, so a pick refreshes all three views.

        Args:
            group_key: Group key, e.g. ``"frame_elements"``.
            label: The entity's SAP label.

        Returns:
            ``True`` when the row existed and was selected.
        """
        index = self._tree_model.index_for(group_key, label)
        if index is None:
            return False
        self._tree_view.expand(index.parent())
        self._tree_view.selectionModel().setCurrentIndex(
            index, QItemSelectionModel.SelectionFlag.ClearAndSelect
        )
        self._tree_view.scrollTo(index)
        return True

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
        a["file.save_results"] = self._placeholder("Save results", "Milestone 7")
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
        a["view.show_labels"] = self._placeholder("Show element labels", "P23")
        a["view.show_loads"] = self._placeholder("Show loads", "Milestone 6")
        a["view.show_forces"] = self._placeholder("Show force diagrams", "Milestone 7")
        a["view.clear_highlights"] = self._real_action(
            "Clear highlights", self._on_clear_highlights, tip="Drop the selection highlight"
        )
        a["view.reset_layout"] = self._placeholder("Reset layout", "Milestone 8")
        a["model.split"] = self._real_action(
            "Split elements",
            self._on_split_elements,
            tip="Preprocess: split frames at interior joints (runs on a worker)",
        )
        a["model.mesh"] = self._real_action(
            "Mesh areas",
            self._on_mesh_areas,
            tip="Preprocess: split frames and create shell elements for areas",
        )
        a["model.selections"] = self._placeholder("Selections", "a future release")
        a["model.units"] = self._placeholder("Units", "a future release")
        a["analysis.run"] = self._placeholder("Run", "Milestone 5")
        a["analysis.static"] = self._placeholder("Static analysis", "Milestone 5")
        a["analysis.modal"] = self._placeholder("Modal analysis", "Milestone 5")
        a["analysis.spectrum"] = self._placeholder("Response spectrum", "Milestone 5")
        a["analysis.pushover"] = self._placeholder("Pushover", "Milestone 5")
        a["analysis.stop"] = self._placeholder("Stop", "Milestone 5")
        a["results.deformed"] = self._toggle_action(
            "Deformed shape",
            self._on_deformed_toggled,
            tip="Draw the active load case's deformed shape, amplified by the scale",
            checked=False,
            enabled=False,
        )
        a["results.forces"] = self._placeholder("Force diagrams", "Milestone 7")
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
            "view.show_labels",
            "view.show_loads",
            "view.show_forces",
        ):
            display.addAction(a[key])
        display.addSeparator()
        display.addAction(a["view.clear_highlights"])
        m.addSeparator()
        m.addAction(a["view.reset_layout"])

        m = bar.addMenu("&Model")
        for key in ("model.split", "model.mesh", "model.selections", "model.units"):
            m.addAction(a[key])

        m = bar.addMenu("&Analysis")
        for key in ("analysis.static", "analysis.modal", "analysis.spectrum", "analysis.pushover"):
            m.addAction(a[key])
        m.addSeparator()
        m.addAction(a["analysis.stop"])

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

        # The deformed-shape scale sits on the toolbar rather than in a dialog:
        # it is adjusted *while* looking at the shape, and re-drawing on every
        # change is what makes finding a readable amplification quick.
        self._deformed_scale = QDoubleSpinBox(self)
        self._deformed_scale.setObjectName("deformed_scale")
        self._deformed_scale.setRange(1.0, 10000.0)
        self._deformed_scale.setDecimals(1)
        self._deformed_scale.setSingleStep(10.0)
        self._deformed_scale.setValue(_DEFAULT_DEFORMED_SCALE)
        self._deformed_scale.setToolTip("Deformed-shape scale factor")
        self._deformed_scale.setStatusTip("Deformed-shape scale factor")
        self._deformed_scale.valueChanged.connect(self._on_deformed_scale_changed)

        def _put(bar: QToolBar, key: Any) -> None:
            """Add one entry: ``None`` is a separator, ``"@name"`` a widget."""
            if key is None:
                bar.addSeparator()
            elif key == "@deformed_scale":
                bar.addWidget(QLabel("Scale"))
                bar.addWidget(self._deformed_scale)
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
            "model.split",
            "model.mesh",
            "results.deformed",
            "@deformed_scale",
            "results.forces",
            None,
            "model.units",
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
            None,
            "view.show_nodes",
            "view.show_shells",
            "view.show_labels",
            "view.show_loads",
            "view.show_forces",
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
        self._tree_view.selectionModel().currentChanged.connect(self._on_tree_selection)

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

        viewer.show_model(show_nodes=True, color_by_section=color_by_section)
        self._viewer = viewer
        self._model = model
        self._selection_index = SelectionIndex.from_viewer(viewer)
        if rebuild_tree:
            self._tree_model.set_model(model, self._views.views())
        self._reset_display_toggles()
        if camera is None:
            self._interactor.reset_camera()
        else:
            self._interactor.camera_position = camera
        self._update_units_label()
        self._set_model_actions_enabled(self._store is not None)
        self._set_view_actions_enabled()
        self.log("Displayed model geometry.")

    def _reset_display_toggles(self) -> None:
        """Re-check the display toggles to match freshly rendered geometry.

        Every overlay is drawn again by :meth:`show_model`, so a toggle left
        unchecked by the previous model would otherwise contradict what is on
        screen.  Signals are blocked: there is nothing to re-render yet.
        """
        for key in ("view.show_nodes", "view.show_shells"):
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

        Args:
            view: The view to display.
            rebuild_tree: Rebuild the Model Tree.  Needed when the view's *name*
                changed (the row's label is the name); a rebuild invalidates the
                current index, so it is skipped for a plain switch — otherwise Qt
                reports an empty selection and the Inspector is cleared again.
        """
        source = self._views.source(view.key)
        if source is None:
            return
        self._views.set_active(view.key)
        self.show_model(
            source,
            reset_view=False,
            rebuild_tree=rebuild_tree,
            selection=view.selection,
        )
        self._inspector.show_object(self._views.get(view.key))
        self.log(f"Showing view: {view.name}.")

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
            self._set_deformed_checked(False)
            return

        self._viewer.overlay_deformed(displacements, scale=float(self._deformed_scale.value()))
        self._viewer.show()
        self.log(f"Deformed shape: scale {self._deformed_scale.value():g}.")

    def _on_deformed_scale_changed(self, _value: float) -> None:
        """Re-draw the deformed shape after the scale changed.

        Only when it is already on — changing the scale must not *start*
        drawing, which would make the spin box a second way to trigger the
        action.
        """
        if self._actions["results.deformed"].isChecked():
            self._clear_deformed()
            self._on_deformed_toggled(True)

    def _on_clear_results(self) -> None:
        """**Results ▸ Clear results**: remove every results overlay."""
        self._clear_deformed()
        self._set_deformed_checked(False)
        self.log("Cleared the results overlay.")

    def _active_displacements(self) -> dict:
        """``{node_id: (dx, dy, dz)}`` for the active results view, else ``{}``."""
        view = self._views.active
        if view is None:
            return {}
        repository = self._views.results(view.key)
        case = _case_of(view)
        if repository is None or not case:
            return {}
        return repository.nodal_displacements(case)

    def _clear_deformed(self) -> None:
        """Remove the deformed overlay, leaving the model itself drawn."""
        if self._viewer is not None:
            self._viewer.clear_deformed()

    def _set_deformed_checked(self, checked: bool) -> None:
        """Set the deformed toggle without re-entering its handler."""
        action = self._actions.get("results.deformed")
        if action is None or action.isChecked() == checked:
            return
        action.blockSignals(True)
        action.setChecked(checked)
        action.blockSignals(False)

    def _reset_results_overlay(self) -> None:
        """Forget the results overlay whose scene :meth:`show_model` replaced.

        The backend's ``clear()`` has already removed the actor, so this only
        has to put the toggle back — hence ``blockSignals`` in
        :meth:`_set_deformed_checked`: re-entering the handler would try to
        clear an actor that is gone.
        """
        self._set_deformed_checked(False)

    def _on_show_nodes(self, checked: bool) -> None:
        """Show or hide the node-marker overlay."""
        self._backend.set_category_visible("nodes", checked)

    def _on_show_shells(self, checked: bool) -> None:
        """Show or hide the area-element overlay."""
        self._backend.set_category_visible("shells", checked)

    # ── Preprocessing (Model menu) ───────────────────────────────────

    def _on_split_elements(self) -> None:
        """**Model ▸ Split elements**: preprocess, splitting frames at joints."""
        self._start_preprocess(
            {"split_elements": True},
            "Splitting elements at joints",
            key="processed",
            name="Processed",
            source="Preprocessor: split_elements",
        )

    def _on_mesh_areas(self) -> None:
        """**Model ▸ Mesh areas**: preprocess with shells (area meshing)."""
        self._start_preprocess(
            {"split_elements": True, "create_shells": True},
            "Splitting elements at joints and meshing areas",
            key="meshed",
            name="Meshed",
            source="Preprocessor: split_elements + create_shells",
        )

    def _start_preprocess(
        self,
        config: dict,
        label: str,
        *,
        key: str,
        name: str,
        source: str,
    ) -> None:
        """Run the Preprocessor on a worker and register the result as a view.

        The Preprocessor is pure topology — it never calls OpenSees — but it
        still runs off the GUI thread: splitting and area meshing a large model
        is not instant (``docs/gui_roadmap.md`` → *Threading model*).

        Args:
            config: Preprocessor configuration, e.g. ``{"split_elements": True}``.
            label: What is happening, for the message log.
            key: View key to register the result under — replacing any earlier
                run of the same kind, so views do not pile up.
            name: Display name for that view.
            source: Provenance recorded on the view.
        """
        if self._store is None:
            self.log("Open a SAP2000 model before preprocessing.", "warn")
            return
        if self._worker is not None and self._worker.isRunning():
            self.log("Preprocessing is already running.", "warn")
            return

        store = self._store
        settings = dict(config)
        self._set_model_actions_enabled(False)
        self._progress.setRange(0, 0)  # busy: the preprocessor reports no progress
        self._progress.setVisible(True)
        self.log(f"{label} \u2026")

        _freeze_gc_once()
        worker = TaskWorker(lambda _should_cancel: store.mesh(settings), parent=self)
        worker.succeeded.connect(
            lambda mesh_model: self._preprocess_finished(mesh_model, key, name, source)
        )
        worker.failed.connect(self._preprocess_failed)
        worker.finished.connect(self._preprocess_ended)
        self._worker = worker
        worker.start()

    def _preprocess_finished(self, mesh_model: Any, key: str, name: str, source: str) -> None:
        """Report what changed, register the result as a view and display it.

        Splitting is *opt-in per element* in the model itself (SAP2000's
        auto-mesh flags: ``AtJoints`` / ``AtFrames``), so a model that asks for
        nothing is legitimately unchanged — say so rather than implying a
        failure.

        Args:
            mesh_model: The ``MeshModel`` produced on the worker.
            key: View key to register it under.
            name: Display name for the view.
            source: Provenance recorded on the view.
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
        self._views.add_geometry(key, name, mesh_model, source=source)
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
        gc.collect()

    def _set_model_actions_enabled(self, enabled: bool) -> None:
        """Enable the preprocessing actions — they need a parsed source model.

        Args:
            enabled: Whether a ``SAPModelData`` is loaded and can be preprocessed.
        """
        for key in ("model.split", "model.mesh"):
            self._actions[key].setEnabled(enabled)

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
