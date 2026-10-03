# SQL ERD Studio

Desktop database client in Python: SQL editor, ERD, editable result grid. Supported SQL
dialects: **PostgreSQL**, **MySQL** (including MariaDB) and **SQLite**. Corporate dialects
(SQL Server, Oracle) are intentionally out of scope.

**Status: stage 1 of 7 — connections.** You can create, test, save and switch between
connections to PostgreSQL, MySQL/MariaDB and SQLite files. The editor, results grid, ERD and
autocomplete are the next stages (see [Roadmap](#roadmap)).

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
uv pip install -e ".[dev]"
python -m sql_erd_studio          # or: sql-erd-studio
```

Config lives in the per-user config directory (`connections.json`, `settings.toml`, `vault.json`)
and data in the per-user data directory (`app.db`). Set `SQL_ERD_STUDIO_HOME=/some/dir` to keep
everything in one place (portable installs, experiments).

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
│  ├─ session/              # Session (state machine) and ConnectionManager
│  ├─ storage/              # app.db (migrations) and settings.toml
│  ├─ paths.py  services.py
├─ ui/                      # PySide6: main window, connection dialog, DB switcher, theme, i18n
└─ app.py
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
- **`execute(max_rows=…)` does not bound memory for PostgreSQL**: psycopg buffers the whole
  result. Page the SQL itself with `dialects.paginate()` (the grid will do this in stage 2).
  MySQL uses an unbuffered cursor and stops the server-side query when the limit is hit.
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
2. SQL editor (tabs, highlighting from the dialect lexer), run/cancel, read-only results grid.
3. Schema introspection, ERD pane, table tabs.
4. Autocomplete (keywords → tables → columns → aliases → JOIN by FK).
5. Editable grid: change set, preview, Alt+S, one transaction, optimistic locking.
6. SSL, SSH tunnel, `~/.pgpass` / `pg_service.conf`.
7. Cloud providers, history and saved queries, ERD export, packaging.
