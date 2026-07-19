"""Поиск, чтение, библиография, статистика — без какой-либо генерации текста."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path

from .config import Config
from .embed import Embedder
from .extract import read_page_texts
from .meta import first_author_surname
from .store import Store


@dataclass
class Hit:
    rank: int
    score: float
    doc_id: int
    cite_key: str
    citation: str          # "Sharma et al., 2022" — для подписи (Автор, Год)
    filename: str
    title: str
    year: int | None
    pages: list[int]       # [page_start, page_end]
    section: str
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


def make_citation(authors: str, year: int | None, filename: str) -> str:
    surname = first_author_surname(authors or "")
    if surname:
        et_al = " et al." if (authors.count(";") >= 1 or authors.count(",") >= 2) else ""
        label = f"{surname}{et_al}"
        return f"{label}, {year}" if year else label
    return f"[{filename}]" + (f", {year}" if year else "")


def search(store: Store, embedder: Embedder, query: str, *, k: int,
           paper: str | None = None, year_min: int | None = None,
           year_max: int | None = None) -> list[Hit]:
    total = store.vector_count()
    if total <= 0:
        return []

    # Фильтры проталкиваем прямо в запрос ChromaDB (а не отсеиваем в Python после):
    # так фильтр по файлу/году надёжен и на большом корпусе — нужные чанки не
    # «вытесняются» из окна кандидатов чужими статьями.
    conds = []
    if year_min is not None:
        conds.append({"year": {"$gte": int(year_min)}})
    if year_max is not None:
        conds.append({"year": {"$lte": int(year_max)}})
    if paper:
        ids = store.doc_ids_by_paper(paper)
        if not ids:
            return []  # под фильтр --paper не подошла ни одна статья
        conds.append({"doc_id": {"$in": ids}})

    where = None
    if len(conds) == 1:
        where = conds[0]
    elif len(conds) > 1:
        where = {"$and": conds}

    n = min(max(k * 4, k), total)
    res = store.collection.query(
        query_embeddings=[embedder.embed_query(query)],
        n_results=n,
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    metas = res["metadatas"][0]
    docs_text = res["documents"][0]
    dists = res["distances"][0]

    doc_ids = list({int(m["doc_id"]) for m in metas})
    doc_rows = store.docs_by_ids(doc_ids)

    hits: list[Hit] = []
    for meta, text, dist in zip(metas, docs_text, dists):
        row = doc_rows.get(int(meta["doc_id"]))
        year = int(meta.get("year") or 0) or None
        hits.append(Hit(
            rank=len(hits) + 1,
            score=round(1.0 - float(dist), 4),
            doc_id=int(meta["doc_id"]),
            cite_key=meta.get("cite_key") or "",
            citation=make_citation(row["authors"] if row else "", year,
                                   meta.get("filename", "")),
            filename=meta.get("filename", ""),
            title=(row["title"] if row else "") or "",
            year=year,
            pages=[int(meta.get("page_start") or 0), int(meta.get("page_end") or 0)],
            section=meta.get("section") or "",
            text=text,
        ))
        if len(hits) >= k:
            break
    return hits


def read_document(store: Store, ref: str, pages: str | None = None):
    """Возвращает (doc_row, [(page_num, text), ...]) либо (None, кандидаты)."""
    doc, candidates = store.resolve_doc(ref)
    if doc is None:
        return None, candidates
    p_from = p_to = None
    if pages:
        parts = pages.replace(" ", "").split("-")
        p_from = int(parts[0]) if parts[0] else None
        p_to = int(parts[-1]) if parts[-1] else p_from
    path = Path(doc["path"])
    if not path.exists():
        raise FileNotFoundError(f"PDF не найден на диске: {path}")
    return doc, read_page_texts(path, p_from, p_to)


def bib_entries(store: Store, keys: list[str] | None = None) -> list[dict]:
    docs = store.all_docs()
    if keys:
        wanted = {k.strip() for k in keys if k.strip()}
        docs = [d for d in docs if d["cite_key"] in wanted]
    entries = []
    for i, d in enumerate(docs, 1):
        entries.append({
            "n": i,
            "cite_key": d["cite_key"],
            "authors": d["authors"] or "не найдено",
            "year": d["year"],
            "title": d["title"] or "не найдено",
            "journal": d["journal"] or "",
            "doi": d["doi"] or "",
            "filename": d["filename"],
        })
    return entries


def format_bib_markdown(entries: list[dict]) -> str:
    lines = ["# Список литературы", ""]
    for e in entries:
        parts = [f"[{e['n']}] **{e['cite_key']}**", f"{e['authors']}"]
        parts.append(f"({e['year']})." if e["year"] else "(год не найден).")
        parts.append(f"{e['title']}.")
        if e["journal"]:
            parts.append(f"*{e['journal']}*.")
        if e["doi"]:
            parts.append(f"DOI: {e['doi']}.")
        parts.append(f"— файл: `{e['filename']}`")
        lines.append(" ".join(parts))
        lines.append("")
    return "\n".join(lines)


def corpus_stats(store: Store) -> dict:
    docs = store.all_docs()
    by_year: dict[str, int] = {}
    by_status: dict[str, int] = {}
    authors_count: dict[str, int] = {}
    for d in docs:
        y = str(d["year"]) if d["year"] else "неизвестен"
        by_year[y] = by_year.get(y, 0) + 1
        by_status[d["status"]] = by_status.get(d["status"], 0) + 1
        surname = first_author_surname(d["authors"] or "")
        if surname:
            authors_count[surname] = authors_count.get(surname, 0) + 1
    top_authors = sorted(authors_count.items(), key=lambda x: -x[1])[:10]
    return {
        "documents": len(docs),
        "chunks": store.chunk_count(),
        "vectors": store.vector_count(),
        "by_year": dict(sorted(by_year.items())),
        "by_status": by_status,
        "top_first_authors": top_authors,
    }
