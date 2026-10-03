"""Start the real entry point in a fresh interpreter, headless, and let it return immediately."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROGRAM = """
import sys
from PySide6.QtWidgets import QApplication, QMessageBox
QApplication.exec = lambda self=None: 0   # build and show everything, but do not block
# a modal warning would wait for a click: report it instead
QMessageBox.warning = staticmethod(lambda parent, title, text, *a, **k: print("NOTICE:", text))
from sql_erd_studio.app import main
sys.exit(main([]))
"""


def run_app(home: Path, *extra_env: tuple[str, str]) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": "offscreen",
        "SQL_ERD_STUDIO_HOME": str(home),
        **dict(extra_env),
    }
    return subprocess.run(
        [sys.executable, "-c", PROGRAM], env=env, capture_output=True, text=True, timeout=60
    )


def test_the_application_starts_and_creates_its_files(tmp_path: Path) -> None:
    result = run_app(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
    assert (tmp_path / "data" / "app.db").exists()


def test_it_starts_again_on_existing_data_and_with_damaged_config(tmp_path: Path) -> None:
    assert run_app(tmp_path).returncode == 0
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "connections.json").write_text("{ not json")
    result = run_app(tmp_path)
    assert result.returncode == 0, result.stderr
    assert list((tmp_path / "config").glob("connections.json.broken-*"))
    assert "NOTICE:" in result.stdout  # the user is told where the damaged file went
    assert "connections.json.broken-" in result.stdout
