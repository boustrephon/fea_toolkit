"""Generic-frame generator — modal T1 validation through the full pipeline.

Each of the 12 frames must reproduce its published fundamental period
``T1 = 0.1·N`` (stiff) or ``0.2·N`` (flexible).  This goes through
:func:`~fea_toolkit.benchmarks.generic_frame.build_builder` (the
``preprocess_model → AnalysisBuilder`` path with the lumped ``imk_peak``
beam-end springs and the total-M-θ column-base spring ``k0 = Kc``), so it
validates the spring placement and the masses together with the geometry.
"""

import math

import pytest

from fea_toolkit.benchmarks import generic_frame as gf
from fea_toolkit.benchmarks.generic_frame_data import FRAME_CODES, FRAME_DATA

#: Acceptance band — the table values are rounded to three significant figures
#: (and the spring/beam-column split is internal, IK-2005 Appendix B), so a
#: ±1 % band is the floor.
_T1_TOL = 0.01


def _period(code: str) -> float:
    """Build *code* through the pipeline and return its fundamental period (s)."""
    import openseespy.opensees as ops

    ops.wipe()
    try:
        builder = gf.build_builder(code)
        builder.compute_seismic_masses()
        omega1 = ops.eigen(3)[0]
    finally:
        ops.wipe()
    return 2.0 * math.pi / math.sqrt(omega1)


@pytest.mark.parametrize("code", list(FRAME_CODES))
def test_generic_frame_fundamental_period(code):
    t1 = _period(code)
    target = FRAME_DATA[code]["T1"]
    assert abs(t1 - target) <= _T1_TOL * target, (
        f"{code}: T1 = {t1:.4f} s vs target {target:.2f} s ({100 * (t1 - target) / target:+.2f} %)"
    )
