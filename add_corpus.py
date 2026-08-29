#!/usr/bin/env python3
"""Добавить тему в реестр corpora.json без интерактива.

`paperbase menu` умеет добавлять темы, но только диалогом (input()). Этот скрипт
делает то же самое из командной строки — удобно для скриптов и автоматизации.
Правила именования и выбора порта те же, что в menu._add.

    python add_corpus.py "Название" "C:/путь/к/Литературе"
    python add_corpus.py "Название" "C:/путь" --port 8790 --data data-my
    python add_corpus.py --list
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
REGISTRY = PROJECT_ROOT / "corpora.json"


def load() -> list[dict]:
    if not REGISTRY.exists():
        return []
    try:
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError as e:
        print(f"ОШИБКА: corpora.json не разбирается ({e}).")
        sys.exit(1)


def save(items: list[dict]) -> None:
    REGISTRY.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")


def slugify(name: str) -> str:
    """Как в menu._add: буквы и цифры оставляем (в т.ч. кириллицу), прочее — в '_'."""
    return "".join(c if c.isalnum() else "_" for c in name).strip("_") or "corpus"


def main() -> int:
    ap = argparse.ArgumentParser(description="Добавить тему в corpora.json")
    ap.add_argument("name", nargs="?", help="Название темы (видно в меню)")
    ap.add_argument("folder", nargs="?", help="Папка с документами")
    ap.add_argument("--data", default=None, help="Папка индекса (по умолчанию data-<slug>)")
    ap.add_argument("--port", type=int, default=None, help="Порт (по умолчанию max+1)")
    ap.add_argument("--list", action="store_true", help="Показать реестр и выйти")
    args = ap.parse_args()

    items = load()

    if args.list or not args.name:
        if not items:
            print("Реестр пуст.")
            return 0
        print(f"Тем в реестре: {len(items)}\n")
        for i, it in enumerate(items, 1):
            data_dir = Path(it["data"])
            if not data_dir.is_absolute():
                data_dir = PROJECT_ROOT / data_dir
            built = "построена" if (data_dir / "corpus.db").exists() else "НЕ построена"
            exists = "есть" if Path(it["corpus"]).exists() else "ПАПКИ НЕТ"
            print(f"  {i}. {it['name']}")
            print(f"     папка: {it['corpus']}  [{exists}]")
            print(f"     индекс: {it['data']} [{built}] | порт: {it['port']}")
        return 0

    if not args.folder:
        print("Нужен путь к папке. См. --help")
        return 1

    folder = Path(args.folder.strip().strip('"'))
    if not folder.exists():
        print(f"Папка не найдена: {folder}")
        return 1

    corpus = str(folder).replace("\\", "/")
    for it in items:
        if it["corpus"].rstrip("/").lower() == corpus.rstrip("/").lower():
            print(f"Такая папка уже есть в реестре под именем «{it['name']}» "
                  f"(индекс {it['data']}, порт {it['port']}). Ничего не менял.")
            return 0

    ports = [int(i.get("port", 8765)) for i in items] or [8764]
    item = {
        "name": args.name,
        "corpus": corpus,
        "data": args.data or f"data-{slugify(args.name)}",
        "port": args.port or max(ports) + 1,
    }
    save(items + [item])

    files = [p for p in folder.rglob("*") if p.is_file()]
    print(f"Добавлено: «{item['name']}»")
    print(f"  папка:  {item['corpus']}  (файлов: {len(files)})")
    print(f"  индекс: {item['data']}")
    print(f"  порт:   {item['port']}")
    print("\nПостроить базу:")
    print(f'  $env:PAPERBASE_CORPUS_DIR="{item["corpus"]}"; '
          f'$env:PAPERBASE_DATA_DIR="{item["data"]}"; .\\pb-gpu.ps1 ingest')
    return 0


if __name__ == "__main__":
    sys.exit(main())
