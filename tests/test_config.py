"""Тест чтения config.toml через переменную PAPERBASE_CONFIG."""
from pathlib import Path

from paperbase.config import load_config


def test_load_config_reads_values(tmp_path, monkeypatch):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    toml = tmp_path / "cfg.toml"
    toml.write_text(
        "[paths]\n"
        f'corpus_dir = "{corpus.as_posix()}"\n'
        f'data_dir = "{(tmp_path / "data").as_posix()}"\n'
        "[embedding]\n"
        'model = "test-model"\n'
        "batch_size = 4\n"
        "[chunking]\n"
        "chunk_tokens = 333\n"
        "overlap_tokens = 77\n"
        "[search]\n"
        "default_k = 5\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PAPERBASE_CONFIG", str(toml))

    cfg = load_config()
    assert cfg.model == "test-model"
    assert cfg.batch_size == 4
    assert cfg.chunk_tokens == 333
    assert cfg.overlap_tokens == 77
    assert cfg.default_k == 5
    assert cfg.corpus_dir == Path(corpus)


def test_load_config_missing_returns_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERBASE_CONFIG", str(tmp_path / "нет.toml"))
    cfg = load_config()
    # без TOML берётся встроенный дефолт модели (запасное значение)
    assert isinstance(cfg.model, str) and cfg.model
    assert cfg.chunk_tokens > 0 and cfg.overlap_tokens >= 0


def test_env_path_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERBASE_CORPUS_DIR", str(tmp_path / "corp"))
    monkeypatch.setenv("PAPERBASE_DATA_DIR", str(tmp_path / "dat"))
    cfg = load_config()
    assert cfg.corpus_dir == tmp_path / "corp"
    assert cfg.data_dir == tmp_path / "dat"


def test_chroma_dir_ascii_relocation(tmp_path, monkeypatch):
    from paperbase.config import Config
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData"))
    # ASCII-путь data_dir → chroma лежит рядом
    ascii_cfg = Config(data_dir=tmp_path / "data")
    assert ascii_cfg.chroma_dir == tmp_path / "data" / "chroma"
    # не-ASCII data_dir → индекс уезжает на ASCII-путь (баг ChromaDB на Windows)
    cyr_cfg = Config(data_dir=tmp_path / "данные-тема")
    assert str(cyr_cfg.chroma_dir).isascii()
    assert "PaperBase" in str(cyr_cfg.chroma_dir)
