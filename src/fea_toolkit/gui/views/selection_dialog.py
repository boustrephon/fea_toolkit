"""Edit a view's ``Selection`` expression, validating as you type.

The expression language is the same one the CLI accepts
(:meth:`fea_toolkit.model.selection.Selection.from_string`), so what is typed
here is exactly what would be typed in a script::

    section=Roof slab z=3:6

An empty field is a valid, unfiltered selection — the view then shows
everything its source does.
"""

from typing import Any, Optional

from qtpy.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from ...model.selection import SELECT_KEYS_HELP, Selection


class SelectionDialog(QDialog):
    """A one-field editor for a view's selection.

    Args:
        current: The selection being edited (``None`` for a fresh duplicate).
        parent: Optional Qt parent widget.
    """

    def __init__(self, current: Optional[Selection] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("View selection")
        self._selection: Optional[Selection] = None

        text = current.to_string() if current is not None else ""

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Show only the elements matching:", self))

        self._field = QLineEdit(text, self)
        self._field.setPlaceholderText("e.g. section=Roof slab z=3:6 — empty shows everything")
        self._field.setMinimumWidth(360)
        layout.addWidget(self._field)

        self._keys = QLabel(f"Keys: {SELECT_KEYS_HELP}", self)
        self._keys.setEnabled(False)
        layout.addWidget(self._keys)

        self._error = QLabel("", self)
        layout.addWidget(self._error)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        layout.addWidget(self._buttons)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        self._field.textChanged.connect(self._validate)

        self._validate(text)
        self._field.setFocus()
        self._field.selectAll()

    # ── Query ────────────────────────────────────────────────────────

    def selection(self) -> Optional[Selection]:
        """The parsed selection (``None`` while the expression is invalid)."""
        return self._selection

    # ── Validation ───────────────────────────────────────────────────

    def _validate(self, text: str) -> None:
        """Parse *text*, showing the error and gating OK on the result."""
        try:
            self._selection = Selection.from_string(text)
        except ValueError as exc:
            self._selection = None
            self._error.setText(str(exc))
        else:
            self._error.setText("")
        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(self._selection is not None)

    # ── Entry point ──────────────────────────────────────────────────

    @staticmethod
    def edit(
        current: Optional[Selection] = None, parent: Optional[Any] = None
    ) -> Optional[Selection]:
        """Run the dialog modally.

        Args:
            current: The selection to start from.
            parent: Optional Qt parent widget.

        Returns:
            The parsed selection — an unfiltered one when the field is left
            empty — or ``None`` when the dialog is cancelled.
        """
        dialog = SelectionDialog(current, parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.selection()
