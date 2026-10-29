"""Tests for SAP2000 member end releases / partial fixity → OpenSees."""

import pytest

from fea_toolkit import SAP2000Parser, preprocess_model
from fea_toolkit.model.sap_data import (
    AreaElement,
    FrameElement,
    FrameRelease,
    ISection,
    Material,
    Node,
    PipeSection,
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
            mesh, b = _build(
                {"1": FrameRelease("1", end_i=[0, 0, 0, 0, 0, 1])},
                {"1": Restraint([1, 1, 1, 1, 1, 1]), "3": Restraint([1, 1, 1, 1, 1, 1])},
            )
            # Release nodes / re-pointing live on the builder, not the mesh (§3.2).
            assert "1_rel_i" in b._release_nodes
            assert b._release_repoints["1"]["I"] == "1_rel_i"
            assert "1_rel_i" not in mesh.nodes  # mesh left un-mutated
            assert mesh.frame_elements["1"].node_i == "1"
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

    def test_release_under_lumped_models_the_pin(self):
        """A released end under ``hinge_model='lumped'`` still gets its pin.

        The hinge loop skips the released end (no hinge spring there) while the
        release route inserts the ``zeroLength`` pin and re-points the released
        end onto its ``*_rel_*`` node; the moment end keeps its hinge spring.
        """
        import openseespy.opensees as ops

        try:
            mesh, b = _build(
                {"1": FrameRelease("1", end_i=[0, 0, 0, 0, 0, 1])},
                {"1": Restraint([1, 1, 1, 1, 1, 1]), "3": Restraint([1, 1, 1, 1, 1, 1])},
                config={"hinge_model": "lumped", "hinge_backbone": "bilinear"},
            )
            # The released I-end is re-pointed to its release node on the builder…
            assert b._release_repoints["1"]["I"] == "1_rel_i"
            assert "1_rel_i" in b._release_nodes
            assert "1_rel_i" not in mesh.nodes  # mesh left un-mutated
            # …and the hinge route leaves the released end alone (no spring there).
            assert b._hinge_repoints["1"][0] == "1"
            # The domain carries the release zeroLength on top of the frames.
            assert len(ops.getEleTags()) >= 3  # 2 frames + ≥1 release zeroLength
        finally:
            ops.wipe()

    def test_non_m3_release_merges_into_hinge_spring(self):
        """A non-M3 release + a hinge at one end merge into a single spring.

        An axial (P) release leaves M3 retained, so the lumped-hinge loop emits
        a spring at that end.  The released DOF is folded into that same spring
        (no second series release element), and the member attaches directly to
        the hinge node instead of a separate release node.
        """
        import openseespy.opensees as ops

        try:
            mesh, b = _build(
                {"1": FrameRelease("1", end_i=[1, 0, 0, 0, 0, 0])},  # P released at I
                {"1": Restraint([1, 1, 1, 1, 1, 1]), "3": Restraint([1, 1, 1, 1, 1, 1])},
                config={"hinge_model": "lumped", "hinge_backbone": "bilinear"},
            )
            # The released end is folded into the hinge spring…
            assert b._combined_release_ends == {("1", "I")}
            # …so no standalone release node or re-point is created for it.
            assert "1_rel_i" not in b._release_nodes
            assert "1" not in b._release_repoints
            # The member's I-end attaches to the hinge node instead.
            hinge_id = b._hinge_repoints["1"][0]
            assert hinge_id in b._hinge_nodes
            assert b._resolve_frame_endpoints(mesh.frame_elements["1"])[0].node_id == hinge_id
            # 2 frames + 4 hinge springs (the node-1 spring is the merged one).
            assert len(ops.getEleTags()) == 6
        finally:
            ops.wipe()

    def test_non_lumped_rebuild_clears_stale_hinge_state(self):
        """A non-lumped rebuild clears the builder-local hinge state."""
        import openseespy.opensees as ops

        try:
            _mesh, b = _build(
                {},
                {"1": Restraint([1, 1, 1, 1, 1, 1]), "3": Restraint([1, 1, 1, 1, 1, 1])},
                config={"hinge_model": "lumped", "hinge_backbone": "bilinear"},
            )
            assert b._hinge_nodes  # the lumped build populated hinge state
            b.build_domain(config_overrides={"hinge_model": "fiber"})
            assert b._hinge_nodes == {}
            assert b._hinge_repoints == {}
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


def _build_release_hinge_cantilever(merged: bool):
    """Hand-built cantilever: axial release + M3 hinge at the fixed end.

    ``merged=True`` → a single ``zeroLength`` carries both the released P and
    the hinge M3 (the new topology).  ``merged=False`` → the old chained
    topology (hinge spring + series release spring).  Returns the tag of the
    coincident spring node whose displacement exposes the spring deformation
    (node 10 merged, node 11 chained).
    """
    import openseespy.opensees as ops

    E = 2.0e11
    G = E / 2.6
    A = 1.0e-2
    I_sec = 1.0e-4
    L = 6.0
    eta = 100.0
    mu = 1.0e-6
    k_axial = E * A / L
    k_shear = G * A / L
    k_torsion = G * I_sec / L
    k_bend = E * I_sec / L
    k_h = 10.0 * 6.0 * k_bend  # hinge elastic M3 (hinge_stiffness_factor × 6EI/L)

    ops.wipe()
    ops.model("basic", "-ndm", 3, "-ndf", 6)
    ops.node(1, 0.0, 0.0, 0.0)
    ops.node(2, L, 0.0, 0.0)
    ops.node(10, 0.0, 0.0, 0.0)  # hinge node (coincident with node 1)
    ops.fix(1, 1, 1, 1, 1, 1, 1)

    # Elastic springs: soft P, rigid P, rigid shear, rigid torsion, rigid
    # bend, hinge M3.
    ops.uniaxialMaterial("Elastic", 101, mu * k_axial)
    ops.uniaxialMaterial("Elastic", 102, eta * k_axial)
    ops.uniaxialMaterial("Elastic", 103, eta * k_shear)
    ops.uniaxialMaterial("Elastic", 104, eta * k_torsion)
    ops.uniaxialMaterial("Elastic", 105, eta * k_bend)
    ops.uniaxialMaterial("Elastic", 106, k_h)

    if merged:
        # Single spring: dir 1 = soft P, dir 6 = hinge M3, rest rigid.
        ops.element(
            "zeroLength",
            20,
            1,
            10,
            "-mat",
            101,
            103,
            103,
            104,
            105,
            106,
            "-dir",
            1,
            2,
            3,
            4,
            5,
            6,
        )
        spring_node = 10
    else:
        # Hinge spring (all rigid except M3) + series release spring.
        ops.node(11, 0.0, 0.0, 0.0)  # release node
        ops.element(
            "zeroLength",
            20,
            1,
            10,
            "-mat",
            102,
            103,
            103,
            104,
            105,
            106,
            "-dir",
            1,
            2,
            3,
            4,
            5,
            6,
        )
        ops.element(
            "zeroLength",
            21,
            10,
            11,
            "-mat",
            101,
            103,
            103,
            104,
            105,
            105,
            "-dir",
            1,
            2,
            3,
            4,
            5,
            6,
        )
        spring_node = 11

    # Elastic member (spring node → tip).  Iz (dir 6) is the M3 axis.
    ops.geomTransf("Linear", 1, 0, 0, 1)
    ops.element("elasticBeamColumn", 1, spring_node, 2, A, E, G, I_sec, I_sec, I_sec, 1)
    return spring_node


def _run_cantilever_static(node_tag, load_vec):
    """Apply a single nodal load and solve one linear static step."""
    import openseespy.opensees as ops

    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    ops.load(node_tag, *load_vec)
    ops.system("BandGen")
    ops.numberer("Plain")
    ops.constraints("Plain")
    ops.integrator("LoadControl", 1.0)
    ops.algorithm("Linear")
    ops.analysis("Static")
    assert ops.analyze(1) == 0


def test_merged_spring_restores_hinge_m3_stiffness():
    """The merged single spring avoids the chained series softening on M3.

    A non-M3 (axial) release retains M3, whose hinge spring is ``10 × 6EI/L``;
    the release's retained M3 is ``η × EI/L = 100 EI/L``.  Chaining them in
    series softens the hinge to ``60·100/(60+100) = 37.5 EI/L`` — 37.5 % softer.
    The merged single spring keeps the hinge's own ``60 EI/L`` on M3, so the
    spring rotation under a tip moment is 1/1.6 of the chained value, while the
    released axial DOF is unchanged (series error μ/η ≈ 1e-8).
    """
    import openseespy.opensees as ops

    Mz = 1.0e5
    Fx = 1.0e4

    try:
        node = _build_release_hinge_cantilever(True)
        _run_cantilever_static(2, (0.0, 0.0, 0.0, 0.0, 0.0, Mz))
        rz_merged = ops.nodeDisp(node)[5]  # rz = M3

        node = _build_release_hinge_cantilever(False)
        _run_cantilever_static(2, (0.0, 0.0, 0.0, 0.0, 0.0, Mz))
        rz_chained = ops.nodeDisp(node)[5]

        # Chained is 37.5 % softer → 1.6× the rotation.
        assert rz_chained / rz_merged == pytest.approx(1.6, rel=1e-6)

        node = _build_release_hinge_cantilever(True)
        _run_cantilever_static(2, (Fx, 0.0, 0.0, 0.0, 0.0, 0.0))
        ux_merged = ops.nodeDisp(node)[0]  # ux = released P

        node = _build_release_hinge_cantilever(False)
        _run_cantilever_static(2, (Fx, 0.0, 0.0, 0.0, 0.0, 0.0))
        ux_chained = ops.nodeDisp(node)[0]

        # Released axial DOF is the same (series error is μ/η ≈ 1e-8).
        assert ux_chained == pytest.approx(ux_merged, rel=1e-6)
    finally:
        ops.wipe()


def _brace_plus_release_model():
    """Three diagonal-pipe braces plus a flexural beam with an axial release.

    The braces become Truss elements under ``brace_truss`` (their per-element
    Hysteretic materials occupy the 100000+ tag band); the beam's I-end axial
    release is merged into its lumped hinge spring.  Building both together
    exercises the merged release-material tag band against the brace-truss band.
    """
    nodes = {
        "d0": Node("d0", 1, 0.0, 0.0, 0.0),
        "d1": Node("d1", 2, 4.0, 0.0, 4.0),
        "d2": Node("d2", 3, 8.0, 0.0, 0.0),
        "d3": Node("d3", 4, 12.0, 0.0, 4.0),
        "d4": Node("d4", 5, 16.0, 0.0, 0.0),
        "d5": Node("d5", 6, 20.0, 0.0, 4.0),
        "b0": Node("b0", 7, 0.0, 0.0, 6.0),
        "b1": Node("b1", 8, 20.0, 0.0, 6.0),
    }
    materials = {
        "STEEL": Material(
            "STEEL", "Steel", E_mod=2e11, G_mod=7.7e10, nu=0.3, unit_weight=77.0, Fy=2.5e8
        ),
    }
    sections = {
        "PIPE": PipeSection(
            name="PIPE",
            shape="Pipe",
            material="STEEL",
            od=0.15,
            t=0.008,
            A=3.57e-3,
            I33=8.6e-6,
            I22=8.6e-6,
            J=1.72e-5,
        ),
        "ISEC": ISection(
            name="ISEC",
            shape="I/Wide Flange",
            material="STEEL",
            depth=0.3,
            bf=0.15,
            tf=0.01,
            tw=0.006,
            A=6e-3,
            I33=8e-5,
            I22=3e-5,
            J=2e-6,
        ),
    }
    frames = {
        "BR1": FrameElement("BR1", 1, "d0", "d1"),
        "BR2": FrameElement("BR2", 2, "d2", "d3"),
        "BR3": FrameElement("BR3", 3, "d4", "d5"),
        "BEAM": FrameElement("BEAM", 4, "b0", "b1"),
    }
    return SAPModelData(
        nodes=nodes,
        restraints={},
        materials=materials,
        sections=sections,
        frame_elements=frames,
        area_elements={},
        frame_assignments={"BR1": "PIPE", "BR2": "PIPE", "BR3": "PIPE", "BEAM": "ISEC"},
        area_assignments={},
        groups={},
        frame_auto_mesh={},
        frame_releases={"BEAM": FrameRelease("BEAM", end_i=[1, 0, 0, 0, 0, 0])},
        units=dict(UNITS),
    )


def test_merged_release_and_brace_truss_tags_disjoint():
    """The merged release springs never collide with the brace-truss band.

    A non-M3 release at a hinged end needs an Elastic release material, and a
    brace truss needs a per-element Hysteretic material (100000+).  Building
    both in one model must succeed — a tag overlap would raise OpenSees'
    "similar tag exists" error during truss material creation.
    """
    import openseespy.opensees as ops

    from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

    try:
        md = _brace_plus_release_model()
        mesh = preprocess_model(
            md, {"split_elements": False, "element_type": "elasticBeamColumn", "verbose": False}
        )
        builder = AnalysisBuilder(
            mesh,
            {
                "brace_truss": True,
                "hinge_model": "lumped",
                "hinge_backbone": "bilinear",
                "verbose": False,
            },
        )
        builder.build_domain()
        # The merge fired (the beam's axial release folded into its hinge).
        assert ("BEAM", "I") in builder._combined_release_ends
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

    def test_offset_remapped_to_leaf(self):
        from fea_toolkit.model.sap_data import FrameEndOffset
        from fea_toolkit.opensees.preprocessor import _remap_frame_end_offsets

        parent = FrameElement("S1", 1, "1", "3", inactive=True, child_ids=["S1-0", "S1-1"])
        leaf_i = FrameElement("S1-0", 2, "1", "2", parent_id="S1")
        leaf_j = FrameElement("S1-1", 3, "2", "3", parent_id="S1")
        elements = {"S1": parent, "S1-0": leaf_i, "S1-1": leaf_j}

        off = FrameEndOffset(end_i=0.3, off_y_i=1.0, end_j=0.4, off_y_j=2.0)
        remapped = _remap_frame_end_offsets({"S1": off}, elements)

        assert set(remapped) == {"S1-0", "S1-1"}
        assert remapped["S1-0"].end_i == 0.3
        assert remapped["S1-0"].off_y_i == 1.0
        assert remapped["S1-0"].end_j == 0.0
        assert remapped["S1-1"].end_j == 0.4
        assert remapped["S1-1"].off_y_j == 2.0
        assert remapped["S1-1"].end_i == 0.0

    def test_unsplit_offset_passes_through(self):
        from fea_toolkit.model.sap_data import FrameEndOffset
        from fea_toolkit.opensees.preprocessor import _remap_frame_end_offsets

        elem = FrameElement("S1", 1, "1", "2")
        off = FrameEndOffset(end_i=0.3, end_j=0.4)
        remapped = _remap_frame_end_offsets({"S1": off}, {"S1": elem})
        assert remapped["S1"] is off

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
            b = AnalysisBuilder(mesh, {"verbose": False})
            b.build_domain()
            assert set(b._release_nodes) == {rn["node_id"] for rn in plan["release_nodes"]}
            # 2 frame elements + 1 zeroLength per released end.
            assert len(ops.getEleTags()) == 2 + len(plan["ends"])
        finally:
            ops.wipe()
