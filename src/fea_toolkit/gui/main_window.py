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
import math
from typing import Any, Optional

import numpy as np
from qtpy.QtCore import (
    QItemSelection,
    QItemSelectionModel,
    QItemSelectionRange,
    QMimeData,
    Qt,
    QTimer,
    QUrl,
)
from qtpy.QtGui import (
    QAction,
    QDesktopServices,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QGuiApplication,
    QKeySequence,
)
from qtpy.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QStackedWidget,
    QTabWidget,
    QToolBar,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ..io.model_loader import (
    ETABS_SUFFIXES,
    SAFE_DB_SUFFIXES,
    SAFE_SUFFIXES,
    TEXT_SUFFIXES,
)
from .app import APP_MISSION, APP_NAME
from .controllers.colour_by import (
    COLOUR_FIELDS,
    FIELD_LABELS,
    colour_by_field,
    frame_labels,
    shell_labels,
    story_levels,
)
from .controllers.interaction import default_config_path, marquee_mode
from .controllers.marquee import (
    point_in_rect,
    polygon_hits_rect,
    polygon_inside_rect,
    segment_hits_rect,
    segment_inside_rect,
)
from .controllers.selection import CATEGORY_GROUPS, CATEGORY_NOUNS, SelectionIndex, selection_set_op
from .controllers.settings import (
    load_gui_settings,
    load_opensees_setting,
    load_theme_settings,
    save_display_settings,
    theme_stylesheet,
)
from .controllers.view_registry import FIGURE, GEOMETRY, RESULTS, TABLE, View, ViewRegistry
from .controllers.view_state import clear_view_state, load_view_state, save_view_state
from .controllers.worker import TaskWorker
from .doc_links import DOCS_URL
from .models.tree_model import ModelTreeModel
from .render_backend import make_select_style
from .session import Session
from .views.interactor import PickResult, ViewportInteraction
from .views.message_log import MessageLog
from .views.property_inspector import PropertyInspector
from .views.qt_mouse import install_mouse_filter
from .views.quad_viewport import QuadViewport

_PROJECT_URL = "https://github.com/boustrephon/fea_toolkit"
#: File extensions dropped onto the window are opened as results archives.
_RESULTS_DROP_EXTENSIONS = (".npz", ".h5", ".hdf5")
#: File extensions dropped onto the window are opened as models — the CSi text
#: exports :func:`~fea_toolkit.io.model_loader.load_model_data` dispatches on,
#: plus the saved-document ``.json`` envelope.
_MODEL_DROP_EXTENSIONS = (
    *TEXT_SUFFIXES,
    *ETABS_SUFFIXES,
    *SAFE_SUFFIXES,
    *SAFE_DB_SUFFIXES,
    ".json",
)
#: File extensions dropped onto the window are opened as a project bundle — the
#: zip ``File ▸ Save project…`` writes, plus the legacy ``.fea`` spelling.
_PROJECT_DROP_EXTENSIONS = (".zip", ".fea")
#: Documentation pages the Help menu links to, keyed by short name — the MkDocs
#: site serves each ``docs/*.md`` as a ``<name>/`` directory URL.
_DOC_PAGES = {
    "gui": "gui/",
    "modeling": "modeling_guidance/",
    "benchmarks": "benchmark_register/",
    "llm": "llm_guide/",
}
_CURSOR_POLL_MS = 60
_SELECT_COLOR = (1.0, 1.0, 0.0)  # selected frame / area element — translucent yellow
_SELECT_NODE_COLOR = (1.0, 1.0, 0.0)  # selected node — the same yellow cue

#: Model-tree group key -> render category, the reverse of
#: ``controllers.selection.CATEGORY_GROUPS`` (which maps render category ->
#: group key).  Used to turn selected tree rows back into a highlight / a
#: ``Selection``.
_GROUP_CATEGORY = {group: category for category, group in CATEGORY_GROUPS.items()}

#: Model-tree group keys whose right-click menu offers "Colour by …".
_COLOURABLE_GROUPS = frozenset(
    {"materials", "sections", "stories", "frame_elements", "area_elements"}
)


def _menu_header(text: str) -> QAction:
    """A disabled menu action used as a section heading in a menu."""
    action = QAction(text)
    action.setEnabled(False)
    return action


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


class _NldStepHistory:
    """Lazy per-step displacement frames for a nonlinear-dynamic result.

    A transient history is one 2-D array per node (``(n_steps, 3)``).  The
    per-step ``{node_id: (dx, dy, dz)}`` dicts an animation frame needs are
    materialised one at a time by :meth:`frame` — never eagerly — so a large
    model's history costs one array per node, not one dict per step.
    """

    __slots__ = ("_arrays",)

    def __init__(self, arrays: dict) -> None:
        self._arrays = arrays

    def __len__(self) -> int:
        return max((a.shape[0] for a in self._arrays.values()), default=0)

    def __bool__(self) -> bool:
        return self.__len__() > 0

    def frame(self, k: int) -> dict:
        """The ``{node_id: (dx, dy, dz)}`` field at step *k*."""
        field: dict = {}
        for node_id, arr in self._arrays.items():
            if k >= arr.shape[0]:
                continue
            field[node_id] = np.array([arr[k, 0], arr[k, 1], arr[k, 2]], dtype=float)
        return field

    def peak_frame(self) -> dict:
        """The step whose largest nodal displacement norm is greatest.

        Each node's displacement norms are computed once with NumPy over its
        whole history, then reduced per step across nodes — so an empty history
        returns ``{}`` and the *first* maximum step wins, exactly as the
        per-step / per-node loop did.
        """
        n_steps = self.__len__()
        if n_steps == 0:
            return {}
        step_max = np.full(n_steps, -np.inf)
        for arr in self._arrays.values():
            if arr.shape[0] == 0:
                continue
            norms = np.linalg.norm(arr[: arr.shape[0], :3], axis=1)
            step_max[: arr.shape[0]] = np.maximum(step_max[: arr.shape[0]], norms)
        return self.frame(int(np.argmax(step_max)))


def _frame_disp_norm(frame: dict) -> float:
    """The largest nodal displacement magnitude in a per-step frame."""
    if not frame:
        return 0.0
    best = 0.0
    for value in frame.values():
        arr = np.asarray(value, dtype=float)
        if arr.size >= 3:
            best = max(best, float(np.linalg.norm(arr[:3])))
        elif arr.size == 2:
            best = max(best, float(np.linalg.norm(arr[:2])))
    return best


def _step_frame_at(frames: Any, k: int) -> dict:
    """The per-step frame at index *k* — for a list or an :class:`_NldStepHistory`."""
    if isinstance(frames, _NldStepHistory):
        return frames.frame(k)
    return frames[k]


