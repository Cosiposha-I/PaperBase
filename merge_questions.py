# -*- coding: utf-8 -*-
"""
Слияние двух вопросов документа-ответов в один.

Программа испытания местами объединяет то, что было расписано отдельно
(«Брожение и анаэробное дыхание», «Мутации и рекомбинации»), а местами
содержит темы, которых в программе нет вовсе, — их материал нужно перенести
внутрь профильного вопроса, а не выбрасывать.

Что делает: берёт вопрос-источник, переносит его «Развёрнуто» в приёмник
отдельным подзаголовком, дописывает строки его таблицы «Ключевые цифры» и
пункты «Уточняющие вопросы», объединяет «Источники» и удаляет источник.

    python merge_questions.py файл.md --from 9 --into 8 --title "Цитоплазматическая мембрана"
"""
import argparse
import re

BLOCKS = ("**Коротко.**", "**Развёрнуто.**", "**Ключевые цифры и имена.**",
          "**Уточняющие вопросы.**", "**Источники.**")


def split_questions(text):
    """[(преамбула), (номер, заголовок, тело), ...] — по '### Вопрос N. '"""
    parts = re.split(r"^### Вопрос (\S+)\. (.+)$", text, flags=re.M)
    head, items = parts[0], []
    for i in range(1, len(parts), 3):
        items.append([parts[i], parts[i + 1], parts[i + 2]])
    return head, items


def get_block(body, name):
    """Возвращает (текст_блока, начало, конец) для блока по имени."""
    i = body.find(name)
    if i < 0:
        return "", -1, -1
    nxt = len(body)
    for b in BLOCKS:
        j = body.find(b, i + len(name))
        if j >= 0:
            nxt = min(nxt, j)
    # не выходим за разделитель вопроса
    hr = body.find("\n---", i)
    if 0 <= hr < nxt:
        nxt = hr
    return body[i + len(name):nxt].strip(), i, nxt


def table_rows(block):
    """Строки markdown-таблицы без шапки и разделителя."""
    rows = [l for l in block.splitlines()
            if l.strip().startswith("|") and not re.match(r"^\|[\s:-]+\|", l.strip())]
    return [r for r in rows if not re.match(r"^\|\s*(Факт|Параметр|Признак)\s*\|", r.strip())]


def bullets(block):
    return [l for l in block.splitlines() if l.strip().startswith("- *")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--from", dest="src", required=True, help="номер вопроса-источника")
    ap.add_argument("--into", dest="dst", required=True, help="номер вопроса-приёмника")
    ap.add_argument("--title", required=True, help="подзаголовок для перенесённого материала")
    a = ap.parse_args()

    text = open(a.source, encoding="utf-8").read()
    head, items = split_questions(text)

    si = next((k for k, it in enumerate(items) if it[0] == a.src), None)
    di = next((k for k, it in enumerate(items) if it[0] == a.dst), None)
    if si is None or di is None:
        raise SystemExit(f"не найден вопрос: from={a.src} into={a.dst}")

    sbody, dbody = items[si][2], items[di][2]

    s_dev, _, _ = get_block(sbody, "**Развёрнуто.**")
    s_tab, _, _ = get_block(sbody, "**Ключевые цифры и имена.**")
    s_q, _, _ = get_block(sbody, "**Уточняющие вопросы.**")
    s_src, _, _ = get_block(sbody, "**Источники.**")

    # 1) вставляем «Развёрнуто» источника в конец «Развёрнуто» приёмника
    d_dev, d_i, d_j = get_block(dbody, "**Развёрнуто.**")
    add = f"\n\n**{a.title}.**\n\n{s_dev.strip()}\n\n"
    dbody = dbody[:d_j] + add + dbody[d_j:]

    # 2) дописываем строки таблицы
    rows = table_rows(s_tab)
    if rows:
        d_tab, t_i, t_j = get_block(dbody, "**Ключевые цифры и имена.**")
        dbody = dbody[:t_j].rstrip() + "\n" + "\n".join(rows) + "\n\n" + dbody[t_j:].lstrip("\n")

    # 3) дописываем уточняющие вопросы
    bs = bullets(s_q)
    if bs:
        d_q, q_i, q_j = get_block(dbody, "**Уточняющие вопросы.**")
        dbody = dbody[:q_j].rstrip() + "\n" + "\n".join(bs) + "\n\n" + dbody[q_j:].lstrip("\n")

    # 4) объединяем источники
    if s_src:
        d_src, r_i, r_j = get_block(dbody, "**Источники.**")
        merged = d_src.rstrip().rstrip(".") + "; " + s_src.strip().lstrip("**Источники.**").strip()
        dbody = dbody[:r_i + len("**Источники.**")] + " " + merged + "\n\n" + dbody[r_j:].lstrip("\n")

    items[di][2] = dbody
    del items[si]

    out = head + "".join(f"### Вопрос {n}. {t}{b}" for n, t, b in items)
    open(a.source, "w", encoding="utf-8").write(out)
    print(f"Вопрос {a.src} влит в {a.dst} как «{a.title}»; "
          f"перенесено строк таблицы: {len(rows)}, уточняющих: {len(bs)}")


if __name__ == "__main__":
    main()
