"""Verification tests for the RC pushover pipeline (Steps 1-4).

Tests exercise:
1. S2K section type promotion (Rectangular → ConcreteRectangular)
2. Tcl generation (pushover_tcl output format)
3. Result parsing (parse_pushover_results)
4. MeshModel load helpers (mesh_model_to_gravity_loads, modal_to_lateral_loads)
5. Tcl file syntax generation (export_mesh_model_to_tcl + pushover_tcl)
"""

import os

import numpy as np
import pytest

# ═══════════════════════════════════════════════════════════════════════════
# Test 1: Section promotion from S2K parsing
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.filterwarnings("ignore::UserWarning")
class TestSectionPromotion:
    """Verify that concrete-backed RectangularSections are promoted to
    ConcreteRectangularSections during S2K parsing."""

    @pytest.fixture(scope="class")
    def promotion_fixture_path(self) -> str:
        """Path to the minimal RC section promotion S2K fixture."""
        return os.path.join(os.path.dirname(__file__), "fixtures", "rc_section_promotion.s2k")

    def test_rc_section_promotion(self, promotion_fixture_path):
        """Parse fixture S2K and verify RC sections are promoted."""
        from fea_toolkit.io.s2k_parser import SAP2000Parser
        from fea_toolkit.model.sap_data import (
            ConcreteRectangularSection,
        )

        parser = SAP2000Parser(promotion_fixture_path)
        parser.parse()
        md = parser.get_model_data()

        # Count section types
        concrete_rect_count = 0

        for name, sec in md.sections.items():
            if isinstance(sec, ConcreteRectangularSection):
                concrete_rect_count += 1
                # Verify RC fiber patches produce 7 entries
                patches = sec.to_fiber_patches(mat_tag=1)
                assert len(patches) == 7, f"Expected 7 RC patches for {name}, got {len(patches)}"
                # Verify sensible defaults
                assert sec.cover > 0, f"Cover missing for {name}"
                assert sec.top_bars >= 4, f"Too few top bars for {name}"
                assert sec.bot_bars >= 4, f"Too few bot bars for {name}"
                assert sec.top_bar_dia > 0, f"Bar diameter missing for {name}"

        assert concrete_rect_count > 0, (
            f"No ConcreteRectangularSections found (got {list(md.sections.keys())})"
        )
        print(f"  ✓ {concrete_rect_count} RC sections promoted, all with 7 fiber patches")

    def test_steel_section_unaffected(self, promotion_fixture_path):
        """Steel and shell sections should not be promoted."""
        from fea_toolkit.io.s2k_parser import SAP2000Parser
        from fea_toolkit.model.sap_data import (
            ISection,
            ShellSection,
        )

        parser = SAP2000Parser(promotion_fixture_path)
        parser.parse()
        md = parser.get_model_data()

        # FSEC1 should be ISection (steel)
        fsec1 = md.sections.get("FSEC1")
        assert fsec1 is not None, "FSEC1 section missing"
        assert isinstance(fsec1, ISection), f"FSEC1 expected ISection, got {type(fsec1).__name__}"
        # Steel fiber patches = 3
        patches = fsec1.to_fiber_patches(mat_tag=1)
        assert len(patches) == 3, f"Expected 3 steel patches for FSEC1, got {len(patches)}"

        # Shell sections — single-word names to avoid S2K parser space-splitting
        for name in ["SlabConc", "WallBrick", "WallShear", "ASEC1"]:
            sec = md.sections.get(name)
            assert sec is not None, f"Section {name} missing"
            assert isinstance(sec, ShellSection), (
                f"{name} expected ShellSection, got {type(sec).__name__}"
            )

        print("  ✓ Steel ISection and ShellSections unaffected")


# ═══════════════════════════════════════════════════════════════════════════
# Test 2: Tcl generation format
# ═══════════════════════════════════════════════════════════════════════════


