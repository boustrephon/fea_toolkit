"""Generic-frame pushover validation — the strength coefficient ``γ = Vy/W``.

The IK-2005 generic frames are designed (Appendix B) for a base-shear
coefficient ``γ = Vy/W = 1.0`` with a parabolic design pattern.  A pushover
(triangular lateral pattern, no P-Δ) must therefore show a yield base shear
near the seismic weight and a peak base shear only mildly higher (the
``αs = 0.03`` hardening).  This is the record-free strength check that sits
between the modal T1 check and the record-dependent IDA collapse check.
"""

import numpy as np
import pytest

from fea_toolkit.benchmarks import generic_frame as gf
from fea_toolkit.benchmarks.generic_frame_data import FRAME_DATA
from fea_toolkit.utils import g_from_units

#: Peak base-shear coefficient band — yield ≈ 1.0 plus the αs=0.03 hardening.
_GAMMA_PEAK = (0.9, 1.4)


def _pushover_peak_gamma(code: str) -> tuple[float, float]:
    import openseespy.opensees as ops

    roof_h = FRAME_DATA[code]["N"] * gf.storey_height_m()
    ops.wipe()
    try:
        builder = gf.build_builder(code)
        masses = builder.compute_seismic_masses()
        w = float(sum(masses.values())) * g_from_units(builder.units)
        res = builder.run_pushover_analysis(
            gravity_patterns={},
            lateral_load_type="triangular",
            lateral_direction="X",
            max_disp=0.03 * roof_h,  # push to ~3 % roof drift (past the peak)
            num_steps=200,
            print_progress=False,
        )
    finally:
        ops.wipe()

    v = np.asarray(res["base_shear"], dtype=float)
    status = np.asarray(res["status"], dtype=int)
    ok = status == 0
    ok[0] = False
    vmax = float(v[ok].max())
    return vmax / w, w


@pytest.mark.parametrize("code", ["0303", "0909", "1818"])
def test_pushover_strength_coefficient(code):
    gamma_peak, _ = _pushover_peak_gamma(code)
    assert _GAMMA_PEAK[0] <= gamma_peak <= _GAMMA_PEAK[1], (
        f"{code}: peak base-shear coefficient γ = {gamma_peak:.3f} "
        f"(expected ~1.0 + hardening, within {_GAMMA_PEAK})"
    )
