"""The GUI's list of views — named lenses over the models it has loaded.

A view is **not** a copy of a model.  It is a small, display-safe record (name,
provenance, counts) plus a key under which :class:`ViewRegistry` holds the
geometry to render, so several views can share one model and — once derived
views land — a refinement can be a parent reference plus a ``Selection`` filter
rather than a duplicate.  That contract is what stops a large model being
multiplied in memory, and it is the seam an HDF5-backed store plugs into (see
the storage seam in ``docs/dev_notes.md``).

Qt-free by design, so it is unit-tested without the ``[gui]`` extra.
"""

from dataclasses import dataclass, replace
from typing import Any, Optional

from ...io.model_store import ModelHeader, ModelStore, model_header

__all__ = ["GEOMETRY", "RESULTS", "View", "ViewRegistry"]

#: A rendering of a model's topology (raw, split, meshed).
GEOMETRY = "geometry"
#: A model plus a result dataset (deformed shapes, force diagrams).
RESULTS = "results"


@dataclass(frozen=True)
class View:
    """One named lens over a model.  Every field is safe to display.

    The counts are unpacked from the store's
    :class:`~fea_toolkit.io.model_store.ModelHeader` rather than nested inside
    it, because the Inspector renders an object's fields **flat** — a nested
    header would show up as one unreadable ``str()``.

    Attributes:
        key: Stable identifier, e.g. ``"unprocessed"`` or ``"meshed"``.
        name: Display name — what the Model Tree lists and the Inspector titles
            the selection with.
        kind: :data:`GEOMETRY` or :data:`RESULTS`.
        source: Human-readable provenance, e.g. ``"model.s2k"`` or
            ``"Preprocessor: split_elements"``.  The geometry itself lives in
            the registry, keyed by :attr:`key`, so a view never carries a model.
        units: Formatted unit system, e.g. ``"N · m · C"``.
        n_nodes: Node count.
        n_frames: Frame elements, including superseded split parents.
        n_frames_active: Frame elements an analysis would build.
        n_shells: Area elements.
        n_materials: Material definitions.
        n_sections: Section definitions.
        active: Whether this is the view currently displayed.
        parent: Key of the view a derived view refines (``None`` for a base).
        selection: Optional ``Selection`` narrowing what the view shows.
    """

    key: str
    name: str
    kind: str
    source: str
    units: str = ""
    n_nodes: int = 0
    n_frames: int = 0
    n_frames_active: int = 0
    n_shells: int = 0
    n_materials: int = 0
    n_sections: int = 0
    active: bool = False
    parent: Optional[str] = None
    selection: Any = None

    @classmethod
    def from_header(
        cls,
        key: str,
        name: str,
        kind: str,
        source: str,
        header: ModelHeader,
        **extra: Any,
    ) -> "View":
        """Build a view from a store's :class:`ModelHeader`.

        Args:
            key: Stable identifier.
            name: Display name.
            kind: :data:`GEOMETRY` or :data:`RESULTS`.
            source: Human-readable provenance.
            header: The counts to unpack.
            **extra: Further :class:`View` fields (``parent``, ``selection``).

        Returns:
            The view, with the header's counts flattened onto it.
        """
        return cls(
            key=key,
            name=name,
            kind=kind,
            source=source,
            units=header.units_label(),
            n_nodes=header.n_nodes,
            n_frames=header.n_frames,
            n_frames_active=header.n_frames_active,
            n_shells=header.n_shells,
            n_materials=header.n_materials,
            n_sections=header.n_sections,
            **extra,
        )


