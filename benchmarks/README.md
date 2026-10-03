# Benchmarks

Two things are measured, both reproducible from this directory:

* `simd_micro.py` — the acceleration layer (native SIMD scanner, Arrow/NumPy sorting and filtering)
  against its pure Python reference.
* `clickbench/` — the 43 [ClickBench](https://github.com/ClickHouse/ClickBench) queries through
  **EasyDBMS's database clients**, next to the same drivers used directly, and to DuckDB and
  ClickHouse as analytical reference points. Reports are written to `benchmarks/results/`.

## ClickBench

```bash
uv pip install duckdb chdb                        # reference engines (not application dependencies)
python -m benchmarks.clickbench.fetch             # hits_0.parquet (1M rows) + official DDL / queries
# PostgreSQL and MariaDB servers with an empty database each; sqlite/duckdb/chdb are files:
export CB_POSTGRES_URL=postgresql+psycopg://user:pw@127.0.0.1:5432/db
export CB_MYSQL_URL=mysql+pymysql://user:pw@127.0.0.1:3306/db
python -m benchmarks.clickbench.load              # loads the data into all five engines
python -m benchmarks.clickbench.run --stage N     # 3 runs per query -> results/clickbench-stage-N.{md,json}
```

`run` takes `--engines`, `--queries 1-10,15`, `--runs`, `--timeout`. For every query it interleaves the
runs of the application client and of the raw driver, so neither one warms the cache for the other.

### How to read the results

* The dataset is **1 % of ClickBench** (one partition). Absolute numbers are not comparable with the
  public leaderboard, which uses 100 M rows; relative positions can also change with scale.
* Servers run with **default configuration**, in the same container as the benchmark; the OS page
  cache is not dropped, so "first run" is warm. DuckDB and ClickHouse (`chdb`, ClickHouse as an
  in-process library) use all cores, PostgreSQL and MariaDB parallelise per their defaults.
* The speed of an engine is the engine's. What this project controls is the **overhead of its own
  layer**: the "app vs raw driver" table. The editor and grid are meant to add nothing measurable to
  a query; they are not meant to make PostgreSQL scan faster.
* *Relative score* is the ClickBench formula: geometric mean over queries of
  `(t + 10 ms) / (fastest runner's t + 10 ms)`. A failed or timed-out query counts as twice the
  slowest successful time of that query (this project's convention).
