"""Run the 43 ClickBench queries on every engine and write a JSON + Markdown report.

    python -m benchmarks.clickbench.run --stage 2                       # everything
    python -m benchmarks.clickbench.run --engines duckdb clickhouse --runs 3 --queries 1-10

Rows of the report ("runners"):

* ``app``   - our :class:`DatabaseClient` (what the editor uses) on PostgreSQL, MariaDB, SQLite
* ``raw``   - the same driver called directly (psycopg, PyMySQL, sqlite3): the cost of our layer
* DuckDB, ClickHouse (``chdb``, the embedded ClickHouse engine) as the analytical references

Every statement runs ``--runs`` times. The first run is reported as "first" (the OS page cache is
*not* dropped; engines are warm from the load); the "best" is the minimum of all runs. A query that
fails or exceeds ``--timeout`` seconds is marked and penalised in the relative score.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from sql_erd_studio.core import simd
from sql_erd_studio.core.connections import parse_connection_url
from sql_erd_studio.core.db import create_client

from . import common

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"
#: ClickBench adds 10 ms to every time before taking ratios, so sub-millisecond noise cannot dominate.
OFFSET = 0.010


class QueryTimeout(Exception):
    pass


class Runner:
    """One engine + access path. ``run`` returns the number of result rows."""

    label: str
    engine: str  # key into common.QUERY_DIRS
    group: str  # "app", "raw" or "reference"

    def open(self) -> None: ...

    def run(self, sql: str) -> int:
        raise NotImplementedError

    def cancel(self) -> None: ...

    def close(self) -> None: ...

    def version(self) -> str:
        return ""


def clean_version(raw: str) -> str:
    """``5.5.5-10.11.14-MariaDB-0ubuntu...`` -> ``10.11.14-MariaDB``; ``16.14 (Ubuntu ...)`` -> ``16.14``."""
    raw = raw.removeprefix("5.5.5-").split(" (")[0]
    return raw.split("-0ubuntu")[0]


class AppRunner(Runner):
    group = "app"

    def __init__(self, label: str, engine: str, url: str) -> None:
        self.label, self.engine, self._url = label, engine, url
        self._client: Any = None

    def open(self) -> None:
        parsed = parse_connection_url(self._url)
        self._client = create_client(parsed.config, parsed.password)
        self._client.connect()

    def run(self, sql: str) -> int:
        return len(self._client.execute(sql).rows)

    def cancel(self) -> None:
        self._client.cancel()

    def close(self) -> None:
        self._client.disconnect()

    def version(self) -> str:
        info = self._client.server_info
        return clean_version(info.raw) if info else ""


class RawPostgres(Runner):
    group = "raw"

    def __init__(self) -> None:
        self.label, self.engine = "PostgreSQL (psycopg)", "postgresql"

    def open(self) -> None:
        import psycopg

        self._conn = psycopg.connect(common.PG_URL.replace("+psycopg", ""), autocommit=True)

    def run(self, sql: str) -> int:
        return len(self._conn.execute(sql).fetchall())  # type: ignore[arg-type]

    def cancel(self) -> None:
        self._conn.cancel()

    def close(self) -> None:
        self._conn.close()


class RawMySql(Runner):
    group = "raw"

    def __init__(self) -> None:
        self.label, self.engine = "MariaDB (PyMySQL)", "mysql"

    def _connect(self) -> Any:
        import pymysql

        cfg = parse_connection_url(common.MYSQL_URL)
        c = cfg.config
        return pymysql.connect(
            host=c.host,  # type: ignore[union-attr]
            port=c.effective_port,  # type: ignore[union-attr]
            user=c.user,  # type: ignore[union-attr]
            password=cfg.password,
            database=c.database,  # type: ignore[union-attr]
            autocommit=True,
            read_timeout=3600,
        )

    def open(self) -> None:
        self._conn = self._connect()

    def run(self, sql: str) -> int:
        with self._conn.cursor() as cursor:
            cursor.execute(sql)
            return len(cursor.fetchall())

    def cancel(self) -> None:
        killer = self._connect()
        try:
            killer.cursor().execute(f"KILL QUERY {self._conn.thread_id()}")
        finally:
            killer.close()

    def close(self) -> None:
        self._conn.close()


class RawSqlite(Runner):
    group = "raw"

    def __init__(self) -> None:
        self.label, self.engine = "SQLite (sqlite3)", "sqlite"

    def open(self) -> None:
        self._conn = sqlite3.connect(common.SQLITE_FILE, check_same_thread=False)

    def run(self, sql: str) -> int:
        return len(self._conn.execute(sql).fetchall())

    def cancel(self) -> None:
        self._conn.interrupt()

    def close(self) -> None:
        self._conn.close()

    def version(self) -> str:
        return sqlite3.sqlite_version


class DuckDb(Runner):
    group = "reference"

    def __init__(self) -> None:
        self.label, self.engine = "DuckDB", "duckdb"

    def open(self) -> None:
        self._con = duckdb.connect(str(common.DUCKDB_FILE), read_only=True)

    def run(self, sql: str) -> int:
        return len(self._con.execute(sql).fetchall())

    def cancel(self) -> None:
        self._con.interrupt()

    def close(self) -> None:
        self._con.close()

    def version(self) -> str:
        return duckdb.__version__


class ClickHouse(Runner):
    group = "reference"

    def __init__(self, timeout: float) -> None:
        self.label, self.engine = "ClickHouse (chdb)", "clickhouse"
        self._timeout = timeout

    def open(self) -> None:
        from chdb import session

        self._session = session.Session(str(common.CHDB_DIR))
        self._session.query("USE clickbench")
        self._session.query(f"SET max_execution_time = {int(self._timeout)}")

    def run(self, sql: str) -> int:
        try:
            output = str(self._session.query(sql, "CSV"))
        except Exception as error:
            if "TIMEOUT_EXCEEDED" in str(error) or "Timeout exceeded" in str(error):
                raise QueryTimeout from error
            raise
        return output.count("\n")

    def version(self) -> str:
        return str(self._session.query("SELECT version()", "CSV")).strip().strip('"')


def build_runners(names: list[str], timeout: float) -> list[Runner]:
    all_runners: dict[str, Callable[[], list[Runner]]] = {
        "postgresql": lambda: [
            AppRunner("PostgreSQL (SQL ERD Studio)", "postgresql", common.PG_URL),
            RawPostgres(),
        ],
        "mysql": lambda: [
            AppRunner("MariaDB (SQL ERD Studio)", "mysql", common.MYSQL_URL),
            RawMySql(),
        ],
        "sqlite": lambda: [
            AppRunner("SQLite (SQL ERD Studio)", "sqlite", f"sqlite:///{common.SQLITE_FILE}"),
            RawSqlite(),
        ],
        "duckdb": lambda: [DuckDb()],
        "clickhouse": lambda: [ClickHouse(timeout)],
    }
    return [runner for name in names for runner in all_runners[name]()]


@dataclass
class Measurement:
    times: list[float | None]  # seconds; None for a failed / timed-out run
    rows: int | None = None
    error: str | None = None

    @property
    def best(self) -> float | None:
        ok = [t for t in self.times if t is not None]
        return min(ok) if ok else None

    @property
    def first(self) -> float | None:
        return self.times[0] if self.times else None


def measure_once(runner: Runner, sql: str, timeout: float, into: Measurement) -> None:
    """One timed run, appended to ``into``; a failure ends the series for that query."""
    timer = threading.Timer(timeout, runner.cancel)
    timer.start()
    started = time.perf_counter()
    try:
        rows = runner.run(sql)
    except Exception as error:
        elapsed = time.perf_counter() - started
        timer.cancel()
        timed_out = isinstance(error, QueryTimeout) or elapsed >= timeout * 0.98
        into.error = "timeout" if timed_out else f"{type(error).__name__}: {str(error)[:160]}"
        into.times.append(None)
        return
    timer.cancel()
    into.times.append(time.perf_counter() - started)
    into.rows = rows


def measure_group(
    runners: list[Runner], sql: str, runs: int, timeout: float
) -> dict[str, Measurement]:
    """Interleave the runs of runners that share an engine, so neither one warms the cache for
    the other (app, raw, app, raw, ...)."""
    measurements = {runner.label: Measurement(times=[]) for runner in runners}
    for _ in range(runs):
        for runner in runners:
            m = measurements[runner.label]
            if m.error is None:
                measure_once(runner, sql, timeout, m)
    return measurements


def parse_selection(spec: str | None, total: int) -> list[int]:
    if not spec:
        return list(range(total))
    picked: list[int] = []
    for part in spec.split(","):
        low, _, high = part.partition("-")
        picked.extend(range(int(low) - 1, int(high or low)))
    return [i for i in picked if 0 <= i < total]


def relative_scores(results: dict[str, list[Measurement]], queries: list[int]) -> dict[str, float]:
    """ClickBench style: geometric mean over queries of (t + 10 ms) / (fastest t + 10 ms).

    A failed query counts as twice the slowest successful time of that query (own convention).
    """
    labels = list(results)
    adjusted: dict[str, list[float]] = {label: [] for label in labels}
    for q in queries:
        bests = {label: results[label][q].best for label in labels}
        known = [t for t in bests.values() if t is not None]
        worst = max(known) if known else 1.0
        for label in labels:
            t = bests[label]
            adjusted[label].append((t if t is not None else 2 * worst) + OFFSET)
    scores = {}
    for label in labels:
        ratios = [
            adjusted[label][i] / min(adjusted[o][i] for o in labels) for i in range(len(queries))
        ]
        scores[label] = math.exp(sum(math.log(r) for r in ratios) / len(ratios))
    return scores


def system_description() -> dict[str, Any]:
    cpu = platform.processor() or "?"
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as fh:
            cpu = next(ln.split(":", 1)[1].strip() for ln in fh if ln.startswith("model name"))
    except (OSError, StopIteration):
        pass
    return {
        "cpu": cpu,
        "cores": os.cpu_count(),
        "python": platform.python_version(),
        "os": platform.platform(),
        "simd": simd.implementation(),
    }


def fmt_ms(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    return f"{seconds * 1000:,.0f}" if seconds >= 0.01 else f"{seconds * 1000:.1f}"


def markdown(
    meta: dict[str, Any],
    results: dict[str, list[Measurement]],
    selected: list[int],
    groups: dict[str, str],
    engine_of: dict[str, str],
    engines: dict[str, str],
) -> str:
    scores = relative_scores(results, selected)
    lines = [
        f"# ClickBench, stage {meta['stage']}",
        "",
        f"Dataset: `hits_0.parquet`, **{meta['rows']:,} rows** (1/100 of the full ClickBench dataset). "
        f"{meta['system']['cores']} cores, {meta['system']['cpu']}, Python {meta['system']['python']}, "
        f"native scanner `{meta['system']['simd']}`. All servers run with their default (untuned) "
        f"configuration on the same machine. Date: {meta['date']}. "
        f"{meta['runs']} runs per query, timeout {meta['timeout']:.0f} s, OS page cache not dropped.",
        "",
        "## Summary",
        "",
        "| Runner | Version | Relative score (best of runs, lower is better) | Sum of best times | First-run sum | Failed |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for label in sorted(results, key=lambda name: scores[name]):
        ms = results[label]
        bests = [ms[q].best for q in selected]
        firsts = [ms[q].first for q in selected]
        failed = sum(1 for b in bests if b is None)
        total_best = sum(b for b in bests if b is not None)
        total_first = sum(f for f in firsts if f is not None)
        lines.append(
            f"| {label} | {engines.get(label, '')} | {scores[label]:.2f} | {total_best:.1f} s | {total_first:.1f} s | {failed} |"
        )
    lines += [
        "",
        "Relative score = geometric mean over the queries of `(t + 10 ms) / (fastest runner's t + 10 ms)`, "
        "the ClickBench formula; a failed query counts as twice the slowest successful time of that query. "
        "Sums skip failed queries, so compare them only between rows with the same *Failed* count.",
        "",
        "## Per query, best of runs (ms)",
        "",
    ]
    labels = list(results)
    lines.append("| # | " + " | ".join(labels) + " |")
    lines.append("|---:|" + "---:|" * len(labels))
    for q in selected:
        cells = []
        bests = [results[label][q].best for label in labels]
        fastest = min((b for b in bests if b is not None), default=None)
        for label, best in zip(labels, bests, strict=True):
            m = results[label][q]
            text = m.error if best is None and m.error else fmt_ms(best)
            cells.append(f"**{text}**" if best is not None and best == fastest else text)
        lines.append(f"| {q + 1} | " + " | ".join(cells) + " |")
    lines += ["", "## Cost of the application layer (app vs the raw driver)", ""]
    lines += [
        "| Engine | app, sum of best | raw driver, sum of best | overhead |",
        "|---|---:|---:|---:|",
    ]
    for engine in ("postgresql", "mysql", "sqlite"):
        pair = {groups[label]: label for label in results if engine_of[label] == engine}
        if "app" in pair and "raw" in pair:
            both = [
                q for q in selected if results[pair["app"]][q].best and results[pair["raw"]][q].best
            ]
            a = sum(results[pair["app"]][q].best or 0 for q in both)
            r = sum(results[pair["raw"]][q].best or 0 for q in both)
            lines.append(f"| {engine} | {a:.2f} s | {r:.2f} s | {(a / r - 1) * 100:+.1f} % |")
    mismatches = row_count_mismatches(results, selected)
    if mismatches:
        lines += ["", "## Row-count differences between engines", ""]
        lines += [
            f"- query {q + 1}: " + ", ".join(f"{label} {rows}" for label, rows in items)
            for q, items in mismatches
        ]
        lines += [
            "",
            "Each engine runs its own official ClickBench query text, which is not always the same "
            "statement (e.g. query 43 truncates time to the minute on PostgreSQL, DuckDB and ClickHouse, "
            "to the hour on MariaDB and to the minute of the hour on SQLite). A difference with identical "
            "SQL is only noted here, not investigated.",
        ]
    return "\n".join(lines) + "\n"


def row_count_mismatches(
    results: dict[str, list[Measurement]], selected: list[int]
) -> list[tuple[int, list[tuple[str, int]]]]:
    out = []
    for q in selected:
        counts = [(label, m[q].rows) for label, m in results.items() if m[q].rows is not None]
        if len({rows for _, rows in counts}) > 1:
            out.append((q, counts))
    return out


def render_saved(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    results = {
        label: [Measurement(times=r["times"], rows=r["rows"], error=r["error"]) for r in rows]
        for label, rows in payload["results"].items()
    }
    runners = payload["runners"]
    versions = {label: clean_version(v) for label, v in payload["versions"].items()}
    report = markdown(
        payload["meta"],
        results,
        payload["queries"],
        {label: r["group"] for label, r in runners.items()},
        {label: r["engine"] for label, r in runners.items()},
        versions,
    )
    path.with_suffix(".md").write_text(report, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--engines", nargs="*", default=list(common.QUERY_DIRS))
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--queries", help="e.g. 1-10,15 (1-based)")
    parser.add_argument("--stage", default="dev")
    parser.add_argument("--out", type=Path, default=RESULTS_DIR)
    parser.add_argument("--render", type=Path, help="rewrite the Markdown report of a saved JSON")
    args = parser.parse_args()
    if args.render:
        render_saved(args.render)
        return

    query_texts = {engine: common.read_queries(engine) for engine in args.engines}
    total = len(next(iter(query_texts.values())))
    selected = parse_selection(args.queries, total)

    results: dict[str, list[Measurement]] = {}
    versions: dict[str, str] = {}
    groups: dict[str, str] = {}
    engine_of: dict[str, str] = {}
    for engine in args.engines:
        runners = build_runners([engine], args.timeout)
        for runner in runners:
            runner.open()
            versions[runner.label] = runner.version()
            groups[runner.label] = runner.group
            engine_of[runner.label] = runner.engine
            results[runner.label] = [Measurement(times=[]) for _ in range(total)]
        known = next((versions[r.label] for r in runners if versions[r.label]), "")
        for runner in runners:
            versions[runner.label] = versions[runner.label] or known
            print(f"== {runner.label} {versions[runner.label]}", flush=True)
        for q in selected:
            measured = measure_group(runners, query_texts[engine][q], args.runs, args.timeout)
            for label, m in measured.items():
                results[label][q] = m
                shown = ", ".join(fmt_ms(t) for t in m.times)
                error = f"  {m.error}" if m.error else ""
                print(f"  [{label}] Q{q + 1:02d}: {shown} ms{error}", flush=True)
        for runner in runners:
            runner.close()

    rows = (
        duckdb.connect()
        .execute(f"SELECT count(*) FROM read_parquet('{common.PARQUET}')")
        .fetchone()
    )
    meta = {
        "stage": args.stage,
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "rows": rows[0] if rows else 0,
        "runs": args.runs,
        "timeout": args.timeout,
        "system": system_description(),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": meta,
        "versions": versions,
        "runners": {
            label: {"engine": engine_of[label], "group": groups[label]} for label in results
        },
        "queries": selected,
        "results": {
            label: [{"times": m.times, "rows": m.rows, "error": m.error} for m in measurements]
            for label, measurements in results.items()
        },
    }
    stem = f"clickbench-stage-{args.stage}"
    (args.out / f"{stem}.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    report = markdown(meta, results, selected, groups, engine_of, versions)
    (args.out / f"{stem}.md").write_text(report, encoding="utf-8")
    print("\n" + report)


if __name__ == "__main__":
    main()
