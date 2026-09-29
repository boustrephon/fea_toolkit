"""Tests for the GUI's worker thread.

Gated by ``needs_gui`` (it is Qt).  No GL context is needed: the worker is a
plain ``QThread``, which is why these run under the offscreen platform.
"""

import pytest

pytestmark = pytest.mark.needs_gui


@pytest.fixture(scope="module")
def qapp():
    """Provide the single process-wide ``QApplication`` Qt requires."""
    from qtpy.QtWidgets import QApplication

    yield QApplication.instance() or QApplication(["pytest-fea-gui"])


@pytest.fixture
def gc_enabled():
    """Run with the cyclic collector on, restoring its prior state afterwards.

    The worker's contract is that it *restores* the collector state, so these
    tests are only meaningful when it started on — otherwise the final assertion
    depends on whatever a previous test left behind.
    """
    import gc

    was_on = gc.isenabled()
    gc.enable()
    try:
        yield
    finally:
        if not was_on:
            gc.disable()


def _run(worker, qapp):
    """Start *worker*, wait for it and deliver its queued signals."""
    from qtpy.QtCore import QCoreApplication

    worker.start()
    assert worker.wait(5000), "the worker did not finish"
    QCoreApplication.processEvents()
    return worker


def test_the_worker_reports_the_result(qapp):
    """A successful task hands its return value back on the GUI thread."""
    from fea_toolkit.gui.controllers.worker import TaskWorker

    worker = TaskWorker(lambda _should_cancel: 6 * 7)
    results = []
    worker.succeeded.connect(results.append)

    _run(worker, qapp)

    assert results == [42]


def test_the_worker_reports_failures_instead_of_raising(qapp):
    """A task that raises becomes a message, not a traceback from a thread."""
    from fea_toolkit.gui.controllers.worker import TaskWorker

    def _boom(_should_cancel):
        raise ValueError("nope")

    worker = TaskWorker(_boom)
    failures = []
    worker.failed.connect(failures.append)

    _run(worker, qapp)

    assert failures == ["ValueError: nope"]


def test_cancellation_is_cooperative(qapp):
    """The task is handed a ``should_cancel`` it can poll; cancellation is a flag.

    An atomic task cannot be interrupted mid-call -- a forced stop would leave
    shared state inconsistent -- so the worker only *asks*.
    """
    from fea_toolkit.gui.controllers.worker import TaskWorker

    observed = []

    def _task(should_cancel):
        observed.append(should_cancel())
        return "done"

    worker = TaskWorker(_task)
    worker.cancel()

    _run(worker, qapp)

    assert observed == [True]
    assert worker.should_cancel() is True


def test_the_worker_is_not_cancelled_by_default(qapp):
    """Fresh workers run to completion unless the caller asks otherwise."""
    from fea_toolkit.gui.controllers.worker import TaskWorker

    worker = TaskWorker(lambda _should_cancel: True)

    assert worker.should_cancel() is False


def test_the_collector_is_held_off_while_the_task_runs(qapp, gc_enabled):
    """A collection on the worker thread can walk the GUI thread's Qt objects.

    shiboken's objects are not built to be traversed from another thread, so the
    task runs with the cyclic collector disabled — ``docs/dev_notes.md``, *The
    macOS GUI segfault*.  Reference counting is unaffected, so only cycles wait.
    """
    import gc

    from fea_toolkit.gui.controllers.worker import TaskWorker

    observed = []

    def _task(_should_cancel):
        observed.append(gc.isenabled())
        return "done"

    _run(TaskWorker(_task), qapp)

    assert observed == [False]
    assert gc.isenabled() is True, "the collector must be restored when the task ends"


def test_a_failing_task_still_restores_the_collector(qapp, gc_enabled):
    """The error path restores it too — otherwise the process keeps it off."""
    import gc

    from fea_toolkit.gui.controllers.worker import TaskWorker

    def _boom(_should_cancel):
        raise RuntimeError("nope")

    _run(TaskWorker(_boom), qapp)

    assert gc.isenabled() is True
