"""Tests for :func:`fea_toolkit.model.units.convert_mesh_units`.

Verifies the N-m -> kip-in -> N-m round trip reproduces the original model
(the guarantee the Phase-3 limit-state workflow relies on), spot-checks the
per-quantity conversion factors, and confirms the input model is never
mutated.
"""

from copy import deepcopy
from dataclasses import fields as dc_fields

import pytest

from fea_toolkit.model.mesh_model import MeshModel, WallElement
from fea_toolkit.model.sap_data import (
    AreaElement,
    AreaGravityLoad,
    AreaUniformLoad,
    ConcreteRectangularSection,
    FrameDistributedLoad,
    FrameElement,
    FrameElementProperties,
    GravityLoad,
    JointLoad,
    Material,
    Node,
)
from fea_toolkit.model.units import (
    KIP_IN_UNITS,
    convert_mesh_units,
    unit_multipliers,
)

_N_M = {"L": "m", "F": "N", "T": "C"}
_KIP_IN = dict(KIP_IN_UNITS)


# ═════════════════════════════════════════════════════════════════════
# Fixture -- a small frame mesh in N-m (SI)
# ═════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def si_mesh():
    """Minimal 1-column frame mesh in N-m with one of every load type.

    Also carries one wall macro-element and one set of resolved frame
    element properties (with ``hinge_params``) so :func:`convert_mesh_units`
    exercises its wall-fibre and hinge-parameter scaling paths.
    """
    n1 = Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0)
    n2 = Node(node_id="2", node_tag=2, x=0.0, y=0.0, z=4.0)
    sec = ConcreteRectangularSection(
        name="C500",
        shape="Concrete Rectangular",
        material="C40",
        rebar_material="REBAR-L",
        A=0.25,
        I33=5.21e-3,
        I22=5.21e-3,
        J=8.6e-3,
        Z33=0.0208,
        Z22=0.0208,
        depth=0.5,
        bf=0.5,
        cover=0.04,
        top_bars=4,
        bot_bars=4,
        top_bar_dia=0.025,
        bot_bar_dia=0.025,
        tie_diameter=0.01,
        tie_spacing=0.15,
        tie_fy=420.0e6,
        tie_rebar_mat="REBAR-T",
    )
    conc = Material(
        name="C40",
        type="Concrete",
        E_mod=32.0e9,
        G_mod=13.3e9,
        nu=0.2,
        unit_weight=24.0e3,
        unit_mass=2450.0,
        Fc=40.0e6,
    )
    tie = Material(name="REBAR-T", type="Rebar", Fy=420.0e6, E_mod=200.0e9)
    frame = FrameElement(elem_id="COL1", elem_tag=10, node_i="1", node_j="2")
    area = AreaElement(area_id="S1", area_tag=20, node_ids=["1", "2"], thickness=0.2)
    # 3-fiber wall macro-element — fibre ``thick``/``width`` are lengths,
    # ``rho``/``Density`` are mass densities (both wall scaling branches).
    # node_ids are illustrative: convert_mesh_units never resolves them.
    wall = WallElement(
        elem_id="W1",
        elem_tag=30,
        node_ids=["1", "2", "4", "3"],  # [i, j, k, l]
        m=3,
        thick=[0.2, 0.2, 0.2],
        width=[1.0, 1.0, 1.0],
        fsam_material_names=["FSAM_bdry", "FSAM_core", "FSAM_bdry"],
        rho=[2450.0, 2450.0, 2450.0],
        Density=2400.0,
        CoR=0.4,
    )
    # Resolved frame properties — hinge_params cover all three key kinds:
    # lpI/lpJ (length, ×L), My/Mc_neg (moment, ×F·L), theta_p (dimensionless).
    col_props = FrameElementProperties(
        element_type="nonlinearBeamColumn",
        material_strategy="fiber_rc",
        integration_type="HingeRadau",
        num_integration_points=4,
        hinge_params={"lpI": 0.15, "lpJ": 0.15, "My": 180.0e3, "Mc_neg": 150.0e3, "theta_p": 0.03},
    )
    return MeshModel(
        nodes={"1": n1, "2": n2},
        frame_elements={"COL1": frame},
        frame_assignments={"COL1": "C500"},
        area_elements={"S1": area},
        area_assignments={"S1": "C500"},
        sections={"C500": sec},
        materials={"C40": conc, "REBAR-T": tie},
        frame_dist_loads=[
            FrameDistributedLoad(
                pattern="DEAD",
                frame_id="COL1",
                direction="Gravity",
                load_type="Force",
                shape="Uniform",
                val_a=25.0e3,
                val_b=25.0e3,
                rdist_a=0.0,
                rdist_b=1.0,
                dist_a=0.0,
                dist_b=4.0,
            )
        ],
        joint_loads=[
            JointLoad(
                pattern="WIND", node_id="2", fx=50.0e3, fy=0.0, fz=0.0, mx=0.0, my=0.0, mz=10.0e3
            )
        ],
        area_uniform_loads=[AreaUniformLoad(pattern="DEAD", area_id="S1", value=2.0e3)],
        frame_gravity_loads=[
            GravityLoad(
                pattern="DEAD",
                frame_id="COL1",
                multiplier_x=0.0,
                multiplier_y=0.0,
                multiplier_z=-1.0,
            )
        ],
        area_gravity_loads=[
            AreaGravityLoad(
                pattern="DEAD", area_id="S1", multiplier_x=0.0, multiplier_y=0.0, multiplier_z=-1.0
            )
        ],
        base_z=0.0,
        diaphragm_levels=[4.0],
        wall_elements={"W1": wall},
        frame_element_properties={"COL1": col_props},
        units=dict(_N_M),
    )


