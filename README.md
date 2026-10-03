# SQL ERD Studio

Desktop database client in Python: SQL editor, ERD, editable result grid. Supported SQL
dialects: **PostgreSQL**, **MySQL** (including MariaDB) and **SQLite**. Corporate dialects
(SQL Server, Oracle) are intentionally out of scope.

**Status: stage 2 of 7 — SQL editor and results.** Connect, write SQL in tabs, run a statement or a
whole script, cancel it, and browse the result in a sortable, filterable grid. The ERD, autocomplete
and editing results are the next stages (see [Roadmap](#roadmap)).

| Run a query | Script with an error | Formatted |
|---|---|---|
| ![](docs/screenshots/stage2-results.png) | ![](docs/screenshots/stage2-script-error.png) | ![](docs/screenshots/stage2-formatted.png) |

| Test a connection | Failure explained | Connected |
|---|---|---|
| ![](docs/screenshots/connections-dialog-test-ok.png) | ![](docs/screenshots/connections-dialog-test-failed.png) | ![](docs/screenshots/main-connected.png) |

More: [SQLite file](docs/screenshots/connections-dialog-sqlite.png),
[production banner](docs/screenshots/main-production.png),
[connection error](docs/screenshots/main-error.png),
[light theme](docs/screenshots/connections-dialog-light.png).

## Run

```bash
uv venv --python 3.12 && . .venv/bin/activate
uv pip install -e ".[dev]"        # builds the optional C extension if a compiler is present
python -m sql_erd_studio          # or: sql-erd-studio
```

Without a C compiler everything still works (pure-Python fallback); `python -c "from sql_erd_studio.core
import simd; print(simd.status())"` shows what is active.

Config lives in the per-user config directory (`connections.json`, `settings.toml`, `vault.json`)
and data in the per-user data directory (`app.db`). Set `SQL_ERD_STUDIO_HOME=/some/dir` to keep
everything in one place (portable installs, experiments).

## What stage 2 does

- **Query tabs.** Open tabs, their text and the active tab are stored per connection (`app.db`) and
  restored on the next start. `Ctrl+T` new, `Ctrl+W` close.
- **Editor.** Line numbers, current-line highlight, syntax colours produced by the *dialect's own
  lexer* (so `$$` bodies, MySQL `#` comments or SQLite `[quoted]` names are coloured correctly),
  `Ctrl+Shift+F` formats with the active dialect.
- **Run what you mean.** `Ctrl+Enter` runs the statement under the cursor (found by the splitter, not by
  blank lines), the selection if there is one; `F5` runs the whole script statement by statement and stops at the first
  error (the rest are marked *skipped*); `Esc` cancels the running statement.
- **Safe by default.** `DROP`, `TRUNCATE`, and `DELETE` / `UPDATE` without `WHERE` ask for confirmation
  first; on a connection marked *production* every statement that changes data does.
- **Results grid.** One result tab per statement; row-limited (100 – 100 000, *Query → Row limit*), with a
  "truncated" marker; click a header to sort, type in the filter box to search every column. Sorting and
  filtering run in Arrow / NumPy kernels, not Python loops. `Ctrl+C` copies the selection, `Ctrl+Shift+C` with headers.
- **Large results stay cheap.** PostgreSQL row-limited `SELECT`s stream from a server-side cursor and stop
  early; MySQL uses an unbuffered cursor; SQLite fetches only what is shown.

## Performance

Two things are separate here and are reported separately: how fast the *engines* are (not ours to
change) and how much the application *adds*.

- **Native SIMD scanner** (`sql_erd_studio/core/simd/_native.c`). The statement splitter that decides what
  `Ctrl+Enter` runs is also on the path of every script run. A C extension does the same job over the raw
  string and skips string / comment / `$$` bodies with AVX2 (32 bytes per step) or SSE2 (16), chosen at
  run time with `__builtin_cpu_supports`; other CPUs use a scalar C loop. It is **optional**: it is built
  when a compiler is available, the pure-Python splitter is the reference and the fallback, and
  `SQL_ERD_STUDIO_NO_SIMD=1` turns it off. The extension returns "not mine" for the few inputs it does not
  model exactly (a non-ASCII digit, a character whose `upper()` is ASCII) and Python takes over.
  Differential fuzzing (`tests/core/simd`: random scripts built from lexer-hostile fragments, raw random
  characters, every truncation of a tricky script, vector boundaries on 1/2/4-byte strings; three
  dialects, with and without the vector scanners) requires identical statements wherever the extension
  answers. 60 000 scripts per dialect and mode were run without a difference.
- **Vectorised grid.** Sorting and filtering use PyArrow / NumPy, which dispatch to AVX2 / AVX-512 at run
  time on their own.

`python -m benchmarks.simd_micro` on the development machine (4 cores, AVX2/AVX-512, Python 3.12):

| Operation | Python reference | native, scalar | native, SIMD |
|---|---|---|---|
| find first of `'` or `\` in 64 chars | 0.5 µs | 0.4 µs | 0.3 µs  (1.7× vs re) |
| find first of `'` or `\` in 1,000 chars | 4.5 µs | 2.9 µs | 0.6 µs  (7.3× vs re) |
| find first of `'` or `\` in 100,000 chars | 454.4 µs | 250.0 µs | 3.8 µs  (118.5× vs re) |
| find first of `'` or `\` in 10,000,000 chars | 45.380 ms | 26.955 ms | 418.1 µs  (108.5× vs re) |
| split 10 statements (3 kB) | 1.284 ms | 46.9 µs | 60.1 µs  (21.4× vs Python) |
| split 200 statements (80 kB) | 26.971 ms | 919.1 µs | 970.5 µs  (27.8× vs Python) |
| split 2,000 statements (811 kB) | 311.453 ms | 10.589 ms | 9.630 ms  (32.3× vs Python) |
| split 2,000 statements (5190 kB) | 821.974 ms | 21.046 ms | 13.392 ms  (61.4× vs Python) |
| sort 1M rows by int | 529.433 ms | — | 134.540 ms  (3.9× vs Python) |
| sort 1M rows by text | 670.733 ms | — | 364.247 ms  (1.8× vs Python) |
| sort 1M rows by float | 453.158 ms | — | 147.728 ms  (3.1× vs Python) |
| filter 1M rows (substring, ignore case) | 87.474 ms | — | 62.664 ms  (1.4× vs Python) |

Honest reading: the vector scan pays off on **long** strings, comments and `$$` bodies (migrations,
seed data, function definitions); on short scripts it is no faster than the scalar C loop (the table shows it within noise or
slightly behind) and almost all of the win over Python comes from not creating a token object per word. The C code does not reduce the time a
database needs to run the query.

### ClickBench (1 M rows)

`python -m benchmarks.clickbench.run` runs the 43 official queries (3 runs each) on 1 M rows (1 % of the
dataset), default configuration of every engine, same machine (4 cores). Rows marked *SQL ERD Studio* go
through the application's own `DatabaseClient`; the others are the plain driver, DuckDB 1.5.6 and
ClickHouse 26.9 (`chdb`, in-process). Stage 2 results (full per-query table in
[`benchmarks/results/clickbench-stage-2.md`](benchmarks/results/clickbench-stage-2.md)):

| Runner | Relative score (lower is better) | Sum of best times, 43 queries |
|---|---:|---:|
| DuckDB 1.5.6 | 1.11 | 0.8 s |
| ClickHouse 26.9 (chdb) | 1.35 | 1.0 s |
| PostgreSQL 16 — SQL ERD Studio / psycopg | 14.90 / 14.87 | 18.4 s / 18.4 s |
| SQLite 3.45 — SQL ERD Studio / sqlite3 | 16.38 / 16.31 | 21.4 s / 21.4 s |
| MariaDB 10.11 — SQL ERD Studio / PyMySQL | 78.03 / 78.22 | 79.5 s / 79.6 s |

What it says, and what it does not:

- **The application layer is free**: the editor's client costs the same as the bare driver within
  measurement noise (+0.0 % PostgreSQL, −0.2 % MariaDB, +0.0 % SQLite, runs interleaved so neither warms
  the cache for the other). That is the part this project controls.
- **Engine speed is the engine's**: DuckDB and ClickHouse, column stores built for this workload, finish the
  suite in about 1 s against 18–80 s for the row stores. SQL ERD Studio does not make PostgreSQL, MariaDB or
  SQLite faster and does not claim to; it is a client for them, not an engine.
- Caveats: 1 M rows rather than 100 M, untuned servers sharing the machine with the benchmark, page cache not
  dropped (first run is warm), a single machine. Absolute numbers are not comparable with the public
  leaderboard. Two queries differ between engines in text (43) or return a different number of rows for
  the same text (29, MariaDB); both are listed in the report.

The benchmark is repeated at the end of every stage; see [`benchmarks/README.md`](benchmarks/README.md).

## What stage 1 does

- **Saved connections**, grouped, with a colour label; production connections get a red banner and
  a red switcher. Duplicate, delete, edit with an "unsaved changes" guard.
- **URL or Host/Port**, kept in sync both ways: paste `postgres://user:pw@host/db?sslmode=require`
  and the fields fill in (the password goes to its own field and is dropped from the URL).
  SQLite connections take a file path (open an existing file, or create a new one).
- **Test connection** reports each step — DNS, TCP, sign-in, test query (file, open, query for
  SQLite) — and explains failures: wrong password, unknown database, closed port, TLS, bad file.
- **Passwords never touch the config file.** They go to the system keyring; where none exists
  (headless Linux, containers) to an encrypted vault protected by a master password
  (scrypt + Fernet). A connection can also ask for its password on each run.
- **`${ENV_VAR}` placeholders** (`${VAR:-default}`, `$${` for a literal) in host, user, database,
  path, parameters and password, resolved at connect time.
- **Read-only connections** switch the session to read-only after connecting.
- **Lazy, parallel sessions.** Picking a connection in the switcher connects in the background;
  several stay open; the UI never blocks.
- **Dark and light themes**, **Russian and English** UI (follows the system; a language change applies on the next start).
- Window size and splitter position are remembered.

## Layout

```
sql_erd_studio/
├─ core/                    # no Qt
│  ├─ dialects/             # PostgreSQL / MySQL / SQLite: quoting, literals, lexer, splitter,
│  │                        #   formatter, translator, vocabulary, type classification
│  ├─ db/                   # DatabaseClient + PostgresClient, MySqlClient, SqliteClient, errors
│  ├─ connections/          # ConnectionConfig, URL parser, ${ENV}, secret stores, store
│  ├─ session/              # Session (state machine, script runs) and ConnectionManager
│  ├─ queries/              # query tabs, danger guard, script job
│  ├─ simd/                 # optional C extension: AVX2/SSE2 scanner + statement splitter
│  ├─ columnar.py           # Arrow/NumPy sort and filter of a result set
│  ├─ storage/              # app.db (migrations) and settings.toml
│  ├─ paths.py  services.py
├─ ui/                      # PySide6: main window, workspace, editor, results grid, dialogs, theme, i18n
└─ app.py
benchmarks/                 # ClickBench harness and micro-benchmarks
```

Dependencies point one way: `ui → core/session → core/*`. Worker threads reach the GUI through
`ui/runtime/qt_bridge.py` (queued signals), so widgets are only touched on the GUI thread.

### Things worth knowing

- **User SQL goes to the driver with no parameter container.** psycopg and PyMySQL treat `%` as a
  placeholder as soon as any parameters are passed (even an empty dict), which would break every
  `LIKE '%x%'` typed into an editor. Clients therefore use the raw cursor; tests cover this.
- **Cancel** is driver specific: PostgreSQL `Connection.cancel()`, MySQL `KILL QUERY` from a second
  connection, SQLite `interrupt()`. `cancel()` is safe from any thread and only fires while a
  statement is running.
- **`execute(max_rows=…)` bounds memory for row-returning queries** (`SELECT`, `WITH`, `VALUES`, `TABLE`, `SHOW`, `EXPLAIN`):
  PostgreSQL streams them through psycopg's `stream()` and MySQL uses an unbuffered cursor and stops the
  server-side query when the limit is hit; SQLite fetches `max_rows + 1`. Statements that can change data
  (`INSERT … RETURNING`, `CALL`) are run buffered so the whole statement is always executed.
- **A missing SQLite file is an error**, not a new empty database (opt in with "Create the file").
- A damaged `connections.json` / `settings.toml` is never overwritten: it is moved aside as
  `*.broken-<timestamp>`, valid entries are kept, and the user is told.

## Dialect system

`sql_erd_studio.core.dialects` is the single place that knows how the three dialects differ.
Everything else asks a `Dialect` instead of branching on a database name:

| Concern | API |
|---|---|
| Lookup by name, alias or URL | `get_dialect("postgres")`, `dialect_from_url("mysql://…")` |
| Identifier quoting | `dialect.quote_ident()`, `quote_qualified()`, `quote_ident_if_needed()` |
| Literals (previews only) | `dialect.render_literal(value)` |
| Null-safe equality (optimistic locking) | `dialect.null_safe_eq(lhs, rhs)` |
| Server-side pagination | `paginate(dialect, sql, limit=…, offset=…, order_by=…)` |
| Read-only sessions | `dialect.read_only_statements()` |
| Column type → editor kind | `dialect.classify_type("tinyint(1)")` |
| Keywords / functions / types | `dialect.keywords`, `dialect.functions`, `dialect.data_types` |
| Tokenizing (highlighting) | `tokenize(sql, dialect)` |
| Statement splitting (Ctrl+Enter) | `split_statements()`, `statement_at()` |
| Formatting / syntax check | `format_sql()`, `check_syntax()` |
| Translation between dialects | `translate(sql, source=…, target=…)` |

## Development

```bash
ruff check . && ruff format --check . && mypy      # mypy runs in strict mode
pytest                                             # SQLite always; Qt runs headless (offscreen)
ERD_FUZZ_ITERATIONS=60000 pytest tests/core/simd   # longer native-vs-Python differential fuzz
```

The suite is most valuable against real servers. Point it at empty throw-away databases:

```bash
export ERD_TEST_POSTGRES_URL="postgresql+psycopg://user:pass@localhost/erd_test"
export ERD_TEST_MYSQL_URL="mysql+pymysql://user:pass@localhost/erd_test"
pytest
```

Without those variables the server cases are skipped. GUI tests use `pytest-qt` on Qt's
`offscreen` platform; on a bare Linux box Qt needs `libegl1 libgl1 libxkbcommon0 libfontconfig1
libdbus-1-3` installed.

## Roadmap

1. ✅ **Connections** — models, URL parsing, secrets, PostgreSQL/MySQL/SQLite clients, dialog,
   switcher.
2. ✅ **SQL editor** (tabs, highlighting from the dialect lexer), run/cancel, read-only results grid,
   optional native SIMD scanner, ClickBench harness.
3. Schema introspection, ERD pane, table tabs.
4. Autocomplete (keywords → tables → columns → aliases → JOIN by FK).
5. Editable grid: change set, preview, Alt+S, one transaction, optimistic locking.
6. SSL, SSH tunnel, `~/.pgpass` / `pg_service.conf`.
7. Cloud providers, history and saved queries, ERD export, packaging.
