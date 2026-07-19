"""Проверки двух видов:

1. validate_* — инварианты этапов пайплайна. Вызываются прямо внутри ingest'а
   после каждого шага, возвращают список (level, message). Ничего не роняют —
   ingest записывает их в Diagnostics и продолжает.

2. run_checks — «доктор»: окружение (зависимости, torch/CUDA, Tesseract, модель),
   конфиг, хранилище и целостность корпуса (совпадение SQLite ↔ ChromaDB и т.п.).
   Используется командой `paperbase check`.
"""

from __future__ import annotations

import math
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .diagnostics import ERROR, INFO, WARN

OK = "OK"
FAIL = "FAIL"


# ─────────────────────────── валидаторы этапов ───────────────────────────

def validate_extraction(extraction) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []
    text_pages = sum(1 for p in extraction.pages if p.text.strip())
    if extraction.num_pages == 0:
        issues.append((ERROR, "PDF без страниц — файл повреждён или пуст"))
    elif text_pages == 0:
        issues.append((ERROR, "нет текстового слоя ни на одной странице — нужен OCR (Tesseract)"))
    if extraction.ocr_needed_pages > 0:
        issues.append((WARN, f"{extraction.ocr_needed_pages} стр. без текста пропущены "
                              f"(Tesseract не установлен) — фрагменты с них не попадут в поиск"))
    if extraction.ocr_pages > 0:
        issues.append((INFO, f"{extraction.ocr_pages} стр. распознаны через OCR"))
    return issues


def validate_chunks(chunks, embedder) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []
    if not chunks:
        issues.append((ERROR, "получено 0 чанков — текст извлечён, но не разбит"))
        return issues
    over = [c for c in chunks if embedder.embedding_token_len(c.text) > embedder.max_seq_length]
    if over:
        worst = max(embedder.embedding_token_len(c.text) for c in over)
        issues.append((WARN, f"{len(over)} из {len(chunks)} чанков длиннее лимита модели "
                             f"({embedder.max_seq_length} ток., макс {worst}) — хвост усечён при "
                             f"эмбеддинге. Уменьшите chunk_tokens в config.toml и переиндексируйте"))
    return issues


def validate_embeddings(embeddings, chunks) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []
    if len(embeddings) != len(chunks):
        issues.append((ERROR, f"векторов {len(embeddings)} ≠ чанков {len(chunks)}"))
        return issues
    if not embeddings:
        return issues
    dim = len(embeddings[0])
    if any(len(v) != dim for v in embeddings):
        issues.append((ERROR, "непостоянная размерность векторов"))
    # NaN/inf ломают косинусный поиск — проверяем края (полный скан 989×1024 не нужен)
    for v in (embeddings[0], embeddings[-1]):
        if any(math.isnan(x) or math.isinf(x) for x in v):
            issues.append((ERROR, "в векторах есть NaN/inf — эмбеддинг некорректен"))
            break
    return issues


def validate_persisted(store, doc_id: int, n_chunks: int,
                       vectors_before: int, vectors_after: int) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []
    sql_n = store.conn.execute("SELECT COUNT(*) FROM chunks WHERE doc_id=?", (doc_id,)).fetchone()[0]
    if sql_n != n_chunks:
        issues.append((ERROR, f"в SQLite записано {sql_n} чанков вместо {n_chunks}"))
    if vectors_before >= 0 and vectors_after >= 0:
        delta = vectors_after - vectors_before
        if delta != n_chunks:
            issues.append((ERROR, f"в ChromaDB добавлено {delta} векторов вместо {n_chunks} "
                                 f"— рассинхрон SQLite ↔ ChromaDB"))
    return issues


# ─────────────────────────────── доктор ───────────────────────────────

@dataclass
class CheckResult:
    stage: str
    status: str          # OK / WARN / FAIL
    detail: str

    def to_dict(self) -> dict:
        return {"stage": self.stage, "status": self.status, "detail": self.detail}


