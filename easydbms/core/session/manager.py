"""The set of open sessions and which one is active."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

from ..connections import (
    JUMP_PASSPHRASE,
    JUMP_PASSWORD,
    PASSWORD,
    SSH_PASSPHRASE,
    SSH_PASSWORD,
    SSL_KEY_PASSWORD,
    ConnectionStore,
    FileConnection,
    SecretStore,
    SecretStoreError,
    ServerConnection,
    SshAuth,
    SshHop,
    complete,
    expand,
    resolve_config,
)
from ..db import ConnectionCheck, create_client
from ..db.tls import key_is_encrypted
from ..dialects import DialectId
from ..ssh import KnownHosts, SshHostKeyUnknown, is_encrypted
from .connector import ClientFactory, check_connection
from .session import Session, SessionEvent, SessionState


@dataclass(frozen=True, slots=True)
class ActiveChanged:
    """The active connection changed (``None`` when the last one was closed or deleted)."""

    connection_id: str | None


ManagerEvent = SessionEvent | ActiveChanged


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
        load_schema: bool = False,
        known_hosts: KnownHosts | None = None,
    ) -> None:
        self._store = store
        self._secrets = secrets
        self._listener = listener
        self._environ = environ
        self._client_factory = client_factory
        self._known_hosts = known_hosts
        self._load_schema = load_schema
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="connect")
        self._lock = threading.RLock()
        self._sessions: dict[str, Session] = {}
        self._futures: dict[str, Future[Session]] = {}
        #: Secrets typed this run (``{connection id: {field: value}}``); never written anywhere.
        self._typed: dict[str, dict[str, str]] = {}
        self._active_id: str | None = None

    # ------------------------------------------------------------------ queries

    @property
    def active_id(self) -> str | None:
        return self._active_id

    @property
    def known_hosts(self) -> KnownHosts | None:
        return self._known_hosts

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
            session = Session(
                self._store.get(connection_id),
                self._forward,
                self._client_factory,
                self._known_hosts,
            )
            self._sessions[connection_id] = session
            return session

    def state_of(self, connection_id: str) -> SessionState:
        with self._lock:
            session = self._sessions.get(connection_id)
        return session.state if session else SessionState.DISCONNECTED

    def needs_password(self, connection_id: str) -> bool:
        """``True`` when the database password is neither saved, typed nor found in a file."""
        return PASSWORD in self.missing_secrets(connection_id)

    def missing_secrets(self, connection_id: str) -> list[str]:
        """Secret fields (``password``, ``ssh.password`` ...) the connection needs and lacks.

        The database password is only asked for when the connection does not save it and
        ``~/.pgpass`` / the service file do not supply one; SSH passwords and key passphrases
        when the login needs them and nobody saved or typed them.
        """
        config = self._store.get(connection_id)
        if not isinstance(config, ServerConnection):
            return []
        environ = os.environ if self._environ is None else self._environ
        missing: list[str] = []
        if (
            not config.save_password
            and PASSWORD not in self._typed.get(connection_id, {})
            and not _password_from_files(config, environ)
        ):
            missing.append(PASSWORD)
        needed: list[str] = []
        if config.ssh is not None:
            if config.ssh.jump is not None:
                needed += _hop_secrets(config.ssh.jump, JUMP_PASSWORD, JUMP_PASSPHRASE, environ)
            needed += _hop_secrets(config.ssh.server, SSH_PASSWORD, SSH_PASSPHRASE, environ)
        if config.ssl.key_file and key_is_encrypted(_expanded(config.ssl.key_file, environ)):
            needed.append(SSL_KEY_PASSWORD)
        for field in needed:
            if not self._has_secret(connection_id, config, field):
                missing.append(field)
        return missing

    def test_connection(
        self,
        config: ServerConnection | FileConnection,
        password: str | None = None,
        secrets: Mapping[str, str] | None = None,
    ) -> ConnectionCheck:
        """The "Test connection" report for ``config`` (blocking: run it on a worker thread)."""
        return check_connection(
            config,
            password,
            secrets or {},
            os.environ if self._environ is None else self._environ,
            self._known_hosts,
            self._client_factory,
        )

    def trust_host_key(self, error: SshHostKeyUnknown) -> None:
        """Remember the SSH server key that ``error`` reported (after the person confirmed it)."""
        if self._known_hosts is None:
            raise RuntimeError("no known-hosts store is configured")
        self._known_hosts.trust(error.host, error.port, error.key)

    # ------------------------------------------------------------------ actions

    def remember_password(self, connection_id: str, password: str) -> None:
        """Keep a typed password in memory for this run only (never written anywhere)."""
        self.remember_secret(connection_id, PASSWORD, password)

    def remember_secret(self, connection_id: str, field: str, value: str) -> None:
        """Keep a typed secret (SSH password, key passphrase ...) in memory for this run only."""
        self._typed.setdefault(connection_id, {})[field] = value

    def activate(
        self,
        connection_id: str,
        password: str | None = None,
        secrets: Mapping[str, str] | None = None,
    ) -> Future[Session]:
        """Make the connection active and connect it if it is not already (non-blocking)."""
        session = self.session(connection_id)
        if password is not None:
            self.remember_password(connection_id, password)
        for field, value in (secrets or {}).items():
            self.remember_secret(connection_id, field, value)
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
            self._typed.pop(connection_id, None)
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
            secrets = self._secrets_for(session)
        except SecretStoreError as error:  # e.g. the vault is locked
            session.fail(error)
        else:
            session.connect(password, self._environ, secrets)
            if self._load_schema and session.state is SessionState.READY:
                session.load_schema()  # runs on the session's meta lane, not on this thread
        return session

    def _password_for(self, session: Session) -> str | None:
        config = session.config
        typed = self._typed.get(config.id, {}).get(PASSWORD)
        if typed is not None:
            return typed
        if isinstance(config, FileConnection) or not config.save_password:
            return None
        return self._secrets.get(config.id)

    def _secrets_for(self, session: Session) -> dict[str, str]:
        """The SSH / TLS secrets to connect with: typed ones, else saved ones."""
        config = session.config
        if isinstance(config, FileConnection):
            return {}
        found: dict[str, str] = {}
        for field in _SECRET_FIELDS:
            value = self._typed.get(config.id, {}).get(field)
            if value is None and config.save_password:
                value = self._secrets.get(config.id, field)
            if value:
                found[field] = value
        return found

    def _has_secret(self, connection_id: str, config: ServerConnection, field: str) -> bool:
        if self._typed.get(connection_id, {}).get(field):
            return True
        if not config.save_password:
            return False
        try:
            return bool(self._secrets.get(connection_id, field))
        except SecretStoreError:
            return False

    def _forward(self, event: SessionEvent) -> None:
        self._emit(event)

    def _emit(self, event: ManagerEvent) -> None:
        if self._listener is not None:
            self._listener(event)


#: The secrets besides the database password, in the order they are looked up.
_SECRET_FIELDS = (SSH_PASSWORD, SSH_PASSPHRASE, JUMP_PASSWORD, JUMP_PASSPHRASE, SSL_KEY_PASSWORD)


def _expanded(value: str, environ: Mapping[str, str]) -> str:
    try:
        return expand(value, environ)
    except ValueError:  # an undefined variable is reported when connecting
        return value


def _hop_secrets(
    hop: SshHop, password_field: str, passphrase_field: str, environ: Mapping[str, str]
) -> list[str]:
    if hop.auth is SshAuth.PASSWORD:
        return [password_field]
    if hop.auth is SshAuth.KEY and is_encrypted(_expanded(hop.key_file, environ)):
        return [passphrase_field]
    return []


def _password_from_files(config: ServerConnection, environ: Mapping[str, str]) -> bool:
    """Would ``~/.pgpass`` or the connection's service supply the password?"""
    if config.dialect is not DialectId.POSTGRESQL:
        return False
    try:
        resolved, _ = resolve_config(config, None, environ)
        assert isinstance(resolved, ServerConnection)
        return bool(complete(resolved, None, None, environ).password)
    except (ValueError, OSError):
        return False


def _done(session: Session) -> Future[Session]:
    future: Future[Session] = Future()
    future.set_result(session)
    return future
