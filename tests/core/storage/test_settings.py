from __future__ import annotations

from pathlib import Path

import pytest

from easydbms.core.storage import AppSettings, SettingsStore


def test_defaults_when_there_is_no_file(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.toml")
    settings = store.load()
    assert (settings.theme, settings.language) == ("dark", "auto")
    assert store.recovered_from is None
    assert not (tmp_path / "settings.toml").exists()


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "config" / "settings.toml"
    store = SettingsStore(path)
    store.save(AppSettings(theme="light", language="ru"))
    assert path.read_text() == (
        'theme = "light"\nlanguage = "ru"\nrow_limit = 1000\nkeyword_case = "upper"\n'
    )
    loaded = SettingsStore(path).load()
    assert (loaded.theme, loaded.language) == ("light", "ru")
    assert [p.name for p in path.parent.iterdir()] == ["settings.toml"]


def test_keyword_case_round_trip_and_validation(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    SettingsStore(path).save(AppSettings(keyword_case="preserve"))
    assert SettingsStore(path).load().keyword_case == "preserve"
    with pytest.raises(ValueError, match="keyword_case"):
        AppSettings(keyword_case="Title")  # type: ignore[arg-type]


def test_unknown_keys_are_ignored_for_forward_compatibility(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text('theme = "light"\nfuture_option = 5\n')
    assert SettingsStore(path).load().theme == "light"


@pytest.mark.parametrize("content", ["theme = ", 'theme = "neon"', "language = 3", "\x00\x01"])
def test_a_broken_file_is_set_aside_and_defaults_are_used(tmp_path: Path, content: str) -> None:
    path = tmp_path / "settings.toml"
    path.write_text(content)
    store = SettingsStore(path)
    assert store.load() == AppSettings()
    assert store.recovered_from is not None
    assert store.recovered_from.read_text() == content
    assert not path.exists()


def test_assignment_is_validated() -> None:
    settings = AppSettings()
    with pytest.raises(ValueError, match="theme"):
        settings.theme = "neon"  # type: ignore[assignment]


def test_row_limit_is_validated_and_persisted(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.toml")
    store.save(AppSettings(row_limit=250))
    assert store.load().row_limit == 250
    for bad in (0, -5):
        with pytest.raises(ValueError, match="row_limit"):
            AppSettings(row_limit=bad)
