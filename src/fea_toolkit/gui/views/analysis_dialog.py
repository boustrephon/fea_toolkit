"""Choose what to run: static cases, combinations, or a case authored here.

The ``Analysis ▸ Run`` dialog is the clickable form of the listing in
:mod:`fea_toolkit.analysis.case_listing`:

* **Static-linear cases** — every case the model offers, each with a **load
  multiplier** (default ``1.0`` = the case exactly as the ``.s2k`` defines it).
  Ticking several runs several solves.
* **Combinations** — tick one to run the load cases it needs and then reduce
  them to composites (``build_combination_results``).  A combination whose
  leaves are not all statically runnable (a cyclic reference, or a
  response-spectrum leaf) is shown greyed with the reason, never silently
  dropped.
* **Custom case** — author ``{"ULT": {"Dead": 1.4, "Live": 1.6}}`` by ticking
  load patterns and giving each a factor; that is one more solve, of factored
  patterns, exactly like a model case.

The factor is a **load** multiplier, not a post-hoc scaling of results — the
same rule the runner documents (``docs/load_cases_and_combinations.md``).  This
module is Qt-only; everything it lists is computed Qt-free in
``analysis/case_listing.py`` and handed in, which is what keeps the dialog a
thin view over the model.
"""

from dataclasses import dataclass, field
from typing import Any, Optional

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...workflow.config_keys import BUILDER_CONFIG_KEYS
from .config_editor import ConfigEditor

__all__ = ["AnalysisDialog"]

#: The widest a factor may be set.  Negative factors are allowed — reversing a
#: load's sense is legitimate (``-1.0`` flips it) — so the range is symmetric.
_FACTOR_RANGE = 1000.0


@dataclass
class _CaseRow:
    """One static-case row: its stored patterns and the widgets that set it."""

    name: str
    patterns: dict[str, float]
    check: QCheckBox
    factor: QDoubleSpinBox


@dataclass
class _ComboRow:
    """One combination row: its spec and the checkbox that selects it."""

    name: str
    leaves: dict[str, float]
    check: QCheckBox


@dataclass
class _PatternRow:
    """One custom-case pattern row."""

    name: str
    check: QCheckBox
    factor: QDoubleSpinBox


@dataclass
class _Request:
    """What the dialog asks for, in the shape the runner consumes.

    Attributes:
        cases: ``{case_name: {pattern: factor}}`` — the full run set: every
            ticked model case (its patterns scaled by the row factor), the
            leaves of any ticked combination, and the custom case if authored.
        combinations: Combination names to reduce from those case results.
        config: OpenSees builder overrides (a partial ``AnalysisBuilder`` config
            dict), empty when the user left the Configuration group untouched.
    """

    cases: dict[str, dict[str, float]] = field(default_factory=dict)
    combinations: list[str] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)


