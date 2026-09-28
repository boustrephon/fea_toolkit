"""One NumPy-typed read seam for results, whatever the backing store.

Every consumer — plotting, Rhino export, the report, the GUI — already reads
results as a ``dict[str, numpy.ndarray]`` (produced by
:func:`fea_toolkit.io.npz_reader.read_results`).  This module turns that habit
into a **contract**: a repository answers case listing, per-case metadata and
per-case arrays, so the backing format can change — NPZ today; Parquet, Feather,
HDF5 or a DuckDB query later — without a single consumer changing.

Why this is the right seam is argued in ``docs/dev_notes.md`` → *Results
repository and the NumPy-typed seam*: Arrow-family tools (pyarrow, Polars,
DuckDB) all hand back NumPy arrays cheaply, so a NumPy-typed boundary is what
keeps that door open rather than closing it.

The rules this interface encodes:

1. Results are read **here or in** :mod:`fea_toolkit.io.npz_reader` — never by
   calling ``np.load`` from a consumer (enforced by
   ``tests/test_storage_seam.py``).
2. A case's ``group`` / ``family`` / ``coords`` travel as **explicit columns**
   (:data:`~fea_toolkit.io.results_schema.CASE_META_KEYS`), not name-mangled keys.
3. Backends stay optional and lazily imported: this module imports **numpy only**.
"""

from abc import ABC, abstractmethod
from typing import Any, Optional

import numpy as np

from .results_schema import CASE_META_KEYS, GEOMETRY_ARRAYS

__all__ = ["NpzResultsRepository", "ResultsRepository"]


def _array_length(arrays: dict, name: str) -> int:
    """Length of *name* in *arrays*, or ``0`` when it is absent."""
    values = arrays.get(name)
    return 0 if values is None else int(len(values))


class ResultsRepository(ABC):
    """Read-only access to one results archive, as NumPy arrays.

    Implementations must not require the caller to know the on-disk format.
    """

    @abstractmethod
    def cases(self) -> list:
        """Static load-case labels present in the archive."""

    @abstractmethod
    def case_meta(self, case: str) -> dict:
        """``{"group": ..., "family": ..., "coords": ...}`` for *case*.

        Missing fields are simply absent — an archive written before schema
        version 3 carries no per-case metadata at all.
        """

    @abstractmethod
    def arrays_for(self, case: str) -> dict:
        """The dense result blocks for one case, keyed by array name."""

    @abstractmethod
    def display_geometry(self) -> dict:
        """Node coordinates, frame/shell connectivity and ids — enough to draw."""

    @abstractmethod
    def geometry_counts(self) -> dict:
        """Geometry size, from array shapes: ``n_nodes`` / ``n_frames`` / ``n_shells``.

        Read without converting anything, so a view can report what an archive
        holds without materialising it.
        """

    @abstractmethod
    def table(self, *columns: str) -> dict:
        """Named columns, for callers that want analytics rather than a case.

        Raises:
            KeyError: If a requested column is not in the archive.
        """


class NpzResultsRepository(ResultsRepository):
    """``.npz`` / ``.h5`` backend, wrapping :mod:`fea_toolkit.io.npz_reader`.

    Args:
        path: Archive path, or an already-read ``{name: array}`` dict.

    Raises:
        FileNotFoundError: If *path* does not exist.
    """

    def __init__(self, path: Any) -> None:
        if isinstance(path, dict):
            self._path = ""
            self._data = path
            return
        from .npz_reader import read_results  # dispatches on the extension

        self._path = str(path)
        self._data = read_results(self._path)

    # ── Cases ────────────────────────────────────────────────────────

    def cases(self) -> list:
        """Static case labels, in archive order."""
        labels = self._data.get("static_case_labels")
        return [] if labels is None else [str(label) for label in labels]

    def case_meta(self, case: str) -> dict:
        """``group`` / ``family`` / ``coords`` for *case*, where recorded."""
        labels = self.cases()
        if case not in labels:
            return {}
        index = labels.index(case)
        meta = {}
        for field_name, array_name in CASE_META_KEYS.items():
            values = self._data.get(array_name)
            if values is not None and index < len(values):
                meta[field_name] = str(values[index])
        return meta

    # ── Arrays ───────────────────────────────────────────────────────

    def arrays_for(self, case: str) -> dict:
        """The arrays under ``static/<case>/``, keyed without that prefix."""
        prefix = f"static/{case}/"
        return {
            key[len(prefix) :]: value
            for key, value in self._data.items()
            if key.startswith(prefix) and isinstance(value, np.ndarray)
        }

    def display_geometry(self) -> dict:
        """The schema's geometry arrays that this archive actually carries."""
        return {
            name: self._data[name]
            for name in GEOMETRY_ARRAYS
            if isinstance(self._data.get(name), np.ndarray)
        }

    def geometry_counts(self) -> dict:
        """Geometry sizes from the array shapes — no array is converted."""
        geometry = self.display_geometry()
        n_frames = _array_length(geometry, "frame_node_i")
        return {
            "n_nodes": _array_length(geometry, "node_tag"),
            "n_frames": n_frames,
            "n_shells": _array_length(geometry, "shell_node_1"),
        }

    def table(self, *columns: str) -> dict:
        """Named columns, e.g. ``table("node_x", "frame_sap_id")``.

        Raises:
            KeyError: If any requested column is missing from the archive.
        """
        missing = [name for name in columns if not isinstance(self._data.get(name), np.ndarray)]
        if missing:
            raise KeyError(f"unknown column(s): {', '.join(sorted(missing))}")
        return {name: self._data[name] for name in columns}

    # ── Convenience ──────────────────────────────────────────────────

    @property
    def path(self) -> str:
        """The archive this repository reads (``""`` for a dict-backed one)."""
        return self._path

    def raw(self) -> dict:
        """The underlying ``{name: array}`` dict.

        An escape hatch for code that has not been migrated yet — prefer the
        named methods above, which are what a non-NPZ backend can also serve.
        """
        return self._data

    def __len__(self) -> int:
        return len(self._data)

    def get(self, name: str, default: Optional[np.ndarray] = None) -> Any:
        """One array by name, or *default* when absent."""
        value = self._data.get(name)
        return default if value is None else value
