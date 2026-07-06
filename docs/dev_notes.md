# fea_toolkit development notes



## CSM (Capacity Spectrum Method)
- `pushover_to_adrs()`: Converts pushover curve to ADRS format.
  - Uses best_mode (max participation in push direction) from modal_props.
  - Gamma = sqrt(M_eff) because extract_mode_shapes returns mass-normalized eigenvectors.
  - Uses abs() on base_shear/control_disp since OpenSees sign convention may give negatives.
- `compute_performance_point()`: Secant-iteration CSM per ATC-40.
  - Falls back to elastic spectral response when iteration drops below first data point.
  - Uses `np.trapezoid` (renamed from `np.trapz` in NumPy 2.0; `np.trapz` is deprecated and emits a warning).
- `plot_capacity_spectrum()` in viz.py for ADRS visualisation.

## Key patterns
- MassSource uses `elements`, `masses`, `loads` kwargs (not `from_element` etc).
- `modalProperties -unorm` returns partiFactorMX as 1.0 for single-DOF, but eigenvectors from nodeEigenvector are mass-normalized.
- Gamma computation: Γ = √M_eff for mass-normalized eigenvectors.

## Brace modelling
### Approach A (subdivided braces) — convergence failure
- Subdivided dispBeamColumn + PDelta/Corotational fails at gravity even with no imperfection
- 100 sub-steps, NormUnbalance, KrylovNewton — still fails
- Root cause: shared-node connectivity with subdivided elements creates ill-conditioned system matrix
- Bugs fixed: E/A swap in rigid link section, node creation on rebuild (_created_node_tags tracking), split_elements conflict

### Approach B (truss + Hysteretic) — recommended for static
- Working for static pushover with directional asymmetry
- For dynamic: upgrade material (Steel02 + Fatigue or BraceMaterial)

### OpenSees CBF brace modelling (from Workshops/OpenSeesDays/Steel2dModels)
- HSSbrace proc: subdivided forceBeamColumn/dispBeamColumn, fiber sections, Corotational, L/1000 imperfection
- Steel02 + Fatigue wrapper for cyclic analysis
- CBF1.tcl..CBF4.tcl: diagonal, X-brace, V-brace, inverted-V configurations

## Key bugs found & fixed in builder.py
1. E/A swapped in rigid link `ops.section('Elastic', ...)` — fixed
2. Node creation on rebuild: `ops.nodeCoord` doesn't raise in OpenSeesPy — fixed with `_created_node_tags`
3. Frame self-weight: `_create_loads` used `self.model.frame_assignments.get(eid)` when iterating `self.split_elements`. Child elements from splitting are tracked in `self.split_assignments`, not the original dict. Fixed by using `self.split_assignments.get(eid)` when `self.split_elements` is active. Symptom: 189 child elements (3510 kN) silently excluded from self-weight.

