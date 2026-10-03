"""One saved connection plus its live client and lifecycle state."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeVar

from ..connections import MEMORY_DATABASE, FileConnection, ServerConnection
from ..db import DatabaseClient, DbError, NotConnectedError, create_client
from ..dialects import ServerInfo, Statement
from ..queries import ScriptRun, StatementOutcome
from ..schema import DatabaseSchema, introspect
from ..ssh import KnownHosts, SshTunnel
from .connector import ClientFactory, Prepared, prepare

T = TypeVar("T")


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


class SchemaState(StrEnum):
    NONE = "none"  # not requested yet, or the session was disconnected
    LOADING = "loading"
    READY = "ready"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class SchemaChanged:
    """Emitted when schema introspection starts, finishes or fails (worker thread)."""

    connection_id: str
    state: SchemaState
    #: Set when ``state`` is ``READY``.
    schema: DatabaseSchema | None = None
    #: Set when ``state`` is ``ERROR``.
    error: Exception | None = None


SessionEvent = SessionStateChanged | SchemaChanged


class Session:
    """State machine ``DISCONNECTED -> CONNECTING -> READY | ERROR`` around one client.

    ``connect`` blocks, so call it from a worker thread; observers are told about each transition
    through ``on_event`` (which therefore runs on that worker thread).

    Besides the *query lane* (the user's statements) a session has a *meta lane*: its own thread
    and its own connection, used for schema introspection and table browsing so that neither waits
    for, nor blocks, a long-running query.
    """

    def __init__(
        self,
        config: ServerConnection | FileConnection,
        on_event: Callable[[SessionEvent], None] | None = None,
        client_factory: ClientFactory = create_client,
        known_hosts: KnownHosts | None = None,
    ) -> None:
        self._config = config
        self._on_event = on_event
        self._client_factory = client_factory
        self._known_hosts = known_hosts
        self._lock = threading.RLock()
        self._state = SessionState.DISCONNECTED
        self._error: Exception | None = None
        self._client: DatabaseClient | None = None
        #: Bumped by ``disconnect`` so a connect that finishes afterwards knows it was abandoned.
        self._epoch = 0
        self._lane: ThreadPoolExecutor | None = None
        self._prepared: Prepared | None = None
        self._meta_lane: ThreadPoolExecutor | None = None
        self._meta_client: DatabaseClient | None = None
        self._schema: DatabaseSchema | None = None
        self._schema_state = SchemaState.NONE
        self._schema_error: Exception | None = None
        self._schema_version = 0

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

    @property
    def schema(self) -> DatabaseSchema | None:
        """The last successfully introspected schema (kept while a refresh is running)."""
        return self._schema

    @property
    def schema_state(self) -> SchemaState:
        return self._schema_state

    @property
    def schema_error(self) -> Exception | None:
        return self._schema_error

    @property
    def schema_version(self) -> int:
        """Bumped by every successful introspection, so caches can tell whether they are stale."""
        return self._schema_version

    @property
    def tunnel(self) -> SshTunnel | None:
        """The SSH tunnel this session runs through (while connected), if it has one."""
        prepared = self._prepared
        return prepared.tunnel if prepared is not None else None

    def connect(
        self,
        password: str | None = None,
        environ: Mapping[str, str] | None = None,
        secrets: Mapping[str, str] | None = None,
    ) -> None:
        """Prepare the connection, open the client and move to ``READY`` or ``ERROR``.

        Preparing resolves ``${ENV}`` placeholders, applies ``pg_service.conf`` / ``~/.pgpass`` and
        opens the SSH tunnel (``secrets``: its passwords and key passphrases by field name).
        Failures are recorded (``error``, ``ERROR`` state, event) rather than raised.
        """
        with self._lock:
            if self._state in (SessionState.READY, SessionState.CONNECTING):
                return
            epoch = self._epoch
            self._set(SessionState.CONNECTING)
        client: DatabaseClient | None = None
        prepared: Prepared | None = None
        try:
            prepared = prepare(
                self._config,
                password,
                secrets or {},
                os.environ if environ is None else environ,
                self._known_hosts,
            )
            client = self._client_factory(prepared.config, prepared.password, prepared.runtime)
            client.connect()
        except (DbError, ValueError) as error:  # ValueError: undefined ${VAR}, unknown service
            self._discard(client, prepared)
            self._finish(epoch, SessionState.ERROR, error=error)
        except BaseException as error:
            self._discard(client, prepared)
            self._finish(epoch, SessionState.ERROR, error=RuntimeError(f"unexpected: {error!r}"))
            raise
        else:
            self._finish(epoch, SessionState.READY, client=client, prepared=prepared)

    def fail(self, error: Exception) -> None:
        """Record a failure that happened before connecting (e.g. the password is unreadable)."""
        with self._lock:
            if self._state in (SessionState.READY, SessionState.CONNECTING):
                return
            epoch = self._epoch
        self._finish(epoch, SessionState.ERROR, error=error)

    def run_script(
        self,
        statements: list[Statement],
        row_limit: int,
        on_outcome: Callable[[int, StatementOutcome], None] | None = None,
    ) -> ScriptRun:
        """Start ``statements`` on this connection's query lane (one at a time, in order)."""
        with self._lock:
            client = self.client
            if client is None:
                raise NotConnectedError("not connected")
            if self._lane is None:
                self._lane = ThreadPoolExecutor(max_workers=1, thread_name_prefix="query")
            lane = self._lane
        return ScriptRun(client, statements, row_limit, on_outcome).start_on(lane.submit)

    # ------------------------------------------------------------------ meta lane

    def load_schema(self) -> Future[None]:
        """Introspect in the background (events: ``LOADING``, then ``READY`` or ``ERROR``)."""
        with self._lock:
            if self.client is None:
                raise NotConnectedError("not connected")
            epoch = self._epoch
            self._schema_state = SchemaState.LOADING
            self._schema_error = None
        self._emit_schema(SchemaState.LOADING)
        return self.run_on_meta(lambda client: self._introspect(client, epoch))

    def run_on_meta(self, job: Callable[[DatabaseClient], T]) -> Future[T]:
        """Run ``job(client)`` on the meta lane (one at a time, on the second connection)."""
        with self._lock:
            if self.client is None or self._prepared is None:
                raise NotConnectedError("not connected")
            if self._meta_lane is None:
                self._meta_lane = ThreadPoolExecutor(max_workers=1, thread_name_prefix="meta")
            lane = self._meta_lane
            epoch = self._epoch
        return lane.submit(lambda: job(self._meta(epoch)))

    def cancel_meta(self) -> bool:
        """Abort the statement the meta lane is running, if any."""
        client = self._meta_client
        return client.cancel() if client is not None else False

    def _meta(self, epoch: int) -> DatabaseClient:
        """The meta lane's connection, opened on first use (runs on the meta worker thread)."""
        with self._lock:
            prepared = self._prepared
            if epoch != self._epoch or prepared is None:
                raise NotConnectedError("the session was disconnected")
            if self._meta_client is not None:
                return self._meta_client
            main = self._client
        config = prepared.config
        if isinstance(config, FileConnection) and config.path == MEMORY_DATABASE:
            assert main is not None  # a second connection would be a different, empty database
            return main
        # the second connection goes through the same tunnel, so it needs no second login
        client = self._client_factory(config, prepared.current_password(), prepared.runtime)
        client.connect()
        with self._lock:
            if epoch != self._epoch:
                client.disconnect()
                raise NotConnectedError("the session was disconnected")
            self._meta_client = client
        return client

    def _introspect(self, client: DatabaseClient, epoch: int) -> None:
        try:
            schema = introspect(client)
        except Exception as error:
            with self._lock:
                if epoch != self._epoch:
                    return
                self._schema_state = SchemaState.ERROR
                self._schema_error = error
            self._emit_schema(SchemaState.ERROR, error=error)
            return
        with self._lock:
            if epoch != self._epoch:
                return
            self._schema = schema
            self._schema_state = SchemaState.READY
            self._schema_error = None
            self._schema_version += 1
        self._emit_schema(SchemaState.READY, schema=schema)

    def disconnect(self) -> None:
        with self._lock:
            self._epoch += 1
            lane, self._lane = self._lane, None
            if lane is not None:
                lane.shutdown(wait=False, cancel_futures=True)
            meta_lane, self._meta_lane = self._meta_lane, None
            meta_client, self._meta_client = self._meta_client, None
            self._schema = None
            self._schema_state = SchemaState.NONE
            self._schema_error = None
            prepared, self._prepared = self._prepared, None
            client, self._client = self._client, None
            was_idle = self._state is SessionState.DISCONNECTED
            self._error = None
            self._state = SessionState.DISCONNECTED
        if meta_lane is not None:
            meta_lane.shutdown(wait=False, cancel_futures=True)
        if meta_client is not None:
            meta_client.cancel()
            meta_client.disconnect()
        if client is not None:
            client.disconnect()
        if prepared is not None:
            prepared.close()  # the tunnel goes last: the connections above run through it
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
        prepared: Prepared | None = None,
        error: Exception | None = None,
    ) -> None:
        with self._lock:
            if epoch != self._epoch:  # disconnected while connecting: throw the result away
                abandoned = True
            else:
                abandoned = False
                self._client = client
                self._prepared = prepared
                self._error = error
                self._state = state
        if abandoned:
            self._discard(client, prepared)
            return
        self._emit(state, error=error, server_info=client.server_info if client else None)

    @staticmethod
    def _discard(client: DatabaseClient | None, prepared: Prepared | None) -> None:
        if client is not None:
            client.disconnect()
        if prepared is not None:
            prepared.close()

    def _emit_schema(
        self,
        state: SchemaState,
        *,
        schema: DatabaseSchema | None = None,
        error: Exception | None = None,
    ) -> None:
        if self._on_event is not None:
            self._on_event(SchemaChanged(self.id, state, schema, error))

    def _emit(
        self,
        state: SessionState,
        *,
        error: Exception | None = None,
        server_info: ServerInfo | None = None,
    ) -> None:
        if self._on_event is not None:
            self._on_event(SessionStateChanged(self.id, state, error, server_info))
