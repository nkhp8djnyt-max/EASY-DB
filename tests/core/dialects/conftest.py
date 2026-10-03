"""Fixtures that run a test against every supported engine.

SQLite always runs. PostgreSQL and MySQL/MariaDB run when ``EASYDBMS_TEST_POSTGRES_URL`` /
``EASYDBMS_TEST_MYSQL_URL`` point at an empty throw-away database, otherwise those cases are
skipped.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine

from easydbms.core.dialects import MYSQL, POSTGRESQL, SQLITE, Dialect

_ENV_URLS = {
    "postgresql": "EASYDBMS_TEST_POSTGRES_URL",
    "mysql": "EASYDBMS_TEST_MYSQL_URL",
}


@dataclass
class EngineCase:
    dialect: Dialect
    engine: Engine
    _tables: list[str] = field(default_factory=list)
    _cleanup: list[str] = field(default_factory=list)

    def new_table_name(self) -> str:
        """A fresh, lower-case, safe table name that is dropped when the test ends."""
        name = f"erd_{uuid.uuid4().hex[:10]}"
        self._tables.append(name)
        return name

    def track_table(self, name: str) -> None:
        self._tables.append(name)

    def add_cleanup(self, sql: str) -> None:
        """Extra statement run after the tables are dropped (functions, ...)."""
        self._cleanup.append(sql)

    def drop_everything(self) -> None:
        quote = self.dialect.quote_ident
        with self.engine.begin() as conn:
            for name in self._tables:
                conn.connection.cursor().execute(f"DROP TABLE IF EXISTS {quote(name)}")
            for sql in self._cleanup:
                conn.connection.cursor().execute(sql)


@pytest.fixture(params=["postgresql", "mysql", "sqlite"])
def engine_case(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[EngineCase]:
    name: str = request.param
    dialect: Dialect
    if name == "sqlite":
        engine = create_engine(f"sqlite:///{tmp_path / 'case.db'}")
        dialect = SQLITE
    else:
        url = os.environ.get(_ENV_URLS[name])
        if not url:
            pytest.skip(f"set {_ENV_URLS[name]} to run against {name}")
        engine = create_engine(url)
        dialect = POSTGRESQL if name == "postgresql" else MYSQL
    case = EngineCase(dialect, engine)
    try:
        yield case
    finally:
        case.drop_everything()
        engine.dispose()
