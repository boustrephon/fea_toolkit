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
    # ── Section (real UB 305×165×40, BS 4-1) ──
    sections = {
        "UB300": Section(
            name="UB300", shape="I/Wide Flange",
            material="Steel", A=0.00509434, I33=8.3935e-5, I22=7.6559e-6,
            J=2.0e-6,
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

    The UB300 section is a real **UB 305×165×40** universal beam
    (BS 4-1): d = 303.4 mm, bf = 165.1 mm, tw = 6.1 mm, tf = 10.2 mm.
    Sectional properties are computed from the standard flange/web model
    (matching the published values A = 50.9 cm², Iₓ = 8490 cm⁴,
    Iᵧ = 762 cm⁴):

    * A    = 2·bf·tf + (d − 2·tf)·tw             = 5.09e-3 m²
    * I33  = bf·d³/12 − (bf − tw)·(d − 2·tf)³/12  = 8.39e-5 m⁴
    * I22  = 2·tf·bf³/12 + (d − 2·tf)·tw³/12      = 7.66e-6 m⁴

    Returns:
        SAPModelData with a nonlinear-capable ISection ready for
        ``preprocess_model(..., create_fiber_sections=True)``.
    """
    md = make_sample_model()
    # Real UB 305×165×40 (BS 4-1) — d, bf, tw, tf in metres.
    d, bf, tw, tf = 0.3034, 0.1651, 0.0061, 0.0102
    md.sections["UB300"] = ISection(
        name="UB300", shape="I/Wide Flange",
        material="Steel",
        A=0.00509434, I33=8.3935e-5, I22=7.6559e-6, J=2.0e-6,
        depth=d, bf=bf, tw=tw, tf=tf,
    )
    return md


def make_rc_frame_model() -> SAPModelData:
    """Build a single‑storey, 1‑bay reinforced‑concrete moment frame.

    This is the representative nonlinear test model for the CSM
    performance‑point workflow.  It is modelled on the OpenSees
    reference example ``docs/references/RCFrameGravity_v2.py`` /
    ``RCFramePushOver_v2.py`` (single‑storey RC portal frame with
    nonlinear fibre columns), expressed through the toolkit's
    programmatic ``SAPModelData`` path rather than a raw OpenSees
    script — mirroring how a real SAP2000 ``.s2k`` model (e.g. the
    Admin Building) flows through the parser → preprocessor →
    ``AnalysisBuilder`` pipeline.

    It follows the production RC path:

    * ``ConcreteRectangularSection`` columns and ``RectangularSection``
      beams reference a concrete material so that both section types
      override :meth:`~fea_toolkit.model.sap_data.Section.to_fiber_patches`.
    * ``run_pushover_analysis()`` therefore auto‑detects fiber‑capable
      sections and rebuilds the domain with ``dispBeamColumn`` + fibre
      sections via ``rebuild_with_fiber_sections()``.
    * The frame yields in the pushover, giving a proper bilinear
      capacity curve, meaningful ductility and a converged ATC‑40
      performance point.

    **Unit system (kN‑m)**: ``md.units = {"F": "KN", "L": "m", "T": "C"}``.

    All material values are authored directly in **model units (kPa =
    kN/m²)** — this model is built in ``SAPModelData`` directly (not
    via .s2k), so values set on :class:`Material` bypass the
    framework's SI→model scaling path (``apply_material_defaults()``
    only fills missing defaults and never overwrites explicit values).

    **Materials** (per ``docs/csm_test_model_plan.md``, effective
    post‑reduction values — C30 with 0.7× short‑term modulus factor):

    ========== ============== ========== ========= ============ ============
    Material   E_mod (kPa)     Fc (kPa)   Fy (kPa)  G_mod (kPa)  unit_weight
    ========== ============== ========== ========= ============ ============
    C30        15.54e6         20.1e3     —         6.475e6       25.0
    Rebar      199.95e6         —         413.685e3 auto (ν=0.3)  77.0
    Q355       206e6            —         355e3     79.23e6       77.0
    ========== ============== ========== ========= ============ ============

    **Mass**: ``MassSource(elements=True, loads=True,
    load_pattern={"DEAD": 1.0})`` converts element self‑weight *and*
    the beam's uniform floor dead load (gravity direction) into lumped
    nodal masses via :meth:`AnalysisBuilder.compute_seismic_masses` —
    the same S2K mass‑source flow exercised by real building models.

    Geometry:

    * Bay width: 4 m in X; storey height: 3 m in Z (single storey).
    * Base nodes ``1``, ``2`` are fully fixed.
    * Roof control node is ``4`` (top‑right).
    * Columns are 300×300 with 4 φ16 top + 4 φ16 bottom bars, 40 mm
      cover; beams are 300×500.

    Returns:
        SAPModelData ready for ``preprocess_model`` + ``AnalysisBuilder``.
    """
    # ── Units: kN–m (model stress units = kPa) ──
    _units = {"F": "KN", "L": "m", "T": "C"}

    # ── Nodes ──
    nodes = {
        "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
        "2": Node(node_id="2", node_tag=2, x=4.0, y=0.0, z=0.0),
        "3": Node(node_id="3", node_tag=3, x=0.0, y=0.0, z=3.0),
        "4": Node(node_id="4", node_tag=4, x=4.0, y=0.0, z=3.0),
    }
    # ── Restraints (fixed base) ──
    restraints = {
        "1": Restraint([1, 1, 1, 1, 1, 1]),
        "2": Restraint([1, 1, 1, 1, 1, 1]),
    }
    # ── Materials (model units: kPa / kN·m⁻³) ──
    materials = {
        "C30": Material(
            name="C30", type="Concrete",
            E_mod=15.54e6, G_mod=6.475e6, nu=0.2,
            unit_weight=25.0,          # kN/m³
            Fc=20.1e3,                 # kPa (20.1 MPa)
        ),
        "Rebar": Material(
            name="Rebar", type="Rebar",
            E_mod=199.95e6, nu=0.3,
            unit_weight=77.0,          # kN/m³
            Fy=413.685e3,              # kPa (A615Gr60)
        ),
        "Q355": Material(
            name="Q355", type="Steel",
            E_mod=206e6, G_mod=79.23e6, nu=0.3,
            unit_weight=77.0,          # kN/m³
            Fy=355e3,                  # kPa
        ),
    }
    # ── Sections ──
    sections = {
        # Columns: fibre-capable RC section; rebar material resolved by
        # the builder's 3-level rebar Fy/Es resolution (config override →
        # SAP2000 lookup → framework defaults).
        "COL": ConcreteRectangularSection(
            name="COL", shape="Concrete Rectangular", material="C30",
            rebar_material="Rebar",
            A=0.09, I33=6.75e-4, I22=6.75e-4, J=1.14e-3,
            depth=0.3, bf=0.3, cover=0.04,
            top_bars=4, bot_bars=4,
            top_bar_dia=0.016, bot_bar_dia=0.016,
        ),
        # Beams: fibre-capable rectangular section referencing "C30"
        # (0.6 % steel ratio with 40 mm cover via to_fiber_patches()).
        "BEAM": RectangularSection(
            name="BEAM", shape="Concrete Rectangular", material="C30",
            A=0.15, I33=3.125e-3, I22=1.125e-3, J=1.5e-3,
            depth=0.5, bf=0.3,
        ),
    }
    # ── Frame elements (columns 1-2, beam 3) ──
    frame_elements = {
        "1": FrameElement(elem_id="1", elem_tag=1, node_i="1", node_j="3"),
        "2": FrameElement(elem_id="2", elem_tag=2, node_i="2", node_j="4"),
        "3": FrameElement(elem_id="3", elem_tag=3, node_i="3", node_j="4"),
    }
    frame_assignments = {
        "1": "COL", "2": "COL", "3": "BEAM",
    }
    # ── Load patterns ──
    load_patterns = {
        "DEAD": LoadPattern(name="DEAD", pattern_type="Dead", self_weight_factor=1),
        "WIND": LoadPattern(name="WIND", pattern_type="Wind", self_weight_factor=0),
    }
    # ── Beam floor dead load (gravity): 20 kN/m over 4 m = 80 kN.
    #    Contributes to seismic mass via MassSource(loads=True, DEAD). ──
    frame_dist_loads = [
        FrameDistributedLoad(
            pattern="DEAD", frame_id="3",
            direction="Gravity", load_type="Force",
            shape="Uniform", val_a=20.0, val_b=20.0,
            rdist_a=0.0, rdist_b=1.0, dist_a=0.0, dist_b=4.0,
        ),
    ]
    # ── Mass source: element self-weight + DEAD floor loads → nodal mass ──
    mass_sources = {
        "MSSSRC1": MassSource(
            name="MSSSRC1", elements=True, masses=False, loads=True,
            load_pattern={"DEAD": 1.0},
        ),
    }
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
        frame_dist_loads=frame_dist_loads,
        mass_sources=mass_sources,
        units=_units,
    )
    # Auto-derive missing material properties (G_mod for Rebar, etc.)
    # from SI defaults scaled to the kN-m model unit system.
    md.apply_material_defaults()
    return md
