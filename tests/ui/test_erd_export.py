"""Saving the diagram: PNG, SVG, PDF, Mermaid and DBML, from the scene and from the pane."""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from easydbms.ui.erd import ErdPane
from easydbms.ui.erd.export import ExportFormat, export_diagram, file_filters, format_for_path

from .conftest import Env, Prompts
from .test_erd_pane import ready_pane


@pytest.fixture
def pane(env: Env, qtbot: QtBot) -> ErdPane:
    return ready_pane(env, qtbot)


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


# ---------------------------------------------------------------------------- formats


def test_the_format_follows_the_file_suffix() -> None:
    assert format_for_path("a/b.PNG") is ExportFormat.PNG
    assert format_for_path("x.svg") is ExportFormat.SVG
    assert format_for_path("x.pdf") is ExportFormat.PDF
    assert format_for_path("x.mmd") is ExportFormat.MERMAID
    assert format_for_path("x.md") is ExportFormat.MERMAID
    assert format_for_path("x.dbml") is ExportFormat.DBML
    assert format_for_path("x.txt") is None
    assert format_for_path("noextension") is None
    assert [f for f, _ in file_filters()] == list(ExportFormat)


# ---------------------------------------------------------------------------- the scene


def test_png_has_the_size_of_the_diagram_at_twice_the_scale(pane: ErdPane, tmp_path: Path) -> None:
    out = tmp_path / "shop.png"
    assert export_diagram(pane.scene, out) is ExportFormat.PNG
    width, height = png_size(out)
    bounds = pane.scene.visible_bounds()
    assert abs(width - 2 * (bounds.width() + 56)) <= 2
    assert abs(height - 2 * (bounds.height() + 56)) <= 2
    image = QImage(str(out))
    assert not image.isNull()
    colors = {
        image.pixel(x, y)
        for x in range(0, width, max(1, width // 40))
        for y in range(0, height, max(1, height // 40))
    }
    assert len(colors) > 3  # not a blank canvas: cards, text and lines were drawn


def test_svg_is_a_valid_drawing_that_contains_the_table_names(
    pane: ErdPane, tmp_path: Path
) -> None:
    out = tmp_path / "shop.svg"
    assert export_diagram(pane.scene, out) is ExportFormat.SVG
    root = ET.parse(out).getroot()
    assert root.tag.endswith("svg")
    text = out.read_text(encoding="utf-8")
    for name in ("author", "book", "tag"):
        assert name in text


def test_pdf_is_a_single_page_document(pane: ErdPane, tmp_path: Path) -> None:
    out = tmp_path / "shop.pdf"
    assert export_diagram(pane.scene, out) is ExportFormat.PDF
    data = out.read_bytes()
    assert data.startswith(b"%PDF-")
    assert data.count(b"/Type /Page\n") + data.count(b"/Type /Page ") >= 1
    assert len(data) > 2000


def test_text_formats_describe_the_same_tables(pane: ErdPane, tmp_path: Path) -> None:
    mermaid = tmp_path / "shop.mmd"
    dbml = tmp_path / "shop.dbml"
    export_diagram(pane.scene, mermaid)
    export_diagram(pane.scene, dbml)
    assert mermaid.read_text(encoding="utf-8").startswith("erDiagram")
    assert "Table author {" in dbml.read_text(encoding="utf-8")
    assert "author ||--o{ book" in mermaid.read_text(encoding="utf-8")


def test_an_explicit_format_overrides_the_suffix(pane: ErdPane, tmp_path: Path) -> None:
    out = tmp_path / "diagram.dat"
    export_diagram(pane.scene, out, ExportFormat.DBML)
    assert out.read_text(encoding="utf-8").startswith("Table ")


def test_unknown_suffix_and_empty_diagrams_are_refused(pane: ErdPane, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown file type"):
        export_diagram(pane.scene, tmp_path / "x.bmp")
    pane.search.setText("zzz-no-such-table")
    pane.scene.set_filter("zzz-no-such-table")
    with pytest.raises(ValueError, match="nothing to export"):
        export_diagram(pane.scene, tmp_path / "x.png")


def test_a_filtered_diagram_exports_only_what_is_visible(pane: ErdPane, tmp_path: Path) -> None:
    full = tmp_path / "full.png"
    export_diagram(pane.scene, full)
    pane.scene.set_filter("author")
    part = tmp_path / "part.png"
    export_diagram(pane.scene, part)
    assert png_size(part)[0] * png_size(part)[1] < png_size(full)[0] * png_size(full)[1]


def test_exporting_does_not_disturb_the_selection(pane: ErdPane, tmp_path: Path) -> None:
    card = pane.scene.cards()[0]
    card.setSelected(True)
    export_diagram(pane.scene, tmp_path / "x.png")
    assert card.isSelected()


def test_an_unwritable_place_is_an_oserror(pane: ErdPane, tmp_path: Path) -> None:
    for name in ("x.png", "x.mmd", "x.svg", "x.pdf"):
        with pytest.raises(FileNotFoundError, match="does not exist"):
            export_diagram(pane.scene, tmp_path / "no" / "such" / "dir" / name)


# ---------------------------------------------------------------------------- the pane


def test_the_pane_exports_and_announces_it(pane: ErdPane, tmp_path: Path, qtbot: QtBot) -> None:
    with qtbot.waitSignal(pane.exported) as signal:
        assert pane.export_to(str(tmp_path / "a.png")) is ExportFormat.PNG
    assert signal.args == [str(tmp_path / "a.png")]


def test_the_dialog_picks_the_format_from_the_suffix(
    pane: ErdPane, prompts: Prompts, tmp_path: Path
) -> None:
    prompts.save_file = str(tmp_path / "chosen.svg")
    assert pane.export_dialog() == str(tmp_path / "chosen.svg")
    assert (tmp_path / "chosen.svg").exists()


def test_the_dialog_adds_a_suffix_from_the_selected_filter(
    pane: ErdPane, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from PySide6.QtWidgets import QFileDialog

    labels = dict(file_filters())
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **k: (str(tmp_path / "bare"), labels[ExportFormat.PDF])),
    )
    assert pane.export_dialog() == str(tmp_path / "bare.pdf")
    assert (tmp_path / "bare.pdf").read_bytes().startswith(b"%PDF")


def test_cancelling_the_dialog_writes_nothing(pane: ErdPane, prompts: Prompts) -> None:
    prompts.save_file = ""
    assert pane.export_dialog() is None


def test_a_failed_export_is_reported_not_raised(
    pane: ErdPane, prompts: Prompts, tmp_path: Path
) -> None:
    prompts.save_file = str(tmp_path / "missing-dir" / "x.png")
    assert pane.export_dialog() is None
    assert prompts.messages
    assert prompts.messages[-1][0] == "Export the diagram"


def test_without_a_diagram_the_dialog_says_so(env: Env, qtbot: QtBot, prompts: Prompts) -> None:
    from easydbms.core.session import Session
    from easydbms.ui.erd import ErdPane as Pane

    config = env.add_sqlite("empty-one")
    session = Session(config)  # never connected: no structure, only the message page
    pane = Pane(session, env.services.erd_store, env.services.db)
    qtbot.addWidget(pane)
    pane.show()
    assert pane.export_dialog() is None
    assert prompts.messages[-1][1] == "There is no diagram to export yet."
    assert QMessageBox is not None
