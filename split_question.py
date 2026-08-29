# -*- coding: utf-8 -*-
"""
Разделение вопроса документа-ответов на два.

Программа испытания дробит темы мельче, чем они были расписаны: один готовый
ответ нередко покрывает два её пункта. Инструмент отрезает часть «Развёрнуто»
начиная с указанного подзаголовка и делает из неё самостоятельный вопрос,
оставляя приёмнику всё, что было до среза.

Блоки «Ключевые цифры», «Уточняющие вопросы» и «Источники» копируются во
второй вопрос целиком — их потом правят вручную под тему.

    python split_question.py файл.md --q 20 --at "Различия архей" \
        --title "Различия в строении и физиологии." --short "Текст блока Коротко"
"""
import argparse
import re

BLOCKS = ("**Ключевые цифры и имена.**", "**Уточняющие вопросы.**", "**Источники.**")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--q", required=True, help="номер разделяемого вопроса")
    ap.add_argument("--at", required=True, help="начало подзаголовка, с которого резать")
    ap.add_argument("--title", required=True, help="заголовок нового вопроса (дословно по программе)")
    ap.add_argument("--short", required=True, help="текст блока «Коротко» для нового вопроса")
    ap.add_argument("--suffix", default="X", help="суффикс временного номера нового вопроса")
    a = ap.parse_args()

    text = open(a.source, encoding="utf-8").read()

    pat = re.compile(r"(### Вопрос " + re.escape(a.q) + r"\. .+?)(?=\n### Вопрос |\Z)", re.S)
    m = pat.search(text)
    if not m:
        raise SystemExit(f"вопрос {a.q} не найден")
    body = m.group(1)

    # точка среза — начало подзаголовка **<at>...
    cut = body.find("**" + a.at)
    if cut < 0:
        raise SystemExit(f"подзаголовок «{a.at}» не найден в вопросе {a.q}")

    # конец отрезаемой части — начало служебных блоков
    end = len(body)
    for b in BLOCKS:
        j = body.find(b, cut)
        if j >= 0:
            end = min(end, j)

    moved = body[cut:end].strip()
    tail = body[end:]                      # служебные блоки — остаются у обоих
    kept = body[:cut].rstrip() + "\n\n" + tail

    new_q = (f"### Вопрос {a.q}{a.suffix}. {a.title}\n\n"
             f"**Коротко.** {a.short}\n\n"
             f"**Развёрнуто.**\n\n{moved}\n\n{tail.lstrip()}")

    text = text[:m.start()] + kept.rstrip() + "\n\n---\n\n" + new_q + text[m.end():]
    open(a.source, "w", encoding="utf-8").write(text)
    print(f"Вопрос {a.q} разделён: новый «{a.q}{a.suffix}» — {a.title[:60]}")
    print(f"  перенесено символов: {len(moved)}")


if __name__ == "__main__":
    main()