def _assert_mesh_close(a: MeshModel, b: MeshModel, rel: float = 1e-9) -> None:
    """Assert two MeshModels are numerically identical field by field."""
    assert a.units == b.units
    assert a.base_z == pytest.approx(b.base_z, rel=rel)
    assert a.diaphragm_levels == pytest.approx(b.diaphragm_levels, rel=rel)
    assert a.diaphragm_z_tolerance == pytest.approx(b.diaphragm_z_tolerance, rel=rel)

    for nid in a.nodes:
        na, nb = a.nodes[nid], b.nodes[nid]
        assert (na.x, na.y, na.z) == pytest.approx((nb.x, nb.y, nb.z), rel=rel)

    for name in a.sections:
        sa, sb = a.sections[name], b.sections[name]
        for f in dc_fields(sa):
            va, vb = getattr(sa, f.name), getattr(sb, f.name)
            if isinstance(va, float):
                assert va == pytest.approx(vb, rel=rel), f"{name}.{f.name}"
            elif isinstance(va, int):
                assert va == vb, f"{name}.{f.name}"

    for mname in a.materials:
        ma, mb = a.materials[mname], b.materials[mname]
        for f in dc_fields(ma):
            va, vb = getattr(ma, f.name), getattr(mb, f.name)
            if isinstance(va, float):
                assert va == pytest.approx(vb, rel=rel), f"{mname}.{f.name}"

    assert len(a.frame_dist_loads) == len(b.frame_dist_loads)
    for la, lb in zip(a.frame_dist_loads, b.frame_dist_loads):
        for f in dc_fields(la):
            va, vb = getattr(la, f.name), getattr(lb, f.name)
            if isinstance(va, float):
                assert va == pytest.approx(vb, rel=rel), f"dl.{f.name}"

    assert len(a.joint_loads) == len(b.joint_loads)
    for la, lb in zip(a.joint_loads, b.joint_loads):
        for f in dc_fields(la):
            va, vb = getattr(la, f.name), getattr(lb, f.name)
            if isinstance(va, float):
                assert va == pytest.approx(vb, rel=rel), f"jl.{f.name}"

    assert len(a.area_uniform_loads) == len(b.area_uniform_loads)
    for la, lb in zip(a.area_uniform_loads, b.area_uniform_loads):
        for f in dc_fields(la):
            va, vb = getattr(la, f.name), getattr(lb, f.name)
            if isinstance(va, float):
                assert va == pytest.approx(vb, rel=rel), f"aul.{f.name}"

    assert len(a.area_elements) == len(b.area_elements)
    for la, lb in zip(a.area_elements.values(), b.area_elements.values()):
        assert la.thickness == pytest.approx(lb.thickness, rel=rel)

    # ── Wall macro-elements (thick/width lengths, rho/Density scaled) ──
    assert len(a.wall_elements) == len(b.wall_elements)
    for wid in a.wall_elements:
        wa, wb = a.wall_elements[wid], b.wall_elements[wid]
        for f in dc_fields(wa):
            va, vb = getattr(wa, f.name), getattr(wb, f.name)
            if isinstance(va, float):
                assert va == pytest.approx(vb, rel=rel), f"wall {wid}.{f.name}"
            elif isinstance(va, int):
                assert va == vb, f"wall {wid}.{f.name}"
            elif isinstance(va, list):
                if va and isinstance(va[0], float):
                    assert va == pytest.approx(vb, rel=rel), f"wall {wid}.{f.name}"
                else:
                    assert va == vb, f"wall {wid}.{f.name}"
            else:
                assert va == vb, f"wall {wid}.{f.name}"

    # ── Frame element properties (incl. hinge_params) ──────────────
    assert a.frame_element_properties.keys() == b.frame_element_properties.keys()
    for eid in a.frame_element_properties:
        pa, pb = a.frame_element_properties[eid], b.frame_element_properties[eid]
        for f in dc_fields(pa):
            va, vb = getattr(pa, f.name), getattr(pb, f.name)
            if f.name == "hinge_params":
                assert (va or {}).keys() == (vb or {}).keys(), f"fep.{eid}.hinge_params"
                for k in va or {}:
                    assert va[k] == pytest.approx(vb[k], rel=rel), f"fep.{eid}.hinge_params.{k}"
            elif isinstance(va, float):
                assert va == pytest.approx(vb, rel=rel), f"fep.{eid}.{f.name}"
            else:
                assert va == vb, f"fep.{eid}.{f.name}"


