"""Tests for SAP2000 member end releases / partial fixity → OpenSees."""

import pytest

from fea_toolkit import SAP2000Parser, preprocess_model
from fea_toolkit.model.sap_data import (
    AreaElement,
    FrameElement,
    FrameRelease,
    Material,
    Node,
    Restraint,
    SAPModelData,
    Section,
)
from fea_toolkit.opensees.releases import (
    emit_release_tcl,
    member_end_stiffness,
    plan_releases,
)

E = 2.0e8  # kN/m²
G = 7.7e7
I_SEC = 1.0e-4  # m⁴ second moment of area
A = 0.01  # m²
UNITS = {"F": "kN", "L": "m", "T": "C"}


def _material():
    return Material("STEEL", "Steel", E_mod=E, G_mod=G, nu=0.3, unit_weight=78.5)


def _section():
    return Section("S", "General", "STEEL", A=A, I33=I_SEC, I22=I_SEC, J=I_SEC)


def _beam(releases, restraints, frames=None):
    """A 6 m beam split at midspan (nodes 1-2-3) with the given releases."""
    nodes = {
        "1": Node("1", 1, 0.0, 0.0, 0.0),
        "2": Node("2", 2, 3.0, 0.0, 0.0),
        "3": Node("3", 3, 6.0, 0.0, 0.0),
    }
    if frames is None:
        frames = {"1": FrameElement("1", 1, "1", "2"), "2": FrameElement("2", 2, "2", "3")}
    return SAPModelData(
        nodes=nodes,
        restraints=restraints,
        materials={"STEEL": _material()},
        sections={"S": _section()},
        frame_elements=frames,
        area_elements={},
        frame_assignments=dict.fromkeys(frames, "S"),
        area_assignments={},
        groups={},
        frame_auto_mesh={},
        frame_releases=releases,
        units=dict(UNITS),
    )


# ═══════════════════════════════════════════════════════════════════
# FrameRelease data model
# ═══════════════════════════════════════════════════════════════════


class TestFrameRelease:
    def test_defaults_have_no_releases(self):
        rel = FrameRelease(frame_id="1")
        assert rel.has_releases is False
        assert rel.has_partial_fixity is False
        assert rel.released_indices("I") == []
        assert rel.retained_indices("I") == [0, 1, 2, 3, 4, 5]

    def test_released_and_retained_indices(self):
        rel = FrameRelease("1", end_i=[0, 0, 0, 0, 1, 1])
        assert rel.released_indices("I") == [4, 5]
        assert rel.retained_indices("I") == [0, 1, 2, 3]
        assert rel.released_labels("I") == ["M2", "M3"]
        assert rel.released_labels("j") == []

    def test_partial_fixity_flag(self):
        rel = FrameRelease("1", end_i=[0, 0, 0, 0, 0, 1], end_i_k=[None] * 5 + [1e4])
        assert rel.has_releases is True
        assert rel.has_partial_fixity is True


# ═══════════════════════════════════════════════════════════════════
# Parser: partial-fixity table
# ═══════════════════════════════════════════════════════════════════


class TestParserPartialFixity:
    def test_partial_fixity_merges_onto_release(self):
        parser = SAP2000Parser("dummy.s2k")
        parser._raw_tables = {
            "FRAME RELEASE ASSIGNMENTS 1 - GENERAL": [
                {"Frame": 1, "M3I": "Yes", "M3J": "No"},
            ],
            "FRAME RELEASE ASSIGNMENTS 2 - PARTIAL FIXITY": [
                {"Frame": 1, "M3I": 50000.0, "M3J": 0.0},
            ],
        }
        releases = parser._get_frame_releases()
        rel = releases["1"]
        assert rel.end_i[5] == 1  # M3 released at I
        assert rel.end_i_k[5] == pytest.approx(50000.0)
        assert rel.end_j[5] == 0  # not released at J
        assert rel.end_j_k[5] is None  # 0 → full release/no spring

    def test_spring_forces_release_flag(self):
        parser = SAP2000Parser("dummy.s2k")
        parser._raw_tables = {
            "FRAME RELEASE ASSIGNMENTS 2 - PARTIAL FIXITY": [{"Frame": 2, "M2J": 1234.0}],
        }
        rel = parser._get_frame_releases()["2"]
        assert rel.end_j[4] == 1
        assert rel.end_j_k[4] == pytest.approx(1234.0)
        assert rel.has_releases is True


