"""Metadata-only model summaries, and the store that provides them.

A GUI *view* is a lens over a model, not a copy of it, so the interface must be
able to answer "how many nodes, beams and shells?" without walking — let alone
duplicating — a geometry graph.  :class:`ModelHeader` is that answer, and
:class:`ModelStore` is the seam that produces it: the in-memory backend reads the
dataclasses it already holds (today), while an HDF5 backend can read array shapes
from a stage file without materialising anything (see the storage seam in
``docs/dev_notes.md``).

Everything here is Qt-free, OpenSees-free and NumPy-only, so it is unit-tested
without the ``[gui]`` extra.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = ["InMemoryModelStore", "ModelHeader", "ModelStore", "model_header"]

#: The declared model collections :meth:`ModelStore.collection` answers — the
#: attribute names the tree, the header and the load backfill read.  A name
#: outside this set raises ``KeyError``, so a typo is an error rather than a
#: silently empty group.
_COLLECTIONS = frozenset(
    {
        # dict-valued
        "nodes",
        "frame_elements",
        "area_elements",
        "wall_elements",
        "materials",
        "sections",
        "groups",
        "restraints",
        "load_patterns",
        "load_cases",
        "load_combinations",
        "mass_sources",
        "response_spectrum_functions",
        "shell_uniform_load_sets",
        "shell_uniform_load_set_assignments",
        "units",
        # list-valued
        "joint_loads",
        "frame_dist_loads",
        "area_uniform_loads",
        "frame_gravity_loads",
        "area_gravity_loads",
        "stories",
        "story_levels",
    }
)

#: The collections :meth:`ModelStore.backfill_mesh_load_metadata` copies from
#: the raw model onto a mesh that lacks them.
_BACKFILL_COLLECTIONS = (
    "load_cases",
    "load_combinations",
    "response_spectrum_functions",
    "shell_uniform_load_sets",
    "shell_uniform_load_set_assignments",
    "stories",
)


@dataclass(frozen=True)
class ModelHeader:
    """Cheap, metadata-only summary of one model.

    Attributes:
        units: ``{"F": ..., "L": ..., "T": ...}`` as the model declares them.
        n_nodes: Node count.
        n_frames: Frame elements, including superseded split parents.
        n_frames_active: Frame elements an analysis would build (``inactive`` is
            ``False``) — the beam count a user means by "the model".
        n_shells: Area elements.
        n_materials: Material definitions.
        n_sections: Section definitions.
    """

    units: dict = field(default_factory=dict)
    n_nodes: int = 0
    n_frames: int = 0
    n_frames_active: int = 0
    n_shells: int = 0
    n_materials: int = 0
    n_sections: int = 0

    def units_label(self) -> str:
        """``"N · m · C"`` for the status bar, or ``"units —"`` when unknown."""
        if not self.units:
            return "units \u2014"
        return (
            f"{self.units.get('F', '?')} \u00b7 "
            f"{self.units.get('L', '?')} \u00b7 "
            f"{self.units.get('T', '?')}"
        )


def model_header(model: Any) -> ModelHeader:
    """Summarise *model* from its metadata alone.

    Args:
        model: A ``SAPModelData``, a ``MeshModel`` or an ``AnalysisBuilder``
            (whose ``.model`` is unwrapped).

    Returns:
        A :class:`ModelHeader` — only dictionary sizes and one pass to count
        active frames.  No geometry is extracted and nothing is copied.
    """
    source = getattr(model, "model", model)  # AnalysisBuilder -> MeshModel
    frames = getattr(source, "frame_elements", None) or {}
    return ModelHeader(
        units=dict(getattr(source, "units", None) or {}),
        n_nodes=len(getattr(source, "nodes", None) or {}),
        n_frames=len(frames),
        n_frames_active=sum(1 for elem in frames.values() if not getattr(elem, "inactive", False)),
        n_shells=len(getattr(source, "area_elements", None) or {}),
        n_materials=len(getattr(source, "materials", None) or {}),
        n_sections=len(getattr(source, "sections", None) or {}),
    )


class ModelStore(ABC):
    """Contract for supplying model topology, and its header, to the GUI.

    Keeping *where* a model comes from behind this interface is what lets the
    GUI hold a **handle** rather than a graph: an HDF5 store can answer
    :meth:`header` from array shapes and materialise :meth:`mesh` on demand
    without a caller changing.
    """

    @abstractmethod
    def header(self, model: Any = None) -> ModelHeader:
        """The header for *model* (default: this store's raw model)."""

    @abstractmethod
    def raw(self) -> Any:
        """The parsed, unprocessed model."""

    @abstractmethod
    def mesh(self, config: Optional[dict] = None) -> Any:
        """Preprocess the raw model and return the ``MeshModel``.

        Runs the full topology pipeline, so callers **must** invoke it off the
        GUI thread (the roadmap's threading model).
        """

    @abstractmethod
    def collection(self, name: str) -> Any:
        """One model collection by name, or ``None`` when the model lacks it.

        The validated read behind the store: the in-memory backend answers
        from the dataclass it holds, an HDF5 backend from a named dataset.
        *name* must be one of :data:`_COLLECTIONS` — anything else raises
        ``KeyError``, so a typo is an error rather than a silently empty
        group.
        """

    def preprocessed(self) -> Any:
        """The ``MeshModel`` a previous :meth:`mesh` produced, or ``None``.

        Distinct from :meth:`mesh`, which *produces* one: this only reports what
        a completed run left behind.  ``Analysis ▸ Run`` reads it to decide
        whether preprocessing is a prerequisite it must ask the user for
        (``docs/_pending_work.md`` → P27, refinement 1), so it must never
        trigger the pipeline itself.  A backend that does not cache a
        preprocessed model simply answers ``None``.
        """
        return None

    def set_preprocessed(self, mesh_model: Any) -> None:
        """Record *mesh_model* as the preprocessed model this store serves.

        The default is a no-op: only a backend that caches a preprocessed model
        (the in-memory one) needs to keep it, and one that does not cannot
        answer :meth:`preprocessed` either.
        """
        return None

    def backfill_mesh_load_metadata(self, mesh_model: Any) -> None:
        """Copy SAP load definitions onto a disk-loaded *mesh_model* lacking them.

        A project saved before ``MeshModel`` carried ``load_cases`` /
        ``load_combinations`` / ``response_spectrum_functions`` holds a mesh
        with empty dicts.  Preprocessing never changes those definitions, so
        backfill them from the raw model at open time — the Model Tree then
        shows Load Cases, Load Combinations and Response Spectra without a
        re-save.  Mapping-valued collections merge **per definition**: a
        definition the mesh already has (even one that differs from the raw
        model's) is left untouched, and only a missing definition is restored.
        List-valued collections (``stories``) are backfilled only when empty.
        """
        for name in _BACKFILL_COLLECTIONS:
            source = getattr(self.raw(), name, None)
            if not source:
                continue
            target = getattr(mesh_model, name, None)
            if isinstance(source, dict):
                if not isinstance(target, dict):
                    target = {}
                    setattr(mesh_model, name, target)
                for key, value in source.items():
                    target.setdefault(key, value)
            elif not target:
                setattr(mesh_model, name, source)


class InMemoryModelStore(ModelStore):
    """Default backend: the model is a Python object already in memory.

    Args:
        raw: The parsed ``SAPModelData``.
        mesh_model: Optional **already-preprocessed** model to serve from
            :meth:`mesh` (and :meth:`preprocessed`) without re-running the
            pipeline.  Leave it ``None`` — the GUI's case — so that every
            :meth:`mesh` call runs the Preprocessor with the config it was
            given.
        source: Human-readable provenance, e.g. a file name.
    """

    def __init__(self, raw: Any, mesh_model: Any = None, source: str = "") -> None:
        self._raw = raw
        self._injected = mesh_model
        #: The last mesh this store *produced* (not injected).  Kept apart from
        #: ``_injected`` deliberately — see :meth:`mesh`.
        self._produced: Any = None
        self.source = source

    def header(self, model: Any = None) -> ModelHeader:
        """Header for *model*, or for the stored raw model when omitted."""
        return model_header(self._raw if model is None else model)

    def raw(self) -> Any:
        return self._raw

    def collection(self, name: str) -> Any:
        if name not in _COLLECTIONS:
            raise KeyError(f"unknown model collection: {name!r}")
        source = self.preprocessed() or self.raw()
        return getattr(source, name, None)

    def mesh(self, config: Optional[dict] = None) -> Any:
        """Preprocess the raw model with *config*.

        A mesh passed to the constructor short-circuits this — the caller has
        already done the work.  Otherwise the pipeline runs **every time, with
        the config given**: ``Model ▸ Split elements`` and ``Model ▸ Mesh
        areas`` are two different configs, and returning the first result for
        the second silently made the second a no-op (the Admin Building was then
        analysed with 323 un-meshed areas instead of 1177 shells, and the run
        went singular).
        """
        if self._injected is not None:
            return self._injected
        from ..opensees.preprocessor import preprocess_model

        return preprocess_model(self._raw, dict(config or {}))

    def preprocessed(self) -> Any:
        """The preprocessed model this store holds, or ``None``.

        The injected one if there is one, else the last one :meth:`mesh`
        produced.
        """
        return self._injected if self._injected is not None else self._produced

    def set_preprocessed(self, mesh_model: Any) -> None:
        """Record *mesh_model* as the last preprocessed model this store produced.

        The GUI calls this when a ``Model ▸ Split`` / ``Mesh areas`` run
        finishes, so a following analysis uses the topology the user is looking
        at (P27, refinement 1).  It is a **record, not a cache**: :meth:`mesh`
        still re-runs the pipeline for a new config.
        """
        self._produced = mesh_model
