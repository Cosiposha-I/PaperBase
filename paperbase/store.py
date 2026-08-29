"""Хранилище: SQLite (documents, chunks) + ChromaDB (векторы).

Идемпотентность по sha256: повторный ingest того же файла пропускается.
В metadata каждого вектора — doc_id, cite_key, filename, year, page_start,
page_end, section (для фильтрации и цитирования).
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

from .chunk import Chunk
from .config import Config

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    cite_key TEXT,
    title TEXT,
    authors TEXT,
    year INTEGER,
    journal TEXT,
    doi TEXT,
    abstract TEXT,
    num_pages INTEGER,
    status TEXT NOT NULL DEFAULT 'ok',
    added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    page_start INTEGER,
    page_end INTEGER,
    section TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_documents_cite ON documents(cite_key);
"""

COLLECTION = "chunks"
_HNSW = {"hnsw:space": "cosine"}


def _vec_id(doc_id: int, index: int) -> str:
    return f"{doc_id}:{index}"


def _chunk_metadata(doc_id, cite_key, filename, year,
                    page_start, page_end, section) -> dict:
    """Единая форма metadata вектора ChromaDB (для add_chunks и reindex)."""
    return {
        "doc_id": doc_id,
        "cite_key": cite_key or "",
        "filename": filename,
        "year": int(year or 0),
        "page_start": page_start,
        "page_end": page_end,
        "section": section or "",
    }


def _ulower(s):
    """Понижение регистра с поддержкой Unicode — замена SQLite-функции lower().

    SQLite lower() работает только с ASCII, поэтому кириллица остаётся как есть.
    Регистрируется как ulower() в Store.__init__.
    """
    return s.lower() if isinstance(s, str) else s


