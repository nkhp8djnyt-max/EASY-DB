"""What a hosted-database plugin is: some defaults to fill in, and perhaps a token to fetch."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field

from ...db.errors import ConnectionFailed
from ...dialects import DialectId
from ..models import ProviderKind, ServerConnection, SslMode


class ProviderError(ConnectionFailed):
    """The hosted service's plugin could not prepare the connection."""


class ProviderUnavailable(ProviderError):
    """The Python package the plugin needs is not installed (``extra`` names the pip extra)."""

    def __init__(self, package: str, extra: str) -> None:
        super().__init__(
            f"the package '{package}' is needed for this provider; "
            f"install it with: pip install 'easydbms[{extra}]'"
        )
        self.package = package
        self.extra = extra


class ProviderAuthError(ProviderError):
    """The cloud account refused to issue a token, or no credentials were found."""


@dataclass(frozen=True, slots=True)
class ProviderField:
    """A setting the plugin asks for (shown in the "Cloud" tab)."""

    name: str
    label: str
    placeholder: str = ""
    required: bool = False


@dataclass(frozen=True, slots=True)
class Defaults:
    """What choosing a provider pre-fills; ``None`` / empty means "leave that field alone"."""

    dialect: DialectId | None = None
    host: str | None = None
    port: int | None = None
    user: str | None = None
    database: str | None = None
    ssl_mode: SslMode | None = None
    options: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Token:
    """A short-lived password. ``expires_at`` is seconds since the epoch (``None``: unknown)."""

    password: str = field(repr=False)
    expires_at: float | None = None

    def valid(self, margin: float = 60.0, now: float | None = None) -> bool:
        if self.expires_at is None:
            return True
        return (time.time() if now is None else now) + margin < self.expires_at


class Provider(ABC):
    kind: ProviderKind
    title: str
    #: One or two sentences for the "Cloud" tab.
    note: str
    fields: tuple[ProviderField, ...] = ()
    dialects: tuple[DialectId, ...] = (DialectId.POSTGRESQL, DialectId.MYSQL)
    #: ``True``: a token replaces the password and is fetched every time a connection is opened.
    uses_token: bool = False

    def missing(self, params: Mapping[str, str]) -> list[ProviderField]:
        """The required settings that are empty."""
        return [f for f in self.fields if f.required and not params.get(f.name, "").strip()]

    def defaults(self, params: Mapping[str, str], dialect: DialectId) -> Defaults:
        return Defaults(ssl_mode=SslMode.REQUIRE)

    def token(
        self, config: ServerConnection, params: Mapping[str, str], environ: Mapping[str, str]
    ) -> Token:
        """A password for ``config`` (only providers with ``uses_token``)."""
        raise NotImplementedError(f"{self.title} does not use tokens")

    @abstractmethod
    def describe(self, token: Token | None) -> str:
        """One line for the connection test: what was prepared."""
