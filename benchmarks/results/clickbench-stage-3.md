# ClickBench, stage 3

Dataset: `hits_0.parquet`, **1,000,000 rows** (1/100 of the full ClickBench dataset). 4 cores, Intel(R) Xeon(R) Processor @ 2.80GHz, Python 3.12.3, native scanner `avx2`. All servers run with their default (untuned) configuration on the same machine. Date: 2026-10-03. 3 runs per query, timeout 120 s, OS page cache not dropped.

## Summary

| Runner | Version | Relative score (best of runs, lower is better) | Sum of best times | First-run sum | Failed |
|---|---|---:|---:|---:|---:|
| DuckDB | 1.5.6 | 1.10 | 0.8 s | 1.0 s | 0 |
| ClickHouse (chdb) | 26.9.2.1 | 1.34 | 1.1 s | 1.4 s | 0 |
| PostgreSQL (psycopg) | 16.14 | 15.19 | 18.1 s | 19.0 s | 0 |
| PostgreSQL (EasyDBMS) | 16.14 | 15.21 | 18.2 s | 19.8 s | 0 |
| SQLite (sqlite3) | 3.45.1 | 15.80 | 20.9 s | 21.8 s | 0 |
| SQLite (EasyDBMS) | 3.45.1 | 15.92 | 21.1 s | 22.8 s | 0 |
| MariaDB (PyMySQL) | 10.11.14-MariaDB | 79.75 | 81.3 s | 83.8 s | 0 |
| MariaDB (EasyDBMS) | 10.11.14-MariaDB | 81.95 | 83.7 s | 86.6 s | 0 |

Relative score = geometric mean over the queries of `(t + 10 ms) / (fastest runner's t + 10 ms)`, the ClickBench formula; a failed query counts as twice the slowest successful time of that query. Sums skip failed queries, so compare them only between rows with the same *Failed* count.

## Per query, best of runs (ms)

