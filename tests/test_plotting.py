"""Tests for the plotting public API and report figures.

Covers the public-API import surface and the CSM four-panel report
figure.  Function-level viz tests live in the mirror files
``test_viz_model.py``, ``test_viz_pushover.py``, ``test_viz_common.py``
and ``test_force_diagram.py``.
"""

from typing import ClassVar

import numpy as np
import pytest

# ============================================================================
# CSM four-panel report figure
# ============================================================================


def _minimal_csm_all_out() -> dict:
    """Minimal ``run_pushover_4dir()``-shaped output with one converged direction."""
    return {
        "+X": {
            "adrs": {"S_d": [0.0, 0.10, 0.20], "S_a": [0.0, 0.50, 1.00]},
            "pp": {
                "S_dy": 0.10,
                "S_ay": 0.50,
                "S_dp": 0.20,
                "S_ap": 1.00,
                "converged": True,
                "mu": 2.0,
            },
        }
    }


# ============================================================================
# CSM four-panel report figure
# ============================================================================


class TestCsmFourPanelUnits:
    """The CSM demand spectrum and axis labels follow the resolved units."""

    def test_defaults_to_si_labels(self):
        """Omitting g/units keeps the SI fallback labels (m, m/s²)."""
        import matplotlib.pyplot as plt

        from fea_toolkit.plotting.report import plot_csm_4panel

        fig = plot_csm_4panel(_minimal_csm_all_out(), {})
        assert fig is not None
        ax = fig.axes[0]
        assert ax.get_xlabel() == "S$_d$ (m)"
        assert ax.get_ylabel() == "S$_a$ (m/s\u00b2)"
        plt.close(fig)

    def test_units_argument_scales_and_labels(self):
        """units= puts the demand spectrum and axis labels in model units."""
        import matplotlib.pyplot as plt

        from fea_toolkit.plotting.report import plot_csm_4panel

        fig = plot_csm_4panel(_minimal_csm_all_out(), {}, units={"F": "N", "L": "mm", "T": "C"})
        assert fig is not None
        ax = fig.axes[0]
        assert ax.get_xlabel() == "S$_d$ (mm)"
        assert ax.get_ylabel() == "S$_a$ (mm/s\u00b2)"
        assert "mm/s" in ax.get_title()
        plt.close(fig)

    def test_suptitle_reflects_plotted_parameters(self):
        """The suptitle names the parameters used, not stale placeholders."""
        import matplotlib.pyplot as plt

        from fea_toolkit.plotting.report import plot_csm_4panel

        fig = plot_csm_4panel(_minimal_csm_all_out(), {}, tg=0.35, alpha_max_rare=0.50)
        assert fig is not None
        suptitle = fig.get_suptitle()
        assert "Tg=0.35s" in suptitle
        assert "0.50" in suptitle
        assert "Project A" not in suptitle
        assert "Site I" not in suptitle
        plt.close(fig)

    def test_explicit_title_overrides_the_default(self):
        """A caller-supplied title replaces the derived suptitle."""
        import matplotlib.pyplot as plt

        from fea_toolkit.plotting.report import plot_csm_4panel

        fig = plot_csm_4panel(_minimal_csm_all_out(), {}, title="Custom CSM Title")
        assert fig is not None
        assert fig.get_suptitle() == "Custom CSM Title"
        plt.close(fig)


# ============================================================================
# Public plotting API
# ============================================================================


