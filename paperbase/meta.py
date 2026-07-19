"""Метаданные статьи best-effort.

Порядок: метаданные PDF -> эвристики по первой странице (крупный шрифт = заголовок,
DOI по регулярке, год) -> обогащение через CrossRef по DOI (только при наличии
интернета, с коротким таймаутом; офлайн не блокирует пайплайн).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import fitz

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>]+)", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(19\d{2}|20[0-2]\d)\b")
MAX_PLAUSIBLE_YEAR = 2027

_JUNK_AUTHORS = ("elsevier", "adobe", "latex", "microsoft", "word", "unknown",
                 "springer", "wiley", "mdpi", "arbortext", "acrobat")


@dataclass
class DocMeta:
    title: str = ""
    authors: str = ""     # "Фамилия, И.; Фамилия, И." либо как в источнике
    year: int | None = None
    journal: str = ""
    doi: str = ""
    abstract: str = ""


def _clean_doi(doi: str) -> str:
    return doi.rstrip(".,;)]}»”’")


def _title_from_layout(doc: fitz.Document) -> str:
    """Заголовок = строки с максимальным кеглем в верхних 2/3 первой страницы."""
    try:
        page = doc[0]
        d = page.get_text("dict")
    except Exception:
        return ""
    page_h = page.rect.height or 1
    lines: list[tuple[float, float, str]] = []  # (size, y, text)
    for block in d.get("blocks", []):
        for line in block.get("lines", []):
            text = "".join(s.get("text", "") for s in line.get("spans", [])).strip()
            if len(text) < 4:
                continue
            y = line["bbox"][1]
            if y > page_h * 0.67:
                continue
            size = max((s.get("size", 0) for s in line.get("spans", [])), default=0)
            lines.append((size, y, text))
    if not lines:
        return ""
    max_size = max(s for s, _, _ in lines)
    picked = [(y, t) for s, y, t in lines if s >= max_size - 0.6]
    picked.sort(key=lambda x: x[0])
    title = " ".join(t for _, t in picked[:6]).strip()
    title = re.sub(r"\s{2,}", " ", title)
    # Явно не заголовок: одна цифра, слишком коротко/длинно
    if len(title) < 12 or len(title) > 400:
        return ""
    return title


def _pdf_meta(doc: fitz.Document) -> tuple[str, str]:
    md = doc.metadata or {}
    title = (md.get("title") or "").strip()
    authors = (md.get("author") or "").strip()
    if title and (len(title) < 8 or title.lower() in ("untitled", "title")):
        title = ""
    if authors and any(j in authors.lower() for j in _JUNK_AUTHORS):
        authors = ""
    return title, authors


def _find_year(first_pages_text: str, filename: str) -> int | None:
    # Год в имени файла — надёжный сигнал (пользователь сам его указал)
    m = YEAR_RE.findall(Path(filename).stem)
    if m:
        y = max(int(x) for x in m)
        if 1900 <= y <= MAX_PLAUSIBLE_YEAR:
            return y
    years = [int(x) for x in YEAR_RE.findall(first_pages_text)]
    years = [y for y in years if 1900 <= y <= MAX_PLAUSIBLE_YEAR]
    return max(years) if years else None


def _find_abstract(first_pages_text: str) -> str:
    m = re.search(r"(?is)\babstract\b[:\s]*(.+?)(?:\bkeywords?\b|\b1\.?\s+introduction\b|\Z)",
                  first_pages_text)
    if not m:
        m = re.search(r"(?is)\bаннотация\b[:\s]*(.+?)(?:\bключевые слова\b|\Z)", first_pages_text)
    if not m:
        return ""
    text = re.sub(r"\s+", " ", m.group(1)).strip()
    return text[:2000]


def _crossref(doi: str, timeout: float, mailto: str) -> dict | None:
    try:
        import requests

        headers = {"User-Agent": f"paperbase/0.1 (mailto:{mailto or 'anon@example.com'})"}
        r = requests.get(f"https://api.crossref.org/works/{doi}", headers=headers, timeout=timeout)
        if r.status_code != 200:
            return None
        msg = r.json().get("message", {})
        authors = "; ".join(
            f"{a.get('family', '')}, {a.get('given', '')}".strip(", ")
            for a in msg.get("author", []) if a.get("family")
        )
        year = None
        for key in ("published-print", "published-online", "issued", "created"):
            parts = (msg.get(key) or {}).get("date-parts") or []
            if parts and parts[0] and parts[0][0]:
                year = int(parts[0][0])
                break
        return {
            "title": (msg.get("title") or [""])[0],
            "authors": authors,
            "year": year,
            "journal": (msg.get("container-title") or [""])[0],
        }
    except Exception:
        return None  # офлайн/таймаут — молча продолжаем


def extract_metadata(pdf_path: Path, first_pages_text: str, *,
                     crossref_enabled: bool, crossref_timeout: float,
                     crossref_mailto: str) -> DocMeta:
    meta = DocMeta()
    try:
        with fitz.open(pdf_path) as doc:
            meta.title, meta.authors = _pdf_meta(doc)
            if not meta.title:
                meta.title = _title_from_layout(doc)
    except Exception:
        pass

    m = DOI_RE.search(first_pages_text)
    if m:
        meta.doi = _clean_doi(m.group(1))
    meta.year = _find_year(first_pages_text, pdf_path.name)
    meta.abstract = _find_abstract(first_pages_text)

    if meta.doi and crossref_enabled:
        cr = _crossref(meta.doi, crossref_timeout, crossref_mailto)
        if cr:
            meta.title = cr["title"] or meta.title
            meta.authors = cr["authors"] or meta.authors
            meta.year = cr["year"] or meta.year
            meta.journal = cr["journal"] or meta.journal

    if not meta.title:
        meta.title = Path(pdf_path).stem.replace("_", " ")
    return meta


def first_author_surname(authors: str) -> str:
    """Фамилия первого автора для cite_key и подписи (Автор, Год)."""
    if not authors:
        return ""
    first = re.split(r"[;]|(?<!,)\s+and\s+", authors)[0].strip()
    if "," in first:
        candidate = first.split(",")[0].strip()
    else:
        tokens = [t for t in re.findall(r"[A-Za-zА-Яа-яЁё\-']{2,}", first)]
        if not tokens:
            return ""
        candidate = tokens[-1] if len(tokens[-1]) > 1 else tokens[0]
    return re.sub(r"[^A-Za-zА-Яа-яЁё\-']", "", candidate)


def make_cite_key(meta: DocMeta, filename: str) -> str:
    surname = first_author_surname(meta.authors)
    year = str(meta.year) if meta.year else "ND"
    if surname:
        return f"{surname.capitalize()}{year}"
    word = next((w for w in re.findall(r"[A-Za-zА-Яа-яЁё]{4,}", meta.title or "")), None)
    if word:
        return f"{word.capitalize()}{year}"
    stem = re.sub(r"[^A-Za-zА-Яа-яЁё0-9]", "", Path(filename).stem)[:20]
    return f"{stem or 'Doc'}{year}"
