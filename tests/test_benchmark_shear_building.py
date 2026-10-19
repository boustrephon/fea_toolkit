"""T2 shear-building benchmark: modal periods + CQC response-spectrum base shear.

A three-storey shear building (equal story stiffness ``k``, equal floor mass
``m``) is the textbook three-DOF system solved by Przemieniecki (1968) / Paz
(1985).  Each floor is a single lateral DOF (the frame is planar, out-of-plane
and rotational DOFs are restrained), so the model has exactly three modes and
the story stiffness matrix is the classic tridiagonal form:

    K = k · [[ 2, -1,  0],      M = m · I
             [-1,  2, -1],
             [ 0, -1,  1]]

Reference periods are the eigenvalues of this generalised eigenproblem; the
CQC base shear under a flat spectrum ``Sa = 1 m/s²`` is combined by hand with
Der Kiureghian's correlation coefficients.
"""

import math

import numpy as np
import pytest

from fea_toolkit.model.sap_data import (
    FrameElement,
    JointLoad,
    LoadPattern,
    MassSource,
    Material,
    Node,
    Restraint,
    SAPModelData,
    Section,
)
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
from fea_toolkit.opensees.preprocessor import preprocess_model
from fea_toolkit.utils import g_from_units

H = 3.0
K_STORY = 2.0e7
M_FLOOR = 2.0e4
E = 2.0e11
A = 0.1
J = 1.0e-5
I_COL = K_STORY * H**3 / 12.0 / E
GRAVITY = g_from_units({"F": "N", "L": "m", "T": "C"})


def _make_shear_building() -> SAPModelData:
    nodes = {
        str(i + 1): Node(node_id=str(i + 1), node_tag=i + 1, x=0.0, y=0.0, z=i * H)
        for i in range(4)
    }
    restraints = {"1": Restraint([1, 1, 1, 1, 1, 1])}
    for i in (2, 3, 4):
        restraints[str(i)] = Restraint([0, 1, 1, 1, 1, 1])  # UX free only
    materials = {
        "Steel": Material(
            name="Steel",
            type="Steel",
            E_mod=E,
            G_mod=E / 2.6,
            nu=0.3,
            unit_weight=0.0,
            Fy=2.5e8,
        ),
    }
    sections = {
        "COL": Section(
            name="COL",
            shape="I/Wide Flange",
            material="Steel",
            A=A,
            I33=I_COL,
            I22=I_COL,
            J=J,
        ),
    }
    frame_elements = {}
    frame_assignments = {}
    for i in (1, 2, 3):
        eid = str(i)
        frame_elements[eid] = FrameElement(
            elem_id=eid, elem_tag=i, node_i=str(i), node_j=str(i + 1)
        )
        frame_assignments[eid] = "COL"
    load_patterns = {"DEAD": LoadPattern(name="DEAD", pattern_type="Dead", self_weight_factor=0)}
    joint_loads = [
        JointLoad(pattern="DEAD", node_id=str(i), fz=-M_FLOOR * GRAVITY) for i in (2, 3, 4)
    ]
    mass_sources = {"MSSSRC1": MassSource(name="MSSSRC1", loads=True, load_pattern={"DEAD": 1.0})}
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
        frame_dist_loads=[],
        joint_loads=joint_loads,
        mass_sources=mass_sources,
        units={"F": "N", "L": "m", "T": "C"},
    )


def _reference_eigensolution():
    K = K_STORY * np.array([[2, -1, 0], [-1, 2, -1], [0, -1, 1]], dtype=float)
    # M = M_FLOOR · I is a scalar multiple of the identity, so the generalised
    # eigenproblem K·φ = ω²·M·φ reduces to the standard problem on K / M_FLOOR.
    w2, phi = np.linalg.eigh(K / M_FLOOR)  # phi columns are L2-orthonormal
    return 2.0 * np.pi / np.sqrt(w2), phi


def _rho(ti, tj, zeta):
    r = ti / tj
    num = 8.0 * zeta**2 * (1.0 + r) * r**1.5
    den = (1.0 - r**2) ** 2 + 4.0 * zeta**2 * r * (1.0 + r) ** 2
    return num / den


def _reference_cqc_base_shear(zeta=0.05, sa=1.0):
    periods, phi = _reference_eigensolution()
    r = np.ones(3)
    # Participation factors with M = m·I and L2-normalised modes: Γ = φᵀr, and
    # the effective modal mass is Γ²·m, so the modal base shear is Sa·Γ²·m.
    gamma = phi.T @ r
    v = sa * M_FLOOR * gamma**2
    rho = np.array([[_rho(ti, tj, zeta) for tj in periods] for ti in periods])
    return math.sqrt(float(v @ rho @ v))


@pytest.fixture
def builder():
    from openseespy.opensees import wipe

    wipe()
    mesh = preprocess_model(_make_shear_building(), {"element_type": "elasticBeamColumn"})
    b = AnalysisBuilder(mesh, {})
    yield b
    wipe()


def test_modal_periods_match_shear_building(builder):
    periods_ref, _ = _reference_eigensolution()
    builder.build_domain()
    result = builder.run_modal_analysis(num_modes=3, print_results=False)
    got = result["periods"][:3]
    for g, r in zip(got, periods_ref):
        assert g == pytest.approx(r, rel=1e-3)


def test_cqc_base_shear_matches_reference(builder):
    ref = _reference_cqc_base_shear()
    builder.build_domain()
    modal = builder.run_modal_analysis(num_modes=3, print_results=False)
    rs = builder.run_response_spectrum_analysis(
        num_modes=3,
        modal_periods=modal["periods"][:3],
        spectrum_periods=[0.0, 10.0],
        spectrum_accels=[1.0, 1.0],
        direction="X",
        damping_ratio=0.05,
        print_results=False,
    )
    assert rs["base_shear_cqc"] == pytest.approx(ref, rel=2e-2)
