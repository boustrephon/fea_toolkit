"""Standalone 2D OpenSees RC cantilever hand-check (test-only).

Per ``.clinerules`` §3.11 the toolkit's main workflows are 3D-only; 2D
OpenSees analyses are permitted **in tests** as hand-check benchmarks.
This test drives ``ops`` directly at ``ndm=2``/``ndf=3`` — it does NOT go
through the Preprocessor/AnalysisBuilder pipeline — to validate the
model-layer RC section conventions (``ConcreteRectangularSection
.to_fiber_patches()`` + the C30/Rebar material values shared with
``make_rc_frame_model``) against a closed-form plastic moment.

Recommended-validation-sequence item 1 of ``docs/deprecation_plan.md``:
single-column ``forceBeamColumn`` + ``Lobatto`` with ``Concrete01`` /
``Steel01`` fibers, pushed to 5 % drift, peak base shear compared against
the hand-calculated plastic moment.
"""

import math


def _build_2d_rc_cantilever(ops) -> list[float]:
    """Build and push a 2D (ndm=2) single-element RC cantilever.

    Returns the absolute base-shear history (kN) at each displacement
    step (3 mm × 50 steps = 150 mm = 5 % drift of the 3 m cantilever).
    """
    from examples.sample_model import _rc_frame_materials
    from fea_toolkit.model.sap_data import ConcreteRectangularSection

    mats = _rc_frame_materials()
    fpc = mats["C30"].Fc  # kPa (kN/m²)
    fy = mats["Rebar"].Fy  # kPa
    es = mats["Rebar"].E_mod  # kPa

    sec = ConcreteRectangularSection(
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
    )
    patches = sec.to_fiber_patches(mat_tag=1)

    ops.wipe()
    ops.model("basic", "-ndm", 2, "-ndf", 3)
    ops.node(1, 0.0, 0.0)  # base
    ops.node(2, 0.0, 3.0)  # tip
    ops.fix(1, 1, 1, 1)
    # Material tags follow to_fiber_patches(): 1 unconfined, 2 confined, 3 steel.
    ops.uniaxialMaterial("Concrete01", 1, -fpc, -0.002, -0.2 * fpc, -0.004)
    ops.uniaxialMaterial("Concrete01", 2, -1.25 * fpc, -0.0025, -0.3 * 1.25 * fpc, -0.012)
    ops.uniaxialMaterial("Steel01", 3, fy, es, 0.005)
    ops.section("Fiber", 1)
    for entry in patches:
        if entry[0] in ("rect", "circ", "quad"):
            ops.patch(*entry)
        elif entry[0] == "straight":
            ops.layer("straight", *entry[1:])
        elif entry[0] == "circ_layer":
            ops.layer("circ", *entry[1:])
    ops.geomTransf("PDelta", 1)
    ops.beamIntegration("Lobatto", 10001, 1, 5)
    ops.element("forceBeamColumn", 1, 1, 2, 1, 10001)
    # Reference load so DisplacementControl has a load vector.
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    ops.load(2, 1.0, 0.0, 0.0)

    ops.system("BandGeneral")
    ops.numberer("RCM")
    ops.constraints("Plain")
    ops.test("NormDispIncr", 1e-5, 20)
    ops.algorithm("Newton")
    ops.integrator("DisplacementControl", 2, 1, 0.003)
    ops.analysis("Static")

    base_shears: list[float] = []
    for _ in range(50):
        if ops.analyze(1) != 0:
            break
        ops.reactions()
        base_shears.append(abs(ops.nodeReaction(1, 1)))
    return base_shears


class TestRC2DCantilever:
    def test_peak_shear_matches_plastic_moment(self):
        """2D RC cantilever peak base shear ≈ hand-calculated plastic moment.

        ACI rectangular-stress-block estimate (tension steel yields, only
        compression steel/concrete from the block)::

            T = A_s · f_y
            a = T / (0.85 · f'c · b)
            M_p = T · (d − a/2),   V_peak = M_p / H

        The fiber model runs ~10 % higher than the block estimate (the
        block's 0.85·f'c is conservative vs the full-fpc fibre stress and
        the compression steel contributes) — accepted band ±15 %.
        """
        from openseespy.opensees import wipe

        try:
            import openseespy.opensees as ops

            base_shears = _build_2d_rc_cantilever(ops)
        finally:
            wipe()

        assert len(base_shears) == 50, (
            f"expected full 5 % drift pushover (50 steps), got {len(base_shears)}"
        )
        peak = max(base_shears)
        assert peak > 0

        from examples.sample_model import _rc_frame_materials

        fpc = _rc_frame_materials()["C30"].Fc
        fy = _rc_frame_materials()["Rebar"].Fy
        A_s = 4 * math.pi * (0.008) ** 2  # 4 × φ16 bars per face (m²)
        tension = A_s * fy  # kN
        a = tension / (0.85 * fpc * 0.3)  # rectangular-block depth (m)
        d = 0.3 - 0.04  # bar-centroid depth (m)
        v_hand = tension * (d - a / 2) / 3.0  # kN

        ratio = peak / v_hand
        assert 0.85 <= ratio <= 1.15, (
            f"peak base shear {peak:.2f} kN vs hand calc {v_hand:.2f} kN "
            f"(ratio {ratio:.3f}) outside ±15 %"
        )
