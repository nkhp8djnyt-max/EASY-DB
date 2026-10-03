# ClickBench, stage 3-mariadb-recheck

Dataset: `hits_0.parquet`, **1,000,000 rows** (1/100 of the full ClickBench dataset). 4 cores, Intel(R) Xeon(R) Processor @ 2.80GHz, Python 3.12.3, native scanner `avx2`. All servers run with their default (untuned) configuration on the same machine. Date: 2026-10-03. 3 runs per query, timeout 120 s, OS page cache not dropped.

## Summary

| Runner | Version | Relative score (best of runs, lower is better) | Sum of best times | First-run sum | Failed |
|---|---|---:|---:|---:|---:|
| MariaDB (PyMySQL) | 10.11.14-MariaDB | 1.01 | 83.2 s | 85.8 s | 0 |
| MariaDB (EasyDBMS) | 10.11.14-MariaDB | 1.04 | 85.3 s | 89.0 s | 0 |

Relative score = geometric mean over the queries of `(t + 10 ms) / (fastest runner's t + 10 ms)`, the ClickBench formula; a failed query counts as twice the slowest successful time of that query. Sums skip failed queries, so compare them only between rows with the same *Failed* count.

## Per query, best of runs (ms)

| # | MariaDB (EasyDBMS) | MariaDB (PyMySQL) |
|---:|---:|---:|
| 1 | 1,003 | **995** |
| 2 | 1,114 | **936** |
| 3 | **1,111** | 1,153 |
| 4 | **1,025** | 1,026 |
| 5 | **1,129** | 1,258 |
| 6 | 3,011 | **2,968** |
| 7 | 1,034 | **1,034** |
| 8 | 1,004 | **993** |
| 9 | **1,296** | 1,352 |
| 10 | **1,403** | 1,438 |
| 11 | **1,509** | 1,518 |
| 12 | 1,706 | **1,617** |
| 13 | 1,429 | **1,338** |
| 14 | 3,651 | **3,400** |
| 15 | 1,474 | **1,380** |
| 16 | **1,104** | 1,169 |
| 17 | 3,468 | **3,148** |
| 18 | 3,334 | **3,307** |
| 19 | 4,314 | **4,253** |
| 20 | **1,023** | 1,052 |
| 21 | 1,779 | **1,755** |
| 22 | **1,685** | 1,866 |
| 23 | 2,033 | **1,899** |
| 24 | 1,833 | **1,760** |
| 25 | 1,437 | **1,334** |
| 26 | 1,360 | **1,253** |
| 27 | 1,441 | **1,296** |
| 28 | 1,416 | **1,381** |
| 29 | 4,393 | **4,220** |
| 30 | 5,224 | **5,066** |
| 31 | 1,434 | **1,384** |
| 32 | **1,423** | 1,424 |
| 33 | 4,874 | **4,802** |
| 34 | 4,542 | **4,426** |
| 35 | **4,520** | 4,537 |
| 36 | **1,299** | 1,450 |
| 37 | 1,860 | **1,765** |
| 38 | 1,511 | **1,447** |
| 39 | 722 | **597** |
| 40 | **2,452** | 2,460 |
| 41 | 557 | **520** |
| 42 | 530 | **467** |
| 43 | 838 | **796** |

## Cost of the application layer (app vs the raw driver)

| Engine | app, sum of best | raw driver, sum of best | overhead |
|---|---:|---:|---:|
| mysql | 85.31 s | 83.24 s | +2.5 % |
