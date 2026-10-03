from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from pytestqt.qtbot import QtBot

from easydbms.core.session import ActiveChanged
from easydbms.ui.runtime import BackgroundRunner, EventBridge


def test_results_are_delivered_on_the_gui_thread(qtbot: QtBot) -> None:
    runner = BackgroundRunner()
    seen: list[tuple[Any, Exception | None, bool]] = []
    worker_threads: list[int] = []

    def work() -> int:
        worker_threads.append(threading.get_ident())
        return 21 * 2

    def done(result: Any, error: Exception | None) -> None:
        seen.append((result, error, threading.get_ident() == main_thread))

    main_thread = threading.get_ident()
    runner.submit(work, done)
    qtbot.waitUntil(lambda: bool(seen), timeout=5000)
    assert seen[0][:2] == (42, None)
    assert seen[0][2] is True  # callback ran on the thread that owns the event loop
    assert worker_threads[0] != main_thread  # the work did not
    runner.shutdown()


def test_errors_are_passed_to_the_callback_not_raised(qtbot: QtBot) -> None:
    runner = BackgroundRunner()
    seen: list[tuple[Any, Exception | None]] = []

    def boom() -> None:
        raise ValueError("nope")

    runner.submit(boom, lambda result, error: seen.append((result, error)))
    qtbot.waitUntil(lambda: bool(seen), timeout=5000)
    assert seen[0][0] is None
    assert isinstance(seen[0][1], ValueError)
    assert str(seen[0][1]) == "nope"
    runner.shutdown()


def test_a_cancelled_job_never_calls_back(qtbot: QtBot) -> None:
    runner = BackgroundRunner()
    gate = threading.Event()
    called: list[str] = []
    job = runner.submit(lambda: gate.wait(5), lambda *_: called.append("cancelled"))
    runner.cancel(job)
    gate.set()
    runner.submit(lambda: 1, lambda *_: called.append("control"))
    qtbot.waitUntil(lambda: "control" in called, timeout=5000)
    assert called == ["control"]
    runner.shutdown()


def test_many_jobs_all_complete(qtbot: QtBot) -> None:
    runner = BackgroundRunner()
    results: list[int] = []

    def collect(result: Any, error: Exception | None) -> None:
        results.append(result)

    def square(n: int) -> Callable[[], int]:
        return lambda: n * n

    for n in range(30):
        runner.submit(square(n), collect)
    qtbot.waitUntil(lambda: len(results) == 30, timeout=5000)
    assert sorted(results) == [n * n for n in range(30)]
    runner.shutdown()


def test_events_posted_from_a_worker_thread_arrive_on_the_gui_thread(qtbot: QtBot) -> None:
    bridge = EventBridge()
    received: list[tuple[object, int]] = []
    bridge.posted.connect(lambda event: received.append((event, threading.get_ident())))
    event = ActiveChanged("abc")
    thread = threading.Thread(target=lambda: bridge.post(event))
    thread.start()
    thread.join()
    qtbot.waitUntil(lambda: bool(received), timeout=5000)
    assert received[0][0] is event
    assert received[0][1] == threading.get_ident()
