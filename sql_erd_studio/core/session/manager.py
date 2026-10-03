"""The set of open sessions and which one is active."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

from ..connections import (
    ConnectionStore,
    FileConnection,
    SecretStore,
    SecretStoreError,
    ServerConnection,
)
from ..db import create_client
from .session import ClientFactory, Session, SessionState, SessionStateChanged


@dataclass(frozen=True, slots=True)
class ActiveChanged:
    """The active connection changed (``None`` when the last one was closed or deleted)."""

    connection_id: str | None


ManagerEvent = SessionStateChanged | ActiveChanged


class ConnectionManager:
    """Owns one :class:`Session` per used connection and connects them lazily.

    ``activate`` makes a connection the active one and connects it in the background if needed;
    several sessions can stay open at once. Events are delivered on worker threads: the UI wraps
    ``listener`` in something that hops to the GUI thread.
    """

    def __init__(
        self,
        store: ConnectionStore,
        secrets: SecretStore,
        *,
        listener: Callable[[ManagerEvent], None] | None = None,
        environ: Mapping[str, str] | None = None,
        client_factory: ClientFactory = create_client,
        max_workers: int = 4,
    ) -> None:
        self._store = store
        self._secrets = secrets
        self._listener = listener
        self._environ = environ
        self._client_factory = client_factory
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="connect")
        self._lock = threading.RLock()
        self._sessions: dict[str, Session] = {}
        self._futures: dict[str, Future[Session]] = {}
        self._typed_passwords: dict[str, str] = {}
        self._active_id: str | None = None

    # ------------------------------------------------------------------ queries

    @property
    def active_id(self) -> str | None:
        return self._active_id

    @property
    def active(self) -> Session | None:
        with self._lock:
            return self._sessions.get(self._active_id) if self._active_id else None

    def session(self, connection_id: str) -> Session:
        """The session for a saved connection (created on first use, not yet connected)."""
        with self._lock:
            existing = self._sessions.get(connection_id)
            if existing is not None:
                return existing
            session = Session(self._store.get(connection_id), self._forward, self._client_factory)
            self._sessions[connection_id] = session
            return session

    def state_of(self, connection_id: str) -> SessionState:
        with self._lock:
            session = self._sessions.get(connection_id)
        return session.state if session else SessionState.DISCONNECTED

    def needs_password(self, connection_id: str) -> bool:
        """``True`` when the connection does not store its password and none was typed yet."""
        config = self._store.get(connection_id)
        return (
            isinstance(config, ServerConnection)
            and not config.save_password
            and connection_id not in self._typed_passwords
        )

    # ------------------------------------------------------------------ actions

    def remember_password(self, connection_id: str, password: str) -> None:
        """Keep a typed password in memory for this run only (never written anywhere)."""
        self._typed_passwords[connection_id] = password

    def activate(self, connection_id: str, password: str | None = None) -> Future[Session]:
        """Make the connection active and connect it if it is not already (non-blocking)."""
        session = self.session(connection_id)
        if password is not None:
            self.remember_password(connection_id, password)
        with self._lock:
            changed = self._active_id != connection_id
            self._active_id = connection_id
        if changed:
            self._emit(ActiveChanged(connection_id))
        if session.state is SessionState.READY:
            return _done(session)
        return self.connect(connection_id)

    def connect(self, connection_id: str) -> Future[Session]:
        """Connect in the background; the future resolves to the session in READY or ERROR."""
        session = self.session(connection_id)
        with self._lock:
            pending = self._futures.get(connection_id)
            if pending is not None and not pending.done():
                return pending
            future = self._executor.submit(self._connect, session)
            self._futures[connection_id] = future
            return future

    def disconnect(self, connection_id: str) -> None:
        with self._lock:
            session = self._sessions.get(connection_id)
        if session is not None:
            session.disconnect()

    def forget(self, connection_id: str) -> None:
        """Close and drop a session, e.g. after its connection was edited or deleted."""
        with self._lock:
            session = self._sessions.pop(connection_id, None)
            self._futures.pop(connection_id, None)
            self._typed_passwords.pop(connection_id, None)
            was_active = self._active_id == connection_id
            if was_active:
                self._active_id = None
        if session is not None:
            session.disconnect()
        if was_active:
            self._emit(ActiveChanged(None))

    def close_all(self) -> None:
        """Disconnect everything and stop the worker threads."""
        with self._lock:
            sessions = list(self._sessions.values())
            self._active_id = None
        # Do not wait for connects in flight: disconnect() makes them discard their result.
        for session in sessions:
            session.disconnect()
        self._executor.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------ internals

    def _connect(self, session: Session) -> Session:
        try:
            password = self._password_for(session)
        except SecretStoreError as error:  # e.g. the vault is locked
            session.fail(error)
        else:
            session.connect(password, self._environ)
        return session

    def _password_for(self, session: Session) -> str | None:
        config = session.config
        typed = self._typed_passwords.get(config.id)
        if typed is not None:
            return typed
        if isinstance(config, FileConnection) or not config.save_password:
            return None
        return self._secrets.get(config.id)

    def _forward(self, event: SessionStateChanged) -> None:
        self._emit(event)

    def _emit(self, event: ManagerEvent) -> None:
        if self._listener is not None:
            self._listener(event)


def _done(session: Session) -> Future[Session]:
    future: Future[Session] = Future()
    future.set_result(session)
    return future
