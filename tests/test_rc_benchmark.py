"""Gap 4: Vecchio & Emara (1992) frame end-to-end pushover benchmark.

End-to-end validation of the toolkit's Preprocessor → AnalysisBuilder →
pushover pipeline against a published large-scale RC frame experiment.

**Reference case** — Vecchio, F.J. & Emara, M.B. (1992), "Shear Deformations
in Reinforced Concrete Frames", *ACI Structural Journal* 89(1) 46–56,
University of Toronto.  Data transcribed from Guner (2008) §2.3.5/§4.7
(``Table 2.5``, ``Figure 2.15``) and the PEER Report 2006/04 (Lee &
Mosalam) §4.5.1/§5.3:

- One-bay, two-storey frame; c-c span 3500 mm; storey heights 2000 mm
  (column centreline to beam centreline, base top to Level 2 = 4000 mm;
  overall 4600 mm incl. the 400-mm base beam).
- All members 300 × 400 mm; beams and columns reinforced with 4 No. 20M
  (As = 300 mm²) top + 4 bottom; No. 10M closed stirrups @ 125 mm.
- Materials: f'c = 30 MPa (Ec = 23 674 MPa, epsc0 = 1.85e-3); longitudinal
  fy = 418 MPa (fu = 596, Es = 192 500 MPa, eps_sh = 9.5e-3,
  Esh = 3100 MPa); ties fy = 454 MPa.
- Loading: constant 700 kN axial per column (force-controlled jacks);
  monotonic lateral displacement at the second-storey beam (Level 2)
  pushed to 155 mm, then unloaded.
- Reported response: peak net lateral load ≈ 330 kN (VecTor5 calc 324,
  calc/obs = 0.98) reached near 50 mm; energy dissipation 44.4 kN·m
  (calc 44.6); effective secant stiffness at yield ≈ 6.1 kN/mm.

**Gap 4 findings (2026-08-16):**

1. **Joint-load application bug (fixed).**  SAP2000 "JOINT LOADS - FORCE"
   were parsed and carried through the Preprocessor but never emitted to
   the OpenSees domain — the 700 kN column loads were silently dropped
   (:meth:`AnalysisBuilder.create_loads` now applies them).  Regression
   test ``test_gravity_joint_loads_applied`` guards this.

2. **Flexure-only bias is systematic and documented, not ±10%.**  The
   flexure-only fiber pushover of this frame overestimates both stiffness
   (secant ≈ 8.6 kN/mm vs experimental 6.1 kN/mm at 50 mm) and peak
   capacity (≈ 495 kN vs ≈ 330 kN, calc/obs ≈ 1.5).  The pure fiber model
   has no bond-slip, no shear deformation (~20 % share in the experiment),
   and no distributed-cracking effective-stiffness reduction, so it is
   stiffer in the cracked range; the higher column shears then inflate the
   frame-action axial force in the beams, which in turn raises their
   confined/hardening section capacity (M ≈ 255 kN·m at P ≈ −750 kN vs
   ≈ 195 kN·m at P = 0).  The elastic-to-first-yield response is in the
   right range (the hand-calc first-yield lateral load of 312 kN is
   exceeded), and the model does show a post-peak descent driven by P-Δ.
   Acceptance criteria below therefore **bracket** the experiment rather
   than claiming ±10 %; the plan's "flexure-only basis" caveat
   (deprecation_plan.md Gap 4, measure 4) is honoured by documenting the
   bias.  Closing the remaining gap requires shear-flexible section
   aggregation (e.g. shear springs) — a follow-up, not part of this test.

3. **Second pass (2026-08-16): the element formulation was part of the
   bias.**  A re-run of the same pipeline with the fibre rebuild on
   ``forceBeamColumn`` (flexibility-based) instead of ``dispBeamColumn``
   (displacement-based) drops the peak to ≈ 291 kN (0.88 × experimental)
   and the secant @ 50 mm to ≈ 5.6 kN/mm (0.93 × experimental) — inside
   the original ±10–15 % acceptance band with no calibration.
   ``dispBeamColumn`` (Euler-Bernoulli) both over-stiffens once fibre
   sections soften *and* never computes section shear deformation, so a
   ``SectionAggregator`` shear spring is inert for it; ``forceBeamColumn``
   engages the aggregated shear DOFs.  The elastic ``GA_v`` shear term
   itself contributes only ≈ 0.2 % for these members (the experimental
   ~20 % shear share is a cracked-shear phenomenon).  See
   :class:`TestVecchioEmaraShearFlexibleVariant` — the shape still lacks
   the experimental post-peak descent (the model plateaus), which needs
   nonlinear cracked-shear / bond-slip modelling (deferred,
   ``docs/_pending_work.md``).
"""

