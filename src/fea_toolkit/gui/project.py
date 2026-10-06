"""Project bundle — save and reload the complete GUI session to a directory.

Directory-first, with an optional zip wrapper.  A project is a directory holding
five canonical files:

    project.json   the manifest: recipe, ordered view list, active view,
                   viewport (camera + hidden set) and display preferences
    model.<h5|npz> the model stages (SAP + mesh), written by
                   :func:`fea_toolkit.io.stage_writer.write_model_file`
    results.npz    static + modal + response-spectrum arrays (the unified
                   results schema)
    figures.json   pushover ADRS figure data (``adrs`` / ``pp`` / ``spectrum``
                   per direction), regenerated to matplotlib figures on load
    tables.json    table-view payloads (modal, response-spectrum)

The loader accepts either the directory or a ``.zip`` of it.  Everything here is
Qt-free and OpenSees-free, so the module is unit-tested without the ``[gui]``
extra.  Heavy imports (the io writers, matplotlib) live inside the functions
they serve, mirroring the project's lazy-import policy.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional

from ..workflow.steps import CASES, MODAL, PUSHOVER, RS
from .controllers._atomic import atomic_write_text

__all__ = [
    "FIGURES_NAME",
    "MANIFEST_NAME",
    "PROJECT_SCHEMA_VERSION",
    "RESULTS_NAME",
    "TABLES_NAME",
    "build_manifest",
    "collect_project_results",
    "detect_model_format",
    "figure_identity",
    "figures_payload",
    "is_project",
    "read_project",
    "regenerate_figure",
    "tables_payload",
    "views_payload",
    "write_project",
    "zip_project",
]

#: Version of the ``project.json`` manifest layout.
PROJECT_SCHEMA_VERSION = 1

MANIFEST_NAME = "project.json"
RESULTS_NAME = "results.npz"
FIGURES_NAME = "figures.json"
TABLES_NAME = "tables.json"


def detect_model_format() -> str:
    """Return the stage-file format to use for the project's model stage.

    ``h5py`` is an optional dependency (lazy import), so a project written where
    it is absent falls back to ``npz`` — the stage-file reader understands both.

    Returns:
        ``"h5"`` when ``h5py`` is importable, else ``"npz"``.
    """
    try:
        import h5py  # noqa: F401
    except ImportError:
        return "npz"
    return "h5"


def figure_identity(label: str, direction: str) -> tuple[str, str]:
    """Return ``(key, name)`` for a pushover direction's ADRS figure view.

    One spelling shared by the figure-view producer and the project loader, so a
    saved figure reopens under the same key it was registered with.

    Args:
        label: The pushover result label, e.g. ``"Pushover"``.
        direction: The push direction label, e.g. ``"+X"``.

    Returns:
        The stable registry key and the display name.
    """
    suffix = direction.replace("+", "plus").replace("-", "minus").lower()
    key = f"figure:{label.lower().replace(' ', '-')}-{suffix}"
    name = f"{label} \u2014 {direction}"
    return key, name


# ── JSON helpers ─────────────────────────────────────────────────────


def _json_safe(value: Any) -> Any:
    """Recursively convert a value to JSON-native Python objects.

    Analysis payloads carry NumPy scalars and arrays (e.g. the performance-point
    dict and the ADRS curves), which :func:`json.dumps` cannot serialise.
    """
    import numpy as np

    if isinstance(value, dict):
        return {str(key): _json_safe(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(val) for val in value]
    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, payload: Any) -> None:
    text = json.dumps(_json_safe(payload), indent=2, sort_keys=True) + "\n"
    atomic_write_text(path, text)


# ── Payload mapping (recipe results → serializable data) ─────────────


def collect_project_results(results: list) -> dict:
    """Map a recipe run's :class:`~fea_toolkit.workflow.steps.StepResult` list
    to the serializable analysis payloads.

    The recipe stores the response-spectrum result on the context per direction
    (overwritten), but the run's *results* list keeps both — so this walks the
    list rather than a single context slot.

    Args:
        results: ``RecipeRun.results`` — one ``StepResult`` per produced result.

    Returns:
        A dict with keys ``static_arrays`` (the merged ``CASES`` archive arrays),
        ``modal`` / ``mode_shapes`` (from the ``MODAL`` payload), ``rs`` (``rs_x``
        / ``rs_y``), and ``pushover`` (``{direction: entry}``).
    """
    static_arrays: dict = {}
    modal: Optional[dict] = None
    mode_shapes: Optional[dict] = None
    rs: dict = {}
    pushover: dict = {}

    for result in results:
        kind = getattr(result, "kind", None)
        payload = getattr(result, "payload", None)
        if kind == CASES:
            if isinstance(payload, dict):
                static_arrays.update(payload)
        elif kind == MODAL:
            data = getattr(payload, "data", None)
            if isinstance(data, dict):
                modal = data["modal"] if isinstance(data.get("modal"), dict) else data
                mode_shapes = data.get("shapes")
        elif kind == RS:
            data = getattr(payload, "data", None)
            if isinstance(data, dict):
                direction = str(data.get("direction", "X")).upper()
                rs_result = data.get("rs_result")
                if isinstance(rs_result, dict):
                    rs[f"rs_{direction.lower()}"] = rs_result
        elif kind == PUSHOVER:
            data = getattr(payload, "data", None)
            if isinstance(data, dict):
                for direction, entry in data.items():
                    if isinstance(entry, dict) and "adrs" in entry:
                        pushover[str(direction)] = entry

    return {
        "static_arrays": static_arrays,
        "modal": modal,
        "mode_shapes": mode_shapes,
        "rs": rs,
        "pushover": pushover,
    }


def figures_payload(pushover: dict, label: str = "Pushover") -> dict:
    """Build the ``figures.json`` payload from pushover per-direction data.

    Each entry carries the three objects the ADRS figure needs — the capacity
    curve in ADRS form (``adrs``), the performance point (``pp``) and the demand
    spectrum (``spectrum``) — so :func:`regenerate_figure` can rebuild the
    identical figure on load.

    Args:
        pushover: ``{direction: {"adrs": …, "pp": …, "spectrum": …}}``.
        label: The pushover result label used to name the figures.

    Returns:
        ``{figure_key: {kind, title, adrs, pp, spectrum}}``.
    """
    figures: dict = {}
    for direction, entry in pushover.items():
        key, name = figure_identity(label, direction)
        figures[key] = {
            "kind": "capacity_spectrum",
            "title": name,
            "adrs": entry.get("adrs") or {"S_a": [], "S_d": []},
            "pp": entry.get("pp") or {},
            "spectrum": entry.get("spectrum") or {"T": [], "Sa": []},
        }
    return figures


def tables_payload(tables: dict) -> dict:
    """Build the ``tables.json`` payload from ``{key: Table}``.

    A :class:`~fea_toolkit.workflow.steps.Table` is ``title`` + ``columns`` +
    ``rows`` (all pre-formatted strings), which round-trips through JSON as-is.

    Args:
        tables: ``{table_key: Table}`` from the view registry.

    Returns:
        ``{table_key: {"title": …, "columns": […], "rows": [[…]]}}``.
    """
    payload: dict = {}
    for key, table in tables.items():
        payload[key] = {
            "title": getattr(table, "title", key),
            "columns": list(getattr(table, "columns", ())),
            "rows": [list(row) for row in getattr(table, "rows", ())],
        }
    return payload


def views_payload(views: list) -> list:
    """Build the manifest ``views`` array from the registry's views.

    Each entry carries the display fields plus the pointer a loader needs to
    recover the payload: a ``case`` for results views (recovered from the key
    suffix), a ``table_key`` for table views and a ``figure_key`` for figure
    views.  ``order`` preserves registration order so a reload lists them in the
    order they appeared.

    Args:
        views: ``ViewRegistry.views()`` — the views in registration order.

    Returns:
        A list of JSON-native view descriptors.
    """
    out: list = []
    for order, view in enumerate(views):
        entry: dict = {
            "key": view.key,
            "name": view.name,
            "kind": view.kind,
            "source": view.source,
            "order": order,
        }
        if view.kind == "results":
            entry["case"] = view.key.split(":", 1)[1] if ":" in view.key else view.key
        elif view.kind == "table":
            entry["table_key"] = view.key
        elif view.kind == "figure":
            entry["figure_key"] = view.key
        out.append(entry)
    return out


# ── Manifest ─────────────────────────────────────────────────────────


def build_manifest(
    *,
    name: str,
    source_label: str,
    model_file: str,
    units: dict,
    recipe: Any,
    views: list,
    active_view: Optional[str],
    camera: Optional[list],
    hidden_elem_ids: list,
    display: dict,
) -> dict:
    """Assemble the ``project.json`` manifest payload.

    Args:
        name: Project display name.
        source_label: Original model path (informational only).
        model_file: The model stage file name, e.g. ``"model.h5"``.
        units: The model's ``{"F", "L", "T"}`` unit system.
        recipe: The :class:`~fea_toolkit.workflow.recipe.Recipe`, serialised via
            its ``to_dict()`` form.
        views: The registry's views (passed to :func:`views_payload`).
        active_view: The displayed view's key, or ``None``.
        camera: The camera position, or ``None``.
        hidden_elem_ids: The hidden-element ids.
        display: The display preferences.

    Returns:
        The JSON-native manifest dict.
    """
    recipe_dict = None
    if recipe is not None:
        recipe_dict = getattr(recipe, "to_dict", lambda: None)()

    return {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "project": {
            "name": name,
            "source_label": source_label,
            "model_file": model_file,
            "results_file": RESULTS_NAME,
            "figures_file": FIGURES_NAME,
            "tables_file": TABLES_NAME,
            "units": units or {},
            "recipe": recipe_dict,
        },
        "views": views_payload(views),
        "active_view": active_view,
        "viewport": {
            "camera_position": camera,
            "hidden_elem_ids": hidden_elem_ids or [],
        },
        "display": display or {},
    }


# ── Write / read ─────────────────────────────────────────────────────


def write_project(
    bundle_dir: str | Path,
    *,
    manifest: dict,
    sap: Any,
    mesh: Any,
    results_arrays: dict,
    figures: dict,
    tables: dict,
    model_fmt: Optional[str] = None,
) -> list[str]:
    """Write a project directory, returning any non-fatal notes.

    Each member is a canonical format, so the write is a thin composition of the
    existing io writers plus three JSON files.  A member that cannot be written
    (e.g. a missing model) is reported in the returned notes rather than raised,
    so a project save never loses the pieces it *can* write.

    Args:
        bundle_dir: The project directory to create.
        manifest: The :func:`build_manifest` payload (its ``model_file`` is
            rewritten to match the chosen *model_fmt*).
        sap: The ``SAPModelData`` (the ``sap`` stage).
        mesh: The preprocessed ``MeshModel``, or ``None``.
        results_arrays: The merged results archive arrays (static + modal + RS).
        figures: The :func:`figures_payload` payload.
        tables: The :func:`tables_payload` payload.
        model_fmt: ``"h5"`` or ``"npz"``; ``None`` auto-detects.

    Returns:
        Notes to log; ``[]`` when everything wrote silently.
    """
    notes: list = []
    fmt = model_fmt or detect_model_format()
    model_file = f"model.{fmt}"
    manifest.setdefault("project", {})["model_file"] = model_file

    directory = Path(bundle_dir)
    directory.mkdir(parents=True, exist_ok=True)

    if sap is not None or mesh is not None:
        from ..io.stage_writer import write_model_file

        try:
            write_model_file(str(directory / model_file), sap=sap, mesh=mesh, fmt=fmt)
        except Exception as exc:  # pragma: no cover - filesystem / h5py failure
            notes.append(f"could not write model stage: {exc}")

    if results_arrays:
        from ..io.npz_writer import save_results_arrays

        try:
            save_results_arrays(str(directory / RESULTS_NAME), results_arrays)
        except Exception as exc:  # pragma: no cover - filesystem failure
            notes.append(f"could not write results: {exc}")

    try:
        _write_json(directory / FIGURES_NAME, {"schema_version": 1, "figures": figures})
    except OSError as exc:  # pragma: no cover
        notes.append(f"could not write figures: {exc}")

    try:
        _write_json(directory / TABLES_NAME, {"schema_version": 1, "tables": tables})
    except OSError as exc:  # pragma: no cover
        notes.append(f"could not write tables: {exc}")

    try:
        _write_json(directory / MANIFEST_NAME, manifest)
    except OSError as exc:  # pragma: no cover
        notes.append(f"could not write manifest: {exc}")

    return notes


def _read_dir(bundle_dir: Path) -> dict:
    manifest = _load_json(bundle_dir / MANIFEST_NAME)
    project = manifest.get("project") or {}

    sap = mesh = None
    model_file = project.get("model_file") or "model.h5"
    model_path = bundle_dir / model_file
    if model_path.is_file():
        from ..io.stage_reader import read_model_stages

        with contextlib.suppress(ValueError):
            sap = read_model_stages(str(model_path), "sap")
        with contextlib.suppress(ValueError):
            mesh = read_model_stages(str(model_path), "mesh")

    results = None
    results_path = bundle_dir / (project.get("results_file") or RESULTS_NAME)
    if results_path.is_file():
        from ..io.results_repository import NpzResultsRepository

        with contextlib.suppress(Exception):
            results = NpzResultsRepository(str(results_path))

    tables: dict = {}
    tables_path = bundle_dir / (project.get("tables_file") or TABLES_NAME)
    if tables_path.is_file():
        from ..workflow.steps import Table

        data = _load_json(tables_path)
        raw = data.get("tables") if isinstance(data, dict) else {}
        if isinstance(raw, dict):
            for key, value in raw.items():
                tables[key] = Table(
                    title=value.get("title", key),
                    columns=tuple(value.get("columns", [])),
                    rows=tuple(tuple(row) for row in value.get("rows", [])),
                )

    figures: dict = {}
    figures_path = bundle_dir / (project.get("figures_file") or FIGURES_NAME)
    if figures_path.is_file():
        data = _load_json(figures_path)
        raw = data.get("figures") if isinstance(data, dict) else {}
        if isinstance(raw, dict):
            figures = raw

    return {
        "manifest": manifest,
        "sap": sap,
        "mesh": mesh,
        "results": results,
        "tables": tables,
        "figures": figures,
    }


def is_project(path: str | Path) -> bool:
    """Whether *path* looks like a project (a directory, or a zip of one).

    Args:
        path: A directory or ``.zip`` / ``.fea`` path.

    Returns:
        ``True`` when the directory contains a manifest, or the path has a zip
        suffix.
    """
    path = Path(path)
    if path.is_dir():
        return (path / MANIFEST_NAME).is_file()
    return path.suffix.lower() in (".zip", ".fea")


def read_project(path: str | Path) -> dict:
    """Read a project directory (or a zip of one) back into memory.

    Args:
        path: The project directory, or a ``.zip`` / ``.fea`` of it.

    Returns:
        A dict with ``manifest``, ``sap``, ``mesh``, ``results`` (an
        :class:`~fea_toolkit.io.results_repository.NpzResultsRepository` or
        ``None``), ``tables`` (``{key: Table}``) and ``figures``
        (``{key: figure data}``).

    Raises:
        ValueError: If *path* is neither a project directory nor a zip.
    """
    path = Path(path)
    if path.is_dir():
        return _read_dir(path)
    if path.suffix.lower() in (".zip", ".fea"):
        tmp = tempfile.mkdtemp(prefix="fea_project_")
        try:
            shutil.unpack_archive(str(path), tmp)
            return _read_dir(Path(tmp))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    raise ValueError(f"{path} is not a project directory or zip")


def zip_project(bundle_dir: str | Path, zip_path: Optional[str | Path] = None) -> str:
    """Archive a project directory as a single ``.fea`` zip.

    The directory is the canonical form; this is a transport wrapper the loader
    (:func:`read_project`) understands unchanged.

    Args:
        bundle_dir: The project directory.
        zip_path: The output path (defaults to ``<bundle_dir>.fea``).

    Returns:
        The written archive path.
    """
    bundle_dir = Path(bundle_dir)
    if not bundle_dir.is_dir():
        raise ValueError(f"{bundle_dir} is not a directory")
    if zip_path is None:
        zip_path = bundle_dir.with_suffix(".fea")
    base = str(Path(zip_path))
    if base.lower().endswith(".fea") or base.lower().endswith(".zip"):
        base = base[: -len(Path(base).suffix)]
    # Archive the *contents* of the directory at the archive root, so a reader
    # (and :func:`read_project`) sees ``project.json`` etc. without a folder
    # prefix.
    return shutil.make_archive(base, "zip", root_dir=bundle_dir)


# ── Figure regeneration ──────────────────────────────────────────────


def regenerate_figure(figure_data: dict) -> Any:
    """Rebuild a matplotlib figure from its serialized payload.

    Only the ``capacity_spectrum`` kind is understood today (the pushover ADRS
    figures); an unknown or empty payload yields ``None`` so a loader skips it
    rather than crashing.

    Args:
        figure_data: One ``figures.json`` entry.

    Returns:
        A ``matplotlib.figure.Figure``, or ``None``.
    """
    if figure_data.get("kind") != "capacity_spectrum":
        return None
    from ...plotting.viz_pushover import plot_capacity_spectrum

    spectrum = figure_data.get("spectrum") or {}
    return plot_capacity_spectrum(
        figure_data.get("adrs") or {},
        spectrum.get("T") or [],
        spectrum.get("Sa") or [],
        performance_point=figure_data.get("pp") or {},
        title=figure_data.get("title"),
    )