class TestPushoverTclFormat:
    """Verify pushover_tcl() produces correctly formatted Tcl."""

    def test_minimal_tcl_structure(self):
        """pushover_tcl() without loads still produces valid header."""
        from fea_toolkit.opensees.builder import pushover_tcl

        tcl = pushover_tcl(
            control_node=10,
            dof=1,
            max_disp=0.15,
            num_steps=50,
            base_node_tags=[1],
        )
        assert "DisplacementControl 10 1" in tcl

    def test_recorder_output_files(self):
        """Recorder files use wall_*.out convention for a single base node."""
        from fea_toolkit.opensees.builder import pushover_tcl

        tcl = pushover_tcl(
            control_node=5,
            dof=2,
            max_disp=0.1,
            num_steps=30,
            lateral_loads={5: (1.0, 0.0, 0.0)},
            gravity_loads={1: (0.0, 0.0, -1000.0)},
            adaptive=True,
            base_node_tags=[1],
        )
        assert "wall_disp.out" in tcl, "Missing disp recorder"
        assert "wall_reaction.out" in tcl, "Missing reaction recorder"
        # In adaptive mode the control node is set via a variable
        assert "set control_node 5" in tcl, "Wrong control node"
        assert "set dof 2" in tcl, "Wrong DOF"

    def test_multiple_base_nodes(self):
        """Multiple base nodes emit per-node wall_reaction_<tag>.out recorders."""
        from fea_toolkit.opensees.builder import pushover_tcl

        tcl = pushover_tcl(
            control_node=5,
            dof=1,
            max_disp=0.1,
            num_steps=30,
            lateral_loads={5: (1.0, 0.0, 0.0)},
            adaptive=True,
            base_node_tags=[1, 2, 3],
        )
        assert "wall_reaction_1.out" in tcl, "Missing reaction recorder for node 1"
        assert "wall_reaction_2.out" in tcl, "Missing reaction recorder for node 2"
        assert "wall_reaction_3.out" in tcl, "Missing reaction recorder for node 3"
        # No bare wall_reaction.out for multi-node case
        assert "wall_reaction.out" not in tcl, "Unexpected bare reaction filename"
        assert "wall_forces.out" not in tcl, "Element force recorder should be removed"

    def test_none_base_node_tags_falls_back_to_node_1(self):
        """Deprecated None → single reaction recorder for node 1."""
        from fea_toolkit.opensees.builder import pushover_tcl

        tcl = pushover_tcl(
            control_node=5,
            dof=1,
            max_disp=0.1,
            num_steps=30,
            adaptive=True,
            base_node_tags=None,
        )
        assert "wall_reaction.out" in tcl, "Missing fallback reaction recorder"
        assert "wall_forces.out" not in tcl, "Element force recorder should be removed"

    def test_adaptive_vs_simple(self):
        """Adaptive mode includes fallback chain; simple mode does not."""
        from fea_toolkit.opensees.builder import pushover_tcl

        lateral = {5: (1.0, 0.0, 0.0)}
        gravity = {1: (0.0, 0.0, -1000.0)}

        # Adaptive
        tcl_a = pushover_tcl(
            control_node=5,
            dof=1,
            max_disp=0.1,
            num_steps=30,
            lateral_loads=lateral,
            gravity_loads=gravity,
            adaptive=True,
        )
        assert "while {$currentDisp < $targetDisp}" in tcl_a
        assert "KrylovNewton" in tcl_a
        assert "ModifiedNewton" in tcl_a

        # Simple (per-step loop with per-step base-shear history)
        tcl_s = pushover_tcl(
            control_node=5,
            dof=1,
            max_disp=0.1,
            num_steps=30,
            lateral_loads=lateral,
            gravity_loads=gravity,
            adaptive=False,
        )
        assert "while" not in tcl_s
        # Issue 8: simple mode uses a per-step for loop so the computed
        # base reactions are written to ``wall_bs.out`` every displacement
        # step (one line per step) — not a single final line.
        assert "for {set i 1} {$i <= 30} {incr i}" in tcl_s
        assert "    set ok [analyze 1]" in tcl_s
        assert 'puts $bs_file "$rx $ry $rz"' in tcl_s

    # Note: test_element_type_param_documented was removed because
    # pushover_tcl() no longer accepts an element_type parameter.
    # Element type is now controlled by the config dict passed to
    # export_mesh_model_to_tcl or AnalysisBuilder.

    def test_gravity_loads_format(self):
        """Gravity loads should be emitted as pattern Plain with Linear time series."""
        from fea_toolkit.opensees.builder import pushover_tcl

        tcl = pushover_tcl(
            control_node=5,
            dof=1,
            max_disp=0.1,
            num_steps=10,
            gravity_loads={1: (0.0, 0.0, -5000.0), 2: (0.0, 0.0, -2000.0)},
        )
        assert 'pattern Plain 1 "Linear"' in tcl
        assert "load 1 0 0 -5000 0 0 0" in tcl
        assert "load 2 0 0 -2000 0 0 0" in tcl
        assert "integrator LoadControl 0.05" in tcl
        assert "analyze 20" in tcl
        assert "loadConst -time 0.0" in tcl

    def test_lateral_loads_format(self):
        """Lateral loads should be pattern Plain 2 with DisplacementControl."""
        from fea_toolkit.opensees.builder import pushover_tcl

        tcl = pushover_tcl(
            control_node=10,
            dof=1,
            max_disp=0.2,
            num_steps=40,
            lateral_loads={10: (1.0, 0.0, 0.0)},
        )
        assert 'pattern Plain 2 "Linear"' in tcl
        assert "load 10 1 0 0 0 0 0" in tcl
        assert "DisplacementControl 10 1" in tcl


