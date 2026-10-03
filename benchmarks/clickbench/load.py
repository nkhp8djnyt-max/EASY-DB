"""Load the ClickBench ``hits`` sample (1M rows, the official parquet file) into every engine.

    python -m benchmarks.clickbench.load postgresql mysql sqlite duckdb clickhouse

Each engine gets the official DDL of the ClickBench repository; the data is converted from the
parquet file (EventTime is epoch seconds, EventDate is days since 1970 there).
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable

import duckdb

from sql_erd_studio.core.connections import parse_connection_url
from sql_erd_studio.core.db import create_client

from . import common

SRC = f"read_parquet('{common.PARQUET}', binary_as_string=True)"
# parquet -> logical types: seconds to timestamp, days to date
CONVERT = (
    "* REPLACE (epoch_ms(EventTime * 1000) AS EventTime, "
    "epoch_ms(ClientEventTime * 1000) AS ClientEventTime, "
    "epoch_ms(LocalEventTime * 1000) AS LocalEventTime, "
    "DATE '1970-01-01' + INTERVAL (EventDate) DAY AS EventDate)"
)


def timed(label: str) -> Callable[[Callable[[], None]], None]:
    def run(fn: Callable[[], None]) -> None:
        started = time.perf_counter()
        fn()
        print(f"[{label}] loaded in {time.perf_counter() - started:.1f}s", flush=True)

    return run


def make_csv() -> None:
    if common.CSV.exists() and common.CSV.stat().st_size > 0:
        return
    print("[csv] exporting from parquet ...", flush=True)
    con = duckdb.connect()
    con.execute(
        f"COPY (SELECT {CONVERT} FROM {SRC}) TO '{common.CSV}' (FORMAT CSV, HEADER false, FORCE_QUOTE *)"
    )
    common.CSV.chmod(0o644)


def load_postgresql() -> None:
    import psycopg

    make_csv()
    parsed = parse_connection_url(common.PG_URL)
    with create_client(parsed.config, parsed.password) as client:
        client.execute("DROP TABLE IF EXISTS hits")
        for statement in common.read_sql_file("postgresql", "create.sql").split(";"):
            if statement.strip():
                client.execute(statement)
    conn = psycopg.connect(common.PG_URL.replace("+psycopg", ""), autocommit=True)
    with (
        conn.cursor() as cur,
        cur.copy("COPY hits FROM STDIN (FORMAT csv)") as copy,
        open(common.CSV, "rb") as fh,
    ):
        while chunk := fh.read(1 << 20):
            copy.write(chunk)
    conn.execute("VACUUM ANALYZE hits")


def load_mysql() -> None:
    import pymysql

    make_csv()
    parsed = parse_connection_url(common.MYSQL_URL)
    with create_client(parsed.config, parsed.password) as client:
        client.execute("DROP TABLE IF EXISTS hits")
        client.execute(common.read_sql_file("mysql", "create.sql").strip().rstrip(";"))
    cfg = parsed.config
    conn = pymysql.connect(
        host=cfg.host,
        port=cfg.effective_port,
        user=cfg.user,
        password=parsed.password,
        database=cfg.database,
        autocommit=True,
        read_timeout=3600,
        local_infile=True,
    )
    conn.cursor().execute(
        f"LOAD DATA LOCAL INFILE '{common.CSV}' INTO TABLE hits FIELDS TERMINATED BY ',' ENCLOSED BY '\"' "
        "ESCAPED BY '' LINES TERMINATED BY '\\n'"
    )
    conn.cursor().execute("ANALYZE TABLE hits")


def load_sqlite() -> None:
    import sqlite3

    common.SQLITE_FILE.unlink(missing_ok=True)
    conn = sqlite3.connect(common.SQLITE_FILE)
    conn.executescript(common.read_sql_file("sqlite", "create.sql"))
    source = duckdb.connect()
    columns = [r[0] for r in source.execute(f"DESCRIBE SELECT * FROM {SRC}").fetchall()]
    projection = ", ".join(
        f"CAST({c} AS VARCHAR) AS {c}" if c in ("EventTime", "EventDate") else c for c in columns
    )
    cursor = source.execute(f"SELECT {projection} FROM (SELECT {CONVERT} FROM {SRC})")
    insert = f"INSERT INTO hits VALUES ({', '.join('?' * len(columns))})"
    while rows := cursor.fetchmany(20000):
        conn.executemany(insert, rows)
    conn.commit()
    conn.execute("ANALYZE")
    conn.close()


def load_duckdb() -> None:
    common.DUCKDB_FILE.unlink(missing_ok=True)
    con = duckdb.connect(str(common.DUCKDB_FILE))
    con.execute(common.read_sql_file("duckdb", "create.sql"))
    con.execute(f"INSERT INTO hits SELECT {CONVERT} FROM {SRC}")
    con.close()


def load_clickhouse() -> None:
    import shutil

    from chdb import session

    shutil.rmtree(common.CHDB_DIR, ignore_errors=True)
    sess = session.Session(str(common.CHDB_DIR))
    sess.query("CREATE DATABASE IF NOT EXISTS clickbench")
    sess.query("USE clickbench")
    sess.query(common.read_sql_file("clickhouse", "create.sql").strip().rstrip(";"))
    sess.query(
        "INSERT INTO hits SELECT * REPLACE (toDateTime(EventTime) AS EventTime, "
        "toDateTime(ClientEventTime) AS ClientEventTime, toDateTime(LocalEventTime) AS LocalEventTime, "
        "toDate(EventDate) AS EventDate) "
        f"FROM file('{common.PARQUET}', Parquet)"
    )
    sess.query("OPTIMIZE TABLE hits FINAL")


LOADERS: dict[str, Callable[[], None]] = {
    "postgresql": load_postgresql,
    "mysql": load_mysql,
    "sqlite": load_sqlite,
    "duckdb": load_duckdb,
    "clickhouse": load_clickhouse,
}


def main(argv: list[str]) -> None:
    for name in argv or list(LOADERS):
        timed(name)(LOADERS[name])


if __name__ == "__main__":
    main(sys.argv[1:])
