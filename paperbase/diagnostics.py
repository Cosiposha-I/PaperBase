"""Единое место для ошибок и предупреждений пайплайна.

Пишет два файла в data/logs/:
  - errors.log        — человекочитаемо, с трейсбеками (WARN/ERROR).
                        Сюда стоит заглядывать, чтобы спокойно прочитать, что пошло не так.
  - diagnostics.jsonl — структурировано (JSON Lines), по записи на проблему,
                        для машинного разбора (команда `check`, скрипты).

Каждая запись: ts, session, stage, level, source (файл/объект), message.
"""

from __future__ import annotations

import json
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import Config

INFO = "INFO"
WARN = "WARN"
ERROR = "ERROR"
_LEVELS = {INFO: 0, WARN: 1, ERROR: 2}


@dataclass
class Diagnostics:
    cfg: Config
    session: str = "session"
    records: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cfg.ensure_dirs()
        # Отметка о начале сессии — чтобы в errors.log было видно границы запусков
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with open(self.cfg.errors_log_path, "a", encoding="utf-8") as f:
            f.write(f"\n===== {ts}  сессия: {self.session} =====\n")

    def record(self, stage: str, level: str, message: str,
               source: str = "", exc: BaseException | None = None) -> None:
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rec = {"ts": ts, "session": self.session, "stage": stage,
               "level": level, "source": source, "message": message}
        self.records.append(rec)

        with open(self.cfg.diagnostics_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        if _LEVELS.get(level, 0) >= _LEVELS[WARN]:
            with open(self.cfg.errors_log_path, "a", encoding="utf-8") as f:
                src = f" | {source}" if source else ""
                f.write(f"[{ts}] {level:5} [{stage}]{src} — {message}\n")
                if exc is not None:
                    f.write("".join(traceback.format_exception(
                        type(exc), exc, exc.__traceback__)))
                    f.write("\n")

    # Удобные обёртки
    def info(self, stage: str, message: str, source: str = "") -> None:
        self.record(stage, INFO, message, source)

    def warn(self, stage: str, message: str, source: str = "") -> None:
        self.record(stage, WARN, message, source)

    def error(self, stage: str, message: str, source: str = "",
              exc: BaseException | None = None) -> None:
        self.record(stage, ERROR, message, source, exc)

    def record_all(self, stage: str, issues: list[tuple[str, str]], source: str = "") -> None:
        """issues — список (level, message), например из checks.validate_*"""
        for level, message in issues:
            self.record(stage, level, message, source)

    def counts(self) -> dict[str, int]:
        out = {INFO: 0, WARN: 0, ERROR: 0}
        for r in self.records:
            out[r["level"]] = out.get(r["level"], 0) + 1
        return out

    @property
    def n_errors(self) -> int:
        return self.counts()[ERROR]

    @property
    def n_warnings(self) -> int:
        return self.counts()[WARN]


def read_diagnostics(cfg: Config, level: str | None = None, limit: int = 0) -> list[dict]:
    """Прочитать записи из diagnostics.jsonl (для команды check / скриптов)."""
    path = cfg.diagnostics_path
    if not path.exists():
        return []
    out: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if level and _LEVELS.get(rec.get("level"), 0) < _LEVELS.get(level, 0):
                continue
            out.append(rec)
    if limit:
        out = out[-limit:]
    return out
