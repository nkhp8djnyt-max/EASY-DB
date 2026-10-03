from __future__ import annotations

import pytest

from easydbms.core.browse import FilterError, page_sql, validate_filter
from easydbms.core.dialects import MYSQL, POSTGRESQL, SQLITE, Dialect
from easydbms.core.schema import Column, Table, introspect

from .conftest import Shop

DIALECTS = [POSTGRESQL, MYSQL, SQLITE]


def sample() -> Table:
    return Table(
        "public",
        "book",
        columns=(Column("id", "integer"), Column("title", "text"), Column("year", "integer")),
        primary_key=("id",),
    )


@pytest.mark.parametrize("dialect", DIALECTS, ids=lambda d: d.id.value)
def test_page_sql_shape(dialect: Dialect) -> None:
    q = dialect.quote_ident
    assert page_sql(dialect, sample(), limit=101) == (
        f"SELECT * FROM {q('public')}.{q('book')} ORDER BY {q('id')} ASC LIMIT 101"
    )
    sql = page_sql(
        dialect, sample(), limit=50, offset=100, order_by=[("title", True)], where="year > 2000"
    )
    assert f"ORDER BY {q('title')} DESC, {q('id')} ASC" in sql
    assert sql.endswith("LIMIT 50 OFFSET 100")
    assert "WHERE (year > 2000\n)" in sql


def test_primary_key_is_not_repeated_when_already_sorted_on() -> None:
    sql = page_sql(POSTGRESQL, sample(), limit=10, order_by=[("id", True)])
    assert sql.count('"id"') == 1
    assert 'ORDER BY "id" DESC' in sql


def test_no_order_without_a_primary_key() -> None:
    table = Table("main", "t", columns=(Column("a", "text"),))
    assert "ORDER BY" not in page_sql(SQLITE, table, limit=5)


def test_unknown_sort_column_and_bad_limits_are_rejected() -> None:
    with pytest.raises(ValueError, match="no column"):
        page_sql(POSTGRESQL, sample(), limit=10, order_by=[("nope", False)])
    with pytest.raises(ValueError, match="limit"):
        page_sql(POSTGRESQL, sample(), limit=0)
    with pytest.raises(ValueError, match="offset"):
        page_sql(POSTGRESQL, sample(), limit=1, offset=-1)


@pytest.mark.parametrize("dialect", DIALECTS, ids=lambda d: d.id.value)
@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "year > 2000",
        "title LIKE '%a;b%'",
        "title = 'it''s' AND year IS NOT NULL",
        "year > 1 -- recent; ones",
        "(year = 1 OR year = 2) AND title <> ''",
    ],
)
def test_valid_filters(dialect: Dialect, text: str) -> None:
    validate_filter(text, dialect)
    page_sql(dialect, sample(), limit=1, where=text)


@pytest.mark.parametrize("dialect", DIALECTS, ids=lambda d: d.id.value)
@pytest.mark.parametrize(
    "text",
    [
        "1=1; DROP TABLE book",
        "1) ; DELETE FROM book; --",
        "year > 1;",
        "year >",
        "(((",
    ],
)
def test_invalid_filters(dialect: Dialect, text: str) -> None:
    with pytest.raises(FilterError):
        validate_filter(text, dialect)
    with pytest.raises(FilterError):
        page_sql(dialect, sample(), limit=1, where=text)


def test_pages_run_on_every_engine_and_do_not_overlap(shop: Shop) -> None:
    client = shop.client
    book = shop.target.quote(shop.name("book"))
    author = shop.target.quote(shop.name("author"))
    client.execute(f"INSERT INTO {author} (id, name) VALUES (1, 'A')")
    for i in range(1, 8):
        client.execute(
            f"INSERT INTO {book} (id, author_id, title, published) "
            f"VALUES ({i}, 1, 't{i % 3}', {2000 + i})"
        )
    schema = introspect(client)
    table = schema.find(shop.name("book"))
    assert table is not None
    dialect = client.dialect

    first = client.execute(page_sql(dialect, table, limit=4)).rows
    second = client.execute(page_sql(dialect, table, limit=4, offset=4)).rows
    assert [r[0] for r in first] == [1, 2, 3, 4]
    assert [r[0] for r in second] == [5, 6, 7]

    by_title = client.execute(page_sql(dialect, table, limit=10, order_by=[("title", False)])).rows
    assert [r[2] for r in by_title] == sorted(r[2] for r in by_title)
    # equal titles keep primary-key order, so paging through them is stable
    assert [r[0] for r in by_title if r[2] == "t0"] == sorted(
        r[0] for r in by_title if r[2] == "t0"
    )

    filtered = client.execute(
        page_sql(dialect, table, limit=10, where="published >= 2005 AND title LIKE 't%'")
    ).rows
    assert [r[0] for r in filtered] == [5, 6, 7]