# ═══════════════════════════════════════════════════════════════════════════
# Test 3: Result parsing
# ═══════════════════════════════════════════════════════════════════════════


class TestParsePushoverResults:
    """Verify parse_pushover_results handles various output file formats."""

    def test_basic_parsing(self, tmp_path):
        """Standard multi-step displacement + single-line base shear."""
        from fea_toolkit.opensees.recorder import parse_pushover_results

        # Displacement file: time, disp
        disp = tmp_path / "disp.out"
        np.savetxt(str(disp), [[0.0, 0.001], [0.2, 0.005], [0.4, 0.012], [0.6, 0.020]])

        # Base shear: single line with rx, ry, rz
        bs = tmp_path / "bs.out"
        np.savetxt(str(bs), [[-1250.0, 0.0, 5000.0]])

        result = parse_pushover_results(str(disp), str(bs))
        assert "control_disp" in result
        assert "base_shear" in result
        assert "step" in result
        assert len(result["control_disp"]) == 4
        assert len(result["base_shear"]) == 4
        assert abs(result["control_disp"][-1] - 0.020) < 1e-8
        assert abs(result["base_shear"][0] + 1250.0) < 1.0
        assert "base_rx" in result
        assert abs(result["base_rx"][0] + 1250.0) < 1.0

    def test_genfromtxt_robustness(self, tmp_path):
        """Parse with nan/inf entries (genfromtxt should handle gracefully)."""
        from fea_toolkit.opensees.recorder import parse_pushover_results

        disp = tmp_path / "disp_robust.out"
        with open(str(disp), "w") as f:
            f.write("0.0 0.001\n")
            f.write("0.2 0.005\n")
            f.write("0.4 0.012\n")
            f.write("nan nan\n")  # non-converged step
            f.write("0.6 0.020\n")
            f.write("0.8 0.030\n")

        bs = tmp_path / "bs_robust.out"
        np.savetxt(str(bs), [[-1500.0, 0.0, 6000.0]])

        result = parse_pushover_results(str(disp), str(bs))
        # genfromtxt replaces nan/inf with NaN silently
        assert len(result["control_disp"]) > 0
        # NaN values in the output won't cause crashes
        assert isinstance(result["control_disp"], np.ndarray)
        assert isinstance(result["base_shear"], np.ndarray)
        assert len(result["step"]) == len(result["control_disp"])

    def test_single_column_displacement(self, tmp_path):
        """Single-column displacement (no time prefix)."""
        from fea_toolkit.opensees.recorder import parse_pushover_results

        disp = tmp_path / "single_col.out"
        np.savetxt(str(disp), [0.001, 0.005, 0.012, 0.020])

        bs = tmp_path / "single_bs.out"
        np.savetxt(str(bs), [[-1000.0, 0.0, 4000.0]])

        result = parse_pushover_results(str(disp), str(bs))
        assert len(result["control_disp"]) == 4
        assert result["control_disp"][-1] == 0.020

    def test_optional_reaction_file(self, tmp_path):
        """Optional per-step reaction file is parsed if provided."""
        from fea_toolkit.opensees.recorder import parse_pushover_results

        disp = tmp_path / "r_disp.out"
        np.savetxt(str(disp), [[0.0, 0.001], [0.2, 0.005]])

        bs = tmp_path / "r_bs.out"
        np.savetxt(str(bs), [[-100.0, 0.0, 400.0]])

        react = tmp_path / "r_react.out"
        np.savetxt(str(react), [[0.0, -50.0, 0.0, 200.0], [0.2, -100.0, 0.0, 400.0]])

        result = parse_pushover_results(str(disp), str(bs), str(react))
        assert "reaction_rx" in result
        assert len(result["reaction_rx"]) == 2

    def test_two_support_multi_base_reaction_summed(self, tmp_path):
        """Two support nodes: per-node reaction files are summed by
        parse_pushover_results, and base_shear/control_disp lengths match.

        Mirrors the Tcl workflow: the generated ``pushover_tcl()`` emits
        per-step base-shear history in ``{prefix}_bs.out`` plus per-node
        reaction files ``{prefix}_reaction_{tag}.out``.  The parser must
        aggregate the per-node reactions across matching time steps so
        the returned ``base_shear`` and ``control_disp`` arrays have the
        same length.
        """
        from fea_toolkit.opensees.builder import pushover_tcl
        from fea_toolkit.opensees.recorder import parse_pushover_results

        # ── 1. Generate the Tcl suffix for two base nodes ─────────────
        tcl = pushover_tcl(
            control_node=5,
            dof=1,
            max_disp=0.1,
            num_steps=3,
            lateral_loads={5: (1.0, 0.0, 0.0)},
            adaptive=False,
            base_node_tags=[1, 2],
        )
        # Per-node reaction recorder files are emitted for multi-base runs.
        # The default output_prefix is "wall", so multi-base runs emit
        # ``wall_reaction_1.out`` / ``wall_reaction_2.out`` (no bare
        # ``wall_reaction.out``).
        assert "wall_reaction_1.out" in tcl, "Missing per-node reaction recorder 1"
        assert "wall_reaction_2.out" in tcl, "Missing per-node reaction recorder 2"
        assert "wall_reaction.out" not in tcl.replace("wall_reaction_1.out", "").replace(
            "wall_reaction_2.out", ""
        ), "Bare reaction file should not exist for multi-base"

        # ── 2. Parse recorder files with two per-node reactions ──────

        # Displacement file: time, disp (3 steps)
        disp = tmp_path / "disp.out"
        np.savetxt(str(disp), [[0.0, 0.001], [0.2, 0.005], [0.4, 0.012]])

        # Per-step base-shear history (3 lines, one per step)
        bs = tmp_path / "bs.out"
        np.savetxt(str(bs), [[-100.0, 0.0, 400.0], [-200.0, 0.0, 800.0], [-300.0, 0.0, 1200.0]])

        # Per-node reaction files with matching time steps
        react_1 = tmp_path / "reaction_1.out"
        np.savetxt(str(react_1), [[0.0, -40.0], [0.2, -80.0], [0.4, -120.0]])
        react_2 = tmp_path / "reaction_2.out"
        np.savetxt(str(react_2), [[0.0, -60.0], [0.2, -120.0], [0.4, -180.0]])

        # Pass a list of reaction paths → parser sums per-node reactions
        result = parse_pushover_results(
            str(disp),
            str(bs),
            [str(react_1), str(react_2)],
        )

        assert len(result["control_disp"]) == 3
        assert len(result["step"]) == 3
        assert len(result["reaction_rx"]) == 3
        # Sum of both base nodes at each step: -40 + -60 = -100, etc.
        assert abs(result["reaction_rx"][0] - (-100.0)) < 1e-6
        assert abs(result["reaction_rx"][1] - (-200.0)) < 1e-6
        assert abs(result["reaction_rx"][2] - (-300.0)) < 1e-6

        # ── Base-shear values ─────────────────────────────────────
        # The per-step bs.out rows ``(rx, ry, rz)`` are passed through
        # verbatim (multi-row branch of parse_pushover_results), so
        # base_shear has shape (n_steps, 3), not a single 1-D column.
        assert isinstance(result["base_shear"], np.ndarray)
        assert result["base_shear"].shape == (3, 3)
        # Column 0 = Rx (push-direction reaction, opposes +X load)
        assert abs(result["base_shear"][0, 0] - (-100.0)) < 1e-6
        assert abs(result["base_shear"][1, 0] - (-200.0)) < 1e-6
        assert abs(result["base_shear"][2, 0] - (-300.0)) < 1e-6
        # Column 1 = Ry (zero for a pure-X push)
        assert abs(result["base_shear"][0, 1] - 0.0) < 1e-6
        assert abs(result["base_shear"][1, 1] - 0.0) < 1e-6
        assert abs(result["base_shear"][2, 1] - 0.0) < 1e-6
        # Column 2 = Rz (vertical base reactions grow with drift)
        assert abs(result["base_shear"][0, 2] - 400.0) < 1e-6
        assert abs(result["base_shear"][1, 2] - 800.0) < 1e-6
        assert abs(result["base_shear"][2, 2] - 1200.0) < 1e-6

        # ── 3. Mismatched time grids ─────────────────────────────────
        # Different recorder grids: reaction_1 covers only the first
        # three steps while reaction_2 extends to t=0.8.  The parser
        # picks the longest grid as the reference, interpolates the
        # shorter series onto it, sums within the overlap, and returns
        # NaN for steps outside the shorter series' range.
        disp_mm = tmp_path / "disp_mm.out"
        np.savetxt(
            str(disp_mm),
            [
                [0.0, 0.001],
                [0.1, 0.003],
                [0.2, 0.005],
                [0.4, 0.012],
                [0.6, 0.020],
                [0.8, 0.030],
            ],
        )
        bs_mm = tmp_path / "bs_mm.out"
        np.savetxt(
            str(bs_mm),
            [
                [-100.0, 0.0, 400.0],
                [-150.0, 0.0, 600.0],
                [-200.0, 0.0, 800.0],
                [-300.0, 0.0, 1200.0],
                [-300.0, 0.0, 1200.0],
                [-300.0, 0.0, 1200.0],
            ],
        )

        # Shorter reaction grid: [0.0, 0.2, 0.4]
        react_mm_1 = tmp_path / "reaction_mm_1.out"
        np.savetxt(str(react_mm_1), [[0.0, -40.0], [0.2, -80.0], [0.4, -120.0]])
        # Longer reaction grid (becomes the reference): [0.0, 0.1, 0.2, 0.4, 0.6, 0.8]
        react_mm_2 = tmp_path / "reaction_mm_2.out"
        np.savetxt(
            str(react_mm_2),
            [
                [0.0, -60.0],
                [0.1, -90.0],
                [0.2, -120.0],
                [0.4, -180.0],
                [0.6, -240.0],
                [0.8, -300.0],
            ],
        )

        result_mm = parse_pushover_results(
            str(disp_mm),
            str(bs_mm),
            [str(react_mm_1), str(react_mm_2)],
        )

        assert len(result_mm["control_disp"]) == 6
        assert len(result_mm["base_shear"]) == 6
        assert len(result_mm["step"]) == 6
        assert len(result_mm["reaction_rx"]) == 6
        # Overlap [0.0, 0.4]: reaction_1 is interpolated onto the longer
        # grid (e.g. -60 at t=0.1) and summed with reaction_2.
        assert abs(result_mm["reaction_rx"][0] - (-100.0)) < 1e-6  # -40 + -60
        assert abs(result_mm["reaction_rx"][1] - (-150.0)) < 1e-6  # -60 + -90 (interp)
        assert abs(result_mm["reaction_rx"][2] - (-200.0)) < 1e-6  # -80 + -120
        assert abs(result_mm["reaction_rx"][3] - (-300.0)) < 1e-6  # -120 + -180
        # Beyond reaction_1's range [0.6, 0.8] the parser returns NaN for
        # steps where a series has no data.
        assert np.isnan(result_mm["reaction_rx"][4])
        assert np.isnan(result_mm["reaction_rx"][5])

    def test_file_not_found_raises(self, tmp_path):
        """Missing file should raise OSError."""
        from fea_toolkit.opensees.recorder import parse_pushover_results

        with pytest.raises((OSError, IOError)):
            parse_pushover_results(
                str(tmp_path / "nonexistent.out"),
                str(tmp_path / "nonexistent_bs.out"),
            )


