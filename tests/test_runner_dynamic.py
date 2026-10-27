"""Tests for the native Newmark transient runner (``run_transient_analysis``)."""

import math

import numpy as np
import pytest

from fea_toolkit.model.sap_data import (
    FrameElement,
    MassSource,
    Material,
    Node,
    Restraint,
    SAPModelData,
    Section,
)
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
from fea_toolkit.opensees.preprocessor import preprocess_model

E = 2.0e11
INERTIA = 1.0e-4
L = 3.0
M = 1.0e3
A = 0.01

# Fixed-fixed sway column (tip translation free, rotation fixed) → k = 12EI/L³.
K = 12.0 * E * INERTIA / L**3
WN = math.sqrt(K / M)  # natural frequency (rad/s)


def _make_sdof() -> SAPModelData:
    nodes = {
        "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
        "2": Node(node_id="2", node_tag=2, x=0.0, y=0.0, z=L),
    }
    restraints = {
        "1": Restraint([1, 1, 1, 1, 1, 1]),
        "2": Restraint([0, 1, 1, 1, 1, 1]),  # UX free only → single DOF
    }
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
            I33=INERTIA,
            I22=INERTIA,
            J=1e-6,
        ),
    }
    frame_elements = {"1": FrameElement(elem_id="1", elem_tag=1, node_i="1", node_j="2")}
    frame_assignments = {"1": "COL"}
    mass_sources = {"MSSSRC1": MassSource(name="MSSSRC1", masses=True)}
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
        load_patterns={},
        frame_dist_loads=[],
        joint_loads=[],
        added_masses={"2": M},
        mass_sources=mass_sources,
        units={"F": "N", "L": "m", "T": "C"},
    )


@pytest.fixture
def builder():
    from openseespy.opensees import wipe

    wipe()
    mesh = preprocess_model(_make_sdof(), {"element_type": "elasticBeamColumn"})
    b = AnalysisBuilder(mesh, {})
    b.build_domain()
    yield b
    wipe()


def test_resonance_amplitude_matches_closed_form(builder):
    """A resonant SDOF reaches the closed-form steady-state amplitude.

    For base excitation ``ug = a sin(wt)`` with damping ratio ``zeta`` at
    resonance, the relative displacement amplitude is ``a / (wn² * 2 * zeta)``.
    """
    zeta = 0.10
    steps_per_cycle = 40
    n_cycles = 30
    num_steps = n_cycles * steps_per_cycle
    dt = (2.0 * math.pi / WN) / steps_per_cycle
    t = np.arange(num_steps) * dt
    accel = np.sin(WN * t)

    res = builder.run_transient_analysis(accel, dt, direction="X", damping_ratio=zeta)

    assert res["converged_steps"] == num_steps
    disp = res["displacements"][2][:, 0]
    tail = disp[-10 * steps_per_cycle :]  # last 10 cycles ≈ steady state
    amp = float(np.max(np.abs(tail)))
    expected = 1.0 / (WN**2 * 2.0 * zeta)
    assert amp == pytest.approx(expected, rel=0.15)


def test_smoke_runs_and_returns_finite(builder):
    accel = np.zeros(100)
    accel[:5] = 1.0
    res = builder.run_transient_analysis(accel, 0.001, direction="X", damping_ratio=0.02)
    assert res["converged_steps"] > 0
    assert np.isfinite(res["peak_displacement"])
    assert res["times"].shape == (res["converged_steps"],)
    # Velocity/acceleration recording is opt-in (default off) — only the
    # displacement histories are populated.
    assert res["velocities"] == {}
    assert res["accelerations"] == {}


class _NodeStub:
    def __init__(self, node_id: str, node_tag: int, z: float = 0.0):
        self.node_id = node_id
        self.node_tag = node_tag
        self.z = z


class _MeshStub:
    def __init__(self):
        self.nodes = {
            "base": _NodeStub("base", 1, 0.0),
            "b1": _NodeStub("b1", 2, 3.0),
            "ground": _NodeStub("ground", 3, 6.0),
            "floor": _NodeStub("floor", 4, 10.0),
        }
        self.restraints = {
            "base": Restraint([1, 1, 1, 1, 1, 1]),  # pinned base — all DOFs fixed
            "b1": Restraint([0, 1, 0, 1, 0, 1]),  # below-ground basement — X free
            "ground": Restraint([1, 1, 0, 1, 0, 1]),  # ground tie — X fixed
            # "floor" has no restraint entry → treated as free
        }


def test_free_dof_node_tags_default_excludes_only_fixed_dof():
    """By default, only nodes fixed in the excitation DOF are excluded.

    The below-ground exclusion is opt-in (the SAC benchmark enables it), so a
    general runner leaves the free B-1 basement in the base-shear node set.
    """
    from fea_toolkit.opensees._runner_dynamic import _free_dof_node_tags

    mesh = _MeshStub()
    # X: base + ground tie fixed; the free B-1 basement and floor both survive.
    assert _free_dof_node_tags(mesh, 1) == {2, 4}
    # Y: base, B-1 and ground all fixed in Y → only the floor survives.
    assert _free_dof_node_tags(mesh, 2) == {4}
    # Z: only the base is fixed in Z → B-1, ground and floor are all free.
    assert _free_dof_node_tags(mesh, 3) == {2, 3, 4}


def test_free_dof_node_tags_ground_exclusion_drops_below_ground():
    """The opt-in ground exclusion drops free nodes below the ground tie.

    Matches the benchmark's ``F_max = Σ mi·xdd``, which sums only the
    above-ground storeys: the pinned base (fixed), the ground tie (fixed), and
    the free B-1 basement below the ground tie all fall out, leaving only the
    above-ground floor.
    """
    from fea_toolkit.opensees._runner_dynamic import _free_dof_node_tags

    mesh = _MeshStub()
    # X (lateral): base + ground tie fixed; B-1 free but below ground → excluded.
    assert _free_dof_node_tags(mesh, 1, ground_exclusion=True) == {4}
    # Y: base, B-1 and ground all fixed in Y → only the floor survives.
    assert _free_dof_node_tags(mesh, 2, ground_exclusion=True) == {4}
    # Z: only the base is fixed in Z → B-1, ground and floor are all free
    # and above the base (z=0).
    assert _free_dof_node_tags(mesh, 3, ground_exclusion=True) == {2, 3, 4}