class ViewRegistry:
    """Owns the registered views and the geometry each one renders.

    Args:
        store: Optional :class:`~fea_toolkit.io.model_store.ModelStore` supplying
            headers.  Without one, headers are computed from the model object
            handed to :meth:`add_geometry` — the same numbers, no seam.
    """

    def __init__(self, store: Optional[ModelStore] = None) -> None:
        self._store = store
        self._views: dict[str, View] = {}
        self._sources: dict[str, Any] = {}
        self._results: dict[str, Any] = {}
        self._active: Optional[str] = None

    # ── Registration ─────────────────────────────────────────────────

    def add_geometry(
        self,
        key: str,
        name: str,
        model: Any,
        source: str = "",
        *,
        activate: bool = True,
    ) -> View:
        """Register (or replace) a geometry view over *model*.

        Replacing by :attr:`~View.key` is deliberate: re-running ``Split``
        refreshes the ``Processed`` view instead of piling up duplicates.

        Args:
            key: Stable identifier for the view.
            name: Display name.
            model: The model to render for this view.
            source: Human-readable provenance.
            activate: Make this the displayed view.

        Returns:
            The registered :class:`View`.
        """
        header = self._header_for(model)
        self._views[key] = View.from_header(key, name, GEOMETRY, source, header)
        self._sources[key] = model
        if activate or self._active is None:
            self._active = key
        return self.get(key)

    def set_store(self, store: Optional[ModelStore]) -> None:
        """Point the registry at a different store (headers come from it)."""
        self._store = store

    def add_derived(
        self,
        key: str,
        name: str,
        parent_key: str,
        selection: Any,
        *,
        activate: bool = True,
    ) -> Optional[View]:
        """Register a view rendering its parent's geometry through *selection*.

        A derived view is a **lens on a lens**: it shares the parent's geometry
        object — no copy, no second model — and adds the filter, so any number of
        refinements cost one model between them.  Its counts are the **filtered**
        counts, because the Inspector should report what the view actually shows.

        Args:
            key: Stable identifier for the derived view.
            name: Display name.
            parent_key: Key of the view being refined.
            selection: The ``Selection`` narrowing it (``None`` for no filter).
            activate: Make this the displayed view.

        Returns:
            The registered view, or ``None`` when *parent_key* is unknown.
        """
        if parent_key not in self._views:
            return None
        source = self._sources.get(parent_key)
        header = self._header_for(source)
        if selection is not None and source is not None:
            frames, areas, nodes = selection.resolve_connected(source)
            header = replace(
                header,
                n_nodes=len(nodes),
                n_frames=len(frames),
                n_frames_active=len(frames),
                n_shells=len(areas),
            )
        parent = self._views[parent_key]
        view = View.from_header(
            key,
            name,
            GEOMETRY,
            parent.source,
            header,
            parent=parent_key,
            selection=selection,
        )
        self._views[key] = view
        self._sources[key] = source
        if activate or self._active is None:
            self._active = key
        return self.get(key)

    def _header_for(self, model: Any) -> "ModelHeader":
        """Counts for *model* — through the store when there is one."""
        if self._store is not None:
            return self._store.header(model)
        return model_header(model)

    def add_results(
        self,
        key: str,
        name: str,
        repository: Any,
        source: str = "",
        model: Any = None,
        *,
        activate: bool = True,
    ) -> View:
        """Register a results view over *repository* — no ``ModelStore`` needed.

        An archive carries its own display geometry, so this view needs neither a
        store nor an open model, and its counts come from the archive's array
        shapes (never a walk).  Passing *model* is what makes it **drawable**:
        callers build one display model from the archive and share it across the
        cases, so several case views cost one model between them.

        Args:
            key: Stable identifier.
            name: Display name.
            repository: A ``ResultsRepository``.
            source: Human-readable provenance, e.g. the file name and case.
            model: Optional model to render (see
                :meth:`ResultsRepository.as_model`).
            activate: Make this the displayed view.

        Returns:
            The registered view.
        """
        counts = repository.geometry_counts()
        n_frames = int(counts.get("n_frames", 0))
        view = View(
            key=key,
            name=name,
            kind=RESULTS,
            source=source,
            n_nodes=int(counts.get("n_nodes", 0)),
            n_frames=n_frames,
            n_frames_active=n_frames,
            n_shells=int(counts.get("n_shells", 0)),
        )
        self._views[key] = view
        self._results[key] = repository
        if model is not None:
            self._sources[key] = model
        if activate or self._active is None:
            self._active = key
        return self.get(key)

    def results(self, key: str) -> Any:
        """The repository a results view reads (``None`` for a geometry view)."""
        return self._results.get(key)

    def reset(self) -> None:
        """Drop every view and its geometry."""
        self._views.clear()
        self._sources.clear()
        self._results.clear()
        self._active = None

    # ── Lookup ───────────────────────────────────────────────────────

    def views(self) -> list:
        """Registered views in registration order, with :attr:`View.active` set."""
        return [replace(view, active=key == self._active) for key, view in self._views.items()]

    def get(self, key: str) -> Optional[View]:
        """The view registered under *key*, or ``None``."""
        view = self._views.get(key)
        return None if view is None else replace(view, active=key == self._active)

    def source(self, key: str) -> Any:
        """The geometry to render for *key* (a view never carries its model)."""
        return self._sources.get(key)

    def set_active(self, key: str) -> bool:
        """Mark *key* as the displayed view; ``False`` if it is not registered."""
        if key not in self._views:
            return False
        self._active = key
        return True

    @property
    def active(self) -> Optional[View]:
        """The displayed view, or ``None`` when nothing is registered."""
        return self.get(self._active) if self._active is not None else None

    def __len__(self) -> int:
        return len(self._views)