# ═══════════════════════════════════════════════════════════════════
# Shared stiffness / plan helpers
# ═══════════════════════════════════════════════════════════════════


class TestReleaseHelpers:
    def test_member_end_stiffness_values(self):
        k = member_end_stiffness(_section(), _material(), 6.0)
        assert k[0] == pytest.approx(E * A / 6.0)  # axial
        assert k[1] == pytest.approx(G * A / 6.0)  # shear
        assert k[3] == pytest.approx(G * I_SEC / 6.0)  # torsion
        assert k[4] == pytest.approx(E * I_SEC / 6.0)  # bending y
        assert k[5] == pytest.approx(E * I_SEC / 6.0)  # bending z

    def test_member_end_stiffness_none_without_section(self):
        assert member_end_stiffness(None, _material(), 6.0) is None
        assert member_end_stiffness(_section(), None, 6.0) is None

    def test_plan_creates_release_nodes_and_endpoints(self):
        md = _beam({"1": FrameRelease("1", end_i=[0, 0, 0, 0, 0, 1])}, {})
        plan = plan_releases(md, {})
        assert len(plan["release_nodes"]) == 1
        rn = plan["release_nodes"][0]
        assert rn["node_id"] == "1_rel_i"
        assert plan["endpoints"]["1"] == ("1_rel_i", "2")
        assert "2" not in plan["endpoints"]  # frame 2 has no release
        end = plan["ends"][0]
        assert end["struct_node_tag"] == 1
        assert [d for d, _ in end["dofs"]] == [1, 2, 3, 4, 5, 6]

    def test_plan_empty_when_disabled(self):
        md = _beam({"1": FrameRelease("1", end_i=[0, 0, 0, 0, 0, 1])}, {})
        assert plan_releases(md, {"apply_releases": False})["ends"] == []

    def test_emit_release_tcl_empty_plan(self):
        assert emit_release_tcl({"ends": [], "release_nodes": []}, 1, 1) == []

    def test_emit_release_tcl_lines(self):
        md = _beam({"1": FrameRelease("1", end_i=[0, 0, 0, 0, 0, 1])}, {})
        plan = plan_releases(md, {})
        lines = emit_release_tcl(plan, start_elem_tag=100, start_mat_tag=5000)

        assert lines[0] == ""
        assert lines[1] == "# ── Member end releases / partial fixity ──"
        zl = [ln for ln in lines if ln.startswith("element zeroLength")]
        assert len(zl) == 1
        assert zl[0].startswith("element zeroLength 100 1 ")
        assert " -dir 1 2 3 4 5 6" in zl[0]
        assert " -orient " in zl[0]
        # One -mat tag per local DOF; materials are deduped by stiffness, so
        # exactly one `uniaxialMaterial Elastic` line per distinct tag.
        mat_tags = zl[0].split("-mat ")[1].split(" -dir ")[0].split()
        assert len(mat_tags) == 6
        uniax = [ln for ln in lines if ln.startswith("uniaxialMaterial Elastic")]
        assert len(uniax) == len(set(mat_tags))
        assert all(ln.startswith("uniaxialMaterial Elastic 5") for ln in uniax)


# ═══════════════════════════════════════════════════════════════════
# AnalysisBuilder integration
# ═══════════════════════════════════════════════════════════════════


