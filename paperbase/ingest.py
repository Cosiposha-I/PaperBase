"""Конвейер ingest: PDF -> текст -> метаданные -> чанки -> векторы -> база.

Ошибки по отдельным файлам пишутся в data/logs/ingest.log, конвейер продолжает
работу. Повторный запуск идемпотентен (sha256); --reingest форсирует переобработку.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from tqdm import tqdm

from . import backup as backup_mod
from . import checks
from .chunk import chunk_pages
from .config import Config
from .diagnostics import Diagnostics
from .embed import Embedder, load_embedder
from .extract import SUPPORTED_EXTS, extract_document
from .meta import extract_metadata, make_cite_key
from .store import Store


@dataclass
class IngestReport:
    processed: int = 0
    skipped: int = 0
    errors: int = 0                  # файлы, упавшие с исключением
    diag_errors: int = 0             # всего ошибок в журнале (файлы + финальный verify)
    warnings: int = 0                # предупреждения диагностики (усечение, OCR и т.п.)
    ocr_pages: int = 0
    ocr_needed_pages: int = 0        # страниц пропущено: нужен Tesseract
    error_files: list[str] = field(default_factory=list)
    ocr_needed_files: list[str] = field(default_factory=list)
    pruned: list[str] = field(default_factory=list)   # записи удалённых с диска файлов
    duplicates: list[str] = field(default_factory=list)  # предупреждения о дубликатах DOI
    backup_path: Path | None = None


def reindex_corpus(cfg: Config, store: Store, batch: int = 512) -> int:
    """Пересобрать векторы ChromaDB из уже сохранённого в SQLite текста чанков —
    без чтения PDF и без CrossRef. Нужно после смены модели эмбеддингов или для
    восстановления векторного индекса. Возвращает число переиндексированных чанков.

    Источник истины — SQLite; коллекция ChromaDB пересоздаётся с нуля, поэтому даже
    прерванный reindex не портит данные (достаточно запустить его снова).
    """
    rows = store.iter_chunk_rows()
    if not rows:
        return 0
    embedder = load_embedder(cfg, tqdm.write)

    store.reset_collection()
    for i in tqdm(range(0, len(rows), batch), desc="reindex", unit="batch"):
        part = rows[i:i + batch]
        embeddings = embedder.embed_passages([r["text"] for r in part])
        store.add_chunk_rows(part, embeddings)
    return len(rows)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _setup_logger(cfg: Config) -> logging.Logger:
    logger = logging.getLogger("paperbase.ingest")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fh = logging.FileHandler(cfg.log_path, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(fh)
    return logger


def ingest_corpus(cfg: Config, store: Store, corpus_dir: Path, *,
                  reingest: bool = False, use_crossref: bool = True,
                  prune: bool = False) -> IngestReport:
    cfg.ensure_dirs()
    log = _setup_logger(cfg)
    session = "ingest " + datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    diag = Diagnostics(cfg, session=session)
    report = IngestReport()

    # Авто-бэкап текущего состояния базы перед изменениями.
    # Для --reingest бэкап форсируется (операция деструктивная).
    try:
        bpath = backup_mod.auto_backup_if_due(cfg, force=reingest)
        if bpath:
            report.backup_path = bpath
            tqdm.write(f"Бэкап базы: {bpath.name}")
    except Exception as e:
        diag.error("backup", "не удалось сделать авто-бэкап перед ingest", exc=e)

    # Очистка: убрать из базы записи, чьих PDF больше нет на диске
    if prune:
        report.pruned = store.prune_missing()
        for name in report.pruned:
            log.info("PRUNE удалён из базы (файл отсутствует): %s", name)
            diag.info("prune", "запись удалена — файла нет на диске", source=name)

    pdfs = sorted(p for p in corpus_dir.rglob("*")
                  if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS)
    if not pdfs:
        log.warning("Документы не найдены в %s", corpus_dir)
        diag.warn("ingest", f"Документы ({', '.join(sorted(SUPPORTED_EXTS))}) не найдены в {corpus_dir}")
        report.warnings = diag.n_warnings
        return report

    # Полная переиндексация: пересоздаём коллекцию векторов целиком (чистые add),
    # иначе массовый delete+add может оставить HNSW-индекс несогласованным.
    if reingest:
        store.reset_collection()

    embedder: Embedder | None = None

    for pdf in tqdm(pdfs, desc="ingest", unit="pdf"):
        try:
            sha = _sha256(pdf)
            existing = store.doc_by_sha(sha)
            if existing and not reingest:
                report.skipped += 1
                continue

            if embedder is None:
                embedder = load_embedder(cfg, tqdm.write)

            # Переобработка: файл с тем же хэшем (--reingest) или заменённый по пути
            if existing:
                store.delete_document(existing["id"])
            else:
                by_path = store.doc_by_path(str(pdf))
                if by_path:
                    store.delete_document(by_path["id"])

            # --- этап extract ---
            extraction = extract_document(pdf, cfg)
            diag.record_all("extract", checks.validate_extraction(extraction), source=pdf.name)
            report.ocr_pages += extraction.ocr_pages
            report.ocr_needed_pages += extraction.ocr_needed_pages
            if extraction.ocr_needed_pages:
                report.ocr_needed_files.append(pdf.name)

            first_pages_text = "\n".join(p.text for p in extraction.pages[:2])
            meta = extract_metadata(
                pdf, first_pages_text,
                crossref_enabled=use_crossref and cfg.crossref_enabled,
                crossref_timeout=cfg.crossref_timeout,
                crossref_mailto=cfg.crossref_mailto,
            )
            cite_key = store.unique_cite_key(make_cite_key(meta, pdf.name))

            # Дедуп: тот же DOI уже есть у другого файла — вероятно дубликат
            if meta.doi:
                dups = [d for d in store.docs_by_doi(meta.doi) if d["sha256"] != sha]
                if dups:
                    report.duplicates.append(pdf.name)
                    diag.warn("dedup", f"DOI {meta.doi} уже в корпусе "
                              f"({dups[0]['filename']}) — возможно дубликат", source=pdf.name)

            text_pages = sum(1 for p in extraction.pages if p.text.strip())
            if text_pages == 0:
                status = "ocr_skipped" if extraction.ocr_needed_pages else "error"
            elif extraction.ocr_needed_pages > 0:
                status = "partial"
            else:
                status = "ok"

            doc_id = store.insert_document(
                filename=pdf.name, path=str(pdf), sha256=sha, cite_key=cite_key,
                title=meta.title, authors=meta.authors, year=meta.year,
                journal=meta.journal, doi=meta.doi, abstract=meta.abstract,
                num_pages=extraction.num_pages, status=status,
            )

            # --- этап chunk ---
            chunks = chunk_pages(extraction.pages, embedder.count_tokens,
                                 cfg.chunk_tokens, cfg.overlap_tokens)
            diag.record_all("chunk", checks.validate_chunks(chunks, embedder), source=pdf.name)

            if chunks:
                try:
                    # --- этап embed ---
                    embeddings = embedder.embed_passages([c.text for c in chunks])
                    diag.record_all("embed", checks.validate_embeddings(embeddings, chunks),
                                    source=pdf.name)
                    # --- этап store ---
                    vec_before = store.vector_count()
                    store.add_chunks(doc_id, {"cite_key": cite_key, "filename": pdf.name,
                                              "year": meta.year}, chunks, embeddings)
                    vec_after = store.vector_count()
                    diag.record_all("store", checks.validate_persisted(
                        store, doc_id, len(chunks), vec_before, vec_after), source=pdf.name)
                except Exception:
                    store.set_status(doc_id, "error")
                    raise

            report.processed += 1
            log.info("OK %s -> doc_id=%s cite_key=%s chunks=%s status=%s",
                     pdf.name, doc_id, cite_key, len(chunks), status)

        except Exception as e:
            report.errors += 1
            report.error_files.append(pdf.name)
            log.exception("Ошибка при обработке %s", pdf)
            diag.error("ingest", f"обработка прервана: {e}", source=pdf.name, exc=e)

    # Финальный гейт: убеждаемся, что векторный индекс читается в этом же процессе
    # и согласован с SQLite. Ловит порчу HNSW-индекса до того, как о ней узнает поиск.
    try:
        vec = store.vector_count()
        sql = store.chunk_count()
        if vec < 0:
            diag.error("verify", "векторный индекс ChromaDB не читается после ingest — "
                       f"восстановите базу: python -m paperbase restore --yes")
        elif vec != sql:
            diag.error("verify", f"рассинхрон после ingest: чанков в SQLite {sql}, "
                       f"векторов в ChromaDB {vec}")
        else:
            diag.info("verify", f"итоговая проверка ок: {sql} чанков ↔ {vec} векторов")
    except Exception as e:
        diag.error("verify", "итоговая проверка целостности не выполнена", exc=e)

    report.diag_errors = diag.n_errors     # все ошибки в журнале (файлы + verify)
    report.warnings = diag.n_warnings
    return report
