"""T1 cantilever benchmark: static tip deflection ``PL³/(3EI)``.

Closed-form reference for a fixed-free vertical cantilever under a lateral tip
point load.  The section is isotropic in bending (``I33 == I22``), so the
lateral stiffness is independent of the element's local-axis orientation, and
the tip deflection is exact for the Euler-Bernoulli column:

    delta = P L³ / (3 E I)

Acceptance band: ±0.01 % (T1 closed form).
"""

import pytest

from fea_toolkit.model.sap_data import (
    FrameElement,
    JointLoad,
    LoadPattern,
    Material,
    Node,
    Restraint,
    SAPModelData,
    Section,
)
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
from fea_toolkit.opensees.preprocessor import preprocess_model

E = 2.0e11
I_SEC = 1.0e-3
A = 0.1
L = 10.0
P = 1.0e4


def _make_cantilever() -> SAPModelData:
    nodes = {
        "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
        "2": Node(node_id="2", node_tag=2, x=0.0, y=0.0, z=L),
    }
    restraints = {"1": Restraint([1, 1, 1, 1, 1, 1])}
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
            name="COL", shape="I/Wide Flange", material="Steel", A=A, I33=I_SEC, I22=I_SEC, J=1.0e-5
        ),
    }
    frame_elements = {"1": FrameElement(elem_id="1", elem_tag=1, node_i="1", node_j="2")}
    load_patterns = {"LAT": LoadPattern(name="LAT", pattern_type="Other", self_weight_factor=0)}
    joint_loads = [JointLoad(pattern="LAT", node_id="2", fx=P)]
    return SAPModelData(
        nodes=nodes,
        restraints=restraints,
        materials=materials,
        sections=sections,
        frame_elements=frame_elements,
        area_elements={},
        frame_assignments={"1": "COL"},
        area_assignments={},
        groups={},
        frame_auto_mesh={},
        load_patterns=load_patterns,
        frame_dist_loads=[],
        joint_loads=joint_loads,
        mass_sources={},
        units={"F": "N", "L": "m", "T": "C"},
    )


@pytest.fixture
def builder():
    from openseespy.opensees import wipe

    wipe()
    mesh = preprocess_model(_make_cantilever(), {"element_type": "elasticBeamColumn"})
    b = AnalysisBuilder(mesh, {})
    yield b
    wipe()


def test_tip_deflection_matches_closed_form(builder):
    expected = P * L**3 / (3.0 * E * I_SEC)
    builder.build_domain()
    result = builder.run_static_analysis(pattern_scales={"LAT": 1.0})
    disp = result["nodal_displacements"]["2"]
    assert disp[0] == pytest.approx(expected, rel=1e-4)
