"""Published benchmark models (SAC 3-story, FEMA 355C LA/Seattle/Boston, …).

Importing this package does **not** load OpenSees: the legacy re-exports below
are resolved lazily via :func:`__getattr__`, so the OpenSees-free
:mod:`fea_toolkit.benchmarks.catalogue` (the benchmark library panel's data)
can be imported without paying the ``ops`` import cost.  The heavy submodules
(``sac``, ``sac_run``, ``generic_frame`` …) load OpenSees only when imported
explicitly.  This package is deliberately **not** re-exported from the
top-level ``fea_toolkit`` package — import it explicitly when a benchmark model
is needed (tests, the figure generator, a viewer example).

:mod:`fea_toolkit.benchmarks.fema355c` is itself OpenSees-free (pure
``SAPModelData`` construction + model-codec export — it imports no ``ops``).
"""

import importlib

#: name -> (submodule, attribute) for the legacy re-exports, resolved lazily by
#: :func:`__getattr__` so importing this package stays OpenSees-free.
_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "build_fema355c": ("fema355c", "build_fema355c"),
    "write_snapshots": ("fema355c", "write_snapshots"),
    "fema355c_schedules": ("fema355c", "schedules"),
    "BEAM": ("sac", "BEAM"),
    "BEAM_FY": ("sac", "BEAM_FY"),
    "COLUMN": ("sac", "COLUMN"),
    "COLUMN_FY": ("sac", "COLUMN_FY"),
    "SAC3_TCL": ("sac", "SAC3_TCL"),
    "SAC3_UNITS": ("sac", "SAC3_UNITS"),
    "build_builder": ("sac", "build_builder"),
    "builder_config": ("sac", "builder_config"),
    "enrich_nonlinear": ("sac", "enrich_nonlinear"),
    "load_model": ("sac", "load_model"),
    "peak_x": ("sac", "peak_x"),
    "preprocess_config": ("sac", "preprocess_config"),
    "MAT_TBL_3": ("sac_member_data", "MAT_TBL_3"),
    "MAT_TBL_9": ("sac_member_data", "MAT_TBL_9"),
    "MAT_TBL_20": ("sac_member_data", "MAT_TBL_20"),
    "MAT_TBLS": ("sac_member_data", "MAT_TBLS"),
    "MemberBackbone": ("sac_member_data", "MemberBackbone"),
    "compute_metrics": ("sac_run", "compute_metrics"),
    "floor_levels": ("sac_run", "floor_levels"),
    "manifest_path": ("sac_run", "manifest_path"),
    "run_case": ("sac_run", "run_case"),
    "run_matrix": ("sac_run", "run_matrix"),
    "write_manifest": ("sac_run", "write_manifest"),
}


def __getattr__(name: str):
    """Resolve a legacy re-export on first access (PEP 562).

    Raises ``AttributeError`` for anything else, which lets ``from
    fea_toolkit.benchmarks import <submodule>`` fall through to the normal
    submodule import.
    """
    try:
        module_name, attr = _LAZY_EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    module = importlib.import_module(f".{module_name}", __name__)
    value = getattr(module, attr)
    globals()[name] = value
    return value


__all__ = [
    "BEAM",
    "BEAM_FY",
    "COLUMN",
    "COLUMN_FY",
    "MAT_TBLS",
    "MAT_TBL_3",
    "MAT_TBL_9",
    "MAT_TBL_20",
    "SAC3_TCL",
    "SAC3_UNITS",
    "MemberBackbone",
    "build_builder",
    "build_fema355c",
    "builder_config",
    "compute_metrics",
    "enrich_nonlinear",
    "fema355c_schedules",
    "floor_levels",
    "load_model",
    "manifest_path",
    "peak_x",
    "preprocess_config",
    "run_case",
    "run_matrix",
    "write_manifest",
    "write_snapshots",
]
