"""Model, mesh, deformed-shape, modal, building and comparison viewers.

PyVista-based scene rendering from ``MeshModel`` / ``AnalysisBuilder`` /
NPZ inputs.  Re-exported by :mod:`fea_toolkit.plotting.viz`."""

import math
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import numpy as np

from ..model.sap_data import SAPModelData
from .viz_common import _NPZ_TYPES, _add_animation_timer, _set_isometric_view

if TYPE_CHECKING:
    from ..model.selection import Selection


def _build_deformed_mesh(
    segments: list,
    seg_npoints: list,
    all_quads: list,
    sec_idxs: list,
    scale: float,
    amp: float,
    shrink: float = 0.0,
) -> "tuple[Any, Optional[Any]]":
    """Build a single merged ``PolyData`` from frame-segment and shell-quad
    geometry, displaced by ``scale * amp`` along each element's eigenvector.

    Shell quads become ``[4, i, j, k, l]`` faces (not triangulated), so
    each quad maps to exactly one face — no cell‑count mismatch.

    Parameters
    ----------
    segments : list of (p1, p2, di, dj) tuples
        Frame segment data — each entry holds the two endpoints and their
        displacement vectors.
    seg_npoints : list of int
        Number of subdivision points per segment (fixed from undeformed
        length, so point count is invariant w.r.t. *amp*).
    all_quads : list of (p1, p2, p3, p4, d1, d2, d3, d4) tuples
        Shell quad data — four corners and their displacement vectors.
    sec_idxs : list of int
        Per-quad section index (one per entry in *all_quads*).  Used to
        assign a ``section_idx`` cell scalar on the shell mesh.
    scale : float
        Base scale factor from the eigenvector normalisation.
    amp : float
        Amplitude multiplier for the current frame.
    shrink : float
        Fraction to shrink shell quads toward their centroid
        (0.0 = full size, 0.1 = 10% gap at edges).  Applied to the
        rest-position vertices before displacement so the point-count
        invariant is preserved for animation.

    Returns
    -------
    tuple[pv.PolyData, pv.PolyData | None]
        ``(frame_mesh, shell_mesh)`` — frame mesh contains ``lines`` only;
        shell mesh contains quad ``faces`` with a ``section_idx`` cell
        scalar.  *shell_mesh* is ``None`` when there are no shell elements.
    """
    import pyvista as pv

    all_pts: list = []
    all_lines: list = []
    all_faces: list = []
    offset = 0

    # Frame lines
    for (p1, p2, di, dj), n in zip(segments, seg_npoints):
        d1 = np.array(di) * scale * amp
        d2 = np.array(dj) * scale * amp
        a = p1 + d1
        b = p2 + d2
        pts = np.linspace(a, b, n)
        all_pts.append(pts)
        for i in range(n - 1):
            all_lines.append([2, offset + i, offset + i + 1])
        offset += n

    n_frame_pts = offset

    # Shell quads — all faces are [4, i, j, k, l].  Use a shell-local
    # vertex offset (starting at 0) since the shell mesh will be created
    # from ``verts[n_frame_pts:]`` with its own 0‑based indexing.
    shell_offset = 0
    for quad in all_quads:
        p1, p2, p3, p4, d1, d2, d3, d4 = quad
        # Apply shrink to rest positions (before displacement) to
        # preserve point-count invariance for animation.
        if shrink:
            c = (p1 + p2 + p3 + p4) / 4
            p1 = p1 + (c - p1) * shrink
            p2 = p2 + (c - p2) * shrink
            p3 = p3 + (c - p3) * shrink
            p4 = p4 + (c - p4) * shrink
        a1 = p1 + d1 * scale * amp
        a2 = p2 + d2 * scale * amp
        a3 = p3 + d3 * scale * amp
        a4 = p4 + d4 * scale * amp
        all_pts.extend([a1, a2, a3, a4])
        all_faces.append([4, shell_offset, shell_offset + 1, shell_offset + 2, shell_offset + 3])
        shell_offset += 4
        offset += 4  # still track global offset for verts partitioning

    if not all_pts:
        return pv.PolyData(), None

    verts = np.vstack(all_pts)

    # ── Frame mesh ──
    frame_mesh = pv.PolyData()
    if n_frame_pts > 0:
        cells = np.array(all_lines, dtype=int) if all_lines else np.empty((0, 3), dtype=int)
        fm = pv.PolyData(verts[:n_frame_pts])
        if len(cells) > 0:
            fm.lines = cells
        frame_mesh = fm

    # ── Shell mesh ──
    shell_mesh: Optional[pv.PolyData] = None
    if all_faces:
        # Build (N, 5) array — set faces BEFORE points so PyVista 0.48
        # correctly interprets the face structure.
        faces = np.array(all_faces, dtype=int)
        sm = pv.PolyData()
        sm.faces = faces
        sm.points = verts[n_frame_pts:]
        if sec_idxs:
            # Each quad → 1 face → 1 cell_data entry (no mismatch)
            sm.cell_data["section_idx"] = np.array(sec_idxs, dtype=int)
        shell_mesh = sm

    return frame_mesh, shell_mesh


def _collapse_to_parents(data, source):
    """Post‑process mesh data to replace child elements with their parents.

    For each frame/shell entry with a non‑empty ``parent`` field, children
    are removed and replaced by a single entry representing the original
    unsplit parent element.  Unsplit elements pass through unchanged.

    Parameters
    ----------
    data : dict
        Mesh data dict from :func:`_resolve_mesh_data` (builder or NPZ path).
    source : object
        Original data source — used to resolve parent geometry.

    Returns
    -------
    dict
        Updated mesh data dict with children collapsed into parents.
    """

    nodes = data["nodes"]

    # ── Collapse frames ─────────────────────────────────────────
    # Build group: parent_id -> [child_frame_entries]
    parent_groups: dict[str, list] = {}
    for fr in data["frames"]:
        pid = fr.get("parent")
        if pid and pid not in {"?", ""} and pid is not None:
            parent_groups.setdefault(pid, []).append(fr)

    if parent_groups:
        collapsed_frames = []
        seen_parents: set = set()

        # Build a node-tag-to-SAP-id lookup for resolving parent endpoints
        # from NPZ data (nodes are already populated by _resolve_mesh_data).
        tag_to_sid: dict[int, str] = {}
        for sid, nd in nodes.items():
            tag_to_sid[nd["tag"]] = sid

        # For builder sources, build a full parent endpoint lookup from model
        parent_frame_endpoints: dict[str, tuple] = {}
        if (not isinstance(source, dict) and hasattr(source, "model")) or (
            hasattr(source, "frame_elements") and not isinstance(source, dict)
        ):
            if hasattr(source, "model"):
                model = source.model
            elif hasattr(source, "mesh_model"):
                model = source.mesh_model
            elif isinstance(source, SAPModelData):
                model = source
            else:
                model = source
            for eid, elem in model.frame_elements.items():
                if getattr(elem, "inactive", False):
                    parent_frame_endpoints[eid] = (elem.node_i, elem.node_j)

        # For NPZ sources, read parent endpoints from the new arrays
        # Keyed by parent_sap_id so the collapse_to_parents lookup by pid works.
        npz_parent_node_i: dict[str, int] = {}
        npz_parent_node_j: dict[str, int] = {}
        if isinstance(source, dict):
            nf = len(source.get("frame_eid", []))
            for i in range(nf):
                pid = str(source.get("frame_parent_sap_id", [""] * nf)[i])
                sid = str(source["frame_sap_id"][i])
                pni = int(source.get("frame_parent_node_i", [0] * nf)[i])
                pnj = int(source.get("frame_parent_node_j", [0] * nf)[i])
                if pid and pni != 0 and pnj != 0:
                    npz_parent_node_i[pid] = pni
                    npz_parent_node_j[pid] = pnj
                elif not pid or pid == sid:
                    # unsplit elements store their own node tags
                    pass

        for fr in data["frames"]:
            pid = fr.get("parent")
            if pid and pid not in {"?", ""} and pid is not None:
                if pid in seen_parents:
                    continue  # already added
                seen_parents.add(pid)
                children = parent_groups.get(pid, [])

                # Resolve parent endpoints
                if not isinstance(source, dict):
                    # Builder path
                    p_ni_id, p_nj_id = parent_frame_endpoints.get(pid, (None, None))
                    if p_ni_id and p_nj_id:
                        p_ni = nodes.get(p_ni_id)
                        p_nj = nodes.get(p_nj_id)
                        if p_ni and p_nj:
                            collapsed_frames.append(
                                {
                                    "id": pid,
                                    "ni_id": p_ni_id,
                                    "nj_id": p_nj_id,
                                    "sec": children[0].get("sec", "?"),
                                    "parent": None,
                                }
                            )
                            continue
                    # Fallback: derive from children endpoints
                    sorted_children = _sort_children_by_location(children, nodes)
                    if sorted_children:
                        first = sorted_children[0]
                        last = sorted_children[-1]
                        collapsed_frames.append(
                            {
                                "id": pid,
                                "ni_id": first.get("ni_id"),
                                "nj_id": last.get("nj_id"),
                                "ni_tag": first.get("ni_tag"),
                                "nj_tag": last.get("nj_tag"),
                                "sec": children[0].get("sec", "?"),
                                "parent": None,
                            }
                        )
                else:
                    # NPZ path — use parent node tags from NPZ arrays
                    p_ni_tag = npz_parent_node_i.get(pid, 0)
                    p_nj_tag = npz_parent_node_j.get(pid, 0)
                    if p_ni_tag and p_nj_tag:
                        collapsed_frames.append(
                            {
                                "id": pid,
                                "ni_tag": p_ni_tag,
                                "nj_tag": p_nj_tag,
                                "sec": children[0].get("sec", "?"),
                                "parent": None,
                            }
                        )
                    else:
                        # Fallback: derive from sorted children
                        sorted_children = _sort_children_by_location(children, nodes)
                        if sorted_children:
                            first = sorted_children[0]
                            last = sorted_children[-1]
                            collapsed_frames.append(
                                {
                                    "id": pid,
                                    "ni_tag": first.get("ni_tag"),
                                    "nj_tag": last.get("nj_tag"),
                                    "sec": children[0].get("sec", "?"),
                                    "parent": None,
                                }
                            )
            else:
                # Unsplit element — pass through
                collapsed_frames.append(fr)
        data["frames"] = collapsed_frames

    # ── Collapse shells ─────────────────────────────────────────
    shell_parent_groups: dict[str, list] = {}
    for sh in data["shells"]:
        pid = sh.get("parent")
        if pid and pid not in {"?", ""} and pid is not None:
            shell_parent_groups.setdefault(pid, []).append(sh)

    if shell_parent_groups:
        collapsed_shells = []
        seen_shell_parents: set = set()
        for sh in data["shells"]:
            pid = sh.get("parent")
            if pid and pid not in {"?", ""} and pid is not None:
                if pid in seen_shell_parents:
                    continue
                seen_shell_parents.add(pid)
                children = shell_parent_groups.get(pid, [])
                # Reconstruct parent from first child's parent reference
                # (parent node IDs are stored in the original area_element)
                parent_node_ids = None
                if not isinstance(source, dict):
                    model = source.model if hasattr(source, "model") else source
                    parent_area = model.area_elements.get(pid)
                    if parent_area is not None:
                        parent_node_ids = parent_area.node_ids[:4]
                if parent_node_ids:
                    collapsed_shells.append(
                        {
                            "id": pid,
                            "sec": children[0].get("sec", "unknown"),
                            "node_ids": parent_node_ids,
                            "inactive": False,
                        }
                    )
                else:
                    # Fallback: use first child's geometry
                    collapsed_shells.append(children[0])
            else:
                collapsed_shells.append(sh)
        data["shells"] = collapsed_shells

    return data


