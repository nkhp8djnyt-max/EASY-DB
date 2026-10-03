"""``settings.toml``: user preferences."""

from __future__ import annotations

import os
import time
import tomllib
from pathlib import Path
from typing import Literal

import tomli_w
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class AppSettings(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    #: ``system`` follows the operating system's light / dark setting.
    theme: Literal["dark", "light", "system"] = "dark"
    #: ``auto`` follows the system locale (Ukrainian for ``uk*``, English otherwise).
    language: Literal["auto", "uk", "en"] = "auto"
    #: Most rows a query result keeps; the rest is cut off (and reported) to protect memory.
    row_limit: int = Field(default=1000, ge=1, le=10_000_000)
    #: Case of keywords inserted by autocomplete; ``preserve`` follows what was typed.
    keyword_case: Literal["upper", "lower", "preserve"] = "upper"

    @field_validator("language", mode="before")
    @classmethod
    def _russian_became_ukrainian(cls, value: object) -> object:
        """Russian left the UI; a settings file that still says ``ru`` stays usable."""
        return "uk" if value == "ru" else value


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
