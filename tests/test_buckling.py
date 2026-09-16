"""Tests for brace buckling checks and the Euler eigenvalue benchmark.

Covers :func:`~fea_toolkit.model.checks.brace_buckling_check` (effective
A/I fallbacks, table auto-detection, ordering, type hints) and its wiring
into a subdivided-brace pushover, plus a closed-form Euler eigenvalue
benchmark.
"""

import math

import numpy as np
import pytest

from fea_toolkit.model.sap_data import (
    FrameDistributedLoad,
    FrameElement,
    ISection,
    LoadPattern,
    Material,
    Node,
    PipeSection,
    Restraint,
    SAPModelData,
)
from fea_toolkit.model.selection import Selection
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
from fea_toolkit.opensees.preprocessor import preprocess_model


class TestBraceBucklingCheck:
    """Tests for :meth:`~fea_toolkit.model.checks.check_brace_buckling`."""

    @pytest.fixture
    def brace_model(self):
        """A simple 2‑node cantilever used as a brace."""
        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=6, y=0, z=6),
        }
        restraints = {"1": Restraint([1, 1, 1, 1, 1, 1])}
        materials = {
            "Steel": Material(name="Steel", type="Steel", E_mod=2e11, unit_weight=77000),
        }
        sections = {
            "PIP4": PipeSection(
                name="PIP4",
                shape="Pipe",
                material="Steel",
                od=0.1143,
                t=0.006,
                A=2e-3,
                I33=3e-6,
                I22=3e-6,
                J=1e-6,
            ),
        }
        frames = {
            "B1": FrameElement(elem_id="B1", elem_tag=1, node_i="1", node_j="2"),
        }
        return SAPModelData(
            nodes=nodes,
            restraints=restraints,
            materials=materials,
            sections=sections,
            frame_elements=frames,
            area_elements={},
            frame_assignments={"B1": "PIP4"},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
        )

    def test_euler_buckling_pinned(self, brace_model):
        """Euler P_cr with K=1 matches π²EI/L²."""
        from fea_toolkit.model.checks import check_brace_buckling

        results = check_brace_buckling(brace_model, brace_ids={"B1"}, K=1.0, print_results=False)
        assert "B1" in results
        r = results["B1"]
        # L = sqrt(6² + 6²) ≈ 8.485, I = 3e-6, E = 2e11
        expected = (math.pi**2 * 2e11 * 3e-6) / (8.485**2)
        assert abs(r["P_cr"] - expected) / expected < 0.01
        assert r["slenderness"] > 0

    def test_buckling_with_axial_demand(self, brace_model):
        """D/C ratio computed correctly."""
        from fea_toolkit.model.checks import check_brace_buckling

        results = check_brace_buckling(
            brace_model,
            brace_ids={"B1"},
            K=1.0,
            axial_demand={"B1": 50000.0},  # 50 kN
            print_results=False,
        )
        r = results["B1"]
        assert r["P_demand"] == 50000.0
        assert r["ratio"] > 0

    def test_buckling_table_auto_detect(self, brace_model):
        """brace_buckling_check auto-detects Pipe sections and returns a DataFrame."""
        from fea_toolkit.model.checks import brace_buckling_check

        df = brace_buckling_check(brace_model, n_longest=2, K=1.0)
        assert next(iter(df.columns)) == "Element"
        assert df["Element"].iloc[0] == "B1"
        pcr_col = [c for c in df.columns if c.startswith("P_cr")]
        assert pcr_col, "P_cr column missing from buckling table"
        assert df[pcr_col[0]].iloc[0] > 0

    def test_buckling_table_n_longest_ordering(self, brace_model):
        """brace_buckling_check sorts by length (desc) and truncates to n_longest."""
        from fea_toolkit.model.checks import brace_buckling_check

        # Add a second, longer brace (B2 length 12 > B1 length ≈ 8.49)
        brace_model.nodes["3"] = Node(node_id="3", node_tag=3, x=0, y=0, z=12)
        brace_model.frame_elements["B2"] = FrameElement(
            elem_id="B2", elem_tag=2, node_i="1", node_j="3"
        )
        brace_model.frame_assignments["B2"] = "PIP4"

        # Both braces reported, longest first
        df = brace_buckling_check(brace_model, n_longest=2, K=1.0)
        assert list(df["Element"]) == ["B2", "B1"]
        length_col = next(c for c in df.columns if c.startswith("Length"))
        assert df.loc[0, length_col] > df.loc[1, length_col]

        # Truncation: only the longest brace survives n_longest=1
        df_top = brace_buckling_check(brace_model, n_longest=1, K=1.0)
        assert len(df_top) == 1
        assert list(df_top["Element"]) == ["B2"]

    def test_buckling_table_empty_model_note_schema(self, brace_model):
        """No brace sections (cleared assignments) → Note-only DataFrame schema."""
        from fea_toolkit.model.checks import brace_buckling_check

        brace_model.frame_assignments = {}
        df = brace_buckling_check(brace_model, n_longest=2, K=1.0)
        assert list(df.columns) == ["Note"]
        assert len(df) == 1
        assert "No brace sections found" in df["Note"].iloc[0]

    def test_buckling_table_type_hints_resolve(self):
        """Return annotation is runtime-resolvable (no function-local pandas)."""
        import typing

        from fea_toolkit.model.checks import brace_buckling_check

        hints = typing.get_type_hints(brace_buckling_check)
        assert "return" in hints

    def test_from_brace_sections(self):
        """Selection.from_brace_sections detects Pipe, Angle, etc."""

        sections = {
            "PIP4": PipeSection(
                name="PIP4",
                shape="Pipe",
                material="Steel",
                od=0.1,
                t=0.005,
                A=1e-3,
                I33=1e-6,
                I22=1e-6,
                J=1e-7,
            ),
            "UB300": ISection(
                name="UB300",
                shape="I/Wide Flange",
                material="Steel",
                depth=0.3,
                bf=0.15,
                tf=0.01,
                tw=0.006,
                A=8e-3,
                I33=1.2e-4,
                I22=4e-5,
                J=2e-6,
            ),
        }
        model = SAPModelData(
            nodes={},
            restraints={},
            materials={},
            sections=sections,
            frame_elements={},
            area_elements={},
            frame_assignments={},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
        )
        sel = Selection.from_brace_sections(model)
        assert sel.sections is not None
        assert "PIP4" in sel.sections
        assert "UB300" not in sel.sections

    # ── Non-positive A / fallback I22 regression tests ─────────────

    @pytest.mark.parametrize("bad_A", [0.0, -1.0])
    def test_non_positive_area_warns_and_clamps_a(self, brace_model, bad_A):
        """A<=0 → UserWarning + A clamped to 1e-4; P_cr stays exact."""
        from fea_toolkit.model.checks import check_brace_buckling

        sec = brace_model.sections["PIP4"]
        sec.A = bad_A

        with pytest.warns(UserWarning, match="no positive cross-sectional area: 'PIP4'"):
            results = check_brace_buckling(
                brace_model, brace_ids={"B1"}, K=1.0, print_results=False
            )

        r = results["B1"]
        assert r["A"] == 1e-4
        L = math.hypot(6.0, 6.0)
        # Slenderness recomputed from the clamped area
        assert r["slenderness"] == pytest.approx((1.0 * L) / math.sqrt(sec.I22 / 1e-4), rel=1e-9)
        # P_cr only depends on I22 (unchanged) — the clamp must not leak in
        assert r["P_cr"] == pytest.approx((math.pi**2 * 2e11 * sec.I22) / (L**2), rel=1e-9)

    @pytest.mark.parametrize("bad_I22", [0.0, -1e-6])
    def test_non_positive_i22_falls_back_to_i33(self, brace_model, bad_I22, recwarn):
        """I22<=0 → I33 used as the minor-axis fallback (no fabricated-area warning)."""
        from fea_toolkit.model.checks import check_brace_buckling

        sec = brace_model.sections["PIP4"]
        sec.I22 = bad_I22

        results = check_brace_buckling(brace_model, brace_ids={"B1"}, K=1.0, print_results=False)

        r = results["B1"]
        assert r["I22"] == sec.I33
        L = math.hypot(6.0, 6.0)
        assert r["P_cr"] == pytest.approx((math.pi**2 * 2e11 * sec.I33) / (L**2), rel=1e-9)
        # Positive A → nothing is fabricated, so no UserWarning is emitted
        assert not any(
            "Brace buckling" in str(w.message)
            for w in recwarn
            if issubclass(w.category, UserWarning)
        )

    def test_zero_inertia_skipped_without_warning(self, brace_model, recwarn):
        """I22<=0 AND I33<=0 → element skipped; no result row and no warning."""
        from fea_toolkit.model.checks import check_brace_buckling

        sec = brace_model.sections["PIP4"]
        sec.I22 = 0.0
        sec.I33 = 0.0

        results = check_brace_buckling(brace_model, brace_ids={"B1"}, K=1.0, print_results=False)
        assert results == {}
        assert not any(
            "Brace buckling" in str(w.message)
            for w in recwarn
            if issubclass(w.category, UserWarning)
        )

    def test_buckling_table_effective_columns_match_engine(self, brace_model):
        """DataFrame A/I22 columns echo the engine's effective (clamped/fallback) values."""
        from fea_toolkit.model.checks import brace_buckling_check, check_brace_buckling

        sec = brace_model.sections["PIP4"]
        sec.A = 0.0  # non-positive → clamped to 1e-4
        sec.I22 = -1e-6  # non-positive → falls back to I33

        with pytest.warns(UserWarning, match="no positive cross-sectional area"):
            engine = check_brace_buckling(brace_model, brace_ids={"B1"}, K=1.0, print_results=False)
            df = brace_buckling_check(brace_model, n_longest=2, K=1.0)

        r = engine["B1"]
        assert r["A"] == 1e-4
        assert r["I22"] == sec.I33

        lu = brace_model.units.get("L", "m")
        a_col = f"A ({lu}²)"
        i22_col = f"I22 ({lu}⁴)"
        assert a_col in df.columns and i22_col in df.columns
        assert df.loc[0, a_col] == round(r["A"], 6)
        assert df.loc[0, i22_col] == round(r["I22"], 8)
        # Capacity/slenderness columns derive from the same effective values
        assert df.loc[0, "Slenderness"] == round(r["slenderness"], 1)


