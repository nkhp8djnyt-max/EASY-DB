"""SQLite vocabulary."""

from __future__ import annotations

from .common import ANSI_KEYWORDS, COMMON_FUNCTIONS
from .spec import FunctionSpec, build_functions

#: SQLite's official keyword list. Most can be used as identifiers in some positions, but
#: quoting them is always safe.
RESERVED_WORDS: frozenset[str] = frozenset(
    """
    ABORT ACTION ADD AFTER ALL ALTER ALWAYS ANALYZE AND AS ASC ATTACH AUTOINCREMENT BEFORE
    BEGIN BETWEEN BY CASCADE CASE CAST CHECK COLLATE COLUMN COMMIT CONFLICT CONSTRAINT
    CREATE CROSS CURRENT CURRENT_DATE CURRENT_TIME CURRENT_TIMESTAMP DATABASE DEFAULT
    DEFERRABLE DEFERRED DELETE DESC DETACH DISTINCT DO DROP EACH ELSE END ESCAPE EXCEPT
    EXCLUDE EXCLUSIVE EXISTS EXPLAIN FAIL FILTER FIRST FOLLOWING FOR FOREIGN FROM FULL
    GENERATED GLOB GROUP GROUPS HAVING IF IGNORE IMMEDIATE IN INDEX INDEXED INITIALLY INNER
    INSERT INSTEAD INTERSECT INTO IS ISNULL JOIN KEY LAST LEFT LIKE LIMIT MATCH MATERIALIZED
    NATURAL NO NOT NOTHING NOTNULL NULL NULLS OF OFFSET ON OR ORDER OTHERS OUTER OVER
    PARTITION PLAN PRAGMA PRECEDING PRIMARY QUERY RAISE RANGE RECURSIVE REFERENCES REGEXP
    REINDEX RELEASE RENAME REPLACE RESTRICT RETURNING RIGHT ROLLBACK ROW ROWS SAVEPOINT
    SELECT SET TABLE TEMP TEMPORARY THEN TIES TO TRANSACTION TRIGGER UNBOUNDED UNION UNIQUE
    UPDATE USING VACUUM VALUES VIEW VIRTUAL WHEN WHERE WINDOW WITH WITHOUT
    """.split()
)

_EXTRA_KEYWORDS: frozenset[str] = frozenset({"ROWID", "STRICT", "TRUE", "FALSE"})

KEYWORDS: frozenset[str] = ANSI_KEYWORDS | RESERVED_WORDS | _EXTRA_KEYWORDS

