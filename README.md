# EasyDBMS

Desktop database client in Python: SQL editor, ERD, editable result grid. Supported SQL
dialects: **PostgreSQL**, **MySQL** (including MariaDB) and **SQLite**. Corporate dialects
(SQL Server, Oracle) are intentionally out of scope.

**Status: stage 6 of 7 — secure connections.** Everything from before (SQL editor with autocomplete, ER diagram,
editable grid) now also works **through SSL/TLS, an SSH tunnel (with a jump host) and the PostgreSQL
`~/.pgpass` / `pg_service.conf` files**. *Test connection* walks the whole path — service file, password source,
SSH jump host, SSH login, tunnel, TLS files, encryption, login, test query — and says which step broke and why.
Cloud providers, history and packaging are the last stage (see [Roadmap](#roadmap)).

| TLS: modes and certificate files | SSH tunnel with a jump host | A test through the tunnel |
|---|---|---|
| ![](docs/screenshots/stage6-ssl.png) | ![](docs/screenshots/stage6-ssh.png) | ![](docs/screenshots/stage6-tunnel-test.png) |

More: [an SSH server nobody trusted yet — fingerprint and a *Trust this server…* button](docs/screenshots/stage6-trust.png),
[light theme, Russian UI](docs/screenshots/stage6-ssh-light-ru.png).

| Pending changes | Review before writing | Someone else changed the row |
|---|---|---|
| ![](docs/screenshots/stage5-pending.png) | ![](docs/screenshots/stage5-review.png) | ![](docs/screenshots/stage5-conflict.png) |

More: [a join result is read-only, and says why](docs/screenshots/stage5-readonly.png),
[light theme, Russian UI](docs/screenshots/stage5-pending-light-ru.png).

| Tables after `FROM` | Columns of an alias | Join condition from the foreign key |
|---|---|---|
| ![](docs/screenshots/stage4-tables.png) | ![](docs/screenshots/stage4-columns.png) | ![](docs/screenshots/stage4-join.png) |

More: [snippets and keywords](docs/screenshots/stage4-snippets.png),
[expand `*`](docs/screenshots/stage4-star.png),
[light theme, Russian UI](docs/screenshots/stage4-light-ru.png).

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

## What stage 6 does

- **SSL / TLS** (PostgreSQL and MySQL/MariaDB). Modes `disable`, `allow` (PostgreSQL), `prefer`, `require`,
  `verify-ca`, `verify-full` plus a CA bundle, a client certificate and key (the key's passphrase is a secret,
  never saved in the JSON). The same settings are accepted as URL parameters (`?sslmode=…&sslrootcert=…`, MySQL's
  `ssl_ca` / `ssl_mode`) and old `options` are migrated. The test looks at the files *before* connecting — missing,
  not a certificate, expired (with the date), key of another certificate, encrypted key without / with the wrong
  passphrase — and afterwards reports what the server really negotiated (`TLSv1.3 · TLS_AES_256_GCM_SHA384`).
- **SSH tunnel.** Password, private key (+ passphrase) or ssh-agent; optionally through a **jump host**. It is opened
  automatically before the database connection and closed after disconnect (also when connecting fails or is
  abandoned). The connection keeps the real host name — TLS verifies the certificate against it and `~/.pgpass`
  is looked up by it — while only the socket goes to the tunnel's local port (`hostaddr` for libpq, a pre-made
  socket for PyMySQL). A session's second ("meta") connection shares the same tunnel: no second login.
- **Host keys are checked before any credential is sent.** Our own `known_hosts` (OpenSSH format, in the config
  directory) plus the read-only `~/.ssh/known_hosts`. An unknown server is **never** accepted silently: the test
  report and the connection error page show its fingerprint and a *Trust this server…* button; a **changed** key
  is refused with a warning.
- **`~/.pgpass`** (honours `PGPASSFILE`, `%APPDATA%\postgresql\pgpass.conf` on Windows): wildcards, `\:` / `\\`
  escapes, first match wins, and — like libpq — a file other users can read is ignored (with the `chmod 0600`
  hint). The form shows whether the file knows the connection, never the password. A password that is saved or
  typed wins; the dialog does not ask when `~/.pgpass` or the service already has one.
- **`pg_service.conf`** (`PGSERVICEFILE`, `~/.pg_service.conf`, `PGSYSCONFDIR`): name a *service* in the connection
  and the host, port, user, database, TLS settings and extra libpq parameters it defines fill in whatever the
  connection leaves empty (what the connection says wins). The host may stay empty.
- **Secrets** (database password, SSH password / key passphrase, jump-host ones, TLS key passphrase) live only in the
  keyring / encrypted vault — or in memory for this run if *Save* is off — and are asked for when missing. Nothing
  secret reaches `connections.json`, URLs or logs; deleting a connection forgets all of them, switching the login
  method forgets the old secret.
- **Diagnostics.** Steps: `Service file`, `Password source`, `SSH jump host`, `SSH login`, `SSH tunnel`, `TLS files`,
  `Encryption`, then the usual `DNS`/`TCP` (skipped behind a tunnel — the SSH steps proved the way), `Sign in`,
  `Test query`. Failures come with advice: wrong SSH password / key, the SSH server cannot reach the database
  ("check them from the SSH server's point of view"), forwarding prohibited, undefined `${VAR}`, unknown service.
- `${ENV}` placeholders are expanded in the service name, TLS file paths and SSH host / user / key file too.

## What stage 5 does

- **What can be edited.** The rows of one table: a table tab, or the result of a plain
  `SELECT … FROM table [WHERE …] [ORDER BY …] [LIMIT …]` (aliases and column lists are fine, as long as
  the key columns are in the list). Everything else — joins, `GROUP BY`, `DISTINCT`, aggregates, CTEs,
  subqueries in `FROM`, views, a read-only connection — is read-only, and the strip under the grid says why
  (*"Read-only: this is not the rows of a single table …"*). A table **without a primary key** uses a unique
  index if it has one; otherwise *Choose key columns…* lets you tick the columns that identify a row (remembered
  per connection in `app.db`, migration 5). A change that would touch more than one row is refused.
- **Editing.** `F2` / double-click opens an editor that fits the type: a combo for booleans (with NULL), a
  calendar for dates, date-time and time editors, a checked line for numbers (validators), a line for text
  (long or multi-line text opens a dialog). The right-click menu has *Set NULL*, *Use the default* (new rows),
  *Edit in a dialog…*, *Revert this cell / row*. `Ctrl+N` adds a row, `Ctrl+D` duplicates the selected rows (key
  columns left to the database), `Del` marks rows for deletion (again: restores them), `Ctrl+Z` / `Ctrl+Y` undo and
  redo, `Esc` discards everything pending (itself undoable). Empty input means NULL for every type but text.
- **Colours.** Edited cells are yellow (the tooltip says what the value *was*), new rows green (cells you did
  not fill show `default`, `NULL` or `required`), rows marked for deletion red and struck through, a statement that
  failed marks its cell dark red with the server's message in the tooltip.
- **The change set survives** sorting, filtering, paging and reloading (rows are identified by their key, not
  by position); new rows stay at the end of every page. `⟳` reload keeps your edits and measures them against the
  fresh rows.
- **Review, then one transaction.** `Alt+S` (or *Query → Apply changes*) opens the review: the SQL with the
  values written out, how many rows will be changed / added / deleted. *Apply* runs the statements with bound
  parameters in **one transaction** on the connection's second ("meta") lane, so a running query is not
  disturbed. On a **production** connection a second question follows. Deletes run first, then updates, then
  inserts. The statement text never contains your values — they are parameters.
- **Optimistic locking.** An `UPDATE` finds its row by the key it was loaded with *and* by the old value of every
  cell it changes (`col = old` / `col IS NULL`). If somebody else changed or deleted the row meanwhile it matches
  nothing: the whole transaction is rolled back, the row is named in the message, your edits stay in the grid,
  and *Reload* lets you rebase them on the current rows and apply again. Floats, JSON, binary and arrays are
  not compared (equality is unreliable) — they rely on the key.
- **Errors.** A constraint violation, a NOT NULL, a duplicate key: rolled back, `Nothing was written.`, the cell
  of the failing statement is marked, the edits are kept. Applying without a connection is reported in the strip,
  not raised.
- **Don't lose edits by accident.** Closing a table tab or a query tab, running a statement that replaces an edited
  result, or quitting the application with pending changes asks first.
- **Copy** (all grids): `Ctrl+C` / `Ctrl+Shift+C` as TSV, *Copy as CSV*, *Copy as Markdown* from the context menu.

## What stage 4 does

- **Where am I?** Every suggestion starts from an analysis of the statement around the cursor
  (`core/autocomplete/context.py`): which clause the cursor is in, which tables (and aliases, CTEs,
  derived tables, `JOIN … USING`) are visible from there, whether a name is being qualified
  (`o.`, `public.orders.`), whether a quote is open, whether the cursor is in a string or comment (nothing
  is offered there). It reads the *tokens* of the dialect's own lexer, so it works on text that does not
  parse yet — `SELECT count(| FROM orders` still knows about `orders` — and asks `sqlglot` only for the
  output columns of finished subqueries and CTEs (with a token fallback, `*` expanded from the schema).
- **What is offered where.**
  statement start → snippets, then statement keywords (per dialect: `COPY` only in PostgreSQL, `PRAGMA`
  only in SQLite, …) · after `FROM` / `JOIN` / `UPDATE` / `INSERT INTO` → tables, views, CTEs, schemas
  (tables related by a foreign key to the ones already in the query come first after `JOIN`) ·
  `SELECT` / `WHERE` / `GROUP BY` / `ORDER BY` / `ON` → columns of the query's tables (qualified with the
  alias when the name is ambiguous), then aliases, outer-query columns in a subquery, functions and
  keywords · `alias.` / `table.` / `schema.` → its columns / tables · after `JOIN t ON` → the ready
  condition from the foreign key (`o.customer_id = c.id`, composite keys and self joins included) ·
  `ORDER BY` → also the select-list aliases · `INSERT (…` / `SET` / `ALTER … DROP COLUMN` → the columns
  of the target not yet listed · `::` / `CAST(… AS` / column definitions → types · after a complete
  clause → the keywords that may follow (`GROUP` → `BY`, `LEFT` → `JOIN`).
  `Ctrl+Space` on a `*` offers to **expand it into the column list**.