def _sort_children_by_location(children, nodes, parent_axis=None):
    """Sort child frame entries by spatial position along the parent axis.

    For NPZ data (using ``ni_tag``/``nj_tag``), computes the midpoint
    along the element axis.  For builder data (using ``ni_id``/``nj_id``),
    sorts by node ID order along the parent span.

    When *parent_axis* is provided (a unit vector), children are sorted
    by their midpoint projected onto the parent axis via dot product,
    rather than using Z-only midpoint.  This gives correct ordering for
    slanted/curved elements that are not vertical.
    """
    import numpy as np

    if not children:
        return children

    def _mid_pos(fr):
        """Compute midpoint of the child element as a 3D point."""
        ni = _resolve_frame_node(nodes, fr, "i")
        nj = _resolve_frame_node(nodes, fr, "j")
        if ni and nj:
            return np.array(
                [(ni["x"] + nj["x"]) * 0.5, (ni["y"] + nj["y"]) * 0.5, (ni["z"] + nj["z"]) * 0.5]
            )
        return np.zeros(3)

    if parent_axis is not None:
        # Project midpoint onto parent axis for correct ordering
        return sorted(children, key=lambda fr: np.dot(_mid_pos(fr), parent_axis))
    else:
        # Fallback: Z-only (backward compatible for vertical elements)
        return sorted(children, key=lambda fr: _mid_pos(fr)[2])


def _resolve_mesh_data(source, collapse_to_parents=False):
    """Extract mesh geometry arrays from a builder, SAPModelData, or NPZ data dict.

    Supports three source types:

    * A ``SAPModelData`` instance (raw model, before preprocessing).
    * An ``AnalysisBuilder`` or builder object (after preprocessing).
    * A dict loaded from a unified NPZ file (via ``np.load()``).

    When *collapse_to_parents* is ``True``, child elements resulting from
    splitting are replaced by their original unsplit parent elements, so
    the displayed geometry matches what the engineer drew in SAP2000.

    Returns a dict with keys:
        nodes          – ``{node_id: {tag, x, y, z}}``
        frames         – ``[{id, ni_tag/ni_id, nj_tag/nj_id, sec, parent}]``
        shells         – ``[{id, sec, node_ids/node_tags, inactive, parent}]``
        orphan_nodes   – ``{node_id: {tag, x, y, z}}``
        edge_constraints – list of constraint tuples
        mesh_node_ids  – ``set`` of node IDs containing ``_mesh_``
    """

    data = {
        "nodes": {},
        "frames": [],
        "shells": [],
        "orphan_nodes": {},
        "edge_constraints": [],
        "mesh_node_ids": set(),
    }

    # ═════════════════════════════════════════════════════════════
    # Approach A: SAPModelData passthrough — raw importer output
    # ═════════════════════════════════════════════════════════════
    if isinstance(source, SAPModelData):
        for nid, nd in source.nodes.items():
            data["nodes"][nid] = {
                "tag": nd.node_tag,
                "x": nd.x,
                "y": nd.y,
                "z": nd.z,
            }
        for eid, elem in source.frame_elements.items():
            data["frames"].append(
                {
                    "id": eid,
                    "ni_id": elem.node_i,
                    "nj_id": elem.node_j,
                    "sec": source.frame_assignments.get(eid, "?"),
                    "parent": None,
                }
            )
        for aid, area in source.area_elements.items():
            data["shells"].append(
                {
                    "id": aid,
                    "sec": source.area_assignments.get(aid, "unknown"),
                    "node_ids": area.node_ids[:4],
                    "inactive": False,
                    "parent": None,
                }
            )
        return data

    # ═════════════════════════════════════════════════════════════
    # NPZ / HDF5 data dict
    # ═════════════════════════════════════════════════════════════
    if isinstance(source, _NPZ_TYPES):
        n = len(source.get("node_tag", []))
        for i in range(n):
            tag = int(source["node_tag"][i])
            sid = str(source.get("node_sap_id", [""] * n)[i])
            node_entry = {
                "tag": tag,
                "x": float(source["node_x"][i]),
                "y": float(source["node_y"][i]),
                "z": float(source["node_z"][i]),
            }
            # Key by both SAP ID (string) and node tag (int) so that both
            # frame endpoint lookups (ni_tag/nj_tag) and shell vertex lookups
            # (shell_node_1..4) resolve immediately without a tag-search fallback.
            data["nodes"][sid] = node_entry
            data["nodes"][tag] = node_entry
            if "_mesh_" in sid:
                data["mesh_node_ids"].add(sid)

        nf = len(source.get("frame_eid", []))
        for i in range(nf):
            data["frames"].append(
                {
                    "id": str(source["frame_sap_id"][i]),
                    "ni_tag": int(source["frame_node_i"][i]),
                    "nj_tag": int(source["frame_node_j"][i]),
                    "sec": str(source["frame_sec_name"][i]),
                    "parent": str(source.get("frame_parent_sap_id", [""] * nf)[i]),
                }
            )

        ns = len(source.get("shell_eid", []))
        shell_parent_arr = source.get("shell_parent_sap_id", [""] * ns)
        for i in range(ns):
            pid = str(shell_parent_arr[i]) if i < len(shell_parent_arr) else ""
            data["shells"].append(
                {
                    "id": str(source["shell_sap_id"][i]),
                    "sec": str(source["shell_sec_name"][i]),
                    "node_tags": [int(source[f"shell_node_{k}"][i]) for k in (1, 2, 3, 4)],
                    "parent": pid if pid and pid not in {"", "?"} else None,
                }
            )

        if collapse_to_parents:
            data = _collapse_to_parents(data, source)
        return data

    # ═════════════════════════════════════════════════════════════
    # Builder / AnalysisBuilder / MeshModel object
    # ═════════════════════════════════════════════════════════════
    builder = source
    if hasattr(builder, "model"):
        model = builder.model
    elif hasattr(builder, "mesh_model"):
        model = builder.mesh_model
    else:
        model = builder  # assume it's already a MeshModel
    elements = (
        builder.split_elements
        if hasattr(builder, "split_elements") and builder.split_elements
        else model.frame_elements
    )
    assignments = (
        builder.split_assignments
        if hasattr(builder, "split_assignments") and builder.split_assignments
        else model.frame_assignments
    )

    # Nodes
    for nid, nd in model.nodes.items():
        data["nodes"][nid] = {
            "tag": nd.node_tag,
            "x": nd.x,
            "y": nd.y,
            "z": nd.z,
        }
        if "_mesh_" in nid:
            data["mesh_node_ids"].add(nid)

    # Orphan nodes
    mm = getattr(builder, "_mesh_model", None) if hasattr(builder, "_mesh_model") else None
    if mm is None:
        mm = getattr(builder, "mesh_model", None)
    if mm is not None and hasattr(mm, "orphan_nodes"):
        for nid, nd in mm.orphan_nodes.items():
            data["orphan_nodes"][nid] = {
                "tag": nd.node_tag,
                "x": nd.x,
                "y": nd.y,
                "z": nd.z,
            }

    if collapse_to_parents:
        # When collapsing, include inactive parents instead of children
        # Use the model's full frame_elements (including inactive parents)
        all_elements = model.frame_elements
        all_assignments = model.frame_assignments

        # Add inactive parent elements as frame entries
        for eid, elem in all_elements.items():
            if not getattr(elem, "inactive", False):
                # Not a parent — skip, will be added by the normal loop
                continue
            data["frames"].append(
                {
                    "id": eid,
                    "ni_id": elem.node_i,
                    "nj_id": elem.node_j,
                    "sec": (all_assignments or {}).get(eid, "?"),
                    "parent": None,
                }
            )

        # Add inactive parent area elements as shell entries
        for aid, area in model.area_elements.items():
            if not getattr(area, "inactive", False):
                continue
            data["shells"].append(
                {
                    "id": aid,
                    "sec": model.area_assignments.get(aid, "unknown"),
                    "node_ids": area.node_ids[:4],
                    "inactive": False,
                    "parent": None,
                }
            )
    else:
        # Normal path: add active (child) elements only
        for eid, elem in elements.items():
            if getattr(elem, "inactive", False):
                continue
            data["frames"].append(
                {
                    "id": eid,
                    "ni_id": elem.node_i,
                    "nj_id": elem.node_j,
                    "sec": (assignments or {}).get(eid, "?"),
                    "parent": getattr(elem, "parent_id", None),
                }
            )

        for aid, area in model.area_elements.items():
            if getattr(area, "inactive", False):
                continue
            data["shells"].append(
                {
                    "id": aid,
                    "sec": model.area_assignments.get(aid, "unknown"),
                    "node_ids": area.node_ids[:4],
                    "inactive": getattr(area, "inactive", False),
                    "parent": getattr(area, "parent_id", None),
                }
            )

        # Also add inactive parent area elements as grey wireframe overlays
        for aid, area in model.area_elements.items():
            if not getattr(area, "inactive", False):
                continue
            data["shells"].append(
                {
                    "id": aid,
                    "sec": model.area_assignments.get(aid, "unknown"),
                    "node_ids": area.node_ids[:4],
                    "inactive": True,
                    "parent": None,
                }
            )

    # Edge constraints
    if mm is not None and hasattr(mm, "detected_edge_pairs"):
        data["edge_constraints"] = list(mm.detected_edge_pairs)
    elif hasattr(builder, "_saved_edge_constraints"):
        data["edge_constraints"] = list(builder._saved_edge_constraints)

    return data


