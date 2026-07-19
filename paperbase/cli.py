"""CLI PaperBase (Typer): ingest, reindex, search, read, bib, list, stats,
check, backup, restore, serve.

Все команды локальные, без LLM. --json даёт машиночитаемый вывод (UTF-8)
для дальнейшего синтеза ответов Claude'ом.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

# Windows-консоль: принудительно UTF-8, иначе кириллица ломается
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

import typer

from .config import load_config
from .store import Store

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  pretty_exceptions_enable=False,
                  help="Локальный поисковый движок по корпусу научных PDF.")


@app.callback()
def _main(
    config: Optional[str] = typer.Option(
        None, "--config", help="Путь к config.toml другого корпуса/темы "
        "(по умолчанию — config.toml проекта). Даёт отдельную базу под свою папку статей."),
):
    """Локальный поисковый движок по корпусу научных PDF.

    Для отдельной темы укажите свой config.toml через --config (в нём свои
    corpus_dir и data_dir) — команды будут работать с этой базой.
    """
    # Опция глобальная: применяется до выполнения любой команды. Прокидываем в
    # переменную окружения, которую читает config.load_config().
    if config:
        os.environ["PAPERBASE_CONFIG"] = config


def _jprint(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


@app.command()
def ingest(
    path: Optional[str] = typer.Argument(None, help="Папка с PDF (по умолчанию из config.toml)"),
    reingest: bool = typer.Option(False, "--reingest", help="Форсировать переобработку"),
    no_crossref: bool = typer.Option(False, "--no-crossref", help="Не обращаться к CrossRef"),
    prune: bool = typer.Option(False, "--prune", help="Удалить из базы записи файлов, которых нет на диске"),
):
    """Индексация корпуса: текст, метаданные, чанки, векторы."""
    from .ingest import ingest_corpus

    cfg = load_config()
    corpus = Path(path) if path else cfg.corpus_dir
    if not corpus.exists():
        typer.echo(f"Папка не найдена: {corpus}", err=True)
        raise typer.Exit(1)

    with Store(cfg) as store:
        report = ingest_corpus(cfg, store, corpus, reingest=reingest,
                               use_crossref=not no_crossref, prune=prune)

    typer.echo("")
    if report.backup_path:
        typer.echo(f"Бэкап перед ingest: {report.backup_path}")
    if report.pruned:
        typer.echo(f"Удалено записей (файлов нет на диске): {len(report.pruned)} — "
                   + ", ".join(report.pruned))
    typer.echo(f"Обработано: {report.processed}  |  пропущено (уже в базе): {report.skipped}"
               f"  |  ошибок: {report.errors}  |  предупреждений: {report.warnings}")
    if report.duplicates:
        typer.echo("Возможные дубликаты по DOI: " + ", ".join(report.duplicates))
    if report.ocr_pages:
        typer.echo(f"Распознано OCR: {report.ocr_pages} стр.")
    if report.ocr_needed_pages:
        typer.echo(f"ВНИМАНИЕ: {report.ocr_needed_pages} стр. без текстового слоя пропущены — "
                   f"не установлен Tesseract OCR (см. README). Файлы: "
                   + ", ".join(report.ocr_needed_files))
    if report.error_files:
        typer.echo("Файлы с ошибками: " + ", ".join(report.error_files))
    if report.diag_errors > report.errors:
        typer.echo("ВНИМАНИЕ: итоговая проверка целостности выявила проблему "
                   "(см. журнал; возможно, потребуется restore).")
    if report.diag_errors or report.warnings:
        typer.echo(f"Подробности ошибок и предупреждений: {cfg.errors_log_path}")
        typer.echo(f"Структурированно (JSON Lines): {cfg.diagnostics_path}")
        typer.echo("Быстрый обзор: python -m paperbase check")


@app.command()
def reindex():
    """Пересобрать векторы ChromaDB из текста в SQLite (без чтения PDF и CrossRef).

    Быстрее полного ingest --reingest и не требует исходных PDF. Нужно после смены
    модели эмбеддингов (config.toml) или для восстановления векторного индекса.
    На GPU запускайте через pb-gpu.ps1 reindex.
    """
    from .ingest import reindex_corpus

    cfg = load_config()
    with Store(cfg) as store:
        n = reindex_corpus(cfg, store)
        typer.echo("")
        if n == 0:
            typer.echo("Нет чанков для переиндексации — сначала выполните ingest.")
        else:
            vec, chunks = store.vector_count(), store.chunk_count()
            typer.echo(f"Переиндексировано чанков: {n}. Векторов в ChromaDB: {vec}.")
            if vec != chunks:
                typer.echo(f"ВНИМАНИЕ: векторов {vec} ≠ чанков {chunks} — см. python -m paperbase check.")


@app.command()
def search(
    query: str = typer.Argument(..., help="Смысловой запрос (RU или EN)"),
    k: Optional[int] = typer.Option(None, "--k", help="Сколько фрагментов вернуть"),
    paper: Optional[str] = typer.Option(None, "--paper", help="Фильтр по имени файла (glob/подстрока)"),
    year_min: Optional[int] = typer.Option(None, "--year-min"),
    year_max: Optional[int] = typer.Option(None, "--year-max"),
    as_json: bool = typer.Option(False, "--json", help="Машиночитаемый вывод"),
):
    """Семантический поиск по корпусу. Только поиск, без генерации текста."""
    from .embed import Embedder
    from .query import search as run_search

    cfg = load_config()
    with Store(cfg) as store:
        embedder = Embedder(cfg)
        hits = run_search(store, embedder, query, k=k or cfg.default_k,
                          paper=paper, year_min=year_min, year_max=year_max)

        if as_json:
            _jprint({"query": query, "model": embedder.model_name,
                     "hits": [h.to_dict() for h in hits]})
        elif not hits:
            typer.echo("Ничего не найдено (корпус пуст? выполните ingest).")
        else:
            for h in hits:
                pages = f"стр. {h.pages[0]}" if h.pages[0] == h.pages[1] \
                    else f"стр. {h.pages[0]}–{h.pages[1]}"
                section = f" ({h.section})" if h.section else ""
                typer.echo(f"#{h.rank}  score={h.score:.3f}  [{h.cite_key}] {h.citation} — "
                           f"{h.filename}, {pages}{section}")
                typer.echo(h.text)
                typer.echo("")


@app.command()
def keyword(
    term: str = typer.Argument(..., help="Слово или фраза для поиска по тексту (регистронезависимо)"),
    paper: Optional[str] = typer.Option(None, "--paper", help="Фильтр по имени файла (glob/подстрока)"),
    limit: int = typer.Option(50, "--limit", help="Максимум документов"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Поиск ПО СЛОВУ: список ВСЕХ документов, где встречается term (не по смыслу).

    Дополняет семантический search — для вопросов «в каких статьях есть слово X».
    Модель не нужна, поэтому быстро. Учтите язык: например, «phycocyanin» и «фикоцианин»
    ищутся отдельно (для смысла — используйте семантический search).
    """
    cfg = load_config()
    with Store(cfg) as store:
        res = store.keyword_search(term, limit=limit, paper=paper)
    total, docs = res["total"], res["documents"]
    if as_json:
        _jprint({"term": term, "total": total, "documents": docs})
    elif total == 0:
        typer.echo(f"Слово «{term}» не встречается в корпусе.")
    else:
        shown = f" (показаны первые {len(docs)})" if total > len(docs) else ""
        typer.echo(f"«{term}» встречается в {total} документах{shown}:")
        for r in docs:
            typer.echo(f"  [{r['cite_key']:<16}] {r['hits']:>3} фрагм.  стр. {r['page']}  {r['filename']}")
            if r["snippet"]:
                typer.echo(f"        …{r['snippet']}")