| # | PostgreSQL (EasyDBMS) | PostgreSQL (psycopg) | MariaDB (EasyDBMS) | MariaDB (PyMySQL) | SQLite (EasyDBMS) | SQLite (sqlite3) | DuckDB | ClickHouse (chdb) |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 96 | 87 | 1,071 | 990 | 24 | 24 | **0.7** | 1.5 |
| 2 | 129 | 130 | 966 | 915 | 276 | 275 | **1.1** | 1.5 |
| 3 | 142 | 140 | 1,080 | 1,037 | 325 | 316 | **1.5** | 4.6 |
| 4 | 117 | 118 | 1,098 | 990 | 70 | 69 | **1.7** | 4.2 |
| 5 | 387 | 392 | 1,103 | 1,179 | 143 | 145 | **9.1** | 9.8 |
| 6 | 593 | 592 | 2,967 | 2,872 | 542 | 541 | 11 | **9.7** |
| 7 | 124 | 123 | 1,033 | 1,128 | 91 | 91 | **1.1** | 3.9 |
| 8 | 159 | 155 | 1,049 | 1,065 | 285 | 278 | **2.5** | 5.1 |
| 9 | 670 | 674 | 1,305 | 1,282 | 515 | 514 | 16 | **9.8** |
| 10 | 900 | 892 | 1,498 | 1,447 | 684 | 678 | 25 | **13** |
| 11 | 162 | 160 | 1,563 | 1,481 | 272 | 272 | 7.5 | **5.8** |
| 12 | 153 | 153 | 1,613 | 1,545 | 281 | 278 | 7.1 | **6.7** |
| 13 | 185 | 185 | 1,513 | 1,408 | 320 | 317 | **6.8** | 13 |
| 14 | 238 | 237 | 3,421 | 3,454 | 330 | 322 | **8.4** | 16 |
| 15 | 467 | 463 | 1,504 | 1,412 | 342 | 344 | **6.6** | 14 |
| 16 | 219 | 218 | 1,232 | 1,075 | 256 | 256 | **12** | 14 |
| 17 | 297 | 294 | 3,240 | 3,156 | 624 | 632 | **16** | 28 |
| 18 | 249 | 251 | 3,295 | 3,138 | 468 | 459 | 14 | **13** |
| 19 | 649 | 636 | 4,144 | 4,005 | 1,150 | 1,104 | **31** | 38 |
| 20 | 113 | 124 | 992 | 953 | 0.1 | **0.1** | 0.6 | 1.9 |
| 21 | 159 | 157 | 1,720 | 1,668 | 292 | 294 | 20 | **12** |
| 22 | 186 | 186 | 1,816 | 1,831 | 289 | 290 | 14 | **4.9** |
| 23 | 213 | 211 | 1,959 | 1,923 | 259 | 260 | **10** | 27 |
| 24 | 162 | 162 | 1,790 | 1,890 | 296 | 293 | **31** | 98 |
| 25 | 151 | 156 | 1,477 | 1,315 | 283 | 278 | **5.4** | 8.3 |
| 26 | 157 | 166 | 1,384 | 1,368 | 280 | 279 | **4.0** | 7.5 |
| 27 | 172 | 176 | 1,370 | 1,371 | 281 | 279 | **5.1** | 9.2 |
| 28 | 241 | 222 | 1,487 | 1,357 | 443 | 437 | 15 | **11** |
| 29 | 2,145 | 2,110 | 4,135 | 4,066 | 2,304 | 2,278 | 196 | **51** |
| 30 | 497 | 500 | 5,208 | 4,998 | 2,192 | 2,137 | **12** | 22 |
| 31 | 217 | 220 | 1,468 | 1,265 | 330 | 325 | **7.9** | 14 |
| 32 | 254 | 269 | 1,410 | 1,319 | 328 | 327 | **11** | 19 |
| 33 | 2,130 | 2,012 | 4,380 | 4,442 | 1,097 | 1,092 | 46 | **35** |
| 34 | 1,221 | 1,228 | 4,399 | 4,156 | 955 | 955 | **45** | 63 |
| 35 | 1,276 | 1,343 | 4,405 | 4,171 | 1,308 | 1,306 | **49** | 83 |
| 36 | 242 | 238 | 1,331 | 1,385 | 699 | 681 | **13** | 14 |
| 37 | 542 | 547 | 1,874 | 1,814 | 492 | 489 | **26** | 65 |
| 38 | 287 | 282 | 1,590 | 1,457 | 343 | 346 | **5.4** | 40 |
| 39 | 223 | 219 | 664 | 701 | 173 | 172 | **8.0** | 37 |
| 40 | 742 | 708 | 2,438 | 2,484 | 1,047 | 1,016 | **57** | 167 |
| 41 | 361 | 361 | 481 | 494 | 212 | 210 | **4.3** | 20 |
| 42 | 342 | 344 | 457 | 501 | 214 | 215 | **3.9** | 16 |
| 43 | 278 | 285 | 815 | 807 | 0.1 | **0.0** | 7.6 | 13 |

## Cost of the application layer (app vs the raw driver)

| Engine | app, sum of best | raw driver, sum of best | overhead |
|---|---:|---:|---:|
| postgresql | 18.25 s | 18.13 s | +0.7 % |
| mysql | 83.74 s | 81.31 s | +3.0 % |
| sqlite | 21.12 s | 20.87 s | +1.2 % |

## Row-count differences between engines

- query 29: PostgreSQL (EasyDBMS) 2, PostgreSQL (psycopg) 2, MariaDB (EasyDBMS) 1, MariaDB (PyMySQL) 1, SQLite (EasyDBMS) 2, SQLite (sqlite3) 2, DuckDB 2, ClickHouse (chdb) 2
- query 43: PostgreSQL (EasyDBMS) 10, PostgreSQL (psycopg) 10, MariaDB (EasyDBMS) 0, MariaDB (PyMySQL) 0, SQLite (EasyDBMS) 0, SQLite (sqlite3) 0, DuckDB 10, ClickHouse (chdb) 10

Each engine runs its own official ClickBench query text, which is not always the same statement (e.g. query 43 truncates time to the minute on PostgreSQL, DuckDB and ClickHouse, to the hour on MariaDB and to the minute of the hour on SQLite). A difference with identical SQL is only noted here, not investigated.