import math

import numpy as np
import pytest

from fea_toolkit.model.sap_data import (
    ConcreteRectangularSection,
    FrameElement,
    JointLoad,
    LoadPattern,
    MassSource,
    Material,
    Node,
    Restraint,
    SAPModelData,
)
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
from fea_toolkit.opensees.preprocessor import preprocess_model

# ── Published experimental anchor values (kN, m) ────────────────────
EXP_PEAK_SHEAR = 330.0  # kN (net lateral load; calc/obs = 0.98 @ 324 kN)
EXP_SECANT_AT_YIELD = 6.1e3  # kN/m (≈ 6.1 kN/mm at complete yielding)
HAND_FIRST_YIELD_LOAD = 312.0  # kN (thesis §2.3.5.1, first hinge B1R)

_BENCH_CONFIG = {
    "element_type": "elasticBeamColumn",
    "verbose": False,
    "create_shells": False,
    "geom_transf_type": "PDelta",  # P-Δ drives the post-peak descent
    "num_int_pts": 5,
    "rebar_b": 3100.0 / 192500.0,  # Esh/Es = 0.0161 (published steel)
}


def make_vecchio_emara_frame() -> SAPModelData:
    """Build the Vecchio & Emara (1992) frame as programmatic SAPModelData.

    Planar X–Z frame in kN-m units (per the 3D-only policy, out-of-plane
    DOFs are unrestrained-elsewhere; the two base nodes are fully fixed).
    Members carry the published 4 No. 20M top/bottom bars; the Mander
    confinement path is fed the published No. 10M @ 125 mm tie data.
    """
    units = {"F": "KN", "L": "m", "T": "C"}
    nodes = {
        "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
        "2": Node(node_id="2", node_tag=2, x=3.5, y=0.0, z=0.0),
        "3": Node(node_id="3", node_tag=3, x=0.0, y=0.0, z=2.0),
        "4": Node(node_id="4", node_tag=4, x=3.5, y=0.0, z=2.0),
        "5": Node(node_id="5", node_tag=5, x=0.0, y=0.0, z=4.0),
        "6": Node(node_id="6", node_tag=6, x=3.5, y=0.0, z=4.0),
    }
    restraints = {"1": Restraint([1, 1, 1, 1, 1, 1]), "2": Restraint([1, 1, 1, 1, 1, 1])}

    materials = {
        "C30": Material(
            name="C30",
            type="Concrete",
            E_mod=23.674e6,  # kPa
            G_mod=9.864e6,
            nu=0.2,
            unit_weight=24.0,
            Fc=30.0e3,  # kPa (30 MPa)
            eFc=1.85e-3,  # strain at peak stress
        ),
        "RebarL": Material(
            name="RebarL",
            type="Rebar",
            E_mod=192.5e6,  # kPa
            Fy=418.0e3,  # kPa
            unit_weight=77.0,
        ),
        "RebarT": Material(
            name="RebarT",
            type="Rebar",
            E_mod=200.0e6,
            Fy=454.0e3,
            unit_weight=77.0,
        ),
    }

    def _section(cover: float, name: str) -> ConcreteRectangularSection:
        return ConcreteRectangularSection(
            name=name,
            shape="Concrete Rectangular",
            material="C30",
            rebar_material="RebarL",
            A=0.12,
            I33=1.6e-3,
            I22=9.0e-4,
            J=2.0e-3,
            depth=0.4,
            bf=0.3,
            cover=cover,
            top_bars=4,
            bot_bars=4,
            top_bar_dia=0.0195,
            bot_bar_dia=0.0195,
            tie_diameter=0.0113,
            tie_spacing=0.125,
            tie_fy=454.0e3,
            tie_rebar_mat="RebarT",
        )

    sections = {"COL": _section(0.051, "COL"), "BEAM": _section(0.041, "BEAM")}

    frame_elements = {
        "1": FrameElement(elem_id="1", elem_tag=1, node_i="1", node_j="3"),
        "2": FrameElement(elem_id="2", elem_tag=2, node_i="2", node_j="4"),
        "3": FrameElement(elem_id="3", elem_tag=3, node_i="3", node_j="5"),
        "4": FrameElement(elem_id="4", elem_tag=4, node_i="4", node_j="6"),
        "5": FrameElement(elem_id="5", elem_tag=5, node_i="3", node_j="4"),
        "6": FrameElement(elem_id="6", elem_tag=6, node_i="5", node_j="6"),
    }
    frame_assignments = {
        "1": "COL",
        "2": "COL",
        "3": "COL",
        "4": "COL",
        "5": "BEAM",
        "6": "BEAM",
    }

    load_patterns = {
        "DEAD": LoadPattern(name="DEAD", pattern_type="Dead", self_weight_factor=1),
        "WIND": LoadPattern(name="WIND", pattern_type="Wind", self_weight_factor=0),
    }
    # 700 kN constant axial per column, applied at the column tops.
    joint_loads = [
        JointLoad(pattern="DEAD", node_id="5", fz=-700.0),
        JointLoad(pattern="DEAD", node_id="6", fz=-700.0),
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
        joint_loads=joint_loads,
        mass_sources=mass_sources,
        units=units,
    )
    md.apply_material_defaults()
    return md


