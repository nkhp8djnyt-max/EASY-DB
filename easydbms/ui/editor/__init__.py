"""The SQL editor widget, its syntax highlighting and autocomplete."""

from .completion import CompletionController, CompletionSource
from .completion_popup import CompletionPopup
from .highlighter import SqlHighlighter
from .sql_editor import SqlEditor

__all__ = [
    "CompletionController",
    "CompletionPopup",
    "CompletionSource",
    "SqlEditor",
    "SqlHighlighter",
]
