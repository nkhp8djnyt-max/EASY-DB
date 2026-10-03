"""Where the application keeps its files."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import platformdirs

APP_NAME = "sql-erd-studio"
#: Point this at a directory to keep config and data together (portable installs, tests).
HOME_ENV = "SQL_ERD_STUDIO_HOME"


@dataclass(frozen=True, slots=True)
class AppPaths:
    config_dir: Path
    data_dir: Path

    @property
    def connections_file(self) -> Path:
        return self.config_dir / "connections.json"

    @property
    def settings_file(self) -> Path:
        return self.config_dir / "settings.toml"

    @property
    def vault_file(self) -> Path:
        return self.config_dir / "vault.json"

    @property
    def app_db(self) -> Path:
        return self.data_dir / "app.db"

    def ensure(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def default(cls, environ: dict[str, str] | None = None) -> AppPaths:
        env = os.environ if environ is None else environ
        home = env.get(HOME_ENV)
        if home:
            root = Path(home).expanduser()
            return cls(config_dir=root / "config", data_dir=root / "data")
        return cls(
            config_dir=Path(platformdirs.user_config_dir(APP_NAME, appauthor=False)),
            data_dir=Path(platformdirs.user_data_dir(APP_NAME, appauthor=False)),
        )

    @classmethod
    def under(cls, root: Path) -> AppPaths:
        return cls(config_dir=root / "config", data_dir=root / "data")
