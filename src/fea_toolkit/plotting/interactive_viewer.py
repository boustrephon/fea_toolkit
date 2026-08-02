"""Interactive 3D viewer for structural engineering results using PyVista widgets.

Provides a single entry point :func:`plot_interactive_viewer` that opens a
PyVista window with:

* **Radio buttons** to switch between force/moment quantities
  (``Mz``, ``My``, ``Mx``, ``Fx``, ``Fy``, ``Fz``).
* **Text slider** to cycle through load combinations / result sets.
* **Checkboxes** to toggle overlays: undeformed structure, force diagram,
  element labels, reactions.
* **Click on an element** to show a floating overlay with the element's ID,
  SAP2000 label, section name, and material name.
* **Click on a force flag** to show its numeric value.
"""

import contextlib
import math
from typing import TYPE_CHECKING, Any, Optional

import numpy as np

from ..utils import compute_flag_parts
from .viz import _set_isometric_view

if TYPE_CHECKING:
    from ..model.selection import Selection


# ============================================================================
# Internal helpers
# ============================================================================


def _local_end_forces_quick(builder, elem, tag, elem_forces):
    """Transform global end forces to local coordinates (inline helper)."""
    try:
        vx, vy, vz = builder._get_local_axes(elem)
    except Exception:
        return None
    T = np.vstack([vx, vy, vz])
    f = elem_forces.get(tag, {})
    f_i = np.array([f.get("Fx", 0.0), f.get("Fy", 0.0), f.get("Fz", 0.0)])
    m_i = np.array([f.get("Mx", 0.0), f.get("My", 0.0), f.get("Mz", 0.0)])
    f_j = np.array([f.get("Fx_j", 0.0), f.get("Fy_j", 0.0), f.get("Fz_j", 0.0)])
    m_j = np.array([f.get("Mx_j", 0.0), f.get("My_j", 0.0), f.get("Mz_j", 0.0)])
    f_i_loc = T @ f_i
    m_i_loc = T @ m_i
    f_j_loc = T @ f_j
    m_j_loc = T @ m_j
    return {
        "Fx": f_i_loc[0],
        "Fy": f_i_loc[1],
        "Fz": f_i_loc[2],
        "Mx": m_i_loc[0],
        "My": m_i_loc[1],
        "Mz": m_i_loc[2],
        "Fx_j": f_j_loc[0],
        "Fy_j": f_j_loc[1],
        "Fz_j": f_j_loc[2],
        "Mx_j": m_j_loc[0],
        "My_j": m_j_loc[1],
        "Mz_j": m_j_loc[2],
    }


