#!/usr/bin/env python3
"""Markdown → .docx без pandoc, на python-docx.

Поддерживает подмножество, которое реально порождает синтез по корпусу:
заголовки #..####, абзацы, списки (маркированные и нумерованные), таблицы
с шапкой, цитаты-выноски (>), горизонтальные линии, а внутри текста —
**жирный**, *курсив*, `моноширинный`.

    python md_to_docx.py вход.md --out выход.docx --title "Заголовок"
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Emu, Inches, Pt, RGBColor

INLINE = re.compile(r"(\*\*.+?\*\*|\*[^*\n]+?\*|`[^`\n]+?`)")
BULLET = re.compile(r"^[-*+]\s+")
NUMBER = re.compile(r"^\d+[.)]\s+")
HRULE = re.compile(r"^(-{3,}|\*{3,})$")
IMAGE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$")

MAX_IMG_WIDTH_IN = 6.0  # ширина текстового блока A4 при полях 2,5 см


def _slug(text: str) -> str:
    """Имя закладки Word: только буквы/цифры/подчёркивание, не длиннее 40."""
    s = re.sub(r"[^\w]+", "_", text, flags=re.U).strip("_")
    return ("bm_" + s)[:40]


def add_bookmark(par, name: str) -> None:
    """Ставит закладку на абзац — цель для гиперссылки из оглавления."""
    bid = str(abs(hash(name)) % 100000)
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), bid)
    start.set(qn("w:name"), name)
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), bid)
    par._p.insert(0, start)
    par._p.append(end)


def add_internal_link(par, text: str, bookmark: str) -> None:
    """Гиперссылка внутри документа (на закладку)."""
    link = OxmlElement("w:hyperlink")
    link.set(qn("w:anchor"), bookmark)
    run = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "1F4E79")
    rpr.append(color)
    u = OxmlElement("w:u")
    u.set(qn("w:val"), "single")
    rpr.append(u)
    run.append(rpr)
    t = OxmlElement("w:t")
    t.text = text
    run.append(t)
    link.append(run)
    par._p.append(link)


def add_image(doc, path: Path, alt: str) -> None:
    """Вставка картинки по центру с подгонкой ширины под страницу."""
    if not path.exists():
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(f"[рисунок не найден: {path.name}]")
        r.italic = True
        r.font.color.rgb = RGBColor(0x99, 0x00, 0x00)
        return
    try:
        from PIL import Image  # ширину считаем по реальному размеру
        with Image.open(path) as im:
            w_px, h_px = im.size
        dpi = 190.0
        width_in = min(MAX_IMG_WIDTH_IN, w_px / dpi)
    except Exception:
        width_in = MAX_IMG_WIDTH_IN
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.add_run().add_picture(str(path), width=Inches(width_in))


def starts_block(line: str) -> bool:
    """Начинает ли строка новый блок — значит, предыдущий абзац/пункт закончен."""
    s = line.strip()
    return (not s or s.startswith(("#", ">", "|"))
            or bool(BULLET.match(s)) or bool(NUMBER.match(s)) or bool(HRULE.match(s)))


def add_runs(par, text: str) -> None:
    """Разложить строку на прогоны с учётом **жирного**, *курсива*, `кода`."""
    for part in INLINE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            par.add_run(part[2:-2]).bold = True
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            par.add_run(part[1:-1]).italic = True
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            r = par.add_run(part[1:-1])
            r.font.name = "Consolas"
            r.font.size = Pt(10)
        else:
            par.add_run(part)


def split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def is_sep(line: str) -> bool:
    """Строка-разделитель шапки таблицы: |---|:--:|---|"""
    s = line.strip().strip("|")
    return bool(s) and all(re.fullmatch(r":?-{2,}:?", c.strip()) for c in s.split("|"))


def add_table(doc, rows: list[list[str]]) -> None:
    head, body = rows[0], rows[1:]
    t = doc.add_table(rows=1, cols=len(head))
    t.style = "Light Grid Accent 1"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for cell, text in zip(t.rows[0].cells, head):
        cell.paragraphs[0].text = ""
        add_runs(cell.paragraphs[0], text)
        for r in cell.paragraphs[0].runs:
            r.bold = True
    for row in body:
        cells = t.add_row().cells
        # строки бывают короче шапки — не падаем
        for cell, text in zip(cells, row + [""] * (len(head) - len(row))):
            cell.paragraphs[0].text = ""
            add_runs(cell.paragraphs[0], text)
    doc.add_paragraph()


def add_quote(doc, lines: list[str]) -> None:
    """Выноска: серый курсив с отступом и линией слева не рисуем — отступа хватает."""
    text = " ".join(l.lstrip("> ").strip() for l in lines if l.strip("> ").strip())
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Pt(18)
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(6)
    add_runs(p, text)
    for r in p.runs:
        r.font.color.rgb = RGBColor(0x60, 0x30, 0x00)
        if not r.bold:
            r.italic = True


def collect_toc(md: str):
    """Собирает записи оглавления: (уровень, текст, имя_закладки)."""
    out = []
    for line in md.splitlines():
        m = re.match(r"^(#{1,3})\s+(.*)$", line.strip())
        if not m:
            continue
        level, text = len(m.group(1)), m.group(2).strip()
        text = re.sub(r"[*`]", "", text)
        out.append((level, text, _slug(text)))
    return out


def add_toc(doc, entries) -> None:
    """Оглавление с кликабельными ссылками на закладки."""
    doc.add_heading("Оглавление", level=1)
    for level, text, mark in entries:
        p = doc.add_paragraph()
        p.paragraph_format.left_indent = Pt(14 * (level - 1))
        p.paragraph_format.space_after = Pt(2)
        add_internal_link(p, text, mark)
        for r in p.runs:
            if level == 1:
                r.bold = True
    doc.add_page_break()


def convert(md: str, title: str | None, base_dir: Path, toc: bool = True) -> Document:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)
    style.paragraph_format.space_after = Pt(6)

    if title:
        doc.add_heading(title, level=0)

    if toc:
        add_toc(doc, collect_toc(md))

    lines = md.splitlines()
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # горизонтальная линия -> разрыв страницы между разделами
        if re.fullmatch(r"-{3,}|\*{3,}", stripped):
            doc.add_page_break()
            i += 1
            continue

        # картинка ![alt](path) — отдельным блоком
        mi = IMAGE.match(stripped)
        if mi:
            add_image(doc, base_dir / mi.group(2), mi.group(1))
            i += 1
            continue

        # заголовки — с закладкой для оглавления
        m = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if m:
            level = len(m.group(1))
            text = m.group(2).strip()
            h = doc.add_heading(text, level=level)
            if level <= 3:
                add_bookmark(h, _slug(re.sub(r"[*`]", "", text)))
            i += 1
            continue

        # таблица: строка с | и следующая — разделитель
        if stripped.startswith("|") and i + 1 < n and is_sep(lines[i + 1]):
            rows = [split_row(stripped)]
            i += 2
            while i < n and lines[i].strip().startswith("|"):
                rows.append(split_row(lines[i]))
                i += 1
            add_table(doc, rows)
            continue

        # цитата
        if stripped.startswith(">"):
            block = []
            while i < n and lines[i].strip().startswith(">"):
                block.append(lines[i])
                i += 1
            add_quote(doc, block)
            continue

        # Списки. Пункт может занимать несколько строк: в исходнике текст
        # переносится по ширине, и продолжение нужно приклеить к тому же пункту,
        # иначе «хвост» превращается в отдельный абзац без отступа.
        marker = next((p for p in (BULLET, NUMBER) if p.match(stripped)), None)
        if marker:
            style = "List Bullet" if marker is BULLET else "List Number"
            while i < n and marker.match(lines[i].strip()):
                item = [marker.sub("", lines[i].strip())]
                i += 1
                while i < n and not starts_block(lines[i]):
                    item.append(lines[i].strip())
                    i += 1
                p = doc.add_paragraph(style=style)
                add_runs(p, " ".join(item))
            continue

        # обычный абзац: склеиваем до пустой строки или начала другого блока
        buf = [stripped]
        i += 1
        while i < n and not starts_block(lines[i]):
            buf.append(lines[i].strip())
            i += 1
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        add_runs(p, " ".join(buf))

    return doc


def main() -> int:
    ap = argparse.ArgumentParser(description="Markdown -> docx (python-docx)")
    ap.add_argument("source")
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default=None)
    ap.add_argument("--no-toc", action="store_true", help="без оглавления")
    args = ap.parse_args()

    src = Path(args.source)
    if not src.exists():
        print(f"Файл не найден: {src}")
        return 1

    doc = convert(src.read_text(encoding="utf-8"), args.title,
                  base_dir=src.parent, toc=not args.no_toc)
    out = Path(args.out)
    doc.save(out)
    print(f"Записано: {out}  ({out.stat().st_size / 1024:.0f} КБ)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
