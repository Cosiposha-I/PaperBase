# -*- coding: utf-8 -*-
"""
Поиск изображений (схем, графиков, формул) в PDF корпуса по ключевым словам.

Ищет страницы, где рядом с подписью «Рисунок N …» / «Рис. N …» есть растровое
изображение достаточного размера, и выдаёт их координаты для последующей вырезки.

Использование:
    python find_figures.py --corpus "<папка>" --query "кривая роста" [--min-area 40000]
"""
import argparse
import re
from pathlib import Path

import fitz  # PyMuPDF

CAPTION = re.compile(r"(Рису?н?о?к|Рис\.|Схема|Таблица)\s*№?\s*(\d+)\s*[–—.-]?\s*(.{0,90})",
                     re.I)


def scan_pdf(path: Path, terms, min_area: int):
    """Возвращает список находок: (стр, подпись, число картинок, макс. площадь)."""
    out = []
    try:
        doc = fitz.open(path)
    except Exception:
        return out
    for pno in range(len(doc)):
        page = doc[pno]
        text = page.get_text()
        low = text.lower()
        if terms and not any(t in low for t in terms):
            continue
        imgs = page.get_images(full=True)
        if not imgs:
            continue
        # площадь самой крупной картинки
        best = 0
        for im in imgs:
            try:
                rects = page.get_image_rects(im[0])
                for r in rects:
                    best = max(best, int(r.width * r.height))
            except Exception:
                pass
        if best < min_area:
            continue
        caps = [f"{m.group(1)} {m.group(2)} {m.group(3).strip()}"[:95]
                for m in CAPTION.finditer(text)]
        out.append((pno + 1, caps[:3], len(imgs), best))
    doc.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--query", required=True, help="ключевые слова через |")
    ap.add_argument("--min-area", type=int, default=40000)
    ap.add_argument("--limit", type=int, default=25)
    a = ap.parse_args()

    terms = [t.strip().lower() for t in a.query.split("|") if t.strip()]
    root = Path(a.corpus)
    found = 0
    for pdf in sorted(root.rglob("*.pdf")):
        hits = scan_pdf(pdf, terms, a.min_area)
        if not hits:
            continue
        print(f"\n=== {pdf.name}")
        for pno, caps, n, area in hits[: a.limit]:
            cap = " | ".join(caps) if caps else "(без подписи)"
            print(f"   стр.{pno:>4}  картинок:{n}  площадь:{area:>7}  {cap}")
            found += 1
            if found >= a.limit:
                return


if __name__ == "__main__":
    main()
