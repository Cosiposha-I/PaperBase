"""Чтение config.toml с дефолтами. Пути резолвятся относительно корня проекта."""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "PaperBase"


def user_data_root() -> Path:
    """Пользовательская папка данных приложения по правилам ОС.

    Windows: %LOCALAPPDATA%; macOS: ~/Library/Application Support;
    Linux/прочее: $XDG_DATA_HOME или ~/.local/share.
    """
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def resolve_model(model: str) -> str:
    """Куда смотреть за моделью эмбеддингов.

    1) существующий путь — берём как есть;
    2) иначе ищем папку с таким же именем рядом с программой (`models/<имя>`) и в
       пользовательских данных (`<user_data>/PaperBase/models/<имя>`) — так модель можно
       подложить вручную, если HuggingFace недоступен;
    3) иначе считаем именем HF-хаба (скачается при первом запуске).
    """
    if not model:
        return model
    p = Path(model).expanduser()
    if p.exists():
        return str(p)
    name = p.name                      # 'bge-m3' из '/путь/к/bge-m3' и из 'BAAI/bge-m3'
    for base in (PROJECT_ROOT / "models", user_data_root() / APP_NAME / "models"):
        candidate = base / name
        if candidate.exists():
            return str(candidate)
    return model                       # имя HF-хаба


@dataclass
class Config:
    corpus_dir: Path = PROJECT_ROOT.parent / "Литература"
    data_dir: Path = PROJECT_ROOT / "data"

    # Дефолт-запас (боевое значение задаётся в config.toml, здесь — на случай без TOML):
    model: str = "BAAI/bge-m3"
    fallback_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    device: str = "auto"
    batch_size: int = 16

    chunk_tokens: int = 450
    overlap_tokens: int = 80
    min_chars_per_page: int = 30

    default_k: int = 8

    ocr_languages: str = "eng+rus"
    tesseract_cmd: str = ""
    ocr_workers: int = 0            # параллельных процессов Tesseract; 0 = авто

    crossref_enabled: bool = True
    crossref_timeout: float = 5.0
    crossref_mailto: str = ""

    # Резервные копии базы (SQLite + ChromaDB)
    backup_enabled: bool = True
    backup_before_ingest: bool = True
    backup_keep: int = 10                 # сколько последних копий хранить
    backup_min_interval_hours: float = 12.0   # авто-бэкап не чаще, чем раз в N часов

    config_path: Path | None = field(default=None, repr=False)
    _extra: dict = field(default_factory=dict, repr=False)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "corpus.db"

    @property
    def chroma_dir(self) -> Path:
        native = self.data_dir / "chroma"
        if str(native).isascii():
            return native
        # ChromaDB (Rust) не умеет не-ASCII пути на Windows: бинарный HNSW-сегмент по
        # пути с кириллицей не читается после перезапуска (Error loading hnsw index).
        # Держим векторный индекс на ASCII-пути; SQLite/логи/бэкапы — в data_dir как есть.
        import hashlib
        key = hashlib.sha1(str(self.data_dir.resolve()).encode("utf-8")).hexdigest()[:12]
        return user_data_root() / APP_NAME / f"chroma-{key}"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def log_path(self) -> Path:
        return self.log_dir / "ingest.log"

    @property
    def errors_log_path(self) -> Path:
        """Человекочитаемый журнал ошибок/предупреждений (с трейсбеками)."""
        return self.log_dir / "errors.log"

    @property
    def diagnostics_path(self) -> Path:
        """Структурированный журнал (JSON Lines) — по записи на проблему."""
        return self.log_dir / "diagnostics.jsonl"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.chroma_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


def _resolve(p: str) -> Path:
    path = Path(p).expanduser()
    return path if path.is_absolute() else (PROJECT_ROOT / path)


def _apply_env_path_overrides(cfg: Config) -> None:
    """Переопределить пути из окружения (лаунчер мультикорпуса задаёт свою тему):
    одна установка обслуживает несколько корпусов — у каждого свои corpus_dir/data_dir,
    а модель/OCR/бэкапы берутся из общего config.toml."""
    corpus = os.environ.get("PAPERBASE_CORPUS_DIR")
    data = os.environ.get("PAPERBASE_DATA_DIR")
    if corpus:
        cfg.corpus_dir = _resolve(corpus)
    if data:
        cfg.data_dir = _resolve(data)


def load_config() -> Config:
    cfg = Config()
    toml_path = Path(os.environ.get("PAPERBASE_CONFIG", PROJECT_ROOT / "config.toml"))
    if not toml_path.exists():
        _apply_env_path_overrides(cfg)
        cfg.model = resolve_model(cfg.model)
        return cfg
    raw = toml_path.read_bytes()
    if raw[:3] == b"\xef\xbb\xbf":          # UTF-8 BOM (напр. от PowerShell/редактора) — tomllib его не любит
        raw = raw[3:]
    data = tomllib.loads(raw.decode("utf-8"))

    paths = data.get("paths", {})
    if paths.get("corpus_dir"):
        cfg.corpus_dir = _resolve(paths["corpus_dir"])
    if paths.get("data_dir"):
        cfg.data_dir = _resolve(paths["data_dir"])

    emb = data.get("embedding", {})
    cfg.model = emb.get("model", cfg.model)
    cfg.fallback_model = emb.get("fallback_model", cfg.fallback_model)
    cfg.device = emb.get("device", cfg.device)
    cfg.batch_size = int(emb.get("batch_size", cfg.batch_size))

    ch = data.get("chunking", {})
    cfg.chunk_tokens = int(ch.get("chunk_tokens", cfg.chunk_tokens))
    cfg.overlap_tokens = int(ch.get("overlap_tokens", cfg.overlap_tokens))
    cfg.min_chars_per_page = int(ch.get("min_chars_per_page", cfg.min_chars_per_page))

    cfg.default_k = int(data.get("search", {}).get("default_k", cfg.default_k))

    ocr = data.get("ocr", {})
    cfg.ocr_languages = ocr.get("languages", cfg.ocr_languages)
    cfg.tesseract_cmd = ocr.get("tesseract_cmd", cfg.tesseract_cmd)
    cfg.ocr_workers = int(ocr.get("workers", cfg.ocr_workers))

    cr = data.get("crossref", {})
    cfg.crossref_enabled = bool(cr.get("enabled", cfg.crossref_enabled))
    cfg.crossref_timeout = float(cr.get("timeout", cfg.crossref_timeout))
    cfg.crossref_mailto = cr.get("mailto", cfg.crossref_mailto)

    bk = data.get("backup", {})
    cfg.backup_enabled = bool(bk.get("enabled", cfg.backup_enabled))
    cfg.backup_before_ingest = bool(bk.get("before_ingest", cfg.backup_before_ingest))
    cfg.backup_keep = int(bk.get("keep", cfg.backup_keep))
    cfg.backup_min_interval_hours = float(
        bk.get("min_interval_hours", cfg.backup_min_interval_hours))

    cfg.config_path = toml_path
    cfg._extra = data
    _apply_env_path_overrides(cfg)
    cfg.model = resolve_model(cfg.model)      # локальная папка модели или имя HF-хаба
    return cfg
