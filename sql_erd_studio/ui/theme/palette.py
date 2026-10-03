"""Design tokens. Dark follows the SQL Academy look: #1a1a1a window, #222 panels, #2f7bf5 accent."""

from __future__ import annotations

from dataclasses import dataclass

from ...core.connections import ConnectionColor


@dataclass(frozen=True, slots=True)
class Tokens:
    name: str
    bg: str
    panel: str
    panel_alt: str
    input_bg: str
    border: str
    text: str
    text_muted: str
    accent: str
    accent_hover: str
    accent_text: str
    selection: str
    hover: str
    danger: str
    success: str
    warning: str


DARK = Tokens(
    name="dark",
    bg="#1a1a1a",
    panel="#222222",
    panel_alt="#2a2a2a",
    input_bg="#171717",
    border="#3a3a3a",
    text="#e6e6e6",
    text_muted="#9a9a9a",
    accent="#2f7bf5",
    accent_hover="#4a8ff7",
    accent_text="#ffffff",
    selection="#26456f",
    hover="#2f2f2f",
    danger="#e5534b",
    success="#3fb950",
    warning="#d29922",
)

LIGHT = Tokens(
    name="light",
    bg="#f4f5f7",
    panel="#ffffff",
    panel_alt="#eef0f3",
    input_bg="#ffffff",
    border="#cfd4da",
    text="#1f2328",
    text_muted="#636c76",
    accent="#2f7bf5",
    accent_hover="#1f66d8",
    accent_text="#ffffff",
    selection="#cfe0fb",
    hover="#e6e9ed",
    danger="#cf222e",
    success="#1a7f37",
    warning="#9a6700",
)

THEMES: dict[str, Tokens] = {"dark": DARK, "light": LIGHT}

#: Label colours of connections (same in both themes so a "prod" red is always the same red).
COLOR_HEX: dict[ConnectionColor, str] = {
    ConnectionColor.RED: "#e5534b",
    ConnectionColor.ORANGE: "#e98a36",
    ConnectionColor.YELLOW: "#d4a72c",
    ConnectionColor.GREEN: "#3fb950",
    ConnectionColor.TEAL: "#2bb3a7",
    ConnectionColor.BLUE: "#2f7bf5",
    ConnectionColor.PURPLE: "#a371f7",
    ConnectionColor.GRAY: "#8b949e",
}


def color_hex(color: ConnectionColor | None, fallback: str = "#6e7681") -> str:
    return COLOR_HEX[color] if color is not None else fallback
