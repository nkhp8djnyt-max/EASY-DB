# SQL ERD Studio

Desktop database client with ERD, editable result grid and dialect-aware SQL tooling.

Supported SQL dialects: **PostgreSQL**, **MySQL** (including MariaDB) and **SQLite**.
Corporate dialects (SQL Server, Oracle) are intentionally out of scope.

## Dialect system

`sql_erd_studio.core.dialects` is the single place that knows how the three dialects differ.
Everything else (editor, autocomplete, editable grid, ERD export) asks a `Dialect` instead of
branching on a database name:

| Concern | API |
|---|---|
| Lookup by name, alias or URL | `get_dialect("postgres")`, `dialect_from_url("mysql://…")` |
| Identifier quoting | `dialect.quote_ident()`, `quote_qualified()`, `quote_ident_if_needed()` |
| Literals (previews only) | `dialect.render_literal(value)` |
| Null-safe equality (optimistic locking) | `dialect.null_safe_eq(lhs, rhs)` |
| Server-side pagination | `dialect.paginate(sql, limit=…, offset=…, order_by=…)` |
| Read-only sessions | `dialect.read_only_statements()` |
| Column type → editor kind | `dialect.classify_type("tinyint(1)")` |
| Keywords / functions / types | `dialect.keywords`, `dialect.functions`, `dialect.data_types` |
| Tokenizing (highlighting) | `tokenize(sql, dialect)` |
| Statement splitting (Ctrl+Enter) | `split_statements()`, `statement_at()` |
| Formatting / syntax check | `format_sql()`, `check_syntax()` |
| Translation between dialects | `translate(sql, source=…, target=…)` |

## Development

```bash
uv venv --python 3.12 && . .venv/bin/activate
uv pip install -e ".[dev]"
ruff check . && mypy && pytest
```

Integration tests always run against SQLite. To run them against PostgreSQL and MySQL/MariaDB,
point them at empty throw-away databases:

```bash
export ERD_TEST_POSTGRES_URL="postgresql+psycopg://user:pass@localhost/erd_test"
export ERD_TEST_MYSQL_URL="mysql+pymysql://user:pass@localhost/erd_test"
pytest
```