- **Order.** The context decides first (columns of `FROM` before functions before keywords), then how
  well the typed text matches (exact, prefix, word start, three or more letters inside, letters in order
  for abbreviations such as `ordit` → `order_items`, and a typo-tolerant `rapidfuzz` match: `custmer` →
  `customers`), then how often you accepted the suggestion on this connection (`app.db`, migration 4),
  then alphabetically.
- **The popup.** Kind badge (table, view, column, alias, keyword, function, type, snippet, join, CTE),
  the column's type or the table's size on the right, and a details pane (table comment and columns,
  column flags and foreign key, function signature). `Tab` / `Enter` accept, `Esc` closes, `↑` `↓`
  `PgUp` `PgDn` move, double-click accepts, `Ctrl+Space` (or *Query → Autocomplete*) forces it. It opens
  150 ms after you stop typing, after `.` and after a space following `FROM`, `JOIN`, `INTO`, `UPDATE`
  or `ON`; the analysis runs in a worker thread and an answer for text that has since changed is
  discarded. The popup never takes the keyboard focus, so typing goes on while it is open.
- **Snippets** (`core/autocomplete/snippets.py`): `sel`, `selc`, `seld`, `ins`, `upd`, `del`, `cte` at
  the start of a statement, `ij` `lj` `rj` `cj` after a table in `FROM` / `JOIN`, `ob` `gb` `lim` after a
  clause. The cursor lands where you type next (`INNER JOIN | ON `).
