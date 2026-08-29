#!/usr/bin/env python3
"""Прописать MCP-сервер PaperBase в конфиг Claude Desktop.

Правит ТОЛЬКО ключ mcpServers.paperbase; остальные записи и настройки не трогает.
Перед изменением делает копию файла с меткой времени.

Запуск:
    python mcp/register.py                      # тема из config.toml, порт 8765
    python mcp/register.py --name Физхимия --port 8766 --config path\\to\\config.toml
    python mcp/register.py --remove             # убрать запись
    python mcp/register.py --dry-run            # показать, что будет сделано

Значения существующих записей (там лежат чужие токены) не печатаются — только имена.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENTRY_KEY = "paperbase"


def config_path() -> Path:
    """Расположение конфига Claude Desktop по платформам."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def python_for_mcp() -> str:
    """Интерпретатор для запуска сервера. Сам сервер зависимостей не требует,
    но venv проекта гарантированно есть и умеет поднимать `paperbase serve`."""
    for c in (PROJECT_ROOT / ".venv" / "Scripts" / "python.exe",
              PROJECT_ROOT / ".venv" / "bin" / "python"):
        if c.exists():
            return str(c)
    return sys.executable


def build_entry(name: str, port: int, cfg_toml: str | None) -> dict:
    env = {"PAPERBASE_MCP_PORT": str(port)}
    if name:
        env["PAPERBASE_CORPUS_NAME"] = name
    if cfg_toml:
        env["PAPERBASE_CONFIG"] = cfg_toml
    return {
        "command": python_for_mcp(),
        "args": [str(PROJECT_ROOT / "mcp" / "server.py")],
        "env": env,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Регистрация PaperBase в Claude Desktop")
    ap.add_argument("--name", default="Биодизель", help="имя темы (видно в описаниях инструментов)")
    ap.add_argument("--port", type=int, default=8765, help="порт paperbase serve")
    ap.add_argument("--config", default=None, help="путь к config.toml темы (мультикорпус)")
    ap.add_argument("--key", default=ENTRY_KEY, help="имя записи в mcpServers")
    ap.add_argument("--remove", action="store_true", help="удалить запись")
    ap.add_argument("--dry-run", action="store_true", help="только показать план")
    args = ap.parse_args()

    path = config_path()
    print(f"Конфиг: {path}")

    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"ОШИБКА: конфиг не разбирается как JSON ({e}). Ничего не менял.")
            return 1
    else:
        print("Файла нет — будет создан.")
        data = {}

    servers = data.setdefault("mcpServers", {})
    before_top = list(data.keys())
    before_srv = list(servers.keys())
    print(f"Сейчас записей в mcpServers: {len(before_srv)} — {', '.join(before_srv) or 'пусто'}")

    if args.remove:
        if args.key not in servers:
            print(f"Записи «{args.key}» нет, удалять нечего.")
            return 0
        action = f"УДАЛИТЬ запись «{args.key}»"
        servers.pop(args.key)
    else:
        entry = build_entry(args.name, args.port, args.config)
        action = ("ОБНОВИТЬ" if args.key in servers else "ДОБАВИТЬ") + f" запись «{args.key}»"
        print("\nБудет записано:")
        print(json.dumps({args.key: entry}, ensure_ascii=False, indent=2))
        servers[args.key] = entry

    if args.dry_run:
        print(f"\n[dry-run] Действие не выполнено: {action}")
        return 0

    # --- контроль целостности до записи ---
    lost_top = set(before_top) - set(data.keys())
    lost_srv = (set(before_srv) - set(servers.keys())) - ({args.key} if args.remove else set())
    if lost_top or lost_srv:
        print(f"ОШИБКА: потерялись ключи {lost_top or ''} {lost_srv or ''}. Ничего не пишу.")
        return 1

    if path.exists():
        backup = path.with_name(f"{path.stem}.backup-{time.strftime('%Y%m%d-%H%M%S')}{path.suffix}")
        shutil.copy2(path, backup)
        print(f"\nКопия: {backup.name}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # --- проверка после записи ---
    check = json.loads(path.read_text(encoding="utf-8"))
    srv = check.get("mcpServers", {})
    ok = (set(check.keys()) == set(before_top) | {"mcpServers"}
          and all(k in srv for k in before_srv if k != args.key or not args.remove))
    print(f"{action} — {'готово' if ok else 'ЗАПИСАНО, НО ПРОВЕРКА НЕ СОШЛАСЬ'}")
    print(f"Записей в mcpServers теперь: {len(srv)} — {', '.join(srv.keys())}")
    print("\nЧтобы изменения вступили в силу, перезапусти Claude Desktop.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
