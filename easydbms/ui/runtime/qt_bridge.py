"""Hand results from worker threads to the GUI thread.

Both helpers are QObjects that live in the GUI thread. A signal emitted from a worker thread
reaches their slots through Qt's queued connection, so UI code only ever runs on the GUI thread.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal, Slot


class EventBridge(QObject):
    """Forwards core events (``ManagerEvent``) to the GUI thread.

    Pass :meth:`post` as the manager's ``listener`` and connect to :attr:`posted`.
    """

    posted = Signal(object)

    def post(self, event: object) -> None:
        self.posted.emit(event)


class BackgroundRunner(QObject):
    """Runs blocking callables off the GUI thread and calls back on it.

    ``submit(work, done)`` runs ``work()`` in a pool thread, then calls ``done(result, error)`` in
    the GUI thread: ``error`` is the exception that ``work`` raised, or ``None``.
    """

    _finished = Signal(int, object, object)

    def __init__(self, parent: QObject | None = None, max_workers: int = 4) -> None:
        super().__init__(parent)
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ui-task")
        self._callbacks: dict[int, Callable[[Any, Exception | None], None]] = {}
        self._ids = itertools.count(1)
        self._finished.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    def submit(self, work: Callable[[], Any], done: Callable[[Any, Exception | None], None]) -> int:
        job_id = next(self._ids)
        self._callbacks[job_id] = done

        def run() -> None:
            try:
                result, error = work(), None
            except Exception as exc:
                result, error = None, exc
            self._finished.emit(job_id, result, error)

        self._pool.submit(run)
        return job_id

    def cancel(self, job_id: int) -> None:
        """Drop the callback of a job whose result nobody wants any more."""
        self._callbacks.pop(job_id, None)

    def shutdown(self) -> None:
        self._callbacks.clear()
        self._pool.shutdown(wait=False, cancel_futures=True)

    @Slot(int, object, object)
    def _deliver(self, job_id: int, result: object, error: object) -> None:
        callback = self._callbacks.pop(job_id, None)
        if callback is not None:
            callback(result, error if isinstance(error, Exception) else None)