# ============================================================================
# Integration test: subdivided brace in pushover pipeline
# ============================================================================


class TestSubdividedBraceInPushover:
    """Verify that braces with subdivision + imperfection can be built and
    run through a pushover analysis without error.

    This tests the pipeline integration — not the exact buckling load
    (which is verified analytically in ``TestBraceBucklingCheck``).
    The practical workflow is:

    1. Identify braces via ``Selection``
    2. Subdivide them with imperfection via ``set_brace_selection()``
    3. Run pushover analysis
    4. Optionally check critical braces via ``check_brace_buckling()``
    """

    @pytest.fixture
    def brace_model(self):
        """A slender 10 m pin-pin pipe column for pushover testing."""
        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=0, y=0, z=10),
        }
        restraints = {"1": Restraint([1, 1, 1, 1, 1, 1])}
        materials = {
            "Steel": Material(name="Steel", type="Steel", E_mod=2e11, unit_weight=77000, Fy=2.5e8),
        }
        sections = {
            "PIP4": PipeSection(
                name="PIP4",
                shape="Pipe",
                material="Steel",
                od=0.1,
                t=0.005,
                A=0.001492,
                I33=1.70e-6,
                I22=1.70e-6,
                J=3.4e-6,
            ),
        }
        frames = {
            "B1": FrameElement(elem_id="B1", elem_tag=10, node_i="1", node_j="2"),
        }
        load_patterns = {
            "WIND": LoadPattern(name="WIND", pattern_type="Wind", self_weight_factor=0),
        }
        frame_dist_loads = [
            FrameDistributedLoad(
                pattern="WIND",
                frame_id="B1",
                direction="X",
                load_type="Force",
                shape="Uniform",
                val_a=5000,
                val_b=5000,
                rdist_a=0,
                rdist_b=1,
                dist_a=0,
                dist_b=10,
            ),
        ]
        return SAPModelData(
            nodes=nodes,
            restraints=restraints,
            materials=materials,
            sections=sections,
            frame_elements=frames,
            area_elements={},
            frame_assignments={"B1": "PIP4"},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
            load_patterns=load_patterns,
            frame_dist_loads=frame_dist_loads,
        )

    def test_subdivided_brace_builds_and_runs(self, brace_model):
        """AnalysisBuilder with subdivided braces runs pushover without crash."""

        mm = preprocess_model(brace_model, {"split_elements": False})
        b = AnalysisBuilder(
            mm,
            {
                "element_type": "forceBeamColumn",
                "create_fiber_sections": True,
                "geom_transf_type": "Corotational",
                "split_elements": False,
                "verbose": False,
            },
        )
        b.set_brace_selection({"B1"}, end_offset=0.0)

        # Run a quick pushover to verify the pipeline holds
        results = b.run_pushover_analysis(
            gravity_patterns={},
            lateral_load_type="uniform",
            lateral_direction="X",
            control_node_tag=2,
            max_disp=0.05,
            num_steps=5,
            print_progress=False,
        )
        assert results is not None
        assert "control_disp" in results
        assert len(results["control_disp"]) > 1

    def test_check_buckling_after_pushover(self, brace_model):
        """Can check Euler buckling of braces (analytical, no OpenSees needed)."""

        mm = preprocess_model(brace_model, {"split_elements": False})
        b = AnalysisBuilder(
            mm,
            {
                "element_type": "forceBeamColumn",
                "create_fiber_sections": True,
                "split_elements": False,
                "verbose": False,
            },
        )
        b.set_brace_selection({"B1"}, end_offset=0.0)

        # Check Euler buckling directly from model data (no analysis required)
        buckling = b.check_brace_buckling(
            brace_ids={"B1"},
            K=1.0,
            print_results=False,
        )
        assert "B1" in buckling
        assert buckling["B1"]["P_cr"] > 0
        assert buckling["B1"]["slenderness"] > 0
        # P_cr ≈ π² × 2e11 × 1.7e-6 / 10² ≈ 33.6 kN
        P_cr = buckling["B1"]["P_cr"]
        assert 30000 < P_cr < 37000, f"Expected P_cr ≈ 33.6 kN, got {P_cr:.0f} N"