class TestPlottingImports:
    def test_rs_force_diagram_no_data(self):
        from fea_toolkit.plotting import plot_force_diagram

        fig = plot_force_diagram([], quantity="My_i", kind="rs", dimension="2d")
        assert fig is None

    def test_rs_force_diagram_unwraps_result_dict(self):
        """plot_force_diagram accepts the full extract_element_rs_forces dict."""
        from fea_toolkit.plotting import plot_force_diagram

        # Empty dict unwraps to no element results → None (no matplotlib needed)
        assert plot_force_diagram({}, quantity="My_i", kind="rs", dimension="2d") is None

    def test_deprecated_plot_names_removed(self):
        """The deprecated plot functions are no longer part of the public API.

        Note: ``plot_force_diagram`` is intentionally absent from this list —
        the name was reintroduced as the *unified* force-diagram dispatcher
        (``fea_toolkit.plotting.force_diagram.plot_force_diagram``), which is
        a different function from the deprecated 3D-static plotter removed in
        the Phase-3 cleanup.
        """
        from fea_toolkit import plotting

        for name in (
            "plot_model_3d",
            "plot_deformed_3d",
            "plot_rs_deformed_3d",
            "plot_mode_3d",
            "plot_static_moment_3d",
            "plot_static_shear_3d",
            "plot_static_axial_3d",
            "plot_static_force_diagram",
            # Phase-B unified-dispatcher cleanup (removed 2026-08-24)
            "plot_force_diagram_3d",
            "plot_rs_force_diagram",
            "plot_npz_force_diagram",
            "plot_npz_moment_3d",
        ):
            assert not hasattr(plotting, name), f"{name} should have been removed"

    def test_force_diagram_unified_import(self):
        """The unified plot_force_diagram dispatcher is importable."""
        from fea_toolkit.plotting import plot_force_diagram

        assert callable(plot_force_diagram)

    def test_force_diagram_invalid_quantity(self):
        """Invalid quantity returns None."""
        from fea_toolkit.plotting import plot_force_diagram

        result = plot_force_diagram({}, quantity="ZZ")
        assert result is None

    def test_force_diagram_no_data_builder(self):
        """Builder without force_data returns None."""
        from fea_toolkit.plotting import plot_force_diagram

        # Use a minimal mock that satisfies _resolve_mesh_data
        class MockModel:
            nodes: ClassVar = {}
            frame_elements: ClassVar = {}
            area_elements: ClassVar = {}
            frame_assignments: ClassVar = {}
            area_assignments: ClassVar = {}

        class MockBuilder:
            model = MockModel()
            split_elements: ClassVar = {}
            split_assignments: ClassVar = {}
            _mesh_model = None

        result = plot_force_diagram(MockBuilder())
        assert result is None

    def test_force_diagram_npz_no_static(self):
        """NPZ dict without static cases raises ValueError."""
        import pytest

        from fea_toolkit.plotting import plot_force_diagram

        with pytest.raises(ValueError, match="No static cases found"):
            plot_force_diagram({}, quantity="Mz", kind="static", dimension="3d")

    def test_unified_functions_import(self):
        """All unified functions are importable from the plotting package."""
        from fea_toolkit.plotting import (
            compare_meshes,
            plot_mesh,
            plot_mode_animation,
        )

        assert callable(plot_mesh)
        assert callable(compare_meshes)
        assert callable(plot_mode_animation)

    def test_model_viewer_import_and_types(self):
        """ModelViewer and its data types import correctly."""

        from fea_toolkit.plotting.renderers import (
            AnnotationDef,
            FrameGeom,
            HighlightDef,
            NodeGeom,
            ShellGeom,
        )

        # Data types construct
        f = FrameGeom(
            elem_id="1", section="UB300", node_i="1", node_j="2", start=np.zeros(3), end=np.ones(3)
        )
        assert f.elem_id == "1"

        s = ShellGeom(area_id="1", section="SLAB", vertices=np.zeros((4, 3)))
        assert s.area_id == "1"

        n = NodeGeom(node_id="1", position=np.zeros(3))
        assert n.node_id == "1"

        h = HighlightDef(frame_ids=["1"], color=(1, 0, 0), label="Test")
        assert h.label == "Test"

        a = AnnotationDef(text="Hello", position=np.zeros(3))
        assert a.text == "Hello"

    @pytest.mark.needs_pyvista
    def test_model_viewer_from_sample(self):
        """ModelViewer extracts geometry from sample model data."""
        from examples.sample_model import make_sample_model
        from fea_toolkit.plotting import ModelViewer

        md = make_sample_model()
        viewer = ModelViewer(model_data=md, backend="pyvista", off_screen=True)

        # show_model should extract geometry and render
        viewer.show_model(show_nodes=True, show_shells=False)
        assert viewer._geom_extracted
        assert len(viewer._frames) == 1
        assert viewer._frames[0].elem_id == "1"

        # Test highlight
        viewer.highlight_elements(frame_ids=["1"], color=(1, 0, 0), label="Test")

        # Test annotation
        viewer.annotate("Hi", node_id="2", color=(1, 1, 0))

        # Test screenshot
        import os
        import tempfile

        tmp = tempfile.mktemp(suffix=".png")
        viewer.screenshot(tmp)
        assert os.path.getsize(tmp) > 0
        os.remove(tmp)

        viewer.clear()
