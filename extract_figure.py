# -*- coding: utf-8 -*-
"""
Вырезка страницы (или её части) из PDF в PNG для вставки в документ.

Рендерит страницу целиком либо только область изображения — это надёжнее, чем
доставать встроенный растр: у сканов встроенная картинка часто равна всей
странице, а у векторных схем растра нет вовсе.

Использование:
    python extract_figure.py "<pdf>" --page 23 --out fig.png [--dpi 200] [--crop auto]
"""
import argparse
from pathlib import Path

import fitz


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--page", type=int, required=True, help="номер страницы (с 1)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--crop", default="none",
                    help="none | auto (по самой крупной картинке) | x0,y0,x1,y1 (доли 0..1)")
    ap.add_argument("--pad", type=float, default=0.02, help="поля при crop=auto, доли страницы")
    a = ap.parse_args()

    doc = fitz.open(a.pdf)
    page = doc[a.page - 1]
    rect = page.rect

    clip = None
    if a.crop == "auto":
        best, area = None, 0
        for im in page.get_images(full=True):
            for r in page.get_image_rects(im[0]):
                if r.width * r.height > area:
                    area, best = r.width * r.height, r
        if best is not None:
            pad_x, pad_y = rect.width * a.pad, rect.height * a.pad
            clip = fitz.Rect(max(rect.x0, best.x0 - pad_x),
                             max(rect.y0, best.y0 - pad_y),
                             min(rect.x1, best.x1 + pad_x),
                             min(rect.y1, best.y1 + pad_y))
    elif "," in a.crop:
        x0, y0, x1, y1 = [float(v) for v in a.crop.split(",")]
        clip = fitz.Rect(rect.x0 + rect.width * x0, rect.y0 + rect.height * y0,
                         rect.x0 + rect.width * x1, rect.y0 + rect.height * y1)

    zoom = a.dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    pix.save(a.out)
    print(f"{a.out}  {pix.width}x{pix.height}px  ({Path(a.out).stat().st_size // 1024} КБ)")
    doc.close()


if __name__ == "__main__":
    main()
