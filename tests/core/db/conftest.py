"""Run client tests against SQLite (always) and PostgreSQL / MySQL (when configured)."""

from __future__ import annotations

import os
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from sql_erd_studio.core.connections import (
    FileConnection,
    ServerConnection,
    parse_connection_url,
)
from sql_erd_studio.core.db import DatabaseClient, create_client

_ENV_URLS = {"postgresql": "ERD_TEST_POSTGRES_URL", "mysql": "ERD_TEST_MYSQL_URL"}


class Unset:
    """Marks "argument not given" where ``None`` is a meaningful value."""


UNSET = Unset()


@dataclass
class Target:
    """One engine under test: how to reach it, plus a registry of tables to clean up."""

    name: str
    config: ServerConnection | FileConnection
    password: str | None
    _tables: list[str]

    def client(
        self,
        config: ServerConnection | FileConnection | None = None,
        password: str | Unset | None = UNSET,
    ) -> DatabaseClient:
        """A new client for this engine; ``password`` defaults to the engine's real one."""
        chosen = self.password if isinstance(password, Unset) else password
        return create_client(config or self.config, chosen)

    def table(self) -> str:
        name = f"erd_{uuid.uuid4().hex[:10]}"
        self._tables.append(name)
        return name

    @property
    def is_server(self) -> bool:
        return isinstance(self.config, ServerConnection)

    @property
    def quote(self) -> Callable[[str], str]:
        return self.config.dialect_impl.quote_ident


@pytest.fixture(params=["postgresql", "mysql", "sqlite"])
def target(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Target]:
    name: str = request.param
    tables: list[str] = []
    if name == "sqlite":
        path = tmp_path / "target.db"
        sqlite3.connect(path).close()
        config: ServerConnection | FileConnection = FileConnection.model_validate(
            {"name": "sqlite", "path": str(path)}
        )
        password = None
    else:
        url = os.environ.get(_ENV_URLS[name])
        if not url:
            pytest.skip(f"set {_ENV_URLS[name]} to run against {name}")
        parsed = parse_connection_url(url, name=name)
        config, password = parsed.config, parsed.password
    result = Target(name, config, password, tables)
    try:
        yield result
    finally:
        if tables:
            with result.client() as client:
                for table in tables:
                    client.execute(f"DROP TABLE IF EXISTS {result.quote(table)}")
