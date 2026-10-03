"""MySQL / MariaDB vocabulary."""

from __future__ import annotations

from .common import ANSI_KEYWORDS, COMMON_FUNCTIONS
from .spec import FunctionSpec, build_functions

#: Reserved words of MySQL 8 (a superset of MariaDB's for practical purposes): must be quoted
#: with backticks when used as identifiers. Over-quoting is harmless, under-quoting is not.
RESERVED_WORDS: frozenset[str] = frozenset(
    """
    ACCESSIBLE ADD ALL ALTER ANALYZE AND AS ASC ASENSITIVE BEFORE BETWEEN BIGINT BINARY BLOB
    BOTH BY CALL CASCADE CASE CHANGE CHAR CHARACTER CHECK COLLATE COLUMN CONDITION
    CONSTRAINT CONTINUE CONVERT CREATE CROSS CUBE CUME_DIST CURRENT_DATE CURRENT_TIME
    CURRENT_TIMESTAMP CURRENT_USER CURSOR DATABASE DATABASES DAY_HOUR DAY_MICROSECOND
    DAY_MINUTE DAY_SECOND DEC DECIMAL DECLARE DEFAULT DELAYED DELETE DENSE_RANK DESC
    DESCRIBE DETERMINISTIC DISTINCT DISTINCTROW DIV DOUBLE DROP DUAL EACH ELSE ELSEIF EMPTY
    ENCLOSED ESCAPED EXCEPT EXISTS EXIT EXPLAIN FALSE FETCH FIRST_VALUE FLOAT FLOAT4 FLOAT8
    FOR FORCE FOREIGN FROM FULLTEXT FUNCTION GENERATED GET GRANT GROUP GROUPING GROUPS
    HAVING HIGH_PRIORITY HOUR_MICROSECOND HOUR_MINUTE HOUR_SECOND IF IGNORE IN INDEX INFILE
    INNER INOUT INSENSITIVE INSERT INT INT1 INT2 INT3 INT4 INT8 INTEGER INTERSECT INTERVAL
    INTO IO_AFTER_GTIDS IO_BEFORE_GTIDS IS ITERATE JOIN JSON_TABLE KEY KEYS KILL LAG
    LAST_VALUE LATERAL LEAD LEADING LEAVE LEFT LIKE LIMIT LINEAR LINES LOAD LOCALTIME
    LOCALTIMESTAMP LOCK LONG LONGBLOB LONGTEXT LOOP LOW_PRIORITY MASTER_BIND
    MASTER_SSL_VERIFY_SERVER_CERT MATCH MAXVALUE MEDIUMBLOB MEDIUMINT MEDIUMTEXT MIDDLEINT
    MINUTE_MICROSECOND MINUTE_SECOND MOD MODIFIES NATURAL NOT NO_WRITE_TO_BINLOG NTH_VALUE
    NTILE NULL NUMERIC OF ON OPTIMIZE OPTIMIZER_COSTS OPTION OPTIONALLY OR ORDER OUT OUTER
    OUTFILE OVER PARTITION PERCENT_RANK PRECISION PRIMARY PROCEDURE PURGE RANGE RANK READ
    READS READ_WRITE REAL RECURSIVE REFERENCES REGEXP RELEASE RENAME REPEAT REPLACE REQUIRE
    RESIGNAL RESTRICT RETURN REVOKE RIGHT RLIKE ROW ROWS ROW_NUMBER SCHEMA SCHEMAS
    SECOND_MICROSECOND SELECT SENSITIVE SEPARATOR SET SHOW SIGNAL SMALLINT SPATIAL SPECIFIC
    SQL SQLEXCEPTION SQLSTATE SQLWARNING SQL_BIG_RESULT SQL_CALC_FOUND_ROWS SQL_SMALL_RESULT
    SSL STARTING STORED STRAIGHT_JOIN SYSTEM TABLE TERMINATED THEN TINYBLOB TINYINT TINYTEXT
    TO TRAILING TRIGGER TRUE UNDO UNION UNIQUE UNLOCK UNSIGNED UPDATE USAGE USE USING
    UTC_DATE UTC_TIME UTC_TIMESTAMP VALUES VARBINARY VARCHAR VARCHARACTER VARYING VIRTUAL
    WHEN WHERE WHILE WINDOW WITH WRITE XOR YEAR_MONTH ZEROFILL
    """.split()
)

