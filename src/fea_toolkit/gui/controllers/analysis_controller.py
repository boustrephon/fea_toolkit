"""Qt-free composition of the analysis steps the Analysis menu presets author.

The window's Analysis-menu entries and the ``Analysis ▸ Run…`` dialog author
recipe steps; this module owns the *composition* of those steps — which verb,
which parameters — so the window handlers stay thin and the step vocabulary is
unit-testable without a running GUI.  The dialogs, the recipe panel and the run
trigger stay in the window.
"""

from typing import Any, Optional

from ...workflow import Step

__all__ = ["AnalysisController"]


class AnalysisController:
    """Compose the analysis steps the Analysis menu's presets and Run dialog author."""

    @staticmethod
    def static_steps(
        cases: dict, combinations: Optional[list] = None, config: Optional[dict] = None
    ) -> list:
        """The ``[run_static, combine, load_verification]`` recipe for a static solve.

        Args:
            cases: ``{case: {pattern: factor}}`` to solve.
            combinations: Combination names to reduce — resolved against the
                model's definitions by the ``combine`` verb.
            config: Optional ``AnalysisBuilder`` config overrides, applied to
                both the solve and the summary's standalone re-solve fallback.

        Returns:
            The ordered steps; ``combine`` is present only when *combinations*
            is named.
        """
        steps = [
            Step(verb="run_static", params={"cases": dict(cases), "config": dict(config or {})})
        ]
        if combinations:
            steps.append(Step(verb="combine", params={"combinations": list(combinations)}))
        steps.append(Step(verb="load_verification", params={"config": dict(config or {})}))
        return steps

    @staticmethod
    def preset_prefill(verb: str, default_modal: str = "") -> dict[str, Any]:
        """The dialog prefill for an Analysis-menu preset step.

        ``modal`` prefills its ``name`` from the model's modal case; the
        spectrum / pushover / nonlinear-dynamic / IDA presets prefill their
        ``modal_case`` from it, so the step the user accepts resolves against the
        modal precursor that will actually run.

        Args:
            verb: The analysis verb (``"modal"``, ``"response_spectrum"``,
                ``"pushover"``, ``"nonlinear_dynamic"`` or ``"ida"``).
            default_modal: The model's preferred modal-case name, or ``""``.

        Returns:
            The parameter dict to prefill (``{}`` when nothing applies).
        """
        prefill: dict[str, Any] = {}
        if verb == "modal" and default_modal:
            prefill["name"] = default_modal
        elif (
            verb in ("response_spectrum", "pushover", "nonlinear_dynamic", "ida") and default_modal
        ):
            prefill["modal_case"] = default_modal
        return prefill