def _resolve_frame_node(nodes, fr, side="i"):
    """Resolve a frame endpoint node from resolved mesh data.

    Tries ``ni_id``/``nj_id`` (string key) first, then attempts a direct
    ``nodes`` lookup by ``ni_tag``/``nj_tag`` (integer tag) for mappings
    that are dual-keyed by tag (NPZ-derived data).  Returns the directly
    matched node when available, and retains a linear scan by tag value
    as a fallback for node mappings without tag keys.
    """
    key_id = f"n{side}_id"
    key_tag = f"n{side}_tag"
    nid = fr.get(key_id)
    if nid is not None:
        nd = nodes.get(nid)
        if nd is not None:
            return nd
    tag = fr.get(key_tag)
    if tag is not None:
        # Direct hit for dual-keyed mappings (int tag as a dict key).
        nd = nodes.get(tag)
        if nd is not None:
            return nd
        # Fallback: linear scan for mappings keyed by SAP ID only.
        for nd in nodes.values():
            if nd["tag"] == tag:
                return nd
    return None


def _resolve_shell_node(nodes, ref):
    """Resolve a shell vertex node from resolved mesh data.

    Tries the string SAP ID key directly first, then int-to-string
    conversion, then falls back to searching by ``tag`` value (int)
    across all nodes.  Returns the node dict or ``None``.

    This is needed because:
    * ``_resolve_mesh_data`` indexes nodes by SAP ID (string, e.g.
      ``"area-1_sub_0_node_1"``).
    * NPZ shell connectivity arrays store node **tags** (integers,
      e.g. ``999``).
    * A direct ``nodes.get(tag)`` fails — the key system differs.
    """
    nd = nodes.get(ref)
    if nd is not None:
        return nd
    nd = nodes.get(str(ref))
    if nd is not None:
        return nd
    # Fallback: search by tag value
    for nd in nodes.values():
        if nd["tag"] == ref:
            return nd
    return None


