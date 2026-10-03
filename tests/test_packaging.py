"""The packaging files and the bits of the application they rely on."""

from __future__ import annotations

import struct
import tomllib
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

import easydbms
from easydbms.app import main
from easydbms.ui.icons import app_icon, icon_file

ROOT = Path(easydbms.__file__).resolve().parent.parent


def test_version_flag_prints_the_version_without_starting_the_gui(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["easydbms", "--version"]) == 0
    assert capsys.readouterr().out.strip() == f"EasyDBMS {easydbms.__version__}"


def test_the_version_in_pyproject_and_in_the_package_agree() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["version"] == easydbms.__version__


def test_the_optional_cloud_sdks_are_extras_not_requirements() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    required = " ".join(project["dependencies"])
    for sdk in ("boto3", "azure-identity", "google-auth"):
        assert sdk not in required
    extras = project["optional-dependencies"]
    assert {"aws", "azure", "gcp", "cloud", "build"} <= set(extras)
    assert any("boto3" in d for d in extras["cloud"])
    assert any("pyinstaller" in d.lower() for d in extras["build"])


def test_the_pyinstaller_spec_and_scripts_are_valid_python() -> None:
    for name in (
        "packaging/easydbms.spec",
        "packaging/launcher.py",
        "scripts/build_app.py",
        "scripts/make_icon.py",
    ):
        compile((ROOT / name).read_text(encoding="utf-8"), name, "exec")


def test_the_launcher_starts_the_real_entry_point() -> None:
    text = (ROOT / "packaging" / "launcher.py").read_text(encoding="utf-8")
    assert "from easydbms.app import main" in text


def test_the_workflows_are_valid_yaml_with_the_expected_jobs() -> None:
    yaml = pytest.importorskip("yaml")
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    assert "test" in ci["jobs"]
    steps = " ".join(str(s.get("run", "")) for s in ci["jobs"]["test"]["steps"])
    assert "ruff check" in steps
    assert "mypy" in steps
    assert "pytest" in steps
    build = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
    )
    assert build["jobs"]["build"]["strategy"]["matrix"]["os"] == [
        "ubuntu-latest",
        "windows-latest",
        "macos-latest",
    ]


def test_the_app_icon_is_the_logo_file_and_is_not_blank(qapp: QApplication) -> None:
    assert icon_file() == ROOT / "packaging" / "icon.png"
    icon = app_icon()
    assert not icon.isNull()
    image = icon.pixmap(64, 64).toImage()
    assert (image.width(), image.height()) == (64, 64)
    colors = {image.pixel(x, y) for x in range(0, 64, 4) for y in range(0, 64, 4)}
    assert len(colors) >= 2  # the background and the mark, not one flat fill


def test_the_shipped_icon_files_have_the_formats_their_extensions_promise() -> None:
    png = (ROOT / "packaging" / "icon.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    ico = (ROOT / "packaging" / "icon.ico").read_bytes()
    reserved, kind, count = struct.unpack("<HHH", ico[:6])
    assert (reserved, kind) == (0, 1)  # an ICO, not a renamed JPEG
    assert count >= 1