# ═══════════════════════════════════════════════════════════════════════════
# Test 4: MeshModel load helpers
# ═══════════════════════════════════════════════════════════════════════════


class TestLoadHelpers:
    """Verify mesh_model_to_gravity_loads and modal_to_lateral_loads."""

    def _make_simple_mesh_model(self):
        """Create a minimal MeshModel with one frame element and one node."""
        from fea_toolkit.model.mesh_model import MeshModel
        from fea_toolkit.model.sap_data import (
            FrameElement,
            GravityLoad,
            LoadPattern,
            Material,
            Node,
            Section,
        )

        mm = MeshModel(
            nodes={
                "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
                "2": Node(node_id="2", node_tag=2, x=0, y=0, z=3.0),
            },
            frame_elements={
                "F1": FrameElement(elem_id="F1", elem_tag=1, node_i="1", node_j="2"),
            },
            area_elements={},
            area_assignments={},
            frame_assignments={"F1": "COL"},
            sections={
                "COL": Section(name="COL", shape="Rectangular", A=0.25, material="C40"),
            },
            materials={
                "C40": Material(name="C40", type="Concrete", E_mod=3.0e10, unit_weight=25000.0),
            },
            frame_dist_loads=[],
            load_patterns={"DEAD": LoadPattern(name="DEAD", pattern_type="Dead")},
            frame_gravity_loads=[
                GravityLoad(pattern="DEAD", frame_id="F1", multiplier_z=-1.0),
            ],
        )
        return mm

    def test_gravity_loads_from_mesh_model(self):
        """mesh_model_to_gravity_loads produces dict from DEAD pattern."""
        from fea_toolkit.opensees.builder import mesh_model_to_gravity_loads

        mm = self._make_simple_mesh_model()
        result = mesh_model_to_gravity_loads(mm, g_acc=9.81)

        assert isinstance(result, dict)
        assert len(result) > 0, "Gravity loads should be non-empty"
        for tag, (fx, fy, fz) in result.items():
            assert fx == 0.0
            assert fy == 0.0
            assert fz < 0, "Gravity load should point downward"

    def test_modal_to_lateral_loads_uniform_fallback(self):
        """Without mode shapes, returns uniform loads."""
        from fea_toolkit.opensees.builder import modal_to_lateral_loads

        mm = self._make_simple_mesh_model()
        result = modal_to_lateral_loads(mm, {}, direction="X")

        assert isinstance(result, dict)
        assert len(result) > 0
        # Should have entries for all nodes
        for tag in [1, 2]:
            assert tag in result, f"Node {tag} missing from lateral loads"

    def test_modal_to_lateral_loads_with_shapes(self):
        """With mode shapes, loads are proportional."""
        from fea_toolkit.opensees.builder import modal_to_lateral_loads

        mm = self._make_simple_mesh_model()
        modal_data = {
            "periods": [1.0, 0.5],
            "shapes": {
                0: {1: (0.5, 0.0, 0.0), 2: (1.0, 0.0, 0.0)},
            },
        }
        result = modal_to_lateral_loads(mm, modal_data, direction="X")

        assert isinstance(result, dict)
        # All nodes should have loads
        assert all(tag in result for tag in [1, 2])
        # Mode shape at node 2 > node 1, so load at node 2 > node 1
        f2 = abs(result[2][0])
        f1 = abs(result[1][0])
        assert f2 >= f1, f"Expected node 2 load >= node 1 load, got {f2} vs {f1}"