def _render_scene(
    plotter,
    data,
    *,
    shrink=0.0,
    xlim=None,
    ylim=None,
    zlim=None,
    show_nodes=True,
    show_orphan_nodes=False,
    show_mesh_nodes=False,
    show_frames=True,
    show_shells=True,
    show_constraints=False,
    show_frame_labels=False,
    show_node_labels=False,
    show_area_labels=False,
    node_label_offset=0.4,
    tag_font=16,
    section_colors=None,
):
    """Render mesh geometry from resolved data into a PyVista plotter.

    Parameters
    ----------
    plotter : pv.Plotter
        The plotter to render into.
    data : dict
        Mesh data dict from :func:`_resolve_mesh_data`.
    shrink : float
        Fraction to shrink quads/lines toward centroid.
    xlim, ylim, zlim : tuple or None
        (lo, hi) bounding-box filters.
    show_nodes, show_orphan_nodes, show_mesh_nodes : bool
        Toggle node groups.
    show_frames, show_shells : bool
        Toggle element groups.
    show_constraints : bool
        Draw edge constraint lines.
    show_frame_labels, show_node_labels, show_area_labels : bool
        Toggle text labels.
    """
    import numpy as np
    import pyvista as pv

    def _in_limits(pt):
        for lim, val in [(xlim, pt[0]), (ylim, pt[1]), (zlim, pt[2])]:
            if lim is None:
                continue
            lo, hi = lim
            if lo is not None and val < lo:
                return False
            if hi is not None and val > hi:
                return False
        return True

    def _shrink_quad(pts, factor):
        c = np.mean(pts, axis=0)
        return pts + (c - pts) * factor

    nodes = data["nodes"]
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
    if section_colors is None:
        section_colors = {}

    # ── Frames ──────────────────────────────────────────────────
    if show_frames:
        frame_lines = []
        for fr in data["frames"]:
            ni = _resolve_frame_node(nodes, fr, "i")
            nj = _resolve_frame_node(nodes, fr, "j")
            if ni is None or nj is None:
                continue
            mid = [(ni["x"] + nj["x"]) / 2, (ni["y"] + nj["y"]) / 2, (ni["z"] + nj["z"]) / 2]
            if not _in_limits(mid):
                continue
            p1 = np.array([ni["x"], ni["y"], ni["z"]])
            p2 = np.array([nj["x"], nj["y"], nj["z"]])
            if shrink:
                m = (p1 + p2) / 2
                p1 = p1 + (m - p1) * shrink
                p2 = p2 + (m - p2) * shrink
            sec = fr.get("sec", "?")
            frame_lines.append((p1, p2, sec))

        all_secs = sorted({s for _, _, s in frame_lines})
        sec_col = section_colors or {s: cmap[i % len(cmap)] for i, s in enumerate(all_secs)}

        for p1, p2, sec in frame_lines:
            n = max(2, int(np.linalg.norm(p2 - p1) * 2))
            pts = np.linspace(p1, p2, n)
            poly = pv.lines_from_points(pts)
            plotter.add_mesh(poly, color=sec_col.get(sec, "#4c72b0"), line_width=4, opacity=0.7)

    # ── Shells ──────────────────────────────────────────────────
    if show_shells:
        active_shells = {}
        inactive_shells = []
        for sh in data["shells"]:
            pts = []
            refs = sh.get("node_ids") or sh.get("node_tags") or []
            for ref in refs:
                # Nodes dict is dual-keyed (SAP ID string + int tag)
                nd = nodes.get(ref)
                if nd is None:
                    break
                pts.append([nd["x"], nd["y"], nd["z"]])
            if len(pts) < 3:
                continue
            centroid = np.mean(pts, axis=0)
            if not _in_limits(centroid):
                continue
            while len(pts) < 4:
                pts.append(pts[-1])
            if sh.get("inactive"):
                inactive_shells.append(np.array(pts))
            else:
                sec = sh.get("sec", "unknown")
                active_shells.setdefault(sec, []).append(np.array(pts))

        for quad_pts in inactive_shells:
            qp = _shrink_quad(np.array(quad_pts), shrink) if shrink else np.array(quad_pts)
            plotter.add_mesh(
                pv.PolyData(qp, faces=[4, 0, 1, 2, 3]),
                color="lightgrey",
                opacity=0.12,
                show_edges=True,
                edge_color="grey",
                line_width=0.5,
            )

        for i, (sec_name, quads) in enumerate(active_shells.items()):
            if section_colors:
                c = section_colors.get(sec_name) or cmap[i % len(cmap)]
            else:
                c = cmap[i % len(cmap)]
            for quad_pts in quads:
                pts = _shrink_quad(np.array(quad_pts), shrink) if shrink else np.array(quad_pts)
                is_tri = np.allclose(pts[2], pts[3])
                face = (
                    pv.PolyData(pts[:3], faces=[3, 0, 1, 2])
                    if is_tri
                    else pv.PolyData(pts, faces=[4, 0, 1, 2, 3])
                )
                plotter.add_mesh(
                    face, color=c, opacity=0.35, show_edges=True, edge_color=c, line_width=0.8
                )

    # ── Nodes ───────────────────────────────────────────────────
    # Deduplicate by tag: NPZ nodes may be dual-keyed (SAP ID + int tag).
    # Built once before the marker and label blocks so node labels work
    # even when show_nodes is False.
    seen_tags = set()
    unique_nodes = []
    for n in nodes.values():
        tag = n.get("tag")
        if tag is not None and tag not in seen_tags:
            seen_tags.add(tag)
            unique_nodes.append(n)

    if show_nodes:
        npts = np.array(
            [[n["x"], n["y"], n["z"]] for n in unique_nodes if _in_limits([n["x"], n["y"], n["z"]])]
        )
        if len(npts):
            plotter.add_mesh(
                pv.PolyData(npts), color="black", point_size=6, render_points_as_spheres=True
            )

    # ── Orphan nodes ────────────────────────────────────────────
    if show_orphan_nodes and data["orphan_nodes"]:
        opts = np.array(
            [
                [n["x"], n["y"], n["z"]]
                for n in data["orphan_nodes"].values()
                if _in_limits([n["x"], n["y"], n["z"]])
            ]
        )
        if len(opts):
            plotter.add_mesh(
                pv.PolyData(opts), color="darkorange", point_size=8, render_points_as_spheres=True
            )

    # ── Mesh-created nodes ──────────────────────────────────────
    if show_mesh_nodes:
        mpts = np.array(
            [
                [nodes[nid]["x"], nodes[nid]["y"], nodes[nid]["z"]]
                for nid in data["mesh_node_ids"]
                if nid in nodes and _in_limits([nodes[nid]["x"], nodes[nid]["y"], nodes[nid]["z"]])
            ]
        )
        if len(mpts):
            plotter.add_mesh(
                pv.PolyData(mpts), color="lime", point_size=10, render_points_as_spheres=True
            )

    # ── Edge constraints ────────────────────────────────────────
    if show_constraints and data["edge_constraints"]:
        segs = []
        for entry in data["edge_constraints"]:
            if not isinstance(entry, tuple) or len(entry) < 3:
                continue
            masters = entry[1] if len(entry) > 1 else []
            slaves = entry[2] if len(entry) > 2 else []
            for mn in masters:
                nid = mn[0] if isinstance(mn, (list, tuple)) else str(mn)
                cn = nodes.get(nid)
                if cn is None:
                    continue
                for sn in slaves:
                    sid = sn[0] if isinstance(sn, (list, tuple)) else str(sn)
                    fn = nodes.get(sid)
                    if fn is None:
                        continue
                    mid = [
                        (cn["x"] + fn["x"]) / 2,
                        (cn["y"] + fn["y"]) / 2,
                        (cn["z"] + fn["z"]) / 2,
                    ]
                    if not _in_limits(mid):
                        continue
                    segs.append([cn["x"], cn["y"], cn["z"], fn["x"], fn["y"], fn["z"]])
        if segs:
            pts = np.array(segs).reshape(-1, 3)
            n_s = len(segs)
            conn = np.column_stack(
                [
                    np.full(n_s, 2, dtype=int),
                    np.arange(0, 2 * n_s, 2, dtype=int),
                    np.arange(1, 2 * n_s + 1, 2, dtype=int),
                ]
            ).ravel()
            plotter.add_mesh(
                pv.PolyData(pts, lines=conn), color="yellow", opacity=0.25, line_width=12
            )

    # ── Labels ──────────────────────────────────────────────────
    if show_node_labels:
        # Use the same tag-deduplicated unique_nodes collection as markers
        pts, tags = [], []
        for n in unique_nodes:
            if _in_limits([n["x"], n["y"], n["z"]]):
                tag_val = n.get("tag", "")
                pts.append([n["x"] + node_label_offset, n["y"] + node_label_offset, n["z"]])
                tags.append(f"N{tag_val}")
        if pts:
            plotter.add_point_labels(
                np.array(pts), tags, font_size=tag_font, point_size=0, shape=None
            )

    if show_frame_labels:
        pts, tags = [], []
        for fr in data["frames"]:
            ni = _resolve_frame_node(nodes, fr, "i")
            nj = _resolve_frame_node(nodes, fr, "j")
            if ni is None or nj is None:
                continue
            mid = [
                (ni["x"] + nj["x"]) / 2 - node_label_offset,
                (ni["y"] + nj["y"]) / 2 - node_label_offset,
                (ni["z"] + nj["z"]) / 2,
            ]
            if not _in_limits(mid):
                continue
            pts.append(mid)
            tags.append(f"F{fr['id']}")
        if pts:
            plotter.add_point_labels(
                np.array(pts), tags, font_size=tag_font, point_size=0, shape=None
            )

    if show_area_labels:
        pts, tags = [], []
        for sh in data["shells"]:
            npts = []
            for ref in sh.get("node_ids") or []:
                nd = nodes.get(ref)
                if nd:
                    npts.append([nd["x"], nd["y"], nd["z"]])
            if len(npts) < 3:
                continue
            centroid = np.mean(npts, axis=0)
            if not _in_limits(centroid):
                continue
            pts.append([centroid[0], centroid[1], centroid[2] + node_label_offset])
            tags.append(f"A{sh['id']}")
        if pts:
            plotter.add_point_labels(
                np.array(pts), tags, font_size=tag_font, point_size=0, shape=None
            )

    plotter.show_grid()


def plot_mesh(
    source,
    *,
    collapse_to_parents=False,
    show_nodes=True,
    show_frames=True,
    show_shells=True,
    show_mesh_nodes=False,
    show_constraints=False,
    show_orphan_nodes=False,
    shrink=0.0,
    xlim=None,
    ylim=None,
    zlim=None,
    show_node_labels=False,
    show_frame_labels=False,
    show_area_labels=False,
    notebook=False,
    **kwargs,
):
    """Display a mesh in 3D from a builder, SAPModelData, or NPZ data dict.

    Single‑model viewer — accepts:

    * An ``SAPModelData`` instance (raw importer output, before preprocessing).
    * An ``AnalysisBuilder`` instance (built, after preprocessing).
    * A dict loaded from a unified NPZ file (via ``np.load()``).

    When ``collapse_to_parents=True``, split children are replaced with their
    unsplit parent elements, showing the model as drawn in SAP2000.

    Args:
        source: Builder, SAPModelData, or NPZ data dict.
        collapse_to_parents: Show unsplit parent elements (default ``False``).
        show_nodes: Draw node markers.
        show_frames: Draw frame elements.
        show_shells: Draw shell elements.
        show_mesh_nodes: Highlight mesh‑created nodes (green).
        show_constraints: Draw edge constraint lines (yellow).
        show_orphan_nodes: Show orphan nodes (darkorange).
        shrink: Fraction to shrink quads/lines toward centroid.
        xlim: ``(lo, hi)`` bounding-box filter.
        ylim: ``(lo, hi)`` bounding-box filter.
        zlim: ``(lo, hi)`` bounding-box filter.
        show_node_labels: Add node labels.
        show_frame_labels: Add frame labels.
        show_area_labels: Add area labels.
        notebook: Return plotter for Jupyter embedding.
        **kwargs: Passed to ``pyvista.Plotter()``.

    Returns:
        ``pv.Plotter`` if *notebook* is True, otherwise ``None``.
    """
    import pyvista as pv

    data = _resolve_mesh_data(source, collapse_to_parents=collapse_to_parents)
    pv.set_plot_theme("document")
    plotter = pv.Plotter(notebook=notebook, **kwargs)
    _render_scene(
        plotter,
        data,
        show_nodes=show_nodes,
        show_frames=show_frames,
        show_shells=show_shells,
        show_mesh_nodes=show_mesh_nodes,
        show_constraints=show_constraints,
        show_orphan_nodes=show_orphan_nodes,
        shrink=shrink,
        xlim=xlim,
        ylim=ylim,
        zlim=zlim,
        show_node_labels=show_node_labels,
        show_frame_labels=show_frame_labels,
        show_area_labels=show_area_labels,
    )
    _set_isometric_view(plotter)
    if notebook:
        return plotter
    plotter.show()
    return None


