"""Download the ClickBench sample file and the official per-engine DDL and query lists.

    python -m benchmarks.clickbench.fetch

``hits_0.parquet`` is the first of the 100 partitions of the full dataset (1,000,000 rows, ~120 MB).
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

from . import common

DATA_URL = "https://clickhouse-public-datasets.s3.amazonaws.com/hits_compatible/athena_partitioned/hits_0.parquet"
REPO_RAW = "https://raw.githubusercontent.com/ClickHouse/ClickBench/main"


def download(url: str, target: Path) -> None:
    if target.exists() and target.stat().st_size > 0:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"{url} -> {target}", flush=True)
    with urllib.request.urlopen(url, timeout=120) as response, target.open("wb") as out:
        while chunk := response.read(1 << 20):
            out.write(chunk)


def main() -> None:
    download(DATA_URL, common.PARQUET)
    for engine, directory in common.QUERY_DIRS.items():
        for name in ("create.sql", "queries.sql"):
            download(f"{REPO_RAW}/{directory}/{name}", common.DATA_DIR / directory / name)
        print(f"{engine}: ok")


if __name__ == "__main__":
    main()
