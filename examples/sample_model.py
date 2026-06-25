"""Simple cantilever column model for use as a --sample fallback.

Builds a ``SAPModelData`` instance programmatically (no .s2k file needed).
The model is a 10 m steel cantilever with I‑section, suitable for quick
testing of static, modal, pushover, and response‑spectrum examples.

Usage::

    from examples.sample_model import make_sample_model
    md = make_sample_model()
"""

from fea_toolkit.model.sap_data import (
    SAPModelData, Node, Restraint, Material, Section,
    FrameElement, LoadPattern, FrameDistributedLoad,
    MassSource,
)


def make_sample_model() -> SAPModelData:
    """Build a simple 10 m steel cantilever column with gravity + lateral loads.

    Returns:
        SAPModelData ready to be passed to ``OpenSeesBuilder``.
    """
    # ── Nodes ──
    nodes = {
        "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
        "2": Node(node_id="2", node_tag=2, x=0.0, y=0.0, z=10.0),
    }
    # ── Restraint ──
    restraints = {"1": Restraint([1, 1, 1, 1, 1, 1])}
    # ── Material ──
    materials = {
        "Steel": Material(
            name="Steel", type="Steel",
            E_mod=2.0e11, G_mod=7.7e10, nu=0.3,
            unit_weight=7.85e4,  # N/m³
            Fy=2.5e8,
        ),
    }
    # ── Section ──
    sections = {
        "UB300": Section(
            name="UB300", shape="I/Wide Flange",
            material="Steel", A=8.0e-3, I33=1.2e-4, I22=4.0e-5, J=2.0e-6,
        ),
    }
    # ── Frame element ──
    frame_elements = {
        "1": FrameElement(elem_id="1", elem_tag=1, node_i="1", node_j="2"),
    }
    frame_assignments = {"1": "UB300"}
    # ── Load patterns ──
    load_patterns = {
        "DEAD": LoadPattern(name="DEAD", pattern_type="Dead", self_weight_factor=1),
        "WIND": LoadPattern(name="WIND", pattern_type="Wind", self_weight_factor=0),
    }
    # ── Distributed load on the frame (uniform X load for WIND) ──
    frame_dist_loads = [
        FrameDistributedLoad(
            pattern="WIND", frame_id="1",
            direction="X", load_type="Force",
            shape="Uniform", val_a=1.0e4, val_b=1.0e4,
            rdist_a=0.0, rdist_b=1.0, dist_a=0.0, dist_b=10.0,
        ),
    ]
    # ── Mass source ──
    mass_sources = {
        "MSSSRC1": MassSource(
            name="MSSSRC1", elements=True, masses=False, loads=False,
        ),
    }
    return SAPModelData(
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
        frame_dist_loads=frame_dist_loads,
        mass_sources=mass_sources,
    )
