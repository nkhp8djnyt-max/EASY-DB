"""Saved-connection models.

``ConnectionConfig`` is a pydantic discriminated union on ``kind``: ``server`` connections
(PostgreSQL, MySQL/MariaDB) and ``file`` connections (SQLite). Passwords and other secrets are never
part of a config; they live in a :class:`~.secrets.SecretStore` under the connection's ``id``.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from ..dialects import Dialect, DialectId, get_dialect


class ConnectionColor(StrEnum):
    """Label colours offered in the connection dialog."""

    RED = "red"
    ORANGE = "orange"
    YELLOW = "yellow"
    GREEN = "green"
    TEAL = "teal"
    BLUE = "blue"
    PURPLE = "purple"
    GRAY = "gray"


class SslMode(StrEnum):
    """How TLS is used; the names are libpq's ``sslmode`` values (MySQL gets the same semantics)."""

    DISABLE = "disable"
    ALLOW = "allow"  # PostgreSQL only: plain first, TLS if the server insists
    PREFER = "prefer"  # TLS if the server offers it, otherwise plain
    REQUIRE = "require"  # TLS, without checking who the server is
    VERIFY_CA = "verify-ca"  # TLS, the certificate must be signed by the given / system CA
    VERIFY_FULL = "verify-full"  # ... and must be issued for the host name used

    @property
    def verifies(self) -> bool:
        return self in (SslMode.VERIFY_CA, SslMode.VERIFY_FULL)


class SslConfig(BaseModel):
    """TLS settings. Files are paths; the key's passphrase (if any) is a secret, not stored here."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True, str_strip_whitespace=True)

    #: ``None``: whatever the driver does by default.
    mode: SslMode | None = None
    #: Certificate authority bundle (PEM) that signed the server's certificate.
    ca_file: str = ""
    #: Client certificate and its private key (PEM), for servers that ask for one.
    cert_file: str = ""
    key_file: str = ""

    @property
    def is_default(self) -> bool:
        return self.mode is None and not (self.ca_file or self.cert_file or self.key_file)

    @property
    def encrypts(self) -> bool:
        """Is TLS asked for (as opposed to disabled or left to the driver)?"""
        return self.mode in (SslMode.REQUIRE, SslMode.VERIFY_CA, SslMode.VERIFY_FULL)


class SshAuth(StrEnum):
    PASSWORD = "password"
    KEY = "key"  # a private key file, optionally protected by a passphrase
    AGENT = "agent"  # ssh-agent / Pageant


class SshHop(BaseModel):
    """One SSH server. Its password / key passphrase are secrets, not stored here."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True, str_strip_whitespace=True)

    host: str = Field(min_length=1)
    port: int = Field(default=22, ge=1, le=65535)
    #: Empty: the current operating-system user.
    user: str = ""
    auth: SshAuth = SshAuth.AGENT
    key_file: str = ""

    @model_validator(mode="after")
    def _a_key_needs_its_file(self) -> SshHop:
        if self.auth is SshAuth.KEY and not self.key_file:
            raise ValueError("choose the private key file")
        return self


