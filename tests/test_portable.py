"""Тесты переносимости: пути по платформам, поиск модели, автопоиск Tesseract."""
import sys

import pytest

from paperbase.config import APP_NAME, resolve_model, user_data_root
from paperbase.extract import _tesseract_candidates


def test_user_data_root_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert user_data_root() == tmp_path / "Local"


def test_user_data_root_macos(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path))
    assert user_data_root() == tmp_path / "Library" / "Application Support"


def test_user_data_root_linux(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    assert user_data_root() == tmp_path / "share"


def test_resolve_model_existing_path(tmp_path):
    d = tmp_path / "my-model"
    d.mkdir()
    assert resolve_model(str(d)) == str(d)


def test_resolve_model_hub_name_kept(monkeypatch, tmp_path):
    # локальной копии нет -> оставляем имя хаба (скачается)
    monkeypatch.setattr("paperbase.config.PROJECT_ROOT", tmp_path / "app")
    monkeypatch.setattr("paperbase.config.user_data_root", lambda: tmp_path / "userdata")
    assert resolve_model("BAAI/bge-m3") == "BAAI/bge-m3"


def test_resolve_model_finds_local_copy(monkeypatch, tmp_path):
    # модель положили вручную рядом с программой -> берём её вместо скачивания
    app = tmp_path / "app"
    (app / "models" / "bge-m3").mkdir(parents=True)
    monkeypatch.setattr("paperbase.config.PROJECT_ROOT", app)
    monkeypatch.setattr("paperbase.config.user_data_root", lambda: tmp_path / "userdata")
    assert resolve_model("BAAI/bge-m3") == str(app / "models" / "bge-m3")


def test_resolve_model_finds_in_user_data(monkeypatch, tmp_path):
    # путь из чужого конфига не существует -> ищем копию по имени в данных пользователя
    ud = tmp_path / "userdata"
    (ud / APP_NAME / "models" / "bge-m3").mkdir(parents=True)
    monkeypatch.setattr("paperbase.config.PROJECT_ROOT", tmp_path / "app")
    monkeypatch.setattr("paperbase.config.user_data_root", lambda: ud)
    assert resolve_model("Z:/нет-такой-папки/bge-m3") == str(
        ud / APP_NAME / "models" / "bge-m3")


@pytest.mark.parametrize("platform,expect", [
    ("win32", "Tesseract-OCR"),
    ("darwin", "homebrew"),
    ("linux", "usr/bin"),
])
def test_tesseract_candidates_per_platform(monkeypatch, platform, expect):
    monkeypatch.setattr(sys, "platform", platform)
    # as_posix(): на Windows Path('/usr/bin') печатается через обратные слэши
    paths = " ".join(p.as_posix() for p in _tesseract_candidates())
    assert expect in paths
