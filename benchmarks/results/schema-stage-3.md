# Schema and diagram benchmarks, stage 3

Median of several runs on the development machine (4 cores). Synthetic schema: tables of 9 columns with a primary key, one or two foreign keys and an index each.

## Reading the structure

| Engine | Tables | Columns | Foreign keys | EasyDBMS | SQLAlchemy `MetaData.reflect()` | SQLAlchemy `Inspector` per table | Same counts |
|---|---:|---:|---:|---:|---:|---:|:--:|
| PostgreSQL | 100 | 900 | 132 | **26 ms** | 134 ms (5.1×) | 955 ms (36.5×) | yes |
| PostgreSQL | 300 | 2700 | 398 | **76 ms** | 314 ms (4.1×) | 2975 ms (39.1×) | yes |
| PostgreSQL | 1000 | 9000 | 1332 | **251 ms** | 1238 ms (4.9×) | 8851 ms (35.2×) | yes |
| MariaDB | 100 | 900 | 132 | **23 ms** | 218 ms (9.4×) | 102 ms (4.4×) | yes |
| MariaDB | 300 | 2700 | 398 | **61 ms** | 388 ms (6.3×) | 281 ms (4.6×) | yes |
| MariaDB | 1000 | 9000 | 1332 | **249 ms** | 1315 ms (5.3×) | 1229 ms (4.9×) | yes |
| SQLite | 100 | 900 | 132 | **6 ms** | 111 ms (17.3×) | 53 ms (8.3×) | yes |
| SQLite | 300 | 2700 | 398 | **20 ms** | 379 ms (19.3×) | 222 ms (11.3×) | yes |
| SQLite | 1000 | 9000 | 1332 | **79 ms** | 1798 ms (22.6×) | 723 ms (9.1×) | yes |

SQLAlchemy builds typed `Table` objects and reflects more (server defaults as expressions, check constraints, comments); EasyDBMS reads only what the diagram and autocomplete need.

## Layout

| Tables | Edges | Layout |
|---:|---:|---:|
| 50 | 65 | 1.1 ms |
| 200 | 265 | 4.9 ms |
| 1,000 | 1,332 | 32.6 ms |
| 3,000 | 3,998 | 174.0 ms |

## Qt scene (offscreen, 1200×800 view)

| Tables | Relations | Build the scene | Repaint, whole diagram in view | Repaint at 100 % | Filter by name |
|---:|---:|---:|---:|---:|---:|
| 200 | 265 | 58 ms | 5 ms (zoom 0.05) | 14 ms | 0.3 ms |
| 1,000 | 1,332 | 394 ms | 29 ms (zoom 0.01) | 28 ms | 3.6 ms |