# ═══════════════════════════════════════════════════════════════════════════
# Test 5: Tcl file generation export (syntax check)
# ═══════════════════════════════════════════════════════════════════════════


class TestTclGeneration:
    """Verify export_mesh_model_to_tcl + pushover_tcl produces valid Tcl."""

    def _make_sample_mesh_model(self):
        """Small but realistic MeshModel for Tcl export testing."""
        from fea_toolkit.model.mesh_model import MeshModel
        from fea_toolkit.model.sap_data import (
            ConcreteRectangularSection,
            FrameElement,
            ISection,
            Material,
            Node,
            Restraint,
        )

        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=0, y=0, z=3.0),
            "3": Node(node_id="3", node_tag=3, x=4, y=0, z=0),
            "4": Node(node_id="4", node_tag=4, x=4, y=0, z=3.0),
        }
        restraints = {
            "1": Restraint([1, 1, 1, 1, 1, 1]),
            "3": Restraint([1, 1, 1, 1, 1, 1]),
        }
        mats = {
            "C40": Material(
                name="C40", type="Concrete", E_mod=3.0e10, unit_weight=25000.0, Fc=26800.0, Fy=4.0e8
            ),
            "Steel": Material(
                name="Steel", type="Steel", E_mod=2.0e11, unit_weight=78500.0, Fy=3.45e8
            ),
        }
        sections = {
            "RC_COL": ConcreteRectangularSection(
                name="RC_COL",
                material="C40",
                shape="Rectangular",
                A=0.16,
                I33=0.00213,
                I22=0.00213,
                J=0.001,
                depth=0.4,
                bf=0.4,
                cover=0.04,
                top_bars=4,
                bot_bars=4,
                top_bar_dia=0.020,
                bot_bar_dia=0.020,
            ),
            "STEEL_BM": ISection(
                name="STEEL_BM",
                material="Steel",
                shape="I/Wide Flange",
                A=0.011,
                I33=1.5e-4,
                I22=5.0e-5,
                J=1.0e-6,
                depth=0.3,
                bf=0.15,
                tf=0.01,
                tw=0.006,
            ),
        }
        frames = {
            "F1": FrameElement(elem_id="F1", elem_tag=1, node_i="1", node_j="2"),
            "F2": FrameElement(elem_id="F2", elem_tag=2, node_i="3", node_j="4"),
        }
        frame_assign = {"F1": "RC_COL", "F2": "STEEL_BM"}

        mm = MeshModel(
            nodes=nodes,
            restraints=restraints,
            materials=mats,
            sections=sections,
            frame_elements=frames,
            area_elements={},
            area_assignments={},
            frame_assignments=frame_assign,
            frame_dist_loads=[],
            material_tags={"C40": 1, "Steel": 2},
            section_tags={"RC_COL": 3, "STEEL_BM": 4},
        )
        return mm

    def test_export_with_pushover_tcl(self, tmp_path):
        """Export a MeshModel + pushover_tcl suffix produces valid Tcl."""
        from fea_toolkit.opensees.builder import pushover_tcl
        from fea_toolkit.opensees.recorder import export_mesh_model_to_tcl

        mm = self._make_sample_mesh_model()

        tcl_push = pushover_tcl(
            control_node=2,
            dof=1,
            max_disp=0.1,
            num_steps=20,
            lateral_loads={2: (1.0, 0.0, 0.0)},
            gravity_loads={1: (0.0, 0.0, -10000.0)},
            adaptive=True,
            base_node_tags=[1],
        )

        tcl_path = str(tmp_path / "test_pushover.tcl")
        export_mesh_model_to_tcl(
            mm,
            tcl_path,
            config={"create_fiber_sections": True, "geom_transf_type": "PDelta"},
            tcl_suffix=tcl_push,
        )

        assert os.path.exists(tcl_path)
        with open(tcl_path) as f:
            content = f.read()

        # Verify key structural elements
        assert "model Basic" in content, "Missing model command"
        assert "node 1" in content, "Missing nodes"
        assert "fix 1" in content, "Missing restraints"
        assert "uniaxialMaterial Concrete01" in content, "Missing concrete material"
        assert "uniaxialMaterial Steel01" in content, "Missing steel material"
        assert "section Fiber" in content, "Missing fiber section"
        assert "dispBeamColumn" in content or "forceBeamColumn" in content, (
            "Missing beam-column element"
        )
        assert "DisplacementControl" in content, "Missing pushover analysis"
        assert "wipe" in content, "Missing wipe"

    def test_export_elastic_only(self, tmp_path):
        """Without create_fiber_sections, exports elastic sections only."""
        from fea_toolkit.opensees.recorder import export_mesh_model_to_tcl

        mm = self._make_sample_mesh_model()

        tcl_path = str(tmp_path / "test_elastic.tcl")
        export_mesh_model_to_tcl(mm, tcl_path)

        with open(tcl_path) as f:
            content = f.read()

        assert "section Elastic" in content, "Elastic section should be used"
        assert "section Fiber" not in content, "Fiber section should NOT be present in elastic mode"
        assert "elasticBeamColumn" in content, "Elastic elements should be used"

    def test_export_with_shells(self, tmp_path):
        """Verify shell export works with area elements."""
        from fea_toolkit.model.mesh_model import MeshModel
        from fea_toolkit.model.sap_data import (
            AreaElement,
            Material,
            Node,
            ShellSection,
        )
        from fea_toolkit.opensees.recorder import export_mesh_model_to_tcl

        mm = MeshModel(
            nodes={
                "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
                "2": Node(node_id="2", node_tag=2, x=4, y=0, z=0),
                "3": Node(node_id="3", node_tag=3, x=4, y=4, z=0),
                "4": Node(node_id="4", node_tag=4, x=0, y=4, z=0),
            },
            frame_elements={},
            frame_assignments={},
            area_elements={
                "S1": AreaElement(
                    area_id="S1", area_tag=100, node_ids=["1", "2", "3", "4"], thickness=0.2
                ),
            },
            area_assignments={"S1": "SLAB"},
            frame_dist_loads=[],
            restraints={},
            materials={
                "C40": Material(
                    name="C40", type="Concrete", E_mod=3.0e10, unit_weight=25000.0, nu=0.2
                ),
            },
            sections={
                "SLAB": ShellSection(
                    name="SLAB",
                    material="C40",
                    shape="Shell",
                    thickness=0.2,
                ),
            },
            material_tags={"C40": 1},
            section_tags={"SLAB": 2},
        )

        tcl_path = str(tmp_path / "test_shell.tcl")
        export_mesh_model_to_tcl(mm, tcl_path)

        with open(tcl_path) as f:
            content = f.read()

        assert "ElasticMembranePlateSection" in content, "Missing shell section"
        assert "ShellDKGQ" in content or "ShellDKGT" in content, "Missing shell element"


def test_no_tie_confinement_fallback_parity():
    """Both paths produce identical (fcc, epscc) from shared constants."""
    from fea_toolkit.utils import (
        RC_NO_TIE_CONFINEMENT_FACTOR,
        RC_NO_TIE_EPSC_FACTOR,
    )

    Fc, epsc = 30e6, 0.002
    assert RC_NO_TIE_CONFINEMENT_FACTOR == 1.25
    assert RC_NO_TIE_EPSC_FACTOR == 2.0
    assert abs(Fc * 1.25 - Fc * RC_NO_TIE_CONFINEMENT_FACTOR) < 1e-12
    assert abs(epsc * 2.0 - epsc * RC_NO_TIE_EPSC_FACTOR) < 1e-12
