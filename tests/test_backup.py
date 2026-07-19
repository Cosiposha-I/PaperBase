"""Тесты вспомогательных функций бэкапа (без создания реальных архивов)."""
from datetime import datetime
from pathlib import Path

from paperbase.config import Config
from paperbase.backup import _stamp_of, resolve_backup


def test_stamp_of_valid():
    assert _stamp_of(Path("paperbase-20260711-023424.zip")) == datetime(2026, 7, 11, 2, 34, 24)


def test_stamp_of_garbage():
    assert _stamp_of(Path("garbage.zip")) is None


def _touch(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")


def test_resolve_backup(tmp_path):
    cfg = Config(data_dir=tmp_path)
    _touch(cfg.backup_dir / "paperbase-20260101-000000.zip")
    _touch(cfg.backup_dir / "paperbase-20260102-000000.zip")

    assert resolve_backup(cfg, None).name == "paperbase-20260102-000000.zip"      # latest
    assert resolve_backup(cfg, "latest").name == "paperbase-20260102-000000.zip"
    assert resolve_backup(cfg, "20260101").name == "paperbase-20260101-000000.zip"  # substring
    assert resolve_backup(cfg, "does-not-exist") is None


def test_resolve_backup_empty(tmp_path):
    cfg = Config(data_dir=tmp_path)
    assert resolve_backup(cfg, None) is None
