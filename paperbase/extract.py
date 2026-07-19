"""Извлечение текста из документов корпуса.

PDF — постранично (PyMuPDF) с OCR-fallback для сканов: текст берётся блоками
(get_text("blocks")), блоки служат абзацами для чанкинга; страница без текстового
слоя распознаётся Tesseract'ом, а без него помечается ocr_needed (пайплайн не падает).
Простой текст (.txt/.md) и .rtf читаются напрямую и разбиваются на псевдостраницы.
Диспетчер по расширению — extract_document(); поддерживаемые форматы — SUPPORTED_EXTS.
"""

from __future__ import annotations

import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF

from .config import Config

# Расширения, которые ingest берёт в корпус
SUPPORTED_EXTS = {".pdf", ".txt", ".md", ".rtf"}
_CHARS_PER_PAGE = 2500        # псевдо-пагинация простого текста для номеров «стр. X»


@dataclass
class Page:
    number: int                      # 1-based
    blocks: list[str] = field(default_factory=list)
    used_ocr: bool = False
    ocr_needed: bool = False         # текста нет, а OCR недоступен

    @property
    def text(self) -> str:
        return "\n\n".join(self.blocks)


@dataclass
class ExtractResult:
    pages: list[Page]
    num_pages: int
    ocr_pages: int = 0               # страниц распознано OCR
    ocr_needed_pages: int = 0        # страниц пропущено из-за отсутствия Tesseract


_ocr_available: bool | None = None


def _tesseract_candidates() -> list[Path]:
    """Типовые места установки Tesseract по платформам (когда его нет в PATH)."""
    if sys.platform == "win32":
        return [Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
                Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe")]
    if sys.platform == "darwin":
        return [Path("/opt/homebrew/bin/tesseract"),      # Apple Silicon (brew)
                Path("/usr/local/bin/tesseract")]         # Intel (brew)
    return [Path("/usr/bin/tesseract"), Path("/usr/local/bin/tesseract")]


def ocr_available(cfg: Config) -> bool:
    """Единожды проверяем наличие бинарника tesseract: конфиг → PATH → типовые пути."""
    global _ocr_available
    if _ocr_available is not None:
        return _ocr_available

    import pytesseract

    cmd = cfg.tesseract_cmd.strip()
    if cmd and Path(cmd).exists():
        pytesseract.pytesseract.tesseract_cmd = cmd
        _ocr_available = True
        return _ocr_available

    found = shutil.which("tesseract")
    if not found:
        found = next((str(p) for p in _tesseract_candidates() if p.exists()), None)
    if found:
        pytesseract.pytesseract.tesseract_cmd = found
    _ocr_available = found is not None
    return _ocr_available


def _clean_block(raw: str) -> str:
    """Склейка переносов слов и строк внутри блока в один абзац.

    Некоторые PDF кодируют символ переноса не дефисом, а '@' или мягким переносом
    (U+00AD) — учитываем и их, иначе слова остаются разорванными («вос@произведение»).

    Внутри строки трогаем '@' только между кириллическими буквами: в остальных
    случаях он часто настоящий — почта, запись композитов «ядро-оболочка»
    (Fe3O4@SiO2), «96 % @ 5 A/g». Полностью разобрать сбитые шрифтом символы
    (напр. '=' → '@', '–' → 'A' в отдельных статьях) надёжно нельзя — см. README.
    """
    # перенос слова на конце строки: дефис / мягкий перенос (U+00AD) / '@' -> склеиваем
    text = re.sub(r"[-­@]\s*\n\s*", "", raw)   # пере-\nнос -> перенос
    # оставшийся '@' между кириллическими буквами — подменённый дефис
    text = re.sub(r"([а-яёА-ЯЁ])@([а-яёА-ЯЁ])", r"\1-\2", text)
    text = re.sub(r"\s*\n\s*", " ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def _page_blocks(page: fitz.Page) -> list[str]:
    blocks = []
    for b in page.get_text("blocks", sort=True):
        if b[6] != 0:  # только текстовые блоки
            continue
        text = _clean_block(b[4])
        if len(text) < 2:
            continue
        blocks.append(text)
    return blocks


def _ocr_page(page: fitz.Page, languages: str) -> list[str]:
    import pytesseract
    from PIL import Image

    pix = page.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72), colorspace=fitz.csGRAY)
    img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    raw = pytesseract.image_to_string(img, lang=languages)
    paragraphs = [_clean_block(p) for p in re.split(r"\n\s*\n", raw)]
    return [p for p in paragraphs if len(p) >= 2]