def _build_flag_mesh(
    builder, elements, assignments, elem_forces, quantity, elem_local_axes, model_height
):
    """Build a merged PolyData of all force/moment flags for *quantity*.

    Returns a ``(mesh, max_abs_val)`` tuple, or ``(None, 0.0)`` if no data.
    Each flag polygon has a ``col_val`` scalar and an ``elem_tag`` scalar so
    that picking can report which element was clicked along with the force value.
    """
    import pyvista as pv

    is_moment = quantity.startswith("M")
    if not is_moment and not quantity.startswith("F"):
        return None, 0.0

    max_val = 0.0
    all_polys = []  # list of (verts, col_val, elem_tag)

    for eid, elem in elements.items():
        if getattr(elem, "inactive", False):
            continue
        tag = elem.elem_tag
        if tag not in elem_forces:
            continue
        ni = builder.model.nodes.get(elem.node_i)
        nj = builder.model.nodes.get(elem.node_j)
        if ni is None or nj is None:
            continue
        p_i = np.array([ni.x, ni.y, ni.z])
        p_j = np.array([nj.x, nj.y, nj.z])

        # Local forces
        loc = _local_end_forces_quick(builder, elem, tag, elem_forces)
        if loc is None:
            continue
        v_i = loc.get(quantity, 0.0)
        v_j = loc.get(quantity + "_j", 0.0)

        # Flag offset direction
        vx_e, vy_e, vz_e = elem_local_axes.get(eid, (None, None, None))
        if vx_e is None:
            continue
        if quantity == "Fx":
            vn = np.array(vz_e)
        elif quantity == "Fy":
            vn = np.array(vy_e)
        elif quantity == "Fz":
            vn = np.array(vz_e)
        elif quantity == "Mx":
            vn = np.array(vy_e)
        elif quantity == "My":
            vn = -np.array(vz_e)
        elif quantity == "Mz":
            vn = np.array(vy_e)
        else:
            vn = np.array(vz_e)

        max_val = max(max_val, abs(v_i), abs(v_j))
        all_polys.append((p_i, p_j, vn, v_i, v_j, tag))

    if not all_polys:
        return None, 0.0

    scale = (model_height * 0.2) / max(max_val, 1.0)

    # Build merged PolyData
    # compute_flag_parts can return polygons with varying vertex counts
    # (e.g., quads for flag body, triangles for caps), so we accumulate
    # faces as a flat buffer instead of a 2D array.
    verts_list = []
    faces_flat: list[int] = []
    scalars_list = []
    elem_tag_list = []
    offset = 0

    for p_i, p_j, vn, Fi, Fj, tag in all_polys:
        for verts, col_val in compute_flag_parts(p_i, p_j, vn, Fi, Fj, scale):
            n = len(verts)
            pts_arr = np.array(verts)
            verts_list.append(pts_arr)
            faces_flat.append(n)
            faces_flat.extend(range(offset, offset + n))
            scalars_list.append(col_val)
            elem_tag_list.append(tag)
            offset += n

    if not verts_list:
        return None, 0.0

    vertices = np.vstack(verts_list)
    faces = np.array(faces_flat, dtype=int)
    mesh = pv.PolyData(vertices, faces=faces)
    mesh.point_data["col_val"] = np.repeat(scalars_list, [len(v) for v in verts_list])
    mesh.point_data["elem_tag"] = np.repeat(elem_tag_list, [len(v) for v in verts_list])
    return mesh, max_val


def _build_structure_tubes(builder, elements, assignments, model):
    """Build tube PolyData for all elements (pickable representation)."""
    import pyvista as pv

    all_verts = []
    all_faces = []
    all_elem_tags = []
    all_sec_names = []
    offset = 0

    sec_set = sorted({assignments.get(e, "?") for e in elements})
    cmap = [
        "#4c72b0",
        "#dd8452",
        "#55a868",
        "#c44e52",
        "#8172b3",
        "#937860",
        "#da8bc3",
        "#8c8c8c",
        "#ccb974",
        "#64b5cd",
    ]

    for eid, elem in elements.items():
        if getattr(elem, "inactive", False):
            continue
        ni = model.nodes.get(elem.node_i)
        nj = model.nodes.get(elem.node_j)
        if ni is None or nj is None:
            continue
        p1 = np.array([ni.x, ni.y, ni.z])
        p2 = np.array([nj.x, nj.y, nj.z])
        length = np.linalg.norm(p2 - p1)
        if length < 1e-12:
            continue

        mid = (p1 + p2) * 0.5
        direction = (p2 - p1) / length
        radius = max(length * 0.015, 0.05)

        cyl = pv.Cylinder(
            center=mid, direction=direction, radius=radius, height=length * 0.95, resolution=8
        )
        # Triangulate to handle mixed quad/triangle capped cylinders
        cyl.triangulate()
        n_pts = cyl.n_points
        n_cells = cyl.n_cells

        all_verts.append(cyl.points)
        # Shift face indices — now all faces are triangles (4 values each: 3 + 3 indices)
        faces = cyl.faces.reshape(-1, 4)
        faces[:, 1:] += offset
        all_faces.append(faces.ravel())
        all_elem_tags.append(np.full(n_cells, elem.elem_tag, dtype=int))
        all_sec_names.append(assignments.get(eid, "?"))
        offset += n_pts

    if not all_verts:
        return None

    vertices = np.vstack(all_verts)
    faces = np.concatenate(all_faces)
    mesh = pv.PolyData(vertices, faces=faces)
    mesh.cell_data["elem_tag"] = np.concatenate(all_elem_tags)

    # Colour array: map section name to colour index
    sec_idx_map = {s: i for i, s in enumerate(sec_set)}
    sec_indices = np.array([sec_idx_map.get(s, 0) for s in all_sec_names], dtype=int)
    # Expand to per-cell
    per_cell = np.repeat(sec_indices, [len(t) for t in all_elem_tags])
    mesh.cell_data["section_idx"] = per_cell
    return mesh, sec_set, cmap