class AnalysisDialog(QDialog):
    """Pick cases and combinations to solve, with a per-case load multiplier.

    Args:
        cases: ``{case_name: {pattern: factor}}`` — the static cases the model
            offers (:func:`fea_toolkit.analysis.case_listing.list_static_cases`).
        combinations: The model's combinations, each a
            :class:`~fea_toolkit.analysis.case_listing.CombinationSpec`.
        patterns: Load-pattern names, for authoring a custom case.
        parent: Optional Qt parent widget.
    """

    def __init__(
        self,
        cases: dict[str, dict[str, float]],
        combinations: Any,
        patterns: Any,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Run analysis")
        self._cases = {name: dict(pats) for name, pats in cases.items()}
        self._combinations = list(combinations)
        self._patterns = list(patterns)
        self._case_rows: list[_CaseRow] = []
        self._combo_rows: list[_ComboRow] = []
        self._pattern_rows: list[_PatternRow] = []

        outer = QVBoxLayout(self)
        outer.addWidget(
            QLabel(
                "Tick what to solve.  A factor is a <b>load</b> multiplier: "
                "1.0 runs the case exactly as the model defines it.",
                self,
            )
        )

        body = QWidget(self)
        body_layout = QVBoxLayout(body)
        body_layout.addWidget(self._build_case_group())
        body_layout.addWidget(self._build_combo_group())
        body_layout.addWidget(self._build_custom_group())
        body_layout.addWidget(self._build_config_group())
        body_layout.addStretch(1)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Run")
        outer.addWidget(self._buttons)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)

        self._update_ok()
        self.resize(560, 620)

    # ── Query ────────────────────────────────────────────────────────

    def request(self) -> _Request:
        """What is ticked, resolved to the runner's own shape.

        Model cases contribute their patterns scaled by the row factor; a ticked
        combination adds its leaf cases (at the model's own factors) so they can
        be reduced; a named custom case adds one more entry.

        A case left at ``1.0`` keeps its model name.  A **scaled** case is stored
        under a distinct name, so the model's own case stays available unscaled:
        a combination reduces the solved cases through the *model's* factors, so
        folding a scaled solve under the model name would silently apply the
        scale twice.  The un-scaled leaf a combination needs is added below.
        A generated name that would collide with a model case — or with another
        scaled case — gains a `` (2)``, `` (3)`` … suffix via
        :func:`_unique_scaled_name`.
        """
        run: dict[str, dict[str, float]] = {}
        # Every model case name is claimed up front: a scaled solve that reused
        # one would shadow that case for the reduction below, which resolves its
        # leaves by *model* name.
        taken: set[str] = set(self._cases)
        for row in self._case_rows:
            if not row.check.isChecked():
                continue
            scale = float(row.factor.value())
            if scale == 1.0:
                run[row.name] = dict(row.patterns)
            else:
                name = _unique_scaled_name(f"{row.name} \u00d7{scale:g}", taken)
                taken.add(name)
                run[name] = {p: f * scale for p, f in row.patterns.items()}

        combos: list[str] = []
        for row in self._combo_rows:
            if not row.check.isChecked():
                continue
            combos.append(row.name)
            for leaf in row.leaves:
                # The reduction reads the *model's* factors, so the leaf must be
                # the un-scaled model definition, never a scaled solve.
                run.setdefault(leaf, dict(self._cases.get(leaf, {})))

        custom_name = self._custom_name.text().strip()
        custom = {
            row.name: float(row.factor.value())
            for row in self._pattern_rows
            if row.check.isChecked()
        }
        if custom_name and custom:
            run[custom_name] = custom

        return _Request(cases=run, combinations=combos, config=self._config_editor.value())

    # ── Buttons ──────────────────────────────────────────────────────

    def _update_ok(self, *_args: Any) -> None:
        """Gate *Run* on there being something to run."""
        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(bool(self.request().cases))

    # ── Widget builders ──────────────────────────────────────────────

    def _factor_box(self, value: float = 1.0, *, enabled: bool = True) -> QDoubleSpinBox:
        """A load-multiplier spin box, defaulting to one (unchanged)."""
        box = QDoubleSpinBox(self)
        box.setRange(-_FACTOR_RANGE, _FACTOR_RANGE)
        box.setDecimals(3)
        box.setSingleStep(0.1)
        box.setValue(value)
        box.setMaximumWidth(90)
        box.setEnabled(enabled)
        box.setToolTip("Load multiplier applied when solving (1.0 = as defined)")
        box.valueChanged.connect(self._update_ok)
        return box

    # ── The three sections ───────────────────────────────────────────

    def _build_case_group(self) -> QGroupBox:
        """The model's static cases: a tick, a factor, and what they apply."""
        group = QGroupBox("Static cases", self)
        grid = QGridLayout(group)
        if not self._cases:
            grid.addWidget(QLabel("The model defines no static load case."), 0, 0)
            return group
        for index, (name, patterns) in enumerate(self._cases.items()):
            check = QCheckBox(name, self)
            check.setToolTip(_patterns_text(patterns))
            check.toggled.connect(self._update_ok)
            factor = self._factor_box()
            self._case_rows.append(
                _CaseRow(name=name, patterns=patterns, check=check, factor=factor)
            )
            grid.addWidget(check, index, 0)
            grid.addWidget(factor, index, 1, Qt.AlignmentFlag.AlignLeft)
            grid.addWidget(QLabel(_patterns_text(patterns)), index, 2)
        grid.setColumnStretch(2, 1)
        return group

    def _build_combo_group(self) -> QGroupBox:
        """The model's combinations, with the cases each one needs."""
        group = QGroupBox("Combinations (run their cases, then reduce)", self)
        grid = QGridLayout(group)
        if not self._combinations:
            grid.addWidget(QLabel("The model defines no load combination."), 0, 0)
            return group
        for index, spec in enumerate(self._combinations):
            missing = [leaf for leaf in spec.leaves if leaf not in self._cases]
            runnable = bool(spec.leaves) and not missing
            if runnable:
                detail = _patterns_text(spec.leaves)
            elif spec.error:
                detail = f"cannot expand \u2014 {spec.error}"
            else:
                detail = "needs a case this slice cannot run: " + ", ".join(missing)
            check = QCheckBox(spec.name, self)
            check.setEnabled(runnable)
            check.setToolTip(detail)
            if runnable:
                check.toggled.connect(self._update_ok)
            self._combo_rows.append(
                _ComboRow(name=spec.name, leaves=dict(spec.leaves), check=check)
            )
            grid.addWidget(check, index, 0)
            label = QLabel(detail)
            label.setEnabled(runnable)
            grid.addWidget(label, index, 1)
        grid.setColumnStretch(1, 1)
        return group

    def _build_custom_group(self) -> QGroupBox:
        """Author a case from load patterns, each with its own factor."""
        group = QGroupBox("Custom case (optional)", self)
        grid = QGridLayout(group)
        self._custom_name = QLineEdit(self)
        self._custom_name.setPlaceholderText("Name, e.g. ULT")
        self._custom_name.textChanged.connect(self._update_ok)
        grid.addWidget(QLabel("Name"), 0, 0)
        grid.addWidget(self._custom_name, 0, 1, 1, 2)
        for index, name in enumerate(self._patterns, start=1):
            check = QCheckBox(name, self)
            check.toggled.connect(self._update_ok)
            factor = self._factor_box()
            self._pattern_rows.append(_PatternRow(name=name, check=check, factor=factor))
            grid.addWidget(check, index, 0)
            grid.addWidget(factor, index, 1, Qt.AlignmentFlag.AlignLeft)
        if not self._patterns:
            grid.addWidget(QLabel("The model defines no load pattern."), 1, 0)
        grid.setColumnStretch(2, 1)
        return group

    def _build_config_group(self) -> QGroupBox:
        """Optional OpenSees builder overrides, each key shown with its own help."""
        group = QGroupBox("Configuration (optional)", self)
        layout = QVBoxLayout(group)
        self._config_editor = ConfigEditor(BUILDER_CONFIG_KEYS, {}, group)
        layout.addWidget(self._config_editor)
        return group

    # ── Entry point ──────────────────────────────────────────────────

    @staticmethod
    def get_request(
        cases: dict[str, dict[str, float]],
        combinations: Any,
        patterns: Any,
        parent: Optional[Any] = None,
    ) -> Optional[_Request]:
        """Run the dialog modally and return the request, or ``None``.

        Args:
            cases: The model's static cases, as listed.
            combinations: The model's :class:`CombinationSpec` list.
            patterns: The model's load-pattern names.
            parent: Optional Qt parent widget.

        Returns:
            The ticked :class:`_Request`, or ``None`` when cancelled.
        """
        dialog = AnalysisDialog(cases, combinations, patterns, parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.request()


def _patterns_text(patterns: dict[str, float]) -> str:
    """``"DEAD × 1.2, LIVE × 1.5"`` for a ``{pattern: factor}`` map."""
    return ", ".join(f"{name} \u00d7 {factor:g}" for name, factor in patterns.items())


def _unique_scaled_name(base: str, taken: set[str]) -> str:
    """A name for a scaled case that no model case or requested case already has.

    A model may already hold a case spelled like the generated name — e.g. a case
    literally named ``DEAD ×2``.  Reusing that spelling would shadow the model
    case for a combination reduction, which resolves its leaves by *model* name,
    so the name gains a `` (2)``, `` (3)`` … suffix until it is free.

    Args:
        base: The preferred name, e.g. ``"DEAD ×2"``.
        taken: Names already claimed — every model case name, plus every name
            requested so far.

    Returns:
        *base*, or *base* followed by the smallest free `` (n)`` suffix.
    """
    name = base
    suffix = 2
    while name in taken:
        name = f"{base} ({suffix})"
        suffix += 1
    return name
