"""Run a callable off the GUI thread and report back through Qt signals.

The roadmap's threading rule is that the GUI thread never calls ``ops.*``, and
that parsing, preprocessing and analysis all run on workers
(``docs/gui_roadmap.md`` → *Threading model*).  This is that worker: a small
``QThread`` wrapper around a plain Python callable.

Cancellation is **cooperative**, exactly as the roadmap prescribes: the task is
handed a ``should_cancel()`` callable and polls it wherever it can.  An atomic
task — a single ``preprocess_model`` or ``analyze`` call — cannot be interrupted
mid-call, so cancellation only takes effect at the next task boundary; a forced
thread kill would leave shared OpenSees state inconsistent.
"""

import gc
from threading import Event
from typing import Any, Callable

from qtpy.QtCore import QThread, Signal

__all__ = ["TaskWorker"]


class TaskWorker(QThread):
    """Execute a task on its own thread and report the outcome.

    Args:
        task: Called with one argument — a zero-argument ``should_cancel()``
            callable — and returns whatever the caller expects.  Any exception
            is reported through :attr:`failed` rather than escaping the thread,
            where Qt would print it and the caller would never learn.
        parent: Optional Qt parent.
    """

    #: Emitted with the task's return value once it completes.
    succeeded = Signal(object)
    #: Emitted as ``"<ExceptionType>: <message>"`` when the task raises.
    failed = Signal(str)
    #: Emitted as ``(current, total, label)`` when the task reports progress.
    #: Emitted *from the worker thread*; Qt queues it onto the GUI thread, which
    #: is what makes it safe to drive a progress bar from it.
    progress = Signal(int, int, str)

    def __init__(self, task: Callable[[Callable[[], bool]], Any], parent: Any = None) -> None:
        super().__init__(parent)
        self._task = task
        self._cancel = Event()

    # ── Cancellation ─────────────────────────────────────────────────

    def cancel(self) -> None:
        """Ask the task to stop at its next cancellation check."""
        self._cancel.set()

    def should_cancel(self) -> bool:
        """Whether :meth:`cancel` has been called (handed to the task)."""
        return self._cancel.is_set()

    def report_progress(self, current: int, total: int, label: str = "") -> None:
        """Emit :attr:`progress` — handed to a task that reports steps.

        A task that wants a determinate bar is given this as an ``on_progress``
        callback (see :meth:`MainWindow._start_analysis`); a task that does not
        simply never calls it and the bar stays in busy mode.
        """
        self.progress.emit(int(current), int(total), str(label))

    # ── QThread ──────────────────────────────────────────────────────

    def run(self) -> None:
        """QThread entry point: run the task, emit the outcome.

        The **cyclic collector is held off for the task's duration**.  A
        collection triggered here walks the whole heap — including the
        PySide6/VTK objects the GUI thread owns — and shiboken's objects are not
        built to be traversed from another thread.  On macOS that is an
        intermittent segfault: ``docs/dev_notes.md`` → *The macOS GUI segfault*.

        Reference counting still frees as usual, so only *cycles* wait, and the
        GUI thread reclaims them when the task reports in
        (``MainWindow._preprocess_ended``).  ``gc.freeze()`` covers the objects
        that existed before the task started; this covers the window itself.
        """
        collector_was_on = gc.isenabled()
        gc.disable()
        try:
            result = self._task(self.should_cancel)
        except Exception as exc:
            # Reported rather than allowed to escape the thread, where Qt would
            # print it and the caller would never learn it failed.
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        finally:
            if collector_was_on:
                gc.enable()
        self.succeeded.emit(result)
