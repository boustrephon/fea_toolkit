"""A read-only help dialog describing one verb, rendered from its ``StepSpec``.

Everything shown comes from the verb's own declaration — the one-line ``help``,
each parameter's name / type / default / choices / help, its ``needs``
prerequisite, and its ``docs`` / ``citations`` provenance — so the dialog cannot
drift from what the verb actually accepts.
"""

from typing import Any, Optional

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..doc_links import doc_url, doi_url

__all__ = ["VerbHelpDialog"]


class VerbHelpDialog(QDialog):
    """Show a verb's help, parameter surface and prerequisites.

    Args:
        verb: The verb name, for the title.
        spec: The verb's ``StepSpec``.
        parent: Optional Qt parent.
    """

    def __init__(self, verb: str, spec: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{verb} \u2014 help")
        self.resize(560, 400)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(f"<b>{verb}</b>"))
        layout.addWidget(QLabel(spec.help))
        layout.addWidget(QLabel("<i>Parameters</i>"))

        table = QTableWidget(len(spec.params), 4)
        table.setHorizontalHeaderLabels(["Parameter", "Type", "Default", "Help"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for row, (name, param) in enumerate(spec.params.items()):
            table.setItem(row, 0, QTableWidgetItem(name))
            table.setItem(row, 1, QTableWidgetItem(param.type.__name__))
            default = repr(param.default)
            if param.choices:
                default += f"  (choices: {', '.join(map(str, param.choices))})"
            table.setItem(row, 2, QTableWidgetItem(default))
            table.setItem(row, 3, QTableWidgetItem(param.help))
        layout.addWidget(table, 1)

        if spec.needs:
            layout.addWidget(QLabel(f"Needs: {', '.join(spec.needs)}"))

        if spec.docs:
            layout.addWidget(
                self._links_label(
                    "Documentation",
                    [(doc_url(name), name[:-3]) for name in spec.docs],
                )
            )
        if spec.citations:
            layout.addWidget(
                self._links_label(
                    "Citations",
                    [(doi_url(doi), f"doi:{doi}") for doi in spec.citations],
                )
            )

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _links_label(heading: str, links: list[tuple[str, str]]) -> QLabel:
        """A rich-text label of clickable links, opened in the external browser."""
        html = " · ".join(f'<a href="{url}">{text}</a>' for url, text in links)
        label = QLabel(f"<b>{heading}</b> — {html}")
        label.setOpenExternalLinks(True)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setWordWrap(True)
        return label
