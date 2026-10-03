"""Which keywords make sense where (the vocabulary of ``dialects`` lists *all* keywords)."""

from __future__ import annotations

from ..dialects import Dialect, DialectId

_COMMON_STATEMENTS = (
    "SELECT", "INSERT INTO", "UPDATE", "DELETE FROM", "WITH", "CREATE", "ALTER", "DROP", "TRUNCATE",
    "EXPLAIN", "BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "VALUES", "GRANT", "REVOKE",
)  # fmt: skip
_EXTRA_STATEMENTS: dict[DialectId, tuple[str, ...]] = {
    DialectId.POSTGRESQL: (
        "COPY", "SHOW", "SET", "ANALYZE", "VACUUM", "REINDEX", "TABLE", "DO", "CALL", "COMMENT ON",
        "LOCK", "REFRESH MATERIALIZED VIEW", "RESET", "DISCARD", "LISTEN", "NOTIFY",
    ),
    DialectId.MYSQL: (
        "SHOW", "USE", "DESCRIBE", "REPLACE INTO", "CALL", "SET", "ANALYZE TABLE", "OPTIMIZE TABLE",
        "FLUSH", "LOCK TABLES", "UNLOCK TABLES", "START TRANSACTION", "RENAME TABLE", "HANDLER",
        "DO",
    ),
    DialectId.SQLITE: (
        "PRAGMA", "VACUUM", "ATTACH DATABASE", "DETACH DATABASE", "REPLACE INTO", "ANALYZE",
        "REINDEX", "RELEASE", "BEGIN TRANSACTION",
    ),
}  # fmt: skip

#: Keywords that can start an expression.
OPERAND_KEYWORDS = (
    "NOT", "NULL", "TRUE", "FALSE", "CASE", "EXISTS", "INTERVAL", "CURRENT_DATE",
    "CURRENT_TIMESTAMP", "CURRENT_TIME",
)  # fmt: skip
AFTER_IS = ("NULL", "NOT NULL", "TRUE", "FALSE", "DISTINCT FROM")
VALUE_KEYWORDS = ("NULL", "DEFAULT", "TRUE", "FALSE")
#: Operators written as words, offered after a complete expression.
EXPRESSION_OPS = (
    "AND",
    "OR",
    "NOT",
    "IN",
    "LIKE",
    "ILIKE",
    "BETWEEN",
    "IS NULL",
    "IS NOT NULL",
    "IS",
)
QUERY_STARTERS = ("SELECT", "WITH", "VALUES", "TABLE")

_JOINS = ("INNER JOIN", "LEFT JOIN", "RIGHT JOIN", "FULL JOIN", "CROSS JOIN", "JOIN")
_TAIL = ("GROUP BY", "ORDER BY", "LIMIT", "UNION", "UNION ALL")

#: Continuations after a complete item of a clause, best first.
NEXT_BY_CLAUSE: dict[str, tuple[str, ...]] = {
    "SELECT": ("FROM", "AS", *EXPRESSION_OPS, "INTO"),
    "FROM": (
        "WHERE", *_JOINS, "AS", "GROUP BY", "ORDER BY", "LIMIT", "HAVING", "UNION", "UNION ALL",
    ),
    "JOIN": ("ON", "USING", "AS"),
    "JOIN ON": (*EXPRESSION_OPS, *_JOINS, "WHERE", "GROUP BY", "ORDER BY", "LIMIT"),
    "ON": (*EXPRESSION_OPS,),
    "WHERE": (
        *EXPRESSION_OPS, "GROUP BY", "ORDER BY", "LIMIT", "HAVING", "UNION", "UNION ALL",
        "RETURNING",
    ),
    "GROUP BY": ("HAVING", "ORDER BY", "LIMIT", "UNION", "UNION ALL"),
    "HAVING": (*EXPRESSION_OPS, "ORDER BY", "LIMIT", "UNION"),
    "ORDER BY": ("ASC", "DESC", "NULLS FIRST", "NULLS LAST", "LIMIT", "OFFSET", "UNION"),
    "LIMIT": ("OFFSET",),
    "OFFSET": (),
    "UPDATE": ("SET", "AS"),
    "SET": (*EXPRESSION_OPS, "WHERE", "FROM", "RETURNING"),
    "DELETE FROM": ("WHERE", "USING", "RETURNING"),
    "INSERT INTO": (
        "VALUES", "SELECT", "DEFAULT VALUES", "ON CONFLICT", "ON DUPLICATE KEY UPDATE", "RETURNING",
    ),
    "VALUES": ("ON CONFLICT", "ON DUPLICATE KEY UPDATE", "RETURNING"),
    "ON CONFLICT": ("DO NOTHING", "DO UPDATE SET"),
    "RETURNING": (),
    "USING": (),
}  # fmt: skip

