"""Short abbreviations that expand to a piece of SQL (``sel`` -> ``SELECT * FROM``)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..dialects import Dialect, DialectId


class Where(StrEnum):
    STATEMENT = "statement"  # at the start of a statement
    TABLE_DONE = "table_done"  # after a table in FROM / JOIN: more joins
    CLAUSE = "clause"  # after any complete expression: the next clause


@dataclass(frozen=True, slots=True)
class Snippet:
    trigger: str
    label: str
    #: Text to insert; ``$0`` marks where the cursor ends up.
    body: str
    where: Where
    description: str = ""
    dialects: frozenset[DialectId] | None = None


CURSOR = "$0"

SNIPPETS: tuple[Snippet, ...] = (
    Snippet("sel", "SELECT * FROM", "SELECT * FROM $0", Where.STATEMENT, "all columns of a table"),
    Snippet(
        "selc", "SELECT COUNT(*) FROM", "SELECT COUNT(*) FROM $0", Where.STATEMENT, "count the rows"
    ),
    Snippet(
        "seld",
        "SELECT DISTINCT … FROM",
        "SELECT DISTINCT $0 FROM ",
        Where.STATEMENT,
        "distinct values",
    ),
    Snippet(
        "ins",
        "INSERT INTO … VALUES",
        "INSERT INTO $0 () VALUES ()",
        Where.STATEMENT,
        "insert a row",
    ),
    Snippet("upd", "UPDATE … SET … WHERE", "UPDATE $0 SET  WHERE ", Where.STATEMENT, "change rows"),
    Snippet("del", "DELETE FROM … WHERE", "DELETE FROM $0 WHERE ", Where.STATEMENT, "delete rows"),
    Snippet(
        "cte",
        "WITH … AS (…) SELECT",
        "WITH $0 AS (\n  SELECT \n)\nSELECT * FROM ",
        Where.STATEMENT,
        "common table expression",
    ),
    Snippet("ij", "INNER JOIN … ON …", "INNER JOIN $0 ON ", Where.TABLE_DONE, "rows with a match"),
    Snippet("lj", "LEFT JOIN … ON …", "LEFT JOIN $0 ON ", Where.TABLE_DONE, "keep every left row"),
    Snippet(
        "rj", "RIGHT JOIN … ON …", "RIGHT JOIN $0 ON ", Where.TABLE_DONE, "keep every right row"
    ),
    Snippet("cj", "CROSS JOIN …", "CROSS JOIN $0", Where.TABLE_DONE, "every combination"),
    Snippet("ob", "ORDER BY …", "ORDER BY $0", Where.CLAUSE, "sort the rows"),
    Snippet("gb", "GROUP BY …", "GROUP BY $0", Where.CLAUSE, "group the rows"),
    Snippet("lim", "LIMIT 100", "LIMIT 100", Where.CLAUSE, "first 100 rows"),
)


def snippets_for(where: Where, dialect: Dialect) -> tuple[Snippet, ...]:
    return tuple(
        s for s in SNIPPETS if s.where is where and (s.dialects is None or dialect.id in s.dialects)
    )