- **Keyword case** — *Query → Keyword case*: UPPER (default), lower, or as typed (follows the case of
  the letters already typed). It affects keywords, functions, types and snippets, never identifiers.
- Names are quoted only when the dialect needs it (`"Order Lines"`, `` `select` ``); inside an open quote
  only identifiers are offered and the closing quote is kept.

Not in this stage: the inside of `$$ … $$` bodies (the editor sees one string there, so nothing is
offered) and JSON keys after `->`. The ClickBench / schema benchmarks were deliberately not rerun for
stage 4.

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
│  ├─ db/                   # DatabaseClient + PostgresClient, MySqlClient, SqliteClient, errors, TLS files / driver mapping
│  ├─ connections/          # ConnectionConfig (+ SSL / SSH settings), URL parser, ${ENV}, secret stores, store,
│  │                        #   ~/.pgpass + pg_service.conf, `complete()` (service / password sources)
│  ├─ ssh/                  # SshTunnel (paramiko; password / key / agent, jump host), known_hosts trust
│  ├─ session/              # Session (query lane + meta lane, schema events), ConnectionManager,
│  │                        #   connector (prepare = service + secrets + tunnel; check_connection)
│  ├─ schema/               # Table / Column / ForeignKey / Index model + per-dialect introspection
│  ├─ erd/                  # relations, cardinality, layered layout, edge routing, saved positions
│  ├─ browse/               # SQL for paging, sorting and filtering a table
│  ├─ queries/              # query tabs, danger guard, script job
│  ├─ autocomplete/         # cursor context, candidates + ranking, snippets, usage counts
│  ├─ editing/              # change set (undo/redo), value parsing, UPDATE/INSERT/DELETE builder, targets
│  ├─ simd/                 # optional C extension: AVX2/SSE2 scanner + statement splitter
│  ├─ columnar.py           # Arrow/NumPy sort and filter of a result set
│  ├─ storage/              # app.db (migrations) and settings.toml
│  ├─ paths.py  services.py
├─ ui/                      # PySide6: main window, workspace, editor (+ completion popup), results +
│                           #   table tabs (+ the editable grid: model, delegates, review dialog), erd/ (cards, lines, view, pane), quick open, dialogs, theme, i18n
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
- **Applying edits** (`DatabaseClient.apply`): `BEGIN`, every statement with bound parameters, the affected row
  count of each is checked against what it must be (1), `COMMIT`; anything else rolls back and is reported with the
  index of the failing statement. MySQL connections are opened with `CLIENT_FOUND_ROWS`, so "rows affected" counts
  rows an `UPDATE` *matched* (as PostgreSQL and SQLite do), which is what tells "row not found" from "row already has
  these values". SQLite starts with `BEGIN IMMEDIATE`. Values typed as text are parsed per column type
  (`core/editing/values.py`); whether a cell *changed* is decided on the values, not their spelling (`5.0` is `5.00`,
  MySQL's `TIME` as `timedelta`, SQLite's text dates, JSON key order).
