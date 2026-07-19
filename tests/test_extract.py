"""Тесты извлечения текста: очистка блока, пагинация, RTF, диспетчер форматов."""
from paperbase.config import Config
from paperbase.extract import (SUPPORTED_EXTS, _clean_block, _paginate_text,
                               _rtf_to_text, extract_document)


def test_clean_block_dehyphenates():
    assert _clean_block("hyphen-\nation test") == "hyphenation test"


def test_clean_block_joins_lines():
    assert _clean_block("line one\nline two") == "line one line two"


def test_clean_block_collapses_spaces():
    assert _clean_block("multiple    spaces   here") == "multiple spaces here"


def test_clean_block_at_sign_hyphenation():
    # некоторые PDF кодируют перенос как '@' (шрифт), а не дефисом
    assert _clean_block("Направле@\nние движения") == "Направление движения"


def test_clean_block_at_sign_inline_is_hyphen():
    # '@' между кириллическими буквами внутри строки — подменённый дефис
    assert _clean_block("физико@химический") == "физико-химический"


def test_clean_block_keeps_latin_at_sign():
    # почту не трогаем (латиница вокруг '@')
    assert _clean_block("mail to user@example.com") == "mail to user@example.com"


def test_supported_exts_covers_text_formats():
    assert {".pdf", ".txt", ".rtf"} <= SUPPORTED_EXTS


def test_paginate_text_keeps_paragraphs():
    pages = _paginate_text("Para one.\n\nPara two.\n\nPara three.")
    assert pages[0].number == 1
    joined = " ".join(p.text for p in pages)
    assert "Para one" in joined and "Para three" in joined


def test_rtf_to_text_strips_controls():
    out = _rtf_to_text(r"{\rtf1\ansi\ansicpg1251 Hello \b world\b0 done\par}")
    assert "Hello" in out and "world" in out and "done" in out
    assert "\\rtf" not in out and "{" not in out


def test_extract_document_txt(tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("First paragraph about biodiesel.\n\nSecond one.", encoding="utf-8")
    res = extract_document(f, Config(data_dir=tmp_path))
    assert res.num_pages >= 1
    assert "biodiesel" in " ".join(p.text for p in res.pages)
