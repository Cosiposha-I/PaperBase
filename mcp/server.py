#!/usr/bin/env python3
"""MCP-сервер над PaperBase — даёт Claude искать по корпусу прямо в разговоре.

ПОЧЕМУ ТОНКИЙ КЛИЕНТ, А НЕ САМОСТОЯТЕЛЬНЫЙ ПОИСК
Сервер не грузит bge-m3 и вообще не импортирует paperbase. Он ходит по HTTP в уже
работающий `paperbase serve`. Причины:
  • модель остаётся в памяти в одном экземпляре (иначе при открытом Electron-приложении
    она оказалась бы в видеопамяти дважды);
  • старт мгновенный — Claude Desktop не ждёт загрузку модели при каждом запуске;
  • ноль зависимостей: только stdlib, поэтому `.venv` с CUDA-torch не трогается вовсе
    (см. предупреждение в CLAUDE.md про pip install --upgrade).

Если `serve` не запущен — поднимаем его сами при первом обращении.

ПРОТОКОЛ
MCP поверх stdio: построчный JSON-RPC 2.0 в UTF-8. В stdout идут ТОЛЬКО сообщения
протокола; всё остальное — в stderr, иначе Claude Desktop не сможет разобрать поток.

НАСТРОЙКА (переменные окружения, все необязательные)
  PAPERBASE_MCP_PORT     порт serve, по умолчанию 8765
  PAPERBASE_MCP_AUTOSTART  "0" — не поднимать serve самому
  PAPERBASE_CONFIG       путь к config.toml нужной темы (мультикорпус)
  PAPERBASE_CORPUS_NAME  человекочитаемое имя темы — попадает в описания инструментов
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Корень проекта: mcp/server.py -> paperbase/
PROJECT_ROOT = Path(__file__).resolve().parent.parent

PORT = int(os.environ.get("PAPERBASE_MCP_PORT", "8765"))
BASE = f"http://127.0.0.1:{PORT}"
AUTOSTART = os.environ.get("PAPERBASE_MCP_AUTOSTART", "1") != "0"
CORPUS_NAME = os.environ.get("PAPERBASE_CORPUS_NAME", "").strip()

SERVER_NAME = "paperbase"
SERVER_VERSION = "0.1.0"


def log(msg: str) -> None:
    """Диагностика — строго в stderr: stdout занят протоколом."""
    print(f"[paperbase-mcp] {msg}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# Клиент к paperbase serve
# --------------------------------------------------------------------------

class ServeUnavailable(RuntimeError):
    """serve не отвечает и поднять его не удалось."""


def _python_for_serve() -> str:
    """Интерпретатор с установленным paperbase: сперва venv проекта, иначе текущий."""
    candidates = [
        PROJECT_ROOT / ".venv" / "Scripts" / "python.exe",  # Windows
        PROJECT_ROOT / ".venv" / "bin" / "python",          # macOS/Linux
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return sys.executable


def _get(path: str, params: dict | None = None, timeout: float = 60.0):
    """GET к serve. Возвращает разобранный JSON или текст (для /api/bib)."""
    url = f"{BASE}{path}"
    if params:
        clean = {k: v for k, v in params.items() if v not in (None, "")}
        url += "?" + urllib.parse.urlencode(clean, encoding="utf-8")
    with urllib.request.urlopen(url, timeout=timeout) as r:
        raw = r.read().decode("utf-8")
        ctype = r.headers.get("Content-Type", "")
    return json.loads(raw) if "json" in ctype else raw


def _serve_alive(timeout: float = 2.0) -> bool:
    try:
        _get("/api/stats", timeout=timeout)
        return True
    except Exception:
        return False


_serve_proc: subprocess.Popen | None = None
_serve_lock = threading.Lock()  # чтобы прогрев и первый вызов не подняли serve дважды


def ensure_serve() -> None:
    """Убедиться, что serve работает; при необходимости поднять и дождаться."""
    with _serve_lock:
        _ensure_serve_locked()


def _ensure_serve_locked() -> None:
    global _serve_proc

    if _serve_alive():
        return
    if not AUTOSTART:
        raise ServeUnavailable(
            f"paperbase serve не отвечает на {BASE}, автозапуск отключён. "
            f"Запусти вручную: .\\pb-gpu.ps1 serve --port {PORT}"
        )

    log(f"serve не отвечает — запускаю на порту {PORT}")
    env = os.environ.copy()
    cmd = [_python_for_serve(), "-m", "paperbase"]
    if os.environ.get("PAPERBASE_CONFIG"):
        cmd += ["--config", os.environ["PAPERBASE_CONFIG"]]
    cmd += ["serve", "--port", str(PORT)]

    try:
        _serve_proc = subprocess.Popen(
            cmd, cwd=str(PROJECT_ROOT), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except OSError as e:
        raise ServeUnavailable(f"не удалось запустить serve: {e}") from e

    # Первый старт грузит bge-m3 — это может занять до полминуты.
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if _serve_alive(timeout=3.0):
            log("serve поднялся")
            return
        if _serve_proc.poll() is not None:
            raise ServeUnavailable(
                f"процесс serve завершился с кодом {_serve_proc.returncode}. "
                f"Проверь: python -m paperbase check"
            )
        time.sleep(1.0)
    raise ServeUnavailable("serve не ответил за 180 с")


def _prewarm() -> None:
    """Фоновый прогрев: поднять serve и сделать холостой поиск.

    Первый поиск после старта serve стоит ~1,3 с (прогрев путей инференса),
    последующие — ~50 мс. Тратим эту секунду до того, как она понадобится.
    Любая ошибка здесь не фатальна: настоящий вызов инструмента попробует снова
    и сообщит о проблеме внятно.
    """
    try:
        t0 = time.monotonic()
        ensure_serve()
        _get("/api/search", {"q": "прогрев", "k": 1}, timeout=120)
        log(f"прогрев завершён за {time.monotonic() - t0:.1f} с — поиск готов")
    except Exception as e:
        log(f"прогрев не удался ({e}); попробую при первом вызове")


# --------------------------------------------------------------------------
# Форматирование выдачи для модели
#
# Возвращаем не сырой JSON, а компактный текст: его я читаю быстрее и не трачу
# контекст на служебные поля. Ссылка (Автор, Год) + страница — в каждом фрагменте,
# потому что без неё фрагмент бесполезен по правилам синтеза из CLAUDE.md.
# --------------------------------------------------------------------------

def _fmt_pages(pages) -> str:
    if not pages:
        return "стр. ?"
    a = pages[0]
    b = pages[-1] if len(pages) > 1 else a
    return f"стр. {a}" if a == b else f"стр. {a}–{b}"


def fmt_search(data: dict) -> str:
    hits = data.get("hits") or []
    q = data.get("query", "")
    if not hits:
        return (f"По запросу «{q}» в корпусе ничего не найдено.\n"
                f"Если вопрос на полноту — попробуй keyword. Иначе считай, "
                f"что корпус этого не покрывает.")
    out = [f"Найдено фрагментов: {len(hits)} — запрос «{q}»", ""]
    for h in hits:
        out.append(
            f"[{h['rank']}] ({h['citation']}) {_fmt_pages(h.get('pages'))} "
            f"— близость {h['score'] * 100:.0f}%"
        )
        meta = [f"файл: {h.get('filename', '')}"]
        if h.get("section"):
            meta.append(f"раздел: {h['section']}")
        if h.get("title"):
            meta.append(f"статья: {h['title'][:90]}")
        out.append("   " + " | ".join(meta))
        out.append("   " + (h.get("text") or "").strip().replace("\n", "\n   "))
        out.append("")
    return "\n".join(out)


def fmt_keyword(data: dict) -> str:
    docs = data.get("documents") or []
    term = data.get("term", "")
    total = data.get("total", 0)
    if not docs:
        return (f"Слово «{term}» не встречается ни в одном документе корпуса.\n"
                f"Проверь язык: в англоязычных статьях термин пишется иначе "
                f"(phycocyanin ≠ фикоцианин).")
    out = [f"Слово «{term}» встречается в документах: {len(docs)} "
           f"(всего вхождений: {total})", ""]
    for d in docs:
        cite = d.get("cite_key") or d.get("filename", "")
        cnt = d.get("count", d.get("hits", ""))
        line = f"• {cite}"
        if d.get("year"):
            line += f" ({d['year']})"
        if cnt != "":
            line += f" — вхождений: {cnt}"
        out.append(line)
        if d.get("filename"):
            out.append(f"    файл: {d['filename']}")
    return "\n".join(out)


def fmt_read(data: dict) -> str:
    if not data.get("found"):
        cands = data.get("candidates") or []
        if not cands:
            return "Документ не найден."
        lines = ["Документ не определён однозначно. Кандидаты:"]
        lines += [f"• id={c['id']} {c['cite_key']} — {c['filename']}" for c in cands]
        return "\n".join(lines)
    out = [f"{data.get('cite_key', '')} — {data.get('title') or data.get('filename')}",
           f"файл: {data.get('filename', '')}", ""]
    for p in data.get("pages", []):
        out.append(f"--- стр. {p['page']} ---")
        out.append(p["text"].strip())
        out.append("")
    return "\n".join(out)


def fmt_list(docs: list) -> str:
    if not docs:
        return "Корпус пуст."
    out = [f"Документов в корпусе: {len(docs)}", ""]
    for d in docs:
        year = d.get("year") or "год не найден"
        out.append(f"• id={d['id']} [{d.get('cite_key', '')}] ({year}) "
                   f"— {(d.get('title') or d.get('filename'))[:100]}")
        out.append(f"    файл: {d.get('filename', '')} | "
                   f"страниц: {d.get('num_pages', '?')} | {d.get('status', '')}")
    return "\n".join(out)


def fmt_stats(data: dict) -> str:
    return "\n".join(f"{k}: {v}" for k, v in data.items())


# --------------------------------------------------------------------------
# Инструменты
#
# Описания намеренно предписывающие («вызывай, когда…»): правила из CLAUDE.md
# переносятся сюда, чтобы соблюдались автоматически, а не по памяти.
# --------------------------------------------------------------------------

_corpus_hint = f" Корпус: «{CORPUS_NAME}»." if CORPUS_NAME else ""

TOOLS = [
    {
        "name": "paperbase_search",
        "description": (
            "Смысловой поиск по корпусу научных статей (top-K фрагментов)." + _corpus_hint +
            " Вызывай, когда нужен содержательный ответ по теме: что известно, какие "
            "значения, как влияет, какие методы. Запрос можно писать по-русски — найдутся "
            "и англоязычные статьи (кросс-язычные эмбеддинги). Возвращает фрагменты с "
            "автором, годом и номером страницы — используй их как ссылки. "
            "ВАЖНО: это top-K, он НЕ перечисляет все документы. Для вопросов на полноту "
            "(«во всех ли статьях», «перечисли все, где встречается X») используй "
            "paperbase_keyword. Для обзора бери k=12-40 и делай несколько запросов "
            "с разными формулировками."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Смысловой запрос."},
                "k": {"type": "integer", "description": "Сколько фрагментов вернуть. "
                      "10 для точечного вопроса, 12-40 для обзора.", "default": 10},
                "paper": {"type": "string", "description": "Необязательный фильтр: "
                          "имя файла, чтобы искать внутри одной статьи."},
                "year_min": {"type": "integer", "description": "Нижняя граница года."},
                "year_max": {"type": "integer", "description": "Верхняя граница года."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "paperbase_keyword",
        "description": (
            "Точный поиск по слову: список ВСЕХ документов корпуса, где слово встречается."
            + _corpus_hint +
            " Вызывай на вопросы полноты: «во всех ли статьях есть X», «перечисли все "
            "работы, где упоминается Y», «сколько статей про Z». Модель не нужна, "
            "работает мгновенно. "
            "ВАЖНО про язык: ищется буквальное совпадение, поэтому phycocyanin и "
            "фикоцианин — разные запросы. Если корпус англоязычный, ищи английский термин."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "term": {"type": "string", "description": "Слово или часть слова."},
                "paper": {"type": "string", "description": "Необязательный фильтр по файлу."},
                "limit": {"type": "integer", "description": "Максимум документов.",
                          "default": 200},
            },
            "required": ["term"],
        },
    },
    {
        "name": "paperbase_read",
        "description": (
            "Полный текст указанных страниц документа." + _corpus_hint +
            " Вызывай, когда фрагмента из поиска мало и нужен контекст вокруг: проверить "
            "условия эксперимента, дочитать таблицу, убедиться в трактовке перед тем, "
            "как утверждать. Документ задаётся id, cite_key или именем файла."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "id, cite_key или имя файла."},
                "pages": {"type": "string", "description": "Диапазон, например 5-8 "
                          "или одна страница 12. Без указания — весь документ."},
            },
            "required": ["ref"],
        },
    },
    {
        "name": "paperbase_list",
        "description": ("Перечень всех статей корпуса: id, cite_key, год, заголовок, файл."
                        + _corpus_hint +
                        " Вызывай, чтобы понять состав корпуса или найти нужный файл "
                        "для фильтра paper."),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "paperbase_bibliography",
        "description": ("Оформленный список литературы по корпусу." + _corpus_hint +
                        " Вызывай, когда нужен готовый перечень источников в конец "
                        "обзора или работы. Можно ограничить конкретными cite_key."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "keys": {"type": "string", "description": "cite_key через запятую; "
                         "пусто — весь корпус."},
            },
        },
    },
    {
        "name": "paperbase_stats",
        "description": ("Сводка по корпусу: сколько документов, страниц, фрагментов, "
                        "векторов." + _corpus_hint +
                        " Вызывай для проверки, что база на месте и проиндексирована."),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def call_tool(name: str, args: dict) -> str:
    ensure_serve()

    if name == "paperbase_search":
        data = _get("/api/search", {
            "q": args.get("query", ""),
            "k": args.get("k", 10),
            "paper": args.get("paper"),
            "year_min": args.get("year_min"),
            "year_max": args.get("year_max"),
        })
        return fmt_search(data)

    if name == "paperbase_keyword":
        data = _get("/api/keyword", {
            "q": args.get("term", ""),
            "k": args.get("limit", 200),
            "paper": args.get("paper"),
        })
        return fmt_keyword(data)

    if name == "paperbase_read":
        data = _get("/api/read", {"ref": args.get("ref", ""), "pages": args.get("pages")})
        return fmt_read(data)

    if name == "paperbase_list":
        return fmt_list(_get("/api/list"))

    if name == "paperbase_bibliography":
        return _get("/api/bib", {"keys": args.get("keys")})

    if name == "paperbase_stats":
        return fmt_stats(_get("/api/stats"))

    raise ValueError(f"неизвестный инструмент: {name}")


# --------------------------------------------------------------------------
# JSON-RPC поверх stdio
# --------------------------------------------------------------------------

def _result(msg_id, payload) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": payload}


def _error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle(msg: dict) -> dict | None:
    """Обработать одно сообщение. None — ответ не нужен (уведомление)."""
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        # Отвечаем той же версией протокола, что запросил клиент — так совместимее.
        version = params.get("protocolVersion", "2024-11-05")
        return _result(msg_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    if method in ("notifications/initialized", "initialized"):
        return None  # уведомление, ответа не ждут

    if method == "ping":
        return _result(msg_id, {})

    if method == "tools/list":
        return _result(msg_id, {"tools": TOOLS})

    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments") or {}
        try:
            text = call_tool(name, args)
            return _result(msg_id, {"content": [{"type": "text", "text": text}]})
        except ServeUnavailable as e:
            return _result(msg_id, {
                "content": [{"type": "text", "text": f"PaperBase недоступен: {e}"}],
                "isError": True,
            })
        except urllib.error.URLError as e:
            return _result(msg_id, {
                "content": [{"type": "text",
                             "text": f"Не удалось обратиться к PaperBase: {e}"}],
                "isError": True,
            })
        except Exception as e:
            return _result(msg_id, {
                "content": [{"type": "text", "text": f"Ошибка инструмента {name}: {e}"}],
                "isError": True,
            })

    if msg_id is None:
        return None  # неизвестное уведомление — молча игнорируем
    return _error(msg_id, -32601, f"метод не поддерживается: {method}")


def main() -> None:
    # Windows: без этого кириллица в stdout ломает протокол.
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    log(f"старт; serve ожидается на {BASE}"
        + (f"; тема «{CORPUS_NAME}»" if CORPUS_NAME else ""))

    # Прогрев в фоне: подъём serve с загрузкой bge-m3 занимает ~27 с, а первый поиск
    # после старта — ~1,3 с против 50 мс на тёплом. Начинаем сразу, не блокируя
    # рукопожатие: к моменту первого вызова инструмента модель обычно уже в памяти.
    if AUTOSTART:
        threading.Thread(target=_prewarm, daemon=True).start()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            log(f"не разобрал строку: {e}")
            continue

        try:
            response = handle(msg)
        except Exception as e:  # ни одно исключение не должно ронять сервер
            log(f"внутренняя ошибка: {e}")
            response = _error(msg.get("id"), -32603, str(e))

        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()

    log("stdin закрыт — завершаюсь")


if __name__ == "__main__":
    main()