def _build(releases, restraints, config=None):
    """Preprocess + build a beam model; returns ``(mesh, builder)``."""
    from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

    md = _beam(releases, restraints)
    mesh = preprocess_model(
        md, {"element_type": "elasticBeamColumn", "split_elements": False, "verbose": False}
    )
    cfg = {"verbose": False}
    cfg.update(config or {})
    builder = AnalysisBuilder(mesh, cfg)
    builder.build_domain()
    return mesh, builder


def _analyse(node_tag, load_z):
    import openseespy.opensees as ops

    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    ops.load(node_tag, 0.0, 0.0, load_z, 0.0, 0.0, 0.0)
    ops.system("BandGen")
    ops.numberer("Plain")
    ops.constraints("Transformation")
    ops.integrator("LoadControl", 1.0)
    ops.algorithm("Newton")
    ops.analysis("Static")
    return ops.analyze(1)


class TestBuilderReleases:
    def test_nodes_and_zero_length_created(self):
        import openseespy.opensees as ops

        try:
            mesh, _b = _build(
                {"1": FrameRelease("1", end_i=[0, 0, 0, 0, 0, 1])},
                {"1": Restraint([1, 1, 1, 1, 1, 1]), "3": Restraint([1, 1, 1, 1, 1, 1])},
            )
            assert "1_rel_i" in mesh.nodes
            assert mesh.frame_elements["1"].node_i == "1_rel_i"
            assert len(ops.getNodeTags()) == 4  # 3 mesh + 1 release
        finally:
            ops.wipe()

    def test_idempotent_double_build(self):
        import openseespy.opensees as ops

        try:
            _mesh, builder = _build(
                {"1": FrameRelease("1", end_i=[0, 0, 0, 0, 0, 1])},
                {"1": Restraint([1, 1, 1, 1, 1, 1]), "3": Restraint([1, 1, 1, 1, 1, 1])},
            )
            n = len(ops.getNodeTags())
            e = len(ops.getEleTags())
            builder.build_domain()
            assert len(ops.getNodeTags()) == n
            assert len(ops.getEleTags()) == e
        finally:
            ops.wipe()

    def test_apply_releases_false_disables(self):
        import openseespy.opensees as ops

        try:
            mesh, _b = _build(
                {"1": FrameRelease("1", end_i=[0, 0, 0, 0, 0, 1])},
                {"1": Restraint([1, 1, 1, 1, 1, 1]), "3": Restraint([1, 1, 1, 1, 1, 1])},
                config={"apply_releases": False},
            )
            assert "1_rel_i" not in mesh.nodes
            assert mesh.frame_elements["1"].node_i == "1"
        finally:
            ops.wipe()


# ═══════════════════════════════════════════════════════════════════
# Physics hand-checks
# ═══════════════════════════════════════════════════════════════════

L = 6.0
P = 100.0
# Outer supports: node 1 also carries a torsional restraint so the model
# stays non-singular; the member ends are released.
_PINNED = {"1": Restraint([1, 1, 1, 1, 0, 0]), "3": Restraint([1, 1, 1, 0, 0, 0])}
_FIXED = {"1": Restraint([1, 1, 1, 1, 1, 1]), "3": Restraint([1, 1, 1, 1, 1, 1])}