_SQLITE_FUNCTIONS: tuple[tuple[str, str, str], ...] = (
    # aggregates
    ("agg", "GROUP_CONCAT", "GROUP_CONCAT(expr [, separator])"),
    ("agg", "TOTAL", "TOTAL(expr)"),
    # strings / blobs
    ("str", "CONCAT", "CONCAT(a, b, ...)"),
    ("str", "CONCAT_WS", "CONCAT_WS(separator, a, b, ...)"),
    ("str", "INSTR", "INSTR(text, substring)"),
    ("str", "SUBSTRING", "SUBSTRING(text, start [, length])"),
    ("str", "CHAR", "CHAR(code, ...)"),
    ("str", "UNICODE", "UNICODE(text)"),
    ("str", "HEX", "HEX(blob)"),
    ("str", "UNHEX", "UNHEX(text)"),
    ("str", "QUOTE", "QUOTE(value)"),
    ("str", "FORMAT", "FORMAT(template, args, ...)"),
    ("str", "PRINTF", "PRINTF(template, args, ...)"),
    ("str", "GLOB", "GLOB(pattern, text)"),
    ("str", "LIKE", "LIKE(pattern, text [, escape])"),
    ("str", "ZEROBLOB", "ZEROBLOB(n)"),
    ("str", "RANDOMBLOB", "RANDOMBLOB(n)"),
    # numeric (the math group needs a build with SQLITE_ENABLE_MATH_FUNCTIONS)
    ("num", "SIGN", "SIGN(n)"),
    ("num", "RANDOM", "RANDOM()"),
    ("num", "CEIL", "CEIL(n)"),
    ("num", "CEILING", "CEILING(n)"),
    ("num", "FLOOR", "FLOOR(n)"),
    ("num", "POWER", "POWER(base, exponent)"),
    ("num", "SQRT", "SQRT(n)"),
    ("num", "MOD", "MOD(a, b)"),
    ("num", "EXP", "EXP(n)"),
    ("num", "LN", "LN(n)"),
    ("num", "LOG", "LOG([base,] n)"),
    ("num", "LOG2", "LOG2(n)"),
    ("num", "LOG10", "LOG10(n)"),
    ("num", "TRUNC", "TRUNC(n)"),
    ("num", "PI", "PI()"),
    # conditional
    ("cond", "IFNULL", "IFNULL(a, b)"),
    ("cond", "IIF", "IIF(condition, then_value, else_value)"),
    ("cond", "LIKELY", "LIKELY(expr)"),
    ("cond", "UNLIKELY", "UNLIKELY(expr)"),
    # date / time (CURRENT_TIMESTAMP etc. are keywords, not functions)
    ("dt", "DATE", "DATE(time_value [, modifier, ...])"),
    ("dt", "TIME", "TIME(time_value [, modifier, ...])"),
    ("dt", "DATETIME", "DATETIME(time_value [, modifier, ...])"),
    ("dt", "JULIANDAY", "JULIANDAY(time_value [, modifier, ...])"),
    ("dt", "UNIXEPOCH", "UNIXEPOCH(time_value [, modifier, ...])"),
    ("dt", "STRFTIME", "STRFTIME(format, time_value [, modifier, ...])"),
    ("dt", "TIMEDIFF", "TIMEDIFF(a, b)"),
    # json (built in since 3.38)
    ("json", "JSON", "JSON(text)"),
    ("json", "JSON_ARRAY", "JSON_ARRAY(a, b, ...)"),
    ("json", "JSON_ARRAY_LENGTH", "JSON_ARRAY_LENGTH(json [, path])"),
    ("json", "JSON_EXTRACT", "JSON_EXTRACT(json, path, ...)"),
    ("json", "JSON_INSERT", "JSON_INSERT(json, path, value, ...)"),
    ("json", "JSON_OBJECT", "JSON_OBJECT(key, value, ...)"),
    ("json", "JSON_PATCH", "JSON_PATCH(target, patch)"),
    ("json", "JSON_QUOTE", "JSON_QUOTE(value)"),
    ("json", "JSON_REMOVE", "JSON_REMOVE(json, path, ...)"),
    ("json", "JSON_REPLACE", "JSON_REPLACE(json, path, value, ...)"),
    ("json", "JSON_SET", "JSON_SET(json, path, value, ...)"),
    ("json", "JSON_TYPE", "JSON_TYPE(json [, path])"),
    ("json", "JSON_VALID", "JSON_VALID(text)"),
    ("json", "JSON_GROUP_ARRAY", "JSON_GROUP_ARRAY(expr)"),
    ("json", "JSON_GROUP_OBJECT", "JSON_GROUP_OBJECT(key, value)"),
    # system
    ("sys", "CHANGES", "CHANGES()"),
    ("sys", "TOTAL_CHANGES", "TOTAL_CHANGES()"),
    ("sys", "LAST_INSERT_ROWID", "LAST_INSERT_ROWID()"),
    ("sys", "SQLITE_VERSION", "SQLITE_VERSION()"),
    ("sys", "TYPEOF", "TYPEOF(expr)"),
)

#: Multi-argument scalar ``MAX`` / ``MIN`` are covered by the aggregates of the same name.
FUNCTIONS: tuple[FunctionSpec, ...] = build_functions(COMMON_FUNCTIONS + _SQLITE_FUNCTIONS)

DATA_TYPES: tuple[str, ...] = (
    "integer",
    "int",
    "bigint",
    "smallint",
    "text",
    "varchar",
    "char",
    "real",
    "double",
    "float",
    "numeric",
    "decimal",
    "boolean",
    "date",
    "datetime",
    "timestamp",
    "json",
    "blob",
)
