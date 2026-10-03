# EasyDBMS

**English** · [Українська](README.uk.md)

A desktop database client for **PostgreSQL**, **MySQL / MariaDB** and **SQLite**. Write SQL with smart
autocomplete, see your database as an ER diagram, edit table data in a grid and review every change before it is
written. Works on Windows, macOS and Linux, in English and Ukrainian, with a dark and a light theme.

| ER diagram and SQL editor | Review edits before they are written | Connection settings |
|---|---|---|
| ![The editor, results and the diagram](docs/screenshots/stage3-diagram.png) | ![Pending edits in the grid](docs/screenshots/stage5-pending.png) | ![TLS settings](docs/screenshots/stage6-ssl.png) |

## What you can do

- **Connect to your databases.** Save as many connections as you like, group them, give them a colour label and
  switch between them from one menu. Passwords are kept in your system keyring (or an encrypted vault), never in a
  settings file. *Test* shows exactly which step fails: DNS, port, sign-in, TLS or the query.
- **Connect securely.** SSL/TLS with CA and client certificates, an SSH tunnel (password, key or ssh-agent, with a jump
  host), `~/.pgpass` and `pg_service.conf`, `${ENV_VAR}` placeholders, and short-lived tokens for AWS RDS, Google
  Cloud SQL and Azure. Templates for Supabase, Neon, PlanetScale and CockroachDB Cloud.
- **Write SQL faster.** Tabs that survive a restart, syntax highlighting for your dialect, autocomplete for tables,
  columns, aliases and joins, snippets, one-key formatting, several statements per script with a result tab for each,
  cancel at any time.
- **See the structure.** An ER diagram with primary and foreign keys, 1:1, 1:N and N:M relations, search, zoom and
  schema selection. Export it as PNG, SVG, PDF, Mermaid or DBML.
- **Edit data safely.** Change cells, add and delete rows right in the grid. Nothing is written until you press
  <kbd>Alt+S</kbd>: you see the generated SQL, everything runs in one transaction, and a row changed by somebody else
  in the meantime stops the whole batch instead of being overwritten.
- **Keep your work.** Every statement you run is kept in a searchable history; save the ones you reuse into folders.
- **Stay safe in production.** Production connections get a red banner and ask before they change data; dangerous
  statements (`DROP`, `TRUNCATE`, `DELETE` or `UPDATE` without a condition) ask first; a connection can be read-only.

## Supported databases

| Database | Versions tested | Notes |
|---|---|---|
| PostgreSQL | 16 | Schemas, TLS, SSH, `~/.pgpass`, `pg_service.conf`, IAM tokens |
| MySQL / MariaDB | MariaDB 10.11 | TLS, SSH, IAM tokens |
| SQLite | 3.x | A file on disk or `:memory:` |

SQL Server, Oracle and ClickHouse are not supported.

## Install and run

You need **Python 3.12 or newer**. The quick path, in a terminal:

```bash
git clone https://github.com/nkhp8djnyt-max/easy-db.git
cd easy-db
python -m venv .venv
. .venv/bin/activate              # Windows (PowerShell): .venv\Scripts\Activate.ps1
pip install -e ".[gui]"
python -m easydbms
```

On Linux, Qt needs a few system libraries; if the window does not open, install them first:

```bash
sudo apt install libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3 libxcb-cursor0
```

Optional extras: `pip install -e ".[cloud]"` adds the AWS, Azure and Google sign-in libraries
(`[aws]`, `[azure]`, `[gcp]` pick one). To build a standalone application folder, run
`pip install -e ".[gui,build]"` and then `python scripts/build_app.py`.

**Your first five minutes**

1. Start the program and click **Connections…** (<kbd>Ctrl+Shift+C</kbd>).
2. Click **+ New**, choose **SQLite**, press **New…** and pick a file name. Or choose **PostgreSQL** or
   **MySQL / MariaDB** and fill in the host, user and database.
3. Press **Test**, then **Connect**.
4. Type `SELECT 1;` in the editor and press <kbd>Ctrl+Enter</kbd>. The diagram of your tables appears on the right.
5. Double-click a table in the diagram to browse its data; double-click a cell to edit it, then press
   <kbd>Alt+S</kbd> to review and write the change.

The full walk-through, with every connection option, is in the **[user guide](docs/user-guide.md)**.

## Keyboard shortcuts

