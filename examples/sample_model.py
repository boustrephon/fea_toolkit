"""Simple cantilever column model for use as a --sample fallback.

Builds a ``SAPModelData`` instance programmatically (no .s2k file needed).
The model is a 10 m steel cantilever with I‑section, suitable for quick
testing of static, modal, pushover, and response‑spectrum examples.

Usage::

    from examples.sample_model import make_sample_model
    md = make_sample_model()
"""

from fea_toolkit.model.sap_data import (
    SAPModelData, Node, Restraint, Material, Section, ISection,
    RectangularSection, ConcreteRectangularSection,
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


def make_nonlinear_sample_model() -> SAPModelData:
    """Build a 10 m steel cantilever with a fiber-based I-section.

    Same geometry and loads as :func:`make_sample_model`, but the frame
    section is an :class:`~fea_toolkit.model.sap_data.ISection` so the
    AnalysisBuilder can generate fiber patches (``section Fiber`` +
    ``patch rect``) and use ``dispBeamColumn`` elements.  This produces
    a genuinely nonlinear pushover curve (elastic → yielding), which is
    required for the CSM performance-point workflow.

    The UB300 flange/web dimensions are back-calculated from the original
    sectional properties (A = 8e-3 m², I33 = 1.2e-4 m⁴, I22 = 4e-5 m⁴):

    * A    = 2·bf·tf + (d - 2·tf)·tw            ≈ 8.0e-3 m²
    * I33  = bf·d³/12 − (bf − tw)·(d − 2·tf)³/12 ≈ 1.2e-4 m⁴
    * I22  = 2·tf·bf³/12 + (d − 2·tf)·tw³/12     ≈ 4.0e-5 m⁴

    Returns:
        SAPModelData with a nonlinear-capable ISection ready for
        ``preprocess_model(..., create_fiber_sections=True)``.
    """
    md = make_sample_model()
    # Dimensions for a typical UB300×150×8×12 (d, bf, tw, tf in metres).
    d, bf, tw, tf = 0.300, 0.150, 0.008, 0.012
    md.sections["UB300"] = ISection(
        name="UB300", shape="I/Wide Flange",
        material="Steel",
        A=8.0e-3, I33=1.2e-4, I22=4.0e-5, J=2.0e-6,
        depth=d, bf=bf, tw=tw, tf=tf,
    )
    return md


def make_rc_frame_model() -> SAPModelData:
    """Build a 2‑storey, 1‑bay reinforced‑concrete moment frame.

    This is the representative nonlinear test model for the CSM
    performance‑point workflow.  It follows the production RC path:

    * ``ConcreteRectangularSection`` columns and ``RectangularSection``
      beams reference a concrete material so that both section types
      override :meth:`~fea_toolkit.model.sap_data.Section.to_fiber_patches`.
    * ``run_pushover_analysis()`` therefore auto‑detects fiber‑capable
      sections and rebuilds the domain with ``dispBeamColumn`` + fibre
      sections via ``rebuild_with_fiber_sections()``.
    * The frame yields in the pushover (unlike a bare steel cantilever),
      giving a proper bilinear capacity curve, meaningful ductility and a
      converged ATC‑40 performance point.

    Geometry (SI units — N, m):

    * Bay width: 4 m in X; storey height: 3 m in Z (2 storeys).
    * Base nodes ``1``, ``2`` are fully fixed.
    * Roof control node is ``6`` (top‑right).
    * Columns are 300×300 with 4 φ16 top + 4 φ16 bottom bars, 40 mm
      cover; beams are 300×500.

    Returns:
        SAPModelData ready for ``preprocess_model`` + ``AnalysisBuilder``.
    """
    # ── Nodes ──
    nodes = {
        "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
        "2": Node(node_id="2", node_tag=2, x=4.0, y=0.0, z=0.0),
        "3": Node(node_id="3", node_tag=3, x=0.0, y=0.0, z=3.0),
        "4": Node(node_id="4", node_tag=4, x=4.0, y=0.0, z=3.0),
        "5": Node(node_id="5", node_tag=5, x=0.0, y=0.0, z=6.0),
        "6": Node(node_id="6", node_tag=6, x=4.0, y=0.0, z=6.0),
    }
    # ── Restraints (fixed base) ──
    restraints = {
        "1": Restraint([1, 1, 1, 1, 1, 1]),
        "2": Restraint([1, 1, 1, 1, 1, 1]),
    }
    # ── Materials (SI: Pa, N/m³; framework scales to model units) ──
    materials = {
        "C30": Material(
            name="C30", type="Concrete",
            E_mod=2.0e10, G_mod=8.0e9, nu=0.2,
            unit_weight=2.4e4,  # N/m³
            Fc=3.0e7,           # 30 MPa concrete
            Fy=4.0e8,           # 400 MPa rebar (used for mat_tag+2 Steel02)
        ),
    }
    # ── Sections ──
    sections = {
        "COL": ConcreteRectangularSection(
            name="COL", shape="Concrete Rectangular", material="C30",
            A=0.09, I33=6.75e-4, I22=6.75e-4, J=1.14e-3,
            depth=0.3, bf=0.3, cover=0.04,
            top_bars=4, bot_bars=4,
            top_bar_dia=0.016, bot_bar_dia=0.016,
        ),
        "BEAM": RectangularSection(
            name="BEAM", shape="Concrete Rectangular", material="C30",
            A=0.15, I33=3.125e-3, I22=1.125e-3, J=1.5e-3,
            depth=0.5, bf=0.3,
        ),
    }
    # ── Frame elements (columns 1-4, beams 5-6) ──
    frame_elements = {
        "1": FrameElement(elem_id="1", elem_tag=1, node_i="1", node_j="3"),
        "2": FrameElement(elem_id="2", elem_tag=2, node_i="2", node_j="4"),
        "3": FrameElement(elem_id="3", elem_tag=3, node_i="3", node_j="5"),
        "4": FrameElement(elem_id="4", elem_tag=4, node_i="4", node_j="6"),
        "5": FrameElement(elem_id="5", elem_tag=5, node_i="3", node_j="4"),
        "6": FrameElement(elem_id="6", elem_tag=6, node_i="5", node_j="6"),
    }
    frame_assignments = {
        "1": "COL", "2": "COL", "3": "COL", "4": "COL",
        "5": "BEAM", "6": "BEAM",
    }
    # ── Load patterns ──
    load_patterns = {
        "DEAD": LoadPattern(name="DEAD", pattern_type="Dead", self_weight_factor=1),
        "WIND": LoadPattern(name="WIND", pattern_type="Wind", self_weight_factor=0),
    }
    # ── Mass source (self-weight → nodal mass) ──
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
        frame_dist_loads=[],
        mass_sources=mass_sources,
    )