_EXTRA_KEYWORDS: frozenset[str] = frozenset(
    """
    AUTO_INCREMENT CHARSET COMMENT DELIMITER DUPLICATE ENGINE ENUM EVENT PARTITIONS TABLES
    TRUNCATE
    """.split()
)

KEYWORDS: frozenset[str] = ANSI_KEYWORDS | RESERVED_WORDS | _EXTRA_KEYWORDS

_MYSQL_FUNCTIONS: tuple[tuple[str, str, str], ...] = (
    # aggregates
    ("agg", "GROUP_CONCAT", "GROUP_CONCAT(expr [ORDER BY ...] [SEPARATOR sep])"),
    ("agg", "JSON_ARRAYAGG", "JSON_ARRAYAGG(expr)"),
    ("agg", "JSON_OBJECTAGG", "JSON_OBJECTAGG(key, value)"),
    ("agg", "STD", "STD(expr)"),
    ("agg", "STDDEV", "STDDEV(expr)"),
    ("agg", "STDDEV_POP", "STDDEV_POP(expr)"),
    ("agg", "STDDEV_SAMP", "STDDEV_SAMP(expr)"),
    ("agg", "VAR_POP", "VAR_POP(expr)"),
    ("agg", "VAR_SAMP", "VAR_SAMP(expr)"),
    ("agg", "VARIANCE", "VARIANCE(expr)"),
    ("agg", "BIT_AND", "BIT_AND(expr)"),
    ("agg", "BIT_OR", "BIT_OR(expr)"),
    ("agg", "BIT_XOR", "BIT_XOR(expr)"),
    # strings
    ("str", "CONCAT", "CONCAT(a, b, ...)"),
    ("str", "CONCAT_WS", "CONCAT_WS(separator, a, b, ...)"),
    ("str", "LEFT", "LEFT(text, n)"),
    ("str", "RIGHT", "RIGHT(text, n)"),
    ("str", "LPAD", "LPAD(text, length, fill)"),
    ("str", "RPAD", "RPAD(text, length, fill)"),
    ("str", "SUBSTRING", "SUBSTRING(text, start [, length])"),
    ("str", "SUBSTRING_INDEX", "SUBSTRING_INDEX(text, delimiter, count)"),
    ("str", "MID", "MID(text, start, length)"),
    ("str", "LOCATE", "LOCATE(substring, text [, start])"),
    ("str", "INSTR", "INSTR(text, substring)"),
    ("str", "POSITION", "POSITION(substring IN text)"),
    ("str", "CHAR_LENGTH", "CHAR_LENGTH(text)"),
    ("str", "CHARACTER_LENGTH", "CHARACTER_LENGTH(text)"),
    ("str", "REVERSE", "REVERSE(text)"),
    ("str", "REPEAT", "REPEAT(text, n)"),
    ("str", "SPACE", "SPACE(n)"),
    ("str", "FORMAT", "FORMAT(number, decimals)"),
    ("str", "INSERT", "INSERT(text, position, length, new_text)"),
    ("str", "ELT", "ELT(n, a, b, ...)"),
    ("str", "FIELD", "FIELD(value, a, b, ...)"),
    ("str", "FIND_IN_SET", "FIND_IN_SET(value, comma_list)"),
    ("str", "HEX", "HEX(value)"),
    ("str", "UNHEX", "UNHEX(text)"),
    ("str", "ASCII", "ASCII(text)"),
    ("str", "ORD", "ORD(text)"),
    ("str", "CHAR", "CHAR(code, ...)"),
    ("str", "MD5", "MD5(text)"),
    ("str", "SHA1", "SHA1(text)"),
    ("str", "SHA2", "SHA2(text, bits)"),
    ("str", "TO_BASE64", "TO_BASE64(text)"),
    ("str", "FROM_BASE64", "FROM_BASE64(text)"),
    ("str", "REGEXP_LIKE", "REGEXP_LIKE(text, pattern [, flags])"),
    ("str", "REGEXP_REPLACE", "REGEXP_REPLACE(text, pattern, replacement)"),
    ("str", "REGEXP_SUBSTR", "REGEXP_SUBSTR(text, pattern)"),
    ("str", "REGEXP_INSTR", "REGEXP_INSTR(text, pattern)"),
    ("str", "QUOTE", "QUOTE(text)"),
    ("str", "LCASE", "LCASE(text)"),
    ("str", "UCASE", "UCASE(text)"),
    ("str", "BIN", "BIN(n)"),
    ("str", "OCT", "OCT(n)"),
    ("str", "SOUNDEX", "SOUNDEX(text)"),
    ("str", "CONVERT", "CONVERT(expr USING charset)"),
    # numeric
    ("num", "CEIL", "CEIL(n)"),
    ("num", "CEILING", "CEILING(n)"),
    ("num", "FLOOR", "FLOOR(n)"),
    ("num", "POWER", "POWER(base, exponent)"),
    ("num", "POW", "POW(base, exponent)"),
    ("num", "SQRT", "SQRT(n)"),
    ("num", "MOD", "MOD(a, b)"),
    ("num", "SIGN", "SIGN(n)"),
    ("num", "TRUNCATE", "TRUNCATE(n, digits)"),
    ("num", "EXP", "EXP(n)"),
    ("num", "LN", "LN(n)"),
    ("num", "LOG", "LOG([base,] n)"),
    ("num", "LOG2", "LOG2(n)"),
    ("num", "LOG10", "LOG10(n)"),
    ("num", "RAND", "RAND([seed])"),
    ("num", "PI", "PI()"),
    ("num", "SIN", "SIN(n)"),
    ("num", "COS", "COS(n)"),
    ("num", "TAN", "TAN(n)"),
    ("num", "ATAN", "ATAN(n)"),
    ("num", "ATAN2", "ATAN2(y, x)"),
    ("num", "DEGREES", "DEGREES(n)"),
    ("num", "RADIANS", "RADIANS(n)"),
    ("num", "CRC32", "CRC32(text)"),
    ("num", "CONV", "CONV(n, from_base, to_base)"),
    # conditional
    ("cond", "IF", "IF(condition, then_value, else_value)"),
    ("cond", "IFNULL", "IFNULL(a, b)"),
    ("cond", "ISNULL", "ISNULL(expr)"),
    ("cond", "GREATEST", "GREATEST(a, b, ...)"),
    ("cond", "LEAST", "LEAST(a, b, ...)"),
    # date / time
    ("dt", "NOW", "NOW()"),
    ("dt", "CURDATE", "CURDATE()"),
    ("dt", "CURTIME", "CURTIME()"),
    ("dt", "SYSDATE", "SYSDATE()"),
    ("dt", "UTC_TIMESTAMP", "UTC_TIMESTAMP()"),
    ("dt", "UTC_DATE", "UTC_DATE()"),
    ("dt", "DATE", "DATE(expr)"),
    ("dt", "TIME", "TIME(expr)"),
    ("dt", "TIMESTAMP", "TIMESTAMP(expr [, time])"),
    ("dt", "YEAR", "YEAR(date)"),
    ("dt", "QUARTER", "QUARTER(date)"),
    ("dt", "MONTH", "MONTH(date)"),
    ("dt", "WEEK", "WEEK(date [, mode])"),
    ("dt", "WEEKDAY", "WEEKDAY(date)"),
    ("dt", "WEEKOFYEAR", "WEEKOFYEAR(date)"),
    ("dt", "DAY", "DAY(date)"),
    ("dt", "DAYOFMONTH", "DAYOFMONTH(date)"),
    ("dt", "DAYOFWEEK", "DAYOFWEEK(date)"),
    ("dt", "DAYOFYEAR", "DAYOFYEAR(date)"),
    ("dt", "DAYNAME", "DAYNAME(date)"),
    ("dt", "MONTHNAME", "MONTHNAME(date)"),
    ("dt", "HOUR", "HOUR(time)"),
    ("dt", "MINUTE", "MINUTE(time)"),
    ("dt", "SECOND", "SECOND(time)"),
    ("dt", "MICROSECOND", "MICROSECOND(expr)"),
    ("dt", "DATE_ADD", "DATE_ADD(date, INTERVAL n unit)"),
    ("dt", "DATE_SUB", "DATE_SUB(date, INTERVAL n unit)"),
    ("dt", "ADDDATE", "ADDDATE(date, INTERVAL n unit)"),
    ("dt", "SUBDATE", "SUBDATE(date, INTERVAL n unit)"),
    ("dt", "DATE_FORMAT", "DATE_FORMAT(date, format)"),
    ("dt", "STR_TO_DATE", "STR_TO_DATE(text, format)"),
    ("dt", "DATEDIFF", "DATEDIFF(a, b)"),
    ("dt", "TIMEDIFF", "TIMEDIFF(a, b)"),
    ("dt", "TIMESTAMPDIFF", "TIMESTAMPDIFF(unit, a, b)"),
    ("dt", "TIMESTAMPADD", "TIMESTAMPADD(unit, n, datetime)"),
    ("dt", "FROM_UNIXTIME", "FROM_UNIXTIME(seconds [, format])"),
    ("dt", "UNIX_TIMESTAMP", "UNIX_TIMESTAMP([datetime])"),
    ("dt", "LAST_DAY", "LAST_DAY(date)"),
    ("dt", "MAKEDATE", "MAKEDATE(year, day_of_year)"),
    ("dt", "EXTRACT", "EXTRACT(unit FROM datetime)"),
    ("dt", "TO_DAYS", "TO_DAYS(date)"),
    ("dt", "FROM_DAYS", "FROM_DAYS(n)"),
    ("dt", "SEC_TO_TIME", "SEC_TO_TIME(seconds)"),
    ("dt", "TIME_TO_SEC", "TIME_TO_SEC(time)"),
    ("dt", "PERIOD_DIFF", "PERIOD_DIFF(a, b)"),
    # json
    ("json", "JSON_EXTRACT", "JSON_EXTRACT(doc, path, ...)"),
    ("json", "JSON_UNQUOTE", "JSON_UNQUOTE(json)"),
    ("json", "JSON_OBJECT", "JSON_OBJECT(key, value, ...)"),
    ("json", "JSON_ARRAY", "JSON_ARRAY(a, b, ...)"),
    ("json", "JSON_SET", "JSON_SET(doc, path, value, ...)"),
    ("json", "JSON_INSERT", "JSON_INSERT(doc, path, value, ...)"),
    ("json", "JSON_REPLACE", "JSON_REPLACE(doc, path, value, ...)"),
    ("json", "JSON_REMOVE", "JSON_REMOVE(doc, path, ...)"),
    ("json", "JSON_CONTAINS", "JSON_CONTAINS(doc, candidate [, path])"),
    ("json", "JSON_CONTAINS_PATH", "JSON_CONTAINS_PATH(doc, 'one'|'all', path, ...)"),
    ("json", "JSON_KEYS", "JSON_KEYS(doc [, path])"),
    ("json", "JSON_LENGTH", "JSON_LENGTH(doc [, path])"),
    ("json", "JSON_TYPE", "JSON_TYPE(json)"),
    ("json", "JSON_VALID", "JSON_VALID(value)"),
    ("json", "JSON_SEARCH", "JSON_SEARCH(doc, 'one'|'all', text)"),
    ("json", "JSON_VALUE", "JSON_VALUE(doc, path)"),
    ("json", "JSON_QUOTE", "JSON_QUOTE(text)"),
    ("json", "JSON_MERGE_PATCH", "JSON_MERGE_PATCH(a, b, ...)"),
    ("json", "JSON_TABLE", "JSON_TABLE(doc, path COLUMNS (...))"),
    # system
    ("sys", "DATABASE", "DATABASE()"),
    ("sys", "SCHEMA", "SCHEMA()"),
    ("sys", "USER", "USER()"),
    ("sys", "VERSION", "VERSION()"),
    ("sys", "CONNECTION_ID", "CONNECTION_ID()"),
    ("sys", "LAST_INSERT_ID", "LAST_INSERT_ID()"),
    ("sys", "ROW_COUNT", "ROW_COUNT()"),
    ("sys", "FOUND_ROWS", "FOUND_ROWS()"),
    ("sys", "UUID", "UUID()"),
    ("sys", "UUID_SHORT", "UUID_SHORT()"),
    ("sys", "SLEEP", "SLEEP(seconds)"),
    ("sys", "BENCHMARK", "BENCHMARK(count, expr)"),
    ("sys", "CHARSET", "CHARSET(text)"),
    ("sys", "COLLATION", "COLLATION(text)"),
)

FUNCTIONS: tuple[FunctionSpec, ...] = build_functions(COMMON_FUNCTIONS + _MYSQL_FUNCTIONS)

DATA_TYPES: tuple[str, ...] = (
    "tinyint",
    "smallint",
    "mediumint",
    "int",
    "bigint",
    "decimal",
    "numeric",
    "float",
    "double",
    "bit",
    "boolean",
    "char",
    "varchar",
    "binary",
    "varbinary",
    "tinytext",
    "text",
    "mediumtext",
    "longtext",
    "tinyblob",
    "blob",
    "mediumblob",
    "longblob",
    "enum",
    "set",
    "date",
    "time",
    "datetime",
    "timestamp",
    "year",
    "json",
    "geometry",
    "point",
    "linestring",
    "polygon",
)
