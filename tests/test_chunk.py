"""Тесты чанкинга со стаб-токенайзером (count_tokens = число слов)."""
from paperbase.chunk import chunk_pages, detect_section, _split_long
from paperbase.extract import Page

TOK = lambda s: len(s.split())  # noqa: E731 — стаб токенайзера для тестов


def test_detect_section_keyword():
    assert detect_section("Introduction") == "Introduction"
    assert detect_section("References") == "References"


def test_detect_section_numbered_keyword():
    assert detect_section("3. Results") == "Results"


def test_detect_section_ru():
    assert detect_section("Заключение") == "Заключение"


def test_detect_section_not_heading():
    long_line = ("это обычный длинный абзац текста который никак не является "
                 "заголовком раздела и должен вернуть None точно")
    assert detect_section(long_line) is None


def test_split_long_no_sentence_boundaries():
    parts = _split_long("a b c d e f", budget=2, count_tokens=TOK)
    assert len(parts) == 3
    assert all(len(p.split()) <= 2 for p in parts)


def test_split_long_by_sentences():
    parts = _split_long("One two three. Four five six.", budget=3, count_tokens=TOK)
    assert len(parts) == 2


def test_chunk_pages_basic():
    pages = [
        Page(number=1, blocks=["Introduction", "Some text about biodiesel yield"]),
        Page(number=2, blocks=["More discussion of catalysts and conversion"]),
    ]
    chunks = chunk_pages(pages, TOK, chunk_tokens=6, overlap_tokens=1)
    assert len(chunks) >= 1
    assert chunks[0].index == 0
    # индексы последовательны
    assert [c.index for c in chunks] == list(range(len(chunks)))
    # диапазоны страниц в пределах корпуса
    assert all(1 <= c.page_start <= c.page_end <= 2 for c in chunks)
    # текст не теряется
    joined = " ".join(c.text for c in chunks)
    assert "biodiesel" in joined and "catalysts" in joined
