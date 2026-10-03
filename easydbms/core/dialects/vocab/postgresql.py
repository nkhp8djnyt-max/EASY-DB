"""PostgreSQL vocabulary."""

from __future__ import annotations

from .common import ANSI_KEYWORDS, COMMON_FUNCTIONS
from .spec import FunctionSpec, build_functions

#: Reserved words of PostgreSQL, including "reserved (can be function or type)": must be quoted
#: when used as identifiers.
RESERVED_WORDS: frozenset[str] = frozenset(
    """
    ALL ANALYSE ANALYZE AND ANY ARRAY AS ASC ASYMMETRIC AUTHORIZATION BINARY BOTH CASE CAST
    CHECK COLLATE COLLATION COLUMN CONCURRENTLY CONSTRAINT CREATE CROSS CURRENT_CATALOG
    CURRENT_DATE CURRENT_ROLE CURRENT_SCHEMA CURRENT_TIME CURRENT_TIMESTAMP CURRENT_USER
    DEFAULT DEFERRABLE DESC DISTINCT DO ELSE END EXCEPT FALSE FETCH FOR FOREIGN FREEZE FROM
    FULL GRANT GROUP HAVING ILIKE IN INITIALLY INNER INTERSECT INTO IS ISNULL JOIN LATERAL
    LEADING LEFT LIKE LIMIT LOCALTIME LOCALTIMESTAMP NATURAL NOT NOTNULL NULL OFFSET ON ONLY
    OR ORDER OUTER OVERLAPS PLACING PRIMARY REFERENCES RETURNING RIGHT SELECT SESSION_USER
    SIMILAR SOME SYMMETRIC SYSTEM_USER TABLE TABLESAMPLE THEN TO TRAILING TRUE UNION UNIQUE
    USER USING VARIADIC VERBOSE WHEN WHERE WINDOW WITH
    """.split()
)

_EXTRA_KEYWORDS: frozenset[str] = frozenset(
    """
    ALWAYS CLUSTER COMMENT CONFLICT COPY CURSOR DEALLOCATE DECLARE DEFINER DISCARD DOMAIN
    ENUM EXECUTE EXTENSION FUNCTION GENERATED IDENTITY IMMUTABLE INCLUDE INHERITS INVOKER
    LANGUAGE LISTEN MATERIALIZED NOTHING NOTIFY NULLS OWNER POLICY PREPARE PROCEDURE REINDEX
    RETURNS ROLE RULE SCHEMA SECURITY SEQUENCE STABLE STORED TABLESPACE TRUNCATE TYPE
    UNLOGGED VACUUM VOLATILE
    """.split()
)

KEYWORDS: frozenset[str] = ANSI_KEYWORDS | RESERVED_WORDS | _EXTRA_KEYWORDS