@pytest.fixture
def ve_builder():
    """Preprocessed Vecchio & Emara frame wrapped in an AnalysisBuilder."""
    from openseespy.opensees import wipe

    # Shallow-copy the module-level config so fixture-specific mutations
    # (e.g. aggregate_shear) cannot leak into other tests.
    # All _BENCH_CONFIG values are immutable scalars, so dict() is fully isolating.
    cfg = dict(_BENCH_CONFIG)
    mesh_model = preprocess_model(make_vecchio_emara_frame(), cfg)
    builder = AnalysisBuilder(mesh_model, cfg)
    yield builder
    wipe()


class TestVecchioEmaraBenchmark:
    def test_gravity_joint_loads_applied(self, ve_builder):
        """Regression: joint loads (700 kN/column) reach the OpenSees domain.

        Discovered during Gap 4: SAP2000 "JOINT LOADS - FORCE" were parsed
        and carried through the Preprocessor but never emitted by
        ``create_loads``, so the benchmark's constant column axial loads
        were silently dropped.  The static result's load/reaction check
        must now balance.
        """
        res = None
        try:
            ve_builder.build_domain()
            res = ve_builder.run_static_analysis(
                extract_reactions=True, pattern_scales={"DEAD": 1.0}
            )
        except Exception as exc:
            pytest.fail(f"static analysis failed: {exc}")
        check = res.get("load_reaction_check")
        assert check is not None, "load_reaction_check missing from result"
        # Applied -1400 kN (2 × 700) + self-weight vs. the upward base
        # reaction; the check compares magnitudes, so delta must be ~0.
        assert check["delta"] < 1.0, (
            f"joint loads not applied: applied_fz={check['applied_fz']:.1f}, "
            f"reaction_fz={check['reaction_fz']:.1f}, delta={check['delta']:.2f}"
        )
        # The 700 kN column loads (2×) reach the domain; the magnitude
        # also includes the frame self-weight (≈ 43 kN).
        assert check["applied_fz"] < -1400.0, (
            f"joint loads missing: applied_fz={check['applied_fz']:.1f}"
        )

    @pytest.fixture(scope="class")
    @classmethod
    def pushover_results(cls):
        """62-step flexure-only pushover, run once for the whole class.

        ``test_pushover_converges_to_155mm``,
        ``test_flexure_only_peak_brackets_experiment`` and
        ``test_stiffness_band`` share the identical 62-step push to 155 mm
        at Level 2; running it once per class avoids repeating the domain
        build and 62 push steps three times.  The returned result dict
        holds plain Python/numpy data captured during the run, so the
        OpenSees domain is wiped before any test body executes and the
        function-scoped ``ve_builder`` tests still start from clean state.
        """
        from openseespy.opensees import wipe

        cfg = dict(_BENCH_CONFIG)
        mesh_model = preprocess_model(make_vecchio_emara_frame(), cfg)
        builder = AnalysisBuilder(mesh_model, cfg)
        try:
            return builder.run_pushover_analysis(
                gravity_patterns={"DEAD": 1.0},
                lateral_load_type="uniform",
                lateral_direction="X",
                control_node_tag=5,
                max_disp=0.155,
                num_steps=62,
                print_progress=False,
            )
        finally:
            wipe()

    def test_pushover_converges_to_155mm(self, pushover_results):
        """The experimental loading protocol (155 mm at Level 2) is reached."""
        res = pushover_results
        d = np.asarray(res["control_disp"], dtype=float)
        v = np.asarray(res["base_shear"], dtype=float)
        assert all(s == 0 for s in res["status"]), "non-converged push steps"
        assert abs(d[-1] - 0.155) < 1e-4, f"final disp {d[-1]:.4f} != 155 mm"
        assert np.all(np.diff(d) > 0), "control displacement must ramp monotonically"
        assert np.all(v[1:] > 0), "base shear must stay positive"

    def test_flexure_only_peak_brackets_experiment(self, pushover_results):
        """Peak base shear brackets the experiment (documented flexure-only bias).

        The pure flexure fiber model overestimates the peak (~1.5×) because
        it has no bond-slip / shear / distributed-cracking stiffness
        reduction and its confined + strain-hardening sections do not
        soften as the real frame did.  We assert the model brackets the
        experimental peak and report the ratio for traceability.
        """
        res = pushover_results
        v = np.asarray(res["base_shear"], dtype=float)
        peak = float(np.max(v))
        ratio = peak / EXP_PEAK_SHEAR
        assert 1.0 <= ratio <= 2.0, (
            f"peak {peak:.1f} kN vs experimental {EXP_PEAK_SHEAR:.0f} kN "
            f"(ratio {ratio:.2f}) outside documented flexure-only band"
        )
        # The flexure-only hand-calc first-yield load is comfortably exceeded.
        d = np.asarray(res["control_disp"], dtype=float)
        v30 = float(np.interp(0.030, d, v))
        assert v30 >= HAND_FIRST_YIELD_LOAD, (
            f"V@30mm {v30:.1f} kN below hand-calc first yield {HAND_FIRST_YIELD_LOAD:.0f} kN"
        )

    def test_stiffness_band(self, pushover_results):
        """Secant stiffness at 50 mm within [0.5, 2] × experimental 6.1 kN/mm."""
        res = pushover_results
        d = np.asarray(res["control_disp"], dtype=float)
        v = np.asarray(res["base_shear"], dtype=float)
        v50 = float(np.interp(0.050, d, v))
        k50 = v50 / 0.050  # kN/m
        assert 0.5 * EXP_SECANT_AT_YIELD <= k50 <= 2.0 * EXP_SECANT_AT_YIELD, (
            f"secant @50mm {k50:.0f} kN/m outside "
            f"[{0.5 * EXP_SECANT_AT_YIELD:.0f}, {2.0 * EXP_SECANT_AT_YIELD:.0f}]"
        )

    def test_bilinearize_rc_real_curve(self, ve_builder, pushover_results):
        """P4: De Luca 10 %-secant fit on the real V&E capacity curve.

        Apply :func:`bilinearize_rc` to the actual Gap 4 pushover curve.
        The fitted yield must NOT snap to the cracking transition (the
        FEMA/EC8 bias the De Luca method removes), must sit below the peak
        (ductility > 1), and must be an exact equal-area fit.

        Empirical result (2026-08-24): S_dy ≈ 14 mm (≈ 0.36 % roof drift) —
        past cracking (~2 mm), below the model's rebar-yield drift (~31 mm)
        and conservative for the experiment's ~51 mm yield.  The curve is
        still hardening at the 155 mm end, so the equal-area yield lands
        earlier than a peaked curve would give.
        """
        from fea_toolkit.model.csm import bilinearize_rc

        ve_builder.build_domain()
        ve_builder.compute_seismic_masses()
        modal = ve_builder.run_modal_analysis(num_modes=3)
        shapes = ve_builder.extract_mode_shapes(3)
        adrs = ve_builder.pushover_to_adrs(pushover_results, modal, shapes, direction="X")
        S_d = np.asarray(adrs["S_d"], dtype=float)
        S_a = np.asarray(adrs["S_a"], dtype=float)
        S_dy, S_ay, method = bilinearize_rc(S_d, S_a)

        peak_idx = int(np.argmax(S_a))
        S_d_peak = S_d[peak_idx]
        S_a_peak = S_a[peak_idx]

        # Regular fit, not the degenerate elastic fallback.
        assert method == "de_luca_10pct", method
        # Yield before the peak → ductility > 1.
        assert S_dy < 0.9 * S_d_peak, f"yield at/near peak (S_dy={S_dy:.4g})"
        assert S_ay < S_a_peak, f"yield strength at peak (S_ay={S_ay:.4g})"
        # NOT the cracking transition: yield sits well past the ADRS
        # displacement where strength first reaches 10 % of peak.
        target = 0.1 * S_a_peak
        i_cross = int(np.argmax(S_a >= target))
        S_d_crack = S_d[i_cross]
        assert S_dy > 2.0 * max(S_d_crack, 1e-9), (
            f"yield snapped to cracking transition (S_dy={S_dy:.4g}, cracking at {S_d_crack:.4g})"
        )
        # Exact equal-area fit: relative area error under 1 %.
        A_cap = sum(
            0.5 * (S_d[i] - S_d[i - 1]) * (S_a[i] + S_a[i - 1]) for i in range(1, peak_idx + 1)
        )
        A_bil = (
            0.5 * S_ay * S_dy
            + S_ay * (S_d_peak - S_dy)
            + 0.5 * (S_a_peak - S_ay) * (S_d_peak - S_dy)
        )
        assert abs(A_bil - A_cap) / A_cap < 0.01, (
            f"equal-area error {(A_bil - A_cap) / A_cap:.3%} > 1 %"
        )

    def test_section_moment_matches_response2000(self):
        """The BEAM section alone reproduces the published flexural capacity.

        A standalone moment-curvature of the BEAM section (zero axial) peaks
        near the Response-2000 value of 206 kN·m quoted in Guner (2008)
        §2.3.5.1, confirming the section geometry/materials are faithful —
        the frame-level overestimate is a member-axial/stiffness effect,
        not a section-capacity error.
        """
        from openseespy.opensees import wipe

        from fea_toolkit.model.confinement import ConfinementData, mander_confined

        sec = make_vecchio_emara_frame().sections["BEAM"]
        fpc = 30.0e3
        data = ConfinementData(
            fc=fpc,
            tie_diameter=0.0113,
            tie_spacing=0.125,
            tie_fy=454.0e3,
            core_bc=sec.bf - 2 * sec.cover - 0.0113,
            core_dc=sec.depth - 2 * sec.cover - 0.0113,
            long_diameter=0.0195,
            long_count_x=4,
            long_count_y=2,
            tie_config="standard",
            ecu_max=0.025,
        )
        res = mander_confined(data)

        import openseespy.opensees as ops

        try:
            ops.wipe()
            ops.model("basic", "-ndm", 2, "-ndf", 3)
            ops.node(1, 0.0, 0.0)
            ops.node(2, 0.0, 1.0)
            ops.fix(1, 1, 1, 1)
            ops.uniaxialMaterial("Concrete01", 1, -fpc, -1.85e-3, -0.2 * fpc, -0.006)
            ops.uniaxialMaterial("Concrete01", 2, -res.fcc, -res.ecc, -0.2 * res.fcc, -res.ecu)
            ops.uniaxialMaterial("Steel02", 3, 418.0e3, 192.5e6, 0.01, 18.0, 0.925, 0.15)
            ops.section("Fiber", 1)
            cv = sec.cover
            half_d, half_b = sec.depth / 2.0, sec.bf / 2.0
            ops.patch("rect", 2, 8, 4, -half_d + cv, -half_b + cv, half_d - cv, half_b - cv)
            ops.patch("rect", 1, 8, 1, half_d - cv, -half_b, half_d, half_b)
            ops.patch("rect", 1, 8, 1, -half_d, -half_b, -half_d + cv, half_b)
            ops.patch("rect", 1, 1, 2, -half_d + cv, -half_b, half_d - cv, -half_b + cv)
            ops.patch("rect", 1, 1, 2, -half_d + cv, half_b - cv, half_d - cv, half_b)
            abar = math.pi * (0.0195 / 2.0) ** 2
            ops.layer("straight", 3, 4, abar, half_d - cv, -half_b + cv, half_d - cv, half_b - cv)
            ops.layer("straight", 3, 4, abar, -half_d + cv, -half_b + cv, -half_d + cv, half_b - cv)
            ops.geomTransf("Linear", 1)
            ops.beamIntegration("Lobatto", 100, 1, 5)
            ops.element("forceBeamColumn", 1, 1, 2, 1, 100)
            ops.timeSeries("Linear", 1)
            ops.pattern("Plain", 1, 1)
            ops.load(2, 0.0, 0.0, 1.0)
            ops.system("BandGeneral")
            ops.numberer("RCM")
            ops.constraints("Plain")
            ops.test("NormDispIncr", 1e-6, 20)
            ops.algorithm("Newton")
            ops.integrator("DisplacementControl", 2, 3, 0.002)
            ops.analysis("Static")
            moments = []
            for _ in range(80):
                if ops.analyze(1) != 0:
                    break
                ops.reactions()
                moments.append(abs(ops.nodeReaction(1, 3)))
            assert moments, "M-phi pushover produced no converged steps — max() undefined"
            peak_m = max(moments)
        finally:
            wipe()

        # Response-2000 section capacity 206 kN·m (Guner 2008 §2.3.5.1);
        # allow a generous band for the confinement/hardening choice.
        assert 150.0 <= peak_m <= 260.0, f"BEAM M-phi peak {peak_m:.1f} kN·m out of band"


