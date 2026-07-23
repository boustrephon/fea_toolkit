"""Pre-build connectivity and model checks on parsed SAP2000 data.

These functions work on ``SAPModelData`` directly and do NOT require
an OpenSees domain or builder.  They are used by the report pipeline
to detect model issues before running any analysis.
"""

from collections import defaultdict
from typing import Any, Dict

from fea_toolkit.model.sap_data import SAPModelData, ShellSection


def check_model_connectivity(
    md: SAPModelData,
    tol: float = 1e-6,
) -> Dict[str, Any]:
    """Pre-build connectivity check on parsed SAP2000 data.

    Call **before** creating a builder to detect model issues that
    will cause a singular stiffness matrix.

    Parameters
    ----------
    md : SAPModelData
        Parsed model data from ``parser.get_model_data()``.
    tol : float
        Coordinate tolerance for duplicate-node detection.

    Returns
    -------
    dict
        Keys ``orphan_nodes``, ``shell_only_base_nodes``,
        ``duplicate_coords``, ``zero_area_sections``, ``summary``.
    """
    report: Dict[str, Any] = {}

    # ── Orphan nodes ───────────────────────────────────────────────
    frame_nodes: set = set()
    area_nodes: set = set()
    for e in md.frame_elements.values():
        frame_nodes.add(e.node_i)
        frame_nodes.add(e.node_j)
    for a in md.area_elements.values():
        area_nodes.update(a.node_ids)
    all_connected = frame_nodes | area_nodes
    orphans = [
        {"node_id": nid, "coord": (nd.x, nd.y, nd.z)}
        for nid, nd in md.nodes.items()
        if nid not in all_connected
    ]
    report["orphan_nodes"] = orphans

    # ── Shell-only base nodes ─────────────────────────────────────
    shell_only: list = []
    if md.nodes:
        min_z = min(nd.z for nd in md.nodes.values())
        base_ids = {
            nid for nid, nd in md.nodes.items()
            if abs(nd.z - min_z) < 0.01
        }
        base_frame_conn = set()
        for e in md.frame_elements.values():
            if e.node_i in base_ids:
                base_frame_conn.add(e.node_i)
            if e.node_j in base_ids:
                base_frame_conn.add(e.node_j)
        shell_only = [
            {
                "node_id": nid,
                "restraint": str(md.restraints.get(nid, "none")),
                "coord": (md.nodes[nid].x, md.nodes[nid].y, md.nodes[nid].z),
            }
            for nid in sorted(base_ids - base_frame_conn)
            if nid in area_nodes and nid not in base_frame_conn
        ]
        report["shell_only_base_nodes"] = shell_only
    else:
        report["shell_only_base_nodes"] = []

    # ── Duplicate coordinate nodes ─────────────────────────────────
    coord_map: Dict[tuple, list] = defaultdict(list)
    for nid, nd in md.nodes.items():
        key = (round(nd.x / tol), round(nd.y / tol), round(nd.z / tol))
        coord_map[key].append(nid)
    dupes = [
        {"coord": (k[0] * tol, k[1] * tol, k[2] * tol), "node_ids": v}
        for k, v in coord_map.items() if len(v) > 1
    ]
    report["duplicate_coords"] = dupes

    # ── Zero-area sections ─────────────────────────────────────────
    zero_secs = []
    for sn, s in md.sections.items():
        if isinstance(s, ShellSection):
            if s.thickness <= 0:
                zero_secs.append({
                    "name": sn, "type": "ShellSection",
                    "thickness": s.thickness,
                })
        elif getattr(s, "A", 1) == 0.0:
            zero_secs.append({
                "name": sn, "type": type(s).__name__,
                "thickness": getattr(s, "thickness", 0),
            })
    report["zero_area_sections"] = zero_secs

    # ── Summary ────────────────────────────────────────────────────
    report["summary"] = (
        f"Nodes: {len(md.nodes)} | Frames: {len(md.frame_elements)} | "
        f"Areas: {len(md.area_elements)} | "
        f"Orphans: {len(orphans)} | "
        f"Shell-only base: {len(shell_only)} | "
        f"Duplicate coords: {len(dupes)} | "
        f"Zero-area sections: {len(zero_secs)}"
    )
    return report
