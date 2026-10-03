"""Fill a PostgreSQL connection from ``pg_service.conf`` and ``~/.pgpass``.

Order of precedence for every value: what the connection itself says, then the service, then
(for the password) a password that was saved or typed, then ``~/.pgpass``, then the service's own
``password``. Everything that came from a file is reported in ``notes`` so the connection test can
say where a value was read.
"""

from __future__ import annotations

import contextlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..dialects import DialectId
from .models import ServerConnection, SslMode, replace
from .pgfiles import find_service, load_pgpass

_SSL_KEYS = ("sslmode", "sslrootcert", "sslcert", "sslkey", "sslpassword")
_PLAIN_KEYS = ("host", "hostaddr", "port", "dbname", "user", "password", "service", "passfile")


@dataclass(frozen=True, slots=True)
class Completed:
    config: ServerConnection
    password: str | None
    ssl_key_password: str | None
    #: ``(what, where)`` pairs: ``("Service", "prod (/home/me/.pg_service.conf)")``.
    notes: tuple[tuple[str, str], ...] = ()
    #: Set when ``~/.pgpass`` exists but cannot be used (permissions).
    pgpass_problem: str = ""


def complete(
    config: ServerConnection,
    password: str | None,
    ssl_key_password: str | None,
    environ: Mapping[str, str],
) -> Completed:
    """Apply the service named by ``config`` and look up a missing password in ``~/.pgpass``."""
    if config.dialect is not DialectId.POSTGRESQL:
        return Completed(config, password, ssl_key_password)
    notes: list[tuple[str, str]] = []
    service_password: str | None = None
    if config.service:
        service = find_service(config.service, environ)
        config, service_password, service_key_password = _apply_service(config, service.params)
        ssl_key_password = ssl_key_password or service_key_password
        notes.append(("Service", f"{service.name} ({_short(service.path)})"))
    pgpass_problem = ""
    if not password:
        file = load_pgpass(environ)
        if file is not None:
            pgpass_problem = file.problem
            found = file.lookup(config.host, config.effective_port, config.database, config.user)
            if found is None and not config.database:
                found = file.lookup(config.host, config.effective_port, config.user, config.user)
            if found is not None:
                password = found.password
                notes.append(("Password", f"{_short(found.path)}, line {found.line}"))
        if not password and service_password:
            password = service_password
            notes.append(("Password", "from the service"))
    return Completed(config, password, ssl_key_password, tuple(notes), pgpass_problem)


def _apply_service(
    config: ServerConnection, params: Mapping[str, str]
) -> tuple[ServerConnection, str | None, str | None]:
    changes: dict[str, object] = {}
    if not config.host:
        changes["host"] = params.get("host") or params.get("hostaddr") or "localhost"
    if config.port is None and params.get("port", "").isdigit():
        changes["port"] = int(params["port"])
    if not config.user and params.get("user"):
        changes["user"] = params["user"]
    if not config.database and params.get("dbname"):
        changes["database"] = params["dbname"]
    ssl = config.ssl.model_dump()
    if config.ssl.mode is None and params.get("sslmode"):
        with contextlib.suppress(ValueError):  # an unknown mode is left to libpq to complain about
            ssl["mode"] = SslMode(params["sslmode"])
    for key, field in (
        ("sslrootcert", "ca_file"),
        ("sslcert", "cert_file"),
        ("sslkey", "key_file"),
    ):
        if not ssl[field] and params.get(key):
            ssl[field] = params[key]
    changes["ssl"] = ssl
    extra = {
        key: value
        for key, value in params.items()
        if key not in _PLAIN_KEYS and key not in _SSL_KEYS
    }
    changes["options"] = {**extra, **config.options}
    completed = replace(config, **changes)
    assert isinstance(completed, ServerConnection)
    return completed, params.get("password"), params.get("sslpassword")


def _short(path: Path) -> str:
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


__all__ = ["Completed", "complete"]