def compare_meshes(
    source_a,
    source_b,
    *,
    collapse_to_parents=False,
    labels=("Model A", "Model B"),
    notebook=False,
    **kwargs,
):
    """Side‑by‑side mesh comparison from two builders or NPZ data dicts.

    Shows two PyVista subplots (left = source_a, right = source_b).

    Args:
        source_a: First model (builder or NPZ dict).
        source_b: Second model (builder or NPZ dict).
        collapse_to_parents: Show unsplit parent elements (default ``False``).
        labels: Pair of titles for the subplots.
        notebook: Return plotter for Jupyter embedding (default ``False``).
        **kwargs: Passed to :func:`_render_scene` (show_*, shrink, xlim, etc.).

    Returns:
        ``pv.Plotter`` if *notebook* is True, otherwise ``None``.
    """
    import pyvista as pv

    pv.set_plot_theme("document")
    plotter = pv.Plotter(
        shape=(1, 2),
        window_size=[2000, 900],
        title=f"Mesh comparison: {labels[0]} (left) vs {labels[1]} (right)",
    )

    for i, (src, label) in enumerate([(source_a, labels[0]), (source_b, labels[1])]):
        data = _resolve_mesh_data(src, collapse_to_parents=collapse_to_parents)
        plotter.subplot(0, i)
        plotter.add_text(label, position="upper_edge", font_size=28)
        _render_scene(plotter, data, **kwargs)
        _set_isometric_view(plotter)

    if notebook:
        return plotter
    plotter.show()
    return None


def plot_deformed_displacement_3d(
    source,
    displacements: dict,
    *,
    collapse_to_parents=False,
    scale: float = 10.0,
    show_undeformed: bool = True,
    shrink: float = 0.0,
    color_nodes: bool = True,
    colormap: str = "plasma",
    show_labels: bool = False,
    max_labels: int = 30,
    label_threshold: float = 0.01,
    label_unit: str = "mm",
    show_bounds: bool = True,
    camera: str = "iso",
    selection: Optional["Selection"] = None,
    save_screenshot: Optional[str] = None,
    screenshot_views: Optional[list] = None,
    notebook: bool = False,
    **kwargs,
) -> Optional[Any]:
    """Display a displaced shape with node-colouring by displacement magnitude.

    Unified replacement for the legacy static/RS deformed-shape viewers.
    Works with any data source (builder, AnalysisBuilder, or NPZ dict).

    Args:
        source: ``AnalysisBuilder``, or NPZ data dict.
        displacements: Dict mapping ``node_tag`` → ``(dx, dy, dz)`` in model
            length units (e.g. metres).  For static analyses, pass the
            ``nodal_displacements`` dict from ``run_static_analysis()``.
            For RS, pass the CQC-combined dict from
            ``compute_rs_nodal_displacements()``.
        scale: Displacement magnification factor.
        show_undeformed: Show the undeformed mesh in grey.
        shrink: Fraction to shrink frame lines toward their midpoint
            (0.0 = full length, 0.1 = 10 percent gap at each end).
        color_nodes: If True, colour node markers by resultant displacement.
        colormap: PyVista colormap name for node colouring.
        show_labels: If True, overlay displacement value labels on nodes.
        max_labels: Max number of labelled nodes (highest displacements).
        label_threshold: Minimum displacement (m) for a node to be labelled.
        label_unit: Display unit for labels (``"mm"`` or ``"m"``).
        show_bounds: Show axis bounds grid.
        camera: Camera position (``"iso"``, ``"xy"``, ``"xz"``, ``"yz"``).
        selection: Optional :class:`~fea_toolkit.model.selection.Selection`
            to restrict which frame elements are shown.  Only supported
            when *source* is a builder/AnalysisBuilder (requires a model
            object to resolve selection criteria).  ``None`` means all.
        save_screenshot: Path to save a screenshot (PNG).
        screenshot_views: List of camera positions for multiple screenshots,
            e.g. ``["iso", "xy"]``.  ``None`` means just *camera*.
        notebook: Return plotter for Jupyter embedding.
        **kwargs: Passed to ``pyvista.Plotter()``.

    Returns:
        ``pv.Plotter`` if *notebook*, else ``None``.

    Example::

        # Static analysis
        from fea_toolkit.opensees.preprocessor import preprocess_model
        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
        mm = preprocess_model(md)
        b = AnalysisBuilder(mm, ...)
        b.build_domain()
        b.create_loads({"DEAD": 1.0, "WIND": 1.0})
        results = b.run_static_analysis()
        plot_deformed_displacement_3d(b, results["nodal_displacements"],
                                       scale=20.0, show_labels=True)

        # RS analysis with selection
        sel = Selection(element_types=['Frame'], groups=['Moment Frame'])
        plot_deformed_displacement_3d(b, results["nodal_displacements"],
                                       scale=20.0, selection=sel)

        # RS analysis
        rs_disp = b.compute_rs_nodal_displacements(...)
        plot_deformed_displacement_3d(b, rs_disp, scale=50.0)
    """
    import math

    import numpy as np

    try:
        import pyvista as pv
    except ImportError:
        print("pyvista not installed — install with: pip install pyvista")
        return None

    # ── Resolve mesh data ──────────────────────────────────────────
    data = _resolve_mesh_data(source, collapse_to_parents=collapse_to_parents)
    nodes = data["nodes"]
    frames = data["frames"]

    # ── Apply selection filter (builder sources only) ─────────────
    if selection is not None and not isinstance(source, _NPZ_TYPES):
        try:
            sel_ids = set(selection.get_frame_ids(source.model))
            frames = [
                fr for fr in frames if fr.get("id") in sel_ids or str(fr.get("id")) in sel_ids
            ]
            data["frames"] = frames  # also used by _render_scene
        except AttributeError:
            print(
                "Warning: selection requires a builder/analysis-builder "
                "source with a .model attribute — ignoring selection."
            )

    if not displacements:
        print("No displacement data provided.")
        return None

    pv.set_plot_theme("document")
    plotter = pv.Plotter(notebook=notebook, **kwargs)

    # ── Undeformed mesh (greyed out) ──────────────────────────────
    if show_undeformed:
        _render_scene(
            plotter,
            data,
            show_nodes=False,
            show_shells=True,
            show_frames=True,
            show_constraints=False,
            shrink=shrink,
        )

    # ── Deformed frame lines (warm red) ───────────────────────────
    for fr in frames:
        ni = _resolve_frame_node(nodes, fr, "i")
        nj = _resolve_frame_node(nodes, fr, "j")
        if ni is None or nj is None:
            continue
        di = displacements.get(ni["tag"], (0, 0, 0))
        dj = displacements.get(nj["tag"], (0, 0, 0))
        p1 = np.array([ni["x"] + di[0] * scale, ni["y"] + di[1] * scale, ni["z"] + di[2] * scale])
        p2 = np.array([nj["x"] + dj[0] * scale, nj["y"] + dj[1] * scale, nj["z"] + dj[2] * scale])
        if shrink:
            m = (p1 + p2) / 2
            p1 = p1 + (m - p1) * shrink
            p2 = p2 + (m - p2) * shrink
        n_pts = max(2, int(np.linalg.norm(p2 - p1) * 2))
        pts = np.linspace(p1, p2, n_pts)
        poly = pv.lines_from_points(pts)
        plotter.add_mesh(poly, color="#c44e52", line_width=3)

    # ── Node colouring by displacement magnitude ──────────────────
    if color_nodes:
        node_pts = []
        node_disp = []
        seen_tags = set()
        for nid, nd in nodes.items():
            tag = nd.get("tag")
            if tag is not None and tag not in seen_tags:
                seen_tags.add(tag)
            else:
                continue
            d = displacements.get(nd["tag"], (0.0, 0.0, 0.0))
            mag = math.hypot(d[0], d[1], d[2])
            node_pts.append([nd["x"], nd["y"], nd["z"]])
            node_disp.append(mag)
        if node_pts:
            pts_arr = np.array(node_pts)
            disp_arr = np.array(node_disp)
            cloud = pv.PolyData(pts_arr)
            cloud["displacement (m)"] = disp_arr
            plotter.add_mesh(
                cloud,
                scalars="displacement (m)",
                cmap=colormap,
                point_size=10,
                render_points_as_spheres=True,
                scalar_bar_args={
                    "title": "Resultant\nDisplacement (m)",
                    "title_font_size": 12,
                    "label_font_size": 10,
                },
                clim=[0, max(disp_arr) * 1.1] if max(disp_arr) > 0 else None,
            )

    # ── Displacement labels ────────────────────────────────────────
    if show_labels:
        label_scale = 1000.0 if label_unit == "mm" else 1.0
        label_suffix = label_unit
        labeled = []
        seen_tags = set()
        for nid, nd in nodes.items():
            tag = nd.get("tag")
            if tag is not None and tag not in seen_tags:
                seen_tags.add(tag)
            else:
                continue
            d = displacements.get(nd["tag"], (0.0, 0.0, 0.0))
            mag = math.hypot(d[0], d[1], d[2])
            if mag > label_threshold:
                labeled.append((nd["x"], nd["y"], nd["z"], mag))
        labeled.sort(key=lambda t: -t[3])
        labeled = labeled[:max_labels]
        for x, y, z, d in labeled:
            val = round(d * label_scale)
            plotter.add_point_labels(
                np.array([[x, y, z]]),
                [f"{val}{label_suffix}"],
                point_size=0,
                font_size=10,
                text_color="black",
                shape="rounded_rect",
                shape_color="white",
                shape_opacity=0.8,
                always_visible=True,
            )

    # ── Axes, bounds, camera ──────────────────────────────────────
    if show_bounds:
        plotter.show_bounds(grid="back", location="outer", font_size=8, color="grey")
    plotter.add_axes(interactive=True, line_width=2, labels_off=False)

    _set_isometric_view(plotter)
    cam_map = {"iso": "iso", "xy": "xy", "xz": "xz", "yz": "yz"}
    cam_pos = cam_map.get(camera, "iso")
    if cam_pos == "iso":
        plotter.camera_position = "iso"
    else:
        plotter.camera_position = cam_pos
    plotter.camera.zoom(0.8)

    # ── Screenshot export ──────────────────────────────────────────
    views_to_capture = screenshot_views or ([camera] if save_screenshot else [])
    for v in views_to_capture:
        vcam = cam_map.get(v, "iso")
        if vcam == "iso":
            plotter.camera_position = "iso"
        else:
            plotter.camera_position = vcam
        plotter.camera.zoom(0.8)
        if save_screenshot:
            path = str(Path(save_screenshot))
            if len(views_to_capture) > 1:
                stem = Path(path).stem
                parent = Path(path).parent
                path = str(parent / f"{stem}_{v}.png")
            plotter.show(screenshot=path, auto_close=False)

    if notebook:
        return plotter
    if not save_screenshot:
        plotter.show()
    plotter.close()
    return None