class TestReleasePhysics:
    def test_simply_supported_deflection(self):
        """Moment releases at both supports → δ = PL³/48EI."""
        import openseespy.opensees as ops

        try:
            _build(
                {
                    "1": FrameRelease("1", end_i=[0, 0, 0, 0, 1, 1]),
                    "2": FrameRelease("2", end_j=[0, 0, 0, 0, 1, 1]),
                },
                _PINNED,
            )
            assert _analyse(2, -P) == 0
            uz = ops.nodeDisp(2)[2]
            expected = -P * L**3 / (48.0 * E * I_SEC)
            assert uz == pytest.approx(expected, rel=1e-3)
        finally:
            ops.wipe()

    def test_partial_fixity_matches_closed_form(self):
        """Known rotational spring k at both ends vs the closed form."""
        import openseespy.opensees as ops

        try:
            k = 1.0e4
            _build(
                {
                    "1": FrameRelease("1", end_i=[0, 0, 0, 0, 0, 1], end_i_k=[None] * 5 + [k]),
                    "2": FrameRelease("2", end_j=[0, 0, 0, 0, 0, 1], end_j_k=[None] * 5 + [k]),
                },
                _FIXED,
            )
            assert _analyse(2, -P) == 0
            uz = ops.nodeDisp(2)[2]
            m_end = (P * L / 8.0) / (1.0 + 2.0 * E * I_SEC / (k * L))
            expected = -(P * L**3 / (48.0 * E * I_SEC) - m_end * L**2 / (8.0 * E * I_SEC))
            assert uz == pytest.approx(expected, rel=1e-3)
        finally:
            ops.wipe()


# ═══════════════════════════════════════════════════════════════════
# Preprocessor split re-mapping
# ═══════════════════════════════════════════════════════════════════


class TestSplitRemap:
    def test_release_remapped_to_leaf(self):
        from fea_toolkit.opensees.preprocessor import _remap_frame_releases

        parent = FrameElement("S1", 1, "1", "3", inactive=True, child_ids=["S1-0", "S1-1"])
        leaf_i = FrameElement("S1-0", 2, "1", "2", parent_id="S1")
        leaf_j = FrameElement("S1-1", 3, "2", "3", parent_id="S1")
        elements = {"S1": parent, "S1-0": leaf_i, "S1-1": leaf_j}

        # M3 released at the parent I-end, M2 at the parent J-end.
        rel = FrameRelease("S1", end_i=[0, 0, 0, 0, 0, 1], end_j=[0, 0, 0, 0, 1, 0])
        remapped = _remap_frame_releases({"S1": rel}, elements)

        assert set(remapped) == {"S1-0", "S1-1"}
        assert remapped["S1-0"].end_i[5] == 1
        assert remapped["S1-0"].end_j == [0] * 6
        assert remapped["S1-1"].end_j[4] == 1
        assert remapped["S1-1"].end_i == [0] * 6

    def test_unsplit_release_passes_through(self):
        from fea_toolkit.opensees.preprocessor import _remap_frame_releases

        elem = FrameElement("S1", 1, "1", "2")
        rel = FrameRelease("S1", end_i=[0, 0, 0, 0, 0, 1])
        remapped = _remap_frame_releases({"S1": rel}, {"S1": elem})
        assert remapped["S1"] is rel

    def test_release_remapped_when_frame_split_by_shell_subdiv(self):
        """Full ``Preprocessor.run`` keeps releases on active leaves.

        A released column passes through an N×N-subdivided wall panel.  The
        subdivision nodes fall on the column segment, so
        ``_split_frames_at_shell_subdiv`` deactivates the parent and creates
        child elements.  The end release must be re-mapped onto the first
        active leaf — ``MeshModel.frame_releases`` is only ever keyed by
        active elements.
        """
        nodes = {
            "1": Node("1", 1, 0.0, 0.0, -1.0),  # column I-end
            "2": Node("2", 2, 0.0, 0.0, 3.0),  # column J-end
            "3": Node("3", 3, -1.0, 0.0, 0.0),  # wall corner
            "4": Node("4", 4, 1.0, 0.0, 0.0),  # wall corner
            "5": Node("5", 5, 1.0, 0.0, 2.0),  # wall corner
            "6": Node("6", 6, -1.0, 0.0, 2.0),  # wall corner
        }
        md = SAPModelData(
            nodes=nodes,
            restraints={},
            materials={"STEEL": _material()},
            sections={"S": _section(), "WALL": _section()},
            frame_elements={"1": FrameElement("1", 1, "1", "2")},
            area_elements={"S1": AreaElement("S1", 20, ["3", "4", "5", "6"], thickness=0.2)},
            frame_assignments={"1": "S"},
            area_assignments={"S1": "WALL"},
            groups={},
            frame_auto_mesh={"1": {"AutoMesh": True, "AtJoints": True}},
            frame_releases={"1": FrameRelease("1", end_i=[0, 0, 0, 0, 0, 1])},
            units=dict(UNITS),
        )

        mesh = preprocess_model(
            md,
            {
                "element_type": "elasticBeamColumn",
                "split_elements": False,
                "create_shells": True,
                "subdivide_shells": 2,
                "verbose": False,
            },
        )

        # The wall subdivision placed nodes on the column segment, so the
        # parent frame was split by ``_split_frames_at_shell_subdiv``.
        assert mesh.frame_elements["1"].inactive is True

        # Every release key must reference an active OpenSees element.
        assert mesh.frame_releases
        for key in mesh.frame_releases:
            assert key in mesh.frame_elements, f"{key!r} is not a frame element"
            assert not mesh.frame_elements[key].inactive, f"{key!r} is inactive"

        # The I-end release landed on the first leaf of the split chain.
        assert mesh.frame_releases["1-0"].end_i[5] == 1


