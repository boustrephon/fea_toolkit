"""Severity-coloured, read-only message log for the GUI's bottom dock."""

import html
from typing import Optional

from qtpy.QtWidgets import QPlainTextEdit, QWidget

# Severity -> foreground colour.  Deliberately muted so the log reads as
# chrome rather than shouting; ``error`` is the only saturated colour.
_LEVEL_COLORS = {
    "info": "#202020",
    "warn": "#8a6d00",
    "error": "#b00020",
}

_MAX_BLOCKS = 5000


class MessageLog(QPlainTextEdit):
    """Read-only, severity-coloured log for the bottom dock.

    ``QPlainTextEdit`` has no ``setTextColor`` (that is a ``QTextEdit`` slot),
    so each line is appended as a colour-styled HTML span; ``toPlainText()``
    still yields the plain message.

    Args:
        parent: Optional Qt parent widget.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setMaximumBlockCount(_MAX_BLOCKS)

    def log(self, message: str, level: str = "info") -> None:
        """Append one line to the log.

        Args:
            message: The text to append.
            level: ``"info"`` | ``"warn"`` | ``"error"`` -- selects the colour.
        """
        color = _LEVEL_COLORS.get(level, _LEVEL_COLORS["info"])
        text = html.escape(message).replace("\n", "<br>")
        self.appendHtml(f'<span style="color: {color};">{text}</span>')
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())
