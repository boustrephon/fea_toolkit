"""SAC 3-story benchmark (T2): modal periods vs Ohtori et al. (2004).

The three-story SAC steel moment frame is the canonical published-code
verification anchor for the elastic modal path.  The N-S perimeter frame is
transcribed into Tcl (``fea_toolkit/benchmarks/sac_3story.tcl``) and imported through
:func:`fea_toolkit.io.tcl_importer.parse_tcl_model`, exercising the Tcl importer
end-to-end, then solved with the native modal runner.

Reference — Ohtori, Christenson, Spencer & Dyke (2004), "Benchmark Control
Problems for Seismically Excited Nonlinear Buildings", *ASCE J. Eng. Mech.*
130(4):366, DOI 10.1061/(ASCE)0733-9399(2004)130:4(366): the first two natural
frequencies of the 3-story evaluation model are 0.99 Hz and 3.06 Hz (periods
1.01 s and 0.327 s).

The transcription follows the official benchmark model data (``struct3.m``):
W14x257 / W14x311 columns, per-level beams, and a leaning column.  The lateral
model uses bare-steel beam inertia (the composite slab is gravity-only), so the
periods land on the published values to within ±3 %.
"""

import pytest

from fea_toolkit.benchmarks.sac import SAC3_TCL
from fea_toolkit.io.tcl_importer import parse_tcl_model
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
from fea_toolkit.opensees.preprocessor import preprocess_model

# First two natural periods (s), Ohtori et al. (2004).
REF_PERIODS = (1.01, 0.327)
REF_TOL = 0.03


@pytest.fixture
def builder():
    from openseespy.opensees import wipe

    wipe()
    md = parse_tcl_model(SAC3_TCL.read_text(encoding="utf-8"), {"F": "N", "L": "m", "T": "C"})
    mesh = preprocess_model(md, {"element_type": "elasticBeamColumn"})
    b = AnalysisBuilder(mesh, {})
    b.build_domain()
    yield b
    wipe()


def test_modal_periods_match_otori(builder):
    result = builder.run_modal_analysis(
        num_modes=2, print_results=False, eigen_solver="fullGenLapack"
    )
    got = result["periods"][:2]
    for g, r in zip(got, REF_PERIODS):
        assert g == pytest.approx(r, rel=REF_TOL), f"period {g:.4f} vs reference {r}"
