"""The guided Analysis wizard — pick the modal→spectrum→pushover→IDA chain.

One dialog lists the modal-dependent analysis chain, greying each analysis
until its precursors are satisfied, and returns the chosen verbs in run order.
The gating is computed Qt-free in
:func:`fea_toolkit.workflow.registry.analysis_chain_rows`, so the wizard cannot
drift from the recipe validator's ``needs`` vocabulary.
"""

from typing import Any, Optional

from qtpy.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QVBoxLayout,
)

from ...workflow import ANALYSIS_CHAIN, STEP_SPECS, analysis_chain_rows

__all__ = ["AnalysisWizard"]

#: Human labels for the chain verbs, in menu language.
_VERB_LABELS = {
    "modal": "Modal analysis",
    "response_spectrum": "Response spectrum",
    "pushover": "Pushover",
    "nonlinear_dynamic": "Nonlinear time-history",
    "ida": "IDA",
}


class AnalysisWizard(QDialog):
    """Tick the analyses to run; each row greys until its precursors hold.

    Args:
        produced: The kinds already satisfied (see
            :func:`~fea_toolkit.workflow.recipe.satisfied_kinds`) — typically
            ``{"geometry"}`` (a preprocessed model) plus whatever the recipe has
            already solved.
        parent: Optional Qt parent widget.
    """

    def __init__(self, produced: set, parent: Optional[Any] = None):
        super().__init__(parent)
        self.setWindowTitle("Analysis wizard")
        self._produced = set(produced)
        self._checks: dict[str, QCheckBox] = {}

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Run a chain of analyses in one pass.  Each analysis needs the ones "
            "above it — tick them in order."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        for row, verb in enumerate(ANALYSIS_CHAIN):
            spec = STEP_SPECS[verb]
            check = QCheckBox(_VERB_LABELS.get(verb, verb), self)
            check.toggled.connect(self._recompute)
            self._checks[verb] = check
            help_label = QLabel(spec.help)
            help_label.setWordWrap(True)
            grid.addWidget(check, row, 0)
            grid.addWidget(help_label, row, 1)
        layout.addLayout(grid)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._recompute()

    def _recompute(self, _checked: bool = False) -> None:
        """Grey (and untick) every row whose precursors are not yet satisfied."""
        wanted = {verb for verb, check in self._checks.items() if check.isChecked()}
        for verb, _help, runnable in analysis_chain_rows(self._produced, wanted):
            check = self._checks[verb]
            if not runnable:
                check.blockSignals(True)
                check.setChecked(False)
                check.blockSignals(False)
            check.setEnabled(runnable)

    def chosen(self) -> list[str]:
        """The ticked verbs, in chain order."""
        return [verb for verb in ANALYSIS_CHAIN if self._checks[verb].isChecked()]

    @staticmethod
    def get_request(produced: set, parent: Optional[Any] = None) -> Optional[list[str]]:
        """Run the wizard modally and return the chosen verbs, or ``None``.

        Args:
            produced: Kinds already satisfied (see :meth:`__init__`).
            parent: Optional Qt parent widget.

        Returns:
            The ticked verbs in chain order, or ``None`` when cancelled.
        """
        dialog = AnalysisWizard(produced, parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.chosen()
