#!/usr/bin/env python3
"""Собрать материал по темам: фрагменты корпуса со ссылками — сырьё для ответов.

Скрипт НЕ пишет ответы (это делает Claude поверх выдачи) — он собирает по каждой
теме лучшие фрагменты с указанием источника и страницы, чтобы ответ можно было
написать с корректными ссылками и ничего не выдумывая.

Как и в coverage_map.py, тема спрашивается несколькими формулировками: составные
названия плохо ложатся в эмбеддинг целиком.

    python build_answers_digest.py темы.txt --section 1 --out digest-1.md
    python build_answers_digest.py темы.txt --out digest-all.md
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from paperbase.config import load_config
from paperbase.embed import load_embedder
from paperbase.query import search
from paperbase.store import Store

FRAGMENTS = 8        # сколько фрагментов оставить на тему
CHARS = 700          # до скольких символов резать фрагмент
MIN_SCORE = 0.45     # ниже — шум, в подборку не берём
DEPTH = 14           # глубина поиска на КАЖДУЮ формулировку
PER_DOC = 3          # не больше N фрагментов из одного документа

# Почему DEPTH заметно больше числа итоговых фрагментов: при корпусе в 181
# документ топ-5 по одной формулировке легко целиком уходит в один источник,
# и хорошие материалы из других лекций не попадают в пул вовсе (проверено на
# теме «16S рРНК»: прямой поиск находил три лекции, которых в подборке не было).
# PER_DOC заставляет ответ опираться на разные источники, а не на один.


def parse_topics(path: Path) -> list[tuple[str, str, str]]:
    """-> [(раздел, подраздел, тема)]. Порядок проверок важен: '###' тоже
    начинается с '##', поэтому подраздел разбираем первым."""
    section, sub, out = "Без раздела", "", []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("###"):
            sub = line.lstrip("#").strip()
        elif line.startswith("##"):
            section = line.lstrip("#").strip()
            sub = ""
        elif line.startswith("#"):
            continue
        else:
            out.append((section, sub, line))
    return out


def variants(topic: str) -> list[str]:
    out = [topic]
    if ":" in topic:
        head, tail = topic.split(":", 1)
        out += [head.strip(), tail.strip()]
    for sep in (r"[,;]", r"\s+и\s+"):
        for part in re.split(sep, topic):
            p = part.strip(" .,;:")
            if len(p) >= 12:
                out.append(p)
    seen, uniq = set(), []
    for v in out:
        if v.lower() not in seen:
            seen.add(v.lower())
            uniq.append(v)
    return uniq


# Учебники распознаём по имени файла и подписываем по-человечески.
# Всё остальное (лекции, презентации) подписываем ИМЕНЕМ ФАЙЛА, а не автором:
# в метаданных PDF, выгруженных из PowerPoint, автор — «Майкрософт», «Admin»,
# «Windows», а год бывает 1901 или 2027. Ссылка «(Майкрософт, 2027)» на экзамене
# недопустима, тогда как «Лекция 17. Получение биогаза, стр. 4» — то что нужно.
BOOKS = {
    "biryukov": "Бирюков В.В., Основы промышленной биотехнологии (2004)",
    "192dac": "Учебное пособие по биотехнологии (Минаева, 2009)",
    "978-5-9963-2407-1": "Шмид Р., Наглядная биотехнология и генетическая инженерия",
}


def label(h) -> str:
    """Ссылка в том виде, в каком она пойдёт в ответ."""
    name = Path(h.filename).stem
    low = name.lower()

    src = next((title for key, title in BOOKS.items() if key in low), None)
    if src is None:
        # имя файла — само по себе осмысленная ссылка; чистим служебные хвосты
        src = re.sub(r"[_]+", " ", name)
        src = re.sub(r"\s*\(\d+\)\s*$", "", src)        # «Лекция 9 (2)» -> «Лекция 9»
        src = re.sub(r"\s*-\s*(копия|copy)\s*$", "", src, flags=re.I)
        src = re.sub(r"\s{2,}", " ", src).strip(" .-")
        if len(src) > 70:
            src = src[:67].rstrip() + "…"
    p = h.pages or []
    pages = f"стр. {p[0]}" if not p or len(p) < 2 or p[0] == p[-1] else f"стр. {p[0]}–{p[-1]}"
    return f"{src}, {pages}"


def clean(t: str) -> str:
    t = re.sub(r"Copyright ОАО «ЦКБ «БИБКОМ».*?Cервис»", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("topics")
    ap.add_argument("--section", default=None, help="номер раздела (1, 2, 3)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--fragments", type=int, default=FRAGMENTS)
    ap.add_argument("--chars", type=int, default=CHARS,
                    help="до скольких символов резать фрагмент")
    ap.add_argument("--depth", type=int, default=DEPTH,
                    help="глубина поиска на каждую формулировку (top-K)")
    ap.add_argument("--per-doc", type=int, default=PER_DOC,
                    help="максимум фрагментов из одного документа")
    args = ap.parse_args()

    topics = parse_topics(Path(args.topics))
    if args.section:
        # '1' -> весь раздел 1; '1.3' -> только подраздел 1.3
        s = args.section.strip()
        if "." in s:
            topics = [t for t in topics if t[1].strip().startswith(s)]
        else:
            topics = [t for t in topics if t[0].strip().startswith(s)]
    print(f"Тем в подборке: {len(topics)}")

    cfg = load_config()
    emb = load_embedder(cfg, lambda m: print(m, flush=True))

    lines = ["# Материал по темам (фрагменты корпуса со ссылками)", ""]
    with Store(cfg) as store:
        cur_sec = cur_sub = None
        for i, (section, sub, topic) in enumerate(topics, 1):
            if section != cur_sec:
                cur_sec, cur_sub = section, None
                lines += ["", f"## {section}", ""]
            if sub != cur_sub:
                cur_sub = sub
                if sub:
                    lines += [f"### {sub}", ""]

            pool: dict[str, object] = {}
            for v in variants(topic):
                for h in search(store, emb, v, k=args.depth):
                    if h.score < MIN_SCORE:
                        continue
                    key = f"{h.filename}|{(h.pages or [0])[0]}"
                    if key not in pool or h.score > pool[key].score:
                        pool[key] = h

            # Отбор с квотой на документ: идём по убыванию близости, но не даём
            # одному источнику занять всю подборку — ответ должен опираться
            # на несколько лекций, а не пересказывать одну.
            best, used = [], {}
            for h in sorted(pool.values(), key=lambda x: -x.score):
                if len(best) >= args.fragments:
                    break
                if used.get(h.filename, 0) >= args.per_doc:
                    continue
                used[h.filename] = used.get(h.filename, 0) + 1
                best.append(h)

            lines += [f"#### {i}. {topic}", ""]
            if not best:
                lines += ["_В корпусе не найдено._", ""]
            for h in best:
                lines.append(f"**[{label(h)}]** ({h.score * 100:.0f}%)")
                lines.append("")
                lines.append(clean(h.text)[:args.chars] + "…")
                lines.append("")
            print(f"  [{i:>2}/{len(topics)}] {topic[:58]:<58} фрагментов: {len(best)}")

    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nПодборка: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
