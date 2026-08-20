"""Validation: genuinely 3D RC moment-frame pushover (Gap 3 — 3D-only).

The toolkit's analysis workflows are 3D-only by design (``ndm=3``,
``ndf=6``) — see ``.clinerules`` §3.11 and ``docs/llm_guide.md`` §6.
:func:`make_rc_frame_model` is a *planar* X–Z portal (a "2D" frame in the
3D domain via out-of-plane restraints); :func:`make_rc_frame_3d` is its
genuinely 3D counterpart — a single-storey 2-bay × 2-bay RC moment frame
with non-zero Y coordinates — exercising the same Preprocessor →
AnalysisBuilder → pushover pipeline on a full 3D frame grid.

Closes Gap 3 of ``docs/deprecation_plan.md`` ("2D vs 3D model dispatch",
resolved as designed — 3D-only): validates that the single 3D domain path
handles both planar and genuinely-3D RC frames.
"""

import numpy as np
import pytest

from examples.sample_model import make_rc_frame_3d
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
from fea_toolkit.opensees.preprocessor import preprocess_model

_CFG = {"element_type": "elasticBeamColumn", "verbose": False, "create_shells": False}


@pytest.fixture
def rc3d_builder():
    """Preprocessed genuinely-3D RC frame wrapped in an AnalysisBuilder."""
    from openseespy.opensees import wipe

    mesh_model = preprocess_model(make_rc_frame_3d(), _CFG)
    builder = AnalysisBuilder(mesh_model, _CFG)
    builder.build_domain()
    yield builder
    wipe()


class TestRC3DFrame:
    def test_genuinely_3d_geometry(self):
        """The fixture spans a non-degenerate Y axis (true 3D, not planar)."""
        md = make_rc_frame_3d()
        ys = {n.y for n in md.nodes.values()}
        assert ys == {0.0, 4.0, 8.0}, f"expected 3 Y levels, got {sorted(ys)}"
        assert len(md.nodes) == 18  # 9 base + 9 roof
        assert len(md.frame_elements) == 21  # 9 columns + 12 beams

    def test_modal_symmetric_x_y(self, rc3d_builder):
        """Modal analysis runs on the 3D frame; the symmetric X/Y modes match."""
        rc3d_builder.compute_seismic_masses()
        modal = rc3d_builder.run_modal_analysis(num_modes=3)
        p = np.asarray(modal["periods"], dtype=float)
        assert np.all(np.isfinite(p)), f"non-finite periods: {p}"
        assert abs(p[0] - p[1]) / p[0] < 1e-2, (
            f"symmetric 2x2 frame: X/Y modes should share a period, got {p[:2]}"
        )

    def test_pushover_3d_rc_frame_yields_and_converges(self, rc3d_builder):
        """Full 3D RC frame converges through the 3D pushover path and yields."""
        rc3d_builder.compute_seismic_masses()
        # Control node: roof node "12" (leading corner, max X at top storey).
        # Derive its OpenSees tag from the MeshModel rather than hard-coding
        # it, so the tag follows the mesh node's identifier (ID → tag map).
        control_node_tag = rc3d_builder.mesh_model.nodes["12"].node_tag
        res = rc3d_builder.run_pushover_analysis(
            gravity_patterns={"DEAD": 1.0},
            lateral_load_type="uniform",
            lateral_direction="X",
            control_node_tag=control_node_tag,
            max_disp=0.15,
            num_steps=50,
            print_progress=False,
        )
        assert res["units"] == {"F": "KN", "L": "m", "T": "C"}
        d = np.asarray(res["control_disp"], dtype=float)
        v = np.asarray(res["base_shear"], dtype=float)
        assert len(d) == 51
        assert np.all(np.diff(d) > 0), "control displacement must ramp monotonically"
        assert np.all(v[1:] > 0), "base shear must stay positive"
        # Yielding: the response softens measurably vs the initial stiffness.
        k_init = v[1] / d[1]
        k_end = (v[-1] - v[-2]) / (d[-1] - d[-2])
        assert k_end / k_init < 0.5, (
            f"expected visible softening (yielding), k_end/k_init = {k_end / k_init:.3f}"
        )