# ═════════════════════════════════════════════════════════════════════
# Multiplier checks (N-m -> kip-in)
# ═════════════════════════════════════════════════════════════════════


class TestUnitMultipliers:
    def test_known_factors(self):
        m = unit_multipliers(_N_M, _KIP_IN)
        assert m["length"] == pytest.approx(39.37007874)
        assert m["force"] == pytest.approx(1.0 / 4448.0)
        # Pa -> model-stress (ksi for kip-in), via the framework's constants.
        assert m["stress"] == pytest.approx((1.0 / 4448.0) / 39.37007874**2)
        assert m["force_times_length"] == pytest.approx((1.0 / 4448.0) * 39.37007874)
        assert m["force_per_length"] == pytest.approx((1.0 / 4448.0) / 39.37007874)

    def test_reverse_is_reciprocal(self):
        fwd = unit_multipliers(_N_M, _KIP_IN)
        rev = unit_multipliers(_KIP_IN, _N_M)
        assert rev["length"] == pytest.approx(1.0 / fwd["length"], rel=1e-12)
        assert rev["force"] == pytest.approx(1.0 / fwd["force"], rel=1e-12)
        assert rev["stress"] == pytest.approx(1.0 / fwd["stress"], rel=1e-12)


# ═════════════════════════════════════════════════════════════════════
# One-way conversion spot checks
# ═════════════════════════════════════════════════════════════════════


class TestConvertMeshUnits:
    def test_nodes_and_metadata(self, si_mesh):
        out = convert_mesh_units(si_mesh, _KIP_IN)
        # target units merged over the source (source-only keys survive)
        assert out.units == {"L": "in", "F": "kip", "T": "C"}
        assert out.nodes["2"].z == pytest.approx(4.0 * 39.37007874)
        assert out.diaphragm_levels == pytest.approx([4.0 * 39.37007874])

    def test_section_and_material(self, si_mesh):
        out = convert_mesh_units(si_mesh, _KIP_IN)
        sec = out.sections["C500"]
        assert sec.depth == pytest.approx(0.5 * 39.37007874)
        assert sec.tie_spacing == pytest.approx(0.15 * 39.37007874)
        sstress = (1.0 / 4448.0) / 39.37007874**2  # Pa -> ksi
        assert sec.tie_fy == pytest.approx(420.0e6 * sstress)
        assert pytest.approx(0.25 * 39.37007874**2) == sec.A
        assert pytest.approx(5.21e-3 * 39.37007874**4) == sec.I33
        conc = out.materials["C40"]
        assert conc.Fc == pytest.approx(40.0e6 * sstress)
        assert conc.E_mod == pytest.approx(32.0e9 * sstress)
        assert conc.unit_weight == pytest.approx(24.0e3 / 39.37007874**3 / 4448.0)
        assert conc.unit_mass == pytest.approx(2450.0 * (1.0 / 4448.0) / 39.37007874**4)

    def test_loads(self, si_mesh):
        out = convert_mesh_units(si_mesh, _KIP_IN)
        dl = out.frame_dist_loads[0]
        assert dl.val_a == pytest.approx(25.0e3 / 4448.0 / 39.37007874)
        assert dl.dist_b == pytest.approx(4.0 * 39.37007874)
        jl = out.joint_loads[0]
        assert jl.fx == pytest.approx(50.0e3 / 4448.0)
        assert jl.mz == pytest.approx(10.0e3 / 4448.0 * 39.37007874)
        aul = out.area_uniform_loads[0]
        assert aul.value == pytest.approx(2.0e3 / 4448.0 / 39.37007874**2)
        # Gravity multipliers are dimensionless — unchanged.
        assert out.frame_gravity_loads[0].multiplier_z == -1.0

    def test_input_not_mutated(self, si_mesh):
        before = si_mesh.nodes["2"].z
        convert_mesh_units(si_mesh, _KIP_IN)
        assert si_mesh.nodes["2"].z == before
        assert si_mesh.units == _N_M


# ═════════════════════════════════════════════════════════════════════
# Round trip
# ═════════════════════════════════════════════════════════════════════


class TestRoundTrip:
    def test_si_kipin_si(self, si_mesh):
        there = convert_mesh_units(si_mesh, _KIP_IN)
        back = convert_mesh_units(there, _N_M)
        assert back.units == _N_M
        _assert_mesh_close(si_mesh, back, rel=1e-9)


# ═════════════════════════════════════════════════════════════════════
# Validation
# ═════════════════════════════════════════════════════════════════════


class TestValidation:
    def test_missing_force_unit_raises(self, si_mesh):
        with pytest.raises(ValueError, match="'L' and 'F'"):
            convert_mesh_units(si_mesh, {"L": "m"})

    def test_unsupported_surfaces_warn(self, si_mesh):
        mesh = deepcopy(si_mesh)
        mesh.nd_materials["ND1"] = None  # placeholder triggers the warning
        with pytest.warns(UserWarning, match="nd_materials"):
            convert_mesh_units(mesh, _KIP_IN)