def _peak_step_frame(frames: Any) -> dict:
    """The step frame with the largest nodal displacement norm, else ``{}``."""
    if isinstance(frames, _NldStepHistory):
        return frames.peak_frame()
    if not frames:
        return {}
    return max(frames, key=_frame_disp_norm)


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
        # Accept a drag-and-drop of a results archive (.npz / .h5 / .hdf5).
        self.setAcceptDrops(True)

        self._model: Any = None
        self._session = Session()
        self._source_label = ""
        self._external_combinations: dict = {}
        self._viewer: Any = None
        self._interactor: Any = None
        self._quad: Any = None
        self._backend: Any = None
        self._selection_index: Any = None
        self._interaction: Any = None
        self._mouse_filter: Any = None
        #: The active colour-by field (``None`` = default section colouring).
        self._color_field: Optional[str] = None
        #: The camera and hidden set right after ``View ▸ Reset view``, so
        #: ``closeEvent`` can tell an unchanged view from one the user has moved.
        self._reset_view_baseline: Optional[tuple] = None
        self._default_styles: dict = {}  # interactor -> style, restored after Select mode
        self._worker: Any = None
        self._policy, self._display, self._policy_notes = load_gui_settings()
        self._theme, self._theme_notes = load_theme_settings()
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
        self._anim_peak_frame: dict = {}
        self._anim_phase: float = 0.0
        self._anim_frames: list = []
        self._anim_frame_index: int = 0
        self._anim_step_mode: bool = False
        self._anim_times: list = []
        self._nld_frames: dict = {}
        self._nld_times: dict = {}
        #: The window's named selections as they were when the recipe run started,
        #: used to merge only what the run changed on completion (see
        #: :meth:`_merge_recipe_named_selections`).  ``None`` outside a run.
        self._recipe_named_snapshot: Optional[dict] = None
        #: The store the recipe run was started against, used to reject a run
        #: whose document was switched away while it was in flight (see
        #: :meth:`_recipe_store_is_current`).  ``None`` outside a run.
        self._recipe_starting_store: Any = None
        #: The modeless "Open example" chooser (builders + benchmarks), built
        #: eagerly in :meth:`_build_docks`.
        self._example_dialog: Any = None

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
        self._apply_theme()
        for note in [*self._policy_notes, *self._theme_notes]:
            self.log(note, "warn")
        self._log_opensees_runtime()
        button = "Left-click" if self._policy.pick_button == "left" else "Right-click"
        marquee = self._policy.marquee_modifier.capitalize()
        add = self._policy.marquee_add_modifier.capitalize()
        remove = self._policy.marquee_remove_modifier.capitalize()
        self.log(
            f"Ready.  {button} an element to select it; drag to orbit.  "
            f"{marquee}+drag marquee-selects ({add}: add, {remove}: remove)."
        )

        if model is not None:
            self._remember_source(model)
            self.show_model(model)

    def _apply_theme(self) -> None:
        """Apply the ``theme`` settings as one style-sheet string, if any.

        The panel colour is resolved here (not in ``settings.py``) because only
        Qt can decide whether a colour name is valid; an invalid value is
        reported rather than silently ignored by the style engine.
        """
        colour = self._theme.panel_colour
        if not colour:
            return
        from qtpy.QtGui import QColor

        if not QColor(colour).isValid():
            self.log(f"Theme panel colour {colour!r} is not a valid colour; ignored.", "warn")
            return
        self.setStyleSheet(theme_stylesheet(self._theme))

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
        self._session.reset()
        self._external_combinations: dict = {}
        self._reset_session_display()
        if isinstance(model, SAPModelData):
            self._session.store = InMemoryModelStore(model, source=source)
            self._session.views = ViewRegistry(self._session.store)
            self._session.views.add_geometry("unprocessed", "Unprocessed", model, source=source)
            # Let the recipe step dialog seed the ``run_static`` cases editor
            # from this model's own static cases.
            panel = getattr(self, "_recipe_panel", None)
            if panel is not None:
                panel.set_model_data(model)
        elif isinstance(model, MeshModel):
            self._session.views.add_geometry("processed", "Processed", model, source=source)

    # ── Viewport ────────────────────────────────────────────────────

    def _create_viewport(self) -> None:
        """Create the quad view (four PyVistaQt viewports) and its render backend.

        The central area is a stacked widget: page 0 is the 3-D viewport — now a
        2×2 grid of ``QtInteractor``s (iso / front / top / side) sharing one
        scene — and pages 1 and 2 are created lazily for a table view and a
        figure view (P30 phase C) — a model view draws geometry, but a ``check``
        step produces a table and a ``chart`` step a figure, neither of which
        belongs in the viewport.
        """
        # Quad-view-ready container (roadmap design rule 10), now holding the
        # 2×2 grid rather than a single viewport.
        self._viewport_container = QWidget(self)
        self._viewport_layout = QVBoxLayout(self._viewport_container)
        self._viewport_layout.setContentsMargins(0, 0, 0, 0)

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._viewport_container)  # page 0: the viewport
        self.setCentralWidget(self._stack)

        # The primary (isometric) viewport keeps the ``_interactor`` /
        # ``_backend`` names the rest of the window and its tests rely on; the
        # other three viewports mirror its scene through the same backend.
        self._quad = QuadViewport(self._viewport_container)
        self._viewport_layout.addWidget(self._quad)
        self._interactor = self._quad.primary_interactor
        self._backend = self._quad.backend
        self._create_interaction()
        self._quad.install_camera_sync()
        self._quad.set_single_view(self._display.single_view)

    # ── Picking (viewport -> tree) ───────────────────────────────────

    def _create_interaction(self) -> None:
        """Wire viewport gestures to selection, per the interaction policy.

        The gesture comes from a **Qt event filter** (`views/qt_mouse.py`), not
        from VTK observers: measured on macOS, the widget forwards a press to
        the interactor but never the matching release, so a release-driven
        gesture as a VTK observer can never complete (`docs/dev_notes.md`).

        Every viewport gets its own picker (so a click in any of the four
        selects), each forwarding to the same handlers after marking itself the
        active view.  The primary keeps the ``_interaction`` / ``_mouse_filter``
        names the pick tests drive directly.
        """
        self._secondary_interactions: list = []
        for index, interactor in enumerate(self._quad.interactors):
            interaction = ViewportInteraction(
                interactor,
                policy=self._policy,
                on_pick=self._make_pick_handler(index),
                node_actors=lambda: self._backend.actors("nodes"),
                on_marquee=self._make_marquee_handler(index),
            )
            mouse_filter = install_mouse_filter(
                interactor,
                interaction,
                self,
                on_context_menu=self._make_context_menu_handler(index),
            )
            if index == 0:
                self._interaction = interaction
                self._mouse_filter = mouse_filter
            else:
                self._secondary_interactions.append((interactor, interaction, mouse_filter))

    def _make_pick_handler(self, index: int):
        """Return an ``on_pick`` that marks viewport *index* active, then picks."""

        def _handle(result: PickResult) -> None:
            self._quad.set_active(index)
            self._on_viewport_pick(result)

        return _handle

    def _make_marquee_handler(self, index: int):
        """Return an ``on_marquee`` that marks viewport *index* active, then selects."""

        def _handle(start: tuple, end: tuple, modifiers: tuple = ()) -> None:
            self._quad.set_active(index)
            self._on_marquee(start, end, modifiers)

        return _handle

    def _make_context_menu_handler(self, index: int):
        """Return an ``on_context_menu`` that marks viewport *index* active first."""

        def _handle(global_pos: Any) -> None:
            self._quad.set_active(index)
            self._on_viewport_context_menu(global_pos)

        return _handle

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

    def _on_marquee(self, start: tuple, end: tuple, modifiers: tuple = ()) -> None:
        """Rubber-band select what the drag rectangle encloses or touches.

        A left-to-right drag is a *window* marquee (only fully-enclosed entities);
        a right-to-left drag is a *crossing* marquee (whatever the box touches).
        The modifiers held alongside the marquee decide replace / union /
        difference (see :func:`fea_toolkit.gui.controllers.interaction.marquee_mode`).

        Args:
            start, end: Rectangle corners in VTK *device* pixels (bottom-left
                origin), the drag's anchor and release.
            modifiers: Canonical modifier names held at the press.
        """
        if self._viewer is None or self._selection_index is None:
            return
        x0, y0 = start
        x1, y1 = end
        if abs(x1 - x0) < 3 or abs(y1 - y0) < 3:
            return  # a click-sized box is a pick, not a marquee
        window = x1 >= x0  # left-to-right -> window, right-to-left -> crossing
        frames, areas, nodes = self._marquee_entities(x0, y0, x1, y1, window)

        mode = marquee_mode(self._policy, modifiers)
        if mode in ("union", "difference"):
            current_frames, current_areas, current_nodes = self._selected_entities()
            if mode == "union":
                frames = current_frames | frames
                areas = current_areas | areas
                nodes = current_nodes | nodes
            else:
                frames = current_frames - frames
                areas = current_areas - areas
                nodes = current_nodes - nodes
        self._select_many(frames, areas, nodes)
        total = len(frames) + len(areas) + len(nodes)
        kind = "window" if window else "crossing"
        verb = {"replace": "selected", "union": "added", "difference": "removed"}[mode]
        self.log(f"Marquee ({kind}) {verb} {total} entit{'y' if total == 1 else 'ies'}.")

    def _marquee_entities(self, x0: float, y0: float, x1: float, y1: float, window: bool) -> tuple:
        """``(frame_ids, area_ids, node_ids)`` sets inside the drag rectangle.

        A *window* marquee keeps only what is fully enclosed; a *crossing* marquee
        keeps whatever the rectangle touches.
        """
        import vtk

        renderer = self._quad.active_interactor.renderer

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

    # ── Tree context menu (colour-by + entity set arithmetic) ─────────

    def _on_tree_context_menu(self, pos) -> None:
        """Open a context menu for the tree row under *pos*.

        A **group** row (Materials, Sections, Stories, Frame Elements, Area
        Elements) offers *Colour by …*; an **entity** row offers Hide / Isolate
        and Add / Remove / Invert in selection, scoped to what that entity names.
        """
        index = self._tree_view.indexAt(pos)
        if not index.isValid():
            return
        if self._tree_model._is_group(index):
            self._group_context_menu(index, pos)
        else:
            self._entity_context_menu(index, pos)

    def _available_colour_fields(self) -> set:
        """The colour-by fields the displayed model can actually resolve."""
        model = self._model
        if model is None:
            return set()
        fields = {"section"}
        if getattr(model, "sections", {}) or {}:
            fields.add("material")
        if story_levels(model):
            fields.add("story")
            fields.add("nearest_story")
        return fields

    def _group_context_menu(self, index, pos) -> None:
        """Colour-by menu for a group row (see :meth:`_on_tree_context_menu`)."""
        group = self._tree_model._group_of(index)
        if group is None or group.key not in _COLOURABLE_GROUPS:
            return
        menu = QMenu(self)
        available = self._available_colour_fields()
        for field in COLOUR_FIELDS:
            if field not in available:
                continue
            action = menu.addAction(f"Colour by {FIELD_LABELS[field]}")
            action.setCheckable(True)
            action.setChecked(self._color_field == field)
            action.triggered.connect(lambda _checked=False, f=field: self._set_colour_field(f))
        if not available:
            menu.addAction("Colour by (nothing to colour by)").setEnabled(False)
        menu.exec(self._tree_view.viewport().mapToGlobal(pos))

    def _set_colour_field(self, field: str) -> None:
        """Recolour the display by *field* (``"material"`` / ``"section"`` / ``"story"``)."""
        self._color_field = field
        self._refresh_display()
        self.log(f"Coloured elements by {FIELD_LABELS[field].lower()}.")

    def _entity_context_menu(self, index, pos) -> None:
        """Hide / Isolate / selection arithmetic for an entity row."""
        group = self._tree_model._group_of(index)
        entity = index.data(Qt.ItemDataRole.UserRole)
        if group is None or entity is None:
            return
        scope = self._entity_scope(group, entity)
        if scope is None:
            return
        frames, areas, nodes = scope
        refs = {f"Frame:{fid}" for fid in frames} | {f"Area:{aid}" for aid in areas}

        menu = QMenu(self)
        hide = menu.addAction("Hide")
        hide.setEnabled(bool(refs))
        hide.triggered.connect(lambda: self._hide_scope(refs))
        isolate = menu.addAction("Isolate")
        isolate.setEnabled(bool(refs))
        isolate.triggered.connect(lambda: self._isolate_scope(refs))
        menu.addSeparator()
        add = menu.addAction("Add to selection")
        add.setEnabled(bool(frames or areas or nodes))
        add.triggered.connect(lambda: self._tree_selection_op("union", frames, areas, nodes))
        remove = menu.addAction("Remove from selection")
        remove.setEnabled(bool(frames or areas or nodes))
        remove.triggered.connect(
            lambda: self._tree_selection_op("difference", frames, areas, nodes)
        )
        invert = menu.addAction("Invert in selection")
        invert.setEnabled(bool(frames or areas or nodes))
        invert.triggered.connect(lambda: self._tree_selection_op("toggle", frames, areas, nodes))
        menu.addSeparator()
        show_all = menu.addAction("Show all")
        show_all.triggered.connect(self._on_show_all)
        menu.exec(self._tree_view.viewport().mapToGlobal(pos))

    def _entity_scope(self, group, entity) -> Optional[tuple[set, set, set]]:
        """``(frame_ids, area_ids, node_ids)`` the *entity* names, or ``None``.

        A frame / area / node row names a single element; a material, section
        or story row names every drawable element carrying that attribute.
        """
        key = group.key
        if key in ("frame_elements", "area_elements", "nodes"):
            attr, value = _entity_identity(entity)
            if attr is None or value is None:
                return None
            if attr == "elem_id":
                return {str(value)}, set(), set()
            if attr == "area_id":
                return set(), {str(value)}, set()
            return set(), set(), {str(value)}
        field = {"materials": "material", "sections": "section", "stories": "story"}.get(key)
        if field is None:
            return None
        name = getattr(entity, "name", None)
        if not name:
            return None
        name = str(name)
        frames = {eid for eid, label in frame_labels(self._model, field).items() if label == name}
        areas = {aid for aid, label in shell_labels(self._model, field).items() if label == name}
        return frames, areas, set()

    def _tree_selection_op(self, op: str, frames: set, areas: set, nodes: set) -> None:
        """Apply a set operation between the selection and the named entities."""
        current = self._selected_entities()
        self._select_many(*selection_set_op(current, (frames, areas, nodes), op))
        verbs = {
            "union": "Added",
            "difference": "Removed",
            "toggle": "Toggled",
            "replace": "Selected",
        }
        self.log(f"{verbs[op]} selection.")

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
        for _interactor, interaction, _mouse_filter in self._secondary_interactions:
            interaction.set_policy(self._policy)
        self._set_select_style(checked)
        self.log("Select mode on." if checked else "Orbit mode on.")

    def _set_select_style(self, select: bool) -> None:
        """Disable the select button's orbit while in Select mode (every viewport)."""
        for interactor in self._quad.interactors:
            iren = getattr(interactor, "iren", None)
            if iren is None:
                continue
            vtk_iren = getattr(iren, "interactor", None) or iren
            if select:
                self._default_styles.setdefault(id(interactor), vtk_iren.GetInteractorStyle())
                vtk_iren.SetInteractorStyle(make_select_style(self._policy.pick_button))
            else:
                default = self._default_styles.pop(id(interactor), None)
                if default is not None:
                    vtk_iren.SetInteractorStyle(default)

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
            "Open", self._on_open, shortcut="Ctrl+O", tip="Open a SAP2000 / ETABS model"
        )
        a["file.open_example"] = self._real_action(
            "Open example\u2026",
            self._on_open_example,
            tip="Open the example models and validated benchmarks (build_model + mesh)",
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
        a["view.single_view"] = self._toggle_action(
            "Single view",
            self._on_single_view_toggled,
            tip="Show the active viewport full-size (single) or a 2×2 grid of four (quad)",
            checked=self._display.single_view,
        )
        a["view.iso"] = self._real_action("Isometric", self._view_iso, tip="Isometric view")
        a["view.xy"] = self._real_action("Top (XY)", self._view_xy, tip="Look down the Z axis")
        a["view.xz"] = self._real_action("Front (XZ)", self._view_xz, tip="Look along the Y axis")
        a["view.yz"] = self._real_action("Side (YZ)", self._view_yz, tip="Look along the X axis")
        a["help.docs"] = self._real_action("Documentation", self._on_docs)
        a["help.modeling"] = self._real_action(
            "Modeling guidance",
            lambda: self._open_docs("modeling"),
            tip="Open the modeling guidance for choosing elements and hinges",
        )
        a["help.benchmarks"] = self._real_action(
            "Benchmark register",
            lambda: self._open_docs("benchmarks"),
            tip="Open the register of validated benchmark studies",
        )
        a["help.llm"] = self._real_action(
            "LLM guide", lambda: self._open_docs("llm"), tip="Open the LLM / AI assistant guide"
        )
        a["help.source"] = self._real_action(
            "Source repository",
            lambda: QDesktopServices.openUrl(QUrl(_PROJECT_URL)),
            tip="Open the project repository on GitHub",
        )
        a["help.copy_context"] = self._real_action(
            "Copy model context\u2026",
            self._on_copy_context,
            tip="Copy a plain-text snapshot (model, selection, recipe, views) to the clipboard",
        )
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
        a["file.export_image"] = self._real_action(
            "Export screenshot\u2026",
            self._on_export_image,
            tip="Save the current viewport to a PNG image",
        )
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
        a["view.show_loads"] = self._toggle_action(
            "Show loads",
            self._on_show_loads,
            tip="Show or hide applied-load glyphs (flags for line loads, arrows for point loads, arrows/flags for area loads)",
            checked=False,
        )
        a["view.show_load_colours"] = self._toggle_action(
            "Colour area loads",
            self._on_show_load_colours,
            tip="Colour each area element by its shell-face pressure (with a scale bar)",
            checked=False,
        )
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
        a["model.selections"] = self._real_action(
            "Name selection\u2026",
            self._on_name_selection,
            tip="Save the current selection under a name (shown in the tree's Selections group)",
        )
        a["model.units"] = self._placeholder("Units", "a future release")
        a["model.combinations_edit"] = self._real_action(
            "Edit combinations\u2026",
            self._on_combinations_edit,
            tip="Add, edit or delete the external load-combination set (listed in the load-source dropdown)",
        )
        a["model.combinations_open"] = self._real_action(
            "Open combinations\u2026",
            self._on_combinations_open,
            tip="Load an external load-combination set from JSON",
        )
        a["model.combinations_save"] = self._real_action(
            "Save combinations\u2026",
            self._on_combinations_save,
            tip="Save the external load-combination set to JSON",
        )
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
        a["recipe.command"] = self._real_action(
            "Command\u2026",
            self._on_recipe_command,
            shortcut="Ctrl+K",
            tip="Compile a command string into a recipe step (verb + KEY=VALUE clauses)",
        )
        a["recipe.import"] = self._real_action(
            "Import script\u2026",
            self._on_recipe_import,
            tip="Import a recipe's RECIPE = {...} literal from a Python script",
        )
        for key in ("recipe.run", "recipe.clear", "recipe.save", "recipe.export"):
            a[key].setEnabled(False)
        a["analysis.run"] = self._real_action(
            "Run static\u2026",
            self._on_analysis_run,
            shortcut="Ctrl+R",
            tip="Solve the model's linear static cases or combinations (needs a preprocessed model)",
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
        a["analysis.nonlinear_dynamic"] = self._real_action(
            "Nonlinear time-history\u2026",
            self._on_nonlinear_dynamic_preset,
            tip="Nonlinear dynamic analysis of a ground-motion record (time history)",
        )
        a["analysis.ida"] = self._real_action(
            "IDA\u2026",
            self._on_ida_preset,
            tip="Incremental dynamic analysis \u2014 collapse intensity over a record ladder",
        )
        a["analysis.wizard"] = self._real_action(
            "Analysis wizard\u2026",
            self._on_analysis_wizard,
            tip="Pick the modal \u2192 spectrum \u2192 pushover \u2192 time-history chain in one guided dialog",
        )
        for key in (
            "analysis.modal",
            "analysis.spectrum",
            "analysis.pushover",
            "analysis.nonlinear_dynamic",
            "analysis.ida",
            "analysis.wizard",
        ):
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
        a["results.shell_forces"] = self._toggle_action(
            "Shell results",
            self._on_shell_forces_toggled,
            tip="Draw the active load case's area stress/force resultants, in the chosen component",
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
        m.addAction(a["file.open_example"])
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
        m.addAction(a["view.single_view"])
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
            "view.show_load_colours",
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
        combos = m.addMenu("Combinations")
        for key in (
            "model.combinations_edit",
            "model.combinations_open",
            "model.combinations_save",
        ):
            combos.addAction(a[key])

        m = bar.addMenu("&Analysis")
        m.addAction(_menu_header("Linear"))
        m.addAction(a["analysis.run"])
        m.addAction(a["analysis.modal"])
        m.addAction(a["analysis.spectrum"])
        m.addSeparator()
        m.addAction(_menu_header("Nonlinear"))
        m.addAction(a["analysis.pushover"])
        m.addAction(a["analysis.nonlinear_dynamic"])
        m.addAction(a["analysis.ida"])
        m.addSeparator()
        m.addAction(a["analysis.wizard"])
        m.addSeparator()
        m.addAction(a["analysis.stop"])

        m = bar.addMenu("&Recipe")
        m.addAction(a["recipe.run"])
        m.addSeparator()
        m.addAction(a["recipe.open"])
        m.addAction(a["recipe.save"])
        m.addAction(a["recipe.export"])
        m.addAction(a["recipe.import"])
        m.addSeparator()
        m.addAction(a["recipe.command"])
        m.addSeparator()
        m.addAction(a["recipe.clear"])

        m = bar.addMenu("&Results")
        for key in (
            "results.deformed",
            "results.forces",
            "results.shell_forces",
            "results.animate",
            "results.storey",
            "results.pushover_curve",
        ):
            m.addAction(a[key])
        m.addSeparator()
        m.addAction(a["results.clear"])

        m = bar.addMenu("&Help")
        m.addAction(a["help.docs"])
        m.addAction(a["help.modeling"])
        m.addAction(a["help.benchmarks"])
        m.addAction(a["help.llm"])
        m.addSeparator()
        m.addAction(a["help.source"])
        m.addAction(a["help.copy_context"])
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

        self._load_scale = QDoubleSpinBox(self)
        self._load_scale.setObjectName("load_scale")
        self._load_scale.setRange(0.1, 1000.0)
        self._load_scale.setDecimals(1)
        self._load_scale.setSingleStep(5.0)
        self._load_scale.setValue(self._display.load_scale)
        self._load_scale.setSuffix("%")
        self._load_scale.setToolTip("Applied-load glyph size (% of model diagonal)")
        self._load_scale.setStatusTip("Applied-load glyph size (% of model diagonal)")
        self._load_scale.valueChanged.connect(self._on_load_scale_changed)

        # Which load source "Show loads" draws — a pattern, static case or
        # (linear) combination.  The list depends on the model, so it is
        # repopulated in :meth:`_load_source_factors` on every display.
        self._load_source = QComboBox(self)
        self._load_source.setObjectName("load_source")
        self._load_source.setToolTip(
            "Load source drawn when Show loads is on (pattern, case or combination)"
        )
        self._load_source.setStatusTip("Load source drawn when Show loads is on")
        self._load_source.currentIndexChanged.connect(self._on_load_source_changed)

        # How an area pressure is drawn — a grid of arrows or a one-sided
        # extrusion flag.  The combo holds the style name as user data and the
        # display text as a title, mirroring the force/shell quantity combos.
        from .controllers.settings import AREA_LOAD_STYLES

        self._area_style = QComboBox(self)
        self._area_style.setObjectName("load_area_style")
        for style in AREA_LOAD_STYLES:
            self._area_style.addItem(style.title(), style)
        self._area_style.setCurrentIndex(
            max(0, self._area_style.findData(self._display.load_area_style))
        )
        self._area_style.setToolTip("How an area pressure is drawn (arrows or extrusion)")
        self._area_style.setStatusTip("How an area pressure is drawn")
        self._area_style.currentIndexChanged.connect(self._on_area_style_changed)

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

        # Which area resultant a shell overlay shows.  The SAP stress-resultant
        # names (Nx/Ny/Nxy/Mx/My/Mxy) read better than the schema's x/y keys;
        # the combo holds the *label* and the handler passes the key underneath
        # (see SHELL_FORCE_QUANTITY_LABELS).
        from ..model.sap_data import SHELL_FORCE_QUANTITY_LABELS

        self._shell_quantity = QComboBox(self)
        self._shell_quantity.setObjectName("shell_quantity")
        for label in SHELL_FORCE_QUANTITY_LABELS:
            self._shell_quantity.addItem(label)
        self._shell_quantity.setCurrentText(self._display.shell_quantity)
        self._shell_quantity.setToolTip("Area resultant drawn as a shell overlay")
        self._shell_quantity.setStatusTip("Area resultant drawn as a shell overlay")
        self._shell_quantity.currentIndexChanged.connect(self._on_shell_quantity_changed)
        self._shell_quantity.setEnabled(False)  # until a view carries shell resultants

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
                "@load_source": ("Load", self._load_source),
                "@load_scale": ("Loads %", self._load_scale),
                "@area_style": ("Area", self._area_style),
                "@force_quantity": ("Force", self._force_quantity),
                "@shell_quantity": ("Shell", self._shell_quantity),
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
            "file.open_example",
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
            "view.show_load_colours",
            "@load_source",
            "@load_scale",
            "@area_style",
            "view.show_forces",
            None,
            "results.deformed",
            "@deformed_scale",
            "results.animate",
            "results.forces",
            "@force_scale",
            "@force_quantity",
            None,
            "results.shell_forces",
            "@shell_quantity",
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
        # Right-click menus: colour-by on a group row, hide/isolate/selection
        # arithmetic on an entity row (see _on_tree_context_menu).
        self._tree_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree_view.customContextMenuRequested.connect(self._on_tree_context_menu)

        trees = QTabWidget(self)
        trees.setObjectName("tabs_trees")

        # A one-row header above the tree holds the collapse-all action.  The
        # tree is lazy — a group lists its entities only when expanded — so
        # collapsing is always cheap, while an "expand all" would materialise
        # every group of a large model and is deliberately not offered.
        tree_panel = QWidget(self)
        tree_layout = QVBoxLayout(tree_panel)
        tree_layout.setContentsMargins(0, 0, 0, 0)
        tree_layout.setSpacing(0)
        header = QWidget(tree_panel)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(4, 2, 4, 2)
        header_layout.setSpacing(4)
        collapse_btn = QToolButton(header)
        collapse_btn.setText("Collapse all")
        collapse_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        collapse_btn.setAutoRaise(True)
        collapse_btn.setToolTip("Collapse every group in the Model Tree")
        collapse_btn.clicked.connect(self._tree_view.collapseAll)
        header_layout.addWidget(collapse_btn)
        header_layout.addStretch(1)
        tree_layout.addWidget(header)
        tree_layout.addWidget(self._tree_view, 1)

        trees.addTab(tree_panel, "Model Tree")
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
        self._build_example_dialog()

    def _build_recipe_dock(self) -> None:
        """Build the Recipe dock — the workflow as an ordered list of steps.

        It sits in the bottom area beside the Message Log, because running a
        recipe is precisely what the log narrates.  ``RecipePanel`` needs Qt, so
        it is imported here rather than at module scope (lazy-import policy).
        """
        from .views.recipe_panel import RecipePanel

        self._recipe_panel = RecipePanel(self)
        self._recipe_panel.changed.connect(self._set_recipe_actions_enabled)
        # The panel is a *view* over the session's recipe, not a second owner:
        # ``changed`` fires on every in-place edit and rebind, so this reference
        # write-back keeps ``session.recipe`` and the panel's recipe the same
        # object (``Recipe`` is mutated in place — no copy, no stale mirror).
        self._recipe_panel.changed.connect(self._sync_recipe_to_session)
        self._session.recipe = self._recipe_panel.recipe  # initial sync
        self._recipe_panel.copied.connect(self.log)
        self._recipe_dock = QDockWidget("Recipe", self)
        self._recipe_dock.setObjectName("dock_recipe")
        self._recipe_dock.setWidget(self._recipe_panel)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._recipe_dock)

    def _build_example_dialog(self) -> None:
        """Build the modeless "Open example" chooser (builders + benchmarks).

        The chooser replaced the persistent Benchmark Library dock: choosing an
        example is an *entry point* — used once to preset a ``build_model`` +
        ``mesh`` recipe into the Recipe dock — not a workspace, so it should not
        hold dock space beside the Model Tree and the Recipe dock
        (``docs/gui_roadmap.md`` §1.1 reserves the left dock for the trees and
        the inspector).
        """
        from .views.benchmark_dialog import BenchmarkDialog

        self._example_dialog = BenchmarkDialog(self)
        self._example_dialog.open_requested.connect(self._open_example)

    def _on_open_example(self) -> None:
        """**File ▸ Open example…**: show the chooser dialog (modeless)."""
        self._example_dialog.show()
        self._example_dialog.raise_()
        self._example_dialog.activateWindow()

    def _open_example(self, example_id: str) -> None:
        """Preset the chosen example's ``build_model`` + ``mesh`` recipe.

        One spelling of "open an example" (anti-pattern 17): a builder id
        (``builder:<name>``) presets through
        :func:`~fea_toolkit.benchmarks.catalogue.builder_recipe`, a catalogue id
        through :func:`~fea_toolkit.benchmarks.catalogue.benchmark_recipe`.  The
        user-choice ``build_model`` parameters (e.g. the frame code) are edited
        in the step dialog, not re-picked in a bespoke dialog.
        """
        from ..benchmarks.catalogue import benchmark_recipe, builder_recipe

        if example_id.startswith("builder:"):
            builder = example_id[len("builder:") :]
            self._recipe_panel.set_recipe(builder_recipe(builder))
            self.log(
                f"Preset recipe for {builder!r} (build_model + mesh). "
                "Edit the step's parameters, then run."
            )
            return
        try:
            recipe = benchmark_recipe(example_id)
        except (KeyError, ValueError) as exc:
            self.log(f"Could not open benchmark {example_id!r}: {exc}", "error")
            return
        self._recipe_panel.set_recipe(recipe)
        self.log(
            f"Preset recipe for benchmark {example_id!r} (build_model + mesh). "
            "Edit the step's parameters, then run."
        )

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

    # ── View decoration (cursor readout) ─────────────────────────────

    def _decorate_view(self) -> None:
        """Prepare the status-bar cursor readout.

        The *interaction* extras -- ``enable_terrain_style`` (which keeps the
        model's Z axis vertical while orbiting) and ``track_mouse_position``
        (which feeds the status-bar coordinates) -- both need a live
        interactor, so they are installed from :meth:`showEvent`: pyvista
        raises ``RuntimeError`` for an off-screen or never-shown plotter.
        """
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
            for interactor in self._quad.interactors:
                interactor.enable_terrain_style()
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
            point = self._quad.active_interactor.pick_mouse_position()
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
        if not self._session.hidden_elem_ids and view_selection is None:
            return None
        from dataclasses import replace

        from ..model.selection import Selection

        base = view_selection if view_selection is not None else Selection()
        if base._selects_nodes_explicitly():
            return view_selection
        excluded = set(base.exclude_element_ids or ())
        excluded.update(self._session.hidden_elem_ids)
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
        view = self._session.views.active
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
                camera of every viewport, which is what switching views wants.
            rebuild_tree: Rebuild the Model Tree for *model*.  ``False`` for a
                pure display change — the same model, so rebuilding would only
                throw away the user's selection and expansion.
        """
        from ..model.mesh_model import MeshModel
        from ..model.sap_data import SAPModelData
        from ..plotting.viewer import ModelViewer

        selection = self._merge_hidden(selection)

        cameras = None
        if not reset_view:
            cameras = [None] * len(self._quad.interactors)
            for index, interactor in enumerate(self._quad.interactors):
                with contextlib.suppress(Exception):
                    cameras[index] = interactor.camera_position

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
            "shell_forces": self._actions["results.shell_forces"].isChecked(),
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

        show_loads = self._actions["view.show_loads"].isChecked()
        show_load_colours = self._actions["view.show_load_colours"].isChecked()
        frame_colors = shell_colors = None
        if self._color_field is not None:
            frame_colors, shell_colors = colour_by_field(model, self._color_field)
        viewer.show_model(
            show_nodes=self._actions["view.show_nodes"].isChecked(),
            show_frames=self._actions["view.show_frames"].isChecked(),
            show_shells=self._actions["view.show_shells"].isChecked(),
            color_by_section=color_by_section,
            shell_opacity=float(self._shell_opacity.value()),
            shrink=float(self._shrink.value()),
            show_restraints=self._actions["view.show_restraints"].isChecked(),
            restraint_style=self._display.restraint_style,
            show_loads=show_loads,
            load_factors=self._load_source_factors(model)
            if show_loads or show_load_colours
            else None,
            load_scale=self._load_scale.value() / 100.0,
            load_area_style=self._display.load_area_style,
            show_load_colours=show_load_colours,
            show_extruded=self._actions["view.show_extruded"].isChecked(),
            extruded_detail=self._display.extruded_detail,
            frame_colors=frame_colors,
            shell_colors=shell_colors,
        )
        self._viewer = viewer
        self._model = model
        self._selection_index = SelectionIndex.from_viewer(viewer)
        # The Inspector describes one object, but support conditions live on the
        # model (keyed by node id), so it is given the parsed source — which keeps
        # its restraints and constraint assignments across every view — rather
        # than the model currently drawn.  With no store (an archive) it falls
        # back to the displayed model, which simply has neither.
        self._inspector.set_source_model(
            self._session.store.raw() if self._session.store is not None else model
        )
        # Resolved soil springs live on the preprocessed MeshModel (its
        # ``point_springs`` is a resolved list), not the raw SAPModelData — so
        # the Inspector is given the processed model for those, alongside the
        # raw model (above) for the spring definitions and assignments.
        processed = self._session.store.preprocessed() if self._session.store is not None else None
        if processed is None and isinstance(model, MeshModel):
            processed = model
        self._inspector.set_processed_model(processed)
        if rebuild_tree:
            self._tree_model.set_model(self._tree_model_source(model), self._session.views.views())
        if cameras is None:
            self._quad.apply_orientations()
        else:
            for interactor, camera in zip(self._quad.interactors, cameras):
                if camera is not None:
                    with contextlib.suppress(Exception):
                        interactor.camera_position = camera
        self._update_units_label()
        self._set_model_actions_enabled(self._session.store is not None)
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
        if self._session.store is not None:
            preprocessed = self._session.store.preprocessed()
            if preprocessed is not None:
                return preprocessed
            raw = self._session.store.raw()
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
        # Loads are dense, so they start hidden until asked for.
        self._set_toggle_checked("view.show_loads", False)
        self._set_toggle_checked("view.show_load_colours", False)
        # ... and the load source falls back to the new model's first case.
        from dataclasses import replace

        self._display = replace(self._display, load_source="")
        self._load_source.clear()
        # Extruded shapes is a user preference, not a per-model default: it
        # keeps the state the settings file loaded (off unless the user asked).
        self._set_toggle_checked("view.show_extruded", self._display.show_extruded)
        self._stop_animation()
        # A fresh model's transient histories belong to the previous session —
        # clear them so a stale nonlinear-dynamic result can never animate.
        self._nld_frames.clear()
        self._nld_times.clear()
        # The session's named selections and last run were already cleared by
        # ``_remember_source`` → ``Session.reset()``.
        self._set_toggle_checked("results.animate", False)
        self._set_toggle_checked("results.deformed", False)
        self._sync_force_toggles(False)
        self._set_toggle_checked("results.shell_forces", False)

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
        if intent.get("shell_forces") and self._actions["results.shell_forces"].isEnabled():
            self._on_shell_forces_toggled(True)

    def _update_units_label(self) -> None:
        """Show the model's unit system in the status bar."""
        from ..io.model_store import model_header

        if self._model is None:
            self._units_label.setText("units \u2014")
            return
        self._units_label.setText(model_header(self._model).units_label())

    # ── Logging ─────────────────────────────────────────────────────

    def _log_opensees_runtime(self) -> None:
        """Report where OpenSeesPy will run (and its version) to the message log.

        One line at start-up tells the user whether the "separate" build found
        OpenSeesPy in-process or will delegate to the configured interpreter —
        including the OpenSeesPy version, so a wrong or stale environment is
        visible immediately instead of at the first failed solve.
        """
        from ..opensees.runtime import describe_opensees_runtime

        try:
            self.log(describe_opensees_runtime(load_opensees_setting()))
        except Exception as exc:  # a broken interpreter must not break start-up
            self.log(f"Could not detect the OpenSeesPy runtime: {exc}", "warn")

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

    def _show_view(
        self, view: View, *, rebuild_tree: bool = False, reset_view: bool = False
    ) -> None:
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
            reset_view: Fit every viewport to the incoming geometry instead of
                keeping the current cameras.  A plain view switch keeps the
                cameras (``False``); opening a fresh project fits the viewports
                so the outgoing project's secondary cameras are not restored,
                and the caller then restores the primary camera it saved.
        """
        previous = self._session.views.active
        self._session.views.set_active(view.key)
        if previous is not None and previous.key != view.key:
            # A view switch invalidates the animation that was drawing the old
            # view's history: stop the timer and clear the toggle so animation
            # can only restart from the newly active view's data.
            self._stop_animation()
            self._set_toggle_checked("results.animate", False)
        if view.kind in (GEOMETRY, RESULTS):
            # A node-scoped lens ignores the hidden-element exclusions, so the
            # hidden elements stay visible until a non-node-scoped view is shown.
            if self._session.hidden_elem_ids:
                self._warn_if_view_node_scoped()
            source = self._session.views.source(view.key)
            if source is None:
                return
            self._stack.setCurrentWidget(self._viewport_container)
            self.show_model(
                source,
                reset_view=reset_view,
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
        self._inspector.show_object(self._session.views.get(view.key))
        self._set_view_actions_enabled()
        self.log(f"Showing view: {view.name}.")

    def _rebuild_tree(self) -> None:
        """Refresh the Model Tree for the current model and registered views.

        ``show_model`` repopulates the tree as a side effect of drawing geometry,
        so a geometry or results view keeps it current.  A table or figure view
        raises its own central page and never calls ``show_model``, which left
        every table/figure registered by a recipe run invisible in the **Views**
        group.  This repopulates the tree without touching the viewport.
        """
        self._tree_model.set_model(
            self._tree_model_source(self._model),
            self._session.views.views(),
            self._session.named_selections,
        )

    def _show_table_view(self, view: View) -> None:
        """Render a ``table`` view — a check's findings as a read-only grid.

        The cells are pre-formatted by the verb that produced the
        :class:`~fea_toolkit.workflow.steps.Table`, so this is a renderer: it
        lays the payload out verbatim and never reformats a value.
        """
        from qtpy.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem

        table = self._session.views.table(view.key)
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
        figure = self._session.views.figure(view.key)
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
        if canvas is None or getattr(canvas, "figure", None) is not figure:
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
        parent = self._session.views.active
        if parent is None:
            return
        from .views.selection_dialog import SelectionDialog

        selection = SelectionDialog.edit(parent.selection, self)
        if selection is None:  # cancelled
            return
        key = self._next_derived_key(parent.key)
        name = f"{parent.name} \u00b7 {selection.to_string() or 'unfiltered'}"
        view = self._session.views.add_derived(key, name, parent.key, selection)
        if view is None:
            return
        self._show_view(view, rebuild_tree=True)
        self.log(f"Added view: {name}.")

    def _on_edit_view_selection(self) -> None:
        """**Edit ▸ Edit view selection…**: re-filter the active derived view."""
        view = self._session.views.active
        if view is None or view.parent is None:
            return
        parent = self._session.views.get(view.parent)
        from .views.selection_dialog import SelectionDialog

        selection = SelectionDialog.edit(view.selection, self)
        if selection is None:  # cancelled
            return
        stem = parent.name if parent is not None else view.name
        name = f"{stem} \u00b7 {selection.to_string() or 'unfiltered'}"
        updated = self._session.views.add_derived(view.key, name, view.parent, selection)
        if updated is None:
            return
        self._show_view(updated, rebuild_tree=True)
        self.log(f"Updated view: {name}.")

    def _next_derived_key(self, parent_key: str) -> str:
        """A derived-view key no registered view is using."""
        index = 1
        while self._session.views.get(f"{parent_key}:{index}") is not None:
            index += 1
        return f"{parent_key}:{index}"

    def _set_view_actions_enabled(self) -> None:
        """Enable duplication / selection editing to match the active view."""
        active = self._session.views.active
        self._actions["edit.duplicate_view"].setEnabled(active is not None)
        self._actions["edit.edit_selection"].setEnabled(
            active is not None and active.kind == GEOMETRY and active.parent is not None
        )
        # A deformed shape needs a results view that carries displacement:
        # a pressed button that could only report "nothing to draw" is worse
        # than a disabled one that is greyed until it can work.
        repository = self._session.views.results(active.key) if active is not None else None
        case = _case_of(active)
        self._actions["results.deformed"].setEnabled(
            repository is not None and bool(case) and repository.has_displacements(case)
        )
        # Animation animates the deformed shape, so it is enabled exactly when
        # the deformed overlay is (a displacement field is its only input) — or
        # when the active view is a nonlinear-dynamic table whose transient
        # history is stored separately and animates in the viewport.
        nld_key = active.key if active is not None else None
        has_nld_history = bool(nld_key and self._nld_frames.get(nld_key))
        self._actions["results.animate"].setEnabled(
            has_nld_history
            or (repository is not None and bool(case) and repository.has_displacements(case))
        )
        # The same rule for the flag diagram, on end forces — and the quantity
        # selector is greyed with it, so it cannot look like it is choosing
        # something the archive has none of.
        has_forces = repository is not None and bool(case) and repository.has_forces(case)
        self._actions["results.forces"].setEnabled(has_forces)
        self._actions["view.show_forces"].setEnabled(has_forces)
        self._force_quantity.setEnabled(has_forces)
        # The shell overlay is enabled exactly when the archive recorded shell
        # resultants — and its quantity selector is greyed with it.
        has_shell_forces = (
            repository is not None and bool(case) and repository.has_shell_forces(case)
        )
        self._actions["results.shell_forces"].setEnabled(has_shell_forces)
        self._shell_quantity.setEnabled(has_shell_forces)
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
        self._clear_shell_forces()
        self._set_toggle_checked("results.shell_forces", False)
        self.log("Cleared the results overlay.")

    def _active_results_source(self) -> tuple[Any, str]:
        """``(repository, case)`` for the active view — ``(None, "")`` without one.

        Every results accessor needs the same pair, and the force overlay needs
        it twice: once for the entries and once for how the archive records
        them.
        """
        view = self._session.views.active
        repository = self._session.views.results(view.key) if view is not None else None
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
        raw = repository.to_arrays()
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

    def _active_step_frames(self) -> tuple[list, list]:
        """Per-step frames and time labels for the active view, else ``([], [])``.

        Pushover histories are read from the results archive; nonlinear-dynamic
        histories from the transient payload stored when its result was shown.
        A view with no recorded history animates as a single oscillating field
        instead (see :meth:`_on_animate_toggled`).
        """
        repository, case = self._active_results_source()
        if repository is not None:
            frames = self._pushover_step_frames(repository, case)
            if frames:
                return frames, []
        view = self._session.views.active
        key = view.key if view is not None else None
        if key:
            frames = self._nld_frames.get(key)
            if frames:
                return frames, self._nld_times.get(key, [])
        return [], []

    def _analysis_tag_map(self) -> dict:
        """``{node_tag: node_id}`` for the analysis mesh, not the displayed model.

        Transient histories are keyed by the OpenSees node tag the solver
        assigned, while the deformed overlay is keyed by node id.  The mapping
        must come from the mesh the analysis ran on — whose generated (split /
        meshed) nodes carry the tags in the history — not whichever model the
        viewport happens to display, so an animation started from an unprocessed
        or external-results view still re-keys generated-node tags correctly.
        Falls back to the displayed model's mapping when no store holds a mesh.
        """
        mesh = self._session.store.preprocessed() if self._session.store is not None else None
        nodes = getattr(mesh, "nodes", None) or {}
        tag_to_id = {
            getattr(node, "node_tag", None): node_id
            for node_id, node in nodes.items()
            if getattr(node, "node_tag", None) is not None
        }
        if not tag_to_id and self._viewer is not None:
            tag_to_id = self._viewer.node_ids_by_tag()
        return tag_to_id

    def _nld_step_frames(self, payload: Any) -> tuple[Any, list]:
        """Per-step displacement frames for a nonlinear-dynamic result.

        The ``nonlinear_dynamic`` verb's payload carries the transient
        ``data["displacements"]`` — ``{node_tag: ndarray(shape=(n_steps, 3))}``
        — keyed by the OpenSees node tag the solver assigned.  Each history is
        converted to a NumPy array once and re-keyed to the node id the overlay
        draws, and the per-step ``{node_id: (dx, dy, dz)}`` fields are built one
        tick at a time by the returned :class:`_NldStepHistory` — never eagerly
        on the GUI thread.  Returns an empty history when the payload holds no
        transient.
        """
        data = getattr(payload, "data", None) or {}
        if not isinstance(data, dict):
            return _NldStepHistory({}), []
        history = data.get("displacements") or {}
        if not isinstance(history, dict) or not history:
            return _NldStepHistory({}), []
        tag_to_id = self._analysis_tag_map()
        arrays: dict = {}
        for tag, hist in history.items():
            arr = np.asarray(hist, dtype=float)
            if arr.ndim != 2 or arr.shape[1] < 3:
                continue
            node_id = tag_to_id.get(tag, str(tag))
            arrays[node_id] = arr
        times = data.get("times")
        time_labels = (
            [float(t) for t in np.asarray(times, dtype=float)] if times is not None else []
        )
        return _NldStepHistory(arrays), time_labels

    def _ensure_analysis_mesh_displayed(self) -> None:
        """Render the geometry the active step history is keyed to.

        A nonlinear-dynamic history is re-keyed by the store mesh's node ids
        (via :meth:`_analysis_tag_map`), so animating it must draw that mesh.  A
        pushover history is keyed to its archive's own geometry — the results
        view's source model — so it must draw that, never the preprocessed store
        mesh (which may be a different model).
        """
        repository, _ = self._active_results_source()
        if repository is not None:
            view = self._session.views.active
            target = self._session.views.source(view.key) if view is not None else None
        else:
            target = self._session.store.preprocessed() if self._session.store is not None else None
        if target is not None and self._model is not target:
            self.show_model(target, reset_view=False, rebuild_tree=False)

    def _on_animate_toggled(self, checked: bool) -> None:
        """**Results ▸ Animate deformed shape**: animate the active shape.

        A single displacement field (a mode shape, a static/RS field) oscillates
        at a sinusoidally varying scale; a pushover or nonlinear-dynamic view
        steps through its recorded history instead.

        Args:
            checked: Whether the animation should run.
        """
        if not checked:
            self._stop_animation()
            # Leave the shape at rest — the peak step for a pushover / nonlinear
            # history, the un-animated scale for a single field — not frozen
            # mid-animation, but only while the deformed-shape toggle is on;
            # otherwise clear it.
            if not self._actions["results.deformed"].isChecked():
                self._clear_deformed()
                return
            fraction = self._deformed_scale.value() / 100.0
            if self._anim_step_mode and self._anim_frames:
                peak = self._anim_peak_frame or _peak_step_frame(self._anim_frames)
                self._viewer.overlay_deformed(peak, scale=None, auto_fraction=fraction)
                self._viewer.show()
            elif self._anim_displacements:
                self._viewer.overlay_deformed(
                    self._anim_displacements, scale=None, auto_fraction=fraction
                )
                self._viewer.show()
            return

        fraction = self._deformed_scale.value() / 100.0
        frames, times = self._active_step_frames()
        if frames:
            self._anim_step_mode = True
            self._anim_frames = frames
            self._anim_frame_index = 0
            self._anim_times = times
            # Bring the viewport forward before the history is drawn into it, and
            # make sure the geometry the history is keyed to is what is drawn —
            # the store mesh for a nonlinear-dynamic history, the archive's own
            # geometry for a pushover one — not a previously displayed
            # unprocessed / external-results model.
            self._stack.setCurrentWidget(self._viewport_container)
            self._ensure_analysis_mesh_displayed()
            # Base the scale on the peak frame, not the last one, so a decaying
            # history whose peak is mid-record is never amplified by its
            # near-zero final frame.
            self._anim_peak_frame = _peak_step_frame(frames)
            self._anim_base_scale = self._viewer.deformed_amplification(
                self._anim_peak_frame, fraction
            )
        else:
            displacements = self._active_displacements()
            if not displacements:
                self.log("No displacement data in this view — nothing to animate.", "error")
                self._set_toggle_checked("results.animate", False)
                return
            self._anim_step_mode = False
            self._anim_frames = []
            self._anim_times = []
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
            frame = _step_frame_at(self._anim_frames, self._anim_frame_index)
            if self._anim_times:
                self.statusBar().showMessage(
                    f"t = {self._anim_times[self._anim_frame_index]:.3f} s"
                )
            self._anim_frame_index = (self._anim_frame_index + 1) % len(self._anim_frames)
            self._viewer.overlay_deformed(frame, scale=self._anim_base_scale)
            self._viewer.show()
            return

        self._anim_phase += 0.25  # ≈ 6.25 rad/s at 40 ms → ~1 Hz
        scale = self._anim_base_scale * math.sin(self._anim_phase)
        self._viewer.overlay_deformed(self._anim_displacements, scale=scale)
        self._viewer.show()

    def _stop_animation(self) -> None:
        """Stop the deformed-shape animation timer without re-drawing."""
        if self._anim_timer is not None:
            self._anim_timer.stop()
        if self._anim_times:
            self.statusBar().showMessage("Ready")

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

    def _on_shell_forces_toggled(self, checked: bool) -> None:
        """**Results ▸ Shell results**: colour areas by the active case's resultants.

        The shell counterpart of the force-flag diagram: each area element is
        coloured by the chosen resultant on the same diverging scale.  The
        chosen quantity is a *label* (``Nx``/``Ny``/``Nxy``/``Mx``/``My``/
        ``Mxy``) read through
        :data:`~fea_toolkit.model.sap_data.SHELL_FORCE_QUANTITY_LABELS`.

        Args:
            checked: Whether the overlay should now be drawn.
        """
        if not checked:
            self._clear_shell_forces()
            return

        shell_forces = self._active_shell_forces()
        if not shell_forces:
            self.log("No shell resultants in this view — nothing to draw.", "error")
            self._set_toggle_checked("results.shell_forces", False)
            return

        from ..model.sap_data import SHELL_FORCE_QUANTITY_LABELS

        label = self._shell_quantity.currentText()
        self._viewer.overlay_shell_forces(
            shell_forces,
            quantity=SHELL_FORCE_QUANTITY_LABELS.get(label, label),
        )
        self._viewer.show()
        self.log(f"Shell results: {label}.")

    def _on_shell_quantity_changed(self, _index: int) -> None:
        """Re-draw the shell overlay after the resultant changed.

        Only when it is already on — choosing a resultant must not *start*
        drawing, the same rule the force quantity selector follows.
        """
        if self._actions["results.shell_forces"].isChecked():
            self._clear_shell_forces()
            self._on_shell_forces_toggled(True)

    def _active_shell_forces(self) -> dict:
        """``{shell_id: {component: value}}`` for the active results view, else ``{}``."""
        repository, case = self._active_results_source()
        if repository is None or not case:
            return {}
        return repository.shell_forces(case)

    def _clear_shell_forces(self) -> None:
        """Remove the shell scalar-field overlay, leaving the model itself drawn."""
        if self._viewer is not None:
            self._viewer.clear_shell_forces()

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
        """Show or hide the node-marker overlay (and the loads on those nodes)."""
        self._backend.set_category_visible("nodes", checked)
        self._backend.set_category_visible("loads_nodes", checked)

    def _on_show_shells(self, checked: bool) -> None:
        """Show or hide the area-element overlay (and the loads on those areas)."""
        self._backend.set_category_visible("shells", checked)
        self._backend.set_category_visible("loads_shells", checked)

    def _on_show_frames(self, checked: bool) -> None:
        """Show or hide the frame-element overlay (and the loads on those frames)."""
        self._backend.set_category_visible("frames", checked)
        self._backend.set_category_visible("loads_frames", checked)

    def _on_show_extruded(self, checked: bool) -> None:
        """Toggle extruded-solid frames; a geometry change, so re-render."""
        from dataclasses import replace

        self._display = replace(self._display, show_extruded=checked)
        self._refresh_display()

    def _on_show_restraints(self, checked: bool) -> None:
        """Show or hide the support symbols."""
        self._backend.set_category_visible("restraints", checked)

    def _on_show_loads(self, _checked: bool) -> None:
        """Show or hide applied-load glyphs; a geometry change, so re-render."""
        self._refresh_display()

    def _on_show_load_colours(self, _checked: bool) -> None:
        """Colour shells by their face pressure; a geometry change, so re-render."""
        self._refresh_display()

    def _on_area_style_changed(self, _index: int) -> None:
        """Re-draw area glyphs in the chosen style (never starts drawing)."""
        from dataclasses import replace

        style = self._area_style.currentData() or "arrows"
        self._display = replace(self._display, load_area_style=style)
        if self._actions["view.show_loads"].isChecked():
            self._refresh_display()

    def _on_load_scale_changed(self, _value: float) -> None:
        """Re-size the load glyphs when shown (same rule: the knob never starts)."""
        if self._actions["view.show_loads"].isChecked():
            self._refresh_display()

    def _on_load_source_changed(self, _index: int) -> None:
        """Re-draw the loads for the newly selected source (never starts drawing)."""
        from dataclasses import replace

        name = self._load_source.currentData() or ""
        self._display = replace(self._display, load_source=name)
        if (
            self._actions["view.show_loads"].isChecked()
            or self._actions["view.show_load_colours"].isChecked()
        ):
            self._refresh_display()

    def _load_source_factors(self, model: Any) -> Optional[dict[str, float]]:
        """Populate the load-source combo for *model* and return the selected factors.

        Lists every pattern, static case and linear combination of the model
        (plus any external combination set), selecting the persisted
        ``load_source`` when it still exists and otherwise the first case — the
        default.  Returns the selected source's ``{pattern: factor}`` map, or
        ``None`` when the model has no loads.
        """
        from ..model.load_glyphs import list_load_sources

        sources = list_load_sources(model, getattr(self, "_external_combinations", None))
        desired = self._display.load_source or ""

        self._load_source.blockSignals(True)
        self._load_source.clear()
        for src in sources:
            self._load_source.addItem(f"{src.kind.title()} · {src.name}", src.name)
        if desired:
            idx = self._load_source.findData(desired)
            if idx >= 0:
                self._load_source.setCurrentIndex(idx)
        if self._load_source.currentIndex() < 0:
            for i, src in enumerate(sources):
                if src.kind == "case":
                    self._load_source.setCurrentIndex(i)
                    break
            else:
                if sources:
                    self._load_source.setCurrentIndex(0)
        self._load_source.blockSignals(False)

        selected = self._load_source.currentData()
        factors: Optional[dict[str, float]] = None
        if selected is not None:
            for src in sources:
                if src.name == selected:
                    factors = dict(src.patterns)
                    break
        inspector = getattr(self, "_inspector", None)
        if inspector is not None:
            inspector.set_load_factors(factors)
        return factors

    def _on_hide_selected(self) -> None:
        """**View ▸ Display ▸ Hide selected**: hide the selected elements.

        Folds the selected frame/area ids into the persistent hidden set, then
        re-renders in place.  A node-only selection is a no-op — a lone joint has
        no surface to hide, and ``Selection.resolve_connected`` drops its nodes
        automatically once their elements are hidden.
        """
        self._hide_scope(self._selected_element_refs())

    def _hide_scope(self, refs: set[str]) -> None:
        """Hide the type-qualified *refs* (``"Frame:1"`` / ``"Area:2"``)."""
        if not refs:
            self.log("Nothing selected to hide.", "warn")
            return
        self._session.hidden_elem_ids.update(refs)
        self._refresh_display()
        self.log(f"Hid {len(refs)} element(s).")
        self._warn_if_view_node_scoped()

    def _on_show_all(self) -> None:
        """**View ▸ Display ▸ Show all**: restore every hidden element."""
        if not self._session.hidden_elem_ids:
            self.log("Nothing is hidden.", "warn")
            return
        self._session.hidden_elem_ids.clear()
        self._refresh_display()
        self.log("Restored all hidden elements.")

    def _on_reset_view(self) -> None:
        """**View ▸ Reset view**: return to a clean view and forget the saved state.

        Resets the camera — restoring the four viewports to their default
        orientations — restores every hidden element and clears the saved
        per-model state, so a strange saved view cannot follow the model to its
        next open.  Display *preferences* (opacity, shrink, …) are untouched —
        those are user settings, not this model's state.
        """
        if self._quad is not None:
            self._quad.reset_orientations()
        if self._session.hidden_elem_ids:
            self._session.hidden_elem_ids.clear()
            self._refresh_display()
        if self._source_label:
            for note in clear_view_state(self._source_label):
                self.log(note, "warn")
        self._reset_view_baseline = (
            self._current_camera(),
            frozenset(self._session.hidden_elem_ids),
        )
        self.log("Reset the view to defaults.")

    def _all_element_ids(self) -> set[str]:
        """Every ``Frame:``/``Area:`` id the displayed model can draw.

        Ids are type-qualified so a frame and an area sharing a SAP label stay
        distinct — ``Selection.exclude_element_ids`` is a flat list that cannot
        tell them apart on its own.  Inactive (split/meshed) parents are
        skipped, so an id that is never drawn cannot leak into the hidden set.
        """
        return self._session._drawable_element_ids(self._model)

    def _restore_hidden_elements(self, hidden: Any, model: Any) -> set[str]:
        """Restore a saved hidden set, dropping it when it names another topology.

        A hidden set captured while the model was preprocessed (split/meshed)
        references child ids (``Frame:5_af_0_1``, ``Area:100_sub_0_0``) that do
        not exist in the freshly-parsed raw model.  Re-applying it would hide
        raw members that merely share ids with the processed topology, so when
        any saved id is not drawable in *model* the whole set is treated as
        stale and dropped.
        """
        if not isinstance(hidden, list):
            return set()
        ids = {str(ref) for ref in hidden}
        if ids and not ids <= self._session._drawable_element_ids(model):
            self.log(
                f"Dropping {len(ids)} hidden element(s) saved for a different "
                "(preprocessed) topology.",
                "warn",
            )
            return set()
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
        self._isolate_scope(self._selected_element_refs())

    def _isolate_scope(self, keep: set[str]) -> None:
        """Hide everything but the type-qualified *keep* refs."""
        if not keep:
            self.log("Nothing selected to isolate.", "warn")
            return
        self._session.hidden_elem_ids = self._all_element_ids() - keep
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
        active = self._session.views.active
        source = self._session.views.source(active.key) if active is not None else None
        if source is None:
            return
        self.show_model(
            source,
            reset_view=False,
            rebuild_tree=False,
            selection=active.selection,
        )

    # ── Preprocessing (Model menu) ───────────────────────────────────

    def _on_combinations_edit(self) -> None:
        """**Model ▸ Combinations ▸ Edit…**: add / edit / delete the external set."""
        from .views.combination_editor import CombinationDialog

        edited = CombinationDialog.edit(self._external_combinations, self)
        if edited is None:
            return
        self._external_combinations = edited
        self._refresh_display()
        self.log(f"Load combinations updated: {len(edited)} combination(s).")

    def _on_combinations_open(self) -> None:
        """**Model ▸ Combinations ▸ Open…**: load an external set from JSON."""
        from ..io.combination_set import read_combination_set

        path, _ = QFileDialog.getOpenFileName(
            self, "Open combinations", "", "Combination JSON (*.json)"
        )
        if not path:
            return
        try:
            self._external_combinations = read_combination_set(path)
        except (OSError, ValueError, TypeError) as exc:
            self.log(f"Could not read combinations: {exc}", "error")
            return
        self._refresh_display()
        self.log(f"Loaded {len(self._external_combinations)} combination(s) from {path}.")

    def _on_combinations_save(self) -> None:
        """**Model ▸ Combinations ▸ Save…**: write the external set to JSON."""
        from ..io.combination_set import write_combination_set

        path, _ = QFileDialog.getSaveFileName(
            self, "Save combinations", "combinations.json", "Combination JSON (*.json)"
        )
        if not path:
            return
        try:
            write_combination_set(path, self._external_combinations)
        except OSError as exc:
            self.log(f"Could not save combinations: {exc}", "error")
            return
        self.log(f"Saved {len(self._external_combinations)} combination(s) to {path}.")

    def _on_name_selection(self) -> None:
        """**Model ▸ Name selection…**: save the current selection under a name.

        A named selection is a saved
        :class:`~fea_toolkit.model.named_selection.NamedSelection`; it appears in
        the tree's **Selections** group and is what a step's
        ``Selection.selections`` field references by name.  It is authored from
        either the current viewport / tree selection (a graphical pick, when one
        exists) or a ``Selection.from_string`` expression.
        """
        from qtpy.QtWidgets import QInputDialog, QMessageBox

        from ..model.named_selection import NamedSelection
        from ..model.selection import Selection

        name, ok = QInputDialog.getText(self, "Name selection", "Selection name:")
        name = name.strip()
        if not ok or not name:
            return

        pick = self.current_selection()
        if pick is not None:
            n_entities = len(pick.element_ids or []) + len(pick.node_ids or [])
            entity_word = "entity" if n_entities == 1 else "entities"
            use_pick = QMessageBox.question(
                self,
                "Name selection",
                f"Use the current viewport/tree selection ({n_entities} {entity_word}) "
                "instead of an expression?",
            )
            if use_pick == QMessageBox.StandardButton.Yes:
                element_ids = list(pick.element_ids or [])
                # A bare picked id collides across element types (a frame and an
                # area can both be labelled "1"), so qualify the ids with the
                # pick's single element type when it has one — the saved selection
                # then resolves the same members the graphical pick showed.
                types = list(pick.element_types or [])
                if types in (["Frame"], ["Area"]):
                    element_ids = [f"{types[0]}:{eid}" for eid in element_ids]
                named = NamedSelection(
                    name=name,
                    element_ids=element_ids,
                    node_ids=list(pick.node_ids or []),
                )
                self._session.named_selections[name] = named
                self.log(
                    f"Named selection {name!r}: {len(named.element_ids)} element(s), "
                    f"{len(named.node_ids)} node(s) from the viewport pick"
                )
                self._rebuild_tree()
                return

        expression, ok = QInputDialog.getText(
            self, "Selection expression", f"Expression for {name} (empty = whole model):"
        )
        if not ok:
            return
        try:
            selection = (
                Selection.from_string(expression.strip()) if expression.strip() else Selection()
            )
        except ValueError as exc:
            self.log(f"Invalid selection expression: {exc}", "error")
            return
        self._session.named_selections[name] = NamedSelection(name=name, selection=selection)
        self.log(f"Named selection {name!r}: {selection.to_string() or 'whole model'}")
        self._rebuild_tree()

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

        if self._session.store is None:
            self.log("Open a SAP2000 model before preprocessing.", "warn")
            return
        edited = StepDialog.edit(Step(verb="mesh"), self, model_data=self._session.store.raw())
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
        from ..workflow import step_to_command

        self.log(f"command: {step_to_command(step)}")
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
        if self._session.store is None:
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

    def _on_nonlinear_dynamic_preset(self) -> None:
        """**Analysis ▸ Nonlinear time-history**: compose and run a nonlinear_dynamic step."""
        self._analysis_preset("nonlinear_dynamic", "Nonlinear dynamic")

    def _on_ida_preset(self) -> None:
        """**Analysis ▸ IDA**: compose and run an ida step."""
        self._analysis_preset("ida", "IDA")

    def _on_analysis_wizard(self) -> None:
        """**Analysis ▸ Analysis wizard…**: guide the modal→…→IDA chain in one dialog.

        Lists the analysis chain, greying each analysis until its precursors are
        satisfied (by the recipe, or by an earlier tick in this dialog), then
        composes the chosen steps — modal first — and runs the recipe once, so
        the whole chain resolves in a single pass.
        """
        store = self._session.store
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

        from ..workflow import Step, satisfied_kinds, step_to_command
        from ..workflow.verbs.modal import resolve_default_modal_case
        from .views.analysis_wizard import AnalysisWizard
        from .views.step_dialog import StepDialog

        produced = satisfied_kinds(list(self._session.recipe), initial={"geometry"})
        verbs = AnalysisWizard.get_request(produced, self)
        if not verbs:
            return

        default_modal = resolve_default_modal_case(store.raw())
        # The modal precursor is resolved lazily, after the first *dependent*
        # step is actually accepted — enabling/adding it upfront would mutate
        # the recipe even when every dependent dialog is cancelled.  When the
        # wizard itself adds the modal step, that step (named *default_modal*)
        # is the precursor; otherwise an existing recipe modal step supplies the
        # name (a CUSTOM precursor must not be silently replaced by the source
        # default) — matching _analysis_preset()/_ensure_modal_precursor().
        from dataclasses import replace

        adds_modal = "modal" in verbs
        modal_case: str = ""
        accepted = 0
        for verb in verbs:
            if verb == "modal":
                params = {"name": default_modal} if default_modal else {}
                step = Step(verb=verb, params=params)
            else:
                if verb in ("nonlinear_dynamic", "ida"):
                    # These verbs need a ground-motion record (and the IDA ladder)
                    # before the step can run — collect them in a dialog, exactly as
                    # _analysis_preset() does, instead of adding a step that will
                    # fail validation for a missing ground_motion_file.
                    prefill = {"modal_case": default_modal} if default_modal else {}
                    step = StepDialog.edit(Step(verb=verb, params=prefill), self, store.raw())
                    if step is None:
                        continue  # cancelled — skip this step
                else:
                    params = {"modal_case": default_modal} if default_modal else {}
                    step = Step(verb=verb, params=params)
                # This dependent step is accepted: ensure its modal precursor now
                # and realign an unchanged default prefill to the precursor that
                # will actually run.
                if not modal_case:
                    modal_case = (
                        default_modal if adds_modal else self._ensure_modal_precursor(default_modal)
                    )
                if modal_case and step.params.get("modal_case", "") == default_modal:
                    step = replace(step, params={**step.params, "modal_case": modal_case})
            self._recipe_panel.add_step(
                step.verb, step.selection, step.params, optional=step.optional
            )
            accepted += 1
            self.log(f"command: {step_to_command(step)}")
        if not accepted:
            self.log("Analysis wizard: no steps were accepted.", "info")
            return
        self._pending_label = "Analysis wizard"
        self._on_recipe_run()

    def _analysis_preset(self, verb: str, label: str) -> None:
        """Compose an analysis step in a dialog, append it, and run the recipe.

        The Analysis-menu entries are presets over the recipe, exactly as
        **Model ▸ Mesh** is: the dialog composes the step, and accepting appends
        it and runs the recipe — so an analysis is a visible, editable,
        exportable step rather than a hidden handler.

        Args:
            verb: The analysis verb — ``"modal"``, ``"response_spectrum"``,
                ``"pushover"``, ``"nonlinear_dynamic"`` or ``"ida"``.
            label: What the Message Log should say is happening.
        """
        store = self._session.store
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
        from ..workflow.verbs.modal import resolve_default_modal_case
        from .views.step_dialog import StepDialog

        default_modal = resolve_default_modal_case(store.raw())
        prefill: dict = {}
        if verb == "modal" and default_modal:
            prefill["name"] = default_modal
        elif (
            verb in ("response_spectrum", "pushover", "nonlinear_dynamic", "ida") and default_modal
        ):
            prefill["modal_case"] = default_modal

        edited = StepDialog.edit(Step(verb=verb, params=prefill), self, store.raw())
        if edited is None:
            return
        if verb in ("response_spectrum", "pushover", "nonlinear_dynamic", "ida"):
            from dataclasses import replace

            selected = self._ensure_modal_precursor(default_modal)
            # The precursor actually enabled may not carry *default_modal*'s name
            # (a recipe's own modal step wins when the model's case name is
            # absent), so realign the step's ``modal_case`` with it when the
            # dialog's prefill was accepted unchanged.
            if selected and edited.params.get("modal_case", "") == default_modal:
                edited = replace(edited, params={**edited.params, "modal_case": selected})
        self._preset_step(edited, label)

    def _ensure_modal_precursor(self, default_name: str = "") -> str:
        """Ensure an *enabled* ``modal`` step precedes the spectrum/pushover step.

        ``response_spectrum`` and ``pushover`` declare
        ``needs=("geometry", "modal")``, so a preset for either must run after a
        modal step has produced the modal result.  A modal step that already ran
        is unchecked (auto-"No" after a run), so it is re-enabled — and, through
        the ``set_enabled`` cascade, its geometry precursor too — rather than
        appending a duplicate.  When the recipe has no modal step, one is added
        with ``name`` set to *default_name* (the model's modal case), so the
        spectrum / pushover step's ``modal_case`` prefill resolves to the same
        name.

        With several modal steps, the one whose ``name`` matches *default_name*
        is preferred (the model's modal case); only when none matches does the
        first modal step stand in, so the returned name always matches the step
        the run will actually execute.

        Args:
            default_name: The model's preferred modal-case name, or ``""``.

        Returns:
            The ``name`` of the modal step now enabled (or added), so the caller
            can align the spectrum / pushover step's ``modal_case`` with it.
        """
        from ..workflow import STEP_SPECS

        panel = self._recipe_panel
        fallback = STEP_SPECS["modal"].params["name"].default

        def _modal_name(step: Any) -> str:
            return (step.params or {}).get("name") or fallback

        chosen = None
        for index, step in enumerate(panel.recipe.steps):
            if step.verb != "modal":
                continue
            if chosen is None:
                chosen = index
            if default_name and _modal_name(step) == default_name:
                chosen = index
                break
        if chosen is not None:
            if not panel.recipe.steps[chosen].enabled:
                panel.recipe.set_enabled(chosen, True)
            return _modal_name(panel.recipe.steps[chosen])

        name = default_name or fallback
        panel.add_step("modal", params={"name": name} if name != fallback else None)
        return name

    def _on_recipe_run(self) -> None:
        """**Recipe ▸ Run recipe**: run the panel's steps on a worker.

        The recipe is snapshotted before the worker starts (through its own
        serialisation), so editing the panel mid-run cannot change what is being
        run.  The steps' log lines are collected and flushed on the GUI thread
        once the run ends — a worker must not touch widgets.
        """
        from ..workflow import Recipe, run_recipe

        store = self._session.store
        if store is None:
            self.log("Open a SAP2000 model before running a recipe.", "warn")
            return
        if not len(self._session.recipe):
            self.log(
                "The recipe has no steps \u2014 use Model \u25b8 Split elements / "
                "Mesh areas, or add one from the Recipe menu.",
                "warn",
            )
            return
        if self._worker is not None and self._worker.isRunning():
            self.log("A run is already in progress.", "warn")
            return

        recipe = Recipe.from_dict(self._session.recipe.to_dict())
        model_data = store.raw()
        from .controllers.settings import load_opensees_setting

        opensees_python = load_opensees_setting()
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
        # Snapshot the window's named selections on the GUI thread, so a recipe
        # step referencing a GUI-authored selection resolves it without the
        # worker touching the live store.  The snapshot is also kept on the
        # window so the completion handler can merge back only what the run
        # changed.  The store the run starts from is likewise remembered, so a
        # run whose document was switched away while in flight is rejected.
        named_snapshot = dict(self._session.named_selections)
        self._recipe_named_snapshot = named_snapshot
        self._recipe_starting_store = store

        def task(should_cancel):
            def on_step(index, verb, status):
                if reporter[0] is not None:
                    reporter[0](index, len(recipe), f"{verb}:{status}")

            def on_progress(index, name, total):
                if reporter[0] is not None:
                    reporter[0](index, total, f"case:{name}")

            run = run_recipe(
                recipe,
                model_data,
                model=starting_mesh,
                cancel=should_cancel,
                pipeline_log=pipeline_log,
                on_step=on_step,
                on_progress=on_progress,
                opensees_python=opensees_python,
                named_selections=named_snapshot,
            )
            return run, messages

        _freeze_gc_once()
        worker = TaskWorker(task, parent=self)
        reporter[0] = worker.report_progress
        worker.progress.connect(self._on_recipe_progress)
        worker.succeeded.connect(self._recipe_finished)
        worker.failed.connect(self._preprocess_failed)
        worker.finished.connect(self._preprocess_ended)
        self._worker = worker
        worker.start()

    def _on_recipe_progress(self, index: int, total: int, label: str) -> None:
        """Update the UI for a worker progress event — a step's status or a case.

        The recipe worker multiplexes two streams over its one progress signal:
        a step boundary is ``"<verb>:<status>"`` (updates the Recipe panel's
        Status column), and a solve reporting the case it is about to run is
        ``"case:<name>"`` (drives the determinate bar and a live log line).

        Args:
            index: For a step event, the zero-based step position; for a case
                event, the 1-based case position.
            total: For a step event, the number of steps (unused); for a case
                event, the number of cases.
            label: ``"<verb>:<status>"`` or ``"case:<name>"``.
        """
        if label.startswith("case:"):
            case_name = label[len("case:") :]
            self._progress.setRange(0, max(int(total), 1))
            self._progress.setValue(int(index))
            if case_name:
                self.log(f"Solving {case_name} ({index}/{total}) \u2026")
            return
        _verb, _sep, status = label.partition(":")
        self._recipe_panel.set_step_status(index, status or "running")

    def _recipe_finished(self, payload: Any) -> None:
        """Report what the recipe produced and register each result as a view.

        A ``cases`` result carries the same in-memory archive the **Analysis ▸
        Run** path serves, so it becomes one view per case, exactly as there —
        a recipe's results are no longer merely a line in the log.  A
        document-lifecycle step (``open`` / ``close`` / ``build_model``) is
        adopted into the window first, so every result registers against the
        document the user is now looking at; when the document is *not* replaced,
        only the named selections the run changed are merged back, so selections
        the user edited while it ran survive.  A run whose document was switched
        away while in flight is rejected rather than applied to the document now
        open.  Cancellation and failure stay distinct: a *cancelled* run
        legitimately holds only the cases it solved before stopping, so no result
        is reported as failed, and an empty one is left to the cancellation line
        rather than called out as missing data.

        Args:
            payload: ``(RecipeRun, [log lines])`` from the worker.
        """
        from ..workflow import CASES, FIGURE, TABLE
        from ..workflow.steps import IDA, MODAL, NLD, PUSHOVER, RS, SESSION

        run, messages = payload
        # A run started for one document must not install its mesh, selections or
        # results onto a document the user switched to while it was in flight
        # (``open_path`` replaced ``self._session.store``): reject the stale run's
        # outcomes rather than affecting the document now open.
        if not self._recipe_store_is_current():
            self.log(
                "Recipe run finished for a document that is no longer open; "
                "its outcomes were discarded.",
                "warn",
            )
            return
        # A document-lifecycle step (``open`` / ``close`` / ``build_model``)
        # replaced or closed the working document; adopt it *before* registering
        # the results below, so a meshing/analysis result attaches to the
        # document the user now sees rather than the stale one (P35 follow-up).
        if run.document_reset:
            self._adopt_document(run.document)
        elif run.document is not None:
            # The document itself was not replaced, but a ``select`` step (or any
            # verb that mutates the document's named selections) left the store
            # in place while changing the selection mapping — merge only the
            # entries the run changed back into the window's store, so selections
            # the user added or edited while the run was in flight survive (the
            # run's snapshot cannot see them), without re-adopting the unchanged
            # model store.
            self._merge_recipe_named_selections(run.document)
        self._session.last_run = run
        # Persist each step's outcome onto the recipe (a step that ran "ok" is
        # unchecked), so the run state survives save → reopen.
        self._recipe_panel.apply_run_outcomes(run.step_status)
        for message in messages:
            self.log(message)
        for result in run.results:
            if result.kind == "geometry":
                self._show_geometry_result(result.label, result.payload)
            elif result.kind == SESSION:
                # The document was already adopted up-front (``document_reset``);
                # a SESSION result here just records the lifecycle event for the
                # log — ``open``/``close``/``save`` carry their path, ``close``
                # carries none.
                if result.payload:
                    self.log(f"{result.label}: {result.payload}")
                else:
                    self.log(result.label)
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
            elif result.kind == NLD:
                self._show_nonlinear_dynamic_result(result.label, result.payload)
            elif result.kind == IDA:
                self._show_ida_result(result.label, result.payload)
            elif result.kind == TABLE:
                self._show_table_result(result.label, result.payload)
            elif result.kind == FIGURE:
                self._show_figure_result(result.label, result.payload)
        for index, verb, message in run.failures:
            self.log(f"Step {index} ({verb}) failed and is optional \u2014 {message}", "warn")
        if run.cancelled:
            self.log("The recipe was cancelled.", "warn")
        self._set_analysis_actions_enabled()

    def _recipe_store_is_current(self) -> bool:
        """Whether the recipe run's document is still the one the window shows.

        ``open_path`` (and ``open_project_path`` / the benchmark openers) replace
        ``self._session.store`` through :meth:`_remember_source`, so a run started for one
        document must not apply its recovered mesh, named selections or results
        to a document the user switched to while it was in flight.  When no run
        is being tracked (``_recipe_starting_store`` is ``None`` — the tests drive
        the completion handlers directly), there is nothing to guard and the
        store is treated as current.
        """
        return (
            self._recipe_starting_store is None
            or self._session.store is self._recipe_starting_store
        )

    def _merge_recipe_named_selections(self, document: Any) -> None:
        """Adopt only the named selections a recipe run actually changed.

        The run operates on a snapshot of the window's selections taken when it
        started; merging the whole ``document.named_selections`` back would
        clobber selections the user added or edited through
        :meth:`_on_name_selection` while the run was in flight (the snapshot
        predates those edits).  Only entries whose value differs from that
        starting snapshot are applied, so the run's ``select`` steps land while
        the user's concurrent edits to *other* keys survive.  A key the recipe
        changed and the user also edited during the run resolves in the recipe's
        favour: its ``select`` step is the explicit, completed operation the run
        was asked to perform.
        """
        snapshot = self._recipe_named_snapshot or {}
        for name, value in document.named_selections.items():
            if name not in snapshot or snapshot[name] != value:
                self._session.named_selections[name] = value

    def _adopt_document(self, document: Any) -> None:
        """Adopt the document a recipe run ended with into the window.

        A document-lifecycle step (``open`` / ``close`` / ``build_model``)
        replaced or closed the worker's document; the window's ``_session.store`` still
        shows the previous one.
        This replaces the store, restores the document's named selections and
        saved view state, raises the viewport page and re-renders — the same
        sequence ``File ▸ Open`` runs — so the results the recipe produced
        register against the document the user is now looking at rather than the
        stale one (P35 follow-up).
        """
        # Preserve the outgoing model's view before switching, as ``open_path``
        # does.
        self._save_view_state()
        self._remember_source(document.model_data, source=document.document_path)
        self._session.named_selections = dict(document.named_selections)
        # Restore the incoming document's saved visibility, matching
        # ``open_path``: a saved hidden set is validated against the incoming
        # model, and no saved state clears it so the previous document's
        # exclusions do not leak across the switch.
        state, notes = load_view_state(document.document_path)
        for note in notes:
            level = "info" if "is stale" in note else "warn"
            self.log(note, level)
        self._session.hidden_elem_ids = self._restore_hidden_elements(
            state.get("hidden_elem_ids"), document.model_data
        )
        # Adoption draws geometry, so raise the viewport page even if the user
        # was looking at a table / figure view.
        self._stack.setCurrentWidget(self._viewport_container)
        self.show_model(document.model_data)
        self._restore_camera(state.get("camera_position"))

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
        view = self._session.views.add_table(key, name, table, source=f"Recipe: {label}")
        self._show_view(view, rebuild_tree=True)
        self.log(f"Added table view: {name}.")

    def _show_figure_result(self, label: str, figure: Any) -> None:
        """Register a chart's ``figure`` result as a view and show it."""
        if figure is None:
            self.log(f"{label}: the chart produced no figure.", "warn")
            return
        key = f"figure:{label.lower().replace(' ', '-')}"
        view = self._session.views.add_figure(key, label, figure, source=f"Recipe: {label}")
        self._show_view(view, rebuild_tree=True)
        self.log(f"Added figure view: {label}.")

    def _show_nonlinear_dynamic_result(self, label: str, payload: Any) -> None:
        """Summarise a nonlinear-dynamic (time-history) result as a table.

        The ``nonlinear_dynamic`` verb's payload is an
        :class:`~fea_toolkit.analysis.base.AnalysisResult` whose ``data`` holds
        the full transient (``times`` and the per-node displacement / velocity /
        acceleration arrays) plus the peak reductions.  The peaks and the run's
        convergence are distilled into a one-row table; the per-step
        displacement history is stored for :meth:`_on_animate_toggled`.
        """
        from ..workflow.steps import Table

        data = getattr(payload, "data", None) or {}
        if not isinstance(data, dict):
            self.log(f"{label}: the nonlinear-dynamic result has no data.", "warn")
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
        self._show_table_result(
            label,
            Table(title=label, columns=("Quantity", "Value"), rows=rows),
        )
        frames, times = self._nld_step_frames(payload)
        if frames:
            self._nld_frames[_table_key(label)] = frames
            self._nld_times[_table_key(label)] = times
            self.log(f"{label}: transient history stored for animation ({len(frames)} steps).")
            # The table view is now animatable, so refresh the Results menu
            # (it was greyed for a view with no displacement field).
            self._set_view_actions_enabled()
        else:
            key = _table_key(label)
            self._nld_frames.pop(key, None)
            self._nld_times.pop(key, None)
            self.log(f"{label}: no transient displacement history to animate.", "warn")
            # A previous run of this label may have stored frames; refresh the
            # Results menu so the now-unanimatable table is greyed correctly.
            self._set_view_actions_enabled()

    def _show_ida_result(self, label: str, payload: Any) -> None:
        """Summarise an IDA run as a collapse table and an IDA-curve figure.

        The ``ida`` verb's payload is the
        :func:`~fea_toolkit.analysis.ida.run_ida` result dict — the intensity
        ladder, per-rung drift ratios and convergence flags, and the collapse
        intensity / criterion / bisection bracket.  The curve is plotted by
        :func:`~fea_toolkit.plotting.viz_ida.plot_ida_curve` and the collapse
        quantities distilled into a one-row table.
        """
        from ..workflow.steps import Table

        data = payload if isinstance(payload, dict) else {}
        if not data:
            self.log(f"{label}: the IDA run produced no result.", "warn")
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
        self._show_table_result(
            label,
            Table(title=label, columns=("Quantity", "Value"), rows=rows),
        )

        from ..plotting.viz_ida import plot_ida_curve

        figure = plot_ida_curve(data, title=label)
        if figure is not None:
            self._show_figure_result(f"{label} curve", figure)
        else:
            self.log(f"{label}: matplotlib unavailable \u2014 no IDA curve plotted.", "warn")

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

        mesh = self._session.store.preprocessed() if self._session.store is not None else None
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

        mesh = self._session.store.preprocessed() if self._session.store is not None else None
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

        mesh = self._session.store.preprocessed() if self._session.store is not None else None
        model = mesh if mesh is not None else repository.as_model()
        for case in cases:
            self._session.views.add_results(
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
            first = self._session.views.get(f"{_RESULTS_KEY_PREFIX}{cases[0]}")
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
            self.log("Preprocessing rebuilt the model; hidden elements were restored.")
        self.show_model(mesh_model, reset_view=False)
        self.log(f"Added view: {name}.")

    def _preprocess_failed(self, message: str) -> None:
        """Report a recipe failure, flushing buffered diagnostics first.

        The pipeline messages collected by ``_collect`` on the worker thread are
        flushed here (on the GUI thread) before the error, so the diagnostics that
        led up to the failure appear before it — matching the success path's flush
        in :meth:`_recipe_finished`.  A non-optional ``StepError`` carries the
        partial run state, so the steps that completed before the failure are
        still committed (unchecked, like a successful run) instead of being
        re-run next time.  It also carries the document state: a completed
        ``open`` / ``close`` / ``build_model`` step may have replaced the
        document before the failing step, so that state is adopted before the
        lifecycle steps are committed — otherwise the window would keep showing
        the pre-recipe document while the recipe recorded the switch as done.  A
        document that was *not* replaced still merges the named selections the
        completed steps changed, and a run whose document was switched away while
        in flight is rejected rather than applied to the document now open.
        """
        for pending in getattr(self, "_recipe_messages", None) or []:
            self.log(pending)
        self._recipe_messages = None
        error = getattr(self._worker, "error", None)
        step_status = getattr(error, "step_status", None)
        document = getattr(error, "document", None)
        document_reset = getattr(error, "document_reset", False)
        # A run started for one document must not install its recovered mesh or
        # document state onto a document the user switched to while it was in
        # flight (``open_path`` replaced ``self._session.store``): reject the stale
        # recovery state and outcomes rather than affecting the document now open.
        if not self._recipe_store_is_current():
            self.log(
                "Recipe failed for a document that is no longer open; "
                "its recovered state was discarded.",
                "warn",
            )
        else:
            # A completed ``open`` / ``close`` / ``build_model`` step may have
            # replaced the document before the failing step; adopt it first so the
            # lifecycle steps are only committed against the document the window now
            # shows.
            if document_reset and document is not None:
                self._adopt_document(document)
            elif document is not None:
                # The document was not replaced, but a completed ``select`` step
                # may have changed its named selections before the failing step —
                # merge only what the run changed (mirroring ``_recipe_finished``)
                # so the completed ``select`` step's work is retained without
                # clobbering selections the user edited while the run was in
                # flight.
                self._merge_recipe_named_selections(document)
            # A completed ``mesh`` step's topology has no view to register on the
            # failure path, but a retry of the analysis step needs it in the store —
            # record it (mirroring ``_show_geometry_result`` on the success path) so
            # the committed mesh step need not re-run.
            if (
                document is not None
                and getattr(document, "model", None) is not None
                and self._session.store is not None
            ):
                self._session.store.set_preprocessed(document.model)
            if step_status:
                if document_reset and document is None:
                    # The document changed but the error carried no state to adopt —
                    # keep the lifecycle steps retryable rather than marking them
                    # complete against a window that still shows the old document.
                    from ..workflow.registry import STEP_SPECS

                    step_status = [
                        (index, verb, status)
                        for index, verb, status in step_status
                        if not (
                            STEP_SPECS.get(verb) is not None and STEP_SPECS[verb].resets_document
                        )
                    ]
                self._recipe_panel.apply_run_outcomes(step_status)
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
        self._set_model_actions_enabled(self._session.store is not None)
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
        store = self._session.store
        mesh = store.preprocessed() if store is not None else None
        running = self._worker is not None and self._worker.isRunning()
        enabled = mesh is not None and not running
        for key in (
            "analysis.run",
            "analysis.modal",
            "analysis.spectrum",
            "analysis.pushover",
            "analysis.nonlinear_dynamic",
            "analysis.ida",
            "analysis.wizard",
        ):
            self._actions[key].setEnabled(enabled)
        # Saving the meshed model needs the same preprocessed ``MeshModel``, but
        # is safe while an analysis runs (the topology is frozen), so it is not
        # gated on ``running``.
        self._actions["file.save_model"].setEnabled(mesh is not None)
        # A project can be saved with just a model (results are optional), so it
        # is gated on a loaded model rather than on a preprocessed mesh.
        self._actions["file.save_project"].setEnabled(self._session.store is not None)
        action = self._actions["analysis.run"]
        if mesh is None:
            action.setToolTip(
                "Run static \u2014 needs a preprocessed model: Model \u25b8 Split elements or Mesh areas"
            )
        else:
            action.setToolTip("Solve the model's linear static cases or combinations")

    def _set_recipe_actions_enabled(self) -> None:
        """Enable the recipe actions that the panel's contents allow.

        Everything except *Run* is meaningful only with a step to act on, so an
        empty recipe leaves them greyed rather than letting a click do nothing.
        """
        if not hasattr(self, "_recipe_panel"):
            return
        # Reads the panel (not ``session.recipe``): this is a ``changed``
        # handler, so it must see the live editor's contents — the session is
        # only rebinded by the *later* ``_sync_recipe_to_session`` connection.
        has_steps = len(self._recipe_panel.recipe) > 0
        for key in ("recipe.run", "recipe.clear", "recipe.save", "recipe.export"):
            self._actions[key].setEnabled(has_steps)

    def _sync_recipe_to_session(self) -> None:
        """Point the session's recipe at the panel's live recipe.

        The recipe panel is the de-facto owner while the user edits; this one
        reference assignment (not a copy — ``Recipe`` is mutated in place) keeps
        ``session.recipe is panel.recipe`` after every change, so save and run
        read exactly what the panel shows.  Connected to
        :attr:`~fea_toolkit.gui.views.recipe_panel.RecipePanel.changed`.

        In-place edits (``add_step`` / ``remove_selected`` / ``move_selected`` /
        ``reset_statuses`` / ``apply_run_outcomes``) mutate the shared object, so
        they need no signal; the rebinds (``set_recipe`` / ``clear``) do emit
        ``changed``, which is what carries this write-back.  Keep this the
        *single* place the session's recipe is rebound, so a later move to a
        panel-as-view design stays a delete-plus-repoint.
        """
        self._session.recipe = self._recipe_panel.recipe

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
            self._session.recipe.to_json(path)
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
            Path(path).write_text(self._session.recipe.to_python(), encoding="utf-8")
        except OSError as exc:
            self.log(f"Could not write {path}: {exc}", "error")
            return
        self.log(f"Exported recipe: {path}")

    def _on_recipe_command(self) -> None:
        """**Recipe ▸ Command…** (Ctrl+K): compile a command string into a step.

        The palette reuses the recipe editor's selection grammar; on accept the
        compiled step is appended and echoed to the log so the session stays
        replayable.
        """
        from ..workflow import step_to_command
        from .views.command_palette import get_step

        mesh = self._session.store.preprocessed() if self._session.store is not None else None
        initial = {"geometry"} if mesh is not None else ()
        step = get_step(self, list(self._session.recipe), initial=initial)
        if step is None:
            return
        self._recipe_panel.add_step(step.verb, step.selection, step.params, optional=step.optional)
        self.log(f"command: {step_to_command(step)}")

    def _on_recipe_import(self) -> None:
        """**Recipe ▸ Import script…**: read a recipe's RECIPE literal from a script."""
        from pathlib import Path

        from ..workflow import Recipe

        path, _ = QFileDialog.getOpenFileName(self, "Import recipe script", "", "Python (*.py)")
        if not path:
            return
        try:
            recipe = Recipe.from_python(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError, SyntaxError) as exc:
            self.log(f"Could not import {path}: {exc}", "error")
            return
        self._recipe_panel.set_recipe(recipe)
        self.log(f"Imported recipe: {path} ({len(recipe)} steps)")

    # ── Handlers ────────────────────────────────────────────────────

    def _on_open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open model",
            "",
            "Structural model (*.s2k *.S2K *.$2k *.$2K *.e2k *.E2K *.$et *.$ET *.f2k *.F2K *.$sf *.$SF *.json *.JSON);;All files (*)",
        )
        if path and not self.open_path(path):
            QMessageBox.critical(
                self, "Open failed", f"Could not open:\n{path}\n\nSee the message log."
            )

    # ── Drag-and-drop ────────────────────────────────────────────────

    @staticmethod
    def _drop_targets(mime_data: QMimeData) -> list[str]:
        """The local file paths carried by a drag's URL list."""
        if not mime_data.hasUrls():
            return []
        return [url.toLocalFile() for url in mime_data.urls() if url.isLocalFile()]

    @staticmethod
    def _is_droppable(path: str) -> bool:
        """Whether a dropped file is a results archive, model or project bundle."""
        lower = path.lower()
        return (
            lower.endswith(_RESULTS_DROP_EXTENSIONS)
            or lower.endswith(_MODEL_DROP_EXTENSIONS)
            or lower.endswith(_PROJECT_DROP_EXTENSIONS)
        )

    def _droppable_paths(self, mime_data: QMimeData) -> list[str]:
        """The local archive-or-model paths in a drag's URL list."""
        return [p for p in self._drop_targets(mime_data) if self._is_droppable(p)]

    def _open_dropped_files(self, paths: list[str]) -> int:
        """Open each dropped archive, model or project; return how many opened.

        Results archives open one view per load case via
        :meth:`open_results_path`; project bundles rebuild a saved session via
        :meth:`open_project_path` after :meth:`_confirm_project_open`; model
        files replace the displayed model via :meth:`open_path` — the same three
        paths the File menus use.
        """
        opened = 0
        for path in paths:
            lower = path.lower()
            if lower.endswith(_RESULTS_DROP_EXTENSIONS):
                ok = self.open_results_path(path)
            elif lower.endswith(_PROJECT_DROP_EXTENSIONS):
                if not self._confirm_project_open(path):
                    continue
                ok = self.open_project_path(path)
            else:
                ok = self.open_path(path)
            if ok:
                opened += 1
        return opened

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._droppable_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self._droppable_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = self._droppable_paths(event.mimeData())
        if not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        if self._open_dropped_files(paths) == 0:
            QMessageBox.warning(
                self,
                "Nothing opened",
                "None of the dropped files is a readable results archive, model or project.\n\nSee the message log.",
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
            self._session.views.add_results(
                f"results:{case}",
                _case_view_name(case, meta),
                repository,
                source=f"{name} \u00b7 {case}",
                model=model,
                activate=False,
            )
        self.log(f"Opened {len(keys)} results view(s) from {name}.")

        first = self._session.views.get(f"results:{keys[0]}")
        if first is not None:
            self._session.hidden_elem_ids.clear()
            self._show_view(first, rebuild_tree=True)

        # IDA (IK-2005 collapse) — register a figure view when the archive
        # carries an ``ida`` block (schema v5).  Detected from the arrays the
        # repository already holds, so an HDF5 archive never re-opens the NPZ
        # reader (or logs its errors).
        try:
            from ..io.results_schema import ida_record_names

            if ida_record_names(repository.to_arrays()):
                from ..plotting.viz_ida import plot_ida_summary

                figure = plot_ida_summary(repository.to_arrays())
                if figure is not None:
                    self._session.views.add_figure(
                        "ida", "IDA collapse", figure, source=name, activate=False
                    )
                    self._rebuild_tree()
        except Exception as exc:  # pragma: no cover - defensive
            self.log(f"Could not build the IDA view: {exc}", "error")
        return True

    def _on_export_image(self) -> None:
        """**File ▸ Export screenshot…**: save the viewport to a PNG.

        Uses PyVista's ``screenshot()``, so what is written is exactly what the
        viewport shows.  The path is always prompted for, with a suggested name.
        """
        from pathlib import Path

        stem = Path(self._source_label).stem or "viewport"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export screenshot", f"{stem}.png", "PNG image (*.png)"
        )
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        try:
            self._interactor.screenshot(path)
        except Exception as exc:  # pragma: no cover - a filesystem/GL failure
            self.log(f"Could not export the screenshot: {exc}", "error")
            return
        self.log(f"Saved screenshot to {path}.")

    def _on_save_results(self) -> None:
        """**File ▸ Save results…**: write the displayed result to a chosen NPZ.

        The path is always prompted for — with a suggested name — rather than a
        fixed output, so variants differing by a small change can be kept side
        by side (P27, refinement 2).  What is saved is the archive the active
        view is reading, so the file round-trips to exactly what the view shows,
        and the run itself stays viewable without it.
        """
        from pathlib import Path

        view = self._session.views.active
        repository = self._session.views.results(view.key) if view is not None else None
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

        try:
            saved = repository.write(path)
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

        store = self._session.store
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

    def _on_save_project(self) -> None:
        """**File ▸ Save project…**: write the full session to a single ``.zip`` archive.

        A project is a zip archive (``<name>.zip``) holding the model stage, the
        results archive, the recipe, the ordered view list and the figure / table
        payloads — everything needed to reconstruct the session without re-running
        an analysis.
        """
        from pathlib import Path

        if self._session.store is None:
            self.log("Open a model before saving a project.", "warn")
            return

        from . import project as project_mod

        stem = Path(self._source_label).stem or "project"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save project", f"{stem}.zip", "FEA project (*.zip)"
        )
        if not path:
            return
        requested = path
        target = Path(path)
        if target.suffix.lower() != ".zip":
            target = Path(f"{target}.zip")
        if str(target) != requested and target.exists():
            answer = QMessageBox.question(
                self,
                "Confirm overwrite",
                f"{target} already exists.\nOverwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        bundle = self._session.to_bundle(
            name=stem,
            source_label=self._source_label,
            camera=self._current_camera(),
            display=self._collect_display_values(),
        )
        notes = project_mod.write_project_zip(target, **bundle)
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
        if not path:
            return
        if not self._confirm_project_open(path):
            return
        if not self.open_project_path(path):
            QMessageBox.critical(
                self,
                "Open failed",
                f"Could not open project:\n{path}\n\nSee the message log.",
            )

    def _confirm_project_open(self, path: str) -> bool:
        """Preview a recognised project archive's members and ask before opening.

        The member list is read from the zip's central directory — nothing is
        extracted — so the user sees what the archive holds before it is
        unpacked.  A directory or a non-project file returns ``True`` and is
        left to :meth:`open_project_path` to open or report.
        """
        from pathlib import Path

        from .project import is_project, project_member_summary

        if Path(path).is_dir() or not is_project(path):
            return True
        members = "\n".join(project_member_summary(path)) or "(no members)"
        answer = QMessageBox.question(
            self,
            "Open project",
            f"This project contains:\n\n{members}\n\nOpen it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        return answer == QMessageBox.StandardButton.Yes

    def open_project_path(self, path: str) -> bool:
        """Load a project (directory or ``.zip`` archive) and rebuild the session.

        Dialog-free so tests can drive it directly.  The model, results, tables
        and figures are read back from the bundle and re-registered as views — so
        nothing is re-run — and the active view, camera and hidden set are
        restored from the manifest.  The session itself is rebuilt Qt-free by
        :meth:`Session.from_bundle`; this method only re-attaches the GUI
        (recipe panel, display knobs, viewport camera).
        """
        from .project import is_project, project_member_summary, read_project

        self.log(f"Opening project {path} …")
        try:
            if not is_project(path):
                self.log(f"{path} is not a project (directory or .zip archive).", "error")
                return False
            for member in project_member_summary(path):
                self.log(f"  · {member}")
            bundle = read_project(path)
        except Exception as exc:
            self.log(f"Failed to open project {path}: {exc}", "error")
            return False

        if bundle.get("sap") is None:
            self.log("Project has no model stage; nothing to display.", "error")
            return False

        self._save_view_state()
        session = Session.from_bundle(bundle)
        self._session = session

        manifest = bundle["manifest"] or {}
        panel = getattr(self, "_recipe_panel", None)
        if panel is not None:
            panel.set_recipe(session.recipe)
        self._apply_display_values(manifest.get("display") or {})

        active = session._preferred_open_view(manifest.get("active_view"))
        if active is None:
            views = session.views.views()
            active = views[0] if views else None
        if active is not None:
            self._show_view(active, rebuild_tree=True, reset_view=True)
            self._restore_camera((manifest.get("viewport") or {}).get("camera_position"))

        self.log(f"Opened project: {len(session.views)} view(s).")
        return True

    def open_path(self, path: str) -> bool:
        """Parse *path* and display it; return False on failure (no dialog).

        A saved document (a ``__document__`` envelope written by the ``save``
        verb) is decoded through the envelope-aware loader so its named
        selections are restored with the model, rather than the full envelope
        being handed to the bare model decoder.

        Dialog-free so tests can drive it directly.
        """
        from ..io.document import load_document

        self.log(f"Opening {path} \u2026")
        try:
            model, selections = load_document(path)
        except Exception as exc:
            self.log(f"Failed to open {path}: {exc}", "error")
            return False
        self._save_view_state()  # preserve the outgoing model's view before switching
        self._remember_source(model, source=path)
        # A newly opened model starts with an empty recipe: the workflow being
        # authored belonged to the model just replaced.  Clearing the panel (the
        # live editor) propagates the empty recipe to the session via ``changed``.
        panel = getattr(self, "_recipe_panel", None)
        if panel is not None:
            panel.clear()
        self._session.named_selections = selections
        state, notes = load_view_state(path)
        for note in notes:
            # A stale state (the model was re-edited) is an ordinary outcome, so
            # it is logged at info; anything else is a warning.
            level = "info" if "is stale" in note else "warn"
            self.log(note, level)
        hidden = state.get("hidden_elem_ids")
        self._session.hidden_elem_ids = self._restore_hidden_elements(hidden, model)
        self.show_model(model)
        self._restore_camera(state.get("camera_position"))
        return True

    def _on_zoom_fit(self) -> None:
        self._quad.apply_orientations()

    def _on_single_view_toggled(self, checked: bool) -> None:
        """**View ▸ Single view**: collapse to one viewport or restore the 2×2 grid."""
        from dataclasses import replace

        self._quad.set_single_view(checked)
        self._display = replace(self._display, single_view=checked)

    def _view_iso(self) -> None:
        self._quad.set_orientation(self._quad.active_index, "iso")

    def _view_xy(self) -> None:
        self._quad.set_orientation(self._quad.active_index, "xy")

    def _view_xz(self) -> None:
        self._quad.set_orientation(self._quad.active_index, "xz")

    def _view_yz(self) -> None:
        self._quad.set_orientation(self._quad.active_index, "yz")

    def _on_docs(self) -> None:
        """**Help ▸ Documentation**: open the documentation site."""
        QDesktopServices.openUrl(QUrl(DOCS_URL))

    def _open_docs(self, page: str) -> None:
        """Open one documentation page on the MkDocs site.

        Args:
            page: A key in ``_DOC_PAGES`` naming the page to open.
        """
        QDesktopServices.openUrl(QUrl(DOCS_URL + _DOC_PAGES[page]))

    def _on_copy_context(self) -> None:
        """**Help ▸ Copy model context…**: snapshot the session to the clipboard.

        Assembles a paste-able plain-text summary — model provenance, unit
        system, entity counts, the current selection, the recipe and the open
        views — so a user can paste it into an LLM conversation or a bug report.
        """
        from ..io.model_store import model_header
        from .controllers.context_snapshot import build_context_snapshot

        model = self._model
        source = self._source_label
        units = ""
        counts: dict = {}
        if model is not None:
            header = model_header(model)
            units = header.units_label()
            counts = {
                "nodes": header.n_nodes,
                "frames": header.n_frames,
                "active frames": header.n_frames_active,
                "areas": header.n_shells,
                "materials": header.n_materials,
                "sections": header.n_sections,
            }

        selection = ""
        sel = self.current_selection()
        if sel is not None:
            selection = sel.to_string()

        recipe_json = ""
        recipe = self._session.recipe
        if len(recipe):
            recipe_json = recipe.to_json()

        views = [(view.name, view.kind) for view in self._session.views.views()]

        snapshot = build_context_snapshot(
            source=source,
            units=units,
            counts=counts,
            selection=selection,
            recipe_json=recipe_json,
            views=views,
        )
        QGuiApplication.clipboard().setText(snapshot)
        self.log(f"Copied model context ({len(snapshot)} characters) to the clipboard.")

    def _on_about(self) -> None:
        from .. import __version__ as version

        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> {version}<br><br>"
            f"{APP_MISSION}<br><br>"
            f'Documented at <a href="{DOCS_URL}">{DOCS_URL.rstrip("/")}</a>.',
        )

    # ── Analysis (Analysis menu) ────────────────────────────────────

    def _on_analysis_run(self) -> None:
        """**Analysis ▸ Run…**: pick cases, then run the static recipe.

        The listing is built Qt-free (``analysis.case_listing``) and handed to
        the dialog, which adds the per-case load multipliers; what comes back is
        a ``[run_static, combine, load_verification]`` recipe — one spelling of
        "solve static cases and show the load/reaction summary", exactly what the
        Recipe menu authors step by step.  Nothing is written to disk — the
        in-memory archive is registered as one view per case, so a result can be
        looked at without an NPZ.  ``File ▸ Save results`` persists one when
        wanted.
        """
        store = self._session.store
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
        request = AnalysisDialog.get_request(
            list_static_cases(md, mesh),
            list_combinations(md),
            list_patterns(md),
            self,
        )
        if request is None:  # cancelled
            return

        self.run_static_analysis(request.cases, request.combinations, config=request.config)

    def run_static_analysis(
        self, cases: dict, combinations: Optional[list] = None, config: Optional[dict] = None
    ) -> bool:
        """Solve *cases* (and reduce *combinations*) via the verbs — dialog-free.

        The entry point the dialog defers to, and that tests drive directly,
        exactly as :meth:`open_results_path` is for ``File ▸ Open results``.  It
        appends ``run_static``, ``combine`` (when combinations are named) and
        ``load_verification`` steps and runs the recipe, so the static solve and
        its reaction summary have one spelling — the verbs — rather than a
        private ``run_case_set`` path beside the recipe.

        Args:
            cases: ``{case: {pattern: factor}}`` to solve.
            combinations: Combination names to reduce — resolved against the
                model's definitions by the ``combine`` verb.
            config: Optional ``AnalysisBuilder`` config overrides, applied to
                both the solve and the summary's standalone re-solve fallback.

        Returns:
            ``True`` when the run started; ``False`` when it cannot yet (no
            preprocessed model) or one is already running.
        """
        store = self._session.store
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

        from ..workflow import Step

        steps = [
            Step(verb="run_static", params={"cases": dict(cases), "config": dict(config or {})})
        ]
        if combinations:
            steps.append(Step(verb="combine", params={"combinations": list(combinations)}))
        steps.append(Step(verb="load_verification", params={"config": dict(config or {})}))
        for step in steps:
            self._recipe_panel.add_step(
                step.verb, step.selection, step.params, optional=step.optional
            )
        self._pending_label = "Static analysis"
        self._on_recipe_run()
        return True

    def _on_analysis_stop(self) -> None:
        """**Analysis ▸ Stop**: ask the worker to stop at its next step boundary.

        Cancellation is cooperative, exactly as the threading model requires: a
        recipe run ends between steps, and a static solve ends between cases
        rather than mid-solve.
        """
        if self._worker is None or not self._worker.isRunning():
            return
        self._worker.cancel()
        self._actions["analysis.stop"].setEnabled(False)
        self.log("Stop requested \u2014 the run ends at the next case boundary.", "warn")

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
        for _interactor, _interaction, mouse_filter in getattr(self, "_secondary_interactions", []):
            _interactor.removeEventFilter(mouse_filter)
        if self._worker is not None and self._worker.isRunning():
            # Preprocessing is atomic, so this only gives it a grace period
            # rather than interrupting it mid-call.
            self._worker.cancel()
            self._worker.wait(3000)
        if self._quad is not None:
            for interactor in self._quad.interactors:
                with contextlib.suppress(Exception):
                    interactor.close()
        super().closeEvent(event)

    def _collect_display_values(self) -> dict:
        """The display knobs, shared by the settings save and the project save."""
        return {
            "shell_opacity": float(self._shell_opacity.value()),
            "shrink": float(self._shrink.value()),
            "deformed_scale": float(self._deformed_scale.value()),
            "force_scale": float(self._force_scale.value()),
            "load_scale": float(self._load_scale.value()),
            "force_quantity": self._force_quantity.currentText(),
            "restraint_style": self._display.restraint_style,
            "show_extruded": self._display.show_extruded,
            "extruded_detail": self._display.extruded_detail,
            "show_nodes": self._actions["view.show_nodes"].isChecked(),
            "show_frames": self._actions["view.show_frames"].isChecked(),
            "show_shells": self._actions["view.show_shells"].isChecked(),
            "show_restraints": self._actions["view.show_restraints"].isChecked(),
            "show_loads": self._actions["view.show_loads"].isChecked(),
            "load_source": self._display.load_source,
            "load_area_style": self._display.load_area_style,
            "show_load_colours": self._actions["view.show_load_colours"].isChecked(),
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
            (self._load_scale, "load_scale"),
        ):
            widget.blockSignals(True)
            widget.setValue(float(getattr(resolved, name)))
            widget.blockSignals(False)

        self._force_quantity.setCurrentText(resolved.force_quantity)
        idx = self._area_style.findData(resolved.load_area_style)
        if idx >= 0:
            self._area_style.setCurrentIndex(idx)

        self._display = replace(
            self._display,
            restraint_style=resolved.restraint_style,
            show_extruded=resolved.show_extruded,
            extruded_detail=resolved.extruded_detail,
            load_source=resolved.load_source,
            load_area_style=resolved.load_area_style,
        )
        self._set_toggle_checked("view.show_extruded", resolved.show_extruded)
        self._set_toggle_checked("view.show_nodes", resolved.show_nodes)
        self._set_toggle_checked("view.show_frames", resolved.show_frames)
        self._set_toggle_checked("view.show_shells", resolved.show_shells)
        self._set_toggle_checked("view.show_restraints", resolved.show_restraints)
        self._set_toggle_checked("view.show_loads", resolved.show_loads)
        self._set_toggle_checked("view.show_load_colours", resolved.show_load_colours)

    def _save_display_settings(self) -> None:
        """Write the display knobs to the settings file, best effort.

        ``single_view`` is a user layout preference, not a per-model display
        value, so it is written to the settings file here rather than through
        :meth:`_collect_display_values` (which also feeds ``File ▸ Save
        project``).

        A failure to save is reported in the log rather than raised, so closing
        the window never blocks on a read-only config directory.
        """
        values = self._collect_display_values()
        values["single_view"] = self._display.single_view
        for note in save_display_settings(default_config_path(), values):
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
        if self._session.store is not None:
            displayed = self._model
            if (
                displayed is not self._session.store.raw()
                and displayed is not self._session.store.preprocessed()
            ):
                return
        camera = self._current_camera()
        if self._reset_view_baseline is not None:
            baseline_camera, baseline_hidden = self._reset_view_baseline
            if (
                camera == baseline_camera
                and frozenset(self._session.hidden_elem_ids) == baseline_hidden
            ):
                return
        state: dict = {}
        # A hidden set only applies to the raw model a later open restores.  If
        # the user hid elements while viewing the preprocessed (split/meshed)
        # topology, those ids reference child elements absent from the raw file,
        # and restoring them would re-hide raw members that merely share ids
        # with the processed topology — so keep only the camera in that case.
        if self._session.store is None or self._model is not self._session.store.preprocessed():
            state["hidden_elem_ids"] = sorted(self._session.hidden_elem_ids)
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
