# Project bundle — saving and reloading a GUI session

`File ▸ Save project…` writes the complete desktop session — model, recipe,
results, figures, the ordered view list and the viewport — to a single
**`.zip` file** (a zip archive named `<name>.zip`).  `File ▸ Open project…`
reads it back with the native file picker and rebuilds the session **without
re-running any analysis**.

The bundle members are all canonical formats, so each is independently usable.
`model.h5` (written with HDF5 gzip) and `results.npz` are already compressed, so
the archive *stores* those members and only deflates the JSON text members.  The
loader (`read_project` / `open_project_path`) also accepts a legacy `<name>.fea`
*directory* when handed a path directly — `zip_project` converts one.

## Contents

The `.zip` archive holds these members at its root:

```
project.json    the manifest (recipe, views, active view, viewport, display)
model.h5        the model stages — sap + mesh (write_model_file; .npz when h5py absent)
results.npz     static + modal + response-spectrum arrays (the unified schema)
figures.json    pushover ADRS figure data (adrs / pp / spectrum per direction)
tables.json     table-view payloads (modal, response-spectrum)
```

The implementation lives in `fea_toolkit/gui/project.py` (Qt-free and
OpenSees-free), wired into the window through `MainWindow._on_save_project` /
`open_project_path`.

## JSON schemas

### `project.json`

```jsonc
{
  "schema_version": 1,
  "project": {
    "name": "Admin_Building",
    "source_label": "Admin_0.7E_short term.s2k",
    "model_file": "model.h5",
    "results_file": "results.npz",
    "figures_file": "figures.json",
    "tables_file": "tables.json",
    "units": {"F": "N", "L": "m", "T": "C"},
    "recipe": { "steps": [ /* Recipe.to_dict() */ ], "name": "…" }
  },
  "views": [
    { "key": "meshed", "name": "Meshed", "kind": "geometry", "source": "…", "order": 0 },
    { "key": "results:DEAD", "name": "DEAD", "kind": "results", "source": "…", "case": "DEAD", "order": 1 },
    { "key": "table:modal", "name": "Modal", "kind": "table", "source": "…", "table_key": "table:modal", "order": 2 },
    { "key": "figure:pushover-plusx", "name": "Pushover — +X", "kind": "figure", "source": "…", "figure_key": "figure:pushover-plusx", "order": 3 }
  ],
  "active_view": "figure:pushover-plusx",
  "viewport": { "camera_position": [[…],[…],[…]], "hidden_elem_ids": ["A-12"] },
  "display": {
    "shell_opacity": 0.6, "shrink": 0.0,
    "show_nodes": true, "show_frames": true, "show_shells": true, "show_restraints": true,
    "…": "…"
  }
}
```

`views[].kind` ∈ `geometry | results | table | figure`.  `order` preserves the
Model Tree order; `case` / `table_key` / `figure_key` point a view at its
payload.  Derived views (with `parent` + `selection`) are out of scope for v1.

On load the **initial view is a geometry view** — `meshed`/`processed` first,
then `unprocessed` — even when `active_view` names a results view: reopening
straight into a mode shape would hide the model's supports, groups and loads.

### `figures.json`

```jsonc
{
  "schema_version": 1,
  "figures": {
    "figure:pushover-plusx": {
      "kind": "capacity_spectrum",
      "title": "Pushover — +X",
      "adrs": { "S_a": […], "S_d": […] },
      "pp": { "S_dp": …, "S_ap": …, "S_dy": …, "S_ay": …, "V_base": …, "mu": … },
      "spectrum": { "T": […], "Sa": […] }
    }
  }
}
```

The `figure_key` equals the figure view's registry key, so each pushover
direction reopens under the same key it was registered with.  On load the
figure is **regenerated** from `adrs` / `pp` / `spectrum` via
`plot_capacity_spectrum` (interactive, not a PNG snapshot).

### `tables.json`

```jsonc
{
  "schema_version": 1,
  "tables": {
    "table:modal": { "title": "Modal", "columns": ["Mode", "Period (s)", …], "rows": [["1", "0.4640", …]] }
  }
}
```

## What a save collects

`collect_project_results(run.results)` maps the recipe run's `StepResult`s to
serializable payloads:

| kind | payload | written to |
|---|---|---|
| `cases` | assembled archive arrays | `results.npz` |
| `modal` | `AnalysisResult.data` (`modal` + `shapes`) | `results.npz` (`collect_modal_arrays` + `mode_shape_arrays`) |
| `response_spectrum` | `AnalysisResult.data` (`rs_result` + `direction` + `node_displacements`) | `results.npz` (`collect_rs_arrays` + `rs_displacement_arrays`) + `tables.json` |
| `pushover` | `AnalysisResult.data` per direction (`adrs`/`pp`/`spectrum`) | `figures.json` |

The response-spectrum results are walked from `run.results` (not the context
slot) because the context's `results["rs"]` is overwritten per direction.

RS displacement fields and modal mode shapes are persisted as **static-shaped
cases** (`static/RS-<direction>/node_*`, `static/Mode-<i>/node_*`), so each
direction's / mode's deformed-shape view round-trips through the bundle exactly
like a static load case (Option C).

## Related fixes

- `_recipe_finished` now dispatches the `RS` kind (`_show_rs_result` registers a
  one-row base-shear/moment table **and** a `results` view per direction from the
  CQC `node_displacements`), so response-spectrum results are visible and
  savable — they previously were logged and dropped.
- `_show_pushover_result` and the loader share `figure_identity` (one spelling
  for the figure view key/name).
- Table and figure views (modal/RS tables, pushover ADRS figures) are now added
  to the Model Tree when a recipe registers them — `show_model` repopulates the
  tree only when it draws geometry, so `_show_view` rebuilds it explicitly for
  table/figure views via `_rebuild_tree`.

## What a load restores

`open_project_path` rebuilds the whole session from the manifest without
re-running anything:

- the model (`sap` + `mesh` stages) and the recipe panel
- every view, in order — geometry, results, tables, and the regenerated ADRS
  figures — plus the active view
- the camera, the hidden-element set, **and the display knobs** (the numeric
  sliders, force quantity, restraint style, extruded-frame toggle and detail,
  and the element-visibility toggles — `show_nodes` / `show_frames` /
  `show_shells` / `show_restraints` — are applied through
  `MainWindow._apply_display_values`, validated by `resolve_display` so a stale
  manifest cannot set an out-of-range value)
- the **Model Tree**, which always presents the real model — the mesh when one
  exists, else the raw SAP model — never a results view's display-only geometry;
  and results views draw the model's section colours and support symbols (the
  archive's display model is given `sections` and `restraints` from the source
  model)

