from __future__ import annotations

from pathlib import Path

from easydbms.core.paths import HOME_ENV, AppPaths


def test_home_override_keeps_everything_under_one_directory(tmp_path: Path) -> None:
    paths = AppPaths.default({HOME_ENV: str(tmp_path / "portable")})
    assert paths.config_dir == tmp_path / "portable" / "config"
    assert paths.data_dir == tmp_path / "portable" / "data"
    assert paths.connections_file == paths.config_dir / "connections.json"
    assert paths.settings_file == paths.config_dir / "settings.toml"
    assert paths.vault_file == paths.config_dir / "vault.json"
    assert paths.app_db == paths.data_dir / "app.db"


def test_default_locations_are_per_user_directories() -> None:
    paths = AppPaths.default({})
    assert "easydbms" in str(paths.config_dir)
    assert "easydbms" in str(paths.data_dir)
    assert paths.config_dir != paths.data_dir


def test_ensure_creates_the_directories(tmp_path: Path) -> None:
    paths = AppPaths.under(tmp_path / "root")
    assert not paths.config_dir.exists()
    paths.ensure()
    paths.ensure()  # idempotent
    assert paths.config_dir.is_dir()
    assert paths.data_dir.is_dir()