def _check_python() -> CheckResult:
    v = sys.version_info
    status = OK if (v.major, v.minor) >= (3, 11) else WARN
    return CheckResult("python", status, f"{v.major}.{v.minor}.{v.micro}")


def _check_dependencies() -> list[CheckResult]:
    mods = {"fitz": "PyMuPDF", "chromadb": "chromadb", "sentence_transformers":
            "sentence-transformers", "torch": "torch", "openpyxl": "openpyxl",
            "requests": "requests", "tqdm": "tqdm", "PIL": "Pillow", "pytesseract": "pytesseract"}
    results = []
    for mod, name in mods.items():
        try:
            m = __import__(mod)
            ver = getattr(m, "__version__", "?")
            results.append(CheckResult(f"dep:{name}", OK, str(ver)))
        except Exception as e:
            results.append(CheckResult(f"dep:{name}", FAIL, f"не импортируется: {e}"))
    return results


def _check_torch_device(cfg: Config) -> CheckResult:
    try:
        import torch
        want = cfg.device
        cuda = torch.cuda.is_available()
        if want == "cuda" and not cuda:
            return CheckResult("torch.device", WARN,
                               "в конфиге cuda, но CUDA недоступна — будет CPU")
        dev = "cuda" if (want in ("auto", "cuda") and cuda) else "cpu"
        name = torch.cuda.get_device_name(0) if dev == "cuda" else "CPU"
        return CheckResult("torch.device", OK, f"{dev} ({name}), torch {torch.__version__}")
    except Exception as e:
        return CheckResult("torch.device", FAIL, str(e))


def _check_tesseract(cfg: Config) -> CheckResult:
    from .extract import ocr_available
    where = cfg.tesseract_cmd or "в PATH"
    if ocr_available(cfg):
        return CheckResult("tesseract", OK, f"доступен ({where}) — сканы распознаются")
    return CheckResult("tesseract", WARN,
                       f"не установлен (ожидался: {where}) — сканы пропускаются (см. README)")


def _check_model_path(cfg: Config) -> CheckResult:
    """Быстрая проверка пути к модели без её загрузки.
    Локальный путь (с диском/абсолютный) должен существовать; имя HF-хаба — скачается."""
    p = Path(cfg.model)
    if p.is_absolute() or p.drive:            # локальный путь, а не имя HF-хаба
        if p.exists():
            return CheckResult("model.path", OK, f"локальная модель на месте: {cfg.model}")
        return CheckResult("model.path", FAIL,
                           f"модель не найдена по пути {cfg.model} — поправьте `model` в config.toml")
    return CheckResult("model.path", OK, f"HF-хаб: {cfg.model} (скачается при необходимости)")


def _check_config(cfg: Config) -> list[CheckResult]:
    res = []
    if cfg.chunk_tokens <= 0:
        res.append(CheckResult("config.chunk_tokens", FAIL, "должно быть > 0"))
    if cfg.overlap_tokens >= cfg.chunk_tokens:
        res.append(CheckResult("config.overlap", FAIL,
                               f"overlap {cfg.overlap_tokens} ≥ chunk {cfg.chunk_tokens}"))
    if cfg.batch_size <= 0:
        res.append(CheckResult("config.batch_size", FAIL, "должно быть > 0"))
    if not any(r.status == FAIL for r in res):
        res.append(CheckResult("config", OK,
                               f"chunk={cfg.chunk_tokens}, overlap={cfg.overlap_tokens}, "
                               f"k={cfg.default_k}, batch={cfg.batch_size}"))
    # путь к корпусу печатаем всегда (видно, откуда берутся PDF)
    if cfg.corpus_dir.exists():
        res.append(CheckResult("config.corpus", OK, str(cfg.corpus_dir)))
    else:
        res.append(CheckResult("config.corpus", WARN, f"папка корпуса не найдена: {cfg.corpus_dir}"))
    return res


