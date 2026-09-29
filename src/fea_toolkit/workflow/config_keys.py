"""Curated OpenSees builder configuration keys for the GUI's config editor.

The ``run_static`` verb's ``config`` parameter is a flat dict of ``AnalysisBuilder``
options.  A free-form literal is the honest representation, but it hides every
option the dict accepts.  This module names the keys a GUI user is most likely to
set, each with its type, its default and a one-line explanation, so the parameter
form can render a structured editor.

**Curated, not exhaustive.**  The builder accepts more keys than are listed here
(see ``AnalysisBuilder._set_defaults`` and ``PUSHOVER_SOLVER_DEFAULTS``); a key
absent from this manifest is simply edited as a free-form literal in the recipe's
JSON.  The ``default`` shown for each key is the builder's documented default and
is used only to seed the editor and to decide which keys a user *changed* — a key
left untouched is omitted, so the builder applies its own default.

This module is Qt-free and imports no OpenSees, so a form can be rendered before a
model is open.
"""

from .steps import ParamSpec

__all__ = ["BUILDER_CONFIG_KEYS"]

BUILDER_CONFIG_KEYS: dict[str, ParamSpec] = {
    "element_type": ParamSpec(
        "elasticBeamColumn",
        str,
        choices=("elasticBeamColumn", "forceBeamColumn", "dispBeamColumn"),
        help="Frame element formulation.",
    ),
    "geom_transf_type": ParamSpec(
        "Linear",
        str,
        choices=("Linear", "PDelta", "Corotational"),
        help="Geometric transformation applied to the frame members.",
    ),
    "verbose": ParamSpec(False, bool, help="Print the solver's output to the console."),
    "use_elastic_sections": ParamSpec(
        True, bool, help="Use elastic section objects (off builds fiber sections)."
    ),
    "create_fiber_sections": ParamSpec(
        False, bool, help="Build fiber sections for nonlinear elements."
    ),
    "apply_releases": ParamSpec(True, bool, help="Honour SAP2000 member end releases."),
    "apply_rigid_bodies": ParamSpec(
        True, bool, help="Apply SAP2000 BODY constraints as rigid links."
    ),
    "simplify_distributed_loads": ParamSpec(
        False, bool, help="Decompose non-uniform member loads into uniform segments."
    ),
    "constraint_method": ParamSpec("spring", str, help="Constraint handling method."),
    "solver_algorithm": ParamSpec(
        "Newton",
        str,
        choices=("Newton", "KrylovNewton", "ModifiedNewton"),
        help="Solution algorithm.",
    ),
    "solver_system": ParamSpec("BandGen", str, help="Linear equation solver."),
    "solver_test_tol": ParamSpec(1e-6, float, help="Convergence tolerance for the solution test."),
    "solver_test_max_iter": ParamSpec(10, int, help="Maximum iterations per step."),
    "gravity_num_substeps": ParamSpec(1, int, help="Number of substeps gravity is ramped over."),
}
