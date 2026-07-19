"""Тесты чистых функций метаданных (без сети/PDF)."""
from paperbase.meta import (DocMeta, _clean_doi, _find_year,
                            first_author_surname, make_cite_key)


def test_first_author_surname_semicolon():
    assert first_author_surname("Ivanov, I.; Petrov, P.") == "Ivanov"


def test_first_author_surname_plain_name():
    assert first_author_surname("John Smith") == "Smith"


def test_first_author_surname_empty():
    assert first_author_surname("") == ""


def test_make_cite_key_from_author_year():
    assert make_cite_key(DocMeta(authors="Ivanov, I.", year=2021), "f.pdf") == "Ivanov2021"


def test_make_cite_key_from_title_when_no_author():
    key = make_cite_key(DocMeta(authors="", title="Deep Learning Study", year=2020), "f.pdf")
    assert key == "Deep2020"


def test_make_cite_key_no_year_uses_nd():
    assert make_cite_key(DocMeta(authors="Ivanov, I.", year=None), "f.pdf") == "IvanovND"


def test_find_year_from_filename_priority():
    # год в имени файла (отделённый пробелом) важнее текста
    assert _find_year("published in 2019", "study 2021.pdf") == 2021


def test_find_year_underscore_not_from_filename():
    # тонкость: '_' — словесный символ, границы слова перед годом нет,
    # поэтому из 'study_2021' год НЕ берётся — падаем на текст
    assert _find_year("published in 2019", "study_2021.pdf") == 2019


def test_find_year_from_text_max():
    assert _find_year("versions 2018 and 2020 discussed", "noyear.pdf") == 2020


def test_find_year_none():
    assert _find_year("no digits here", "paper.pdf") is None


def test_clean_doi_strips_trailing_punct():
    assert _clean_doi("10.1234/abc.def).") == "10.1234/abc.def"
