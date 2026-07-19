"""Резервные копии базы: SQLite + ChromaDB + config.toml -> один .zip.

Копии кладутся в data/backups/paperbase-ГГГГММДД-ЧЧММСС.zip.

Когда делается бэкап:
  - вручную командой `paperbase backup`;
  - автоматически перед каждым `ingest` — но не чаще, чем раз в
    backup_min_interval_hours (по умолчанию 12 ч), чтобы не плодить копии.

Так как корпус меняется только во время ingest, привязка бэкапа к ingest'у
защищает именно рабочее состояние базы. Хранятся последние `keep` копий
(ротация). Восстановление — `paperbase restore` (с предварительным
страховочным бэкапом текущего состояния).
"""

from __future__ import annotations

import shutil
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

from .config import Config

_PREFIX = "paperbase-"
_STAMP_FMT = "%Y%m%d-%H%M%S"


def list_backups(cfg: Config) -> list[Path]:
    d = cfg.backup_dir
    if not d.exists():
        return []
    return sorted(d.glob(f"{_PREFIX}*.zip"))


def _stamp_of(path: Path) -> datetime | None:
    try:
        return datetime.strptime(path.stem[len(_PREFIX):], _STAMP_FMT)
    except (ValueError, IndexError):
        return None


def last_backup_time(cfg: Config) -> datetime | None:
    backups = list_backups(cfg)
    if not backups:
        return None
    return _stamp_of(backups[-1]) or datetime.fromtimestamp(backups[-1].stat().st_mtime)


def _prune(cfg: Config) -> list[Path]:
    backups = list_backups(cfg)
    keep = cfg.backup_keep
    removed = []
    if keep > 0 and len(backups) > keep:
        for old in backups[:-keep]:
            old.unlink(missing_ok=True)
            removed.append(old)
    return removed


def create_backup(cfg: Config, reason: str = "manual", db_only: bool = False) -> Path:
    """Сделать снимок базы. Возвращает путь к .zip.

    db_only=True — копировать только SQLite и config (без каталога chroma). Компактно
    для большого корпуса: векторы восстановимы из текста в SQLite командой `reindex`.
    """
    cfg.backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime(_STAMP_FMT)
    suffix = "-dbonly" if db_only else ""
    zip_path = cfg.backup_dir / f"{_PREFIX}{stamp}{suffix}.zip"

    # Консистентный снимок SQLite через backup API (безопасно даже при WAL)
    tmp_db = cfg.backup_dir / f".corpus-{stamp}.tmp.db"
    if cfg.db_path.exists():
        src = sqlite3.connect(cfg.db_path)
        dst = sqlite3.connect(tmp_db)
        with dst:
            src.backup(dst)
        dst.close()
        src.close()

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        if tmp_db.exists():
            z.write(tmp_db, "corpus.db")
        if not db_only and cfg.chroma_dir.exists():
            for p in cfg.chroma_dir.rglob("*"):
                if p.is_file():
                    z.write(p, str(Path("chroma") / p.relative_to(cfg.chroma_dir)))
        if cfg.config_path and Path(cfg.config_path).exists():
            z.write(cfg.config_path, "config.toml")
        z.writestr("MANIFEST.txt",
                   f"paperbase backup\nсоздан: {datetime.now().isoformat(timespec='seconds')}\n"
                   f"причина: {reason}\nтип: {'db-only' if db_only else 'полный'}\n"
                   f"sqlite: {cfg.db_path.name}\n"
                   f"chroma: {'НЕ включён — восстановить через reindex' if db_only else cfg.chroma_dir.name}\n")

    if tmp_db.exists():
        tmp_db.unlink()
    _prune(cfg)
    return zip_path


def auto_backup_if_due(cfg: Config, force: bool = False) -> Path | None:
    """Авто-бэкап перед ingest. force=True (для --reingest) игнорирует throttle,
    т.к. полная переиндексация деструктивна. None — если бэкап не требуется."""
    if not (cfg.backup_enabled and cfg.backup_before_ingest):
        return None
    if not cfg.db_path.exists():
        return None  # базы ещё нет — нечего копировать
    if not force:
        last = last_backup_time(cfg)
        if last is not None:
            age_h = (datetime.now() - last).total_seconds() / 3600
            if age_h < cfg.backup_min_interval_hours:
                return None
    return create_backup(cfg, reason="auto перед reingest" if force else "auto перед ingest")


def resolve_backup(cfg: Config, which: str | None) -> Path | None:
    """which: None/'latest' -> последняя; иначе имя файла или его часть."""
    backups = list_backups(cfg)
    if not backups:
        return None
    if which in (None, "", "latest"):
        return backups[-1]
    for b in backups:
        if b.name == which or which in b.name:
            return b
    return None


def restore_backup(cfg: Config, zip_path: Path) -> tuple[Path, bool]:
    """Восстановить базу из .zip. Текущее состояние сначала уходит в страховочный
    бэкап. Возвращает (путь к страховочной копии, восстановлены ли векторы chroma).
    Для db-only копий векторов нет — их нужно пересобрать командой `reindex`."""
    safety = create_backup(cfg, reason=f"перед restore {zip_path.name}")

    # Убираем текущие БД и векторы
    for suffix in ("", "-wal", "-shm"):
        f = cfg.db_path.with_name(cfg.db_path.name + suffix)
        if f.exists():
            f.unlink()
    if cfg.chroma_dir.exists():
        shutil.rmtree(cfg.chroma_dir)
    cfg.chroma_dir.mkdir(parents=True, exist_ok=True)

    chroma_restored = False
    with zipfile.ZipFile(zip_path, "r") as z:
        for name in z.namelist():
            if name == "corpus.db":
                with z.open(name) as s, open(cfg.db_path, "wb") as d:
                    shutil.copyfileobj(s, d)
            elif name.startswith("chroma/"):
                target = cfg.chroma_dir / Path(name).relative_to("chroma")
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(name) as s, open(target, "wb") as d:
                    shutil.copyfileobj(s, d)
                chroma_restored = True
    return safety, chroma_restored
