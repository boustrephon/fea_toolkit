"""Analysis-builder mixin: analysis runners (facade re-export).

The runner logic is split into per-analysis-type mixins so no single
module dominates the runner layer:

* :mod:`fea_toolkit.opensees._runner_static` — static analysis, seismic
  masses, and result extraction.
* :mod:`fea_toolkit.opensees._runner_modal` — modal (eigenvalue) analysis.
* :mod:`fea_toolkit.opensees._runner_rs` — response-spectrum analysis.
* :mod:`fea_toolkit.opensees._runner_pushover` — pushover analysis and
  post-processing.

``RunnerMixin`` combines the four so :class:`AnalysisBuilder` keeps its
single mixin import unchanged.
"""

from ._runner_modal import ModalRunnerMixin
from ._runner_pushover import PushoverRunnerMixin, _record_step
from ._runner_rs import RsRunnerMixin
from ._runner_static import StaticRunnerMixin, _normalise_frame_response

__all__ = [
    "RunnerMixin",
    "_normalise_frame_response",
    "_record_step",
]


class RunnerMixin(
    StaticRunnerMixin,
    ModalRunnerMixin,
    RsRunnerMixin,
    PushoverRunnerMixin,
):
    """Analysis execution mixin — static / modal / RS / pushover runners."""