class SshConfig(BaseModel):
    """An SSH tunnel to the database host, optionally through a jump host (bastion)."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    server: SshHop
    jump: SshHop | None = None


#: Options that mean TLS settings; they are moved into :class:`SslConfig` when a config is loaded
#: (older files and URLs carry them as plain driver options).
_PG_SSL_KEYS = {
    "sslmode": "mode",
    "sslrootcert": "ca_file",
    "sslcert": "cert_file",
    "sslkey": "key_file",
}
_MYSQL_SSL_KEYS = {"ssl_ca": "ca_file", "ssl_cert": "cert_file", "ssl_key": "key_file"}
_TRUE = {"1", "true", "yes", "on"}


def ssl_from_options(
    dialect: DialectId, options: dict[str, str]
) -> tuple[SslConfig, dict[str, str]]:
    """Split the TLS driver options off ``options``: ``(ssl settings, the remaining options)``."""
    rest = dict(options)
    fields: dict[str, Any] = {}
    if dialect is DialectId.POSTGRESQL:
        for key, name in _PG_SSL_KEYS.items():
            if key in rest:
                fields[name] = rest.pop(key)
    else:
        for key, name in _MYSQL_SSL_KEYS.items():
            if key in rest:
                fields[name] = rest.pop(key)
        disabled = rest.pop("ssl_disabled", "").lower() in _TRUE
        identity = rest.pop("ssl_verify_identity", "").lower() in _TRUE
        verify = rest.pop("ssl_verify_cert", "").lower() in _TRUE
        named = rest.pop("ssl_mode", rest.pop("ssl-mode", "")).strip().lower().replace("_", "-")
        named = {"disabled": "disable", "required": "require", "preferred": "prefer"}.get(
            named, {"verify-identity": "verify-full"}.get(named, named)
        )
        if disabled:
            fields["mode"] = SslMode.DISABLE
        elif named:
            fields["mode"] = named
        elif identity:
            fields["mode"] = SslMode.VERIFY_FULL
        elif verify:
            fields["mode"] = SslMode.VERIFY_CA
        elif fields.get("ca_file") or fields.get("cert_file"):
            fields["mode"] = SslMode.REQUIRE  # pymysql: TLS is mandatory once files are given
    return SslConfig.model_validate(fields), rest


_MYSQL_MODE_NAMES = {
    SslMode.DISABLE: "DISABLED",
    SslMode.ALLOW: "PREFERRED",
    SslMode.PREFER: "PREFERRED",
    SslMode.REQUIRE: "REQUIRED",
    SslMode.VERIFY_CA: "VERIFY_CA",
    SslMode.VERIFY_FULL: "VERIFY_IDENTITY",
}


def ssl_to_options(dialect: DialectId, ssl: SslConfig) -> dict[str, str]:
    """The TLS settings as URL query parameters (the inverse of :func:`ssl_from_options`)."""
    params: dict[str, str] = {}
    if dialect is DialectId.POSTGRESQL:
        if ssl.mode is not None:
            params["sslmode"] = ssl.mode.value
        if ssl.ca_file:
            params["sslrootcert"] = ssl.ca_file
        if ssl.cert_file:
            params["sslcert"] = ssl.cert_file
        if ssl.key_file:
            params["sslkey"] = ssl.key_file
        return params
    if ssl.mode is not None:
        params["ssl_mode"] = _MYSQL_MODE_NAMES[ssl.mode]
    if ssl.ca_file:
        params["ssl_ca"] = ssl.ca_file
    if ssl.cert_file:
        params["ssl_cert"] = ssl.cert_file
    if ssl.key_file:
        params["ssl_key"] = ssl.key_file
    return params


class _ConnectionBase(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, str_strip_whitespace=True)

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    name: str = Field(min_length=1, max_length=100)
    color: ConnectionColor | None = None
    group: str = Field(default="", max_length=100)
    #: Block writes: the session is switched to read-only after connecting.
    read_only: bool = False
    #: A production database: shown in red and (from stage 5 on) asks before applying changes.
    production: bool = False

    @property
    def display_color(self) -> ConnectionColor | None:
        """The explicit colour label, or red for production connections."""
        return self.color or (ConnectionColor.RED if self.production else None)


class ServerConnection(_ConnectionBase):
    kind: Literal["server"] = "server"
    dialect: Literal[DialectId.POSTGRESQL, DialectId.MYSQL]
    #: Empty is allowed only together with a ``service`` that supplies the host.
    host: str = "localhost"
    #: ``None`` means the dialect's default port.
    port: int | None = Field(default=None, ge=1, le=65535)
    user: str = ""
    database: str = ""
    #: Extra driver options, e.g. ``{"connect_timeout": "5"}``; passed through to the driver.
    #: (TLS settings are in :attr:`ssl`.)
    options: dict[str, str] = Field(default_factory=dict)
    ssl: SslConfig = Field(default_factory=SslConfig)
    #: An SSH tunnel through which the database is reached (``None``: connect directly).
    ssh: SshConfig | None = None
    #: PostgreSQL: a ``[service]`` of ``pg_service.conf`` that supplies whatever is left empty here.
    service: str = ""
    #: Keep the password (and the SSH / key passphrases) in the secret store; otherwise they are
    #: asked for when connecting.
    save_password: bool = True

    @model_validator(mode="before")
    @classmethod
    def _move_ssl_options(cls, data: Any) -> Any:
        """Accept TLS driver options in ``options`` (old files, URLs) and turn them into ``ssl``."""
        if not isinstance(data, dict) or not isinstance(data.get("options"), dict):
            return data
        try:
            dialect_id = DialectId(str(data.get("dialect")))
        except ValueError:
            return data
        moved, rest = ssl_from_options(
            dialect_id, {str(k): str(v) for k, v in data["options"].items()}
        )
        if moved.is_default:
            return data
        merged = {**moved.model_dump(exclude_defaults=True), **_ssl_dict(data.get("ssl"))}
        return {**data, "options": rest, "ssl": merged}

    @model_validator(mode="after")
    def _a_host_or_a_service(self) -> ServerConnection:
        if not self.host.strip() and not self.service.strip():
            raise ValueError("enter the host name (or a pg_service.conf service)")
        return self

    @field_validator("options")
    @classmethod
    def _option_names_are_not_empty(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not key.strip() for key in value):
            raise ValueError("option names must not be empty")
        return value

    @property
    def dialect_impl(self) -> Dialect:
        return get_dialect(self.dialect)

    @property
    def effective_port(self) -> int | None:
        return self.port if self.port is not None else self.dialect_impl.default_port

    @property
    def subtitle(self) -> str:
        who = f"{self.user}@" if self.user else ""
        where = f":{self.port}" if self.port is not None else ""
        host = self.host or f"service:{self.service}"
        label = f"{who}{host}{where}/{self.database}" if self.database else f"{who}{host}{where}"
        return f"{label} (SSH {self.ssh.server.host})" if self.ssh else label


def _ssl_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, SslConfig):
        return value.model_dump(exclude_defaults=True)
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if v not in (None, "")}
    return {}


class FileConnection(_ConnectionBase):
    kind: Literal["file"] = "file"
    dialect: Literal[DialectId.SQLITE] = DialectId.SQLITE
    path: str = Field(min_length=1)
    #: Create the file when it does not exist. Off by default so a typo never makes an empty DB.
    create_if_missing: bool = False

    @property
    def dialect_impl(self) -> Dialect:
        return get_dialect(self.dialect)

    @property
    def subtitle(self) -> str:
        return self.path


ConnectionConfig = Annotated[ServerConnection | FileConnection, Field(discriminator="kind")]

_ADAPTER: TypeAdapter[ServerConnection | FileConnection] = TypeAdapter(ConnectionConfig)


def parse_config(data: Any) -> ServerConnection | FileConnection:
    """Validate a plain dict (e.g. loaded JSON) into the right config class."""
    return _ADAPTER.validate_python(data)


def replace(
    config: ServerConnection | FileConnection, **changes: Any
) -> ServerConnection | FileConnection:
    """A validated copy of ``config`` with ``changes`` applied (``model_copy`` skips validation)."""
    return parse_config({**config.model_dump(mode="python"), **changes})
