# ClickBench, stage 2

Dataset: `hits_0.parquet`, **1,000,000 rows** (1/100 of the full ClickBench dataset). 4 cores, Intel(R) Xeon(R) Processor @ 2.80GHz, Python 3.12.3, native scanner `avx2`. All servers run with their default (untuned) configuration on the same machine. Date: 2026-10-03. 3 runs per query, timeout 120 s, OS page cache not dropped.

## Summary

| Runner | Version | Relative score (best of runs, lower is better) | Sum of best times | First-run sum | Failed |
|---|---|---:|---:|---:|---:|
| DuckDB | 1.5.6 | 1.11 | 0.8 s | 1.0 s | 0 |
| ClickHouse (chdb) | 26.9.2.1 | 1.35 | 1.0 s | 1.4 s | 0 |
| PostgreSQL (psycopg) | 16.14 | 14.87 | 18.4 s | 18.8 s | 0 |
| PostgreSQL (EasyDBMS) | 16.14 | 14.90 | 18.4 s | 19.4 s | 0 |
| SQLite (sqlite3) | 3.45.1 | 16.31 | 21.4 s | 22.3 s | 0 |
| SQLite (EasyDBMS) | 3.45.1 | 16.38 | 21.4 s | 22.5 s | 0 |
| MariaDB (EasyDBMS) | 10.11.14-MariaDB | 78.03 | 79.5 s | 82.0 s | 0 |
| MariaDB (PyMySQL) | 10.11.14-MariaDB | 78.22 | 79.6 s | 82.0 s | 0 |

Relative score = geometric mean over the queries of `(t + 10 ms) / (fastest runner's t + 10 ms)`, the ClickBench formula; a failed query counts as twice the slowest successful time of that query. Sums skip failed queries, so compare them only between rows with the same *Failed* count.

## Per query, best of runs (ms)