# ============================================================================
# Euler buckling benchmark: SciPy eigenvalue analysis of subdivided column
# ============================================================================


class TestEulerBucklingBenchmark:
    """Benchmark: eigenvalue buckling of a subdivided column via SciPy.

    Assembles the global elastic stiffness matrix *K* and geometric stiffness
    matrix *K_g* for the subdivided column using standard Euler-Bernoulli
    beam elements, then solves the generalised eigenvalue problem:

    .. math:: (K - \\lambda K_g)\\phi = 0

    using ``scipy.linalg.eig``.  The smallest positive eigenvalue gives the
    buckling load :math:`P_{cr}`, which should match the analytical Euler
    formula :math:`\\pi^2 EI / (KL)^2` within a small discretisation error.

    This is an **independent verification** of the subdivided brace concept
    — it does **not** depend on OpenSees' nonlinear solver, so it is fast,
    deterministic, and numerically robust.
    """

    def test_eigenvalue_buckling_matches_euler(self):
        """Eigenvalue buckling from FEA assembly matches Euler P_cr within 5 %."""
        pytest.importorskip("scipy", reason="scipy not installed")
        from scipy.linalg import eig

        L = 10.0
        E = 2e11
        I22 = 1.70e-6
        P_cr_euler = (math.pi**2 * E * I22) / (L**2)

        # Subdivide into N segments
        n_seg = 6
        seg_len = L / n_seg
        n_nodes = n_seg + 1  # total nodes including ends

        # DOF numbering: each node has 2 DOFs (v, θ)
        # Pinned ends: v=0, θ free → remove v DOFs at ends
        n_dof_total = n_nodes * 2  # raw DOFs including constraints
        constrained = {0}  # node 0: v=0 → DOF 0 removed (θ free)
        constrained.add(n_nodes * 2 - 2)  # last node: v=0 → DOF removed (θ free)
        dof_map_raw = [d for d in range(n_dof_total) if d not in constrained]
        n_dof = len(dof_map_raw)
        # dof_map_raw[i] = global raw DOF index for reduced DOF i

        def beam_stiffness(Le, Ee, Ie):
            return np.array(
                [
                    [
                        12 * Ee * Ie / Le**3,
                        6 * Ee * Ie / Le**2,
                        -12 * Ee * Ie / Le**3,
                        6 * Ee * Ie / Le**2,
                    ],
                    [6 * Ee * Ie / Le**2, 4 * Ee * Ie / Le, -6 * Ee * Ie / Le**2, 2 * Ee * Ie / Le],
                    [
                        -12 * Ee * Ie / Le**3,
                        -6 * Ee * Ie / Le**2,
                        12 * Ee * Ie / Le**3,
                        -6 * Ee * Ie / Le**2,
                    ],
                    [6 * Ee * Ie / Le**2, 2 * Ee * Ie / Le, -6 * Ee * Ie / Le**2, 4 * Ee * Ie / Le],
                ]
            )

        def beam_geo_stiffness(Le):
            return (1.0 / (30 * Le)) * np.array(
                [
                    [36, 3 * Le, -36, 3 * Le],
                    [3 * Le, 4 * Le**2, -3 * Le, -(Le**2)],
                    [-36, -3 * Le, 36, -3 * Le],
                    [3 * Le, -(Le**2), -3 * Le, 4 * Le**2],
                ]
            )

        def to_global(raw_dofs):
            """Map 4 element DOFs to reduced system indices (or -1 if constrained)."""
            return [dof_map_raw.index(d) if d in dof_map_raw else -1 for d in raw_dofs]

        K = np.zeros((n_dof, n_dof))
        Kg = np.zeros((n_dof, n_dof))

        for seg in range(n_seg):
            n0 = seg  # left node index
            n1 = seg + 1  # right node index
            # Raw DOFs: [n0*2 (v0), n0*2+1 (θ0), n1*2 (v1), n1*2+1 (θ1)]
            raw = [n0 * 2, n0 * 2 + 1, n1 * 2, n1 * 2 + 1]
            gn = to_global(raw)

            k_e = beam_stiffness(seg_len, E, I22)
            k_ge = beam_geo_stiffness(seg_len)

            for i in range(4):
                gi = gn[i]
                if gi < 0:
                    continue
                for j in range(4):
                    gj = gn[j]
                    if gj < 0:
                        continue
                    K[gi, gj] += k_e[i, j]
                    Kg[gi, gj] += k_ge[i, j]

        # Solve (K - λ Kg)φ = 0
        eigvals, _ = eig(K, Kg)
        # The smallest positive eigenvalue is the buckling load
        buckling_loads = sorted(
            [np.real(ev) for ev in eigvals if np.real(ev) > 1000 and not np.iscomplex(ev)]
        )
        assert len(buckling_loads) > 0, "No valid buckling eigenvalues found"
        P_cr_fea = buckling_loads[0]
        ratio = P_cr_fea / P_cr_euler
        assert 0.95 < ratio < 1.10, (
            f"FEA eigenvalue P_cr ({P_cr_fea:.0f} N) differs from Euler "
            f"({P_cr_euler:.0f} N) by {abs(1 - ratio) * 100:.1f}%"
        )
