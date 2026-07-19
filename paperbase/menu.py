"""Интерактивное меню выбора темы/корпуса — одна точка входа для ярлыка.

Реестр тем — corpora.json в корне проекта: список {name, corpus, data, port}.
Выбор темы задаёт пути через переменные окружения (PAPERBASE_CORPUS_DIR/DATA_DIR),
при необходимости строит базу и запускает веб-интерфейс. Новую тему можно добавить,
указав папку и название — она сохраняется в реестр для будущих сессий.

Одна установка (код + модель bge-m3) обслуживает все темы; у каждой свой data_dir.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .config import PROJECT_ROOT, load_config

REGISTRY = PROJECT_ROOT / "corpora.json"


def _load() -> list[dict]:
    if REGISTRY.exists():
        try:
            data = json.loads(REGISTRY.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []
    return []


def _save(items: list[dict]) -> None:
    REGISTRY.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def _data_path(item: dict) -> Path:
    d = Path(item["data"])
    return d if d.is_absolute() else PROJECT_ROOT / d


def _indexed(item: dict) -> bool:
    return (_data_path(item) / "corpus.db").exists()


def _launch(item: dict) -> None:
    """Задать пути темы, при необходимости построить базу и запустить веб-интерфейс."""
    os.environ["PAPERBASE_CORPUS_DIR"] = item["corpus"]
    os.environ["PAPERBASE_DATA_DIR"] = item["data"]
    cfg = load_config()

    from .server import run_server
    from .store import Store

    if not _indexed(item):
        if not cfg.corpus_dir.exists():
            print(f"  ⚠ Папка не найдена: {cfg.corpus_dir}. Проверьте путь темы в corpora.json.")
            return
        print(f"\nБаза для «{item['name']}» ещё не построена — индексирую (разово, "
              f"несколько минут)…\n")
        from .ingest import ingest_corpus
        with Store(cfg) as store:
            ingest_corpus(cfg, store, cfg.corpus_dir)

    print(f"\nЗапускаю «{item['name']}» → http://127.0.0.1:{item['port']}  "
          f"(Ctrl+C или закрыть окно — остановить)\n")
    run_server(cfg, port=int(item["port"]), open_browser=True)


def _add(items: list[dict]) -> list[dict]:
    folder = input("Путь к папке со статьями: ").strip().strip('"')
    if not folder or not Path(folder).exists():
        print("  Папка не найдена — отмена.")
        return items
    name = input("Название темы (для меню): ").strip() or Path(folder).name
    slug = "".join(c if c.isalnum() else "_" for c in name).strip("_") or "corpus"
    ports = [int(i.get("port", 8765)) for i in items] or [8764]
    item = {"name": name, "corpus": folder.replace("\\", "/"),
            "data": f"data-{slug}", "port": max(ports) + 1}
    items = items + [item]
    _save(items)
    print(f"  Добавлено: «{name}» (порт {item['port']}, индекс {item['data']}).")
    if input("Построить базу и запустить сейчас? [Y/n]: ").strip().lower() != "n":
        _launch(item)
    return items


def run_menu() -> None:
    items = _load()
    while True:
        items = _load()  # перечитываем на случай изменений
        print("\n=== PaperBase — выбор темы ===")
        for i, it in enumerate(items, 1):
            mark = "" if _indexed(it) else "   (база ещё не построена)"
            print(f"  {i}. {it['name']}{mark}")
        print("  N. Добавить новую тему (папку)")
        print("  0. Выход")
        try:
            choice = input("Выбор: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if choice == "0":
            break
        if choice.lower() in ("n", "н"):
            _add(items)
            continue
        if choice.isdigit() and 1 <= int(choice) <= len(items):
            _launch(items[int(choice) - 1])
            continue
        print("  Не понял выбор, попробуйте снова.")
