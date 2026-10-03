"""Connection URLs <-> :class:`~.models.ConnectionConfig`.

``postgresql://user:pw@host:5432/db?sslmode=require`` becomes a config plus the password, which is
returned separately because configs never hold secrets. Building a URL never includes the password.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import SplitResult, parse_qsl, quote, unquote, urlsplit

from ..dialects import Dialect, UnknownDialectError, dialect_from_url
from .models import FileConnection, ServerConnection

MEMORY_DATABASE = ":memory:"


class ConnectionUrlError(ValueError):
    """The text is not a usable connection URL."""


@dataclass(frozen=True, slots=True)
class ParsedUrl:
    config: ServerConnection | FileConnection
    #: The password found in the URL, already percent-decoded.
    password: str | None


def parse_connection_url(url: str, *, name: str | None = None) -> ParsedUrl:
    """Parse ``url`` for any supported dialect (``postgres://`` and ``mariadb://`` included)."""
    text = url.strip()
    if not text:
        raise ConnectionUrlError("the URL is empty")
    try:
        dialect = dialect_from_url(text)
    except UnknownDialectError as error:
        raise ConnectionUrlError(str(error)) from None
    except ValueError as error:
        raise ConnectionUrlError(str(error)) from None
    try:
        parts = urlsplit(text)
    except ValueError as error:
        raise ConnectionUrlError(f"invalid URL: {error}") from None
    if dialect.file_based:
        return _parse_file(dialect, parts.path, parts.query, parts.netloc, name)
    return _parse_server(dialect, parts, name)


def _parse_file(
    dialect: Dialect, raw_path: str, query: str, netloc: str, name: str | None
) -> ParsedUrl:
    if netloc:
        raise ConnectionUrlError(
            f"{dialect.display_name} URLs have no host: use {dialect.url_schemes[0]}:///path/to.db"
        )
    # sqlite:///rel/x.db -> "rel/x.db"; sqlite:////abs/x.db -> "/abs/x.db";
    # sqlite:///C:/x.db -> "C:/x.db"
    path = unquote(raw_path[1:] if raw_path.startswith("/") else raw_path) or MEMORY_DATABASE
    read_only = False
    for key, value in parse_qsl(query, keep_blank_values=True):
        if key == "mode" and value == "ro":
            read_only = True
        else:
            raise ConnectionUrlError(
                f"unsupported {dialect.display_name} URL option: {key}={value}"
            )
    label = name or ("In-memory database" if path == MEMORY_DATABASE else _basename(path))
    config = FileConnection.model_validate({"name": label, "path": path, "read_only": read_only})
    return ParsedUrl(config, None)


def _parse_server(dialect: Dialect, parts: SplitResult, name: str | None) -> ParsedUrl:
    netloc = parts.netloc
    userinfo, _, hostport = netloc.rpartition("@")
    if "," in hostport:
        raise ConnectionUrlError("several hosts in one URL are not supported")
    try:
        port = parts.port
    except ValueError:
        raise ConnectionUrlError("the port must be a number between 1 and 65535") from None
    user, has_password, password = userinfo.partition(":")
    host = parts.hostname or "localhost"
    database = unquote(parts.path[1:]) if parts.path.startswith("/") else unquote(parts.path)
    options = dict(parse_qsl(parts.query, keep_blank_values=True))
    label = name or _default_name(unquote(user), host, database)
    try:
        config = ServerConnection.model_validate(
            {
                "name": label,
                "dialect": dialect.id.value,
                "host": host,
                "port": port,
                "user": unquote(user),
                "database": database,
                "options": options,
            }
        )
    except ValueError as error:
        raise ConnectionUrlError(str(error)) from None
    return ParsedUrl(config, unquote(password) if has_password else None)


def build_connection_url(config: ServerConnection | FileConnection) -> str:
    """A URL for ``config`` without any password. Round-trips with :func:`parse_connection_url`."""
    dialect = config.dialect_impl
    scheme = dialect.url_schemes[0]
    if isinstance(config, FileConnection):
        path = "" if config.path == MEMORY_DATABASE else quote(config.path, safe="/:")
        suffix = "?mode=ro" if config.read_only else ""
        if config.path == MEMORY_DATABASE:
            return f"{scheme}://{suffix}"
        # a leading "/" in the path (absolute) yields the four-slash form
        return f"{scheme}:///{path}{suffix}"
    host = f"[{config.host}]" if ":" in config.host else config.host
    port = f":{config.port}" if config.port is not None else ""
    user = f"{quote(config.user, safe='')}@" if config.user else ""
    database = f"/{quote(config.database, safe='')}" if config.database else ""
    query = "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in config.options.items())
    return f"{scheme}://{user}{host}{port}{database}{'?' + query if query else ''}"


def _default_name(user: str, host: str, database: str) -> str:
    base = database or host
    return f"{user}@{base}" if user else base


def _basename(path: str) -> str:
    return path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or path
