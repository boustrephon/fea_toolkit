"""Tests for the pushover facade control-node selection and solver defaults.

Covers:
1. ``_select_control_node`` — explicit tag/id wins; the default skips
   isolated rooftop appendage levels and picks the node nearest the plan
   centroid of the highest significant floor level.
2. ``AnalysisBuilder`` automatic sparse-solver selection — large meshes
   default to ``UmfPack`` unless the user set ``solver_system`` explicitly.
3. Gravity load/reaction check on a *pinned-base* model — the check must
   count every vertically-restrained node, not just fully-fixed (6-DOF)
   restraints (regression: the Admin Building's 90 pinned column bases
   produced a spurious 78.9 % "mismatch" on an equilibrated model).
"""

import openseespy.opensees as ops
import pytest

from fea_toolkit.model.mesh_model import MeshModel
from fea_toolkit.model.sap_data import (
    ConcreteRectangularSection,
    FrameElement,
    JointLoad,
    LoadPattern,
    Material,
    Node,
    Restraint,
    SAPModelData,
)
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
from fea_toolkit.opensees.preprocessor import preprocess_model
from fea_toolkit.opensees.pushover import _select_control_node


def _mesh_with_levels(level_counts):
    """Build a bare MeshModel with ``{z: n_nodes}`` levels at x = 0..n-1."""
    nodes = {}
    tag = 1
    for z, n in sorted(level_counts.items()):
        for i in range(n):
            nid = f"n{tag}"
            nodes[nid] = Node(node_id=nid, node_tag=tag, x=float(i), y=0.0, z=float(z))
            tag += 1
    return MeshModel(
        nodes=nodes,
        materials={},
        sections={},
        frame_elements={},
        frame_assignments={},
        area_elements={},
        area_assignments={},
        frame_dist_loads=[],
        material_tags={},
        section_tags={},
        shell_sec_tags={},
        shell_sec_variants={},
        frame_element_types={},
        area_element_types={},
        offset_rigid_links=[],
        edge_constraint_args=[],
        edge_loads_from_areas=[],
        loads_only_area_ids=set(),
        base_z=0.0,
        units={"F": "N", "L": "m", "T": "C"},
    )


def _mesh_with_n_nodes(n):
    return _mesh_with_levels({0.0: n})


class TestSelectControlNode:
    def test_skips_appendage_level(self):
        # 20-node base, 100-node main roof, 5-node penthouse on top.
        mm = _mesh_with_levels({0.0: 20, 3.0: 100, 6.0: 5})
        node = _select_control_node(mm)
        # The penthouse (5 < 25 % of 100) and the base (20 < 25) are
        # skipped; the main roof is chosen.
        assert node.z == 3.0

    def test_single_level_picks_centroid(self):
        mm = _mesh_with_levels({0.0: 11})
        node = _select_control_node(mm)
        assert node.z == 0.0
        # Plan centroid at x = 5.0 (0..10) — nearest node.
        assert abs(node.x - 5.0) <= 1.0

    def test_explicit_tag_wins(self):
        mm = _mesh_with_levels({0.0: 10, 3.0: 100, 6.0: 5})
        penthouse = next(n for n in mm.nodes.values() if n.z == 6.0)
        node = _select_control_node(mm, control_node_tag=penthouse.node_tag)
        assert node.node_id == penthouse.node_id

    def test_explicit_id_wins(self):
        mm = _mesh_with_levels({0.0: 10})
        first = next(iter(mm.nodes.values()))
        node = _select_control_node(mm, control_node_id=first.node_id)
        assert node.node_id == first.node_id

    def test_missing_tag_raises(self):
        mm = _mesh_with_levels({0.0: 4})
        with pytest.raises(ValueError):
            _select_control_node(mm, control_node_tag=9999)

    def test_missing_id_raises(self):
        mm = _mesh_with_levels({0.0: 4})
        with pytest.raises(ValueError):
            _select_control_node(mm, control_node_id="nope")

    def test_empty_model_raises(self):
        mm = _mesh_with_levels({})
        with pytest.raises(ValueError):
            _select_control_node(mm)