class TestVecchioEmaraShearFlexibleVariant:
    """Gap 4 close-out (second pass): element-formulation + shear-flexible variant.

    Re-run of the benchmark with the fibre rebuild using ``forceBeamColumn``
    (flexibility-based) instead of ``dispBeamColumn`` (displacement-based)
    and with elastic shear aggregation enabled (``aggregate_shear``).
    Findings:

    1. **``dispBeamColumn`` was part of the bias.**  The displacement-based
       element (Euler-Bernoulli) is over-stiff once the fibre sections
       soften and — critically — never computes section shear deformation,
       so a ``SectionAggregator`` shear spring is *inert* for it.  The
       original flexure-only benchmark (peak ≈ 495 kN, ratio 1.5) is
       dominated by this element-formulation stiffness.
    2. **``forceBeamColumn`` lands inside the original ±10–15 % acceptance
       band without calibration**: peak ≈ 291 kN (0.88 × experimental 330),
       secant @ 50 mm ≈ 5.6 kN/mm (0.93 × experimental 6.1).
    3. **Elastic shear aggregation contributes little for these members**
       (≈ 0.2 % — the elastic ``GA_v`` shear share of a 300 × 400 @ 2 m
       member is only a few percent; the experimental ~20 % shear share is a
       *cracked*-shear phenomenon).  Its role here is to provide the
       section-level shear-DOF mechanism for a future nonlinear shear
       spring.
    4. **Shape caveat:** the forceBeamColumn curve plateaus (no post-peak
       descent) while the experiment softened after ≈ 50 mm — reproducing
       that requires nonlinear cracked-shear degradation / bond-slip
       (deferred, ``docs/_pending_work.md``).
    5. **Rigid joint end zones close the strength/stiffness gap
       (2026-08-16).**  The flexure-only ``forceBeamColumn`` run sits at
       ≈ 0.88 × the experimental peak because the members are modelled
       centreline-to-centreline.  Auto-generating rigid joint zones
       (``rigid_end_zones``, offset = 0.5 × the intersecting member's
       depth) with MPC links (``rigid_link_mpc``) shortens the flexible
       members to the joint faces and lifts the peak to ≈ 1.07 × and the
       secant @ 50 mm to ≈ 1.03 × — inside the original ±10-15 %
       acceptance band.  The post-peak shape limitation remains (the model
       keeps rising instead of descending after ≈ 50 mm).

    The tests below therefore assert the *improved band* (peak ratio
    [0.75, 1.15], secant [0.8, 1.2] × experimental) rather than the loose
    flexure-only bracket.
    """

    @pytest.fixture
    def ve_builder_fbc(self):
        from openseespy.opensees import wipe

        cfg = dict(_BENCH_CONFIG)
        cfg["fiber_element_type"] = "forceBeamColumn"
        cfg["aggregate_shear"] = True
        mesh = preprocess_model(make_vecchio_emara_frame(), cfg)
        builder = AnalysisBuilder(mesh, cfg)
        yield builder
        wipe()

    @pytest.fixture
    def ve_builder_fbc_rigid(self):
        """forceBeamColumn + auto rigid joint end zones (MPC links)."""
        from openseespy.opensees import wipe

        cfg = dict(_BENCH_CONFIG)
        cfg["fiber_element_type"] = "forceBeamColumn"
        cfg["rigid_end_zones"] = True
        cfg["rigid_link_mpc"] = True
        mesh = preprocess_model(make_vecchio_emara_frame(), cfg)
        builder = AnalysisBuilder(mesh, cfg)
        yield builder
        wipe()

    @staticmethod
    def _push(builder):
        return builder.run_pushover_analysis(
            gravity_patterns={"DEAD": 1.0},
            lateral_load_type="uniform",
            lateral_direction="X",
            control_node_tag=5,
            max_disp=0.155,
            num_steps=62,
            print_progress=False,
        )

    def test_forcebeamcolumn_peak_in_band(self, ve_builder_fbc):
        """forceBeamColumn peak falls inside the ±15 % acceptance band.

        291 kN / 330 kN = 0.88 — no longer the 1.5× overestimate, and below
        the experimental peak rather than above it.
        """
        res = self._push(ve_builder_fbc)
        assert all(s == 0 for s in res["status"]), "non-converged push steps"
        v = np.asarray(res["base_shear"], dtype=float)
        peak = float(np.max(v))
        ratio = peak / EXP_PEAK_SHEAR
        assert 0.75 <= ratio <= 1.15, (
            f"forceBeamColumn peak {peak:.1f} kN vs experimental "
            f"{EXP_PEAK_SHEAR:.0f} kN (ratio {ratio:.2f}) outside band"
        )
        assert ratio < 1.0, "forceBeamColumn variant must not over-predict the peak"

    def test_forcebeamcolumn_stiffness_in_band(self, ve_builder_fbc):
        """forceBeamColumn secant stiffness at 50 mm inside [0.8, 1.2]×exp."""
        res = self._push(ve_builder_fbc)
        d = np.asarray(res["control_disp"], dtype=float)
        v = np.asarray(res["base_shear"], dtype=float)
        v50 = float(np.interp(0.050, d, v))
        k50 = v50 / 0.050
        assert 0.8 * EXP_SECANT_AT_YIELD <= k50 <= 1.2 * EXP_SECANT_AT_YIELD, (
            f"forceBeamColumn secant @50mm {k50:.0f} kN/m outside "
            f"[{0.8 * EXP_SECANT_AT_YIELD:.0f}, {1.2 * EXP_SECANT_AT_YIELD:.0f}]"
        )

    def test_rigid_end_zones_lands_in_acceptance_band(self, ve_builder_fbc_rigid):
        """Auto rigid joint end zones put the peak inside the ±10-15 % band.

        With the members shortened to the joint faces (0.5 x connector depth
        via ``rigid_end_zones``) and MPC rigid links (``rigid_link_mpc``),
        the peak moves from ≈ 0.88 x experimental (centreline flexure-only)
        to ≈ 1.07 x and the secant @ 50 mm to ≈ 1.03 x — inside the plan's
        ±10-15 % acceptance band.  The post-peak shape (the model keeps
        rising instead of descending after ≈ 50 mm) remains the documented
        cracked-shear / bond-slip limitation.
        """
        res = self._push(ve_builder_fbc_rigid)
        assert all(s == 0 for s in res["status"]), "non-converged push steps"
        d = np.asarray(res["control_disp"], dtype=float)
        v = np.asarray(res["base_shear"], dtype=float)
        peak = float(np.max(v))
        ratio = peak / EXP_PEAK_SHEAR
        v50 = float(np.interp(0.050, d, v))
        k50 = v50 / 0.050
        assert 0.95 <= ratio <= 1.15, (
            f"rigid-end peak {peak:.1f} kN vs experimental {EXP_PEAK_SHEAR:.0f} "
            f"(ratio {ratio:.2f}) outside acceptance band"
        )
        assert 0.9 * EXP_SECANT_AT_YIELD <= k50 <= 1.15 * EXP_SECANT_AT_YIELD, (
            f"rigid-end secant @50mm {k50:.0f} kN/m outside band"
        )

    @pytest.fixture
    def ve_builder_fbc_concrete02(self):
        """forceBeamColumn + rigid end zones + Concrete02 strain-softening.

        P5 Phase A: the config-gated concrete law (``concrete_material`` =
        ``"Concrete02"``) with core-residual reduction
        (``core_residual_factor`` = 0.02) and an earlier core crushing cap
        (``confined_ecu_max`` = 0.010).  All three knobs default to the
        accepted Concrete01 behaviour, so existing models are unchanged.
        """
        from openseespy.opensees import wipe

        cfg = dict(_BENCH_CONFIG)
        cfg["fiber_element_type"] = "forceBeamColumn"
        cfg["rigid_end_zones"] = True
        cfg["rigid_link_mpc"] = True
        cfg["concrete_material"] = "Concrete02"
        cfg["core_residual_factor"] = 0.02
        cfg["confined_ecu_max"] = 0.010
        mesh = preprocess_model(make_vecchio_emara_frame(), cfg)
        builder = AnalysisBuilder(mesh, cfg)
        yield builder
        wipe()

    def test_concrete02_strain_softening_trims_peak_with_descent(self, ve_builder_fbc_concrete02):
        """P5 Phase A: Concrete02 + core-residual reduction (default-off gate).

        The accepted rigid-end-zone model overestimates the peak (≈ 1.07×)
        and rises monotonically to the 155 mm end.  With Concrete02 and the
        residual-reduction lever, the confined core sheds stress as it
        crushes: the peak lands inside the strength band (≈ 0.97 × 330),
        the secant @ 50 mm stays in band (≈ 0.97 × 6.1), and a real
        post-peak descent appears (peak ≈ 132 mm, ≈ 7 % end drop by
        155 mm).  The full experimental post-peak branch (monotonic ≥ 10 %
        descent from the 40–70 mm band) is NOT yet reproduced — that needs
        the bond-slip / shear-degradation increment (P5 Phase B), tracked
        in ``docs/_pending_work.md``.
        """
        res = self._push(ve_builder_fbc_concrete02)
        assert all(s == 0 for s in res["status"]), "non-converged push steps"
        d = np.asarray(res["control_disp"], dtype=float)
        v = np.asarray(res["base_shear"], dtype=float)
        peak_i = int(np.argmax(v))
        peak = float(v[peak_i])
        peak_d = float(d[peak_i])
        ratio = peak / EXP_PEAK_SHEAR
        v50 = float(np.interp(0.050, d, v))
        k50 = v50 / 0.050
        # Strength in band — the trim moves the 1.07× peak into the band.
        assert 0.9 <= ratio <= 1.05, (
            f"Concrete02 peak {peak:.1f} kN vs experimental "
            f"{EXP_PEAK_SHEAR:.0f} kN (ratio {ratio:.2f}) outside band"
        )
        # Stiffness not regressed by the tension-softening branch.
        assert 0.9 * EXP_SECANT_AT_YIELD <= k50 <= 1.15 * EXP_SECANT_AT_YIELD, (
            f"Concrete02 secant @50mm {k50:.0f} kN/m outside band"
        )
        # Peak moved off the push end (a real descent is present).
        assert peak_d < 0.9 * 0.155, f"peak still at the push end ({peak_d * 1000:.1f} mm)"
        assert v[-1] < peak, "no post-peak descent (V_end >= peak)"

    @pytest.fixture
    def ve_builder_fbc_nlshear(self):
        """forceBeamColumn + auto rigid joint end zones + nonlinear
        simplified-MCFT shear backbone.

        Matches the accepted benchmark configuration (rigid end zones + MPC
        links, as in ``ve_builder_fbc_rigid`` and the Duong benchmark) with
        the nonlinear shear backbone added.  A centreline (no-rigid-zones)
        nonlinear-shear variant does not converge at the gravity stage —
        the nonlinear spring's initial tangent combined with the 700 kN
        column axial makes the first gravity increment ill-conditioned.
        """
        from openseespy.opensees import wipe

        cfg = dict(_BENCH_CONFIG)
        cfg["fiber_element_type"] = "forceBeamColumn"
        cfg["rigid_end_zones"] = True
        cfg["rigid_link_mpc"] = True
        cfg["aggregate_shear"] = "nonlinear"
        mesh = preprocess_model(make_vecchio_emara_frame(), cfg)
        builder = AnalysisBuilder(mesh, cfg)
        yield builder
        wipe()

    def test_nonlinear_shear_variant_stays_in_band(self, ve_builder_fbc_nlshear):
        """Nonlinear MCFT shear on the flexure-critical V&E frame is in-band.

        P5 empirical finding (2026-08-24): the V&E frame is shear-strong
        (flexure-critical by design), so the nonlinear shear backbone is
        essentially inert for the post-peak shape — peak ≈ 348 kN (1.05×)
        vs 353 kN elastic-shear (1.07×), and the curve still rises after
        ≈ 50 mm instead of descending like the experiment.  The nonlinear
        shear mechanism itself is validated on the shear-critical Duong
        frame (``test_duong_benchmark.py`` asserts a ≥ 15 % post-peak
        drop).  This test locks in the in-band, converged result for the
        nonlinear-shear V&E variant.
        """
        res = self._push(ve_builder_fbc_nlshear)
        assert all(s == 0 for s in res["status"]), "non-converged push steps"
        d = np.asarray(res["control_disp"], dtype=float)
        v = np.asarray(res["base_shear"], dtype=float)
        peak = float(np.max(v))
        ratio = peak / EXP_PEAK_SHEAR
        v50 = float(np.interp(0.050, d, v))
        k50 = v50 / 0.050
        assert 0.9 <= ratio <= 1.15, (
            f"nonlinear-shear peak {peak:.1f} kN vs experimental "
            f"{EXP_PEAK_SHEAR:.0f} kN (ratio {ratio:.2f}) outside band"
        )
        assert 0.85 * EXP_SECANT_AT_YIELD <= k50 <= 1.2 * EXP_SECANT_AT_YIELD, (
            f"nonlinear-shear secant @50mm {k50:.0f} kN/m outside band"
        )

    def test_shear_aggregation_warns_inert_for_dispbeam(self, ve_builder):
        """``aggregate_shear`` with ``dispBeamColumn`` warns and is inert.

        Section shear DOFs are only engaged by flexibility-based elements;
        ``dispBeamColumn`` (Euler-Bernoulli) never computes them.  The
        SectionAggregator is present in the domain (6-component section
        deformation) but its shear DOFs stay at zero, so the pushover must
        reproduce the non-aggregated flexure-only baseline (peak ratio
        ≈ 1.5 × experimental) and a warning must explain the mismatch.
        """
        ve_builder.config["aggregate_shear"] = True
        import openseespy.opensees as ops

        with pytest.warns(UserWarning, match="aggregate_shear"):
            ve_builder.rebuild_with_fiber_sections()
        resp = ops.eleResponse(1, "section", 1, "deformation")
        assert len(resp) == 6, "SectionAggregator must be present (6 section DOFs)"
        res = self._push(ve_builder)
        v = np.asarray(res["base_shear"], dtype=float)
        peak = float(np.max(v))
        ratio = peak / EXP_PEAK_SHEAR
        # Inert ⇒ identical to the flexure-only dispBeamColumn baseline.
        assert 1.4 <= ratio <= 1.6, (
            f"expected inert aggregation (peak ratio ≈ 1.5), got {ratio:.2f}"
        )