| Keys | Action |
|---|---|
| <kbd>Ctrl+Shift+C</kbd> | Open the connections window |
| <kbd>Ctrl+Enter</kbd> | Run the selection or the current statement |
| <kbd>F5</kbd> | Run the whole script |
| <kbd>Esc</kbd> | Stop the running query |
| <kbd>Ctrl+Space</kbd> | Show suggestions |
| <kbd>Ctrl+Shift+F</kbd> | Format the SQL |
| <kbd>Ctrl+T</kbd> / <kbd>Ctrl+W</kbd> | New / close query tab |
| <kbd>Ctrl+S</kbd> | Save the query |
| <kbd>Ctrl+H</kbd> / <kbd>Ctrl+Shift+H</kbd> | History / saved queries |
| <kbd>Alt+S</kbd> | Review and write the edits made in the grid |
| <kbd>Ctrl+P</kbd> | Go to a table or column |
| <kbd>Ctrl+Shift+R</kbd> | Re-read the database structure |
| <kbd>Ctrl+E</kbd> | Export the diagram |

## Where your data lives

Connections, settings and the trusted SSH servers are stored in your user configuration folder; the history, saved
queries and tab contents in the data folder (on Linux `~/.config/easydbms` and `~/.local/share/easydbms`). Passwords
and key passphrases are only ever stored in the system keyring or the encrypted vault. There is no telemetry: the program contacts
only the servers you set up (databases, SSH hosts and, for token sign-in, your cloud account). Set `EASYDBMS_HOME=/some/folder` to keep everything in one place, for example on a
USB stick.

## Documentation

- **[User guide](docs/user-guide.md)**: install, connect, secure connections, editor, diagram, editing, troubleshooting.
- [Development notes](docs/development.md): architecture, how each feature works, tests, packaging.
- [Roadmap page](docs/roadmap.html) (open it in a browser; it has a language switch).

## Roadmap

The seven planned stages are finished. The list below shows what each one contains, what has been verified against
real systems and what has not, and what is proposed next. The same content, with a language switch, is in
[docs/roadmap.html](docs/roadmap.html).

<!-- roadmap:start -->

### What is done

The stages went in order: each built on the previous one and ended with a test run, screenshots and documentation.

**1. Connections** — The base: the connection model, the database clients and the Connections window.

- PostgreSQL, MySQL/MariaDB and SQLite clients behind one interface, with readable errors.
- Parsing and building connection URLs; `${ENV}` substitution in fields.
- Passwords only in the system keyring or an encrypted vault, never in JSON.
- A Test button that diagnoses step by step: DNS, port, sign-in, query. A database switcher above the diagram.

**2. SQL editor** — Query tabs, running and cancelling, a results table.

- Dialect-aware highlighting, tabs restored on start, SQL formatting.
- `Ctrl+Enter` runs the statement under the cursor, `F5` the whole script; each statement's result gets its own tab.
- Guards against the dangerous: `DROP`, `TRUNCATE`, `DELETE` and `UPDATE` without a condition, writes to a production database.
- An optional C scanner (AVX2/SSE2/NEON) with a pure-Python fallback; a ClickBench rig.

**3. Schema and ER diagram** — The database structure is read in the background and drawn as a diagram.

- Tables, columns, primary and foreign keys, indexes; 1:1 and 1:N relations, N:M junction tables.
- Layered layout, orthogonal lines, zoom and pan; card positions are remembered.
- Search over tables and columns, schema choice, `Ctrl+P` to jump to a table, tabs with a table's data.

**4. Autocomplete** — Suggestions from the dialect's syntax and from the connected database's schema.

- Keywords, tables, columns, aliases and CTEs; a `JOIN` condition from the foreign key; `*` expansion.
- The cursor context copes with unfinished queries; snippets; frequently chosen items rise to the top.

**5. Editable grid** — Edits in the results table that are not written without confirmation.

- Cell editing, new rows, deletion, undo and redo; editors that fit the type: date, boolean, number.
- `Alt+S` shows the generated `UPDATE`/`INSERT`/`DELETE`; everything is written in one transaction: all or nothing.
- Optimistic locking: if someone else changed a row, the transaction rolls back and names the row.
- A table without a primary key: the key columns can be chosen by hand.

**6. Secure connections** — SSL/TLS, the SSH tunnel and the PostgreSQL files.

- **TLS:** modes from `disable` to `verify-full`, a CA, a client certificate and a key with a passphrase. The files are checked before connecting; after sign-in the TLS version and cipher are shown.
- **SSH tunnel:** password, key or ssh-agent, and a jump host (bastion). The server key is verified before any credentials are sent; an unknown key needs explicit trust, a changed key is refused.
- **PostgreSQL files:** `~/.pgpass` and `pg_service.conf`; a file other users can read is ignored, as in libpq.
- Connection test steps: service, password source, SSH, tunnel, TLS files, encryption, sign-in, query.

