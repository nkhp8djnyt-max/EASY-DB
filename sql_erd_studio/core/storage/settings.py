"""``settings.toml``: user preferences."""

from __future__ import annotations

import os
import time
import tomllib
from pathlib import Path
from typing import Literal

import tomli_w
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class AppSettings(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    theme: Literal["dark", "light"] = "dark"
    #: ``auto`` follows the system locale (Russian for ``ru*``, English otherwise).
    language: Literal["auto", "ru", "en"] = "auto"
    #: Most rows a query result keeps; the rest is cut off (and reported) to protect memory.
    row_limit: int = Field(default=1000, ge=1, le=10_000_000)


class SettingsStore:
    """Reads and writes :class:`AppSettings`; a broken file is set aside and defaults are used."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self.recovered_from: Path | None = None

    def load(self) -> AppSettings:
        self.recovered_from = None
        if not self._path.exists():
            return AppSettings()
        try:
            data = tomllib.loads(self._path.read_text(encoding="utf-8"))
            return AppSettings.model_validate(data)
        except (OSError, ValueError, ValidationError):
            target = self._path.with_name(f"{self._path.name}.broken-{int(time.time())}")
            try:
                os.replace(self._path, target)
                self.recovered_from = target
            except OSError:
                pass
            return AppSettings()

    def save(self, settings: AppSettings) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text(tomli_w.dumps(settings.model_dump(mode="json")), encoding="utf-8")
        os.replace(temporary, self._path)