@app.command()
def read(
    ref: str = typer.Argument(..., help="doc_id, имя файла, его часть или cite_key"),
    pages: Optional[str] = typer.Option(None, "--pages", help="Диапазон страниц, напр. 3-5"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Полный текст статьи (или диапазона страниц) — перечитывает PDF."""
    from .query import read_document

    cfg = load_config()
    with Store(cfg) as store:
        doc, result = read_document(store, ref, pages)
        if doc is None:
            if result:
                typer.echo("Неоднозначная ссылка, кандидаты:", err=True)
                for r in result:
                    typer.echo(f"  id={r['id']}  [{r['cite_key']}]  {r['filename']}", err=True)
            else:
                typer.echo(f"Документ не найден: {ref}", err=True)
            raise typer.Exit(1)

        if as_json:
            _jprint({"doc_id": doc["id"], "cite_key": doc["cite_key"],
                     "filename": doc["filename"], "title": doc["title"],
                     "pages": [{"page": n, "text": t} for n, t in result]})
        else:
            typer.echo(f"===== [{doc['cite_key']}] {doc['filename']} =====")
            for n, t in result:
                typer.echo(f"----- стр. {n} -----")
                typer.echo(t)


@app.command()
def bib(
    out: Optional[str] = typer.Option(None, "--out", help="Записать в файл (например, references.md)"),
    keys: Optional[str] = typer.Option(None, "--keys", help="Только эти cite_key, через запятую"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Экспорт списка литературы (метаданные best-effort, правьте вручную)."""
    from .query import bib_entries, format_bib_markdown

    cfg = load_config()
    with Store(cfg) as store:
        entries = bib_entries(store, keys.split(",") if keys else None)
    if as_json:
        _jprint(entries)
    else:
        md = format_bib_markdown(entries)
        if out:
            Path(out).write_text(md, encoding="utf-8")
            typer.echo(f"Записано {len(entries)} записей в {out}")
        else:
            typer.echo(md)


@app.command("list")
def list_docs(as_json: bool = typer.Option(False, "--json")):
    """Список документов корпуса."""
    cfg = load_config()
    with Store(cfg) as store:
        docs = store.all_docs()
    if as_json:
        _jprint([dict(d) for d in docs])
    elif not docs:
        typer.echo("Корпус пуст — выполните ingest.")
    else:
        for d in docs:
            title = (d["title"] or "")[:70]
            typer.echo(f"id={d['id']:<4} [{d['cite_key']:<16}] {d['year'] or '????'}  "
                       f"{d['status']:<11} {d['num_pages']:>4} стр.  {d['filename']}")
            typer.echo(f"      {title}")


@app.command()
def stats(as_json: bool = typer.Option(False, "--json")):
    """Статистика корпуса: количество, годы, авторы, статусы."""
    from .query import corpus_stats

    cfg = load_config()
    with Store(cfg) as store:
        s = corpus_stats(store)
    if as_json:
        _jprint(s)
    else:
        typer.echo(f"Документов: {s['documents']}   Чанков: {s['chunks']}   "
                   f"Векторов: {s['vectors']}")
        typer.echo("По годам:   " + ", ".join(f"{y}: {n}" for y, n in s["by_year"].items()))
        typer.echo("По статусу: " + ", ".join(f"{k}: {n}" for k, n in s["by_status"].items()))
        if s["top_first_authors"]:
            typer.echo("Топ первых авторов: "
                       + ", ".join(f"{a} ({n})" for a, n in s["top_first_authors"]))


@app.command()
def check(
    deep: bool = typer.Option(False, "--deep", help="Дополнительно загрузить модель эмбеддингов"),
    errors: bool = typer.Option(False, "--errors", help="Показать последние ошибки из errors.log"),
    tail: int = typer.Option(40, "--tail", help="Сколько строк errors.log показать при --errors"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Диагностика: окружение, конфиг, хранилище, целостность корпуса.

    Проверяет зависимости, torch/CUDA, Tesseract, согласованность SQLite ↔ ChromaDB
    и показывает сводку по журналу ошибок (errors.log / diagnostics.jsonl).
    """
    from .checks import FAIL, OK, run_checks
    from .diagnostics import ERROR, WARN, read_diagnostics

    cfg = load_config()

    if errors:
        if cfg.errors_log_path.exists():
            lines = cfg.errors_log_path.read_text(encoding="utf-8").splitlines()
            typer.echo(f"===== {cfg.errors_log_path} (последние {tail}) =====")
            for line in lines[-tail:]:
                typer.echo(line)
        else:
            typer.echo(f"Журнал ошибок пуст: {cfg.errors_log_path}")
        return

    results = run_checks(cfg, deep=deep)
    recs = read_diagnostics(cfg)
    n_err = sum(1 for r in recs if r["level"] == ERROR)
    n_warn = sum(1 for r in recs if r["level"] == WARN)

    if as_json:
        _jprint({"checks": [r.to_dict() for r in results],
                 "diagnostics": {"errors": n_err, "warnings": n_warn,
                                 "errors_log": str(cfg.errors_log_path),
                                 "diagnostics_log": str(cfg.diagnostics_path)}})
        return

    mark = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]"}
    for r in results:
        typer.echo(f"{mark.get(r.status, '[????]')} {r.stage:<22} {r.detail}")

    n_fail = sum(1 for r in results if r.status == FAIL)
    n_wchk = sum(1 for r in results if r.status == WARN)
    typer.echo("")
    typer.echo(f"Проверки: сбоев {n_fail}, предупреждений {n_wchk}.")
    typer.echo(f"Журнал ingest: ошибок {n_err}, предупреждений {n_warn}.")
    if n_err or n_warn:
        typer.echo(f"  читать: {cfg.errors_log_path}")
        typer.echo("  или:    python -m paperbase check --errors")
    if not deep:
        typer.echo("Подсказка: `check --deep` дополнительно проверит загрузку модели.")


@app.command()
def backup(
    show: bool = typer.Option(False, "--list", help="Показать имеющиеся копии, ничего не создавая"),
    db_only: bool = typer.Option(False, "--db-only",
                                 help="Копировать только SQLite (без векторов; восстановить через reindex)"),
):
    """Создать резервную копию базы (SQLite + ChromaDB + config) в data/backups/.

    --db-only делает компактную копию без векторов (для большого корпуса); после
    восстановления такой копии выполните reindex, чтобы пересобрать поиск.
    """
    from .backup import create_backup, list_backups

    cfg = load_config()
    if show:
        backups = list_backups(cfg)
        if not backups:
            typer.echo(f"Копий пока нет ({cfg.backup_dir}).")
        else:
            typer.echo(f"Копии в {cfg.backup_dir}:")
            for b in backups:
                size = b.stat().st_size / (1024 * 1024)
                kind = " (db-only)" if "-dbonly" in b.name else ""
                typer.echo(f"  {b.name}   {size:.1f} МБ{kind}")
        return

    path = create_backup(cfg, reason="manual", db_only=db_only)
    size = path.stat().st_size / (1024 * 1024)
    typer.echo(f"Создан бэкап{' (db-only)' if db_only else ''}: {path}  ({size:.1f} МБ)")
    if db_only:
        typer.echo("Векторы не включены — после restore этой копии выполните: python -m paperbase reindex")
    typer.echo(f"Хранятся последние {cfg.backup_keep} копий (ротация).")


@app.command()
def restore(
    which: Optional[str] = typer.Argument(None, help="Имя копии или её часть; пусто — последняя"),
    yes: bool = typer.Option(False, "--yes", help="Подтвердить перезапись текущей базы"),
):
    """Восстановить базу из резервной копии (текущее состояние уйдёт в страховочный бэкап)."""
    from .backup import resolve_backup, restore_backup

    cfg = load_config()
    target = resolve_backup(cfg, which)
    if target is None:
        typer.echo(f"Копия не найдена (which={which!r}). Список: python -m paperbase backup --list",
                   err=True)
        raise typer.Exit(1)

    if not yes:
        typer.echo(f"Будет восстановлено из: {target.name}")
        typer.echo("Текущие corpus.db и chroma/ будут заменены (сначала сделаю страховочный бэкап).")
        typer.echo("Повторите с флагом --yes для выполнения.")
        return

    safety, chroma_restored = restore_backup(cfg, target)
    typer.echo(f"Восстановлено из: {target.name}")
    typer.echo(f"Страховочная копия прежнего состояния: {safety.name}")
    if not chroma_restored:
        typer.echo("Это db-only копия (без векторов). Пересоберите поиск: "
                   "python -m paperbase reindex")


@app.command()
def serve(
    port: int = typer.Option(8765, "--port", help="Порт (по умолчанию 8765)"),
    host: str = typer.Option("127.0.0.1", "--host", help="Адрес (по умолчанию только localhost)"),
    open_browser: bool = typer.Option(False, "--open", help="Открыть браузер автоматически"),
):
    """Локальный веб-интерфейс: поиск, просмотр источников, экспорт. Без LLM.

    Модель грузится один раз при старте и остаётся тёплой — поиск быстрый.
    Для GPU запускайте через pb-gpu.ps1 serve --open. Остановка — Ctrl+C.
    """
    from .server import run_server

    cfg = load_config()
    run_server(cfg, host=host, port=port, open_browser=open_browser)


@app.command()
def menu():
    """Интерактивное меню тем: выбрать папку-корпус и запустить (точка входа для ярлыка).

    Показывает сохранённые темы из corpora.json, позволяет добавить новую (папка +
    название) и запускает её веб-интерфейс. Одна установка обслуживает все темы.
    """
    from .menu import run_menu

    run_menu()


if __name__ == "__main__":
    app()
