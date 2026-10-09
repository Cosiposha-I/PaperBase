# -*- coding: utf-8 -*-
r"""Батч-поиск по корпусу paperbase: модель BAAI/bge-m3 грузится ОДИН раз,
прогоняется список запросов, результат — JSON (rank/score/cite_key/citation/
filename/pages/section/text).

Зачем: обычный CLI-`search` грузит модель на каждый вызов (~20-30 с холодного
старта). Когда запросов много (сбор материала под обзор/таблицу/Excel), это долго.
Здесь модель грузится один раз на все запросы. Инструмент ТОЛЬКО ищет — синтез
(ответы, обзоры, таблицы) делает Claude поверх этого JSON, как и в остальном paperbase.

Запуск из папки paperbase:
    GPU:  .\.venv\Scripts\python.exe batch_search.py out.json [queries.json]
    CPU:  python batch_search.py out.json [queries.json]

Запросы берутся из файла queries.json (если указан вторым аргументом), формат:
    [["тег", "текст запроса", 25], ...]
Если файл не указан — используется встроенный список QUERIES ниже (правьте под задачу).
"""
import json, sys, os

# Найти пакет `paperbase` рядом с этим файлом — скрипт запускается из любой папки.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from paperbase.config import load_config
from paperbase.store import Store
from paperbase.embed import Embedder
from paperbase.query import search

# --- отредактируйте под свою задачу ---
QUERIES = [
    ("transesterification", "переэтерификация триглицериды спирт метанол катализатор", 10),
    ("catalysts", "гомогенные гетерогенные ферментативные катализаторы переэтерификации", 10),
    ("factors", "факторы влияющие на выход биодизеля соотношение температура время загрузка", 12),
    ("properties", "физико-химические свойства биодизеля цетановое число вязкость плотность", 10),
    ("standards", "стандарты качества EN 14214 ASTM D6751", 8),
]

def load_queries(path):
    """Запросы из JSON-файла: [["тег","текст",k], ...] либо {"тег":["текст",k]}."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return [(k, v[0], v[1]) for k, v in data.items()]
    return [tuple(x) for x in data]

def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "batch_search_out.json"
    queries = QUERIES
    if len(sys.argv) > 2:                      # запросы из внешнего JSON-файла
        queries = load_queries(sys.argv[2])
        print(f"запросов из файла: {len(queries)}", file=sys.stderr)
    cfg = load_config()
    with Store(cfg) as store:
        emb = Embedder(cfg)
        out = {}
        for key, q, k in queries:
            hits = search(store, emb, q, k=k)
            out[key] = {"query": q, "hits": [h.to_dict() for h in hits]}
            print(f"[{key}] {len(hits)} hits", file=sys.stderr)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    print("written", os.path.abspath(out_path), file=sys.stderr)

if __name__ == "__main__":
    main()