class TestSparseSolverAutoSelection:
    def teardown_method(self):
        ops.wipe()

    def test_large_mesh_auto_selects_umfpack(self):
        """Meshes at/above SPARSE_SOLVER_NODE_THRESHOLD default to UmfPack."""
        mm = _mesh_with_n_nodes(AnalysisBuilder.SPARSE_SOLVER_NODE_THRESHOLD + 1)
        ab = AnalysisBuilder(mm, {})
        assert ab.config["solver_system"] == "UmfPack"

    def test_small_mesh_keeps_bandgen(self):
        mm = _mesh_with_n_nodes(10)
        ab = AnalysisBuilder(mm, {})
        assert ab.config["solver_system"] == "BandGen"

    def test_explicit_solver_system_wins(self):
        """An explicit user solver_system is never overridden."""
        mm = _mesh_with_n_nodes(AnalysisBuilder.SPARSE_SOLVER_NODE_THRESHOLD + 1)
        ab = AnalysisBuilder(mm, {"solver_system": "BandGen"})
        assert ab.config["solver_system"] == "BandGen"


def _pinned_base_frame() -> SAPModelData:
    """One-bay two-storey frame (kN-m) with PINNED column bases.

    Reuses the Vecchio & Emara geometry from test_rc_benchmark.py but pins
    the base nodes (``(1, 1, 1, 0, 0, 0)``) instead of fixing them.
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
    # Pinned bases: translations fixed, rotations free.
    restraints = {
        "1": Restraint([1, 1, 1, 0, 0, 0]),
        "2": Restraint([1, 1, 1, 0, 0, 0]),
    }
    materials = {
        "C30": Material(
            name="C30",
            type="Concrete",
            E_mod=23.674e6,
            G_mod=9.864e6,
            nu=0.2,
            unit_weight=24.0,
            Fc=30.0e3,
            eFc=1.85e-3,
        ),
        "RebarL": Material(
            name="RebarL",
            type="Rebar",
            E_mod=192.5e6,
            Fy=418.0e3,
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

    def _section(name, cover):
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

    sections = {"COL": _section("COL", 0.051), "BEAM": _section("BEAM", 0.041)}
    frame_elements = {
        "1": FrameElement(elem_id="1", elem_tag=1, node_i="1", node_j="3"),
        "2": FrameElement(elem_id="2", elem_tag=2, node_i="2", node_j="4"),
        "3": FrameElement(elem_id="3", elem_tag=3, node_i="3", node_j="5"),
        "4": FrameElement(elem_id="4", elem_tag=4, node_i="4", node_j="6"),
        "5": FrameElement(elem_id="5", elem_tag=5, node_i="3", node_j="4"),
        "6": FrameElement(elem_id="6", elem_tag=6, node_i="5", node_j="6"),
    }
    frame_assignments = dict.fromkeys(("1", "2", "3", "4"), "COL")
    frame_assignments.update({"5": "BEAM", "6": "BEAM"})
    load_patterns = {
        "DEAD": LoadPattern(name="DEAD", pattern_type="Dead", self_weight_factor=1),
    }
    joint_loads = [
        JointLoad(pattern="DEAD", node_id="5", fz=-700.0),
        JointLoad(pattern="DEAD", node_id="6", fz=-700.0),
    ]
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
        units=units,
    )
    md.apply_material_defaults()
    return md


class TestPinnedBaseReactionCheck:
    def teardown_method(self):
        ops.wipe()

    def test_pinned_bases_balance_reaction_check(self):
        """Gravity reaction check must count every vertically-restrained node.

        The base nodes are pinned (not fully fixed) — the check must still
        balance, otherwise it falsely reports a load/reaction mismatch on an
        equilibrated model (regression from the Admin Building run).
        """
        cfg = {
            "element_type": "elasticBeamColumn",
            "verbose": False,
            "create_shells": False,
        }
        mm = preprocess_model(_pinned_base_frame(), cfg)
        ab = AnalysisBuilder(mm, cfg)
        ab.build_domain()
        try:
            res = ab.run_static_analysis(extract_reactions=True, pattern_scales={"DEAD": 1.0})
        finally:
            ops.wipe()
        check = res.get("load_reaction_check")
        assert check is not None, "load_reaction_check missing from result"
        assert check["applied_fz"] < -1400.0, "joint loads missing from domain"
        assert check["delta"] < 1.0, (
            f"pinned-base model falsely flagged: applied_fz={check['applied_fz']:.1f}, "
            f"reaction_fz={check['reaction_fz']:.1f}, delta={check['delta']:.2f}"
        )