def extract_pages(pdf_path: Path, cfg: Config) -> ExtractResult:
    result_pages: list[Page] = []
    ocr_pages = 0
    ocr_needed_pages = 0

    with fitz.open(pdf_path) as doc:
        for i, page in enumerate(doc):
            p = Page(number=i + 1, blocks=_page_blocks(page))
            if len(p.text) < cfg.min_chars_per_page:
                if ocr_available(cfg):
                    try:
                        ocr_blocks = _ocr_page(page, cfg.ocr_languages)
                        if ocr_blocks:
                            p.blocks = ocr_blocks
                            p.used_ocr = True
                            ocr_pages += 1
                    except Exception:
                        p.ocr_needed = True
                        ocr_needed_pages += 1
                else:
                    # OCR недоступен, а текста нет — помечаем страницу как пропущенную
                    p.ocr_needed = True
                    ocr_needed_pages += 1
            result_pages.append(p)
        num_pages = doc.page_count

    return ExtractResult(
        pages=result_pages,
        num_pages=num_pages,
        ocr_pages=ocr_pages,
        ocr_needed_pages=ocr_needed_pages,
    )


def _read_text_file(path: Path) -> str:
    """Прочитать текстовый файл, подбирая кодировку (utf-8 → cp1251 → latin-1)."""
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1251", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def _rtf_to_text(raw: str) -> str:
    """Грубое извлечение текста из RTF (best-effort: спецсимволы, control words)."""
    text = re.sub(r"\\u(-?\d+)\??", lambda m: chr(int(m.group(1)) % 0x10000), raw)
    text = re.sub(r"\\'([0-9a-fA-F]{2})",
                  lambda m: bytes([int(m.group(1), 16)]).decode("cp1251", "ignore"), text)
    text = re.sub(r"\\par[d]?\b", "\n\n", text)          # абзацы
    text = re.sub(r"\\[a-zA-Z]+-?\d* ?", " ", text)       # прочие control words
    text = text.replace("{", "").replace("}", "")
    text = re.sub(r"\\[^a-zA-Z]", "", text)               # экранированные символы
    return text


def _paginate_text(text: str) -> list[Page]:
    """Разбить простой текст на псевдостраницы (абзацы по пустой строке, ~2500 симв.)."""
    paras = [_clean_block(p) for p in re.split(r"\n\s*\n", text)]
    paras = [p for p in paras if len(p) >= 2]
    pages: list[Page] = []
    buf: list[str] = []
    size = 0
    for p in paras:
        buf.append(p)
        size += len(p)
        if size >= _CHARS_PER_PAGE:
            pages.append(Page(number=len(pages) + 1, blocks=buf))
            buf, size = [], 0
    if buf:
        pages.append(Page(number=len(pages) + 1, blocks=buf))
    return pages or [Page(number=1, blocks=[])]


def extract_document(path: Path, cfg: Config) -> ExtractResult:
    """Диспетчер по расширению: PDF → постранично (+OCR), .txt/.md/.rtf → текстом."""
    suf = path.suffix.lower()
    if suf == ".pdf":
        return extract_pages(path, cfg)
    text = _read_text_file(path)
    if suf == ".rtf":
        text = _rtf_to_text(text)
    pages = _paginate_text(text)
    return ExtractResult(pages=pages, num_pages=len(pages))


def read_page_texts(path: Path, page_from: int | None = None, page_to: int | None = None) -> list[tuple[int, str]]:
    """Полный текст страниц для команды `read` (перечитывает исходный файл)."""
    p = Path(path)
    if p.suffix.lower() != ".pdf":
        raw = _read_text_file(p)
        if p.suffix.lower() == ".rtf":
            raw = _rtf_to_text(raw)
        pages = _paginate_text(raw)
        start = page_from or 1
        end = page_to or len(pages)
        return [(pg.number, pg.text) for pg in pages if start <= pg.number <= end]

    out: list[tuple[int, str]] = []
    with fitz.open(p) as doc:
        start = (page_from or 1) - 1
        end = page_to or doc.page_count
        start = max(0, start)
        end = min(doc.page_count, end)
        for i in range(start, end):
            out.append((i + 1, doc[i].get_text("text")))
    return out
