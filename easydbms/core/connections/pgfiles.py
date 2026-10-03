"""PostgreSQL's own connection files: ``~/.pgpass`` and ``pg_service.conf``.

libpq reads both by itself, but the application needs them earlier: to know whether a password has
to be asked for, to look the password up under the *real* host name when an SSH tunnel makes libpq
see ``127.0.0.1``, and to let the user pick a service in the connection dialog.

The formats and the search order follow the libpq documentation: ``PGPASSFILE`` / ``~/.pgpass``
(``%APPDATA%\\postgresql\\pgpass.conf`` on Windows; ignored when other users can read it), and
``PGSERVICEFILE`` / ``~/.pg_service.conf`` / ``$PGSYSCONFDIR/pg_service.conf``.
"""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PORT = 5432
#: Where libpq looks for the system-wide service file when ``PGSYSCONFDIR`` is not set.
_SYSTEM_CONF_DIRS = ("/etc/postgresql-common", "/etc/postgresql", "/etc")


class ServiceNotFoundError(ValueError):
    """A connection names a ``pg_service.conf`` service that no service file defines."""

    def __init__(self, name: str, searched: list[Path]) -> None:
        self.name = name
        where = ", ".join(str(p) for p in searched) or "no service file exists"
        super().__init__(f"the service '{name}' is not defined ({where})")


# ---------------------------------------------------------------------- ~/.pgpass


@dataclass(frozen=True, slots=True)
class PgPassEntry:
    host: str
    port: str
    database: str
    user: str
    password: str
    line: int

    def matches(self, host: str, port: str, database: str, user: str) -> bool:
        return (
            _field_matches(self.host, host)
            and _field_matches(self.port, port)
            and _field_matches(self.database, database)
            and _field_matches(self.user, user)
        )


@dataclass(frozen=True, slots=True)
class PgPassMatch:
    password: str
    path: Path
    line: int


@dataclass(frozen=True, slots=True)
class PgPass:
    path: Path
    entries: tuple[PgPassEntry, ...] = ()
    #: Why the file is not used even though it exists (permissions); empty when it is fine.
    problem: str = ""

    def lookup(self, host: str, port: int | None, database: str, user: str) -> PgPassMatch | None:
        """The first line that fits, like libpq (``host`` may be a unix socket directory)."""
        if self.problem:
            return None
        name = "localhost" if not host or host.startswith("/") else host
        number = str(port if port is not None else DEFAULT_PORT)
        for entry in self.entries:
            if entry.matches(name, number, database, user):
                return PgPassMatch(entry.password, self.path, entry.line)
        return None


def _field_matches(pattern: str, value: str) -> bool:
    return pattern == "*" or pattern == value


def _split_fields(line: str) -> list[str] | None:
    """Split on unescaped colons; ``\\:`` and ``\\\\`` are literal. ``None``: not five fields."""
    fields: list[str] = []
    current: list[str] = []
    chars = iter(line)
    for char in chars:
        if char == "\\":
            current.append(next(chars, "\\"))
        elif char == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(char)
    fields.append("".join(current))
    return fields if len(fields) == 5 else None


def parse_pgpass(text: str, path: Path | None = None) -> PgPass:
    entries: list[PgPassEntry] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip("\r\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = _split_fields(line)
        if fields is not None:
            host, port, database, user, password = fields
            entries.append(PgPassEntry(host, port, database, user, password, number))
    return PgPass(path or Path("pgpass"), tuple(entries))


def pgpass_path(environ: Mapping[str, str] | None = None, *, windows: bool | None = None) -> Path:
    env = os.environ if environ is None else environ
    configured = env.get("PGPASSFILE")
    if configured:
        return Path(configured).expanduser()
    if windows if windows is not None else sys.platform == "win32":
        appdata = env.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / "postgresql" / "pgpass.conf"
    return Path(env.get("HOME") or Path.home()) / ".pgpass"


def load_pgpass(environ: Mapping[str, str] | None = None) -> PgPass | None:
    """The password file, or ``None`` when there is none; an unsafe file is returned unusable."""
    path = pgpass_path(environ)
    try:
        info = path.stat()
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if sys.platform != "win32" and info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        return PgPass(
            path,
            problem=f"{path} is ignored: other users can read it (run: chmod 0600 {path})",
        )
    return parse_pgpass(text, path)


# ---------------------------------------------------------------------- pg_service.conf


@dataclass(frozen=True, slots=True)
class Service:
    name: str
    #: libpq keywords: ``host``, ``port``, ``dbname``, ``user``, ``password``, ``sslmode`` ...
    params: Mapping[str, str]
    path: Path = field(default_factory=lambda: Path("pg_service.conf"))


def parse_service_file(text: str, path: Path | None = None) -> dict[str, Service]:
    services: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip()
            current = services.setdefault(name, {})
        elif current is not None and "=" in line:
            key, _, value = line.partition("=")
            current[key.strip()] = value.strip()
    where = path or Path("pg_service.conf")
    return {name: Service(name, params, where) for name, params in services.items()}


def service_file_paths(environ: Mapping[str, str] | None = None) -> list[Path]:
    """The service files libpq would read, in the order it reads them (existing or not)."""
    env = os.environ if environ is None else environ
    paths: list[Path] = []
    own = env.get("PGSERVICEFILE")
    paths.append(
        Path(own).expanduser() if own else Path(env.get("HOME") or Path.home()) / ".pg_service.conf"
    )
    system = env.get("PGSYSCONFDIR")
    if system:
        paths.append(Path(system) / "pg_service.conf")
    else:
        paths.extend(Path(directory) / "pg_service.conf" for directory in _SYSTEM_CONF_DIRS)
    return paths


def load_services(environ: Mapping[str, str] | None = None) -> dict[str, Service]:
    """Every service of every readable service file; the first file that defines a name wins."""
    found: dict[str, Service] = {}
    for path in service_file_paths(environ):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name, service in parse_service_file(text, path).items():
            found.setdefault(name, service)
    return found


def find_service(name: str, environ: Mapping[str, str] | None = None) -> Service:
    services = load_services(environ)
    if name not in services:
        raise ServiceNotFoundError(name, service_file_paths(environ))
    return services[name]
