"""The workflow layer's vocabulary — steps, their results and specifications.

A *step* is one operation applied to a selection: mesh the areas, soften a
section, solve the static cases.  A *recipe* (:mod:`fea_toolkit.workflow.recipe`)
is an ordered list of them.  This module holds only that vocabulary — the
dataclasses and the error type — so it imports without OpenSees and without Qt.

A verb's two halves live apart, deliberately:

* its **implementation** — the function that does the work — sits in
  :mod:`fea_toolkit.workflow.verbs`, one module per verb;
* its **specification** — the name, the parameters it accepts and which input
  it needs — sits in :mod:`fea_toolkit.workflow.registry`, as pure data.

Keeping the specification pure data is what lets a caller list the available
verbs and render their parameter forms without importing a single
implementation — and therefore without loading OpenSees.
"""

import copy
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..model.sap_data import SAPModelData
from ..model.selection import Selection

__all__ = [
    "CASES",
    "GEOMETRY",
    "MODEL",
    "ParamSpec",
    "Step",
    "StepContext",
    "StepError",
    "StepResult",
    "StepSpec",
    "validate_params",
]

#: A step's output is prepared topology (a ``MeshModel``).
GEOMETRY = "geometry"
#: A step's output is a prepared ``SAPModelData`` — properties edited,
#: topology untouched.
MODEL = "model"
#: A step's output is solved case results (``{case: result}``).
CASES = "cases"


def _never_cancel() -> bool:
    """Default cancellation predicate — a run is not cancelled."""
    return False


def _discard(message: str) -> None:
    """Default log sink — a run without a message log."""
    return None


@dataclass(frozen=True)
class ParamSpec:
    """One parameter a verb accepts — its default, type and help text.

    The GUI renders a parameter form straight from these, so this is the only
    description of a verb's parameters: an implementation must not read a
    parameter its verb has not declared here.

    Attributes:
        default: The value used when a step does not supply one.
        type: The required Python type — ``bool``, ``int``, ``float``, ``str``,
            ``dict`` or ``list``.  A ``dict`` or ``list`` default is copied per
            step, so editing one step's parameters cannot reach another's.
        help: One-line description, shown beside the field.
        choices: Accepted values when the parameter is an enumeration;
            ``None`` means any value of :attr:`type`.
    """

    default: Any
    type: type
    help: str = ""
    choices: Optional[tuple] = None

    def coerce(self, value: Any, *, verb: str, name: str) -> Any:
        """Return *value* as :attr:`type`, rejecting a wrong value.

        Args:
            value: The value supplied by a step or read from a recipe file.
            verb: The verb name, for the error message.
            name: The parameter name, for the error message.

        Returns:
            The value, converted where a faithful conversion exists.

        Raises:
            ValueError: If *value* has the wrong type, or is not one of
                :attr:`choices`.
        """
        expected = self.type
        if expected is bool:
            if not isinstance(value, bool):
                raise ValueError(f"step {verb!r}: {name} must be a bool, got {value!r}")
        elif expected is float:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"step {verb!r}: {name} must be a number, got {value!r}")
            value = float(value)
        elif expected is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"step {verb!r}: {name} must be an int, got {value!r}")
        elif expected is str:
            if not isinstance(value, str):
                raise ValueError(f"step {verb!r}: {name} must be a string, got {value!r}")
        elif expected is dict:
            if not isinstance(value, dict):
                raise ValueError(f"step {verb!r}: {name} must be a mapping, got {value!r}")
            value = copy.deepcopy(value)
        elif expected is list:
            if not isinstance(value, (list, tuple)):
                raise ValueError(f"step {verb!r}: {name} must be a list, got {value!r}")
            value = list(value)
        if self.choices is not None and value not in self.choices:
            raise ValueError(f"step {verb!r}: {name} must be one of {self.choices}, got {value!r}")
        return value


@dataclass(frozen=True)
class Step:
    """One operation applied to a selection.

    Attributes:
        verb: Which operation — a key of
            :data:`fea_toolkit.workflow.registry.STEP_SPECS`.
        selection: The elements the step acts on; ``None`` means the whole
            model.  It is resolved against the model the step operates on: a
            preparation step reads the working ``SAPModelData``, an analysis
            step reads the topology the previous step produced.
        params: Verb-specific parameters.  Every key must be declared by the
            verb's :class:`StepSpec`; an undeclared key is rejected rather than
            ignored, so a typo cannot silently change a model.
        optional: When ``True``, a failure is logged and the recipe continues at
            the next step.  The default (``False``) stops the run — a
            silently-skipped step is a silently-different model.
    """

    verb: str
    selection: Optional[Selection] = None
    params: dict[str, Any] = field(default_factory=dict)
    optional: bool = False


