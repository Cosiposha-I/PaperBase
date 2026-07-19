"""Тесты SQLite-части Store на временной БД (ChromaDB не трогаем — она ленивая)."""
import pytest

from paperbase.config import Config
from paperbase.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(Config(data_dir=tmp_path))
    yield s
    s.close()


def _insert(store, *, filename, cite_key, sha, year=2021):
    return store.insert_document(
        filename=filename, path=f"C:/x/{filename}", sha256=sha, cite_key=cite_key,
        title=f"Title {cite_key}", authors="Ivanov, I.", year=year, journal="Fuel",
        doi="", abstract="", num_pages=10, status="ok")


def test_insert_and_lookup(store):
    doc_id = _insert(store, filename="a.pdf", cite_key="Ivanov2021", sha="sha1")
    assert store.doc_by_id(doc_id)["cite_key"] == "Ivanov2021"
    assert store.doc_by_sha("sha1")["filename"] == "a.pdf"


def test_unique_cite_key_suffix(store):
    _insert(store, filename="a.pdf", cite_key="Ivanov2021", sha="sha1")
    assert store.unique_cite_key("Ivanov2021") == "Ivanov2021a"
    assert store.unique_cite_key("Petrov2020") == "Petrov2020"


def test_resolve_doc_by_id_and_name(store):
    doc_id = _insert(store, filename="mixed_oils.pdf", cite_key="Brahma2022", sha="sha2")
    by_id, _ = store.resolve_doc(str(doc_id))
    assert by_id["id"] == doc_id
    by_name, _ = store.resolve_doc("mixed_oils.pdf")
    assert by_name["cite_key"] == "Brahma2022"


def test_doc_ids_by_paper(store):
    _insert(store, filename="mixed_oils.pdf", cite_key="Brahma2022", sha="sha2")
    _insert(store, filename="review_algae.pdf", cite_key="Gaurav2024", sha="sha3")
    assert len(store.doc_ids_by_paper("mixed")) == 1        # подстрока
    assert len(store.doc_ids_by_paper("*.pdf")) == 2         # glob
    assert store.doc_ids_by_paper("nonexistent") == []


def test_keyword_search(store):
    d1 = _insert(store, filename="a.pdf", cite_key="A2021", sha="s1")
    d2 = _insert(store, filename="b.pdf", cite_key="B2022", sha="s2")
    # чанки вставляем напрямую — keyword ищет по chunks.text, ChromaDB не нужен
    store.conn.executemany(
        "INSERT INTO chunks (doc_id, chunk_index, text, page_start, page_end, section) "
        "VALUES (?,?,?,?,?,?)",
        [(d1, 0, "Phycocyanin is a blue pigment", 1, 1, ""),
         (d1, 1, "more about phycocyanin here", 2, 2, ""),
         (d2, 0, "unrelated text about lipids", 1, 1, "")])
    store.conn.commit()

    res = store.keyword_search("phycocyanin")           # регистронезависимо
    assert res["total"] == 1                            # только один документ
    assert res["documents"][0]["cite_key"] == "A2021"
    assert res["documents"][0]["hits"] == 2             # два чанка с термином
    assert "phycocyanin" in res["documents"][0]["snippet"].lower()
    assert store.keyword_search("nonexistent")["total"] == 0
