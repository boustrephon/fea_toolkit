"""The Ctrl+K command palette — a command string compiled into a recipe step.

A Qt-only wrapper over
:func:`fea_toolkit.workflow.command.compile_command`; the grammar itself is
Qt-free and lives in :mod:`fea_toolkit.workflow.command`.  The palette
autocompletes verb names and shows which prerequisite kind is still missing
before a step is accepted.
"""

from typing import Any, Optional

from qtpy.QtCore import Qt
from qtpy.QtWidgets import QCompleter, QDialog, QLabel, QLineEdit, QVBoxLayout

from ...workflow import STEP_SPECS, compile_command, missing_needs, satisfied_kinds


def get_step(parent: Any, recipe_steps: Any, *, initial: Any = ()) -> Optional[Any]:
    """Open the palette and return the compiled step, or ``None`` on cancel.

    Args:
        parent: The parent widget.
        recipe_steps: The steps already in the recipe (for the precursor hint).
        initial: Kinds already satisfied (e.g. ``{"geometry"}`` when a prepared
            topology exists).

    Returns:
        The compiled :class:`~fea_toolkit.workflow.steps.Step`, or ``None``.
    """
    dialog = _PaletteDialog(parent, recipe_steps, initial=initial)
    if dialog.exec_() != QDialog.DialogCode.Accepted:
        return None
    return dialog.step()


class _PaletteDialog(QDialog):
    """A line edit that compiles a command into a step on Enter.

    The hint line shows the verb's declared ``needs`` against the recipe so far,
    so a step whose precursor is missing is flagged before it is accepted.
    """

    def __init__(self, parent: Any, recipe_steps: Any, *, initial: Any = ()) -> None:
        super().__init__(parent)
        self.setWindowTitle("Command")
        self.setMinimumWidth(440)
        self._produced = satisfied_kinds(recipe_steps, initial=initial)
        self._step: Any = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Type a command \u2014 <verb> [KEY=VALUE \u2026]:"))
        self._edit = QLineEdit(self)
        self._edit.setPlaceholderText('e.g. mesh section="brick wall" create_shells=True')
        completer = QCompleter(sorted(STEP_SPECS), self._edit)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._edit.setCompleter(completer)
        layout.addWidget(self._edit)

        self._hint = QLabel("")
        self._hint.setWordWrap(True)
        layout.addWidget(self._hint)

        self._edit.returnPressed.connect(self._on_accept)
        self._edit.textChanged.connect(self._on_changed)

    def _on_changed(self, text: str) -> None:
        """Show the verb's help, or its missing precursor."""
        verb = text.strip().partition(" ")[0]
        spec = STEP_SPECS.get(verb)
        if spec is None:
            self._hint.setText("")
            return
        missing = missing_needs(verb, self._produced)
        if missing:
            self._hint.setText(
                f"needs {', '.join(missing)} \u2014 run a step that produces it first"
            )
        else:
            self._hint.setText(spec.help)

    def _on_accept(self) -> None:
        text = self._edit.text().strip()
        try:
            self._step = compile_command(text)
        except ValueError as exc:
            self._hint.setText(str(exc))
            return
        self.accept()

    def step(self) -> Any:
        """The compiled step, after an accepted command."""
        return self._step