#: What may follow a word that is only the first half of a keyword phrase.
FOLLOWERS: dict[str, tuple[str, ...]] = {
    "GROUP": ("BY",),
    "ORDER": ("BY",),
    "PARTITION": ("BY",),
    "INSERT": ("INTO",),
    "DELETE": ("FROM",),
    "INNER": ("JOIN",),
    "CROSS": ("JOIN",),
    "NATURAL": ("JOIN", "INNER JOIN", "LEFT JOIN", "RIGHT JOIN", "FULL JOIN"),
    "LEFT": ("JOIN", "OUTER JOIN"),
    "RIGHT": ("JOIN", "OUTER JOIN"),
    "FULL": ("JOIN", "OUTER JOIN"),
    "NULLS": ("FIRST", "LAST"),
    "UNION": ("ALL", "DISTINCT", "SELECT"),
    "INTERSECT": ("ALL", "SELECT"),
    "EXCEPT": ("ALL", "SELECT"),
}

CREATE_OBJECTS = (
    "TABLE", "INDEX", "UNIQUE INDEX", "VIEW", "TRIGGER", "SCHEMA", "TEMPORARY TABLE",
    "MATERIALIZED VIEW", "SEQUENCE", "OR REPLACE VIEW", "DATABASE",
)  # fmt: skip
DROP_OBJECTS = (
    "TABLE",
    "INDEX",
    "VIEW",
    "TRIGGER",
    "SCHEMA",
    "MATERIALIZED VIEW",
    "SEQUENCE",
    "DATABASE",
)
ALTER_ACTIONS = (
    "ADD COLUMN", "DROP COLUMN", "ALTER COLUMN", "RENAME TO", "RENAME COLUMN", "ADD CONSTRAINT",
    "DROP CONSTRAINT", "ADD PRIMARY KEY", "ADD FOREIGN KEY",
)  # fmt: skip
COLUMN_CONSTRAINTS = ("PRIMARY KEY", "NOT NULL", "NULL", "DEFAULT", "UNIQUE", "REFERENCES", "CHECK")

_NOT_IN = {
    "ILIKE": {DialectId.MYSQL, DialectId.SQLITE},
    "FULL JOIN": {DialectId.MYSQL},
    "NULLS FIRST": {DialectId.MYSQL},
    "NULLS LAST": {DialectId.MYSQL},
    "RETURNING": {DialectId.MYSQL},
    "ON CONFLICT": {DialectId.MYSQL},
    "DO NOTHING": {DialectId.MYSQL},
    "DO UPDATE SET": {DialectId.MYSQL},
    "ON DUPLICATE KEY UPDATE": {DialectId.POSTGRESQL, DialectId.SQLITE},
    "DISTINCT FROM": {DialectId.MYSQL},
    "INTO": {DialectId.SQLITE, DialectId.MYSQL},
    "MATERIALIZED VIEW": {DialectId.MYSQL, DialectId.SQLITE},
    "SEQUENCE": {DialectId.MYSQL, DialectId.SQLITE},
    "SCHEMA": {DialectId.SQLITE},
    "DATABASE": {DialectId.POSTGRESQL, DialectId.SQLITE},
    "OR REPLACE VIEW": {DialectId.SQLITE},
    "USING": set(),
}  # fmt: skip


def allowed(words: tuple[str, ...], dialect: Dialect) -> tuple[str, ...]:
    """``words`` without the ones ``dialect`` does not have."""
    return tuple(w for w in words if dialect.id not in _NOT_IN.get(w, set()))


def statement_keywords(dialect: Dialect) -> tuple[str, ...]:
    extra = _EXTRA_STATEMENTS.get(dialect.id, ())
    seen: set[str] = set()
    out = []
    for word in (*_COMMON_STATEMENTS, *extra):
        if word not in seen:
            seen.add(word)
            out.append(word)
    return tuple(out)
