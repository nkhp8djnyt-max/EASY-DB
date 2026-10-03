"""Schema introspection and diagram benchmarks.

    python -m benchmarks.schema_bench --stage 3

* **Introspection**: reading a synthetic schema of N tables (about 8 columns each, a primary key, one
  or two foreign keys and an index per table) with EasyDBMS's bulk catalog queries, against
  SQLAlchemy ``MetaData.reflect()`` and an ``Inspector`` loop over the tables. They are *not* the same
  work: SQLAlchemy builds dialect-typed ``Table`` objects with constraints, EasyDBMS builds plain data
  rows; the point is what a user waits for before the diagram appears.
* **Layout and scene**: how long placing N cards and building the Qt scene takes (offscreen), and how
  long one repaint takes at several zoom levels.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import statistics
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from sqlalchemy import MetaData, create_engine, inspect

from easydbms.core.connections import parse_connection_url
from easydbms.core.db import DatabaseClient, create_client
from easydbms.core.erd import build_erd, layout
from easydbms.core.schema import introspect

RESULTS_DIR = Path(__file__).resolve().parent / "results"
PG_URL = os.environ.get("SB_POSTGRES_URL", "postgresql+psycopg://erd:erd@127.0.0.1:5432/erd_bench")
MYSQL_URL = os.environ.get("SB_MYSQL_URL", "mysql+pymysql://erd:erd@127.0.0.1:3306/erd_bench")


def median_ms(fn: Callable[[], object], repeat: int) -> float:
    times = []
    for _ in range(repeat):
        started = time.perf_counter()
        fn()
        times.append((time.perf_counter() - started) * 1000)
    return statistics.median(times)


# ---------------------------------------------------------------------- synthetic schema


def synthetic_ddl(count: int) -> tuple[list[str], list[str]]:
    """``(create statements, drop statements)`` for ``count`` linked tables."""
    rng = random.Random(count)
    create, drop = [], []
    for i in range(count):
        name = f"sb_t{i:04d}"
        parent = f"sb_t{rng.randrange(i):04d}" if i else None
        owner = f"sb_t{rng.randrange(i):04d}" if i and i % 3 == 0 else None
        columns = [
            "id INTEGER PRIMARY KEY",
            "parent_id INTEGER",
            "owner_id INTEGER",
            "name VARCHAR(100) NOT NULL",
            "c1 INTEGER",
            "c2 INTEGER",
            "c3 VARCHAR(50)",
            "created TIMESTAMP",
            "note TEXT",
        ]
        if parent:
            columns.append(f"FOREIGN KEY (parent_id) REFERENCES {parent} (id)")
        if owner:
            columns.append(f"FOREIGN KEY (owner_id) REFERENCES {owner} (id)")
        create.append(f"CREATE TABLE {name} ({', '.join(columns)})")
        create.append(f"CREATE INDEX {name}_name ON {name} (name)")
        drop.insert(0, f"DROP TABLE IF EXISTS {name}")
    return create, drop


def prepare(client: DatabaseClient, count: int) -> list[str]:
    create, drop = synthetic_ddl(count)
    for statement in drop:
        client.execute(statement)
    for statement in create:
        client.execute(statement)
    return drop


# ---------------------------------------------------------------------- introspection


def bench_introspection(sizes: tuple[int, ...], repeat: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    sqlite_file = Path(tempfile.mkdtemp()) / "bench.sqlite"
    sqlite3.connect(sqlite_file).close()
    engines = {
        "PostgreSQL": PG_URL,
        "MariaDB": MYSQL_URL,
        "SQLite": f"sqlite:///{sqlite_file}",
    }
    for label, url in engines.items():
        parsed = parse_connection_url(url)
        client = create_client(parsed.config, parsed.password)
        client.connect()
        try:
            for count in sizes:
                drop = prepare(client, count)
                try:
                    rows.append(measure_engine(label, url, client, count, repeat))
                    print(rows[-1], flush=True)
                finally:
                    for statement in drop:
                        client.execute(statement)
        finally:
            client.disconnect()
    return rows


def measure_engine(
    label: str, url: str, client: DatabaseClient, count: int, repeat: int
) -> dict[str, Any]:
    ours_schema = introspect(client)
    mine = [t for t in ours_schema.tables if t.name.startswith("sb_t")]
    columns = sum(len(t.columns) for t in mine)
    foreign_keys = sum(len(t.foreign_keys) for t in mine)
    ours = median_ms(lambda: introspect(client), repeat)

    engine = create_engine(url)
    only = lambda name, _meta: name.startswith("sb_t")  # noqa: E731

    def reflect() -> MetaData:
        metadata = MetaData()
        with engine.connect() as connection:
            metadata.reflect(bind=connection, only=only)
        return metadata

    metadata = reflect()
    sa_columns = sum(len(t.columns) for t in metadata.tables.values())
    sa_fks = sum(len(t.foreign_key_constraints) for t in metadata.tables.values())
    sa_reflect = median_ms(reflect, max(2, repeat // 2))

    def inspector_loop() -> None:
        inspector = inspect(engine)
        for name in inspector.get_table_names():
            if name.startswith("sb_t"):
                inspector.get_columns(name)
                inspector.get_pk_constraint(name)
                inspector.get_foreign_keys(name)
                inspector.get_indexes(name)

    sa_inspector = median_ms(inspector_loop, 2)
    engine.dispose()
    return {
        "engine": label,
        "tables": count,
        "columns": columns,
        "foreign_keys": foreign_keys,
        "easydbms_ms": ours,
        "sqlalchemy_reflect_ms": sa_reflect,
        "sqlalchemy_inspector_ms": sa_inspector,
        "same_counts": len(mine) == len(metadata.tables)
        and columns == sa_columns
        and foreign_keys == sa_fks,
    }


# ---------------------------------------------------------------------- layout and scene


def random_schema(
    count: int, seed: int = 1
) -> tuple[list[str], dict[str, tuple[float, float]], list[tuple[str, str]]]:
    rng = random.Random(seed)
    names = [f"t{i:04d}" for i in range(count)]
    sizes = {n: (rng.randint(180, 300), 30 + 22 * rng.randint(3, 14)) for n in names}
    edges = []
    for i in range(1, count):
        edges.append((names[i], names[rng.randrange(i)]))
        if i % 3 == 0:
            edges.append((names[i], names[rng.randrange(i)]))
    return names, sizes, edges


def bench_layout(sizes: tuple[int, ...]) -> list[dict[str, Any]]:
    rows = []
    for count in sizes:
        _, boxes, edges = random_schema(count)
        ms = median_ms(lambda boxes=boxes, edges=edges: layout(boxes, edges), 5)
        rows.append({"tables": count, "edges": len(edges), "layout_ms": ms})
        print(rows[-1], flush=True)
    return rows


def bench_scene(sizes: tuple[int, ...]) -> list[dict[str, Any]]:
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QApplication

    from easydbms.ui.erd import ErdScene, ErdView
    from easydbms.ui.theme import apply_theme, current_tokens

    app = QApplication.instance() or QApplication([])
    assert isinstance(app, QApplication)
    apply_theme(app, "dark")
    rows = []
    sqlite_file = Path(tempfile.mkdtemp()) / "scene.sqlite"
    sqlite3.connect(sqlite_file).close()
    parsed = parse_connection_url(f"sqlite:///{sqlite_file}")
    for count in sizes:
        client = create_client(parsed.config, None)
        client.connect()
        prepare(client, count)
        schema = introspect(client)
        client.disconnect()
        scene = ErdScene(current_tokens())
        view = ErdView(scene)
        view.resize(1200, 800)
        view.show()
        model = build_erd(schema)
        started = time.perf_counter()
        scene.build(model)
        build_ms = (time.perf_counter() - started) * 1000
        view.fit_all()
        fit_zoom = view.zoom

        def paint(view: ErdView = view) -> None:
            image = QImage(1200, 800, QImage.Format.Format_ARGB32)
            image.fill(0)
            painter = QPainter(image)
            view.render(painter)
            painter.end()

        overview_ms = median_ms(paint, 5)
        first = scene.cards()[0]
        view.set_zoom(1.0)
        view.centerOn(first)
        detail_ms = median_ms(paint, 5)
        scene_ms = median_ms(lambda scene=scene: scene.set_filter("t00"), 5)
        scene.set_filter("")
        rows.append(
            {
                "tables": count,
                "relations": len(model.relations),
                "build_ms": build_ms,
                "fit_zoom": fit_zoom,
                "paint_overview_ms": overview_ms,
                "paint_detail_ms": detail_ms,
                "filter_ms": scene_ms,
            }
        )
        print(rows[-1], flush=True)
        view.close()
    return rows


# ---------------------------------------------------------------------- report


def markdown(
    stage: str,
    introspection: list[dict[str, Any]],
    layouts: list[dict[str, Any]],
    scenes: list[dict[str, Any]],
) -> str:
    lines = [
        f"# Schema and diagram benchmarks, stage {stage}",
        "",
        "Median of several runs on the development machine (4 cores). Synthetic schema: tables of 9 columns "
        "with a primary key, one or two foreign keys and an index each.",
        "",
        "## Reading the structure",
        "",
        "| Engine | Tables | Columns | Foreign keys | EasyDBMS | SQLAlchemy `MetaData.reflect()` | SQLAlchemy `Inspector` per table | Same counts |",
        "|---|---:|---:|---:|---:|---:|---:|:--:|",
    ]
    for r in introspection:
        lines.append(
            f"| {r['engine']} | {r['tables']} | {r['columns']} | {r['foreign_keys']} | "
            f"**{r['easydbms_ms']:.0f} ms** | {r['sqlalchemy_reflect_ms']:.0f} ms "
            f"({r['sqlalchemy_reflect_ms'] / r['easydbms_ms']:.1f}×) | "
            f"{r['sqlalchemy_inspector_ms']:.0f} ms ({r['sqlalchemy_inspector_ms'] / r['easydbms_ms']:.1f}×) | "
            f"{'yes' if r['same_counts'] else 'NO'} |"
        )
    lines += [
        "",
        "SQLAlchemy builds typed `Table` objects and reflects more (server defaults as expressions, "
        "check constraints, comments); EasyDBMS reads only what the diagram and autocomplete need.",
        "",
        "## Layout",
        "",
        "| Tables | Edges | Layout |",
        "|---:|---:|---:|",
    ]
    for r in layouts:
        lines.append(f"| {r['tables']:,} | {r['edges']:,} | {r['layout_ms']:.1f} ms |")
    lines += [
        "",
        "## Qt scene (offscreen, 1200×800 view)",
        "",
        "| Tables | Relations | Build the scene | Repaint, whole diagram in view | Repaint at 100 % | Filter by name |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for r in scenes:
        lines.append(
            f"| {r['tables']:,} | {r['relations']:,} | {r['build_ms']:.0f} ms | "
            f"{r['paint_overview_ms']:.0f} ms (zoom {r['fit_zoom']:.2f}) | {r['paint_detail_ms']:.0f} ms | "
            f"{r['filter_ms']:.1f} ms |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", default="dev")
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--out", type=Path, default=RESULTS_DIR)
    parser.add_argument("--skip-db", action="store_true")
    args = parser.parse_args()

    introspection = [] if args.skip_db else bench_introspection((100, 300, 1000), args.repeat)
    layouts = bench_layout((50, 200, 1000, 3000))
    scenes = bench_scene((200, 1000))
    args.out.mkdir(parents=True, exist_ok=True)
    stem = f"schema-stage-{args.stage}"
    payload = {"introspection": introspection, "layout": layouts, "scene": scenes}
    (args.out / f"{stem}.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    report = markdown(args.stage, introspection, layouts, scenes)
    (args.out / f"{stem}.md").write_text(report, encoding="utf-8")
    print("\n" + report)


if __name__ == "__main__":
    main()
