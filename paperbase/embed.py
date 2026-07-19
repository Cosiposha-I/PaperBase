"""Локальные эмбеддинги через sentence-transformers.

Модель задаётся в config.toml (сейчас BAAI/bge-m3 — мультиязычная, кросс-язычный
поиск EN/RU, контекст 8192, префиксы не нужны). Для e5-моделей обязательны префиксы
"query: " / "passage: " — они включаются автоматически по флагу is_e5. Если основная
модель не загрузилась (память и т.п.) — fallback на лёгкую multilingual-MiniLM.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from .config import Config


def _pick_device(torch) -> str:
    """Ускоритель для device='auto': CUDA (NVIDIA) → MPS (Apple Silicon) → CPU.

    Без ветки MPS на Mac всё считалось бы на CPU, а индексация там заметно медленнее.
    """
    try:
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    try:
        if torch.backends.mps.is_available():   # Apple Silicon (Metal)
            return "mps"
    except Exception:                            # старый torch без backends.mps
        pass
    return "cpu"


class Embedder:
    def __init__(self, cfg: Config):
        import torch
        from sentence_transformers import SentenceTransformer

        device = cfg.device
        if device == "auto":
            device = _pick_device(torch)

        self.model_name = cfg.model
        try:
            self.model = SentenceTransformer(cfg.model, device=device)
        except Exception as e:
            print(f"[warn] не удалось загрузить {cfg.model} ({e}); "
                  f"использую fallback {cfg.fallback_model}", file=sys.stderr)
            self.model_name = cfg.fallback_model
            self.model = SentenceTransformer(cfg.fallback_model, device=device)

        self.device = device
        self.batch_size = cfg.batch_size
        self.is_e5 = "e5" in self.model_name.lower()
        self.tokenizer = self.model.tokenizer
        self.max_seq_length = int(getattr(self.model, "max_seq_length", None)
                                  or getattr(self.tokenizer, "model_max_length", 512))

    def count_tokens(self, text: str) -> int:
        return len(self.tokenizer.encode(text, add_special_tokens=False))

    def embedding_token_len(self, text: str) -> int:
        """Сколько токенов реально уйдёт в модель (с префиксом e5 и спецтокенами).
        Если больше max_seq_length — хвост будет усечён при эмбеддинге."""
        t = f"passage: {text}" if self.is_e5 else text
        return len(self.tokenizer.encode(t, add_special_tokens=True))

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        if self.is_e5:
            texts = [f"passage: {t}" for t in texts]
        emb = self.model.encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return emb.tolist()

    def embed_query(self, text: str) -> list[float]:
        q = f"query: {text}" if self.is_e5 else text
        emb = self.model.encode([q], normalize_embeddings=True,
                                show_progress_bar=False, convert_to_numpy=True)
        return emb[0].tolist()


def load_embedder(cfg: Config, printer=print) -> Embedder:
    """Создать Embedder, сообщив о загрузке и готовности через printer.
    printer — куда писать: print (сервер) или tqdm.write (прогресс-бар ingest)."""
    printer(f"Загружаю модель эмбеддингов ({cfg.model})…")
    emb = Embedder(cfg)
    printer(f"Модель готова: {emb.model_name} [{emb.device}]")
    return emb