# ═══════════════════════════════════════════════════════════════════
# Tcl export
# ═══════════════════════════════════════════════════════════════════


class TestTclExport:
    def test_mesh_tcl_emits_releases(self, tmp_path):
        from fea_toolkit.opensees.recorder import export_mesh_model_to_tcl

        md = _beam(
            {"1": FrameRelease("1", end_i=[0, 0, 0, 0, 0, 1])},
            {"1": Restraint([1, 1, 1, 1, 1, 1]), "3": Restraint([1, 1, 1, 1, 1, 1])},
        )
        mesh = preprocess_model(
            md, {"element_type": "elasticBeamColumn", "split_elements": False, "verbose": False}
        )
        out = tmp_path / "mesh.tcl"
        export_mesh_model_to_tcl(mesh, str(out), config={"apply_releases": True})
        text = out.read_text()
        assert "element zeroLength" in text
        assert "Release nodes" in text


# ═══════════════════════════════════════════════════════════════════
# OpenSeesPy / Tcl parity (single shared planner)
# ═══════════════════════════════════════════════════════════════════


class TestPlanParity:
    """The builder and the Tcl export must consume the same plan."""

    def test_builder_and_tcl_agree(self, tmp_path):
        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
        from fea_toolkit.opensees.recorder import export_mesh_model_to_tcl

        md = _beam(
            {
                "1": FrameRelease("1", end_i=[0, 0, 0, 0, 0, 1]),
                "2": FrameRelease("2", end_j=[0, 0, 0, 0, 1, 1]),
            },
            _PINNED,
        )
        mesh = preprocess_model(
            md, {"element_type": "elasticBeamColumn", "split_elements": False, "verbose": False}
        )
        plan = plan_releases(mesh, {})
        assert len(plan["ends"]) == 2  # one released end per frame
        assert len(plan["release_nodes"]) == 2

        # Tcl export (does not mutate the mesh) → one zeroLength per end.
        out = tmp_path / "parity.tcl"
        export_mesh_model_to_tcl(mesh, str(out), config={"apply_releases": True})
        assert out.read_text().count("element zeroLength") == len(plan["ends"])

        import openseespy.opensees as ops

        try:
            AnalysisBuilder(mesh, {"verbose": False}).build_domain()
            created = {nid for nid in mesh.nodes if nid.endswith(("_rel_i", "_rel_j"))}
            assert created == {rn["node_id"] for rn in plan["release_nodes"]}
            # 2 frame elements + 1 zeroLength per released end.
            assert len(ops.getEleTags()) == 2 + len(plan["ends"])
        finally:
            ops.wipe()
