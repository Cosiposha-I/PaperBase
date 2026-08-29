# -*- coding: utf-8 -*-
"""
Каталог всех рисунков корпуса: файл, страница, подпись, размер картинки.

Строит единый список, по которому потом подбираются иллюстрации к вопросам.

Использование:
    python figure_catalog.py --corpus "<папка>" --out catalog.tsv [--min-area 25000]
"""
import argparse
import re
from pathlib import Path

import fitz

CAPTION = re.compile(
    r"(?:Рису?нок|Рис\.)\s*№?\s*(\d+)\s*[–—.\-:]?\s*([^\n]{3,110})", re.I)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-area", type=int, default=25000)
    a = ap.parse_args()

    rows = []
    for pdf in sorted(Path(a.corpus).rglob("*.pdf")):
        try:
            doc = fitz.open(pdf)
        except Exception:
            continue
        for pno in range(len(doc)):
            page = doc[pno]
            imgs = page.get_images(full=True)
            if not imgs:
                continue
            area = 0
            for im in imgs:
                try:
                    for r in page.get_image_rects(im[0]):
                        area = max(area, int(r.width * r.height))
                except Exception:
                    pass
            if area < a.min_area:
                continue
            text = page.get_text()
            caps = [f"Рис.{m.group(1)} {m.group(2).strip()}" for m in CAPTION.finditer(text)]
            cap = caps[0] if caps else ""
            rows.append((pdf.name, pno + 1, area, cap.replace("\t", " ")[:110]))
        doc.close()

    with open(a.out, "w", encoding="utf-8") as f:
        f.write("файл\tстраница\tплощадь\tподпись\n")
        for r in rows:
            f.write("\t".join(str(x) for x in r) + "\n")
    print(f"Рисунков в каталоге: {len(rows)}  ->  {a.out}")


if __name__ == "__main__":
    main()
