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
4. Results are written through ``ResultsRepository.write()`` or
   :func:`~fea_toolkit.io.npz_writer.save_results_arrays` — never by calling
   ``np.savez`` from a consumer (enforced by ``tests/test_storage_seam.py``).
"""

from abc import ABC, abstractmethod
from typing import Any, Optional

import numpy as np

from .results_schema import (
    CASE_META_KEYS,
    GEOMETRY_ARRAYS,
    STATIC_FORCE_ARRAYS,
    STATIC_LOCAL_FORCE_ARRAYS,
    STATIC_NODAL_ARRAYS,
    STATIC_SHELL_COMPONENT_ARRAYS,
    transient_case_names,
)

__all__ = ["NpzResultsRepository", "ResultsRepository", "mesh_model_from_geometry"]

#: Frame end-force components, in the schema's array spelling.  The archive
#: writes each one per end — ``{component}_i`` / ``{component}_j`` — see
#: :data:`~fea_toolkit.io.results_schema.STATIC_FORCE_ARRAYS`.  Spelled out
#: rather than parsed out of the schema names, so the two lists fail loudly
#: side by side instead of one silently deriving from the other.
_FORCE_COMPONENTS = ("fx", "fy", "fz", "mx", "my", "mz")

#: Shell resultants (membrane + bending per unit width), in the array spelling
#: used both by ``extract_static_shell_forces`` and by the per-case ``shell_*``
#: block.  Spelled out (not derived from the schema names) so the two lists
#: fail loudly side by side — the same rule as :data:`_FORCE_COMPONENTS`.
_SHELL_COMPONENTS = ("fx", "fy", "fxy", "mx", "my", "mxy")

#: Transient nodal displacement components, in the array spelling used by the
#: per-case ``transient/<case>/node_disp_*`` block.  Spelled out (not derived
#: from the schema names) so the two fail loudly side by side — the same rule
#: as :data:`_FORCE_COMPONENTS`.
_TRANSIENT_DISP_COMPONENTS = ("node_disp_x", "node_disp_y", "node_disp_z")


def _array_length(arrays: dict, name: str) -> int:
    """Length of *name* in *arrays*, or ``0`` when it is absent."""
    values = arrays.get(name)
    return 0 if values is None else int(len(values))


def _floats(values: Any) -> list:
    """Coerce an optional numeric array to a list of floats (``[]`` if absent)."""
    return [] if values is None else [float(value) for value in values]


def _ints(values: Any) -> list:
    """Coerce an optional integer array to a list of ints (``[]`` if absent)."""
    return [] if values is None else [int(value) for value in values]


def _texts(values: Any) -> list:
    """Coerce an optional string array to a list of ``str`` (``[]`` if absent)."""
    return [] if values is None else [str(value) for value in values]


def _at(sequence: list, index: int, default: Any = 0) -> Any:
    """``sequence[index]``, or *default* when the array is shorter (or absent)."""
    return sequence[index] if index < len(sequence) else default


def _at_float(values: Any, index: int) -> Optional[float]:
    """``float(values[index])``, or ``None`` when the array is absent or short.

    Absent and *shorter than the geometry* both mean **not covered**: an archive
    may record a component for fewer elements than it draws, and that has to
    leave the rest out rather than fabricate a zero — the same guard the
    plotting readers apply component by component.
    """
    if values is None or index >= len(values):
        return None
    return float(values[index])


def _children_by_parent(ids: list, parents: list) -> dict:
    """Map each parent label to the ids that name it as their parent."""
    children: dict = {}
    for elem_id, parent in zip(ids, parents):
        if parent:
            children.setdefault(parent, []).append(elem_id)
    return children


def mesh_model_from_geometry(
    geometry: dict, sections: Optional[dict] = None, restraints: Optional[dict] = None
) -> Any:
    """Build a displayable ``MeshModel`` from an archive's geometry arrays.

    An archive carries enough geometry to *draw* a model — node coordinates,
    element connectivity, section names and parent/child links — and not enough
    to analyse one.  The model returned here is therefore for **display**: a
    results view renders it without needing the ``.s2k`` it came from, and
    analysis from it is impossible because there are no materials, loads or
    restraints behind it.  Archives carry no unit system either, so the model
    reports the toolkit's default.

    Parent links are reconstructed rather than trusted: an element named in
    another's ``*_parent_sap_id`` is marked ``inactive`` with its ``child_ids``,
    exactly as the Preprocessor leaves it, so a rebuilt model collapses and
    expands the way the analysed one did.

    Args:
        geometry: The arrays from :meth:`ResultsRepository.display_geometry`.
            Missing blocks are skipped, so a frame-only or shell-only archive
            rebuilds cleanly.
        sections: Optional ``{name: Section}`` to attach to the rebuilt model.
            A results archive stores section *names* but not definitions; pass
            the source model's sections so a results view colours its elements
            the same way the geometry views do.
        restraints: Optional ``{node_id: Restraint}`` to attach, so a results
            view draws the model's support symbols.  The archive itself carries
            no restraints.

    Returns:
        A ``MeshModel`` ready for ``ModelViewer(mesh_model=...)``.
    """
    from ..model.mesh_model import MeshModel
    from ..model.sap_data import AreaElement, FrameElement, Node

    node_ids = _texts(geometry.get("node_sap_id"))
    node_tags = _ints(geometry.get("node_tag"))
    xs, ys, zs = (_floats(geometry.get(name)) for name in ("node_x", "node_y", "node_z"))

    nodes: dict = {}
    node_of_tag: dict = {}
    for index, tag in enumerate(node_tags):
        node_id = _at(node_ids, index) or str(tag)
        nodes[node_id] = Node(
            node_id=node_id,
            node_tag=int(tag),
            x=_at(xs, index, 0.0),
            y=_at(ys, index, 0.0),
            z=_at(zs, index, 0.0),
        )
        node_of_tag[str(tag)] = node_id

    frame_ids = _texts(geometry.get("frame_sap_id"))
    frame_tags = _ints(geometry.get("frame_eid"))
    frame_i = _ints(geometry.get("frame_node_i"))
    frame_j = _ints(geometry.get("frame_node_j"))
    frame_sections = _texts(geometry.get("frame_sec_name"))
    frame_parents = _texts(geometry.get("frame_parent_sap_id"))
    frame_children = _children_by_parent(frame_ids, frame_parents)

    frames: dict = {}
    frame_assignments: dict = {}
    for index, elem_id in enumerate(frame_ids):
        node_i = node_of_tag.get(str(_at(frame_i, index)))
        node_j = node_of_tag.get(str(_at(frame_j, index)))
        if node_i is None or node_j is None:
            continue  # an element with no drawable geometry
        frames[elem_id] = FrameElement(
            elem_id=elem_id,
            elem_tag=int(_at(frame_tags, index, index + 1)),
            node_i=node_i,
            node_j=node_j,
            inactive=elem_id in frame_children,
            parent_id=_at(frame_parents, index) or None,
            child_ids=list(frame_children.get(elem_id, ())),
        )
        section = _at(frame_sections, index)
        if section:
            frame_assignments[elem_id] = section

    shell_ids = _texts(geometry.get("shell_sap_id"))
    shell_tags = _ints(geometry.get("shell_eid"))
    shell_sections = _texts(geometry.get("shell_sec_name"))
    shell_parents = _texts(geometry.get("shell_parent_sap_id"))
    shell_children = _children_by_parent(shell_ids, shell_parents)
    corners = [_ints(geometry.get(f"shell_node_{column}")) for column in (1, 2, 3, 4)]

    areas: dict = {}
    area_assignments: dict = {}
    for index, area_id in enumerate(shell_ids):
        node_ids_of_area: list = []
        for column in corners:
            node_id = node_of_tag.get(str(_at(column, index)))
            if node_id is not None and node_id not in node_ids_of_area:
                node_ids_of_area.append(node_id)
        if len(node_ids_of_area) < 3:
            continue  # a triangle stored with a repeated corner still has three
        areas[area_id] = AreaElement(
            area_id=area_id,
            area_tag=int(_at(shell_tags, index, index + 1)),
            node_ids=node_ids_of_area,
            inactive=area_id in shell_children,
            parent_id=_at(shell_parents, index) or None,
            child_ids=list(shell_children.get(area_id, ())),
        )
        section = _at(shell_sections, index)
        if section:
            area_assignments[area_id] = section

    return MeshModel(
        nodes=nodes,
        frame_elements=frames,
        frame_assignments=frame_assignments,
        area_elements=areas,
        area_assignments=area_assignments,
        frame_dist_loads=[],
        sections=sections or {},
        restraints=restraints or {},
    )


class ResultsRepository(ABC):
    """Access to one results archive, as NumPy arrays.

    Read-mostly, with a named bundle read (:meth:`to_arrays`) and a write
    closure (:meth:`write`).  Implementations must not require the caller to
    know the on-disk format.
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
    def transient_cases(self) -> list:
        """Transient (time-history) case labels present in the archive."""

    @abstractmethod
    def transient_arrays_for(self, case: str) -> dict:
        """The transient result block for one case, keyed by array name."""

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
    def metadata(self, name: str, default: Any = None) -> Any:
        """One **file-level** array by name, or *default* when absent.

        The arrays in
        :data:`~fea_toolkit.io.results_schema.META_ARRAYS` — the unit labels,
        ``forces_coordinate_system``, ``created``, the schema version — are
        written **once per archive**, not per case, so they are not part of
        :meth:`arrays_for`.  A view needs them to know what it is showing: a
        force diagram has to know whether the end forces it just read are
        element-local or global before it can orient them.
        """

    def as_model(self, sections: Optional[dict] = None, restraints: Optional[dict] = None) -> Any:
        """A **displayable** ``MeshModel`` built from this archive's geometry.

        What lets a results view draw the deformed — or simply the analysed —
        geometry without the ``.s2k`` it came from.  The model is for display
        only: an archive carries no materials, loads or restraints, and no unit
        system, so nothing here can be analysed.

        Args:
            sections: Optional ``{name: Section}`` to attach, so a results view
                colours its elements the same way the geometry views do.  The
                archive itself carries section names, not definitions.
            restraints: Optional ``{node_id: Restraint}`` to attach, so a results
                view draws the model's support symbols.
        """
        return mesh_model_from_geometry(
            self.display_geometry(), sections=sections, restraints=restraints
        )

    def has_displacements(self, case: str) -> bool:
        """Whether *case* carries nodal displacement — the cheap pre-check.

        Displacement recording is optional, so a perfectly valid archive can
        have none; a caller deciding whether to *offer* a deformed shape wants
        to know that without building the arrays.

        Args:
            case: Static load-case label, as in :meth:`cases`.

        Returns:
            ``True`` when any displacement component is present for *case*.
        """
        arrays = self.arrays_for(case)
        return any(name in arrays for name in STATIC_NODAL_ARRAYS)

    def nodal_displacements(self, case: str) -> dict:
        """``{node_id: (dx, dy, dz)}`` for *case* — a deformed shape's input.

        Concrete rather than abstract, because it is assembled from
        :meth:`display_geometry` and :meth:`arrays_for`, so every backend has
        it.  The ids are the ones :meth:`as_model` names its nodes with, which
        is what the viewers are keyed by.

        Args:
            case: Static load-case label, as in :meth:`cases`.

        Returns:
            ``{node_id: np.ndarray}`` of ``(dx, dy, dz)`` in model units,
            with zero for a node the archive does not cover.  Empty when the
            archive recorded no displacement for *case* — a normal state, not
            an error, so callers report it rather than raising.
        """
        arrays = self.arrays_for(case)
        if not any(name in arrays for name in STATIC_NODAL_ARRAYS):
            return {}

        geometry = self.display_geometry()
        node_ids = _texts(geometry.get("node_sap_id"))
        node_tags = _ints(geometry.get("node_tag"))
        components = [np.asarray(arrays.get(name, []), dtype=float) for name in STATIC_NODAL_ARRAYS]

        displacements: dict = {}
        for index, tag in enumerate(node_tags):
            node_id = _at(node_ids, index) or str(tag)
            displacements[node_id] = np.array(
                [_at(component, index, 0.0) for component in components], dtype=float
            )
        return displacements

    def has_transient_displacements(self, case: str) -> bool:
        """Whether *case* carries transient nodal displacement history.

        The time-history counterpart of :meth:`has_displacements`: recording is
        optional, so a caller deciding whether to *offer* a transient animation
        asks here rather than building the arrays.

        Args:
            case: Transient case label, as in :meth:`transient_cases`.

        Returns:
            ``True`` when any ``node_disp_*`` column is present for *case*.
        """
        arrays = self.transient_arrays_for(case)
        return any(name in arrays for name in _TRANSIENT_DISP_COMPONENTS)

    def transient_nodal_displacements(self, case: str) -> tuple:
        """``(times, base_shear, {node_id: (N_t, 3)})`` for *case*.

        Re-keys the dense ``node_disp_*`` columns (``N_t × N_rec`` over the
        recorded subset, column order given by ``node_tag``) and maps each
        ``node_tag`` to its ``node_id`` via :meth:`display_geometry` — exactly
        the tag→id join :meth:`nodal_displacements` performs, so the ids are the
        ones :meth:`as_model` names its nodes with.  Unrecorded nodes are
        omitted (the writer records exactly the nodes in ``node_tag`` and never
        NaN-fills displacements), so a caller degrades to "gray for missing"
        rather than fabricating zeros.

        Args:
            case: Transient case label, as in :meth:`transient_cases`.

        Returns:
            ``(times, base_shear, displacements)`` — ``times`` and ``base_shear``
            are the 1-D ``N_t`` histories (``None`` when absent);
            ``displacements`` maps each recorded node id to its ``(N_t, 3)``
            history.  The dict is empty when *case* carries no displacement
            history — a normal state, not an error.
        """
        arrays = self.transient_arrays_for(case)
        times = arrays.get("time")
        base_shear = arrays.get("base_shear")
        if not any(name in arrays for name in _TRANSIENT_DISP_COMPONENTS):
            return times, base_shear, {}

        geometry = self.display_geometry()
        node_ids = _texts(geometry.get("node_sap_id"))
        node_tags = _ints(geometry.get("node_tag"))
        id_of_tag = {
            str(tag): _at(node_ids, index) or str(tag) for index, tag in enumerate(node_tags)
        }

        recorded_tags = _ints(arrays.get("node_tag"))
        columns = []
        for name in _TRANSIENT_DISP_COMPONENTS:
            values = arrays.get(name)
            if values is None:
                columns.append(None)
                continue
            column = np.asarray(values, dtype=float)
            columns.append(column[:, None] if column.ndim == 1 else column)
        n_t = max((column.shape[0] for column in columns if column is not None), default=0)

        displacements: dict = {}
        for column_index, tag in enumerate(recorded_tags):
            node_id = id_of_tag.get(str(tag), str(tag))
            history = np.zeros((n_t, 3), dtype=float)
            for axis, column in enumerate(columns):
                if column is not None and column.shape[1] > column_index:
                    history[: column.shape[0], axis] = column[:, column_index]
            displacements[node_id] = history
        return times, base_shear, displacements

    def has_forces(self, case: str) -> bool:
        """Whether *case* carries frame end forces — the cheap pre-check.

        The counterpart of :meth:`has_displacements` for force diagrams: force
        recording is optional too, so a caller deciding whether to *offer* the
        overlay asks here rather than building the dict.

        Args:
            case: Static load-case label, as in :meth:`cases`.

        Returns:
            ``True`` when any end-force array is present for *case* — the bare
            schema payload, or the optional explicit local aliases.
        """
        arrays = self.arrays_for(case)
        present = (*STATIC_FORCE_ARRAYS, *STATIC_LOCAL_FORCE_ARRAYS)
        return any(name in arrays for name in present)

    def element_forces(self, case: str) -> dict:
        """``{elem_id: {component_key: value}}`` for *case* — a flag diagram's input.

        Concrete for the same reason as :meth:`nodal_displacements`: assembled
        from :meth:`display_geometry` and :meth:`arrays_for`, so every backend
        has it.  The ids are the ones :meth:`as_model` names its frame elements
        with, which is what the viewers are keyed by.

        The per-case arrays are written **in geometry order** — index ``i`` of
        ``static/<case>/mz_i`` belongs to frame ``i`` of ``frame_sap_id`` — so
        the arrays are read index-wise, never joined on a tag.  That is the
        same invariant :func:`~fea_toolkit.plotting.viz_forces._extract_npz_frame_forces`
        relies on, so the two agree by construction rather than by luck.

        Keys are the schema's own spelling (``"mz_i"`` / ``"mz_j"``, lower
        case).  Each component is *also* exposed under
        ``"{component}_{end}_local"`` when its values are element-local, which
        is what tells :meth:`~fea_toolkit.plotting.viewer.ModelViewer.overlay_forces`
        (with ``use_local=True``) that it can draw them verbatim instead of
        rotating already-local values a second time.  Either the archive's
        ``forces_coordinate_system`` metadata says local — and then the bare
        arrays *are* the local values, so the aliases mirror them, exactly as
        the plotting readers do — or the archive wrote the optional explicit
        ``*_local`` arrays, and those are read.

        Args:
            case: Static load-case label, as in :meth:`cases`.

        Returns:
            ``{elem_id: {key: float}}`` in model units, one entry per element
            the archive covers with at least one component.  Empty when the
            archive recorded no forces for *case* — a normal state, not an
            error, so callers report it rather than raising.
        """
        arrays = self.arrays_for(case)
        if not any(name in arrays for name in (*STATIC_FORCE_ARRAYS, *STATIC_LOCAL_FORCE_ARRAYS)):
            return {}

        geometry = self.display_geometry()
        frame_ids = _texts(geometry.get("frame_sap_id"))
        frame_tags = _ints(geometry.get("frame_eid"))
        is_local = self.forces_are_local()

        forces: dict = {}
        for index in range(_array_length(geometry, "frame_node_i")):
            elem_id = _at(frame_ids, index) or str(_at(frame_tags, index, index + 1))
            entry: dict = {}
            for component in _FORCE_COMPONENTS:
                for end in ("i", "j"):
                    key = f"{component}_{end}"
                    local_key = f"{key}_local"
                    value = _at_float(arrays.get(key), index)
                    if value is not None:
                        entry[key] = value
                    # A local archive's bare arrays *are* the local values, so
                    # the alias mirrors them; otherwise read the producer's own
                    # explicit alias array — and read it independently of the
                    # bare one, since an archive may carry only the aliases.
                    alias = value if is_local else _at_float(arrays.get(local_key), index)
                    if alias is not None:
                        entry[local_key] = alias
            if entry:
                forces[elem_id] = entry
        return forces

    def forces_are_local(self) -> bool:
        """Whether this archive declares its end forces element-local.

        The flag a force-diagram caller needs to read before choosing how to
        consume :meth:`element_forces`: an overlay that draws *local* component
        values passes ``use_local=True`` (then it reads these entries' ``*_local``
        keys), while a legacy archive whose bare arrays are global has to be
        read the other way.

        ``forces_coordinate_system`` is file-level, and ``"local"`` for
        everything this toolkit writes — the recorders use OpenSees
        ``localForces``.  An archive too old to carry the flag reads as *not*
        local, the conservative answer: the values stay under their bare keys
        and a consumer that needs local values has to rotate them itself.
        """
        value = self.metadata("forces_coordinate_system")
        if value is None:
            return False
        try:
            return str(np.asarray(value).ravel()[0]) == "local"
        except (IndexError, TypeError, ValueError):
            return False

    def has_shell_forces(self, case: str) -> bool:
        """Whether *case* carries shell (area) resultants — the cheap pre-check.

        The counterpart of :meth:`has_forces` for shell displays: shell force
        recording is optional (only models whose areas are created as shell
        elements produce them), so a caller deciding whether to *offer* the
        overlay asks here rather than building the dict.

        Args:
            case: Static load-case label, as in :meth:`cases`.

        Returns:
            ``True`` when any per-case shell resultant array is present for
            *case*.
        """
        arrays = self.arrays_for(case)
        return any(name in arrays for name in STATIC_SHELL_COMPONENT_ARRAYS)

    def shell_forces(self, case: str) -> dict:
        """``{shell_id: {component: value}}`` for *case* — a shell overlay's input.

        Reads the per-case ``shell_*`` block and re-keys it by ``shell_sap_id``,
        the same shape :meth:`element_forces` gives frame consumers.  The
        component keys are ``fx``/``fy``/``fxy`` (membrane) and
        ``mx``/``my``/``mxy`` (bending), matching
        :meth:`~fea_toolkit.opensees._runner_static.AnalysisBuilder.extract_static_shell_forces`.

        Args:
            case: Static load-case label, as in :meth:`cases`.

        Returns:
            ``{shell_id: {component: float}}`` in model units, one entry per
            shell the archive covers with at least one component.  Empty when
            the archive recorded no shell forces for *case* — a normal state,
            not an error, so callers report it rather than raising.
        """
        arrays = self.arrays_for(case)
        if not any(name in arrays for name in STATIC_SHELL_COMPONENT_ARRAYS):
            return {}

        shell_ids = _texts(arrays.get("shell_sap_id"))
        forces: dict = {}
        for index, shell_id in enumerate(shell_ids):
            entry: dict = {}
            for component in _SHELL_COMPONENTS:
                value = _at_float(arrays.get(f"shell_{component}"), index)
                if value is not None:
                    entry[component] = value
            if entry:
                forces[shell_id] = entry
        return forces

    @abstractmethod
    def table(self, *columns: str) -> dict:
        """Named columns, for callers that want analytics rather than a case.

        Raises:
            KeyError: If a requested column is not in the archive.
        """

    @abstractmethod
    def to_arrays(self) -> dict:
        """The archive's full ``{name: ndarray}`` bundle, for writing it back.

        The one legitimate reason to want the whole dict is to *write* it
        (:meth:`write`) or to merge several archives into one.  Readers that
        need a slice of it should keep using the named readers above
        (:meth:`arrays_for`, :meth:`display_geometry`, :meth:`table`), which a
        non-NPZ backend can also serve.
        """

    def write(self, path: str, *, name: Optional[str] = None) -> str:
        """Serialise this repository's arrays to *path*.

        The write half of the seam: hands the bundle to the shared NPZ writer,
        so no caller reaches into :meth:`to_arrays` just to save it.  A future
        HDF5 backend overrides this to write a named group instead of a flat
        archive.

        Args:
            path: Output file path (``.npz``).
            name: Reserved for the HDF5 backend — the named group to write
                within the file.  The NPZ backend ignores it (one archive per
                file).

        Returns:
            The absolute path written.
        """
        from .npz_writer import save_results_arrays

        return save_results_arrays(path, self.to_arrays())


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

    def transient_cases(self) -> list:
        """Transient case labels, sorted — the schema's own discovery order."""
        return transient_case_names(self._data)

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

    def transient_arrays_for(self, case: str) -> dict:
        """The arrays under ``transient/<case>/``, keyed without that prefix."""
        prefix = f"transient/{case}/"
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

    def metadata(self, name: str, default: Any = None) -> Any:
        """One file-level array, e.g. ``"forces_coordinate_system"``.

        Reads the archive's **root**, not the case-namespaced arrays that
        :meth:`arrays_for` serves.
        """
        value = self._data.get(name)
        return default if value is None else value

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

    def to_arrays(self) -> dict:
        """The underlying ``{name: array}`` dict this archive was read into."""
        return self._data

    def __len__(self) -> int:
        return len(self._data)

    def get(self, name: str, default: Optional[np.ndarray] = None) -> Any:
        """One array by name, or *default* when absent."""
        value = self._data.get(name)
        return default if value is None else value
