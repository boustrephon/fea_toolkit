"""Shared warning classes and report-request helpers (single-concern root module).

The toolkit's one named warning category is :class:`NotImplementedWarning`: a
diagnostic emitted when an input definition (currently a SAP2000/ETABS link
property) is *recognised but not yet imported*.  It is a warning, not an error,
because the model still parses — the definition is kept with linear-only data —
and the message asks the user to report it so support can be added.

Like the other root-level single-concern modules (``_unit_scaling``,
``_flags``, ``_cqc``), this module has no project imports and no heavy
dependencies, so both ``io`` and ``opensees`` can import it freely.
"""

from __future__ import annotations

import warnings

#: The repository's issues page, linked from every "please report this" warning.
REPORT_URL = "https://github.com/boustrephon/fea_toolkit/issues"


class NotImplementedWarning(UserWarning):
    """A definition the importer recognises but does not yet parse/emit.

    Emitted once per model for the *first* uncovered definition (with the full
    report-request message and the offending line), then tersely for each
    subsequent uncovered definition.  Subclasses :class:`UserWarning` so it is
    visible by default and can be filtered or escalated independently (e.g.
    ``-W error::fea_toolkit._warnings.NotImplementedWarning``).
    """


class LinkCoverageReporter:
    """Emit :class:`NotImplementedWarning` for uncovered link definitions.

    The full report-request message (with ``REPORT_URL`` and the offending
    line(s)) is printed once; subsequent definitions print a one-line message,
    and :meth:`summarise` reports the running total.
    """

    def __init__(self) -> None:
        self._full_emitted = False
        self._count = 0

    def report(self, property_name: str, link_type: str, reason: str, lines: str) -> None:
        """Warn about one uncovered link-property definition.

        Args:
            property_name: The link property name (e.g. ``"ISO1"``).
            link_type: The canonical link type (e.g. ``"High Damping Rubber Isolator"``).
            reason: A short reason string (from :func:`link_type_status`).
            lines: The offending ``Link=…`` / ``LINKPROP …`` line(s), for the report.
        """
        if not self._full_emitted:
            warnings.warn(
                f"link property {property_name!r} has type {link_type!r}, which the "
                f"importer does not yet handle ({reason}); it was kept with linear-only "
                f"data.\n"
                f"Please report this so support can be added: {REPORT_URL}\n"
                f"Offending line(s):\n{lines}",
                NotImplementedWarning,
                stacklevel=2,
            )
            self._full_emitted = True
        else:
            warnings.warn(
                f"link property {property_name!r} has type {link_type!r}, which the "
                f"importer does not yet handle ({reason}).",
                NotImplementedWarning,
                stacklevel=2,
            )
        self._count += 1

    def report_field(self, subject: str, field: str, context: str, lines: str) -> None:
        """Warn about a recognised-but-unparsed *field* on a link definition.

        Unlike :meth:`report` (a whole uncovered link *type*), this is for a
        single dropped field — a token, column or flag the importer recognises
        but does not yet interpret.  The message names the field so the report
        tells us exactly which spelling to add.  Shares the same
        once-then-terse + summary behaviour as :meth:`report`.

        Args:
            subject: The link id or link-property name carrying the field.
            field: The dropped field name (e.g. ``"PropMod"``, ``"RATIO"``).
            context: Where it appeared (a table name or ``$ LINK PROPERTIES``).
            lines: The offending raw line(s), for the report.
        """
        if not self._full_emitted:
            warnings.warn(
                f"link {subject!r} carries field {field!r} (in {context}), which the "
                f"importer does not parse yet; it was ignored. Please send this export "
                f"so support can be added: {REPORT_URL}\n"
                f"Offending line(s):\n{lines}",
                NotImplementedWarning,
                stacklevel=2,
            )
            self._full_emitted = True
        else:
            warnings.warn(
                f"link {subject!r} carries field {field!r} (in {context}), which the "
                f"importer does not parse yet; it was ignored.",
                NotImplementedWarning,
                stacklevel=2,
            )
        self._count += 1

    def summarise(self) -> None:
        """Print a single summary line when more than one definition was skipped."""
        if self._count > 1:
            warnings.warn(
                f"{self._count} link property definitions could not be imported in full "
                f"— see the first warning above ({REPORT_URL}).",
                NotImplementedWarning,
                stacklevel=2,
            )
