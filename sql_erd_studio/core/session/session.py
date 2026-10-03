"""One saved connection plus its live client and lifecycle state."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from ..connections import FileConnection, ServerConnection, resolve_config
from ..db import DatabaseClient, DbError, create_client
from ..dialects import ServerInfo

ClientFactory = Callable[[ServerConnection | FileConnection, str | None], DatabaseClient]


class SessionState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    READY = "ready"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class SessionStateChanged:
    """Emitted on every transition; safe to forward to the UI thread."""

    connection_id: str
    state: SessionState
    #: Set when ``state`` is ``ERROR``.
    error: Exception | None = None
    #: Set when ``state`` is ``READY``.
    server_info: ServerInfo | None = None


class Session:
    """State machine ``DISCONNECTED -> CONNECTING -> READY | ERROR`` around one client.

    ``connect`` blocks, so call it from a worker thread; observers are told about each transition
    through ``on_event`` (which therefore runs on that worker thread).
    """

    def __init__(
        self,
        config: ServerConnection | FileConnection,
        on_event: Callable[[SessionStateChanged], None] | None = None,
        client_factory: ClientFactory = create_client,
    ) -> None:
        self._config = config
        self._on_event = on_event
        self._client_factory = client_factory
        self._lock = threading.RLock()
        self._state = SessionState.DISCONNECTED
        self._error: Exception | None = None
        self._client: DatabaseClient | None = None
        #: Bumped by ``disconnect`` so a connect that finishes afterwards knows it was abandoned.
        self._epoch = 0

    @property
    def id(self) -> str:
        return self._config.id

    @property
    def config(self) -> ServerConnection | FileConnection:
        return self._config

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def error(self) -> Exception | None:
        return self._error

    @property
    def client(self) -> DatabaseClient | None:
        """The live client while ``READY``, otherwise ``None``."""
        return self._client if self._state is SessionState.READY else None

    @property
    def server_info(self) -> ServerInfo | None:
        client = self.client
        return client.server_info if client else None

    def connect(
        self, password: str | None = None, environ: Mapping[str, str] | None = None
    ) -> None:
        """Resolve ``${ENV}`` placeholders, open the client and move to ``READY`` or ``ERROR``.

        Failures are recorded (``error``, ``ERROR`` state, event) rather than raised.
        """
        with self._lock:
            if self._state in (SessionState.READY, SessionState.CONNECTING):
                return
            epoch = self._epoch
            self._set(SessionState.CONNECTING)
        client: DatabaseClient | None = None
        try:
            resolved, resolved_password = resolve_config(
                self._config, password, os.environ if environ is None else environ
            )
            client = self._client_factory(resolved, resolved_password)
            client.connect()
        except (DbError, ValueError) as error:  # ValueError: undefined ${VAR}, invalid expansion
            if client is not None:
                client.disconnect()
            self._finish(epoch, SessionState.ERROR, error=error)
        except BaseException as error:
            if client is not None:
                client.disconnect()
            self._finish(epoch, SessionState.ERROR, error=RuntimeError(f"unexpected: {error!r}"))
            raise
        else:
            self._finish(epoch, SessionState.READY, client=client)

    def fail(self, error: Exception) -> None:
        """Record a failure that happened before connecting (e.g. the password is unreadable)."""
        with self._lock:
            if self._state in (SessionState.READY, SessionState.CONNECTING):
                return
            epoch = self._epoch
        self._finish(epoch, SessionState.ERROR, error=error)

    def disconnect(self) -> None:
        with self._lock:
            self._epoch += 1
            client, self._client = self._client, None
            was_idle = self._state is SessionState.DISCONNECTED
            self._error = None
            self._state = SessionState.DISCONNECTED
        if client is not None:
            client.disconnect()
        if not was_idle:
            self._emit(SessionState.DISCONNECTED)

    # ------------------------------------------------------------------ internals

    def _set(self, state: SessionState) -> None:
        self._state = state
        self._error = None
        self._emit(state)

    def _finish(
        self,
        epoch: int,
        state: SessionState,
        *,
        client: DatabaseClient | None = None,
        error: Exception | None = None,
    ) -> None:
        with self._lock:
            if epoch != self._epoch:  # disconnected while connecting: throw the result away
                abandoned = True
            else:
                abandoned = False
                self._client = client
                self._error = error
                self._state = state
        if abandoned:
            if client is not None:
                client.disconnect()
            return
        self._emit(state, error=error, server_info=client.server_info if client else None)

    def _emit(
        self,
        state: SessionState,
        *,
        error: Exception | None = None,
        server_info: ServerInfo | None = None,
    ) -> None:
        if self._on_event is not None:
            self._on_event(SessionStateChanged(self.id, state, error, server_info))
