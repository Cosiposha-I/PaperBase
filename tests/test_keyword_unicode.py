"""Регрессия: поиск по слову должен находить кириллицу независимо от регистра.

Встроенная в SQLite функция lower() понижает регистр только у ASCII —
lower('Андрюса') возвращает строку без изменений. Пока keyword_search
использовал её, слова с заглавной кириллической буквы не находились вовсе:
запрос «Андрюс» по корпусу, где фамилия встречается на трёх страницах,
возвращал ноль документов. Страдали в первую очередь имена собственные.

Проверяем на живой временной базе, а не на моках: ошибка была именно
в поведении SQLite, мок бы её не поймал.
"""

from __future__ import annotations

import hashlib

import pytest

from paperbase.store import Store, _ulower


def _add_doc(store: Store, filename: str, texts: list[str]) -> int:
    """Документ с чанками — через штатный insert_document, а не сырым SQL."""
    doc_id = store.insert_document(
        filename=filename, path=f"/tmp/{filename}",
        sha256=hashlib.sha256(filename.encode()).hexdigest(),
        cite_key=f"Test{filename[:4]}", title=filename, authors="Тестов И.И.",
        year=2020, journal="", doi="", abstract="", num_pages=len(texts),
        status="ok")
    for i, t in enumerate(texts):
        store.conn.execute(
            "INSERT INTO chunks (doc_id, chunk_index, page_start, page_end, "
            "section, text) VALUES (?, ?, ?, ?, ?, ?)",
            (doc_id, i, i + 1, i + 1, "", t))
    store.conn.commit()
    return doc_id


@pytest.fixture()
def store(tmp_path, monkeypatch):
    from paperbase.config import load_config

    monkeypatch.setenv("PAPERBASE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PAPERBASE_CORPUS_DIR", str(tmp_path / "corpus"))
    (tmp_path / "corpus").mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    with Store(cfg) as s:
        yield s


def test_ulower_handles_cyrillic():
    """Сама функция: Python понижает кириллицу, в отличие от SQLite."""
    assert _ulower("Андрюса") == "андрюса"
    assert _ulower("ИЕРУСАЛИМСКИЙ") == "иерусалимский"
    assert _ulower("ANDRUS") == "andrus"
    assert _ulower(None) is None


def test_sqlite_builtin_lower_is_ascii_only(store):
    """Фиксируем причину ошибки: встроенная lower() кириллицу не трогает.

    Если однажды SQLite это исправит, тест упадёт — и подскажет,
    что обходной путь больше не нужен.
    """
    assert store.conn.execute("SELECT lower('Андрюса')").fetchone()[0] == "Андрюса"
    assert store.conn.execute("SELECT lower('ANDRUS')").fetchone()[0] == "andrus"
    # а наша функция понижает как надо
    assert store.conn.execute("SELECT ulower('Андрюса')").fetchone()[0] == "андрюса"


def test_keyword_finds_capitalized_cyrillic(store):
    """Главная регрессия: имя собственное с заглавной буквы должно находиться."""
    _add_doc(store, "model.pdf", [
        "Зависимость удельной скорости роста по Андрюсу имеет явный экстремум.",
        "Графическое выражение зависимости по модели Иерусалимского приведено ниже.",
    ])
    _add_doc(store, "other.pdf", ["Уравнение Моно и модель Блэкмана."])

    for term in ("Андрюс", "андрюс", "АНДРЮС"):
        res = store.keyword_search(term)
        assert res["total"] == 1, f"«{term}» не нашёлся (было 0 до исправления)"

    assert store.keyword_search("Иерусалимск")["total"] == 1
    assert store.keyword_search("Блэкман")["total"] == 1
    assert store.keyword_search("моно")["total"] == 1


def test_keyword_case_insensitive_both_ways(store):
    """Регистр не должен влиять ни со стороны запроса, ни со стороны текста."""
    _add_doc(store, "mixed.pdf", ["ФЕРМЕНТАЦИЯ идёт в биореакторе.",
                                  "ферментация периодическая."])
    for term in ("ФЕРМЕНТАЦИЯ", "Ферментация", "ферментация", "фермент"):
        assert store.keyword_search(term)["total"] == 1, term


def test_keyword_snippet_not_broken_by_case(store):
    """Фрагмент-подсказка должен находиться даже при несовпадении регистра."""
    _add_doc(store, "snip.pdf", ["Модель Кобозева описывает начальный участок."])
    res = store.keyword_search("кобозев")
    assert res["total"] == 1
    assert "Кобозева" in res["documents"][0]["snippet"]
