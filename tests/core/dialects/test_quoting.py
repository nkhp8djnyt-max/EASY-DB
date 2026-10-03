from __future__ import annotations

import pytest

from sql_erd_studio.core.dialects import MYSQL, POSTGRESQL, SQLITE, Dialect, all_dialects


@pytest.mark.parametrize(
    ("dialect", "name", "expected"),
    [
        (POSTGRESQL, "users", '"users"'),
        (POSTGRESQL, 'we"ird', '"we""ird"'),
        (POSTGRESQL, "Mixed Case", '"Mixed Case"'),
        (SQLITE, 'we"ird', '"we""ird"'),
        (MYSQL, "users", "`users`"),
        (MYSQL, "we`ird", "`we``ird`"),
        (MYSQL, 'has"double', '`has"double`'),
    ],
)
def test_quote_ident(dialect: Dialect, name: str, expected: str) -> None:
    assert dialect.quote_ident(name) == expected


@pytest.mark.parametrize("dialect", all_dialects(), ids=lambda d: d.id.value)
def test_quote_ident_rejects_unusable_names(dialect: Dialect) -> None:
    with pytest.raises(ValueError, match="empty"):
        dialect.quote_ident("")
    with pytest.raises(ValueError, match="NUL"):
        dialect.quote_ident("a\x00b")


def test_quote_qualified_skips_missing_parts() -> None:
    assert POSTGRESQL.quote_qualified("public", "Users", "id") == '"public"."Users"."id"'
    assert POSTGRESQL.quote_qualified(None, "t") == '"t"'
    assert MYSQL.quote_qualified("db", "t") == "`db`.`t`"
    with pytest.raises(ValueError, match="at least one"):
        MYSQL.quote_qualified(None, None)


@pytest.mark.parametrize(
    ("dialect", "name", "needs"),
    [
        (POSTGRESQL, "users", False),
        (POSTGRESQL, "_x1$", False),
        (POSTGRESQL, "Users", True),  # unquoted names fold to lower case
        (POSTGRESQL, "select", True),
        (POSTGRESQL, "user", True),
        (POSTGRESQL, "my col", True),
        (POSTGRESQL, "1abc", True),
        (POSTGRESQL, "naïve", True),
        (MYSQL, "Users", False),  # MySQL keeps the case, no folding
        (MYSQL, "order", True),
        (MYSQL, "key", True),
        (MYSQL, "rank", True),
        (MYSQL, "my-col", True),
        (SQLITE, "Users", False),
        (SQLITE, "table", True),
        (SQLITE, "group", True),
        (SQLITE, "a b", True),
    ],
)
def test_needs_quoting(dialect: Dialect, name: str, needs: bool) -> None:
    assert dialect.needs_quoting(name) is needs
    expected = dialect.quote_ident(name) if needs else name
    assert dialect.quote_ident_if_needed(name) == expected


@pytest.mark.parametrize("dialect", all_dialects(), ids=lambda d: d.id.value)
def test_keywords_and_reserved_words_are_consistent(dialect: Dialect) -> None:
    assert dialect.reserved_words <= dialect.keywords
    assert all(word == word.upper() for word in dialect.keywords)
    assert {"SELECT", "FROM", "WHERE", "JOIN"} <= dialect.reserved_words
    names = [f.name for f in dialect.functions]
    assert len(names) == len(set(names))
    assert all(name == name.upper() for name in names)
    assert {"COUNT", "SUM", "COALESCE"} <= set(names)
    assert len(dialect.data_types) == len(set(dialect.data_types))


def test_dialect_exclusive_vocabulary() -> None:
    assert "ILIKE" in POSTGRESQL.keywords
    assert "ILIKE" not in MYSQL.keywords
    assert "AUTO_INCREMENT" in MYSQL.keywords
    assert "AUTO_INCREMENT" not in SQLITE.keywords
    assert "PRAGMA" in SQLITE.keywords
    assert "PRAGMA" not in POSTGRESQL.keywords
    pg, my, lite = ({f.name for f in d.functions} for d in (POSTGRESQL, MYSQL, SQLITE))
    assert "DATE_TRUNC" in pg
    assert "DATE_TRUNC" not in my | lite
    assert {"GROUP_CONCAT"} <= my & lite
    assert "GROUP_CONCAT" not in pg
    assert "IIF" in lite
    assert "IIF" not in pg | my
