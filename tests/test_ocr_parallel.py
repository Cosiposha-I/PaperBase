"""Параллельный OCR: порядок страниц, изоляция сбоя, выбор числа потоков.

Tesseract здесь подменён — проверяется обвязка в extract_pages, а не само
распознавание: сканы должны уходить в пул, результаты возвращаться на свои
страницы, а упавшая страница не должна тянуть за собой остальные.
"""
import threading
import time

import fitz
import pytesseract

from paperbase import extract
from paperbase.config import Config


def _blank_pdf(path, pages):
    """PDF без текстового слоя; ширина страницы кодирует её номер."""
    doc = fitz.open()
    for i in range(pages):
        doc.new_page(width=200 + i * 10, height=300)
    doc.save(path)
    doc.close()


def _page_no(img):
    # рендер 300 dpi: ширина в пикселях = ширина в пунктах * 300 / 72
    return round((img.width * 72 / 300 - 200) / 10) + 1


def test_parallel_ocr_keeps_page_order(tmp_path, monkeypatch):
    pdf = tmp_path / "scan.pdf"
    _blank_pdf(pdf, 12)
    seen_threads = set()

    def fake(img, lang=None):
        seen_threads.add(threading.get_ident())
        n = _page_no(img)
        time.sleep(0.02 * (12 - n))          # поздние страницы завершаются раньше
        return f"текст страницы номер {n}"

    monkeypatch.setattr(extract, "_ocr_available", True)
    monkeypatch.setattr(pytesseract, "image_to_string", fake)
    cfg = Config()
    cfg.ocr_workers = 4

    res = extract.extract_pages(pdf, cfg)

    assert res.ocr_pages == 12 and res.ocr_needed_pages == 0
    assert [p.text for p in res.pages] == [f"текст страницы номер {n}" for n in range(1, 13)]
    assert all(p.used_ocr for p in res.pages)
    assert len(seen_threads) > 1             # работа действительно шла в несколько потоков


def test_failed_page_does_not_break_others(tmp_path, monkeypatch):
    pdf = tmp_path / "scan.pdf"
    _blank_pdf(pdf, 6)

    def fake(img, lang=None):
        n = _page_no(img)
        if n == 3:
            raise RuntimeError("tesseract упал")
        return f"текст страницы номер {n}"

    monkeypatch.setattr(extract, "_ocr_available", True)
    monkeypatch.setattr(pytesseract, "image_to_string", fake)
    cfg = Config()
    cfg.ocr_workers = 3

    res = extract.extract_pages(pdf, cfg)

    assert res.ocr_pages == 5 and res.ocr_needed_pages == 1
    assert res.pages[2].ocr_needed and not res.pages[2].used_ocr
    assert res.pages[3].text == "текст страницы номер 4"


def test_ocr_workers_auto_and_explicit(monkeypatch):
    cfg = Config()
    cfg.ocr_workers = 5
    assert extract._ocr_workers(cfg) == 5
    cfg.ocr_workers = 0
    monkeypatch.setattr(extract.os, "cpu_count", lambda: 32)
    assert extract._ocr_workers(cfg) == 16   # потолок авто-режима
    monkeypatch.setattr(extract.os, "cpu_count", lambda: 4)
    assert extract._ocr_workers(cfg) == 2
    monkeypatch.setattr(extract.os, "cpu_count", lambda: 1)
    assert extract._ocr_workers(cfg) == 1
