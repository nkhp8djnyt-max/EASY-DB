"""Vocabulary shared by PostgreSQL, MySQL/MariaDB and SQLite."""

from __future__ import annotations

#: Words every dialect understands; used for highlighting and statement-start suggestions.
ANSI_KEYWORDS: frozenset[str] = frozenset(
    """
    ADD ALL ALTER AND AS ASC BEGIN BETWEEN BY CASCADE CASE CAST CHECK COLLATE COLUMN COMMIT
    CONSTRAINT CREATE CROSS CURRENT DEFAULT DELETE DESC DISTINCT DROP ELSE END ESCAPE EXCEPT
    EXISTS EXPLAIN FALSE FETCH FILTER FIRST FOLLOWING FOREIGN FROM FULL GROUP HAVING IF IN
    INDEX INNER INSERT INTERSECT INTO IS JOIN KEY LEFT LIKE LIMIT NATURAL NEXT NOT NULL
    OFFSET ON ONLY OR ORDER OUTER OVER PARTITION PRECEDING PRIMARY RANGE RECURSIVE
    REFERENCES RELEASE RENAME REPLACE RESTRICT RIGHT ROLLBACK ROW ROWS SAVEPOINT SELECT SET
    TABLE TEMP TEMPORARY THEN TO TRANSACTION TRIGGER TRUE UNBOUNDED UNION UNIQUE UPDATE
    USING VALUES VIEW WHEN WHERE WINDOW WITH
    """.split()
)

#: Statement-initial keywords for the "start of statement" autocomplete context.
STATEMENT_STARTERS: tuple[str, ...] = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "WITH",
    "CREATE",
    "ALTER",
    "DROP",
    "EXPLAIN",
    "BEGIN",
    "COMMIT",
    "ROLLBACK",
)

#: ``(category, NAME, signature)`` rows of functions that behave alike in all three dialects.
COMMON_FUNCTIONS: tuple[tuple[str, str, str], ...] = (
    ("agg", "COUNT", "COUNT(expr)"),
    ("agg", "SUM", "SUM(expr)"),
    ("agg", "AVG", "AVG(expr)"),
    ("agg", "MIN", "MIN(expr)"),
    ("agg", "MAX", "MAX(expr)"),
    ("win", "ROW_NUMBER", "ROW_NUMBER() OVER (...)"),
    ("win", "RANK", "RANK() OVER (...)"),
    ("win", "DENSE_RANK", "DENSE_RANK() OVER (...)"),
    ("win", "PERCENT_RANK", "PERCENT_RANK() OVER (...)"),
    ("win", "CUME_DIST", "CUME_DIST() OVER (...)"),
    ("win", "NTILE", "NTILE(n) OVER (...)"),
    ("win", "LAG", "LAG(expr [, offset [, default]]) OVER (...)"),
    ("win", "LEAD", "LEAD(expr [, offset [, default]]) OVER (...)"),
    ("win", "FIRST_VALUE", "FIRST_VALUE(expr) OVER (...)"),
    ("win", "LAST_VALUE", "LAST_VALUE(expr) OVER (...)"),
    ("win", "NTH_VALUE", "NTH_VALUE(expr, n) OVER (...)"),
    ("cond", "COALESCE", "COALESCE(a, b, ...)"),
    ("cond", "NULLIF", "NULLIF(a, b)"),
    ("str", "LOWER", "LOWER(text)"),
    ("str", "UPPER", "UPPER(text)"),
    ("str", "LENGTH", "LENGTH(text)"),
    ("str", "TRIM", "TRIM([chars FROM] text)"),
    ("str", "LTRIM", "LTRIM(text [, chars])"),
    ("str", "RTRIM", "RTRIM(text [, chars])"),
    ("str", "REPLACE", "REPLACE(text, from, to)"),
    ("str", "SUBSTR", "SUBSTR(text, start [, length])"),
    ("num", "ABS", "ABS(n)"),
    ("num", "ROUND", "ROUND(n [, digits])"),
)
