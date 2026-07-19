"""Локальное веб-приложение — «читальный зал» корпуса.

Только поиск и просмотр источников, без генерации текста (синтез делает Claude).
Сервер на stdlib http.server: ни одной новой зависимости. Модель эмбеддингов
грузится один раз при старте и остаётся тёплой — поэтому поиск быстрый (~0.1 с),
в отличие от CLI, где модель загружается заново на каждый вызов.

Доступ к Store/Embedder/ChromaDB сериализуется одним замком (single-user localhost).
"""

from __future__ import annotations

import io
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import Config
from .embed import load_embedder
from .query import (bib_entries, corpus_stats, format_bib_markdown,
                    read_document, search)
from .store import Store

WEB_DIR = Path(__file__).resolve().parent / "web"


def _qs(params: dict, key: str, default=None):
    """Первое значение query-параметра или default."""
    vals = params.get(key)
    return vals[0] if vals else default


class ServerState:
    """Держит тёплые Store и Embedder; сериализует доступ к ним."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.store = Store(cfg)
        self.embedder = load_embedder(cfg, lambda m: print(m, flush=True))

    # --- операции (все под замком вызывающим кодом) ---

    def do_search(self, params: dict) -> dict:
        q = _qs(params, "q", "").strip()
        if not q:
            return {"query": "", "hits": []}
        k = int(_qs(params, "k", self.cfg.default_k))
        paper = _qs(params, "paper") or None
        ymin = _qs(params, "year_min")
        ymax = _qs(params, "year_max")
        hits = search(self.store, self.embedder, q, k=k, paper=paper,
                      year_min=int(ymin) if ymin else None,
                      year_max=int(ymax) if ymax else None)
        return {"query": q, "model": self.embedder.model_name,
                "hits": [h.to_dict() for h in hits]}

    def do_keyword(self, params: dict) -> dict:
        term = _qs(params, "q", "").strip()
        if not term:
            return {"term": "", "total": 0, "documents": []}
        paper = _qs(params, "paper") or None
        res = self.store.keyword_search(term, limit=int(_qs(params, "k", 200)), paper=paper)
        return {"term": term, **res}

    def do_read(self, params: dict) -> dict:
        ref = _qs(params, "ref", "")
        pages = _qs(params, "pages") or None
        doc, result = read_document(self.store, ref, pages)
        if doc is None:
            return {"found": False,
                    "candidates": [{"id": r["id"], "cite_key": r["cite_key"],
                                    "filename": r["filename"]} for r in result]}
        return {"found": True, "doc_id": doc["id"], "cite_key": doc["cite_key"],
                "filename": doc["filename"], "title": doc["title"],
                "pages": [{"page": n, "text": t} for n, t in result]}

    def do_stats(self) -> dict:
        return corpus_stats(self.store)

    def do_list(self) -> list:
        return [{"id": d["id"], "cite_key": d["cite_key"], "title": d["title"],
                 "authors": d["authors"], "year": d["year"], "journal": d["journal"],
                 "doi": d["doi"], "num_pages": d["num_pages"], "status": d["status"],
                 "filename": d["filename"]} for d in self.store.all_docs()]

    def do_bib(self, params: dict) -> str:
        keys = (params.get("keys") or [None])[0]
        entries = bib_entries(self.store, keys.split(",") if keys else None)
        return format_bib_markdown(entries)

    def do_check(self) -> dict:
        from .checks import run_checks
        results = run_checks(self.cfg, deep=False)
        return {"checks": [r.to_dict() for r in results]}

    def render_page_png(self, params: dict) -> bytes | None:
        """PNG страницы PDF — визуальная привязка к источнику."""
        import fitz

        ref = _qs(params, "doc") or _qs(params, "ref") or ""
        page_no = int(_qs(params, "page", "1"))
        doc, _ = self.store.resolve_doc(ref)
        if doc is None:
            return None
        path = Path(doc["path"])
        if not path.exists() or path.suffix.lower() != ".pdf":
            return None  # у txt/rtf нет страниц для рендера
        with fitz.open(path) as pdf:
            idx = max(0, min(page_no - 1, pdf.page_count - 1))
            pix = pdf[idx].get_pixmap(matrix=fitz.Matrix(140 / 72, 140 / 72))
            buf = io.BytesIO(pix.tobytes("png"))
        return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    state: ServerState = None  # проставляется в run_server

    def log_message(self, *args):  # тише в консоли
        pass

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path
        params = parse_qs(parsed.query)
        try:
            if route in ("/", "/index.html"):
                self._serve_index()
            elif route == "/api/search":
                with self.state.lock:
                    self._json(self.state.do_search(params))
            elif route == "/api/keyword":
                with self.state.lock:
                    self._json(self.state.do_keyword(params))
            elif route == "/api/read":
                with self.state.lock:
                    self._json(self.state.do_read(params))
            elif route == "/api/stats":
                with self.state.lock:
                    self._json(self.state.do_stats())
            elif route == "/api/list":
                with self.state.lock:
                    self._json(self.state.do_list())
            elif route == "/api/check":
                with self.state.lock:
                    self._json(self.state.do_check())
            elif route == "/api/bib":
                with self.state.lock:
                    md = self.state.do_bib(params)
                self._send(200, md.encode("utf-8"), "text/markdown; charset=utf-8",
                           {"Content-Disposition": 'attachment; filename="references.md"'})
            elif route == "/api/page.png":
                with self.state.lock:
                    png = self.state.render_page_png(params)
                if png is None:
                    self._json({"error": "страница не найдена"}, 404)
                else:
                    self._send(200, png, "image/png")
            else:
                self._json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionError):
            pass  # клиент закрыл вкладку/отменил запрос — это нормально
        except Exception as e:  # не роняем сервер из-за одного запроса
            try:
                self._json({"error": str(e)}, 500)
            except OSError:
                pass  # сокет уже закрыт — сообщить об ошибке некуда

    def _serve_index(self):
        index = WEB_DIR / "index.html"
        if not index.exists():
            self._send(500, b"index.html not found", "text/plain; charset=utf-8")
            return
        self._send(200, index.read_bytes(), "text/html; charset=utf-8")


def run_server(cfg: Config, host: str = "127.0.0.1", port: int = 8765,
               open_browser: bool = False) -> None:
    Handler.state = ServerState(cfg)
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"\nPaperBase запущен: {url}")
    print("Остановить — Ctrl+C.\n")
    if open_browser:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановка сервера.")
    finally:
        httpd.server_close()
        Handler.state.store.close()
