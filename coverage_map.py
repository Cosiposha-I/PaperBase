#!/usr/bin/env python3
"""Карта покрытия: какие темы из списка корпус закрывает, а какие — нет.

ЗАЧЕМ
Готовясь к экзамену или обзору, важно знать не то, что в корпусе есть, а то, чего
в нём НЕТ. Семантический поиск всегда что-то возвращает, поэтому «нашлось» само по
себе ничего не значит — значение имеет, насколько близко нашлось и сколько
независимых источников подтверждают.

МЕТОД
По каждой теме — НЕСКОЛЬКО формулировок плюс проверка по словам.

Почему не одна формулировка: первая версия проверяла тему её собственным названием
и дала 3 ложных пробела из 3. Составные названия («Брожение и анаэробное дыхание»)
и термины-ярлыки («блочный принцип») плохо ложатся в эмбеддинг целиком, хотя
содержание в корпусе есть — «молочнокислое брожение» находилось на 66%. Поэтому:
  • тема разбирается на варианты (по двоеточию, запятым, союзу «и»);
  • берётся ЛУЧШИЙ результат по всем вариантам;
  • дополнительно — поиск по характерным словам: в скольких документах они есть.
Пробелом тема считается, только если провалились ОБА способа.

Пороги подобраны эмпирически и вынесены в константы: у разных корпусов и моделей
абсолютные значения score различаются, так что при переносе стоит перепроверить.

ЗАПУСК
    $env:PAPERBASE_CORPUS_DIR="..."; $env:PAPERBASE_DATA_DIR="..."
    python coverage_map.py "путь\\к\\темам.txt" --out "путь\\к\\карте.md"
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

from paperbase.config import load_config
from paperbase.embed import load_embedder
from paperbase.query import search
from paperbase.store import Store

K = 8
STRONG = 0.58        # близость, при которой попадание считаем уверенным
PARTIAL = 0.52       # ниже этого — тема практически не закрыта
MIN_STRONG_HITS = 2  # сколько уверенных попаданий нужно для «покрыто»


def parse_topics(path: Path) -> list[tuple[str, str]]:
    """-> [(раздел, тема)]. Строки '## X' — раздел, '#' — комментарий."""
    section = "Без раздела"
    out: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("##"):
            section = line.lstrip("#").strip()
        elif line.startswith("#"):
            continue
        else:
            out.append((section, line))
    return out


STOP = {"основные", "процессы", "процесс", "получение", "методы", "понятия",
        "принципы", "принцип", "особенности", "классификация", "различия",
        "система", "системе", "систем", "биотехнологии", "биотехнология",
        "биотехнологических", "микроорганизмов", "используемых", "который"}


def variants(topic: str) -> list[str]:
    """Разные способы спросить об одном и том же — компенсируют слабость
    эмбеддинга на составных названиях."""
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
        key = v.lower()
        if key not in seen:
            seen.add(key)
            uniq.append(v)
    return uniq


def keywords(topic: str) -> list[str]:
    """Характерные слова темы, усечённые до основы (русская морфология)."""
    words = re.findall(r"[А-Яа-яЁёA-Za-z]{6,}", topic)
    out = []
    for w in words:
        lw = w.lower()
        if lw in STOP:
            continue
        out.append(lw[:7])          # грубое усечение вместо стеммера
    return out[:4]


def classify(best: float, strong: int, docs: int, kw_docs: int) -> str:
    """Вердикт по совокупности смыслового и словарного признаков."""
    if best >= STRONG and strong >= MIN_STRONG_HITS and docs >= 2:
        return "покрыто"
    if best >= STRONG and kw_docs >= 3:
        return "покрыто"          # тема названа иначе, но слова широко представлены
    if best >= PARTIAL or kw_docs >= 2:
        return "частично"
    return "пробел"


BOOKS = {
    "biryukov": "Бирюков (2004)",
    "192dac": "Минаева (2009)",
    "978-5-9963-2407-1": "Шмид, Наглядная биотехнология",
}


def short_src(h) -> str:
    """Читаемая ссылка.

    Для лекций и презентаций берём ИМЯ ФАЙЛА, а не автора из метаданных PDF:
    у выгрузок из PowerPoint автор — «Майкрософт»/«Admin»/«Windows», а год
    случайный. Имя файла («Лекция 17. Получение биогаза») куда информативнее.
    """
    name = Path(h.filename).stem
    low = name.lower()
    label = next((t for k, t in BOOKS.items() if k in low), None)
    if label is None:
        label = re.sub(r"[_]+", " ", name)
        label = re.sub(r"\s*\(\d+\)\s*$", "", label)
        label = re.sub(r"\s{2,}", " ", label).strip(" .-")
        if len(label) > 52:
            label = label[:49].rstrip() + "…"
    pages = h.pages or []
    p = f"стр. {pages[0]}" if pages else "стр. ?"
    if len(pages) > 1 and pages[-1] != pages[0]:
        p = f"стр. {pages[0]}–{pages[-1]}"
    return f"{label}, {p}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Карта покрытия тем корпусом")
    ap.add_argument("topics", help="файл со списком тем")
    ap.add_argument("--out", default=None, help="куда записать markdown-отчёт")
    ap.add_argument("--k", type=int, default=K)
    args = ap.parse_args()

    topics_path = Path(args.topics)
    if not topics_path.exists():
        print(f"Файл с темами не найден: {topics_path}")
        return 1
    topics = parse_topics(topics_path)
    print(f"Тем к проверке: {len(topics)}")

    cfg = load_config()
    print(f"Корпус: {cfg.corpus_dir}")
    embedder = load_embedder(cfg, lambda m: print(m, flush=True))

    rows = []
    with Store(cfg) as store:
        for i, (section, topic) in enumerate(topics, 1):
            # смысловой поиск по нескольким формулировкам — берём лучшую
            best, strong, docs, top_hit, used = 0.0, 0, 0, None, topic
            for v in variants(topic):
                hits = search(store, embedder, v, k=args.k)
                if not hits:
                    continue
                if hits[0].score > best:
                    best = hits[0].score
                    top_hit, used = hits[0], v
                    strong = sum(1 for h in hits if h.score >= STRONG)
                    docs = len({h.filename for h in hits if h.score >= PARTIAL})

            # словарная проверка: в скольких документах встречаются слова темы
            kw_docs = 0
            for w in keywords(topic):
                res = store.keyword_search(w, limit=50)
                kw_docs = max(kw_docs, len(res.get("documents", [])))

            verdict = classify(best, strong, docs, kw_docs)
            rows.append({"section": section, "topic": topic, "verdict": verdict,
                         "strong": strong, "docs": docs, "best": best,
                         "kw_docs": kw_docs, "used": used,
                         "src": short_src(top_hit) if top_hit else "—"})
            mark = {"покрыто": "OK ", "частично": "~~ ", "пробел": "!! "}[verdict]
            print(f"  {mark}[{i:>2}/{len(topics)}] {topic[:58]:<58} "
                  f"{best * 100:3.0f}%  ист.{docs}  слов.{kw_docs}")

    counts = Counter(r["verdict"] for r in rows)
    print("\n" + "=" * 72)
    print(f"покрыто: {counts['покрыто']}   частично: {counts['частично']}   "
          f"ПРОБЕЛ: {counts['пробел']}   (всего {len(rows)})")
    print("=" * 72)

    if not args.out:
        return 0

    lines = [
        "# Карта покрытия тем корпусом", "",
        f"Корпус: `{cfg.corpus_dir}` · тем проверено: {len(rows)}", "",
        f"**Покрыто: {counts['покрыто']} · частично: {counts['частично']} · "
        f"пробел: {counts['пробел']}**", "",
        "Как читать: «покрыто» — тема уверенно находится минимум в двух источниках; "
        "«частично» — находится, но слабо или в одном источнике; «пробел» — корпус "
        "темы практически не содержит, нужен внешний материал.", "",
        "Оценка автоматическая, по близости эмбеддингов. Пограничные случаи "
        "(«частично») стоит проверить глазами.", "",
    ]

    # Сперва — то, ради чего всё затевалось.
    gaps = [r for r in rows if r["verdict"] == "пробел"]
    if gaps:
        lines += ["## Пробелы — темы без опоры в корпусе", "",
                  "Проверено несколькими формулировками и поиском по словам: "
                  "провалились оба способа. Здесь нужен внешний материал.", ""]
        for r in gaps:
            lines.append(f"- **{r['topic']}**  ·  {r['section'].split('.')[0].strip()}"
                         f"  ·  лучшее совпадение {r['best'] * 100:.0f}%, "
                         f"документов со словами темы: {r['kw_docs']}")
        lines.append("")

    partial = [r for r in rows if r["verdict"] == "частично"]
    if partial:
        lines += ["## Частично — есть, но тонко", "",
                  "Материал находится, но слабо или в одном источнике. "
                  "Проверить глазами: часть окажется полноценной.", ""]
        for r in partial:
            lines.append(f"- **{r['topic']}** — {r['src']} "
                         f"({r['best'] * 100:.0f}%, источников: {r['docs']}, "
                         f"по словам: {r['kw_docs']})")
        lines.append("")

    lines += ["## Полная таблица", ""]
    cur = None
    for r in rows:
        if r["section"] != cur:
            cur = r["section"]
            lines += ["", f"### {cur}", "",
                      "| Тема | Статус | Близость | Источников | Где смотреть |",
                      "|---|---|---|---|---|"]
        badge = {"покрыто": "✅", "частично": "⚠️", "пробел": "❌"}[r["verdict"]]
        lines.append(f"| {r['topic']} | {badge} {r['verdict']} | "
                     f"{r['best'] * 100:.0f}% | {r['docs']} | {r['src']} |")

    out = Path(args.out)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nОтчёт: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
