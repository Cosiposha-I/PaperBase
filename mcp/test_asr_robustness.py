#!/usr/bin/env python3
"""Насколько поиск устойчив к ошибкам распознавания речи.

ЗАЧЕМ
В схеме «ассистент звонков» между собеседником и корпусом стоит распознавание речи.
Научная терминология — худший для него случай. Прямой вопрос «точен ли Whisper»
требует звука; косвенный и более полезный — «сколько точности вообще нужно» —
проверяется без звука вовсе.

МЕТОД
Берём термины из корпуса, портим их так, как их портит русское ASR (разрыв слова,
фонетическая замена, потеря окончания, латиница кириллицей), и сравниваем выдачу
поиска с эталонной. Если документы совпадают — ошибки распознавания не смертельны,
и требования к STT можно снизить.

Требуется работающий `paperbase serve` (поднимется сам через MCP-сервер, либо
запусти вручную).  Запуск:  python mcp/test_asr_robustness.py
"""

from __future__ import annotations

import json
import statistics
import sys
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8765"
K = 8

# (эталон, [искажения]) — искажения смоделированы по типовым ошибкам русского ASR
CASES: list[tuple[str, list[tuple[str, str]]]] = [
    ("переэтерификация растительных масел", [
        ("разрыв слова",        "пере этерификация растительных масел"),
        ("фонетическая замена", "переэстерификация растительных масел"),
        ("сильное искажение",   "пере это рефикация растительных масел"),
    ]),
    ("мольное соотношение спирта к маслу", [
        ("похожее слово",       "модное соотношение спирта к маслу"),
        ("разрыв слова",        "моль ное соотношение спирта к маслу"),
        ("потеря окончаний",    "мольн соотношени спирт к масл"),
    ]),
    ("гетерогенный катализатор оксид кальция", [
        ("разрыв слова",        "гетеро генный катализатор оксид кальция"),
        ("согласование",        "гетерогенной катализатор оксид кальций"),
        ("латиница кириллицей", "гетерогенный катализатор сао"),
    ]),
    ("свободные жирные кислоты и омыление", [
        ("согласование",        "свободный жирный кислоты и омыление"),
        ("разрыв слова",        "свободные жирные кислоты и о мыление"),
        ("похожее слово",       "свободные жирные кислоты и умиление"),
    ]),
    ("гидроксид калия как катализатор", [
        ("разрыв слова",        "гидро оксид калия как катализатор"),
        ("похожее слово",       "гидроксид кальция как катализатор"),
        ("сокращение",          "кон как катализатор"),
    ]),
    ("сверхкритический метанол высокое давление", [
        ("разрыв слова",        "сверх критический метанол высокое давление"),
        ("фонетическая замена", "сверхкритичный метанол высокое давление"),
        ("потеря слова",        "критический метанол давление"),
    ]),
]


def search(q: str, k: int = K) -> dict:
    url = f"{BASE}/api/search?" + urllib.parse.urlencode({"q": q, "k": k}, encoding="utf-8")
    with urllib.request.urlopen(url, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))


def docs_of(data: dict) -> list[str]:
    """Порядок документов в выдаче, без повторов."""
    seen, out = set(), []
    for h in data.get("hits", []):
        f = h.get("filename", "")
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def main() -> int:
    try:
        search("проверка связи", k=1)
    except Exception as e:
        print(f"paperbase serve не отвечает на {BASE}: {e}")
        print("Запусти:  .\\pb-gpu.ps1 serve")
        return 1

    print("=" * 78)
    print("Устойчивость поиска к ошибкам распознавания речи")
    print("=" * 78)
    print("Сравниваем: набор документов и топ-1 при эталонном и искажённом запросе.\n")

    all_overlap: list[float] = []
    all_top1: list[bool] = []
    by_kind: dict[str, list[float]] = {}

    for clean, corruptions in CASES:
        ref = search(clean)
        ref_docs = docs_of(ref)
        if not ref_docs:
            print(f"[пропуск] эталон ничего не нашёл: {clean}")
            continue

        print(f"■ эталон: «{clean}»")
        print(f"  документов: {len(ref_docs)}; топ-1: {ref_docs[0][:58]}")

        for kind, bad in corruptions:
            got = search(bad)
            got_docs = docs_of(got)
            overlap = len(set(got_docs) & set(ref_docs)) / len(ref_docs) * 100
            top1 = bool(got_docs) and got_docs[0] == ref_docs[0]

            all_overlap.append(overlap)
            all_top1.append(top1)
            by_kind.setdefault(kind, []).append(overlap)

            mark = "OK  " if top1 else "top1"
            print(f"    {mark} [{kind:>20}] «{bad[:46]}»")
            print(f"         документы совпали: {overlap:3.0f}%   "
                  f"топ-1 {'совпал' if top1 else 'НЕ совпал'}")
        print()

    if not all_overlap:
        print("Нет данных.")
        return 1

    print("=" * 78)
    print("ИТОГ")
    print("=" * 78)
    print(f"Проверено искажений:            {len(all_overlap)}")
    print(f"Совпадение набора документов:   среднее {statistics.mean(all_overlap):.0f}%, "
          f"медиана {statistics.median(all_overlap):.0f}%, "
          f"минимум {min(all_overlap):.0f}%")
    print(f"Топ-1 документ устоял:          {sum(all_top1)} из {len(all_top1)} "
          f"({sum(all_top1) / len(all_top1) * 100:.0f}%)")
    print("\nПо типам искажений (совпадение документов):")
    for kind, vals in sorted(by_kind.items(), key=lambda kv: -statistics.mean(kv[1])):
        print(f"  {kind:>22}: {statistics.mean(vals):3.0f}%  (проверок: {len(vals)})")

    print("\nКак читать: высокое совпадение означает, что ошибки распознавания")
    print("не разрушают поиск — значит, к качеству STT можно предъявлять")
    print("умеренные требования. Низкое — распознавание становится узким местом.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
