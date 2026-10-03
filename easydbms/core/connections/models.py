"""Saved-connection models.

``ConnectionConfig`` is a pydantic discriminated union on ``kind``: ``server`` connections
(PostgreSQL, MySQL/MariaDB) and ``file`` connections (SQLite). Passwords and other secrets are never
part of a config; they live in a :class:`~.secrets.SecretStore` under the connection's ``id``.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

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
    host: str = Field(default="localhost", min_length=1)
    #: ``None`` means the dialect's default port.
    port: int | None = Field(default=None, ge=1, le=65535)
    user: str = ""
    database: str = ""
    #: Extra driver options, e.g. ``{"sslmode": "require"}``; passed through to the driver.
    options: dict[str, str] = Field(default_factory=dict)
    #: Keep the password in the secret store (otherwise it is asked for on connect).
    save_password: bool = True

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
        return (
            f"{who}{self.host}{where}/{self.database}"
            if self.database
            else f"{who}{self.host}{where}"
        )


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
