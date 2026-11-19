#!/usr/bin/env python
"""Build the portal-frame demo model and dump it as model-codec JSON.

This is the *authoring* half of the two-artifact workflow: it constructs the
``SAPModelData`` (nodes, restraints, steel, I-sections, the WX wind load case)
and serialises it with :func:`fea_toolkit.io.model_codec.model_to_json`.  The
*solve* half is the recipe runner, which consumes that JSON like any other
model file::

    python -m fea_toolkit run solve.json --model portal_frame_model.json

The model is a planar 3D frame in the X–Z plane (the toolkit's 3D-only engine
with out-of-plane restraints): four column lines, three levels, pinned bases,
GB hot-rolled H-beam sections (Q355B / Q235B), and a +X wind line load on the
windward columns (WX).  Units: kN–m.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from fea_toolkit.io.model_codec import model_to_json
from fea_toolkit.model.sap_data import (
    FrameDistributedLoad,
    FrameElement,
    ISection,
    LoadCase,
    LoadPattern,
    Material,
    Node,
    Restraint,
    SAPModelData,
)

# ═══════════════════════════════════════════════════════════════════
# Model definition (data)
# ═══════════════════════════════════════════════════════════════════

# Model units: force = kN, length = m, temperature = C.
UNITS: dict[str, str] = {"F": "KN", "L": "m", "T": "C"}

# Grid coordinates (metres), indexed [x][z] below.
X_GRID: list[float] = [0.0, 2.25, 14.535, 16.91]
Z_GRID: list[float] = [0.0, 2.525, 6.051]

# Steel grades — moduli/strengths authored in model stress units (kPa = kN/m²).
# E = 206 GPa; G = E / (2 (1 + ν)); fy: Q355B = 355 MPa, Q235B = 235 MPa.
_STEEL: dict[str, dict[str, float]] = {
    "Q355B": {"E_mod": 2.06e8, "G_mod": 7.923e7, "nu": 0.3, "Fy": 3.55e5, "unit_weight": 77.0},
    "Q235B": {"E_mod": 2.06e8, "G_mod": 7.923e7, "nu": 0.3, "Fy": 2.35e5, "unit_weight": 77.0},
}

# Sections — GB hot-rolled H-beams, ``H × B × tw × tf`` in mm.
#   HW = wide flange, HM = medium flange (GB/T 11263).
_SECTIONS: dict[str, tuple[str, tuple[float, float, float, float]]] = {
    "SB3": ("Q355B", (200, 200, 8, 12)),  # HW200x200x8x12
    "SB6": ("Q235B", (340, 250, 9, 14)),  # HM340x250x9x14
    "SC4": ("Q355B", (300, 300, 10, 15)),  # HW300x300x10x15
    "SC5": ("Q355B", (294, 200, 8, 12)),  # HM294x200x8x12
}

# Members: (elem_id, section, (xi, zi), (xj, zj), line load kN/m in +X or None).
_MEMBERS: list[tuple[str, str, tuple[int, int], tuple[int, int], float | None]] = [
    ("C00-01", "SC5", (0, 0), (0, 1), 0.5),
    ("B01-11", "SB3", (0, 1), (1, 1), None),
    ("C10-11", "SC4", (1, 0), (1, 1), 0.5),
    ("C11-12", "SC4", (1, 1), (1, 2), 15.0),
    ("B12-22", "SB6", (1, 2), (2, 2), None),
    ("C20-21", "SC4", (2, 0), (2, 1), 0.5),
    ("C21-22", "SC4", (2, 1), (2, 2), None),
    ("C30-31", "SC5", (3, 0), (3, 1), 0.5),
    ("B21-31", "SB3", (2, 1), (3, 1), None),
]

# Pinned (out-of-plane-restrained) base nodes.
_BASE_NODES: set[tuple[int, int]] = {(0, 0), (1, 0), (2, 0), (3, 0)}

# Restraint DOF order: [UX, UY, UZ, RX, RY, RZ].
_PIN_DOFS = [1, 1, 1, 1, 0, 1]  # base: translations fixed, in-plane RY free
_OOP_DOFS = [0, 1, 0, 1, 0, 1]  # interior: out-of-plane (UY, RX, RZ) only


def _node_id(xi: int, zi: int) -> str:
    """Stable node label from grid indices, e.g. ``N11`` = (x=1, z=1)."""
    return f"N{xi}{zi}"


def _isection_props(
    d: float, bf: float, tf: float, tw: float
) -> tuple[float, float, float, float, float, float]:
    """Return ``(A, I33, I22, J, Z33, Z22)`` for a symmetric I-section (lengths in m)."""
    hw = d - 2.0 * tf  # web height
    A = 2.0 * bf * tf + hw * tw
    I33 = bf * d**3 / 12.0 - (bf - tw) * hw**3 / 12.0  # strong (major) axis
    I22 = 2.0 * tf * bf**3 / 12.0 + hw * tw**3 / 12.0  # weak (minor) axis
    J = (2.0 * bf * tf**3 + (d - tf) * tw**3) / 3.0  # St. Venant torsion
    # Plastic section moduli about the plastic neutral axis (doubly-symmetric I).
    Z33 = bf * tf * (d - tf) + tw * hw**2 / 4.0  # strong (major) axis
    Z22 = bf**2 * tf / 2.0 + hw * tw**2 / 4.0  # weak (minor) axis
    return A, I33, I22, J, Z33, Z22


def build_portal_frame_model() -> SAPModelData:
    """Assemble the portal frame as a :class:`SAPModelData` instance."""
    # ── Nodes — the 10 grid points actually connected by members ──
    node_indices: set[tuple[int, int]] = set()
    for _, _, (xi, zi), (xj, zj), _ in _MEMBERS:
        node_indices.add((xi, zi))
        node_indices.add((xj, zj))

    nodes: dict[str, Node] = {}
    for tag, (xi, zi) in enumerate(sorted(node_indices), start=1):
        nid = _node_id(xi, zi)
        nodes[nid] = Node(node_id=nid, node_tag=tag, x=X_GRID[xi], y=0.0, z=Z_GRID[zi])

    # ── Restraints ──
    restraints: dict[str, Restraint] = {}
    for xi, zi in node_indices:
        dofs = _PIN_DOFS if (xi, zi) in _BASE_NODES else _OOP_DOFS
        restraints[_node_id(xi, zi)] = Restraint(list(dofs))

    # ── Materials ──
    materials = {name: Material(name=name, type="Steel", **props) for name, props in _STEEL.items()}

    # ── Sections ──
    sections: dict[str, ISection] = {}
    for name, (steel, (d, bf, tw, tf)) in _SECTIONS.items():
        d_m, bf_m, tf_m, tw_m = d / 1000.0, bf / 1000.0, tf / 1000.0, tw / 1000.0
        A, I33, I22, J, Z33, Z22 = _isection_props(d_m, bf_m, tf_m, tw_m)
        sections[name] = ISection(
            name=name,
            shape="I/Wide Flange",
            material=steel,
            A=A,
            I33=I33,
            I22=I22,
            J=J,
            Z33=Z33,
            Z22=Z22,
            depth=d_m,
            bf=bf_m,
            tf=tf_m,
            tw=tw_m,
        )

    # ── Frame elements ──
    frame_elements: dict[str, FrameElement] = {}
    frame_assignments: dict[str, str] = {}
    for tag, (eid, sec, (xi, zi), (xj, zj), _) in enumerate(_MEMBERS, start=1):
        frame_elements[eid] = FrameElement(
            elem_id=eid,
            elem_tag=tag,
            node_i=_node_id(xi, zi),
            node_j=_node_id(xj, zj),
        )
        frame_assignments[eid] = sec

    # ── Load pattern + static case: WX (unfactored wind in +X) ──
    load_patterns = {
        "WX": LoadPattern(name="WX", pattern_type="Wind", self_weight_factor=0.0),
    }
    load_cases = {
        "WX": LoadCase(
            case_name="WX",
            case_type="LinStatic",
            design_type_option="",
            design_type="WIND",
            design_action_option="",
            design_action="Non-Composite",
            initial_condition="Zero",
            case_data={"CASE - STATIC 1 - LOAD ASSIGNMENTS": [{"LoadName": "WX", "LoadSF": 1.0}]},
        ),
    }

    # ── Distributed line loads (global +X) on the windward columns ──
    frame_dist_loads: list[FrameDistributedLoad] = []
    for eid, _sec, (xi, zi), (xj, zj), w in _MEMBERS:
        if w is None:
            continue
        length = abs(X_GRID[xj] - X_GRID[xi]) + abs(Z_GRID[zj] - Z_GRID[zi])
        frame_dist_loads.append(
            FrameDistributedLoad(
                pattern="WX",
                frame_id=eid,
                direction="X",
                load_type="Force",
                shape="Uniform",
                val_a=w,
                val_b=w,
                rdist_a=0.0,
                rdist_b=1.0,
                dist_a=0.0,
                dist_b=length,
            )
        )

    md = SAPModelData(
        nodes=nodes,
        restraints=restraints,
        materials=materials,
        sections=sections,
        frame_elements=frame_elements,
        area_elements={},
        frame_assignments=frame_assignments,
        area_assignments={},
        groups={},
        frame_auto_mesh={},
        load_patterns=load_patterns,
        load_cases=load_cases,
        frame_dist_loads=frame_dist_loads,
        units=UNITS,
    )
    md.apply_material_defaults()
    return md


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the portal-frame demo model and dump it as model-codec JSON."
    )
    parser.add_argument(
        "--out",
        default=str(Path(__file__).parent / "output" / "portal_frame_model.json"),
        help="Output .json path (default: examples/output/portal_frame_model.json).",
    )
    args = parser.parse_args(argv)

    md = build_portal_frame_model()
    target = Path(args.out).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(model_to_json(md), encoding="utf-8")
    print(f"Model JSON written: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
