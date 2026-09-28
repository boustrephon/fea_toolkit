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


class InMemoryModelStore(ModelStore):
    """Default backend: the model is a Python object already in memory.

    Args:
        raw: The parsed ``SAPModelData``.
        mesh_model: Optional already-preprocessed model to return from
            :meth:`mesh` without re-running the pipeline.
        source: Human-readable provenance, e.g. a file name.
    """

    def __init__(self, raw: Any, mesh_model: Any = None, source: str = "") -> None:
        self._raw = raw
        self._mesh_model = mesh_model
        self.source = source

    def header(self, model: Any = None) -> ModelHeader:
        """Header for *model*, or for the stored raw model when omitted."""
        return model_header(self._raw if model is None else model)

    def raw(self) -> Any:
        return self._raw

    def mesh(self, config: Optional[dict] = None) -> Any:
        """Preprocess the raw model (uncached — configs differ per run)."""
        if self._mesh_model is not None:
            return self._mesh_model
        from ..opensees.preprocessor import preprocess_model

        return preprocess_model(self._raw, dict(config or {}))