def plot_mode_animation(
    source,
    mode_shapes,
    mode=0,
    *,
    collapse_to_parents=False,
    scale=30.0,
    show_original=True,
    shrink=0.0,
    animate=True,
    periods=None,
    font_size=14,
    anim_speed=2.0,
    anim_amplitude=1.5,
    selection=None,
    notebook=False,
    **kwargs,
):
    """Display / animate a mode shape from a builder or NPZ data.

    Works with either:

    * An ``AnalysisBuilder`` (built) + mode shapes
      from ``extract_mode_shapes()``.
    * An NPZ data dict (from ``np.load()``) + mode shapes from the
      ``modal/mode_dx``, ``modal/mode_dy``, ``modal/mode_dz`` arrays.

    When drawing shells, each SAP section type gets its own colour from a
    fixed palette, with a legend in the lower-right corner (model-specific
    colours, not section-index cycling — required for visualising shells
    by material/assignment in mixed models like the admin building).

    Args:
        source: Builder or NPZ dict.
        mode_shapes: Dict ``{mode_idx: {node_tag: (dx, dy, dz)}}`` from
            ``extract_mode_shapes()``, OR ``None`` to extract from NPZ.
        mode: 0‑based mode index.
        collapse_to_parents: Show unsplit parent elements (default ``False``).
        scale: Displacement magnification factor.
        show_original: Show undeformed model in grey.
        shrink: Fraction to shrink frame lines toward their midpoint
            (0.0 = full length, 0.1 = 10 percent gap at each end).
        animate: Oscillate amplitude sinusoidally.
        periods: List of modal periods (s).  The period for *mode* is shown.
        font_size: Display font size.
        anim_speed: Animation speed factor.
        anim_amplitude: Animation amplitude factor.
        selection: Optional Selection to filter elements (builder only).
        notebook: Return plotter for Jupyter.
        **kwargs: Passed to ``pyvista.Plotter()``.

    Returns:
        ``pv.Plotter`` if *notebook*, else ``None``.
    """
    import numpy as np

    try:
        import pyvista as pv
    except ImportError:
        print("pyvista not installed — install with: pip install pyvista")
        return None

    _SECTION_COLORS = [
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

    # Resolve mode shape displacements for this mode
    if isinstance(source, _NPZ_TYPES) and mode_shapes is None:
        # NPZ data — extract mode shapes from arrays
        dx = source.get("modal/mode_dx")
        dy = source.get("modal/mode_dy")
        dz = source.get("modal/mode_dz")
        if dx is None or mode >= dx.shape[1]:
            print(f"No mode shape data for mode {mode} in NPZ.")
            return None
        # Row alignment: the N_node × N_mode mode arrays are written in
        # SORTED node-tag order (see npz_writer._collect_modal), which does
        # not match the geometry ``node_tag`` array (MeshModel dict order).
        # Prefer the explicit ``modal/node_tag`` row-alignment array when
        # present; fall back to the geometry node_tag for legacy NPZ files
        # where the two orders happened to coincide.
        tags = list(source.get("modal/node_tag", source.get("node_tag", [])))
        mode_shapes = {
            mode: {
                int(tags[i]): (float(dx[i, mode]), float(dy[i, mode]), float(dz[i, mode]))
                for i in range(len(tags))
            }
        }
    elif isinstance(source, _NPZ_TYPES):
        # NPZ data with pre-extracted mode_shapes — resolve tags
        pass

    if mode not in mode_shapes or not mode_shapes.get(mode):
        print(f"No mode shape data for mode {mode}.")
        return None

    disp = mode_shapes[mode]

    # Resolve mesh geometry into common format
    data = _resolve_mesh_data(source, collapse_to_parents=collapse_to_parents)

    # ── Separate inactive (parent) shells from active shells ──
    # Also group active shells by section name for per-section colouring.
    inactive_shell_quads: list = []
    shell_groups: dict = {}
    for sh in data["shells"]:
        npts = []
        ds = []
        for ref in sh.get("node_ids") or sh.get("node_tags") or []:
            # Nodes dict is dual-keyed (SAP ID string + int tag)
            nd = data["nodes"].get(ref)
            if nd is None:
                break
            npts.append(np.array([nd["x"], nd["y"], nd["z"]]))
            ds.append(np.array(disp.get(nd["tag"], (0, 0, 0))))
        if len(npts) == 4:
            if sh.get("inactive"):
                inactive_shell_quads.append(npts)
            else:
                sec = sh.get("sec", "unknown")
                shell_groups.setdefault(sec, []).append(tuple(npts) + tuple(ds))

    # Build frame segments
    segments = []
    seg_npoints = []
    for fr in data["frames"]:
        ni = _resolve_frame_node(data["nodes"], fr, "i")
        nj = _resolve_frame_node(data["nodes"], fr, "j")
        if ni is None or nj is None:
            continue
        p1 = np.array([ni["x"], ni["y"], ni["z"]])
        p2 = np.array([nj["x"], nj["y"], nj["z"]])
        if shrink:
            m = (p1 + p2) / 2
            p1 = p1 + (m - p1) * shrink
            p2 = p2 + (m - p2) * shrink
        di = np.array(disp.get(ni["tag"], (0, 0, 0)))
        dj = np.array(disp.get(nj["tag"], (0, 0, 0)))
        segments.append((p1, p2, di, dj))
        seg_npoints.append(max(2, int(np.linalg.norm(p2 - p1) * 2)))

    # Flatten shell groups for deformed mesh builder, tracking section index
    all_quads = []
    all_sec_idxs = []
    sec_names_sorted = sorted(shell_groups.keys())
    sec_name_to_idx = {name: i for i, name in enumerate(sec_names_sorted)}
    for sec_name, quads in shell_groups.items():
        sidx = sec_name_to_idx[sec_name]
        for quad in quads:
            all_quads.append(quad)
            all_sec_idxs.append(sidx)
    n_sections = len(sec_names_sorted)

    pv.set_plot_theme("document")
    plotter = pv.Plotter(notebook=notebook, **kwargs)

    # ── Undeformed shells ──
    if show_original:
        # Inactive (parent) quad overlays — shrink consistently with active shells
        if inactive_shell_quads:
            for quad_pts_raw in inactive_shell_quads:
                if shrink:
                    arr = np.array(quad_pts_raw)
                    c = np.mean(arr, axis=0)
                    quad_pts = arr + (c - arr) * shrink
                else:
                    quad_pts = quad_pts_raw
                face = pv.PolyData(quad_pts, faces=[4, 0, 1, 2, 3])
                plotter.add_mesh(
                    face,
                    color="lightgrey",
                    opacity=0.12,
                    show_edges=True,
                    edge_color="grey",
                    line_width=0.5,
                )
        # Undeformed active shells at amp=0
        if all_quads:
            _, und_shells = _build_deformed_mesh(
                segments, seg_npoints, all_quads, all_sec_idxs, scale, 0.0, shrink=shrink
            )
            if und_shells is not None and und_shells.n_points:
                plotter.add_mesh(
                    und_shells, color="lightgrey", opacity=0.3, show_edges=True, line_width=1
                )

    # ── Undeformed frames ──
    if show_original:
        for p1, p2, _, _ in segments:
            n = max(2, int(np.linalg.norm(p2 - p1) * 2))
            poly = pv.lines_from_points(np.linspace(p1, p2, n))
            plotter.add_mesh(poly, color="#999999", line_width=1, opacity=0.5)

    # ── Deformed mesh (amp=1.0 for static, overridden during animation) ──
    frame_mesh, shell_mesh = _build_deformed_mesh(
        segments, seg_npoints, all_quads, all_sec_idxs, scale, 1.0, shrink=shrink
    )

    # Shells: per-section colours via cell scalars
    if shell_mesh is not None and shell_mesh.n_points:
        if n_sections > 0:
            plotter.add_mesh(
                shell_mesh,
                scalars="section_idx",
                cmap=_SECTION_COLORS[:n_sections],
                show_edges=True,
                edge_color="#333333",
                opacity=0.85,
                clim=[-0.5, n_sections - 0.5],
            )
        else:
            plotter.add_mesh(
                shell_mesh, color="#4c72b0", show_edges=True, edge_color="#333333", opacity=0.85
            )

    # Frames: dark grey (distinct from shell colours)
    if frame_mesh is not None and frame_mesh.n_points:
        plotter.add_mesh(frame_mesh, color="#555555", line_width=3, opacity=0.8)

    # ── Section legend ──
    if n_sections > 1:
        legend_entries = [
            (name, pv.Color(_SECTION_COLORS[i % len(_SECTION_COLORS)]))
            for i, name in enumerate(sec_names_sorted)
        ]
        try:
            # Newer PyVista supports label_size; older versions don't
            plotter.add_legend(
                legend_entries,
                border=True,
                size=[0.2, 0.12],
                loc="lower right",
                face="rectangle",
                label_size=max(8, 14 - n_sections),
            )
        except TypeError:
            # Fallback for older PyVista without label_size kwarg
            plotter.add_legend(
                legend_entries,
                border=True,
                size=[0.2, 0.12],
                loc="lower right",
            )

    # ── Title ──
    period_str = ""
    if periods is not None and mode < len(periods):
        period_str = f"  T = {periods[mode]:.4f} s"
    plotter.add_text(f"Mode {mode + 1}  {period_str}", position="upper_edge", font_size=font_size)
    plotter.show_grid()
    _set_isometric_view(plotter)

    # ── Animation ──
    if animate:
        import math as _math

        def callback(step):
            amp = _math.sin(anim_speed * 2.0 * _math.pi * step / 60.0) * anim_amplitude
            nfm, nsm = _build_deformed_mesh(
                segments, seg_npoints, all_quads, all_sec_idxs, scale, amp, shrink=shrink
            )
            if nfm is not None and nfm.n_points and frame_mesh.n_points:
                frame_mesh.points = nfm.points
            if shell_mesh is not None and nsm is not None and nsm.n_points:
                shell_mesh.points = nsm.points

        _add_animation_timer(
            plotter,
            callback,
            max_steps=3600,
            interval_ms=17,
        )
        plotter.show(auto_close=False)
    else:
        plotter.show()

    if notebook:
        return plotter
    return None


def plot_building_views(md, mesh_model=None, window_size=(1200, 900)):
    """Return a 2×2 matplotlib figure with plan, two elevations, isometric.

    Uses the two-stage path when *mesh_model* is provided; otherwise the
    model data is used directly without a builder.

    Parameters
    ----------
    md : SAPModelData
        Parsed model data.
    mesh_model : optional
        Preprocessed mesh model for two-stage path.
    window_size : tuple
        PyVista window dimensions (width, height) in pixels.

    Returns
    -------
    matplotlib.figure.Figure or None
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    try:
        import pyvista as pv
    except ImportError:
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.text(
            0.5,
            0.5,
            "Building views: PyVista not available",
            ha="center",
            va="center",
            transform=ax.transAxes,
            fontsize=12,
        )
        ax.axis("off")
        return fig

    _ab_views = None
    try:
        if mesh_model is not None:
            from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

            _ab_views = AnalysisBuilder(mesh_model, {"verbose": False})
            _ab_views.build_domain()
        else:
            from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
            from fea_toolkit.opensees.preprocessor import preprocess_model

            _mm = preprocess_model(
                md, {"element_type": "elasticBeamColumn", "split_elements": True, "verbose": False}
            )
            _ab_views = AnalysisBuilder(_mm, {"verbose": False})
            _ab_views.build_domain()
    except Exception:
        import warnings

        warnings.warn("Could not build model for building views.")
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.text(
            0.5,
            0.5,
            "Building views: model build failed",
            ha="center",
            va="center",
            transform=ax.transAxes,
            fontsize=12,
        )
        ax.axis("off")
        return fig

    views = ["Plan (XY)", "Elevation (XZ)", "Elevation (YZ)", "Isometric"]
    fig, axes = plt.subplots(2, 2, figsize=(14, 13))
    axes_flat = axes.flatten()
    pv.OFF_SCREEN = True

    xs = [n.x for n in md.nodes.values()]
    ys = [n.y for n in md.nodes.values()]
    zs = [n.z for n in md.nodes.values()]
    x_c, y_c, z_c = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2, (max(zs) + min(zs)) / 2
    w_aspect = window_size[0] / window_size[1]

    for ax, title in zip(axes_flat, views):
        try:
            pl = plot_mesh(
                _ab_views,
                notebook=True,
                show_nodes=False,
                show_frames=True,
                show_shells=True,
            )
        except Exception:
            pl = None
        if pl is not None:
            try:
                pl.camera.parallel_projection = True
                if title == "Plan (XY)":
                    pl.view_xy()

                    def uv_map(x, y, z):
                        return (x, y)
                elif title == "Elevation (XZ)":
                    pl.view_xz()

                    def uv_map(x, y, z):
                        return (x, z)
                elif title == "Elevation (YZ)":
                    pl.view_yz()

                    def uv_map(x, y, z):
                        return (y, z)
                else:
                    pl.view_isometric()
                    inv_sqrt2 = 1.0 / math.sqrt(2)
                    inv_sqrt6 = 1.0 / math.sqrt(6)

                    def uv_map(x, y, z, inv_sqrt2=inv_sqrt2, inv_sqrt6=inv_sqrt6):
                        return ((-x + y) * inv_sqrt2, (-x - y + 2 * z) * inv_sqrt6)

                u_vals, v_vals = [], []
                for x in (min(xs), max(xs)):
                    for y in (min(ys), max(ys)):
                        for z in (min(zs), max(zs)):
                            u, v = uv_map(x, y, z)
                            u_vals.append(u)
                            v_vals.append(v)
                u_span = max(u_vals) - min(u_vals)
                v_span = max(v_vals) - min(v_vals)
                pl.camera.parallel_scale = max(v_span / 2, u_span / (2 * w_aspect)) * 1.1
                pl.camera.focal_point = (x_c, y_c, z_c)
                pl.render()
                img = pl.screenshot(return_img=True, window_size=window_size)
            except Exception:
                img = np.zeros((100, 100, 3))
            pl.close()
            ax.imshow(img)
        ax.set_title(title, fontsize=11, fontweight="bold")
        ax.axis("off")

    fig.suptitle("Structural Model Views", fontsize=13, fontweight="bold")
    fig.tight_layout()
    fig.canvas.draw()
    return fig


def plot_model_comparison(
    md,
    mesh_model=None,
    out_dir=None,
    off_screen=True,
    LOADS_ONLY=None,
):
    """Open an interactive PyVista viewer (or save screenshots) comparing
    the original model geometry with the split/meshed model.

    When *mesh_model* is provided, uses it directly (no re-build needed).
    Otherwise falls back to the two-stage build path.
    """
    import copy

    from fea_toolkit.model.sap_data import Restraint
    from fea_toolkit.model.selection import Selection
    from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
    from fea_toolkit.opensees.preprocessor import preprocess_model

    if LOADS_ONLY is None:
        LOADS_ONLY = set()

    md_copy = copy.deepcopy(md)

    # Fix shell-only base nodes
    min_z = min(nd.z for nd in md_copy.nodes.values())
    base_ids = {nd.node_id for nd in md_copy.nodes.values() if nd.z == min_z}
    frame_conn: set = set()
    for e in md_copy.frame_elements.values():
        if e.node_i in base_ids:
            frame_conn.add(e.node_i)
        if e.node_j in base_ids:
            frame_conn.add(e.node_j)
    for nid in sorted(base_ids - frame_conn):
        if nid in md_copy.restraints:
            md_copy.restraints[nid] = Restraint([1, 1, 1, 1, 1, 1])

    for sn in LOADS_ONLY:
        if sn in md_copy.sections:
            s = md_copy.sections[sn]
            for attr in ("A", "I33", "I22", "J"):
                setattr(s, attr, getattr(s, attr) * 0.01)

    if mesh_model is not None:
        _mm = mesh_model
        # Build domain from existing MeshModel
        builder = AnalysisBuilder(
            _mm,
            {
                "element_type": "elasticBeamColumn",
                "verbose": False,
            },
        )
        builder.build_domain()
    else:
        Selection(sections=list(LOADS_ONLY), element_types=["Area"])
        _mm = preprocess_model(
            md_copy,
            {
                "element_type": "elasticBeamColumn",
                "split_elements": True,
                "create_shells": True,
                "verbose": False,
            },
        )
        builder = AnalysisBuilder(
            _mm,
            {
                "element_type": "elasticBeamColumn",
                "verbose": False,
            },
        )
        builder.build_domain()

    import openseespy.opensees as ops

    ops.wipe()

    mm = getattr(builder, "mesh_model", None)
    if off_screen:
        return _save_comparison_images(md, builder, LOADS_ONLY, out_dir, mesh_model=mm)
    _run_interactive_viewer(md, builder, LOADS_ONLY, mesh_model=mm)


def _save_comparison_images(md, builder, LOADS_ONLY, out_dir, mesh_model=None):
    """Save PNG screenshots of original and meshed views."""
    from pathlib import Path

    import pyvista as pv

    pv.set_plot_theme("document")
    pv.OFF_SCREEN = True
    out_path = Path(out_dir).resolve() if out_dir else Path.cwd().resolve()
    out_path.mkdir(parents=True, exist_ok=True)

    orig_png = str(out_path / "model_original.png")
    mesh_png = str(out_path / "model_meshed.png")

    plotter = pv.Plotter(off_screen=True, window_size=[1400, 900])
    _add_original_geometry(plotter, md)
    plotter.view_isometric()
    plotter.show(auto_close=False)
    plotter.screenshot(orig_png)
    plotter.close()

    plotter = pv.Plotter(off_screen=True, window_size=[1400, 900])
    _add_meshed_geometry(
        plotter, md, builder, LOADS_ONLY, mesh_model=getattr(builder, "mesh_model", None)
    )
    plotter.view_isometric()
    plotter.show(auto_close=False)
    plotter.screenshot(mesh_png)
    plotter.close()

    print(f"  Model comparison saved: {orig_png}, {mesh_png}")
    return {"orig_png": orig_png, "mesh_png": mesh_png}


def _add_original_geometry(plotter, md):
    """Add original (unsplit) geometry actors to a plotter."""
    import pyvista as pv

    orig_nodes = dict(md.nodes.items())
    orig_frames = dict(md.frame_elements.items())
    orig_areas = dict(md.area_elements.items())

    FRAME_SHRINK = 0.9
    pts, lines = [], []
    off = 0
    for eid, elem in orig_frames.items():
        ni = orig_nodes.get(elem.node_i)
        nj = orig_nodes.get(elem.node_j)
        if ni is None or nj is None:
            continue
        a = np.array([ni.x, ni.y, ni.z])
        b = np.array([nj.x, nj.y, nj.z])
        mid = (a + b) * 0.5
        a = mid + FRAME_SHRINK * (a - mid)
        b = mid + FRAME_SHRINK * (b - mid)
        pts.append(a.tolist())
        pts.append(b.tolist())
        lines.append([2, off, off + 1])
        off += 2
    if pts:
        plotter.add_mesh(
            pv.PolyData(np.array(pts), lines=np.array(lines, dtype=int)),
            color="#4c72b0",
            line_width=4,
            opacity=0.85,
        )

    all_verts, all_faces, cell_cols = [], [], []
    off = 0
    for aid, area in orig_areas.items():
        if len(area.node_ids) < 3:
            continue
        verts = []
        for nid in area.node_ids:
            nd = orig_nodes.get(nid)
            if nd is None:
                break
            verts.append([nd.x, nd.y, nd.z])
        if len(verts) < 3:
            continue
        n = len(verts)
        arr = np.array(verts)
        centroid = arr.mean(axis=0)
        arr = centroid + 0.9 * (arr - centroid)
        all_verts.extend(arr.tolist())
        for i in range(1, n - 1):
            all_faces.append([3, off, off + i, off + i + 1])
            cell_cols.append((0.29, 0.44, 0.69))
        off += n
    if all_verts:
        m = pv.PolyData(np.array(all_verts), faces=np.array(all_faces, dtype=int).ravel())
        m.cell_data["rgb"] = np.array(cell_cols)
        plotter.add_mesh(m, scalars="rgb", rgb=True, opacity=0.35, lighting=False, show_edges=False)

    node_pts = np.array([[nd.x, nd.y, nd.z] for nd in orig_nodes.values()])
    plotter.add_mesh(
        pv.PolyData(node_pts),
        color="#333333",
        point_size=12,
        style="points",
        render_points_as_spheres=True,
    )


def _add_meshed_geometry(plotter, md, builder, LOADS_ONLY, mesh_model=None):
    """Add meshed (split frames + shells) geometry actors to a plotter."""
    import pyvista as pv

    mm = mesh_model or getattr(builder, "mesh_model", None)
    if mm is not None:
        mesh_frames = mm.frame_elements
        mesh_coords = {nid: (nd.x, nd.y, nd.z) for nid, nd in mm.nodes.items()}
    else:
        mesh_frames = builder.split_elements or builder.model.frame_elements
        mesh_coords = {nid: (nd.x, nd.y, nd.z) for nid, nd in builder.model.nodes.items()}

    FRAME_SHRINK = 0.9

    for color, is_split in [("#3a5588", False), ("#dd8452", True)]:
        pts, lines = [], []
        off = 0
        for eid, elem in mesh_frames.items():
            if getattr(elem, "inactive", False):
                continue
            pid = getattr(elem, "parent_id", None)
            if (pid is not None) != is_split:
                continue
            ci = mesh_coords.get(elem.node_i)
            cj = mesh_coords.get(elem.node_j)
            if ci is None or cj is None:
                continue
            a = np.array(ci)
            b = np.array(cj)
            mid = (a + b) * 0.5
            a = mid + FRAME_SHRINK * (a - mid)
            b = mid + FRAME_SHRINK * (b - mid)
            pts.append(a.tolist())
            pts.append(b.tolist())
            lines.append([2, off, off + 1])
            off += 2
        if pts:
            plotter.add_mesh(
                pv.PolyData(np.array(pts), lines=np.array(lines, dtype=int)),
                color=color,
                line_width=4,
                opacity=0.85,
            )

    all_verts, all_faces, cell_cols = [], [], []
    off = 0
    _area_elems = mm.area_elements if mm is not None else builder.model.area_elements
    _area_asgn = mm.area_assignments if mm is not None else builder.model.area_assignments
    for aid, area in _area_elems.items():
        if getattr(area, "inactive", False):
            continue
        sec_name = _area_asgn.get(aid, "")
        if sec_name in LOADS_ONLY:
            continue
        if len(area.node_ids) < 3:
            continue
        verts = []
        for nid in area.node_ids:
            nd = mm.nodes.get(nid) if mm is not None else builder.model.nodes.get(nid)
            if nd is None:
                break
            verts.append([nd.x, nd.y, nd.z])
        if len(verts) < 3:
            continue
        n = len(verts)
        arr = np.array(verts)
        centroid = arr.mean(axis=0)
        arr = centroid + 0.9 * (arr - centroid)
        all_verts.extend(arr.tolist())
        c = (0.20, 0.31, 0.48)
        for i in range(1, n - 1):
            all_faces.append([3, off, off + i, off + i + 1])
            cell_cols.append(c)
        off += n
    if all_verts:
        m = pv.PolyData(np.array(all_verts), faces=np.array(all_faces, dtype=int).ravel())
        m.cell_data["rgb"] = np.array(cell_cols)
        plotter.add_mesh(m, scalars="rgb", rgb=True, opacity=0.45, lighting=False, show_edges=False)

    _model_nodes = mm.nodes if mm is not None else builder.model.nodes
    node_pts = np.array([[nd.x, nd.y, nd.z] for nd in _model_nodes.values()])
    plotter.add_mesh(
        pv.PolyData(node_pts),
        color="#333333",
        point_size=10,
        style="points",
        render_points_as_spheres=True,
    )
    orig_ids = set(md.nodes.keys())
    split_ids = set(_model_nodes.keys()) - orig_ids
    if split_ids:
        split_pts = np.array([list(mesh_coords[t]) for t in split_ids if t in mesh_coords])
        if len(split_pts):
            plotter.add_mesh(
                pv.PolyData(split_pts),
                color="#ff8c00",
                point_size=20,
                style="points",
                render_points_as_spheres=True,
            )


def _run_interactive_viewer(md, builder, LOADS_ONLY, mesh_model=None):
    """Open an interactive PyVista window with original/meshed toggle."""
    import pyvista as pv

    pv.set_plot_theme("document")
    plotter = pv.Plotter(window_size=[1400, 900])
    _add_original_geometry(plotter, md)
    _add_meshed_geometry(plotter, md, builder, LOADS_ONLY, mesh_model=mesh_model)
    plotter.set_background("white")
    plotter.view_isometric()
    plotter.show()