def _check_model(cfg: Config) -> CheckResult:
    try:
        from .embed import Embedder
        emb = Embedder(cfg)
        dim = len(emb.embed_query("проверка"))
        status = OK
        detail = f"{emb.model_name} [{emb.device}], dim={dim}, max_seq={emb.max_seq_length}"
        if cfg.chunk_tokens > emb.max_seq_length:
            status = WARN
            detail += (f" — chunk_tokens={cfg.chunk_tokens} > max_seq={emb.max_seq_length}, "
                       f"часть чанков усечётся")
        return CheckResult("model", status, detail)
    except Exception as e:
        return CheckResult("model", FAIL, f"модель не загрузилась: {e}")


def _check_storage(cfg: Config) -> list[CheckResult]:
    res = []
    if not cfg.db_path.exists():
        res.append(CheckResult("sqlite", WARN, "corpus.db ещё нет — выполните ingest"))
        return res
    try:
        conn = sqlite3.connect(cfg.db_path)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        missing = {"documents", "chunks"} - tables
        if missing:
            res.append(CheckResult("sqlite", FAIL, f"нет таблиц: {', '.join(missing)}"))
        else:
            n = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            res.append(CheckResult("sqlite", OK, f"схема на месте, документов: {n}"))
        conn.close()
    except Exception as e:
        res.append(CheckResult("sqlite", FAIL, str(e)))
    return res


def _check_corpus_integrity(cfg: Config) -> list[CheckResult]:
    res = []
    if not cfg.db_path.exists():
        return res
    from .store import Store
    store = Store(cfg)
    try:
        docs = store.all_docs()
        if not docs:
            res.append(CheckResult("integrity", WARN, "корпус пуст"))
            return res

        sql_chunks = store.chunk_count()
        vectors = store.vector_count()
        if vectors < 0:
            res.append(CheckResult("integrity.vectors", FAIL, "ChromaDB недоступна"))
        elif sql_chunks != vectors:
            res.append(CheckResult("integrity.sync", FAIL,
                                   f"чанков в SQLite {sql_chunks} ≠ векторов в ChromaDB {vectors} "
                                   f"— переиндексируйте: ingest --reingest"))
        else:
            res.append(CheckResult("integrity.sync", OK,
                                   f"SQLite ↔ ChromaDB согласованы ({sql_chunks} чанков)"))

        # документы без чанков и со статусом-ошибкой
        empty = [d["filename"] for d in docs if store.conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE doc_id=?", (d["id"],)).fetchone()[0] == 0]
        if empty:
            res.append(CheckResult("integrity.chunks", WARN,
                                   f"{len(empty)} документ(ов) без чанков: {', '.join(empty[:5])}"))
        bad = [d["filename"] for d in docs if d["status"] not in ("ok",)]
        if bad:
            res.append(CheckResult("integrity.status", WARN,
                                   f"{len(bad)} документ(ов) со статусом ≠ ok: {', '.join(bad[:5])}"))

        # пропавшие с диска PDF (нужны для команды read)
        missing = [d["filename"] for d in docs if not Path(d["path"]).exists()]
        if missing:
            res.append(CheckResult("integrity.files", WARN,
                                   f"{len(missing)} PDF не найдены на диске: {', '.join(missing[:5])}"))
    finally:
        store.close()
    return res


def run_checks(cfg: Config, deep: bool = False) -> list[CheckResult]:
    """Все проверки. deep=True дополнительно грузит модель эмбеддингов."""
    results: list[CheckResult] = [_check_python()]
    results += _check_dependencies()
    results.append(_check_torch_device(cfg))
    results.append(_check_tesseract(cfg))
    results.append(_check_model_path(cfg))
    results += _check_config(cfg)
    results += _check_storage(cfg)
    results += _check_corpus_integrity(cfg)
    if deep:
        results.append(_check_model(cfg))
    return results