- **Autocomplete never blocks typing.** The analysis (`Completer.complete`) is a pure function of
  `(text, offset, dialect, schema)`; the editor runs it on one worker thread per connection and drops the
  answer if the text or the cursor moved. Sentences the core writes itself ("4 cols", "foreign key …") go
  through `autocomplete.messages.t()`, which the UI points at its Russian catalog, so the core still
  knows no UI toolkit and no language.
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
ruff check . && ruff format --check . && mypy      # mypy runs in strict mode, tests included
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

### Testing TLS and SSH

`tests/support/` holds an in-process SSH server, ssh-agent and a throw-away PKI, so the SSH tunnel and TLS-file tests
need nothing installed. To run the TLS tests against real servers, generate certificates and point both servers at
them:

```bash
python -m tests.support.certs /tmp/pki      # ca.pem, server.pem/.key, client.pem/.key (CN erd_cert), client-encrypted.key, other-ca.pem
export EASYDBMS_TEST_TLS_DIR=/tmp/pki
```

PostgreSQL: `ssl = on`, `ssl_cert_file` / `ssl_key_file` = `server.pem` / `server.key`, `ssl_ca_file = ca.pem`, a
`hostssl <db> erd_cert 127.0.0.1/32 cert` line in `pg_hba.conf` and `CREATE ROLE erd_cert LOGIN`.
MariaDB: `ssl_ca` / `ssl_cert` / `ssl_key` in `[mysqld]` and `CREATE USER erd_cert@'%' REQUIRE X509`. Without
`EASYDBMS_TEST_TLS_DIR` those tests are skipped; the tunnel-to-a-real-database tests run whenever the server URLs above are set.

## Roadmap

1. ✅ **Connections** — models, URL parsing, secrets, PostgreSQL/MySQL/SQLite clients, dialog,
   switcher.
2. ✅ **SQL editor** (tabs, highlighting from the dialect lexer), run/cancel, read-only results grid,
   optional native SIMD scanner, ClickBench harness.
3. ✅ **Schema introspection, ERD pane, table tabs**, `Ctrl+P` go to table, schema benchmarks.
4. ✅ **Autocomplete** (keywords → tables → columns → aliases → JOIN by FK), snippets, usage ranking.
5. ✅ **Editable grid**: change set, review (Alt+S), one transaction, optimistic locking, key columns.
6. ✅ **SSL / TLS, SSH tunnel** (password / key / agent, jump host, host-key trust), **`~/.pgpass`** and **`pg_service.conf`**.
7. Cloud providers, history and saved queries, ERD export, packaging.
