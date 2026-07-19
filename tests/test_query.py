"""Тесты форматирования цитат и библиографии."""
from paperbase.query import format_bib_markdown, make_citation


def test_make_citation_et_al_for_multiple_authors():
    assert make_citation("Ivanov, I.; Petrov, P.", 2021, "f.pdf") == "Ivanov et al., 2021"


def test_make_citation_single_author_no_etal():
    assert make_citation("Ivanov, I.", 2021, "f.pdf") == "Ivanov, 2021"


def test_make_citation_fallback_to_filename():
    assert make_citation("", None, "file.pdf") == "[file.pdf]"


def test_format_bib_markdown_contains_fields():
    entry = {"n": 1, "cite_key": "Ivanov2021", "authors": "Ivanov, I.",
             "year": 2021, "title": "Biodiesel study", "journal": "Fuel",
             "doi": "10.1000/x", "filename": "f.pdf"}
    md = format_bib_markdown([entry])
    assert "# Список литературы" in md
    assert "**Ivanov2021**" in md
    assert "Biodiesel study" in md
    assert "10.1000/x" in md
    assert "f.pdf" in md
