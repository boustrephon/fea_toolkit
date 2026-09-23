"""Main application window for the fea_toolkit desktop GUI.

Milestone 2 builds the application *chrome*: the menubar, toolbars, dock
layout, message log and status bar around the central 3-D viewport that
Milestone 1 embedded.  The window is deliberately **read-only**, and most
domain actions are present but disabled, each labelled with the milestone that
will wire it (``docs/gui_roadmap.md`` §9.6) -- a menu that appears later is
more jarring than a greyed-out one.

Live in M2: opening a model (``Open`` / :meth:`MainWindow.open_path`), the
camera view buttons, the orientation (view) cube, the message log, the units
readout and the cursor-coordinate readout.
"""

import contextlib
from typing import Any, Optional

from qtpy.QtCore import Qt, QTimer, QUrl
from qtpy.QtGui import QAction, QDesktopServices, QKeySequence
from qtpy.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .render_backend import QtRenderBackend
from .views.message_log import MessageLog

_PROJECT_URL = "https://github.com/boustrephon/fea_toolkit"
_CURSOR_POLL_MS = 60


class MainWindow(QMainWindow):
    """Top-level window hosting the 3-D viewport and the application chrome.

    Args:
        model: Optional model to render immediately -- a ``SAPModelData``, a
            ``MeshModel`` or an ``AnalysisBuilder``.
        parent: Optional Qt parent widget.
    """

    def __init__(self, model: Optional[Any] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("fea_toolkit")

        self._model: Any = None
        self._viewer: Any = None
        self._interactor: Any = None
        self._backend: Any = None
        self._cursor_timer: Optional[QTimer] = None
        self._cursor_tracking = False
        self._actions: dict = {}

        self._create_viewport()
        self._create_actions()
        self._build_menus()
        self._build_toolbars()
        self._build_docks()
        self._build_status_bar()
        self._decorate_view()
        self.log("Ready.")

        if model is not None:
            self.show_model(model)

    # ── Viewport ────────────────────────────────────────────────────

    def _create_viewport(self) -> None:
        """Create the embedded PyVistaQt interactor and its render backend."""
        from pyvistaqt import QtInteractor

        # Quad-view-ready container: holds a single viewport today, a grid of
        # them later (roadmap design rule 10).
        self._viewport_container = QWidget(self)
        self._viewport_layout = QVBoxLayout(self._viewport_container)
        self._viewport_layout.setContentsMargins(0, 0, 0, 0)
        self.setCentralWidget(self._viewport_container)

        self._interactor = QtInteractor(self._viewport_container)
        self._viewport_layout.addWidget(self._interactor)
        self._backend = QtRenderBackend(self._interactor)

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

    def _create_actions(self) -> None:
        """Create every action once so menus and toolbars can share them."""
        a = self._actions

        # ── Live in M2 ──
        a["file.open"] = self._real_action(
            "Open", self._on_open, shortcut="Ctrl+O", tip="Open a SAP2000 .s2k or JSON model"
        )
        a["file.quit"] = self._real_action("Quit", self.close, shortcut="Ctrl+Q")
        a["view.fit"] = self._real_action("Zoom to fit", self._on_zoom_fit)
        a["view.iso"] = self._real_action("Isometric", self._view_iso, tip="Isometric view")
        a["view.xy"] = self._real_action("Top (XY)", self._view_xy, tip="Look down the Z axis")
        a["view.xz"] = self._real_action("Front (XZ)", self._view_xz, tip="Look along the Y axis")
        a["view.yz"] = self._real_action("Side (YZ)", self._view_yz, tip="Look along the X axis")
        a["help.docs"] = self._real_action("Documentation", self._on_docs)
        a["help.about"] = self._real_action("About fea_toolkit", self._on_about)

        # ── Greyed placeholders (each names its milestone) ──
        a["file.save_results"] = self._placeholder("Save results", "Milestone 7")
        a["file.export_tcl"] = self._placeholder("Export Tcl", "Milestone 7")
        a["file.export_image"] = self._placeholder("Export screenshot", "Milestone 7")
        a["edit.copy"] = self._placeholder("Copy", "Milestone 3")
        a["edit.preferences"] = self._placeholder("Preferences", "Milestone 8")
        a["view.show_nodes"] = self._placeholder("Show nodes", "Milestone 3")
        a["view.show_shells"] = self._placeholder("Show shells", "Milestone 3")
        a["view.show_labels"] = self._placeholder("Show element labels", "Milestone 3")
        a["view.show_loads"] = self._placeholder("Show loads", "Milestone 6")
        a["view.show_forces"] = self._placeholder("Show force diagrams", "Milestone 7")
        a["view.reset_layout"] = self._placeholder("Reset layout", "Milestone 8")
        a["model.mesh"] = self._placeholder("Mesh", "Milestone 3")
        a["model.split"] = self._placeholder("Split elements", "Milestone 3")
        a["model.selections"] = self._placeholder("Selections", "Milestone 3")
        a["model.units"] = self._placeholder("Units", "Milestone 3")
        a["analysis.run"] = self._placeholder("Run", "Milestone 5")
        a["analysis.static"] = self._placeholder("Static analysis", "Milestone 5")
        a["analysis.modal"] = self._placeholder("Modal analysis", "Milestone 5")
        a["analysis.spectrum"] = self._placeholder("Response spectrum", "Milestone 5")
        a["analysis.pushover"] = self._placeholder("Pushover", "Milestone 5")
        a["analysis.stop"] = self._placeholder("Stop", "Milestone 5")
        a["results.deformed"] = self._placeholder("Deformed shape", "Milestone 7")
        a["results.forces"] = self._placeholder("Force diagrams", "Milestone 7")
        a["results.storey"] = self._placeholder("Storey response", "Milestone 7")
        a["results.pushover_curve"] = self._placeholder("Pushover curve", "Milestone 7")
        a["results.clear"] = self._placeholder("Clear results", "Milestone 7")

    # ── Menus + toolbars ────────────────────────────────────────────

    def _build_menus(self) -> None:
        a = self._actions
        bar = self.menuBar()

        m = bar.addMenu("&File")
        m.addAction(a["file.open"])
        m.addSeparator()
        for key in ("file.save_results", "file.export_tcl", "file.export_image"):
            m.addAction(a[key])
        m.addSeparator()
        m.addAction(a["file.quit"])

        m = bar.addMenu("&Edit")
        m.addAction(a["edit.copy"])
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
        m.addSeparator()
        m.addAction(a["view.reset_layout"])

        m = bar.addMenu("&Model")
        for key in ("model.mesh", "model.split", "model.selections", "model.units"):
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
            "results.deformed",
            "results.forces",
            None,
            "model.units",
        ):
            main.addSeparator() if key is None else main.addAction(a[key])
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
            view.addSeparator() if key is None else view.addAction(a[key])
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
        trees = QTabWidget(self)
        trees.setObjectName("tabs_trees")
        trees.addTab(self._placeholder_panel("Model tree", "Milestone 3"), "Model Tree")
        trees.addTab(self._placeholder_panel("Property tree", "Milestone 3"), "Property Tree")
        self._tree_dock = QDockWidget("Model", self)
        self._tree_dock.setObjectName("dock_trees")
        self._tree_dock.setWidget(trees)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._tree_dock)

        self._inspector_dock = QDockWidget("Inspector", self)
        self._inspector_dock.setObjectName("dock_inspector")
        self._inspector_dock.setWidget(self._placeholder_panel("Property inspector", "Milestone 3"))
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
        view cube.  The cursor readout needs an *interactive* viewport, so its
        timer is created here but started from :meth:`showEvent`: pyvista's
        ``track_mouse_position`` raises ``RuntimeError`` on a non-interactive
        (offscreen / never-shown) plotter.
        """
        self._interactor.add_camera_orientation_widget()
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setInterval(_CURSOR_POLL_MS)
        self._cursor_timer.timeout.connect(self._update_cursor_position)

    def showEvent(self, event):
        """Enable the live cursor readout once the viewport is interactive."""
        super().showEvent(event)
        if self._cursor_tracking:
            return
        self._cursor_tracking = True
        try:
            self._interactor.track_mouse_position()
        except RuntimeError:
            # Offscreen viewports are not interactive, so pyvista refuses to
            # install the mouse observer.  The readout is cosmetic -- log it.
            self.log("Cursor readout unavailable (non-interactive viewport).", "warn")
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

    def show_model(self, model: Any, color_by_section: bool = True) -> None:
        """Render *model* into the embedded viewport.

        Args:
            model: A ``SAPModelData``, a ``MeshModel`` or an ``AnalysisBuilder``.
            color_by_section: Colour elements by section name.
        """
        from ..model.mesh_model import MeshModel
        from ..model.sap_data import SAPModelData
        from ..plotting.viewer import ModelViewer

        if isinstance(model, MeshModel):
            viewer = ModelViewer(mesh_model=model, backend=self._backend)
        elif isinstance(model, SAPModelData):
            viewer = ModelViewer(model_data=model, backend=self._backend)
        else:
            viewer = ModelViewer(builder=model, backend=self._backend)

        viewer.show_model(show_nodes=True, color_by_section=color_by_section)
        self._viewer = viewer
        self._model = model
        self._interactor.reset_camera()
        self._update_units_label()
        self.log("Displayed model geometry.")

    def _update_units_label(self) -> None:
        """Show the model's unit system in the status bar."""
        md = getattr(self._model, "model", self._model)  # unwrap an AnalysisBuilder
        units = getattr(md, "units", None)
        if not units:
            self._units_label.setText("units \u2014")
            return
        self._units_label.setText(
            f"{units.get('F', '?')} \u00b7 {units.get('L', '?')} \u00b7 {units.get('T', '?')}"
        )

    # ── Logging ─────────────────────────────────────────────────────

    def log(self, message: str, level: str = "info") -> None:
        """Append a line to the message log (``info`` | ``warn`` | ``error``)."""
        log_widget = getattr(self, "_message_log", None)
        if log_widget is not None:
            log_widget.log(message, level)

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
            "About fea_toolkit",
            f"<b>fea_toolkit</b> {version}<br><br>"
            "A FEA to OpenSees/Rhino conversion toolkit.<br>"
            "GUI: Milestone 2 (application chrome).",
        )

    # ── Teardown ────────────────────────────────────────────────────

    def closeEvent(self, event):
        """Stop the cursor timer and release the VTK render window."""
        if self._cursor_timer is not None:
            self._cursor_timer.stop()
        if self._interactor is not None:
            with contextlib.suppress(Exception):
                self._interactor.close()
        super().closeEvent(event)