class Store:
    def __init__(self, cfg: Config):
        cfg.ensure_dirs()
        self.cfg = cfg
        # check_same_thread=False: веб-сервер читает базу из рабочих потоков
        # (доступ сериализуется блокировкой в server.py; ingest однопоточный).
        self.conn = sqlite3.connect(cfg.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # Встроенная в SQLite lower() понижает регистр ТОЛЬКО у латиницы:
        # lower('Андрюса') возвращает 'Андрюса' без изменений. Из-за этого поиск
        # по слову слеп на кириллице с заглавной буквы — то есть на именах
        # собственных («Андрюс», «Блэкман», «Иерусалимский» не находились вовсе).
        # Регистрируем свою функцию: str.lower() в Python знает Unicode.
        self.conn.create_function("ulower", 1, _ulower, deterministic=True)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        self._chroma = None
        self._collection = None

    # --- Chroma (ленивая инициализация: для list/stats/bib не нужна) ---

    def _ensure_client(self):
        """Единая точка создания PersistentClient (использует collection и reset)."""
        if self._chroma is None:
            import chromadb
            from chromadb.config import Settings

            self._chroma = chromadb.PersistentClient(
                path=str(self.cfg.chroma_dir),
                settings=Settings(anonymized_telemetry=False),
            )
        return self._chroma

    @property
    def collection(self):
        if self._collection is None:
            self._collection = self._ensure_client().get_or_create_collection(
                COLLECTION, metadata=_HNSW)
        return self._collection

    # --- documents ---

    def doc_by_sha(self, sha: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM documents WHERE sha256=?", (sha,)).fetchone()

    def doc_by_path(self, path: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM documents WHERE path=?", (path,)).fetchone()

    def doc_by_id(self, doc_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()

    def docs_by_ids(self, ids: list[int]) -> dict[int, sqlite3.Row]:
        if not ids:
            return {}
        qmarks = ",".join("?" * len(ids))
        rows = self.conn.execute(f"SELECT * FROM documents WHERE id IN ({qmarks})", ids).fetchall()
        return {r["id"]: r for r in rows}

    def all_docs(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM documents ORDER BY cite_key, id").fetchall()

    def resolve_doc(self, ref: str) -> tuple[sqlite3.Row | None, list[sqlite3.Row]]:
        """id | точное имя файла | подстрока имени/cite_key. Возвращает (doc, кандидаты)."""
        if ref.isdigit():
            d = self.doc_by_id(int(ref))
            return (d, [d] if d else [])
        row = self.conn.execute(
            "SELECT * FROM documents WHERE filename=? OR cite_key=?", (ref, ref)).fetchone()
        if row:
            return row, [row]
        like = f"%{ref}%"
        rows = self.conn.execute(
            "SELECT * FROM documents WHERE filename LIKE ? OR cite_key LIKE ? OR title LIKE ?",
            (like, like, like)).fetchall()
        return (rows[0] if len(rows) == 1 else None), rows

    def unique_cite_key(self, base: str) -> str:
        existing = {r["cite_key"] for r in
                    self.conn.execute("SELECT cite_key FROM documents WHERE cite_key LIKE ?",
                                      (base + "%",)).fetchall()}
        if base not in existing:
            return base
        for suffix in "abcdefghijklmnopqrstuvwxyz":
            if base + suffix not in existing:
                return base + suffix
        return f"{base}_{len(existing)}"

    def insert_document(self, *, filename: str, path: str, sha256: str, cite_key: str,
                        title: str, authors: str, year: int | None, journal: str,
                        doi: str, abstract: str, num_pages: int, status: str) -> int:
        cur = self.conn.execute(
            """INSERT INTO documents (filename, path, sha256, cite_key, title, authors,
               year, journal, doi, abstract, num_pages, status, added_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (filename, path, sha256, cite_key, title, authors, year, journal, doi,
             abstract, num_pages, status, datetime.now(timezone.utc).isoformat()))
        self.conn.commit()
        return cur.lastrowid

    def set_status(self, doc_id: int, status: str) -> None:
        self.conn.execute("UPDATE documents SET status=? WHERE id=?", (status, doc_id))
        self.conn.commit()

    def delete_document(self, doc_id: int) -> None:
        try:
            self.collection.delete(where={"doc_id": doc_id})
        except Exception:
            pass
        self.conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        self.conn.commit()

    def reset_collection(self) -> None:
        """Пересоздать коллекцию векторов с нуля. Нужно для чистого --reingest:
        полная замена через delete+add в одной сессии может оставить HNSW-индекс
        ChromaDB в несогласованном состоянии, а свежая коллекция с одними add — нет.
        """
        client = self._ensure_client()
        try:
            client.delete_collection(COLLECTION)
        except Exception:
            pass
        self._collection = client.get_or_create_collection(COLLECTION, metadata=_HNSW)

    # --- chunks ---

    def add_chunks(self, doc_id: int, doc_row: dict, chunks: list[Chunk],
                   embeddings: list[list[float]]) -> None:
        self.conn.executemany(
            "INSERT INTO chunks (doc_id, chunk_index, text, page_start, page_end, section) "
            "VALUES (?,?,?,?,?,?)",
            [(doc_id, c.index, c.text, c.page_start, c.page_end, c.section) for c in chunks])
        self.conn.commit()
        self.collection.add(
            ids=[_vec_id(doc_id, c.index) for c in chunks],
            embeddings=embeddings,
            documents=[c.text for c in chunks],
            metadatas=[_chunk_metadata(doc_id, doc_row["cite_key"], doc_row["filename"],
                                       doc_row["year"], c.page_start, c.page_end, c.section)
                       for c in chunks])

    def add_chunk_rows(self, rows, embeddings: list[list[float]]) -> None:
        """Добавить в ChromaDB уже готовые строки чанков (для reindex).
        rows — записи из iter_chunk_rows (в них есть текст, страницы и метаданные)."""
        self.collection.add(
            ids=[_vec_id(r["doc_id"], r["chunk_index"]) for r in rows],
            embeddings=embeddings,
            documents=[r["text"] for r in rows],
            metadatas=[_chunk_metadata(r["doc_id"], r["cite_key"], r["filename"],
                                       r["year"], r["page_start"], r["page_end"], r["section"])
                       for r in rows])

    def iter_chunk_rows(self) -> list[sqlite3.Row]:
        """Все чанки с метаданными их документа — источник для reindex
        (эмбеддинги пересобираются из этого текста, PDF читать не нужно)."""
        return self.conn.execute(
            """SELECT c.doc_id, c.chunk_index, c.text, c.page_start, c.page_end,
                      c.section, d.cite_key, d.filename, d.year
               FROM chunks c JOIN documents d ON d.id = c.doc_id
               ORDER BY c.doc_id, c.chunk_index""").fetchall()

    def doc_ids_by_paper(self, pattern: str) -> list[int]:
        """doc_id статей, чьё имя файла подходит под glob/подстроку —
        чтобы протолкнуть фильтр --paper прямо в запрос ChromaDB."""
        import fnmatch
        p = pattern.lower()
        use_glob = any(ch in p for ch in "*?[")
        ids: list[int] = []
        for r in self.conn.execute("SELECT id, filename FROM documents").fetchall():
            f = (r["filename"] or "").lower()
            if fnmatch.fnmatch(f, p) if use_glob else p in f:
                ids.append(int(r["id"]))
        return ids

    def docs_by_doi(self, doi: str) -> list[sqlite3.Row]:
        """Документы с таким же DOI (для предупреждения о дубликатах)."""
        if not doi:
            return []
        return self.conn.execute(
            "SELECT * FROM documents WHERE doi = ? AND doi <> ''", (doi,)).fetchall()

    def prune_missing(self) -> list[str]:
        """Удалить из базы записи, чьих PDF больше нет на диске. Возвращает имена."""
        removed: list[str] = []
        for d in self.all_docs():
            if not Path(d["path"]).exists():
                self.delete_document(d["id"])
                removed.append(d["filename"])
        return removed

    def keyword_search(self, term: str, limit: int = 50,
                       paper: str | None = None) -> dict:
        """Поиск ПО СЛОВУ (не по смыслу): документы, где term встречается в тексте
        (подстрока, регистронезависимо). Модель не нужна. Возвращает
        {"total": сколько документов всего, "documents": [до limit записей]}; в каждой:
        cite_key, filename, year, title, hits (число фрагментов), page, snippet."""
        term_l = term.lower()
        like = f"%{term_l}%"
        params: list = [like]
        paper_clause = ""
        if paper:
            ids = self.doc_ids_by_paper(paper)
            if not ids:
                return {"total": 0, "documents": []}
            paper_clause = f" AND c.doc_id IN ({','.join('?' * len(ids))})"
            params += ids

        # ulower(), а не lower(): встроенная в SQLite версия не понижает кириллицу,
        # из-за чего слова с заглавной буквы (имена собственные) не находились.
        total = self.conn.execute(
            f"SELECT COUNT(DISTINCT c.doc_id) FROM chunks c "
            f"WHERE ulower(c.text) LIKE ?{paper_clause}", params).fetchone()[0]
        rows = self.conn.execute(
            "SELECT d.id, d.cite_key, d.filename, d.year, d.title, "
            "COUNT(*) AS hits, MIN(c.page_start) AS first_page "
            "FROM chunks c JOIN documents d ON d.id = c.doc_id "
            f"WHERE ulower(c.text) LIKE ?{paper_clause} "
            "GROUP BY d.id ORDER BY hits DESC, d.year DESC LIMIT ?",
            params + [limit]).fetchall()

        out: list[dict] = []
        for r in rows:
            crow = self.conn.execute(
                "SELECT text, page_start FROM chunks WHERE doc_id = ? AND ulower(text) LIKE ? "
                "ORDER BY chunk_index LIMIT 1", (r["id"], like)).fetchone()
            snippet, page = "", r["first_page"]
            if crow:
                page = crow["page_start"]
                t = crow["text"]
                pos = t.lower().find(term_l)
                a, b = max(0, pos - 80), min(len(t), pos + len(term) + 80)
                snippet = ("…" if a > 0 else "") + t[a:b].strip() + ("…" if b < len(t) else "")
            out.append({"doc_id": r["id"], "cite_key": r["cite_key"],
                        "filename": r["filename"], "year": r["year"],
                        "title": r["title"] or "", "hits": r["hits"],
                        "page": page, "snippet": snippet})
        return {"total": total, "documents": out}

    def chunk_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

    def vector_count(self) -> int:
        try:
            return self.collection.count()
        except Exception:
            return -1

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> bool:
        self.close()
        return False
