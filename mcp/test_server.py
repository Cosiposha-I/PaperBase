#!/usr/bin/env python3
"""Сквозная проверка MCP-сервера: говорим с ним по JSON-RPC, как Claude Desktop.

Запуск:  python mcp/test_server.py
Ничего не мокает — поднимает настоящий сервер, ходит в настоящий корпус.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "mcp" / "server.py"

ok_count = 0
fail_count = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global ok_count, fail_count
    if condition:
        ok_count += 1
        print(f"  OK   {label}")
    else:
        fail_count += 1
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))


class Client:
    """Минимальный MCP-клиент поверх stdio."""

    def __init__(self, proc: subprocess.Popen):
        self.proc = proc
        self._id = 0

    def send(self, method: str, params: dict | None = None, notify: bool = False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self._id += 1
            msg["id"] = self._id
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        if notify:
            return None
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("сервер закрыл поток без ответа")
        return json.loads(line)

    def call(self, name: str, args: dict) -> str:
        resp = self.send("tools/call", {"name": name, "arguments": args})
        result = resp.get("result", {})
        content = result.get("content") or []
        text = content[0]["text"] if content else ""
        if result.get("isError"):
            return f"[ОШИБКА] {text}"
        return text


def main() -> int:
    print("=" * 74)
    print("Проверка MCP-сервера PaperBase")
    print("=" * 74)

    proc = subprocess.Popen(
        [sys.executable, str(SERVER)],
        cwd=str(ROOT),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", bufsize=1,
    )
    c = Client(proc)

    try:
        # --- рукопожатие ---
        print("\n1. Рукопожатие")
        r = c.send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        })
        check("initialize отвечает", "result" in r, json.dumps(r, ensure_ascii=False))
        check("версия протокола эхом", r["result"].get("protocolVersion") == "2024-11-05")
        check("объявлены возможности tools", "tools" in r["result"].get("capabilities", {}))
        c.send("notifications/initialized", {}, notify=True)

        # --- список инструментов ---
        print("\n2. Список инструментов")
        r = c.send("tools/list")
        tools = r["result"]["tools"]
        names = [t["name"] for t in tools]
        check(f"инструментов: {len(tools)}", len(tools) == 6, str(names))
        for expected in ("paperbase_search", "paperbase_keyword", "paperbase_read",
                         "paperbase_list", "paperbase_bibliography", "paperbase_stats"):
            check(f"есть {expected}", expected in names)
        check("у search описано, когда вызывать",
              "полнот" in next(t for t in tools if t["name"] == "paperbase_search")["description"])

        # --- реальные вызовы ---
        print("\n3. Вызовы инструментов (первый может ждать загрузку модели)")
        t0 = time.monotonic()
        out = c.call("paperbase_stats", {})
        dt = time.monotonic() - t0
        print(f"     (stats занял {dt:.1f} с — включая подъём serve, если он не был запущен)")
        check("stats вернул данные", len(out) > 10 and "ОШИБКА" not in out, out[:200])

        out = c.call("paperbase_list", {})
        check("list вернул документы", "Документов в корпусе" in out, out[:200])
        print(f"     {out.splitlines()[0] if out else ''}")

        t0 = time.monotonic()
        out = c.call("paperbase_search", {"query": "катализатор переэтерификации", "k": 3})
        dt = (time.monotonic() - t0) * 1000
        check("search вернул фрагменты", "Найдено фрагментов" in out, out[:200])
        check("в выдаче есть ссылка со страницей", "стр." in out, out[:200])
        print(f"     поиск: {dt:.0f} мс")
        print("     --- начало выдачи ---")
        for ln in out.splitlines()[:6]:
            print(f"     {ln}")

        t0 = time.monotonic()
        out = c.call("paperbase_keyword", {"term": "biodiesel", "limit": 50})
        dt = (time.monotonic() - t0) * 1000
        check("keyword вернул документы", "встречается в документах" in out or
              "не встречается" in out, out[:200])
        print(f"     keyword: {dt:.0f} мс — {out.splitlines()[0] if out else ''}")

        # --- устойчивость ---
        print("\n4. Устойчивость")
        r = c.send("tools/call", {"name": "нет_такого", "arguments": {}})
        check("неизвестный инструмент → isError, не падение",
              r.get("result", {}).get("isError") is True, json.dumps(r, ensure_ascii=False)[:200])
        r = c.send("ping")
        check("ping отвечает", "result" in r)
        r = c.send("совсем/неизвестный/метод")
        check("неизвестный метод → JSON-RPC error", "error" in r)
        r = c.send("tools/list")
        check("сервер жив после ошибок", "result" in r)

    finally:
        try:
            proc.stdin.close()
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

    print("\n" + "=" * 74)
    print(f"Успешно: {ok_count}   Провалено: {fail_count}")
    print("=" * 74)
    return 1 if fail_count else 0


if __name__ == "__main__":
    sys.exit(main())