## Area import from JSON
- `SAP2000Parser.from_json()` loads SAP2000 JSON exports (same table structure as .S2K)
- `_get_area_elements()` now consolidates multi-row area connectivity (old SAP2000 format where one area's joints span multiple rows). Duplicate joint IDs are avoided.
- `_create_shell_elements()` in builder creates ShellMITC4 elements for areas not in the loads-only selection. Uses `ElasticMembranePlate` section with material properties.
- Config `create_shells=True` enables shell creation. Areas matching `selection` remain loads-only; all others become shells.

## Rhino module (`src/fea_toolkit/rhino/`)
- 6 files: `__init__.py`, `colors.py`, `layers.py`, `geometry.py`, `groups.py`, `importer.py`
- All Rhino API calls are lazy-imported — raises `RuntimeError` outside Rhino
- **Layer hierarchy**: `SAP2000/Joints`, `SAP2000/Frames/{Centreline,Extrusion}/{Section}`, `SAP2000/Shells/{Centreline,Extrusion}/{Section}`
- **Centreline**: points (joints), lines (frames), planar Breps (shells)
- **Extrusion**: lightweight `Extrusion` objects — section profiles (I/Box/Pipe/Channel/Rect/Circular) for frames, thickness offset for shells
- Objects added via `doc.Objects.AddExtrusion()` to stay lightweight
- **Metadata**: all objects carry `SAP_*` UserStrings for Grasshopper
- **Groups**: SAP2000 groups → Rhino groups; `SAP_All_Frames/Shells/Joints` via doc scan
- **Joint colour-coding**: Red (fixed), Blue (pinned), Green (roller), LightGray (free), Purple (constrained)
- **Usage**: `RhinoImporter(md).run(create_centreline=True, create_extrusions=True)`
- **Tests**: 23 tests in `tests/test_rhino.py` — colour conversion, layer name sanitisation, RuntimeError without Rhino API

## Xara OpenSeesRT (tclsh8.6) Compatibility
- **`nodalLoad`** exists OUTSIDE `pattern` blocks, but not inside — use `load` inside pattern blocks.
- **`pattern`** requires braced body: `pattern Plain $tag $tsTag { load ... }` — RecordingOpenSees flat output doesn't group.
- **`beamIntegration Lobatto`** needs explicit section tags per integration point: `beamIntegration Lobatto $tag 5 $s $s $s $s $s` (not the abbreviated `$tag 5 $s` form).
- **`UmfPack`** segfaults on large models → use `ProfileSPD` instead.
- **Area-only nodes** (not connected to frames) cause singular stiffness → filter them out (29 orphans in Admin building).
- **Query commands** (`nodeCoord`, `getNodeTags`, etc.) produce errors if nodes don't exist → skip in Tcl output.
- **Fiber sections**: `section Fiber`, `uniaxialMaterial Concrete01/Steel02`, `patch`, `layer ALL work`.
- **`ElasticMembranePlateSection`** NOT supported in Xara's OpenSeesRT.
- **Library**: auto-detected by `export_model_to_tcl()`; falls back to `"libOpenSeesRT.dylib"`. Override via `lib_path` argument or set `OPENSEESRT_LIB` environment variable.
- **Docs**: `docs/rhino_export.md` — quick start, layer structure, geometry types, metadata reference, joint colour coding
- **Frame member docs**: Added steel + planned RC section documentation to `docs/pushover_analysis.md`

## NPZ export (export_results_to_npz in builder.py)
- Stores metadata_json (JSON string with created timestamp, model stats, config, has_local_forces flag)
- Stores local force arrays: sub_fx_i_local, sub_fy_i_local, ..., sub_mx_j_local, etc.
- Stores element connectivity: sub_node_i_tag, sub_node_j_tag (OpenSees node tags)
- metadata_json key in NPZ arrays

## Standalone NPZ plotter (plotting/viz.py)
- plot_npz_force_diagram() — 2D matplotlib force vs elevation from NPZ
- plot_npz_moment_3d() — 3D PyVista force/moment diagram from NPZ
- _load_npz_for_plotting() helper — loads NPZ, builds element-centric dict with coordinates and forces
- Both are standalone (no OpenSeesBuilder needed), support local/global forces, flag/tube modes

## NPZ → Rhino colouring (rhino/colour_from_npz.py)
- colour_from_npz() matches SAP_FrameID UserStrings to NPZ sap_ids, colours by force quantity
- colour_frame_by_npz_ratio() colours by ratio of two quantities
- Runs inside Rhino CPython environment

## Shear + axial 3D plots (plotting/viz.py)
- _plot_moment_flags and _plot_moment_tubes generalized for any M* or F* quantity
- plot_static_moment_3d accepts M* (moment) or F* (force) quantities
- plot_static_shear_3d() and plot_static_axial_3d() convenience wrappers
- Force flags use world-perpendicular direction (not local axes)

## PyVista widgets available (viz.py)
- Radio buttons (`add_radio_button_widget`) — mutually exclusive selection, good for switching result quantities (Mz/My/Fx etc.)
- Checkboxes (`add_checkbox_button_widget`) — toggle independent layers (undeformed, force diagram, labels, reactions)
- Sliders (`add_slider_widget`) — continuous params (disp scale, threshold)
- Text slider (`add_text_slider_widget`) — discrete text choices (mode shapes, load cases)
- Plane widget (`add_mesh_clip_plane` / `add_mesh_slice`) — interactive clipping/slicing
- Box widget (`add_mesh_clip_box`) — interactive box crop
- Spline slice (`add_mesh_slice_spline`) — slice along drawn polyline
- Picking: `enable_mesh_picking`, `enable_point_picking`, `enable_cell_picking`, `enable_element_picking`, `enable_surface_point_picking` — click to inspect element/node data
- Sphere widget (`add_sphere_widget`) — draggable control points
- Line widget (`add_line_widget`) — draw seed line (streamlines etc.)
- Measurement (`add_measurement_widget`) — interactive distance tool
- Animation timer (`add_timer_event`) — animate mode shapes, pushover
- Key events (`add_key_event`) — keyboard shortcuts
- Labels: `add_point_labels(..., always_visible=True)` — permanent overlay labels
- Export to interactive HTML: `export_html()`

## Interactive viewer (plotting/interactive_viewer.py)
- `plot_interactive_viewer(builder, combo_forces, combo_results, ...)` — PyVista widget-driven viewer
- Radio buttons for quantity (Mz/My/Mx/Fz/Fy/Fx)
- Text slider for load combo selection
- Checkboxes: Centreline, Labels, Reactions
- Click element → info overlay with elem_tag, SAP ID, section, material
- Click flag → shows numeric value with unit
- Structure built as coloured tubes (section-colour mapped)
- Force flags use RdBu diverging colormap, merged PolyData with `col_val` + `elem_tag` point data
- Caches flag meshes per combo+quantity pair
- Import: `from fea_toolkit.plotting import plot_interactive_viewer`
- Strategy: use radio buttons to switch result quantity, checkboxes for overlay toggles, sliders for continuous params
- Callbacks can swap mesh by name (`add_mesh(new, name='actor')`), toggle visibility, or update coords in-place

## view_admin_model.py — standalone model viewer (local/view_admin_model.py)
- Created for visualising Admin_0.7E_short term.s2k model with Original/Meshed toggle
- **View toggle** (checkbox bottom-left): switches between unsplit wireframe and split+shell views
- **Label toggles** (3 checkboxes above view toggle): show/hide numeric tags for Nodes/Frames/Areas
- **Click-to-identify** (yellow dot at click position, info text at top-centre): intended to identify elements, but unreliable after camera rotation/zoom
- **Picking limitation**: VTK's mesh picking with `enable_mesh_picking` + `find_closest_cell` loses accuracy after orbit/zoom transforms. Custom centroid-distance search improved but still inconsistent. Root cause: VTK interactor `picked_point` doesn't reliably map to correct cell after perspective changes.
- **Shrink factors**: `AREA_SHRINK=0.9`, `FRAME_SHRINK=0.9` — elements shrunk toward centroid/midpoint for visual gaps at joints
- **Node spheres**: original (size 12), meshed (size 10), split nodes orange (size 20)
- **Terrain style**: `enable_terrain_style()` locks Z as vertical during rotation
- **Render order**: shells bottom → frames middle → nodes top
- **Materials coloured**: concrete (blue), brick (red)
- Text positioning with `add_text(..., position=tuple)` uses top-left origin on macOS (not bottom-left as documented)
