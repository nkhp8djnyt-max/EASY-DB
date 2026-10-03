"""The SQL editor widget and its syntax highlighting."""

from .highlighter import SqlHighlighter
from .sql_editor import SqlEditor

__all__ = ["SqlEditor", "SqlHighlighter"]