@dataclass(frozen=True)
class StepResult:
    """What one step produced, for display and for a later step to consume.

    Attributes:
        kind: :data:`GEOMETRY`, :data:`MODEL` or :data:`CASES`.
        label: Display name for the output, e.g. ``"Meshed"``.
        payload: The output itself — a ``MeshModel``, a ``SAPModelData``, or
            ``{case_name: result_dict}``.
    """

    kind: str
    label: str
    payload: Any


def validate_params(
    verb: str, declared: dict[str, ParamSpec], params: dict[str, Any]
) -> dict[str, Any]:
    """Fill *params* with defaults, rejecting an undeclared key.

    Kept as a module function rather than a method so a verb's implementation
    can validate its own parameters without importing the registry — which
    imports the implementation, and so would be circular.

    Args:
        verb: The verb name, for error messages.
        declared: The verb's ``{name: ParamSpec}`` set.
        params: The parameters supplied.  A subset is normal — the rest take
            their defaults.

    Returns:
        The complete parameter set, each value type-checked.

    Raises:
        ValueError: If a key is not declared, or a value has the wrong type.
    """
    unknown = sorted(set(params) - set(declared))
    if unknown:
        raise ValueError(
            f"step {verb!r}: unknown parameter(s) {unknown}; expected {sorted(declared)}"
        )
    filled = {name: copy.deepcopy(spec.default) for name, spec in declared.items()}
    for name, value in params.items():
        filled[name] = declared[name].coerce(value, verb=verb, name=name)
    return filled


@dataclass
class StepContext:
    """The working state a recipe is run against.

    Attributes:
        model_data: The parsed model the recipe prepares.  A step that edits
            properties replaces this with a copy, so the caller's own model is
            never mutated.
        model: The prepared topology, once a meshing step has produced one.
        case_results: ``{case name: payload}`` solved so far — the raw results a
            :mod:`fea_toolkit.workflow.verbs.combine` step reduces.  Keys are
            merged, never replaced, so several analysis steps can contribute.
        cancel: Zero-argument predicate — ``True`` asks the run to stop at the
            next step boundary.  Wired to the GUI worker's flag.
        log: One-line sink for progress, wired to the GUI message log.
        cancelled: Set by a step that observed its **own** cooperative
            cancellation (e.g. a solve stopped between cases).  ``run_recipe``
            reads it after each step, so a cancellation inside the *last* step
            still marks the run cancelled.
    """

    model_data: SAPModelData
    model: Optional[Any] = None
    case_results: dict = field(default_factory=dict)
    cancel: Callable[[], bool] = _never_cancel
    log: Callable[[str], None] = _discard
    cancelled: bool = False


@dataclass(frozen=True)
class StepSpec:
    """The declarative description of a verb — everything except the work.

    Attributes:
        verb: The verb's name; also its key in ``STEP_SPECS``.
        run: The implementation, called as ``run(context, step)``.
        params: ``{parameter name: ParamSpec}`` — the complete parameter set.
        kind: The :class:`StepResult` kind this verb produces.
        help: One-line description, for menus and the verb listing.
        needs: Input prerequisites, e.g. ``("model",)`` for a verb that needs a
            prepared topology.  Lets a caller grey out a step that cannot run
            yet instead of failing on it.
    """

    verb: str
    run: Callable[[StepContext, Step], list[StepResult]]
    params: dict[str, ParamSpec]
    kind: str
    help: str = ""
    needs: tuple = ()

    def defaults(self) -> dict[str, Any]:
        """Every parameter's default — the form of a freshly added step.

        Each default is **deep-copied**, so a mutable default (a ``dict`` or a
        ``list``) is independent for every step — the same guarantee
        :func:`validate_params` gives a run.
        """
        return {name: copy.deepcopy(spec.default) for name, spec in self.params.items()}

    def validate(self, params: dict[str, Any]) -> dict[str, Any]:
        """Fill *params* with defaults, rejecting an undeclared key.

        Args:
            params: The parameters a step supplies.  A subset of the declared
                set is normal — the rest take their defaults.

        Returns:
            The complete parameter set, each value type-checked.

        Raises:
            ValueError: If a key is not declared by this verb, or a value has
                the wrong type.
        """
        return validate_params(self.verb, self.params, params)


class StepError(RuntimeError):
    """A non-optional step failed, so the recipe stopped at it.

    Attributes:
        index: Zero-based position of the failing step in the recipe.
        verb: The failing step's verb.
        cause: The exception the implementation raised.
    """

    def __init__(self, index: int, verb: str, cause: BaseException) -> None:
        super().__init__(f"step {index} ({verb}) failed: {type(cause).__name__}: {cause}")
        self.index = index
        self.verb = verb
        self.cause = cause