**7. Clouds, history, export, build** — The last stage of the plan. The interface language was later changed from Russian to Ukrainian.

- **History and saved queries:** `Ctrl+H`, `Ctrl+Shift+H`, `Ctrl+S`. Search, failures only, folders, a scope of “one connection” or “all connections”.
- **Diagram export** (`Ctrl+E`): PNG, SVG, PDF, and the text formats Mermaid and DBML.
- **Cloud providers:** AWS RDS/Aurora, Google Cloud SQL and Azure Entra work through a token instead of a password. Supabase, Neon, PlanetScale and CockroachDB get field templates.
- **The System theme** follows the operating system's light or dark setting and switches live.
- **Packaging:** PyInstaller, `scripts/build_app.py`, CI on every commit, Linux, Windows and macOS builds on demand.

### What is verified and what is not

The tests are green, but not everything in them is equally close to reality. This shows where a check is real and where a stand-in replaces it.

| Area | Status | How it was checked |
|---|---|---|
| PostgreSQL 16, MariaDB 10.11, SQLite | ✅ live | The whole test suite, including grid editing, schema, TLS and tunnel, against real servers. |
| TLS and client certificates | ✅ live | PostgreSQL and MariaDB with a test certificate authority: `verify-ca`, `verify-full`, certificate login, an encrypted key. |
| SSH tunnel | 🟡 partly | Against an SSH server and ssh-agent written for the tests (paramiko); real databases behind the tunnel. The system OpenSSH was not used. |
| AWS RDS (IAM) | 🟡 partly | The token is signed locally by the real boto3. Signing in to a live RDS cluster was not tried. |
| Azure Entra, Google Cloud SQL | 🟡 on stand-ins | Token and error logic checked against fake SDK modules; no cloud accounts were available. |
| Supabase, Neon, PlanetScale, CockroachDB | ⬜ not checked | Only the values the templates fill in are tested. No real service was connected. |
| Oracle MySQL | ⬜ not checked | The MySQL dialect is tested on MariaDB 10.11; Oracle MySQL servers were not run. |
| Application build | 🟡 Linux only | On Linux PyInstaller builds the app; it starts and prints its version. The Windows and macOS workflow is written but has never run. |
| Speed (ClickBench, schema) | ⬜ postponed | The benchmark rigs are ready; the runs are postponed until they are started separately. |

### What is proposed next

These are proposals, not commitments. The size S, M, L is a rough estimate of the work; the order inside a group reflects the value to users.

**Close the gaps.** The checks that stages 6 and 7 still need before they count as fully closed.

- **Windows and macOS builds** (`S`) — Run the build workflow on all three systems and make sure the app starts.
- **Real cloud accounts** (`S`) — One sign-in run against RDS, Azure and Cloud SQL, then Supabase, Neon, PlanetScale and CockroachDB.
- **System OpenSSH** (`S`) — Run the tunnel and the jump host against a real `sshd` instead of the test server.
- **Benchmarks** (`S`) — Run ClickBench and the schema tests and keep the results next to the code.

**New features.** What the app still lacks next to mature database clients.

- **Export results to a file** (`S`) — CSV, JSON, XLSX and Parquet from the grid. Copying as CSV and Markdown already exists.
- **Query plan** (`M`) — `EXPLAIN` and `EXPLAIN ANALYZE` as a tree, with the most expensive nodes highlighted.
- **CSV import into a table** (`M`) — Column mapping, a preview, loading in one transaction.
- **Table designer** (`L`) — Creating and changing tables, indexes and keys, with a DDL preview before applying.
- **Schema diff and migrations** (`L`) — The differences between two databases and a generated migration script.

**Release.** So the app can be used without Python.

- **Signed installers** (`M`) — MSI or NSIS for Windows, a notarised DMG for macOS, an AppImage for Linux.
- **Auto-update** (`M`) — Checking for a new version and updating without a manual reinstall.
- **Cloud SQL Connector** (`M`) — Embed Google's connector instead of connecting by IP or through the Auth Proxy.
- **DuckDB files** (`M`) — A fourth dialect. The client architecture allows it; the original plan postponed it.

### Not planned

Decisions made at the start of the project.

- **SQL Server, Oracle, ClickHouse.** Corporate dialects are out of scope: the app supports three dialects.

<!-- roadmap:end -->
