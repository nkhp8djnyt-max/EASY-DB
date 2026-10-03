"""Shared paths and helpers of the ClickBench harness."""

from __future__ import annotations

import os
from pathlib import Path

DATA_DIR = Path(
    os.environ.get("CLICKBENCH_DIR", Path.home() / ".cache" / "easydbms" / "clickbench")
)
PARQUET = DATA_DIR / "hits_0.parquet"
CSV = Path(
    os.environ.get("CLICKBENCH_CSV", "/tmp/hits_clickbench.csv")
)  # must be readable by the DB servers
DUCKDB_FILE = DATA_DIR / "hits.duckdb"
CHDB_DIR = DATA_DIR / "chdb"

PG_URL = os.environ.get("CB_POSTGRES_URL", "postgresql+psycopg://erd:erd@127.0.0.1:5432/erd_test")
MYSQL_URL = os.environ.get("CB_MYSQL_URL", "mysql+pymysql://erd:erd@127.0.0.1:3306/erd_test")
SQLITE_FILE = DATA_DIR / "hits.sqlite"

#: Query set directory per engine (the official ClickBench repository layout).
QUERY_DIRS = {
    "postgresql": "postgresql",
    "mysql": "mysql",
    "sqlite": "sqlite",
    "duckdb": "duckdb",
    "clickhouse": "clickhouse",
}


def read_sql_file(engine: str, name: str) -> str:
    return (DATA_DIR / QUERY_DIRS[engine] / name).read_text(encoding="utf-8")


def read_queries(engine: str) -> list[str]:
    """The 43 queries, one per line in the official files."""
    lines = [ln.strip() for ln in read_sql_file(engine, "queries.sql").splitlines() if ln.strip()]
    return [ln.rstrip(";") for ln in lines]