_POSTGRES_FUNCTIONS: tuple[tuple[str, str, str], ...] = (
    # aggregates
    ("agg", "STRING_AGG", "STRING_AGG(expr, delimiter [ORDER BY ...])"),
    ("agg", "ARRAY_AGG", "ARRAY_AGG(expr [ORDER BY ...])"),
    ("agg", "BOOL_AND", "BOOL_AND(bool_expr)"),
    ("agg", "BOOL_OR", "BOOL_OR(bool_expr)"),
    ("agg", "JSON_AGG", "JSON_AGG(expr)"),
    ("agg", "JSONB_AGG", "JSONB_AGG(expr)"),
    ("agg", "JSON_OBJECT_AGG", "JSON_OBJECT_AGG(key, value)"),
    ("agg", "JSONB_OBJECT_AGG", "JSONB_OBJECT_AGG(key, value)"),
    ("agg", "STDDEV", "STDDEV(expr)"),
    ("agg", "VARIANCE", "VARIANCE(expr)"),
    ("agg", "PERCENTILE_CONT", "PERCENTILE_CONT(fraction) WITHIN GROUP (ORDER BY ...)"),
    ("agg", "PERCENTILE_DISC", "PERCENTILE_DISC(fraction) WITHIN GROUP (ORDER BY ...)"),
    # strings
    ("str", "CONCAT", "CONCAT(a, b, ...)"),
    ("str", "CONCAT_WS", "CONCAT_WS(separator, a, b, ...)"),
    ("str", "LEFT", "LEFT(text, n)"),
    ("str", "RIGHT", "RIGHT(text, n)"),
    ("str", "LPAD", "LPAD(text, length [, fill])"),
    ("str", "RPAD", "RPAD(text, length [, fill])"),
    ("str", "BTRIM", "BTRIM(text [, chars])"),
    ("str", "POSITION", "POSITION(substring IN text)"),
    ("str", "STRPOS", "STRPOS(text, substring)"),
    ("str", "SUBSTRING", "SUBSTRING(text FROM start [FOR length])"),
    ("str", "SPLIT_PART", "SPLIT_PART(text, delimiter, n)"),
    ("str", "REGEXP_REPLACE", "REGEXP_REPLACE(text, pattern, replacement [, flags])"),
    ("str", "REGEXP_MATCH", "REGEXP_MATCH(text, pattern [, flags])"),
    ("str", "REGEXP_MATCHES", "REGEXP_MATCHES(text, pattern [, flags])"),
    ("str", "REGEXP_SPLIT_TO_ARRAY", "REGEXP_SPLIT_TO_ARRAY(text, pattern [, flags])"),
    ("str", "REGEXP_SPLIT_TO_TABLE", "REGEXP_SPLIT_TO_TABLE(text, pattern [, flags])"),
    ("str", "INITCAP", "INITCAP(text)"),
    ("str", "REPEAT", "REPEAT(text, n)"),
    ("str", "REVERSE", "REVERSE(text)"),
    ("str", "TRANSLATE", "TRANSLATE(text, from, to)"),
    ("str", "CHR", "CHR(code)"),
    ("str", "ASCII", "ASCII(text)"),
    ("str", "MD5", "MD5(text)"),
    ("str", "ENCODE", "ENCODE(bytea, format)"),
    ("str", "DECODE", "DECODE(text, format)"),
    ("str", "FORMAT", "FORMAT(template, args, ...)"),
    ("str", "QUOTE_IDENT", "QUOTE_IDENT(text)"),
    ("str", "QUOTE_LITERAL", "QUOTE_LITERAL(text)"),
    ("str", "TO_CHAR", "TO_CHAR(value, format)"),
    ("str", "TO_NUMBER", "TO_NUMBER(text, format)"),
    ("str", "STARTS_WITH", "STARTS_WITH(text, prefix)"),
    ("str", "OVERLAY", "OVERLAY(text PLACING text FROM start [FOR length])"),
    ("str", "CHAR_LENGTH", "CHAR_LENGTH(text)"),
    ("str", "OCTET_LENGTH", "OCTET_LENGTH(text)"),
    ("str", "BIT_LENGTH", "BIT_LENGTH(text)"),
    ("str", "STRING_TO_ARRAY", "STRING_TO_ARRAY(text, delimiter)"),
    ("str", "ARRAY_TO_STRING", "ARRAY_TO_STRING(array, delimiter [, null_string])"),
    # numeric
    ("num", "CEIL", "CEIL(n)"),
    ("num", "CEILING", "CEILING(n)"),
    ("num", "FLOOR", "FLOOR(n)"),
    ("num", "POWER", "POWER(base, exponent)"),
    ("num", "SQRT", "SQRT(n)"),
    ("num", "MOD", "MOD(a, b)"),
    ("num", "DIV", "DIV(a, b)"),
    ("num", "SIGN", "SIGN(n)"),
    ("num", "TRUNC", "TRUNC(n [, digits])"),
    ("num", "EXP", "EXP(n)"),
    ("num", "LN", "LN(n)"),
    ("num", "LOG", "LOG([base,] n)"),
    ("num", "RANDOM", "RANDOM()"),
    ("num", "PI", "PI()"),
    ("num", "GENERATE_SERIES", "GENERATE_SERIES(start, stop [, step])"),
    # conditional
    ("cond", "GREATEST", "GREATEST(a, b, ...)"),
    ("cond", "LEAST", "LEAST(a, b, ...)"),
    # date / time
    ("dt", "NOW", "NOW()"),
    ("dt", "CLOCK_TIMESTAMP", "CLOCK_TIMESTAMP()"),
    ("dt", "STATEMENT_TIMESTAMP", "STATEMENT_TIMESTAMP()"),
    ("dt", "DATE_TRUNC", "DATE_TRUNC(field, timestamp)"),
    ("dt", "DATE_PART", "DATE_PART(field, timestamp)"),
    ("dt", "EXTRACT", "EXTRACT(field FROM timestamp)"),
    ("dt", "AGE", "AGE([newer,] older)"),
    ("dt", "TO_TIMESTAMP", "TO_TIMESTAMP(text, format)"),
    ("dt", "TO_DATE", "TO_DATE(text, format)"),
    ("dt", "MAKE_DATE", "MAKE_DATE(year, month, day)"),
    ("dt", "MAKE_TIMESTAMP", "MAKE_TIMESTAMP(y, mo, d, h, mi, s)"),
    ("dt", "MAKE_INTERVAL", "MAKE_INTERVAL(years, months, weeks, days, hours, mins, secs)"),
    ("dt", "ISFINITE", "ISFINITE(date_or_timestamp)"),
    # json
    ("json", "JSON_BUILD_OBJECT", "JSON_BUILD_OBJECT(key, value, ...)"),
    ("json", "JSONB_BUILD_OBJECT", "JSONB_BUILD_OBJECT(key, value, ...)"),
    ("json", "JSON_BUILD_ARRAY", "JSON_BUILD_ARRAY(a, b, ...)"),
    ("json", "JSONB_BUILD_ARRAY", "JSONB_BUILD_ARRAY(a, b, ...)"),
    ("json", "JSONB_SET", "JSONB_SET(target, path, new_value [, create_missing])"),
    ("json", "JSONB_INSERT", "JSONB_INSERT(target, path, new_value [, insert_after])"),
    ("json", "JSONB_PRETTY", "JSONB_PRETTY(jsonb)"),
    ("json", "JSONB_STRIP_NULLS", "JSONB_STRIP_NULLS(jsonb)"),
    ("json", "JSON_EXTRACT_PATH", "JSON_EXTRACT_PATH(json, path, ...)"),
    ("json", "JSON_EXTRACT_PATH_TEXT", "JSON_EXTRACT_PATH_TEXT(json, path, ...)"),
    ("json", "JSONB_EXTRACT_PATH", "JSONB_EXTRACT_PATH(jsonb, path, ...)"),
    ("json", "JSONB_EXTRACT_PATH_TEXT", "JSONB_EXTRACT_PATH_TEXT(jsonb, path, ...)"),
    ("json", "JSON_ARRAY_ELEMENTS", "JSON_ARRAY_ELEMENTS(json)"),
    ("json", "JSONB_ARRAY_ELEMENTS", "JSONB_ARRAY_ELEMENTS(jsonb)"),
    ("json", "JSONB_ARRAY_ELEMENTS_TEXT", "JSONB_ARRAY_ELEMENTS_TEXT(jsonb)"),
    ("json", "JSONB_ARRAY_LENGTH", "JSONB_ARRAY_LENGTH(jsonb)"),
    ("json", "JSON_EACH", "JSON_EACH(json)"),
    ("json", "JSONB_EACH", "JSONB_EACH(jsonb)"),
    ("json", "JSONB_EACH_TEXT", "JSONB_EACH_TEXT(jsonb)"),
    ("json", "JSONB_OBJECT_KEYS", "JSONB_OBJECT_KEYS(jsonb)"),
    ("json", "JSON_TYPEOF", "JSON_TYPEOF(json)"),
    ("json", "JSONB_TYPEOF", "JSONB_TYPEOF(jsonb)"),
    ("json", "TO_JSON", "TO_JSON(value)"),
    ("json", "TO_JSONB", "TO_JSONB(value)"),
    ("json", "ROW_TO_JSON", "ROW_TO_JSON(record)"),
    ("json", "JSONB_PATH_QUERY", "JSONB_PATH_QUERY(target, path)"),
    # arrays
    ("arr", "ARRAY_LENGTH", "ARRAY_LENGTH(array, dimension)"),
    ("arr", "ARRAY_APPEND", "ARRAY_APPEND(array, element)"),
    ("arr", "ARRAY_PREPEND", "ARRAY_PREPEND(element, array)"),
    ("arr", "ARRAY_CAT", "ARRAY_CAT(a, b)"),
    ("arr", "ARRAY_REMOVE", "ARRAY_REMOVE(array, element)"),
    ("arr", "ARRAY_POSITION", "ARRAY_POSITION(array, element)"),
    ("arr", "ARRAY_DIMS", "ARRAY_DIMS(array)"),
    ("arr", "ARRAY_UPPER", "ARRAY_UPPER(array, dimension)"),
    ("arr", "ARRAY_LOWER", "ARRAY_LOWER(array, dimension)"),
    ("arr", "CARDINALITY", "CARDINALITY(array)"),
    ("arr", "UNNEST", "UNNEST(array)"),
    # system
    ("sys", "VERSION", "VERSION()"),
    ("sys", "CURRENT_DATABASE", "CURRENT_DATABASE()"),
    ("sys", "CURRENT_SCHEMA", "CURRENT_SCHEMA()"),
    ("sys", "CURRENT_SETTING", "CURRENT_SETTING(name)"),
    ("sys", "SET_CONFIG", "SET_CONFIG(name, value, is_local)"),
    ("sys", "PG_TYPEOF", "PG_TYPEOF(any)"),
    ("sys", "PG_SIZE_PRETTY", "PG_SIZE_PRETTY(bytes)"),
    ("sys", "PG_TOTAL_RELATION_SIZE", "PG_TOTAL_RELATION_SIZE(regclass)"),
    ("sys", "PG_RELATION_SIZE", "PG_RELATION_SIZE(regclass)"),
    ("sys", "PG_TABLE_SIZE", "PG_TABLE_SIZE(regclass)"),
    ("sys", "PG_DATABASE_SIZE", "PG_DATABASE_SIZE(name)"),
    ("sys", "PG_BACKEND_PID", "PG_BACKEND_PID()"),
    ("sys", "PG_CANCEL_BACKEND", "PG_CANCEL_BACKEND(pid)"),
    ("sys", "PG_TERMINATE_BACKEND", "PG_TERMINATE_BACKEND(pid)"),
    ("sys", "GEN_RANDOM_UUID", "GEN_RANDOM_UUID()"),
    ("sys", "NEXTVAL", "NEXTVAL(regclass)"),
    ("sys", "CURRVAL", "CURRVAL(regclass)"),
    ("sys", "SETVAL", "SETVAL(regclass, value [, is_called])"),
    ("sys", "LASTVAL", "LASTVAL()"),
)

FUNCTIONS: tuple[FunctionSpec, ...] = build_functions(COMMON_FUNCTIONS + _POSTGRES_FUNCTIONS)

DATA_TYPES: tuple[str, ...] = (
    "smallint",
    "integer",
    "bigint",
    "smallserial",
    "serial",
    "bigserial",
    "numeric",
    "decimal",
    "real",
    "double precision",
    "money",
    "boolean",
    "char",
    "varchar",
    "text",
    "bytea",
    "date",
    "time",
    "timetz",
    "timestamp",
    "timestamptz",
    "interval",
    "uuid",
    "json",
    "jsonb",
    "xml",
    "inet",
    "cidr",
    "macaddr",
    "bit",
    "varbit",
    "tsvector",
    "tsquery",
    "point",
    "line",
    "box",
    "path",
    "polygon",
    "circle",
    "int4range",
    "int8range",
    "numrange",
    "tsrange",
    "tstzrange",
    "daterange",
    "oid",
)
