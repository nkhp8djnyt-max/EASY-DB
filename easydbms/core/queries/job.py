"""Running a script (a list of statements) on a client in the background."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from dataclasses import dataclass
from enum import StrEnum

from ..db import DatabaseClient, DbError, QueryCancelled, QueryResult
from ..dialects import Statement


class Outcome(StrEnum):
    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"  # not run because an earlier statement failed or was cancelled


@dataclass(frozen=True, slots=True)
class StatementOutcome:
    statement: Statement
    outcome: Outcome
    result: QueryResult | None = None
    error: DbError | None = None
    #: Wall-clock seconds the statement took (0 for statements that never ran).
    duration: float = 0.0


class ScriptRun:
    """Handle of a running script. ``on_outcome`` is called (on the worker thread) as each
    statement finishes; ``future`` resolves to all outcomes in statement order.
    """

    def __init__(
        self,
        client: DatabaseClient,
        statements: Sequence[Statement],
        row_limit: int,
        on_outcome: Callable[[int, StatementOutcome], None] | None = None,
    ) -> None:
        self._client = client
        self._statements = list(statements)
        self._row_limit = row_limit
        self._on_outcome = on_outcome
        self._stop = threading.Event()
        self.future: Future[list[StatementOutcome]] = Future()

    def start_on(self, submit: Callable[[Callable[[], None]], object]) -> ScriptRun:
        submit(self._work)
        return self

    def cancel(self) -> None:
        """Stop after (and abort) the running statement; the rest are reported as skipped."""
        self._stop.set()
        self._client.cancel()

    # ------------------------------------------------------------------ worker

    def _work(self) -> None:
        outcomes: list[StatementOutcome] = []
        try:
            for index, statement in enumerate(self._statements):
                outcome = self._run_one(statement)
                outcomes.append(outcome)
                self._emit(index, outcome)
                if outcome.outcome is not Outcome.OK:
                    for rest in self._statements[index + 1 :]:
                        skipped = StatementOutcome(rest, Outcome.SKIPPED)
                        outcomes.append(skipped)
                        self._emit(len(outcomes) - 1, skipped)
                    break
            self.future.set_result(outcomes)
        except BaseException as error:
            self.future.set_exception(error)

    def _run_one(self, statement: Statement) -> StatementOutcome:
        if self._stop.is_set():
            return StatementOutcome(statement, Outcome.CANCELLED)
        started = time.perf_counter()
        try:
            result = self._client.execute(statement.body, max_rows=self._row_limit)
        except QueryCancelled as error:
            return StatementOutcome(
                statement, Outcome.CANCELLED, error=error, duration=time.perf_counter() - started
            )
        except DbError as error:
            return StatementOutcome(
                statement, Outcome.ERROR, error=error, duration=time.perf_counter() - started
            )
        return StatementOutcome(statement, Outcome.OK, result=result, duration=result.duration)

    def _emit(self, index: int, outcome: StatementOutcome) -> None:
        if self._on_outcome is not None:
            self._on_outcome(index, outcome)
