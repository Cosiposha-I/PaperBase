"""Чанкинг с уважением к абзацам (блокам PDF).

Абзацы набираются в чанк до бюджета токенов (по токенизатору модели эмбеддингов),
с перекрытием overlap между соседними чанками. Для каждого чанка сохраняются
диапазон страниц и текущий раздел статьи (эвристика по заголовкам EN/RU).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from .extract import Page

_SECTION_WORDS = (
    r"abstract|introduction|background|literature review|materials?\s+and\s+methods?|"
    r"methodology|methods?|experimental(?:\s+setup|\s+section)?|results?\s+and\s+discussions?|"
    r"results?|discussions?|conclusions?(?:\s+and\s+future\s+work)?|acknowledg(?:e)?ments?|"
    r"references|bibliography|nomenclature|appendix|"
    r"аннотация|введение|обзор литературы|материалы и методы|методика|методы|"
    r"экспериментальная часть|результаты и обсуждение|результаты|обсуждение|"
    r"заключение|выводы|благодарности|список литературы|литература|приложение"
)
_HEADING_RE = re.compile(
    rf"^\s*(?:\d+(?:\.\d+)*\.?\s+)?({_SECTION_WORDS})\s*\.?\s*$", re.IGNORECASE
)
_NUMBERED_HEADING_RE = re.compile(r"^\s*\d+(?:\.\d+)*\.?\s+[A-ZА-ЯЁ][^.]{2,80}$")

_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Chunk:
    index: int
    text: str
    page_start: int
    page_end: int
    section: str


@dataclass
class _Piece:
    text: str
    page: int
    tokens: int
    section: str


def detect_section(block_text: str) -> str | None:
    """Если блок похож на заголовок раздела — вернуть его нормализованное имя."""
    line = block_text.strip()
    if len(line) > 90:
        return None
    m = _HEADING_RE.match(line)
    if m:
        return m.group(1).strip().capitalize()
    if _NUMBERED_HEADING_RE.match(line) and len(line.split()) <= 10:
        return re.sub(r"^\s*\d+(?:\.\d+)*\.?\s+", "", line).strip().capitalize()
    return None


def _split_long(text: str, budget: int, count_tokens: Callable[[str], int]) -> list[str]:
    """Абзац длиннее бюджета режем по предложениям."""
    sentences = _SENT_SPLIT_RE.split(text)
    parts: list[str] = []
    cur: list[str] = []
    cur_tok = 0
    for s in sentences:
        t = count_tokens(s)
        if t > budget:  # монструозное «предложение» — режем по словам
            words = s.split()
            step = max(1, int(len(words) * budget / max(t, 1)))
            for i in range(0, len(words), step):
                parts.append(" ".join(words[i:i + step]))
            continue
        if cur_tok + t > budget and cur:
            parts.append(" ".join(cur))
            cur, cur_tok = [], 0
        cur.append(s)
        cur_tok += t
    if cur:
        parts.append(" ".join(cur))
    return [p for p in parts if p.strip()]


def chunk_pages(pages: list[Page], count_tokens: Callable[[str], int],
                chunk_tokens: int = 800, overlap_tokens: int = 150) -> list[Chunk]:
    # 1) Поток кусочков (абзац или часть длинного абзаца) с секциями
    pieces: list[_Piece] = []
    section = ""
    for page in pages:
        for block in page.blocks:
            new_section = detect_section(block)
            if new_section:
                section = new_section
            t = count_tokens(block)
            if t > chunk_tokens:
                for part in _split_long(block, chunk_tokens, count_tokens):
                    pieces.append(_Piece(part, page.number, count_tokens(part), section))
            else:
                pieces.append(_Piece(block, page.number, t, section))

    # 2) Набор кусочков в чанки с перекрытием
    chunks: list[Chunk] = []
    buf: list[_Piece] = []
    buf_tok = 0
    dirty = False  # появились ли в буфере новые кусочки после последнего flush

    def flush() -> None:
        nonlocal buf, buf_tok, dirty
        if not buf:
            return
        text = "\n\n".join(p.text for p in buf).strip()
        if text:
            chunks.append(Chunk(
                index=len(chunks),
                text=text,
                page_start=min(p.page for p in buf),
                page_end=max(p.page for p in buf),
                section=buf[0].section,
            ))
        # хвост для перекрытия: последние кусочки на ~overlap токенов (не более половины чанка)
        tail: list[_Piece] = []
        tok = 0
        for p in reversed(buf):
            if tok + p.tokens > overlap_tokens or tok + p.tokens > buf_tok // 2:
                break
            tail.insert(0, p)
            tok += p.tokens
        buf = tail
        buf_tok = tok
        dirty = False

    for piece in pieces:
        if buf_tok + piece.tokens > chunk_tokens and dirty:
            flush()
        buf.append(piece)
        buf_tok += piece.tokens
        dirty = True
    if dirty:
        flush()

    return chunks