| # | PostgreSQL (EasyDBMS) | PostgreSQL (psycopg) | MariaDB (EasyDBMS) | MariaDB (PyMySQL) | SQLite (EasyDBMS) | SQLite (sqlite3) | DuckDB | ClickHouse (chdb) |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 95 | 82 | 880 | 928 | 24 | 24 | **0.7** | 1.5 |
| 2 | 136 | 138 | 985 | 966 | 281 | 268 | **1.2** | 1.7 |
| 3 | 147 | 147 | 1,060 | 1,036 | 304 | 303 | **1.7** | 4.1 |
| 4 | 95 | 97 | 1,011 | 972 | 65 | 64 | **1.8** | 3.4 |
| 5 | 380 | 385 | 1,049 | 1,126 | 145 | 145 | 11 | **6.3** |
| 6 | 557 | 559 | 2,759 | 2,791 | 544 | 542 | 9.8 | **9.4** |
| 7 | 108 | 123 | 1,061 | 1,072 | 87 | 90 | **0.8** | 3.2 |
| 8 | 145 | 147 | 1,017 | 965 | 285 | 281 | **2.0** | 4.3 |
| 9 | 637 | 642 | 1,290 | 1,346 | 522 | 517 | 15 | **9.6** |
| 10 | 843 | 877 | 1,367 | 1,450 | 697 | 703 | 27 | **13** |
| 11 | 152 | 150 | 1,462 | 1,488 | 284 | 285 | **4.6** | 5.6 |
| 12 | 153 | 154 | 1,425 | 1,470 | 297 | 287 | **5.1** | 6.2 |
| 13 | 168 | 161 | 1,413 | 1,333 | 333 | 325 | **5.8** | 12 |
| 14 | 218 | 230 | 3,302 | 3,359 | 329 | 331 | **7.5** | 15 |
| 15 | 466 | 459 | 1,387 | 1,402 | 346 | 339 | **6.1** | 14 |
| 16 | 214 | 214 | 1,124 | 1,055 | 252 | 253 | **10** | 14 |
| 17 | 272 | 259 | 3,170 | 3,073 | 630 | 629 | **16** | 35 |
| 18 | 240 | 240 | 3,107 | 3,165 | 477 | 475 | 15 | **13** |
| 19 | 621 | 627 | 4,095 | 4,127 | 1,126 | 1,114 | **32** | 38 |
| 20 | 116 | 107 | 966 | 951 | 0.1 | **0.0** | 0.7 | 2.1 |
| 21 | 153 | 155 | 1,601 | 1,634 | 325 | 323 | 20 | **15** |
| 22 | 183 | 196 | 1,648 | 1,745 | 328 | 318 | 14 | **5.2** |
| 23 | 205 | 201 | 1,786 | 1,826 | 284 | 289 | **11** | 29 |
| 24 | 150 | 150 | 1,750 | 1,757 | 323 | 324 | **32** | 102 |
| 25 | 141 | 144 | 1,348 | 1,331 | 297 | 297 | **5.7** | 9.0 |
| 26 | 140 | 140 | 1,366 | 1,315 | 290 | 290 | **5.1** | 8.1 |
| 27 | 138 | 139 | 1,213 | 1,275 | 292 | 290 | **5.0** | 9.1 |
| 28 | 213 | 212 | 1,335 | 1,316 | 480 | 466 | 20 | **13** |
| 29 | 2,060 | 2,100 | 4,088 | 4,191 | 2,338 | 2,322 | 198 | **48** |
| 30 | 513 | 500 | 5,043 | 5,078 | 2,184 | 2,300 | **9.7** | 20 |
| 31 | 233 | 235 | 1,250 | 1,258 | 340 | 343 | **6.8** | 13 |
| 32 | 264 | 251 | 1,229 | 1,286 | 352 | 352 | **8.0** | 18 |
| 33 | 2,066 | 2,070 | 4,186 | 4,040 | 1,130 | 1,140 | 53 | **37** |
| 34 | 1,658 | 1,679 | 4,262 | 4,173 | 939 | 940 | **44** | 64 |
| 35 | 1,663 | 1,586 | 4,286 | 4,177 | 1,351 | 1,305 | **50** | 62 |
| 36 | 237 | 230 | 1,414 | 1,374 | 689 | 679 | 13 | **12** |
| 37 | 519 | 506 | 1,742 | 1,787 | 481 | 481 | **30** | 71 |
| 38 | 269 | 268 | 1,443 | 1,388 | 349 | 344 | **5.8** | 40 |
| 39 | 208 | 208 | 608 | 570 | 169 | 169 | **8.3** | 37 |
| 40 | 685 | 687 | 2,258 | 2,291 | 1,016 | 1,033 | **60** | 161 |
| 41 | 357 | 365 | 488 | 477 | 222 | 219 | **4.2** | 21 |
| 42 | 334 | 321 | 441 | 442 | 222 | 224 | **4.6** | 16 |
| 43 | 265 | 268 | 743 | 803 | 0.1 | **0.0** | 8.9 | 14 |

## Cost of the application layer (app vs the raw driver)

| Engine | app, sum of best | raw driver, sum of best | overhead |
|---|---:|---:|---:|
| postgresql | 18.42 s | 18.41 s | +0.0 % |
| mysql | 79.46 s | 79.61 s | -0.2 % |
| sqlite | 21.43 s | 21.42 s | +0.0 % |

## Row-count differences between engines

- query 29: PostgreSQL (EasyDBMS) 2, PostgreSQL (psycopg) 2, MariaDB (EasyDBMS) 1, MariaDB (PyMySQL) 1, SQLite (EasyDBMS) 2, SQLite (sqlite3) 2, DuckDB 2, ClickHouse (chdb) 2
- query 43: PostgreSQL (EasyDBMS) 10, PostgreSQL (psycopg) 10, MariaDB (EasyDBMS) 0, MariaDB (PyMySQL) 0, SQLite (EasyDBMS) 0, SQLite (sqlite3) 0, DuckDB 10, ClickHouse (chdb) 10

Each engine runs its own official ClickBench query text, which is not always the same statement (e.g. query 43 truncates time to the minute on PostgreSQL, DuckDB and ClickHouse, to the hour on MariaDB and to the minute of the hour on SQLite). A difference with identical SQL is only noted here, not investigated.
