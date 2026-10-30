"""Generic-frame generator — modal T1 validation against the IK-2005 target.

Each of the 12 frames must reproduce its published fundamental period
``T1 = 0.1·N`` (stiff) or ``0.2·N`` (flexible).  This builds the OpenSees
domain directly from :func:`~fea_toolkit.benchmarks.generic_frame.build_model`
— elastic members + lumped masses plus the column-base rotational spring ``Kc``
(a zeroLength to a fixed ground node) — and runs an eigen-analysis.  The direct
domain build (rather than the ``preprocess_model → AnalysisBuilder`` path) is
deliberate: the base spring is a total M-θ spring that the lumped-hinge path
does not yet place, so this test pins the generator's *numbers* (E, I, Kc,
masses) independently of the hinge machinery.
"""

import math

import pytest

from fea_toolkit.benchmarks.generic_frame import (
    base_spring_props,
    build_model,
    steel_e_pa,
)
from fea_toolkit.benchmarks.generic_frame_data import FRAME_CODES, FRAME_DATA

#: Acceptance band — the table values are rounded to three significant figures,
#: so the exact period is not recoverable; a ±1 % band is the floor.
_T1_TOL = 0.01


def _build_and_period(code: str) -> float:
    """Build the OpenSees domain for *code* and return T1 (s)."""
    import openseespy.opensees as ops

    md = build_model(code)
    e_mod = steel_e_pa()
    g_shear = e_mod / 2.6

    ops.wipe()
    ops.model("basic", "-ndm", 3, "-ndf", 6)

    tags: dict[str, int] = {}
    for nid, node in md.nodes.items():
        ops.node(node.node_tag, node.x, node.y, node.z)
        tags[nid] = node.node_tag
    for nid, r in md.restraints.items():
        ops.fix(tags[nid], *r.dofs)

    sec_map: dict[str, int] = {}
    sec_tag = 1
    for sname, sec in md.sections.items():
        ops.section("Elastic", sec_tag, e_mod, sec.A, sec.I33, sec.I22, g_shear, sec.J)
        sec_map[sname] = sec_tag
        sec_tag += 1

    # Column-base rotational spring: fixed ground node + zeroLength (dir 5 = ry).
    base = base_spring_props(code)
    gtag = max(tags.values()) + 1
    for line in (0, 1):
        node = md.nodes[f"0-{line}"]
        ops.node(gtag + line, node.x, node.y, node.z)
        ops.fix(gtag + line, 1, 1, 1, 1, 1, 1)
    spring_mat = sec_tag
    ops.uniaxialMaterial("Elastic", spring_mat, base["Kc"])
    for line in (0, 1):
        ops.element(
            "zeroLength", line + 1, gtag + line, tags[f"0-{line}"], "-mat", spring_mat, "-dir", 5
        )

    ops.geomTransf("Linear", 1, 0.0, 1.0, 0.0)  # local z = global Y (in-plane bending)
    etag = 3
    for eid, elem in md.frame_elements.items():
        ops.element(
            "elasticBeamColumn",
            etag,
            tags[elem.node_i],
            tags[elem.node_j],
            sec_map[md.frame_assignments[eid]],
            1,
        )
        etag += 1

    for nid, m in md.added_masses.items():
        ops.mass(tags[nid], m, m, m, 0.0, 0.0, 0.0)

    omega1 = ops.eigen(3)[0]
    return 2.0 * math.pi / math.sqrt(omega1)


@pytest.mark.parametrize("code", list(FRAME_CODES))
def test_generic_frame_fundamental_period(code):
    t1 = _build_and_period(code)
    target = FRAME_DATA[code]["T1"]
    assert abs(t1 - target) <= _T1_TOL * target, (
        f"{code}: T1 = {t1:.4f} s vs target {target:.2f} s ({100 * (t1 - target) / target:+.2f} %)"
    )
