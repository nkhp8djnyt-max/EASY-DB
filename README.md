# EasyDBMS

Desktop database client in Python: SQL editor, ERD, editable result grid. Supported SQL
dialects: **PostgreSQL**, **MySQL** (including MariaDB) and **SQLite**. Corporate dialects
(SQL Server, Oracle) are intentionally out of scope.

**Status: stage 3 of 7 — structure and diagram.** Connect, write SQL in tabs, run a statement or a
whole script, browse the result in a sortable, filterable grid — and see the database as an ER diagram:
tables with key icons, foreign keys as crow's-foot lines, drag to arrange, double-click a table to read its
rows. Autocomplete and editing results are the next stages (see [Roadmap](#roadmap)).

| The diagram | Select a table | Table data |
|---|---|---|
| ![](docs/screenshots/stage3-diagram.png) | ![](docs/screenshots/stage3-highlight.png) | ![](docs/screenshots/stage3-table-tab.png) |

More: [all schemas, wide table collapsed](docs/screenshots/stage3-all-schemas.png),
[light theme](docs/screenshots/stage3-diagram-light.png).

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
python -m easydbms          # or: easydbms
```

Without a C compiler everything still works (pure-Python fallback); `python -c "from easydbms.core
import simd; print(simd.status())"` shows what is active.

Config lives in the per-user config directory (`connections.json`, `settings.toml`, `vault.json`)
and data in the per-user data directory (`app.db`). Set `EASYDBMS_HOME=/some/dir` to keep
everything in one place (portable installs, experiments).

## What stage 3 does

- **Reads the structure with a handful of queries**, not one per table: tables, views (and PostgreSQL
  materialized views), columns with declared type / `NOT NULL` / default / comment, primary keys in key
  order, foreign keys (composite, `ON DELETE` / `ON UPDATE`, across schemas), unique and plain indexes —
  from `pg_catalog`, `information_schema` or SQLite's pragma functions. It runs on the session's own
  *meta lane* (a second connection), so a long query never delays it and it never delays a query.
  Re-read it with ↻ or `Ctrl+Shift+R`; it is also read on every connect.
- **The diagram.** One card per table: title bar, then a row per column with a gold key (primary key), a
  blue key (foreign key), the declared type and a "?" badge when the column has a comment (shown in the
  tooltip). Wide tables are collapsed to 14 columns with "+ N more columns" (keys are never hidden);
  click that row to expand. Views have a purple title bar, junction tables (the many-to-many middle
  table) an `N:M` badge.
- **Relations** are orthogonal lines from the foreign-key column to the referenced table: a crow's foot
  at the referencing (child) end, `||` (required) or `o|` (the foreign key is nullable) at the parent
  end, and a single bar instead of the crow's foot when the foreign-key columns are unique (1:1). Lines
  that skip a layer are routed between the cards instead of through them.
- **Placement.** Referenced tables go left, referencing tables right; unconnected tables are packed into
  rows below. Drag a card and its position is remembered per connection and schema (`app.db`), while
  everything you did not move still follows the automatic layout — a table added later simply appears
  where the layout puts it. ⟲ resets the layout.
- **Navigation.** Mouse wheel zooms, dragging the background pans, `+` `−` ⛶ `?` buttons and `Ctrl+0` /
  `Ctrl++` / `Ctrl+-` zoom and fit. Selecting a card highlights it and the tables it is connected to and
  dims the rest. The search box shows only the tables whose name or a column name matches (Enter fits
  and selects the first); PostgreSQL / multi-database connections get a schema selector; the ⏵ button
  folds the right pane down to the connection switcher.
- **Big schemas.** Levels of detail: below 35 % zoom columns are not drawn, below 14 % titles are not,
  below 5 % lines are not; a diagram of more than 60 tables across several schemas is grouped into one
  labelled frame per schema.
- **Click, double-click, right-click.** Clicking a column types its name into the editor (quoted if
  needed); double-clicking a card opens the table's data; the context menu offers *Open data*,
  `SELECT * FROM …`, *Insert table name* and *Copy table name*. `Ctrl+P` jumps to a table or column by
  name.
- **Table tabs** (`▦ name`) live in the results area next to the statement results of the current query
  tab and survive running queries. They page through the rows (page size = *Row limit*), sort on the
  *server* when you click a header (ascending, descending, off) and take a `WHERE` condition. The
  condition is checked before it is sent: exactly one condition, no `;` outside quotes. The primary key
  is always the last sort key, so consecutive pages neither repeat nor skip rows. Paging uses
  `LIMIT … OFFSET …`, which is exact but re-reads the skipped rows; keyset paging would be faster deep
  inside very large tables and is not done yet.

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

- **Native SIMD scanner** (`easydbms/core/simd/_native.c`). The statement splitter that decides what
  `Ctrl+Enter` runs is also on the path of every script run. A C extension does the same job over the raw
  string and skips string / comment / `$$` bodies with AVX2 (32 bytes per step) or SSE2 (16), chosen at
  run time with `__builtin_cpu_supports`; other CPUs use a scalar C loop. It is **optional**: it is built
  when a compiler is available, the pure-Python splitter is the reference and the fallback, and
  `EASYDBMS_NO_SIMD=1` turns it off. The extension returns "not mine" for the few inputs it does not
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

### Structure and diagram (stage 3)

`python -m benchmarks.schema_bench`: a synthetic schema of N tables (9 columns, a primary key, one or two
foreign keys and an index each), read with EasyDBMS's bulk catalog queries and with SQLAlchemy 2.0
(`MetaData.reflect()`, and an `Inspector` loop over the tables). Median of 5 runs, same machine; full
tables in [`benchmarks/results/schema-stage-3.md`](benchmarks/results/schema-stage-3.md).

| 1 000 tables, 9 000 columns, 1 332 foreign keys | EasyDBMS | SQLAlchemy `reflect()` | SQLAlchemy `Inspector` loop |
|---|---:|---:|---:|
| PostgreSQL 16 | **251 ms** | 1 238 ms (4.9×) | 8 851 ms (35×) |
| MariaDB 10.11 | **249 ms** | 1 315 ms (5.3×) | 1 229 ms (4.9×) |
| SQLite 3.45 | **79 ms** | 1 798 ms (22.6×) | 723 ms (9.1×) |

The two read different things: SQLAlchemy builds typed `Table` objects and reflects more (check
constraints, server defaults as expressions); EasyDBMS reads what the diagram and, in stage 4,
autocomplete need. Both returned the same table, column and foreign-key counts. What the user waits for
is the first column.

| Placing and drawing | 200 tables | 1 000 tables | 3 000 tables |
|---|---:|---:|---:|
| Automatic layout (layers, long-edge channels, packing) | 4.9 ms | 33 ms | 174 ms |
| Build the Qt scene (cards + lines) | 58 ms | 394 ms | — |
| Repaint with the whole diagram in view | 5 ms | 29 ms | — |
| Repaint at 100 % zoom | 14 ms | 28 ms | — |

The layout is pure Python and takes about a tenth of a second for the largest schema measured; it is
not an SIMD candidate — the time goes to building the scene, which is Qt objects, not to arithmetic.
Details that matter at that size: cards switch to a title-only drawing below 35 % zoom, lines are
skipped below 5 %, and only items inside the viewport are painted.

### ClickBench (1 M rows)

`python -m benchmarks.clickbench.run` runs the 43 official queries (3 runs each) on 1 M rows (1 % of the
dataset), default configuration of every engine, same machine (4 cores). Rows marked *EasyDBMS* go
through the application's own `DatabaseClient`; the others are the plain driver, DuckDB 1.5.6 and
ClickHouse 26.9 (`chdb`, in-process). It is repeated at the end of every stage. Stage 3 (full per-query
table in [`benchmarks/results/clickbench-stage-3.md`](benchmarks/results/clickbench-stage-3.md); stage 2 is
next to it):

| Runner | Relative score (lower is better) | Sum of best times, 43 queries | Stage 2 |
|---|---:|---:|---:|
| DuckDB 1.5.6 | 1.10 | 0.8 s | 0.8 s |
| ClickHouse 26.9 (chdb) | 1.34 | 1.1 s | 1.0 s |
| PostgreSQL 16 — EasyDBMS / psycopg | 15.21 / 15.19 | 18.2 s / 18.1 s | 18.4 s / 18.4 s |
| SQLite 3.45 — EasyDBMS / sqlite3 | 15.92 / 15.80 | 21.1 s / 20.9 s | 21.4 s / 21.4 s |
| MariaDB 10.11 — EasyDBMS / PyMySQL | 81.95 / 79.75 | 83.7 s / 81.3 s | 79.5 s / 79.6 s |

What it says, and what it does not:

- **The application layer adds almost nothing**: the editor's client against the bare driver, runs
  interleaved so neither warms the cache for the other: +0.7 % PostgreSQL, +1.2 % SQLite, +3.0 % MariaDB
  (stage 2: +0.0 %, +0.0 %, −0.2 %). The MariaDB figure is within what two connections of the *same* driver
  differ by: measured separately, two plain PyMySQL connections to the same server were 3.3 % apart on
  this suite, and the EasyDBMS connection sat between them. A MariaDB-only re-run gave +2.5 %. So the
  honest statement is "not distinguishable from zero at the ±3 % this setup can resolve".
- **Engine speed is the engine's**: DuckDB and ClickHouse, column stores built for this workload, finish
  the suite in about 1 s against 18–84 s for the row stores. EasyDBMS does not make PostgreSQL, MariaDB or
  SQLite faster and does not claim to; it is a client for them, not an engine.
- Nothing in stage 3 touched the query path (the structure is read on a second connection), and the
  numbers agree: the EasyDBMS sums moved by −0.9 % (PostgreSQL), −1.5 % (SQLite) and +5.4 % (MariaDB)
  since stage 2, while the plain PyMySQL row moved by +2.1 %, i.e. the same noise.
- Caveats: 1 M rows rather than 100 M, untuned servers sharing the machine with the benchmark, page cache
  not dropped (first run is warm), a single machine. Absolute numbers are not comparable with the public
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
easydbms/
├─ core/                    # no Qt
│  ├─ dialects/             # PostgreSQL / MySQL / SQLite: quoting, literals, lexer, splitter,
│  │                        #   formatter, translator, vocabulary, type classification
│  ├─ db/                   # DatabaseClient + PostgresClient, MySqlClient, SqliteClient, errors
│  ├─ connections/          # ConnectionConfig, URL parser, ${ENV}, secret stores, store
│  ├─ session/              # Session (query lane + meta lane, schema events), ConnectionManager
│  ├─ schema/               # Table / Column / ForeignKey / Index model + per-dialect introspection
│  ├─ erd/                  # relations, cardinality, layered layout, edge routing, saved positions
│  ├─ browse/               # SQL for paging, sorting and filtering a table
│  ├─ queries/              # query tabs, danger guard, script job
│  ├─ simd/                 # optional C extension: AVX2/SSE2 scanner + statement splitter
│  ├─ columnar.py           # Arrow/NumPy sort and filter of a result set
│  ├─ storage/              # app.db (migrations) and settings.toml
│  ├─ paths.py  services.py
├─ ui/                      # PySide6: main window, workspace, editor, results + table tabs,
│                           #   erd/ (cards, lines, view, pane), quick open, dialogs, theme, i18n
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
- **Two connections per session.** The query lane runs your statements; the meta lane (opened on
  first use) reads the structure and loads table pages. `disconnect()` cancels a running statement and
  waits for it before closing the connection — closing a SQLite connection under a running statement
  crashes the process. In-memory SQLite databases have only one connection, so the meta lane shares it.
- **MySQL/MariaDB: join in Python, not in the server.** Joining `KEY_COLUMN_USAGE` to
  `REFERENTIAL_CONSTRAINTS` inside `information_schema` took 590 ms for 1 000 tables where the two queries
  alone take 15 ms each, so the join is done on the client.
- **A missing SQLite file is an error**, not a new empty database (opt in with "Create the file").
- A damaged `connections.json` / `settings.toml` is never overwritten: it is moved aside as
  `*.broken-<timestamp>`, valid entries are kept, and the user is told.

## Dialect system

`easydbms.core.dialects` is the single place that knows how the three dialects differ.
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
EASYDBMS_FUZZ_ITERATIONS=60000 pytest tests/core/simd   # longer native-vs-Python differential fuzz
```

The suite is most valuable against real servers. Point it at empty throw-away databases:

```bash
export EASYDBMS_TEST_POSTGRES_URL="postgresql+psycopg://user:pass@localhost/easydbms_test"
export EASYDBMS_TEST_MYSQL_URL="mysql+pymysql://user:pass@localhost/easydbms_test"
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
3. ✅ **Schema introspection, ERD pane, table tabs**, `Ctrl+P` go to table, schema benchmarks.
4. Autocomplete (keywords → tables → columns → aliases → JOIN by FK).
5. Editable grid: change set, preview, Alt+S, one transaction, optimistic locking.
6. SSL, SSH tunnel, `~/.pgpass` / `pg_service.conf`.
7. Cloud providers, history and saved queries, ERD export, packaging.
