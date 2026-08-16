"""Simple cantilever column model for use as a --sample fallback.

Builds a ``SAPModelData`` instance programmatically (no .s2k file needed).
The model is a 10 m steel cantilever with I‑section, suitable for quick
testing of static, modal, pushover, and response‑spectrum examples.

Usage::

    from examples.sample_model import make_sample_model
    md = make_sample_model()
"""

from fea_toolkit.model.sap_data import (
    ConcreteRectangularSection,
    FrameDistributedLoad,
    FrameElement,
    ISection,
    LoadPattern,
    MassSource,
    Material,
    Node,
    RectangularSection,
    Restraint,
    SAPModelData,
    Section,
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
            name="Steel",
            type="Steel",
            E_mod=2.0e11,
            G_mod=7.7e10,
            nu=0.3,
            unit_weight=7.85e4,  # N/m³
            Fy=2.5e8,
        ),
    }
    # ── Section (UB 305×165×40, nominal flange/web approximation) ──
    # NOTE: The key/name "UB300" is retained as a legacy identifier —
    # the section is actually a UB 305×165×40, so exported labels use
    # "UB300" for backward compatibility with existing fixtures/tests.
    sections = {
        "UB300": Section(
            name="UB300",
            shape="I/Wide Flange",
            material="Steel",
            A=0.00509434,
            I33=8.3935e-5,
            I22=7.6559e-6,
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
            pattern="WIND",
            frame_id="1",
            direction="X",
            load_type="Force",
            shape="Uniform",
            val_a=1.0e4,
            val_b=1.0e4,
            rdist_a=0.0,
            rdist_b=1.0,
            dist_a=0.0,
            dist_b=10.0,
        ),
    ]
    # ── Mass source ──
    mass_sources = {
        "MSSSRC1": MassSource(
            name="MSSSRC1",
            elements=True,
            masses=False,
            loads=False,
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

    The UB300 section is a **UB 305×165×40** universal beam
    (nominal flange/web approximation): d = 303.4 mm, bf = 165.1 mm,
    tw = 6.1 mm, tf = 10.2 mm.  Sectional properties are computed from
    the standard flange/web model (nominal values; published BS 4-1
    Iₓ/Iᵧ differ slightly):

    * A    = 2·bf·tf + (d − 2·tf)·tw             = 5.09e-3 m²
    * I33  = bf·d³/12 − (bf − tw)·(d − 2·tf)³/12  = 8.39e-5 m⁴
    * I22  = 2·tf·bf³/12 + (d − 2·tf)·tw³/12      = 7.66e-6 m⁴

    Returns:
        SAPModelData with a nonlinear-capable ISection ready for
        ``preprocess_model(..., create_fiber_sections=True)``.
    """
    md = make_sample_model()
    # UB 305×165×40 (nominal flange/web approximation) — d, bf, tw, tf in metres.
    d, bf, tw, tf = 0.3034, 0.1651, 0.0061, 0.0102
    md.sections["UB300"] = ISection(
        name="UB300",
        shape="I/Wide Flange",
        material="Steel",
        A=0.00509434,
        I33=8.3935e-5,
        I22=7.6559e-6,
        J=2.0e-6,
        depth=d,
        bf=bf,
        tw=tw,
        tf=tf,
    )
    return md


def _rc_frame_materials() -> dict[str, Material]:
    """C30 / Rebar / Q355 materials in kN-m model units (kPa).

    Shared by :func:`make_rc_frame_model` and :func:`make_rc_frame_3d`.
    Values match the effective post-reduction numbers in
    ``docs/csm_test_model_plan.md`` (C30 with 0.7× short-term modulus factor).
    """
    return {
        "C30": Material(
            name="C30",
            type="Concrete",
            E_mod=15.54e6,
            G_mod=6.475e6,
            nu=0.2,
            unit_weight=25.0,  # kN/m³
            Fc=20.1e3,  # kPa (20.1 MPa)
        ),
        "Rebar": Material(
            name="Rebar",
            type="Rebar",
            E_mod=199.95e6,
            nu=0.3,
            unit_weight=77.0,  # kN/m³
            Fy=413.685e3,  # kPa (A615Gr60)
        ),
        "Q355": Material(
            name="Q355",
            type="Steel",
            E_mod=206e6,
            G_mod=79.23e6,
            nu=0.3,
            unit_weight=77.0,  # kN/m³
            Fy=355e3,  # kPa
        ),
    }


def _rc_frame_sections() -> dict[str, Section]:
    """Fibre-capable RC sections: 300×300 columns, 300×500 beams.

    Shared by :func:`make_rc_frame_model` and :func:`make_rc_frame_3d`.
    Both reference the ``C30`` concrete material so they promote to fibre
    patches (and yield in pushover).
    """
    return {
        # Columns: fibre-capable RC section; rebar material resolved by
        # the builder's 3-level rebar Fy/Es resolution (config override →
        # SAP2000 lookup → framework defaults).
        "COL": ConcreteRectangularSection(
            name="COL",
            shape="Concrete Rectangular",
            material="C30",
            rebar_material="Rebar",
            A=0.09,
            I33=6.75e-4,
            I22=6.75e-4,
            J=1.14e-3,
            depth=0.3,
            bf=0.3,
            cover=0.04,
            top_bars=4,
            bot_bars=4,
            top_bar_dia=0.016,
            bot_bar_dia=0.016,
        ),
        # Beams: fibre-capable rectangular section referencing "C30"
        # (0.6 % steel ratio with 40 mm cover via to_fiber_patches()).
        "BEAM": RectangularSection(
            name="BEAM",
            shape="Concrete Rectangular",
            material="C30",
            A=0.15,
            I33=3.125e-3,
            I22=1.125e-3,
            J=1.5e-3,
            depth=0.5,
            bf=0.3,
        ),
    }


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
    # ── Restraints ──
    # Base nodes 1, 2 are fully fixed.  Roof nodes 3, 4 are restrained
    # against out-of-plane translation (UY) only, so the frame is a true
    # 2D X–Z portal: the X-sway mode becomes the fundamental mode and is
    # the first one returned by ``run_modal_analysis``.
    restraints = {
        "1": Restraint([1, 1, 1, 1, 1, 1]),
        "2": Restraint([1, 1, 1, 1, 1, 1]),
        "3": Restraint([0, 1, 0, 0, 0, 0]),
        "4": Restraint([0, 1, 0, 0, 0, 0]),
    }
    # ── Materials + sections (shared C30/Rebar/Q355 + COL/BEAM set) ──
    materials = _rc_frame_materials()
    sections = _rc_frame_sections()
    # ── Frame elements (columns 1-2, beam 3) ──
    frame_elements = {
        "1": FrameElement(elem_id="1", elem_tag=1, node_i="1", node_j="3"),
        "2": FrameElement(elem_id="2", elem_tag=2, node_i="2", node_j="4"),
        "3": FrameElement(elem_id="3", elem_tag=3, node_i="3", node_j="4"),
    }
    frame_assignments = {
        "1": "COL",
        "2": "COL",
        "3": "BEAM",
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
            pattern="DEAD",
            frame_id="3",
            direction="Gravity",
            load_type="Force",
            shape="Uniform",
            val_a=20.0,
            val_b=20.0,
            rdist_a=0.0,
            rdist_b=1.0,
            dist_a=0.0,
            dist_b=4.0,
        ),
    ]
    # ── Mass source: element self-weight + DEAD floor loads → nodal mass ──
    mass_sources = {
        "MSSSRC1": MassSource(
            name="MSSSRC1",
            elements=True,
            masses=False,
            loads=True,
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


def make_rc_frame_3d() -> SAPModelData:
    """Build a single‑storey, 2‑bay × 2‑bay three‑dimensional RC moment frame.

    A genuinely 3D counterpart to :func:`make_rc_frame_model` — nodes have
    non‑zero Y coordinates (8 m × 8 m plan, 3 m storey), so the model
    exercises the full 3D OpenSees domain (``ndm=3``, ``ndf=6``) rather
    than a planar X–Z portal with out‑of‑plane restraints.

    * 9 columns on a 3×3 grid (300×300 ``ConcreteRectangularSection``),
      fully fixed at the base.
    * 12 roof beams (300×500 ``RectangularSection``) — 6 spanning X, 6
      spanning Y — forming a 2×2‑bay roof grid.
    * Same kN‑m (kPa) materials as :func:`make_rc_frame_model`
      (C30 / Rebar / Q355, shared helpers), so the model auto‑promotes to
      fibre sections and yields in pushover.
    * DEAD gravity (20 kN/m on each roof beam) + ``MassSource`` for
      seismic mass.

    Returns:
        SAPModelData ready for ``preprocess_model`` + ``AnalysisBuilder``.
    """
    _units = {"F": "KN", "L": "m", "T": "C"}
    _x = [0.0, 4.0, 8.0]
    _y = [0.0, 4.0, 8.0]

    # ── Nodes: 3×3 grid at z=0 (base 1..9) and z=3 (roof 10..18) ──
    nodes: dict[str, Node] = {}
    for j, yy in enumerate(_y):
        for i, xx in enumerate(_x):
            base_id = str(1 + 3 * j + i)
            nodes[base_id] = Node(node_id=base_id, node_tag=1 + 3 * j + i, x=xx, y=yy, z=0.0)
            roof_id = str(10 + 3 * j + i)
            nodes[roof_id] = Node(node_id=roof_id, node_tag=10 + 3 * j + i, x=xx, y=yy, z=3.0)

    # ── Restraints: base fully fixed, roof free ──
    restraints = {str(n): Restraint([1, 1, 1, 1, 1, 1]) for n in range(1, 10)}

    # ── Frame elements: 9 columns + 12 beams ──
    frame_elements: dict[str, FrameElement] = {}
    frame_assignments: dict[str, str] = {}
    _elem_id = 1
    for n in range(1, 10):  # columns: base n → roof n+9
        _id = str(_elem_id)
        frame_elements[_id] = FrameElement(
            elem_id=_id, elem_tag=_elem_id, node_i=str(n), node_j=str(n + 9)
        )
        frame_assignments[_id] = "COL"
        _elem_id += 1
    # Beams spanning X (roof rows at Y = 0, 4, 8)
    _x_beams = [(10, 11), (11, 12), (13, 14), (14, 15), (16, 17), (17, 18)]
    # Beams spanning Y (roof columns at X = 0, 4, 8)
    _y_beams = [(10, 13), (13, 16), (11, 14), (14, 17), (12, 15), (15, 18)]
    for _ni, _nj in _x_beams + _y_beams:
        _id = str(_elem_id)
        frame_elements[_id] = FrameElement(
            elem_id=_id, elem_tag=_elem_id, node_i=str(_ni), node_j=str(_nj)
        )
        frame_assignments[_id] = "BEAM"
        _elem_id += 1

    # ── Load patterns + gravity floor load on every roof beam ──
    load_patterns = {
        "DEAD": LoadPattern(name="DEAD", pattern_type="Dead", self_weight_factor=1),
        "WIND": LoadPattern(name="WIND", pattern_type="Wind", self_weight_factor=0),
    }
    frame_dist_loads = [
        FrameDistributedLoad(
            pattern="DEAD",
            frame_id=_id,
            direction="Gravity",
            load_type="Force",
            shape="Uniform",
            val_a=20.0,
            val_b=20.0,
            rdist_a=0.0,
            rdist_b=1.0,
            dist_a=0.0,
            dist_b=4.0,
        )
        for _id, _sec in frame_assignments.items()
        if _sec == "BEAM"
    ]
    mass_sources = {
        "MSSSRC1": MassSource(
            name="MSSSRC1",
            elements=True,
            masses=False,
            loads=True,
            load_pattern={"DEAD": 1.0},
        ),
    }

    md = SAPModelData(
        nodes=nodes,
        restraints=restraints,
        materials=_rc_frame_materials(),
        sections=_rc_frame_sections(),
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
