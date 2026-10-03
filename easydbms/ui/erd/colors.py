"""Colours of the diagram, derived from the active theme."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor

from ..theme import Tokens


@dataclass(frozen=True, slots=True)
class ErdColors:
    card: QColor
    header: QColor
    header_view: QColor
    border: QColor
    text: QColor
    muted: QColor
    pk: QColor
    fk: QColor
    edge: QColor
    edge_active: QColor
    row_hover: QColor
    group_fill: QColor
    group_border: QColor


def erd_colors(tokens: Tokens) -> ErdColors:
    dark = tokens.name == "dark"
    return ErdColors(
        card=QColor(tokens.panel_alt),
        header=QColor("#2d3a52" if dark else "#dbe7fb"),
        header_view=QColor("#3a3250" if dark else "#e8e0f7"),
        border=QColor(tokens.border),
        text=QColor(tokens.text),
        muted=QColor(tokens.text_muted),
        pk=QColor("#e3b341" if dark else "#a67c00"),
        fk=QColor("#58a6ff" if dark else "#0b5fd0"),
        edge=QColor("#7d8590" if dark else "#7a838c"),
        edge_active=QColor(tokens.accent),
        row_hover=QColor(tokens.hover),
        group_fill=QColor(255, 255, 255, 8 if dark else 40),
        group_border=QColor(tokens.border),
    )
