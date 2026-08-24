"""fea_toolkit — FEA to OpenSees/Rhino conversion toolkit.

Quick start
-----------
The canonical pipeline is::

    from fea_toolkit import (
        SAP2000Parser,           # parser
        preprocess_model,        # topology prep
        AnalysisBuilder,         # OpenSees domain + analysis
        plot_mesh,               # visualisation
    )
    from fea_toolkit.io import write_results_npz  # NPZ serialisation

    # 1. Parse
    md = SAP2000Parser("model.s2k").parse().get_model_data()

    # 2. Preprocess (split elements, mesh areas)
    mesh = preprocess_model(md, {"element_type": "elasticBeamColumn"})

    # 3. Build OpenSees domain and run analysis
    builder = AnalysisBuilder(mesh, config={})
    builder.build_domain()
    results = builder.run_static_analysis()

    # 4. Visualise
    plot_mesh(builder)
    plot_deformed_displacement_3d(builder, results, scale=100.0)

Task → Function reference
-------------------------
| "I want to..."                                   | Recommended function                         |
|--------------------------------------------------|----------------------------------------------|
| Parse a SAP2000 .s2k file                        | ``SAP2000Parser(path).parse().get_model_data()`` |
| Filter elements by type/section/group            | ``Selection(element_types=['Frame'], ...)``     |
| Preprocess (split, mesh) a model                 | ``preprocess_model(md, config)``               |
| Build & run static analysis                      | ``AnalysisBuilder(mesh, config).build_domain()`` + ``.run_static_analysis()`` |
| Run modal (eigenvalue) analysis                  | ``AnalysisBuilder(...).run_modal_analysis()`` |
| Run response spectrum (CQC)                      | ``AnalysisBuilder(...).run_response_spectrum_analysis()`` |
| Run pushover (non-linear) analysis               | ``AnalysisBuilder(...).run_pushover_analysis()`` |
| Print a modal summary table                      | ``from fea_toolkit.io import modal_table``      |
| Plot the 3D model                                | ``plot_mesh(builder)``                           |
| Plot deformed shape                              | ``plot_deformed_displacement_3d(builder, results, scale=100)`` |
| Plot force/moment diagrams                       | ``plot_force_diagram(builder, results, quantity='Mz')`` |
| Plot pushover capacity curve                     | ``plot_pushover_curve(results)``                |
| Plot storey displacements / drifts               | ``from fea_toolkit.model import storey_displacements`` |
| Export results to NPZ                            | ``from fea_toolkit.io import write_results_npz`` |
| Export model to Tcl script                       | ``from fea_toolkit.opensees import export_mesh_model_to_tcl`` |
| Visualise in browser (interactive)               | ``plot_interactive_viewer(builder, results)``   |
| Check model connectivity                         | ``from fea_toolkit.model import check_model_connectivity`` |
| Check brace buckling                             | ``from fea_toolkit.model import check_brace_buckling`` |

OpenSeesPy element / material support
--------------------------------------
The toolkit uses ``openseespy``. See ``typings/openseespy/opensees/__init__.pyi``
for the full set of stubbed functions. Known limitations:

- **8-argument trapezoidal ``eleLoad``** is broken in OpenSeesPy 3.8.0.0
  — non-uniform loads are decomposed into 4 partial-span uniform segments.
- **``Corotational`` geometric transformation** does not support ``eleLoad``
  in 3D — a warning is emitted if used.
- **Brace modelling**: Approach B (``Truss`` + ``Hysteretic``) is recommended
  for pushovers — numerically robust with asymmetric tension/compression.
  See ``docs/pushover_analysis.md`` for details.
"""

import importlib.metadata

try:
    __version__ = importlib.metadata.version("fea_toolkit")
except importlib.metadata.PackageNotFoundError:
    __version__ = "0.0.0.dev0+unknown"


def ops_version() -> str:
    """Return the installed OpenSeesPy version string.

    Returns:
        Version string (e.g. ``"3.8.0.0"``) if openseespy is installed,
        or ``"unknown (openseespy not installed)"`` otherwise.
    """
    try:
        return importlib.metadata.version("openseespy")
    except importlib.metadata.PackageNotFoundError:
        return "unknown (openseespy not installed)"


# ── Core model types ───────────────────────────────────────────────

# ── Pipeline ───────────────────────────────────────────────────────
from fea_toolkit.io.s2k_parser import SAP2000Parser
from fea_toolkit.model.mesh_model import MeshModel
from fea_toolkit.model.sap_data import (
    AreaElement,
    BoxSection,
    CircularSection,
    FrameDistributedLoad,
    FrameElement,
    ISection,
    JointLoad,
    LoadPattern,
    Material,
    Node,
    PipeSection,
    RectangularSection,
    SAPModelData,
    Section,
)
from fea_toolkit.model.sections import SectionLibrary
from fea_toolkit.model.selection import Selection
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder, run_modal
from fea_toolkit.opensees.preprocessor import Preprocessor, preprocess_model
from fea_toolkit.opensees.pushover import pushover_rc_openseespy

# ── Visualisation ─────────────────────────────────────────────────
from fea_toolkit.plotting.force_diagram import plot_force_diagram
from fea_toolkit.plotting.interactive_viewer import plot_interactive_viewer
from fea_toolkit.plotting.viewer import ModelViewer
from fea_toolkit.plotting.viz import (
    compare_meshes,
    plot_building_views,
    plot_capacity_spectrum,
    plot_deformed_displacement_3d,
    plot_mesh,
    plot_mode_animation,
    plot_model_comparison,
    plot_pushover_curve,
)

# ── Report ─────────────────────────────────────────────────────────
from fea_toolkit.report import generate_report
from fea_toolkit.spectrum import ResponseSpectrum

__all__ = [
    "AnalysisBuilder",
    "AreaElement",
    "BoxSection",
    "CircularSection",
    "FrameDistributedLoad",
    "FrameElement",
    "ISection",
    "JointLoad",
    "LoadPattern",
    "Material",
    "MeshModel",
    "ModelViewer",
    "Node",
    "PipeSection",
    "Preprocessor",
    "RectangularSection",
    "ResponseSpectrum",
    # Pipeline
    "SAP2000Parser",
    # Core model types
    "SAPModelData",
    "Section",
    "SectionLibrary",
    "Selection",
    # Version
    "__version__",
    "compare_meshes",
    # Report
    "generate_report",
    "ops_version",
    "plot_building_views",
    "plot_capacity_spectrum",
    "plot_deformed_displacement_3d",
    "plot_force_diagram",
    "plot_interactive_viewer",
    "plot_mesh",
    "plot_mode_animation",
    # Visualisation
    "plot_model_comparison",
    "plot_pushover_curve",
    "preprocess_model",
    "pushover_rc_openseespy",
    "run_modal",
]
