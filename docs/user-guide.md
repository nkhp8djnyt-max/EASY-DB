# EasyDBMS user guide

**English** · [Українська](user-guide.uk.md) · [Back to the README](../README.md)

This guide takes you from installing EasyDBMS to editing data on a production server. Each section can be read on its
own.

**Contents**
[1. Install](#1-install) ·
[2. First start](#2-first-start) ·
[3. Connect to a database](#3-connect-to-a-database) ·
[4. Secure connections](#4-secure-connections) ·
[5. Cloud databases](#5-cloud-databases) ·
[6. Write and run SQL](#6-write-and-run-sql) ·
[7. The diagram](#7-the-diagram) ·
[8. Browse and edit data](#8-browse-and-edit-data) ·
[9. History and saved queries](#9-history-and-saved-queries) ·
[10. Settings and files](#10-settings-and-files) ·
[11. Troubleshooting](#11-troubleshooting) ·
[12. Shortcuts](#12-keyboard-shortcuts)

## 1. Install

**You need:** Python 3.12 or newer, and about 400 MB of disk space (most of it is the Qt toolkit).

### Windows

1. Install Python 3.12+ from [python.org](https://www.python.org/downloads/) (tick *Add python.exe to PATH*).
2. Open PowerShell in the folder where you want the program and run:

```powershell
git clone https://github.com/nkhp8djnyt-max/easy-db.git
cd easy-db
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[gui]"
python -m easydbms
```

### macOS

```bash
git clone https://github.com/nkhp8djnyt-max/easy-db.git
cd easy-db
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[gui]"
python -m easydbms
```

### Linux

Install the Qt system libraries first (Debian / Ubuntu names; other distributions have equivalents):

```bash
sudo apt install python3.12-venv libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3 libxcb-cursor0
git clone https://github.com/nkhp8djnyt-max/easy-db.git
cd easy-db
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e ".[gui]"
python -m easydbms
```

### Starting it again later

Open a terminal in the `easy-db` folder, activate the environment (`. .venv/bin/activate`, or
`.venv\Scripts\Activate.ps1` on Windows) and run `python -m easydbms` (or just `easydbms`). To update, run `git pull`
and `pip install -e ".[gui]"` again.

### Optional extras

| Command | What it adds |
|---|---|
| `pip install -e ".[cloud]"` | Sign-in libraries for AWS, Azure and Google Cloud (see [section 5](#5-cloud-databases)) |
| `pip install -e ".[aws]"`, `".[azure]"`, `".[gcp]"` | Only one of them |
| `pip install -e ".[gui,build]"` | The tools to build a standalone application folder |

### A standalone application folder

If you would rather not start the program from a terminal, build a folder with an application inside:

```bash
pip install -e ".[gui,cloud,build]"
python scripts/build_app.py --check
```

The result is `dist/EasyDBMS/`; start `EasyDBMS` (`EasyDBMS.exe` on Windows) from there, or on macOS open
`dist/EasyDBMS.app`. You can copy the folder to another computer with the same operating system.

## 2. First start

The window has three parts: the **query area** on the left (editor above, results below), the **diagram** on the
right, and a menu bar with **File**, **Query**, **Database** and **View**. Until you connect, the left side shows
*No connection selected* with a **Connections…** button.

- **Language:** *View → Language* (*System default*, *Українська*, *English*). It applies the next time you start the
  program. By default it follows your system language: Ukrainian for Ukrainian systems, English otherwise.
- **Theme:** *View → System theme / Dark theme / Light theme*. The system theme follows your operating system and
  switches by itself when it does.
- **Row limit:** *Query → Row limit* sets how many rows a result keeps (100, 1 000, 10 000 or 100 000). A larger
  result is cut off and the grid says so.

## 3. Connect to a database

Open **Connections…** (<kbd>Ctrl+Shift+C</kbd>). On the left are your saved connections, grouped; on the right is the
form for the selected one.

### SQLite

1. Click **+ New** and choose **SQLite**.
2. Press **Open…** to pick an existing file, or **New…** to create one (the *Create the file if it does not exist*
   box is then ticked for you). The path `:memory:` gives a temporary database that disappears when you disconnect.
3. Type a **Name** at the top, press **Connect**.

### PostgreSQL and MySQL / MariaDB

1. Click **+ New** and choose **PostgreSQL** or **MySQL / MariaDB**.
2. Fill in one of two ways:
   - **Host / Port** tab: host, port (empty means the default, 5432 or 3306), user, database, and optional
     *Extra parameters* such as `connect_timeout=5`.
   - **URL** tab: paste a connection URL such as `postgresql://app@db.example.com:5432/shop?sslmode=require`. The
     fields fill themselves, and editing a field rewrites the URL. A password in the URL is moved to the password
     box, never kept in the URL.
3. Type the **Password**. Leave **Save the password** ticked to keep it in the system keyring (or the encrypted
   vault, see below); untick it to be asked every time you connect.
4. Give the connection a **Name**, and optionally a **Colour** and a **Group** to keep a long list tidy.
5. Press **Test**, then **Connect**.

**Production and read-only.** Tick **Production database** for anything real: the connection gets a red label and a
banner, and the program asks before changing data. Tick **Read-only** and the session refuses every write.

**Placeholders.** Any text field may contain `${NAME}` or `${NAME:-fallback}`; it is replaced from your environment
variables when you connect, so the saved connection holds no secrets or machine-specific paths. Write `$${` for a
literal `${`.

### What *Test* tells you

*Test* walks through the connection step by step and stops at the first one that fails, with advice:

| Step | What it checks |
|---|---|
| Service file, Password source | `pg_service.conf` and `~/.pgpass` were found and which line was used |
| Cloud token | A cloud sign-in token could be created ([section 5](#5-cloud-databases)) |
| SSH jump host, SSH login, SSH tunnel | The bastion, the SSH server and the path to the database |
| DNS lookup, Port reachable | The name resolves and something is listening (skipped behind a tunnel) |
| TLS files | Your certificate and key files exist, are valid and belong together |
| Sign in, Encryption | The login worked; which TLS version and cipher were negotiated |
| Test query | `SELECT 1` ran |

### The password store

Passwords go to your system keyring (Windows Credential Manager, macOS Keychain, Secret Service on Linux). If none is
available, EasyDBMS uses an **encrypted vault file** protected by a master password that it asks for the first time
it is needed. Nothing secret is ever written to `connections.json`.

## 4. Secure connections

Open the **SSL / TLS** and **SSH tunnel** tabs of a connection.

### SSL / TLS

| Mode | Meaning |
|---|---|
| Driver default | Whatever the driver does: PostgreSQL tries TLS first, MySQL uses it when offered |
| `disable` | No encryption |
| `allow` (PostgreSQL) | Plain first; TLS only if the server insists |
| `prefer` | TLS when the server offers it, otherwise plain |
| `require` | Always encrypted, but the server is not verified |
| `verify-ca` | Encrypted, and the certificate must be signed by your CA (or a system CA) |
| `verify-full` | As `verify-ca`, and the certificate must match the host name |

Choose a **CA certificate** for `verify-ca` / `verify-full` with a private certificate authority. If the server asks
for a client certificate, set **Client certificate** and **Client key** (and the **Key passphrase** if the key is
encrypted; it is kept like a password). *Test* checks the files first and names the problem: missing, not a
certificate, expired (with the date), a key that does not match, a wrong passphrase.

### SSH tunnel

Tick **Connect through an SSH tunnel**, then fill in the **SSH server**: host, port, user and how to **sign in**:
*ssh-agent*, *Password*, or *Private key* (with its file and, if it has one, the passphrase). The *Host* in the main
tab is then the database host **as the SSH server sees it**, for example `10.0.3.17` or `localhost`.

To reach the SSH server through a bastion, tick **Reach the SSH server through a jump host** and fill in the second
block.

**Trusting a server.** The first time you reach an SSH server, its key is not known yet. *Test* (or the connection
error page) shows its fingerprint and a **Trust this server…** button. Compare the fingerprint with the one your
administrator gave you, and only then trust it. If a key you trusted **changes**, the connection is refused: that
means either the server was reinstalled or somebody is intercepting the connection. Trusted keys are kept in the
`known_hosts` file in your configuration folder, and the file `~/.ssh/known_hosts` is honoured too.

The tunnel opens automatically before the connection and closes when you disconnect.

### `~/.pgpass` and `pg_service.conf` (PostgreSQL)

- If a connection has no saved or typed password, `~/.pgpass` is searched (`PGPASSFILE` changes the location). On
  Linux and macOS the file must not be readable by others (`chmod 0600 ~/.pgpass`), otherwise it is ignored and the
  form says so.
- Type a **Service** name (or pick one from the list of services found) on the *Host / Port* tab. Whatever the
  connection leaves empty (host, port, user, database, TLS files) is taken from `pg_service.conf`; what you filled in
  always wins. The host may stay empty when a service provides it.

## 5. Cloud databases

Open the **Cloud** tab and choose your service; the form fills in what it can, without overwriting what you typed.

| Service | How it signs in | Needs |
|---|---|---|
| AWS RDS / Aurora (IAM) | A 15-minute token, created from your AWS credentials every time | `pip install -e ".[aws]"`; credentials from the environment, `~/.aws` or a role. Optional region and profile |
| Google Cloud SQL (IAM) | Your Google account's access token | `pip install -e ".[gcp]"`; `gcloud auth application-default login` or a service-account key file. Connect to the instance IP or a running Cloud SQL Auth Proxy |
| Azure Database (Entra ID) | An Entra access token | `pip install -e ".[azure]"`; `az login` or the `AZURE_*` variables. The user is the Entra principal name |
| Supabase | Ordinary password | The project reference fills in `db.<ref>.supabase.co` |
| Neon | Ordinary password | Paste the host from the Neon console |
| PlanetScale | Ordinary password | MySQL; the certificate is verified |
| CockroachDB Cloud | Ordinary password | Port 26257; for Serverless, the cluster name goes before the database name; choose the cluster's CA certificate in the TLS tab |

For the first three there is **no password box**: the token is created each time a connection opens, shown as the
*Cloud token* step of *Test*, and renewed if needed. If the sign-in library is not installed, *Test* tells you which
extra to install. The database user must be enabled for IAM / Entra sign-in on the server side.

## 6. Write and run SQL

- **Tabs:** <kbd>Ctrl+T</kbd> new, <kbd>Ctrl+W</kbd> close, double-click a tab title to rename. Tabs and their text
  come back the next time you open the connection. The drop-down next to *Run* sets the dialect of the tab.
- **Run:** <kbd>Ctrl+Enter</kbd> runs the selection, or the statement under the cursor; <kbd>F5</kbd> (*Run script*)
  runs every statement in order. Each statement gets its own result tab. A failed statement stops the script and
  marks the position of the error in the editor; the rest are shown as skipped.
- **Stop:** <kbd>Esc</kbd> or the **Stop** button cancels the running statement on the server.
- **Format:** <kbd>Ctrl+Shift+F</kbd> formats the selection or the whole text for the tab's dialect.
- **Autocomplete:** suggestions appear as you type and after a `.`; <kbd>Ctrl+Space</kbd> opens them on demand. Use
  <kbd>↑</kbd> / <kbd>↓</kbd> and <kbd>Enter</kbd> or <kbd>Tab</kbd>. They offer keywords, tables (`FROM` and `JOIN`
  put the tables first), the columns of the aliases you wrote, CTEs and snippets; after `JOIN` the `ON` condition is
  suggested from the foreign key. Choices you make often rise to the top. *Query → Keyword case* sets whether
  inserted keywords are upper case, lower case or as typed. Press <kbd>Esc</kbd> to close the list.
- **Safety:** a statement that would drop or empty something, or delete / update every row of a table, asks you
  first. On a production connection every statement that changes data asks too.

## 7. The diagram

The diagram shows the tables of the connected database with their columns (key symbols mark primary and foreign
keys) and the relations between them: a crow's foot is the "many" side.

- **Move around:** mouse wheel to zoom, drag the background to pan, **+ / − / ⛶** buttons or <kbd>Ctrl++</kbd>,
  <kbd>Ctrl+-</kbd>, <kbd>Ctrl+0</kbd> (fit everything). Drag a card to rearrange; positions are remembered.
- **Select:** click a table to highlight the tables related to it. Click a column to insert its name in the editor.
- **Open data:** double-click a card. Right-click for *Open data*, *SELECT \* FROM this table*, *Insert table name*,
  *Copy table name*.
- **Search:** type in the box above the diagram to show only tables and columns that match.
- **Schemas:** with several schemas a drop-down lets you pick one or *All schemas*.
- **Refresh:** ↻ (or <kbd>Ctrl+Shift+R</kbd>) re-reads the structure after you changed it; ⟲ resets the card layout.
- **Go to table:** <kbd>Ctrl+P</kbd> finds a table or column by a few letters.
- **Hide it:** *Database → Show the diagram* collapses the right side.

### Export

*Database → Export diagram…* (<kbd>Ctrl+E</kbd>, or the ⤓ button) saves the diagram as **PNG**, **SVG**, **PDF**, or as
text: a **Mermaid** `erDiagram` or **DBML** (paste it into other tools). The images contain only the cards
currently shown (after a search); the text formats list every table of the selected schema.

## 8. Browse and edit data

Double-click a table card to open its data in a tab. Click a column header to sort (on the server, so it works on
millions of rows), type a `WHERE` condition in the filter box, page with ◀ ▶, reload with ⟳.

You can also edit the result of a simple `SELECT * FROM table WHERE …` typed in the editor. Joins, grouping,
`DISTINCT`, views and read-only connections cannot be edited, and the line under the grid says why.

### Making changes

| Do this | Keys |
|---|---|
| Edit a cell | <kbd>F2</kbd> or double-click; an editor that fits the type opens (calendar, true / false / NULL list, number box) |
| Set NULL, use the default, edit long text in a window | Right-click the cell |
| Add a row | <kbd>Ctrl+N</kbd> |
| Duplicate the selected rows | <kbd>Ctrl+D</kbd> |
| Mark rows for deletion (again restores) | <kbd>Del</kbd> |
| Undo / redo | <kbd>Ctrl+Z</kbd> / <kbd>Ctrl+Y</kbd> |
| Discard everything pending | <kbd>Esc</kbd> |
| Review and write | <kbd>Alt+S</kbd> |

Changed cells turn yellow (hover to see the old value), new rows green, rows to delete red and struck through. Your
edits survive sorting, filtering, paging and reloading.

### Review, then one transaction

<kbd>Alt+S</kbd> opens a review with the SQL that will run (values written out) and how many rows will change, be
added or be deleted. Press **Apply** to write everything in **one transaction**: all of it, or none of it. On a
production connection one more confirmation follows.

### If somebody else changed the row

An update looks the row up by its key **and** by the old values of the cells you changed. If the row was changed or
deleted meanwhile, the whole transaction is rolled back (*Nothing was written*), the row is named, and your edits stay
in the grid. Press **Reload** to rebase them on the current data and apply again.

### Tables without a primary key

A table without a primary key can still be edited if it has a unique index. Otherwise choose **Choose key columns…**
and tick the columns that identify a row; the choice is remembered. A change that would touch more than one row is
refused.

Closing a tab, running another query over an edited result, or quitting while changes are pending asks you first.

## 9. History and saved queries

- **History** (<kbd>Ctrl+H</kbd>): every statement you ran on the connection with its time, duration, number of rows
  and any error. Search, show only failures, open an entry in a new tab, insert it at the cursor, copy it, save it as a
  query or delete it. Running the same statement again right away only counts a run (`x3`). *Clear history…* asks
  first.
- **Save a query** (<kbd>Ctrl+S</kbd>): saves the selection (or the whole editor) under a name, in an optional folder
  such as `Reports/Monthly`. Tick *Only for this connection* to keep it out of your other connections.
- **Saved queries** (<kbd>Ctrl+Shift+H</kbd>): browse, search (names and text), open in a new tab, insert, rename, move to
  a folder, delete.

## 10. Settings and files

*Query* and *View* menus change the row limit, keyword case, theme and language; they are saved in
`settings.toml`. Everything else is in the connection dialog.

| File | Where | What |
|---|---|---|
| `connections.json` | configuration folder | Your connections (no passwords) |
| `settings.toml` | configuration folder | Theme, language, row limit, keyword case |
| `vault.json` | configuration folder | Encrypted passwords, only if the system keyring is not used |
| `known_hosts` | configuration folder | SSH servers you trusted |
| `app.db` | data folder | Query tabs, history, saved queries, diagram positions, autocomplete usage |

On Linux the folders are `~/.config/easydbms` and `~/.local/share/easydbms`; on macOS both are
`~/Library/Application Support/easydbms`; on Windows `%LOCALAPPDATA%\easydbms`. Set the environment variable
`EASYDBMS_HOME` to a folder to keep everything there instead (it makes the program portable). A damaged
`connections.json` or `settings.toml` is never overwritten: it is renamed to `*.broken-<time>` and the program starts
with what could be read, telling you so.

**Starting over:** close the program and delete the two folders. This also removes your saved connections and history;
passwords stored in the system keyring can be removed from the keyring's own manager (look for entries named
`easydbms`).

## 11. Troubleshooting

**The window does not open on Linux** (`Could not load the Qt platform plugin "xcb"`). Install the libraries listed in
[section 1](#linux), in particular `libxcb-cursor0`.

**`pip install` says the Python version is too old.** EasyDBMS needs Python 3.12 or newer. Check `python --version`,
and create the environment with `python3.12 -m venv .venv`.

**Test says "Port reachable" failed.** The host is found but nothing accepts connections on that port. Check that
the server is running, the port number, and any firewall or VPN. Behind an SSH tunnel the host and port are those
the **SSH server** can reach.

**The password is rejected, but it is right.** Look at the user and the database name, and at which password is
used: a saved one beats `~/.pgpass`. If you saved a wrong password, type the correct one into the box and save again.

**TLS errors.** *Test* names the file or step. A "certificate verify failed" with `verify-full` usually means the
host name in the connection differs from the one in the certificate; use the name the certificate was issued for, or
`verify-ca`. With a private CA, set its certificate in **CA certificate**.

**"The identity of the SSH server is not known yet".** Use **Trust this server…** after checking the fingerprint. If
it says the key **changed**, do not connect until you know why.

**`~/.pgpass` is ignored.** Run `chmod 0600 ~/.pgpass`. The connection form shows whether the file knows the
connection (never the password itself).

**"Undefined environment variable".** A `${NAME}` placeholder has no value and no `:-fallback`. Set the variable
before starting the program (a program started from a menu may not see variables set in a terminal).

**The keyring cannot be used.** On a server or in a minimal desktop session there may be no keyring service; the
program then asks for a master password and keeps an encrypted vault instead.

**A cloud connection says a package is needed.** Install the extra it names, for example
`pip install -e ".[aws]"`, in the same environment you start the program from.

**The diagram is empty** after you changed the database. Press ↻ (<kbd>Ctrl+Shift+R</kbd>) to re-read the structure.

## 12. Keyboard shortcuts

| Keys | Action |
|---|---|
| <kbd>Ctrl+Shift+C</kbd> | Connections window |
| <kbd>Ctrl+Enter</kbd> | Run selection / current statement |
| <kbd>F5</kbd> | Run script |
| <kbd>Esc</kbd> | Stop the query; discard pending edits (in the grid); close suggestions |
| <kbd>Ctrl+Space</kbd> | Suggestions |
| <kbd>Ctrl+Shift+F</kbd> | Format SQL |
| <kbd>Ctrl+T</kbd> / <kbd>Ctrl+W</kbd> | New / close tab |
| <kbd>Ctrl+S</kbd> | Save query |
| <kbd>Ctrl+H</kbd> / <kbd>Ctrl+Shift+H</kbd> | History / saved queries |
| <kbd>Alt+S</kbd> | Review and write edits |
| <kbd>F2</kbd>, <kbd>Ctrl+N</kbd>, <kbd>Ctrl+D</kbd>, <kbd>Del</kbd> | Edit cell, new row, duplicate rows, delete rows |
| <kbd>Ctrl+Z</kbd> / <kbd>Ctrl+Y</kbd> | Undo / redo edits |
| <kbd>Ctrl+C</kbd> / <kbd>Ctrl+Shift+C</kbd> (in a grid) | Copy cells / with headers |
| <kbd>Ctrl+P</kbd> | Go to table or column |
| <kbd>Ctrl+Shift+R</kbd> | Re-read the structure |
| <kbd>Ctrl+E</kbd> | Export the diagram |
| <kbd>Ctrl++</kbd>, <kbd>Ctrl+-</kbd>, <kbd>Ctrl+0</kbd> | Diagram zoom in / out / fit |
| <kbd>Ctrl+Q</kbd> | Quit |
