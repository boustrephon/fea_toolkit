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