def _build_centreline_mesh(builder, elements, model):
    """Build merged centreline line PolyData (undeformed grey overlay)."""
    import pyvista as pv

    all_pts = []
    all_lines = []
    offset = 0
    for eid, elem in elements.items():
        if getattr(elem, "inactive", False):
            continue
        ni = model.nodes.get(elem.node_i)
        nj = model.nodes.get(elem.node_j)
        if ni is None or nj is None:
            continue
        p1 = np.array([ni.x, ni.y, ni.z])
        p2 = np.array([nj.x, nj.y, nj.z])
        n = max(2, int(np.linalg.norm(p2 - p1) * 2))
        pts = np.linspace(p1, p2, n)
        all_pts.append(pts)
        for i in range(n - 1):
            all_lines.append([2, offset + i, offset + i + 1])
        offset += n
    if not all_pts:
        return pv.PolyData()
    verts = np.vstack(all_pts)
    cells = np.array(all_lines, dtype=int)
    return pv.PolyData(verts, lines=cells)


# ============================================================================
# Main entry point
# ============================================================================


def plot_interactive_viewer(
    builder,
    combo_forces: dict[str, dict[int, dict[str, float]]] = None,
    combo_results: dict[str, dict[str, Any]] = None,
    initial_combo: str = None,
    initial_quantity: str = "Mz",
    selection: Optional["Selection"] = None,
    notebook: bool = False,
    **kwargs,
) -> Optional[Any]:
    """Interactive 3D viewer with widgets for structural engineering results.

    Opens a PyVista window (or returns a plotter for Jupyter) with:

    * **Radio buttons** — switch between force/moment quantities
      (``Mz``, ``My``, ``Mx``, ``Fx``, ``Fy``, ``Fz``).
    * **Text slider** — cycle through load combinations / result sets.
    * **Checkboxes** — toggle undeformed structure, force flags, labels,
      reactions.
    * **Click on tube** — floating overlay shows element ID, SAP2000 label,
      section name, and material name.
    * **Click on flag** — overlay shows the numeric value.

    Args:
        builder: Built :class:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder`.
        combo_forces: Dict ``{combo_name: elem_forces_dict}`` where each
            *elem_forces_dict* is the output of
            :meth:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder.extract_static_element_forces`.
            If ``None``, defaults to a single ``"Primary"`` empty combo
            (structure-only view).
        combo_results: Dict ``{combo_name: results_dict}`` where each
            *results_dict* is the output of
            :meth:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder.run_static_analysis`.
            Required for reaction arrows.
        initial_combo: Combo to show on startup.  Defaults to first key.
        initial_quantity: Initial quantity.  Default ``'Mz'``.
        selection: Optional :class:`~fea_toolkit.model.selection.Selection`
            to restrict which elements appear.
        notebook: If ``True``, return plotter for Jupyter embedding.
        **kwargs: Passed to ``pyvista.Plotter()``.

    Returns:
        ``pyvista.Plotter`` if *notebook* is True, otherwise ``None``.
    """
    try:
        import pyvista as pv
    except ImportError:
        print("Warning: pyvista not installed.  Install with: pip install pyvista")
        return None

    pv.set_plot_theme("document")

    # ------------------------------------------------------------------
    # 1. Data preparation
    # ------------------------------------------------------------------
    elements = builder.split_elements or builder.model.frame_elements
    assignments = (
        builder.split_assignments if builder.split_elements else builder.model.frame_assignments
    )

    if selection is not None:
        sel_ids = set(selection.get_frame_ids(builder.model))
        elements = {eid: elem for eid, elem in elements.items() if eid in sel_ids}

    model = builder.model

    if combo_forces is None:
        combo_forces = {"Primary": {}}
    combo_names = list(combo_forces.keys())
    if initial_combo is None:
        initial_combo = combo_names[0] if combo_names else "Primary"
    if initial_combo not in combo_forces and combo_names:
        initial_combo = combo_names[0]

    # Active elements only
    active_elems = {
        eid: elem for eid, elem in elements.items() if not getattr(elem, "inactive", False)
    }

    # Element info lookup
    elem_info: dict[int, dict] = {}
    for eid, elem in active_elems.items():
        sec_name = assignments.get(eid, "?")
        sec = model.sections.get(sec_name)
        mat_name = sec.material if sec else "?"
        sap_id = getattr(elem, "sap_id", getattr(elem, "elem_id", eid)) or eid
        elem_info[elem.elem_tag] = {
            "elem_id": eid,
            "sap_id": sap_id,
            "section_name": sec_name,
            "material_name": mat_name,
        }

    # Model height for auto-scaling
    z_all = [n.z for n in model.nodes.values()]
    model_height = max(z_all) - min(z_all) if z_all else 10.0

    # Pre-compute local axes for all active elements
    elem_local_axes: dict[str, tuple] = {}
    for eid, elem in active_elems.items():
        try:
            vx, vy, vz = builder._get_local_axes(elem)
            elem_local_axes[eid] = (vx, vy, vz)
        except Exception:
            pass

    # Unit labels
    force_unit = builder.units.get("F", "N")
    length_unit = builder.units.get("L", "m")
    moment_unit = f"{force_unit}·{length_unit}"

    # ------------------------------------------------------------------
    # 2. Build static meshes
    # ------------------------------------------------------------------
    # Structure tubes (pickable)
    tube_result = _build_structure_tubes(builder, active_elems, assignments, model)
    if tube_result is None:
        print("No elements to display.")
        return None
    tube_mesh, sec_names, cmap = tube_result

    # Centreline mesh (undeformed grey overlay)
    centreline_mesh = _build_centreline_mesh(builder, active_elems, model)

    # Pre-build flag meshes for each combo (just the first quantity for now)
    # We'll rebuild on quantity change
    flag_cache: dict[str, dict[str, Any]] = {}  # combo -> {quantity: (mesh, max_val)}

    def _get_flag(combo: str, quantity: str):
        """Get or build flag mesh for a combo+quantity pair."""
        key = f"{combo}::{quantity}"
        if key in flag_cache:
            return flag_cache[key]
        forces = combo_forces.get(combo, {})
        mesh, max_val = _build_flag_mesh(
            builder,
            active_elems,
            assignments,
            forces,
            quantity,
            elem_local_axes,
            model_height,
        )
        flag_cache[key] = (mesh, max_val)
        return mesh, max_val

    # ------------------------------------------------------------------
    # 3. Set up plotter
    # ------------------------------------------------------------------
    plotter = pv.Plotter(notebook=notebook, **kwargs)

    # -- Structure tubes --
    # Colour tubes by section
    n_sec = len(sec_names)
    lut = pv.LookupTable()
    lut.table = np.array([pv.Color(cmap[i % len(cmap)]).int_rgba for i in range(max(n_sec, 1))])
    plotter.add_mesh(
        tube_mesh,
        scalars="section_idx",
        lookup_table=lut,
        pickable=True,
        name="structure_tubes",
        show_scalar_bar=False,
        lighting=True,
        smooth_shading=True,
    )

    # -- Centreline overlay (grey, toggleable) --
    centreline_actor = plotter.add_mesh(
        centreline_mesh,
        color="lightgrey",
        line_width=2,
        opacity=0.4,
        name="centreline",
    )

    # -- Force flags (will be replaced on quantity/combo change) --
    current_combo = initial_combo
    current_quantity = initial_quantity
    flag_actor_ref = {"actor": None}  # mutable ref for closures
    legend_actor_ref = {"actor": None}

    def _update_flags(combo, quantity):
        """Replace the flag actor with a new one for *combo*/*quantity*."""
        nonlocal flag_actor_ref
        # Remove old flag actor
        if flag_actor_ref["actor"] is not None:
            with contextlib.suppress(Exception):
                plotter.remove_actor(flag_actor_ref["actor"])
            flag_actor_ref["actor"] = None
        # Remove old legend text
        if legend_actor_ref["actor"] is not None:
            with contextlib.suppress(Exception):
                plotter.remove_actor(legend_actor_ref["actor"])
            legend_actor_ref["actor"] = None

        flag_mesh, max_val = _get_flag(combo, quantity)
        if flag_mesh is None or max_val < 1e-15:
            return

        # Build a colour LUT: red (+ve) → white (0) → blue (-ve)
        clim = [-max_val, max_val]
        lut2 = pv.LookupTable(cmap="RdBu", scalar_range=(-max_val, max_val))
        lut2.divergent = True

        actor = plotter.add_mesh(
            flag_mesh,
            scalars="col_val",
            lookup_table=lut2,
            clim=clim,
            name="force_flags",
            pickable=True,
            opacity=0.85,
            show_scalar_bar=True,
            lighting=False,
        )
        flag_actor_ref["actor"] = actor

        # Add/replace legend text
        "Moment" if quantity.startswith("M") else "Force"
        unit = moment_unit if quantity.startswith("M") else force_unit
        legend_actor_ref["actor"] = plotter.add_text(
            f"{quantity} [{unit}]  (red = +ve, blue = −ve)",
            position="lower_edge",
            font_size=10,
        )

    _update_flags(current_combo, current_quantity)

    # -- Reaction arrows (toggleable) --
    reaction_actors: list[Any] = []
    _reactions_visible = False  # tracks checkbox state; starts hidden

    def _build_reactions(combo):
        """Build reaction arrow meshes for *combo*."""
        if combo_results is None:
            return []
        res = combo_results.get(combo)
        if res is None:
            return []
        reactions = res.get("nodal_reactions", {})
        if not reactions:
            return []
        # Precompute node_tag → node lookup to avoid O(N×R) nested search
        node_by_tag = {nd.node_tag: nd for nd in model.nodes.values()}
        actors = []
        z_vals = [n.z for n in node_by_tag.values()]
        z_rng = max(z_vals) - min(z_vals) if z_vals else 1.0
        max_h = 0.0
        max_v = 0.0
        data = []
        for nid_tag, r in reactions.items():
            fx, fy, fz = r[0], r[1], r[2]
            node = node_by_tag.get(nid_tag)
            if node is None:
                continue
            pos = np.array([node.x, node.y, node.z])
            horiz = math.hypot(fx, fy)
            vert = abs(fz)
            if horiz > 1e-6:
                max_h = max(max_h, horiz)
                data.append(("h", pos, np.array([fx, fy, 0.0]), horiz))
            if vert > 1e-6:
                max_v = max(max_v, vert)
                data.append(("v", pos, np.array([0.0, 0.0, fz]), vert))
        s_h = (z_rng * 0.08) / max(max_h, 1.0)
        s_v = (z_rng * 0.08) / max(max_v, 1.0)
        for atype, pos, vec, mag in data:
            sc = s_h if atype == "h" else s_v
            arrow = pv.Arrow(start=pos, direction=vec / max(mag, 1e-12), scale=mag * sc)
            colour = (0.9, 0.1, 0.1) if atype == "h" else (0.1, 0.8, 0.1)
            act = plotter.add_mesh(arrow, color=colour, opacity=0.85, name=f"reaction_{id(arrow)}")
            # Apply current visibility state immediately
            act.SetVisibility(_reactions_visible)
            actors.append(act)
        return actors

    reaction_actors = _build_reactions(current_combo)

    def _clear_reactions():
        nonlocal reaction_actors
        for act in reaction_actors:
            with contextlib.suppress(Exception):
                plotter.remove_actor(act)
        reaction_actors = []

    # -- Element labels (toggleable, built on demand) --
    labels_actor = {"actor": None}

    def _build_labels():
        """Create a single point-labels actor for all elements."""
        pts = []
        labels = []
        for eid, elem in active_elems.items():
            ni = model.nodes.get(elem.node_i)
            nj = model.nodes.get(elem.node_j)
            if ni is None or nj is None:
                continue
            mid = np.array([(ni.x + nj.x) * 0.5, (ni.y + nj.y) * 0.5, (ni.z + nj.z) * 0.5])
            sec = assignments.get(eid, "?")
            pts.append(mid)
            labels.append(f"{elem.elem_tag} [{sec}]")
        if not pts:
            return None
        pts_arr = np.array(pts)
        return plotter.add_point_labels(
            pts_arr,
            labels,
            font_size=8,
            point_size=4,
            shape="rounded_rect",
            fill_shape=True,
            shape_opacity=0.7,
            always_visible=True,
            name="elem_labels",
        )

    # ------------------------------------------------------------------
    # 4. Info overlay (updated on picking)
    # ------------------------------------------------------------------
    info_actor = {"actor": None}

    def _set_info_text(text: str):
        """Set or update the info overlay at the top-left."""
        if info_actor["actor"] is not None:
            with contextlib.suppress(Exception):
                plotter.remove_actor(info_actor["actor"])
        text = text.strip()
        info_actor["actor"] = plotter.add_text(
            text,
            position="upper_left",
            font_size=11,
            color="black",
        )

    _set_info_text(
        "Click any element or flag for info  |  "
        "Radio buttons (left): Mz/My/Mx/Fz/Fy/Fx  |  "
        "Checkboxes: Centreline / Labels / Reactions"
    )

    # ------------------------------------------------------------------
    # 5. Picking callback
    # ------------------------------------------------------------------
    def _picking_callback(mesh_or_actor):
        """Called when the user clicks on a pickable mesh."""
        # Determine what was picked
        try:
            actor = mesh_or_actor  # when use_actor=True
            name = actor.name if hasattr(actor, "name") else ""
        except Exception:
            return

        if name.startswith("elem_") or name == "structure_tubes":
            # Tube element picked — get elem_tag from cell data
            try:
                # Get the picked point and find closest element
                pick_pos = plotter.picked_point
            except Exception:
                pick_pos = None

            if pick_pos is not None:
                # Find closest element by checking midpoints
                best_tag = None
                best_dist = float("inf")
                for eid, elem in active_elems.items():
                    ni = model.nodes.get(elem.node_i)
                    nj = model.nodes.get(elem.node_j)
                    if ni is None or nj is None:
                        continue
                    mid = np.array([(ni.x + nj.x) * 0.5, (ni.y + nj.y) * 0.5, (ni.z + nj.z) * 0.5])
                    d = np.linalg.norm(np.array(pick_pos[:3]) - mid)
                    if d < best_dist:
                        best_dist = d
                        best_tag = elem.elem_tag

                if best_tag is not None and best_tag in elem_info:
                    info = elem_info[best_tag]
                    _set_info_text(
                        f"Element {best_tag}\n"
                        f"  SAP ID:  {info['sap_id']}\n"
                        f"  Section: {info['section_name']}\n"
                        f"  Material: {info['material_name']}"
                    )
        elif name == "force_flags":
            # Flag picked — get the col_val and elem_tag from point data
            try:
                pick_pos = plotter.picked_point
            except Exception:
                pick_pos = None
            if pick_pos is not None:
                # Search for the closest flag polygon
                flag_mesh, _ = _get_flag(current_combo, current_quantity)
                if flag_mesh is not None:
                    # Find closest point
                    pts = flag_mesh.points
                    vals = flag_mesh.point_data.get("col_val", None)
                    tags = flag_mesh.point_data.get("elem_tag", None)
                    if vals is not None and tags is not None:
                        dists = np.linalg.norm(pts - np.array(pick_pos[:3]), axis=1)
                        idx = np.argmin(dists)
                        if dists[idx] < model_height * 0.5:
                            val = float(vals[idx])
                            tag = int(tags[idx])
                            info = elem_info.get(tag, {})
                            sec = info.get("section_name", "?") if info else "?"
                            unit = moment_unit if current_quantity.startswith("M") else force_unit
                            _set_info_text(
                                f"Element {tag}  [{sec}]\n  {current_quantity} = {val:.3f} {unit}"
                            )

    plotter.enable_mesh_picking(callback=_picking_callback, use_actor=True, show=False)

    # ------------------------------------------------------------------
    # 6. Widgets
    # ------------------------------------------------------------------

    # -- 6a. Radio buttons: quantity selection --
    quantities = ["Mz", "My", "Mx", "Fz", "Fy", "Fx"]
    q_start_y = 10.0
    q_spacing = 55.0
    q_btn_size = 40

    class _QuantityCallback:
        def __init__(self, q):
            self.q = q

        def __call__(self):
            nonlocal current_quantity
            current_quantity = self.q
            _update_flags(current_combo, current_quantity)
            _clear_reactions()
            if combo_results and current_combo in combo_results:
                reaction_actors.extend(_build_reactions(current_combo))

    for i, q in enumerate(quantities):
        is_moment = q.startswith("M")
        btn_color = "#c44e52" if is_moment else "#4c72b0"
        plotter.add_radio_button_widget(
            _QuantityCallback(q),
            "quantity_group",
            position=(10.0, q_start_y + i * q_spacing),
            title=q,
            value=(q == initial_quantity),
            size=q_btn_size,
            border_size=6,
            color_on=btn_color,
            color_off="grey",
            background_color="white",
        )

    # -- 6b. Text slider: combo selection --
    if len(combo_names) > 1:

        def _combo_callback(value: str):
            nonlocal current_combo
            current_combo = value
            _update_flags(current_combo, current_quantity)
            _clear_reactions()
            if combo_results and current_combo in combo_results:
                reaction_actors.extend(_build_reactions(current_combo))

        plotter.add_text_slider_widget(
            _combo_callback,
            data=combo_names,
            value=combo_names.index(current_combo),
            pointa=(0.35, 0.95),
            pointb=(0.85, 0.95),
            color="black",
        )

    # -- 6c. Checkboxes: overlay toggles --
    # Checkboxes are ordered vertically: Centreline, Labels, Reactions.
    # Their positions are documented in the function docstring.
    toggle_y_start = q_start_y + len(quantities) * q_spacing + 20.0
    toggle_spacing = 45.0

    class _ToggleCentreline:
        def __call__(self, state):
            centreline_actor.SetVisibility(state)

    plotter.add_checkbox_button_widget(
        _ToggleCentreline(),
        value=True,
        position=(10.0, toggle_y_start),
        size=30,
        border_size=4,
        color_on="#55a868",
        color_off="grey",
    )

    class _ToggleLabels:
        def __call__(self, state):
            if state:
                if labels_actor["actor"] is None:
                    labels_actor["actor"] = _build_labels()
                else:
                    try:
                        labels_actor["actor"].SetVisibility(True)
                    except Exception:
                        labels_actor["actor"] = _build_labels()
            elif labels_actor["actor"] is not None:
                with contextlib.suppress(Exception):
                    labels_actor["actor"].SetVisibility(False)

    plotter.add_checkbox_button_widget(
        _ToggleLabels(),
        value=False,
        position=(10.0, toggle_y_start + toggle_spacing),
        size=30,
        border_size=4,
        color_on="#55a868",
        color_off="grey",
    )

    class _ToggleReactions:
        def __call__(self, state):
            nonlocal _reactions_visible
            _reactions_visible = state
            for act in reaction_actors:
                with contextlib.suppress(Exception):
                    act.SetVisibility(state)

    plotter.add_checkbox_button_widget(
        _ToggleReactions(),
        value=False,
        position=(10.0, toggle_y_start + 2 * toggle_spacing),
        size=30,
        border_size=4,
        color_on="#55a868",
        color_off="grey",
    )

    # ------------------------------------------------------------------
    # 7. Finishing touches
    # ------------------------------------------------------------------
    plotter.show_grid()
    _set_isometric_view(plotter)

    if notebook:
        return plotter
    plotter.show()
    return None
