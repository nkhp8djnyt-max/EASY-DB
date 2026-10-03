"""A small bookshop schema created on whichever engine is under test."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from easydbms.core.db import DatabaseClient
from tests.core.conftest import Target


@dataclass(frozen=True)
class Shop:
    target: Target
    client: DatabaseClient
    prefix: str

    def name(self, short: str) -> str:
        return f"{self.prefix}_{short}"


_DDL = (
    "CREATE TABLE {author} (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL, "
    "email VARCHAR(100) UNIQUE, bio VARCHAR(500) DEFAULT 'n/a')",
    "CREATE TABLE {book} (id INTEGER PRIMARY KEY, author_id INTEGER NOT NULL, "
    "title VARCHAR(200), isbn VARCHAR(20) UNIQUE, published INTEGER, "
    "FOREIGN KEY (author_id) REFERENCES {author} (id) ON DELETE CASCADE)",
    "CREATE INDEX {book}_title ON {book} (title)",
    "CREATE TABLE {tag} (id INTEGER PRIMARY KEY, label VARCHAR(50) NOT NULL)",
    "CREATE TABLE {book_tag} (book_id INTEGER NOT NULL, tag_id INTEGER NOT NULL, "
    "PRIMARY KEY (book_id, tag_id), "
    "FOREIGN KEY (book_id) REFERENCES {book} (id), FOREIGN KEY (tag_id) REFERENCES {tag} (id))",
    "CREATE TABLE {profile} (author_id INTEGER PRIMARY KEY, website VARCHAR(100), "
    "FOREIGN KEY (author_id) REFERENCES {author} (id))",
    "CREATE TABLE {line} (order_id INTEGER NOT NULL, line_no INTEGER NOT NULL, "
    "PRIMARY KEY (order_id, line_no))",
    "CREATE TABLE {note} (id INTEGER PRIMARY KEY, order_id INTEGER, line_no INTEGER, "
    "body VARCHAR(100), "
    "FOREIGN KEY (order_id, line_no) REFERENCES {line} (order_id, line_no))",
    "CREATE TABLE {node} (id INTEGER PRIMARY KEY, parent_id INTEGER, "
    "FOREIGN KEY (parent_id) REFERENCES {node} (id))",
    "CREATE VIEW {authors_v} AS SELECT id, name FROM {author}",
)
_ORDER = ("authors_v", "node", "note", "line", "profile", "book_tag", "tag", "book", "author")


@pytest.fixture
def shop(target: Target) -> Iterator[Shop]:
    prefix = f"erd{uuid.uuid4().hex[:8]}"
    names = {
        short: f"{prefix}_{short}"
        for short in ("author", "book", "tag", "book_tag", "profile", "line", "note", "node")
    }
    names["authors_v"] = f"{prefix}_authors_v"
    quoted = {short: target.quote(name) for short, name in names.items()}
    quoted["book"] = target.quote(names["book"])
    index_name = target.quote(f"{names['book']}_title")
    with target.client() as client:
        try:
            for statement in _DDL:
                client.execute(statement.replace("{book}_title", index_name).format(**quoted))
            yield Shop(target, client, prefix)
        finally:
            for short in _ORDER:
                kind = "VIEW" if short.endswith("_v") else "TABLE"
                client.execute(f"DROP {kind} IF EXISTS {quoted[short]}")
